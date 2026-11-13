"""偏好指纹（纯函数）：`flags_hash` 与 `concept_filter_hash`。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

_FLAGS_KEYS: tuple[tuple[str, str], ...] = (
    ("chase_margin_bps", "bill_chase_margin_bps"),
    ("near_equal_tolerance_bps", "bill_near_equal_tolerance_bps"),
    ("short_term_margin_bps", "bill_short_term_margin_bps"),
    ("stop_loss_margin_bps", "bill_stop_loss_margin_bps"),
    ("window_trading_days", "bill_window_trading_days"),
)


def canonical_json(payload: object) -> str:
    """规范 JSON（排序键、紧凑分隔符、保留非 ASCII）。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def flags_hash(values: Mapping[str, int]) -> str:
    """`{4 个 bps + window}` 规范指纹（16 hex），只在生效值上计算。

    `values` 的键是 DB 列名（`bill_*`）；规范 JSON 用去前缀的短键，与
    `stock_contracts.preferences.DEFAULT_FLAGS_HASH` 对齐。
    """
    return _digest({short: int(values[column]) for short, column in _FLAGS_KEYS})


def concept_filter_hash(mode: str, excluded_codes: Iterable[str]) -> str:
    """概念过滤指纹；`off` 返回空串（不进 `inputs_hash`/ETag）。"""
    if mode == "off":
        return ""
    return _digest({"mode": mode, "excluded": sorted(set(excluded_codes))})


__all__ = ["canonical_json", "concept_filter_hash", "flags_hash"]
