"""G6b 门禁②：受保护端点越权覆盖（跨租户 / 401 / admin 403）。

判据（Period2-Entry-Checklist G6）：
1. 从运行时 ``create_app().openapi()`` 取 ``x-required-scope``；``public`` 与
   ``ops`` 标签端点不算受保护；
2. 每个受保护 operation 必须在 ``backend/tests/isolation/test_isolation_matrix.py``
   的矩阵中出现（RESOURCE_MATRIX / ADMIN_FORBIDDEN / LIST_ISOLATION_MATRIX /
   UNAUTHENTICATED_MATRIX），或在脚本内 ``EXEMPT`` 表显式豁免并写明原因；
3. 带路径参数的受保护 operation 必须有跨租户 404 用例（RESOURCE_MATRIX）；
4. 所有受保护 operation 必须有 401 未认证用例（UNAUTHENTICATED_MATRIX）；
5. 列表型端点 /trades /imports /jobs /reconciliation 必须有跨租户列表隔离用例。

缺失即打印清单并 exit 1。无 DB、无网络、不启动服务。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "backend/tests/isolation/test_isolation_matrix.py"
API_PREFIX = "/api/v1"

#: 受保护但不适合/不需要「跨租户资源 ID」用例的端点：显式豁免 + 原因。
#: key = (METHOD, path)；path 为去掉 /api/v1 前缀后的模板。
EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/auth/logout"): "只撤销当前凭证自己的会话/令牌族，无跨租户资源 ID；401 参数化已覆盖",
    ("GET", "/auth/me"): "只读当前凭证对应账号，无跨租户资源 ID；401 参数化已覆盖",
    ("DELETE", "/account"): "只注销当前账号并级联删除自有数据，无跨租户资源 ID；401 参数化已覆盖",
    ("POST", "/tokens"): "在本人账号下创建 PAT，无跨租户输入；401 参数化 + API-007 用例覆盖",
    ("GET", "/tokens"): "列表按 user_id 过滤；跨用户列表用例见 API-008（backend/tests/api）",
    (
        "POST",
        "/imports/preview",
    ): "文件预览只在本账号上下文解析、不落库，无资源 ID；API-010 用例覆盖",
    (
        "POST",
        "/imports",
    ): "基于本人 preview_id 提交导入，跨账号 preview 由 404 兜底；API-011 用例覆盖",
}

#: 必须有跨租户列表隔离用例的列表型端点。
REQUIRED_LIST_ISOLATION: tuple[tuple[str, str], ...] = (
    ("GET", "/trades"),
    ("GET", "/imports"),
    ("GET", "/jobs"),
    ("GET", "/reconciliation"),
)

_PROTECTED_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def emit(*parts: object) -> None:
    """统一输出（ruff T20 禁止散落的 print）。"""
    sys.stdout.write(" ".join(str(part) for part in parts) + "\n")


def _normalize(path: str) -> str:
    return path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path


def _parse_pairs(text: str, name: str) -> set[tuple[str, str]]:
    """解析测试文件中的 ``NAME: list[tuple[str, str]] = [...]`` 字面量。"""
    block = re.search(
        rf"^{re.escape(name)}\s*:\s*list\[tuple\[str,\s*str\]\]\s*=\s*\[(.*?)^\]",
        text,
        re.S | re.M,
    )
    if block is None:
        raise SystemExit(f"无法在 {MATRIX_PATH} 中解析 {name}")
    pairs = re.findall(r'\("([a-z]+)",\s*"([^"]+)"\)', block.group(1))
    if not pairs:
        raise SystemExit(f'{name} 为空或格式不符（期望 ("get", "/path") 元组）')
    return {(method.upper(), _normalize(path)) for method, path in pairs}


def load_matrix() -> dict[str, set[tuple[str, str]]]:
    text = MATRIX_PATH.read_text(encoding="utf-8")
    return {
        name: _parse_pairs(text, name)
        for name in (
            "RESOURCE_MATRIX",
            "ADMIN_FORBIDDEN",
            "LIST_ISOLATION_MATRIX",
            "UNAUTHENTICATED_MATRIX",
        )
    }


def load_protected_operations() -> list[dict[str, str]]:
    """运行时 OpenAPI 中非 public、非 ops 标签的 operation。"""
    from stock_api.app import create_app

    spec = create_app().openapi()
    protected: list[dict[str, str]] = []
    for path, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() not in _PROTECTED_METHODS:
                continue
            tags = [str(tag) for tag in operation.get("tags") or []]
            scope = operation.get("x-required-scope")
            if not isinstance(scope, str):
                raise SystemExit(f"{method.upper()} {path} 缺 x-required-scope（契约未 enrich？）")
            if scope == "public" or "ops" in tags:
                continue
            protected.append(
                {
                    "method": method.upper(),
                    "path": _normalize(path),
                    "operation_id": str(operation.get("operationId")),
                    "scope": scope,
                }
            )
    return protected


def main() -> int:
    emit("== G6b 端到端门禁②：受保护端点越权覆盖 ==")
    protected = load_protected_operations()
    matrix = load_matrix()
    all_matrix_pairs = set().union(*matrix.values())

    failures: list[str] = []
    covered: dict[tuple[str, str], list[str]] = {}
    for operation in protected:
        key = (operation["method"], operation["path"])
        sources = [name for name, pairs in matrix.items() if key in pairs]
        if key in EXEMPT:
            sources.append("EXEMPT")
        covered[key] = sources

    resource_required = [op for op in protected if "{" in op["path"]]
    missing_isolation = [f"{m} {p}" for (m, p), sources in covered.items() if not sources]
    missing_resource = [
        f"{op['method']} {op['path']}"
        for op in resource_required
        if not covered[(op["method"], op["path"])]
    ]
    missing_unauth = [
        f"{op['method']} {op['path']}"
        for op in protected
        if (op["method"], op["path"]) not in matrix["UNAUTHENTICATED_MATRIX"]
    ]
    missing_list = [
        f"{method} {path}"
        for method, path in REQUIRED_LIST_ISOLATION
        if (method, path) not in matrix["LIST_ISOLATION_MATRIX"]
    ]

    # EXEMPT 必须仍对应受保护端点，避免豁免表陈旧后无限豁免。
    protected_keys = {(op["method"], op["path"]) for op in protected}
    stale_exempt = sorted(f"{m} {p}" for m, p in EXEMPT if (m, p) not in protected_keys)
    stale_matrix = sorted(f"{m} {p}" for m, p in all_matrix_pairs if (m, p) not in protected_keys)

    emit(f"矩阵：{MATRIX_PATH.relative_to(REPO_ROOT)}")
    emit(
        "受保护 operation："
        f"{len(protected)}（public / ops 标签已排除）；"
        f"RESOURCE {len(matrix['RESOURCE_MATRIX'])} / ADMIN_FORBIDDEN {len(matrix['ADMIN_FORBIDDEN'])} / "
        f"LIST_ISOLATION {len(matrix['LIST_ISOLATION_MATRIX'])} / "
        f"UNAUTHENTICATED {len(matrix['UNAUTHENTICATED_MATRIX'])} / EXEMPT {len(EXEMPT)}"
    )
    emit("")
    for operation in protected:
        key = (operation["method"], operation["path"])
        sources = covered[key]
        mark = "✓" if sources else "✗"
        emit(
            f"  [{mark}] {operation['method']:6s} {operation['path']:36s} "
            f"scope={operation['scope']:5s} "
            f"via={', '.join(sources) if sources else 'MISSING'}"
        )
    emit("")

    if missing_isolation:
        failures.append(
            f"{len(missing_isolation)} 个受保护端点无隔离覆盖：" + "、".join(missing_isolation)
        )
    if missing_resource:
        failures.append(
            f"{len(missing_resource)} 个资源型端点无跨租户 404 用例：" + "、".join(missing_resource)
        )
    if missing_unauth:
        failures.append(
            f"{len(missing_unauth)} 个受保护端点无 401 用例：" + "、".join(missing_unauth)
        )
    if missing_list:
        failures.append(
            f"{len(missing_list)} 个列表端点无跨租户列表用例：" + "、".join(missing_list)
        )
    if stale_exempt:
        failures.append(f"EXEMPT 含非受保护/已删除端点：{'、'.join(stale_exempt)}")
    if stale_matrix:
        emit(f"⚠ 矩阵中引用非受保护（或不存在）端点（不计失败）：{'、'.join(stale_matrix)}")

    if failures:
        for failure in failures:
            emit(f"❌ {failure}")
        emit("❌ G6b 越权覆盖门禁失败")
        return 1
    emit(
        f"✅ G6b 越权覆盖门禁通过：受保护 {len(protected)}/{len(protected)}；"
        f"跨租户资源 {len(resource_required)}/{len(resource_required)}；"
        f"401 {len(protected)}/{len(protected)}；"
        f"列表隔离 {len(REQUIRED_LIST_ISOLATION)}/{len(REQUIRED_LIST_ISOLATION)}；"
        f"EXEMPT {len(EXEMPT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
