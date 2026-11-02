"""五因子逐段剖分（设计 §3.4 恒等式 + §5.2 decompose）。

逐段因子（金额域，分）::

    path        = end_value − base
    sector      = 段已带（单步取整后的板块金额；不可归因段为 None → 整段进残差）
    buy_timing  = base − gross_buy        （仅区间内建仓段）
    sell_timing = gross_sell − end_value  （仅 closed 段）
    fee         = −段费用
    selection   = path − sector + D       （plug；D = 段股息分摊）

恒等式（逐段，代数恒成立，仅作实现一致性自检）::

    selection + sector + buy_timing + sell_timing − fee_amount == gross_sell − gross_buy + D − F
    （closed 段 = fill 净损益 + D；open 段 = 期末市值 − 净买入 + D）

组合级：`Σfactors + residual == total_pnl`（residual 为 plug）。真正的质量门禁是
装配层的受控 fixture residual==0、残差桶独立复算与 asset_daily 对账（设计 §3.1/§7.2）。

`extra_fee_units`（设计 §5.2 形状之上的 additive 参数）：组合级费用因子还须含
**独立费用行**与**未归因标的的费用**（§3.4 fee 行，PRD FR-R5.4「费用=区间费用合计」），
这些费用不落在任何段上，由装配层汇总后传入；相应地装配层的未归因残差桶须把
该标的费用加回（费用已由 fee 因子承担，避免双计）。
"""

from __future__ import annotations

from stock_core.attribution.types import (
    FACTOR_CODES,
    AttributionInput,
    Decomposition,
    FactorCode,
    SegmentFactors,
    SegmentInput,
)


def decompose_segment(seg: SegmentInput) -> SegmentFactors:
    """单段五因子剖分（不可归因段：因子全 0，path 供残差/自检）。"""
    path = seg.end_value_units - seg.base_units
    if not seg.attributed:
        return SegmentFactors(
            path_units=path,
            selection_units=0,
            sector_units=0,
            buy_timing_units=0,
            sell_timing_units=0,
            fee_units=0,
            attributed=False,
        )
    sector = seg.sector_amount_units
    assert sector is not None  # attributed == True 蕴含非 None
    buy_timing = 0 if seg.gross_buy_units is None else seg.base_units - seg.gross_buy_units
    sell_timing = 0 if seg.gross_sell_units is None else seg.gross_sell_units - seg.end_value_units
    selection = path - sector + seg.dividend_units
    return SegmentFactors(
        path_units=path,
        selection_units=selection,
        sector_units=sector,
        buy_timing_units=buy_timing,
        sell_timing_units=sell_timing,
        fee_units=seg.fee_units,
        attributed=True,
    )


def decompose(
    segments: list[SegmentInput] | tuple[SegmentInput, ...],
    total_pnl_units: int,
    *,
    extra_fee_units: int = 0,
) -> Decomposition:
    """组合级五因子汇总；`residual = total_pnl − Σfactors`（整数减法，恒闭合）。"""
    totals: dict[FactorCode, int] = dict.fromkeys(FACTOR_CODES, 0)
    n_segments = 0
    n_attributed = 0
    base_capital = 0
    base_attributed = 0
    for seg in segments:
        n_segments += 1
        base_capital += seg.base_units
        factors = decompose_segment(seg)
        if not factors.attributed:
            continue
        n_attributed += 1
        base_attributed += seg.base_units
        totals[FactorCode.SELECTION] += factors.selection_units
        totals[FactorCode.SECTOR] += factors.sector_units
        totals[FactorCode.BUY_TIMING] += factors.buy_timing_units
        totals[FactorCode.SELL_TIMING] += factors.sell_timing_units
        totals[FactorCode.FEE] -= factors.fee_units
    # 组合级费用：段费用 + 独立费用行 + 未归因标的费用（均为正数口径，因子金额取负）。
    totals[FactorCode.FEE] -= extra_fee_units
    factor_sum = sum(totals.values())
    return Decomposition(
        factors=totals,
        residual_units=total_pnl_units - factor_sum,
        n_segments=n_segments,
        n_attributed_segments=n_attributed,
        base_capital_units=base_capital,
        base_capital_attributed_units=base_attributed,
    )


class FiveFactorModel:
    """`AttributionModel` 的 M1 实现（EP-6：加文件扩展 M2，不改端点骨架）。"""

    model_id = "five_factor"
    definition_version = "1.0"

    def decompose(self, data: AttributionInput) -> Decomposition:
        return decompose(
            list(data.segments),
            data.total_pnl_units,
            extra_fee_units=data.extra_fee_units,
        )


__all__ = ["FiveFactorModel", "decompose", "decompose_segment"]
