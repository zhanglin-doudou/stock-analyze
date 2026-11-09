"""账单级旗标应用服务：按月批量构建 + 详情页读取。

判定在 `stock_core.trade_flags`（纯函数）；本层只负责取数、持仓推演、幂等落库。
构建范围 = 目标月份内所有 buy/sell 流水（排除公司行动行），持仓成本按全历史推演。
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.insights.forward import ForwardTrade, price_net
from stock_core.trade_flags import (
    DEFINITION_VERSION,
    DISPLAY_STATUSES,
    STATUS_NO_COST_BASIS,
    TRADE_FLAG_CODES,
    TRADE_FLAG_DEFINITIONS,
    TradeContext,
    TradeFlagDefinition,
    evaluate_trade_flags,
)
from stock_ledger.corporate_actions import is_corporate_action
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketDataSource

from stock_review.metrics.context import Period
from stock_review.metrics.datasets import DEFAULT_BENCHMARK, benchmark_fetch_symbol
from stock_review.models import TradeFlag

# 取数窗口：目标月前后各留 45 个自然日，覆盖 5 个行情日 + 长假。
_BUILD_WINDOW_PADDING = timedelta(days=45)
_CODES_BY_SIDE: dict[str, tuple[str, ...]] = {
    "buy": tuple(item.code for item in TRADE_FLAG_DEFINITIONS if item.side == "buy"),
    "sell": tuple(item.code for item in TRADE_FLAG_DEFINITIONS if item.side == "sell"),
}


@dataclass(slots=True)
class BuildResult:
    trades: int = 0
    rows: int = 0
    dry_run: bool = False
    status_counts: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class TradeFlagRecord:
    code: str
    status: str
    reason: str | None
    lookahead: bool


def trade_flag_glossary() -> list[TradeFlagDefinition]:
    """账单旗标词表（中英文案单点维护）。"""
    return list(TRADE_FLAG_DEFINITIONS)


async def _fetch_bars(
    market_data: MarketDataSource,
    symbols: Sequence[str],
    *,
    start: date,
    end: date,
) -> list[Bar]:
    """批量取日线；单标的 `NOT_FOUND` 逐只降级（与洞察取数同口径）。"""
    try:
        return list(await market_data.get_daily_bars(symbols, start, end))
    except ApiError as exc:
        if exc.code != ErrorCode.NOT_FOUND:
            raise
    collected: list[Bar] = []
    for symbol in symbols:
        try:
            collected.extend(await market_data.get_daily_bars([symbol], start, end))
        except ApiError as exc:
            if exc.code != ErrorCode.NOT_FOUND:
                raise
    return collected


def _trade_order_key(trade: Trade) -> tuple[date, int, str, int, int, str]:
    """与 FIFO/回合洞察一致的稳定序（同日以文件行号破平局）。"""
    return (
        trade.trade_date,
        trade.line_no or 0,
        trade.summary or "",
        trade.quantity_units,
        trade.price_units,
        trade.id,
    )


@dataclass(slots=True)
class _PositionState:
    qty_before: int = 0
    avg_cost_before: int | None = None


def _position_states(trades: Sequence[Trade]) -> dict[str, _PositionState]:
    """按稳定序推演含费加权平均成本（scale=1e6）；公司行动增加份额、不增成本。"""
    states: dict[str, _PositionState] = {}
    qty: dict[str, int] = defaultdict(int)
    cost: dict[str, int] = defaultdict(int)
    for trade in sorted(trades, key=_trade_order_key):
        held = qty[trade.security_code]
        states[trade.id] = _PositionState(
            qty_before=held,
            avg_cost_before=(cost[trade.security_code] * 10**8 // held) if held > 0 else None,
        )
        if trade.side == "buy" and trade.quantity_units > 0:
            qty[trade.security_code] += trade.quantity_units
            cost[trade.security_code] += (
                abs(trade.amount_units)
                + trade.commission_units
                + trade.stamp_tax_units
                + trade.other_fee_units
            )
        elif trade.side == "sell" and trade.quantity_units > 0:
            if held > 0:
                sold = min(trade.quantity_units, held)
                cost[trade.security_code] -= cost[trade.security_code] * sold // held
                qty[trade.security_code] = held - sold
        elif trade.quantity_units > 0 and trade.amount_units == 0:
            qty[trade.security_code] += trade.quantity_units
    return states


def _is_analyzed_trade(trade: Trade) -> bool:
    return (
        trade.side in ("buy", "sell")
        and trade.quantity_units > 0
        and not is_corporate_action(
            category=trade.category,
            summary=trade.summary,
            quantity_units=trade.quantity_units,
            amount_units=trade.amount_units,
        )
    )


def _flag_id(user_id: uuid.UUID, dedupe_key: str, code: str) -> str:
    raw = f"{user_id}|{dedupe_key}|{code}|{DEFINITION_VERSION}".encode()
    return "tfl_" + hashlib.sha256(raw).hexdigest()[:32]


def _net_price(trade: Trade) -> int | None:
    return price_net(
        ForwardTrade(
            dedupe_key=trade.dedupe_key,
            trade_id=trade.id,
            version=trade.version,
            side=trade.side,
            d0=trade.trade_date,
            price_units=trade.price_units,
            amount_units=trade.amount_units,
            quantity_units=trade.quantity_units,
        )
    )


async def build_trade_flags_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    period: Period,
    market_data: MarketDataSource,
    clock: Clock,
    dry_run: bool = False,
) -> BuildResult:
    """构建某用户某月的账单旗标；同月同版本先删后插，重跑幂等。"""
    trades = list(
        await session.scalars(
            select(Trade)
            .where(
                Trade.user_id == user_id,
                Trade.deleted_at.is_(None),
                Trade.trade_date <= period.end,
            )
            .order_by(Trade.trade_date, Trade.line_no, Trade.id)
        )
    )
    month_trades = [
        trade
        for trade in trades
        if period.start <= trade.trade_date <= period.end and _is_analyzed_trade(trade)
    ]
    if not month_trades:
        return BuildResult(trades=0, rows=0, dry_run=dry_run, status_counts={})

    states = _position_states(trades)
    bench_symbol = benchmark_fetch_symbol(DEFAULT_BENCHMARK, market_data)
    symbols = sorted({trade.security_code for trade in month_trades} | {bench_symbol})
    bars = await _fetch_bars(
        market_data,
        symbols,
        start=period.start - _BUILD_WINDOW_PADDING,
        end=period.end + _BUILD_WINDOW_PADDING,
    )
    grouped: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol].append((bar.trade_date, bar.close_units))
    bench_bars = sorted(grouped.get(bench_symbol, ()))
    calendar = tuple(day for day, _ in bench_bars)
    data_as_of = max((bar.trade_date for bar in bars), default=None)

    now = clock.now()
    rows: list[TradeFlag] = []
    counts: Counter[str] = Counter()
    for trade in month_trades:
        state = states[trade.id]
        net_price = _net_price(trade)
        results: list[tuple[str, str, str | None]]
        if net_price is None:
            results = [
                (code, STATUS_NO_COST_BASIS, "no_net_price") for code in _CODES_BY_SIDE[trade.side]
            ]
        else:
            evaluated = evaluate_trade_flags(
                TradeContext(
                    side=trade.side,
                    trade_date=trade.trade_date,
                    net_price_units=net_price,
                    quantity_units=trade.quantity_units,
                    quantity_before_units=state.qty_before,
                    avg_cost_units=state.avg_cost_before,
                ),
                bars=grouped.get(trade.security_code, ()),
                calendar=calendar,
                data_as_of=data_as_of,
            )
            results = [(item.code, item.status, item.reason) for item in evaluated]
        for code, status, reason in results:
            counts[status] += 1
            rows.append(
                TradeFlag(
                    id=_flag_id(user_id, trade.dedupe_key, code),
                    user_id=user_id,
                    trade_dedupe_key=trade.dedupe_key,
                    trade_id=trade.id,
                    security_code=trade.security_code,
                    side=trade.side,
                    trade_date=trade.trade_date,
                    flag_code=code,
                    status=status,
                    reason=reason,
                    lookahead=next(
                        item.lookahead for item in TRADE_FLAG_DEFINITIONS if item.code == code
                    ),
                    definition_version=DEFINITION_VERSION,
                    computed_at=now,
                )
            )

    if not dry_run:
        await session.execute(
            delete(TradeFlag).where(
                TradeFlag.user_id == user_id,
                TradeFlag.definition_version == DEFINITION_VERSION,
                TradeFlag.trade_date >= period.start,
                TradeFlag.trade_date <= period.end,
            )
        )
        session.add_all(rows)
    return BuildResult(
        trades=len(month_trades),
        rows=len(rows),
        dry_run=dry_run,
        status_counts=dict(counts),
    )


async def load_trade_flags(
    session: AsyncSession,
    user_id: uuid.UUID,
    dedupe_keys: Iterable[str],
) -> dict[str, list[TradeFlagRecord]]:
    """按逻辑键批量读取当前版本的账单旗标（仅返回可展示状态）。"""
    keys = list(dict.fromkeys(dedupe_keys))
    if not keys:
        return {}
    rows = await session.scalars(
        select(TradeFlag).where(
            TradeFlag.user_id == user_id,
            TradeFlag.definition_version == DEFINITION_VERSION,
            TradeFlag.trade_dedupe_key.in_(keys),
            TradeFlag.status.in_(DISPLAY_STATUSES),
        )
    )
    order = {code: index for index, code in enumerate(TRADE_FLAG_CODES)}
    grouped: dict[str, list[TradeFlagRecord]] = {}
    for row in rows:
        grouped.setdefault(row.trade_dedupe_key, []).append(
            TradeFlagRecord(
                code=row.flag_code,
                status=row.status,
                reason=row.reason,
                lookahead=row.lookahead,
            )
        )
    for records in grouped.values():
        records.sort(key=lambda item: order[item.code])
    return grouped


__all__ = [
    "BuildResult",
    "TradeFlagRecord",
    "build_trade_flags_for_user",
    "load_trade_flags",
    "trade_flag_glossary",
]
