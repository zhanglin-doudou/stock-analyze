"""05 引擎侧最小契约断言（07 在 `tests/contract/kit.py` 上扩展）。

覆盖：样本门禁、非 ok 无 value、无 float 序列化、compute 纯度（AST 静态扫描）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from stock_review.metrics.base import MetricDef
from stock_review.metrics.engine import (
    INSUFFICIENT_SAMPLE_STATUS,
    OK_SAMPLE_STATUS,
    PENDING_SAMPLE_STATUS,
    MetricOutcome,
)

FORBIDDEN_CALLS = {"float", "open", "input", "exec", "eval", "__import__"}
FORBIDDEN_MODULES = {
    "socket",
    "sqlalchemy",
    "psycopg",
    "requests",
    "httpx",
    "aiohttp",
    "random",
    "time",
}


def assert_no_float(value: Any, path: str = "$") -> None:
    """递归断言 JSON 载荷中没有 float（标度整数/定点字符串是唯一合法数值形态）。"""
    if isinstance(value, float):
        raise AssertionError(f"{path} 出现 float: {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_float(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_float(item, f"{path}[{index}]")


def assert_blocked_has_no_value(outcome: MetricOutcome) -> None:
    """blocked/stale/unreliable：`value_units` 必须缺省、CI 必须为空、不得物化。"""
    assert outcome.status != "ok", outcome.status
    assert outcome.value_units is None, outcome.value_units
    assert outcome.ci_low_units is None
    assert outcome.ci_high_units is None
    assert outcome.persistable is False


def assert_sample_gate(outcome: MetricOutcome, definition: MetricDef) -> None:
    """按 `sample_policy` 断言 sample_status 与 value 出口的一致性（§6.3）。"""
    policy = definition.sample_policy
    if outcome.n < policy.min_n:
        assert outcome.sample_status == INSUFFICIENT_SAMPLE_STATUS
    elif policy.min_trading_days > 0 and outcome.sample_status == PENDING_SAMPLE_STATUS:
        assert outcome.value_units is None
        assert outcome.reason == "PENDING_WINDOW"
    else:
        assert outcome.sample_status == OK_SAMPLE_STATUS
    if outcome.status != "ok":
        assert outcome.value_units is None
        assert outcome.reason is not None


def assert_compute_is_pure(module_path: str | Path) -> None:
    """AST 静态扫描：def 模块不得 import I/O/时钟模块、不得调用 float/open 等。"""
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in FORBIDDEN_MODULES, f"{module_path}: 禁止 import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in FORBIDDEN_MODULES, f"{module_path}: 禁止 from {node.module} import"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, f"{module_path}: 禁止调用 {node.func.id}()"


__all__ = [
    "assert_blocked_has_no_value",
    "assert_compute_is_pure",
    "assert_no_float",
    "assert_sample_gate",
]
