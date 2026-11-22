"""助手执行过程事件（2026-11-21 P0-4）

新增 `platform.chat_turn_event`：append-only 时间线，随 `chat_turn` 级联；
事件单独 180 天 TTL，聊天正文永久（用户可删）。本迁移不引入 MCP 表。

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
SCHEMA_VERSION = "0022_chat_turn_event"
PREVIOUS_SCHEMA_VERSION = "0021_chat_message_status_width"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    op.create_table(
        "chat_turn_event",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "turn_id",
            sa.String(length=40),
            sa.ForeignKey(f"{SCHEMA}.chat_turn.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("turn_id", "seq", name="uq_chat_turn_event_turn_seq"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_turn_event_user_at",
        "chat_turn_event",
        ["user_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chat_turn_event_created_at",
        "chat_turn_event",
        ["created_at"],
        schema=SCHEMA,
    )
    op.execute("ALTER TABLE platform.chat_turn_event ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON platform.chat_turn_event")
    op.execute(
        "CREATE POLICY tenant_isolation ON platform.chat_turn_event "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON platform.chat_turn_event")
    op.drop_table("chat_turn_event", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
