"""单位解析（纯整数域，禁 float）：百分比串 ↔ bps、窗口整数字符串、`HH:MM`。"""

from __future__ import annotations

import re

#: 百分比串：1~2 位整数 + 2 位小数 + `%`（§附录 C V-02）。
_PCT_RE = re.compile(r"^(\d{1,2})\.(\d{2})%$")
#: 窗口串：1~2 位十进制整数（§附录 C V-03）。
_WINDOW_RE = re.compile(r"^\d{1,2}$")
#: `HH:MM`（24 小时制）。
_CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

BPS_DENOMINATOR = 100


class UnitError(ValueError):
    """单位解析失败（由 patch 层转成字段级校验错误）。"""


def parse_pct_to_bps(value: str) -> int:
    """`"1.00%"` → `100`（bps 整数）。"""
    matched = _PCT_RE.match(value)
    if matched is None:
        raise UnitError(f"百分比格式非法：{value!r}（应形如 1.00%）")
    return int(matched.group(1)) * BPS_DENOMINATOR + int(matched.group(2))


def format_bps(bps: int) -> str:
    """`100` → `"1.00%"`。"""
    return f"{bps // BPS_DENOMINATOR}.{bps % BPS_DENOMINATOR:02d}%"


def parse_window(value: str) -> int:
    """`"5"` → `5`（不接受符号/小数点/空白）。"""
    if _WINDOW_RE.match(value) is None:
        raise UnitError(f"窗口必须是十进制整数字符串：{value!r}")
    return int(value)


def format_window(days: int) -> str:
    return str(days)


def parse_clock_minutes(value: str) -> int:
    """`"09:30"` → `570`（分钟）；非法抛 `UnitError`。"""
    matched = _CLOCK_RE.match(value)
    if matched is None:
        raise UnitError(f"时间格式非法：{value!r}（应形如 09:30）")
    return int(matched.group(1)) * 60 + int(matched.group(2))


__all__ = [
    "BPS_DENOMINATOR",
    "UnitError",
    "format_bps",
    "format_window",
    "parse_clock_minutes",
    "parse_pct_to_bps",
    "parse_window",
]
