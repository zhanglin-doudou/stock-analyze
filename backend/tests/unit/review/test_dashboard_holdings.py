"""Dashboard 当前持仓（`_holdings_at`）口径单测：必须与资产表逐项一致。

回归 `DASHBOARD_HOLDING_PRICE_MISSING` 误报（2026-10-29 线上问题）：
货基行被当成持仓 → 请求行情必然缺失 → 看板报一串「部分持仓缺少行情」。同源口径还有
可转债发债代码未归并、债券/待上市新股缺行情未按成本兜底，本文件逐项锁定。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketCapabilities, Quote
from stock_review.application.dashboard import _holdings_at

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
    quantity_units: int = 0,
    amount_units: int = 0,
    security_code: str = "600519",
    security_name: str = "贵州茅台",
    side: str = "buy",
    price_units: int = 0,
    cash_balance_units: int | None = None,
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
        category=category,
        occurrence_idx=0,
        line_no=0,
        source="import",
        version=1,
        summary=category,
    )


def _anchor(**overrides: object) -> Trade:
    base: dict[str, object] = {
        "trade_date": date(2026, 1, 2),
        "category": "interest",
        "amount_units": 0,
        "cash_balance_units": cents("100000.00"),
        "security_code": "",
        "security_name": "",
    }
    base.update(overrides)
    return _trade(**base)  # type: ignore[arg-type]


class StubMarketData:
    """只回放给定 bar 的行情桩，并记录被请求的标的（货基不得出现在请求里）。"""

    def __init__(self, bars: dict[str, list[Bar]] | None = None) -> None:
        self.requested: list[str] = []
        self._bars = bars or {}

    @property
    def capabilities(self) -> MarketCapabilities:
        return MarketCapabilities(mode="live")

    @property
    def as_of(self) -> date:
        return date(2026, 10, 29)

    @property
    def provenance(self) -> str:
        return "stub:market"

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        return ()

    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        self.requested.extend(symbols)
        return tuple(
            bar
            for symbol in symbols
            for bar in self._bars.get(symbol, ())
            if start <= bar.trade_date <= end
        )


def _bar(symbol: str, trade_date: date, close: str) -> Bar:
    return Bar(symbol=symbol, trade_date=trade_date, close_units=price(close), return_units=0)


_AS_OF = date(2026, 10, 19)


async def test_holdings_exclude_money_fund_and_skip_price_request() -> None:
    """货基行是余额口径资产：不进持仓、不请求行情，也不产生缺行情告警。"""
    market = StubMarketData({"600519": [_bar("600519", date(2026, 10, 19), "1700.00")]})
    trades = [
        _anchor(),
        _trade(
            trade_date=date(2026, 3, 2),
            category="buy",
            security_code="600519",
            security_name="贵州茅台",
            quantity_units=shares("100"),
            price_units=price("1700.00"),
            amount_units=cents("-170000.00"),
        ),
        _trade(
            trade_date=date(2026, 9, 30),
            category="buy",
            security_code="940018",
            security_name="华泰紫金天天发货币",
            quantity_units=shares("51847.59"),
            price_units=price("1.0000"),
            amount_units=cents("-51847.59"),
            money_fund_balance_units=cents("51847.59"),
        ),
    ]

    holdings, missing, fallback, truncated = await _holdings_at(
        trades, as_of=_AS_OF, market_data=market
    )

    assert [item.security_code for item in holdings] == ["600519"]
    assert missing == []
    assert fallback == []
    assert truncated is False
    assert market.requested == ["600519"]


async def test_holdings_merge_cbond_issue_code_into_listed_code() -> None:
    """发债代码（754019 曙26发债）归并到上市代码；可转债 1 手 = 10 张。"""
    market = StubMarketData({"113708": [_bar("113708", date(2026, 10, 19), "110.00")]})
    trades = [
        _anchor(),
        _trade(
            trade_date=date(2026, 7, 1),
            category="new_stock",
            security_code="754019",
            security_name="曙26发债",
            quantity_units=0,
            amount_units=cents("-1000.00"),
        ),
        # 上市代码出现即建立名称 stem（发债 → 上市）唯一映射。
        _trade(
            trade_date=date(2026, 7, 20),
            category="transfer",
            security_code="113708",
            security_name="曙26转债",
        ),
        _trade(
            trade_date=date(2026, 7, 20),
            category="buy",
            security_code="754019",
            security_name="曙26发债",
            quantity_units=shares("1"),
            price_units=price("100.00"),
            amount_units=cents("-1000.00"),
        ),
    ]

    holdings, missing, fallback, _truncated = await _holdings_at(
        trades, as_of=_AS_OF, market_data=market
    )

    assert [item.security_code for item in holdings] == ["113708"]
    # 名称取最后一条成交行（发债行），代码已归并到上市代码。
    assert holdings[0].security_name in {"曙26发债", "曙26转债"}
    # 1 手 × 110.00 元/张 × 10 = 1100.00 元
    assert holdings[0].market_value_units == cents("1100.00")
    assert missing == []
    assert fallback == []
    assert market.requested == ["113708"]


async def test_holdings_bond_and_pending_new_stock_fall_back_to_cost() -> None:
    """债券 / 待上市新股缺行情 → 按买入成本兜底，不算「缺行情」。"""
    market = StubMarketData()
    trades = [
        _anchor(),
        _trade(
            trade_date=date(2026, 6, 1),
            category="buy",
            security_code="019547",
            security_name="16国债19",
            quantity_units=shares("100"),
            price_units=price("100.00"),
            amount_units=cents("-100000.00"),
        ),
        _trade(
            trade_date=date(2026, 5, 18),
            category="new_stock",
            security_code="001237",
            security_name="惠康科技",
            quantity_units=shares("500"),
            price_units=price("135.00"),
            amount_units=cents("-67500.00"),
        ),
    ]

    holdings, missing, fallback, _truncated = await _holdings_at(
        trades, as_of=_AS_OF, market_data=market
    )

    by_code = {item.security_code: item for item in holdings}
    assert by_code["019547"].market_value_units == cents("100000.00")
    assert by_code["001237"].market_value_units == cents("67500.00")
    assert missing == []
    assert fallback == ["001237", "019547"]


async def test_holdings_report_missing_price_for_ordinary_stock() -> None:
    """普通股票真缺行情时仍要报 missing（不能把真缺口洗成 ok）。"""
    market = StubMarketData()
    trades = [
        _anchor(),
        _trade(
            trade_date=date(2026, 6, 1),
            category="buy",
            security_code="600519",
            security_name="贵州茅台",
            quantity_units=shares("100"),
            price_units=price("1700.00"),
            amount_units=cents("-170000.00"),
        ),
    ]

    _holdings, missing, fallback, _truncated = await _holdings_at(
        trades, as_of=_AS_OF, market_data=market
    )

    assert missing == ["600519"]
    assert fallback == []
