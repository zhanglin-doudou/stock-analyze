"""PATCH 合并（纯函数，§4.2）：typed 局部更新 + JSONB RFC 7396 合并 + 跨字段校验。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from stock_contracts.preferences import (
    BILL_FLAG_PCT_FIELDS,
    BILL_WINDOW_COLUMN,
    BILL_WINDOW_DEFAULT,
    BILL_WINDOW_FIELD,
    BILL_WINDOW_MAX,
    BILL_WINDOW_MIN,
    CONCEPT_CODE_MAX_LEN,
    CONCEPT_EXCLUDED_LIMIT,
    CONCEPT_MODES,
    CONCEPT_NAME_MAX_LEN,
    DENSITIES,
    GROUP_APPEARANCE,
    GROUP_BILL_FLAGS,
    GROUP_CONCEPT_FILTERS,
    GROUP_MARKET_SESSIONS,
    GROUPS,
    JSONB_COLUMNS,
    JSONB_WRITABLE_KEYS,
    READONLY_KEYS,
    THEMES,
    TOP_LEVEL_READONLY,
    TRENDS,
)

from stock_core.preferences.resolve import (
    effective_appearance,
    effective_bill_bps,
    effective_concept_filters,
    effective_market_sessions,
)
from stock_core.preferences.units import (
    UnitError,
    parse_clock_minutes,
    parse_pct_to_bps,
    parse_window,
)


class PreferenceValidationError(ValueError):
    """字段级校验错误（API 层转 422 VALIDATION_ERROR，`field` 为点号路径）。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True, slots=True)
class PatchResult:
    """合并结果：新的原始存储值、需要落库的列、变更点列表（点号路径）。"""

    stored: dict[str, Any]
    columns: dict[str, Any]
    changed: list[str]


_PCT_DEFAULTS: dict[str, tuple[str, int, int, int]] = {
    api_key: (column, default, minimum, maximum)
    for api_key, column, default, minimum, maximum in BILL_FLAG_PCT_FIELDS
}

_BILL_WRITABLE: frozenset[str] = frozenset(_PCT_DEFAULTS) | {BILL_WINDOW_FIELD}


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreferenceValidationError(field, "必须是对象")
    return value


def _check_keys(
    group: str,
    payload: Mapping[str, Any],
    writable: Iterable[str],
) -> None:
    allowed = set(writable)
    readonly = READONLY_KEYS.get(group, frozenset())
    for key in payload:
        if key in readonly:
            raise PreferenceValidationError(f"{group}.{key}", "只读字段不可修改")
        if key not in allowed:
            raise PreferenceValidationError(f"{group}.{key}", "未知字段")


def _validate_enum(field: str, value: object, allowed: Iterable[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise PreferenceValidationError(field, f"取值必须是 {sorted(allowed)} 之一")
    return value


def _validate_sessions(value: object) -> list[dict[str, str]]:
    field = f"{GROUP_MARKET_SESSIONS}.sessions"
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        raise PreferenceValidationError(field, "交易时段必须是 1~2 段")
    sessions: list[dict[str, str]] = []
    previous_close = -1
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"open", "close"}:
            raise PreferenceValidationError(field, f"第 {index} 段必须且只能包含 open/close")
        open_at = item.get("open")
        close_at = item.get("close")
        if not isinstance(open_at, str) or not isinstance(close_at, str):
            raise PreferenceValidationError(field, f"第 {index} 段 open/close 必须是字符串")
        try:
            open_minutes = parse_clock_minutes(open_at)
            close_minutes = parse_clock_minutes(close_at)
        except UnitError as exc:
            raise PreferenceValidationError(field, f"第 {index} 段：{exc}") from exc
        if open_minutes >= close_minutes:
            raise PreferenceValidationError(field, f"第 {index} 段 open 必须早于 close")
        if open_minutes < previous_close:
            raise PreferenceValidationError(field, f"第 {index} 段与上一段重叠或未升序")
        previous_close = close_minutes
        sessions.append({"open": open_at, "close": close_at})
    return sessions


def _validate_excluded(value: object) -> list[dict[str, str]]:
    field = f"{GROUP_CONCEPT_FILTERS}.excluded"
    if not isinstance(value, list):
        raise PreferenceValidationError(field, "必须是数组")
    if len(value) > CONCEPT_EXCLUDED_LIMIT:
        raise PreferenceValidationError(field, f"最多 {CONCEPT_EXCLUDED_LIMIT} 个概念")
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"code", "name"}:
            raise PreferenceValidationError(field, f"第 {index} 项必须且只能包含 code/name")
        code = item.get("code")
        name = item.get("name")
        if not isinstance(code, str) or not isinstance(name, str):
            raise PreferenceValidationError(field, f"第 {index} 项 code/name 必须是字符串")
        if not code or len(code) > CONCEPT_CODE_MAX_LEN:
            raise PreferenceValidationError(field, f"第 {index} 项 code 长度必须是 1~32")
        if len(name) > CONCEPT_NAME_MAX_LEN:
            raise PreferenceValidationError(field, f"第 {index} 项 name 超过 64 字符")
        if code in seen:
            continue
        seen.add(code)
        excluded.append({"code": code, "name": name})
    return excluded


def _merge_jsonb(
    group: str,
    stored: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """JSONB 组：RFC 7396 语义（`null` 删键）；整组重置由调用方传空对象处理。"""
    column = JSONB_COLUMNS[group]
    writable = sorted(JSONB_WRITABLE_KEYS[group])
    _check_keys(group, payload, writable)
    raw = stored.get(column)
    merged = dict(raw) if isinstance(raw, Mapping) else {}
    for key, value in payload.items():
        if value is None:
            merged.pop(key, None)
            continue
        if key == "sessions":
            merged[key] = _validate_sessions(value)
        elif key == "excluded":
            merged[key] = _validate_excluded(value)
        elif key == "mode":
            merged[key] = _validate_enum(f"{group}.mode", value, CONCEPT_MODES)
        elif key in {"theme", "density", "trend"}:
            allowed = {"theme": THEMES, "density": DENSITIES, "trend": TRENDS}[key]
            merged[key] = _validate_enum(f"{group}.{key}", value, allowed)
        elif key == "data_ready_after":
            if not isinstance(value, str):
                raise PreferenceValidationError(f"{group}.{key}", "必须是 HH:MM 字符串")
            try:
                parse_clock_minutes(value)
            except UnitError as exc:
                raise PreferenceValidationError(f"{group}.{key}", str(exc)) from exc
            merged[key] = value
        elif key == "session_notice_enabled":
            if not isinstance(value, bool):
                raise PreferenceValidationError(f"{group}.{key}", "必须是布尔值")
            merged[key] = value
        else:  # pragma: no cover - `_check_keys` 已拦截
            raise PreferenceValidationError(f"{group}.{key}", "未知字段")
    return {key: merged[key] for key in writable if key in merged}


def _apply_appearance(
    stored: Mapping[str, Any], payload: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    old = effective_appearance(stored)
    new_raw = _merge_jsonb(GROUP_APPEARANCE, stored, payload) if payload is not None else {}
    new = effective_appearance({**stored, "display_prefs": new_raw})
    changed = [
        f"{GROUP_APPEARANCE}.{key}" for key in ("theme", "density", "trend") if old[key] != new[key]
    ]
    columns = {"display_prefs": new_raw} if changed else {}
    return columns, {"display_prefs": new_raw}, changed


def _apply_bill_flags(
    stored: Mapping[str, Any], payload: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    old = effective_bill_bps(stored)
    new = dict(old)
    if payload is None:
        for _api_key, (column, default, _, _) in _PCT_DEFAULTS.items():
            new[column] = default
        new[BILL_WINDOW_COLUMN] = BILL_WINDOW_DEFAULT
    else:
        _check_keys(GROUP_BILL_FLAGS, payload, _BILL_WRITABLE)
        for key, value in payload.items():
            if key == BILL_WINDOW_FIELD:
                if value is None:
                    new[BILL_WINDOW_COLUMN] = BILL_WINDOW_DEFAULT
                    continue
                if not isinstance(value, str):
                    raise PreferenceValidationError(
                        f"{GROUP_BILL_FLAGS}.{key}", "必须是十进制整数字符串"
                    )
                try:
                    parsed = parse_window(value)
                except UnitError as exc:
                    raise PreferenceValidationError(f"{GROUP_BILL_FLAGS}.{key}", str(exc)) from exc
                if not BILL_WINDOW_MIN <= parsed <= BILL_WINDOW_MAX:
                    raise PreferenceValidationError(
                        f"{GROUP_BILL_FLAGS}.{key}",
                        f"必须在 {BILL_WINDOW_MIN}~{BILL_WINDOW_MAX} 之间",
                    )
                new[BILL_WINDOW_COLUMN] = parsed
                continue
            column, default, minimum, maximum = _PCT_DEFAULTS[key]
            if value is None:
                new[column] = default
                continue
            if not isinstance(value, str):
                raise PreferenceValidationError(f"{GROUP_BILL_FLAGS}.{key}", "必须是百分比字符串")
            try:
                parsed = parse_pct_to_bps(value)
            except UnitError as exc:
                raise PreferenceValidationError(f"{GROUP_BILL_FLAGS}.{key}", str(exc)) from exc
            if not minimum <= parsed <= maximum:
                raise PreferenceValidationError(
                    f"{GROUP_BILL_FLAGS}.{key}", f"必须在 {minimum}~{maximum} bps 之间"
                )
            new[column] = parsed
    columns: dict[str, Any] = {}
    changed: list[str] = []
    for api_key, (column, _, _, _) in _PCT_DEFAULTS.items():
        if new[column] != old[column]:
            columns[column] = new[column]
            changed.append(f"{GROUP_BILL_FLAGS}.{api_key}")
    if new[BILL_WINDOW_COLUMN] != old[BILL_WINDOW_COLUMN]:
        columns[BILL_WINDOW_COLUMN] = new[BILL_WINDOW_COLUMN]
        changed.append(f"{GROUP_BILL_FLAGS}.{BILL_WINDOW_FIELD}")
    return columns, {**stored, **columns}, changed


def _apply_market_sessions(
    stored: Mapping[str, Any], payload: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    old = effective_market_sessions(stored)
    new_raw = _merge_jsonb(GROUP_MARKET_SESSIONS, stored, payload) if payload is not None else {}
    new = effective_market_sessions({**stored, "market_session_prefs": new_raw})
    if payload is not None:
        last_close = max(parse_clock_minutes(item["close"]) for item in new["sessions"])
        if parse_clock_minutes(str(new["data_ready_after"])) < last_close:
            raise PreferenceValidationError(
                f"{GROUP_MARKET_SESSIONS}.data_ready_after", "必须不早于最后一段收盘时间"
            )
    changed = [
        f"{GROUP_MARKET_SESSIONS}.{key}"
        for key in ("sessions", "data_ready_after", "session_notice_enabled")
        if old[key] != new[key]
    ]
    columns = {"market_session_prefs": new_raw} if changed else {}
    return columns, {"market_session_prefs": new_raw}, changed


def _apply_concept_filters(
    stored: Mapping[str, Any], payload: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    old = effective_concept_filters(stored)
    new_raw = _merge_jsonb(GROUP_CONCEPT_FILTERS, stored, payload) if payload is not None else {}
    new = effective_concept_filters({**stored, "concept_filter_prefs": new_raw})
    changed = [
        f"{GROUP_CONCEPT_FILTERS}.{key}" for key in ("mode", "excluded") if old[key] != new[key]
    ]
    columns = {"concept_filter_prefs": new_raw} if changed else {}
    return columns, {"concept_filter_prefs": new_raw}, changed


def apply_patch(stored: Mapping[str, Any], patch: Mapping[str, Any]) -> PatchResult:
    """合并并校验一次 PATCH；无差异时 `changed=[]`（调用方不 bump 版本）。"""
    if not patch:
        raise PreferenceValidationError("body", "没有可修改的字段")
    readonly = sorted(set(patch) & TOP_LEVEL_READONLY)
    if readonly:
        raise PreferenceValidationError(readonly[0], "只读字段不可修改")
    unknown = sorted(set(patch) - set(GROUPS))
    if unknown:
        raise PreferenceValidationError(unknown[0], "未知字段")

    columns: dict[str, Any] = {}
    merged_stored: dict[str, Any] = dict(stored)
    changed: list[str] = []
    handlers = (
        (GROUP_APPEARANCE, _apply_appearance),
        (GROUP_BILL_FLAGS, _apply_bill_flags),
        (GROUP_MARKET_SESSIONS, _apply_market_sessions),
        (GROUP_CONCEPT_FILTERS, _apply_concept_filters),
    )
    for group, handler in handlers:
        if group not in patch:
            continue
        group_patch = patch[group]
        if group_patch is not None:
            group_patch = _require_mapping(group_patch, group)
        group_columns, group_stored, group_changed = handler(stored, group_patch)
        columns.update(group_columns)
        merged_stored.update(group_stored)
        changed.extend(group_changed)

    if changed:
        merged_stored.update(columns)
    return PatchResult(stored=merged_stored, columns=columns, changed=changed)


__all__ = ["PatchResult", "PreferenceValidationError", "apply_patch"]
