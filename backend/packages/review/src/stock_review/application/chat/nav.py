"""`nav_summary`：资产序列、区间 TWR、最大回撤（只读资产表，不重建 NAV）。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from stock_contracts.chat.dto import ToolResult
from stock_contracts.chat.enums import ToolName

from stock_review.application.assets import get_asset_build_state
from stock_review.application.portfolio import ratio_units
from stock_review.repositories.assets import AssetRepository

from ._common import ToolContext, iso, make_citation, money, ok, pct, period_of


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    resolved = period_of(ctx, args)
    build, definition_version = await get_asset_build_state(
        ctx.session,
        user_id=ctx.user_id,
        clock=ctx.clock,
        market_data=ctx.market_data,
    )
    rows = [
        row
        for row in await AssetRepository(ctx.session).series(
            ctx.user_id, definition_version=definition_version
        )
        if resolved.start <= row.trade_date <= resolved.end
    ]
    points = [
        {
            "trade_date": iso(row.trade_date),
            "total_assets": money(row.total_assets_units),
            "daily_return_pct": pct(row.daily_return_units),
            "daily_pnl": money(row.daily_pnl_units),
            "status": row.status,
        }
        for row in rows
    ]
    twr_units = _compound(rows)
    mdd_units, peak_date, trough_date = _mdd(rows)
    data_as_of: date | None = build.data_as_of
    if data_as_of is None:
        data_as_of = rows[-1].trade_date if rows else None
    latest = rows[-1] if rows else None
    citations = []
    for index, (label, value, key) in enumerate(
        [
            ("期末总资产", money(latest.total_assets_units) if latest else None, "total_assets"),
            ("区间 TWR", pct(twr_units), "twr"),
            ("最大回撤", pct(mdd_units), "max_drawdown"),
        ],
        start=1,
    ):
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=label,
            value=value,
            endpoint="GET /api/v1/nav",
            params={"period": resolved.label, "field": key},
            definition_version=latest.definition_version if latest else None,
            data_as_of=latest.trade_date if latest else None,
        )
        if citation:
            citations.append(citation)
    data = {
        "period": resolved.label,
        "period_start": iso(resolved.start),
        "period_end": iso(resolved.end),
        "points": points,
        "summary": {
            "latest_total_assets": money(latest.total_assets_units) if latest else None,
            "twr_pct": pct(twr_units),
            "max_drawdown_pct": pct(mdd_units),
            "max_drawdown_peak_date": iso(peak_date),
            "max_drawdown_trough_date": iso(trough_date),
            "days": len(points),
        },
        "data_as_of": iso(data_as_of),
        "definition_version": definition_version,
    }
    return ok(
        ToolName.NAV_SUMMARY,
        data=data,
        citations=citations,
        data_as_of=data_as_of,
        definition_version=definition_version,
        row_count=len(points),
    )


def _compound(rows: list[Any]) -> int | None:
    if not rows:
        return None
    product = Decimal(1)
    for row in rows:
        if row.daily_return_units is None or row.status != "ok":
            continue
        product *= Decimal(1) + Decimal(row.daily_return_units) / Decimal(100_000_000)
    if product == Decimal(1):
        return 0
    return int(((product - 1) * Decimal(100_000_000)).to_integral_value())


def _mdd(rows: list[Any]) -> tuple[int | None, Any | None, Any | None]:
    peak: int | None = None
    peak_date = None
    worst_units: int | None = None
    worst_peak_value: int | None = None
    worst_peak = None
    worst_trough = None
    for row in rows:
        if row.status != "ok":
            continue
        value = row.total_assets_units
        if peak is None or value > peak:
            peak, peak_date = value, row.trade_date
        if peak and peak > 0:
            drawdown = peak - value
            if worst_units is None or drawdown > worst_units:
                worst_units, worst_peak_value, worst_peak, worst_trough = (
                    drawdown,
                    peak,
                    peak_date,
                    row.trade_date,
                )
    if worst_units is None or worst_units <= 0:
        return 0, None, None
    return ratio_units(worst_units, worst_peak_value or 0), worst_peak, worst_trough
