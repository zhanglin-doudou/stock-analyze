"""用户助手模型偏好（2026-11-22 动态切换设计 §4）

`platform.user_setting` 新增 `llm_prefs` JSONB：`{"provider": ..., "model": ...}`，
空对象/缺键 = 跟随部署默认（`config/llm_providers.json` 的 default_*）。
`user_setting` 先例列（display_prefs 等）同样 JSONB + server_default '{}'。

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
SCHEMA_VERSION = "0024_user_llm_prefs"
PREVIOUS_SCHEMA_VERSION = "0023_email_verification"
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
        "user_setting",
        sa.Column(
            "llm_prefs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_column("user_setting", "llm_prefs", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
