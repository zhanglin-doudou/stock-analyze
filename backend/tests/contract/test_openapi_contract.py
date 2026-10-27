"""OpenAPI 契约元数据 / 示例 / problem+json 校验（API-Catalog §7.4，无 DB）。"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import pytest
from stock_api.app import create_app
from stock_api.openapi_meta import (
    ERROR_SCHEMA,
    MONEY_FIELDS,
    OPERATION_META,
    PRICE_FIELDS,
    PROBLEM_CONTENT_TYPE,
    QUANTITY_FIELDS,
    RATIO_FIELDS,
    apply_contract_metadata,
    validate_contract,
)

_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_PRICE_RE = re.compile(r"^-?\d+\.\d{6}$")
_QUANTITY_RE = re.compile(r"^-?\d+\.\d{4}$")


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    operations: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                operations.append((method, path, operation))
    return operations


def _find_operation(spec: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for _, _, operation in _operations(spec):
        if operation.get("operationId") == operation_id:
            return operation
    raise AssertionError(f"operation {operation_id} 不存在")


def _walk_example_fields(value: Any, field: str | None = None) -> Any:
    """递归产出 (字段名, 值) 对（用于示例格式断言）。"""
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_example_fields(child, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_example_fields(item, field)


def _json_examples(spec: dict[str, Any]) -> Any:
    for _, _, operation in _operations(spec):
        for response in operation["responses"].values():
            for media_type, media in (response.get("content") or {}).items():
                if "example" in media:
                    yield media_type, media["example"]


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    """create_app().openapi()（enrich 后、模块级缓存；不连库）。"""
    return create_app().openapi()


# ── 基准：enrich 后的 spec 必须自洽 ────────────────────────────


def test_validate_contract_returns_no_violations(spec: dict[str, Any]) -> None:
    assert validate_contract(spec) == []


def test_covers_all_registered_operations(spec: dict[str, Any]) -> None:
    """49 个 operation（P2-06 新增 API-033/034/035/079）全部登记且与 OPERATION_META 一致。"""
    operations = _operations(spec)
    operation_ids = [operation["operationId"] for _, _, operation in operations]
    assert len(operations) == 49
    assert len(set(operation_ids)) == 49
    assert set(operation_ids) == set(OPERATION_META), "OPERATION_META 与实现端点漂移"
    paths = {path for _, path, _ in operations}
    # 探针/元数据端点（/api/v1/openapi.json 由 FastAPI 自引用，不在 paths 中）。
    assert {"/healthz", "/readyz", "/version", "/metrics", "/api/v1/version"} <= paths
    app = create_app()
    assert app.openapi_url == "/api/v1/openapi.json"
    assert app.docs_url == "/docs"


def test_every_operation_has_contract_metadata(spec: dict[str, Any]) -> None:
    for _, _, operation in _operations(spec):
        operation_id = operation["operationId"]
        assert operation["x-required-scope"] in {"public", "read", "write", "admin"}
        assert isinstance(operation["x-idempotent"], bool)
        assert isinstance(operation["x-async"], bool)
        codes = operation["x-error-codes"]
        assert isinstance(codes, list) and codes
        assert all(isinstance(code, str) and code.isupper() for code in codes)
        assert operation["x-required-scope"] == OPERATION_META[operation_id]["scope"]


def test_every_2xx_has_example(spec: dict[str, Any]) -> None:
    json_count = 0
    text_count = 0
    for _, _, operation in _operations(spec):
        for status, response in operation["responses"].items():
            if not str(status).startswith("2"):
                continue
            content = response.get("content") or {}
            if not content:
                continue  # 204/304 无 body
            assert any("example" in media for media in content.values()), (
                f"{operation['operationId']} {status} 缺少示例"
            )
            for media_type, _media in content.items():
                if media_type == "application/json":
                    json_count += 1
                else:
                    text_count += 1
    # 49 个 operation 中 2 个文本响应（SSE / Prometheus）、5 个 204 无 body，
    # 其余 42 个 2xx JSON 响应必须各带确定性示例（新增端点漏示例即失败）。
    assert json_count == 42
    assert text_count == 2  # SSE（API-063）+ Prometheus 文本（API-074）
    stream = _find_operation(spec, "streamJobEvents")["responses"]["200"]["content"]
    assert "text/event-stream" in stream
    metrics = _find_operation(spec, "metricsText")["responses"]["200"]["content"]
    assert "text/plain" in metrics


def test_every_error_status_has_problem_json(spec: dict[str, Any]) -> None:
    count = 0
    for _, _, operation in _operations(spec):
        declared = set(operation["x-error-codes"])
        for status, response in operation["responses"].items():
            if not str(status).startswith(("4", "5")):
                continue
            problem = (response.get("content") or {}).get(PROBLEM_CONTENT_TYPE)
            assert problem is not None, f"{operation['operationId']} {status} 缺 problem+json"
            assert problem["schema"]["$ref"].endswith(f"/{ERROR_SCHEMA}")
            example = problem["example"]
            for field in ("type", "title", "status", "detail", "code", "user_message"):
                assert field in example
            assert {"what_to_do", "retryable", "request_id"} <= set(example)
            assert example["status"] == int(status)
            assert example["code"] in declared
            count += 1
    assert count >= 100


def test_error_schema_fields(spec: dict[str, Any]) -> None:
    schema = spec["components"]["schemas"][ERROR_SCHEMA]
    required = set(schema["required"])
    for field in (
        "type",
        "title",
        "status",
        "detail",
        "code",
        "user_message",
        "what_to_do",
        "retryable",
        "request_id",
    ):
        assert field in required
        assert field in schema["properties"]
    errors = schema["properties"]["errors"]
    assert errors["type"] == "array"
    assert {"field", "code", "message"} <= set(errors["items"]["properties"])
    assert set(errors["items"]["required"]) == {"field", "code", "message"}


def test_example_money_and_ratio_formats(spec: dict[str, Any]) -> None:
    money_checked = 0
    price_checked = 0
    quantity_checked = 0
    ratio_checked = 0
    for _, example in _json_examples(spec):
        for field, value in _walk_example_fields(example):
            if field in MONEY_FIELDS:
                assert isinstance(value, str) and _MONEY_RE.match(value), (field, value)
                money_checked += 1
            elif field in PRICE_FIELDS:
                assert isinstance(value, str) and _PRICE_RE.match(value), (field, value)
                price_checked += 1
            elif field in QUANTITY_FIELDS:
                assert isinstance(value, str) and _QUANTITY_RE.match(value), (field, value)
                quantity_checked += 1
            elif field in RATIO_FIELDS:
                if isinstance(value, str):
                    assert re.match(r"^-?\d+\.\d+$", value), (field, value)
                else:
                    assert isinstance(value, (int, float)) and not isinstance(value, bool)
                    assert -1.0 <= float(value) <= 1.0
                ratio_checked += 1
    assert money_checked > 20
    assert price_checked > 3
    assert quantity_checked > 3
    assert ratio_checked >= 1


def test_trade_example_is_fixed_point(spec: dict[str, Any]) -> None:
    example = _find_operation(spec, "listTrades")["responses"]["200"]["content"][
        "application/json"
    ]["example"]
    trade = example["items"][0]
    assert _MONEY_RE.match(trade["amount"])
    assert _PRICE_RE.match(trade["price"])
    assert _QUANTITY_RE.match(trade["quantity"])
    assert trade["side"] == "buy"


# ── 元测试：删掉任一强制项 → validate 必须非空 ──────────────────

_OPERATION_MUTATIONS: list[tuple[str, Any, str]] = [
    ("missing_2xx_example", "缺少 example"),
    ("missing_problem_json", "缺少 " + PROBLEM_CONTENT_TYPE),
    ("missing_scope", "x-required-scope"),
    ("bad_idempotent", "x-idempotent"),
    ("bad_async", "x-async"),
    ("missing_error_codes", "x-error-codes"),
    ("unknown_error_code", "未登记错误码"),
    ("duplicate_operation_id", "operationId 重复"),
]


def _mutate(spec: dict[str, Any], name: str) -> None:
    if name == "missing_2xx_example":
        operation = _find_operation(spec, "listTrades")
        operation["responses"]["200"]["content"]["application/json"].pop("example")
    elif name == "missing_problem_json":
        operation = _find_operation(spec, "listTrades")
        operation["responses"]["400"]["content"].pop(PROBLEM_CONTENT_TYPE)
    elif name == "missing_scope":
        _find_operation(spec, "getMe").pop("x-required-scope")
    elif name == "bad_idempotent":
        _find_operation(spec, "getMe")["x-idempotent"] = "yes"
    elif name == "bad_async":
        _find_operation(spec, "getMe")["x-async"] = 1
    elif name == "missing_error_codes":
        _find_operation(spec, "getMe").pop("x-error-codes")
    elif name == "unknown_error_code":
        _find_operation(spec, "getMe")["x-error-codes"].append("NOT_A_REAL_CODE")
    elif name == "duplicate_operation_id":
        _find_operation(spec, "listTrades")["operationId"] = "getMe"
    else:  # pragma: no cover - 防拼写错误
        raise AssertionError(name)


@pytest.mark.parametrize(("mutation", "expected"), _OPERATION_MUTATIONS)
def test_validate_detects_operation_metadata_gaps(
    spec: dict[str, Any], mutation: str, expected: str
) -> None:
    broken = deepcopy(spec)
    _mutate(broken, mutation)
    problems = validate_contract(broken)
    assert problems, f"{mutation} 未被校验器发现"
    assert any(expected in problem for problem in problems), (mutation, problems)


_ERROR_SCHEMA_MUTATIONS: list[tuple[str, str]] = [
    ("missing_schema", "Error 缺失"),
    ("missing_required_field", "required 未包含 retryable"),
    ("missing_errors_field", "errors[]"),
]


def _mutate_error_schema(spec: dict[str, Any], name: str) -> None:
    schema = spec["components"]["schemas"][ERROR_SCHEMA]
    if name == "missing_schema":
        del spec["components"]["schemas"][ERROR_SCHEMA]
    elif name == "missing_required_field":
        schema["required"].remove("retryable")
    elif name == "missing_errors_field":
        schema["properties"]["errors"]["items"]["required"].remove("message")
    else:  # pragma: no cover - 防拼写错误
        raise AssertionError(name)


@pytest.mark.parametrize(("mutation", "expected"), _ERROR_SCHEMA_MUTATIONS)
def test_validate_detects_error_schema_gaps(
    spec: dict[str, Any], mutation: str, expected: str
) -> None:
    broken = deepcopy(spec)
    _mutate_error_schema(broken, mutation)
    problems = validate_contract(broken)
    assert problems, f"{mutation} 未被校验器发现"
    assert any(expected in problem for problem in problems), (mutation, problems)


# ── 确定性与幂等 ─────────────────────────────────────────────


def test_apply_contract_metadata_is_deterministic_and_idempotent(
    spec: dict[str, Any],
) -> None:
    assert apply_contract_metadata(spec) == spec
    twice = apply_contract_metadata(apply_contract_metadata(spec))
    assert twice == spec
    assert create_app().openapi() == spec
