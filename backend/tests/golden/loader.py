"""golden 用例加载器（§8.2）：`glob("golden/**/golden*.json")` 参数化。

格式约定（与 §8.2 示例的唯一偏差：`decomposition` 值一律为**标度整数的字符串**而非
展示字符串——避免逐 key 单位映射，防 float 中转的目的不变）：
- `expected.value` / `ci_low` / `ci_high`：展示字符串（如 "0.66666667"、"1042.16"）或 null，
  由测试侧按 `definition.precision` 折算回标度整数；
- `expected.decomposition`：key → 标度整数字符串（"-75"）、计数 int、或 null；
- 解析全程拒绝 float（`parse_float`/`parse_int` 不走浮点）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

GOLDEN_ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1


class GoldenFormatError(ValueError):
    """golden JSON 结构非法（缺键/类型错/float 混入）。"""


def _reject_float(value: float) -> None:
    raise GoldenFormatError(f"golden JSON 禁止 float: {value!r}")


def _parse_display(text: str | None, precision: int, *, path: str) -> int | None:
    """展示字符串 → 标度整数（Decimal 精确折算）。"""
    if text is None:
        return None
    if not isinstance(text, str):
        raise GoldenFormatError(f"{path}: value 必须是字符串或 null，实际 {text!r}")
    try:
        scaled = Decimal(text) * (Decimal(10) ** precision)
    except InvalidOperation as exc:
        raise GoldenFormatError(f"{path}: 非法数值 {text!r}") from exc
    if scaled != scaled.to_integral_value():
        raise GoldenFormatError(f"{path}: {text!r} 超出 precision={precision} 的精度")
    return int(scaled)


def _parse_decomposition(raw: object, *, path: str) -> dict[str, int | None]:
    if not isinstance(raw, dict):
        raise GoldenFormatError(f"{path}: decomposition 必须是对象")
    parsed: dict[str, int | None] = {}
    for key, value in raw.items():
        if value is None:
            parsed[key] = None
        elif isinstance(value, bool):
            raise GoldenFormatError(f"{path}.{key}: 禁止 bool")
        elif isinstance(value, int):
            parsed[key] = value
        elif isinstance(value, str):
            try:
                parsed[key] = int(value)
            except ValueError as exc:
                raise GoldenFormatError(f"{path}.{key}: 非法整数字符串 {value!r}") from exc
        else:
            raise GoldenFormatError(f"{path}.{key}: 只接受整数字符串/int/null，实际 {value!r}")
    return parsed


@dataclass(frozen=True, slots=True)
class GoldenCase:
    path: Path
    metric_id: str
    definition_version: str
    fixture_id: str
    fixture_flags: dict[str, bool]
    period: str
    scope: str
    params: dict[str, object]
    unit: str
    value_text: str | None
    n: int
    ci_low_text: str | None
    ci_high_text: str | None
    sample_status: str
    status: str
    decomposition: dict[str, int | None]
    tolerance: dict[str, str]
    expected_source: dict[str, object]
    boundaries: tuple[str, ...]

    def value_units(self, precision: int) -> int | None:
        return _parse_display(self.value_text, precision, path=str(self.path))

    def ci_low_units(self, precision: int) -> int | None:
        return _parse_display(self.ci_low_text, precision, path=str(self.path))

    def ci_high_units(self, precision: int) -> int | None:
        return _parse_display(self.ci_high_text, precision, path=str(self.path))


def _require(raw: dict[str, object], key: str, *, path: Path) -> object:
    if key not in raw:
        raise GoldenFormatError(f"{path}: 缺键 {key!r}")
    return raw[key]


def load_case(path: Path) -> GoldenCase:
    raw = json.loads(path.read_text(encoding="utf-8"), parse_float=_reject_float)
    if not isinstance(raw, dict):
        raise GoldenFormatError(f"{path}: 顶层必须是对象")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise GoldenFormatError(f"{path}: schema_version 必须是 {SCHEMA_VERSION}")
    expected = _require(raw, "expected", path=path)
    if not isinstance(expected, dict):
        raise GoldenFormatError(f"{path}: expected 必须是对象")
    fixture = _require(raw, "fixture", path=path)
    if not isinstance(fixture, dict) or not isinstance(fixture.get("id"), str):
        raise GoldenFormatError(f"{path}: fixture.id 缺失")
    flags = fixture.get("flags") or {}
    if not isinstance(flags, dict) or not all(isinstance(v, bool) for v in flags.values()):
        raise GoldenFormatError(f"{path}: fixture.flags 必须是 bool 映射")
    n = expected.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        raise GoldenFormatError(f"{path}: expected.n 必须是 int")
    boundaries = raw.get("boundaries", [])
    return GoldenCase(
        path=path,
        metric_id=str(_require(raw, "metric_id", path=path)),
        definition_version=str(_require(raw, "definition_version", path=path)),
        fixture_id=fixture["id"],
        fixture_flags=dict(flags),
        period=str(_require(raw, "period", path=path)),
        scope=str(raw.get("scope", "")),
        params=dict(raw.get("params") or {}),
        unit=str(expected.get("unit", "")),
        value_text=expected.get("value"),
        n=n,
        ci_low_text=expected.get("ci_low"),
        ci_high_text=expected.get("ci_high"),
        sample_status=str(expected.get("sample_status", "")),
        status=str(expected.get("status", "")),
        decomposition=_parse_decomposition(
            _require(expected, "decomposition", path=path), path=str(path)
        ),
        tolerance=dict(raw.get("tolerance") or {"kind": "exact", "eps": "0"}),
        expected_source=dict(raw.get("expected_source") or {}),
        boundaries=tuple(str(item) for item in boundaries),
    )


def load_all(root: Path | None = None) -> tuple[GoldenCase, ...]:
    """按路径排序加载全部 golden；空集合视为错误（防 glob 失效静默绿）。"""
    base = root or GOLDEN_ROOT
    paths = sorted(base.glob("**/golden*.json"))
    if not paths:
        raise GoldenFormatError(f"{base}: 未发现任何 golden*.json")
    return tuple(load_case(path) for path in paths)


__all__ = ["GOLDEN_ROOT", "GoldenCase", "GoldenFormatError", "load_all", "load_case"]
