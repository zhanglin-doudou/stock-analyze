"""`assets_query`：直接读资产表（只读，不触发构建）。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName

from stock_review.application.assets import list_assets

from ._common import ToolContext, iso, make_citation, money, ok, pct, period_of


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    limit = max(1, min(int(args.get("limit") or 50), 100))
    status = args.get("status")
    sort = str(args.get("sort") or "-trade_date")
    date_from = _date(args.get("date_from"))
    date_to = _date(args.get("date_to"))
    if args.get("period") or date_from is not None or date_to is not None:
        resolved = period_of(ctx, args)
        date_from = date_from or resolved.start
        date_to = date_to or resolved.end
    rows, next_cursor, build = await list_assets(
        ctx.session,
        user_id=ctx.user_id,
        clock=ctx.clock,
        market_data=ctx.market_data,
        limit=limit,
        cursor=args.get("cursor"),
        date_from=date_from,
        date_to=date_to,
        status=str(status) if status else None,
        sort=sort,
    )
    citations = []
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        total_assets = money(row.total_assets_units)
        item = {
            "trade_date": iso(row.trade_date),
            "cash": money(row.cash_units),
            "market_value": money(row.market_value_units),
            "total_assets": total_assets,
            "holding_cost": money(row.holding_cost_units),
            "unrealized_pnl": money(row.unrealized_pnl_units),
            "holding_count": row.holding_count,
            "daily_pnl": money(row.daily_pnl_units),
            "daily_return_pct": pct(row.daily_return_units),
            "status": row.status,
            "status_reason": row.status_reason,
            "definition_version": row.definition_version,
        }
        items.append(item)
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=f"{row.trade_date} 总资产",
            value=total_assets,
            endpoint="GET /api/v1/assets",
            params={"date_from": row.trade_date.isoformat(), "date_to": row.trade_date.isoformat()},
            definition_version=row.definition_version,
            data_as_of=row.trade_date,
        )
        if citation:
            citations.append(citation)

    latest = next((item for item in reversed(items) if item["total_assets"] is not None), None)
    missing: list[MissingEntry] = []
    if build.state in {"missing", "blocked"}:
        missing.append(
            MissingEntry(
                code=f"ASSET_{build.state.upper()}",
                message=build.status_reason or f"资产表状态：{build.state}",
                what_to_do="到资产页构建或修复后重试。",
            )
        )
    data = {
        "items": items,
        "build_state": {
            "state": build.state,
            "status": build.last_status,
            "data_as_of": iso(build.data_as_of),
            "definition_version": build.definition_version,
            "rows_count": build.rows_count,
            "assumption": build.assumption,
            "status_reason": build.status_reason,
        },
        "summary": {
            "start": items[0]["trade_date"] if items else None,
            "end": items[-1]["trade_date"] if items else None,
            "days": len(items),
            "latest_total_assets": latest["total_assets"] if latest else None,
        },
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }
    return ok(
        ToolName.ASSETS_QUERY,
        data=data,
        citations=citations,
        data_as_of=build.data_as_of,
        definition_version=build.definition_version,
        missing=missing,
        row_count=len(items),
    )


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
