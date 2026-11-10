"""API-095 验收：`GET /dashboard/trade-flags` 账单旗标月度聚合。

覆盖：
* happy path：6 个完整月（2026-01~2026-06）的买卖笔数与六个状态旗标命中数；
* 月边界：12/24 个月跨年正确；
* 未构建月份显式 0 + `months_without_data`；无任何数据时 `has_data=false`；
* 单月收益（additive）：资产日表日收益按月复利 → 基点整数/符号/展示串全由后端给；
  无资产行月份显式 0 高度且 `available=false`，不把「无数据」伪装成 0 收益；
* 参数非法 422（months 非 6/12/24）；无 token 401；admin 403；
* 跨用户隔离：B 看不到 A 的计数；
* 只读：调用前后 `review.trade_flag` 行数不变。

时钟用 FrozenClock 固定到 2026-07-15（东八区），最新完整月 = 2026-06。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_api.app import create_app
from stock_contracts.clock import FrozenClock
from stock_core.trade_flags import DEFINITION_VERSION

pytestmark = pytest.mark.api

API_PREFIX = "/api/v1"
_CLOCK = FrozenClock("2026-07-15T09:00:00+08:00")
_ASSET_DEFINITION_VERSION = "1.7"

_INSERT_ASSET_DAILY_SQL = """
insert into review.asset_daily (
    id, user_id, trade_date, cash_units, market_value_units, total_assets_units,
    holding_cost_units, unrealized_pnl_units, holding_count, cashflow_units,
    prev_total_assets_units, daily_pnl_units, daily_return_units,
    status, status_reason, definition_version, rebuilt_at
) values (
    :id, :uid, :d, 0, :total, :total, 0, 0, 1, :cashflow,
    :prev, :pnl, :ret, :status, :reason, :version, now()
)
"""

_INSERT_ASSET_STATE_SQL = """
insert into review.asset_build_state (
    user_id, start_date, last_built_through, last_built_at, data_as_of,
    last_status, last_mode, definition_version, rows_count, last_rows_written, version
) values (
    :uid, :start, :through, now(), :through,
    'ok', 'full', :version, :rows, :rows, 1
)
"""

_INSERT_FLAG_SQL = """
insert into review.trade_flag (
    id, user_id, trade_dedupe_key, trade_id, security_code, side, trade_date,
    flag_code, status, reason, lookahead, definition_version, computed_at
) values (
    :id, :user_id, :dedupe_key, :trade_id, :security_code, :side, :trade_date,
    :flag_code, :status, :reason, :lookahead, :definition_version, :computed_at
)
"""


@pytest_asyncio.fixture
async def flags_client(settings: Any) -> Any:
    app = create_app(settings, clock=_CLOCK)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _seed_flag(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    *,
    trade_date: date,
    dedupe_key: str,
    code: str,
    status: str,
    side: str,
    security_code: str = "600519",
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(_INSERT_FLAG_SQL),
            {
                "id": f"tfl_{uuid.uuid4().hex[:32]}",
                "user_id": user_id,
                "dedupe_key": dedupe_key,
                "trade_id": f"trd_{dedupe_key}",
                "security_code": security_code,
                "side": side,
                "trade_date": trade_date,
                "flag_code": code,
                "status": status,
                "reason": None,
                "lookahead": code in ("short_term_win", "short_term_top"),
                "definition_version": DEFINITION_VERSION,
                "computed_at": datetime(2026, 7, 1, tzinfo=UTC),
            },
        )


def _item(payload: dict[str, Any], month: str) -> dict[str, Any]:
    return next(item for item in payload["items"] if item["month"] == month)


def _flag_counts(item: dict[str, Any]) -> dict[str, int]:
    return {row["code"]: row["count"] for row in item["flags"]}


def _state_counts(item: dict[str, Any]) -> dict[str, int]:
    return {row["code"]: row["count"] for row in item["states"]}


async def _seed_asset_days(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    rows: list[tuple[date, int | None, int, int | None, str, str]],
) -> None:
    """直插资产日行。

    rows=(trade_date, daily_return_units, cashflow_units, daily_pnl_units, status, version)。
    `daily_pnl_units` 为 None 表示 blocked 行（无资产值）。
    """
    async with engine.begin() as conn:
        for trade_date, return_units, cashflow_units, pnl_units, status, version in rows:
            await conn.execute(
                text(_INSERT_ASSET_DAILY_SQL),
                {
                    "id": f"ast_{uuid.uuid4().hex[:32]}",
                    "uid": user_id,
                    "d": trade_date,
                    "total": 10_000_000,
                    "prev": None if return_units is None else 10_000_000,
                    "pnl": pnl_units,
                    "ret": return_units,
                    "cashflow": cashflow_units,
                    "status": status,
                    "reason": "测试阻断" if status == "blocked" else None,
                    "version": version,
                },
            )


async def _seed_asset_state(
    engine: AsyncEngine, user_id: uuid.UUID, *, through: date, rows: int
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(_INSERT_ASSET_STATE_SQL),
            {
                "uid": user_id,
                "start": date(2026, 1, 1),
                "through": through,
                "version": _ASSET_DEFINITION_VERSION,
                "rows": rows,
            },
        )


async def _seed_six_month_fixture(engine: AsyncEngine, user_id: uuid.UUID) -> None:
    """2026-06：3 买 2 卖；2026-05：1 买（未命中任何旗标）。

    * 买入侧：b1 均价加仓、b2 抄底加仓、b3 首次建仓 → 首次建仓 + 三加仓合计 3 = 3 笔；
    * 卖出侧：s1 止盈减仓、s2 回本清仓 → 减仓三态 + 清仓三态合计 2 = 2 笔。
    """
    rows = [
        (date(2026, 6, 3), "b1", "chase", "hit", "buy"),
        (date(2026, 6, 3), "b1", "instant_bottom", "hit", "buy"),
        (date(2026, 6, 3), "b1", "short_term_win", "pending", "buy"),
        (date(2026, 6, 3), "b1", "first_entry", "miss", "buy"),
        (date(2026, 6, 3), "b1", "addon_average", "hit", "buy"),
        (date(2026, 6, 3), "b1", "addon_dip", "miss", "buy"),
        (date(2026, 6, 3), "b1", "addon_chase", "miss", "buy"),
        (date(2026, 6, 10), "b2", "chase", "hit", "buy"),
        (date(2026, 6, 10), "b2", "instant_bottom", "miss", "buy"),
        (date(2026, 6, 10), "b2", "first_entry", "miss", "buy"),
        (date(2026, 6, 10), "b2", "addon_average", "miss", "buy"),
        (date(2026, 6, 10), "b2", "addon_dip", "hit", "buy"),
        (date(2026, 6, 10), "b2", "addon_chase", "miss", "buy"),
        (date(2026, 6, 15), "b3", "first_entry", "hit", "buy"),
        (date(2026, 6, 15), "b3", "addon_average", "not_applicable", "buy"),
        (date(2026, 6, 15), "b3", "addon_dip", "not_applicable", "buy"),
        (date(2026, 6, 15), "b3", "addon_chase", "not_applicable", "buy"),
        (date(2026, 6, 20), "s1", "stop_loss", "hit", "sell"),
        (date(2026, 6, 20), "s1", "instant_top", "miss", "sell"),
        (date(2026, 6, 20), "s1", "short_term_top", "hit", "sell"),
        (date(2026, 6, 20), "s1", "reduce_breakeven", "miss", "sell"),
        (date(2026, 6, 20), "s1", "reduce_profit", "hit", "sell"),
        (date(2026, 6, 20), "s1", "reduce_loss", "miss", "sell"),
        (date(2026, 6, 20), "s1", "full_exit", "miss", "sell"),
        (date(2026, 6, 20), "s1", "full_exit_breakeven", "not_applicable", "sell"),
        (date(2026, 6, 20), "s1", "full_exit_profit", "not_applicable", "sell"),
        (date(2026, 6, 20), "s1", "full_exit_loss", "not_applicable", "sell"),
        (date(2026, 6, 25), "s2", "reduce_breakeven", "not_applicable", "sell"),
        (date(2026, 6, 25), "s2", "reduce_profit", "not_applicable", "sell"),
        (date(2026, 6, 25), "s2", "reduce_loss", "not_applicable", "sell"),
        (date(2026, 6, 25), "s2", "full_exit", "hit", "sell"),
        (date(2026, 6, 25), "s2", "full_exit_breakeven", "hit", "sell"),
        (date(2026, 6, 25), "s2", "full_exit_profit", "miss", "sell"),
        (date(2026, 6, 25), "s2", "full_exit_loss", "miss", "sell"),
        (date(2026, 5, 8), "b0", "chase", "miss", "buy"),
    ]
    for trade_date, dedupe_key, code, status, side in rows:
        await _seed_flag(
            engine,
            user_id,
            trade_date=trade_date,
            dedupe_key=dedupe_key,
            code=code,
            status=status,
            side=side,
        )


async def test_api_095_happy_path_monthly_counts(
    flags_client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    account = await account_factory()
    await _seed_six_month_fixture(owner_engine, account.user_id)

    resp = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=account.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["window_months"] == 6
    assert body["start_month"] == "2026-01"
    assert body["end_month"] == "2026-06"
    assert body["latest_complete_month"] == "2026-06"
    assert body["definition_version"] == DEFINITION_VERSION
    assert body["has_data"] is True
    assert body["has_flag_data"] is True
    assert body["has_state_data"] is True
    assert [row["code"] for row in body["series"]] == [
        "chase",
        "instant_bottom",
        "short_term_win",
        "stop_loss",
        "instant_top",
        "short_term_top",
    ]
    assert [row["side"] for row in body["series"]] == ["buy", "buy", "buy", "sell", "sell", "sell"]
    assert body["total_labels"] == {"buy": "买入笔数", "sell": "卖出笔数"}
    assert [row["lookahead"] for row in body["series"]] == [
        False,
        False,
        True,
        False,
        False,
        True,
    ]
    # 柱状图口径：买侧首次建仓 + 三加仓；卖侧减仓三态 + 清仓三态（均不是事后指标）。
    assert [row["code"] for row in body["state_series"]] == [
        "first_entry",
        "addon_average",
        "addon_dip",
        "addon_chase",
        "reduce_breakeven",
        "reduce_profit",
        "reduce_loss",
        "full_exit_breakeven",
        "full_exit_profit",
        "full_exit_loss",
    ]
    assert [row["side"] for row in body["state_series"]] == [
        "buy",
        "buy",
        "buy",
        "buy",
        "sell",
        "sell",
        "sell",
        "sell",
        "sell",
        "sell",
    ]
    assert all(row["lookahead"] is False for row in body["state_series"])
    assert [row["label_zh"] for row in body["state_series"]] == [
        "首次建仓",
        "均价加仓",
        "抄底加仓",
        "追涨加仓",
        "回本减仓",
        "止盈减仓",
        "止损减仓",
        "回本清仓",
        "止盈清仓",
        "止损清仓",
    ]
    assert [item["month"] for item in body["items"]] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
    ]
    assert body["months_without_data"] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    # 单月收益 additive：没有资产行时显式 0 高度，但 available=false（不伪造 0 收益）。
    assert body["has_return_data"] is False
    assert body["return_labels"] == {"pos": "正收益", "neg": "负收益", "none": "无数据"}
    assert body["return_metric_labels"] == {"return": "收益率", "amount": "金额"}
    assert body["return_basis"]
    assert all(item["monthly_return"]["value_bp"] == 0 for item in body["items"])
    assert all(item["monthly_return"]["available"] is False for item in body["items"])
    assert all(item["monthly_return"]["sign"] == "none" for item in body["items"])
    assert all(item["monthly_return"]["pct"] is None for item in body["items"])

    june = _item(body, "2026-06")
    assert june["buy_count"] == 3
    assert june["sell_count"] == 2
    assert _flag_counts(june) == {
        "chase": 2,
        "instant_bottom": 1,
        "short_term_win": 0,
        "stop_loss": 1,
        "instant_top": 0,
        "short_term_top": 1,
    }
    # 互斥状态：b1 均价、b2 抄底、b3 首次建仓；s1 止盈减仓、s2 回本清仓。
    assert _state_counts(june) == {
        "first_entry": 1,
        "addon_average": 1,
        "addon_dip": 1,
        "addon_chase": 0,
        "reduce_breakeven": 0,
        "reduce_profit": 1,
        "reduce_loss": 0,
        "full_exit_breakeven": 1,
        "full_exit_profit": 0,
        "full_exit_loss": 0,
    }
    states = _state_counts(june)
    # 两侧叠放合计必须严格等于该侧真实笔数（无残余）。
    assert (
        sum(states[code] for code in ("first_entry", "addon_average", "addon_dip", "addon_chase"))
        == june["buy_count"]
    )
    assert (
        sum(
            states[code]
            for code in (
                "reduce_breakeven",
                "reduce_profit",
                "reduce_loss",
                "full_exit_breakeven",
                "full_exit_profit",
                "full_exit_loss",
            )
        )
        == june["sell_count"]
    )

    may = _item(body, "2026-05")
    assert may["buy_count"] == 1
    assert may["sell_count"] == 0
    assert sum(_flag_counts(may).values()) == 0
    assert sum(_state_counts(may).values()) == 0


@pytest.mark.parametrize(
    ("months", "start", "end", "expected_len"),
    [(12, "2025-07", "2026-06", 12), (24, "2024-07", "2026-06", 24)],
)
async def test_api_095_window_boundaries_cross_year(
    flags_client: httpx.AsyncClient,
    account_factory: Any,
    months: int,
    start: str,
    end: str,
    expected_len: int,
) -> None:
    account = await account_factory()
    resp = await flags_client.get(
        f"{API_PREFIX}/dashboard/trade-flags", params={"months": months}, headers=account.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_months"] == months
    assert body["start_month"] == start
    assert body["end_month"] == end
    assert len(body["items"]) == expected_len
    assert body["has_data"] is False
    assert body["has_flag_data"] is False
    assert body["has_state_data"] is False


async def test_api_095_isolation_and_read_only(
    flags_client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    alice = await account_factory()
    bob = await account_factory()
    await _seed_six_month_fixture(owner_engine, alice.user_id)

    async with owner_engine.connect() as conn:
        before = await conn.scalar(text("select count(*) from review.trade_flag"))

    bob_resp = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=bob.headers)
    assert bob_resp.status_code == 200
    bob_body = bob_resp.json()
    assert bob_body["has_data"] is False
    assert all(item["buy_count"] == 0 and item["sell_count"] == 0 for item in bob_body["items"])

    async with owner_engine.connect() as conn:
        after = await conn.scalar(text("select count(*) from review.trade_flag"))
    assert before == after


async def test_api_095_rejects_unknown_months(
    flags_client: httpx.AsyncClient, account_factory: Any
) -> None:
    account = await account_factory()
    resp = await flags_client.get(
        f"{API_PREFIX}/dashboard/trade-flags", params={"months": 7}, headers=account.headers
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_api_095_requires_auth_and_blocks_admin(
    flags_client: httpx.AsyncClient,
    account_factory: Any,
) -> None:
    unauth = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags")
    assert unauth.status_code == 401

    admin = await account_factory(role="admin")
    forbidden = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=admin.headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "ADMIN_CANNOT_READ_TRADES"


async def test_api_095_monthly_return_compounds_daily_twr(
    flags_client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    """两个 +1% 交易日 → +2.01%（复利），符号/百分比/展示串全部由后端给出。"""
    account = await account_factory()
    await _seed_asset_days(
        owner_engine,
        account.user_id,
        [
            (date(2026, 6, 1), 1_000_000, 0, 10_000, "ok", _ASSET_DEFINITION_VERSION),
            (date(2026, 6, 2), 1_000_000, 0, 10_000, "ok", _ASSET_DEFINITION_VERSION),
        ],
    )
    await _seed_asset_state(owner_engine, account.user_id, through=date(2026, 6, 30), rows=2)

    resp = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=account.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["has_return_data"] is True
    assert body["return_labels"] == {"pos": "正收益", "neg": "负收益", "none": "无数据"}

    june = _item(body, "2026-06")["monthly_return"]
    assert june == {
        "month": "2026-06",
        "value_bp": 201,
        "pct": "2.01%",
        "display": "+2.01%",
        "sign": "pos",
        "available": True,
        "days": 2,
        "partial": False,
        "note": None,
        "amount": "200.00",
        "amount_display": "200.00",
    }

    # 无资产行月份：柱高显式 0，但数值为 null、sign=none（禁止伪装成 0 收益/0 金额）。
    may = _item(body, "2026-05")["monthly_return"]
    assert may["value_bp"] == 0
    assert may["available"] is False
    assert may["pct"] is None
    assert may["display"] is None
    assert may["sign"] == "none"
    assert may["amount"] is None
    assert may["amount_display"] is None
    assert may["note"] == "当月无可用资产日收益"


async def test_api_095_monthly_return_negative_excludes_blocked_and_other_version(
    flags_client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    """-2% × +1% = -1.02%；blocked 行与非当前口径版本的行不参与复利；覆盖不足标 partial。"""
    account = await account_factory()
    await _seed_asset_days(
        owner_engine,
        account.user_id,
        [
            (date(2026, 6, 1), -2_000_000, 0, -20_000, "ok", _ASSET_DEFINITION_VERSION),
            (date(2026, 6, 2), 1_000_000, 0, 10_000, "ok", _ASSET_DEFINITION_VERSION),
            (date(2026, 6, 3), None, 0, None, "blocked", _ASSET_DEFINITION_VERSION),
            (date(2026, 6, 4), 50_000_000, 0, 500_000, "ok", "0.9"),
        ],
    )
    await _seed_asset_state(owner_engine, account.user_id, through=date(2026, 6, 15), rows=4)

    resp = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=account.headers)
    assert resp.status_code == 200, resp.text
    june = _item(resp.json(), "2026-06")["monthly_return"]

    assert june["days"] == 2
    assert june["value_bp"] == -102
    assert june["pct"] == "-1.02%"
    assert june["display"] == "-1.02%"
    assert june["sign"] == "neg"
    assert june["available"] is True
    assert june["partial"] is True
    # blocked 行与非当前口径版本行不参与金额累加，只剩两个可用日：-200.00 + 100.00。
    assert june["amount"] == "-100.00"
    assert june["amount_display"] == "-100.00"
    assert june["note"] == "资产表未覆盖整月，收益与金额均为已覆盖区间"


async def test_api_095_monthly_return_amount_excludes_cashflow(
    flags_client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    """收益额 = 每日盈亏累加（已剔除出入金），分红等投资损益仍计入；无数据月为 null。"""
    account = await account_factory()
    await _seed_asset_days(
        owner_engine,
        account.user_id,
        [
            # 2026-06：收益 +1% / 0%；当日入金 5000.00 必须被剔除 → 金额仅 300.00。
            (date(2026, 6, 1), 1_000_000, 500_000, 10_000, "ok", _ASSET_DEFINITION_VERSION),
            (date(2026, 6, 2), 0, -200_000, 20_000, "ok", _ASSET_DEFINITION_VERSION),
            # 2026-05：收益 +5%，当日大额出金 30000.00 同样不影响金额。
            (date(2026, 5, 8), 5_000_000, -3_000_000, 5_000, "ok", _ASSET_DEFINITION_VERSION),
        ],
    )
    await _seed_asset_state(owner_engine, account.user_id, through=date(2026, 6, 30), rows=3)

    resp = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=account.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    june = _item(body, "2026-06")["monthly_return"]
    assert june["display"] == "+1.00%"
    assert june["sign"] == "pos"
    assert june["amount"] == "300.00"
    assert june["amount_display"] == "300.00"
    # 若把出入金混入金额，这里会是 3300.00（说明断言确实覆盖了剔除口径）。
    assert june["amount"] != "3300.00"

    may = _item(body, "2026-05")["monthly_return"]
    assert may["display"] == "+5.00%"
    assert may["amount"] == "50.00"
    assert may["amount_display"] == "50.00"

    # 无资产行月份金额为 null（不是 0）。
    assert _item(body, "2026-04")["monthly_return"]["amount"] is None
    assert _item(body, "2026-04")["monthly_return"]["amount_display"] is None


async def test_api_095_monthly_return_without_build_state_is_unavailable(
    flags_client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    """没有 asset_build_state（未构建/口径未知）时不读任何资产行，整区间显式 0 + null。"""
    account = await account_factory()
    await _seed_asset_days(
        owner_engine,
        account.user_id,
        [(date(2026, 6, 1), 10_000_000, 0, 100_000, "ok", _ASSET_DEFINITION_VERSION)],
    )

    resp = await flags_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=account.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_return_data"] is False
    assert all(item["monthly_return"]["available"] is False for item in body["items"])
    assert all(item["monthly_return"]["value_bp"] == 0 for item in body["items"])
