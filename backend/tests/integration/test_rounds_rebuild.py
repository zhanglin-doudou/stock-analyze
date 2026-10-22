"""回合重建 + 桥接恒等式集成测试（真实 PG + RLS）。

覆盖 INTERFACE §3.7：
- full rebuild：回合/明细数量、pnl/成本/收益率与手算值一致（写死期望，不由被测代码生成）；
- 幂等：重复 rebuild 的确定性 ID 与值不变；
- period 脏窗口：只重建受影响证券（窗口内活动 / 上次 rebuild 后修改 / 区间起点仍持仓），
  其他证券的回合原封不动；软删流水也要触发对应证券重算；
- 空数据 / 撤销后清空；
- 桥接：现金链闭合 → balanced=true、residual=0；缺期末余额 → degraded；
  无任何现金锚点 → blocked；inputs_hash 确定性。

造数一律用 owner（绕过 RLS），重建走 app 角色的 tenant 会话（RLS 真正生效）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_contracts.clock import FrozenClock
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_review.application.bridge import bridge_for_period
from stock_review.application.rebuild import (
    RebuildResult,
    build_round_id,
    rebuild_rounds_for_user,
)
from stock_review.application.rounds import get_round, list_round_fills, list_rounds
from stock_review.models import Round, RoundBuildState

pytestmark = [pytest.mark.integration]

CLOCK = FrozenClock("2026-03-31T12:00:00+08:00")

INSERT_TRADE_SQL = """
insert into ledger.trade (
    id, user_id, trade_date, security_code, security_name, summary, side,
    quantity_units, price_units, amount_units, commission_units, stamp_tax_units,
    other_fee_units, cash_balance_units, cash_balance_available, category,
    dedupe_key, occurrence_idx, source, version, line_no
) values (
    :id, :user_id, :trade_date, :security_code, :security_name, :summary, :side,
    :quantity_units, :price_units, :amount_units, 0, 0,
    0, :cash_balance_units, :cash_balance_available, :category,
    :dedupe_key, 0, 'import', 1, :line_no
)
"""


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
    side: str,
    quantity_units: int,
    price_units: int = 0,
    amount_units: int,
    category: str,
    summary: str = "",
    line_no: int = 1,
    cash_balance_units: int | None = None,
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
                "summary": summary,
                "side": side,
                "quantity_units": quantity_units,
                "price_units": price_units,
                "amount_units": amount_units,
                "cash_balance_units": cash_balance_units,
                "cash_balance_available": cash_balance_units is not None,
                "category": category,
                "dedupe_key": f"{code}:{resolved_id}",
                "line_no": line_no,
            },
        )
    return resolved_id


async def _soft_delete_trade(engine: AsyncEngine, trade_id: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("update ledger.trade set deleted_at = now() where id = :id"), {"id": trade_id}
        )


async def _touch_trade(engine: AsyncEngine, trade_id: str) -> None:
    """模拟窗口外修改：刷新 updated_at（不改业务值），触发脏窗口检测。"""
    async with engine.begin() as conn:
        await conn.execute(
            text("update ledger.trade set updated_at = now() where id = :id"), {"id": trade_id}
        )


@pytest_asyncio.fixture
async def app_db(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings, role="app")
    try:
        yield db
    finally:
        await db.dispose()


async def _rebuild(
    db: Database,
    user_id: uuid.UUID,
    *,
    scope: str = "full",
    period_start: date | None = None,
    period_end: date | None = None,
) -> RebuildResult:
    async with db.tenant_tx(user_id) as session:
        return await rebuild_rounds_for_user(
            session,
            user_id=user_id,
            clock=CLOCK,
            scope=scope,
            period_start=period_start,
            period_end=period_end,
        )


async def _load_rounds(db: Database, user_id: uuid.UUID) -> list[Round]:
    async with db.tenant_tx(user_id) as session:
        rows = await session.scalars(
            select(Round).order_by(Round.close_date.asc().nulls_last(), Round.id)
        )
        return list(rows)


async def _load_build_state(db: Database, user_id: uuid.UUID) -> RoundBuildState | None:
    async with db.tenant_tx(user_id) as session:
        return await session.get(RoundBuildState, user_id)


async def _seed_two_securities(engine: AsyncEngine, user_id: uuid.UUID) -> dict[str, str]:
    """A=600000 有 2025 买入 + 2026-03 卖出；B=000001 在 2023 全部平仓。"""
    a_buy = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2025, 6, 2),
        code="600000",
        side="buy",
        quantity_units=1_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        category="buy",
        summary="证券买入",
        line_no=1,
    )
    a_sell = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2026, 3, 10),
        code="600000",
        side="sell",
        quantity_units=400_000,
        price_units=12_000_000,
        amount_units=480_000,
        category="sell",
        summary="证券卖出",
        line_no=2,
    )
    b_buy = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2023, 6, 1),
        code="000001",
        side="buy",
        quantity_units=1_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        category="buy",
        summary="证券买入",
        line_no=3,
    )
    b_sell = await _seed_trade(
        engine,
        user_id,
        trade_date=date(2023, 6, 20),
        code="000001",
        side="sell",
        quantity_units=1_000_000,
        price_units=12_000_000,
        amount_units=1_200_000,
        category="sell",
        summary="证券卖出",
        line_no=4,
    )
    return {"a_buy": a_buy, "a_sell": a_sell, "b_buy": b_buy, "b_sell": b_sell}


# ── full rebuild ────────────────────────────────────────────


async def test_full_rebuild_matches_hand_computed_round(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    buy_id = await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 1, 5),
        code="600519",
        side="buy",
        quantity_units=10_000_000,  # 1000 股
        price_units=10_000_000,  # 10.00
        amount_units=-1_000_000,  # -10000.00 元
        category="buy",
        summary="证券买入",
        line_no=1,
    )
    sell_id = await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 1, 7),
        code="600519",
        side="sell",
        quantity_units=4_000_000,  # 400 股
        price_units=12_000_000,  # 12.00
        amount_units=480_000,  # +4800.00 元
        category="sell",
        summary="证券卖出",
        line_no=2,
    )

    result = await _rebuild(app_db, user_id)
    assert result.rounds_total == 2
    assert result.rounds_closed == 1
    assert result.rounds_open == 1
    assert result.fills == 1
    assert result.data_errors == 0
    assert result.securities == ("600519",)
    assert result.full is True
    assert result.window_from is None

    async with app_db.tenant_tx(user_id) as session:
        closed = await session.get(Round, build_round_id(user_id, "600519", f"{buy_id}:{sell_id}"))
        open_round = await session.get(Round, build_round_id(user_id, "600519", f"{buy_id}:open"))
    assert closed is not None and open_round is not None
    # 手算：卖出 400 股分摊成本 4000.00，现金 4800.00 → pnl 800.00；收益率 20%。
    assert closed.status == "closed"
    assert closed.quantity_units == 4_000_000
    assert closed.cost_units == 400_000
    assert closed.proceeds_units == 480_000
    assert closed.dividend_units == 0
    assert closed.pnl_units == 80_000
    assert closed.return_rate_units == 20_000_000
    assert closed.fills_count == 1
    assert closed.data_error is False
    # 期末未平仓：剩余 600 股、成本 6000.00。
    assert open_round.status == "open"
    assert open_round.close_trade_id is None
    assert open_round.quantity_units == 6_000_000
    assert open_round.cost_units == 600_000
    assert open_round.pnl_units == 0
    assert open_round.fills_count == 0

    state = await _load_build_state(app_db, user_id)
    assert state is not None
    assert state.last_scope == "full"
    assert state.rounds_count == 2
    assert state.last_trade_updated_at is not None

    # 明细行与回合拆分一致
    async with app_db.tenant_tx(user_id) as session:
        fills = await list_round_fills(session, user_id, closed.id)
    assert len(fills) == 1
    assert fills[0].buy_trade_id == buy_id
    assert fills[0].sell_trade_id == sell_id
    assert fills[0].quantity_units == 4_000_000
    assert fills[0].buy_cost_units == 400_000
    assert fills[0].sell_proceeds_units == 480_000
    assert fills[0].pnl_units == 80_000


async def test_repeated_full_rebuild_is_idempotent(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_two_securities(owner_engine, user_id)

    await _rebuild(app_db, user_id)
    first = await _load_rounds(app_db, user_id)
    first_snapshot = {
        row.id: (row.status, row.quantity_units, row.cost_units, row.pnl_units) for row in first
    }

    result = await _rebuild(app_db, user_id)
    second = await _load_rounds(app_db, user_id)
    second_snapshot = {
        row.id: (row.status, row.quantity_units, row.cost_units, row.pnl_units) for row in second
    }
    assert result.rounds_total == 3  # A closed + A open + B closed
    assert first_snapshot == second_snapshot


async def test_empty_data_clears_rounds_and_records_state(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    result = await _rebuild(app_db, user_id)
    assert result.rounds_total == 0
    assert result.securities == ()
    state = await _load_build_state(app_db, user_id)
    assert state is not None and state.rounds_count == 0

    buy_id = await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 1, 5),
        code="600519",
        side="buy",
        quantity_units=10_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        category="buy",
        line_no=1,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 1, 7),
        code="600519",
        side="sell",
        quantity_units=4_000_000,
        price_units=12_000_000,
        amount_units=480_000,
        category="sell",
        line_no=2,
    )
    await _rebuild(app_db, user_id)
    assert len(await _load_rounds(app_db, user_id)) == 2

    # 撤销：软删全部流水 → full rebuild 清空回合（回合表不能残留“幽灵持仓”）。
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("update ledger.trade set deleted_at = now() where user_id = :uid"),
            {"uid": user_id},
        )
    result = await _rebuild(app_db, user_id)
    assert result.rounds_total == 0
    assert await _load_rounds(app_db, user_id) == []
    assert buy_id  # 造数已生效（避免 F841）


# ── period 脏窗口 ───────────────────────────────────────────


async def test_period_rebuild_only_touches_affected_securities(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    ids = await _seed_two_securities(owner_engine, user_id)
    await _rebuild(app_db, user_id)
    before = {
        row.id: (row.security_code, row.pnl_units) for row in await _load_rounds(app_db, user_id)
    }
    b_round_ids = [rid for rid, (code, _) in before.items() if code == "000001"]
    assert len(b_round_ids) == 1

    # A 在区间内新增一笔买入（会形成新的 open 回合）。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 20),
        code="600000",
        side="buy",
        quantity_units=500_000,
        price_units=12_000_000,
        amount_units=-600_000,
        category="buy",
        line_no=5,
    )

    result = await _rebuild(
        app_db, user_id, scope="period", period_start=date(2026, 3, 1), period_end=date(2026, 3, 31)
    )
    assert result.scope == "period"
    assert result.full is False
    assert result.window_from == date(2025, 1, 25)  # 2026-03-01 回看 400 天
    assert result.window_to == date(2026, 3, 31)
    # 只重建 A；B（2023 年、已全部平仓、窗口外）不动。
    assert result.securities == ("600000",)

    after = {
        row.id: (row.security_code, row.pnl_units) for row in await _load_rounds(app_db, user_id)
    }
    for round_id in b_round_ids:
        assert after[round_id] == before[round_id]
    assert len(after) == len(before) + 1
    assert ids["a_sell"]

    # 同参数重跑：ID 与值不变（确定性 + 幂等）。
    rerun = await _rebuild(
        app_db, user_id, scope="period", period_start=date(2026, 3, 1), period_end=date(2026, 3, 31)
    )
    assert rerun.securities == ("600000",)
    after_rerun = {
        row.id: (row.security_code, row.pnl_units) for row in await _load_rounds(app_db, user_id)
    }
    assert after_rerun == after


async def test_period_rebuild_picks_up_updated_trade_outside_window(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    ids = await _seed_two_securities(owner_engine, user_id)
    await _rebuild(app_db, user_id)

    # 修改窗口之外的旧流水（2023 年 B 的买入）→ 该证券必须进入脏窗口。
    await _touch_trade(owner_engine, ids["b_buy"])
    result = await _rebuild(
        app_db, user_id, scope="period", period_start=date(2026, 3, 1), period_end=date(2026, 3, 31)
    )
    assert result.securities == ("000001", "600000")

    # marker 已前移：下一次 period rebuild 不再把 B 当脏（避免每次全量重算）。
    rerun = await _rebuild(
        app_db, user_id, scope="period", period_start=date(2026, 3, 1), period_end=date(2026, 3, 31)
    )
    assert rerun.securities == ("600000",)


async def test_period_rebuild_recomputes_soft_deleted_trade(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    ids = await _seed_two_securities(owner_engine, user_id)
    await _rebuild(app_db, user_id)
    assert len(await _load_rounds(app_db, user_id)) == 3

    # 撤销区间内的卖出 → A 的 closed 回合必须消失（软删流水所在的证券要脏）。
    await _soft_delete_trade(owner_engine, ids["a_sell"])
    result = await _rebuild(
        app_db, user_id, scope="period", period_start=date(2026, 3, 1), period_end=date(2026, 3, 31)
    )
    assert "600000" in result.securities
    rounds = await _load_rounds(app_db, user_id)
    a_rounds = [row for row in rounds if row.security_code == "600000"]
    assert [row.status for row in a_rounds] == ["open"]


# ── 查询应用层（分页 / 详情 / 明细）────────────────────────────


async def test_list_get_and_fills_rounds(owner_engine: AsyncEngine, app_db: Database) -> None:
    user_id = await _insert_user(owner_engine)
    ids = await _seed_two_securities(owner_engine, user_id)
    await _rebuild(app_db, user_id)

    async with app_db.tenant_tx(user_id) as session:
        first_page, cursor = await list_rounds(session, user_id, limit=1)
        assert len(first_page) == 1
        assert cursor is not None
        second_page, next_cursor = await list_rounds(session, user_id, limit=1, cursor=cursor)
        assert len(second_page) == 1
        assert next_cursor is not None
        # closed 回合（有 close_date）排在 open 前面。
        assert first_page[0].close_date is not None

        closed_dto = first_page[0]
        assert closed_dto.round_id == closed_dto.id
        detail = await get_round(session, user_id, closed_dto.id)
        assert detail.id == closed_dto.id
        assert detail.pnl_units == closed_dto.pnl_units
        fills = await list_round_fills(session, user_id, closed_dto.id)
        assert fills and fills[0].round_id == closed_dto.id
        assert fills[0].fill_id == fills[0].id

        with pytest.raises(NotFoundError):
            await get_round(session, user_id, "rnd_not_exists")
        with pytest.raises(NotFoundError):
            await list_round_fills(session, user_id, "rnd_not_exists")
        with pytest.raises(ApiError) as exc:
            await list_rounds(session, user_id, cursor="not-a-cursor")
        assert exc.value.code is ErrorCode.INVALID_CURSOR
        assert ids["a_buy"]


# ── 桥接恒等式 ──────────────────────────────────────────────


async def test_bridge_balanced_identity_and_deterministic_hash(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 2, 20),
        code="600519",
        side="buy",
        quantity_units=10_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        cash_balance_units=9_000_000,
        category="buy",
        line_no=1,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 5),
        code="600519",
        side="sell",
        quantity_units=4_000_000,
        price_units=12_000_000,
        amount_units=480_000,
        cash_balance_units=9_480_000,
        category="sell",
        line_no=2,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 10),
        code="600519",
        side="buy",
        quantity_units=0,
        amount_units=50_000,
        cash_balance_units=9_530_000,
        category="dividend",
        summary="红利入账",
        line_no=3,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 15),
        code="600519",
        side="buy",
        quantity_units=0,
        amount_units=-10_000,
        cash_balance_units=9_520_000,
        category="fee",
        summary="股息红利税",
        line_no=4,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 20),
        code="600519",
        side="buy",
        quantity_units=0,
        amount_units=100_000,
        cash_balance_units=9_620_000,
        category="transfer",
        summary="银行转证券",
        line_no=5,
    )

    async with app_db.tenant_tx(user_id) as session:
        result = await bridge_for_period(
            session,
            user_id=user_id,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            clock=CLOCK,
        )
        again = await bridge_for_period(
            session,
            user_id=user_id,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            clock=CLOCK,
        )

    assert result.period == "2026-03"
    assert result.status == "ok"
    assert result.balanced is True
    assert result.residual_units == 0
    # 期初现金 = 区间前最后一条余额；期初持仓成本 = 1000 股成本。
    assert result.initial_cash_units == 9_000_000
    assert result.initial_open_cost_units == 1_000_000
    # 区间分类：卖出 400 股 pnl 800.00、股息净额 500.00、费用 100.00、转账 1000.00。
    assert result.realized_pnl_units == 80_000
    assert result.dividends_units == 50_000
    assert result.fees_units == 10_000
    assert result.cashflows_units == 100_000
    assert result.other_units == 0
    # 期末：现金 9620.00 + 未平仓成本 6000.00。
    assert result.ending_value_units == 10_220_000
    assert result.open_cost_units == 600_000
    assert result.tolerance_units == 1
    assert result.data_as_of == CLOCK.now()
    assert result.inputs_hash == again.inputs_hash


async def test_bridge_derived_ending_cash_is_degraded(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 2, 20),
        code="600519",
        side="buy",
        quantity_units=10_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        cash_balance_units=9_000_000,
        category="buy",
        line_no=1,
    )
    # 区间内没有余额行：期末现金只能按现金流推导 → degraded。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 5),
        code="600519",
        side="buy",
        quantity_units=0,
        amount_units=100_000,
        category="transfer",
        summary="银行转证券",
        line_no=2,
    )
    async with app_db.tenant_tx(user_id) as session:
        result = await bridge_for_period(
            session,
            user_id=user_id,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            clock=CLOCK,
        )
    assert result.status == "degraded"
    assert result.cashflows_units == 100_000
    assert result.ending_value_units == 9_100_000 + 1_000_000
    assert result.balanced is True


async def test_bridge_without_cash_balance_is_blocked(
    owner_engine: AsyncEngine, app_db: Database
) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 5),
        code="600519",
        side="buy",
        quantity_units=1_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        category="buy",
        line_no=1,
    )
    async with app_db.tenant_tx(user_id) as session:
        result = await bridge_for_period(
            session,
            user_id=user_id,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            clock=CLOCK,
        )
    assert result.status == "blocked"
    assert result.balanced is False
    assert result.residual_units == 0
    assert result.ending_value_units == 0
    assert isinstance(result.data_as_of, datetime)


async def test_bridge_other_categories_degrade(owner_engine: AsyncEngine, app_db: Database) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 2, 20),
        code="600519",
        side="buy",
        quantity_units=10_000_000,
        price_units=10_000_000,
        amount_units=-1_000_000,
        cash_balance_units=9_000_000,
        category="buy",
        line_no=1,
    )
    # 打新冻结/解冻等未分类现金流（category=new_stock）只能整体进 other 并降级。
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 3, 6),
        code="CASH",
        side="buy",
        quantity_units=0,
        amount_units=-20_000,
        cash_balance_units=8_980_000,
        category="new_stock",
        summary="新股申购",
        line_no=2,
    )
    async with app_db.tenant_tx(user_id) as session:
        result = await bridge_for_period(
            session,
            user_id=user_id,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            clock=CLOCK,
        )
    assert result.status == "degraded"
    assert result.other_units == -20_000


# ── worker job handler（入队 → claim → handler → finish）───────────────


async def test_rebuild_rounds_job_runs_through_worker(
    owner_engine: AsyncEngine, app_db: Database, settings: Settings
) -> None:
    """`rebuild_rounds` handler 端到端：JobQueue 入队 → WorkerRunner 领取执行 → 成功收口。"""
    from stock_platform.jobs import JobQueue
    from stock_worker.runner import WorkerRunner

    user_id = await _insert_user(owner_engine)
    await _seed_two_securities(owner_engine, user_id)

    queue = JobQueue(app_db, CLOCK)
    async with app_db.tenant_tx(user_id) as session:
        job_id, reused = await queue.enqueue(
            session,
            kind="rebuild_rounds",
            user_id=user_id,
            scope_key=f"{user_id}:full",
            params={"scope": "full", "reason": "test"},
        )
    assert reused is False

    runner = WorkerRunner(settings)
    try:
        claimed = await runner._claim_one()
        assert claimed is not None
        assert claimed["kind"] == "rebuild_rounds"
        await runner._execute(claimed)
    finally:
        await runner.close()

    async with app_db.tenant_tx(user_id) as session:
        job = await queue.get_for_user(session, user_id, job_id)
    assert job is not None
    assert job.status == "succeeded"
    assert job.result is not None
    assert job.result["scope"] == "full"
    assert job.result["rounds_total"] == 3
    # worker 结果里的日期是 JSON 友好的 isoformat 字符串（不能是 date 对象）。
    assert isinstance(job.result["rebuilt_at"], str)

    rows = await _load_rounds(app_db, user_id)
    assert len(rows) == 3
