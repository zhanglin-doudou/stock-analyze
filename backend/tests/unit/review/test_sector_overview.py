"""板块概览聚合（`application/sectors.py` 行业/概念分支，设计 §6/§10.2）。

直接调用无 DB 依赖的 `_industry_overview` / `_concept_overview`，注入桩数据源，
逐字符锁定手算 golden（E=100、E_map=96）：权重/HHI/主动权重/偏离度/概念暴露。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from stock_platform.ports import (
    BenchmarkMember,
    SectorBar,
    SectorCapabilities,
    SectorInfo,
    SectorMembership,
)
from stock_review.application import sectors as app

_AS_OF = date(2026, 6, 30)
_TAXONOMY = "industry_sw"
_CONCEPT = "concept_em"
_BASE = {"ok_min": 95_000_000, "deg_min": 90_000_000, "flat": 100_000}


def cents(value: int) -> int:
    """元 → 分（scale=2）。"""
    return value * 100


class StubSectorData:
    def __init__(
        self,
        *,
        catalog: Sequence[SectorInfo],
        memberships: Sequence[SectorMembership],
        benchmark: Sequence[BenchmarkMember] = (),
        provenance: str = "stub:replay#sha256:" + "a" * 64,
        as_of: date = _AS_OF,
        history_start: date = date(2026, 6, 30),
    ) -> None:
        self._catalog = tuple(catalog)
        self._memberships = tuple(memberships)
        self._benchmark = tuple(benchmark)
        self._provenance = provenance
        self._as_of = as_of
        self._history_start = history_start

    @property
    def capabilities(self) -> SectorCapabilities:
        return SectorCapabilities(
            mode="replay",
            taxonomies=(_TAXONOMY, _CONCEPT),
            benchmark_ids=("hs300",),
            history_start=self._history_start,
        )

    @property
    def as_of(self) -> date:
        return self._as_of

    @property
    def provenance(self) -> str:
        return self._provenance

    async def get_catalog(self, taxonomy: str | None = None) -> tuple[SectorInfo, ...]:
        if taxonomy is None:
            return self._catalog
        return tuple(row for row in self._catalog if row.taxonomy == taxonomy)

    async def get_memberships(
        self, taxonomy: str, *, as_of: date | None = None, level: int | None = None
    ) -> tuple[SectorMembership, ...]:
        return tuple(row for row in self._memberships if row.taxonomy == taxonomy)

    async def get_board_bars(
        self, sector_keys: Sequence[tuple[str, str]], start: date, end: date
    ) -> tuple[SectorBar, ...]:
        return ()

    async def get_benchmark_members(
        self, benchmark_id: str, *, as_of: date | None = None
    ) -> tuple[BenchmarkMember, ...]:
        return tuple(row for row in self._benchmark if row.benchmark_id == benchmark_id)


def _info(code: str, name: str, level: int = 1, parent: str | None = None) -> SectorInfo:
    return SectorInfo(
        taxonomy=_TAXONOMY,
        sector_code=code,
        name=name,
        level=level,
        parent_code=parent,
        classification_version="SW2021",
    )


_CATALOG = (
    _info("801030.SI", "基础化工"),
    _info("801050.SI", "银行"),
    _info("801080.SI", "电子"),
)

# A=50 化工、B=30 化工、C=10 银行、D=6 电子、E=4 未分类（E=100、E_map=96）。
_STOCK_MV = {
    "600001": cents(50),
    "600002": cents(30),
    "600003": cents(10),
    "600004": cents(6),
    "600005": cents(4),
}
_MEMBERSHIPS = (
    SectorMembership(_TAXONOMY, "801030.SI", "600001"),
    SectorMembership(_TAXONOMY, "801030.SI", "600002"),
    SectorMembership(_TAXONOMY, "801050.SI", "600003"),
    SectorMembership(_TAXONOMY, "801080.SI", "600004"),
)
_BENCHMARK = (
    BenchmarkMember("hs300", "600001", 15_000_000, _AS_OF),
    BenchmarkMember("hs300", "600002", 15_000_000, _AS_OF),
    BenchmarkMember("hs300", "600003", 40_000_000, _AS_OF),
    BenchmarkMember("hs300", "600004", 30_000_000, _AS_OF),
)


async def _industry(**overrides: object) -> app._Body:  # type: ignore[name-defined]
    params: dict[str, object] = {
        "sector_data": StubSectorData(
            catalog=_CATALOG, memberships=_MEMBERSHIPS, benchmark=_BENCHMARK
        ),
        "db_taxonomy": _TAXONOMY,
        "catalog": _CATALOG,
        "stock_mv": _STOCK_MV,
        "equity_units": cents(100),
        "total_assets": cents(110),
        "holding_mv": cents(104),
        "non_stock_units": cents(4),
        "equity_position": 90_909_091,
        "holding_count": 5,
        "snapshot": _AS_OF,
        "level": 1,
        "parent": None,
        "benchmark_id": "hs300",
        "limit": 100,
        "benchmark_max_age_days": 45,
        "coverage_ok_min_units": _BASE["ok_min"],
        "coverage_degraded_min_units": _BASE["deg_min"],
        "flat_active_units": _BASE["flat"],
        "classification_version": "SW2021",
        "statuses": ["current"],
    }
    params.update(overrides)
    return await app._industry_overview(**params)  # type: ignore[arg-type]


async def test_industry_golden_hand_calc() -> None:
    """§10.2 手算表：权重/HHI/effective_n/top1/top3/active/deviation 逐字符。"""
    body = await _industry()

    assert body.status == "ok"
    assert [row.sector_code for row in body.sectors] == [
        "801030.SI",
        "801050.SI",
        "801080.SI",
    ]
    rows = {row.sector_code: row for row in body.sectors}
    assert rows["801030.SI"].weight_units == 83_333_333
    assert rows["801050.SI"].weight_units == 10_416_667
    assert rows["801080.SI"].weight_units == 6_250_000
    assert rows["801030.SI"].market_value_units == cents(80)
    assert rows["801030.SI"].holding_count == 2

    assert rows["801030.SI"].benchmark_weight_units == 30_000_000
    assert rows["801030.SI"].active_weight_units == 53_333_333
    assert rows["801050.SI"].active_weight_units == -29_583_333
    assert rows["801080.SI"].active_weight_units == -23_750_000
    assert [rows[code].direction for code in rows] == ["over", "under", "under"]

    concentration = body.concentration
    assert concentration.method == "industry_single_membership"
    assert concentration.status == "ok"
    assert concentration.hhi_units == 70_920_139
    assert concentration.effective_n_units == 141_003_672
    assert concentration.top1_units == 83_333_333
    assert concentration.top3_units == 100_000_000
    assert concentration.benchmark_hhi_units == 34_000_000
    assert concentration.n_sectors == 3

    assert body.preference is not None
    assert body.preference.deviation_units == 53_333_333
    assert body.preference.max_overweight is not None
    assert body.preference.max_overweight.sector_code == "801030.SI"
    assert body.preference.max_underweight is not None
    assert body.preference.max_underweight.sector_code == "801050.SI"
    assert [item.sector_code for item in body.preference.top_underweight] == [
        "801050.SI",
        "801080.SI",
    ]

    portfolio = body.portfolio
    assert portfolio.equity_market_value_units == cents(100)
    assert portfolio.mapped_market_value_units == cents(96)
    assert portfolio.unclassified_market_value_units == cents(4)
    assert portfolio.non_stock_market_value_units == cents(4)
    assert portfolio.coverage_units == 96_000_000
    assert portfolio.equity_position_units == 90_909_091
    assert portfolio.holding_count == 5
    assert portfolio.classified_holding_count == 4

    assert body.unclassified is not None
    assert body.unclassified.market_value_units == cents(4)
    assert body.unclassified.ratio_of_equity_units == 4_000_000
    assert body.unclassified.symbols == ["600005"]

    assert body.missing == []
    assert body.concept is None
    assert body.assumptions["weight_basis"] == "mapped_equity_market_value"
    assert body.assumptions["benchmark_weight_source"] == "csindex:hs300"
    assert body.assumptions["industry_standard"] == "SW2021"


async def test_industry_benchmark_missing_degrades_active_weights() -> None:
    body = await _industry(
        sector_data=StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS, benchmark=())
    )

    assert body.status == "degraded"
    assert body.preference is None
    assert body.concentration.benchmark_hhi_units is None
    for row in body.sectors:
        assert row.benchmark_weight_units is None
        assert row.active_weight_units is None
        assert row.direction is None
    codes = {item.code for item in body.missing}
    assert "SECTOR_BENCHMARK_WEIGHTS_MISSING" in codes


async def test_industry_benchmark_stale_degrades_without_changing_numbers() -> None:
    stale = (
        BenchmarkMember("hs300", "600001", 15_000_000, date(2025, 1, 1)),
        BenchmarkMember("hs300", "600002", 15_000_000, date(2025, 1, 1)),
        BenchmarkMember("hs300", "600003", 40_000_000, date(2025, 1, 1)),
        BenchmarkMember("hs300", "600004", 30_000_000, date(2025, 1, 1)),
    )
    body = await _industry(
        sector_data=StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS, benchmark=stale)
    )

    assert body.status == "degraded"
    assert body.benchmark_as_of == date(2025, 1, 1)
    assert body.preference is not None
    assert body.preference.deviation_units == 53_333_333
    assert any(item.code == "SECTOR_BENCHMARK_WEIGHTS_STALE" for item in body.missing)


async def test_industry_non_held_benchmark_sector_is_underweight() -> None:
    """基准含非持仓成分时，该板块必须以 active<0 计入低配（行业通用口径）。

    旧实现只把「持仓 ∩ 基准」的成分纳入基准权重并重新归一，会丢弃非持仓基准板块、
    放大持仓板块基准权重，导致主动权重与偏离度失真。
    """
    catalog = (
        _info("801030.SI", "基础化工"),
        _info("801050.SI", "银行"),
        _info("801150.SI", "医药生物"),
    )
    stock_mv = {
        "600001": cents(50),
        "600002": cents(30),
        "600003": cents(10),
    }
    memberships = (
        SectorMembership(_TAXONOMY, "801030.SI", "600001"),
        SectorMembership(_TAXONOMY, "801030.SI", "600002"),
        SectorMembership(_TAXONOMY, "801050.SI", "600003"),
        SectorMembership(_TAXONOMY, "801150.SI", "600006"),  # 基准成分，非持仓
    )
    # 基准：化工 40% / 银行 30% / 医药 30%（600006 非持仓）
    benchmark = (
        BenchmarkMember("hs300", "600001", 20_000_000, _AS_OF),
        BenchmarkMember("hs300", "600002", 20_000_000, _AS_OF),
        BenchmarkMember("hs300", "600003", 30_000_000, _AS_OF),
        BenchmarkMember("hs300", "600006", 30_000_000, _AS_OF),
    )
    body = await _industry(
        sector_data=StubSectorData(catalog=catalog, memberships=memberships, benchmark=benchmark),
        stock_mv=stock_mv,
        equity_units=cents(90),
        total_assets=cents(100),
        holding_mv=cents(90),
        non_stock_units=cents(10),
        holding_count=3,
    )

    rows = {row.sector_code: row for row in body.sectors}
    # 医药非持仓但在基准中 → 必须出现且为低配
    assert "801150.SI" in rows
    assert rows["801150.SI"].market_value_units == 0
    assert rows["801150.SI"].benchmark_weight_units == 30_000_000
    assert rows["801150.SI"].active_weight_units == -30_000_000
    assert rows["801150.SI"].direction == "under"

    # 化工：组合 80/90=88.8889% - 基准 40% = +48.8889%
    assert rows["801030.SI"].benchmark_weight_units == 40_000_000
    assert rows["801030.SI"].active_weight_units == 48_888_889
    assert rows["801030.SI"].direction == "over"

    # 银行：组合 10/90=11.1111% - 基准 30% = -18.8889%
    assert rows["801050.SI"].active_weight_units == -18_888_889
    assert rows["801050.SI"].direction == "under"

    # 偏离度 D = Σ|Δ|/2 = (48.8889 + 18.8889 + 30) / 2 = 48.8889%
    assert body.preference is not None
    assert body.preference.deviation_units == 48_888_889


async def test_industry_all_unmapped_is_insufficient() -> None:
    body = await _industry(
        sector_data=StubSectorData(catalog=_CATALOG, memberships=(), benchmark=_BENCHMARK)
    )

    assert body.status == "insufficient"
    assert body.portfolio.mapped_market_value_units == 0
    assert body.portfolio.coverage_units == 0
    assert body.concentration.hhi_units is None
    assert body.concentration.top1_units is None
    assert body.concentration.n_sectors == 0
    assert body.portfolio.unclassified_market_value_units == cents(100)
    assert body.preference is None
    assert any(item.code == "SECTOR_MEMBERSHIP_MISSING" for item in body.missing)


async def test_industry_coverage_in_degraded_band() -> None:
    # 只映射 92/100 → coverage 0.92 ∈ [0.90, 0.95) → degraded。
    memberships = (
        SectorMembership(_TAXONOMY, "801030.SI", "600001"),
        SectorMembership(_TAXONOMY, "801030.SI", "600002"),
        SectorMembership(_TAXONOMY, "801050.SI", "600003"),
        SectorMembership(_TAXONOMY, "801080.SI", "600004"),
    )
    stock_mv = {**_STOCK_MV, "600005": cents(4), "600006": cents(4)}
    body = await _industry(
        sector_data=StubSectorData(catalog=_CATALOG, memberships=memberships, benchmark=_BENCHMARK),
        stock_mv=stock_mv,
        equity_units=cents(104),
    )

    assert body.status == "degraded"
    assert body.portfolio.coverage_units == 92_307_692
    assert body.concentration.hhi_units is not None


async def test_industry_parent_filter_and_limit_truncation() -> None:
    body = await _industry(parent="801030.SI")
    assert [row.sector_code for row in body.sectors] == ["801030.SI"]

    truncated = await _industry(limit=1)
    assert len(truncated.sectors) == 1
    assert truncated.others is not None
    assert truncated.others.market_value_units == cents(16)
    assert truncated.others.weight_units == 16_666_667
    assert truncated.others.holding_count == 2
    assert any(item.code == "SECTOR_TRUNCATED" for item in truncated.missing)


def _concept_info(code: str, name: str) -> SectorInfo:
    return SectorInfo(
        taxonomy=_CONCEPT,
        sector_code=code,
        name=name,
        level=0,
        parent_code=None,
        classification_version="EM-2026-06",
    )


_CONCEPT_CATALOG = (_concept_info("BK0999", "人工智能"), _concept_info("BK1010", "新能源"))


async def _concept(
    memberships: Sequence[SectorMembership],
    *,
    limit: int = 100,
    statuses: Sequence[str] = ("current",),
) -> app._Body:  # type: ignore[name-defined]
    return await app._concept_overview(
        sector_data=StubSectorData(catalog=_CONCEPT_CATALOG, memberships=memberships),
        db_taxonomy=_CONCEPT,
        catalog=_CONCEPT_CATALOG,
        stock_mv=_STOCK_MV,
        equity_units=cents(100),
        total_assets=cents(110),
        holding_mv=cents(104),
        non_stock_units=cents(4),
        equity_position=90_909_091,
        holding_count=5,
        snapshot=_AS_OF,
        limit=limit,
        coverage_ok_min_units=_BASE["ok_min"],
        coverage_degraded_min_units=_BASE["deg_min"],
        statuses=list(statuses),
    )


_CONCEPT_MEMBERSHIPS = (
    # A(50)∈{C1,C2}、B(30)∈{C1}、C(10)∉、D(6)∈{C2}、E(4)∉
    SectorMembership(_CONCEPT, "BK0999", "600001"),
    SectorMembership(_CONCEPT, "BK1010", "600001"),
    SectorMembership(_CONCEPT, "BK0999", "600002"),
    SectorMembership(_CONCEPT, "BK1010", "600004"),
)


async def test_concept_golden_hand_calc() -> None:
    """§10.2：exposure 0.80/0.56、coverage 0.86、overlap 1.36、multi_tag 0.50、HHI_tag。"""
    body = await _concept(_CONCEPT_MEMBERSHIPS)

    block = body.concept
    assert block is not None
    assert block.coverage_units == 86_000_000
    assert block.overlap_units == 136_000_000
    assert block.multi_tag_units == 50_000_000
    assert block.hhi_tag_units == 51_557_093
    assert block.tagged_market_value_units == cents(86)
    assert block.multi_tag_market_value_units == cents(50)
    assert block.caveat is not None

    rows = {row.sector_code: row for row in body.sectors}
    assert rows["BK0999"].market_value_units == cents(80)
    assert rows["BK1010"].market_value_units == cents(56)
    assert rows["BK0999"].exposure_units == 80_000_000
    assert rows["BK1010"].exposure_units == 56_000_000
    assert rows["BK0999"].overlap is True
    assert rows["BK1010"].overlap is True
    assert rows["BK0999"].holding_count == 2
    assert block.top1_concept is not None
    assert block.top1_concept.sector_code == "BK0999"
    assert {item.sector_code for item in block.top5_concepts} == {"BK0999", "BK1010"}

    concentration = body.concentration
    assert concentration.method == "concept_overlap"
    assert concentration.status == "unavailable"
    assert concentration.hhi_units is None
    assert body.preference is None
    # 行业映射专属字段在概念请求下必须为 null。
    assert body.portfolio.mapped_market_value_units is None
    assert body.portfolio.unclassified_market_value_units is None
    assert body.portfolio.coverage_units is None
    assert body.portfolio.equity_position_units == 90_909_091
    assert body.portfolio.non_stock_market_value_units == cents(4)

    codes = {item.code for item in body.missing}
    assert "SECTOR_CONCEPT_HHI_UNAVAILABLE" in codes
    assert "SECTOR_CONCEPT_MULTI_MEMBERSHIP" in codes
    # 概念覆盖度 0.86 ≥ 0.90? 否；< 0.50 才给 LOW 提示，这里不触发。
    assert "SECTOR_CONCEPT_COVERAGE_LOW" not in codes
    # 0.86 < 0.90 → 统计轴 insufficient，但暴露数字照常输出。
    assert body.status == "insufficient"


async def test_concept_without_overlap_has_no_caveat() -> None:
    memberships = (
        SectorMembership(_CONCEPT, "BK0999", "600001"),
        SectorMembership(_CONCEPT, "BK0999", "600002"),
        SectorMembership(_CONCEPT, "BK0999", "600003"),
        SectorMembership(_CONCEPT, "BK1010", "600004"),
    )
    body = await _concept(memberships)

    block = body.concept
    assert block is not None
    assert block.overlap_units == 96_000_000
    assert block.multi_tag_units == 0
    assert block.coverage_units == 96_000_000
    assert block.caveat is None
    codes = {item.code for item in body.missing}
    assert "SECTOR_CONCEPT_MULTI_MEMBERSHIP" not in codes
    assert body.status == "ok"


async def test_concept_low_coverage_hint_still_outputs_exposure() -> None:
    memberships = (
        SectorMembership(_CONCEPT, "BK0999", "600002"),
        SectorMembership(_CONCEPT, "BK0999", "600004"),
    )
    body = await _concept(memberships)

    block = body.concept
    assert block is not None
    assert block.coverage_units == 36_000_000
    codes = {item.code for item in body.missing}
    assert "SECTOR_CONCEPT_COVERAGE_LOW" in codes
    rows = [row for row in body.sectors if row.sector_code == "BK0999"]
    assert rows and rows[0].exposure_units == 36_000_000
    assert body.status == "insufficient"


async def test_concept_limit_truncates_with_others() -> None:
    body = await _concept(_CONCEPT_MEMBERSHIPS, limit=1)
    assert len(body.sectors) == 1
    assert body.others is not None
    assert body.others.market_value_units == cents(56)
    assert body.others.exposure_units == 56_000_000
    assert any(item.code == "SECTOR_TRUNCATED" for item in body.missing)


async def test_sector_overview_dataclass_shape() -> None:
    """应用层结果字段齐全（API 边界据此格式化）。"""
    body = await _industry()
    assert isinstance(body, app._Body)  # type: ignore[attr-defined]
    assert isinstance(body.portfolio, app.SectorPortfolio)
    assert isinstance(body.concentration, app.SectorConcentration)
