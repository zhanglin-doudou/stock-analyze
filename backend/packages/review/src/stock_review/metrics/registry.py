"""指标注册表：自动发现 + 编译期校验（Period2-05，E-10）。

发现规则：
- 生产只扫 `stock_review.metrics.defs`（放文件即注册；07 的 def 全在这里）；
- 测试通过 `build_registry(extra_paths=[...])` 注入 `backend/tests/support/metric_defs/`
  下的 stub，生产 registry 永远不含 stub（E-10）。

校验（fail-fast，请求期不再兜底）：
- 13+4 字段齐全；id/version 正则；`id` 前缀 = domain；
- `scope.period` 必须是 month（E-07，M1）；
- `params_schema` 必须 `extra="forbid"` 的 Pydantic 模型；
- DatasetDep 必须在白名单；MetricDep 必须已注册；
- deps 深度 ≤3、无环；unit/precision 与 UNIT_PRECISION 一致；
- `core_conclusion=True` 的生产 def 总数 ∈ [8,12]（生产为空时跳过，07 冻结时收敛）。
"""

from __future__ import annotations

import importlib.util
import pkgutil
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel

from stock_review.metrics.base import (
    DATASET_WHITELIST,
    DEFINITION_VERSION_PATTERN,
    MAX_DEP_DEPTH,
    METRIC_ID_PATTERN,
    UNIT_PRECISION,
    DatasetDep,
    MetricDef,
    MetricDep,
    ScopeSpec,
    StatusRule,
)
from stock_review.metrics.errors import CycleError, RegistryError

PRODUCTION_PACKAGE = "stock_review.metrics.defs"
REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "definition_version",
    "domain",
    "scope",
    "params_schema",
    "dependencies",
    "compute",
    "explain",
    "drilldown",
    "sample_policy",
    "status_policy",
    "unit",
    "precision",
    "core_conclusion",
    "frequency",
    "benchmark_required",
    "mvp",
)
DOMAINS: frozenset[str] = frozenset({"perf", "timing", "behavior", "selection", "attribution"})
STATUS_KINDS: frozenset[str] = frozenset({"blocked", "stale", "unreliable"})
FREQUENCIES: frozenset[str] = frozenset({"sync", "async"})
MVP_VALUES: frozenset[str] = frozenset({"P0", "M1", "M2"})
CI_KINDS: frozenset[str] = frozenset({"wilson", "bootstrap", "none"})
CORE_CONCLUSION_RANGE = (8, 12)


class MetricRegistry:
    """不可变注册表；按 metric_id 升序返回（`ids` 请求顺序由 API 层决定）。"""

    __slots__ = ("_defs", "_order")

    def __init__(self, defs: Mapping[str, MetricDef], order: Sequence[str]) -> None:
        self._defs = dict(defs)
        self._order = tuple(order)

    def get(self, metric_id: str) -> MetricDef | None:
        return self._defs.get(metric_id)

    def require(self, metric_id: str) -> MetricDef:
        definition = self._defs.get(metric_id)
        if definition is None:
            raise KeyError(metric_id)
        return definition

    def all(self) -> tuple[MetricDef, ...]:
        return tuple(self._defs[key] for key in sorted(self._defs))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._defs))

    def by_domain(self, domain: str | None) -> tuple[MetricDef, ...]:
        if domain is None or domain == "all":
            return self.all()
        return tuple(item for item in self.all() if item.domain == domain)

    def topological(self) -> tuple[MetricDef, ...]:
        """依赖在前的执行顺序（Kahn/DFS 结果，含所有 def）。"""
        return tuple(self._defs[key] for key in self._order)

    @property
    def version(self) -> str:
        """registry 内容版本（id + definition_version），进 ETag 指纹。"""
        from stock_review.metrics.hash import sha256_hex

        payload = "|".join(
            f"{key}:{self._defs[key].definition_version}" for key in sorted(self._defs)
        )
        return sha256_hex(payload)

    def __len__(self) -> int:
        return len(self._defs)

    def __contains__(self, metric_id: object) -> bool:
        return metric_id in self._defs


def _load_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件系统异常
        raise RegistryError(f"无法加载 def 模块: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _discover_production() -> list[tuple[ModuleType, bool]]:
    package = importlib.import_module(PRODUCTION_PACKAGE)
    found: list[tuple[ModuleType, bool]] = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_") or info.ispkg:
            continue
        module = importlib.import_module(f"{PRODUCTION_PACKAGE}.{info.name}")
        found.append((module, False))
    return found


def _discover_extra(paths: Sequence[str | Path]) -> list[tuple[ModuleType, bool]]:
    found: list[tuple[ModuleType, bool]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            raise RegistryError(f"测试 def 路径不存在: {path}")
        for index, file_path in enumerate(sorted(path.glob("*.py"))):
            if file_path.name.startswith("_"):
                continue
            module_name = f"_metric_def_extra_{path.name}_{file_path.stem}_{index}"
            found.append((_load_module(file_path, module_name), True))
    return found


def _module_def(module: ModuleType) -> MetricDef:
    definition = getattr(module, "DEF", None)
    if definition is None:
        raise RegistryError(f"{module.__name__} 必须导出模块级实例 DEF（Extensibility §3.5）")
    return definition  # type: ignore[no-any-return]


def _validate_shape(definition: MetricDef, *, module_name: str) -> None:
    missing = [name for name in REQUIRED_FIELDS if not hasattr(definition, name)]
    if missing:
        raise RegistryError(f"{module_name}: 缺字段 {missing}")
    if not callable(getattr(definition, "compute", None)):
        raise RegistryError(f"{module_name}: compute 必须是可调用对象")

    metric_id = definition.id
    if not isinstance(metric_id, str) or not METRIC_ID_PATTERN.match(metric_id):
        raise RegistryError(f"{module_name}: 非法 metric id {metric_id!r}")
    if not isinstance(definition.definition_version, str) or not DEFINITION_VERSION_PATTERN.match(
        definition.definition_version
    ):
        raise RegistryError(
            f"{module_name}: 非法 definition_version {definition.definition_version!r}"
        )
    if metric_id.split(".", 1)[0] != definition.domain:
        raise RegistryError(
            f"{module_name}: id 前缀必须与 domain 一致（{metric_id} / {definition.domain}）"
        )
    if definition.domain not in DOMAINS:
        raise RegistryError(f"{module_name}: 非法 domain {definition.domain!r}")

    scope = definition.scope
    if not isinstance(scope, ScopeSpec):
        raise RegistryError(f"{module_name}: scope 必须是 ScopeSpec")
    if scope.period != "month":
        raise RegistryError(f"{module_name}: M1 只支持 period=month，实际 {scope.period!r}")

    schema = definition.params_schema
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise RegistryError(f"{module_name}: params_schema 必须是 Pydantic BaseModel 子类")
    if (getattr(schema, "model_config", None) or {}).get("extra") != "forbid":
        raise RegistryError(f"{module_name}: params_schema 必须 extra='forbid'")

    if not isinstance(definition.dependencies, tuple):
        raise RegistryError(f"{module_name}: dependencies 必须是 tuple")
    for dependency in definition.dependencies:
        if isinstance(dependency, DatasetDep):
            if dependency.name not in DATASET_WHITELIST:
                raise RegistryError(f"{module_name}: dataset 不在白名单: {dependency.name}")
        elif isinstance(dependency, MetricDep):
            if not METRIC_ID_PATTERN.match(dependency.metric_id):
                raise RegistryError(f"{module_name}: 非法上游 metric id {dependency.metric_id!r}")
        else:
            raise RegistryError(f"{module_name}: 非法依赖类型 {type(dependency).__name__}")

    policy = definition.sample_policy
    if policy.ci not in CI_KINDS:
        raise RegistryError(f"{module_name}: 非法 ci 策略 {policy.ci!r}")
    if policy.min_n < 0 or policy.ci_min_n < 0 or policy.min_trading_days < 0:
        raise RegistryError(f"{module_name}: sample_policy 计数不能为负")

    if not isinstance(definition.status_policy, tuple):
        raise RegistryError(f"{module_name}: status_policy 必须是 tuple")
    for rule in definition.status_policy:
        if not isinstance(rule, StatusRule) or rule.kind not in STATUS_KINDS or not rule.code:
            raise RegistryError(f"{module_name}: 非法 status_policy 规则 {rule!r}")

    if definition.unit not in UNIT_PRECISION:
        raise RegistryError(f"{module_name}: 非法 unit {definition.unit!r}")
    if not isinstance(definition.precision, int) or isinstance(definition.precision, bool):
        raise RegistryError(f"{module_name}: precision 必须是 int")
    if definition.precision != UNIT_PRECISION[definition.unit]:
        raise RegistryError(
            f"{module_name}: precision({definition.precision}) 必须与 unit({definition.unit}) 口径一致"
            f"（{UNIT_PRECISION[definition.unit]}）"
        )

    if not isinstance(definition.core_conclusion, bool):
        raise RegistryError(f"{module_name}: core_conclusion 必须是 bool")
    if definition.frequency not in FREQUENCIES:
        raise RegistryError(f"{module_name}: 非法 frequency {definition.frequency!r}")
    if not isinstance(definition.benchmark_required, bool):
        raise RegistryError(f"{module_name}: benchmark_required 必须是 bool")
    if definition.mvp not in MVP_VALUES:
        raise RegistryError(f"{module_name}: 非法 mvp {definition.mvp!r}")


def _topological_order(defs: Mapping[str, MetricDef]) -> tuple[str, ...]:
    """DFS 后序：依赖先于消费方；同时做环检测与深度校验。"""
    state: dict[str, int] = {}
    order: list[str] = []
    depth: dict[str, int] = {}

    def visit(metric_id: str, stack: tuple[str, ...]) -> int:
        marker = state.get(metric_id, 0)
        if marker == 2:
            return depth[metric_id]
        if marker == 1:
            cycle = " -> ".join((*stack, metric_id))
            raise CycleError(f"指标依赖成环: {cycle}")
        state[metric_id] = 1
        current_depth = 0
        for dependency in defs[metric_id].dependencies:
            if isinstance(dependency, MetricDep):
                current_depth = max(
                    current_depth, 1 + visit(dependency.metric_id, (*stack, metric_id))
                )
        state[metric_id] = 2
        depth[metric_id] = current_depth
        order.append(metric_id)
        return current_depth

    for metric_id in sorted(defs):
        visit(metric_id, ())
    deepest = max(depth.values(), default=0)
    if deepest > MAX_DEP_DEPTH:
        raise RegistryError(f"指标依赖深度 {deepest} 超过上限 {MAX_DEP_DEPTH}")
    return tuple(order)


def build_registry(*, extra_paths: Sequence[str | Path] = ()) -> MetricRegistry:
    """发现并校验全部 def；任何配置错误在装配期抛 `RegistryError`。"""
    modules = _discover_production()
    if extra_paths:
        modules = [*modules, *_discover_extra(extra_paths)]

    defs: dict[str, MetricDef] = {}
    production_core = 0
    for module, is_extra in modules:
        definition = _module_def(module)
        _validate_shape(definition, module_name=module.__name__)
        if definition.id in defs:
            raise RegistryError(f"重复 metric id: {definition.id}")
        defs[definition.id] = definition
        if not is_extra and definition.core_conclusion:
            production_core += 1

    if defs:
        for definition in defs.values():
            for dependency in definition.dependencies:
                if isinstance(dependency, MetricDep) and dependency.metric_id not in defs:
                    raise RegistryError(f"{definition.id}: 上游指标未注册 {dependency.metric_id}")
        order = _topological_order(defs)
    else:
        order = ()

    if (
        production_core
        and not CORE_CONCLUSION_RANGE[0] <= production_core <= CORE_CONCLUSION_RANGE[1]
    ):
        raise RegistryError(
            f"core_conclusion=True 的指标总数 {production_core} 不在 {CORE_CONCLUSION_RANGE}"
        )

    return MetricRegistry(defs, order)


def iter_dependencies(definition: MetricDef) -> Iterable[Any]:
    return definition.dependencies


__all__ = [
    "MetricRegistry",
    "build_registry",
    "iter_dependencies",
]
