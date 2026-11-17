"""移除货币基金余额口径（2026-11-15）

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-15

背景：资产表取消「货币基金」列，货基按普通持仓计成本与市值；解析期不再识别货基、
不再推演「货币基金余额」。因此删除两列并重写资产表恒等式。

**不可逆**：两列删除后无法恢复历史值。采用方案 A（用户 2026-11-15 拍板）：

1. 先清空 `review.asset_daily`（旧行的 total 含 money_fund，删列后与旧 CHECK 冲突）；
2. 复位 `review.asset_build_state`（清水位线/指纹/状态，靠 `ASSET_DEFINITION_VERSION`
   bump 到 2.1 触发全量重建）；
3. 删除 CHECK `ck_asset_daily_total` 并重建为 `total = cash + market_value`；
4. 删除 `review.asset_daily.money_fund_units`；
5. 删除 `ledger.trade.money_fund_balance_units`。

`review.nav_daily` / metrics / attribution / trade_flags 不再单独清理：资产版本
bump 后由各自 stale 判定自愈（用户拍板第 7 项）。

downgrade 只还原 DDL（列可加回，但历史数值不可恢复；重建资产表仍需重新构建）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_VERSION = "0017_remove_money_fund"
PREVIOUS_SCHEMA_VERSION = "0016_import_preview_quarantine"
APP_VERSION = "0.1.0"

OLD_TOTAL_CHECK = "total_assets_units = cash_units + money_fund_units + market_value_units"
NEW_TOTAL_CHECK = "total_assets_units = cash_units + market_value_units"


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    # 方案 A：旧资产行含 money_fund，删列前先清空，避免 CHECK 校验失败与口径混淆。
    op.execute("DELETE FROM review.asset_daily")
    op.execute("DELETE FROM review.asset_build_state")
    op.drop_constraint("ck_asset_daily_total", "asset_daily", schema="review", type_="check")
    op.create_check_constraint(
        "ck_asset_daily_total", "asset_daily", NEW_TOTAL_CHECK, schema="review"
    )
    op.drop_column("asset_daily", "money_fund_units", schema="review")
    op.drop_column("trade", "money_fund_balance_units", schema="ledger")
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
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
        "ck_asset_daily_total", "asset_daily", OLD_TOTAL_CHECK, schema="review"
    )
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
