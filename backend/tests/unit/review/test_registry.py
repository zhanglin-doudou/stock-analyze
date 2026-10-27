"""指标注册表单测（Period2-05）：发现、校验、DAG、测试 def 注入隔离。"""

from __future__ import annotations

from pathlib import Path

import pytest
from stock_review.metrics.errors import CycleError, RegistryError
from stock_review.metrics.registry import build_registry

SUPPORT_DEFS = Path(__file__).resolve().parents[2] / "support" / "metric_defs"

_TEMPLATE = """
from pydantic import BaseModel, ConfigDict

from stock_review.metrics.base import (
    DatasetDep,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricDep,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
)


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _items(ctx, filters):
    return ()


class DEF:
    id = {id!r}
    definition_version = {version!r}
    domain = {domain!r}
    scope = ScopeSpec(period={scope_period!r})
    params_schema = {params_schema}
    dependencies = {dependencies}
    sample_policy = SamplePolicy()
    status_policy = ()
    unit = {unit!r}
    precision = {precision}
    core_conclusion = {core!r}
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(conclusion_zh="c", conclusion_en="c", method_zh="m", method_en="m")
    drilldown = DrilldownSpec(
        endpoint="/api/v1/rounds",
        params={{"period": "{{period}}"}},
        description_zh="d",
        description_en="d",
        build_items=_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        return MetricValue(value_units=0, n=0)

    def validate_config(self) -> None:
        return None
"""


def _write(
    tmp_path: Path,
    filename: str,
    *,
    metric_id: str = "perf.ok",
    version: str = "1.0",
    domain: str = "perf",
    scope_period: str = "month",
    params_schema: str = "Params",
    dependencies: str = "()",
    unit: str = "ratio",
    precision: int = 8,
    core: bool = False,
) -> Path:
    source = _TEMPLATE.format(
        id=metric_id,
        version=version,
        domain=domain,
        scope_period=scope_period,
        params_schema=params_schema,
        dependencies=dependencies,
        unit=unit,
        precision=precision,
        core=core,
    )
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    return path


def test_support_defs_are_injected_but_not_in_production() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    assert registry.ids() == ("perf.expectancy_stub", "perf.win_rate_stub")
    assert registry.get("perf.win_rate_stub") is not None
    assert registry.get("perf.missing") is None
    assert registry.require("perf.win_rate_stub").domain == "perf"
    with pytest.raises(KeyError):
        registry.require("perf.missing")

    assert len(registry) == 2
    assert "perf.win_rate_stub" in registry
    assert len(registry.version) == 64
    assert [item.id for item in registry.topological()] == list(registry.ids())
    assert {item.id for item in registry.by_domain("all")} == set(registry.ids())
    assert {item.id for item in registry.by_domain("behavior")} == set()

    production = build_registry()
    assert len(production) == 0


def test_deep_chain_and_cycle(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.a", dependencies="(MetricDep('perf.b'),)")
    _write(tmp_path, "b.py", metric_id="perf.b", dependencies="(MetricDep('perf.c'),)")
    _write(tmp_path, "c.py", metric_id="perf.c", dependencies="(MetricDep('perf.d'),)")
    _write(tmp_path, "d.py", metric_id="perf.d", dependencies="(MetricDep('perf.e'),)")
    _write(tmp_path, "e.py", metric_id="perf.e")

    with pytest.raises(RegistryError, match="深度"):
        build_registry(extra_paths=[tmp_path])


def test_cycle_detection(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.a", dependencies="(MetricDep('perf.b'),)")
    _write(tmp_path, "b.py", metric_id="perf.b", dependencies="(MetricDep('perf.a'),)")

    with pytest.raises(CycleError):
        build_registry(extra_paths=[tmp_path])


def test_duplicate_and_missing_def(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.dup")
    _write(tmp_path, "b.py", metric_id="perf.dup")
    with pytest.raises(RegistryError, match="重复"):
        build_registry(extra_paths=[tmp_path])


def test_invalid_fields(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf-bad")
    with pytest.raises(RegistryError, match="非法 metric id"):
        build_registry(extra_paths=[tmp_path])


def test_domain_prefix_mismatch(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.x", domain="timing")
    with pytest.raises(RegistryError, match="domain"):
        build_registry(extra_paths=[tmp_path])


def test_bad_version_scope_unit_and_schema(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.x", version="v1")
    with pytest.raises(RegistryError, match="definition_version"):
        build_registry(extra_paths=[tmp_path])

    (tmp_path / "a.py").unlink()
    _write(tmp_path, "b.py", metric_id="perf.x", scope_period="week")
    with pytest.raises(RegistryError, match="period=month"):
        build_registry(extra_paths=[tmp_path])

    (tmp_path / "b.py").unlink()
    _write(tmp_path, "c.py", metric_id="perf.x", unit="z", precision=4)
    assert build_registry(extra_paths=[tmp_path]).ids() == ("perf.x",)
    (tmp_path / "c.py").unlink()


def test_bad_dataset_and_unknown_upstream(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.x", dependencies="(DatasetDep('nope'),)")
    with pytest.raises(RegistryError, match="白名单"):
        build_registry(extra_paths=[tmp_path])

    (tmp_path / "a.py").unlink()
    _write(tmp_path, "b.py", metric_id="perf.x", dependencies="(MetricDep('perf.nope'),)")
    with pytest.raises(RegistryError, match="未注册"):
        build_registry(extra_paths=[tmp_path])


def test_non_forbid_schema_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", metric_id="perf.x", params_schema="BaseModel")
    with pytest.raises(RegistryError, match="extra='forbid'"):
        build_registry(extra_paths=[tmp_path])


def test_missing_def_export_and_missing_path(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="DEF"):
        build_registry(extra_paths=[tmp_path])

    with pytest.raises(RegistryError, match="不存在"):
        build_registry(extra_paths=[tmp_path / "nope"])
