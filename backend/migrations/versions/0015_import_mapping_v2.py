"""导入映射 v2：模板 / 账单类型 / 列规则（2026-11-13）

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-13

设计 `docs/architecture/2026-11-13-import-mapping-refactor.md` §5.2：
- 新表 `ledger.import_template` / `ledger.import_bill_type` / `ledger.import_column_rule`；
- `import_preview` / `import_batch` 增加两阶段草稿与提交快照列；
- 历史预览标记 `stage='legacy'`，历史批次 `mapping_source='legacy'`；
- 三张新表启用 RLS tenant 策略，版本推进到 `0015_import_mapping_v2`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ledger"
SCHEMA_VERSION = "0015_import_mapping_v2"
PREVIOUS_SCHEMA_VERSION = "0014_user_setting_preferences"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
RLS_TABLES: tuple[str, ...] = (
    "import_template",
    "import_bill_type",
    "import_column_rule",
)


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def _create_import_template() -> None:
    op.create_table(
        "import_template",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker", sa.String(length=16), nullable=False),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("header_count", sa.Integer(), nullable=False),
        sa.Column("date_format", sa.String(length=32), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ignored", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin_batch_id", sa.String(length=40), nullable=True),
        sa.Column("last_batch_id", sa.String(length=40), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.UniqueConstraint("user_id", "signature", name="uq_import_template_user_signature"),
        sa.CheckConstraint("header_count >= 0", name="ck_import_template_header_count"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform.user.id"], name="fk_import_template_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_import_template_user",
        "import_template",
        ["user_id", "updated_at"],
        schema=SCHEMA,
    )


def _create_import_bill_type() -> None:
    op.create_table(
        "import_bill_type",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_key", sa.String(length=64), nullable=False),
        sa.Column("type_label", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=True),
        sa.Column("discarded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("broker", sa.String(length=16), nullable=True),
        sa.Column("origin_batch_id", sa.String(length=40), nullable=True),
        sa.Column("last_write_batch_id", sa.String(length=40), nullable=True),
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
        sa.UniqueConstraint("user_id", "type_key", name="uq_import_bill_type_user_key"),
        sa.CheckConstraint("length(type_key) BETWEEN 1 AND 64", name="ck_import_bill_type_key"),
        sa.CheckConstraint(
            "(discarded AND category IS NULL) OR (NOT discarded AND category IS NOT NULL)",
            name="ck_import_bill_type_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform.user.id"], name="fk_import_bill_type_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_import_bill_type_user",
        "import_bill_type",
        ["user_id", "updated_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_import_bill_type_write",
        "import_bill_type",
        ["user_id", "last_write_batch_id"],
        schema=SCHEMA,
    )


def _create_import_column_rule() -> None:
    op.create_table(
        "import_column_rule",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("broker", sa.String(length=16), nullable=False),
        sa.Column("header_norm", sa.String(length=128), nullable=False),
        sa.Column(
            "match_type", sa.String(length=8), nullable=False, server_default=sa.text("'exact'")
        ),
        sa.Column("target_field", sa.String(length=24), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_fingerprint", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("weight", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.UniqueConstraint(
            "user_id",
            "broker",
            "header_norm",
            "target_field",
            name="uq_import_column_rule",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("length(broker) BETWEEN 1 AND 16", name="ck_import_column_rule_broker"),
        sa.CheckConstraint("weight BETWEEN 1 AND 10", name="ck_import_column_rule_weight"),
        sa.CheckConstraint(
            "match_type IN ('exact','prefix','contains')", name="ck_import_column_rule_match"
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 999", name="ck_import_column_rule_priority"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.user.id"],
            name="fk_import_column_rule_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_import_column_rule_lookup",
        "import_column_rule",
        ["broker", "enabled", "priority"],
        schema=SCHEMA,
    )


def _alter_draft_tables() -> None:
    op.add_column(
        "import_preview",
        sa.Column(
            "broker", sa.String(length=16), nullable=False, server_default=sa.text("'generic'")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview", sa.Column("signature", sa.String(length=64), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "import_preview", sa.Column("header_row_index", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "import_preview",
        sa.Column("mapping_fingerprint", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column(
            "stage", sa.String(length=16), nullable=False, server_default=sa.text("'mapping'")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column(
            "mapping_items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column(
            "ignored_columns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column("mapping_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column("mapping_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column("types_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column(
            "type_resolutions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column("template_id", sa.String(length=40), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column(
            "verification",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_preview",
        sa.Column("discarded_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )

    op.add_column(
        "import_batch", sa.Column("template_id", sa.String(length=40), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "import_batch", sa.Column("signature", sa.String(length=64), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "import_batch", sa.Column("header_row_index", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "import_batch",
        sa.Column("mapping_fingerprint", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "import_batch",
        sa.Column(
            "mapping_items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_batch",
        sa.Column(
            "type_resolutions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_batch",
        sa.Column(
            "mapping_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "import_batch",
        sa.Column("discarded_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )
    op.add_column(
        "import_batch",
        sa.Column("remap_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_import_batch_user_file",
        "import_batch",
        ["user_id", "file_sha256"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_import_batch_user_signature",
        "import_batch",
        ["user_id", "signature", "created_at"],
        schema=SCHEMA,
    )

    # 历史预览/批次统一标记为 legacy：v2 提交要求 stage=ready 与映射快照，
    # 旧数据没有这些信息，只能走旧提交路径或重新上传。
    op.execute(f"UPDATE {SCHEMA}.import_preview SET broker = 'huatai', stage = 'legacy'")
    op.execute(f"UPDATE {SCHEMA}.import_batch SET mapping_source = 'legacy'")


def _enable_rls() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        if table == "import_column_rule":
            # 全局规则（user_id IS NULL）对所有租户可见；这是显式例外。
            using = f"user_id IS NULL OR ({TENANT_PREDICATE})"
            op.execute(
                f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
                f"USING ({using}) WITH CHECK ({using})"
            )
            continue
        op.execute(
            f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def _grant_roles() -> None:
    tables = ", ".join(f"{SCHEMA}.{table}" for table in RLS_TABLES)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO stock_app")
    op.execute(f"GRANT SELECT ON {tables} TO stock_backup")


def upgrade() -> None:
    _create_import_template()
    _create_import_bill_type()
    _create_import_column_rule()
    _alter_draft_tables()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_import_batch_user_signature", table_name="import_batch", schema=SCHEMA)
    op.drop_index("ix_import_batch_user_file", table_name="import_batch", schema=SCHEMA)
    for column in (
        "remap_required",
        "discarded_count",
        "mapping_source",
        "type_resolutions",
        "mapping_items",
        "mapping_fingerprint",
        "header_row_index",
        "signature",
        "template_id",
    ):
        op.drop_column("import_batch", column, schema=SCHEMA)
    for column in (
        "discarded_count",
        "verification",
        "template_id",
        "type_resolutions",
        "types_confirmed_at",
        "mapping_confirmed_at",
        "mapping_version",
        "ignored_columns",
        "mapping_items",
        "stage",
        "mapping_fingerprint",
        "header_row_index",
        "signature",
        "broker",
    ):
        op.drop_column("import_preview", column, schema=SCHEMA)
    op.drop_table("import_column_rule", schema=SCHEMA)
    op.drop_table("import_bill_type", schema=SCHEMA)
    op.drop_table("import_template", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
