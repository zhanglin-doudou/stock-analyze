"""板块端点（API-087/088，2026-10-31 sector-data 设计 §7）。

* `GET /sectors/overview`（`getSectorsOverview`）：当前快照的板块配置/偏好/集中度；
* `GET /sectors`（`listSectors`）：公共目录树（行业多级 / 概念扁平）；
* 两个端点都只读：不建 job、不写表、不联网（数据源由 AppState 注入）；
* 金额/比率一律在本层转**定点字符串**；不可用字段显式 `null` + `missing` 码；
* admin 不可读业务数据 → 403 `ADMIN_CANNOT_READ_TRADES`。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.metrics.policy import SECTOR_FLAT_ACTIVE_UNITS
from stock_core.values import Money, Ratio
from stock_platform.ports import SectorDataSource
from stock_platform.tenant import TenantContext
from stock_review.application.sectors import (
    CATALOG_DEFAULT_LIMIT,
    CATALOG_MAX_LIMIT,
    TAXONOMY_CONCEPT,
    TAXONOMY_INDUSTRY,
    ConceptBlock,
    SectorCatalogItem,
    SectorCatalogResult,
    SectorOverview,
    SectorRow,
    build_sector_overview,
    list_sector_catalog,
)

from stock_api.deps import get_market_data, get_sector_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    SectorCatalogItemOut,
    SectorCatalogOut,
    SectorCatalogPathOut,
    SectorConcentrationOut,
    SectorConceptBlockOut,
    SectorConceptTopOut,
    SectorMissingOut,
    SectorOthersOut,
    SectorOverviewLinksOut,
    SectorOverviewOut,
    SectorPortfolioOut,
    SectorPreferenceItemOut,
    SectorPreferenceOut,
    SectorRowOut,
    SectorUnclassifiedOut,
)

router = APIRouter(tags=["sectors"], route_class=StrictQueryRoute)

_ETAG_304: dict[int | str, dict[str, Any]] = {
    304: {"description": "Not Modified（If-None-Match 命中）"}
}

_COVERAGE_BASIS_INDUSTRY = "mapped_equity_market_value / equity_market_value"
_COVERAGE_BASIS_CONCEPT = "tagged_equity_market_value / equity_market_value"


def _if_none_match_hit(header_value: str | None, etag: str) -> bool:
    """条件 GET：忽略 `W/` 弱校验前缀与引号后逐字比较。"""
    if not header_value:
        return False
    candidate = header_value.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:]
    return candidate.strip('"') == etag


def _money(value: int | None) -> str | None:
    return None if value is None else str(Money(value))


def _ratio(value: int | None) -> str | None:
    return None if value is None else Ratio(value).as_decimal_str()


def _pct(value: int | None) -> str | None:
    if value is None:
        return None
    text = Ratio(value).as_pct(2)
    return "0.00%" if text == "-0.00%" else text


def _money_display(value: int | None) -> str | None:
    """金额 → 千分位展示串（展示层格式化；raw 定点串仍保留）。"""
    if value is None:
        return None
    text = str(Money(value))
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    whole, _, fraction = text.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    grouped = ",".join(groups)
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


def _direction(active_units: int | None) -> str | None:
    if active_units is None:
        return None
    if active_units > SECTOR_FLAT_ACTIVE_UNITS:
        return "over"
    if active_units < -SECTOR_FLAT_ACTIVE_UNITS:
        return "under"
    return "flat"


def _portfolio_out(result: SectorOverview) -> SectorPortfolioOut:
    portfolio = result.portfolio
    concept = result.taxonomy == TAXONOMY_CONCEPT
    return SectorPortfolioOut(
        total_assets=_money(portfolio.total_assets_units),
        total_assets_display=_money_display(portfolio.total_assets_units),
        holding_market_value=_money(portfolio.holding_market_value_units),
        equity_market_value=_money(portfolio.equity_market_value_units),
        mapped_market_value=_money(portfolio.mapped_market_value_units),
        unclassified_market_value=_money(portfolio.unclassified_market_value_units),
        non_stock_market_value=_money(portfolio.non_stock_market_value_units),
        coverage=_ratio(portfolio.coverage_units),
        coverage_pct=_pct(portfolio.coverage_units),
        coverage_basis=(
            None if concept or portfolio.coverage_units is None else _COVERAGE_BASIS_INDUSTRY
        ),
        equity_position=_ratio(portfolio.equity_position_units),
        equity_position_pct=_pct(portfolio.equity_position_units),
        holding_count=portfolio.holding_count,
        classified_holding_count=portfolio.classified_holding_count,
    )


def _row_out(row: SectorRow, *, concept: bool) -> SectorRowOut:
    return SectorRowOut(
        sector_code=row.sector_code,
        sector_name=row.sector_name,
        level=row.level,
        parent_code=row.parent_code,
        market_value=_money(row.market_value_units),
        market_value_display=_money_display(row.market_value_units),
        weight=_ratio(row.weight_units),
        weight_pct=_pct(row.weight_units),
        holding_count=row.holding_count,
        benchmark_weight=_ratio(row.benchmark_weight_units),
        benchmark_weight_pct=_pct(row.benchmark_weight_units),
        active_weight=_ratio(row.active_weight_units),
        active_weight_pct=_pct(row.active_weight_units),
        direction=row.direction if not concept else None,
        exposure=_ratio(row.exposure_units) if concept else None,
        exposure_pct=_pct(row.exposure_units) if concept else None,
        overlap=row.overlap if concept else None,
    )


def _preference_item(item: Any) -> SectorPreferenceItemOut:
    return SectorPreferenceItemOut(
        sector_code=item.sector_code,
        sector_name=item.sector_name,
        active_weight=Ratio(item.active_weight_units).as_decimal_str(),
        active_weight_pct=_pct(item.active_weight_units) or "0.00%",
        direction=_direction(item.active_weight_units),
    )


def _concept_out(block: ConceptBlock) -> SectorConceptBlockOut:
    return SectorConceptBlockOut(
        coverage=Ratio(block.coverage_units).as_decimal_str(),
        coverage_pct=_pct(block.coverage_units) or "0.00%",
        coverage_basis=_COVERAGE_BASIS_CONCEPT,
        tagged_market_value=str(Money(block.tagged_market_value_units)),
        multi_tag_market_value=str(Money(block.multi_tag_market_value_units)),
        multi_tag_pct=_pct(block.multi_tag_units) or "0.00%",
        overlap=Ratio(block.overlap_units).as_decimal_str(),
        hhi_tag=_ratio(block.hhi_tag_units),
        top1_concept=(
            None
            if block.top1_concept is None
            else SectorConceptTopOut(
                sector_code=block.top1_concept.sector_code,
                sector_name=block.top1_concept.sector_name,
                exposure=Ratio(block.top1_concept.exposure_units or 0).as_decimal_str(),
                exposure_pct=_pct(block.top1_concept.exposure_units) or "0.00%",
            )
        ),
        top5_concepts=[
            SectorConceptTopOut(
                sector_code=row.sector_code,
                sector_name=row.sector_name,
                exposure=Ratio(row.exposure_units or 0).as_decimal_str(),
                exposure_pct=_pct(row.exposure_units) or "0.00%",
            )
            for row in block.top5_concepts
        ],
        caveat=block.caveat,
    )


def _to_out(result: SectorOverview) -> SectorOverviewOut:
    concept = result.taxonomy == TAXONOMY_CONCEPT
    preference = None
    if result.preference is not None:
        preference = SectorPreferenceOut(
            deviation=_ratio(result.preference.deviation_units),
            max_overweight=(
                None
                if result.preference.max_overweight is None
                else _preference_item(result.preference.max_overweight)
            ),
            max_underweight=(
                None
                if result.preference.max_underweight is None
                else _preference_item(result.preference.max_underweight)
            ),
            top_overweight=[_preference_item(item) for item in result.preference.top_overweight],
            top_underweight=[_preference_item(item) for item in result.preference.top_underweight],
        )
    others = None
    if result.others is not None:
        others = SectorOthersOut(
            market_value=str(Money(result.others.market_value_units)),
            weight=_ratio(result.others.weight_units),
            weight_pct=_pct(result.others.weight_units),
            exposure=_ratio(result.others.exposure_units),
            exposure_pct=_pct(result.others.exposure_units),
            holding_count=result.others.holding_count,
        )
    unclassified = None
    if result.unclassified is not None:
        unclassified = SectorUnclassifiedOut(
            market_value=str(Money(result.unclassified.market_value_units)),
            ratio_of_equity=_ratio(result.unclassified.ratio_of_equity_units),
            holding_count=result.unclassified.holding_count,
            symbols=list(result.unclassified.symbols),
        )
    concept_block = None if result.concept is None else _concept_out(result.concept)
    return SectorOverviewOut(
        as_of=result.as_of,
        taxonomy=result.taxonomy,
        level=result.level,
        status=result.status,
        asset_build_state=result.asset_build_state,
        sector_data_state=result.sector_data_state,
        definition_version=result.definition_version,
        sector_dataset_version=result.sector_dataset_version,
        data_as_of=result.data_as_of,
        sector_data_as_of=result.sector_data_as_of,
        benchmark_id=result.benchmark_id,
        benchmark_as_of=result.benchmark_as_of,
        weight_mode=result.weight_mode,
        portfolio=_portfolio_out(result),
        sectors=[_row_out(row, concept=concept) for row in result.sectors],
        others=others,
        unclassified=unclassified,
        concentration=SectorConcentrationOut(
            method=result.concentration.method,
            status=result.concentration.status,
            hhi=_ratio(result.concentration.hhi_units),
            effective_n=_ratio(result.concentration.effective_n_units),
            top1_weight=_ratio(result.concentration.top1_units),
            top3_weight=_ratio(result.concentration.top3_units),
            benchmark_hhi=_ratio(result.concentration.benchmark_hhi_units),
            n_sectors=result.concentration.n_sectors,
        ),
        preference=preference,
        concept=concept_block,
        missing=[
            SectorMissingOut(
                field=item.field, code=item.code, reason=item.reason, what_to_do=item.what_to_do
            )
            for item in result.missing
        ],
        links=SectorOverviewLinksOut(
            dashboard="/dashboard",
            sectors=f"/sectors?taxonomy={result.taxonomy}&level={result.level}",
            attribution=f"/attribution?period={result.as_of.strftime('%Y-%m')}",
        ),
        assumptions=dict(result.assumptions),
        inputs_hash=result.inputs_hash,
    )


def _catalog_item(item: SectorCatalogItem) -> SectorCatalogItemOut:
    return SectorCatalogItemOut(
        sector_code=item.sector_code,
        sector_name=item.sector_name,
        taxonomy=item.taxonomy,
        level=item.level,
        parent_code=item.parent_code,
        path=[
            SectorCatalogPathOut(sector_code=node.sector_code, sector_name=node.sector_name)
            for node in item.path
        ],
        member_count=item.member_count,
        source=item.source,
        classification_version=item.classification_version,
        is_active=item.is_active,
    )


def _catalog_out(result: SectorCatalogResult) -> SectorCatalogOut:
    return SectorCatalogOut(
        as_of=result.as_of,
        taxonomy=result.taxonomy,
        catalog_version=result.catalog_version,
        sector_data_state=result.sector_data_state,
        items=[_catalog_item(item) for item in result.items],
        next_cursor=result.next_cursor,
        has_more=result.has_more,
        missing=[
            SectorMissingOut(
                field=item.field, code=item.code, reason=item.reason, what_to_do=item.what_to_do
            )
            for item in result.missing
        ],
    )


def _sector_digest(sector_data: SectorDataSource) -> str:
    """数据源指纹（replay/cache 用 provenance sha256 前 16 位）。"""
    provenance = sector_data.provenance
    if "sha256:" in provenance:
        digest = provenance.rsplit("sha256:", 1)[-1].split("#", 1)[0]
        return digest[:16]
    return ""


def _resolve_level(taxonomy: str, level: int | None) -> int:
    """concept 恒 level=0；industry 默认 L1，显式非 0 以外的层级一律 422。"""
    if taxonomy == TAXONOMY_CONCEPT:
        if level not in (None, 0):
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="概念板块 level 只能为 0")
        return 0
    resolved = 1 if level is None else level
    if resolved not in (1, 2, 3):
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="行业 level 只能为 1/2/3")
    return resolved


@router.get(
    "/sectors/overview",
    response_model=SectorOverviewOut,
    operation_id="getSectorsOverview",
    responses=_ETAG_304,
)
async def get_sectors_overview_endpoint(
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    as_of: Annotated[date | None, Query()] = None,
    taxonomy: Annotated[str, Query()] = TAXONOMY_INDUSTRY,
    level: Annotated[int | None, Query()] = None,
    parent: Annotated[str | None, Query()] = None,
    benchmark: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SectorOverviewOut | Response:
    """API-087：当前快照的板块配置/偏好/集中度（只读；ETag 命中 → 304）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    sector_data = get_sector_data(request)
    if taxonomy not in (TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT):
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"未知 taxonomy: {taxonomy}")
    resolved_level = _resolve_level(taxonomy, level)
    if as_of is not None and as_of > state.clock.today():
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="as_of 不能晚于今天")
    resolved_benchmark = benchmark or state.settings.sector_benchmark_id
    if resolved_benchmark not in sector_data.capabilities.benchmark_ids:
        raise ApiError(ErrorCode.UNKNOWN_BENCHMARK, detail=f"未知基准: {resolved_benchmark}")

    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await build_sector_overview(
            session,
            user_id,
            sector_data,
            market_data=get_market_data(request),
            clock=state.clock,
            as_of=as_of,
            taxonomy=taxonomy,
            level=resolved_level,
            parent=parent,
            benchmark_id=resolved_benchmark,
            limit=limit,
            coverage_ok_min_units=state.settings.sector_coverage_ok_min_units,
            coverage_degraded_min_units=state.settings.sector_coverage_degraded_min_units,
            flat_active_units=state.settings.sector_flat_active_units,
            benchmark_max_age_days=state.settings.sector_benchmark_max_age_days,
            industry_stale_days=state.settings.sector_industry_stale_days,
            concept_stale_days=state.settings.sector_concept_stale_days,
        )

    etag_value = (
        f"{result.as_of.isoformat()}-{_sector_digest(sector_data)}-{result.inputs_hash[:16]}"
    )
    etag_header = f'W/"{etag_value}"'
    if _if_none_match_hit(request.headers.get("if-none-match"), etag_value):
        return Response(status_code=304, headers={"ETag": etag_header})
    response.headers["ETag"] = etag_header
    return _to_out(result)


@router.get("/sectors", response_model=SectorCatalogOut, operation_id="listSectors")
async def list_sectors_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    taxonomy: Annotated[str, Query()] = TAXONOMY_INDUSTRY,
    level: Annotated[int | None, Query()] = None,
    parent: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    active_only: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=CATALOG_MAX_LIMIT)] = CATALOG_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> SectorCatalogOut:
    """API-088：板块目录树（公共数据、无用户维度；未同步 → 200 + items=[]）。"""
    ctx.require_user()
    ctx.forbid_admin_trades()
    state = get_state(request)
    sector_data = get_sector_data(request)
    if taxonomy not in (TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT):
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"未知 taxonomy: {taxonomy}")
    resolved_level = _resolve_level(taxonomy, level)
    async with state.db.session(user_id=ctx.require_user(), begin=True) as session:
        result = await list_sector_catalog(
            session,
            sector_data,
            taxonomy=taxonomy,
            level=resolved_level,
            parent=parent,
            q=q,
            active_only=active_only,
            limit=limit,
            cursor=cursor,
        )
    return _catalog_out(result)


__all__ = ["router"]
