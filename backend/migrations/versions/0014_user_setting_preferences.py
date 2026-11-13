"""用户偏好配置 v2.1（2026-11-13）

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-13

- `platform.user_setting`：+7 列（4 bps 阈值 + 窗口 + 2 个 JSONB）与 6 条 CHECK；
  `display_prefs` 原地启用为外观真值源；
- `review.trade_flag`：+`flags_hash`（默认 flags_hash 回填后 **DROP DEFAULT**，
  让构建漏传 hash 立即失败而不是被默认值掩盖）+ `(user_id, flags_hash, definition_version)` 索引；
- 版本推进到 `0014_user_setting_preferences`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_VERSION = "0014_user_setting_preferences"
PREVIOUS_SCHEMA_VERSION = "0013_review_trade_flags"
APP_VERSION = "0.1.0"

#: 默认配置的 flags_hash（`stock_contracts.preferences.DEFAULT_FLAGS_HASH`）。
DEFAULT_FLAGS_HASH = "c425bc5b871f59f5"

_SETTING_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("bill_near_equal_tolerance_bps", "integer", "100"),
    ("bill_chase_margin_bps", "integer", "100"),
    ("bill_stop_loss_margin_bps", "integer", "100"),
    ("bill_short_term_margin_bps", "integer", "500"),
    ("bill_window_trading_days", "smallint", "5"),
    ("market_session_prefs", "jsonb", "'{}'::jsonb"),
    ("concept_filter_prefs", "jsonb", "'{}'::jsonb"),
)

_SETTING_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "ck_user_setting_near_equal",
        "bill_near_equal_tolerance_bps BETWEEN 10 AND 500",
    ),
    ("ck_user_setting_chase", "bill_chase_margin_bps BETWEEN 10 AND 500"),
    ("ck_user_setting_stop_loss", "bill_stop_loss_margin_bps BETWEEN 10 AND 500"),
    ("ck_user_setting_short_term", "bill_short_term_margin_bps BETWEEN 50 AND 2000"),
    ("ck_user_setting_window", "bill_window_trading_days BETWEEN 1 AND 20"),
    (
        "ck_user_setting_prefs_json",
        "jsonb_typeof(market_session_prefs) = 'object' AND "
        "jsonb_typeof(concept_filter_prefs) = 'object'",
    ),
)


def _stamp_schema_version(version: str) -> None:
    op.execute(
        "INSERT INTO platform.schema_version (id, version, app_version, applied_at) "
        f"VALUES (1, '{version}', '{APP_VERSION}', now()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
        "app_version = EXCLUDED.app_version, applied_at = EXCLUDED.applied_at"
    )


def upgrade() -> None:
    for column, sql_type, default in _SETTING_COLUMNS:
        op.execute(
            f"ALTER TABLE platform.user_setting ADD COLUMN {column} {sql_type} "
            f"NOT NULL DEFAULT {default}"
        )
    for name, predicate in _SETTING_CHECKS:
        op.execute(f"ALTER TABLE platform.user_setting ADD CONSTRAINT {name} CHECK ({predicate})")

    op.add_column(
        "trade_flag",
        sa.Column(
            "flags_hash",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_FLAGS_HASH}'"),
        ),
        schema="review",
    )
    op.create_index(
        "ix_trade_flag_user_hash",
        "trade_flag",
        ["user_id", "flags_hash", "definition_version"],
        schema="review",
    )
    # 回填完成后撤掉默认值：构建路径必须显式带 hash（§3.3）。
    op.execute("ALTER TABLE review.trade_flag ALTER COLUMN flags_hash DROP DEFAULT")

    _stamp_schema_version(SCHEMA_VERSION)


def downgrade() -> None:
    op.drop_index("ix_trade_flag_user_hash", table_name="trade_flag", schema="review")
    op.drop_column("trade_flag", "flags_hash", schema="review")
    for name, _ in reversed(_SETTING_CHECKS):
        op.execute(f"ALTER TABLE platform.user_setting DROP CONSTRAINT IF EXISTS {name}")
    for column, _, _ in reversed(_SETTING_COLUMNS):
        op.execute(f"ALTER TABLE platform.user_setting DROP COLUMN IF EXISTS {column}")
    _stamp_schema_version(PREVIOUS_SCHEMA_VERSION)
