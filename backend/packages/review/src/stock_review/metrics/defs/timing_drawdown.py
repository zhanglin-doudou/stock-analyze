"""timing.drawdown（§3.1 #11，口径 A18/D-12；公式 §5.6/§7.2.4）。

最大回撤族：序列 = period 内 `status≠blocked` 且 `daily_return IS NOT NULL` 的资产日行
（逐自然日）；`value=MDD`；decomp 给持续/恢复/水下比例/Ulcer/Calmar/TWR/年化 TWR。
区间含 blocked 行（断链不拼接）或 degraded 行或 |r|>1 → 不出值；
`n_obs<60` 由引擎 `min_trading_days` 判 pending_window（无 value）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_contracts.constants import MIN_TRADING_DAYS, SCALE_RATIO
from stock_core.metrics.portfolio_index import compound_index
from stock_core.metrics.timing import timing_drawdown

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
    asset_days_window,
)
from stock_review.metrics.gate import (
    REASON_ASSET_BUILD_BLOCKED,
    REASON_ASSET_BUILD_STALE,
    REASON_DATA_DEGRADED,
    REASON_DATA_OUT_OF_RANGE,
    REASON_RECON_BLOCKED,
)

_MAX_ABS_RETURN = 10**SCALE_RATIO


def _build_items(ctx: MetricContext, filters: DrilldownFilters) -> Sequence[DrilldownItem]:
    del filters
    return tuple(
        DrilldownItem(
            key=row.trade_date.isoformat(),
            value_units=row.daily_return_units,
            unit="ratio",
            label=f"{row.trade_date.isoformat()} 日收益",
            event_date=row.trade_date,
        )
        for row in asset_days_in_period(ctx)
    )


class DrawdownDef:
    id = "timing.drawdown"
    definition_version = "1.0"
    domain = "timing"
    scope = ScopeSpec(unit="day", cohort="trade_date", period="month")
    params_schema = EmptyParams
    dependencies = (DatasetDep("asset_daily"), DatasetDep("asset_build"))
    sample_policy = SamplePolicy(
        min_n=10, ci_min_n=10, ci="none", min_trading_days=MIN_TRADING_DAYS
    )
    status_policy = (
        StatusRule(kind="blocked", code=REASON_RECON_BLOCKED),
        StatusRule(kind="blocked", code=REASON_ASSET_BUILD_BLOCKED),
        StatusRule(kind="stale", code=REASON_ASSET_BUILD_STALE),
        StatusRule(kind="unreliable", code=REASON_DATA_DEGRADED),
        StatusRule(kind="unreliable", code=REASON_DATA_OUT_OF_RANGE),
    )
    unit = "ratio"
    precision = 8
    core_conclusion = True
    frequency = "sync"
    benchmark_required = False
    mvp = "P0"
    explain = ExplainSpec(
        conclusion_zh="本月净值从高点最大回落的幅度，越小说明回撤控制越好。",
        conclusion_en="Largest peak-to-trough decline of the month; smaller is better.",
        method_zh="把现金流调整后的日收益复利成指数，取峰谷最大落差。",
        method_en="Compound cashflow-adjusted daily returns and take the max peak-to-trough.",
        what_to_do_zh="回撤偏深时，回看水下天数与恢复期评估是否需要降仓。",
        what_to_do_en="When deep, check underwater days and recovery before cutting risk.",
    )
    drilldown = DrilldownSpec(
        endpoint="/api/v1/metrics/timing.drawdown/drilldown",
        params={"period": "{period}"},
        description_zh="查看本期每个资产日的收益。",
        description_en="Daily returns behind the drawdown path.",
        build_items=_build_items,
    )

    def compute(self, ctx: MetricContext) -> MetricValue:
        # 期末回看 60 个交易日（不是自然月内）：视图已向前加载回看资产日。
        rows = asset_days_window(ctx, MIN_TRADING_DAYS)
        n_blocked = sum(1 for row in rows if row.status == "blocked")
        n_degraded = sum(1 for row in rows if row.status == "degraded")
        # D5：少量 degraded（≤20% 窗口）仍可计算，避免 60TD 回看窗口被个别降级日整段拒答；
        # blocked 一律拒绝（断链不可拼接）。
        degraded_ratio = (n_degraded / len(rows)) if rows else 0
        if n_blocked > 0 or degraded_ratio > 0.25:
            return MetricValue(
                value_units=None,
                n=0,
                decomposition={
                    "n_blocked_rows": n_blocked,
                    "n_degraded_rows": n_degraded,
                },
            )
        usable = [row for row in rows if row.daily_return_units is not None]
        n_missing = len(rows) - len(usable)
        returns = [row.daily_return_units for row in usable if row.daily_return_units is not None]
        # 年化自然日：窗口覆盖整个 period 时沿用 period 跨度（保持既有 golden 口径）；
        # 否则用真实窗口跨度（月度 60TD 回看时不能用 30 天当年化分母）。
        period_span = (ctx.period_end - ctx.period_start).days + 1
        window_span = (rows[-1].trade_date - rows[0].trade_date).days + 1 if rows else period_span
        natural_days = (
            period_span if rows and rows[0].trade_date <= ctx.period_start else window_span
        )
        family = timing_drawdown(returns, natural_days=natural_days)
        n_trading_days = sum(1 for row in usable if row.trade_date.weekday() < 5)
        decomposition = {
            "mdd_duration_td": family.mdd_duration_obs,
            "mdd_recovery_td": family.mdd_recovery_obs,
            "recovery_censored": 1 if family.recovery_censored else 0,
            "underwater_ratio": family.underwater_ratio_units,
            "ulcer": family.ulcer_units,
            "calmar": family.calmar_units,
            "twr": compound_index(returns),
            "ann_twr": family.twr_annual_units,
            "n_obs": family.n_obs,
            "n_missing": n_missing,
            "n_trading_days": n_trading_days,
        }
        if family.unreliable:
            return MetricValue(
                value_units=None, n=family.n_obs, decomposition={**decomposition, "unreliable": 1}
            )
        return MetricValue(
            value_units=family.mdd_units, n=family.n_obs, decomposition=decomposition
        )

    def validate_config(self) -> None:
        assert self.params_schema.model_config.get("extra") == "forbid"


DEF = DrawdownDef()
