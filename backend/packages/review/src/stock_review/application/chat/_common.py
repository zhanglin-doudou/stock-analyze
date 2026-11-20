"""助手工具公共类型/格式化（只读、JSON 友好、金额走标度整数）。"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.chat.dto import Citation, MissingEntry, ToolResult
from stock_contracts.chat.enums import ChatStatus, ToolName
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError
from stock_core.values import Money, Ratio, format_quantity
from stock_platform.config import Settings
from stock_platform.ports import MarketDataSource, SectorDataSource

from stock_review.metrics.context import Period, resolve_period


@dataclass(slots=True)
class ToolContext:
    session: AsyncSession
    user_id: uuid.UUID
    clock: Clock
    market_data: MarketDataSource
    sector_data: SectorDataSource
    settings: Settings


def money(units: int | None) -> str | None:
    return None if units is None else str(Money(units))


def pct(units: int | None, digits: int = 2) -> str | None:
    return None if units is None else Ratio(units).as_pct(digits)


def qty(units: int | None) -> str | None:
    return None if units is None else format_quantity(units)


def iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def period_of(ctx: ToolContext, args: Mapping[str, Any]) -> Period:
    return resolve_period(
        period=args.get("period"),
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
        today=ctx.clock.today(),
    )


def make_citation(
    *,
    prefix: str,
    index: int,
    label: str,
    value: str | None,
    endpoint: str,
    params: Mapping[str, Any],
    definition_version: str | None,
    data_as_of: date | None,
) -> Citation | None:
    if value is None:
        return None
    return Citation(
        citation_id=f"{prefix}{index}",
        label=label,
        value_display=value,
        source_endpoint=endpoint,
        source_params=dict(params),
        definition_version=definition_version,
        data_as_of=data_as_of,
    )


def ok(
    tool: ToolName,
    *,
    data: Mapping[str, Any],
    citations: list[Citation] | None = None,
    data_as_of: date | None = None,
    definition_version: str | None = None,
    missing: list[MissingEntry] | None = None,
    truncated: bool = False,
    row_count: int = 0,
) -> ToolResult:
    status = ChatStatus.DEGRADED if missing else ChatStatus.OK
    return ToolResult(
        tool=tool,
        status=status,
        data=data,
        citations=tuple(item for item in (citations or []) if item is not None),
        data_as_of=data_as_of,
        definition_version=definition_version,
        missing=tuple(missing or []),
        truncated=truncated,
        row_count=row_count,
    )


def failed(tool: ToolName, exc: Exception) -> ToolResult:
    if isinstance(exc, ApiError):
        code = exc.code.value
        message = exc.detail or exc.code.value
    else:
        code = "INTERNAL_ERROR"
        message = str(exc)[:200]
    return ToolResult(
        tool=tool,
        status=ChatStatus.ERROR,
        data={},
        missing=(MissingEntry(code=code, message=message),),
        error_code=code,
    )


def flag_glossary_entry(entry: Any) -> dict[str, Any]:
    return {
        "code": entry.code,
        "label_zh": entry.label_zh,
        "label_en": entry.label_en,
        "description_zh": entry.description_zh,
        "description_en": entry.description_en,
        "kind": getattr(entry, "kind", "item_flag"),
    }


def trade_flag_glossary_entry(entry: Any) -> dict[str, Any]:
    return {
        "code": entry.code,
        "label_zh": entry.label_zh,
        "label_en": entry.label_en,
        "description_zh": entry.description_zh,
        "description_en": entry.description_en,
        "side": entry.side,
        "lookahead": entry.lookahead,
    }


def flag_config_entry(config: Any) -> dict[str, Any] | None:
    if config is None:
        return None
    return {
        "window_trading_days": config.window_trading_days,
        "near_equal_tolerance_pct": config.near_equal_tolerance_pct,
        "chase_margin_pct": config.chase_margin_pct,
        "stop_loss_margin_pct": config.stop_loss_margin_pct,
        "short_term_margin_pct": config.short_term_margin_pct,
        "flags_hash": config.flags_hash,
        "is_default": config.is_default,
        "stale": config.stale,
    }
