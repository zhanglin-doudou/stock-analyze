"""持仓重建辅助（Period2-07）：从成交回合流重建每日持仓。

零 I/O、零时钟、纯函数。输出的每日持仓表供 behavior.HHI / behavior.TO / timing.BETA 使用。

所有输入为标度整数（金额 scale=2 分、数量 scale=2 股、价格 scale=8 元），输出统一为
同标度整数；中间用 Decimal 保证精度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Final

from stock_contracts.constants import SCALE_AMOUNT, SCALE_PRICE, SCALE_QTY, SCALE_RATIO

# 标度常量（本地方便引用）
_MONEY_SF: Final[Decimal] = Decimal(10) ** SCALE_AMOUNT  # 1e2 分
_PRICE_SF: Final[Decimal] = Decimal(10) ** SCALE_PRICE  # 1e6 元
_QTY_SF: Final[Decimal] = Decimal(10) ** SCALE_QTY  # 1e4 股
_RATIO_SF: Final[Decimal] = Decimal(10) ** SCALE_RATIO  # 1e8 比率


@dataclass(frozen=True)
class HoldingLot:
    """标的在某个交易日末的一个持仓 lot。"""

    symbol: str
    quantity_units: int  # scale=2 股
    avg_cost_units: int  # scale=8 元（FIFO 均价）
    market_value_units: int  # scale=2 分（数量 × 收盘价）
    closing_price_units: int  # scale=8 元


@dataclass
class DailyPortfolio:
    """交易日末完整持仓（供 HHI/TO 等指标消费）。"""

    date_index: int  # 交易日序号（0-based）
    cash_units: int  # scale=2 分
    lots: list[HoldingLot] = field(default_factory=list)

    @property
    def total_assets_units(self) -> int:
        """总资产 = Σ mv + cash（scale=2 分）。"""
        return sum(lot.market_value_units for lot in self.lots) + self.cash_units

    @property
    def equity_mv_units(self) -> int:
        """权益类总市值（scale=2 分）。"""
        return sum(lot.market_value_units for lot in self.lots)


def build_daily_portfolio(
    *,
    prior_cash_units: int,
    prior_lots: dict[str, tuple[int, int]],  # symbol → (qty_units, avg_cost_units)
    day_fills: list[tuple[str, int, int, int]],  # (symbol, qty_units, price_units, fee_units)
    day_closes: dict[str, int],  # symbol → closing_price_units (scale=8)
    scale_money: int = SCALE_AMOUNT,
    scale_price: int = SCALE_PRICE,
) -> DailyPortfolio:
    """推进一个交易日的持仓状态。

    Args:
        prior_cash_units: 前一日末现金余额（scale=2 分）。
        prior_lots: 前一日末各标的持仓（symbol → (qty, avg_cost)）。
        day_fills: 当日成交序列（买为正 qty、卖为负 qty）。
        day_closes: 当日收盘价（未出现的 symbol 用上一次价——调用方负责保证；
                   本函数对缺失 price 的 symbol 保守用 avg_cost 代替）。

    Returns:
        当日末 DailyPortfolio。
    """
    with localcontext() as ctx:
        ctx.prec = 40
        cash = Decimal(prior_cash_units)
        # 复制 prior_lots，按当日成交更新
        lots: dict[str, list[Decimal]] = {
            s: [Decimal(q), Decimal(ac)] for s, (q, ac) in prior_lots.items()
        }

        for symbol, qty_u, price_u, fee_u in day_fills:
            qty = Decimal(qty_u)  # scale=2 股
            price = Decimal(price_u) / (Decimal(10) ** scale_price)  # → 真实元
            fee = Decimal(fee_u)  # scale=2 分

            # 现金变动：买入负（减）、卖出正（加）
            # Δcash = -qty × price(元) × 100(分/元) - fee
            # qty 已带符号（买正卖负）
            delta_cash = -qty * price * (Decimal(10) ** scale_money) - fee
            cash += delta_cash

            if symbol not in lots:
                if qty > 0:
                    lots[symbol] = [qty, price]
                continue

            cur_qty, cur_ac = lots[symbol]
            cur_qty += qty
            if qty > 0:
                # 买入 → FIFO 均价更新
                # new_avg = (old_avg × old_qty + price × buy_qty) / (old_qty + buy_qty)
                total_cost = cur_ac * (cur_qty - qty) + price * qty
                cur_ac = total_cost / cur_qty if cur_qty > 0 else Decimal(0)
            elif cur_qty <= 0:
                # 清仓 → 移除
                cur_qty = Decimal(0)
                cur_ac = Decimal(0)
                del lots[symbol]
            # 卖出且未清仓 → avg_cost 不变

            if symbol in lots:
                lots[symbol] = [cur_qty, cur_ac]

        # 生成 lot 列表
        result_lots: list[HoldingLot] = []
        for symbol, (qty, ac) in lots.items():
            if qty <= 0:
                continue
            close_u = day_closes.get(symbol)
            if close_u is not None:
                close_price = Decimal(close_u) / (Decimal(10) ** scale_price)
            else:
                close_price = ac  # 兜底：用上一次均价
            # market_value = qty(股) × close_price(元) × 100(分/元)
            mv_dec = qty * close_price * (Decimal(10) ** scale_money)

            close_u_actual = close_u if close_u is not None else int(
                (close_price * (Decimal(10) ** scale_price)).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )
            result_lots.append(
                HoldingLot(
                    symbol=symbol,
                    quantity_units=int(qty),
                    avg_cost_units=int(
                        (ac * (Decimal(10) ** scale_price)).quantize(
                            Decimal(1), rounding=ROUND_HALF_UP
                        )
                    ),
                    market_value_units=int(
                        mv_dec.quantize(Decimal(1), rounding=ROUND_HALF_UP)
                    ),
                    closing_price_units=close_u_actual,
                )
            )

        cash_int = int(cash.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return DailyPortfolio(date_index=0, cash_units=cash_int, lots=result_lots)
