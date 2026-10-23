"""公共行情缓存表（market schema，无 RLS）（MARKET_MODE=live）

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23

背景：`MARKET_MODE=live` 需要把 akshare 拉到的真实日线落库缓存，避免重复外网取数。
行情是**跨租户公共数据**（不属于任何用户），因此：

- 新建独立 `market` schema，两表 `daily_bar` / `pull_range`；
- **不启用 RLS、不设 `user_id`**：G7 门禁只扫 platform/ledger/review 且只查带
  `user_id` 的列，market 不在其判定范围；写路径只允许内部装配（不开放 HTTP 端点）；
- 授权沿用三角色模型：`stock_app` 只 DML、`stock_backup` 只 SELECT；
- `market` schema 是可再生缓存，不进备份/恢复演练的正确性断言（丢了可重拉）。

设计要点（详见 `docs/architecture/2026-10-22-live-market-data-source.md` §3）：

- `daily_bar` 按 `(symbol, adjust, trade_date)` 幂等 upsert；主键顺序同时服务
  “按标的 + 日期区间”扫描，不重复建索引；
- `pull_range.range_end` 是**覆盖判定上界**而非“上游最后交易日”（周末/节假日
  不会让同一历史区间反复重拉）；`data_end` 才记录上游实际最后交易日；
- 负缓存 `bar_count = 0` 用短 TTL 过期，避免把“未上市/停牌”永久固化成无数据；
- `platform.schema_version.version` 更新为 `0005_market_cache`（服务启动即校验）；
- downgrade 可回滚：删两表 + 删 schema + 版本字符串回 `0004_period2_nav`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "market"
SCHEMA_VERSION = "0005_market_cache"
PREVIOUS_SCHEMA_VERSION = "0004_period2_nav"
APP_VERSION = "0.1.0"


def _create_schema() -> None:
    # 公共 schema：owner 拥有，stock_app/stock_backup 只按下方 GRANT 取用。
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")


def _create_daily_bar() -> None:
    op.create_table(
        "daily_bar",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("adjust", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close_units", sa.BigInteger(), nullable=False),
        sa.Column("return_units", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
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
        sa.PrimaryKeyConstraint("symbol", "adjust", "trade_date"),
        schema=SCHEMA,
    )


def _create_pull_range() -> None:
    op.create_table(
        "pull_range",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("adjust", sa.String(length=8), nullable=False),
        sa.Column("adjust_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("data_end", sa.Date(), nullable=True),
        sa.Column("requested_end", sa.Date(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "adjust",
            "adjust_version",
            "range_start",
            "range_end",
            name="uq_pull_range_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pull_range_symbol_pulled",
        "pull_range",
        ["symbol", "adjust", "pulled_at"],
        schema=SCHEMA,
    )


def _grant_roles() -> None:
    """行情是公开数据，`stock_app` 需 DML、`stock_backup` 只读（无 RLS 也要显式授权）。"""
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO stock_app, stock_backup")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.daily_bar, {SCHEMA}.pull_range "
        "TO stock_app"
    )
    op.execute(f"GRANT SELECT ON {SCHEMA}.daily_bar, {SCHEMA}.pull_range TO stock_backup")


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    _create_schema()
    _create_pull_range()
    _create_daily_bar()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_index("ix_pull_range_symbol_pulled", table_name="pull_range", schema=SCHEMA)
    op.drop_table("pull_range", schema=SCHEMA)
    op.drop_table("daily_bar", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
