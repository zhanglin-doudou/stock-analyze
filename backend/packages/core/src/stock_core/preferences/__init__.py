"""用户偏好纯函数层（设计 2026-11-11 §3.5）：合并 / 归一 / 指纹 / 单位。

只依赖 `stock_contracts`（L2）；不触库、不依赖 API/平台层，便于 100% 覆盖单测。
"""

from stock_core.preferences.hash import canonical_json, concept_filter_hash, flags_hash
from stock_core.preferences.patch import (
    PatchResult,
    PreferenceValidationError,
    apply_patch,
)
from stock_core.preferences.resolve import (
    EffectivePreferences,
    effective_appearance,
    effective_bill_bps,
    effective_concept_filters,
    effective_market_sessions,
    resolve,
)
from stock_core.preferences.units import (
    UnitError,
    format_bps,
    format_window,
    parse_clock_minutes,
    parse_pct_to_bps,
    parse_window,
)

__all__ = [
    "EffectivePreferences",
    "PatchResult",
    "PreferenceValidationError",
    "UnitError",
    "apply_patch",
    "canonical_json",
    "concept_filter_hash",
    "effective_appearance",
    "effective_bill_bps",
    "effective_concept_filters",
    "effective_market_sessions",
    "flags_hash",
    "format_bps",
    "format_window",
    "parse_clock_minutes",
    "parse_pct_to_bps",
    "parse_window",
    "resolve",
]
