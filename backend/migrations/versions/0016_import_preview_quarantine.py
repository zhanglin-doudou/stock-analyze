"""预览隔离明细：import_preview.quarantine_rows（2026-11-14）

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14

隔离确认改为「提交前弹窗提醒、不阻断提交」，弹窗需要逐行展示隔离原因与定位字段；
预览只有计数不够，故把解析期隔离明细随预览草稿一起落库（新增 JSONB 列）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ledger"
SCHEMA_VERSION = "0016_import_preview_quarantine"
PREVIOUS_SCHEMA_VERSION = "0015_import_mapping_v2"
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
        "import_preview",
        sa.Column(
            "quarantine_rows",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_column("import_preview", "quarantine_rows", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
