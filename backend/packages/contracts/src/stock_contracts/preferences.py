"""用户偏好配置的**声明式**注册表（设计 2026-11-11 §3/§4）。

本模块只声明「API 组 → 存储形态（typed 列 / JSONB 键）/ 默认值 / 只读 / 单位」，
不做合并、校验或哈希（那些是 `stock_core.preferences` 的纯函数）。这样 contracts
保持零依赖（L1），core 反向依赖 contracts 的默认值。

单位约定：
* 阈值在 API 边界是百分比字符串（`"1.00%"`），DB 用整数 bps；
* 窗口在 API 边界也是十进制字符串（`"5"`），避免前端 `Number()/parseInt`；
* 默认值与 DB `DEFAULT` 由单测锁定一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

# ── 组名（PATCH 顶层键）────────────────────────────────────
GROUP_APPEARANCE: Final = "appearance"
GROUP_BILL_FLAGS: Final = "bill_flags"
GROUP_MARKET_SESSIONS: Final = "market_sessions"
GROUP_CONCEPT_FILTERS: Final = "concept_filters"

GROUPS: Final[tuple[str, ...]] = (
    GROUP_APPEARANCE,
    GROUP_BILL_FLAGS,
    GROUP_MARKET_SESSIONS,
    GROUP_CONCEPT_FILTERS,
)

# ── 枚举 ───────────────────────────────────────────────────
THEMES: Final[tuple[str, ...]] = ("light", "dark", "system")
DENSITIES: Final[tuple[str, ...]] = ("cozy", "compact")
TRENDS: Final[tuple[str, ...]] = ("red-up", "green-up")
CONCEPT_MODES: Final[tuple[str, ...]] = ("off", "hide", "exclude")

# ── 外观与显示（存 display_prefs JSONB）────────────────────
DEFAULT_APPEARANCE: Final[dict[str, Any]] = {
    "theme": "light",
    "density": "cozy",
    "trend": "red-up",
}

# ── 账单旗标阈值（typed 列）──────────────────────────────────
#: API 键 → (DB 列, 默认 bps, 最小 bps, 最大 bps)。
BILL_FLAG_PCT_FIELDS: Final[tuple[tuple[str, str, int, int, int], ...]] = (
    ("near_equal_tolerance_pct", "bill_near_equal_tolerance_bps", 100, 10, 500),
    ("chase_margin_pct", "bill_chase_margin_bps", 100, 10, 500),
    ("stop_loss_margin_pct", "bill_stop_loss_margin_bps", 100, 10, 500),
    ("short_term_margin_pct", "bill_short_term_margin_bps", 500, 50, 2000),
)

BILL_WINDOW_FIELD: Final = "window_trading_days"
BILL_WINDOW_COLUMN: Final = "bill_window_trading_days"
BILL_WINDOW_DEFAULT: Final = 5
BILL_WINDOW_MIN: Final = 1
BILL_WINDOW_MAX: Final = 20

#: 默认 flags_hash（`flags_hash(默认四阈值 + 默认窗口)` 的 SHA-256 前 16 hex）。
#: 由 `DEFAULT_BILL_FLAGS_BPS` 规范 JSON 计算，单测断言与 core 实现一致。
DEFAULT_FLAGS_HASH: Final = "c425bc5b871f59f5"


def default_bill_flags_bps() -> dict[str, int]:
    """默认阈值（bps 整数域，键为 DB 列名）。"""
    return {column: default for _, column, default, _, _ in BILL_FLAG_PCT_FIELDS} | {
        BILL_WINDOW_COLUMN: BILL_WINDOW_DEFAULT
    }


DEFAULT_BILL_FLAGS_BPS: Final[dict[str, int]] = default_bill_flags_bps()

# ── 行情时段（存 market_session_prefs JSONB，仅展示/提醒）───
MARKET_TIMEZONE: Final = "Asia/Shanghai"

DEFAULT_MARKET_SESSIONS: Final[dict[str, Any]] = {
    "sessions": [
        {"open": "09:30", "close": "11:30"},
        {"open": "13:00", "close": "15:00"},
    ],
    "data_ready_after": "16:30",
    "session_notice_enabled": True,
}

#: 只读键（PATCH 传入 → 422）。
MARKET_SESSIONS_READONLY: Final[frozenset[str]] = frozenset({"timezone"})

# ── 概念板块过滤（存 concept_filter_prefs JSONB）─────────────
CONCEPT_EXCLUDED_LIMIT: Final = 100
CONCEPT_CODE_MAX_LEN: Final = 32
CONCEPT_NAME_MAX_LEN: Final = 64

DEFAULT_CONCEPT_FILTERS: Final[dict[str, Any]] = {"mode": "off", "excluded": []}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """单个可写字段的声明（组 / API 键 / 存储 / 默认 / 只读 / 单位）。"""

    group: str
    key: str
    storage: Literal["jsonb", "typed"]
    column: str
    default: Any
    readonly: bool = False
    unit: str = ""


FIELD_SPECS: Final[tuple[FieldSpec, ...]] = (
    FieldSpec(GROUP_APPEARANCE, "theme", "jsonb", "display_prefs", "light", unit="enum"),
    FieldSpec(GROUP_APPEARANCE, "density", "jsonb", "display_prefs", "cozy", unit="enum"),
    FieldSpec(GROUP_APPEARANCE, "trend", "jsonb", "display_prefs", "red-up", unit="enum"),
    *(
        FieldSpec(
            GROUP_BILL_FLAGS,
            api_key,
            "typed",
            column,
            default_bps,
            unit="bps",
        )
        for api_key, column, default_bps, _, _ in BILL_FLAG_PCT_FIELDS
    ),
    FieldSpec(
        GROUP_BILL_FLAGS,
        BILL_WINDOW_FIELD,
        "typed",
        BILL_WINDOW_COLUMN,
        BILL_WINDOW_DEFAULT,
        unit="trading_days",
    ),
    FieldSpec(GROUP_BILL_FLAGS, "flags_hash", "typed", "", "", readonly=True),
    FieldSpec(GROUP_BILL_FLAGS, "is_default", "typed", "", "", readonly=True),
    FieldSpec(GROUP_MARKET_SESSIONS, "sessions", "jsonb", "market_session_prefs", ()),
    FieldSpec(GROUP_MARKET_SESSIONS, "data_ready_after", "jsonb", "market_session_prefs", "16:30"),
    FieldSpec(
        GROUP_MARKET_SESSIONS,
        "session_notice_enabled",
        "jsonb",
        "market_session_prefs",
        True,
    ),
    FieldSpec(
        GROUP_MARKET_SESSIONS,
        "timezone",
        "jsonb",
        "market_session_prefs",
        MARKET_TIMEZONE,
        readonly=True,
    ),
    FieldSpec(GROUP_CONCEPT_FILTERS, "mode", "jsonb", "concept_filter_prefs", "off", unit="enum"),
    FieldSpec(GROUP_CONCEPT_FILTERS, "excluded", "jsonb", "concept_filter_prefs", ()),
)

#: 组 → 可写 JSONB 键白名单（typed 组由 BILL_FLAG_* 承担）。
JSONB_WRITABLE_KEYS: Final[dict[str, frozenset[str]]] = {
    GROUP_APPEARANCE: frozenset({"theme", "density", "trend"}),
    GROUP_MARKET_SESSIONS: frozenset({"sessions", "data_ready_after", "session_notice_enabled"}),
    GROUP_CONCEPT_FILTERS: frozenset({"mode", "excluded"}),
}

#: 组 → JSONB 列名。
JSONB_COLUMNS: Final[dict[str, str]] = {
    GROUP_APPEARANCE: "display_prefs",
    GROUP_MARKET_SESSIONS: "market_session_prefs",
    GROUP_CONCEPT_FILTERS: "concept_filter_prefs",
}

#: 组 → 只读 API 键（PATCH 传入 → 422）。
READONLY_KEYS: Final[dict[str, frozenset[str]]] = {
    GROUP_APPEARANCE: frozenset(),
    GROUP_BILL_FLAGS: frozenset({"flags_hash", "is_default"}),
    GROUP_MARKET_SESSIONS: MARKET_SESSIONS_READONLY,
    GROUP_CONCEPT_FILTERS: frozenset(),
}

#: 顶层只读回显键（本期不开放写入口）。
TOP_LEVEL_READONLY: Final[frozenset[str]] = frozenset(
    {"benchmark_id", "privacy_flags", "version", "updated_at"}
)

__all__ = [
    "BILL_FLAG_PCT_FIELDS",
    "BILL_WINDOW_COLUMN",
    "BILL_WINDOW_DEFAULT",
    "BILL_WINDOW_FIELD",
    "BILL_WINDOW_MAX",
    "BILL_WINDOW_MIN",
    "CONCEPT_CODE_MAX_LEN",
    "CONCEPT_EXCLUDED_LIMIT",
    "CONCEPT_MODES",
    "CONCEPT_NAME_MAX_LEN",
    "DEFAULT_APPEARANCE",
    "DEFAULT_BILL_FLAGS_BPS",
    "DEFAULT_CONCEPT_FILTERS",
    "DEFAULT_FLAGS_HASH",
    "DEFAULT_MARKET_SESSIONS",
    "DENSITIES",
    "FIELD_SPECS",
    "GROUPS",
    "GROUP_APPEARANCE",
    "GROUP_BILL_FLAGS",
    "GROUP_CONCEPT_FILTERS",
    "GROUP_MARKET_SESSIONS",
    "JSONB_COLUMNS",
    "JSONB_WRITABLE_KEYS",
    "MARKET_SESSIONS_READONLY",
    "MARKET_TIMEZONE",
    "READONLY_KEYS",
    "THEMES",
    "TOP_LEVEL_READONLY",
    "TRENDS",
    "FieldSpec",
]
