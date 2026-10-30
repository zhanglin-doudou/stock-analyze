"""指标呈现层单测（P0-3）：方向/区间/可信度/进度/事实-解读-建议。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from stock_review.metrics.engine import MetricOutcome
from stock_review.metrics.presentation import build_presentation
from stock_review.metrics.registry import build_registry

REGISTRY = build_registry()


def _outcome(metric_id: str, **overrides: Any) -> MetricOutcome:
    definition = REGISTRY.require(metric_id)
    values: dict[str, Any] = {
        "metric_id": metric_id,
        "definition_version": definition.definition_version,
        "domain": definition.domain,
        "scope": {"unit": "round", "cohort": "close_date", "period": "month"},
        "period": "2026-09",
        "params": {},
        "params_hash": "a" * 64,
        "inputs_hash": "sha256:" + "b" * 64,
        "status": "ok",
        "sample_status": "ok",
        "reason": None,
        "value_units": 53_333_333,
        "unit": "ratio",
        "precision": 8,
        "n": 15,
        "n_pending": 0,
        "ci_low_units": 30_116_980,
        "ci_high_units": 75_190_465,
        "decomposition": {},
        "assumptions": {},
        "data_as_of": date(2026, 9, 30),
        "computed_at": datetime(2026, 9, 30, 15, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return MetricOutcome(**values)


def _presentation(metric_id: str, **overrides: Any) -> dict[str, Any]:
    return build_presentation(_outcome(metric_id, **overrides), REGISTRY.require(metric_id))


def test_win_rate_ci_crossing_bands_is_neutral_with_fact_meaning_action() -> None:
    p = _presentation("perf.win_rate")
    assert p["display_name"] == "胜率"
    assert p["direction"] == "higher_better"
    assert p["confidence"]["level"] == "high"
    assert p["verdict"]["level"] == "neutral"
    assert "置信区间跨" in p["verdict"]["rationale"]
    assert p["bands"] and {band["level"] for band in p["bands"]} == {"watch", "neutral", "good"}
    assert "0.53333333" in p["interpretation"]["fact"]
    assert "95%CI" in p["interpretation"]["fact"]
    assert p["interpretation"]["meaning"]
    assert p["interpretation"]["action"]


def test_insufficient_sample_shows_progress_and_no_conclusion() -> None:
    p = _presentation("perf.win_rate", sample_status="insufficient", n=9)
    assert p["confidence"]["level"] == "low"
    assert p["verdict"]["level"] == "unknown"
    assert p["progress"]["current"] == 9
    assert p["progress"]["required"] == 10
    assert p["progress"]["remaining"] == 1
    assert "还不" in p["interpretation"]["meaning"] or "原始值" in p["interpretation"]["meaning"]


def test_pending_window_uses_trading_days_progress() -> None:
    p = _presentation(
        "timing.drawdown",
        sample_status="pending_window",
        value_units=None,
        ci_low_units=None,
        ci_high_units=None,
        n=38,
        decomposition={"n_trading_days": 38},
    )
    assert p["window"]["kind"] == "trailing_60td"
    assert p["progress"]["unit"] == "trading_days"
    assert p["progress"]["required"] == 60
    assert p["progress"]["remaining"] == 22
    assert p["verdict"]["level"] == "unknown"
    assert "窗口" in p["caveats"][0]


def test_blocked_is_unavailable_and_human_reason() -> None:
    p = _presentation(
        "selection.bhar_20",
        status="blocked",
        sample_status="blocked",
        reason="DATASET_UNAVAILABLE:bars_daily",
        value_units=None,
        ci_low_units=None,
        ci_high_units=None,
        n=0,
    )
    assert p["confidence"]["level"] == "unavailable"
    assert p["verdict"]["level"] == "unavailable"
    assert "行情" in p["confidence"]["reason"] or "数据" in p["confidence"]["reason"]
    assert p["progress"] is None


def test_display_names_for_renamed_metrics() -> None:
    assert _presentation("perf.concentration")["display_name"] == "盈利集中度"
    assert _presentation("behavior.hhi")["display_name"] == "持仓集中度"
