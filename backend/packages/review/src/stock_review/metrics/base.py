"""指标引擎的编译期契约（Period2-05，07 defs 直接依赖，冻结）。

只放「形状」不放「口径」：18 个指标怎么算由 07 的 defs 决定；这里定义引擎会自动补齐的
字段（sample_status/CI/assumptions）与 def 必须声明的 13+4 个字段。

硬约束：
- 数值一律标度整数（scale = 10**precision），禁止 float；
- `compute` 是纯函数：不做 I/O、不读时钟、不碰随机（随机只由引擎按 inputs_hash 派生 seed）；
- `ci_sample` 只在内存里交接给 CI 计算，不出口 API、不落库。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel
from stock_contracts.constants import MIN_CI_SAMPLE, MIN_CONCLUSION_SAMPLE

MAX_DEP_DEPTH = 3
CI_SAMPLE_MAX = 2000

METRIC_ID_PATTERN = re.compile(r"^(selection|timing|behavior|attribution|perf)\.[a-z0-9_]+$")
DEFINITION_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+$")

DATASET_WHITELIST: tuple[str, ...] = (
    "rounds",
    "fills",
    "trades",
    "asset_daily",
    "asset_build",
    "bars_daily",
    "benchmark_series",
    "calendar",
)

Domain = Literal["perf", "timing", "behavior", "selection", "attribution"]
Unit = Literal["CNY", "ratio", "days", "count", "z"]

UNIT_PRECISION: Mapping[str, int] = {
    "CNY": 2,
    "ratio": 8,
    "days": 2,
    "count": 0,
    "z": 4,
}


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    """统计单元元数据；M1 只实现 period=month（E-07）。

    存在的原因：`unit`（round/position/day/trade）描述「一行样本是什么」，
    而 period 描述「按什么周期聚合」；二者不是同一个概念，必须显式分开。
    """

    unit: str = "month"
    cohort: str = "all"
    period: str = "month"

    def to_dict(self) -> dict[str, str]:
        return {"unit": self.unit, "cohort": self.cohort, "period": self.period}


@dataclass(frozen=True, slots=True)
class SamplePolicy:
    min_n: int = MIN_CONCLUSION_SAMPLE
    ci_min_n: int = MIN_CI_SAMPLE
    ci: Literal["wilson", "bootstrap", "none"] = "none"
    min_trading_days: int = 0


@dataclass(frozen=True, slots=True)
class StatusRule:
    """声明式状态规则：`kind` ∈ {blocked, unreliable, stale}，`code` 是 flags 键或机读原因。"""

    kind: str
    code: str


@dataclass(frozen=True, slots=True)
class DatasetDep:
    name: str


@dataclass(frozen=True, slots=True)
class MetricDep:
    metric_id: str


MetricDependency = DatasetDep | MetricDep


@dataclass(frozen=True, slots=True)
class MetricValue:
    """def.compute 的唯一返回值；不含 status/CI/data_as_of（框架代算）。"""

    value_units: int | None
    n: int
    decomposition: Mapping[str, int | str | None] = field(default_factory=dict)
    n_pending: int = 0
    ci_sample: tuple[tuple[int, ...], ...] | None = None
    ci_stat_id: str | None = None

    def __post_init__(self) -> None:
        if self.value_units is not None and not isinstance(self.value_units, int):
            raise TypeError("value_units must be int or None")
        if bool(self.value_units is not None and isinstance(self.value_units, bool)):
            raise TypeError("value_units must be int or None")
        if self.n < 0:
            raise ValueError("n must be >= 0")
        if self.n_pending < 0:
            raise ValueError("n_pending must be >= 0")
        if self.ci_sample is not None:
            if len(self.ci_sample) > CI_SAMPLE_MAX:
                raise ValueError(f"ci_sample exceeds {CI_SAMPLE_MAX}")
            for observation in self.ci_sample:
                if not observation:
                    raise ValueError("ci_sample observations must not be empty")
                for item in observation:
                    if not isinstance(item, int) or isinstance(item, bool):
                        raise TypeError("ci_sample items must be int")


@dataclass(frozen=True, slots=True)
class ExplainSpec:
    conclusion_zh: str
    conclusion_en: str
    method_zh: str
    method_en: str
    what_to_do_zh: str | None = None
    what_to_do_en: str | None = None


@dataclass(frozen=True, slots=True)
class DrilldownFilters:
    period: str
    benchmark: str | None = None
    round_id: str | None = None
    security_code: str | None = None


@dataclass(frozen=True, slots=True)
class DrilldownItem:
    key: str
    value_units: int | None
    unit: Unit
    label: str
    round_id: str | None = None
    trade_id: str | None = None
    event_date: date | None = None
    security_code: str | None = None


@dataclass(frozen=True, slots=True)
class DrilldownSpec:
    endpoint: str
    params: Mapping[str, str]
    description_zh: str
    description_en: str
    build_items: Callable[[MetricContext, DrilldownFilters], Sequence[DrilldownItem]]


@runtime_checkable
class MetricContext(Protocol):
    """冻结的上下文形状（§4.3）；视图均为不可变序列/映射，禁止暴露 ORM 对象。

    以 property 声明：实现方是 frozen dataclass（只读属性），可结构匹配。
    """

    @property
    def user_id(self) -> uuid.UUID: ...

    @property
    def period(self) -> str: ...

    @property
    def period_start(self) -> date: ...

    @property
    def period_end(self) -> date: ...

    @property
    def as_of(self) -> date: ...

    @property
    def params(self) -> Mapping[str, Any]: ...

    @property
    def benchmark(self) -> str | None: ...

    @property
    def rf_annual(self) -> Any: ...

    @property
    def rounds(self) -> Any: ...

    @property
    def fills(self) -> Any: ...

    @property
    def trades(self) -> Any: ...

    @property
    def asset_daily(self) -> Any: ...

    @property
    def asset_build(self) -> Any: ...

    @property
    def bars(self) -> Any: ...

    @property
    def benchmark_series(self) -> Any: ...

    @property
    def calendar(self) -> Any: ...

    @property
    def flags(self) -> Mapping[str, bool]: ...

    @property
    def upstream(self) -> Any: ...

    @property
    def fingerprints(self) -> Mapping[str, str]: ...

    @property
    def market_as_of(self) -> date | None: ...

    @property
    def observed_trading_days(self) -> int: ...


class MetricDef(Protocol):
    """13 字段 + 4 个附加；defs 模块级实例名必须是 `DEF`（唯一）。"""

    id: str
    definition_version: str
    domain: Domain
    scope: ScopeSpec
    params_schema: type[BaseModel]
    dependencies: tuple[MetricDependency, ...]
    sample_policy: SamplePolicy
    status_policy: tuple[StatusRule, ...]
    unit: Unit
    precision: int
    core_conclusion: bool
    frequency: Literal["sync", "async"]
    benchmark_required: bool
    mvp: Literal["P0", "M1", "M2"]
    explain: ExplainSpec
    drilldown: DrilldownSpec

    def compute(self, ctx: MetricContext) -> MetricValue: ...

    def validate_config(self) -> None: ...


def assert_no_status_leak(value: MetricValue) -> None:
    """compute 返回值只能含 5 个字段（+ ci 两项），不得夹带 status/CI/as_of。"""
    allowed = {"value_units", "n", "decomposition", "n_pending", "ci_sample", "ci_stat_id"}
    actual = set(value.__dataclass_fields__)
    if actual != allowed:
        raise AssertionError(f"MetricValue fields drifted: {sorted(actual ^ allowed)}")


def format_units(units: int | None, unit: str, precision: int) -> str | None:
    """标度整数 → 定点字符串（不引入 float；precision 与 unit 口径一致）。"""
    if units is None:
        return None
    if precision == 0:
        return str(units)
    sign = "-" if units < 0 else ""
    digits = str(abs(units)).rjust(precision + 1, "0")
    text = f"{digits[:-precision]}.{digits[-precision:]}"
    return sign + text if text.strip("0.") else f"{0:.{precision}f}"
