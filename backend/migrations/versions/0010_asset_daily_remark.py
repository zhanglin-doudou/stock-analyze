"""资产表新增 remark 列（2026-10-31）

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31

背景：新股按认购成本兜底、可转债按买入价兜底不再标记 degraded（视为正常估值）；
为保留「当日资产含可转债」的信息，`review.asset_daily` 新增 `remark varchar(256)`
可空列。口径版本同步 bump 到 1.6，全量重建时写入。

downgrade 删除该列并回退 schema 版本戳。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
SCHEMA_VERSION = "0010_asset_daily_remark"
PREVIOUS_SCHEMA_VERSION = "0009_metrics_period_tokens"
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
        "asset_daily",
        sa.Column("remark", sa.String(length=256), nullable=True),
        schema=SCHEMA,
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_column("asset_daily", "remark", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
