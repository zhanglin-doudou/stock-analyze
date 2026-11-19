"""导入模板携带账单类型映射（2026-11-17）

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-17

`import_template.bill_type_ids` 存本模板引用的 `import_bill_type.id` 列表（按界面
顺序，含废弃项）。单向引用、不建外键：类型行被删/重建时，读取方按「失效」处理
（标 `invalid`、不阻断导入），保存时以本次确认结果整体覆盖并自然丢弃失效 id。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ledger"
SCHEMA_VERSION = "0019_import_template_bill_types"
PREVIOUS_SCHEMA_VERSION = "0018_trade_manual_revision"
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
        "import_template",
        sa.Column(
            "bill_type_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_column("import_template", "bill_type_ids", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
