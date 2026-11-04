"""真实交易日历工具（设计 D-03/D-05）。

日历 = 基准（默认 `hs300_tr`）日线日期集合，升序、去重。所有“交易日”计数都以此为准，
不再使用 `fifo/calendar.py` 的 weekday proxy。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date


def normalize(calendar: Sequence[date]) -> tuple[date, ...]:
    """去重 + 升序；调用方重复传入不会影响结果。"""
    return tuple(sorted(set(calendar)))


def holding_trading_days(calendar: Sequence[date], open_date: date, close_date: date) -> int | None:
    """`(open_date, close_date]` 的交易日数；同日=1；日历无法覆盖则 None。

    日历必须覆盖 `open_date`：`open_date` 晚于日历最后一天（行情尚未覆盖）时返回 None，
    避免把“行情缺失”误算成 0 个交易日。
    """
    if not calendar or open_date > calendar[-1]:
        return None
    if close_date <= open_date:
        return 1
    start = bisect_right(calendar, open_date)
    end = bisect_right(calendar, close_date)
    return end - start


def base_index(calendar: Sequence[date], day: date) -> int | None:
    """最后一个 `<= day` 的交易日序号（与 BHAR `j0` 锚定规则一致）；无 → None。"""
    index = bisect_right(calendar, day) - 1
    return index if index >= 0 else None


def nth_after(calendar: Sequence[date], index: int, horizon: int) -> date | None:
    """从 `index` 起严格向后第 `horizon` 个交易日；不足 → None。"""
    target = index + horizon
    return calendar[target] if 0 <= target < len(calendar) else None


def trading_days_after(calendar: Sequence[date], day: date) -> int:
    """严格晚于 `day` 的交易日数量（判断“是否已过 20 交易日”等）。"""
    return len(calendar) - bisect_right(calendar, day)


def is_late_annotated(calendar: Sequence[date], trade_date: date, annotated_on: date) -> bool:
    """标注日晚于 `d0 + 20` 交易日 → late（服务端计算，客户端不得传入）。"""
    if not calendar:
        return False
    target_index = base_index(calendar, trade_date)
    if target_index is None:
        return False
    cutoff = nth_after(calendar, target_index, 20)
    if cutoff is None:
        return False
    return annotated_on > cutoff


def count_between(calendar: Sequence[date], start: date, end: date) -> int:
    """`(start, end]` 内的交易日数（含端点校验，供 stale 距离判断）。"""
    if end <= start:
        return 0
    return (
        bisect_left(calendar, end) - bisect_right(calendar, start) + (1 if end in calendar else 0)
    )
