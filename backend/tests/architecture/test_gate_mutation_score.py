"""gate_mutation_score.py 元测试（Period2-07 §8.9：门禁脚本自带元测试）。

覆盖：达标 / 低于 --min / 环比退化超阈与未超阈 / 基线缺失 / stats 缺失 /
分母为 0 / --update-baseline 写基线。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "gate_mutation_score.py"

_spec = importlib.util.spec_from_file_location("gate_mutation_score", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _stats(tmp_path: Path, *, killed: int, survived: int, timeout: int = 0) -> Path:
    path = tmp_path / "stats.json"
    path.write_text(
        json.dumps({"killed": killed, "survived": survived, "timeout": timeout}),
        encoding="utf-8",
    )
    return path


def _baseline(tmp_path: Path, score: float) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"score": score}), encoding="utf-8")
    return path


def test_pass_when_score_above_min_and_no_baseline(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=1663, survived=39, timeout=2)
    rc = gate.main(["--stats", str(stats), "--baseline", str(tmp_path / "nope.json")])
    assert rc == 0


def test_fail_when_below_min(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=70, survived=30)
    rc = gate.main(["--stats", str(stats), "--baseline", str(tmp_path / "nope.json")])
    assert rc == 1


def test_timeout_counts_against_score(tmp_path: Path) -> None:
    # 1663/(1663+39+2)=97.59%：timeout 进分母；若只算 survived 会更高
    stats = _stats(tmp_path, killed=80, survived=10, timeout=10)
    rc = gate.main(["--stats", str(stats), "--min", "81", "--baseline", str(tmp_path / "x.json")])
    assert rc == 1  # 80/100=80% < 81%


def test_regression_drop_over_threshold_fails(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=94, survived=6)  # 94.00%
    baseline = _baseline(tmp_path, 96.5)  # 降 2.5pp > 2pp
    rc = gate.main(["--stats", str(stats), "--baseline", str(baseline)])
    assert rc == 1


def test_regression_within_threshold_passes(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=95, survived=5)  # 95.00%
    baseline = _baseline(tmp_path, 96.5)  # 降 1.5pp ≤ 2pp
    rc = gate.main(["--stats", str(stats), "--baseline", str(baseline)])
    assert rc == 0


def test_missing_stats_file_exits_2(tmp_path: Path) -> None:
    rc = gate.main(["--stats", str(tmp_path / "missing.json")])
    assert rc == 2


def test_zero_evaluated_mutants_exits_2(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=0, survived=0)
    rc = gate.main(["--stats", str(stats), "--baseline", str(tmp_path / "x.json")])
    assert rc == 2


def test_update_baseline_writes_score(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=1663, survived=39, timeout=2)
    baseline = tmp_path / "baseline.json"
    rc = gate.main(["--stats", str(stats), "--baseline", str(baseline), "--update-baseline"])
    assert rc == 0
    written = json.loads(baseline.read_text(encoding="utf-8"))["score"]
    assert written == pytest.approx(1663 / 1704 * 100, abs=1e-3)
