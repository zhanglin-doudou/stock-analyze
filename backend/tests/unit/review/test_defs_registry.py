"""18 个生产 def 的注册表形状断言（Period2-07 S4）。"""

from __future__ import annotations

from pathlib import Path

from stock_review.metrics.registry import build_registry

from support.engine_kit import assert_compute_is_pure

DEFS_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "review"
    / "src"
    / "stock_review"
    / "metrics"
    / "defs"
)

EXPECTED_IDS = (
    "behavior.averaging_down",
    "behavior.de",
    "behavior.hhi",
    "behavior.turnover",
    "perf.concentration",
    "perf.expectancy",
    "perf.fee",
    "perf.fee_breakeven",
    "perf.holding_days",
    "perf.pnl_ratio",
    "perf.win_rate",
    "selection.bhar_20",
    "selection.bhar_60",
    "selection.bhar_hit",
    "timing.beta",
    "timing.chase",
    "timing.drawdown",
    "timing.fly",
)

EXPECTED_CORE = {
    "behavior.de",
    "perf.win_rate",
    "perf.pnl_ratio",
    "perf.fee",
    "perf.fee_breakeven",
    "timing.chase",
    "timing.beta",
    "timing.drawdown",
    "selection.bhar_20",
    "selection.bhar_60",
}


def test_production_registry_discovers_18_defs() -> None:
    registry = build_registry()
    assert registry.ids() == EXPECTED_IDS
    assert len(registry) == 18


def test_core_conclusion_budget_is_10() -> None:
    registry = build_registry()
    core_ids = {item.id for item in registry.topological() if item.core_conclusion}
    assert core_ids == EXPECTED_CORE
    assert len(core_ids) == 10
    p0_ids = {item.id for item in registry.topological() if item.mvp == "P0"}
    assert p0_ids == core_ids


def test_domain_distribution() -> None:
    registry = build_registry()
    assert len(registry.by_domain("perf")) == 7
    assert len(registry.by_domain("timing")) == 4
    assert len(registry.by_domain("behavior")) == 4
    assert len(registry.by_domain("selection")) == 3
    assert {item.id for item in registry.by_domain("all")} == set(EXPECTED_IDS)


def test_topological_order_respects_metric_dependency() -> None:
    registry = build_registry()
    order = [item.id for item in registry.topological()]
    assert order.index("selection.bhar_20") < order.index("selection.bhar_hit")


def test_frequency_benchmark_and_trading_day_flags() -> None:
    registry = build_registry()
    async_ids = {item.id for item in registry.topological() if item.frequency == "async"}
    assert async_ids == {"selection.bhar_20", "selection.bhar_60", "selection.bhar_hit"}
    bench_ids = {item.id for item in registry.topological() if item.benchmark_required}
    assert bench_ids == {
        "timing.fly",
        "timing.beta",
        "selection.bhar_20",
        "selection.bhar_60",
        "selection.bhar_hit",
    }
    window_ids = {
        item.id for item in registry.topological() if item.sample_policy.min_trading_days == 60
    }
    assert window_ids == {"timing.beta", "timing.drawdown"}


def test_validate_config_passes_for_all_defs() -> None:
    registry = build_registry()
    for definition in registry.topological():
        definition.validate_config()
        assert definition.params_schema.model_config.get("extra") == "forbid"


def test_all_def_modules_are_pure() -> None:
    for path in sorted(DEFS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        assert_compute_is_pure(path)
