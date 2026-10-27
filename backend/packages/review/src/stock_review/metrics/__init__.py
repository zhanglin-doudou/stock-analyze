"""指标引擎包（Period2-05）。

子模块职责：
- `base`：冻结的形状（MetricDef/MetricValue/ScopeSpec…）；
- `registry`：自动发现 + 编译期校验；
- `views` / `datasets`：8 个冻结视图 + 装配 + 指纹；
- `gate`：blocked > stale > unreliable > ok 状态机；
- `engine`：拓扑执行 + 缓存命中 + 框架代算；
- `estimators`：CI estimator 派发；
- `persist`：ok 结果的物化与血缘；
- `hash`：canonical JSON / 指纹 / seed；
- `context`：period 解析与 MetricContext 实现；
- `defs`：生产指标 def（07 交付，05 为空）。
"""

from __future__ import annotations

from stock_review.metrics.base import (
    CI_SAMPLE_MAX,
    DATASET_WHITELIST,
    MAX_DEP_DEPTH,
    DatasetDep,
    DrilldownFilters,
    DrilldownItem,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricDef,
    MetricDep,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
    StatusRule,
    assert_no_status_leak,
    format_units,
)
from stock_review.metrics.errors import CycleError, RegistryError
from stock_review.metrics.registry import MetricRegistry, build_registry

__all__ = [
    "CI_SAMPLE_MAX",
    "DATASET_WHITELIST",
    "MAX_DEP_DEPTH",
    "CycleError",
    "DatasetDep",
    "DrilldownFilters",
    "DrilldownItem",
    "DrilldownSpec",
    "ExplainSpec",
    "MetricContext",
    "MetricDef",
    "MetricDep",
    "MetricRegistry",
    "MetricValue",
    "RegistryError",
    "SamplePolicy",
    "ScopeSpec",
    "StatusRule",
    "assert_no_status_leak",
    "build_registry",
    "format_units",
]
