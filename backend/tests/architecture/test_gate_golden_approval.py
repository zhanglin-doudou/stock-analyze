"""gate_golden_approval.py 元测试（§8.11 #8：门禁脚本自带元测试）。

覆盖：已审批通过 / 未审批 / reviewer 缺失 / sha256 不一致 / version 不一致 /
fixture 未登记 / golden 覆盖不全。全部走临时目录，不依赖真实 golden。
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


def _case(tmp_path: Path, **overrides: Any) -> tuple[Path, Path, Path]:
    tests_root = tmp_path / "tests"
    module = tests_root / "support" / "fx_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("VALUE = 1\n", encoding="utf-8")
    sha = hashlib.sha256(module.read_bytes()).hexdigest()

    golden_dir = tmp_path / "golden" / "perf.win_rate"
    golden_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "metric_id": METRIC,
        "definition_version": "1.0",
        "fixture": {"id": FIXTURE},
        "review": dict(REVIEW),
    }
    payload.update(overrides.get("payload", {}))
    (golden_dir / "golden.json").write_text(json.dumps(payload), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "fixtures": {
            FIXTURE: {
                "module": "support/fx_x.py",
                "sha256": overrides.get("sha", sha),
            }
        },
        "definition_versions": overrides.get("versions", {METRIC: "1.0"}),
    }
    manifest_path = tmp_path / "golden" / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return golden_dir.parent, manifest_path, tests_root


def _run(golden_dir: Path, manifest: Path, tests_root: Path) -> int:
    return gate.main(
        [
            "--golden-dir",
            str(golden_dir),
            "--manifest",
            str(manifest),
            "--tests-root",
            str(tests_root),
        ]
    )


def test_pass_when_approved_and_consistent(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path)) == 0


def test_fail_when_unapproved(tmp_path: Path) -> None:
    review = {**REVIEW, "approved": False, "reviewer": None, "approved_at": None}
    assert _run(*_case(tmp_path, payload={"review": review})) == 1


def test_fail_when_reviewer_missing(tmp_path: Path) -> None:
    review = {**REVIEW, "reviewer": None}
    assert _run(*_case(tmp_path, payload={"review": review})) == 1


def test_fail_when_fixture_sha_mismatch(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, sha="0" * 64)) == 1


def test_fail_when_version_mismatch(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, payload={"definition_version": "2.0"})) == 1


def test_fail_when_fixture_not_registered(tmp_path: Path) -> None:
    assert _run(*_case(tmp_path, payload={"fixture": {"id": "fx_missing"}})) == 1


def test_fail_when_coverage_is_incomplete(tmp_path: Path) -> None:
    versions = {METRIC: "1.0", "perf.pnl_ratio": "1.0"}
    assert _run(*_case(tmp_path, versions=versions)) == 1
