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

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode
from stock_contracts.preferences import DEFAULT_FLAGS_HASH
from stock_core.insights.forward import ForwardTrade, price_net
from stock_core.metrics.portfolio_index import compound_index
from stock_core.trade_flags import (
    DEFINITION_VERSION,
    DISPLAY_STATUSES,
    STACKED_STATE_FLAG_CODES,
    STATUS_HIT,
    STATUS_NO_COST_BASIS,
    TRADE_FLAG_BY_CODE,
    TRADE_FLAG_CODES,
    TRADE_FLAG_DEFINITIONS,
    TREND_FLAG_CODES,
    TradeContext,
    TradeFlagDefinition,
    evaluate_trade_flags,
)
from stock_ledger.corporate_actions import is_corporate_action
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketDataSource

from stock_review.metrics.context import Period
from stock_review.metrics.datasets import DEFAULT_BENCHMARK, benchmark_fetch_symbol
from stock_review.models import AssetDaily, TradeFlag
from stock_review.repositories.assets import AssetRepository

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
            # `amount_units` 已是含费净现金流（解析层统一），不能再叠加费用字段。
            cost[trade.security_code] += abs(trade.amount_units)
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
                    # P0：构建仍按默认阈值（P1 接 `flag_config` 后改为传生效 hash）。
                    flags_hash=DEFAULT_FLAGS_HASH,
                    computed_at=now,
                )
            )

    if not dry_run:
        # 重建即覆盖：连同旧 definition_version 的行一起清掉，保证库内该月只有一套口径。
        await session.execute(
            delete(TradeFlag).where(
                TradeFlag.user_id == user_id,
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


@dataclass(frozen=True, slots=True)
class MonthlyFlagCount:
    """一个完整月的账单总数 + 折线趋势旗标 + 柱状图互斥状态旗标命中数。"""

    month: str
    buy_count: int
    sell_count: int
    flag_counts: dict[str, int]
    state_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class FlagSeries:
    """看板图表的旗标系列元数据（标签/方向/事后属性由后端单点提供）。"""

    code: str
    label_zh: str
    label_en: str
    side: str
    lookahead: bool


@dataclass(frozen=True, slots=True)
class MonthlyReturn:
    """一个完整月的组合 TWR（资产日表逐日收益按月复利，scale=8）+ 当月收益额（scale=2）。

    `value_units is None` 表示当月没有任何可用日收益（不是 0 收益）；调用方据此显式
    渲染 0 高度并标注「无数据」，禁止用 0 冒充。

    `amount_units` = 当月每日 `daily_pnl_units` 的累加（**已剔除出入金**：资产日表的
    daily_pnl 只剔除银证转账，分红/打新等投资损益仍计入）；无数据时同样为 None。
    这与 `value_units` 同源同日行，partial 月只累加已覆盖日期，不会跨缺口放大。
    """

    month: str
    value_units: int | None
    amount_units: int | None
    days: int
    partial: bool


@dataclass(frozen=True, slots=True)
class TradeFlagMonthlySummary:
    window_months: int
    start_month: str
    end_month: str
    latest_complete_month: str
    items: tuple[MonthlyFlagCount, ...]
    series: tuple[FlagSeries, ...]
    state_series: tuple[FlagSeries, ...]
    months_without_data: tuple[str, ...]
    has_data: bool
    has_flag_data: bool
    has_state_data: bool
    definition_version: str
    returns: tuple[MonthlyReturn, ...]
    has_return_data: bool


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def _complete_month_windows(today: date, window_months: int) -> list[tuple[str, date, date]]:
    """最近 N 个「完整月」窗口（含起止日）。

    「完整月」= 已自然结束的日历月；以服务端业务日（东八区）为准，
    最新完整月固定是今天所在月的前一个月。区间按时间升序返回。
    """
    last_year, last_month = _shift_month(today.year, today.month, -1)
    windows: list[tuple[str, date, date]] = []
    for offset in range(window_months - 1, -1, -1):
        year, month = _shift_month(last_year, last_month, -offset)
        next_year, next_month = _shift_month(year, month, 1)
        windows.append(
            (
                f"{year:04d}-{month:02d}",
                date(year, month, 1),
                date(next_year, next_month, 1) - timedelta(days=1),
            )
        )
    return windows


def _last_weekday(day: date) -> date:
    """自然月最后一天 → 最后一个工作日（资产表按工作日逐日落行）。"""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


async def monthly_trade_flag_summary(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    window_months: int,
    clock: Clock,
) -> TradeFlagMonthlySummary:
    """看板聚合：最近 N 个完整月的买卖笔数、趋势旗标、互斥状态旗标命中数与单月收益。

    口径：
    * 只统计当前 `definition_version` 的落库结果（不做重算）；
    * 买卖笔数 = 当月按 `trade_dedupe_key` 去重后的账单数；
    * 旗标/状态数量 = 当月 `status='hit'` 的账单数（同一账单命中多个旗标会分别计数）；
    * 折线口径 `TREND_FLAG_CODES`，柱状图口径 `STACKED_STATE_FLAG_CODES`；
    * 单月收益 = 当前资产口径版本的 `asset_daily.daily_return_units` 在自然月内复利
      （TWR，出入金已剔除）；无可用日收益的月份 `value_units=None`，由 API 层显式 0 高度；
    * 没有旗标记录的月份显式 0，并在 `months_without_data` 中列出（可能未构建）。
    """
    if window_months not in (6, 12, 24):
        raise ValueError(f"window_months 只支持 6/12/24，收到 {window_months}")
    windows = _complete_month_windows(clock.today(), window_months)
    period_start, period_end = windows[0][1], windows[-1][2]
    month_expr = func.to_char(TradeFlag.trade_date, "YYYY-MM")

    base_where = (
        TradeFlag.user_id == user_id,
        TradeFlag.definition_version == DEFINITION_VERSION,
        TradeFlag.trade_date >= period_start,
        TradeFlag.trade_date <= period_end,
    )
    total_rows = await session.execute(
        select(month_expr, TradeFlag.side, func.count(distinct(TradeFlag.trade_dedupe_key)))
        .where(*base_where)
        .group_by(month_expr, TradeFlag.side)
    )
    totals: dict[tuple[str, str], int] = {
        (str(month), str(side)): int(count) for month, side, count in total_rows.all()
    }
    flag_rows = await session.execute(
        select(
            month_expr,
            TradeFlag.flag_code,
            func.count(distinct(TradeFlag.trade_dedupe_key)),
        )
        .where(
            *base_where,
            TradeFlag.status == STATUS_HIT,
            TradeFlag.flag_code.in_(TREND_FLAG_CODES + STACKED_STATE_FLAG_CODES),
        )
        .group_by(month_expr, TradeFlag.flag_code)
    )
    code_counts: dict[tuple[str, str], int] = {
        (str(month), str(code)): int(count) for month, code, count in flag_rows.all()
    }

    asset_state = await AssetRepository(session).get_state(user_id)
    daily_returns: dict[str, list[int]] = defaultdict(list)
    monthly_amount: dict[str, int] = defaultdict(int)
    if asset_state is not None:
        asset_rows = await session.execute(
            select(
                AssetDaily.trade_date,
                AssetDaily.daily_return_units,
                AssetDaily.daily_pnl_units,
            )
            .where(
                AssetDaily.user_id == user_id,
                AssetDaily.definition_version == asset_state.definition_version,
                AssetDaily.trade_date >= period_start,
                AssetDaily.trade_date <= period_end,
                AssetDaily.status.in_(("ok", "degraded")),
                AssetDaily.daily_return_units.is_not(None),
            )
            .order_by(AssetDaily.trade_date)
        )
        for trade_date, return_units, pnl_units in asset_rows.all():
            if return_units is None:  # SQL 已过滤，显式收窄供 mypy
                continue
            month_key = trade_date.strftime("%Y-%m")
            daily_returns[month_key].append(int(return_units))
            if pnl_units is not None:
                # 收益额 = 逐日 daily_pnl 累加；daily_pnl 已剔除银证转账出入金。
                monthly_amount[month_key] += int(pnl_units)

    items: list[MonthlyFlagCount] = []
    months_without_data: list[str] = []
    for month, _start, _end in windows:
        buy_count = totals.get((month, "buy"), 0)
        sell_count = totals.get((month, "sell"), 0)
        if buy_count == 0 and sell_count == 0:
            months_without_data.append(month)
        items.append(
            MonthlyFlagCount(
                month=month,
                buy_count=buy_count,
                sell_count=sell_count,
                flag_counts={code: code_counts.get((month, code), 0) for code in TREND_FLAG_CODES},
                state_counts={
                    code: code_counts.get((month, code), 0) for code in STACKED_STATE_FLAG_CODES
                },
            )
        )

    def _series(codes: tuple[str, ...]) -> tuple[FlagSeries, ...]:
        return tuple(
            FlagSeries(
                code=code,
                label_zh=definition.label_zh,
                label_en=definition.label_en,
                side=definition.side,
                lookahead=definition.lookahead,
            )
            for code in codes
            for definition in (TRADE_FLAG_BY_CODE[code],)
        )

    returns: list[MonthlyReturn] = []
    for month, _start, month_end in windows:
        values = daily_returns.get(month, [])
        covered_through = asset_state.last_built_through if asset_state is not None else None
        returns.append(
            MonthlyReturn(
                month=month,
                value_units=compound_index(values) if values else None,
                amount_units=monthly_amount.get(month) if values else None,
                days=len(values),
                partial=bool(values)
                and covered_through is not None
                and covered_through < _last_weekday(month_end),
            )
        )

    return TradeFlagMonthlySummary(
        window_months=window_months,
        start_month=windows[0][0],
        end_month=windows[-1][0],
        latest_complete_month=windows[-1][0],
        items=tuple(items),
        series=_series(TREND_FLAG_CODES),
        state_series=_series(STACKED_STATE_FLAG_CODES),
        months_without_data=tuple(months_without_data),
        has_data=any(item.buy_count or item.sell_count for item in items),
        has_flag_data=any(sum(item.flag_counts.values()) > 0 for item in items),
        has_state_data=any(sum(item.state_counts.values()) > 0 for item in items),
        definition_version=DEFINITION_VERSION,
        returns=tuple(returns),
        has_return_data=any(item.value_units is not None for item in returns),
    )


__all__ = [
    "BuildResult",
    "FlagSeries",
    "MonthlyFlagCount",
    "MonthlyReturn",
    "TradeFlagMonthlySummary",
    "TradeFlagRecord",
    "build_trade_flags_for_user",
    "load_trade_flags",
    "monthly_trade_flag_summary",
    "trade_flag_glossary",
]
