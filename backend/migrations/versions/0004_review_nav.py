"""review 净值表 + 租户 RLS（Period2-03）

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22

背景：Period2-03 要落库每日单位净值（`review.nav_daily`），按日存单位净值、日收益、
总资产、外部现金流、份额。带 `user_id`，与 0003 一样启用 RLS tenant 策略。

设计要点：
- 主键确定性（`nav_` + sha256(user_id|trade_date)[:32]），重复 rebuild 幂等；
- 唯一索引 (user_id, trade_date) 保证同日不重复；
- blocked 行的 nav/return/total_value 字段为 0（API 层不输出资产值）；
- RLS 谓词与 0001/0002/0003 完全一致；
- 授权沿用三角色模型；
- `platform.schema_version.version` 更新为 `0004_period2_nav`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
SCHEMA_VERSION = "0004_period2_nav"
PREVIOUS_SCHEMA_VERSION = "0003_period2_review"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
RLS_TABLES: tuple[str, ...] = ("nav_daily",)


def _create_nav_daily() -> None:
    op.create_table(
        "nav_daily",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("nav_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("return_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_value_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cashflow_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("shares_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("blocked_reason", sa.String(length=256), nullable=True),
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
            ["user_id"], ["platform.user.id"], name="fk_nav_daily_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "trade_date", name="uq_nav_daily_user_date"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_nav_daily_user_date",
        "nav_daily",
        ["user_id", "trade_date"],
        schema=SCHEMA,
    )


def _enable_rls() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def _grant_roles() -> None:
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
    _create_nav_daily()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("nav_daily", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
