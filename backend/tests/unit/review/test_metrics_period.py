"""period token 三态解析单测（月度 / 年度 / 自定义区间）。"""

from __future__ import annotations

from datetime import date

import pytest
from stock_contracts.errors import ApiError, ErrorCode
from stock_review.metrics.context import (
    CUSTOM_MAX_DAYS,
    parse_period_label,
    period_is_cacheable,
    resolve_period,
)

TODAY = date(2026, 10, 30)


def test_month_token_is_closed_month() -> None:
    period = parse_period_label("2026-06", today=TODAY)
    assert period.kind == "month"
    assert period.label == "2026-06"
    assert period.start == date(2026, 6, 1)
    assert period.end == date(2026, 6, 30)
    assert period.cacheable is True


def test_year_token_is_closed_year() -> None:
    period = parse_period_label("2026", today=TODAY)
    assert period.kind == "year"
    assert period.label == "2026"
    assert period.start == date(2026, 1, 1)
    assert period.end == date(2026, 12, 31)
    assert period.cacheable is True


def test_custom_range_token_is_normalized_and_not_cacheable() -> None:
    period = parse_period_label(" 2026-01-01 .. 2026-06-30 ", today=TODAY)
    assert period.kind == "custom"
    assert period.label == "2026-01-01..2026-06-30"
    assert period.start == date(2026, 1, 1)
    assert period.end == date(2026, 6, 30)
    assert period.cacheable is False


def test_custom_range_allows_exactly_max_days() -> None:
    # 2025-01-01 + 365 天 = 2026-01-01，含端点共 366 天。
    period = parse_period_label("2025-01-01..2026-01-01", today=TODAY)
    assert (period.end - period.start).days + 1 == CUSTOM_MAX_DAYS


@pytest.mark.parametrize(
    "raw",
    [
        "2027-01",  # 未来月
        "2027",  # 未来年
        "2025-01-01..2026-01-02",  # 367 天
        "2026-06-30..2026-01-01",  # 起止倒置
        "2027-01-01..2027-03-01",  # 未来起点
        "2026-1-1..2026-06-30",  # 非 ISO 补零
        "2026-13",  # 非法月份
        "abcd",  # 非数字
        "",  # 空串
        "2026-06-01..",  # 缺终点
    ],
)
def test_invalid_period_tokens_raise_invalid_period(raw: str) -> None:
    with pytest.raises(ApiError) as exc:
        parse_period_label(raw, today=TODAY)
    assert exc.value.code is ErrorCode.INVALID_PERIOD


def test_period_is_cacheable_is_string_level() -> None:
    assert period_is_cacheable("2026-06") is True
    assert period_is_cacheable("2026") is True
    assert period_is_cacheable("2026-01-01..2026-06-30") is False
    assert period_is_cacheable(None) is True


def test_resolve_period_defaults_to_current_month() -> None:
    period = resolve_period(period=None, date_from=None, date_to=None, today=TODAY)
    assert period.label == "2026-10"
    assert period.kind == "month"


def test_resolve_period_accepts_year_token() -> None:
    period = resolve_period(period="2026", date_from=None, date_to=None, today=TODAY)
    assert period.kind == "year"
    assert period.label == "2026"
