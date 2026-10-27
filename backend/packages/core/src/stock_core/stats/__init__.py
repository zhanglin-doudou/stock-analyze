"""整数域统计内核：数值基元 / Wilson / Bootstrap / 回归 / 集中度 / 回撤 / t 值 / 统计量注册表。

core 依赖约束：本包只依赖 `stock_contracts`，不做任何 I/O、不读时钟、不用 float。
Period2-05 交付：numeric / wilson / bootstrap / estimators 框架 + mean。
Period2-07 交付：regression / concentration / drawdown / tstat。
"""

from __future__ import annotations

from stock_core.stats.bootstrap import (
    BOOTSTRAP_B,
    BOOTSTRAP_CI_ALPHA_PERMILLE,
    Observation,
    bootstrap_percentile,
)
from stock_core.stats.concentration import (
    HHI,
    cr1_units,
    gini_units,
    hhi_indices,
    top_k_share,
)
from stock_core.stats.drawdown import DrawdownFamily, drawdown_family
from stock_core.stats.estimators import (
    Estimator,
    get_stats,
    register,
    reset_registry_for_tests,
    stats_ids,
)
from stock_core.stats.numeric import (
    XorShift64Star,
    div_round,
    median_units,
    percentile_index,
    sqrt_units,
    xorshift64_star,
)
from stock_core.stats.regression import (
    BetaAlpha,
    beta_alpha,
    rf_daily_units,
    stdev_units,
)
from stock_core.stats.tstat import cluster_t
from stock_core.stats.wilson import wilson_interval

__all__ = [
    "BOOTSTRAP_B",
    "BOOTSTRAP_CI_ALPHA_PERMILLE",
    "HHI",
    "BetaAlpha",
    "DrawdownFamily",
    "Estimator",
    "Observation",
    "XorShift64Star",
    "beta_alpha",
    "bootstrap_percentile",
    "cluster_t",
    "cr1_units",
    "div_round",
    "drawdown_family",
    "get_stats",
    "gini_units",
    "hhi_indices",
    "median_units",
    "percentile_index",
    "register",
    "reset_registry_for_tests",
    "rf_daily_units",
    "sqrt_units",
    "stats_ids",
    "stdev_units",
    "top_k_share",
    "wilson_interval",
    "xorshift64_star",
]
