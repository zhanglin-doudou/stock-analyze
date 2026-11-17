"""卖出人工修订流水来源标记（2026-11-15）

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-15

背景：超卖卖出允许用户补录买入成本。补录时不改原账单，而是在同一交易日、排在卖出
之前插入两条「人工修订」流水（资金划转 + 无费用买入），使 FIFO 配对闭合。为了：

1. 区分注入行与真实账单（页面提示、禁止被区间导入覆盖、审计可追溯）；
2. 支持同一卖出重复修订（覆盖旧注入行）与撤回（软删后重建），

新增 `ledger.trade.revision_of`（指向被修订的卖出 trade_id）并建部分索引
`(user_id, revision_of) WHERE revision_of IS NOT NULL`（只索引注入行，几乎零成本）。

注入行恒 `batch_id IS NULL`、`source='manual'`、`dedupe_key='mr:<sell_id>:cash|buy'`，
因此不会进入导入去重键空间（sha256 64-hex），也不会被批次撤销/区间替换误删。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_VERSION = "0018_trade_manual_revision"
PREVIOUS_SCHEMA_VERSION = "0017_remove_money_fund"
APP_VERSION = "0.1.0"


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    op.add_column(
        "trade",
        sa.Column("revision_of", sa.String(length=40), nullable=True),
        schema="ledger",
    )
    op.create_index(
        "ix_trade_user_revision",
        "trade",
        ["user_id", "revision_of"],
        schema="ledger",
        postgresql_where=sa.text("revision_of IS NOT NULL"),
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_index("ix_trade_user_revision", table_name="trade", schema="ledger")
    op.drop_column("trade", "revision_of", schema="ledger")
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
