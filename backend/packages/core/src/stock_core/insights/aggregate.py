"""回合洞察聚合与排序（设计 §3.7）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from stock_core.insights.calendar import holding_trading_days

RANKING_BASIS = "benchmark_excess_20d"
SAMPLE_MIN_RANK = 30
SAMPLE_MIN_RAW = 10
SWING_FILL_MIN = 2
SWING_FILL_MAX = 20


def weighted_mean(pairs: Sequence[tuple[int | None, int]]) -> int | None:
    """`(value_units, weight_units)` 金额加权均值；无有效样本/权重为 0 → None。"""
    total_weight = sum(weight for value, weight in pairs if value is not None and weight > 0)
    if total_weight <= 0:
        return None
    total = sum(value * weight for value, weight in pairs if value is not None and weight > 0)
    return total // total_weight


def combined_20(buy_excess_bps: int | None, sell_avoid_bps: int | None) -> int | None:
    """买卖两侧等权平均；单侧缺失沿用另一侧（设计 §3.7.4）。"""
    if buy_excess_bps is None and sell_avoid_bps is None:
        return None
    if buy_excess_bps is None:
        return sell_avoid_bps
    if sell_avoid_bps is None:
        return buy_excess_bps
    return (buy_excess_bps + sell_avoid_bps) // 2


def sample_status(n: int) -> str:
    """n≥30 可排名；10–29 样本不足；<10 不下结论。"""
    if n >= SAMPLE_MIN_RANK:
        return "ok"
    if n >= SAMPLE_MIN_RAW:
        return "insufficient"
    return "no_conclusion"


def quadrant(buy_excess_bps: int | None, sell_avoid_bps: int | None) -> str | None:
    """四象限：买/卖两侧是否为正（0 归入正侧，与“不亏”语义一致）。"""
    if buy_excess_bps is None or sell_avoid_bps is None:
        return None
    if buy_excess_bps >= 0 and sell_avoid_bps >= 0:
        return "both_positive"
    if buy_excess_bps >= 0:
        return "buy_positive_sell_negative"
    if sell_avoid_bps >= 0:
        return "buy_negative_sell_positive"
    return "both_negative"


@dataclass(slots=True, frozen=True)
class FillSwingStats:
    """fill 级波段配对结果（设计 §3.4）；与回合级统计**不可相加**。"""

    pairs: int
    wins: int
    pnl_units: tuple[int, ...]
    holding_trading_days: tuple[int, ...]


def swing_fill_pairs(
    fills: Sequence[tuple[date, date, int]],
    calendar: Sequence[date],
) -> FillSwingStats:
    """fill 级波段配对：`(open_date, close_date, pnl_units)` 的持有 TD 落在 2–20。

    fill 是回合内 FIFO 配对单位，与回合级 `bucket` 是两种统计单位（AC-S3）；
    日历不覆盖 open_date（持有 TD 为 None）的 fill 不计入，也不以 0 兜底。
    """
    rows: list[tuple[int, int]] = []
    for open_date, close_date, pnl_units in fills:
        held = holding_trading_days(calendar, open_date, close_date)
        if held is None or held < SWING_FILL_MIN or held > SWING_FILL_MAX:
            continue
        rows.append((held, pnl_units))
    return FillSwingStats(
        pairs=len(rows),
        wins=sum(1 for _, pnl in rows if pnl > 0),
        pnl_units=tuple(pnl for _, pnl in rows),
        holding_trading_days=tuple(held for held, _ in rows),
    )
