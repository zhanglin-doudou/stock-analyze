"""注册邮箱验证码与忘记密码（2026-11-22 设计）

新增两张认证前置表：
- `platform.email_verification_code`：每 (email, purpose) 一行当前验证码；
- `platform.email_code_send_throttle`：IP/邮箱冷却与小时/日配额。

两张表都按 `platform.user` / `login_attempt` 先例**不启用 RLS**（注册前没有
user_id），由 `stock_app` 直接 DML、`stock_backup` 只读。

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
SCHEMA_VERSION = "0023_email_verification"
PREVIOUS_SCHEMA_VERSION = "0022_chat_turn_event"
APP_VERSION = "0.1.0"


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    op.create_table(
        "email_verification_code",
        sa.Column("email", postgresql.CITEXT(), primary_key=True),
        sa.Column("purpose", sa.String(length=24), primary_key=True, server_default="register"),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False, server_default="10"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("send_ip_hash", sa.String(length=64)),
        sa.Column("request_id", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts",
            name="ck_email_code_attempts",
        ),
        sa.CheckConstraint("expires_at > sent_at", name="ck_email_code_expiry"),
        sa.CheckConstraint(
            "status = 'active' OR code_hash = ''",
            name="ck_email_code_terminal_hash",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_code_expires",
        "email_verification_code",
        ["expires_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_code_status_expires",
        "email_verification_code",
        ["status", "expires_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "email_code_send_throttle",
        sa.Column("scope", sa.String(length=16), primary_key=True),
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("hour_start", sa.DateTime(timezone=True)),
        sa.Column("hour_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("day_start", sa.DateTime(timezone=True)),
        sa.Column("day_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "hour_count >= 0 AND day_count >= 0",
            name="ck_email_throttle_counts",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_throttle_last_sent",
        "email_code_send_throttle",
        ["last_sent_at"],
        schema=SCHEMA,
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "platform.email_verification_code, platform.email_code_send_throttle "
        "TO stock_app"
    )
    op.execute(
        "GRANT SELECT ON "
        "platform.email_verification_code, platform.email_code_send_throttle "
        "TO stock_backup"
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_index(
        "ix_email_throttle_last_sent",
        table_name="email_code_send_throttle",
        schema=SCHEMA,
    )
    op.drop_table("email_code_send_throttle", schema=SCHEMA)
    op.drop_index(
        "ix_email_code_status_expires",
        table_name="email_verification_code",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_email_code_expires",
        table_name="email_verification_code",
        schema=SCHEMA,
    )
    op.drop_table("email_verification_code", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
