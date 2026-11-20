"""扩展助手消息状态列宽度（2026-11-20）

`needs_clarification` 长度 19，0020 的 `chat_message.status varchar(16)` 无法写入；
`glm-4-flash` 在部分问题下会返回该状态，导致 500。统一扩到 32，与轮次状态一致。

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
SCHEMA_VERSION = "0021_chat_message_status_width"
PREVIOUS_SCHEMA_VERSION = "0020_chat_assistant"
APP_VERSION = "0.1.0"


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    op.alter_column(
        "chat_message",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
        schema=SCHEMA,
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    # 先收敛超长状态，避免缩列失败；`needs_clarification` 归并为通用 degraded。
    op.execute("UPDATE platform.chat_message SET status = 'degraded' WHERE length(status) > 16")
    op.alter_column(
        "chat_message",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
        schema=SCHEMA,
    )
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
