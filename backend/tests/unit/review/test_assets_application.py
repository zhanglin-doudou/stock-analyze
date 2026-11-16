"""资产表计算口径单测（Period2-16）：逐日推演 / blocked / degraded / 日收益 / 窗口。

`compute_daily_valuations` 是纯函数（无 I/O、无 wall-clock），因此可直接用内存 Trade 列表
复现设计 §5.3 的全部口径；增量窗口衔接（rebuild_from / prev_row）也在这里覆盖，
集成测试只验证“增量 == 全量”与落库/事务语义。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from stock_contracts.clock import FrozenClock
from stock_ledger.models import Trade
from stock_platform.ports import Bar
from stock_review.application.assets import (
    FIFO_ERROR_REASON,
    MARKET_UNCOVERED_REASON,
    MISSING_PRICE_PREFIX,
    MISSING_QUOTE_HINT,
    OVERSELL_REASON,
    _ratio_units,
    _session_notice,
    align_to_trading_day,
    compute_daily_valuations,
    valuation_end,
)
from stock_review.application.valuation import closing_price_ladder
from stock_review.models import AssetDaily

_USER = uuid.uuid4()
_REBUILT_AT = datetime.fromisoformat("2026-10-01T12:00:00+08:00")
_AS_OF = date(2026, 12, 31)


def cents(value: str) -> int:
    return int(Decimal(value) * Decimal(100))


def price(value: str) -> int:
    return int(Decimal(value) * Decimal(10**6))


def shares(value: str) -> int:
    return int(Decimal(value) * Decimal(10**4))


# ── 收盘口径：valuation_end / _session_notice（2026-11-02）────────────────


class TestValuationEnd:
    """资产表只在已收盘的工作日写行：未收盘当日/周末都不是构建终点。"""

    def test_workday_before_close_uses_previous_workday(self) -> None:
        # 2026-11-02 周一 10:00（上海）→ 终点为上周五 10-30。
        clock = FrozenClock("2026-11-02T10:00:00+08:00")
        assert valuation_end(clock) == date(2026, 10, 30)

    def test_workday_at_close_boundary_uses_today(self) -> None:
        # 15:00 整即视为已收盘。
        clock = FrozenClock("2026-11-02T15:00:00+08:00")
        assert valuation_end(clock) == date(2026, 11, 2)

    def test_workday_after_close_uses_today(self) -> None:
        clock = FrozenClock("2026-11-02T16:30:00+08:00")
        assert valuation_end(clock) == date(2026, 11, 2)

    def test_monday_morning_rolls_back_over_weekend(self) -> None:
        # 周一早盘 09:25 → 前一工作日是上周五（跨周末）。
        clock = FrozenClock("2026-11-02T09:25:00+08:00")
        assert valuation_end(clock) == date(2026, 10, 30)

    def test_weekend_ignores_time_of_day(self) -> None:
        # 周六无论几点都回退到周五。
        for now in ("2026-10-31T09:00:00+08:00", "2026-10-31T16:00:00+08:00"):
            assert valuation_end(FrozenClock(now)) == date(2026, 10, 30)

    def test_utc_clock_converted_to_shanghai(self) -> None:
        # UTC 07:00 = 上海 15:00（收盘边界）。
        assert valuation_end(FrozenClock("2026-11-02T07:00:00+00:00")) == date(2026, 11, 2)
        # UTC 06:59 = 上海 14:59（未收盘）。
        assert valuation_end(FrozenClock("2026-11-02T06:59:00+00:00")) == date(2026, 10, 30)


# ── 交易日对齐：align_to_trading_day（归因 PIT 板块时点）─────────────────


class TestTradingDayAlignment:
    """区间末日 → ≤ 末日的最后一个交易日（行情表最大交易日为判定源）。

    `latest_bar_date` 为 `market.daily_bar` 中 ≤ day 的最大交易日；节假日只能
    通过行情数据识别（周末另有工作日回退兜底）。
    """

    def test_month_end_on_weekend_rolls_back(self) -> None:
        # 2026-10-31 周六为非工作日 → 10-30（用户报告的核心场景）。
        assert align_to_trading_day(date(2026, 10, 31), date(2026, 10, 30)) == date(2026, 10, 30)

    def test_sunday_rolls_back_to_friday(self) -> None:
        # 2026-11-01 周日 → 10-30 周五。
        assert align_to_trading_day(date(2026, 11, 1), date(2026, 10, 30)) == date(2026, 10, 30)

    def test_holiday_rolls_back_to_last_bar(self) -> None:
        # 2026-10-01 国庆（周四，非交易日）→ 行情表最大交易日 09-30。
        assert align_to_trading_day(date(2026, 10, 1), date(2026, 9, 30)) == date(2026, 9, 30)

    def test_period_end_is_trading_day_unchanged(self) -> None:
        # 末日恰为交易日（10-30 周五）→ 原样返回。
        assert align_to_trading_day(date(2026, 10, 30), date(2026, 10, 30)) == date(2026, 10, 30)

    def test_empty_market_falls_back_to_workday(self) -> None:
        # 空库/replay 无行情：只剔除周末（不含节假日）。
        assert align_to_trading_day(date(2026, 10, 31), None) == date(2026, 10, 30)
        assert align_to_trading_day(date(2026, 10, 30), None) == date(2026, 10, 30)


class TestSessionNotice:
    def test_market_open_before_close(self) -> None:
        clock = FrozenClock("2026-11-02T10:00:00+08:00")
        assert _session_notice(clock, date(2026, 10, 30)) == "market_open"

    def test_sync_pending_after_close_without_today_bars(self) -> None:
        clock = FrozenClock("2026-11-02T16:00:00+08:00")
        assert _session_notice(clock, date(2026, 10, 30)) == "sync_pending"
        assert _session_notice(clock, None) == "sync_pending"

    def test_none_after_close_with_today_bars(self) -> None:
        clock = FrozenClock("2026-11-02T16:00:00+08:00")
        assert _session_notice(clock, date(2026, 11, 2)) is None

    def test_none_on_weekend(self) -> None:
        clock = FrozenClock("2026-10-31T10:00:00+08:00")
        assert _session_notice(clock, date(2026, 10, 30)) is None


def _trade(
    *,
    trade_date: date,
    category: str,
    amount_units: int,
    security_code: str = "600519",
    quantity_units: int = 0,
    price_units: int = 0,
    cash_balance_units: int | None = None,
    side: str = "buy",
    line_no: int = 1,
) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=_USER,
        batch_id=None,
        trade_date=trade_date,
        security_code=security_code,
        security_name=security_code,
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=cash_balance_units,
        cash_balance_available=cash_balance_units is not None,
        category=category,
        occurrence_idx=0,
        line_no=line_no,
        source="import",
        version=1,
        summary=category,
    )


def _bar(symbol: str, trade_date: date, close: str) -> Bar:
    return Bar(symbol=symbol, trade_date=trade_date, close_units=price(close), return_units=0)


def _ladder(*bars: Bar, symbols: list[str] | None = None) -> dict[str, list[tuple[date, int]]]:
    return closing_price_ladder(list(bars), symbols or ["600519"])


def _run(
    trades: list[Trade],
    start: date,
    end: date,
    *,
    ladder: dict[str, list[tuple[date, int]]] | None = None,
    as_of: date = _AS_OF,
    fifo_error_codes: set[str] | None = None,
    rebuild_from: date | None = None,
    prev_row: AssetDaily | None = None,
    market_causes: dict[str, str] | None = None,
    bond_alias: dict[str, str] | None = None,
    oversell_proceeds: dict[str, int] | None = None,
    quotes_pull_attempted: bool = False,
):
    return compute_daily_valuations(
        trades,
        user_id=_USER,
        start=start,
        end=end,
        rebuild_from=rebuild_from or start,
        ladder=ladder if ladder is not None else {},
        market_causes=market_causes or {},
        as_of=as_of,
        prev_row=prev_row,
        fifo_error_codes=fifo_error_codes or set(),
        rebuilt_at=_REBUILT_AT,
        bond_alias=bond_alias,
        oversell_proceeds=oversell_proceeds,
        quotes_pull_attempted=quotes_pull_attempted,
    )


def _row(result, trade_date: date) -> dict[str, object]:
    matched = [row for row in result.rows if row["trade_date"] == trade_date]
    assert matched, f"缺少 {trade_date} 的行"
    return matched[0]


# ── 现金 / 首行锚点 ────────────────────────────────────────


def test_pure_cash_days_have_zero_market_and_zero_pnl() -> None:
    """UT-01：单条余额行后 5 个自然日（含周末不写行）→ 3 工作日行，首行 prev=期初锚点。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
        )
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 5))
    assert result.global_blocked is False
    assert len(result.rows) == 3
    for row in result.rows:
        assert row["cash_units"] == cents("100000.00")
        assert row["market_value_units"] == 0
        assert row["total_assets_units"] == cents("100000.00")
        assert row["holding_count"] == 0
        assert row["status"] == "ok"
        assert row["status_reason"] is None
    # 01-03/01-04 为周六/周日：不写行（资产表只保留工作日）。
    assert [row["trade_date"] for row in result.rows] == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 5),
    ]
    first = _row(result, date(2026, 1, 1))
    assert first["prev_total_assets_units"] == cents("100000.00")
    assert first["daily_pnl_units"] == 0
    assert first["daily_return_units"] == 0
    assert all(row["daily_pnl_units"] == 0 for row in result.rows)


def test_money_fund_merged_into_cash_and_never_held() -> None:
    """2.3：货基是现金等价物 —— 余额并入现金列，不进持仓/成本/市值/行情，总资产不变。"""
    trades = [
        _trade(
            trade_date=date(2026, 6, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
        ),
        _trade(
            trade_date=date(2026, 6, 2),
            category="buy",
            security_code="119924",
            quantity_units=shares("50000"),
            price_units=price("1.00"),
            amount_units=-cents("50000.00"),
            line_no=2,
        ),
        _trade(
            trade_date=date(2026, 6, 3),
            category="sell",
            security_code="119924",
            quantity_units=shares("20000"),
            price_units=price("1.00"),
            amount_units=cents("20000.00"),
            side="sell",
            line_no=3,
        ),
    ]
    result = _run(trades, date(2026, 6, 1), date(2026, 6, 3))
    assert result.global_blocked is False

    first = _row(result, date(2026, 6, 1))
    assert first["cash_units"] == cents("100000.00")
    assert first["total_assets_units"] == cents("100000.00")

    # 06-02 申购 50000：券商现金 50000 + 货基 50000 = 现金列 100000，无持仓。
    bought = _row(result, date(2026, 6, 2))
    assert bought["cash_units"] == cents("100000.00")
    assert bought["market_value_units"] == 0
    assert bought["holding_count"] == 0
    assert bought["holding_cost_units"] == 0
    assert bought["status"] == "ok"
    assert bought["daily_pnl_units"] == 0

    # 06-03 赎回 20000：券商现金 70000 + 货基 30000，总资产仍 100000。
    sold = _row(result, date(2026, 6, 3))
    assert sold["cash_units"] == cents("100000.00")
    assert sold["total_assets_units"] == cents("100000.00")
    assert sold["holding_count"] == 0
    assert sold["daily_pnl_units"] == 0
    assert sold["cashflow_units"] == 0


def test_money_fund_reinvested_dividend_counts_as_return() -> None:
    """货基红利再投（金额 0、份额增加）→ 现金列增加并计入当日收益，不算外部现金流。"""
    trades = [
        _trade(
            trade_date=date(2026, 6, 1),
            category="buy",
            security_code="119924",
            quantity_units=shares("100000"),
            price_units=price("1.00"),
            amount_units=-cents("100000.00"),
            cash_balance_units=0,
        ),
        _trade(
            trade_date=date(2026, 6, 2),
            category="dividend",
            security_code="119924",
            quantity_units=shares("100"),
            price_units=price("1.00"),
            amount_units=0,
            line_no=2,
        ),
    ]
    result = _run(trades, date(2026, 6, 1), date(2026, 6, 2))
    row = _row(result, date(2026, 6, 2))
    assert row["cash_units"] == cents("100100.00")
    assert row["daily_pnl_units"] == cents("100.00")
    assert row["cashflow_units"] == 0
    assert row["holding_count"] == 0


def test_buy_and_month_end_valuation_matches_oracle() -> None:
    """UT-02：05-29 余额 100000；06-30 买 100@1700（余额 -70000）；收盘 1706.20。

    cash=-70000.00、market=170620.00、total=100620.00、cost=170000.00、unrealized=620.00、
    pnl=620.00、return=0.0062（620000 units）。
    """
    trades = [
        _trade(
            trade_date=date(2026, 5, 29),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 6, 30),
            category="buy",
            amount_units=-cents("170000.00"),
            quantity_units=shares("100"),
            price_units=price("1700.00"),
            cash_balance_units=-cents("70000.00"),
            line_no=2,
        ),
    ]
    result = _run(
        trades,
        date(2026, 5, 29),
        date(2026, 6, 30),
        ladder=_ladder(_bar("600519", date(2026, 6, 30), "1706.20")),
    )
    assert len(result.rows) == 23
    row = _row(result, date(2026, 6, 30))
    assert row["cash_units"] == -cents("70000.00")
    assert row["market_value_units"] == cents("170620.00")
    assert row["total_assets_units"] == cents("100620.00")
    assert row["holding_cost_units"] == cents("170000.00")
    assert row["unrealized_pnl_units"] == cents("620.00")
    assert row["holding_count"] == 1
    assert row["prev_total_assets_units"] == cents("100000.00")
    assert row["daily_pnl_units"] == cents("620.00")
    assert row["daily_return_units"] == 620_000
    assert row["status"] == "ok"


def test_back_solved_anchor_first_row_buy() -> None:
    """UT-11：首行即买入（余额 -70000、amount -170000）→ 期初现金 100000，首日 pnl 正确。"""
    trades = [
        _trade(
            trade_date=date(2026, 6, 30),
            category="buy",
            amount_units=-cents("170000.00"),
            quantity_units=shares("100"),
            price_units=price("1700.00"),
            cash_balance_units=-cents("70000.00"),
        )
    ]
    result = _run(
        trades,
        date(2026, 6, 30),
        date(2026, 6, 30),
        ladder=_ladder(_bar("600519", date(2026, 6, 30), "1706.20")),
    )
    row = _row(result, date(2026, 6, 30))
    assert row["cash_units"] == -cents("70000.00")
    assert row["prev_total_assets_units"] == cents("100000.00")
    assert row["daily_pnl_units"] == cents("620.00")
    assert row["daily_return_units"] == 620_000


def test_transfer_is_external_cashflow_and_excluded_from_pnl() -> None:
    """UT-05：银证转账 +50000 → cashflow=50000，pnl 不含该 50000。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="transfer",
            amount_units=cents("50000.00"),
            line_no=2,
        ),
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 2))
    row = _row(result, date(2026, 1, 2))
    assert row["cash_units"] == cents("150000.00")
    assert row["cashflow_units"] == cents("50000.00")
    assert row["total_assets_units"] == cents("150000.00")
    assert row["daily_pnl_units"] == 0


def test_sell_carries_cost_proportionally() -> None:
    """UT-03：买 200@10（-2000），卖 100@12（+1200）→ 剩余 qty=100、cost=1000。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("10000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("2000.00"),
            quantity_units=shares("200"),
            price_units=price("10.00"),
            line_no=2,
        ),
        _trade(
            trade_date=date(2026, 1, 5),
            category="sell",
            amount_units=cents("1200.00"),
            quantity_units=shares("100"),
            price_units=price("12.00"),
            side="sell",
            line_no=3,
        ),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 5), "12.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 5), ladder=ladder)
    row = _row(result, date(2026, 1, 5))
    assert row["holding_count"] == 1
    assert row["holding_cost_units"] == cents("1000.00")
    assert row["market_value_units"] == cents("1200.00")
    assert row["unrealized_pnl_units"] == cents("200.00")


def test_dividend_and_fee_change_cash_without_touching_holdings() -> None:
    """UT-04：分红/利息/费用只改现金，不改持仓/成本；非 transfer 不计 cashflow。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("10000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("100"),
            price_units=price("10.00"),
            line_no=2,
        ),
        _trade(
            trade_date=date(2026, 1, 5), category="dividend", amount_units=cents("50.00"), line_no=3
        ),
        _trade(
            trade_date=date(2026, 1, 5), category="interest", amount_units=cents("5.00"), line_no=4
        ),
        _trade(trade_date=date(2026, 1, 5), category="fee", amount_units=-cents("2.00"), line_no=5),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 2), "10.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 5), ladder=ladder)
    row = _row(result, date(2026, 1, 5))
    assert row["cash_units"] == cents("9053.00")
    assert row["holding_cost_units"] == cents("1000.00")
    assert row["holding_count"] == 1
    assert row["cashflow_units"] == 0


def test_balance_line_overrides_daily_cash() -> None:
    """UT-07：同日多笔含余额行 → 现金取当日最后一笔余额（既有简化行为固定）。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="other",
            amount_units=cents("10.00"),
            cash_balance_units=cents("111.00"),
            line_no=2,
        ),
        _trade(
            trade_date=date(2026, 1, 2), category="other", amount_units=cents("99.00"), line_no=3
        ),
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 2))
    # 余额行之后的 amount 被“覆盖”吞掉（与 nav 一致，v1 不修）。
    assert _row(result, date(2026, 1, 2))["cash_units"] == cents("111.00")


# ── 债券：不读行情，按含费成本估值 ──────────────────────────


def test_bond_missing_quote_is_ok_with_cost_remark() -> None:
    """债券按含费成本估值：不读行情、不 blocked、不 degraded，只在 remark 留痕。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("10000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            security_code="019547",
            amount_units=-cents("1000.00"),
            quantity_units=shares("10"),
            price_units=price("100.00"),
            line_no=2,
        ),
    ]
    result = _run(
        trades,
        date(2026, 1, 1),
        date(2026, 1, 3),
        ladder={"019547": []},
        market_causes={"019547": "not_supported"},
    )
    day2 = _row(result, date(2026, 1, 2))
    assert day2["status"] == "ok"
    assert day2["status_reason"] is None
    assert day2["remark"] == "按买入价估值：019547"
    assert day2["market_value_units"] == cents("1000.00")
    assert day2["cash_units"] == cents("9000.00")
    assert day2["total_assets_units"] == cents("10000.00")
    # 非行情标的不会触发补拉（没有 missing 区间）。
    assert result.missing_quotes == {}


# ── blocked / degraded ─────────────────────────────────────


def test_missing_price_blocks_day_with_zero_values_and_reason() -> None:
    """UT-09：持有行情品种但无 bar → 受影响日 blocked；数值 0、prev/pnl/return NULL、原因含代码。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("100"),
            price_units=price("10.00"),
            security_code="600519",
            line_no=2,
        ),
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 3), ladder=_ladder())
    assert _row(result, date(2026, 1, 1))["status"] == "ok"
    blocked = _row(result, date(2026, 1, 2))
    assert blocked["status"] == "blocked"
    assert (
        blocked["cash_units"] == blocked["market_value_units"] == blocked["total_assets_units"] == 0
    )
    assert (
        blocked["holding_cost_units"]
        == blocked["unrealized_pnl_units"]
        == blocked["cashflow_units"]
        == 0
    )
    assert blocked["holding_count"] == 0
    assert blocked["prev_total_assets_units"] is None
    assert blocked["daily_pnl_units"] is None
    assert blocked["daily_return_units"] is None
    assert blocked["status_reason"] is not None
    assert blocked["status_reason"].startswith(MISSING_PRICE_PREFIX)
    assert "600519" in blocked["status_reason"]
    # 缺失区间供构建做一次定向补拉：只有真正 blocked 的工作日（周末不写行）。
    assert result.missing_quotes == {"600519": (date(2026, 1, 2), date(2026, 1, 2))}
    assert MISSING_QUOTE_HINT not in blocked["status_reason"]


def test_missing_price_reason_carries_support_hint_after_pull_attempt() -> None:
    """UT-27：补拉一次后仍缺行情 → 行级原因追加最终提示（文案由后端给，前端只渲染）。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("100"),
            price_units=price("10.00"),
            security_code="600519",
            line_no=2,
        ),
    ]
    result = _run(
        trades,
        date(2026, 1, 1),
        date(2026, 1, 5),
        ladder=_ladder(),
        quotes_pull_attempted=True,
    )
    blocked = _row(result, date(2026, 1, 2))
    assert blocked["status"] == "blocked"
    assert blocked["status_reason"].startswith(MISSING_PRICE_PREFIX)
    assert blocked["status_reason"].endswith(MISSING_QUOTE_HINT)
    # 连续缺行情跨多个工作日：补拉区间覆盖首尾两端。
    assert result.missing_quotes == {"600519": (date(2026, 1, 2), date(2026, 1, 5))}


def test_blocked_next_day_has_null_pnl_then_recovers() -> None:
    """UT-13：blocked 次日 pnl/return=NULL，再下一日恢复。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("100"),
            price_units=price("10.00"),
            line_no=2,
        ),
    ]
    # 01-02 没有「当日或之前」收盘 → blocked；01-05（周一）起有 bar。
    ladder = _ladder(_bar("600519", date(2026, 1, 5), "10.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 6), ladder=ladder)
    assert _row(result, date(2026, 1, 2))["status"] == "blocked"
    after_blocked = _row(result, date(2026, 1, 5))
    assert after_blocked["status"] == "ok"
    assert after_blocked["prev_total_assets_units"] is None
    assert after_blocked["daily_pnl_units"] is None
    assert after_blocked["daily_return_units"] is None
    recovered = _row(result, date(2026, 1, 6))
    assert recovered["prev_total_assets_units"] == after_blocked["total_assets_units"]
    assert recovered["daily_pnl_units"] == 0


def test_uncovered_market_degraded_only_when_holding() -> None:
    """行情 as_of 之后：有持仓 → degraded；纯现金行不降级。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("100"),
            price_units=price("10.00"),
            line_no=2,
        ),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 2), "10.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 5), ladder=ladder, as_of=date(2026, 1, 2))
    assert _row(result, date(2026, 1, 2))["status"] == "ok"
    uncovered = _row(result, date(2026, 1, 5))
    assert uncovered["status"] == "degraded"
    assert uncovered["status_reason"] == MARKET_UNCOVERED_REASON

    pure_cash = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100.00"),
        )
    ]
    cash_result = _run(pure_cash, date(2026, 1, 1), date(2026, 1, 3), as_of=date(2026, 1, 1))
    assert all(row["status"] == "ok" for row in cash_result.rows)


def test_fifo_error_only_degrades_days_with_problem_holdings() -> None:
    """FIFO 异常只影响持有问题代码的日子；纯现金日 / 未持有问题代码的其他持仓日不受影响。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="sell",
            amount_units=cents("100.00"),
            quantity_units=shares("100"),
            security_code="000001",
            side="sell",
            line_no=2,
        ),
    ]
    ladder = _ladder(_bar("000001", date(2026, 1, 2), "10.00"), symbols=["000001"])
    result = _run(
        trades, date(2026, 1, 1), date(2026, 1, 2), ladder=ladder, fifo_error_codes={"000001"}
    )
    # 纯现金日（2026-01-01）→ ok
    assert _row(result, date(2026, 1, 1))["status"] == "ok"
    # 持有 000001 的日子（2026-01-02）→ degraded，原因是 FIFO
    r2 = _row(result, date(2026, 1, 2))
    assert r2["status"] == "degraded"
    assert r2["status_reason"] == FIFO_ERROR_REASON


def test_oversell_clamped_to_zero_and_degrades_day() -> None:
    """超卖：持仓按 0 截断（不产生负市值），卖款进总资产但不算收益，当日标 degraded。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("10000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="sell",
            amount_units=cents("2000.00"),
            quantity_units=shares("100"),
            security_code="000001",
            side="sell",
            line_no=2,
        ),
    ]
    ladder = _ladder(_bar("000001", date(2026, 1, 2), "10.00"), symbols=["000001"])
    result = _run(
        trades,
        date(2026, 1, 1),
        date(2026, 1, 2),
        ladder=ladder,
        oversell_proceeds={trades[1].id: cents("2000.00")},
    )
    row = _row(result, date(2026, 1, 2))
    assert row["market_value_units"] == 0
    assert row["total_assets_units"] == cents("12000.00")
    # 超卖日降级，提示人工补录成本；无超卖的纯现金日保持 ok。
    assert row["status"] == "degraded"
    assert row["status_reason"] == OVERSELL_REASON
    assert _row(result, date(2026, 1, 1))["status"] == "ok"
    # 卖款已进现金余额，但既不算收益也不标成资金划转：cashflow 仍为 0，日收益为 0。
    assert row["cashflow_units"] == 0
    assert row["daily_pnl_units"] == 0


def test_global_blocked_when_no_cash_balance() -> None:
    """UT-10：全部 cash_balance=NULL → 全局阻断、不产出行。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="buy",
            amount_units=-cents("100.00"),
            quantity_units=shares("10"),
            price_units=price("10.00"),
        ),
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 3))
    assert result.global_blocked is True
    assert result.rows == []
    assert result.global_reason == "无现金余额锚点（无 cash_balance 行）"


# ── 日收益边界 / 四舍五入 / 确定性 ────────────────────────


def test_daily_return_null_when_prev_total_non_positive() -> None:
    """UT-13：prev_total <= 0 → daily_return=NULL（daily_pnl 仍为差值）。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=0,
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="transfer",
            amount_units=cents("100.00"),
            line_no=2,
        ),
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 2))
    first = _row(result, date(2026, 1, 1))
    assert first["prev_total_assets_units"] == 0
    assert first["daily_pnl_units"] == 0
    assert first["daily_return_units"] is None
    second = _row(result, date(2026, 1, 2))
    assert second["daily_pnl_units"] == 0
    assert second["daily_return_units"] is None


def test_ratio_units_rounds_half_up_including_negative() -> None:
    """UT-23：比率 Decimal + ROUND_HALF_UP 到 1e-8。"""
    assert _ratio_units(1, 512) == 195_313  # 195312.5 → 进位
    assert _ratio_units(-1, 512) == -195_313  # 负数同样远离 0
    assert _ratio_units(1, 100) == 1_000_000
    assert _ratio_units(620 * 100, 10_000_000) == 620_000


def test_rows_are_deterministic() -> None:
    """UT-14：同输入/时钟两次推演的行完全一致（排除审计时间列，此处本就不含）。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("100"),
            price_units=price("10.00"),
            line_no=2,
        ),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 2), "10.00"))
    first = _run(trades, date(2026, 1, 1), date(2026, 1, 3), ladder=ladder)
    second = _run(trades, date(2026, 1, 1), date(2026, 1, 3), ladder=ladder)
    assert first.rows == second.rows


# ── 增量窗口衔接 ──────────────────────────────────────────


def test_incremental_window_only_emits_from_rebuild_from_and_uses_prev_row() -> None:
    """增量：只产窗口行；rebuild_from 落在周末 → 首行顺延到周一，prev 取落库上一工作日行。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="transfer",
            amount_units=cents("50000.00"),
            line_no=2,
        ),
    ]
    prev_row = AssetDaily(
        trade_date=date(2026, 1, 2),
        status="ok",
        total_assets_units=cents("150000.00"),
    )
    result = _run(
        trades,
        date(2026, 1, 1),
        date(2026, 1, 6),
        rebuild_from=date(2026, 1, 3),
        prev_row=prev_row,
    )
    # 01-03（周六）/01-04（周日）不写行；窗口首行是 01-05（周一）。
    assert [row["trade_date"] for row in result.rows] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]
    first = _row(result, date(2026, 1, 5))
    assert first["prev_total_assets_units"] == cents("150000.00")
    assert first["daily_pnl_units"] == 0


def test_incremental_window_prev_row_blocked_gives_null_pnl() -> None:
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100.00"),
        )
    ]
    prev_row = AssetDaily(trade_date=date(2026, 1, 2), status="blocked", total_assets_units=0)
    result = _run(
        trades,
        date(2026, 1, 1),
        date(2026, 1, 6),
        rebuild_from=date(2026, 1, 3),
        prev_row=prev_row,
    )
    first = _row(result, date(2026, 1, 5))
    assert first["prev_total_assets_units"] is None
    assert first["daily_pnl_units"] is None
    assert first["daily_return_units"] is None


def test_total_equals_cash_plus_market_and_pnl_identity() -> None:
    """UT-16 简化版：任意行 total=cash+market；非 blocked 行 pnl=total-cashflow-prev。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 3),
            category="buy",
            amount_units=-cents("170000.00"),
            quantity_units=shares("100"),
            price_units=price("1700.00"),
            cash_balance_units=-cents("70000.00"),
            line_no=2,
        ),
        _trade(
            trade_date=date(2026, 1, 5),
            category="transfer",
            amount_units=cents("1000.00"),
            line_no=3,
        ),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 3), "1710.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 6), ladder=ladder)
    for row in result.rows:
        assert row["total_assets_units"] == row["cash_units"] + row["market_value_units"]
        assert row["unrealized_pnl_units"] == row["market_value_units"] - row["holding_cost_units"]
        if row["status"] != "blocked":
            assert row["daily_pnl_units"] == (
                row["total_assets_units"] - row["cashflow_units"] - row["prev_total_assets_units"]
            )


def test_window_boundary_arithmetic() -> None:
    """周末不写行：01-01 至 01-31 只保留工作日行（22 天），日期严格递增。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100.00"),
        )
    ]
    start, end = date(2026, 1, 1), date(2026, 1, 31)
    result = _run(trades, start, end)
    dates = [row["trade_date"] for row in result.rows]
    assert len(dates) == 22
    expected: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            expected.append(cursor)
        cursor += timedelta(days=1)
    assert dates == expected


# ── 可转债发债代码归并（设计 2026-10-29 D3）：上市前 degraded / 上市后正常 ──


def test_new_stock_prelisting_degraded_not_blocked_and_holding_counted_once() -> None:
    """新股 001237：中签/入帐通知行（金额 0）不计持仓，缴款行建仓 500 股。

    上市前（05-18 ~ 05-21）无行情 → 按认购成本兜底，视为正常（ok，不 degraded）；
    上市首日（05-22）有行情 → 按市价 ok；通知行不再重复计数（持仓恒 500 股）。
    """
    trades = [
        _trade(
            trade_date=date(2026, 5, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
        ),
        # 中签通知 / 缴款 / 入帐通知三行：只有缴款行建仓。
        _trade(
            trade_date=date(2026, 5, 14),
            category="new_stock",
            security_code="001237",
            quantity_units=shares("500"),
            price_units=price("53.26"),
            amount_units=0,
        ),
        _trade(
            trade_date=date(2026, 5, 18),
            category="new_stock",
            security_code="001237",
            quantity_units=shares("500"),
            price_units=price("53.26"),
            amount_units=-cents("26630.00"),
        ),
        _trade(
            trade_date=date(2026, 5, 19),
            category="new_stock",
            security_code="001237",
            quantity_units=shares("500"),
            price_units=price("53.26"),
            amount_units=0,
        ),
        _trade(
            trade_date=date(2026, 5, 25),
            category="sell",
            security_code="001237",
            side="sell",
            quantity_units=shares("500"),
            price_units=price("135.00"),
            amount_units=cents("67500.00"),
        ),
    ]
    ladder = _ladder(_bar("001237", date(2026, 5, 22), "135.00"), symbols=["001237"])
    result = _run(trades, date(2026, 5, 1), date(2026, 5, 25), ladder=ladder)
    assert result.global_blocked is False

    # 中签通知行当天：还没缴款，不持有 → ok。
    notice = _row(result, date(2026, 5, 14))
    assert notice["status"] == "ok"
    assert notice["holding_count"] == 0

    # 缴款后上市前：按认购成本兜底，视为正常（ok），现金 73370 + 成本 26630 = 100000。
    pre = _row(result, date(2026, 5, 20))
    assert pre["status"] == "ok"
    assert pre["status_reason"] is None
    assert pre["remark"] is None
    assert pre["market_value_units"] == cents("26630")
    assert pre["total_assets_units"] == cents("100000")
    assert pre["holding_count"] == 1  # 通知行不重复计数：500 股而非 1500 股

    # 上市首日：有行情 → 市价估值 ok，现金 73370 + 67500 = 140870。
    listed = _row(result, date(2026, 5, 22))
    assert listed["status"] == "ok"
    assert listed["market_value_units"] == cents("67500")
    assert listed["total_assets_units"] == cents("140870")
    assert listed["holding_count"] == 1
    # 次日卖出：持仓清零，现金 140870。
    after = _row(result, date(2026, 5, 25))
    assert after["status"] == "ok"
    assert after["holding_count"] == 0
    assert after["total_assets_units"] == cents("140870")


def test_cbond_alias_valued_at_cost_throughout() -> None:
    """754019 曙26发债（07-20 中签缴款 1 手@-1000）归并到 113050 曙26转债：
    债券不读行情，上市前后都按含费成本 1000 估值（ok + remark），全程不 blocked。"""
    alias = {"754019": "113050"}
    trades = [
        _trade(
            trade_date=date(2026, 7, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("100000.00"),
        ),
        _trade(
            trade_date=date(2026, 7, 20),
            category="buy",
            security_code="754019",
            quantity_units=shares("1"),
            price_units=price("100.00"),
            amount_units=-cents("1000"),
        ),
    ]
    ladder = _ladder(_bar("113050", date(2026, 8, 3), "130"), symbols=["113050"])
    result = _run(
        trades,
        date(2026, 7, 20),
        date(2026, 8, 4),
        ladder=ladder,
        bond_alias=alias,
    )
    assert result.global_blocked is False

    pre = _row(result, date(2026, 7, 20))
    # 上市前：按买入价估值，视为正常（ok），remark 留痕；现金 99000 + 成本 1000 = 100000。
    assert pre["status"] == "ok"
    assert pre["status_reason"] is None
    assert pre["remark"] == "按买入价估值：113050"
    assert pre["total_assets_units"] == cents("100000")
    assert pre["market_value_units"] == cents("1000")

    listed = _row(result, date(2026, 8, 3))
    # 上市后也不读行情：仍按含费成本 1000 估值（与资产表/净值/归因同口径）。
    assert listed["status"] == "ok"
    assert listed["status_reason"] is None
    assert listed["remark"] == "按买入价估值：113050"
    assert listed["market_value_units"] == cents("1000")
    assert listed["total_assets_units"] == cents("100000")
    assert listed["holding_count"] == 1
