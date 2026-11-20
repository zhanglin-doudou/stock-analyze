"""`trades_query`：流水 + 账单旗标 + 顶层旗标解释。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ToolName
from stock_contracts.enums import BILL_CATEGORY_LABELS
from stock_core.trade_flags import TradeFlagConfig
from stock_core.values import Money, Price, format_quantity
from stock_ledger.repositories.trades import TradeRepository

from stock_review.trade_flags.service import (
    has_stale_flags,
    load_flag_config,
    load_trade_flags,
    trade_flag_glossary,
)

from ._common import ToolContext, iso, make_citation, ok, period_of


async def run(ctx: ToolContext, args: Mapping[str, Any], *, prefix: str) -> ToolResult:
    resolved = period_of(ctx, args)
    limit = max(1, min(int(args.get("limit") or 50), 50))
    rows, next_cursor = await TradeRepository(ctx.session).page(
        ctx.user_id,
        cursor=args.get("cursor"),
        limit=limit,
        security_code=str(args["security"]) if args.get("security") else None,
        side=str(args["side"]) if args.get("side") else None,
        date_from=resolved.start,
        date_to=resolved.end,
        category=str(args["category"]) if args.get("category") else None,
    )
    config, flags_hash = await load_flag_config(ctx.session, ctx.user_id)
    flag_map = await load_trade_flags(
        ctx.session,
        ctx.user_id,
        [row.dedupe_key for row in rows],
        flags_hash=flags_hash,
    )
    stale = await has_stale_flags(
        ctx.session,
        ctx.user_id,
        [row.dedupe_key for row in rows],
        flags_hash=flags_hash,
    )
    glossary = list(trade_flag_glossary(config))
    counts: Counter[str] = Counter()
    items: list[dict[str, Any]] = []
    citations = []
    for index, row in enumerate(rows, start=1):
        flags = []
        for record in flag_map.get(row.dedupe_key, []):
            definition = next((item for item in glossary if item.code == record.code), None)
            counts[record.code] += 1
            flags.append(
                {
                    "code": record.code,
                    "label_zh": definition.label_zh if definition else record.code,
                    "status": record.status,
                    "reason": record.reason,
                    "lookahead": record.lookahead,
                }
            )
        amount = str(Money(row.amount_units))
        item = {
            "trade_id": row.id,
            "trade_date": iso(row.trade_date),
            "security_code": row.security_code,
            "security_name": row.security_name,
            "side": row.side,
            "category": row.category,
            "category_label": BILL_CATEGORY_LABELS.get(row.category or "", "未分类"),
            "quantity": format_quantity(row.quantity_units),
            "price": str(Price(row.price_units)),
            "amount": amount,
            "commission": str(Money(row.commission_units)),
            "stamp_tax": str(Money(row.stamp_tax_units)),
            "other_fee": str(Money(row.other_fee_units)),
            "summary": row.summary,
            "flags": flags,
            "flag_stale": stale,
        }
        items.append(item)
        citation = make_citation(
            prefix=prefix,
            index=index,
            label=f"{row.trade_date} {row.security_name} 金额",
            value=amount,
            endpoint="GET /api/v1/trades",
            params={"period": resolved.label, "security": row.security_code},
            definition_version=None,
            data_as_of=row.trade_date,
        )
        if citation:
            citations.append(citation)

    missing: list[MissingEntry] = []
    if stale:
        missing.append(
            MissingEntry(
                code="FLAG_STALE",
                message="部分流水旗标是旧阈值生成，计数可能不是当前口径。",
                what_to_do="到设置页点「立即重算」后重试。",
            )
        )
    data = {
        "period": resolved.label,
        "items": items,
        "flag_glossary": [
            {
                "code": item.code,
                "label_zh": item.label_zh,
                "label_en": item.label_en,
                "description_zh": item.description_zh,
                "description_en": item.description_en,
                "side": item.side,
                "lookahead": item.lookahead,
            }
            for item in glossary
        ],
        "flag_config": _config_dict(config, flags_hash),
        "flag_summary": dict(counts),
        "flag_stale": stale,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }
    return ok(
        ToolName.TRADES_QUERY,
        data=data,
        citations=citations,
        data_as_of=resolved.end,
        missing=missing,
        row_count=len(items),
    )


def _bps_pct(value: int) -> str:
    return f"{value // 100}.{value % 100:02d}%"


def _config_dict(config: TradeFlagConfig, flags_hash: str) -> dict[str, Any]:
    return {
        "window_trading_days": str(config.window_trading_days),
        "near_equal_tolerance_pct": _bps_pct(config.near_equal_tolerance_bps),
        "chase_margin_pct": _bps_pct(config.chase_margin_bps),
        "stop_loss_margin_pct": _bps_pct(config.stop_loss_margin_bps),
        "short_term_margin_pct": _bps_pct(config.short_term_margin_bps),
        "flags_hash": flags_hash,
    }
