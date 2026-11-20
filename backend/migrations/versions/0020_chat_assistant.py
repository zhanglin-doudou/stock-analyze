"""智能分析助手：单会话、消息、轮次、工具/LLM 调用留痕（2026-11-20）

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-20

5 张 `platform` 表均启用 tenant RLS；`chat_conversation.user_id` 唯一，
保证一个用户只有一条对话。工具/LLM 调用保存原始 request/response 以支持回放；
本设计不引入 LangGraph checkpointer，避免第二套状态表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
SCHEMA_VERSION = "0020_chat_assistant"
PREVIOUS_SCHEMA_VERSION = "0019_import_template_bill_types"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
RLS_TABLES: tuple[str, ...] = (
    "chat_conversation",
    "chat_message",
    "chat_turn",
    "chat_tool_call",
    "chat_llm_call",
)


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    op.create_table(
        "chat_conversation",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=80), nullable=False, server_default="我的复盘"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_message_id", sa.String(length=40)),
        sa.Column("summary_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "focus",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("window_turns", sa.SmallInteger(), nullable=False, server_default="10"),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", name="uq_chat_conversation_user"),
        schema=SCHEMA,
    )
    op.create_table(
        "chat_message",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=40),
            sa.ForeignKey(f"{SCHEMA}.chat_conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_id", sa.String(length=40)),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role in ('user','assistant')", name="ck_chat_message_role"),
        schema=SCHEMA,
    )
    op.create_table(
        "chat_turn",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=40),
            sa.ForeignKey(f"{SCHEMA}.chat_conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_message_id", sa.String(length=40)),
        sa.Column("assistant_message_id", sa.String(length=40)),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("resolved_query", sa.Text()),
        sa.Column("understanding", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("context_manifest", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ok"),
        sa.Column("degrade_reason", sa.String(length=40)),
        sa.Column("llm_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fallback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "chat_tool_call",
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
        sa.Column("tool_name", sa.String(length=40), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=40)),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "chat_llm_call",
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
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=40)),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=40)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema=SCHEMA,
    )

    op.create_index("ix_chat_message_conversation_at", "chat_message", ["conversation_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_chat_message_turn", "chat_message", ["turn_id"], schema=SCHEMA)
    op.create_index("ix_chat_message_user_at", "chat_message", ["user_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_chat_turn_conversation_at", "chat_turn", ["conversation_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_chat_turn_user_at", "chat_turn", ["user_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_chat_tool_call_turn_seq", "chat_tool_call", ["turn_id", "seq"], schema=SCHEMA)
    op.create_index("ix_chat_tool_call_user_at", "chat_tool_call", ["user_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_chat_llm_call_turn_seq", "chat_llm_call", ["turn_id", "seq"], schema=SCHEMA)
    op.create_index("ix_chat_llm_call_user_at", "chat_llm_call", ["user_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_chat_llm_call_purpose_at", "chat_llm_call", ["purpose", "created_at"], schema=SCHEMA)

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )

    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
    op.drop_table("chat_llm_call", schema=SCHEMA)
    op.drop_table("chat_tool_call", schema=SCHEMA)
    op.drop_table("chat_turn", schema=SCHEMA)
    op.drop_table("chat_message", schema=SCHEMA)
    op.drop_table("chat_conversation", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
