"""`stock_api.cli` —— 一期本地运维 / Mock 命令入口（对应 Makefile 目标）。

全部子命令都用 `uv run python -m stock_api.cli <cmd>` 调用；只依赖标准库
（`argparse` + `asyncio`），不引入 `click`/`typer` 等三方 CLI 依赖。

子命令一览：seed-tenants / seed-demo / api-check / api-snapshot / sdk /
api-coverage / verify-p1 / demo-p1。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from stock_contracts.clock import FrozenClock
from stock_contracts.enums import Role
from stock_contracts.errors import CATALOG
from stock_ledger.application.imports import commit_batch
from stock_ledger.application.preview import create_preview, default_object_store
from stock_ledger.fixtures import generate_huatai_bill, generate_market_quotes
from stock_ledger.repositories.imports import ImportBatchRepository
from stock_ledger.repositories.rules import seed_system_rules
from stock_platform.config import REPO_ROOT, get_settings
from stock_platform.db import Database
from stock_platform.jobs import JobQueue
from stock_platform.repositories.users import UserRepository
from stock_platform.security import hasher_for

# ── 常量 ────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"
ROOT_MARK = "（根）"
OPENAPI_SELF_REF = "/openapi.json"
DEMO_CLOCK = "2026-10-01T09:30:00+08:00"
DEFAULT_PASSWORD = "Demo!Passw0rd2026"
# 邮箱必须满足 EmailStr（带点的域名），否则 seed 出来的账号无法经 API 登录。
DEMO_ACCOUNTS: tuple[tuple[str, str, Role, int | None], ...] = (
    ("a@demo.example.com", "演示账号 A（有业务数据）", Role.USER, 7),
    ("b@demo.example.com", "演示账号 B（有业务数据）", Role.USER, 13),
    ("admin@demo.example.com", "运维管理员（不可读写业务明细）", Role.ADMIN, None),
)
DEMO_EMAIL = "demo@demo.example.com"

CATALOG_PATH = REPO_ROOT / "docs/architecture/v2/API-Catalog.md"
DEV_PLAN_PATH = REPO_ROOT / "docs/DEV-PLAN-v2.md"
CONTRACTS_PATH = REPO_ROOT / "contracts/openapi.json"
MARKET_DIR = REPO_ROOT / "fixtures/market"
SDK_OUTPUT = REPO_ROOT / "frontend/packages/api-client/src/api/generated/schema.d.ts"
SDK_INPUT_REL = "../contracts/openapi.json"


def emit(*parts: object) -> None:
    """统一输出行（绕过 ruff T20：CLI 大量输出，集中在此处）。"""
    sys.stdout.write(" ".join(str(part) for part in parts) + "\n")


def fail(message: str) -> int:
    emit(f"❌ {message}")
    return 1


# ── Markdown 文档解析 ───────────────────────────────────────────
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, number: int) -> str:
    match = re.search(rf"^## §{number}\b(.*?)(?=^## §|\Z)", text, re.S | re.M)
    if match is None:
        raise SystemExit(f"无法在 {CATALOG_PATH.name} 中定位 §{number}")
    return match.group(1)


def _table_rows(block: str, ncols: int) -> list[list[str]]:
    """严格解析 Markdown 表格：只取「恰好 ncols 列」的数据行（避开代码块里的 `|`）。"""
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


def _norm_path(raw: str) -> str:
    return raw.replace(ROOT_MARK, "").strip().strip("`").strip()


def load_catalog() -> tuple[list[dict[str, Any]], dict[str, str], set[str]]:
    """返回（§1 端点行, §6 API-ID→P, §3 错误码集合）。"""
    text = _read(CATALOG_PATH)
    endpoints: list[dict[str, Any]] = []
    for cells in _table_rows(_section(text, 1), 10):
        if cells[0] == "API-ID":
            continue
        endpoints.append(
            {
                "api_id": cells[0],
                "method": cells[1].upper(),
                "path": _norm_path(cells[2]),
                "root": ROOT_MARK in cells[2],
                "fr": cells[4],
                "priority": cells[8],
            }
        )
    acceptance = {
        cells[0]: cells[4]
        for cells in _table_rows(_section(text, 6), 5)
        if cells[0].startswith("API-")
    }
    codes = {
        cells[0] for cells in _table_rows(_section(text, 3), 6) if cells[0] and cells[0] != "code"
    }
    return endpoints, acceptance, codes


def load_phases() -> dict[str, str]:
    """DEV-PLAN 附录 A.1：API-ID → 期次（一/二/三）。"""
    text = _read(DEV_PLAN_PATH)
    match = re.search(r"^### A\.1\b(.*?)(?=^### |^## |\Z)", text, re.S | re.M)
    if match is None:
        return {}
    phases: dict[str, str] = {}
    for cells in _table_rows(match.group(1), 6):
        for index in (0, 3):
            if cells[index].startswith("API-"):
                phases[cells[index]] = cells[index + 1]
    return phases


# ── OpenAPI 契约 ───────────────────────────────────────────────
def openapi_spec() -> dict[str, Any]:
    from stock_api.app import create_app

    return create_app().openapi()


def spec_operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    operations: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                operations.append((method.lower(), path, operation))
    return operations


def coverage_map(
    endpoints: list[dict[str, Any]], spec_set: set[tuple[str, str]]
) -> dict[str, bool]:
    covered: dict[str, bool] = {}
    for row in endpoints:
        path = row["path"]
        if path.endswith(OPENAPI_SELF_REF):
            # OpenAPI 文档端点（契约快照）：FastAPI 自引用，不列入 spec.paths，视为已实现
            covered[row["api_id"]] = True
            continue
        if path.startswith(API_PREFIX):
            candidates = [path]
        elif row["root"]:
            # 标注「根」的端点：真实路径不在公共前缀下（如 /healthz、/version）
            candidates = [path, API_PREFIX + path]
        else:
            # 总表 Path 省略公共前缀，真实路径一定带前缀
            candidates = [API_PREFIX + path]
        method = row["method"].lower()
        covered[row["api_id"]] = any((method, cand) in spec_set for cand in candidates)
    return covered


# ── api-check ──────────────────────────────────────────────────
def _api_check() -> int:
    spec = openapi_spec()
    operations = spec_operations(spec)
    spec_set = {(method, path) for method, path, _ in operations}
    endpoints, acceptance, section3_codes = load_catalog()
    phases = load_phases()
    covered = coverage_map(endpoints, spec_set)

    emit("== 契约完整性检查（api-check）==")
    emit(f"spec：{len(spec.get('paths', {}))} 条路径 / {len(operations)} 个操作")
    emit(f"文档：{len(endpoints)} 个 API-ID（§1）/ {len(acceptance)} 条验收（§6）")

    # (a) 端点覆盖：一期缺失只报「未实现」不判失败（以 §6 P0/P1 且属于一期 为口径）
    missing_required: list[dict[str, Any]] = []
    missing_later: list[dict[str, Any]] = []
    for row in endpoints:
        if covered[row["api_id"]]:
            continue
        required = phases.get(row["api_id"]) == "一" and acceptance.get(row["api_id"]) in (
            "P0",
            "P1",
        )
        (missing_required if required else missing_later).append(row)
    emit(
        f"[a] 端点覆盖：已实现 {sum(covered.values())}/{len(endpoints)}；"
        f"一期未实现 {len(missing_required)} 个；后续期次未实现 {len(missing_later)} 个"
    )
    for row in missing_required:
        emit(
            f"    · 未实现（一期 {acceptance.get(row['api_id'], '?')}）："
            f"{row['api_id']} {row['method']} {row['path']}"
        )
    if missing_later:
        listed = "、".join(row["api_id"] for row in missing_later)
        emit(f"    · 未实现（后续期次，非本期范围）：{listed}")
    for row in endpoints:
        if row["path"].endswith(OPENAPI_SELF_REF):
            emit(
                f"    · {row['api_id']} {row['path']}：契约快照端点，"
                "FastAPI OpenAPI 自引用不列入 paths（按已实现计）"
            )

    # (b) operationId 唯一
    # spec 是 FastAPI 生成的动态 dict，`.get()` 静态为 `Any | None`；标注 list[Any]
    # 以便下游 sorted/join 通过（运行时不做转换，语义与原先一致）。
    operation_ids: list[Any] = [operation.get("operationId") for _, _, operation in operations]
    duplicates = sorted({oid for oid in operation_ids if operation_ids.count(oid) > 1})

    # (c) §3 错误码 vs stock_contracts.errors.CATALOG
    catalog_codes = {code.value for code in CATALOG}
    codes_missing_in_catalog = sorted(section3_codes - catalog_codes)
    codes_extra_in_catalog = sorted(catalog_codes - section3_codes)
    emit(
        f"[c] 错误码：§3={len(section3_codes)} CATALOG={len(catalog_codes)}"
        f"（§3 缺于 CATALOG {len(codes_missing_in_catalog)} 个；"
        f"CATALOG 多于 §3 {len(codes_extra_in_catalog)} 个）"
    )
    if codes_extra_in_catalog:
        emit(
            "    · 提示：以下 code 已在 CATALOG 实现但 §3 表未收录，建议补录文档："
            + "、".join(codes_extra_in_catalog)
        )

    # (d) 非运维端点必须用 application/problem+json 声明错误响应（RFC 7807）
    without_problem: list[tuple[str, str, str]] = []
    for method, path, operation in operations:
        if "ops" in (operation.get("tags") or []):
            continue
        responses = operation.get("responses", {})
        has_problem = any(
            not str(code).startswith("2")
            and "application/problem+json" in (response.get("content") or {})
            for code, response in responses.items()
        )
        if not has_problem:
            without_problem.append((method, path, str(operation.get("operationId"))))
    emit(
        f"[d] 错误响应：非运维端点 {len(operations) - _ops_count(operations)} 个，"
        f"缺 problem+json {len(without_problem)} 个"
    )
    for method, path, oid in without_problem:
        emit(f"    · {method.upper()} {path}（{oid}）无 application/problem+json 错误响应")

    # (e) §7.4 契约元数据：2xx 示例 / x-* / Error schema / problem+json 引用 / 码一致
    from stock_api.openapi_meta import validate_contract

    contract_violations = validate_contract(spec)
    emit(f"[e] 契约元数据（§7.4）：违规 {len(contract_violations)} 条")
    for violation in contract_violations[:20]:
        emit(f"    · {violation}")
    if len(contract_violations) > 20:
        emit(f"    · … 其余 {len(contract_violations) - 20} 条省略")

    failures: list[str] = []
    if missing_required:
        missing_ids = "、".join(row["api_id"] for row in missing_required)
        failures.append(f"一期端点未实现：{missing_ids}")
    if duplicates:
        failures.append(f"operationId 不唯一：{'、'.join(duplicates)}")
    if codes_missing_in_catalog:
        failures.append(f"§3 错误码未在 CATALOG 实现：{'、'.join(codes_missing_in_catalog)}")
    if without_problem:
        failures.append(f"{len(without_problem)} 个端点缺少 problem+json 错误响应")
    if contract_violations:
        failures.append(f"契约元数据违规 {len(contract_violations)} 条（见 [e]）")

    emit("")
    if failures:
        emit("❌ api-check 失败：")
        for item in failures:
            emit(f"    - {item}")
        return 1
    emit("✅ api-check 通过（端点覆盖 / operationId 唯一 / 错误码一致 / 示例与 x-* 齐备）")
    if missing_required:
        emit(
            f"⚠ 注意：{len(missing_required)} 个一期端点尚未实现（见上文 [a] 段），"
            "按约定只报「未实现」，不阻断。"
        )
    return 0


def _ops_count(operations: list[tuple[str, str, dict[str, Any]]]) -> int:
    return sum(1 for _, _, operation in operations if "ops" in (operation.get("tags") or []))


# ── api-coverage ───────────────────────────────────────────────
def _api_coverage() -> int:
    spec = openapi_spec()
    spec_set = {(method, path) for method, path, _ in spec_operations(spec)}
    endpoints, _, _ = load_catalog()
    covered = coverage_map(endpoints, spec_set)
    phases = load_phases()

    order = ["L1", "L2", "L3", "L4", "L5", "其他（L6+/非 L）"]
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key in order}
    for row in endpoints:
        match = re.search(r"L(\d+)", row["fr"])
        key = f"L{match.group(1)}" if match else order[-1]
        if key not in groups:
            key = order[-1]
        groups[key].append(row)

    emit("== API 覆盖（api-coverage，按 §1 FR 的 L 值分组）==")
    for key in order:
        items = groups[key]
        if not items:
            continue
        done = sum(1 for row in items if covered[row["api_id"]])
        emit(f"\n[{key}] 覆盖 {done}/{len(items)}")
        for row in items:
            mark = "✓" if covered[row["api_id"]] else "✗"
            phase = phases.get(row["api_id"], "?")
            emit(f"  {mark} {row['api_id']} {row['method']} {row['path']}（{phase}期）")
    return 0


# ── api-snapshot ───────────────────────────────────────────────
def _normalized_spec() -> str:
    spec = openapi_spec()
    return json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _git_drift() -> int | None:
    try:
        result = subprocess.run(
            ["git", "diff", "--exit-code", "--", "contracts/openapi.json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:  # pragma: no cover - 无 git 环境
        return None
    return result.returncode


def _api_snapshot(check: bool) -> int:
    text = _normalized_spec()
    if check:
        if not CONTRACTS_PATH.exists():
            return fail(
                f"{CONTRACTS_PATH.relative_to(REPO_ROOT)} 不存在；"
                "请先运行 `uv run python -m stock_api.cli api-snapshot` 生成快照"
            )
        current = CONTRACTS_PATH.read_text(encoding="utf-8")
        if current != text:
            emit("契约快照与当前实现不一致（存在漂移）：")
            emit(f"  已提交/工作区 {len(current)} 字节 vs 重新生成 {len(text)} 字节")
            emit("  重新运行 api-snapshot 覆盖，或确认变更后提交 contracts/openapi.json")
            return 1
        emit("✅ 契约快照无漂移（与 create_app().openapi() 规范化结果一致）")
        return 0

    CONTRACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACTS_PATH.write_text(text, encoding="utf-8")
    emit(f"已写入 {CONTRACTS_PATH.relative_to(REPO_ROOT)}（{len(text)} 字节）")
    drift = _git_drift()
    if drift == 0:
        emit("git diff --exit-code：快照与版本库一致，无未提交漂移")
    elif drift == 1:
        emit("git diff --exit-code：存在未提交漂移（刚生成的快照尚未提交）")
    else:
        emit("git diff --exit-code：当前非 git 工作区，跳过漂移判定")
    return 0


# ── sdk ────────────────────────────────────────────────────────
def _sdk() -> int:
    if not CONTRACTS_PATH.exists():
        return fail(
            "contracts/openapi.json 不存在；请先运行 `uv run python -m stock_api.cli api-snapshot`"
        )
    SDK_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # 生成器固定在 frontend/packages/api-client 的 devDependencies（7.4.4），
    # 走 workspace 本地 bin，避免 `pnpm dlx` 临时下载导致的版本漂移/离线不可复现。
    command = [
        "pnpm",
        "--filter",
        "@sa/api-client",
        "exec",
        "openapi-typescript",
        str(CONTRACTS_PATH),
        "-o",
        str(SDK_OUTPUT),
    ]
    emit(f"→ 在 frontend/ 执行：{' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT / "frontend",
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return fail(
            "未找到 pnpm 可执行文件。请安装 pnpm（https://pnpm.io）后重试："
            "`cd frontend && pnpm dlx openapi-typescript ...`"
        )
    if result.returncode != 0:
        emit(result.stdout.strip())
        emit(result.stderr.strip())
        return fail(
            "openapi-typescript 生成失败（常见原因：frontend 依赖未安装）。"
            "请执行 `cd frontend && pnpm install` 后重试："
            "`cd frontend && pnpm --filter @sa/api-client exec openapi-typescript "
            "../contracts/openapi.json -o packages/api-client/src/api/generated/schema.d.ts`"
        )
    emit(f"✅ 已生成 {SDK_OUTPUT.relative_to(REPO_ROOT)}")
    return 0


# ── verify-p1 ──────────────────────────────────────────────────
# 一期 DoD 的完整门禁：静态检查 + 分层测试 + 一期 API 验收 + 越权/RLS +
# 契约完整性/快照一致性 + 前端 tsc/vitest/build。缺任一环都不算“一期验收通过”。
VERIFY_STEPS: tuple[tuple[str, list[str]], ...] = (
    ("ruff check", ["uv", "run", "ruff", "check", "backend"]),
    ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "backend"]),
    ("mypy (strict)", ["uv", "run", "mypy", "backend/packages", "backend/services"]),
    ("import-linter (L1~L5)", ["uv", "run", "lint-imports"]),
    (
        "pytest 单测（不含 api/integration）",
        ["uv", "run", "pytest", "-m", "not integration and not api", "-q"],
    ),
    (
        "core 覆盖率 100%",
        [
            "uv",
            "run",
            "pytest",
            "backend/tests/unit/core",
            "--cov=stock_core",
            "--cov-fail-under=100",
            "-q",
        ],
    ),
    ("pytest 一期 API 验收", ["uv", "run", "pytest", "backend/tests/api", "-q"]),
    ("pytest 越权矩阵", ["uv", "run", "pytest", "-m", "isolation", "-q"]),
    (
        "pytest RLS 直连",
        ["uv", "run", "pytest", "backend/tests/integration/test_rls_isolation.py", "-q"],
    ),
    ("pytest 架构约束", ["uv", "run", "pytest", "backend/tests/architecture", "-q"]),
    (
        "契约快照无漂移",
        ["uv", "run", "python", "-m", "stock_api.cli", "api-snapshot", "--check"],
    ),
    (
        "api-check（覆盖/错误码/示例/x-*）",
        ["uv", "run", "python", "-m", "stock_api.cli", "api-check"],
    ),
    (
        "gate 端点覆盖（Period1 30/30）",
        ["uv", "run", "python", "scripts/ci/gate_endpoint_coverage.py"],
    ),
    (
        "gate 越权覆盖（受保护端点）",
        ["uv", "run", "python", "scripts/ci/gate_tenant_coverage.py"],
    ),
    (
        "gate RLS 覆盖（platform/ledger/review）",
        ["uv", "run", "python", "scripts/ci/gate_rls_coverage.py"],
    ),
    ("前端 tsc typecheck", ["pnpm", "-C", "frontend", "-r", "typecheck"]),
    ("前端 vitest", ["pnpm", "-C", "frontend", "-r", "test"]),
    ("前端生产构建", ["pnpm", "-C", "frontend", "-r", "build"]),
)


def _verify_p1() -> int:
    emit("== verify-p1：一期质量门禁（一条命令全绿）==")
    emit(f"共 {len(VERIFY_STEPS)} 步；任一步失败即中止并打印已通过清单。\n")
    passed: list[str] = []
    for index, (name, command) in enumerate(VERIFY_STEPS, start=1):
        emit(f"[{index}/{len(VERIFY_STEPS)}] ▶ {name}")
        emit(f"          $ {' '.join(command)}")
        result = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if result.returncode != 0:
            emit(f"\n❌ verify-p1 中止：步骤 «{name}» 失败（exit {result.returncode}）")
            emit(f"   失败命令：{' '.join(command)}")
            emit("\n勾选清单：")
            for done in passed:
                emit(f"  [x] {done}")
            emit(f"  [ ] {name}  ← 失败")
            for remaining in VERIFY_STEPS[index:]:
                emit(f"  [ ] {remaining[0]}")
            return 1
        passed.append(name)
        emit(f"          ✅ {name} 通过\n")

    emit("✅ verify-p1 全绿（一期 DoD 全部门禁通过）")
    emit("\n勾选清单：")
    for done in passed:
        emit(f"  [x] {done}")
    emit(
        "\n一期端点：30/30 已实现（见 api-check 输出）；API 验收用例覆盖见 backend/tests/api/*.py。"
    )
    return 0


# ── seed-tenants / seed-demo ───────────────────────────────────
def _seed_plan() -> list[str]:
    return [
        "1. 写入系统分类规则（ledger.summary_rule，幂等）",
        "2. 账号 A a@demo.example.com（user）：导入一份正常账单",
        "3. 账号 B b@demo.example.com（user）：导入一份正常账单",
        "4. 账号 admin@demo.example.com（admin）：仅建账号，无业务数据",
        f"统一口令：{DEFAULT_PASSWORD}（三个账号共用）",
    ]


async def _ensure_user(db: Database, email: str, note: str, role: Role, password_hash: str) -> Any:
    async with db.session(user_id=None) as session:
        repo = UserRepository(session)
        user = await repo.by_email(email)
        if user is None:
            user = await repo.create(
                email=email, password_hash=password_hash, role=role, display_name=note
            )
            emit(f"  + 新建账号 {email}（{role}）")
        else:
            emit(f"  = 账号已存在 {email}（{user.role}），跳过创建")
        return user.id


async def _import_bill(
    db: Database, clock: FrozenClock, user_id: Any, data: bytes, file_name: str
) -> None:
    async with db.session(user_id=user_id) as session:
        stats = await ImportBatchRepository(session).aggregate_stats(user_id)
        existing = sum(stats.values())
        if existing > 0:
            emit(f"  = 已有 {existing} 个导入批次，跳过导入（幂等）")
            return
        queue = JobQueue(db, clock)
        preview = await create_preview(
            session,
            clock=clock,
            settings=get_settings(),
            user_id=user_id,
            data=data,
            file_name=file_name,
            object_store=default_object_store(),
        )
        emit(
            f"  预览：{preview.row_count} 行 / 解析 {preview.parsed_count} 行 / "
            f"隔离 {preview.quarantined_count} 行 / 预估新增 {preview.estimated_inserted} 行"
        )
        params = {"preview_id": preview.preview_id, "mode": "full"}
        job_id, _ = await queue.enqueue(
            session,
            kind="import_commit",
            user_id=user_id,
            scope_key=f"{user_id}:{preview.preview_id}",
            params=params,
        )
        job = {"id": job_id, "user_id": str(user_id), "params": params}
        result = await commit_batch(queue, session, job, clock)
        emit(f"  提交结果：{json.dumps(result, ensure_ascii=False, default=str)}")

        emit("  分类：已在 commit 阶段按规则写入（未命中摘要进隔离区）")


async def _seed(db: Database, clock: FrozenClock, market_bill: bool) -> int:
    async with db.session(user_id=None) as session:
        seeded = await seed_system_rules(session)
    emit(f"系统分类规则：新增 {seeded} 条（幂等，重复执行为 0）")

    password_hash = hasher_for().hash(DEFAULT_PASSWORD)
    for email, note, role, seed in DEMO_ACCOUNTS:
        emit(f"\n账号 {email}：{note}")
        user_id = await _ensure_user(db, email, note, role, password_hash)
        if seed is None:
            emit("  = 管理员仅建账号，不写入业务数据")
            continue
        data = generate_huatai_bill(seed=seed, months=3, count=120)
        file_name = "bill_huatai.xlsx" if market_bill else "huatai_2024.xlsx"
        await _import_bill(db, clock, user_id, data, file_name)
    return 0


def write_market_fixtures() -> dict[str, Path]:
    """确定性行情/账单夹具（离线可复现，写入 fixtures/market/）。

    行情 JSON 与 `stock_ledger.market.ReplayMarketDataSource` 的读取契约同源：
    由 `generate_market_quotes()` 生成（固定日期/排序、无运行时刻戳），
    重复执行写出的 bytes 完全一致，不产生非确定性 git diff。
    """
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    bill = MARKET_DIR / "bill_huatai.xlsx"
    bill.write_bytes(generate_huatai_bill(seed=42, months=3, count=120))
    quotes = MARKET_DIR / "quotes.json"
    quotes.write_bytes(generate_market_quotes())
    return {"bill": bill, "quotes": quotes}


async def _run_seed(label: str, market_bill: bool) -> int:
    db = Database(get_settings(), role="app")
    try:
        await _seed(db, FrozenClock(DEMO_CLOCK), market_bill=market_bill)
    except Exception as exc:
        emit(f"\n❌ {label} 失败：{type(exc).__name__}: {exc}")
        if isinstance(exc, KeyError) and str(exc) == "4":
            emit(
                "   上游缺口：stock_core/values.py 的 _QUANT 缺 scale=4（数量 1e-4 定点），"
                "导致 Qty.__str__ 抛 KeyError；"
            )
            emit(
                '   需上游在 `_QUANT` 中补齐 `4: Decimal("0.0001")` 后，'
                "账单预览/导入方能跑通（本 CLI 不越权修改 packages）。"
            )
        else:
            emit("   请检查本地 PostgreSQL 可用性与 .env 配置。")
        return 1
    finally:
        await db.dispose()
    emit(f"\n✅ {label} 完成")
    return 0


async def _seed_demo() -> int:
    fixtures = write_market_fixtures()
    emit("行情回放夹具已写入：")
    for key, path in fixtures.items():
        emit(f"  - {key}: {path.relative_to(REPO_ROOT)}")
    return await _run_seed("seed-demo（演示数据 + 行情回放就绪）", market_bill=True)


async def _seed_tenants() -> int:
    return await _run_seed("seed-tenants（A / B / admin 三账号就绪）", market_bill=False)


# ── demo-p1 ────────────────────────────────────────────────────
def demo_steps(base: str, bill: Path) -> list[tuple[str, str, str]]:
    """返回（步骤标题, 待执行命令, 预期输出）。命令均在仓库根目录执行。"""
    rel_bill = bill.relative_to(REPO_ROOT)
    return [
        (
            "1) 服务探活",
            f"curl -s {base}{API_PREFIX}/version",
            '{"version":"0.1.0", ...}（200）',
        ),
        (
            "2) 注册演示账号",
            f"curl -s -X POST {base}{API_PREFIX}/auth/register "
            "-H 'Content-Type: application/json' "
            f"""-d '{{"email":"{DEMO_EMAIL}","password":"{DEFAULT_PASSWORD}","accept_tos":true}}'""",
            "201 + role=user（重复注册返回 409 EMAIL_TAKEN）",
        ),
        (
            "3) 登录换取令牌",
            f"curl -s -X POST {base}{API_PREFIX}/auth/login "
            "-H 'Content-Type: application/json' "
            f"""-d '{{"email":"{DEMO_EMAIL}","password":"{DEFAULT_PASSWORD}"}}'""",
            "200 + access_token / refresh_token / expires_in=900",
        ),
        (
            "4) 上传预览（同步）",
            f"curl -s -X POST {base}{API_PREFIX}/imports/preview "
            f'-H "Authorization: Bearer $TOKEN" -F "file=@{rel_bill}"',
            "200 + preview_id / columns / estimated / quarantined_count",
        ),
        (
            "5) 确认导入（异步）",
            f"curl -s -X POST {base}{API_PREFIX}/imports "
            "-H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' "
            "-H 'Idempotency-Key: <32+ chars>' "
            """-d '{"preview_id":"<preview_id>","mode":"full"}'""",
            "202 + batch_id / job_id",
        ),
        (
            "6) 轮询任务",
            f'curl -s {base}{API_PREFIX}/jobs/<job_id> -H "Authorization: Bearer $TOKEN"',
            "200 + status=succeeded / progress=100 / stage=done",
        ),
        (
            "7) 查询流水",
            f"curl -s '{base}{API_PREFIX}/trades?limit=5' -H \"Authorization: Bearer $TOKEN\"",
            "200 + items 含金额（定点字符串）/ 分类 / 批次",
        ),
        (
            "8) SSE 观察事件流",
            f'curl -N {base}{API_PREFIX}/jobs/<job_id>/events -H "Authorization: Bearer $TOKEN"',
            "事件流：queued → progress → succeeded（Last-Event-ID 可续传）",
        ),
        (
            "9) 越权核对",
            "用 b@demo.example.com 令牌 GET /trades（应只见自己的数据）；用 admin@demo.example.com 令牌 GET /trades",
            "B 看不到 A（隔离）；admin 读业务明细被拒",
        ),
    ]


def _is_up(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}{API_PREFIX}/version", timeout=2) as resp:
            return int(resp.status) == 200
    except (urllib.error.URLError, OSError):
        return False


def _http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "null")


def _run_demo(base: str, bill: Path) -> int:
    emit("→ 实际执行 curl 序列（对照上文命令与预期输出）")
    status, _ = _http_json("GET", f"{base}{API_PREFIX}/version")
    emit(f"  [1] version → {status}")
    status, body = _http_json(
        "POST",
        f"{base}{API_PREFIX}/auth/register",
        {"email": DEMO_EMAIL, "password": DEFAULT_PASSWORD, "accept_tos": True},
    )
    emit(f"  [2] register → {status} {json.dumps(body, ensure_ascii=False)[:160]}")
    if status not in (201, 409):
        return fail(f"注册演示账号失败（{status}）：{json.dumps(body, ensure_ascii=False)}")
    status, body = _http_json(
        "POST",
        f"{base}{API_PREFIX}/auth/login",
        {"email": DEMO_EMAIL, "password": DEFAULT_PASSWORD},
    )
    if status != 200:
        return fail(f"登录失败（{status}）：{json.dumps(body, ensure_ascii=False)}")
    token = body["access_token"]
    emit(f"  [3] login → {status}（拿到 access_token）")
    upload = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            f"{base}{API_PREFIX}/imports/preview",
            "-H",
            f"Authorization: Bearer {token}",
            "-F",
            f"file=@{bill}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    emit(f"  [4] upload preview → {upload.stdout.strip()[:200]}")
    if upload.returncode != 0:
        return fail(f"上传预览失败：{upload.stderr.strip()}")
    preview_id = json.loads(upload.stdout)["preview_id"]
    idem_key = "demo-" + uuid.uuid4().hex
    status, body = _http_json(
        "POST",
        f"{base}{API_PREFIX}/imports",
        {"preview_id": preview_id, "mode": "full"},
        token=token,
        headers={"Idempotency-Key": idem_key},
    )
    emit(f"  [5] commit → {status} {json.dumps(body, ensure_ascii=False)}")
    job_id = body.get("job_id")
    if status != 202 or not job_id:
        return fail(f"提交导入失败（{status}）：{json.dumps(body, ensure_ascii=False)}")

    job: dict[str, Any] = {}
    deadline = time.time() + 30
    while time.time() < deadline:
        status, job = _http_json("GET", f"{base}{API_PREFIX}/jobs/{job_id}", token=token)
        if job.get("status") in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.5)
    emit(f"  [6] job → {status} status={job.get('status')} progress={job.get('progress')}")
    if job.get("status") != "succeeded":
        return fail(f"导入任务未成功：{json.dumps(job, ensure_ascii=False)}")
    status, body = _http_json("GET", f"{base}{API_PREFIX}/trades?limit=5", token=token)
    count = len(body.get("items", [])) if isinstance(body, dict) else 0
    emit(f"  [7] trades → {status} items={count}")
    if status != 200 or count == 0:
        return fail(f"导入后流水为空（{status}）：{json.dumps(body, ensure_ascii=False)[:200]}")
    emit(
        f"  [8] SSE：curl -N {base}{API_PREFIX}/jobs/{job_id}/events "
        f'-H "Authorization: Bearer $TOKEN"（用 -N 观察事件流）'
    )
    emit("✅ demo-p1 执行完毕")
    return 0


def _demo_p1(dry_run: bool, run: bool) -> int:
    base = os.environ.get("STOCK_API_BASE", "http://127.0.0.1:8000")
    fixtures = write_market_fixtures()
    bill = fixtures["bill"]
    emit("== demo-p1：5 分钟演示脚本 ==")
    emit(f"基础地址：{base}（可用 STOCK_API_BASE 覆盖）")
    emit(f"演示账单夹具：{bill.relative_to(REPO_ROOT)}（由 seed-demo 生成，确定性）")
    emit("前置：先 `make seed-tenants`（或 make seed-demo）造好 A/B/admin 账号 + 数据")
    emit("")
    for title, command, expected in demo_steps(base, bill):
        emit(title)
        emit(f"  $ {command}")
        emit(f"  预期：{expected}")
    emit("")
    if dry_run:
        emit("（--dry-run：仅说明，不实际执行）")
        return 0
    if not _is_up(base):
        emit(
            "⚠ 未检测到 API 服务（默认 http://127.0.0.1:8000）。"
            "离线说明已给出；如需实跑请先 `make api` 或 `make dev` 后重试。"
        )
        return 0
    if not run:
        emit("如需按上述序列实跑，追加 --run；当前仅输出脚本与预期（离线可用）。")
        return 0
    return _run_demo(base, bill)


# ── 参数解析 / 分发 ────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock_api.cli",
        description="个人交易复盘平台 —— 一期本地运维 / Mock 命令（Makefile 目标）",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("seed-tenants", help="造 A/B/admin 三账号与账单（幂等）")
    subparsers.add_parser("seed-demo", help="造演示数据（含行情回放夹具）")
    subparsers.add_parser("api-check", help="契约完整性检查（路径/operationId/错误码/错误响应）")
    snapshot = subparsers.add_parser("api-snapshot", help="规范化写出 contracts/openapi.json")
    snapshot.add_argument("--check", action="store_true", help="只比对不写，漂移则退出码 1")
    subparsers.add_parser("sdk", help="由 contracts/openapi.json 生成前端 TS 类型")
    subparsers.add_parser("api-coverage", help="按 L1~L5 分组打印 API-ID 覆盖情况")
    subparsers.add_parser("verify-p1", help="一期质量门禁（ruff + pytest + api-check）")
    demo = subparsers.add_parser("demo-p1", help="5 分钟演示脚本（curl 序列）")
    demo.add_argument("--dry-run", action="store_true", help="只打印脚本，不执行")
    demo.add_argument("--run", action="store_true", help="按脚本实际执行 curl 序列")

    for name in ("seed-tenants", "seed-demo"):
        subparsers.choices[name].add_argument(
            "--dry-run", action="store_true", help="只打印计划，不写数据库"
        )
    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    command: str = args.command
    if command == "seed-tenants":
        if args.dry_run:
            emit("== seed-tenants（--dry-run：仅打印计划）==")
            for line in _seed_plan():
                emit(f"  {line}")
            return 0
        return await _seed_tenants()
    if command == "seed-demo":
        if args.dry_run:
            emit("== seed-demo（--dry-run：仅打印计划）==")
            emit(f"  fixtures/market/ 写入确定性行情/账单夹具：{MARKET_DIR}")
            for line in _seed_plan():
                emit(f"  {line}")
            return 0
        return await _seed_demo()
    if command == "api-check":
        return _api_check()
    if command == "api-snapshot":
        return _api_snapshot(check=args.check)
    if command == "sdk":
        return _sdk()
    if command == "api-coverage":
        return _api_coverage()
    if command == "verify-p1":
        return _verify_p1()
    if command == "demo-p1":
        return _demo_p1(dry_run=args.dry_run, run=args.run)
    raise SystemExit(f"未知子命令：{command}")


COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = dict.fromkeys(
    (
        "seed-tenants",
        "seed-demo",
        "api-check",
        "api-snapshot",
        "sdk",
        "api-coverage",
        "verify-p1",
        "demo-p1",
    ),
    _dispatch,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
