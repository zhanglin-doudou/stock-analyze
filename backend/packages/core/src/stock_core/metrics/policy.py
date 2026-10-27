"""指标层常量（Period2-07）。

跨指标共享的阈值、默认参数、分位锚点。只含整数常量（比率用整数 × 10^-scale），
零 I/O、零时钟、零依赖（除 stock_contracts.constants 的共享标度）。
"""

from __future__ import annotations

from typing import Final

# —— 择时阈值 ——
# 追涨/杀跌的"反向价格运动"百分比阈值（默认 3%）
CHASE_THRESHOLD_SCALE8: Final[int] = 3_000_000  # 0.03
FLY_THRESHOLD_SCALE8: Final[int] = 3_000_000   # 0.03

# —— 行为域 ——
# 换手率日均分位锚（用于判断"过度交易"的绝对水平）
TURNOVER_HIGH_SCALE8: Final[int] = 500_000  # 0.05 → 日换手 ≥ 5% 算偏高

# —— 选股域 ——
# BHAR hit 的下限（20/60 日窗口超额收益 ≥ 此值算命中）
BHAR_HIT_THRESHOLD_SCALE8: Final[int] = 0  # 0%（超额收益非负即命中）

# —— 杠杆/保证金 ——
# 账户平均杠杆（用于 beta 调整）
DEFAULT_LEVERAGE_SCALE8: Final[int] = 100_000_000  # 1.0（无杠杆）

# —— 回测完整性 ——
# bar 密度阈值：bar_coverage = #有 bar 交易日 / #应交易日
# 低于此值视为数据稀疏（timing.beta 等择时指标的信号质量下降）
BAR_DENSITY_THRESHOLD_SCALE8: Final[int] = 95_000_000  # 0.95
