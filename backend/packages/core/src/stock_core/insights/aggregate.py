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

# 盈亏判定容差（Money scale=2 的标度整数）：|损益| ≤ 100.00 元计为「保本」。
# 调容差只改这一处：API 原样透出 flat_tolerance_display，前端不重复判定。
PNL_FLAT_TOLERANCE_UNITS = 100 * 100

PNL_CLASS_LOSS = "loss"
PNL_CLASS_FLAT = "flat"
PNL_CLASS_WIN = "win"
# 展示顺序：亏损 → 保本 → 盈利（柱状图从左到右）。
PNL_CLASS_ORDER: tuple[str, ...] = (PNL_CLASS_LOSS, PNL_CLASS_FLAT, PNL_CLASS_WIN)


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


@dataclass(slots=True, frozen=True)
class PnlHoldingStats:
    """单类盈亏（亏损/保本/盈利）的回合数与平均持有交易日。

    `avg_holding_trading_days_x10` 是「交易日 × 10」的定点整数（3.5 日 → 35），
    与全仓其它标度整数一致，避免 float 进入契约。
    """

    code: str
    count: int
    avg_holding_trading_days_x10: int | None


def pnl_class(
    pnl_units: int,
    *,
    tolerance_units: int = PNL_FLAT_TOLERANCE_UNITS,
) -> str:
    """盈亏分类：`|pnl| ≤ tolerance` → 保本；容差边界含在内。"""
    if pnl_units > tolerance_units:
        return PNL_CLASS_WIN
    if pnl_units < -tolerance_units:
        return PNL_CLASS_LOSS
    return PNL_CLASS_FLAT


def holding_by_pnl(
    rows: Sequence[tuple[int, int | None]],
    *,
    tolerance_units: int = PNL_FLAT_TOLERANCE_UNITS,
) -> tuple[PnlHoldingStats, ...]:
    """按盈亏分组统计平均持有交易日（设计：/rounds/insights 持有期分布图表）。

    `rows` 为已平仓回合的 `(pnl_units, holding_trading_days)`；三类**恒定返回三条**
    （无样本时 `count=0`、均值为 None，不省略分类，图表轴保持稳定）。
    持有交易日缺失的回合计入 `count` 但不参与均值（不以 0 兜底）。
    """
    counts: dict[str, int] = dict.fromkeys(PNL_CLASS_ORDER, 0)
    samples: dict[str, list[int]] = {code: [] for code in PNL_CLASS_ORDER}
    for pnl_units, holding in rows:
        code = pnl_class(pnl_units, tolerance_units=tolerance_units)
        counts[code] += 1
        if holding is not None:
            samples[code].append(holding)
    result: list[PnlHoldingStats] = []
    for code in PNL_CLASS_ORDER:
        values = samples[code]
        # 四舍五入到一位小数：sum × 10 后整数除（交易日非负，无需处理负半边）。
        avg_x10 = (sum(values) * 10 + len(values) // 2) // len(values) if values else None
        result.append(
            PnlHoldingStats(
                code=code,
                count=counts[code],
                avg_holding_trading_days_x10=avg_x10,
            )
        )
    return tuple(result)
