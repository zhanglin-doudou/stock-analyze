"""review 每日资产快照表 + 构建水位线 + 租户 RLS（Period2-16）

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25

背景：资产变化与后续回撤族指标需要「全历史、逐自然日、可增量维护」的资产序列表。
本迁移新建：

- `review.asset_daily`：一行 = 某用户某自然日收盘后的现金 + 持仓估值
  （金额 scale=2 的 `*_units` BigInteger；超卖日允许负市值，因此**不建** `market_value>=0`）；
- `review.asset_build_state`：每用户一行增量水位线 + 构建摘要（`version` 乐观锁做状态 CAS）。

设计要点：
- 主键确定性（`ast_` + sha256(user_id|trade_date)[:32]），重复构建幂等；
- `UNIQUE(user_id, trade_date)` 保证同日不重复（其唯一索引同时支持列表范围/倒序扫描，
  不重复建普通索引）；
- DB CHECK：`total_assets_units = cash_units + market_value_units`、
  `holding_count >= 0`（应用层另有 blocked 行数值全 0 的不变式）；
- `ledger.trade` 新增 `ix_trade_user_updated_at (user_id, updated_at, trade_date)`：
  增量脏查询 index-only（与 ORM `Trade.__table_args__` 同批声明，`make migrate-check` 无漂移）；
- RLS 谓词与 0001~0004 完全一致（USING / WITH CHECK 同谓词，未设 `app.user_id` fail-closed）；
- 授权沿用三角色模型：`stock_app` 只 DML、`stock_backup` 只 SELECT；
- `platform.schema_version.version` 更新为 `0006_review_asset_daily`（服务启动即校验）；
- downgrade 完整回滚：删索引/策略/两表，并把版本字符串还原到 `0005_market_cache`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
LEDGER_SCHEMA = "ledger"
SCHEMA_VERSION = "0006_review_asset_daily"
PREVIOUS_SCHEMA_VERSION = "0005_market_cache"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"

# 本迁移启用 RLS 的 review 租户表（表名清单与 stock_review.models.REVIEW_TABLES 一致）
RLS_TABLES: tuple[str, ...] = ("asset_daily", "asset_build_state")

# 增量脏查询索引：WHERE user_id = ? AND updated_at >= ? ORDER BY updated_at
TRADE_INDEX = "ix_trade_user_updated_at"


def _create_asset_daily() -> None:
    op.create_table(
        "asset_daily",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("cash_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("market_value_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_assets_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("holding_cost_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("holding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cashflow_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prev_total_assets_units", sa.BigInteger(), nullable=True),
        sa.Column("daily_pnl_units", sa.BigInteger(), nullable=True),
        sa.Column("daily_return_units", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("status_reason", sa.String(length=256), nullable=True),
        sa.Column("definition_version", sa.String(length=16), nullable=False),
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
            ["user_id"],
            ["platform.user.id"],
            name="fk_asset_daily_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "trade_date", name="uq_asset_daily_user_date"),
        sa.CheckConstraint(
            "total_assets_units = cash_units + market_value_units",
            name="ck_asset_daily_total",
        ),
        sa.CheckConstraint("holding_count >= 0", name="ck_asset_daily_holding_count"),
        schema=SCHEMA,
    )
    # 注意：不重复建 (user_id, trade_date) 普通索引——UNIQUE 约束的索引已支持
    # 范围过滤 + 倒序扫描（列表默认 -trade_date），也满足迁移漂移比对。


def _create_asset_build_state() -> None:
    op.create_table(
        "asset_build_state",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("last_built_through", sa.Date(), nullable=True),
        sa.Column("last_built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_trade_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_trade_count", sa.BigInteger(), nullable=True),
        sa.Column("last_trade_version_sum", sa.BigInteger(), nullable=True),
        sa.Column("data_as_of", sa.Date(), nullable=True),
        sa.Column("assumption", sa.String(length=16), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("last_fifo_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status_reason", sa.String(length=256), nullable=True),
        sa.Column("last_mode", sa.String(length=16), nullable=False, server_default="full"),
        sa.Column("last_rebuild_from", sa.Date(), nullable=True),
        sa.Column("last_rows_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("definition_version", sa.String(length=16), nullable=False),
        sa.Column("rows_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            ["user_id"],
            ["platform.user.id"],
            name="fk_asset_build_state_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema=SCHEMA,
    )


def _create_trade_updated_index() -> None:
    op.create_index(
        TRADE_INDEX,
        "trade",
        ["user_id", "updated_at", "trade_date"],
        unique=False,
        schema=LEDGER_SCHEMA,
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


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    _create_asset_daily()
    _create_asset_build_state()
    _create_trade_updated_index()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_index(TRADE_INDEX, table_name="trade", schema=LEDGER_SCHEMA)
    op.drop_table("asset_build_state", schema=SCHEMA)
    op.drop_table("asset_daily", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
