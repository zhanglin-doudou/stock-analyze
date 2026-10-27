"""指标纯公式层（Period2-07）。

本包 18 个指标的纯函数实现，零 I/O、零时钟、零墙钟依赖。
输入输出全部为标度整数，中间计算用 Decimal(prec=40)。

分层：
- policy.py          —— 常量阈值
- holdings.py        —— 持仓重建辅助
- portfolio_index.py —— TWR / 日组合收益
- perf.py            —— 绩效域 7 指标
- timing.py          —— 择时域 4 指标（复用 stats.regression / stats.drawdown）
- behavior.py        —— 行为域 4 指标（复用 stats.concentration）
- selection.py       —— 选股域 3 指标（复用 stats.tstat）

依赖：stats 族（同层，允许依赖）、stock_contracts.constants。
"""

from __future__ import annotations

from stock_core.metrics.behavior import (
    averaging_down_rate,
    decision_efficiency,
    portfolio_concentration_mean,
    turnover_daily_mean,
)
from stock_core.metrics.holdings import (
    DailyPortfolio,
    HoldingLot,
    build_daily_portfolio,
)
from stock_core.metrics.perf import (
    concentration_top3,
    expectancy_annual,
    fee_per_round_mean,
    holding_days_mean,
    pnl_ratio,
    win_rate,
)
from stock_core.metrics.portfolio_index import (
    compound_index,
    daily_portfolio_returns,
)
from stock_core.metrics.selection import (
    bhar_cluster_t,
    bhar_hit_rate,
    bhar_mean,
)
from stock_core.metrics.timing import (
    chase_ratio,
    fly_ratio,
    timing_beta,
    timing_drawdown,
)

__all__ = [
    # holdings
    "DailyPortfolio",
    "HoldingLot",
    # behavior
    "averaging_down_rate",
    # selection
    "bhar_cluster_t",
    "bhar_hit_rate",
    "bhar_mean",
    "build_daily_portfolio",
    # timing
    "chase_ratio",
    # portfolio_index
    "compound_index",
    # perf
    "concentration_top3",
    "daily_portfolio_returns",
    "decision_efficiency",
    "expectancy_annual",
    "fee_per_round_mean",
    "fly_ratio",
    "holding_days_mean",
    "pnl_ratio",
    "portfolio_concentration_mean",
    "timing_beta",
    "timing_drawdown",
    "turnover_daily_mean",
    "win_rate",
]
