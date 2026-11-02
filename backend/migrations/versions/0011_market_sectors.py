"""板块数据底座（market schema，无 RLS）（2026-10-31）

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31

背景：引入三级行业（申万 SW2021）与概念（东财）板块数据，用于看板统计
（板块偏好/板块集中度）与后续归因模块。设计见
`docs/architecture/2026-10-31-sector-data.md` §3。

- 新建 6 张 `market` 公共缓存表（无 `user_id`、无 RLS、可再生）；
- `sector_member` 用 SCD2 半开区间 `[effective_from, effective_to)`，
  行业当前唯一性由**部分唯一索引**兜底（一股最多一个三级行业）；
- 授权沿用三角色模型：`stock_app` 只 DML、`stock_backup` 只 SELECT；
- `platform.schema_version.version` 更新为 `0011_market_sectors`。

downgrade 只按 FK 逆序删本迁移的 6 表，**不删 market schema**（daily_bar 仍在用）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "market"
SCHEMA_VERSION = "0011_market_sectors"
PREVIOUS_SCHEMA_VERSION = "0010_asset_daily_remark"
APP_VERSION = "0.1.0"

CONCEPT = "concept_em"
INDUSTRY = "industry_sw"


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def _create_sector() -> None:
    op.create_table(
        "sector",
        sa.Column("taxonomy", sa.String(length=24), nullable=False),
        sa.Column("sector_code", sa.String(length=32), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("parent_code", sa.String(length=32), nullable=True),
        sa.Column("classification_version", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint("level BETWEEN 0 AND 3", name="ck_sector_level_range"),
        sa.CheckConstraint(
            "(taxonomy = 'concept_em' AND level = 0 AND parent_code IS NULL) "
            "OR (taxonomy = 'industry_sw' AND level BETWEEN 1 AND 3)",
            name="ck_sector_taxonomy_level",
        ),
        sa.PrimaryKeyConstraint("taxonomy", "sector_code"),
        schema=SCHEMA,
    )
    op.create_index("ix_sector_parent", "sector", ["taxonomy", "parent_code"], schema=SCHEMA)
    op.create_index("ix_sector_level", "sector", ["taxonomy", "level", "is_active"], schema=SCHEMA)


def _create_sector_member() -> None:
    op.create_table(
        "sector_member",
        sa.Column("taxonomy", sa.String(length=24), nullable=False),
        sa.Column("sector_code", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("vendor_symbol", sa.String(length=16), nullable=True),
        sa.Column("upstream_effective_on", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["taxonomy", "sector_code"],
            [f"{SCHEMA}.sector.taxonomy", f"{SCHEMA}.sector.sector_code"],
            name="fk_sector_member_sector",
        ),
        sa.PrimaryKeyConstraint("taxonomy", "sector_code", "symbol", "effective_from"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sector_member_symbol",
        "sector_member",
        ["symbol", "effective_from"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sector_member_current",
        "sector_member",
        ["taxonomy", "sector_code"],
        schema=SCHEMA,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "uq_sector_member_industry_current",
        "sector_member",
        ["symbol"],
        schema=SCHEMA,
        unique=True,
        postgresql_where=sa.text("taxonomy = 'industry_sw' AND effective_to IS NULL"),
    )


def _create_benchmark_member() -> None:
    op.create_table(
        "benchmark_member",
        sa.Column("benchmark_id", sa.String(length=24), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("weight_units", sa.BigInteger(), nullable=False),
        sa.Column("weight_as_of", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("weight_units >= 0", name="ck_benchmark_member_weight_nonneg"),
        sa.PrimaryKeyConstraint("benchmark_id", "symbol", "effective_from"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_benchmark_member_current",
        "benchmark_member",
        ["benchmark_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def _create_sector_bar() -> None:
    op.create_table(
        "sector_bar",
        sa.Column("taxonomy", sa.String(length=24), nullable=False),
        sa.Column("sector_code", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close_units", sa.BigInteger(), nullable=False),
        sa.Column("return_units", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("close_units > 0", name="ck_sector_bar_close_positive"),
        sa.ForeignKeyConstraint(
            ["taxonomy", "sector_code"],
            [f"{SCHEMA}.sector.taxonomy", f"{SCHEMA}.sector.sector_code"],
            name="fk_sector_bar_sector",
        ),
        sa.PrimaryKeyConstraint("taxonomy", "sector_code", "trade_date"),
        schema=SCHEMA,
    )


def _create_sector_pull_range() -> None:
    op.create_table(
        "sector_pull_range",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("taxonomy", sa.String(length=24), nullable=False),
        sa.Column("sector_code", sa.String(length=32), nullable=False),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("data_end", sa.Date(), nullable=True),
        sa.Column("requested_end", sa.Date(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "taxonomy",
            "sector_code",
            "range_start",
            "range_end",
            name="uq_sector_pull_range_scope",
        ),
        schema=SCHEMA,
    )


def _create_sector_sync_state() -> None:
    op.create_table(
        "sector_sync_state",
        sa.Column("dataset_key", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="succeeded"),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("catalog_as_of", sa.Date(), nullable=True),
        sa.Column("membership_as_of", sa.Date(), nullable=True),
        sa.Column("bar_as_of", sa.Date(), nullable=True),
        sa.Column("weight_as_of", sa.Date(), nullable=True),
        sa.Column("classification_version", sa.String(length=24), nullable=True),
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("dataset_key"),
        schema=SCHEMA,
    )


def _grant_roles() -> None:
    tables = (
        f"{SCHEMA}.sector, {SCHEMA}.sector_member, {SCHEMA}.benchmark_member, "
        f"{SCHEMA}.sector_bar, {SCHEMA}.sector_pull_range, {SCHEMA}.sector_sync_state"
    )
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO stock_app, stock_backup")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO stock_app")
    op.execute(f"GRANT SELECT ON {tables} TO stock_backup")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    _create_sector()
    _create_sector_member()
    _create_benchmark_member()
    _create_sector_bar()
    _create_sector_pull_range()
    _create_sector_sync_state()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_table("sector_sync_state", schema=SCHEMA)
    op.drop_table("sector_pull_range", schema=SCHEMA)
    op.drop_table("sector_bar", schema=SCHEMA)
    op.drop_index("ix_benchmark_member_current", table_name="benchmark_member", schema=SCHEMA)
    op.drop_table("benchmark_member", schema=SCHEMA)
    op.drop_index("uq_sector_member_industry_current", table_name="sector_member", schema=SCHEMA)
    op.drop_index("ix_sector_member_current", table_name="sector_member", schema=SCHEMA)
    op.drop_index("ix_sector_member_symbol", table_name="sector_member", schema=SCHEMA)
    op.drop_table("sector_member", schema=SCHEMA)
    op.drop_index("ix_sector_level", table_name="sector", schema=SCHEMA)
    op.drop_index("ix_sector_parent", table_name="sector", schema=SCHEMA)
    op.drop_table("sector", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
