"""platform 租户表补 RLS（G7）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21

背景：`0001_phase1` 只对 `ledger` 的 7 张业务表启用 RLS；`platform` 里的
**租户数据表**（job / idempotency_key / user_setting / outbox_event）仍无策略，
应用层漏过滤时没有第二道防线（P1-5）。本迁移补齐这 4 张表的 RLS，并把
`review`/`delivery` 新表的覆盖交给 `scripts/ci/gate_rls_coverage.py` 门禁
（P2-02 建表后自动纳入，本迁移不建假表）。

启用 RLS 的表（tenant 策略，USING / WITH CHECK 同一谓词，未设变量时 fail-closed）：

  platform.job            任务队列（user_id 非空）
  platform.idempotency_key 幂等键（user_id 非空）
  platform.user_setting   用户设置（user_id 即主键）
  platform.outbox_event   事件表（user_id 可空；系统级事件 user_id IS NULL，
                          只有 owner/迁移能读写，app 只能读写本租户事件）

**不启用 RLS 的认证/系统表及原因**（开启会让认证链路在“未知 user_id”时瘫痪）：

  platform.user            登录要按 email 查用户（此时尚未知 user_id，无法 SET LOCAL）
  platform.refresh_token   刷新/轮换按 token_hash 查（认证前置，未知 user_id）
  platform.api_token       PAT 校验按 token_hash 查（认证前置，未知 user_id）
  platform.login_attempt   登录失败按 email/IP 计数，无 user_id 语义
  platform.rate_limit_bucket 限流按 IP/账号/窗口键计数，认证前即需读写
  platform.config_release  全局配置版本（系统级，无 user_id）
  platform.schema_version  迁移版本（readyz/启动校验用，全局单行）
  platform.backup_run      备份运维记录（admin/系统级，无 user_id）
  platform.audit_log       审计含登录失败等 actor 未知事件；跨租户不可见由 API 层
                           的 actor 过滤保证（admin 视图不含业务明细）
  platform.job_event       无 user_id 列（从属于 job，按 job_id 访问；写入发生在
                           job 的租户事务内，job 本身已受 RLS 约束）

运行时适配（否则开启后队列/注册会坏）：
  - worker 的跨租户 claim/reap/heartbeat 用 **owner** 角色 Database（JobQueue 控制面），
    handler 仍走 app 角色的 tenant_tx；
  - API `/jobs*` 端点一律 `session(user_id=..., begin=True)`；
  - `auth.register` 在系统 session 里 `set_tenant(user.id)` 后再读写 user_setting；
  - 幂等键/outbox 的写入路径都已在 tenant 会话内（imports/trades 应用层）。

幂等：`ENABLE RLS` 与 `DROP POLICY IF EXISTS` + `CREATE POLICY` 组合可在已启用
策略的库上重复执行；Alembic 版本表仍保证 `upgrade head` 只跑一次。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"

# 本迁移启用 RLS 的 platform 租户表
PLATFORM_RLS_TABLES: tuple[str, ...] = (
    "job",
    "idempotency_key",
    "user_setting",
    "outbox_event",
)


def upgrade() -> None:
    for table in PLATFORM_RLS_TABLES:
        op.execute(f"ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON platform.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON platform.{table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def downgrade() -> None:
    for table in PLATFORM_RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON platform.{table}")
        op.execute(f"ALTER TABLE platform.{table} DISABLE ROW LEVEL SECURITY")
