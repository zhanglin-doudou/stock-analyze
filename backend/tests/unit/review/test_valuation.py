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
    bond_face_divisors,
    bond_value_units,
    category,
    close_on_or_before,
    closing_price_ladder,
    holding_delta,
    initial_money_fund_balances,
    is_cash_balance,
    is_money_fund_row,
    load_prices,
    market_failure_cause,
    money_fund_share_delta,
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
    security_code: str = "600519",
    security_name: str = "贵州茅台",
    side: str = "buy",
    price_units: int = 0,
    money_fund_balance_units: int | None = None,
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
        money_fund_balance_units=money_fund_balance_units,
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
    total, missing, bond_fallback = total_market_value(holdings, ladder, date(2026, 2, 1))
    # 逐标的 floor：3*1_000_001//1e8=0，3*999_999//1e8=0；qty=0 不请求行情、不报缺失。
    assert total == 0
    assert missing == []
    assert bond_fallback == []
    holdings = {"600519": 10**8, "000001": 10**8}
    total, missing, bond_fallback = total_market_value(holdings, ladder, date(2026, 2, 1))
    assert total == 1_000_001 + 999_999
    assert missing == []
    assert bond_fallback == []
    # 持有但无任何收盘 → 缺失（由调用方判 blocked）
    total, missing, _bond_fallback = total_market_value({"300750": 10**8}, ladder, date(2026, 2, 1))
    assert total == 0
    assert missing == ["300750"]


# ── 货币基金 / 债券（设计 2026-10-29）─────────────────────────


_MF_NAME = "华泰紫金天\n天发货币"


def test_money_fund_primitives_and_opening_balances() -> None:
    buy = _trade(
        trade_date=date(2026, 1, 5),
        category_="buy",
        security_code="940018",
        security_name=_MF_NAME,
        quantity_units=shares("10000"),
        amount_units=-cents("10000"),
        price_units=price("1.0000"),
        money_fund_balance_units=cents("10000"),
    )
    sell = _trade(
        trade_date=date(2026, 1, 6),
        category_="sell",
        security_code="940018",
        security_name=_MF_NAME,
        side="sell",
        quantity_units=shares("2000"),
        amount_units=cents("2000"),
        price_units=price("1.0000"),
        money_fund_balance_units=cents("8000"),
    )
    assert is_money_fund_row(buy) and is_money_fund_row(sell)
    # 余额列为 NULL 的普通行不是货基行（哪怕名称含「货币」）。
    assert not is_money_fund_row(_trade(trade_date=date(2026, 1, 5), category_="buy"))
    # 份额 × 1.0000 → 金额；卖出取负。
    assert money_fund_share_delta(buy) == cents("10000")
    assert money_fund_share_delta(sell) == -cents("2000")
    # 区间前有余额行 → 直取最后一条。
    assert initial_money_fund_balances([buy, sell], date(2026, 2, 1)) == {"940018": cents("8000")}
    # 区间内首条余额行 → 反推期初（8000 − (−2000) = 10000）。
    assert initial_money_fund_balances([sell], date(2026, 1, 6)) == {"940018": cents("10000")}
    # 无货基行 → 空。
    assert initial_money_fund_balances([], date(2026, 2, 1)) == {}


def test_bond_face_divisor_consistent_with_valuation() -> None:
    """成交金额反推系数必须与 `qty × close ÷ 1e8 ÷ divisor` 自洽。"""
    # 数量按「张」：10 张 × 净价 100 = 1000 元 → 系数 1。
    zhang = _trade(
        trade_date=date(2026, 1, 5),
        category_="buy",
        security_code="019547",
        security_name="国债",
        quantity_units=shares("10"),
        price_units=price("100.00"),
        amount_units=-cents("1000"),
    )
    # 数量按「面值」：1000 面值 × 净价 100 ÷ 100 = 1000 元 → 系数 100（公司债，
    # 可转债不走反推，见下方 cbond 专项用例）。
    face = _trade(
        trade_date=date(2026, 1, 5),
        category_="buy",
        security_code="122009",
        security_name="公司债",
        quantity_units=shares("1000"),
        price_units=price("100.00"),
        amount_units=-cents("1000"),
    )
    assert bond_face_divisors([zhang]) == {"019547": 1}
    assert bond_face_divisors([face]) == {"122009": 100}

    ladder = {"019547": [(date(2026, 1, 5), price("100.00"))]}
    total, missing, fallback = total_market_value(
        {"019547": shares("10")},
        ladder,
        date(2026, 1, 5),
        bond_divisors=bond_face_divisors([zhang]),
    )
    assert (total, missing, fallback) == (cents("1000"), [], [])


def test_bond_missing_quote_falls_back_to_cost_basis() -> None:
    ladder = {"019547": [], "600519": []}
    holdings = {"019547": shares("1000"), "600519": shares("100")}
    cost = {"019547": cents("980"), "600519": cents("5000")}
    # 债券缺行情 → 按买入成本兜底（fallback），非债券缺行情 → missing（调用方 blocked）。
    total, missing, fallback = total_market_value(
        holdings, ladder, date(2026, 1, 5), cost_basis=cost, bond_divisors={"019547": 1}
    )
    assert total == cents("980")
    assert fallback == ["019547"]
    assert missing == ["600519"]
    # 不启用兜底（cost_basis=None）时债券也进 missing。
    total, missing, fallback = total_market_value(holdings, ladder, date(2026, 1, 5))
    assert total == 0
    assert fallback == []
    assert missing == ["019547", "600519"]


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


# ── 可转债市值：固定 1 手 = 10 张（设计 2026-10-29 D2）──────────────────────


def test_cbond_value_fixed_lot_10() -> None:
    """1 手 @ 收盘 130 元/张 → 10 张 × 130 = 1300 元（不走成交金额反推）。"""
    assert bond_value_units("113050", shares("1"), price("130"), None) == cents("1300")
    # 其它债券仍按反推系数折算（张口径：10 张 × 101.5 = 1015 元）。
    assert bond_value_units("019547", shares("10"), price("101.5"), {"019547": 1}) == cents("1015")
    # 面值口径公司债：1000 面值 × 100 ÷ 100 = 1000 元。
    assert bond_value_units("122009", shares("1000"), price("100"), {"122009": 100}) == cents(
        "1000"
    )


def test_bond_face_divisors_skips_convertible() -> None:
    """可转债不反推（申购代码无成交行可推，口径固定 1 手 = 10 张）。"""
    cbond = _trade(
        trade_date=date(2026, 7, 20),
        category_="buy",
        security_code="113050",
        security_name="曙26转债",
        quantity_units=shares("1"),
        price_units=price("100.00"),
        amount_units=-cents("1000"),
    )
    gov = _trade(
        trade_date=date(2026, 1, 5),
        category_="buy",
        security_code="019547",
        security_name="国债",
        quantity_units=shares("10"),
        price_units=price("100.00"),
        amount_units=-cents("1000"),
    )
    divisors = bond_face_divisors([cbond, gov])
    assert "113050" not in divisors
    assert divisors["019547"] == 1


def test_cbond_market_value_in_total() -> None:
    ladder = {"113050": [(date(2026, 8, 3), price("130"))]}
    total, missing, fallback = total_market_value(
        {"113050": shares("1")},
        ladder,
        date(2026, 8, 3),
        cost_basis={"113050": cents("1000")},
        bond_divisors={},
    )
    assert (total, missing, fallback) == (cents("1300"), [], [])


def test_cbond_missing_quote_falls_back_to_cost_basis() -> None:
    """上市前窗口（无行情）：按买入成本兜底，进 fallback 不进 missing。"""
    total, missing, fallback = total_market_value(
        {"113050": shares("1")},
        {"113050": []},
        date(2026, 7, 21),
        cost_basis={"113050": cents("1000")},
        bond_divisors={},
    )
    assert (total, missing, fallback) == (cents("1000"), [], ["113050"])
