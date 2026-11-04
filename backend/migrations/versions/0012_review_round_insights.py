"""回合洞察：逐笔交易理由 + 前瞻收益（2026-11-04）

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-04

设计 `docs/architecture/2026-11-04-fifo-round-insights.md` §3：
- `review.trade_reason`：理由逻辑键 (user_id, trade_dedupe_key)（有效行部分唯一），
  文本上限 500、tags<=8、软删 + version 乐观锁；
- `review.trade_forward_return`：一行 = 一笔交易 × 一个 horizon（5/10/20），
  唯一 (user_id, trade_dedupe_key, horizon, definition_version)，全部数值列为标度整数；
- 两张表同批启用 RLS tenant 策略，版本推进到 `0012_review_round_insights`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
SCHEMA_VERSION = "0012_review_round_insights"
PREVIOUS_SCHEMA_VERSION = "0011_market_sectors"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
RLS_TABLES: tuple[str, ...] = ("trade_reason", "trade_forward_return")


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def _create_trade_reason() -> None:
    op.create_table(
        "trade_reason",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("trade_id", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("reason_code", sa.String(length=24), nullable=False),
        sa.Column("reason_text", sa.String(length=500), nullable=True),
        sa.Column("emotion_code", sa.String(length=16), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("annotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("late_annotated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            ["user_id"], ["platform.user.id"], name="fk_trade_reason_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_trade_reason_user_dedupe_active",
        "trade_reason",
        ["user_id", "trade_dedupe_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_trade_reason_user_dedupe",
        "trade_reason",
        ["user_id", "trade_dedupe_key"],
        schema=SCHEMA,
    )


def _create_trade_forward_return() -> None:
    op.create_table(
        "trade_forward_return",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("trade_id", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("d0", sa.Date(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("base_shifted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("data_as_of", sa.Date(), nullable=True),
        sa.Column("calendar_basis", sa.String(length=16), nullable=False),
        sa.Column("benchmark_id", sa.String(length=32), nullable=False),
        sa.Column("definition_version", sa.String(length=8), nullable=False),
        sa.Column("trade_version", sa.Integer(), nullable=False),
        sa.Column("price_gross_units", sa.BigInteger(), nullable=True),
        sa.Column("price_net_units", sa.BigInteger(), nullable=True),
        sa.Column("c0_units", sa.BigInteger(), nullable=True),
        sa.Column("cn_units", sa.BigInteger(), nullable=True),
        sa.Column("ret_b0_units", sa.BigInteger(), nullable=True),
        sa.Column("ret_b0_net_units", sa.BigInteger(), nullable=True),
        sa.Column("ret_b1_units", sa.BigInteger(), nullable=True),
        sa.Column("ret_b2_units", sa.BigInteger(), nullable=True),
        sa.Column("ret_b3_units", sa.BigInteger(), nullable=True),
        sa.Column("bench_ret_n_units", sa.BigInteger(), nullable=True),
        sa.Column("max_close_units", sa.BigInteger(), nullable=True),
        sa.Column("min_close_units", sa.BigInteger(), nullable=True),
        sa.Column(
            "price_jump_review", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("bars_fingerprint", sa.String(length=64), nullable=False),
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
            "horizon",
            "definition_version",
            name="uq_trade_forward_return_key",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.user.id"],
            name="fk_trade_forward_return_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_trade_forward_return_user_dedupe",
        "trade_forward_return",
        ["user_id", "trade_dedupe_key", "definition_version"],
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
    _create_trade_reason()
    _create_trade_forward_return()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("trade_forward_return", schema=SCHEMA)
    op.drop_table("trade_reason", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
