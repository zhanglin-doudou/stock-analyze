"""测试用指标 def：基准依赖（blocked 场景）。

仅由测试通过 `build_registry(extra_paths=[...])` 注入；生产 registry 永不包含它
（E-10）。用途：声明 ``DatasetDep("benchmark_series")`` + ``benchmark_required=True``，
当请求的 benchmark 在 replay 夹具中无数据（如 ``zz500_tr``）时，门禁产出
``blocked / BENCHMARK_MISSING`` 且不进入 compute——验证 §8.7「坏指标无 value
而好指标仍 ok」的状态传播。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict
from stock_review.metrics.base import (
    DATASET_WHITELIST,
    DatasetDep,
    DrilldownFilters,
    DrilldownItem,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
)


class BenchmarkStubParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    return ()


class BenchmarkStubDef:
    id = "perf.benchmark_stub"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="day", cohort="all", period="month")
    params_schema = BenchmarkStubParams
    dependencies = (DatasetDep("benchmark_series"),)
    sample_policy = SamplePolicy(min_n=1, ci_min_n=1, ci="none")
    status_policy = ()
    unit = "count"
    precision = 0
    core_conclusion = False
    frequency = "sync"
    benchmark_required = True
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="基准行情可用时，本指标返回窗口内基准交易日条数。",
        conclusion_en="Returns the number of benchmark trading days in the window.",
        method_zh="统计窗口内基准行情的交易日条数。",
        method_en="Counts benchmark bars within the window.",
        what_to_do_zh="确认基准行情已覆盖查询区间后再看。",
        what_to_do_en="Make sure benchmark bars cover the requested window.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/rounds",
        params={"period": "{period}"},
        description_zh="本指标无逐条明细。",
        description_en="This metric has no row-level drilldown.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        bars = tuple(ctx.benchmark_series)
        return MetricValue(value_units=len(bars), n=len(bars), decomposition={"bars": len(bars)})

    def validate_config(self) -> None:
        assert "benchmark_series" in DATASET_WHITELIST
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = BenchmarkStubDef()
