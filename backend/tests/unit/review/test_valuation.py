"""资产估值共享原语单测（Period2-16 / D3）：价格阶梯、市值 floor、行情失败分类。

这些原语由 nav 与资产表共用，行为必须与 nav 既有口径逐字节一致（nav 回归由
`test_nav_application.py` + API-032 兜底）。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.models import Trade
from stock_platform.ports import Bar
from stock_review.application.valuation import (
    CASHFLOW_CATEGORIES,
    COST_ADD_CATEGORIES,
    PRICE_LOOKBACK,
    QTY_PRICE_SCALE,
    blocked_price_reason,
    category,
    close_on_or_before,
    closing_price_ladder,
    holding_delta,
    is_cash_balance,
    load_prices,
    market_failure_cause,
    total_market_value,
)

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
    category_: str | None,
    quantity_units: int = 0,
    amount_units: int = 0,
    cash_balance_units: int | None = None,
) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=_USER,
        batch_id=None,
        trade_date=trade_date,
        security_code="600519",
        security_name="贵州茅台",
        side="buy",
        quantity_units=quantity_units,
        price_units=0,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=cash_balance_units,
        cash_balance_available=cash_balance_units is not None,
        category=category_,
        occurrence_idx=0,
        line_no=0,
        source="import",
        version=1,
        summary=category_,
    )


def _bar(symbol: str, trade_date: date, close: str) -> Bar:
    return Bar(symbol=symbol, trade_date=trade_date, close_units=price(close), return_units=0)


# ── 分类 / 持仓语义 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("buy", "buy"),
        (" SELL ", "sell"),
        ("Transfer", "transfer"),
        (None, ""),
        ("", ""),
    ],
)
def test_category_normalizes(raw: str | None, expected: str) -> None:
    assert category(_trade(trade_date=date(2026, 1, 1), category_=raw)) == expected


def test_is_cash_balance_treats_zero_as_balance() -> None:
    # 0 也是有效余额行（清零账户），只有 NULL 才表示“非余额行”。
    assert is_cash_balance(
        _trade(trade_date=date(2026, 1, 1), category_="other", cash_balance_units=0)
    )
    assert not is_cash_balance(_trade(trade_date=date(2026, 1, 1), category_="buy"))


@pytest.mark.parametrize(
    ("cat", "qty", "expected"),
    [
        ("buy", shares("100"), shares("100")),
        ("bonus", shares("10"), shares("10")),
        ("rights", shares("10"), shares("10")),
        ("new_stock", shares("5"), shares("5")),
        ("sell", shares("100"), -shares("100")),
        ("dividend", shares("100"), 0),
        ("transfer", 0, 0),
        ("", 0, 0),
    ],
)
def test_holding_delta(cat: str, qty: int, expected: int) -> None:
    assert holding_delta(cat, qty) == expected


def test_constants_match_design() -> None:
    assert QTY_PRICE_SCALE == 10**8
    assert PRICE_LOOKBACK.days == 45
    assert frozenset({"buy", "rights", "new_stock"}) == COST_ADD_CATEGORIES
    assert frozenset({"transfer"}) == CASHFLOW_CATEGORIES


# ── 价格阶梯 ────────────────────────────────────────────────


def test_closing_price_ladder_sorts_by_date_and_keeps_requested_symbols() -> None:
    bars = [
        _bar("600519", date(2026, 2, 27), "1700.00"),
        _bar("600519", date(2026, 1, 30), "1600.00"),
    ]
    ladder = closing_price_ladder(bars, ["600519", "000001"])
    assert ladder["600519"] == [
        (date(2026, 1, 30), price("1600.00")),
        (date(2026, 2, 27), price("1700.00")),
    ]
    # 请求了但没有 bar 的标的保留空序列（供缺失判定）。
    assert ladder["000001"] == []


def test_close_on_or_before_carries_forward_and_returns_none_before_first_bar() -> None:
    ladder = {
        symbol: [(date(2026, 1, 30), price("1600.00")), (date(2026, 2, 27), price("1700.00"))]
        for symbol in ["600519"]
    }
    # 精确命中
    assert close_on_or_before(ladder, "600519", date(2026, 2, 27)) == price("1700.00")
    # 周末/节假日 carry-forward：取当日或之前最近收盘
    assert close_on_or_before(ladder, "600519", date(2026, 3, 1)) == price("1700.00")
    # 早于首个 bar → 缺行情
    assert close_on_or_before(ladder, "600519", date(2026, 1, 1)) is None
    # 未请求/无行情标的
    assert close_on_or_before(ladder, "000001", date(2026, 3, 1)) is None


def test_total_market_value_floors_each_symbol() -> None:
    ladder = {
        "600519": [(date(2026, 1, 30), 1_000_001)],
        "000001": [(date(2026, 1, 30), 999_999)],
        "300750": [],
    }
    holdings = {"600519": 3, "000001": 3, "300750": 0}
    total, missing = total_market_value(holdings, ladder, date(2026, 2, 1))
    # 逐标的 floor：3*1_000_001//1e8=0，3*999_999//1e8=0；qty=0 不请求行情、不报缺失。
    assert total == 0
    assert missing == []
    holdings = {"600519": 10**8, "000001": 10**8}
    total, missing = total_market_value(holdings, ladder, date(2026, 2, 1))
    assert total == 1_000_001 + 999_999
    assert missing == []
    # 持有但无任何收盘 → 缺失（由调用方判 blocked）
    total, missing = total_market_value({"300750": 10**8}, ladder, date(2026, 2, 1))
    assert total == 0
    assert missing == ["300750"]


# ── 行情失败分类 ────────────────────────────────────────────


def _failure(cause: str | None) -> ApiError:
    metadata = {} if cause is None else {"market_failure": cause}
    return ApiError(ErrorCode.NOT_FOUND, detail="x", metadata=metadata)


@pytest.mark.parametrize(
    ("cause", "expected"),
    [("unavailable", "unavailable"), ("no_data", "no_data"), ("weird", ""), (None, "")],
)
def test_market_failure_cause_filters_known_values(cause: str | None, expected: str) -> None:
    assert market_failure_cause(_failure(cause)) == expected


def test_blocked_price_reason_sorts_symbols_and_picks_priority_suffix() -> None:
    causes = {"000651": "unavailable", "600519": "no_data"}
    reason = blocked_price_reason("缺行情数据", ["600519", "000651"], causes)
    assert reason == "缺行情数据：000651,600519（行情源暂时不可用，可重试）"
    # 无已知类别 → 只给代码清单
    assert blocked_price_reason("缺行情数据", ["600519"], {}) == "缺行情数据：600519"


# ── 行情取数（批量失败 → 逐标的降级）────────────────────────


class _Market:
    def __init__(
        self, bars: list[Bar], *, fail_batch: bool = False, fail_symbols: set[str] | None = None
    ) -> None:
        self._bars = bars
        self._fail_batch = fail_batch
        self._fail_symbols = fail_symbols or set()

    async def get_daily_bars(self, symbols, start, end):
        if self._fail_batch and len(list(symbols)) > 1:
            raise _failure("unavailable")
        wanted = list(symbols)
        for symbol in wanted:
            if symbol in self._fail_symbols:
                raise _failure("not_supported")
        return [
            bar for bar in self._bars if bar.symbol in wanted and start <= bar.trade_date <= end
        ]


@pytest.mark.asyncio
async def test_load_prices_empty_symbols_short_circuits() -> None:
    ladder, causes = await load_prices(_Market([]), [], date(2026, 1, 1), date(2026, 2, 1))
    assert ladder == {} and causes == {}


@pytest.mark.asyncio
async def test_load_prices_batch_success() -> None:
    market = _Market([_bar("600519", date(2026, 1, 30), "1600.00")])
    ladder, causes = await load_prices(
        market, ["600519", "000001"], date(2026, 1, 1), date(2026, 2, 1)
    )
    assert ladder["600519"] == [(date(2026, 1, 30), price("1600.00"))]
    # 请求了但区间内无 bar → no_data
    assert causes == {"000001": "no_data"}


@pytest.mark.asyncio
async def test_load_prices_degrades_to_per_symbol_on_batch_failure() -> None:
    market = _Market(
        [_bar("600519", date(2026, 1, 30), "1600.00")],
        fail_batch=True,
        fail_symbols={"999999"},
    )
    ladder, causes = await load_prices(
        market, ["600519", "999999"], date(2026, 1, 1), date(2026, 2, 1)
    )
    assert ladder["600519"] == [(date(2026, 1, 30), price("1600.00"))]
    # 逐标的失败类别被保留，供 blocked_reason 细化
    assert causes == {"999999": "not_supported"}
