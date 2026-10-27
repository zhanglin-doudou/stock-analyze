"""整数域统计内核（Period2-05）：数值基元 / Wilson / Bootstrap / 统计量注册表。

core 依赖约束：本包只依赖 `stock_contracts`，不做任何 I/O、不读时钟、不用 float。
"""

from __future__ import annotations

from stock_core.stats.bootstrap import (
    BOOTSTRAP_B,
    BOOTSTRAP_CI_ALPHA_PERMILLE,
    Observation,
    bootstrap_percentile,
)
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
from stock_core.stats.wilson import wilson_interval

__all__ = [
    "BOOTSTRAP_B",
    "BOOTSTRAP_CI_ALPHA_PERMILLE",
    "Estimator",
    "Observation",
    "XorShift64Star",
    "bootstrap_percentile",
    "div_round",
    "get_stats",
    "median_units",
    "percentile_index",
    "register",
    "reset_registry_for_tests",
    "sqrt_units",
    "stats_ids",
    "wilson_interval",
    "xorshift64_star",
]
