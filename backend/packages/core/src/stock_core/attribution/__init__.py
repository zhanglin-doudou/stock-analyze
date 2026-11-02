"""五因子归因纯函数内核（Period2-08，设计 §5.2）。

零 I/O、零 wall-clock、零随机；全部金额为标度整数（分）。口径冻结见
`docs/architecture/2026-11-02-five-factor-attribution.md` §3/§5.2。
"""

from __future__ import annotations

from stock_core.attribution.allocate import allocate
from stock_core.attribution.five_factor import FiveFactorModel, decompose, decompose_segment
from stock_core.attribution.model import AttributionModel
from stock_core.attribution.ranking import (
    rank_factors,
    ratio_na_reason,
    ratio_of,
    share_of,
    shares_of,
    total_abs_amounts,
)
from stock_core.attribution.sector_return import sector_amount_units, sector_return_units
from stock_core.attribution.types import (
    FACTOR_CODES,
    FACTOR_NAMES,
    AttributionInput,
    Decomposition,
    FactorCode,
    SegmentFactors,
    SegmentInput,
    SegmentKind,
)

__all__ = [
    "FACTOR_CODES",
    "FACTOR_NAMES",
    "AttributionInput",
    "AttributionModel",
    "Decomposition",
    "FactorCode",
    "FiveFactorModel",
    "SegmentFactors",
    "SegmentInput",
    "SegmentKind",
    "allocate",
    "decompose",
    "decompose_segment",
    "rank_factors",
    "ratio_na_reason",
    "ratio_of",
    "sector_amount_units",
    "sector_return_units",
    "share_of",
    "shares_of",
    "total_abs_amounts",
]
