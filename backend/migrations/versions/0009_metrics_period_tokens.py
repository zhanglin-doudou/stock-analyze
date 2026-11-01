"""指标 period 支持月/年/自定义区间（2026-10-30）

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-30

背景：月度指标对低频操作者常样本不足，指标页与看板改为「年度」默认口径，并允许
切换月度 / 自定义闭区间。`metric_result.period` / `metric_dependency.period` 由
`YYYY-MM`（7 字符）扩展为三种 token：

- `YYYY-MM`（月）
- `YYYY`（年）
- `YYYY-MM-DD..YYYY-MM-DD`（自定义闭区间，最长 23 字符；不落库，仅兼容查询）

本迁移：两表 `period` 列宽 `varchar(7)` → `varchar(32)`，并把
`ck_metric_result_period` 重写为三态正则。历史行（YYYY-MM）仍满足新约束。

downgrade 还原列宽与旧 CHECK；自定义区间行已超过 7 字符，回退时需先清理（生产
不会出现自定义区间行 —— 自定义区间不落库）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_VERSION = "0009_metrics_period_tokens"
PREVIOUS_SCHEMA_VERSION = "0008_money_fund_bond"
APP_VERSION = "0.1.0"

OLD_PERIOD_CHECK = r"period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'"
NEW_PERIOD_CHECK = (
    r"period ~ '^([0-9]{4}|[0-9]{4}-(0[1-9]|1[0-2])"
    r"|[0-9]{4}-(0[1-9]|1[0-2])-[0-9]{2}\.\.[0-9]{4}-(0[1-9]|1[0-2])-[0-9]{2})$'"
)


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    for table in ("metric_result", "metric_dependency"):
        op.alter_column(
            table,
            "period",
            existing_type=sa.String(length=7),
            type_=sa.String(length=32),
            existing_nullable=False,
            schema="review",
        )
    op.drop_constraint("ck_metric_result_period", "metric_result", schema="review", type_="check")
    op.create_check_constraint(
        "ck_metric_result_period", "metric_result", NEW_PERIOD_CHECK, schema="review"
    )
    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_constraint("ck_metric_result_period", "metric_result", schema="review", type_="check")
    op.create_check_constraint(
        "ck_metric_result_period", "metric_result", OLD_PERIOD_CHECK, schema="review"
    )
    for table in ("metric_result", "metric_dependency"):
        op.alter_column(
            table,
            "period",
            existing_type=sa.String(length=32),
            type_=sa.String(length=7),
            existing_nullable=False,
            schema="review",
        )
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
