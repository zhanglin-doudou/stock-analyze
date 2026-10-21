"""G6b 门禁①：一期端点实现覆盖 + 测试引用覆盖。

判据（Period2-Entry-Checklist G6）：
1. `docs/DEV-PLAN-v2.md` 附录 A.1 中「期次 = 一」的 API-ID 必须恰好 30 个；
2. 每个一期 API-ID 对应的 ``(METHOD, path)`` 必须出现在运行时
   ``create_app().openapi()``（含 ``openapi_meta`` enrich）中；
   API-075 ``/api/v1/openapi.json`` 是 FastAPI 自引用端点、不列入 ``paths``，
   与 ``stock_api.cli.coverage_map`` 口径一致，按「已实现」计；
3. 每个一期 API-ID 必须在 ``backend/tests/api/*.py`` + ``backend/tests/isolation/*.py``
   中被引用（``API-NNN`` 字符串或 ``test_api_NNN_`` 命名）。

缺失即打印清单并 exit 1；全绿打印 30/30。无 DB、无网络、不启动服务。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_PLAN = REPO_ROOT / "docs/DEV-PLAN-v2.md"
API_CATALOG = REPO_ROOT / "docs/architecture/v2/API-Catalog.md"
TEST_DIRS = (REPO_ROOT / "backend/tests/api", REPO_ROOT / "backend/tests/isolation")
API_PREFIX = "/api/v1"
ROOT_MARK = "（根）"
EXPECTED_PERIOD1 = 30
EXPECTED_ALL_ENDPOINTS = 80


def emit(*parts: object) -> None:
    """统一输出（ruff T20 禁止散落的 print）。"""
    sys.stdout.write(" ".join(str(part) for part in parts) + "\n")


def _section(text: str, heading: str, next_heading: str) -> str:
    matched = re.search(rf"^{heading}\b(.*?)(?=^{next_heading}|\Z)", text, re.S | re.M)
    if matched is None:
        raise SystemExit(f"无法定位文档段落：{heading}")
    return matched.group(1)


def _table_rows(block: str, ncols: int) -> list[list[str]]:
    """严格解析 Markdown 表格（只取恰好 ncols 列的数据行）。"""
    rows: list[list[str]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != ncols:
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def load_period1_ids() -> list[str]:
    """附录 A.1：一期（期次 = 一）的 API-ID 集合（期望 30 个）。"""
    text = DEV_PLAN.read_text(encoding="utf-8")
    block = _section(text, "### A\\.1", "### A\\.2")
    ids: list[str] = []
    for cells in _table_rows(block, 6):
        for index in (0, 3):
            api_id = cells[index]
            if re.fullmatch(r"API-\d{3}", api_id) and cells[index + 1] == "一":
                ids.append(api_id)
    return sorted(set(ids))


def load_catalog_endpoints() -> dict[str, dict[str, object]]:
    """API-Catalog §1：API-ID → {method, path, root}（80 行）。"""
    text = API_CATALOG.read_text(encoding="utf-8")
    block = _section(text, "## §1", "## §2")
    endpoints: dict[str, dict[str, object]] = {}
    for cells in _table_rows(block, 10):
        api_id = cells[0]
        if not re.fullmatch(r"API-\d{3}", api_id):
            continue
        raw_path = cells[2]
        endpoints[api_id] = {
            "method": cells[1].upper(),
            "path": raw_path.replace(ROOT_MARK, "").strip().strip("`").strip(),
            "root": ROOT_MARK in raw_path,
        }
    return endpoints


def load_runtime_operations() -> set[tuple[str, str]]:
    """运行时 OpenAPI 的 (method.lower, path) 集合（不连库）。"""
    from stock_api.app import create_app

    spec = create_app().openapi()
    operations: set[tuple[str, str]] = set()
    for path, item in spec.get("paths", {}).items():
        for method in item:
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                operations.add((method.lower(), path))
    return operations


def _candidate_paths(row: dict[str, object]) -> list[str]:
    path = str(row["path"])
    if path.startswith(API_PREFIX):
        return [path]
    if bool(row["root"]):
        return [path, API_PREFIX + path]
    return [API_PREFIX + path]


def _implementation_note(api_id: str, row: dict[str, object], runtime: set[tuple[str, str]]) -> str:
    path = str(row["path"])
    if path.endswith("/openapi.json"):
        return "FastAPI 自引用端点，不列入 spec.paths（按已实现计）"
    method = str(row["method"]).lower()
    for candidate in _candidate_paths(row):
        if (method, candidate) in runtime:
            return f"{row['method']} {candidate}"
    return f"未实现：{row['method']} {' 或 '.join(_candidate_paths(row))}"


def is_implemented(api_id: str, row: dict[str, object], runtime: set[tuple[str, str]]) -> bool:
    path = str(row["path"])
    if path.endswith("/openapi.json"):
        return True
    method = str(row["method"]).lower()
    return any((method, candidate) in runtime for candidate in _candidate_paths(row))


def load_test_references() -> set[str]:
    """扫描测试文件中的 API-ID 引用（``API-NNN`` 或 ``test_api_NNN_``）。"""
    referenced: set[str] = set()
    for directory in TEST_DIRS:
        for test_file in sorted(directory.glob("*.py")):
            text = test_file.read_text(encoding="utf-8")
            referenced.update(re.findall(r"API-\d{3}", text))
            referenced.update(f"API-{suffix}" for suffix in re.findall(r"test_api_(\d{3})_", text))
    return referenced


def main() -> int:
    emit("== G6b 端到端门禁①：一期端点实现覆盖 + 测试引用覆盖 ==")
    period1 = load_period1_ids()
    catalog = load_catalog_endpoints()
    runtime = load_runtime_operations()
    references = load_test_references()

    emit(f"文档：DEV-PLAN A.1 一期 API-ID {len(period1)} 个；API-Catalog §1 {len(catalog)} 个")
    emit(
        f"运行时：OpenAPI operation {len(runtime)} 个 / path {len({path for _, path in runtime})} 条"
    )
    emit(
        f"测试引用：{len(references)} 个不同 API-ID（backend/tests/api + backend/tests/isolation）"
    )
    emit("")

    failures: list[str] = []
    if len(period1) != EXPECTED_PERIOD1:
        failures.append(
            f"DEV-PLAN A.1 一期 API-ID 应为 {EXPECTED_PERIOD1} 个，实际 {len(period1)} 个"
        )
    if len(catalog) != EXPECTED_ALL_ENDPOINTS:
        failures.append(
            f"API-Catalog §1 应为 {EXPECTED_ALL_ENDPOINTS} 个端点，实际 {len(catalog)} 个"
        )

    missing_impl: list[str] = []
    missing_tests: list[str] = []
    for api_id in period1:
        row = catalog.get(api_id)
        if row is None:
            missing_impl.append(f"{api_id}（API-Catalog §1 未登记）")
            continue
        implemented = is_implemented(api_id, row, runtime)
        referenced = api_id in references
        if not implemented:
            missing_impl.append(f"{api_id} {row['method']} {row['path']}")
        if not referenced:
            missing_tests.append(api_id)
        mark = "✓" if implemented and referenced else "✗"
        emit(
            f"  [{mark}] {api_id} {row['method']!s:6s} {row['path']!s:38s} "
            f"impl={'yes' if implemented else 'NO ':3s} "
            f"test={'yes' if referenced else 'NO'}  {_implementation_note(api_id, row, runtime)}"
        )

    emit("")
    if missing_impl:
        failures.append(f"{len(missing_impl)} 个一期端点未实现：" + "、".join(missing_impl))
    if missing_tests:
        failures.append(f"{len(missing_tests)} 个一期端点无测试引用：" + "、".join(missing_tests))

    if failures:
        for failure in failures:
            emit(f"❌ {failure}")
        emit("❌ G6b 端点门禁失败")
        return 1
    emit(
        f"✅ G6b 端点门禁通过：实现 {len(period1)}/{EXPECTED_PERIOD1}；"
        f"测试引用 {len(period1)}/{EXPECTED_PERIOD1}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
