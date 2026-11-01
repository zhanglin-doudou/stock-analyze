"""独立 oracle：板块纯函数朴素实现（§10 三方对拍）。

铁律（与 ``naive_stats`` / ``naive_metrics`` 一致）：只 import 标准库，**禁止** import
``stock_contracts / stock_core / stock_platform / ...``。输入是自然单位（元），
输出一律 ``Fraction``（比率即 0.8 这类），由测试层按 scale 折算为标度整数。

公式独立重写：HHI 用「板块合计值的平方和」，概念 HHI 用「标签合计值的平方和」，
与生产实现的精确整数式在数学上等价，但不复用其代码。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction


def sector_weights(values: Sequence[int], total: int) -> list[Fraction]:
    """每个板块值 / 总映射市值。"""
    if total <= 0:
        raise ZeroDivisionError("total must be positive")
    return [Fraction(value, total) for value in values]


def sector_hhi(sector_values: Sequence[int]) -> Fraction | None:
    """HHI = Σ(v_s/E_map)²；分母为 0 → None。"""
    total = sum(sector_values)
    if total <= 0:
        return None
    return sum(Fraction(value, total) ** 2 for value in sector_values)


def effective_n(hhi: Fraction | None) -> Fraction | None:
    """有效板块数 = 1/HHI。"""
    if hhi is None or hhi <= 0:
        return None
    return 1 / hhi


def topk_share(values: Sequence[int], total: int, k: int) -> Fraction | None:
    """前 k 大板块合计 / total；先降序再切。"""
    if k <= 0:
        raise ValueError("k must be positive")
    if total <= 0:
        return None
    ordered = sorted(values, reverse=True)
    return Fraction(sum(ordered[:k]), total)


def active_weights(
    portfolio: Mapping[str, int], benchmark: Mapping[str, int]
) -> dict[str, Fraction]:
    """w_p - w_b（并集；缺席按 0）。"""
    keys = set(portfolio) | set(benchmark)
    return {
        key: Fraction(portfolio.get(key, 0), 10**8) - Fraction(benchmark.get(key, 0), 10**8)
        for key in keys
    }


def deviation(active: Mapping[str, Fraction]) -> Fraction | None:
    """偏离度 D = Σ|Δ| / 2；空映射 → None。"""
    if not active:
        return None
    return sum(abs(value) for value in active.values()) / 2


def concept_exposure(
    mv_by_symbol: Mapping[str, int],
    memberships: Mapping[str, Sequence[str]],
    *,
    total: int,
) -> dict[str, object]:
    """概念多归属暴露（不归一）。

    返回 ``{"exposure": {code: Fraction}, "coverage"/"overlap"/"multi_tag"/"hhi_tag"}``。
    """
    if total <= 0:
        raise ZeroDivisionError("total must be positive")
    mv_by_concept: dict[str, int] = {}
    tagged = 0
    multi_tag = 0
    for symbol, value in mv_by_symbol.items():
        tags = set(memberships.get(symbol, ()))
        if not tags:
            continue
        tagged += value
        if len(tags) >= 2:
            multi_tag += value
        for code in tags:
            mv_by_concept[code] = mv_by_concept.get(code, 0) + value
    overlap = sum(mv_by_concept.values())
    hhi_tag: Fraction | None = None
    if overlap > 0:
        hhi_tag = sum(Fraction(value, overlap) ** 2 for value in mv_by_concept.values())
    return {
        "exposure": {code: Fraction(value, total) for code, value in mv_by_concept.items()},
        "coverage": Fraction(tagged, total),
        "overlap": Fraction(overlap, total),
        "multi_tag": Fraction(multi_tag, total),
        "hhi_tag": hhi_tag,
    }


def coverage_status(coverage: Fraction) -> str:
    """覆盖率阈值：≥0.95 ok；≥0.90 degraded；否则 insufficient。"""
    if coverage >= Fraction(95, 100):
        return "ok"
    if coverage >= Fraction(90, 100):
        return "degraded"
    return "insufficient"


__all__ = [
    "active_weights",
    "concept_exposure",
    "coverage_status",
    "deviation",
    "effective_n",
    "sector_hhi",
    "sector_weights",
    "topk_share",
]
