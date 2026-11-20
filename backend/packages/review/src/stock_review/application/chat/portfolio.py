"""`portfolio_snapshot`：Dashboard 资产/历史概览。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName
from stock_core.values import Money, Ratio, format_quantity

from stock_review.application.dashboard import build_dashboard

from ._common import ToolContext, iso, make_citation, money, ok, pct


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    result = await build_dashboard(
        ctx.session,
        user_id=ctx.user_id,
        clock=ctx.clock,
        market_data=ctx.market_data,
    )
    asset = result.asset
    history = result.history
    citations = []
    citation_specs = [
        ("总资产", money(asset.total_assets_units), "GET /api/v1/dashboard", "total_assets"),
        ("总收益", money(asset.total_profit_units), "GET /api/v1/dashboard", "total_profit"),
        ("胜率", pct(history.win_ratio_units), "GET /api/v1/dashboard", "win_ratio"),
        ("最大回撤", pct(history.max_drawdown.mdd_units), "GET /api/v1/dashboard", "max_drawdown"),
    ]
    for index, (label, value, endpoint, key) in enumerate(citation_specs, start=1):
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=label,
            value=value,
            endpoint=endpoint,
            params={"period": result.legacy_period, "field": key},
            definition_version=result.definition_version,
            data_as_of=result.data_as_of,
        )
        if citation:
            citations.append(citation)

    data = {
        "as_of": iso(result.as_of),
        "data_as_of": iso(result.data_as_of),
        "legacy_period": result.legacy_period,
        "metrics_period": result.metrics_period,
        "status": result.status,
        "build_state": result.build_state,
        "definition_version": result.definition_version,
        "asset": {
            "status": asset.status,
            "market_value": money(asset.market_value_units),
            "cash": money(asset.cash_units),
            "total_assets": money(asset.total_assets_units),
            "position_ratio_pct": pct(asset.position_ratio_units),
            "total_profit": money(asset.total_profit_units),
            "max_invested_cost": money(asset.max_invested_cost_units),
            "total_fees": money(asset.total_fees_units),
            "bill_start_date": iso(asset.bill_start_date),
            "bill_end_date": iso(asset.bill_end_date),
            "initial_capital": money(asset.initial_capital_units),
            "annualized_return_pct": pct(asset.annualized_return_ratio_units),
            "holdings": [
                {
                    "security_code": holding.security_code,
                    "security_name": holding.security_name,
                    "quantity": format_quantity(holding.quantity_units),
                    "cost": money(holding.cost_units),
                    "market_value": money(holding.market_value_units),
                    "weight_pct": pct(holding.weight_ratio_units),
                }
                for holding in asset.holdings
            ],
        },
        "history": {
            "status": history.status,
            "closed_security_count": history.closed_security_count,
            "closed_round_count": history.closed_round_count,
            "win_count": history.win_count,
            "loss_count": history.loss_count,
            "flat_count": history.flat_count,
            "win_ratio_pct": pct(history.win_ratio_units),
            "loss_ratio_pct": pct(history.loss_ratio_units),
            "flat_ratio_pct": pct(history.flat_ratio_units),
            "max_profit": money(history.max_profit_units),
            "max_profit_round": _round(history.max_profit_round),
            "top_rounds": [_round(item) for item in history.top_rounds],
            "bottom_rounds": [_round(item) for item in history.bottom_rounds],
            "top_holding_rounds": [_round(item) for item in history.top_holding_rounds],
            "max_drawdown": {
                "from_date": iso(history.max_drawdown.from_date),
                "to_date": iso(history.max_drawdown.to_date),
                "duration_days": history.max_drawdown.duration_days,
                "recovery_days": history.max_drawdown.recovery_days,
                "mdd_pct": pct(history.max_drawdown.mdd_units),
                "sample_status": history.max_drawdown.sample_status,
                "reason": history.max_drawdown.reason,
            },
        },
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
    missing = [
        MissingEntry(code=item.code, message=item.reason, what_to_do=item.what_to_do)
        for item in result.missing
    ]
    return ok(
        ToolName.PORTFOLIO_SNAPSHOT,
        data=data,
        citations=citations,
        data_as_of=result.data_as_of,
        definition_version=result.definition_version,
        missing=missing,
        row_count=len(asset.holdings),
    )


def _round(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "round_id": item.round_id,
        "security_code": item.security_code,
        "security_name": item.security_name,
        "close_date": iso(item.close_date),
        "pnl": str(Money(item.pnl_units)),
        "return_rate_pct": Ratio(item.return_rate_units).as_pct(2),
        "holding_days": item.holding_days,
    }
