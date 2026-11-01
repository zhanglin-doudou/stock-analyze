"""证券持仓重建（Dashboard 与板块统计共用同一估值口径）。

``holdings_at`` 原为 ``dashboard._holdings_at``，提取到这里是为了让
``/sectors/overview`` 与 ``/dashboard`` 不会出现「两套持仓市值」：
货基不进持仓、可转债发债代码归一、债券按面值换算、缺行情的债券/新股按成本兜底。

行为与口径保持不变（`dashboard._holdings_at` 保留为薄包装，兼容既有单测）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from stock_ledger.models import Trade
from stock_platform.ports import MarketDataSource
from stock_platform.symbols import is_bond

from stock_review.application.bond_alias import build_bond_alias, canonical_code
from stock_review.application.valuation import (
    bond_face_divisors,
    bond_value_units,
    category,
    close_on_or_before,
    holding_delta,
    is_money_fund_row,
    load_prices,
    new_stock_pending_codes,
)

HOLDINGS_MAX = 200
# 当前持仓估值只看最近 45 天行情（与资产表安全窗同源）。
PRICE_WINDOW_DAYS = 45


@dataclass(slots=True, frozen=True)
class Holding:
    """当前持仓（证券维度；现金不在列表内）。"""

    security_code: str
    security_name: str
    quantity_units: int
    cost_units: int
    market_value_units: int | None
    weight_ratio_units: int | None


def ratio_units(numerator: int, denominator: int) -> int | None:
    """标度比率（scale=8，四舍五入）；分母 <= 0 → None（不伪造 0）。"""
    if denominator <= 0:
        return None
    value = (Decimal(numerator) / Decimal(denominator)).scaleb(8)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


async def holdings_at(
    trades: list[Trade],
    *,
    as_of: date,
    market_data: MarketDataSource,
) -> tuple[list[Holding], list[str], list[str], bool]:
    """按资产表同一口径重建 ``as_of`` 收盘持仓。

    与 ``asset_daily`` 必须逐项对齐，否则会出现「持仓市值合计与快照不一致」的假告警：
    * 货基行不进持仓、不请求行情（货基已计入 ``money_fund_units`` 余额）；
    * 可转债发债代码归一到上市代码（发债代码永无行情）；
    * 债券按面值换算系数估值（``bond_value_units``），可转债固定 1 手 = 10 张；
    * 债券 / 待上市新股缺行情时按买入成本兜底，不算「缺行情」。

    返回 ``(holdings, 缺价标的, 成本兜底标的, 是否截断)``；成本与
    ``asset_daily.holding_cost`` 同源：买入/配股/打新累加 ``-amount``，卖出按数量比例结转。
    """
    bond_alias, _unresolved = build_bond_alias(
        (trade.security_code, trade.security_name) for trade in trades
    )
    holdings: dict[str, int] = {}
    cost_basis: dict[str, int] = {}
    names: dict[str, str] = {}

    for trade in trades:
        if trade.trade_date > as_of:
            break
        raw_code = (trade.security_code or "").strip()
        if not raw_code:
            continue
        # 货基：余额口径资产，不进持仓，也不请求行情（否则必然缺行情）。
        if is_money_fund_row(trade):
            continue
        code = canonical_code(bond_alias, raw_code)
        trade_category = category(trade)
        delta = holding_delta(trade_category, trade.quantity_units, amount_units=trade.amount_units)
        if delta:
            holdings[code] = holdings.get(code, 0) + delta
            names[code] = trade.security_name or code
        if trade_category in {"buy", "rights", "new_stock"} and trade.amount_units != 0:
            cost_basis[code] = cost_basis.get(code, 0) + (-trade.amount_units)
        elif trade_category == "sell":
            old_qty = holdings.get(code, 0) - delta
            if old_qty > 0 and delta < 0:
                old_cost = cost_basis.get(code, 0)
                cost_basis[code] = max(0, old_cost + (old_cost * delta) // old_qty)

    held = {code: qty for code, qty in holdings.items() if qty != 0}
    if not held:
        return [], [], [], False

    symbols = sorted(held)
    # 只读缓存取价：与 asset_daily 快照同价，避免盘中补拉造成市值漂移。
    ladder, _causes = await load_prices(
        market_data,
        symbols,
        as_of - timedelta(days=PRICE_WINDOW_DAYS),
        as_of,
        cached_only=True,
    )
    # 债券面值换算系数（成交金额反推；可转债固定 1 手 = 10 张）与待上市新股集合。
    bond_divisors = bond_face_divisors([trade for trade in trades if trade.trade_date <= as_of])
    pending_new_stock = {
        canonical_code(bond_alias, code) for code in new_stock_pending_codes(trades)
    }

    rows: list[tuple[int, Holding]] = []
    missing_prices: list[str] = []
    cost_fallback: list[str] = []
    for code in symbols:
        quantity = held[code]
        close = close_on_or_before(ladder, code, as_of)
        if close is None:
            # 债券 / 待上市新股缺行情 → 按买入成本兜底（与资产表 degraded 口径一致）。
            cost = max(0, cost_basis.get(code, 0))
            if cost and (is_bond(code) or code in pending_new_stock):
                cost_fallback.append(code)
                market_value = cost
            else:
                missing_prices.append(code)
                market_value = None
        elif is_bond(code):
            market_value = bond_value_units(code, quantity, close, bond_divisors)
        else:
            market_value = quantity * close // 100_000_000
        rows.append(
            (
                market_value if market_value is not None else -1,
                Holding(
                    security_code=code,
                    security_name=names.get(code, code),
                    quantity_units=quantity,
                    cost_units=max(0, cost_basis.get(code, 0)),
                    market_value_units=market_value,
                    weight_ratio_units=None,
                ),
            )
        )

    # 持仓权重：mv / Σmv（行内和 100%），与 position_pct（mv/total_assets）分开。
    total_mv = sum(item.market_value_units or 0 for _, item in rows)
    weighted: list[Holding] = []
    for _, item in rows:
        weight = (
            ratio_units(item.market_value_units, total_mv)
            if item.market_value_units is not None
            else None
        )
        weighted.append(
            Holding(
                security_code=item.security_code,
                security_name=item.security_name,
                quantity_units=item.quantity_units,
                cost_units=item.cost_units,
                market_value_units=item.market_value_units,
                weight_ratio_units=weight,
            )
        )

    weighted.sort(key=lambda item: (-(item.market_value_units or 0), item.security_code))
    truncated = len(weighted) > HOLDINGS_MAX
    return weighted[:HOLDINGS_MAX], missing_prices, cost_fallback, truncated


__all__ = ["HOLDINGS_MAX", "PRICE_WINDOW_DAYS", "Holding", "holdings_at", "ratio_units"]
