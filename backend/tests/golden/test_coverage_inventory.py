"""覆盖清单一致性（G8 骨架）：COVERAGE.json 的每个 required 场景要么已有 golden，
要么有 `_skeleton/*.json.tmpl` 骨架；且清单不引用未注册指标。"""

from __future__ import annotations

import json

import pytest
from stock_review.metrics.registry import build_registry

from golden.loader import GOLDEN_ROOT, load_all, load_case

pytestmark = pytest.mark.golden

COVERAGE_PATH = GOLDEN_ROOT / "COVERAGE.json"
SKELETON_ROOT = GOLDEN_ROOT / "_skeleton"


def _coverage() -> dict[str, object]:
    return json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))


def _required() -> list[dict[str, object]]:
    return list(_coverage()["required"])  # type: ignore[arg-type]


def test_coverage_inventory_schema() -> None:
    payload = _coverage()
    assert payload["schema_version"] == 1
    required = _required()
    ids = [str(item["id"]) for item in required]
    assert len(ids) == len(set(ids)), "COVERAGE scenario id 重复"


def test_coverage_metrics_registered() -> None:
    registry_ids = set(build_registry().ids())
    for item in _required():
        metric_id = str(item["metric_id"])
        assert metric_id in registry_ids, f"COVERAGE 引用了未注册指标 {metric_id}"


def test_required_scenarios_are_covered_or_skeletoned() -> None:
    existing = {case.path.relative_to(GOLDEN_ROOT).as_posix() for case in load_all()}
    for item in _required():
        rel = str(item["file"])
        if rel in existing:
            continue
        template = SKELETON_ROOT / f"{rel}.tmpl"
        assert template.exists(), (
            f"COVERAGE 场景 {item['id']} 缺 golden 且无骨架模板：{rel} / {template}"
        )
        skeleton = json.loads(template.read_text(encoding="utf-8"))
        assert skeleton["_skeleton"] is True
        assert skeleton["target"] == rel
        assert skeleton["metric_id"] == item["metric_id"]


def test_every_existing_golden_is_declared() -> None:
    declared = {str(item["file"]) for item in _required()}
    for case in load_all():
        rel = case.path.relative_to(GOLDEN_ROOT).as_posix()
        assert rel in declared, f"golden {rel} 未登记到 COVERAGE.json"


def test_coverage_expected_fields_are_well_typed() -> None:
    for item in _required():
        for key in ("expect_status", "expect_sample_status", "expect_value_present"):
            if key not in item:
                continue
            value = item[key]
            if key == "expect_value_present":
                assert isinstance(value, bool)
            else:
                assert isinstance(value, str) and value
        if "expect_status" in item:
            assert item["expect_status"] in {"ok", "stale", "blocked", "unreliable"}
        if "expect_sample_status" in item:
            assert item["expect_sample_status"] in {
                "ok",
                "insufficient",
                "pending_window",
                "blocked",
            }


def test_existing_golden_cases_match_declared_metric() -> None:
    by_rel = {case.path.relative_to(GOLDEN_ROOT).as_posix(): case for case in load_all()}
    for item in _required():
        case = by_rel.get(str(item["file"]))
        if case is None:
            continue
        assert case.metric_id == item["metric_id"]
        assert load_case(case.path).metric_id == item["metric_id"]
