"""资产表增量正确性集成测试（真实 PG + RLS + FrozenClock）。

覆盖设计 §9.4 的核心断言：
- IT-01 增量 == 全量（逐行字段 + state 字段；含 canary 自检）；
- IT-02 最小写入（45 天安全窗，窗口外行 rebuilt_at 不变）；
- IT-03 no-op；IT-04/IT-13 修正/改日期 → 全量；IT-05 撤销/软删/恢复；
- IT-06/IT-17 行情 as_of 前进（含修复 degraded 段）；as_of 跨度 >45 天 → 全量；
- IT-11 end 回退 → 全量；IT-14 指纹竞态兜底；FIFO 异常集合变化 → 全量；
- IT-08 失败原子性（事务回滚，旧行/旧 state 完好）；状态 CAS 冲突；
- 回填（首个余额行前 → 全量；之后 → 增量）；
- `GET /assets` 的 build.state 判定链（current/stale/missing/blocked）与游标分页；
- worker handler 的 progress/result 键与取消。

造数一律用 owner（绕过 RLS），构建/读取走 app 角色的租户事务（RLS 真正生效）。
软删/修正一律刷新 `updated_at`（与 ORM UPDATE 一致），否则水位线/指纹检测不到。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_contracts.clock import FrozenClock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_platform.ports import Bar
from stock_review.application.assets import (
    FIFO_ERROR_REASON,
    MARKET_UNCOVERED_REASON,
    AssetBuildSummary,
    build_assets,
    build_assets_job,
    list_assets,
)
from stock_review.models import AssetBuildState, AssetDaily
from stock_review.repositories.assets import AssetRepository

pytestmark = [pytest.mark.integration]

# 业务日 2026-06-30（Asia/Shanghai）；replay 行情 as_of=2026-09-30。
CLOCK = FrozenClock("2026-06-30T12:00:00+08:00")
# 造数用固定 updated_at（保证两条构建路径指纹一致/可复现）。
BASE_UPDATED_AT = datetime.fromisoformat("2026-06-01T00:00:00+08:00")
LATER_UPDATED_AT = datetime.fromisoformat("2026-07-01T00:00:00+08:00")

INSERT_TRADE_SQL = """
insert into ledger.trade (
    id, user_id, trade_date, security_code, security_name, summary, side,
    quantity_units, price_units, amount_units, commission_units, stamp_tax_units,
    other_fee_units, cash_balance_units, cash_balance_available, category,
    dedupe_key, occurrence_idx, source, version, line_no, updated_at
) values (
    :id, :user_id, :trade_date, :security_code, :security_name, :summary, :side,
    :quantity_units, :price_units, :amount_units, 0, 0,
    0, :cash_balance_units, :cash_balance_available, :category,
    :dedupe_key, 0, 'import', :version, :line_no, :updated_at
)
"""

# 用于 IT-01 逐行比较的字段（排除 rebuilt_at/created_at/updated_at）。
ROW_FIELDS: tuple[str, ...] = (
    "trade_date",
    "cash_units",
    "market_value_units",
    "total_assets_units",
    "holding_cost_units",
    "unrealized_pnl_units",
    "holding_count",
    "cashflow_units",
    "prev_total_assets_units",
    "daily_pnl_units",
    "daily_return_units",
    "status",
    "status_reason",
    "definition_version",
)
# state 比较字段：排除时间戳/审计列/build-meta（IT-01 口径）。
STATE_FIELDS: tuple[str, ...] = (
    "start_date",
    "last_built_through",
    "last_trade_count",
    "last_trade_version_sum",
    "data_as_of",
    "assumption",
    "last_status",
    "last_fifo_error",
    "status_reason",
    "definition_version",
    "rows_count",
)


def cents(value: str) -> int:
    from decimal import Decimal

    return int(Decimal(value) * Decimal(100))


def price(value: str) -> int:
    from decimal import Decimal

    return int(Decimal(value) * Decimal(10**6))


def shares(value: str) -> int:
    from decimal import Decimal

    return int(Decimal(value) * Decimal(10**4))


# ── 造数（owner）──────────────────────────────────────────


async def _insert_user(engine: AsyncEngine) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.user (id, email, password_hash, role, status) "
                "values (:id, :email, 'x', 'user', 'active')"
            ),
            {"id": user_id, "email": f"{user_id.hex[:12]}@example.com"},
        )
    return user_id


async def _seed_trade(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    *,
    trade_date: date,
    code: str,
    category: str,
    side: str,
    quantity_units: int = 0,
    price_units: int = 0,
    amount_units: int = 0,
    cash_balance_units: int | None = None,
    line_no: int = 1,
    updated_at: datetime = BASE_UPDATED_AT,
    version: int = 1,
    trade_id: str | None = None,
) -> str:
    resolved_id = trade_id or f"trd_{uuid.uuid4().hex[:12]}"
    async with engine.begin() as conn:
        await conn.execute(
            text(INSERT_TRADE_SQL),
            {
                "id": resolved_id,
                "user_id": user_id,
                "trade_date": trade_date,
                "security_code": code,
                "security_name": code,
                "summary": category,
                "side": side,
                "quantity_units": quantity_units,
                "price_units": price_units,
                "amount_units": amount_units,
                "cash_balance_units": cash_balance_units,
                "cash_balance_available": cash_balance_units is not None,
                "category": category,
                "dedupe_key": f"{code}:{resolved_id}",
                "version": version,
                "line_no": line_no,
                "updated_at": updated_at,
            },
        )
    return resolved_id


async def _seed_baseline(
    engine: AsyncEngine, user_id: uuid.UUID, *, updated_at: datetime = BASE_UPDATED_AT
) -> dict[str, str]:
    """基准数据（长历史 >45 天）：2025-12-31 余额 100000；2026-03-02 买 600519 100 股。

    三条 `updated_at` 故意错开：只有**最后一条**等于水位线（06-01），这样「1 小时重叠窗」
    不会把全部旧行都当作脏行（真实 PG 里每行 updated_at 也各不相同）。
    """
    anchor = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2025, 12, 31),
        code="CASH",
        category="other",
        side="buy",
        amount_units=0,
        cash_balance_units=cents("100000.00"),
        line_no=1,
        updated_at=datetime.fromisoformat("2026-05-01T00:00:00+08:00"),
    )
    buy = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2026, 3, 2),
        code="600519",
        category="buy",
        side="buy",
        quantity_units=shares("100"),
        price_units=price("1700.00"),
        amount_units=-cents("170000.00"),
        cash_balance_units=-cents("70000.00"),
        line_no=2,
        updated_at=datetime.fromisoformat("2026-05-15T00:00:00+08:00"),
    )
    transfer = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2026, 6, 25),
        code="CASH",
        category="transfer",
        side="buy",
        amount_units=cents("5000.00"),
        line_no=3,
        updated_at=updated_at,
    )
    return {"anchor": anchor, "buy": buy, "transfer": transfer}


async def _update_trade(
    engine: AsyncEngine,
    trade_id: str,
    *,
    updated_at: datetime = LATER_UPDATED_AT,
    **changes: object,
) -> None:
    """修正流水：刷新 updated_at 且 version+1（与 ORM PATCH 行为一致）。"""
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    sql = (
        f"update ledger.trade set {assignments}, version = version + 1, updated_at = :updated_at "
        "where id = :id"
    )
    async with engine.begin() as conn:
        await conn.execute(text(sql), {**changes, "updated_at": updated_at, "id": trade_id})


async def _soft_delete(
    engine: AsyncEngine, trade_id: str, *, updated_at: datetime = LATER_UPDATED_AT
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "update ledger.trade set deleted_at = :at, version = version + 1, "
                "updated_at = :updated_at where id = :id"
            ),
            {"at": updated_at, "updated_at": updated_at, "id": trade_id},
        )


async def _restore(
    engine: AsyncEngine, trade_id: str, *, updated_at: datetime = LATER_UPDATED_AT
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "update ledger.trade set deleted_at = null, version = version + 1, "
                "updated_at = :updated_at where id = :id"
            ),
            {"updated_at": updated_at, "id": trade_id},
        )


# ── 构建 / 读取辅助 ───────────────────────────────────────


@pytest_asyncio.fixture
async def app_db(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings, role="app")
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
def replay_market(settings: Settings) -> Any:
    from stock_ledger.market import create_market_source

    return create_market_source(settings)


async def _build(
    db: Database,
    user_id: uuid.UUID,
    market_data: Any,
    *,
    clock: FrozenClock = CLOCK,
    mode: str = "auto",
) -> AssetBuildSummary:
    async with db.tenant_tx(user_id) as session:
        return await build_assets(
            session, user_id=user_id, clock=clock, market_data=market_data, mode=mode
        )


async def _rows(db: Database, user_id: uuid.UUID) -> list[AssetDaily]:
    async with db.tenant_tx(user_id) as session:
        return list(await session.scalars(select(AssetDaily).order_by(AssetDaily.trade_date.asc())))


async def _state(db: Database, user_id: uuid.UUID) -> AssetBuildState | None:
    async with db.tenant_tx(user_id) as session:
        return await session.get(AssetBuildState, user_id)


def _row_snapshot(rows: list[AssetDaily]) -> list[tuple[object, ...]]:
    return [tuple(getattr(row, field) for field in ROW_FIELDS) for row in rows]


def _state_snapshot(state: AssetBuildState | None) -> tuple[object, ...] | None:
    if state is None:
        return None
    return tuple(getattr(state, field) for field in STATE_FIELDS)


def _assert_paths_equal(
    rows_a: list[AssetDaily],
    rows_b: list[AssetDaily],
    state_a: AssetBuildState,
    state_b: AssetBuildState,
) -> None:
    assert _row_snapshot(rows_a) == _row_snapshot(rows_b)
    assert _state_snapshot(state_a) == _state_snapshot(state_b)


class _StubMarket:
    """可调 as_of 的桩行情：bars 截断到 as_of（模拟 live 覆盖边界）。"""

    def __init__(self, bars: list[Bar], as_of: date) -> None:
        self.bars = bars
        self.as_of = as_of

    async def get_daily_bars(self, symbols: list[str], start: date, end: date) -> list[Bar]:
        effective_end = min(end, self.as_of)
        if effective_end < start:
            return []
        return [
            bar
            for bar in self.bars
            if bar.symbol in symbols and start <= bar.trade_date <= effective_end
        ]


def _monthly_bars() -> list[Bar]:
    closes = {
        date(2026, 1, 30): "1600.00",
        date(2026, 2, 27): "1650.00",
        date(2026, 3, 31): "1680.00",
        date(2026, 4, 30): "1700.00",
        date(2026, 5, 29): "1705.00",
        date(2026, 6, 30): "1706.20",
    }
    return [
        Bar(symbol="600519", trade_date=day, close_units=price(close), return_units=0)
        for day, close in closes.items()
    ]


# ── IT-01：增量 == 全量 ───────────────────────────────────


async def test_incremental_equals_full(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    """旧数据 build → 追加新流水 → auto（增量）；与“一次性全量”逐行/state 相等。"""
    # 路径 A：一次性全量
    user_full = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_full)
    await _seed_trade(
        owner_engine,
        user_full,
        trade_date=date(2026, 6, 19),
        code="000001",
        category="buy",
        side="buy",
        quantity_units=shares("100"),
        price_units=price("12.40"),
        amount_units=-cents("1240.00"),
        line_no=4,
        updated_at=LATER_UPDATED_AT,
    )
    await _build(app_db, user_full, replay_market, mode="full")

    # 路径 B：旧数据 build → 追加（version=1）→ auto
    user_inc = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_inc)
    first = await _build(app_db, user_inc, replay_market)
    assert first.mode == "full"
    await _seed_trade(
        owner_engine,
        user_inc,
        trade_date=date(2026, 6, 19),
        code="000001",
        category="buy",
        side="buy",
        quantity_units=shares("100"),
        price_units=price("12.40"),
        amount_units=-cents("1240.00"),
        line_no=4,
        updated_at=LATER_UPDATED_AT,
    )
    second = await _build(app_db, user_inc, replay_market)
    assert second.mode == "incremental"
    assert second.rows_written < second.rows_total

    rows_a = await _rows(app_db, user_full)
    rows_b = await _rows(app_db, user_inc)
    state_a = await _state(app_db, user_full)
    state_b = await _state(app_db, user_inc)
    assert state_a is not None and state_b is not None
    _assert_paths_equal(rows_a, rows_b, state_a, state_b)

    # canary 1：只改 rebuilt_at → 仍相等（审计列被排除）。
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("update review.asset_daily set rebuilt_at = now() where user_id = :uid"),
            {"uid": user_full},
        )
    _assert_paths_equal(
        await _rows(app_db, user_full), rows_b, await _state(app_db, user_full), state_b
    )
    # canary 2：改一个业务字段 → 必须不等（证明比较有效）。
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "update review.asset_daily set daily_pnl_units = coalesce(daily_pnl_units, 0) + 1 "
                "where user_id = :uid and trade_date = :d"
            ),
            {"uid": user_full, "d": date(2026, 6, 30)},
        )
    with pytest.raises(AssertionError):
        _assert_paths_equal(
            await _rows(app_db, user_full), rows_b, await _state(app_db, user_full), state_b
        )


# ── IT-02：最小写入 ───────────────────────────────────────


async def test_incremental_minimal_window_writes(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    first = await _build(app_db, user_id, replay_market)
    assert first.mode == "full"
    rows_before = {row.trade_date: row.rebuilt_at for row in await _rows(app_db, user_id)}

    later = FrozenClock("2026-07-01T12:00:00+08:00")
    second = await _build(app_db, user_id, replay_market, clock=later)
    assert second.mode == "incremental"
    assert second.rebuild_from is not None
    rebuild_from = second.rebuild_from
    # 安全窗：end=07-01，窗口起点 max(start, end-44)=05-18。
    assert rebuild_from == date(2026, 5, 18)
    assert second.rows_written == (date(2026, 7, 1) - rebuild_from).days + 1

    rows_after = await _rows(app_db, user_id)
    # 窗口外的旧行存在且 rebuilt_at 未被改写。
    older = [row for row in rows_after if row.trade_date < rebuild_from]
    assert older
    for row in older:
        assert row.rebuilt_at == rows_before[row.trade_date]


# ── IT-03：no-op ─────────────────────────────────────────


async def test_repeated_build_is_noop(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)
    rows_first = await _rows(app_db, user_id)
    state_first = await _state(app_db, user_id)
    assert state_first is not None

    summary = await _build(app_db, user_id, replay_market)
    assert summary.mode == "noop"
    assert summary.rows_written == 0
    state_second = await _state(app_db, user_id)
    assert state_second is not None
    assert state_second.last_mode == "noop"
    assert state_second.last_rebuild_from is None
    assert state_second.last_rows_written == 0
    # 数据/水位线/指纹/rows_count 不变（version 与 updated_at 会变，属预期）。
    assert state_second.last_trade_updated_at == state_first.last_trade_updated_at
    assert state_second.last_trade_count == state_first.last_trade_count
    assert state_second.last_trade_version_sum == state_first.last_trade_version_sum
    assert state_second.last_built_through == state_first.last_built_through
    assert state_second.rows_count == state_first.rows_count
    assert _row_snapshot(await _rows(app_db, user_id)) == _row_snapshot(rows_first)


# ── IT-04 / IT-13：version>1（修正、日期改晚）→ 全量 ─────


async def test_version_gt_1_update_triggers_full_and_matches_full(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_inc = await _insert_user(owner_engine)
    ids = await _seed_baseline(owner_engine, user_inc)
    await _build(app_db, user_inc, replay_market)
    # 把 03-02 的买入改到更晚的 06-20（version=2）→ auto 必须全量。
    await _update_trade(owner_engine, ids["buy"], trade_date=date(2026, 6, 20))

    summary = await _build(app_db, user_inc, replay_market)
    assert summary.mode == "full"

    # 全量等价用户：同一最终数据。
    user_full = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_full,
        trade_date=date(2025, 12, 31),
        code="CASH",
        category="other",
        side="buy",
        amount_units=0,
        cash_balance_units=cents("100000.00"),
        line_no=1,
    )
    await _seed_trade(
        owner_engine,
        user_full,
        trade_date=date(2026, 6, 20),
        code="600519",
        category="buy",
        side="buy",
        quantity_units=shares("100"),
        price_units=price("1700.00"),
        amount_units=-cents("170000.00"),
        cash_balance_units=-cents("70000.00"),
        line_no=2,
    )
    await _seed_trade(
        owner_engine,
        user_full,
        trade_date=date(2026, 4, 10),
        code="CASH",
        category="transfer",
        side="buy",
        amount_units=cents("5000.00"),
        line_no=3,
    )
    await _build(app_db, user_full, replay_market, mode="full")
    # 修正路径的 version 已变为 2，不同用户的 version_sum 天然不同；因此与**同一用户**的
    # mode=full 结果比较（数据历史一致，才是“增量 == 全量”的严格口径）。
    auto_rows = await _rows(app_db, user_inc)
    auto_state = await _state(app_db, user_inc)
    await _build(app_db, user_inc, replay_market, mode="full")
    _assert_paths_equal(
        auto_rows,
        await _rows(app_db, user_inc),
        auto_state,
        await _state(app_db, user_inc),
    )  # type: ignore[arg-type]
    assert user_full == user_full


# ── IT-05：撤销 / 软删 / 恢复 ─────────────────────────────


async def test_soft_delete_and_restore_trigger_full(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    ids = await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)

    # 撤销买入（软删，version=2）→ 全量；不再有持仓。
    await _soft_delete(owner_engine, ids["buy"])
    deleted = await _build(app_db, user_id, replay_market)
    assert deleted.mode == "full"
    rows = await _rows(app_db, user_id)
    assert all(row.market_value_units == 0 for row in rows)
    assert all(row.holding_count == 0 for row in rows)

    # 恢复 → 全量；持仓重新出现。
    await _restore(owner_engine, ids["buy"])
    restored = await _build(app_db, user_id, replay_market)
    assert restored.mode == "full"
    last = (await _rows(app_db, user_id))[-1]
    assert last.holding_count == 1
    assert last.market_value_units > 0


# ── IT-06 / IT-17：行情 as_of 前进（修复 degraded 段）────


async def test_as_of_advance_repairs_uncovered_segment(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    bars = _monthly_bars()
    market = _StubMarket(bars, as_of=date(2026, 5, 31))

    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, market)
    before = await _rows(app_db, user_id)
    assert any(row.status == "degraded" for row in before if row.trade_date > market.as_of)
    june_30_before = next(row for row in before if row.trade_date == date(2026, 6, 30))
    assert june_30_before.status == "degraded"
    assert june_30_before.status_reason == MARKET_UNCOVERED_REASON

    # 行情前进但 end 不变 → 重算安全窗（非全量），修复 date > as_of 的 degraded 行。
    market.as_of = date(2026, 6, 30)
    repaired = await _build(app_db, user_id, market)
    assert repaired.mode == "incremental"
    after = await _rows(app_db, user_id)
    june_30_after = next(row for row in after if row.trade_date == date(2026, 6, 30))
    assert june_30_after.status == "ok"
    assert june_30_after.market_value_units != june_30_before.market_value_units

    # 与全量结果逐行相等。
    user_full = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_full)
    await _build(app_db, user_full, _StubMarket(bars, as_of=date(2026, 6, 30)), mode="full")
    _assert_paths_equal(
        after,
        await _rows(app_db, user_full),
        await _state(app_db, user_id),
        await _state(app_db, user_full),
    )  # type: ignore[arg-type]


async def test_as_of_span_over_safety_window_triggers_full(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    bars = _monthly_bars()
    market = _StubMarket(bars, as_of=date(2026, 4, 1))
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, market)

    # as_of 跨度 = 06-30 - 04-01 = 90 天 > 44 → 全量。
    market.as_of = date(2026, 6, 30)
    summary = await _build(app_db, user_id, market)
    assert summary.mode == "full"


# ── IT-11：end 回退 → 全量 ────────────────────────────────


async def test_clock_rollback_triggers_full_and_trims_rows(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)

    earlier = FrozenClock("2026-06-15T12:00:00+08:00")
    summary = await _build(app_db, user_id, replay_market, clock=earlier)
    assert summary.mode == "full"
    rows = await _rows(app_db, user_id)
    assert rows[-1].trade_date == date(2026, 6, 15)
    assert len(rows) == (date(2026, 6, 15) - date(2025, 12, 31)).days + 1
    state = await _state(app_db, user_id)
    assert state is not None
    assert state.last_built_through == date(2026, 6, 15)
    assert state.rows_count == len(rows)


# ── 回填（版本=1 的旧日期新流水）────────────────────────


async def test_backfill_before_first_balance_line_triggers_full(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    # start=2025-12-31 是买入（无余额），首个余额行在 2026-01-02。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2025, 12, 31),
        code="600519",
        category="buy",
        side="buy",
        quantity_units=shares("10"),
        price_units=price("1600.00"),
        amount_units=-cents("16000.00"),
        line_no=1,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 1, 2),
        code="CASH",
        category="other",
        side="buy",
        amount_units=0,
        cash_balance_units=cents("100000.00"),
        line_no=2,
    )
    short = FrozenClock("2026-01-10T12:00:00+08:00")
    await _build(app_db, user_id, replay_market, clock=short)

    # 在首个余额行之前回填一笔（version=1）→ back_solved 锚点变化 → 全量。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2025, 12, 31),
        code="CASH",
        category="transfer",
        side="buy",
        amount_units=cents("1000.00"),
        line_no=0,
        updated_at=LATER_UPDATED_AT,
    )
    summary = await _build(app_db, user_id, replay_market, clock=short)
    assert summary.mode == "full"


async def test_backfill_after_first_balance_line_is_incremental(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)

    # 回填一笔 05-01（首个余额行之后、45 天窗口之前）→ 从回填日起增量重算。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 5, 1),
        code="000001",
        category="buy",
        side="buy",
        quantity_units=shares("100"),
        price_units=price("12.40"),
        amount_units=-cents("1240.00"),
        line_no=4,
        updated_at=LATER_UPDATED_AT,
    )
    summary = await _build(app_db, user_id, replay_market)
    assert summary.mode == "incremental"
    assert summary.rebuild_from == date(2026, 5, 1)

    # 与全量等价（全量用户也含该回填）。
    user_full = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_full)
    await _seed_trade(
        owner_engine,
        user_full,
        trade_date=date(2026, 5, 1),
        code="000001",
        category="buy",
        side="buy",
        quantity_units=shares("100"),
        price_units=price("12.40"),
        amount_units=-cents("1240.00"),
        line_no=4,
    )
    await _build(app_db, user_full, replay_market, mode="full")
    _assert_paths_equal(
        await _rows(app_db, user_id),
        await _rows(app_db, user_full),
        await _state(app_db, user_id),
        await _state(app_db, user_full),
    )  # type: ignore[arg-type]


# ── FIFO 异常集合变化 → 全量 ─────────────────────────────


async def test_fifo_error_set_change_triggers_full(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)

    # 新增超卖卖出（version=1）→ 指纹变化但版本都是 1；FIFO 异常集合变化必须全量。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 6, 20),
        code="600519",
        category="sell",
        side="sell",
        quantity_units=shares("200"),
        price_units=price("1700.00"),
        amount_units=cents("340000.00"),
        line_no=4,
        updated_at=LATER_UPDATED_AT,
    )
    summary = await _build(app_db, user_id, replay_market)
    assert summary.mode == "full"
    rows = await _rows(app_db, user_id)
    # FIFO 超卖（600519 卖 200 > 持仓 100）只让**持有 600519 的日期段** degraded；
    # 纯现金日（2025-12-31 ~ 2026-03-01）与超卖清仓日（2026-06-20 ~ ）保持 ok。
    degraded = [r for r in rows if r.status == "degraded"]
    assert len(degraded) > 0, "持有 600519 的日子应 degraded"
    assert len(degraded) < len(rows), "纯现金日 / 清仓日不应因 600519 的 FIFO error 被连带 degraded"
    assert all(r.status_reason == FIFO_ERROR_REASON for r in degraded)


# ── IT-14：指纹竞态兜底 ──────────────────────────────────


async def test_fingerprint_changed_without_dirty_row_triggers_full(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)
    state = await _state(app_db, user_id)
    assert state is not None and state.last_trade_updated_at is not None
    stale = state.last_trade_updated_at - timedelta(hours=2)

    async def _no_dirty(
        self: AssetRepository, user_id_: uuid.UUID, *, since_updated_at: datetime
    ) -> list[Any]:
        return []

    # 仓储层构造「指纹变化但 updated_at 无变化」：计数变了、脏查询却为空 → 全量兜底。
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(AssetRepository, "dirty_trades", _no_dirty)
    try:
        await _seed_trade(
            owner_engine,
            user_id,
            trade_date=date(2026, 6, 15),
            code="000001",
            category="buy",
            side="buy",
            quantity_units=shares("10"),
            price_units=price("12.00"),
            amount_units=-cents("120.00"),
            line_no=4,
            updated_at=stale,
        )
        summary = await _build(app_db, user_id, replay_market)
    finally:
        monkeypatch.undo()
    assert summary.mode == "full"


# ── IT-08：失败原子性 / 状态 CAS ─────────────────────────


async def test_failure_rolls_back_rows_and_state(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)
    rows_before = _row_snapshot(await _rows(app_db, user_id))
    state_before = _state_snapshot(await _state(app_db, user_id))

    async def _boom(self: AssetRepository, rows: list[dict[str, object]]) -> None:
        raise RuntimeError("注入的写入失败")

    monkeypatch.setattr(AssetRepository, "insert_rows", _boom)
    # 先制造一次真实变更，确保 build 会走 delete+insert 路径。
    later = FrozenClock("2026-07-01T12:00:00+08:00")
    with pytest.raises(RuntimeError):
        await _build(app_db, user_id, replay_market, clock=later)
    monkeypatch.undo()

    assert _row_snapshot(await _rows(app_db, user_id)) == rows_before
    assert _state_snapshot(await _state(app_db, user_id)) == state_before

    # 重试成功（事务回滚后旧水位线完好，仍可增量）。
    summary = await _build(app_db, user_id, replay_market, clock=later)
    assert summary.mode == "incremental"


async def test_state_cas_conflict_raises_version_conflict(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)
    async with app_db.tenant_tx(user_id) as session:
        repo = AssetRepository(session)
        state = await repo.get_state(user_id)
        assert state is not None
        expected = state.version
        await repo.update_state_cas(user_id, expected_version=expected, last_mode="noop")
        with pytest.raises(ApiError) as exc:
            await repo.update_state_cas(user_id, expected_version=expected, last_mode="noop")
        assert exc.value.code is ErrorCode.VERSION_CONFLICT


# ── build.state 判定链 / 游标分页 ───────────────────────


async def test_list_assets_state_chain_and_pagination(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    await _build(app_db, user_id, replay_market)

    async with app_db.tenant_tx(user_id) as session:
        points, cursor, build = await list_assets(
            session,
            user_id=user_id,
            clock=CLOCK,
            market_data=replay_market,
            limit=10,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
        )
    assert build.state == "current"
    assert build.last_status == "ok"
    assert build.definition_version == "1.3"
    assert build.data_as_of == replay_market.as_of
    assert build.rows_count > 0
    assert [point.trade_date for point in points] == sorted(
        (point.trade_date for point in points), reverse=True
    )
    assert cursor is not None

    # 游标翻全集：无重无漏。
    seen: list[date] = [point.trade_date for point in points]
    while cursor is not None:
        async with app_db.tenant_tx(user_id) as session:
            page, cursor, _ = await list_assets(
                session,
                user_id=user_id,
                clock=CLOCK,
                market_data=replay_market,
                limit=10,
                cursor=cursor,
                date_from=None,
                date_to=None,
                status=None,
            )
        seen.extend(point.trade_date for point in page)
    assert len(seen) == len(set(seen)) == build.rows_count

    # 有新流水 → stale；重建后 current。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 6, 25),
        code="000001",
        category="buy",
        side="buy",
        quantity_units=shares("10"),
        price_units=price("12.00"),
        amount_units=-cents("120.00"),
        line_no=4,
        updated_at=LATER_UPDATED_AT,
    )
    async with app_db.tenant_tx(user_id) as session:
        _, _, stale_build = await list_assets(
            session,
            user_id=user_id,
            clock=CLOCK,
            market_data=replay_market,
            limit=10,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
        )
    assert stale_build.state == "stale"
    await _build(app_db, user_id, replay_market)
    async with app_db.tenant_tx(user_id) as session:
        _, _, current_build = await list_assets(
            session,
            user_id=user_id,
            clock=CLOCK,
            market_data=replay_market,
            limit=10,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
        )
    assert current_build.state == "current"

    # 未构建用户 → missing。
    fresh = await _insert_user(owner_engine)
    async with app_db.tenant_tx(fresh) as session:
        empty_points, _, missing_build = await list_assets(
            session,
            user_id=fresh,
            clock=CLOCK,
            market_data=replay_market,
            limit=10,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
        )
    assert empty_points == []
    assert missing_build.state == "missing"
    assert missing_build.rows_count == 0


async def test_global_blocked_state(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    # 只有买入、全部 cash_balance=NULL → 全局无锚点。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 6, 1),
        code="600519",
        category="buy",
        side="buy",
        quantity_units=shares("10"),
        price_units=price("1700.00"),
        amount_units=-cents("17000.00"),
    )
    summary = await _build(app_db, user_id, replay_market)
    assert summary.status == "blocked"
    assert await _rows(app_db, user_id) == []
    state = await _state(app_db, user_id)
    assert state is not None
    assert state.last_status == "blocked"
    assert state.last_built_through is None
    assert state.status_reason

    async with app_db.tenant_tx(user_id) as session:
        points, _, build = await list_assets(
            session,
            user_id=user_id,
            clock=CLOCK,
            market_data=replay_market,
            limit=10,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
        )
    assert points == []
    assert build.state == "blocked"
    assert build.status_reason


async def test_list_assets_missing_after_empty_build_hides_state_fields(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    """§7.1 可空性：无流水构建后 state 存在（last_mode/last_built_at 有值），

    但 GET /assets 的 build 块必须按 `missing` 展示：除 state/rows_count/last_mode/
    has_new_market_data/definition_version 外，last_* 与 assumption/status_reason/
    data_as_of 全部 null（不得透出 DB 里的构建摘要）。
    """
    user_id = await _insert_user(owner_engine)
    summary = await _build(app_db, user_id, replay_market)
    assert summary.mode == "full"
    assert summary.rows_written == 0

    # DB state 确实记录了一次 full 构建（with last_mode=full、last_built_at 有值）。
    state = await _state(app_db, user_id)
    assert state is not None
    assert state.last_mode == "full"
    assert state.last_built_at is not None
    assert state.last_built_through is None

    async with app_db.tenant_tx(user_id) as session:
        points, cursor, build = await list_assets(
            session,
            user_id=user_id,
            clock=CLOCK,
            market_data=replay_market,
            limit=10,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
        )
    assert points == []
    assert cursor is None
    assert build.state == "missing"
    # last_* 全 null / 0（missing 可空性）。
    assert build.last_status is None
    assert build.last_built_at is None
    assert build.last_built_through is None
    assert build.last_mode is None
    assert build.last_rebuild_from is None
    assert build.last_rows_written == 0
    assert build.rows_count == 0
    # 其余可空字段全 null；口径版本取当前/state 版本。
    assert build.assumption is None
    assert build.status_reason is None
    assert build.data_as_of is None
    assert build.has_new_market_data is False
    assert build.definition_version == "1.3"


# ── worker handler：progress / result / 取消 ─────────────


class _FakeQueue:
    def __init__(self, *, cancel: bool = False) -> None:
        self.progress_calls: list[tuple[int, str]] = []
        self.finished: tuple[JobStatus, dict[str, Any] | None] | None = None
        self._cancel = cancel

    async def progress(
        self, session: Any, job_id: str, *, percent: int, stage: str, message: str | None
    ) -> None:
        self.progress_calls.append((percent, stage))

    async def is_cancel_requested(self, session: Any, job_id: str) -> bool:
        return self._cancel

    async def finish(
        self,
        session: Any,
        job_id: str,
        *,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.finished = (status, result)


async def test_build_assets_job_result_keys_and_progress(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    queue = _FakeQueue()
    job = {"id": "job_assets_1", "user_id": str(user_id), "params": {"mode": "auto"}}
    async with app_db.tenant_tx(user_id) as session:
        result = await build_assets_job(queue, session, job, CLOCK, replay_market)  # type: ignore[arg-type]
    assert queue.finished is not None
    status, payload = queue.finished
    assert status is JobStatus.SUCCEEDED
    assert payload is not None
    assert set(payload) == {
        "mode",
        "rebuild_from",
        "rows_written",
        "rows_total",
        "built_through",
        "status",
        "assumption",
        "data_as_of",
    }
    assert payload["mode"] == "full"
    assert payload["built_through"] == "2026-06-30"
    assert result["job_id"] == "job_assets_1"
    # progress 阶段合法且单调递增。
    percents = [percent for percent, _ in queue.progress_calls]
    assert percents == sorted(percents)
    assert [stage for _, stage in queue.progress_calls] == ["recomputing", "writing", "finalizing"]


async def test_build_assets_job_cancel_before_write(
    owner_engine: AsyncEngine, app_db: Database, replay_market: Any
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_baseline(owner_engine, user_id)
    queue = _FakeQueue(cancel=True)
    job = {"id": "job_assets_2", "user_id": str(user_id), "params": {"mode": "auto"}}
    async with app_db.tenant_tx(user_id) as session:
        await build_assets_job(queue, session, job, CLOCK, replay_market)  # type: ignore[arg-type]
    assert queue.finished is not None
    status, payload = queue.finished
    assert status is JobStatus.CANCELLED
    assert payload == {"cancelled": True}
    assert await _rows(app_db, user_id) == []
