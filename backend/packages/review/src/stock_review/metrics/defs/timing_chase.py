"""timing.chase（§3.1 #8，口径 A17；公式 §7.2.1 与 core 层阈值语义）。

追涨占比 = #(买入后 20 根 bar 内最高收盘价 > 买入价 × (1+3%)) / n_valid。
注意：设计文档 §7.2.1 的 A17 原文是「买入前 20 根 bar 的分位 q=(P−min)/(max−min)」，
core 层 `chase_ratio` 实现的是后向 20 根 bar 的阈值命中语义；按 07 任务约束「def 不重写
公式」，本 def 调用 core 函数并负责组装 (entry, post_max) 输入对（差异已在 07 报告披露）。
窗口不足 20 根 bar（或越过 as_of）→ 计 n_pending 不进分母。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from stock_core.metrics.timing import chase_ratio

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
    close_ladders,
    period_trades,
)
from stock_review.metrics.gate import (
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
            label=f"{item.security_code} 买入价 {item.price_units}",
            trade_id=item.id,
            event_date=item.trade_date,
            security_code=item.security_code,
        )
        for item in period_trades(ctx)
        if item.category == "buy"
    )


class ChaseDef:
    id = "timing.chase"
    definition_version = "1.0"
    domain = "timing"
    scope = ScopeSpec(unit="trade", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("trades"), DatasetDep("bars_daily"))
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none")
    status_policy = (
        StatusRule(kind="blocked", code=REASON_DATASET_UNAVAILABLE),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="买入后价格继续上冲的比例越高，说明买点越偏追涨。",
        conclusion_en="A higher share of buys followed by further upside means more chasing.",
        method_zh="对每笔买入，看其后 20 根日线最高价是否超过买入价 3%。",
        method_en="For each buy, whether the max close of the next 20 bars exceeds entry by 3%.",
        what_to_do_zh="追涨比例高时，把买入决策提前到信号出现当天。",
        what_to_do_en="When high, move buy decisions closer to the signal day.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/timing.chase/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每笔买入事件。",
        description_en="Each buy event behind the chase ratio.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        ladders = close_ladders(ctx.bars)
        buys = [item for item in period_trades(ctx) if item.category == "buy"]
        pairs: list[tuple[int, int]] = []
        n_pending = 0
        for trade in buys:
            ladder = ladders.get(trade.security_code)
            if ladder is None or trade.price_units <= 0:
                n_pending += 1
                continue
            dates, closes = ladder
            start = bisect_right(dates, trade.trade_date)
            window = [
                closes[index]
                for index in range(start, len(dates))
                if dates[index] <= ctx.as_of
            ][:POST_WINDOW]
            if len(window) < POST_WINDOW:
                n_pending += 1
                continue
            pairs.append((trade.price_units, max(window)))
        value = chase_ratio(pairs) if pairs else None
        return MetricValue(
            value_units=value,
            n=len(pairs),
            decomposition={
                "n_events": len(buys),
                "n_valid": len(pairs),
                "n_pending": n_pending,
                "post_window": POST_WINDOW,
            },
            n_pending=n_pending,
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = ChaseDef()
