"""股息红利差别化税率档位（Period2-01，INTERFACE §2.3；PRD FR-R2.3）。

持有交易日 ≤30 → 20%；31~365 → 10%；>365 → 0%。全部整数运算，四舍五入到分。
"""

from __future__ import annotations

from typing import Final

# (持有交易日上限, 税率 bp)；上限 0 为兜底档（>365 天 → 0%）
DIVIDEND_TAX_TIERS: tuple[tuple[int, int], ...] = ((30, 2000), (365, 1000), (0, 0))

_TAX_DENOMINATOR: Final[int] = 10_000
_TAX_HALF: Final[int] = 5_000


def dividend_tax_rate_bp(holding_days: int) -> int:
    """持有交易日 → 税率（bp）。边界精确：30→20%、31→10%、365→10%、366→0%。"""
    for max_days, rate_bp in DIVIDEND_TAX_TIERS[:-1]:
        if holding_days <= max_days:
            return rate_bp
    return DIVIDEND_TAX_TIERS[-1][1]


def dividend_tax_units(gross_units: int, holding_days: int) -> int:
    """红利税（分，正=税负）；四舍五入到分 `(gross*bp + 5000) // 10000`。"""
    rate_bp = dividend_tax_rate_bp(holding_days)
    return (gross_units * rate_bp + _TAX_HALF) // _TAX_DENOMINATOR


def dividend_net_units(gross_units: int, holding_days: int) -> int:
    """税后股息净额（分）= gross − tax。"""
    return gross_units - dividend_tax_units(gross_units, holding_days)
