"""交易日历（Period2-01，INTERFACE §2.4）。

纯函数、无节假日数据源：按周一至周五计工作日。节假日/停牌口径由上层数据源负责，
core 保持零 I/O。
"""

from __future__ import annotations

from datetime import date, timedelta


def business_days_between(start: date, end: date) -> int:
    """`(start, end]` 区间的工作日数；`end <= start` 返回 1（同日记 1）。"""
    if end <= start:
        return 1
    days = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days
