"""股息红利差别化税率档位（分档边界 + 四舍五入）单测。"""

from __future__ import annotations

import pytest
from stock_core.fifo.dividend import (
    DIVIDEND_TAX_TIERS,
    dividend_net_units,
    dividend_tax_rate_bp,
    dividend_tax_units,
)


def test_tiers_shape_matches_prd() -> None:
    # (30, 2000) / (365, 1000) / (0, 0)：>365 → 0%，31~365 → 10%，≤30 → 20%
    assert DIVIDEND_TAX_TIERS == ((30, 2000), (365, 1000), (0, 0))


@pytest.mark.parametrize(
    ("holding_days", "expected_bp"),
    [
        (0, 2000),
        (1, 2000),
        (30, 2000),
        (31, 1000),  # 边界：31 天 → 10%
        (60, 1000),
        (365, 1000),  # 边界：365 天 → 10%
        (366, 0),  # 边界：366 天 → 0%
        (1000, 0),
    ],
)
def test_dividend_tax_rate_bp_boundaries(holding_days: int, expected_bp: int) -> None:
    assert dividend_tax_rate_bp(holding_days) == expected_bp


@pytest.mark.parametrize(
    ("gross_units", "holding_days", "expected_tax_units"),
    [
        (100_000, 10, 20_000),  # 1000.00 元 × 20% = 200.00 元
        (100_000, 60, 10_000),  # 1000.00 元 × 10% = 100.00 元
        (100_000, 400, 0),  # 1000.00 元 × 0% = 0
        (3, 10, 1),  # 0.03 元 × 20% = 0.006 → 四舍五入 0.01
        (2, 10, 0),  # 0.02 元 × 20% = 0.004 → 0.00
        (1, 10, 0),
        (0, 10, 0),
    ],
)
def test_dividend_tax_units_round_half_up(
    gross_units: int, holding_days: int, expected_tax_units: int
) -> None:
    assert dividend_tax_units(gross_units, holding_days) == expected_tax_units


@pytest.mark.parametrize(
    ("gross_units", "holding_days"),
    [(100_000, 10), (100_000, 60), (100_000, 400), (3, 10), (0, 30)],
)
def test_dividend_net_units_equals_gross_minus_tax(gross_units: int, holding_days: int) -> None:
    tax = dividend_tax_units(gross_units, holding_days)
    assert dividend_net_units(gross_units, holding_days) == gross_units - tax
    assert dividend_net_units(gross_units, holding_days) + tax == gross_units
