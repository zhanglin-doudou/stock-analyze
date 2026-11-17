"""证券持仓重建（Dashboard 与板块统计共用同一估值口径）。

``holdings_from_rounds`` 从 FIFO ``open`` 回合聚合持仓（数量+成本均来自回合，与
回合页面一致）；``holdings_at`` 从流水直接累加（移动加权平均成本，仅用于需要历史
时点持仓且无回合快照的场景）。两者共用 ``_value_holdings`` 估值，保证市值/权重
口径一致。

货币基金（现金等价物）不进持仓列表、并入现金余额；可转债发债代码归一；只有 A 股/
场内基金读行情，债券与伪代码按含费成本估值，待上市新股缺行情按认购成本兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from stock_ledger.models import Trade
from stock_platform.ports import MarketDataSource
from stock_platform.symbols import needs_quote

from stock_review.application.bond_alias import build_bond_alias, canonical_code
from stock_review.application.money_fund import money_fund_codes
from stock_review.application.rebuild import rights_resolution
from stock_review.application.valuation import (
    close_on_or_before,
    holding_delta,
    load_prices,
    new_stock_pending_codes,
    valued_position_units,
)
from stock_review.models import Round

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


async def _value_holdings(
    held: dict[str, int],
    cost_basis: dict[str, int],
    names: dict[str, str],
    *,
    bond_alias: dict[str, str],
    trades: list[Trade],
    as_of: date,
    market_data: MarketDataSource,
) -> tuple[list[Holding], list[str], list[str], bool]:
    """持仓估值共享逻辑：取价 → 估值 → 权重 → 排序截断。

    ``held``/``cost_basis``/``names`` 由调用方（从 rounds 或从 trades 累加）提供，
    本函数只负责估值口径，与回合/资产表保持一致。
    """
    symbols = sorted(held)
    # 只读缓存取价：与 asset_daily 快照同价，避免盘中补拉造成市值漂移。
    # 只有 A 股/场内基金需要行情；债券/伪代码按含费成本估值，不发起请求。
    ladder, _causes = await load_prices(
        market_data,
        [code for code in symbols if needs_quote(code)],
        as_of - timedelta(days=PRICE_WINDOW_DAYS),
        as_of,
        cached_only=True,
    )
    # 待上市新股：缺行情时按认购成本兜底（与资产表/净值同源）。
    pending_new_stock = {
        canonical_code(bond_alias, code) for code in new_stock_pending_codes(trades)
    }

    rows: list[tuple[int, Holding]] = []
    missing_prices: list[str] = []
    cost_fallback: list[str] = []
    for code in symbols:
        quantity = held[code]
        cost = max(0, cost_basis.get(code, 0))
        close = close_on_or_before(ladder, code, as_of) if needs_quote(code) else None
        market_value, source = valued_position_units(
            code, quantity, close, cost, new_stock_codes=pending_new_stock
        )
        if market_value is None:
            missing_prices.append(code)
        elif source in {"cost", "new_stock_cost"}:
            # 仅用于监控/日志：按成本估值的标的（非行情品种常态 + 待上市新股兜底）。
            cost_fallback.append(code)
        rows.append(
            (
                market_value if market_value is not None else -1,
                Holding(
                    security_code=code,
                    security_name=names.get(code, code),
                    quantity_units=quantity,
                    cost_units=cost,
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


async def holdings_at(
    trades: list[Trade],
    *,
    as_of: date,
    market_data: MarketDataSource,
) -> tuple[list[Holding], list[str], list[str], bool]:
    """按资产表同一口径从流水重建 ``as_of`` 收盘持仓（移动加权平均成本）。

    注意：本函数的**成本**为移动加权平均，与 FIFO 回合的剩余成本可能不同；若需与
    回合页面对齐，请用 ``holdings_from_rounds``。本函数保留用于无回合快照、需回溯
    历史时点持仓的场景。

    返回 ``(holdings, 缺价标的, 成本兜底标的, 是否截断)``。
    """
    bond_alias, _unresolved = build_bond_alias(
        (trade.security_code, trade.security_name) for trade in trades
    )
    # 配股前置校验：零金额且此前无持仓的配股按 broker_action 处理（与 FIFO/资产表同源）。
    # 降级只取决于「该笔之前」的流水，因此即使 trades 含未来行，结论也不受影响。
    resolution = rights_resolution(trades, bond_alias=bond_alias)
    holdings: dict[str, int] = {}
    cost_basis: dict[str, int] = {}
    names: dict[str, str] = {}
    fund_codes = money_fund_codes(trades)

    for trade in trades:
        if trade.trade_date > as_of:
            break
        raw_code = (trade.security_code or "").strip()
        if not raw_code:
            continue
        if raw_code in fund_codes:
            continue  # 货基是现金等价物：不进持仓列表（资产表现金列已含其余额）
        code = canonical_code(bond_alias, raw_code)
        trade_category = resolution.category(trade)
        delta = holding_delta(trade_category, trade.quantity_units, amount_units=trade.amount_units)
        if delta:
            # 超卖部分视同资金划转：持仓按 0 截断，不产生负持仓/负市值。
            old_qty = holdings.get(code, 0)
            new_qty = max(0, old_qty + delta)
            delta = new_qty - old_qty
            holdings[code] = new_qty
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

    return await _value_holdings(
        held,
        cost_basis,
        names,
        bond_alias=bond_alias,
        trades=trades,
        as_of=as_of,
        market_data=market_data,
    )


async def holdings_from_rounds(
    open_rounds: list[Round],
    *,
    trades: list[Trade],
    bond_alias: dict[str, str],
    as_of: date,
    market_data: MarketDataSource,
) -> tuple[list[Holding], list[str], list[str], bool]:
    """从 FIFO ``open`` 回合聚合当前持仓（与回合页面数量+成本口径完全一致）。

    每个 open 回合的 ``quantity_units`` + ``cost_units`` 是 FIFO 配对后的剩余持仓，
    按 ``security_code``（可转债归并）累加即得证券维度持仓。估值逻辑与
    ``holdings_at`` 共用 ``_value_holdings``。

    ``trades`` 仅用于 ``bond_alias``（如未传入）与待上市新股识别，不参与数量/成本
    计算；``data_error`` 回合跳过（与回合页面统计剔除一致）。

    降级：若 ``open_rounds`` 为空（用户尚未重建回合），回退到 ``holdings_at``
    （从流水累加，移动加权平均成本），保证看板/板块仍能展示持仓；回合重建后自动
    切换到 FIFO 口径。
    """
    if not open_rounds:
        return await holdings_at(trades, as_of=as_of, market_data=market_data)

    holdings: dict[str, int] = {}
    cost_basis: dict[str, int] = {}
    names: dict[str, str] = {}
    for round_ in open_rounds:
        if round_.data_error:
            continue
        raw_code = (round_.security_code or "").strip()
        if not raw_code:
            continue
        code = canonical_code(bond_alias, raw_code)
        holdings[code] = holdings.get(code, 0) + round_.quantity_units
        cost_basis[code] = cost_basis.get(code, 0) + round_.cost_units
        name = round_.security_name or code
        if code not in names:
            names[code] = name

    held = {code: qty for code, qty in holdings.items() if qty != 0}
    if not held:
        return [], [], [], False

    return await _value_holdings(
        held,
        cost_basis,
        names,
        bond_alias=bond_alias,
        trades=trades,
        as_of=as_of,
        market_data=market_data,
    )


__all__ = [
    "HOLDINGS_MAX",
    "PRICE_WINDOW_DAYS",
    "Holding",
    "holdings_at",
    "holdings_from_rounds",
    "ratio_units",
]
