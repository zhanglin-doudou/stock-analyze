"""G7 门禁：租户表 RLS 覆盖（platform / ledger / review）。

判据（Period2-Entry-Checklist G7 / Testing §4.4）：
1. 枚举三个 schema 中**带 `user_id` 列**的表（含分区父表，`relkind in ('r','p')`）；
2. 每张表必须 `relrowsecurity = true`，且至少存在一条 `USING` / `WITH CHECK`
   都引用 `app.user_id` 的策略（即真正的 tenant 隔离策略）；
3. 认证前置 / 系统表在 `ALLOWLIST` 中显式豁免（**必须写原因**），未在豁免表
   且缺 RLS/策略 → 打印清单并 exit 1；
4. `review` 当前 0 张表时输出 `review 0/0`，并提示 P2-02 建表后自动纳入。

无网络、不启动服务；用 **owner** 连接（owner 不受 RLS 约束，才能读 pg_catalog）。

用法::

    .venv/bin/python scripts/ci/gate_rls_coverage.py
    .venv/bin/python scripts/ci/gate_rls_coverage.py --dsn postgresql://...
    STOCK_DB_NAME=stock_g7_check STOCK_DB_HOST=127.0.0.1 STOCK_DB_PORT=5432 \
        .venv/bin/python scripts/ci/gate_rls_coverage.py

退出码：0 = 通过；1 = 有租户表缺 RLS/策略；2 = 连接/查询失败。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg  # type: ignore[import-untyped]  # asyncpg 未发布 py.typed
from stock_platform.config import Settings

SCHEMAS: tuple[str, ...] = ("platform", "ledger", "review")

# 认证前置 / 系统表：按设计**不启用** RLS（原因必须显式；见 0002 迁移 docstring）。
# schema.table → 原因
ALLOWLIST: dict[tuple[str, str], str] = {
    ("platform", "user"): "登录按 email 查用户时尚未知 user_id，无法 SET LOCAL（认证前置）",
    ("platform", "refresh_token"): "刷新/轮换按 token_hash 查（认证前置，未知 user_id）",
    ("platform", "api_token"): "PAT 校验按 token_hash 查（认证前置，未知 user_id）",
    ("platform", "login_attempt"): "登录失败按 email/IP 计数，无 user_id 语义",
    ("platform", "rate_limit_bucket"): "限流按 IP/账号/窗口键计数，认证前即需读写",
    ("platform", "config_release"): "全局配置版本（系统级，无 user_id 列）",
    ("platform", "schema_version"): "迁移版本（readyz/启动校验用，全局单行）",
    ("platform", "backup_run"): "备份运维记录（admin/系统级，无 user_id 列）",
    ("platform", "audit_log"): "审计含登录等 actor 未知事件；跨租户不可见由 API 层过滤保证",
    ("platform", "job_event"): "无 user_id 列：从属于 job，按 job_id 访问；写入在 job 租户事务内",
}

_TENANT_EXPR = re.compile(r"app\.user_id")


@dataclass(frozen=True, slots=True)
class TableState:
    schema: str
    table: str
    rls_enabled: bool
    policies: tuple[str, ...]  # 形如 "tenant_isolation"（仅统计 tenant 策略名）


@dataclass(frozen=True, slots=True)
class GateResult:
    covered: int
    total: int
    allowlisted: tuple[tuple[str, str, str], ...]
    problems: tuple[str, ...]
    per_schema: dict[str, tuple[int, int]]


def emit(*parts: object) -> None:
    """统一输出（ruff T20 禁止散落的 print）。"""
    sys.stdout.write(" ".join(str(part) for part in parts) + "\n")


def _asyncpg_dsn(dsn: str) -> str:
    """SQLAlchemy DSN → asyncpg DSN（去掉方言后缀）。"""
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def _display_target(dsn: str) -> str:
    """不打印口令的连接目标（host:port/db）。"""
    matched = re.search(r"@([^:/]+(?::\d+)?)/([^?]+)$", dsn)
    if matched is None:
        return "<dsn>"
    return f"{matched.group(1)}/{matched.group(2)}"


async def collect_states(conn: asyncpg.Connection) -> list[TableState]:
    """枚举三个 schema 中带 user_id 列的表及其 RLS/tenant 策略状态。"""
    tables = await conn.fetch(
        """
        select n.nspname as schema, c.relname as table, c.relrowsecurity as rls
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = any($1::text[])
          and c.relkind in ('r', 'p')
        order by n.nspname, c.relname
        """,
        list(SCHEMAS),
    )
    with_user_id = {
        (row["schema"], row["table"])
        for row in await conn.fetch(
            """
            select n.nspname as schema, c.relname as table
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            join pg_attribute a
              on a.attrelid = c.oid and a.attname = 'user_id' and not a.attisdropped
            where n.nspname = any($1::text[]) and c.relkind in ('r', 'p')
            """,
            list(SCHEMAS),
        )
    }
    policies = await conn.fetch(
        """
        select schemaname as schema, tablename as table, policyname as name,
               qual as using_expr, with_check as check_expr
        from pg_policies
        where schemaname = any($1::text[])
        """,
        list(SCHEMAS),
    )
    tenant_policies: dict[tuple[str, str], list[str]] = {}
    for row in policies:
        using_expr = row["using_expr"] or ""
        check_expr = row["check_expr"] or ""
        if _TENANT_EXPR.search(using_expr) and _TENANT_EXPR.search(check_expr):
            tenant_policies.setdefault((row["schema"], row["table"]), []).append(row["name"])

    states: list[TableState] = []
    for row in tables:
        key = (row["schema"], row["table"])
        if key not in with_user_id:
            continue
        states.append(
            TableState(
                schema=row["schema"],
                table=row["table"],
                rls_enabled=bool(row["rls"]),
                policies=tuple(sorted(tenant_policies.get(key, ()))),
            )
        )
    return states


def evaluate(states: Sequence[TableState]) -> GateResult:
    """按 ALLOWLIST 与 RLS/策略要求计算覆盖结果。

    `total` 只统计**需要 RLS 的租户表**（带 user_id 且未豁免）；豁免表单独列出。
    """
    problems: list[str] = []
    covered = 0
    total = 0
    allowlisted: list[tuple[str, str, str]] = []
    counts: dict[str, list[int]] = {schema: [0, 0] for schema in SCHEMAS}

    for state in states:
        key = (state.schema, state.table)
        if key in ALLOWLIST:
            allowlisted.append((state.schema, state.table, ALLOWLIST[key]))
            continue

        total += 1
        counts[state.schema][1] += 1
        if not state.rls_enabled:
            problems.append(
                f"{state.schema}.{state.table}：未启用 RLS（缺 ALTER TABLE ... ENABLE RLS）"
            )
        elif not state.policies:
            problems.append(
                f"{state.schema}.{state.table}：已启用 RLS 但缺少引用 app.user_id 的 "
                "tenant 策略（USING + WITH CHECK）"
            )
        else:
            covered += 1
            counts[state.schema][0] += 1

    return GateResult(
        covered=covered,
        total=total,
        allowlisted=tuple(allowlisted),
        problems=tuple(problems),
        per_schema={schema: (value[0], value[1]) for schema, value in counts.items()},
    )


async def run_gate(dsn: str) -> int:
    emit("== RLS 覆盖门禁（G7：platform / ledger / review）==")
    emit(f"数据库：{_display_target(dsn)}（owner 连接）")
    try:
        conn = await asyncpg.connect(_asyncpg_dsn(dsn))
    except (OSError, asyncpg.PostgresError) as exc:
        emit(f"❌ 无法连接数据库：{exc}")
        return 2
    try:
        states = await collect_states(conn)
    except asyncpg.PostgresError as exc:
        emit(f"❌ 查询 pg_catalog 失败：{exc}")
        return 2
    finally:
        await conn.close()

    result = evaluate(states)
    for schema in SCHEMAS:
        schema_covered, schema_total = result.per_schema[schema]
        if schema_total == 0:
            emit(f"  {schema}: 0/0（当前无带 user_id 的表；P2-02 建表后自动纳入本门禁）")
        else:
            emit(f"  {schema}: {schema_covered}/{schema_total} 租户表启用 RLS + tenant 策略")

    if result.allowlisted:
        emit(f"允许豁免的认证/系统表（{len(result.allowlisted)} 张，按设计不启用 RLS）：")
        for schema, table, reason in result.allowlisted:
            emit(f"  - {schema}.{table}：{reason}")

    if result.problems:
        emit(
            f"\n❌ RLS 覆盖失败：{result.covered}/{result.total}，缺失 {len(result.problems)} 项："
        )
        for problem in result.problems:
            emit(f"  - {problem}")
        return 1

    emit(f"\n✅ RLS 覆盖通过：{result.covered}/{result.total}（豁免 {len(result.allowlisted)} 张）")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G7 租户表 RLS 覆盖门禁")
    parser.add_argument(
        "--dsn",
        default=None,
        help="owner DSN（默认取 Settings.owner_dsn；环境变量 STOCK_DB_* 可覆盖）",
    )
    args = parser.parse_args(argv)
    dsn = args.dsn or Settings().owner_dsn()
    return asyncio.run(run_gate(dsn))


if __name__ == "__main__":
    raise SystemExit(main())
