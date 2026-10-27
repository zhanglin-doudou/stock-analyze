"""loader schema 严格性测试（G8 强化）：坏的 golden 必须在加载期报错，不进入参数化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from golden import loader

pytestmark = pytest.mark.golden


def _valid_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "metric_id": "perf.win_rate",
        "definition_version": "1.0",
        "fixture": {"id": "fx_x"},
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
        "expected_source": {"kind": "hand_calc", "ref": "hand calc"},
        "review": {
            "author": "test-designer",
            "reviewer": None,
            "approved": False,
            "approved_at": None,
        },
    }


def _write(tmp_path: Path, payload: dict[str, Any], name: str = "golden.json") -> Path:
    path = tmp_path / "perf.win_rate" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_case_loads(tmp_path: Path) -> None:
    case = loader.load_case(_write(tmp_path, _valid_payload()))
    assert case.metric_id == "perf.win_rate"
    assert case.n == 10


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["unexpected"] = 1
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_unknown_expected_key_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["expected"]["ci_lo"] = "0.1"
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_missing_review_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    del payload["review"]
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_pending_window_with_value_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["expected"]["sample_status"] = "pending_window"
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_non_ok_with_ci_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["expected"].update(status="unreliable", ci_low="0.1", ci_high="0.2", value=None)
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_ci_must_contain_value(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["expected"].update(value="0.90000000", ci_low="0.10000000", ci_high="0.20000000")
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_self_approval_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["review"].update(
        approved=True, reviewer="test-designer", approved_at="2026-10-27T00:00:00Z"
    )
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_invalid_source_kind_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["expected_source"] = {"kind": "production", "ref": "x"}
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_unknown_fixture_flag_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["fixture"] = {"id": "fx_x", "flags": {"typo_flag": True}}
    with pytest.raises(loader.GoldenFormatError):
        loader.load_case(_write(tmp_path, payload))


def test_duplicate_case_identity_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    _write(tmp_path, payload, name="golden.json")
    _write(tmp_path, payload, name="golden.dup.json")
    with pytest.raises(loader.GoldenFormatError):
        loader.load_all(tmp_path)
