"""`data_quality`：资产构建状态、对账断链、数据覆盖。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName
from stock_ledger.models import Reconciliation, Trade

from stock_review.application.assets import get_asset_build_state
from stock_review.models import AssetDaily, Round

from ._common import ToolContext, iso, make_citation, ok


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    build, definition_version = await get_asset_build_state(
        ctx.session,
        user_id=ctx.user_id,
        clock=ctx.clock,
        market_data=ctx.market_data,
    )
    trade_count = await ctx.session.scalar(
        select(func.count())
        .select_from(Trade)
        .where(Trade.user_id == ctx.user_id, Trade.deleted_at.is_(None))
    )
    round_count = await ctx.session.scalar(
        select(func.count()).select_from(Round).where(Round.user_id == ctx.user_id)
    )
    asset_count = await ctx.session.scalar(
        select(func.count())
        .select_from(AssetDaily)
        .where(
            AssetDaily.user_id == ctx.user_id,
            AssetDaily.definition_version == definition_version,
        )
    )
    reconciliation = await ctx.session.scalar(
        select(Reconciliation)
        .where(Reconciliation.user_id == ctx.user_id)
        .order_by(Reconciliation.period_end.desc())
        .limit(1)
    )
    missing: list[MissingEntry] = []
    if build.state in {"missing", "blocked"}:
        missing.append(
            MissingEntry(
                code=f"ASSET_{build.state.upper()}",
                message=build.status_reason or f"资产表状态：{build.state}",
                what_to_do="先导入/修复账单，再构建资产表。",
            )
        )
    if reconciliation is not None and reconciliation.break_count > 0:
        missing.append(
            MissingEntry(
                code="RECONCILIATION_BREAK",
                message=f"对账仍有 {reconciliation.break_count} 处断链。",
                what_to_do="查看对账页并修复断链后重算。",
            )
        )
    citations = []
    citation = make_citation(
        prefix=prefix,
        index=1,
        label="资产表数据截至",
        value=iso(build.data_as_of),
        endpoint="GET /api/v1/assets",
        params={"field": "build.data_as_of"},
        definition_version=definition_version,
        data_as_of=build.data_as_of,
    )
    if citation:
        citations.append(citation)
    data = {
        "build_state": {
            "state": build.state,
            "last_status": build.last_status,
            "data_as_of": iso(build.data_as_of),
            "definition_version": definition_version,
            "rows_count": build.rows_count,
            "status_reason": build.status_reason,
            "assumption": build.assumption,
        },
        "counts": {
            "trades": int(trade_count or 0),
            "rounds": int(round_count or 0),
            "asset_rows": int(asset_count or 0),
        },
        "reconciliation": {
            "status": reconciliation.status if reconciliation else None,
            "break_count": reconciliation.break_count if reconciliation else None,
            "checked_rows": reconciliation.checked_rows if reconciliation else None,
            "period_start": iso(reconciliation.period_start) if reconciliation else None,
            "period_end": iso(reconciliation.period_end) if reconciliation else None,
        },
        "definition_version": definition_version,
        "data_as_of": iso(build.data_as_of),
    }
    return ok(
        ToolName.DATA_QUALITY,
        data=data,
        citations=citations,
        data_as_of=build.data_as_of,
        definition_version=definition_version,
        missing=missing,
        row_count=1,
    )
