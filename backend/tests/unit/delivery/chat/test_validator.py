from __future__ import annotations

from datetime import date

import pytest
from stock_contracts.chat.dto import Citation, Claim
from stock_delivery.chat.validator import validate

pytestmark = pytest.mark.no_db


def _citation() -> Citation:
    return Citation(
        citation_id="c1_1",
        label="收益",
        value_display="-1,234.56",
        source_endpoint="GET /api/v1/dashboard",
        definition_version="1.0",
        data_as_of=date(2026, 11, 18),
        number=1,
    )


def test_valid_answer_with_marker_and_number() -> None:
    result = validate(
        answer="本月收益 -1,234.56 元 [[1]]",
        claims=[Claim(claim_id="k1", text="本月收益 -1,234.56 元", citation_ids=("c1_1",))],
        citations=[_citation()],
        allow_tokens=["2026-11"],
    )
    assert result.ok, result.errors


def test_unknown_number_and_banned_advice_rejected() -> None:
    result = validate(
        answer="建议买入，目标价 99 元 [[1]]",
        claims=[],
        citations=[_citation()],
    )
    assert not result.ok
    assert any(item.startswith("unknown_number") for item in result.errors)
    assert "banned_advice" in result.errors


def test_number_precision_is_normalized() -> None:
    citation = Citation(
        citation_id="c1_1",
        label="成交价",
        value_display="215.300000",
        source_endpoint="GET /api/v1/trades",
        number=1,
    )
    result = validate(
        answer="成交价 215.30 元 [[1]]",
        claims=[],
        citations=[citation],
    )
    assert result.ok, result.errors


def test_integer_and_decimal_forms_match() -> None:
    citation = Citation(
        citation_id="c1_1",
        label="现金",
        value_display="78000.00",
        source_endpoint="GET /api/v1/dashboard",
        number=1,
    )
    result = validate(answer="现金 78000 元 [[1]]", claims=[], citations=[citation])
    assert result.ok, result.errors


def test_sign_magnitude_is_warning_not_error() -> None:
    citation = Citation(
        citation_id="c1_1",
        label="银行转取",
        value_display="-78000.00",
        source_endpoint="GET /api/v1/trades",
        number=1,
    )
    result = validate(answer="银行转取 78000 元 [[1]]", claims=[], citations=[citation])
    assert result.ok, result.errors
    assert any(item.startswith("sign_relaxed") for item in result.warnings)


def test_ordinals_dates_and_counts_do_not_blocks() -> None:
    result = validate(answer="第 3 笔，11 月 17 日，共 20 笔。", claims=[], citations=[])
    assert result.ok, result.errors
    assert any(item.startswith("nonfinancial_number") for item in result.warnings)
