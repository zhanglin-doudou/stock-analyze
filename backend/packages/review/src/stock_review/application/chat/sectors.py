"""`sector_exposure`：行业/概念板块概览。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName

from stock_review.application.sectors import build_sector_overview

from ._common import ToolContext, iso, make_citation, money, ok, pct


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    taxonomy = str(args.get("taxonomy") or "industry")
    level = int(args.get("level") or (1 if taxonomy == "industry" else 0))
    result = await build_sector_overview(
        ctx.session,
        ctx.user_id,
        ctx.sector_data,
        market_data=ctx.market_data,
        clock=ctx.clock,
        as_of=args.get("as_of"),
        taxonomy=taxonomy,
        level=level,
        benchmark_id=ctx.settings.sector_benchmark_id,
        limit=max(1, min(int(args.get("limit") or 20), 50)),
        coverage_ok_min_units=ctx.settings.sector_coverage_ok_min_units,
        coverage_degraded_min_units=ctx.settings.sector_coverage_degraded_min_units,
        flat_active_units=ctx.settings.sector_flat_active_units,
        benchmark_max_age_days=ctx.settings.sector_benchmark_max_age_days,
        industry_stale_days=ctx.settings.sector_industry_stale_days,
        concept_stale_days=ctx.settings.sector_concept_stale_days,
    )
    citations = []
    rows = []
    for index, row in enumerate(result.sectors, start=1):
        market_value = money(row.market_value_units)
        item = {
            "sector_code": row.sector_code,
            "sector_name": row.sector_name,
            "level": row.level,
            "parent_code": row.parent_code,
            "market_value": market_value,
            "holding_count": row.holding_count,
            "weight_pct": pct(row.weight_units),
            "benchmark_weight_pct": pct(row.benchmark_weight_units),
            "active_weight_pct": pct(row.active_weight_units),
            "direction": row.direction,
            "exposure_pct": pct(row.exposure_units),
            "overlap": row.overlap,
            "holdings": [{"symbol": h.symbol, "name": h.name} for h in row.holdings],
        }
        rows.append(item)
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=f"板块 {row.sector_name} 市值",
            value=market_value,
            endpoint="GET /api/v1/sectors/overview",
            params={"taxonomy": taxonomy, "sector": row.sector_code},
            definition_version=result.definition_version,
            data_as_of=result.data_as_of,
        )
        if citation:
            citations.append(citation)
    missing = [
        MissingEntry(code=item.code, message=item.reason, what_to_do=item.what_to_do)
        for item in result.missing
    ]
    data = {
        "as_of": iso(result.as_of),
        "taxonomy": result.taxonomy,
        "level": result.level,
        "status": result.status,
        "asset_build_state": result.asset_build_state,
        "sector_data_state": result.sector_data_state,
        "definition_version": result.definition_version,
        "sector_dataset_version": result.sector_dataset_version,
        "data_as_of": iso(result.data_as_of),
        "benchmark_id": result.benchmark_id,
        "weight_mode": result.weight_mode,
        "portfolio": {
            "total_assets": money(result.portfolio.total_assets_units),
            "holding_market_value": money(result.portfolio.holding_market_value_units),
            "equity_market_value": money(result.portfolio.equity_market_value_units),
            "mapped_market_value": money(result.portfolio.mapped_market_value_units),
            "unclassified_market_value": money(result.portfolio.unclassified_market_value_units),
            "coverage_pct": pct(result.portfolio.coverage_units),
            "equity_position_pct": pct(result.portfolio.equity_position_units),
            "holding_count": result.portfolio.holding_count,
        },
        "sectors": rows,
        "concentration": {
            "method": result.concentration.method,
            "status": result.concentration.status,
            "hhi": pct(result.concentration.hhi_units),
            "effective_n": pct(result.concentration.effective_n_units),
            "top1_pct": pct(result.concentration.top1_units),
            "top3_pct": pct(result.concentration.top3_units),
            "n_sectors": result.concentration.n_sectors,
        },
        "preference": _preference(result.preference),
        "concept": _concept(result.concept),
        "missing": [
            {
                "code": item.code,
                "field": item.field,
                "reason": item.reason,
                "what_to_do": item.what_to_do,
            }
            for item in result.missing
        ],
    }
    return ok(
        ToolName.SECTOR_EXPOSURE,
        data=data,
        citations=citations,
        data_as_of=result.data_as_of,
        definition_version=result.definition_version,
        missing=missing,
        row_count=len(rows),
    )


def _preference(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "deviation_pct": pct(value.deviation_units),
        "max_overweight": _pref_item(value.max_overweight),
        "max_underweight": _pref_item(value.max_underweight),
        "top_overweight": [_pref_item(item) for item in value.top_overweight],
        "top_underweight": [_pref_item(item) for item in value.top_underweight],
    }


def _pref_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "sector_code": item.sector_code,
        "sector_name": item.sector_name,
        "active_weight_pct": pct(item.active_weight_units),
    }


def _concept(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "coverage_pct": pct(value.coverage_units),
        "tagged_market_value": money(value.tagged_market_value_units),
        "overlap_pct": pct(value.overlap_units),
        "hhi_tag": pct(value.hhi_tag_units),
        "top1_concept": value.top1_concept.sector_name if value.top1_concept else None,
        "caveat": value.caveat,
    }
