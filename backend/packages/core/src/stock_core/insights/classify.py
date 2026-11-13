"""回合洞察分类纯函数（设计 §3.5/§3.6；评审 P0-9 要求抽成 core 纯函数）。

只做判定，不做 I/O、不读时钟；阈值以标度整数表达，避免 float。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from stock_contracts.enums import RoundBucket

from stock_core.fifo.types import EventKind

# 阈值（比率 scale=1e-8；价格比较用百分比整数 100/103/95）
CONTINUED_DECLINE_B2 = -3 * 10**6  # -3%
WORST_CLOSE_DROP = 95  # P_net × 95%
SELL_FLY_PREMIUM = 103  # P_net × 103%
DRAWDOWN_DISCOUNT = 95  # 平均卖价 < 区间最高收盘 × 95%

# 卖飞分侧（账单旗标 `short_term_sold_early` 命中后，按净卖价与卖出前均价比较）。
SOLD_EARLY_STOP_LOSS = "stop_loss"
SOLD_EARLY_PROFIT = "profit"

# 日内调仓的对手方候选：只有真实二级市场证券成交才算「买入/卖出其它证券」。
# `CASH` 是资金伪代码（银证转账/费用/利息/红利税补缴），公司行动（分红/送转/配股）、
# 新股申购与货基申赎都不是换股；把它们算进来会把「卖出后补缴红利税」「卖出后申购
# 货基」误判成日内调仓。category 词表复用 FIFO 事件类型（唯一真相来源）。
CASH_SECURITY_CODE = "CASH"
REBALANCE_CATEGORIES: frozenset[str] = frozenset({EventKind.BUY.value, EventKind.SELL.value})


def is_rebalance_counterparty(*, security_code: str, category: str, is_money_fund: bool) -> bool:
    """该笔流水能否作为日内调仓的对手方（真实二级市场证券成交）。

    - `security_code == CASH`：资金流水，不是证券；
    - `category` 只接受 `buy` / `sell`：分红/送转/配股/新股申购/费用/转账/利息均非
      二级市场成交；
    - 货基申赎（`category` 为 buy/sell 但带货基标记）是现金管理，不是调仓。
    """
    return (
        security_code != CASH_SECURITY_CODE
        and category.strip().lower() in REBALANCE_CATEGORIES
        and not is_money_fund
    )


# 持有期分桶上界（交易日，闭区间；约 21 TD/月、5 TD/周）：
# 超短线 ≤3、短线 ≤10、中线 ≤126（6 个月），更长归长线。
BUCKET_SCALP_MAX_TD = 3
BUCKET_SHORT_MAX_TD = 10
BUCKET_POSITION_MAX_TD = 126


def bucket_of(holding_trading_days: int | None, *, status: str) -> str:
    """超短线/短线/中线/长线分桶；非 closed 或缺日历 → unknown。

    区间（交易日，含端点）：超短 0–3、短线 4–10、中线 11–126、长线 ≥127。
    """
    if status != "closed" or holding_trading_days is None:
        return RoundBucket.UNKNOWN
    if holding_trading_days <= BUCKET_SCALP_MAX_TD:
        return RoundBucket.SCALP
    if holding_trading_days <= BUCKET_SHORT_MAX_TD:
        return RoundBucket.SHORT
    if holding_trading_days <= BUCKET_POSITION_MAX_TD:
        return RoundBucket.POSITION
    return RoundBucket.LONG


def is_addon(quantity_before_units: int) -> bool:
    """买入前已有持仓 → 加仓（含当日先买后卖，按发生顺序）。"""
    return quantity_before_units > 0


def sold_early_side(price_net_sell_units: int, avg_cost_net_units: int | None) -> str | None:
    """卖飞分侧：净卖价 ≤ 卖出前均价 → 止损侧（含等号）；严格高于 → 止盈侧。

    成本缺失（`None`）或负成本（异常）无法分侧 → `None`；零成本是有效成本，
    任意正卖价归止盈侧。
    """
    if avg_cost_net_units is None or avg_cost_net_units < 0:
        return None
    if price_net_sell_units <= avg_cost_net_units:
        return SOLD_EARLY_STOP_LOSS
    return SOLD_EARLY_PROFIT


def is_round_drawdown(avg_sell_net_units: int | None, max_close_units: int | None) -> bool:
    """收益回撤：数量加权平均卖价严格低于区间最高收盘的 95%。"""
    if avg_sell_net_units is None or max_close_units is None:
        return False
    if avg_sell_net_units <= 0 or max_close_units <= 0:
        return False
    return avg_sell_net_units * 100 < max_close_units * DRAWDOWN_DISCOUNT


def is_partial_sell(quantity_units: int, quantity_before_units: int) -> bool:
    """部分卖出：卖出数量大于 0 且严格小于卖出前持仓。"""
    return 0 < quantity_units < quantity_before_units


def detect_t_patterns(
    events: Sequence[tuple[date, str, int, int]],
) -> tuple[bool, bool]:
    """识别日内做T / 波段做T。

    `events` 为本回合当前证券的账单，按 FIFO 稳定序排列，元素为
    `(交易日, 方向, 卖出数量, 卖出前持仓数量)`（买入笔后两个数量可忽略）。
    对每笔「部分卖出」取其后第一笔买入：同日 → 日内做T；更晚交易日 → 波段做T。
    同一笔卖出只按其后第一笔买入归类，已被日内配对的不再计入波段。
    """
    intraday = False
    swing = False
    for index, (day, side, quantity, quantity_before) in enumerate(events):
        if side != "sell" or not is_partial_sell(quantity, quantity_before):
            continue
        for next_day, next_side, _, _ in events[index + 1 :]:
            if next_side != "buy":
                continue
            if next_day == day:
                intraday = True
            else:
                swing = True
            break
    return intraday, swing


def has_same_day_after(
    earlier: Sequence[tuple[date, int]], later: Sequence[tuple[date, int]]
) -> bool:
    """`earlier` 中任一事件之后（同日、全局序号更大）存在 `later` 事件。

    用于日内调仓：先卖当前证券再看同日其它证券的买入，或先看同日其它证券的卖出
    再看当前证券的买入。序号取同一份 FIFO 稳定序的全局下标，保证「先后」可判定。
    """
    for day, index in earlier:
        if any(other_day == day and other_index > index for other_day, other_index in later):
            return True
    return False


def is_profit_taking(price_net_sell_units: int, avg_cost_net_units: int | None) -> bool:
    """卖出净价高于持仓成本净价 → 止盈型卖出；零成本时任意正卖价即止盈。

    `None`（无成本数据）与负成本（异常）不判定。
    """
    if avg_cost_net_units is None or avg_cost_net_units < 0:
        return False
    return price_net_sell_units > avg_cost_net_units


def sold_too_early(max_close_20_units: int | None, price_net_sell_units: int) -> bool:
    """20 个交易日内最高收盘 > 卖出净价 × 1.03（唯一主口径）。"""
    if max_close_20_units is None or price_net_sell_units <= 0:
        return False
    return max_close_20_units * 100 > price_net_sell_units * SELL_FLY_PREMIUM


def continued_decline(
    b2_20_units: int | None, worst_close_20_units: int | None, price_net_buy_units: int
) -> bool:
    """买后 20D：B2 ≤ −3% 或最差收盘 ≤ 净买价 × 0.95。"""
    if b2_20_units is not None and b2_20_units <= CONTINUED_DECLINE_B2:
        return True
    return (
        worst_close_20_units is not None
        and price_net_buy_units > 0
        and worst_close_20_units * 100 <= price_net_buy_units * WORST_CLOSE_DROP
    )
