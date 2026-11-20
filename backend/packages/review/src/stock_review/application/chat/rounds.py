"""`rounds_query` / `round_detail`：回合旗标 + 顶层旗标解释。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stock_contracts.chat.dto import ToolResult
from stock_contracts.chat.enums import ToolName
from stock_core.values import Money, Ratio

from stock_review.insights.service import get_insights_page, get_round_insight

from ._common import (
    ToolContext,
    flag_config_entry,
    flag_glossary_entry,
    iso,
    make_citation,
    ok,
    trade_flag_glossary_entry,
)


def _flag_glossary(entries: Any) -> list[dict[str, Any]]:
    return [flag_glossary_entry(item) for item in entries]


def _trade_flag_glossary(entries: Any) -> list[dict[str, Any]]:
    return [trade_flag_glossary_entry(item) for item in entries]


def _side(side: Any) -> dict[str, Any] | None:
    if side is None:
        return None
    return {
        "trade_id": side.trade_id,
        "date": iso(side.date),
        "price": side.price_display,
        "quantity": side.quantity_display,
        "amount": side.amount_display,
        "reason": side.reason.reason_text if side.reason is not None else None,
        "reason_label": side.reason_label,
        "trade_flags": [
            {
                "code": flag.code,
                "status": flag.status,
                "reason": flag.reason,
                "lookahead": flag.lookahead,
            }
            for flag in side.trade_flags
        ],
    }


async def run_list(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    flag = args.get("flag")
    sort = str(args.get("sort") or "combined")
    sort_dir = str(args.get("sort_dir") or "desc")
    page = await get_insights_page(
        ctx.session,
        ctx.user_id,
        market_data=ctx.market_data,
        clock=ctx.clock,
        period=args.get("period"),
        bucket=args.get("bucket"),
        flags=(str(flag),) if flag else (),
        security_code=str(args["security"]) if args.get("security") else None,
        sort=sort,
        sort_dir=sort_dir,
        include_summary=True,
        limit=max(1, min(int(args.get("limit") or 20), 20)),
        cursor=args.get("cursor"),
    )
    items = []
    citations = []
    for index, item in enumerate(page.items, start=1):
        flags = list(item.flags)
        items.append(
            {
                "round_id": item.round_id,
                "security_code": item.security_code,
                "security_name": item.security_name,
                "status": item.status,
                "open_date": iso(item.open_date),
                "close_date": iso(item.close_date),
                "holding_trading_days": item.holding_trading_days,
                "bucket": item.bucket,
                "pnl": str(Money(item.pnl_units)),
                "return_rate_pct": Ratio(item.return_rate_units).as_pct(2),
                "flags": flags,
                "status_counts": item.status_counts,
                "quality": item.quality,
                "rank": item.rank,
            }
        )
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=f"回合 {item.security_name} 盈亏",
            value=str(Money(item.pnl_units)),
            endpoint="GET /api/v1/rounds/insights",
            params={"period": page.period, "round_id": item.round_id},
            definition_version=page.definition_version,
            data_as_of=page.data_as_of,
        )
        if citation:
            citations.append(citation)
    data = {
        "period": page.period,
        "definition_version": page.definition_version,
        "bucket_basis": page.bucket_basis,
        "data_as_of": iso(page.data_as_of),
        "items": items,
        "flag_glossary": _flag_glossary(page.flag_glossary),
        "flag_config": flag_config_entry(page.flag_config),
        "summary": _summary(page.summary),
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
        "missing": page.missing,
    }
    return ok(
        ToolName.ROUNDS_QUERY,
        data=data,
        citations=citations,
        data_as_of=page.data_as_of,
        definition_version=page.definition_version,
        row_count=len(items),
    )


async def run_detail(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    round_id = str(args.get("round_id") or "")
    if not round_id:
        raise ValueError("round_detail 需要 round_id")
    detail = await get_round_insight(
        ctx.session,
        ctx.user_id,
        round_id=round_id,
        market_data=ctx.market_data,
        clock=ctx.clock,
    )
    citations = []
    pnl = str(Money(detail.pnl_units))
    citation = make_citation(
        prefix=prefix,
        index=1,
        label=f"回合 {detail.security_name} 盈亏",
        value=pnl,
        endpoint="GET /api/v1/rounds/{round_id}/insights",
        params={"round_id": round_id},
        definition_version=None,
        data_as_of=detail.close_date or detail.open_date,
    )
    if citation:
        citations.append(citation)
    data = {
        "round": {
            "round_id": detail.round_id,
            "security_code": detail.security_code,
            "security_name": detail.security_name,
            "status": detail.status,
            "open_date": iso(detail.open_date),
            "close_date": iso(detail.close_date),
            "holding_trading_days": detail.holding_trading_days,
            "bucket": detail.bucket,
            "pnl": pnl,
            "return_rate_pct": Ratio(detail.return_rate_units).as_pct(2),
        },
        "flags": detail.flags,
        "status_counts": detail.status_counts,
        "quality": detail.quality,
        "flag_glossary": _flag_glossary(detail.flag_glossary),
        "trade_flag_glossary": _trade_flag_glossary(detail.trade_flag_glossary),
        "flag_config": flag_config_entry(detail.flag_config),
        "fills": [
            {
                "fill_id": fill.get("fill_id"),
                "buy": _side(fill.get("buy")),
                "sell": _side(fill.get("sell")),
            }
            for fill in detail.fills
        ],
        "disclaimer": detail.disclaimer,
    }
    return ok(
        ToolName.ROUND_DETAIL,
        data=data,
        citations=citations,
        data_as_of=detail.close_date or detail.open_date,
        row_count=1,
    )


def _summary(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "swing": {
            "closed": summary.swing.closed,
            "scalp": summary.swing.scalp,
            "short": summary.swing.short,
            "position": summary.swing.position,
            "long": summary.swing.long,
            "win_rate_pct": summary.swing.win_rate_pct,
            "avg_holding_trading_days": summary.swing.avg_holding_trading_days,
            "avg_pnl": summary.swing.avg_pnl_display,
            "total_pnl": summary.swing.total_pnl_display,
        },
        "flag_round_counts": [
            {"flag": item.flag, "count": item.count} for item in summary.flag_round_counts
        ],
        "sell_fly": {
            "profit_take_fly": summary.sell_fly.profit_take_fly,
            "stop_loss_rebound": summary.sell_fly.stop_loss_rebound,
            "fly_pct": summary.sell_fly.fly_pct,
        },
    }
