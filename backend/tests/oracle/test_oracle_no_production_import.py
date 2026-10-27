"""oracle 纯度守卫（§8.5 / Testing §9.3）：oracle 只允许标准库 + 包内相对导入。

生产代码禁止依赖 ``backend/tests/``；反向同理——oracle 不得 import
``stock_contracts / stock_core / stock_platform / stock_ledger / stock_review / stock_api``，
否则"独立参考实现"退化为生产代码的复读机，三方对拍失去意义。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ORACLE_DIR = Path(__file__).resolve().parent
FORBIDDEN_PREFIX = "stock_"


def _imported_roots(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 包内相对导入（.naive_stats 等）允许
                continue
            if node.module:
                roots.append(node.module.split(".")[0])
    return roots


def test_oracle_only_stdlib() -> None:
    files = sorted(ORACLE_DIR.glob("*.py"))
    assert files, f"{ORACLE_DIR}: 未发现 oracle 模块"

    violations: list[str] = []
    for path in files:
        if path.name == Path(__file__).name:
            continue
        for root in _imported_roots(path):
            if root.startswith(FORBIDDEN_PREFIX):
                violations.append(f"{path.name}: 禁止 import {root}（生产包）")
            elif root not in sys.stdlib_module_names:
                violations.append(f"{path.name}: 非标准库依赖 {root}")
    assert not violations, "oracle 纯度违规:\n" + "\n".join(violations)
