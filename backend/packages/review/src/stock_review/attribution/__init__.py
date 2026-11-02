"""五因子归因纯装配层（Period2-08，设计 §5.2）。

分层（§5.1）：本包在 `stock_core.attribution`（纯函数）之上做**标的→段→因子**的
装配（FIFO 重放结果 → SegmentInput、残差六桶、coverage、人话文案），零 I/O、
零 wall-clock；编排取数在 `stock_review.application.attribution`。
"""

from __future__ import annotations

from stock_review.attribution.context import (
    BEGIN_SOURCE_PREV_TOTAL,
    BEGIN_SOURCE_STATEMENT,
    BEGIN_SOURCE_ZERO,
    AssetWindow,
    MissingEntry,
    resolve_asset_window,
)
from stock_review.attribution.corporate import corporate_action_symbols
from stock_review.attribution.coverage import (
    MIN_SAMPLE_SEGMENTS,
    UNATTRIBUTED_LIMIT,
    Coverage,
    UnattributedEntry,
    build_coverage,
)
from stock_review.attribution.presentation import (
    FactorPresentation,
    assert_copy_compliance,
    build_summary,
    factor_conclusion,
    factor_presentation,
)
from stock_review.attribution.residual import (
    BUCKET_CORPORATE,
    BUCKET_DIVIDEND,
    BUCKET_GAP,
    BUCKET_INTEREST,
    BUCKET_KEYS,
    BUCKET_OTHER,
    BUCKET_UNATTRIBUTED,
    ResidualSplit,
    split_residual,
)
from stock_review.attribution.segments import (
    REASON_ASSET_CLASS,
    REASON_BAR,
    REASON_CORPORATE,
    REASON_DATA_ERROR,
    REASON_FEE,
    REASON_MAPPING,
    REASON_PRICE,
    PlanBundle,
    SymbolPlan,
    build_symbol_plans,
)

__all__ = [
    "BEGIN_SOURCE_PREV_TOTAL",
    "BEGIN_SOURCE_STATEMENT",
    "BEGIN_SOURCE_ZERO",
    "BUCKET_CORPORATE",
    "BUCKET_DIVIDEND",
    "BUCKET_GAP",
    "BUCKET_INTEREST",
    "BUCKET_KEYS",
    "BUCKET_OTHER",
    "BUCKET_UNATTRIBUTED",
    "AssetWindow",
    "Coverage",
    "FactorPresentation",
    "MIN_SAMPLE_SEGMENTS",
    "MissingEntry",
    "PlanBundle",
    "REASON_ASSET_CLASS",
    "REASON_BAR",
    "REASON_CORPORATE",
    "REASON_DATA_ERROR",
    "REASON_FEE",
    "REASON_MAPPING",
    "REASON_PRICE",
    "ResidualSplit",
    "SymbolPlan",
    "UNATTRIBUTED_LIMIT",
    "UnattributedEntry",
    "assert_copy_compliance",
    "build_coverage",
    "build_summary",
    "build_symbol_plans",
    "corporate_action_symbols",
    "factor_conclusion",
    "factor_presentation",
    "resolve_asset_window",
    "split_residual",
]
