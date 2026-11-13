"""核心偏好纯函数单测（`make test-core` 要求 100% 行覆盖）。"""

from __future__ import annotations

import pytest
from stock_contracts.preferences import (
    DEFAULT_FLAGS_HASH,
    DEFAULT_MARKET_SESSIONS,
)
from stock_core.preferences import (
    PreferenceValidationError,
    UnitError,
    apply_patch,
    concept_filter_hash,
    effective_appearance,
    effective_bill_bps,
    effective_concept_filters,
    effective_market_sessions,
    flags_hash,
    format_bps,
    format_window,
    parse_clock_minutes,
    parse_pct_to_bps,
    parse_window,
    resolve,
)


def _patch_error(field: str, stored: dict[str, object] | None = None, **patch: object) -> str:
    with pytest.raises(PreferenceValidationError) as caught:
        apply_patch(stored or {}, patch)
    assert caught.value.field == field
    return caught.value.message


# ── units ──────────────────────────────────────────────────
def test_pct_round_trip() -> None:
    assert parse_pct_to_bps("1.00%") == 100
    assert parse_pct_to_bps("5.50%") == 550
    assert format_bps(1) == "0.01%"
    assert format_bps(500) == "5.00%"
    for bad in ("0.5%", "1,0%", "-1.00%", "1%", "", "100.00%"):
        with pytest.raises(UnitError):
            parse_pct_to_bps(bad)


def test_window_and_clock() -> None:
    assert parse_window("05") == 5
    assert format_window(5) == "5"
    for bad in ("5.0", " 5", "-1", "", "abc"):
        with pytest.raises(UnitError):
            parse_window(bad)
    assert parse_clock_minutes("09:30") == 570
    for bad in ("9:30", "24:00", "09:60", ""):
        with pytest.raises(UnitError):
            parse_clock_minutes(bad)


# ── hash ───────────────────────────────────────────────────
def test_flags_hash_matches_contract_default() -> None:
    assert flags_hash(effective_bill_bps({})) == DEFAULT_FLAGS_HASH


def test_concept_filter_hash() -> None:
    assert concept_filter_hash("off", ["BK0999"]) == ""
    first = concept_filter_hash("hide", ["BK1010", "BK0999", "BK1010"])
    second = concept_filter_hash("hide", ["BK0999", "BK1010"])
    assert first == second
    assert first != concept_filter_hash("exclude", ["BK0999", "BK1010"])


# ── resolve（宽容读取）────────────────────────────────────
def test_resolve_defaults() -> None:
    effective = resolve({})
    assert effective.appearance == {"theme": "light", "density": "cozy", "trend": "red-up"}
    assert effective.bill_flags["is_default"] is True
    assert effective.bill_flags["flags_hash"] == DEFAULT_FLAGS_HASH
    assert effective.market_sessions["sessions"] == DEFAULT_MARKET_SESSIONS["sessions"]
    assert effective.concept_filters == {"mode": "off", "excluded": []}
    assert effective.concept_filter_hash == ""


def test_resolve_invalid_values_fall_back() -> None:
    stored = {
        "display_prefs": {"theme": "neon", "density": 5, "trend": None},
        "bill_near_equal_tolerance_bps": True,
        "bill_window_trading_days": 0,
        "market_session_prefs": "not-a-dict",
        "concept_filter_prefs": {"mode": "hide", "excluded": "nope"},
    }
    effective = resolve(stored)
    assert effective.appearance == {"theme": "light", "density": "cozy", "trend": "red-up"}
    assert effective.bill_flags["near_equal_tolerance_pct"] == "1.00%"
    assert effective.bill_flags["window_trading_days"] == "5"
    assert effective.market_sessions == {
        "sessions": DEFAULT_MARKET_SESSIONS["sessions"],
        "data_ready_after": "16:30",
        "session_notice_enabled": True,
        "timezone": "Asia/Shanghai",
    }
    assert effective.concept_filters == {"mode": "hide", "excluded": []}


def test_resolve_invalid_market_sessions() -> None:
    assert (
        effective_market_sessions(
            {"market_session_prefs": {"sessions": [], "data_ready_after": "99:99"}}
        )["data_ready_after"]
        == "16:30"
    )
    assert (
        effective_market_sessions(
            {"market_session_prefs": {"sessions": ["x"], "session_notice_enabled": "yes"}}
        )["session_notice_enabled"]
        is True
    )
    assert (
        effective_market_sessions(
            {"market_session_prefs": {"sessions": [{"open": 1, "close": "15:00"}]}}
        )["sessions"]
        == DEFAULT_MARKET_SESSIONS["sessions"]
    )
    assert (
        effective_market_sessions(
            {"market_session_prefs": {"sessions": [{"open": "09:30", "close": "bad"}]}}
        )["sessions"]
        == DEFAULT_MARKET_SESSIONS["sessions"]
    )
    assert (
        effective_market_sessions(
            {"market_session_prefs": {"sessions": [{"open": "09:30", "close": "11:30"}] * 3}}
        )["sessions"]
        == DEFAULT_MARKET_SESSIONS["sessions"]
    )


def test_resolve_concept_excluded_normalization() -> None:
    stored = {
        "concept_filter_prefs": {
            "mode": "exclude",
            "excluded": [
                {"code": "BK0999", "name": "人工智能"},
                {"code": "BK0999", "name": "重复"},
                {"code": "", "name": "空"},
                {"code": "X" * 33, "name": "过长"},
                {"code": "BK1010", "name": "N" * 65},
                {"code": "BK1010"},
                "not-a-dict",
            ],
        }
    }
    effective = effective_concept_filters(stored)
    assert effective == {"mode": "exclude", "excluded": [{"code": "BK0999", "name": "人工智能"}]}


def test_effective_appearance_accepts_valid_values() -> None:
    assert effective_appearance(
        {"display_prefs": {"theme": "dark", "density": "compact", "trend": "green-up"}}
    ) == {"theme": "dark", "density": "compact", "trend": "green-up"}


# ── patch：入口校验 ────────────────────────────────────────
def test_empty_patch_rejected() -> None:
    assert _patch_error("body") == "没有可修改的字段"


def test_top_level_readonly_and_unknown() -> None:
    _patch_error("benchmark_id", None, benchmark_id="x")
    _patch_error("privacy_flags", None, privacy_flags={})
    _patch_error("unknown_group", None, unknown_group={"a": 1})


def test_group_must_be_object() -> None:
    _patch_error("appearance", None, appearance="dark")


# ── patch：外观 ────────────────────────────────────────────
def test_appearance_merge_and_reset() -> None:
    result = apply_patch({}, {"appearance": {"theme": "dark"}})
    assert result.columns == {"display_prefs": {"theme": "dark"}}
    assert result.changed == ["appearance.theme"]

    stored = {"display_prefs": {"theme": "dark", "density": "compact"}}
    removed = apply_patch(stored, {"appearance": {"theme": None}})
    assert removed.columns == {"display_prefs": {"density": "compact"}}
    assert removed.changed == ["appearance.theme"]

    reset = apply_patch(stored, {"appearance": None})
    assert reset.columns == {"display_prefs": {}}
    assert reset.changed == ["appearance.theme", "appearance.density"]


def test_appearance_no_change_and_errors() -> None:
    assert apply_patch({}, {"appearance": {"theme": "light"}}).changed == []
    assert apply_patch({"display_prefs": {"theme": "dark"}}, {"appearance": {}}).changed == []
    _patch_error("appearance.theme", None, appearance={"theme": "neon"})
    _patch_error("appearance.density", None, appearance={"density": "wide"})
    _patch_error("appearance.trend", None, appearance={"trend": "blue-up"})
    _patch_error("appearance.unknown", None, appearance={"unknown": 1})


# ── patch：账单旗标 ────────────────────────────────────────
def test_bill_flags_update_and_reset() -> None:
    result = apply_patch(
        {},
        {
            "bill_flags": {
                "near_equal_tolerance_pct": "2.00%",
                "window_trading_days": "10",
            }
        },
    )
    assert result.columns == {
        "bill_near_equal_tolerance_bps": 200,
        "bill_window_trading_days": 10,
    }
    assert result.changed == [
        "bill_flags.near_equal_tolerance_pct",
        "bill_flags.window_trading_days",
    ]

    reset_one = apply_patch(
        {"bill_chase_margin_bps": 300}, {"bill_flags": {"chase_margin_pct": None}}
    )
    assert reset_one.columns == {"bill_chase_margin_bps": 100}

    reset_all = apply_patch({"bill_chase_margin_bps": 300}, {"bill_flags": None})
    assert reset_all.columns == {"bill_chase_margin_bps": 100}
    assert reset_all.changed == ["bill_flags.chase_margin_pct"]

    reset_window = apply_patch(
        {"bill_window_trading_days": 10}, {"bill_flags": {"window_trading_days": None}}
    )
    assert reset_window.columns == {"bill_window_trading_days": 5}
    assert reset_window.changed == ["bill_flags.window_trading_days"]


def test_bill_flags_validation() -> None:
    _patch_error("bill_flags.flags_hash", None, bill_flags={"flags_hash": "x"})
    _patch_error("bill_flags.is_default", None, bill_flags={"is_default": True})
    _patch_error("bill_flags.unknown", None, bill_flags={"unknown": "1.00%"})
    _patch_error("bill_flags.chase_margin_pct", None, bill_flags={"chase_margin_pct": 1})
    _patch_error("bill_flags.chase_margin_pct", None, bill_flags={"chase_margin_pct": "abc"})
    _patch_error("bill_flags.chase_margin_pct", None, bill_flags={"chase_margin_pct": "99.00%"})
    _patch_error(
        "bill_flags.short_term_margin_pct", None, bill_flags={"short_term_margin_pct": "0.10%"}
    )
    _patch_error("bill_flags.window_trading_days", None, bill_flags={"window_trading_days": 5})
    _patch_error("bill_flags.window_trading_days", None, bill_flags={"window_trading_days": "5.0"})
    _patch_error("bill_flags.window_trading_days", None, bill_flags={"window_trading_days": "21"})
    _patch_error("bill_flags.window_trading_days", None, bill_flags={"window_trading_days": "0"})


def test_bill_flags_no_change() -> None:
    assert apply_patch({}, {"bill_flags": {"chase_margin_pct": "1.00%"}}).changed == []


# ── patch：行情时段 ────────────────────────────────────────
def test_market_sessions_merge_and_reset() -> None:
    result = apply_patch(
        {},
        {
            "market_sessions": {
                "sessions": [{"open": "09:30", "close": "15:00"}],
                "data_ready_after": "17:00",
                "session_notice_enabled": False,
            }
        },
    )
    assert result.changed == [
        "market_sessions.sessions",
        "market_sessions.data_ready_after",
        "market_sessions.session_notice_enabled",
    ]
    assert result.columns["market_session_prefs"] == {
        "sessions": [{"open": "09:30", "close": "15:00"}],
        "data_ready_after": "17:00",
        "session_notice_enabled": False,
    }

    reset = apply_patch(
        {"market_session_prefs": {"session_notice_enabled": False}},
        {"market_sessions": None},
    )
    assert reset.columns == {"market_session_prefs": {}}
    assert reset.changed == ["market_sessions.session_notice_enabled"]

    removed = apply_patch(
        {"market_session_prefs": {"data_ready_after": "17:00"}},
        {"market_sessions": {"data_ready_after": None}},
    )
    assert removed.changed == ["market_sessions.data_ready_after"]


def test_market_sessions_validation() -> None:
    _patch_error("market_sessions.timezone", None, market_sessions={"timezone": "UTC"})
    _patch_error("market_sessions.unknown", None, market_sessions={"unknown": 1})
    _patch_error("market_sessions.sessions", None, market_sessions={"sessions": []})
    _patch_error(
        "market_sessions.sessions",
        None,
        market_sessions={"sessions": [{"open": "09:30", "close": "11:30", "x": 1}]},
    )
    _patch_error(
        "market_sessions.sessions",
        None,
        market_sessions={"sessions": [{"open": 930, "close": "11:30"}]},
    )
    _patch_error(
        "market_sessions.sessions",
        None,
        market_sessions={"sessions": [{"open": "09:30", "close": "25:00"}]},
    )
    _patch_error(
        "market_sessions.sessions",
        None,
        market_sessions={"sessions": [{"open": "11:30", "close": "09:30"}]},
    )
    _patch_error(
        "market_sessions.sessions",
        None,
        market_sessions={
            "sessions": [
                {"open": "13:00", "close": "15:00"},
                {"open": "09:30", "close": "11:30"},
            ]
        },
    )
    _patch_error(
        "market_sessions.data_ready_after",
        None,
        market_sessions={"data_ready_after": 1630},
    )
    _patch_error(
        "market_sessions.data_ready_after",
        None,
        market_sessions={"data_ready_after": "9:30"},
    )
    _patch_error(
        "market_sessions.data_ready_after",
        None,
        market_sessions={
            "sessions": [{"open": "09:30", "close": "17:00"}],
            "data_ready_after": "16:30",
        },
    )
    _patch_error(
        "market_sessions.session_notice_enabled",
        None,
        market_sessions={"session_notice_enabled": "yes"},
    )
    assert apply_patch({}, {"market_sessions": {}}).changed == []


# ── patch：概念过滤 ────────────────────────────────────────
def test_concept_filters_merge_and_reset() -> None:
    result = apply_patch(
        {},
        {
            "concept_filters": {
                "mode": "hide",
                "excluded": [
                    {"code": "BK0999", "name": "人工智能"},
                    {"code": "BK0999", "name": "重复"},
                ],
            }
        },
    )
    assert result.changed == ["concept_filters.mode", "concept_filters.excluded"]
    assert result.columns["concept_filter_prefs"] == {
        "mode": "hide",
        "excluded": [{"code": "BK0999", "name": "人工智能"}],
    }

    reset = apply_patch({"concept_filter_prefs": {"mode": "hide"}}, {"concept_filters": None})
    assert reset.columns == {"concept_filter_prefs": {}}
    assert reset.changed == ["concept_filters.mode"]
    assert apply_patch({}, {"concept_filters": {"mode": "off"}}).changed == []


def test_concept_filters_validation() -> None:
    _patch_error("concept_filters.unknown", None, concept_filters={"unknown": 1})
    _patch_error("concept_filters.mode", None, concept_filters={"mode": "all"})
    _patch_error("concept_filters.excluded", None, concept_filters={"excluded": "BK"})
    _patch_error(
        "concept_filters.excluded",
        None,
        concept_filters={"excluded": [{"code": "BK", "name": "x", "resolved": True}]},
    )
    _patch_error(
        "concept_filters.excluded", None, concept_filters={"excluded": [{"code": 1, "name": "x"}]}
    )
    _patch_error(
        "concept_filters.excluded", None, concept_filters={"excluded": [{"code": "", "name": "x"}]}
    )
    _patch_error(
        "concept_filters.excluded",
        None,
        concept_filters={"excluded": [{"code": "X" * 33, "name": "x"}]},
    )
    _patch_error(
        "concept_filters.excluded",
        None,
        concept_filters={"excluded": [{"code": "BK", "name": "N" * 65}]},
    )
    _patch_error(
        "concept_filters.excluded",
        None,
        concept_filters={"excluded": [{"code": f"BK{i:04d}", "name": "x"} for i in range(101)]},
    )


# ── patch：多组合并 ────────────────────────────────────────
def test_multi_group_patch() -> None:
    result = apply_patch(
        {},
        {
            "appearance": {"theme": "dark"},
            "bill_flags": {"window_trading_days": "10"},
            "concept_filters": {"mode": "exclude", "excluded": [{"code": "BK0999", "name": "AI"}]},
        },
    )
    assert result.changed == [
        "appearance.theme",
        "bill_flags.window_trading_days",
        "concept_filters.mode",
        "concept_filters.excluded",
    ]
    assert result.stored["display_prefs"] == {"theme": "dark"}
    assert result.stored["bill_window_trading_days"] == 10
    assert result.stored["concept_filter_prefs"]["mode"] == "exclude"
