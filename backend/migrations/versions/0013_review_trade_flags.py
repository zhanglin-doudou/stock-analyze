"""账单级旗标（2026-11-09）

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09

- `review.trade_flag`：行粒度 = (账单, 检查项)，非 hit 状态也落库以显式记录数据边界；
- 唯一 (user_id, trade_dedupe_key, flag_code, definition_version) 保证按月重跑幂等；
- 索引 (user_id, flag_code, status) 为后续按旗标 groupBy 铺路；
- 同批启用 RLS tenant 策略，版本推进到 `0013_review_trade_flags`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
SCHEMA_VERSION = "0013_review_trade_flags"
PREVIOUS_SCHEMA_VERSION = "0012_review_round_insights"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
RLS_TABLES: tuple[str, ...] = ("trade_flag",)


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def _create_trade_flag() -> None:
    op.create_table(
        "trade_flag",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("trade_id", sa.String(length=40), nullable=False),
        sa.Column("security_code", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("flag_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("lookahead", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("definition_version", sa.String(length=8), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint(
            "user_id",
            "trade_dedupe_key",
            "flag_code",
            "definition_version",
            name="uq_trade_flag_key",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.user.id"],
            name="fk_trade_flag_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_trade_flag_user_date",
        "trade_flag",
        ["user_id", "trade_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_trade_flag_user_code",
        "trade_flag",
        ["user_id", "flag_code", "status"],
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


def upgrade() -> None:
    _create_trade_flag()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("trade_flag", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
