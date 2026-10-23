"""review 回合表 + 租户 RLS（Period2-02）

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22

背景：Period2-02 要落库 FIFO 回合（`review.round` / `review.round_fill`）与增量重建水位线
（`review.round_build_state`）。三张表都带 `user_id`，必须在**同一迁移**里启用 RLS
tenant 策略，否则 `scripts/ci/gate_rls_coverage.py` 会让 CI 红（review 必须 3/3）。

设计要点：
- 回合主键确定性（`rnd_` + sha256(user_id|security_code|key)[:32]），重复 rebuild 幂等；
- `round_fill.round_id` 级联删除，删回合不留孤儿明细；
- 游标分页排序键 `close_date DESC NULLS LAST, id DESC`，open 回合（close_date 为 NULL）
  始终排在 closed 之后，翻页不因新写入错位；
- RLS 谓词与 0001/0002 完全一致（`user_id = NULLIF(current_setting('app.user_id', true), '')::uuid`），
  USING 与 WITH CHECK 同谓词；未设置 `app.user_id` 时 fail-closed（0 行 / 写入被拒）；
- 授权沿用三角色模型：`stock_app` 只 DML、`stock_backup` 只 SELECT；
- `platform.schema_version.version` 更新为 `0003_period2_review`（服务启动即校验）。
- downgrade 可回滚：删三表 + 恢复版本字符串到 0002 时的值（`0001_phase1`，0002 不写版本）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
SCHEMA_VERSION = "0003_period2_review"
# 0002 是纯 RLS 迁移、不写版本字符串；rollback 回滚到 0001 落下的值。
PREVIOUS_SCHEMA_VERSION = "0001_phase1"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"

# 本迁移启用 RLS 的 review 租户表（表名清单与 stock_review.models.REVIEW_TABLES 一致）
RLS_TABLES: tuple[str, ...] = ("round", "round_fill", "round_build_state")


def _create_round() -> None:
    op.create_table(
        "round",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_code", sa.String(length=16), nullable=False),
        sa.Column("security_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("open_trade_id", sa.String(length=40), nullable=False),
        sa.Column("close_trade_id", sa.String(length=40), nullable=True),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("close_date", sa.Date(), nullable=True),
        sa.Column("quantity_units", sa.BigInteger(), nullable=False),
        sa.Column("cost_units", sa.BigInteger(), nullable=False),
        sa.Column("proceeds_units", sa.BigInteger(), nullable=False),
        sa.Column("dividend_units", sa.BigInteger(), nullable=False),
        sa.Column("pnl_units", sa.BigInteger(), nullable=False),
        sa.Column("return_rate_units", sa.BigInteger(), nullable=False),
        sa.Column("holding_days", sa.Integer(), nullable=False),
        sa.Column("data_error", sa.Boolean(), nullable=False),
        sa.Column("oversell_units", sa.BigInteger(), nullable=False),
        sa.Column("fills_count", sa.Integer(), nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform.user.id"], name="fk_round_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    # 游标分页：close_date DESC NULLS LAST（open 回合在最后），自定义排序方向需要 raw SQL。
    op.execute(
        f"CREATE INDEX ix_round_user_close_id ON {SCHEMA}.round "
        "(user_id, close_date DESC NULLS LAST, id DESC)"
    )
    op.create_index(
        "ix_round_user_security_close",
        "round",
        ["user_id", "security_code", "close_date"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_round_user_status", "round", ["user_id", "status"], unique=False, schema=SCHEMA
    )


def _create_round_fill() -> None:
    op.create_table(
        "round_fill",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_id", sa.String(length=40), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("buy_trade_id", sa.String(length=40), nullable=False),
        sa.Column("sell_trade_id", sa.String(length=40), nullable=False),
        sa.Column("quantity_units", sa.BigInteger(), nullable=False),
        sa.Column("buy_cost_units", sa.BigInteger(), nullable=False),
        sa.Column("sell_proceeds_units", sa.BigInteger(), nullable=False),
        sa.Column("dividend_units", sa.BigInteger(), nullable=False),
        sa.Column("pnl_units", sa.BigInteger(), nullable=False),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("close_date", sa.Date(), nullable=False),
        sa.Column("holding_days", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["round_id"],
            [f"{SCHEMA}.round.id"],
            name="fk_round_fill_round",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "sequence", name="uq_round_fill_round_sequence"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_round_fill_user_round",
        "round_fill",
        ["user_id", "round_id"],
        unique=False,
        schema=SCHEMA,
    )


def _create_round_build_state() -> None:
    op.create_table(
        "round_build_state",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_rebuilt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_trade_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scope", sa.String(length=16), nullable=False),
        sa.Column("last_period_start", sa.Date(), nullable=True),
        sa.Column("last_period_end", sa.Date(), nullable=True),
        sa.Column("rounds_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform.user.id"], name="fk_round_build_state_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema=SCHEMA,
    )


def _enable_rls() -> None:
    """三张表启用 RLS + tenant_isolation 策略；未设租户变量时 fail-closed。"""
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def _grant_roles() -> None:
    """运行时角色只给 DML；备份角色只给 SELECT（与 0001 的三角色模型一致）。"""
    tables = ", ".join(f"{SCHEMA}.{table}" for table in RLS_TABLES)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO stock_app")
    op.execute(f"GRANT SELECT ON {tables} TO stock_backup")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO stock_app")


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    _create_round()
    _create_round_fill()
    _create_round_build_state()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    # 先删子表（round_fill 依赖 round），再删主表；策略随表一起消失，显式清理更稳。
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("round_build_state", schema=SCHEMA)
    op.drop_table("round_fill", schema=SCHEMA)
    op.drop_table("round", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
