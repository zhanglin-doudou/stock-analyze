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
    new_stock_codes_in_window,
    new_stock_pending_codes,
    total_market_value,
    valued_position_units,
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
    security_code: str = "600519",
    security_name: str = "贵州茅台",
    side: str = "buy",
    price_units: int = 0,
) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=_USER,
        batch_id=None,
        trade_date=trade_date,
        security_code=security_code,
        security_name=security_name,
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
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
    # new_stock 缴款行（金额非 0）按买入计持仓。
    assert holding_delta(cat, qty, amount_units=-cents("5.00")) == expected


def test_new_stock_notification_rows_do_not_change_holdings() -> None:
    """新股中签 / 新股入帐通知行（金额 0）不计持仓：同一批份额只由缴款行建仓一次。"""
    assert holding_delta("new_stock", shares("500"), amount_units=0) == 0
    assert holding_delta("new_stock", shares("500"), amount_units=-cents("26630.00")) == shares(
        "500"
    )
    # 送股（other 分类走 BONUS）不受影响：数量照加。
    assert holding_delta("other", shares("5"), amount_units=0) == 0
    assert holding_delta("bonus", shares("5"), amount_units=0) == shares("5")


def test_constants_match_design() -> None:
    assert QTY_PRICE_SCALE == 10**8
    assert PRICE_LOOKBACK.days == 45
    assert frozenset({"buy", "rights", "new_stock"}) == COST_ADD_CATEGORIES
    # broker_action（券商行为）按金额计外部现金流：登记行金额为 0，回款行不计收益。
    assert frozenset({"transfer", "broker_action"}) == CASHFLOW_CATEGORIES


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
    total, missing, cost_fallback, stock_fallback = total_market_value(
        holdings, ladder, date(2026, 2, 1)
    )
    # 逐标的 floor：3*1_000_001//1e8=0，3*999_999//1e8=0；qty=0 不请求行情、不报缺失。
    assert total == 0
    assert missing == []
    assert cost_fallback == []
    assert stock_fallback == []
    holdings = {"600519": 10**8, "000001": 10**8}
    total, missing, cost_fallback, stock_fallback = total_market_value(
        holdings, ladder, date(2026, 2, 1)
    )
    assert total == 1_000_001 + 999_999
    assert missing == []
    assert cost_fallback == []
    assert stock_fallback == []
    # 持有但无任何收盘 → 缺失（由调用方判 blocked）
    total, missing, _cost_fallback, _stock_fallback = total_market_value(
        {"300750": 10**8}, ladder, date(2026, 2, 1)
    )
    assert total == 0
    assert missing == ["300750"]


# ── 债券 / 伪代码：不读行情，按含费成本估值 ────────────────────


def test_valued_position_units_sources() -> None:
    """共用估值原语：行情品种按市价、非行情品种按成本、待上市新股按认购成本。"""
    assert valued_position_units("600519", shares("100"), price("1700"), cents("170045")) == (
        cents("170000"),
        "market",
    )
    assert valued_position_units("019547", shares("1000"), None, cents("980")) == (
        cents("980"),
        "cost",
    )
    assert valued_position_units(
        "001237", shares("500"), None, cents("26630"), new_stock_codes={"001237"}
    ) == (cents("26630"), "new_stock_cost")
    # 行情品种缺价且不是待上市新股 → 不可估值（调用方按缺行情处理）。
    assert valued_position_units("600519", shares("100"), None, cents("5000")) == (None, "")


def test_non_quote_symbols_use_cost_basis_not_quote() -> None:
    """债券/伪代码不读行情：即使缓存里有 bar，市值也取含费成本（买入价）。"""
    ladder = {"113050": [(date(2026, 8, 3), price("130"))]}
    total, missing, cost_fallback, stock_fallback = total_market_value(
        {"113050": shares("1")},
        ladder,
        date(2026, 8, 3),
        cost_basis={"113050": cents("1000")},
    )
    assert (total, missing, cost_fallback, stock_fallback) == (
        cents("1000"),
        [],
        ["113050"],
        [],
    )


def test_bond_and_pseudo_missing_quote_never_blocks() -> None:
    """无行情时：债券/伪代码按成本、不进 missing；行情品种仍算缺失。"""
    ladder = {"019547": [], "600519": []}
    holdings = {"019547": shares("1000"), "970172": shares("10"), "600519": shares("100")}
    cost = {"019547": cents("980"), "970172": cents("1000"), "600519": cents("5000")}
    total, missing, cost_fallback, stock_fallback = total_market_value(
        holdings, ladder, date(2026, 1, 5), cost_basis=cost
    )
    assert total == cents("1980")
    assert cost_fallback == ["019547", "970172"]
    assert stock_fallback == []
    assert missing == ["600519"]
    # 无成本（cost_basis=None）→ 非行情标的也无法估值，进 missing 由调用方决定文案。
    total, missing, cost_fallback, _stock_fallback = total_market_value(
        {"019547": shares("1000")}, ladder, date(2026, 1, 5)
    )
    assert (total, missing, cost_fallback) == (0, ["019547"], [])


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


def test_new_stock_pending_codes_only_paid_rows() -> None:
    """只有「认购缴款」（金额非 0）的行算待上市新股；中签/入帐通知行不算。"""
    paid = _trade(
        trade_date=date(2026, 5, 18),
        category_="new_stock",
        security_code="001237",
        quantity_units=shares("500"),
        amount_units=-cents("26630"),
    )
    notice = _trade(
        trade_date=date(2026, 5, 14),
        category_="new_stock",
        security_code="001237",
        quantity_units=shares("500"),
        amount_units=0,
    )
    other = _trade(trade_date=date(2026, 5, 20), category_="new_stock", security_code="001237")
    assert new_stock_pending_codes([paid, notice, other]) == {"001237"}
    assert new_stock_pending_codes([notice, other]) == set()


def test_new_stock_codes_in_window_is_period_scoped() -> None:
    """窗口化判定（归因专用）：只有 `[start, end]` 内的缴款行计入。

    历史缴款（窗口前）、零金额通知行均不计；边界日（左/右端）按闭区间计入。
    """
    paid_may = _trade(
        trade_date=date(2026, 5, 18),
        category_="new_stock",
        security_code="001237",
        quantity_units=shares("500"),
        amount_units=-cents("26630"),
    )
    paid_jun = _trade(
        trade_date=date(2026, 6, 2),
        category_="new_stock",
        security_code="002001",
        quantity_units=shares("500"),
        amount_units=-cents("10000"),
    )
    notice_jun = _trade(
        trade_date=date(2026, 6, 2),
        category_="new_stock",
        security_code="003001",
        quantity_units=shares("500"),
        amount_units=0,
    )
    boundary_jun = _trade(
        trade_date=date(2026, 6, 30),  # 右端闭区间
        category_="new_stock",
        security_code="004001",
        quantity_units=shares("500"),
        amount_units=-cents("8000"),
    )
    trades = [paid_may, paid_jun, notice_jun, boundary_jun]

    june = new_stock_codes_in_window(trades, start=date(2026, 6, 1), end=date(2026, 6, 30))
    assert june == {"002001", "004001"}  # 001237 是历史缴款；003001 是零金额通知
    may = new_stock_codes_in_window(trades, start=date(2026, 5, 1), end=date(2026, 5, 31))
    assert may == {"001237"}
    assert new_stock_codes_in_window(trades, start=date(2026, 7, 1), end=date(2026, 7, 31)) == set()


def test_new_stock_pending_quote_falls_back_to_subscription_cost() -> None:
    """待上市新股（001237）无行情：按认购缴款成本兜底进 stock_fallback，不阻断。"""
    total, missing, cost_fallback, stock_fallback = total_market_value(
        {"001237": shares("500"), "600519": shares("100")},
        {"001237": [], "600519": []},
        date(2026, 5, 20),
        cost_basis={"001237": cents("26630"), "600519": cents("5000")},
        new_stock_codes={"001237"},
    )
    assert (total, missing, cost_fallback, stock_fallback) == (
        cents("26630"),
        ["600519"],
        [],
        ["001237"],
    )
    # 不在 new_stock_codes 里的普通标的缺行情仍进 missing（真缺行情不能被掩盖）。
    total, missing, cost_fallback, stock_fallback = total_market_value(
        {"001237": shares("500")},
        {"001237": []},
        date(2026, 5, 20),
        cost_basis={"001237": cents("26630")},
        new_stock_codes=set(),
    )
    assert (total, missing, cost_fallback, stock_fallback) == (0, ["001237"], [], [])
