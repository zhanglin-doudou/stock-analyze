"""测试用指标 def：胜率（Wilson CI）。

仅由测试通过 `build_registry(extra_paths=[...])` 注入；生产 registry 永不包含它
（E-10）。id 带 `_stub` 后缀，与 07 的 `perf.win_rate` 不冲突。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict
from stock_core.stats.numeric import div_round
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


class WinRateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_holding_days: int = 0


def _closed_rounds(ctx: MetricContext, filters: DrilldownFilters | None = None):
    rounds = [
        item
        for item in ctx.rounds
        if item.status == "closed"
        and item.return_rate_units is not None
        and (filters is None or filters.round_id in (None, item.id))
    ]
    if filters is not None and filters.security_code is not None:
        # stub 无持仓口径：security_code 过滤只用于分页契约测试
        return []
    return rounds


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    return [
        DrilldownItem(
            key=item.id,
            value_units=item.return_rate_units,
            unit="ratio",
            label=f"{item.id} 回合收益",
            round_id=item.id,
            event_date=item.close_date or item.open_date,
        )
        for item in _closed_rounds(ctx, filters)
    ]


class WinRateStubDef:
    id = "perf.win_rate_stub"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = WinRateParams
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="wilson")
    status_policy = ()
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="本月盈利回合占比越高，说明择时与选股的综合胜率越好。",
        conclusion_en="A higher share of profitable rounds means better overall hit rate.",
        method_zh="统计本月已结束回合中收益为正的回合占比，并给出置信区间。",
        method_en="Share of closed rounds with positive return, with a confidence interval.",
        what_to_do_zh="查看每个回合的收益明细，确认亏损回合是否集中在同一标的。",
        what_to_do_en="Review each round's return to see whether losses cluster in one symbol.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/rounds",
        params={"period": "{period}"},
        description_zh="查看构成胜率的每个回合。",
        description_en="Inspect each round behind the win rate.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        minimum = int(ctx.params.get("min_holding_days", 0))
        rounds = [item for item in _closed_rounds(ctx) if item.holding_days >= minimum]
        if not rounds:
            return MetricValue(value_units=None, n=0, decomposition={"wins": 0, "losses": 0})
        wins = sum(1 for item in rounds if (item.return_rate_units or 0) > 0)
        losses = len(rounds) - wins
        samples = tuple(sorted([(1,)] * wins + [(0,)] * losses))
        return MetricValue(
            value_units=div_round(wins * 100_000_000, len(rounds)),
            n=len(rounds),
            decomposition={"wins": wins, "losses": losses},
            ci_sample=samples,
        )

    def validate_config(self) -> None:
        assert "rounds" in DATASET_WHITELIST
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = WinRateStubDef()
