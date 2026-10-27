"""timing.fly（§3.1 #9，口径 A16；公式 §7.2.2 与 core 层阈值语义，exploratory）。

卖飞占比 = #(卖出后 20 根 bar 内最高收盘价 > 卖出价 × (1+3%)) / n_valid；decomp 并列
h=5/10 口径。注意：设计文档 §7.2.2 的 A16 原文是「z=(R_after−R_bench)/(σ·√h)>1」，
core 层 `fly_ratio` 实现的是阈值命中语义；按 07 任务约束调用 core 函数（差异已披露）。
缺基准（benchmark 未配置或无可用序列）→ value=None；窗口不足 → n_pending。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from stock_core.metrics.timing import fly_ratio

from stock_review.metrics.base import (
    DatasetDep,
    DrilldownFilters,
    DrilldownItem,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
    StatusRule,
)
from stock_review.metrics.defs._common import (
    EmptyParams,
    benchmark_available,
    close_ladders,
    period_trades,
)
from stock_review.metrics.gate import (
    REASON_BENCHMARK_MISSING,
    REASON_DATA_OUT_OF_RANGE,
    REASON_DATASET_UNAVAILABLE,
    REASON_MARKET_ASOF_LAG,
)

POST_WINDOW = 20


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    del filters
    return tuple(
        DrilldownItem(
            key=item.id,
            value_units=None,
            unit="ratio",
            label=f"{item.security_code} 卖出价 {item.price_units}",
            trade_id=item.id,
            event_date=item.trade_date,
            security_code=item.security_code,
        )
        for item in period_trades(ctx)
        if item.category == "sell"
    )


class FlyDef:
    id = "timing.fly"
    definition_version = "1.0"
    domain = "timing"
    scope = ScopeSpec(unit="trade", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (
        DatasetDep("trades"),
        DatasetDep("bars_daily"),
        DatasetDep("benchmark_series"),
    )
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_DATASET_UNAVAILABLE),
        StatusRule(kind="blocked", code=REASON_BENCHMARK_MISSING),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = False
    frequency = "sync"
    benchmark_required = True
    mvp = "M1"
    explain = ExplainSpec(
        conclusion_zh="卖出后价格继续大涨的比例越高，说明卖点越偏「卖飞」。",
        conclusion_en="A higher share of sells followed by further rallies means selling early.",
        method_zh="对每笔卖出，看其后 20 根日线最高价是否超过卖出价 3%。",
        method_en="For each sell, whether the max close of the next 20 bars exceeds exit by 3%.",
        what_to_do_zh="卖飞比例高时，试试分批止盈而不是一次清仓。",
        what_to_do_en="When high, consider scaling out instead of selling all at once.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/timing.fly/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每笔卖出事件。",
        description_en="Each sell event behind the fly ratio.",
        build_items=_build_items,
    )

    def _share(self, ctx: MetricContext, *, window: int) -> tuple[int | None, int, int]:
        ladders = close_ladders(ctx.bars)
        sells = [item for item in period_trades(ctx) if item.category == "sell"]
        pairs: list[tuple[int, int]] = []
        n_pending = 0
        for trade in sells:
            ladder = ladders.get(trade.security_code)
            if ladder is None or trade.price_units <= 0:
                n_pending += 1
                continue
            dates, closes = ladder
            start = bisect_right(dates, trade.trade_date)
            post = [
                closes[index]
                for index in range(start, len(dates))
                if dates[index] <= ctx.as_of
            ][:window]
            if len(post) < window:
                n_pending += 1
                continue
            pairs.append((trade.price_units, max(post)))
        return (fly_ratio(pairs) if pairs else None), len(pairs), n_pending

    def compute(self, ctx: MetricContext) -> MetricValue:
        sells = sum(1 for item in period_trades(ctx) if item.category == "sell")
        if not benchmark_available(ctx):
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={
                    "n_events": sells,
                    "n_valid": 0,
                    "n_pending": 0,
                    "fly_share_h5": None,
                    "fly_share_h10": None,
                    "benchmark_id": ctx.benchmark,
                },
            )
        value, n_valid, n_pending = self._share(ctx, window=POST_WINDOW)
        share_h5, _, _ = self._share(ctx, window=5)
        share_h10, _, _ = self._share(ctx, window=10)
        return MetricValue(
            value_units=value,
            n=n_valid,
            decomposition={
                "n_events": sells,
                "n_valid": n_valid,
                "n_pending": n_pending,
                "fly_share_h5": share_h5,
                "fly_share_h10": share_h10,
                "benchmark_id": ctx.benchmark,
            },
            n_pending=n_pending,
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = FlyDef()
