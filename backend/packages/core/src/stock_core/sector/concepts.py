"""概念板块：多归属标签的可重叠暴露（不归一、不与行业 HHI 横比）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stock_core.stats.numeric import div_round


@dataclass(frozen=True, slots=True)
class ConceptExposure:
    """概念暴露输出（比率 scale=8）。

    * `exposure`：每个概念 `MV_c / E_t`（可 >1、不可加总）；
    * `coverage_units`：至少属于一个概念的市值 / E_t；
    * `overlap_units`：MV 加权平均标签数（= ΣMV_c / E_t，≥ coverage）；
    * `multi_tag_units`：≥2 个标签的持仓市值 / E_t；
    * `hhi_tag_units`：标签份额 HHI（辅助口径，独立命名）。
    """

    exposure: dict[str, int]
    coverage_units: int
    overlap_units: int
    multi_tag_units: int
    hhi_tag_units: int | None


def concept_exposure(
    mv_by_symbol: Mapping[str, int],
    memberships: Mapping[str, Sequence[str]],
    *,
    total_units: int,
    scale: int = 8,
) -> ConceptExposure:
    """按概念标签聚合暴露；分母是**权益市值 `E_t`**（含未分类/无标签股票）。

    `memberships`: symbol -> 概念代码序列（可重复出现同一 code，按一次计）。
    `total_units<=0` 抛 `ZeroDivisionError`（空仓由调用方处理）。
    """
    if total_units <= 0:
        raise ZeroDivisionError("total must be positive")

    tagged_total = 0
    multi_tag_total = 0
    overlap_total = 0
    mv_by_concept: dict[str, int] = {}
    for symbol, value in mv_by_symbol.items():
        tags = set(memberships.get(symbol, ()))
        if not tags:
            continue
        tagged_total += value
        if len(tags) >= 2:
            multi_tag_total += value
        for code in tags:
            mv_by_concept[code] = mv_by_concept.get(code, 0) + value
    overlap_total = sum(mv_by_concept.values())

    exposure = {
        code: div_round(value, total_units, scale=scale) for code, value in mv_by_concept.items()
    }
    hhi_tag: int | None = None
    if overlap_total > 0:
        sum_sq = sum(value * value for value in mv_by_concept.values())
        hhi_tag = div_round(sum_sq, overlap_total * overlap_total, scale=scale)
    return ConceptExposure(
        exposure=exposure,
        coverage_units=div_round(tagged_total, total_units, scale=scale),
        overlap_units=div_round(overlap_total, total_units, scale=scale),
        multi_tag_units=div_round(multi_tag_total, total_units, scale=scale),
        hhi_tag_units=hhi_tag,
    )
