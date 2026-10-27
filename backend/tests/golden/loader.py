"""golden 用例加载器（§8.2）：`glob("golden/**/golden*.json")` 参数化。

格式约定（与 §8.2 示例的唯一偏差：`decomposition` 值一律为**标度整数的字符串**而非
展示字符串——避免逐 key 单位映射，防 float 中转的目的不变）：
- `expected.value` / `ci_low` / `ci_high`：展示字符串（如 "0.66666667"、"1042.16"）或 null，
  由测试侧按 `definition.precision` 折算回标度整数；
- `expected.decomposition`：key → 标度整数字符串（"-75"）、计数 int、非整数文本（如
  `benchmark_id`）或 null；
- 解析全程拒绝 float（`parse_float` 不走浮点）。

本加载器是 golden 的**唯一 schema 入口**（G8 审批脚本也复用它）：
- 顶层/expected/fixture/review/expected_source/tolerance 的未知 key 一律拒绝；
- 必填键缺失、枚举非法、状态-字段矩阵矛盾（如 pending_window 带 value、非 ok 带 CI、
  CI 不夹住 value）一律 `GoldenFormatError`；
- 可选字段 `expected.n_pending` / `expected.assumptions` / `scenario` / `coverage` 供骨架
  用例逐步补齐，存在时由测试断言。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

GOLDEN_ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1

_METRIC_ID_RE = re.compile(r"^(selection|timing|behavior|attribution|perf)\.[a-z0-9_]+$")
_VERSION_RE = re.compile(r"^\d+\.\d+$")
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(\.\.\d{4}-(0[1-9]|1[0-2]))?$")
_UNITS = frozenset({"CNY", "ratio", "days", "count", "z"})
_STATUSES = frozenset({"ok", "stale", "blocked", "unreliable"})
_SAMPLE_STATUSES = frozenset({"ok", "insufficient", "pending_window", "blocked"})
_SOURCE_KINDS = frozenset({"hand_calc", "oracle", "hand_calc+oracle"})
_TOLERANCE_KINDS = frozenset({"exact", "ratio_bp", "cny", "days", "z", "fee"})

_ALLOWED_TOP = frozenset(
    {
        "schema_version",
        "metric_id",
        "definition_version",
        "fixture",
        "period",
        "scope",
        "params",
        "expected",
        "tolerance",
        "expected_source",
        "review",
        "boundaries",
        "scenario",
        "coverage",
    }
)
_ALLOWED_FIXTURE = frozenset({"id", "flags"})
_ALLOWED_EXPECTED = frozenset(
    {
        "value",
        "unit",
        "n",
        "n_pending",
        "ci_low",
        "ci_high",
        "sample_status",
        "status",
        "decomposition",
        "assumptions",
    }
)
_ALLOWED_REVIEW = frozenset({"author", "reviewer", "approved", "approved_at", "note"})
_ALLOWED_SOURCE = frozenset({"kind", "ref", "oracle", "note"})
_ALLOWED_TOLERANCE = frozenset({"kind", "eps", "rel"})
_ALLOWED_FIXTURE_FLAGS = frozenset(
    {
        # 引擎/门禁 fixture flags（新增 flag 必须同时登记到 loader 与设计文档）
        "data_out_of_range",
        "dataset_missing:bars_daily",
        "dataset_missing:benchmark_series",
        "asset_build_missing",
        "asset_build_blocked",
        "asset_build_stale",
        "recon_blocked",
        "market_asof_lag",
        "bar_density_low",
        "degraded_rows",
    }
)


class GoldenFormatError(ValueError):
    """golden JSON 结构非法（缺键/类型错/float 混入/状态矩阵矛盾）。"""


def _reject_float(value: str) -> None:
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


def _parse_decomposition(raw: object, *, path: str) -> dict[str, int | str | None]:
    """decomposition 值：整数字符串 → 标度整数；非整数文本 → 原样字符串（如 benchmark_id）。"""
    if not isinstance(raw, dict):
        raise GoldenFormatError(f"{path}: decomposition 必须是对象")
    parsed: dict[str, int | str | None] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise GoldenFormatError(f"{path}: decomposition key 必须是非空字符串")
        if value is None:
            parsed[key] = None
        elif isinstance(value, bool):
            raise GoldenFormatError(f"{path}.{key}: 禁止 bool")
        elif isinstance(value, int):
            parsed[key] = value
        elif isinstance(value, str):
            try:
                parsed[key] = int(value)
            except ValueError:
                parsed[key] = value
        else:
            raise GoldenFormatError(f"{path}.{key}: 只接受整数字符串/文本/int/null，实际 {value!r}")
    return parsed


def _require(raw: Mapping[str, object], key: str, *, path: Path) -> object:
    if key not in raw:
        raise GoldenFormatError(f"{path}: 缺键 {key!r}")
    return raw[key]


def _reject_unknown(
    raw: Mapping[str, object], allowed: frozenset[str], *, path: Path, where: str
) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise GoldenFormatError(f"{path}: {where} 含未知键 {unknown}（允许：{sorted(allowed)}）")


def _as_mapping(raw: object, *, path: Path, where: str) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise GoldenFormatError(f"{path}: {where} 必须是对象")
    return raw


def _parse_iso_datetime(text: str, *, path: Path) -> None:
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise GoldenFormatError(f"{path}: approved_at 必须是 ISO8601，实际 {text!r}") from exc


def _parse_decimal_text(raw: object, *, path: Path, where: str) -> None:
    if not isinstance(raw, str):
        raise GoldenFormatError(f"{path}: {where} 必须是字符串，实际 {raw!r}")
    try:
        Decimal(raw)
    except InvalidOperation as exc:
        raise GoldenFormatError(f"{path}: {where} 非法小数 {raw!r}") from exc


def _parse_review(raw: object, *, path: Path) -> dict[str, object]:
    review = _as_mapping(raw, path=path, where="review")
    _reject_unknown(review, _ALLOWED_REVIEW, path=path, where="review")
    author = review.get("author")
    reviewer = review.get("reviewer")
    approved = review.get("approved")
    approved_at = review.get("approved_at")
    if not isinstance(author, str) or not author.strip():
        raise GoldenFormatError(f"{path}: review.author 必须是非空字符串")
    if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
        raise GoldenFormatError(f"{path}: review.reviewer 必须是字符串或 null")
    if not isinstance(approved, bool):
        raise GoldenFormatError(f"{path}: review.approved 必须是 bool")
    if approved_at is not None:
        if not isinstance(approved_at, str) or not approved_at.strip():
            raise GoldenFormatError(f"{path}: review.approved_at 必须是 ISO8601 字符串或 null")
        _parse_iso_datetime(approved_at, path=path)
    if approved and (not reviewer or not approved_at):
        raise GoldenFormatError(f"{path}: approved=true 时 reviewer/approved_at 必须非空")
    if approved and reviewer == author:
        raise GoldenFormatError(f"{path}: reviewer 不能等于 author（禁止自签）")
    return dict(review)


def _parse_expected_source(raw: object, *, path: Path) -> dict[str, object]:
    source = _as_mapping(raw, path=path, where="expected_source")
    _reject_unknown(source, _ALLOWED_SOURCE, path=path, where="expected_source")
    kind = source.get("kind")
    ref = source.get("ref")
    oracle = source.get("oracle")
    if kind not in _SOURCE_KINDS:
        raise GoldenFormatError(
            f"{path}: expected_source.kind 必须是 {sorted(_SOURCE_KINDS)}，实际 {kind!r}"
        )
    if not isinstance(ref, str) or not ref.strip():
        raise GoldenFormatError(f"{path}: expected_source.ref 必须是非空字符串")
    if oracle is not None and (not isinstance(oracle, str) or not oracle.strip()):
        raise GoldenFormatError(f"{path}: expected_source.oracle 必须是字符串或 null")
    return dict(source)


def _parse_tolerance(raw: object, *, path: Path) -> dict[str, str]:
    tolerance = _as_mapping(raw, path=path, where="tolerance")
    _reject_unknown(tolerance, _ALLOWED_TOLERANCE, path=path, where="tolerance")
    kind = tolerance.get("kind")
    if kind not in _TOLERANCE_KINDS:
        raise GoldenFormatError(
            f"{path}: tolerance.kind 必须是 {sorted(_TOLERANCE_KINDS)}，实际 {kind!r}"
        )
    eps = tolerance.get("eps")
    if not isinstance(eps, str) or not eps:
        raise GoldenFormatError(f"{path}: tolerance.eps 必须是非空字符串")
    _parse_decimal_text(eps, path=path, where="tolerance.eps")
    if kind == "exact" and Decimal(eps) != 0:
        raise GoldenFormatError(f"{path}: tolerance.kind=exact 时 eps 必须为 0")
    rel = tolerance.get("rel")
    if rel is not None:
        _parse_decimal_text(rel, path=path, where="tolerance.rel")
    if kind == "fee" and rel is None:
        raise GoldenFormatError(f"{path}: tolerance.kind=fee 必须给 rel（相对容差）")
    parsed = {"kind": str(kind), "eps": eps}
    if rel is not None:
        parsed["rel"] = str(rel)
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
    decomposition: dict[str, int | str | None]
    tolerance: dict[str, str]
    expected_source: dict[str, object]
    review: dict[str, object]
    boundaries: tuple[str, ...]
    n_pending: int | None = None
    assumptions: dict[str, object] | None = None
    scenario: str | None = None
    coverage: tuple[str, ...] = ()

    def value_units(self, precision: int) -> int | None:
        return _parse_display(self.value_text, precision, path=str(self.path))

    def ci_low_units(self, precision: int) -> int | None:
        return _parse_display(self.ci_low_text, precision, path=str(self.path))

    def ci_high_units(self, precision: int) -> int | None:
        return _parse_display(self.ci_high_text, precision, path=str(self.path))


def _parse_expected(raw: object, *, path: Path) -> dict[str, Any]:
    expected = _as_mapping(raw, path=path, where="expected")
    _reject_unknown(expected, _ALLOWED_EXPECTED, path=path, where="expected")
    required = (
        "value",
        "unit",
        "n",
        "ci_low",
        "ci_high",
        "sample_status",
        "status",
        "decomposition",
    )
    for key in required:
        _require(expected, key, path=path)

    value = expected["value"]
    if value is not None and not isinstance(value, str):
        raise GoldenFormatError(f"{path}: expected.value 必须是字符串或 null")
    unit = expected["unit"]
    if unit not in _UNITS:
        raise GoldenFormatError(f"{path}: expected.unit 必须是 {sorted(_UNITS)}，实际 {unit!r}")
    n = expected["n"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise GoldenFormatError(f"{path}: expected.n 必须是非负 int")
    n_pending = expected.get("n_pending")
    if n_pending is not None and (
        not isinstance(n_pending, int) or isinstance(n_pending, bool) or n_pending < 0
    ):
        raise GoldenFormatError(f"{path}: expected.n_pending 必须是非负 int 或省略")
    status = expected["status"]
    sample_status = expected["sample_status"]
    if status not in _STATUSES:
        raise GoldenFormatError(
            f"{path}: expected.status 必须是 {sorted(_STATUSES)}，实际 {status!r}"
        )
    if sample_status not in _SAMPLE_STATUSES:
        raise GoldenFormatError(
            f"{path}: expected.sample_status 必须是 {sorted(_SAMPLE_STATUSES)}，实际 {sample_status!r}"
        )
    ci_low = expected["ci_low"]
    ci_high = expected["ci_high"]
    for name, ci_value in (("ci_low", ci_low), ("ci_high", ci_high)):
        if ci_value is not None and not isinstance(ci_value, str):
            raise GoldenFormatError(f"{path}: expected.{name} 必须是字符串或 null")

    # 状态-字段矩阵（§4.2）：非 ok / pending_window 不得有 value/CI；
    # insufficient 不得有 CI；CI 必须成对且夹住 value；ok+ok 必须有 value。
    if status != "ok" and value is not None:
        raise GoldenFormatError(f"{path}: status={status} 时 expected.value 必须为 null")
    if status != "ok" and (ci_low is not None or ci_high is not None):
        raise GoldenFormatError(f"{path}: status={status} 时 expected.ci_* 必须为 null")
    if sample_status == "pending_window" and value is not None:
        raise GoldenFormatError(
            f"{path}: sample_status=pending_window 时 expected.value 必须为 null"
        )
    if sample_status == "insufficient" and (ci_low is not None or ci_high is not None):
        raise GoldenFormatError(f"{path}: sample_status=insufficient 时 expected.ci_* 必须为 null")
    if status == "ok" and sample_status == "ok" and value is None:
        raise GoldenFormatError(
            f"{path}: status=ok 且 sample_status=ok 时 expected.value 不得为 null"
        )
    if (ci_low is None) != (ci_high is None):
        raise GoldenFormatError(f"{path}: expected.ci_low/ci_high 必须同时为 null 或同时非 null")
    if ci_low is not None and ci_high is not None:
        if status != "ok" or sample_status != "ok":
            raise GoldenFormatError(f"{path}: 只有 status=ok 且 sample_status=ok 才允许 CI")
        if Decimal(str(ci_low)) > Decimal(str(ci_high)):
            raise GoldenFormatError(f"{path}: ci_low > ci_high")
        if value is not None and not (
            Decimal(str(ci_low)) <= Decimal(str(value)) <= Decimal(str(ci_high))
        ):
            raise GoldenFormatError(f"{path}: expected.value 未落在 [ci_low, ci_high]")

    assumptions = expected.get("assumptions")
    if assumptions is not None:
        if not isinstance(assumptions, dict):
            raise GoldenFormatError(f"{path}: expected.assumptions 必须是对象")
        for key, val in assumptions.items():
            if not isinstance(key, str) or not key:
                raise GoldenFormatError(f"{path}: expected.assumptions key 必须是非空字符串")
            if isinstance(val, float):
                raise GoldenFormatError(f"{path}: expected.assumptions.{key} 禁止 float")
            if not isinstance(val, (str, int, bool)) and val is not None:
                raise GoldenFormatError(
                    f"{path}: expected.assumptions.{key} 只接受 str/int/bool/null"
                )

    return {
        "value_text": value,
        "unit": unit,
        "n": n,
        "n_pending": n_pending,
        "ci_low_text": ci_low,
        "ci_high_text": ci_high,
        "sample_status": sample_status,
        "status": status,
        "decomposition": _parse_decomposition(
            _require(expected, "decomposition", path=path), path=str(path)
        ),
        "assumptions": dict(assumptions) if assumptions is not None else None,
    }


def load_case(path: Path) -> GoldenCase:
    raw = json.loads(path.read_text(encoding="utf-8"), parse_float=_reject_float)
    if not isinstance(raw, dict):
        raise GoldenFormatError(f"{path}: 顶层必须是对象")
    _reject_unknown(raw, _ALLOWED_TOP, path=path, where="顶层")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise GoldenFormatError(f"{path}: schema_version 必须是 {SCHEMA_VERSION}")

    metric_id = _require(raw, "metric_id", path=path)
    if not isinstance(metric_id, str) or not _METRIC_ID_RE.match(metric_id):
        raise GoldenFormatError(f"{path}: metric_id 非法 {metric_id!r}")
    definition_version = _require(raw, "definition_version", path=path)
    if not isinstance(definition_version, str) or not _VERSION_RE.match(definition_version):
        raise GoldenFormatError(f"{path}: definition_version 非法 {definition_version!r}")
    period = _require(raw, "period", path=path)
    if not isinstance(period, str) or not _PERIOD_RE.match(period):
        raise GoldenFormatError(
            f"{path}: period 必须是 YYYY-MM 或 YYYY-MM..YYYY-MM，实际 {period!r}"
        )

    fixture = _as_mapping(_require(raw, "fixture", path=path), path=path, where="fixture")
    _reject_unknown(fixture, _ALLOWED_FIXTURE, path=path, where="fixture")
    fixture_id = fixture.get("id")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise GoldenFormatError(f"{path}: fixture.id 缺失")
    flags = fixture.get("flags") or {}
    if not isinstance(flags, dict) or not all(isinstance(value, bool) for value in flags.values()):
        raise GoldenFormatError(f"{path}: fixture.flags 必须是 bool 映射")
    unknown_flags = sorted(set(flags) - _ALLOWED_FIXTURE_FLAGS)
    if unknown_flags:
        raise GoldenFormatError(f"{path}: fixture.flags 未登记 {unknown_flags}")

    scope = raw.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise GoldenFormatError(f"{path}: scope 必须是非空字符串")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise GoldenFormatError(f"{path}: params 必须是对象")

    expected = _parse_expected(_require(raw, "expected", path=path), path=path)
    tolerance = _parse_tolerance(_require(raw, "tolerance", path=path), path=path)
    expected_source = _parse_expected_source(_require(raw, "expected_source", path=path), path=path)
    review = _parse_review(_require(raw, "review", path=path), path=path)

    boundaries = raw.get("boundaries", [])
    if not isinstance(boundaries, list) or not all(isinstance(item, str) for item in boundaries):
        raise GoldenFormatError(f"{path}: boundaries 必须是字符串数组")
    scenario = raw.get("scenario")
    if scenario is not None and (not isinstance(scenario, str) or not scenario.strip()):
        raise GoldenFormatError(f"{path}: scenario 必须是非空字符串或省略")
    coverage = raw.get("coverage", [])
    if not isinstance(coverage, list) or not all(
        isinstance(item, str) and item for item in coverage
    ):
        raise GoldenFormatError(f"{path}: coverage 必须是字符串数组")

    return GoldenCase(
        path=path,
        metric_id=metric_id,
        definition_version=definition_version,
        fixture_id=fixture_id,
        fixture_flags=dict(flags),
        period=period,
        scope=scope,
        params=dict(params),
        unit=expected["unit"],
        value_text=expected["value_text"],
        n=expected["n"],
        ci_low_text=expected["ci_low_text"],
        ci_high_text=expected["ci_high_text"],
        sample_status=expected["sample_status"],
        status=expected["status"],
        decomposition=expected["decomposition"],
        tolerance=tolerance,
        expected_source=expected_source,
        review=review,
        boundaries=tuple(boundaries),
        n_pending=expected["n_pending"],
        assumptions=expected["assumptions"],
        scenario=scenario,
        coverage=tuple(coverage),
    )


def load_all(root: Path | None = None) -> tuple[GoldenCase, ...]:
    """按路径排序加载全部 golden；空集合视为错误（防 glob 失效静默绿）。"""
    base = root or GOLDEN_ROOT
    paths = sorted(base.glob("**/golden*.json"))
    if not paths:
        raise GoldenFormatError(f"{base}: 未发现任何 golden*.json")
    cases = tuple(load_case(path) for path in paths)
    seen: dict[tuple[str, str, str, str, str], Path] = {}
    for case in cases:
        identity = (
            case.metric_id,
            case.period,
            case.fixture_id,
            json.dumps(case.params, sort_keys=True),
            json.dumps(case.fixture_flags, sort_keys=True),
        )
        previous = seen.get(identity)
        if previous is not None:
            raise GoldenFormatError(
                f"{base}: 重复 golden 用例 {case.metric_id}/{case.period}/{case.fixture_id}："
                f"{previous.name} 与 {case.path.name}"
            )
        seen[identity] = case.path
    return cases


__all__ = ["GOLDEN_ROOT", "GoldenCase", "GoldenFormatError", "load_all", "load_case"]
