"""review 指标物化 + 血缘表 + 租户 RLS（Period2-05/06）

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26

背景：GET /metrics 需要「读即命中」的物化结果，report/trends 也需要历史可读的指标行。
本迁移新建：

- `review.metric_result`：一行 = 某用户某指标在某 period、口径版本、参数组合下的
  最近一次成功计算（数值一律 BigInteger 标度整数；非 ok 不落库，E-11）；
- `review.metric_dependency`：一次物化实际消费的上游指标 / 数据集 + 指纹（血缘审计）。

设计要点：
- 主键确定性（`mtr_` / `mtd_` + sha256 前缀），重复计算按唯一键 UPSERT 幂等；
- `UNIQUE(user_id, metric_id, period, definition_version, params_hash)`：口径 bump 后
  新版本插入、旧版本保留（趋势断点可读历史段）；
- DB CHECK：status/sample_status/unit/period/version 白名单与正则、非 ok 不出数字、
  CI 上下界成对且仅 ok 行可出、非 ok 必带 reason；
- RLS 谓词与 0001~0006 完全一致（USING / WITH CHECK 同谓词，未设 `app.user_id` fail-closed）；
- 授权沿用三角色模型：`stock_app` 只 DML、`stock_backup` 只 SELECT；
- `platform.schema_version.version` 更新为 `0007_review_metrics`（服务启动即校验）；
- downgrade 完整回滚：删索引/策略/两表，并把版本字符串还原到 `0006_review_asset_daily`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "review"
SCHEMA_VERSION = "0007_review_metrics"
PREVIOUS_SCHEMA_VERSION = "0006_review_asset_daily"
APP_VERSION = "0.1.0"

TENANT_PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"

# 本迁移启用 RLS 的 review 租户表（表名清单与 stock_review.models.REVIEW_TABLES 一致）
RLS_TABLES: tuple[str, ...] = ("metric_result", "metric_dependency")


def _create_metric_result() -> None:
    op.create_table(
        "metric_result",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", sa.String(length=64), nullable=False),
        sa.Column("definition_version", sa.String(length=16), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sample_status", sa.String(length=16), nullable=False),
        sa.Column("value_units", sa.BigInteger(), nullable=True),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("precision", sa.Integer(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ci_low_units", sa.BigInteger(), nullable=True),
        sa.Column("ci_high_units", sa.BigInteger(), nullable=True),
        sa.Column("decomposition", postgresql.JSONB(), nullable=True),
        sa.Column(
            "assumptions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("inputs_hash", sa.String(length=80), nullable=False),
        sa.Column("data_as_of", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.user.id"],
            name="fk_metric_result_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "metric_id",
            "period",
            "definition_version",
            "params_hash",
            name="uq_metric_result_user_metric_period_version_params",
        ),
        sa.CheckConstraint(
            "status IN ('ok','stale','blocked','unreliable')",
            name="ck_metric_result_status",
        ),
        sa.CheckConstraint(
            "sample_status IN ('ok','insufficient','pending_window','blocked')",
            name="ck_metric_result_sample_status",
        ),
        sa.CheckConstraint(
            "definition_version ~ '^[0-9]+[.][0-9]+$'",
            name="ck_metric_result_definition_version",
        ),
        sa.CheckConstraint(
            r"period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_metric_result_period",
        ),
        sa.CheckConstraint(
            "unit IN ('CNY','ratio','days','count','z')", name="ck_metric_result_unit"
        ),
        sa.CheckConstraint("precision BETWEEN 0 AND 8", name="ck_metric_result_precision"),
        sa.CheckConstraint("n >= 0 AND n_pending >= 0", name="ck_metric_result_n_nonneg"),
        sa.CheckConstraint(
            "value_units IS NULL OR (status = 'ok' AND sample_status IN ('ok','insufficient'))",
            name="ck_metric_result_value_gate",
        ),
        sa.CheckConstraint(
            "(ci_low_units IS NULL) = (ci_high_units IS NULL) "
            "AND (ci_low_units IS NULL OR (status = 'ok' AND sample_status = 'ok'))",
            name="ck_metric_result_ci_gate",
        ),
        sa.CheckConstraint(
            "NOT (status = 'ok' AND sample_status = 'blocked') "
            "AND (status = 'ok' OR reason IS NOT NULL)",
            name="ck_metric_result_status_gate",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metric_result_user_period",
        "metric_result",
        ["user_id", "period"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metric_result_user_domain",
        "metric_result",
        ["user_id", "domain"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metric_result_user_metric_period",
        "metric_result",
        ["user_id", "metric_id", "period"],
        unique=False,
        schema=SCHEMA,
    )


def _create_metric_dependency() -> None:
    op.create_table(
        "metric_dependency",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("definition_version", sa.String(length=16), nullable=False),
        sa.Column("dep_kind", sa.String(length=16), nullable=False),
        sa.Column("dep_id", sa.String(length=64), nullable=False),
        sa.Column("dep_hash", sa.String(length=80), nullable=False),
        sa.Column("metric_result_id", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform.user.id"],
            name="fk_metric_dependency_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["metric_result_id"],
            [f"{SCHEMA}.metric_result.id"],
            name="fk_metric_dependency_result",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "metric_id",
            "period",
            "definition_version",
            "dep_kind",
            "dep_id",
            name="uq_metric_dependency_key",
        ),
        sa.CheckConstraint("dep_kind IN ('metric','dataset')", name="ck_metric_dependency_kind"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metric_dependency_user_metric",
        "metric_dependency",
        ["user_id", "metric_id", "period"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metric_dependency_user_dep",
        "metric_dependency",
        ["user_id", "dep_kind", "dep_id"],
        unique=False,
        schema=SCHEMA,
    )


def _enable_rls() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def _grant_roles() -> None:
    tables = ", ".join(f"{SCHEMA}.{table}" for table in RLS_TABLES)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO stock_app")
    op.execute(f"GRANT SELECT ON {tables} TO stock_backup")


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    _create_metric_result()
    _create_metric_dependency()
    _enable_rls()
    _grant_roles()
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_metric_dependency_user_dep", table_name="metric_dependency", schema=SCHEMA)
    op.drop_index("ix_metric_dependency_user_metric", table_name="metric_dependency", schema=SCHEMA)
    op.drop_table("metric_dependency", schema=SCHEMA)
    op.drop_index("ix_metric_result_user_metric_period", table_name="metric_result", schema=SCHEMA)
    op.drop_index("ix_metric_result_user_domain", table_name="metric_result", schema=SCHEMA)
    op.drop_index("ix_metric_result_user_period", table_name="metric_result", schema=SCHEMA)
    op.drop_table("metric_result", schema=SCHEMA)
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
