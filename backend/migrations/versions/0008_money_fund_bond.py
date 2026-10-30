"""货币基金余额列 + 资产表货币基金列（2026-10-29）

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-29

背景（设计见 `docs/architecture/2026-10-29-money-fund-and-bond-market-data.md`）：
货币基金单位净值恒为 1.0000，没有可估值净值；账单中的货基申赎必须按「余额」口径
计入资产，而不是当普通持仓去请求行情（否则整日 blocked）。

本迁移新增两列：

- `ledger.trade.money_fund_balance_units`：该行发生后该标的的货币基金余额（金额
  scale=2 的标度整数）。`NULL` = 非货基行；非空即「货基行」标记。与
  `cash_balance_units` 同构，支持人工修正 / 批次撤销 / 增量脏查询。
- `review.asset_daily.money_fund_units`：当日货币基金余额合计（NOT NULL DEFAULT 0）。

同时重写资产表恒等式 CHECK：`total_assets_units = cash_units + money_fund_units +
market_value_units`（旧约束只含现金 + 市值）。

downgrade 反向 DDL + 还原 CHECK + 版本字符串回退 `0007_review_metrics`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_VERSION = "0008_money_fund_bond"
PREVIOUS_SCHEMA_VERSION = "0007_review_metrics"
APP_VERSION = "0.1.0"

OLD_TOTAL_CHECK = "total_assets_units = cash_units + market_value_units"
NEW_TOTAL_CHECK = "total_assets_units = cash_units + money_fund_units + market_value_units"


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
        sa.Column("money_fund_balance_units", sa.BigInteger(), nullable=True),
        schema="ledger",
    )
    op.add_column(
        "asset_daily",
        sa.Column(
            "money_fund_units",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="review",
    )
    op.drop_constraint("ck_asset_daily_total", "asset_daily", schema="review", type_="check")
    op.create_check_constraint(
        "ck_asset_daily_total", "asset_daily", NEW_TOTAL_CHECK, schema="review"
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_constraint("ck_asset_daily_total", "asset_daily", schema="review", type_="check")
    op.create_check_constraint(
        "ck_asset_daily_total", "asset_daily", OLD_TOTAL_CHECK, schema="review"
    )
    op.drop_column("asset_daily", "money_fund_units", schema="review")
    op.drop_column("trade", "money_fund_balance_units", schema="ledger")
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
