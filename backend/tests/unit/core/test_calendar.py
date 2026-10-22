"""交易日历 `business_days_between`（区间 (start, end]，同日记 1）单测。"""

from __future__ import annotations

from datetime import date

import pytest
from stock_core.fifo.calendar import business_days_between


def test_same_day_returns_one() -> None:
    assert business_days_between(date(2024, 1, 2), date(2024, 1, 2)) == 1


def test_end_before_start_returns_one() -> None:
    assert business_days_between(date(2024, 1, 10), date(2024, 1, 2)) == 1


def test_next_weekday_counts_one() -> None:
    # 周二 → 周三：(周二, 周三] = {周三}
    assert business_days_between(date(2024, 1, 2), date(2024, 1, 3)) == 1


def test_full_week_counts_four() -> None:
    # 周一 2024-01-01 → 周五 2024-01-05：(周一, 周五] = 周二三五四天
    assert business_days_between(date(2024, 1, 1), date(2024, 1, 5)) == 4


def test_weekend_span_counts_zero_but_engine_clamps() -> None:
    # 周六 → 周日：区间内没有工作日 → 0（引擎负责 max(., 1)）
    assert business_days_between(date(2024, 1, 6), date(2024, 1, 7)) == 0


def test_friday_to_monday_counts_monday_only() -> None:
    # 周五 2024-01-05 → 周一 2024-01-08：{周六, 周日, 周一} 中只有周一
    assert business_days_between(date(2024, 1, 5), date(2024, 1, 8)) == 1


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # 2024-01-02(周二) → 2024-01-10(周三)：1/3,1/4,1/5,1/8,1/9,1/10 = 6
        (date(2024, 1, 2), date(2024, 1, 10), 6),
        # 跨两周：2024-01-02 → 2024-01-15(周一) = 6 + 1/11,1/12,1/15 = 9
        (date(2024, 1, 2), date(2024, 1, 15), 9),
        # 全年 2024（闰年）自然日 365，工作日 = 262
        (date(2023, 12, 31), date(2024, 12, 31), 262),
    ],
)
def test_business_days_between_matrix(start: date, end: date, expected: int) -> None:
    assert business_days_between(start, end) == expected
