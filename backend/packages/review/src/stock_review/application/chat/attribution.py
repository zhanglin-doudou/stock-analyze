"""`attribution_summary`：五因子归因。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName
from stock_core.values import Money

from stock_review.application.attribution import build_attribution

from ._common import ToolContext, iso, make_citation, ok, pct


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    result = await build_attribution(
        ctx.session,
        user_id=ctx.user_id,
        period=args.get("period"),
        model=str(args.get("model") or "five_factor"),
        benchmark=args.get("benchmark"),
        classification_lookahead=False,
        clock=ctx.clock,
        market_data=ctx.market_data,
        sector_data=ctx.sector_data,
    )
    citations = []
    factors = []
    for index, factor in enumerate(result.factors, start=1):
        amount = None if factor.amount_units is None else str(Money(factor.amount_units))
        item = {
            "code": factor.code,
            "name": factor.name,
            "rank": factor.rank,
            "amount": amount,
            "ratio_pct": pct(factor.ratio_units),
            "share_pct": pct(factor.share_units),
            "conclusion": factor.conclusion,
        }
        factors.append(item)
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=f"归因 {factor.name}",
            value=amount,
            endpoint="GET /api/v1/attribution",
            params={"period": result.period, "factor": factor.code},
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
        "model": result.model,
        "period": result.period,
        "period_start": iso(result.period_start),
        "period_end": iso(result.period_end),
        "status": result.status,
        "definition_version": result.definition_version,
        "total_pnl": None if result.total_pnl_units is None else str(Money(result.total_pnl_units)),
        "base_capital": None
        if result.base_capital_units is None
        else str(Money(result.base_capital_units)),
        "summary": result.summary,
        "summary_na_reason": result.summary_na_reason,
        "factors": factors,
        "residual": None if result.residual_units is None else str(Money(result.residual_units)),
        "residual_ratio_pct": pct(result.residual_ratio_units),
        "residual_breakdown": result.residual_breakdown,
        "sample": {
            "n_segments": result.sample.n_segments,
            "n_attributed_segments": result.sample.n_attributed_segments,
            "n_rounds": result.sample.n_rounds,
            "n_symbols": result.sample.n_symbols,
            "min_n": result.sample.min_n,
            "sample_status": result.sample.sample_status,
        },
        "coverage": asdict(result.coverage) if result.coverage is not None else None,
        "missing": [
            {
                "code": item.code,
                "field": item.field,
                "reason": item.reason,
                "what_to_do": item.what_to_do,
                "severity": item.severity,
            }
            for item in result.missing
        ],
        "data_as_of": iso(result.data_as_of),
    }
    return ok(
        ToolName.ATTRIBUTION_SUMMARY,
        data=data,
        citations=citations,
        data_as_of=result.data_as_of,
        definition_version=result.definition_version,
        missing=missing,
        row_count=len(factors),
    )
