"""读取侧归一：把 DB 原始列/JSONB 解析成「生效值」（宽容：非法值回落默认）。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from stock_contracts.preferences import (
    BILL_FLAG_PCT_FIELDS,
    BILL_WINDOW_COLUMN,
    BILL_WINDOW_DEFAULT,
    BILL_WINDOW_MAX,
    BILL_WINDOW_MIN,
    CONCEPT_CODE_MAX_LEN,
    CONCEPT_MODES,
    CONCEPT_NAME_MAX_LEN,
    DEFAULT_APPEARANCE,
    DEFAULT_CONCEPT_FILTERS,
    DEFAULT_MARKET_SESSIONS,
    DENSITIES,
    MARKET_TIMEZONE,
    THEMES,
    TRENDS,
)

from stock_core.preferences.hash import concept_filter_hash, flags_hash
from stock_core.preferences.units import format_bps, format_window, parse_clock_minutes


@dataclass(frozen=True, slots=True)
class EffectivePreferences:
    """四组生效值（API 形状）+ 两个派生指纹。"""

    appearance: dict[str, Any]
    bill_flags: dict[str, Any]
    market_sessions: dict[str, Any]
    concept_filters: dict[str, Any]
    flags_hash: str
    concept_filter_hash: str
    #: 生效阈值（bps 整数域，键为 DB 列名）；供旗标判定构造 `TradeFlagConfig`。
    bill_bps: dict[str, int]


def _pick_enum(raw: Mapping[str, Any], key: str, allowed: Iterable[str]) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) and value in allowed else DEFAULT_APPEARANCE[key]


def effective_appearance(stored: Mapping[str, Any]) -> dict[str, Any]:
    raw = stored.get("display_prefs")
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "theme": _pick_enum(source, "theme", THEMES),
        "density": _pick_enum(source, "density", DENSITIES),
        "trend": _pick_enum(source, "trend", TRENDS),
    }


def _valid_bps(value: object, minimum: int, maximum: int, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum:
        return value
    return default


def effective_bill_bps(stored: Mapping[str, Any]) -> dict[str, int]:
    """生效阈值（bps 整数域，键为 DB 列名）。"""
    values = {
        column: _valid_bps(stored.get(column), minimum, maximum, default)
        for _, column, default, minimum, maximum in BILL_FLAG_PCT_FIELDS
    }
    values[BILL_WINDOW_COLUMN] = _valid_bps(
        stored.get(BILL_WINDOW_COLUMN),
        BILL_WINDOW_MIN,
        BILL_WINDOW_MAX,
        BILL_WINDOW_DEFAULT,
    )
    return values


def _valid_sessions(value: object) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        return None
    sessions: list[dict[str, str]] = []
    try:
        for item in value:
            if not isinstance(item, Mapping):
                return None
            open_at = item.get("open")
            close_at = item.get("close")
            if not isinstance(open_at, str) or not isinstance(close_at, str):
                return None
            parse_clock_minutes(open_at)
            parse_clock_minutes(close_at)
            sessions.append({"open": open_at, "close": close_at})
    except ValueError:
        return None
    return sessions


def effective_market_sessions(stored: Mapping[str, Any]) -> dict[str, Any]:
    raw = stored.get("market_session_prefs")
    source = raw if isinstance(raw, Mapping) else {}
    sessions = _valid_sessions(source.get("sessions")) or [
        dict(item) for item in DEFAULT_MARKET_SESSIONS["sessions"]
    ]
    ready = source.get("data_ready_after")
    if not isinstance(ready, str):
        ready = DEFAULT_MARKET_SESSIONS["data_ready_after"]
    else:
        try:
            parse_clock_minutes(ready)
        except ValueError:
            ready = DEFAULT_MARKET_SESSIONS["data_ready_after"]
    notice = source.get("session_notice_enabled")
    return {
        "sessions": sessions,
        "data_ready_after": ready,
        "session_notice_enabled": notice if isinstance(notice, bool) else True,
        "timezone": MARKET_TIMEZONE,
    }


def effective_concept_filters(stored: Mapping[str, Any]) -> dict[str, Any]:
    raw = stored.get("concept_filter_prefs")
    source = raw if isinstance(raw, Mapping) else {}
    mode = source.get("mode")
    if not isinstance(mode, str) or mode not in CONCEPT_MODES:
        mode = DEFAULT_CONCEPT_FILTERS["mode"]
    excluded: list[dict[str, str]] = []
    candidates = source.get("excluded")
    if isinstance(candidates, list):
        seen: set[str] = set()
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            code = item.get("code")
            name = item.get("name")
            if not isinstance(code, str) or not isinstance(name, str):
                continue
            if not code or len(code) > CONCEPT_CODE_MAX_LEN or len(name) > CONCEPT_NAME_MAX_LEN:
                continue
            if code in seen:
                continue
            seen.add(code)
            excluded.append({"code": code, "name": name})
    return {"mode": mode, "excluded": excluded}


def resolve(stored: Mapping[str, Any]) -> EffectivePreferences:
    appearance = effective_appearance(stored)
    bps = effective_bill_bps(stored)
    bill_flags: dict[str, Any] = {
        api_key: format_bps(bps[column]) for api_key, column, _, _, _ in BILL_FLAG_PCT_FIELDS
    }
    bill_flags["window_trading_days"] = format_window(bps[BILL_WINDOW_COLUMN])
    digest = flags_hash(bps)
    bill_flags["flags_hash"] = digest
    bill_flags["is_default"] = digest == flags_hash(
        {column: default for _, column, default, _, _ in BILL_FLAG_PCT_FIELDS}
        | {BILL_WINDOW_COLUMN: BILL_WINDOW_DEFAULT}
    )
    market_sessions = effective_market_sessions(stored)
    concept_filters = effective_concept_filters(stored)
    return EffectivePreferences(
        appearance=appearance,
        bill_flags=bill_flags,
        market_sessions=market_sessions,
        concept_filters=concept_filters,
        flags_hash=digest,
        concept_filter_hash=concept_filter_hash(
            str(concept_filters["mode"]),
            [str(item["code"]) for item in concept_filters["excluded"]],
        ),
        bill_bps=bps,
    )


__all__ = [
    "EffectivePreferences",
    "effective_appearance",
    "effective_bill_bps",
    "effective_concept_filters",
    "effective_market_sessions",
    "resolve",
]
