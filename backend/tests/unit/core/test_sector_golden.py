"""板块 golden 三方对拍（§10.2）：生产纯函数 ↔ 独立 oracle ↔ 手算期望。

期望值来自 `backend/tests/golden/sector_cases.json`（人工手算），**不得**由被测代码生成。
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from stock_core.sector import (
    SectorNode,
    active_weights,
    concept_exposure,
    coverage_status,
    deviation_units,
    effective_n_units,
    resolve_level,
    sector_hhi_from_values,
    sum_by_sector,
    topk_weights,
    weights_from_values,
)
from stock_core.stats.numeric import div_round

from oracle.naive_sector import (
    active_weights as oracle_active,
)
from oracle.naive_sector import (
    concept_exposure as oracle_concepts,
)
from oracle.naive_sector import (
    coverage_status as oracle_coverage_status,
)
from oracle.naive_sector import (
    deviation as oracle_deviation,
)
from oracle.naive_sector import (
    sector_hhi as oracle_hhi,
)
from oracle.naive_sector import (
    sector_weights as oracle_weights,
)

SCALE = 8
CASES = Path(__file__).resolve().parents[2] / "golden" / "sector_cases.json"


def _reject_float(raw: str) -> Any:
    raise AssertionError(f"golden 禁止 float 字面量: {raw}")


def load_cases() -> dict[str, Any]:
    return json.loads(CASES.read_text(encoding="utf-8"), parse_float=_reject_float)


def _units(value: Fraction) -> int:
    """自然单位 Fraction → scale=8 标度整数（HALF_UP，与生产 div_round 同规）。"""
    scaled = value * 10**SCALE
    dec = Decimal(scaled.numerator) / Decimal(scaled.denominator)
    return int(dec.to_integral_value(rounding=ROUND_HALF_UP))


def _str_units(raw: str) -> int:
    """定点字符串（8 位）→ 标度整数（走 Decimal，不经 float）。"""
    scaled = Decimal(raw).scaleb(SCALE)
    try:
        return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:  # pragma: no cover - 夹具格式错误
        raise AssertionError(f"非法定点值: {raw!r}") from exc


def _fixture_industries(fixture: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    """按 L1 rollup：返回 (L1 合计, L1 权重)。"""
    catalog = {
        row["sector_code"]: SectorNode(row["sector_code"], int(row["level"]), row["parent_code"])
        for row in fixture["catalog"]
    }
    l1 = resolve_level(dict(fixture["industry_l3"]), catalog, 1)
    totals = sum_by_sector(l1, dict(fixture["symbols"]))
    total_mapped = int(fixture["mapped_market_value"])
    return totals, dict(
        zip(
            sorted(totals),
            weights_from_values([totals[code] for code in sorted(totals)], total_mapped),
            strict=True,
        )
    )


def test_industry_weights_three_way() -> None:
    cases = load_cases()
    fixture = cases["fixture"]
    totals, weights = _fixture_industries(fixture)
    expected = {
        code: _str_units(raw) for code, raw in cases["expected"]["industry_weights"].items()
    }
    assert weights == expected

    oracle = oracle_weights(
        [totals[code] for code in sorted(totals)], fixture["mapped_market_value"]
    )
    assert {
        code: _units(item) for code, item in zip(sorted(totals), oracle, strict=True)
    } == expected


def test_coverage_and_status() -> None:
    cases = load_cases()
    fixture = cases["fixture"]
    coverage = div_round(fixture["mapped_market_value"], fixture["total_market_value"], scale=SCALE)
    assert coverage == _str_units(cases["expected"]["industry_coverage"])
    assert coverage_status(coverage) == cases["expected"]["industry_coverage_status"]
    assert (
        oracle_coverage_status(
            Fraction(fixture["mapped_market_value"], fixture["total_market_value"])
        )
        == cases["expected"]["industry_coverage_status"]
    )


def test_hhi_effective_n_and_topk() -> None:
    cases = load_cases()
    totals, _ = _fixture_industries(cases["fixture"])
    values = [totals[code] for code in sorted(totals)]
    total = int(cases["fixture"]["mapped_market_value"])

    hhi = sector_hhi_from_values(values, total)
    assert hhi == _str_units(cases["expected"]["industry_hhi"])
    assert _units(oracle_hhi(values) or Fraction(0)) == hhi

    assert topk_weights(values, total, k=1) == _str_units(cases["expected"]["industry_top1"])
    assert topk_weights(values, total, k=3) == _str_units(cases["expected"]["industry_top3"])
    assert effective_n_units(hhi or 0) == _str_units(cases["expected"]["industry_effective_n"])


def test_active_and_deviation() -> None:
    cases = load_cases()
    fixture = cases["fixture"]
    total = int(fixture["mapped_market_value"])
    catalog = {
        row["sector_code"]: SectorNode(row["sector_code"], int(row["level"]), row["parent_code"])
        for row in fixture["catalog"]
    }
    l1 = resolve_level(dict(fixture["industry_l3"]), catalog, 1)
    totals = sum_by_sector(l1, dict(fixture["symbols"]))
    codes = sorted(totals)
    portfolio = dict(
        zip(codes, weights_from_values([totals[code] for code in codes], total), strict=True)
    )
    benchmark = {code: int(unit) for code, unit in fixture["benchmark_l1_units"].items()}

    active = active_weights(portfolio, benchmark)
    expected = {code: _str_units(raw) for code, raw in cases["expected"]["industry_active"].items()}
    assert active == expected
    assert sum(active.values()) == 0

    oracle = oracle_active(portfolio, benchmark)
    assert {code: _units(value) for code, value in oracle.items()} == expected
    assert _units(sum(oracle.values())) == 0

    deviation = deviation_units(active)
    assert deviation == _str_units(cases["expected"]["industry_deviation"])
    assert _units(oracle_deviation(oracle) or Fraction(0)) == deviation


def test_concepts_three_way() -> None:
    cases = load_cases()
    fixture = cases["fixture"]
    result = concept_exposure(
        dict(fixture["symbols"]), fixture["concept_tags"], total_units=fixture["total_market_value"]
    )
    expected = cases["expected"]
    assert dict(result.exposure.items()) == {
        code: _str_units(raw) for code, raw in expected["concept_exposure"].items()
    }
    assert result.coverage_units == _str_units(expected["concept_coverage"])
    assert result.overlap_units == _str_units(expected["concept_overlap"])
    assert result.multi_tag_units == _str_units(expected["concept_multi_tag"])
    assert result.hhi_tag_units == _str_units(expected["concept_hhi_tag"])

    oracle = oracle_concepts(
        dict(fixture["symbols"]), fixture["concept_tags"], total=fixture["total_market_value"]
    )
    exposure = {code: _units(value) for code, value in oracle["exposure"].items()}  # type: ignore[union-attr]
    assert exposure == {code: _str_units(raw) for code, raw in expected["concept_exposure"].items()}
    assert _units(oracle["coverage"]) == result.coverage_units  # type: ignore[arg-type]
    assert _units(oracle["overlap"]) == result.overlap_units  # type: ignore[arg-type]
    assert _units(oracle["multi_tag"]) == result.multi_tag_units  # type: ignore[arg-type]
    assert oracle["hhi_tag"] is not None
    assert _units(oracle["hhi_tag"]) == result.hhi_tag_units  # type: ignore[arg-type]


def test_edges() -> None:
    cases = load_cases()
    edges = cases["edges"]

    assert sector_hhi_from_values([], 0) is None
    assert topk_weights([], 0) is None
    assert effective_n_units(0) is None
    assert deviation_units({}) is None

    assert sector_hhi_from_values([100], 100) == _str_units(edges["single_sector"]["hhi"])
    assert topk_weights([100], 100, k=1) == _str_units(edges["single_sector"]["top1"])
    assert effective_n_units(100000000) == _str_units(edges["single_sector"]["effective_n"])
    assert deviation_units({"only": 0}) == _str_units(edges["single_sector"]["deviation"])

    unclassified = edges["all_unclassified"]
    assert div_round(0, 100, scale=SCALE) == _str_units(unclassified["coverage"])
    assert coverage_status(_str_units(unclassified["coverage"])) == unclassified["coverage_status"]
    assert sector_hhi_from_values([], 0) is None

    for row in edges["coverage_thresholds"]:
        assert coverage_status(_str_units(row["coverage"])) == row["status"]


@pytest.mark.parametrize("k", [5])
def test_top3_with_more_k_than_sectors(k: int) -> None:
    """k>n 时取全部 → 1.0（不越界、不补齐 0）。"""
    assert topk_weights([80, 10, 6], 96, k=k) == 100000000
