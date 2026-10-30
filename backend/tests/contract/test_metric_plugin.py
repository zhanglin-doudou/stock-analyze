"""L2a 契约 kit：18 个 MetricDef 的形状/文案/下钻/样本门槛（§8.6、§8.11 #3）。

只依赖 registry 暴露的 DefSpec 形状，不启动 DB、不计算指标值；数值口径由
`golden/test_metrics_three_way.py` 与 `oracle/` 负责。失败信息统一带
`metric_id@definition_version`，便于定位到具体插件。
"""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel
from stock_contracts.constants import MIN_CI_SAMPLE, MIN_CONCLUSION_SAMPLE
from stock_review.metrics.base import (
    MAX_DEP_DEPTH,
    MetricDef,
    MetricValue,
    assert_no_status_leak,
)
from stock_review.metrics.registry import build_registry

REGISTRY = build_registry()
DEFS = REGISTRY.all()
BY_ID = {definition.id: definition for definition in DEFS}

# §8.6 assert_explain_no_jargon：只扫结论/建议（method 允许最多 1 个括注术语）。
JARGON = re.compile(r"\b(DE|BHAR|PGR|PLR|TWR|MWR|FIFO|HHI|Wilson|IR)\b|[βα]", re.IGNORECASE)
CORE_TOTAL_RANGE = range(8, 13)
VALID_UNITS = {"CNY", "ratio", "days", "count", "z"}


def label(definition: MetricDef) -> str:
    return f"{definition.id}@{definition.definition_version}"


def test_registry_partition_18_core_10_exploratory_8() -> None:
    """D-06：07a 核心 10（mvp=P0）+ 07b exploratory 8（mvp=M1）。"""
    assert len(DEFS) == 18
    core = [d for d in DEFS if d.mvp == "P0"]
    exploratory = [d for d in DEFS if d.mvp == "M1"]
    assert len(core) == 10
    assert len(exploratory) == 8
    assert {d.id for d in core} == {d.id for d in DEFS if d.core_conclusion}
    assert len(core) in CORE_TOTAL_RANGE


@pytest.mark.parametrize("definition", DEFS, ids=[d.id for d in DEFS])
def test_required_fields_and_versions(definition: MetricDef) -> None:
    """13 字段齐全；id 前缀与 domain 一致；version 1.x；unit/precision 匹配。"""
    tag = label(definition)
    for name in (
        "id",
        "definition_version",
        "domain",
        "scope",
        "params_schema",
        "dependencies",
        "sample_policy",
        "status_policy",
        "unit",
        "precision",
        "core_conclusion",
        "frequency",
        "benchmark_required",
        "mvp",
        "explain",
        "drilldown",
    ):
        assert hasattr(definition, name), f"{tag}: 缺少字段 {name}"

    assert definition.id.startswith(f"{definition.domain}."), tag
    assert re.fullmatch(r"1\.\d+", definition.definition_version), tag
    assert definition.unit in VALID_UNITS, tag
    assert definition.frequency in {"sync", "async"}, tag
    assert definition.mvp in {"P0", "M1", "M2"}, tag
    assert definition.scope.period == "month", f"{tag}: M1 只支持 period=month（E-07）"
    assert definition.precision >= 0, tag
    if definition.unit == "count":
        assert definition.precision == 0, f"{tag}: count 必须整数"
    definition.validate_config()


@pytest.mark.parametrize("definition", DEFS, ids=[d.id for d in DEFS])
def test_params_schema_is_strict_and_deps_are_bounded(definition: MetricDef) -> None:
    """params_schema 必须 Pydantic + extra=forbid；依赖存在、深度 ≤3、无环。"""
    tag = label(definition)
    schema = definition.params_schema
    assert isinstance(schema, type) and issubclass(schema, BaseModel), tag
    assert schema.model_config.get("extra") == "forbid", f"{tag}: params_schema 必须 extra=forbid"

    for dependency in definition.dependencies:
        metric_id = getattr(dependency, "metric_id", None)
        if metric_id is not None:
            assert metric_id in BY_ID, f"{tag}: 依赖 {metric_id} 不存在"
            assert metric_id != definition.id, f"{tag}: 自依赖"
        else:
            assert getattr(dependency, "name", None), f"{tag}: 数据集依赖缺 name"

    def depth(metric_id: str, seen: tuple[str, ...] = ()) -> int:
        assert metric_id not in seen, f"{tag}: 依赖环 {(*seen, metric_id)}"
        deps = [
            dep.metric_id
            for dep in BY_ID[metric_id].dependencies
            if getattr(dep, "metric_id", None) is not None
        ]
        return 1 + max((depth(dep, (*seen, metric_id)) for dep in deps), default=0)

    assert depth(definition.id) <= MAX_DEP_DEPTH, f"{tag}: 依赖深度超过 {MAX_DEP_DEPTH}"


@pytest.mark.parametrize("definition", DEFS, ids=[d.id for d in DEFS])
def test_sample_policy_uses_shared_constants(definition: MetricDef) -> None:
    """D-08：阈值来自 contracts.constants（禁魔数 10/30/60）；ci 种类合法。"""
    tag = label(definition)
    policy = definition.sample_policy
    assert policy.ci in {"wilson", "bootstrap", "none"}, tag
    assert policy.ci_min_n >= MIN_CI_SAMPLE, tag
    assert policy.min_n >= MIN_CONCLUSION_SAMPLE, tag
    if policy.ci != "none":
        assert policy.ci_min_n >= MIN_CONCLUSION_SAMPLE, tag


@pytest.mark.parametrize("definition", DEFS, ids=[d.id for d in DEFS])
def test_explain_is_bilingual_and_jargon_free(definition: MetricDef) -> None:
    """中英三字段非空；conclusion/what_to_do 不含 §8.6 裸术语。"""
    tag = label(definition)
    explain = definition.explain
    for name in ("conclusion_zh", "conclusion_en", "method_zh", "method_en"):
        assert getattr(explain, name).strip(), f"{tag}: explain.{name} 为空"
    if explain.what_to_do_zh is None:
        assert explain.what_to_do_en is None, f"{tag}: what_to_do 中英必须成对"
    else:
        assert explain.what_to_do_en, f"{tag}: what_to_do 中英必须成对"

    for name in ("conclusion_zh", "conclusion_en", "what_to_do_zh", "what_to_do_en"):
        text = getattr(explain, name)
        if text is None:
            continue
        matched = JARGON.search(text)
        assert matched is None, f"{tag}: explain.{name} 含裸术语 {matched.group(0)!r}: {text}"


@pytest.mark.parametrize("definition", DEFS, ids=[d.id for d in DEFS])
def test_drilldown_endpoint_and_builder(definition: MetricDef) -> None:
    """下钻 endpoint 指向自身；params 至少含 period；build_items 可调用。"""
    tag = label(definition)
    drilldown = definition.drilldown
    assert drilldown.endpoint == f"/api/v1/metrics/{definition.id}/drilldown", tag
    assert "period" in drilldown.params, tag
    assert all(isinstance(value, str) for value in drilldown.params.values()), tag
    assert callable(drilldown.build_items), tag
    assert drilldown.description_zh.strip() and drilldown.description_en.strip(), tag


def test_metric_value_has_no_status_leak() -> None:
    """`assert_no_status_leak`：compute 只能返回 6 个内存字段。"""
    assert_no_status_leak(MetricValue(value_units=None, n=0))
