"""资产表计算口径单测（Period2-16）：逐日推演 / blocked / degraded / 日收益 / 窗口。

`compute_daily_valuations` 是纯函数（无 I/O、无 wall-clock），因此可直接用内存 Trade 列表
复现设计 §5.3 的全部口径；增量窗口衔接（rebuild_from / prev_row）也在这里覆盖，
集成测试只验证“增量 == 全量”与落库/事务语义。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from stock_ledger.models import Trade
from stock_platform.ports import Bar
from stock_review.application.assets import (
    FIFO_ERROR_REASON,
    MARKET_UNCOVERED_REASON,
    MISSING_PRICE_PREFIX,
    _ratio_units,
    compute_daily_valuations,
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
    money_fund_balance_units: int | None = None,
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
        money_fund_balance_units=money_fund_balance_units,
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
    )


def _row(result, trade_date: date) -> dict[str, object]:
    matched = [row for row in result.rows if row["trade_date"] == trade_date]
    assert matched, f"缺少 {trade_date} 的行"
    return matched[0]


# ── 现金 / 首行锚点 ────────────────────────────────────────


def test_pure_cash_days_have_zero_market_and_zero_pnl() -> None:
    """UT-01：单条余额行后 5 天无流水 → 5 行纯现金，首行 prev=期初锚点。"""
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
    assert len(result.rows) == 5
    for row in result.rows:
        assert row["cash_units"] == cents("100000.00")
        assert row["market_value_units"] == 0
        assert row["total_assets_units"] == cents("100000.00")
        assert row["holding_count"] == 0
        assert row["status"] == "ok"
        assert row["status_reason"] is None
    assert [row["trade_date"] for row in result.rows] == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
        date(2026, 1, 5),
    ]
    first = _row(result, date(2026, 1, 1))
    assert first["prev_total_assets_units"] == cents("100000.00")
    assert first["daily_pnl_units"] == 0
    assert first["daily_return_units"] == 0
    assert all(row["daily_pnl_units"] == 0 for row in result.rows)


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
    assert len(result.rows) == 33
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
            trade_date=date(2026, 1, 3),
            category="sell",
            amount_units=cents("1200.00"),
            quantity_units=shares("100"),
            price_units=price("12.00"),
            side="sell",
            line_no=3,
        ),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 3), "12.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 3), ladder=ladder)
    row = _row(result, date(2026, 1, 3))
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
            trade_date=date(2026, 1, 3), category="dividend", amount_units=cents("50.00"), line_no=3
        ),
        _trade(
            trade_date=date(2026, 1, 3), category="interest", amount_units=cents("5.00"), line_no=4
        ),
        _trade(trade_date=date(2026, 1, 3), category="fee", amount_units=-cents("2.00"), line_no=5),
    ]
    ladder = _ladder(_bar("600519", date(2026, 1, 2), "10.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 3), ladder=ladder)
    row = _row(result, date(2026, 1, 3))
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


# ── 货币基金 / 债券（设计 2026-10-29）─────────────────────────


def test_money_fund_balance_counts_into_assets_without_market_request() -> None:
    """货基行不进持仓/不请求行情：总资产 = 现金 + 货基 + 市值。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("1000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            security_code="940018",
            amount_units=-cents("500.00"),
            quantity_units=shares("500"),
            price_units=price("1.0000"),
            money_fund_balance_units=cents("500.00"),
            line_no=2,
        ),
    ]
    # ladder 为空：若货基被当普通持仓会因缺行情 blocked；这里应保持 ok。
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 3))
    day2 = _row(result, date(2026, 1, 2))
    assert day2["status"] == "ok"
    assert day2["cash_units"] == cents("500.00")
    assert day2["money_fund_units"] == cents("500.00")
    assert day2["market_value_units"] == 0
    assert day2["holding_count"] == 0
    assert day2["total_assets_units"] == cents("1000.00")
    assert day2["status_reason"] is None


def test_money_fund_negative_balance_is_clamped_not_degraded() -> None:
    """历史入库的负货基余额（账单缺赎回流水）→ 按 0 兜底，不产生降级行。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("20000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            security_code="004369",
            amount_units=-cents("10000.00"),
            quantity_units=shares("10000"),
            price_units=price("1.0000"),
            money_fund_balance_units=-cents("9971.96"),
            line_no=2,
        ),
    ]
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 3))

    day2 = _row(result, date(2026, 1, 2))
    assert day2["status"] == "ok"
    assert day2["status_reason"] is None
    assert day2["money_fund_units"] == 0
    assert day2["total_assets_units"] == cents("10000.00")


def test_money_fund_balance_override_takes_last_row_of_day() -> None:
    """同日两行货基 → 余额按稳定序最后一行覆盖（不是累加）。"""
    trades = [
        _trade(
            trade_date=date(2026, 1, 1),
            category="other",
            amount_units=0,
            cash_balance_units=cents("1000.00"),
            line_no=1,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            security_code="940018",
            amount_units=-cents("300.00"),
            quantity_units=shares("300"),
            price_units=price("1.0000"),
            money_fund_balance_units=cents("300.00"),
            line_no=3,
        ),
        _trade(
            trade_date=date(2026, 1, 2),
            category="buy",
            security_code="940018",
            amount_units=-cents("200.00"),
            quantity_units=shares("200"),
            price_units=price("1.0000"),
            money_fund_balance_units=cents("500.00"),
            line_no=4,
        ),
    ]
    day2 = _row(_run(trades, date(2026, 1, 1), date(2026, 1, 2)), date(2026, 1, 2))
    assert day2["money_fund_units"] == cents("500.00")
    assert day2["cash_units"] == cents("500.00")
    assert day2["total_assets_units"] == cents("1000.00")


def test_bond_missing_quote_is_degraded_not_blocked() -> None:
    """债券缺行情 → 按买入价（cost_basis）估值 + degraded；非债券缺行情仍 blocked。"""
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
    assert day2["status"] == "degraded"
    assert day2["market_value_units"] == cents("1000.00")
    assert day2["cash_units"] == cents("9000.00")
    assert day2["total_assets_units"] == cents("10000.00")
    assert day2["status_reason"] is not None
    assert "债券按买入价估值" in day2["status_reason"]
    assert "019547" in day2["status_reason"]


# ── blocked / degraded ─────────────────────────────────────


def test_missing_price_blocks_day_with_zero_values_and_reason() -> None:
    """UT-09：持有无行情标的 → 受影响日 blocked；数值 0、prev/pnl/return NULL、原因含代码。"""
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
            security_code="999999",
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
    assert "999999" in blocked["status_reason"]


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
    # 01-02 没有「当日或之前」收盘 → blocked；01-03 起有 bar。
    ladder = _ladder(_bar("600519", date(2026, 1, 3), "10.00"))
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 4), ladder=ladder)
    assert _row(result, date(2026, 1, 2))["status"] == "blocked"
    after_blocked = _row(result, date(2026, 1, 3))
    assert after_blocked["status"] == "ok"
    assert after_blocked["prev_total_assets_units"] is None
    assert after_blocked["daily_pnl_units"] is None
    assert after_blocked["daily_return_units"] is None
    recovered = _row(result, date(2026, 1, 4))
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
    result = _run(trades, date(2026, 1, 1), date(2026, 1, 4), ladder=ladder, as_of=date(2026, 1, 2))
    assert _row(result, date(2026, 1, 2))["status"] == "ok"
    uncovered = _row(result, date(2026, 1, 3))
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


def test_oversell_allows_negative_market_value_and_degrades() -> None:
    """D8/UT-12：超卖 → 负持仓/负市值，status=degraded（不建非负 CHECK）。"""
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
        trades, date(2026, 1, 1), date(2026, 1, 2), ladder=ladder, fifo_error_codes={"000001"}
    )
    row = _row(result, date(2026, 1, 2))
    assert row["market_value_units"] == -cents("1000.00")
    assert row["status"] == "degraded"
    assert row["status_reason"] == FIFO_ERROR_REASON


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
    """增量：只产窗口行；窗口首行 prev_total 取落库上一日行。"""
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
        date(2026, 1, 4),
        rebuild_from=date(2026, 1, 3),
        prev_row=prev_row,
    )
    assert [row["trade_date"] for row in result.rows] == [
        date(2026, 1, 3),
        date(2026, 1, 4),
    ]
    first = _row(result, date(2026, 1, 3))
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
        date(2026, 1, 4),
        rebuild_from=date(2026, 1, 3),
        prev_row=prev_row,
    )
    first = _row(result, date(2026, 1, 3))
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
    """相邻自然日不跳日：跨越周末/节假日的行连续（UT-08/UT-15 简化）。"""
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
    assert len(dates) == 31
    assert dates == [start + timedelta(days=offset) for offset in range(31)]


# ── 可转债发债代码归并（设计 2026-10-29 D3）：上市前 degraded / 上市后正常 ──


def test_new_stock_prelisting_degraded_not_blocked_and_holding_counted_once() -> None:
    """新股 001237：中签/入帐通知行（金额 0）不计持仓，缴款行建仓 500 股。

    上市前（05-18 ~ 05-21）无行情 → 按认购成本兜底 degraded（不 blocked）；
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
            trade_date=date(2026, 5, 23),
            category="sell",
            security_code="001237",
            side="sell",
            quantity_units=shares("500"),
            price_units=price("135.00"),
            amount_units=cents("67500.00"),
        ),
    ]
    ladder = _ladder(_bar("001237", date(2026, 5, 22), "135.00"), symbols=["001237"])
    result = _run(trades, date(2026, 5, 1), date(2026, 5, 23), ladder=ladder)
    assert result.global_blocked is False

    # 中签通知行当天：还没缴款，不持有 → ok。
    notice = _row(result, date(2026, 5, 14))
    assert notice["status"] == "ok"
    assert notice["holding_count"] == 0

    # 缴款后上市前：按认购成本兜底 degraded，现金 73370 + 成本 26630 = 100000。
    pre = _row(result, date(2026, 5, 20))
    assert pre["status"] == "degraded"
    assert "新股待上市" in (pre["status_reason"] or "")
    assert "001237" in (pre["status_reason"] or "")
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
    after = _row(result, date(2026, 5, 23))
    assert after["status"] == "ok"
    assert after["holding_count"] == 0
    assert after["total_assets_units"] == cents("140870")


def test_cbond_alias_prelisting_degraded_postlisting_ok() -> None:
    """754019 曙26发债（07-20 中签缴款 1 手@-1000）归并到 113050 曙26转债：
    上市前（08-03 前）按买入价兜底 degraded；上市后按行情估值 ok，且全程不 blocked。"""
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
    # 上市前无行情 → 债券按买入价兜底：现金 99000 + 成本 1000 = 100000。
    assert pre["status"] == "degraded"
    assert "113050" in (pre["status_reason"] or "")
    assert "754019" not in (pre["status_reason"] or "")
    assert pre["total_assets_units"] == cents("100000")
    assert pre["market_value_units"] == cents("1000")

    listed = _row(result, date(2026, 8, 3))
    # 上市后按行情：1 手 × 10 张 × 130 元/张 = 1300 元。
    assert listed["status"] == "ok"
    assert listed["market_value_units"] == cents("1300")
    assert listed["total_assets_units"] == cents("100300")
    assert listed["holding_count"] == 1
