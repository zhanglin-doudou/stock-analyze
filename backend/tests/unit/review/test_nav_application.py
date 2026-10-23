"""净值应用层单测（Period2-03 / API-032）：份额法 TWR 与 XIRR/MWR 口径。

用桩行情源（月频收盘）覆盖三条硬性口径：
1. 大额入金**遇上涨** → MWR > 年化 TWR（资金更多投在盈利期）；
2. 大额入金**遇下跌** → MWR < 年化 TWR；
3. 无外部现金流 → MWR ≈ 年化 TWR；
4. 持仓标的缺行情 → 逐日阻断，不得抛异常；
5. 期初基准点落在区间首日（否则整段收益漏掉首日涨跌）。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.nav import build_nav_series
from stock_ledger.models import Trade
from stock_platform.ports import Bar
from stock_review.application.nav import compute_nav_points, investor_cashflows

_USER = uuid.uuid4()


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
) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=_USER,
        batch_id=None,
        trade_date=trade_date,
        security_code=security_code,
        security_name="贵州茅台",
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
        line_no=0,
        source="import",
        version=1,
        summary=category,
    )


class StubMarket:
    """桩行情源：给定 {(symbol, date): close}，未知标的按端口契约抛 ValueError 之外的异常。"""

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    async def get_daily_bars(self, symbols, start, end):
        return [
            bar for bar in self._bars if bar.symbol in symbols and start <= bar.trade_date <= end
        ]


class MissingSymbolMarket:
    """模拟回放夹具缺标的：任何取数都抛 ApiError(NOT_FOUND)。"""

    async def get_daily_bars(self, symbols, start, end):
        raise ApiErrorNotFound()


def ApiErrorNotFound():  # noqa: N802
    from stock_contracts.errors import ApiError, ErrorCode

    return ApiError(ErrorCode.NOT_FOUND, detail="找不到该资源")


def _bar(symbol: str, trade_date: date, close: str) -> Bar:
    return Bar(symbol=symbol, trade_date=trade_date, close_units=price(close), return_units=0)


async def _run(trades: list[Trade], start: date, end: date, market) -> tuple:
    points, blocked, reasons, degraded, _ = await compute_nav_points(trades, start, end, market)
    series = build_nav_series(points) if points else None
    mwr = 0
    if series is not None:
        flows = investor_cashflows(series)
        if flows:
            from stock_core.nav import compute_xirr

            mwr = compute_xirr(flows)
    return series, mwr, blocked, reasons, degraded


def _deposit_then_buy(
    deposit_date: date, *, deposit: str, qty: str, price_value: str
) -> list[Trade]:
    """一笔入金 + 同价买入（入金立刻投入），返回两条 trade。"""
    amount = cents(deposit)
    return [
        _trade(
            trade_date=deposit_date,
            category="transfer",
            amount_units=amount,
            cash_balance_units=amount,
            security_code="CASH",
        ),
        _trade(
            trade_date=deposit_date,
            category="buy",
            amount_units=-cents(deposit),
            cash_balance_units=0,
            quantity_units=shares(qty),
            price_units=price(price_value),
        ),
    ]


PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 9, 30)


def _base_trades() -> list[Trade]:
    """2025-12-31 期初 100000；2026-01-30 全额买入 100 股 @1000。"""
    return [
        _trade(
            trade_date=date(2025, 12, 31),
            category="transfer",
            amount_units=cents("100000.00"),
            cash_balance_units=cents("100000.00"),
            security_code="CASH",
        ),
        _trade(
            trade_date=date(2026, 1, 30),
            category="buy",
            amount_units=-cents("100000.00"),
            cash_balance_units=0,
            quantity_units=shares("100"),
            price_units=price("1000.00"),
        ),
    ]


def _rising_market(close_at_end: str) -> StubMarket:
    return StubMarket(
        [
            _bar("600519", date(2026, 1, 30), "1000.00"),
            _bar("600519", date(2026, 9, 15), "1000.00"),
            _bar("600519", date(2026, 9, 30), close_at_end),
        ]
    )


@pytest.mark.asyncio
async def test_deposit_before_rise_mwr_above_twr() -> None:
    """大额入金遇上涨：MWR（年化）> 年化 TWR —— 资金更多投在盈利期。"""
    trades = _base_trades() + _deposit_then_buy(
        date(2026, 9, 15), deposit="100000.00", qty="100", price_value="1000.00"
    )
    series, mwr, blocked, reasons, degraded = await _run(
        trades, PERIOD_START, PERIOD_END, _rising_market("1500.00")
    )

    assert blocked == [] and reasons == [] and degraded is False
    assert series is not None
    # TWR 只看单位净值：1000 → 1500 = +50%
    assert abs(Decimal(series.twr_units) / Decimal(10**8) - Decimal("0.5")) <= Decimal("0.0001")
    assert mwr > series.annualized_twr_units


@pytest.mark.asyncio
async def test_deposit_before_fall_mwr_below_twr() -> None:
    """大额入金遇下跌：MWR < 年化 TWR。"""
    trades = _base_trades() + _deposit_then_buy(
        date(2026, 9, 15), deposit="100000.00", qty="100", price_value="1000.00"
    )
    series, mwr, _, _, _ = await _run(trades, PERIOD_START, PERIOD_END, _rising_market("750.00"))

    assert series is not None
    assert Decimal(series.twr_units) < 0
    assert mwr < series.annualized_twr_units


@pytest.mark.asyncio
async def test_no_external_cashflow_mwr_equals_annualized_twr() -> None:
    """无区间内外部现金流：MWR 与年化 TWR 应一致（差异 <1bp）。"""
    trades = _base_trades()
    series, mwr, _, _, _ = await _run(trades, PERIOD_START, PERIOD_END, _rising_market("1500.00"))

    assert series is not None
    assert abs(mwr - series.annualized_twr_units) <= 10**8 // 10000


@pytest.mark.asyncio
async def test_missing_market_data_blocks_day_not_raise() -> None:
    """持仓标的缺行情 → 该日进入 blocked_points，不抛异常。"""
    trades = _base_trades()
    series, _, blocked, reasons, _ = await _run(
        trades, PERIOD_START, PERIOD_END, MissingSymbolMarket()
    )

    assert reasons == []
    assert blocked, "缺行情应产生逐日阻断"
    assert series is not None
    # 期初基准点仅含现金（无持仓），仍可计算
    assert series.points[0].trade_date == PERIOD_START


@pytest.mark.asyncio
async def test_baseline_point_on_period_start() -> None:
    """首个点必须是区间首日基准，否则整段 TWR 漏掉首日前的持仓涨跌。"""
    trades = _base_trades()
    points, _, _, _, _ = await compute_nav_points(
        trades, PERIOD_START, PERIOD_END, _rising_market("1500.00")
    )
    assert points[0][0] == PERIOD_START
    assert points[0][1] == cents("100000.00")
    assert points[0][2] == 0


@pytest.mark.asyncio
async def test_no_cash_anchor_is_global_block() -> None:
    """无任何余额行 → 全局阻断（reason 非空，无点）。"""
    trades = [
        _trade(
            trade_date=date(2026, 2, 2),
            category="buy",
            amount_units=-cents("1000.00"),
            quantity_units=shares("1"),
            price_units=price("1000.00"),
        )
    ]
    points, blocked, reasons, _, _ = await compute_nav_points(
        trades, PERIOD_START, PERIOD_END, _rising_market("1500.00")
    )
    assert points == [] and blocked == []
    assert reasons and "现金" in reasons[0]


# ── 行情失败类别 → blocked_reason（D2=A：不新增错误码，只细化自由文本）─────
class _FailureMarket:
    def __init__(self, cause: str) -> None:
        self._cause = cause

    async def get_daily_bars(self, symbols, start, end):
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            detail="upstream down",
            metadata={"market_failure": self._cause},
        )


class _EmptyMarket:
    async def get_daily_bars(self, symbols, start, end):
        return ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cause", "label"),
    [
        ("unavailable", "行情源暂时不可用"),
        ("not_supported", "标的不在支持范围"),
    ],
)
async def test_blocked_reason_annotates_market_failure(cause: str, label: str) -> None:
    """逐日阻断仍返回 200 blocked，但原因带可操作类别（契约不变）。"""
    trades = _base_trades()
    _, blocked, reasons, _, _ = await compute_nav_points(
        trades, PERIOD_START, PERIOD_END, _FailureMarket(cause)
    )
    assert reasons == []
    assert blocked
    reason = blocked[0][1]
    assert reason.startswith("缺行情数据：")
    assert label in reason


@pytest.mark.asyncio
async def test_blocked_reason_marks_requested_but_empty_as_no_data() -> None:
    """请求了标的但区间内无 bar（适配器返回空序列）→ 标记 no_data。"""
    trades = _base_trades()
    _, blocked, reasons, _, _ = await compute_nav_points(
        trades, PERIOD_START, PERIOD_END, _EmptyMarket()
    )
    assert reasons == []
    assert blocked
    assert "该区间无行情" in blocked[0][1]
