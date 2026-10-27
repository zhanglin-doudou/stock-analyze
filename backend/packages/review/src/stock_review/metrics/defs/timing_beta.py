"""timing.beta（§3.1 #10，口径 A23；公式 §5.5/§7.2.3）。

组合日收益（asset_daily.daily_return，现金流已调整）对基准日收益做超额 OLS：
`value=β`；decomp 给 α 年化/α 的 t/IR/TE/超额算术/几何。估计窗口 = 期末回看
60 个成对观测（D-18）；`n<60` 由引擎 `min_trading_days` 判 pending_window（无 value）；
Sxx=0（基准恒定）/区间含 blocked 或 degraded 行/|r_p|>1 → 不出值。
已知口径差异：§5.5 的 α/t/TE 校验值与 core 层 OLS 闭式在 rf 口径上有出入（β 精确匹配），
以 core 层 `beta_alpha` 为准（07 报告歧义点 a）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_contracts.constants import A_SHUFFLE_DAYS, MIN_TRADING_DAYS, SCALE_RATIO
from stock_core.metrics.timing import timing_beta

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
    asset_days_in_period,
    benchmark_available,
    sorted_benchmark,
)
from stock_review.metrics.gate import (
    REASON_BENCHMARK_MISSING,
    REASON_DATA_DEGRADED,
    REASON_DATA_OUT_OF_RANGE,
    REASON_MARKET_ASOF_LAG,
    REASON_RECON_BLOCKED,
)

_WINDOW = MIN_TRADING_DAYS
_MAX_ABS_RETURN = 10**SCALE_RATIO


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    del filters
    return tuple(
        DrilldownItem(
            key=row.trade_date.isoformat(),
            value_units=row.daily_return_units,
            unit="ratio",
            label=f"{row.trade_date.isoformat()} 组合日收益",
            event_date=row.trade_date,
        )
        for row in asset_days_in_period(ctx)
    )


class BetaDef:
    id = "timing.beta"
    definition_version = "1.0"
    domain = "timing"
    scope = ScopeSpec(unit="day", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("asset_daily"), DatasetDep("benchmark_series"))
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="none", min_trading_days=_WINDOW)
    status_policy = (
        StatusRule(kind="blocked", code=REASON_RECON_BLOCKED),
        StatusRule(kind="blocked", code=REASON_BENCHMARK_MISSING),
        StatusRule(kind="stale", code=REASON_MARKET_ASOF_LAG),
        StatusRule(kind="unreliable", code=REASON_DATA_DEGRADED),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = True
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="回归系数大于 1 说明组合波动放大基准，小于 1 说明更抗跌。",
        conclusion_en="Beta above 1 amplifies the benchmark; below 1 is more defensive.",
        method_zh="用期末回看 60 个交易日的组合与基准日收益做超额回归。",
        method_en="Excess-return OLS of portfolio vs benchmark over the last 60 trading days.",
        what_to_do_zh="结合信息比率与超额收益看回归系数是否换来了足够的主动收益。",
        what_to_do_en="Read with the information ratio and excess return to judge whether beta paid off.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/timing.beta/drilldown",
        params={"period": "{period}"},
        description_zh="查看估计窗口内每个资产日的组合收益。",
        description_en="Daily portfolio returns inside the estimation window.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        base = {
            "benchmark_id": ctx.benchmark,
            "rf_rate": str(ctx.rf_annual),
            "a_base": A_SHUFFLE_DAYS,
        }
        if not benchmark_available(ctx):
            return MetricValue(value_units=None, n=0, decomposition=base)
        period_rows = asset_days_in_period(ctx)
        n_blocked = sum(1 for row in period_rows if row.status == "blocked")
        n_degraded = sum(1 for row in period_rows if row.status == "degraded")
        if n_blocked > 0 or n_degraded > 0:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={**base, "n_blocked_rows": n_blocked, "n_degraded_rows": n_degraded},
            )
        bench = {row.trade_date: row.return_units for row in sorted_benchmark(ctx)}
        candidates = [
            row
            for row in ctx.asset_daily
            if row.trade_date <= ctx.period_end and row.status != "blocked"
        ]
        candidates.sort(key=lambda row: row.trade_date)
        pairs = [
            (row.daily_return_units, bench[row.trade_date])
            for row in candidates
            if row.daily_return_units is not None and row.trade_date in bench
        ]
        n_missing = len(candidates) - len(pairs)
        pairs = pairs[-_WINDOW:]
        if not pairs:
            return MetricValue(
                value_units=None, n=0, decomposition={**base, "n_obs": 0, "n_missing": n_missing}
            )
        y = [pair[0] for pair in pairs]
        x = [pair[1] for pair in pairs]
        if any(abs(value) > _MAX_ABS_RETURN for value in y):
            return MetricValue(
                value_units=None,
                n=len(pairs),
                decomposition={
                    **base,
                    "n_obs": len(pairs),
                    "n_missing": n_missing,
                    "unreliable": 1,
                },
            )
        result = timing_beta(y, x, rf_annual=ctx.rf_annual)
        value = result.beta_units if not (result.blocked or result.unreliable) else None
        return MetricValue(
            value_units=value,
            n=result.n_obs,
            decomposition={
                **base,
                "alpha_annual": result.alpha_annual_units,
                "alpha_t": result.t_alpha_units,
                "ir": result.ir_units,
                "tracking_error": result.te_units,
                "excess_arith": result.excess_arith_units,
                "excess_geo": result.excess_geo_units,
                "n_obs": result.n_obs,
                "n_missing": n_missing,
                "sxx_zero": 1 if result.blocked else 0,
            },
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = BetaDef()
