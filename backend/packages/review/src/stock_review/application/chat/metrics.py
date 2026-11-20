"""`performance_metrics` / `metric_explain`：18 指标与口径说明。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName

from stock_review.application.metrics import batch_metrics, explain_metric
from stock_review.metrics.base import format_units

from ._common import ToolContext, iso, make_citation, ok


def _ids(args: Mapping[str, Any]) -> list[str] | None:
    raw = args.get("ids")
    if raw is None:
        return None
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        values = [str(item) for item in raw]
    return values or None


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    result = await batch_metrics(
        ctx.session,
        user_id=ctx.user_id,
        clock=ctx.clock,
        market_data=ctx.market_data,
        rf_annual=ctx.settings.rf_annual,
        period=args.get("period"),
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
        benchmark=args.get("benchmark"),
        ids=_ids(args),
        domain=args.get("domain"),
        include_exploratory=bool(args.get("include_exploratory", False)),
    )
    citations = []
    items = []
    for index, item in enumerate(result.items, start=1):
        value = format_units(item.value_units, item.unit, item.precision)
        items.append(
            {
                "metric_id": item.metric_id,
                "domain": item.domain,
                "period": item.period,
                "status": item.status,
                "sample_status": item.sample_status,
                "value": value,
                "unit": item.unit,
                "n": item.n,
                "n_pending": item.n_pending,
                "ci_low": format_units(item.ci_low_units, item.unit, item.precision),
                "ci_high": format_units(item.ci_high_units, item.unit, item.precision),
                "reason": item.reason,
                "definition_version": item.definition_version,
                "data_as_of": iso(item.data_as_of),
            }
        )
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=f"指标 {item.metric_id}",
            value=value,
            endpoint="GET /api/v1/metrics",
            params={"ids": item.metric_id, "period": item.period},
            definition_version=item.definition_version,
            data_as_of=item.data_as_of,
        )
        if citation:
            citations.append(citation)
    missing = [
        MissingEntry(code="METRIC_INSUFFICIENT", message=f"{item.metric_id} 状态 {item.status}")
        for item in result.items
        if item.status != "ok" or item.value_units is None
    ]
    data = {
        "period": result.period,
        "data_as_of": iso(result.data_as_of),
        "definition_version": result.definition_version,
        "assumptions": result.assumptions,
        "items": items,
    }
    return ok(
        ToolName.PERFORMANCE_METRICS,
        data=data,
        citations=citations,
        data_as_of=result.data_as_of,
        definition_version=result.definition_version,
        missing=missing,
        row_count=len(items),
    )


async def run_explain(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    metric_id = str(args.get("metric_id") or "")
    if not metric_id:
        raise ValueError("metric_explain 需要 metric_id")
    result = await explain_metric(
        ctx.session,
        user_id=ctx.user_id,
        clock=ctx.clock,
        market_data=ctx.market_data,
        rf_annual=ctx.settings.rf_annual,
        metric_id=metric_id,
        period=args.get("period"),
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
        benchmark=args.get("benchmark"),
        accept_language=args.get("accept_language"),
    )
    return ok(
        ToolName.METRIC_EXPLAIN,
        data={
            "metric_id": result.metric_id,
            "meaning": result.meaning,
            "method": result.method,
            "conclusion": result.conclusion,
            "status": result.status,
            "sample": result.sample,
            "drilldown": result.drilldown,
            "definition_version": result.definition_version,
            "data_as_of": iso(result.data_as_of),
        },
        data_as_of=result.data_as_of,
        definition_version=result.definition_version,
        row_count=1,
    )
