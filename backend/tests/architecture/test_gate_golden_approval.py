"""gate_golden_approval.py 元测试（§8.11 #8：门禁脚本自带元测试）。

覆盖：schema 严格性 / 已审批通过 / 未审批 / reviewer 缺失 / 自签 / sha256 不一致 /
version 不一致 / fixture 未登记 / golden 内容 hash / 结构模式 / 覆盖矩阵缺失与断言。
全部走临时目录，不依赖真实 golden。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "gate_golden_approval.py"

_spec = importlib.util.spec_from_file_location("gate_golden_approval", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

METRIC = "perf.win_rate"
FIXTURE = "fx_x"
REVIEW: dict[str, Any] = {
    "author": "test-designer",
    "reviewer": "alice",
    "approved": True,
    "approved_at": "2026-10-27T00:00:00Z",
}


def _valid_payload(*, review: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "metric_id": METRIC,
        "definition_version": "1.0",
        "fixture": {"id": FIXTURE},
        "period": "2026-09",
        "scope": "round",
        "params": {},
        "expected": {
            "value": "0.50000000",
            "unit": "ratio",
            "n": 10,
            "ci_low": None,
            "ci_high": None,
            "sample_status": "ok",
            "status": "ok",
            "decomposition": {},
        },
        "tolerance": {"kind": "ratio_bp", "eps": "0.0001"},
        "expected_source": {"kind": "hand_calc", "ref": "unit-test hand calc"},
        "review": dict(REVIEW if review is None else review),
    }


def _case(
    tmp_path: Path,
    *,
    payload: dict[str, Any] | None = None,
    sha: str | None = None,
    versions: dict[str, str] | None = None,
    coverage: dict[str, Any] | None = None,
    golden_sha: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    tests_root = tmp_path / "tests"
    module = tests_root / "support" / "fx_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("VALUE = 1\n", encoding="utf-8")
    module_sha = hashlib.sha256(module.read_bytes()).hexdigest()

    golden_dir = tmp_path / "golden" / "perf.win_rate"
    golden_dir.mkdir(parents=True)
    golden_path = golden_dir / "golden.json"
    body = _valid_payload()
    if payload:
        body.update(payload)
    golden_path.write_text(json.dumps(body), encoding="utf-8")
    actual_golden_sha = hashlib.sha256(golden_path.read_bytes()).hexdigest()

    manifest = {
        "schema_version": 1,
        "fixtures": {FIXTURE: {"module": "support/fx_x.py", "sha256": sha or module_sha}},
        "definition_versions": versions or {METRIC: "1.0"},
        "goldens": {"perf.win_rate/golden.json": golden_sha or actual_golden_sha},
    }
    manifest_path = tmp_path / "golden" / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    coverage_payload = coverage or {
        "schema_version": 1,
        "required": [
            {
                "id": "perf.win_rate.base",
                "metric_id": METRIC,
                "file": "perf.win_rate/golden.json",
                "reason": "unit test",
            }
        ],
    }
    coverage_path = tmp_path / "golden" / "COVERAGE.json"
    coverage_path.write_text(json.dumps(coverage_payload), encoding="utf-8")
    return golden_dir.parent, manifest_path, tests_root, coverage_path


def _run(
    golden_dir: Path,
    manifest: Path,
    tests_root: Path,
    coverage: Path,
    *,
    mode: str = "approval",
) -> int:
    return gate.main(
        [
            "--mode",
            mode,
            "--golden-dir",
            str(golden_dir),
            "--manifest",
            str(manifest),
            "--tests-root",
            str(tests_root),
            "--coverage",
            str(coverage),
        ]
    )


def test_pass_when_approved_and_consistent(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path)) == 0


def test_structure_mode_ignores_approval_and_coverage(tmp_path: Path) -> None:
    review = {**REVIEW, "approved": False, "reviewer": None, "approved_at": None}
    case = _case(tmp_path, payload={"review": review})
    missing = tmp_path / "missing-coverage.json"
    assert _run(case[0], case[1], case[2], missing, mode="structure") == 0


def test_fail_when_unapproved(tmp_path: Path) -> None:
    review = {**REVIEW, "approved": False, "reviewer": None, "approved_at": None}
    assert _run(*_case(tmp_path, payload={"review": review})) == 1


def test_fail_when_reviewer_missing(tmp_path: Path) -> None:
    review = {**REVIEW, "reviewer": None}
    assert _run(*_case(tmp_path, payload={"review": review})) == 1


def test_fail_when_self_approved(tmp_path: Path) -> None:
    review = {**REVIEW, "reviewer": REVIEW["author"]}
    assert _run(*_case(tmp_path, payload={"review": review})) == 1


def test_fail_when_fixture_sha_mismatch(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, sha="0" * 64)) == 1


def test_fail_when_golden_content_hash_mismatch(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, golden_sha="0" * 64)) == 1


def test_fail_when_version_mismatch(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, payload={"definition_version": "2.0"})) == 1


def test_fail_when_fixture_not_registered(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, payload={"fixture": {"id": "fx_missing"}})) == 1


def test_fail_when_unknown_key(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, payload={"expected_unused": 1})) == 1


def test_fail_when_pending_window_has_value(tmp_path: Path) -> None:
    expected = {
        "value": "0.50000000",
        "unit": "ratio",
        "n": 10,
        "ci_low": None,
        "ci_high": None,
        "sample_status": "pending_window",
        "status": "ok",
        "decomposition": {},
    }
    assert _run(*_case(tmp_path, payload={"expected": expected})) == 1


def test_fail_when_invalid_source_kind(tmp_path: Path) -> None:
    assert (
        _run(*_case(tmp_path, payload={"expected_source": {"kind": "production", "ref": "x"}})) == 1
    )


def test_fail_when_coverage_is_incomplete(tmp_path: Path) -> None:
    versions = {METRIC: "1.0", "perf.pnl_ratio": "1.0"}
    assert _run(*_case(tmp_path, versions=versions)) == 1


def test_fail_when_coverage_file_missing(tmp_path: Path) -> None:
    case = _case(tmp_path)
    missing = tmp_path / "missing-coverage.json"
    assert _run(case[0], case[1], case[2], missing) == 1


def test_fail_when_coverage_status_expectation_mismatch(tmp_path: Path) -> None:
    coverage = {
        "schema_version": 1,
        "required": [
            {
                "id": "perf.win_rate.base",
                "metric_id": METRIC,
                "file": "perf.win_rate/golden.json",
                "reason": "unit test",
                "expect_status": "blocked",
            }
        ],
    }
    assert _run(*_case(tmp_path, coverage=coverage)) == 1
