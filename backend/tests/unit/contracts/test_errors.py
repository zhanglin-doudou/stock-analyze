"""stock_contracts.errors 单测：错误码总表的稳定性与完备性。"""

from __future__ import annotations

import re

from stock_contracts.errors import (
    CATALOG,
    DEGRADED_CODES,
    SEED_ERROR_COUNT,
    ApiError,
    ErrorCode,
    FieldError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
    problem_catalog,
    problem_for,
)

_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def test_seed_error_count_is_47() -> None:
    assert SEED_ERROR_COUNT == 47


def test_every_error_code_has_problem() -> None:
    assert set(CATALOG) == set(ErrorCode)
    for code in ErrorCode:
        problem = CATALOG[code]
        assert problem.code is code


def test_error_code_naming_is_stable_upper_snake() -> None:
    for code in ErrorCode:
        assert _UPPER_SNAKE.match(code.name), code.name
        assert code.value == code.name


def test_problem_fields_are_complete() -> None:
    for problem in CATALOG.values():
        assert problem.http_status >= 200
        assert problem.user_message  # 人话必填（NFR-12）
        assert problem.what_to_do
        assert isinstance(problem.retryable, bool)
        assert problem.title


def test_degraded_codes_are_http_200() -> None:
    assert DEGRADED_CODES
    for code in DEGRADED_CODES:
        assert CATALOG[code].http_status == 200
        assert code in CATALOG


def test_problem_catalog_and_for_roundtrip() -> None:
    catalog = problem_catalog()
    assert len(catalog) == len(CATALOG)
    assert {p.code for p in catalog} == set(ErrorCode)
    assert problem_for(ErrorCode.LOGIN_RATE_LIMITED).http_status == 429


def test_api_error_status_and_problem() -> None:
    err = ApiError(ErrorCode.VALIDATION_ERROR, errors=[FieldError("a", "bad", "x")])
    assert err.http_status == 422
    assert err.problem.code is ErrorCode.VALIDATION_ERROR
    assert str(err)


def test_error_subclasses_default_codes() -> None:
    assert NotFoundError().code is ErrorCode.NOT_FOUND
    assert ForbiddenError().code is ErrorCode.FORBIDDEN
    assert ValidationFailedError().code is ErrorCode.VALIDATION_ERROR
    assert ValidationFailedError([FieldError("f", "c", "m")]).errors[0].field == "f"
