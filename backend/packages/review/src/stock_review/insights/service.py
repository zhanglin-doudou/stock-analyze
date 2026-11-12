"""回合洞察应用服务（API-092/093/094，设计 §3.4–§3.7）。

原则：
- 读路径**不写库**；前瞻收益优先读缓存，缓存缺失/指纹过期时在内存里现算；
- 日历/基准缺失 → 显式 status + null，不显示 0；
- 金额/比率全部标度整数，展示字符串只在 API 边界层生成。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import ForwardReturnStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.insights import DEFINITION_VERSION, classify
from stock_core.insights import aggregate as agg
from stock_core.insights import calendar as cal
from stock_core.insights.flags import FLAG_DEFINITIONS, FlagDefinition
from stock_core.insights.forward import ForwardResult, ForwardTrade, compute_forward, price_net
from stock_core.trade_flags import TradeFlagDefinition
from stock_core.values import Money, Price, Ratio, format_quantity
from stock_ledger.corporate_actions import is_corporate_action
from stock_ledger.models import Trade
from stock_platform.ids import new_id
from stock_platform.ports import Bar, MarketDataSource
from stock_platform.symbols import classify_symbol, is_convertible_bond
from stock_review.insights import reasons
from stock_review.insights.reasons import REASON_LABELS, ReasonDTO
from stock_review.metrics.context import Period, parse_period_label
from stock_review.metrics.datasets import DEFAULT_BENCHMARK, benchmark_fetch_symbol
from stock_review.models import Round, RoundFill, TradeForwardReturn, TradeReason
from stock_review.trade_flags import (
    TradeFlagRecord,
    load_trade_flags,
    trade_flag_glossary,
)

HORIZONS: tuple[int, ...] = (5, 10, 20)
_ADDON_LOOKBACK_TRADING_DAYS = 60

# 证券类型筛选词表（设计 §6.3 `security_type`）：四类互斥且覆盖全部回合。
SECURITY_TYPE_STOCK = "stock"
SECURITY_TYPE_FUND = "fund"
SECURITY_TYPE_CONVERTIBLE_BOND = "convertible_bond"
SECURITY_TYPE_OTHER = "other"
SECURITY_TYPES: tuple[str, ...] = (
    SECURITY_TYPE_STOCK,
    SECURITY_TYPE_FUND,
    SECURITY_TYPE_CONVERTIBLE_BOND,
    SECURITY_TYPE_OTHER,
)

# 盈亏分类标签（图表图例/坐标轴文案由后端给，前端不维护同义文案）。
PNL_CLASS_LABELS: dict[str, str] = {
    agg.PNL_CLASS_LOSS: "亏损",
    agg.PNL_CLASS_FLAT: "保本",
    agg.PNL_CLASS_WIN: "盈利",
}


@dataclass(slots=True)
class ForwardView:
    horizon: int
    status: str
    base_shifted: bool
    b0_pct: str | None
    b0_net_pct: str | None
    b1_pct: str | None
    b2_pct: str | None
    b3_pct: str | None
    max_close_display: str | None
    min_close_display: str | None
    price_jump_review: bool
    ret_b3_units: int | None = None
    ret_b2_units: int | None = None


@dataclass(slots=True)
class TradeSideView:
    trade_id: str
    date: date
    price_display: str | None
    quantity_display: str | None
    amount_display: str | None
    reason: ReasonDTO | None
    reason_label: str | None
    forward: dict[int, ForwardView] = field(default_factory=dict)
    trade_flags: list[TradeFlagRecord] = field(default_factory=list)


@dataclass(slots=True)
class ReasonAggregate:
    code: str
    label: str
    count: int
    share_pct: str


@dataclass(slots=True)
class InsightItem:
    round_id: str
    security_code: str
    security_name: str
    status: str
    open_date: date
    close_date: date | None
    holding_days_legacy: int
    holding_trading_days: int | None
    bucket: str
    pnl_units: int
    return_rate_units: int
    buy_trade: TradeSideView | None
    sell_trade: TradeSideView | None
    buy_excess_units: int | None
    sell_avoid_units: int | None
    combined_units: int | None
    rank: int | None
    quadrant: str | None
    flags: list[str]
    quality: list[str]
    missed_profit_worst_units: int | None
    missed_profit_end_units: int | None
    missed_profit_worst_abs_display: str | None = None
    missed_profit_worst_label: str | None = None
    missed_profit_worst_label_en: str | None = None
    missed_profit_end_abs_display: str | None = None
    missed_profit_end_label: str | None = None
    missed_profit_end_label_en: str | None = None
    # 非序列化：加仓笔的 20 日 B2（仅 status=ok 时非空），供 `summary.addon` 取均值。
    addon_b2_units: int | None = None


@dataclass(slots=True)
class SummaryBucket:
    closed: int
    scalp: int
    short: int
    position: int
    long: int
    avg_holding_trading_days: str | None
    win_rate_pct: str | None
    avg_pnl_display: str | None
    total_pnl_display: str | None


@dataclass(slots=True)
class FillSwingBucket:
    """fill 级波段配对（设计 §3.4）；统计单位是 fill，与回合级 `swing` 不可相加。"""

    pairs: int
    win_rate_pct: str | None
    avg_holding_trading_days: str | None
    avg_pnl_display: str | None
    total_pnl_display: str | None


@dataclass(slots=True)
class PnlHoldingClass:
    """单类盈亏的持有期（标签/容差由后端给，前端只渲染）。"""

    code: str
    label: str
    count: int
    avg_holding_trading_days: str | None
    avg_holding_trading_days_x10: int | None


@dataclass(slots=True)
class PnlHoldingSummary:
    """盈亏 × 持有期（`/rounds/insights` 持有期分布图表的数据源）。

    「保本」判定容差在后端常量 `agg.PNL_FLAT_TOLERANCE_UNITS`（默认 100.00 元），
    原样透出 `flat_tolerance_display`，避免前端重复判定口径。
    """

    flat_tolerance_display: str
    classes: list[PnlHoldingClass]


@dataclass(slots=True)
class RankingSummary:
    n_sample: int
    n_eligible: int
    sample_status: str
    combined_median_pct: str | None
    combined_avg_pct: str | None
    quadrant_counts: dict[str, int]
    per_stock: list[dict[str, Any]]
    basis: str = agg.RANKING_BASIS
    definition_version: str = DEFINITION_VERSION


@dataclass(slots=True)
class ReasonCoverage:
    annotated: int
    total_rounds: int
    coverage_pct: str | None
    by_code: list[ReasonAggregate]


@dataclass(slots=True)
class AddonSummary:
    """加仓（含补仓/抄底）统计（设计 §6.3）。

    `count` 与兼容字段 `rounds` 同值；`avg_dip_pct` 是加仓笔次 20 日 B2 的均值，
    只对有 `status=ok` 的样本取均值，`avg_dip_coverage` 显式给出「有效/总数」。
    """

    count: int
    declined_after_addon: int
    declined_pct: str | None
    avg_dip_pct: str | None
    avg_dip_coverage: str
    rounds: int
    share_pct: str | None


@dataclass(slots=True)
class SellFlySummary:
    """卖飞统计：只算止盈型卖飞（`profit_take_fly`），止损后反弹单列不并入。"""

    sell_count: int
    profit_take_fly: int
    stop_loss_rebound: int
    fly_pct: str | None
    missed_profit_worst_display: str | None
    missed_profit_end_display: str | None
    rounds: int
    share_pct: str | None
    missed_profit_worst_abs_display: str | None = None
    missed_profit_worst_label: str | None = None
    missed_profit_worst_label_en: str | None = None
    missed_profit_end_abs_display: str | None = None
    missed_profit_end_label: str | None = None
    missed_profit_end_label_en: str | None = None


@dataclass(slots=True)
class InsightsSummary:
    swing: SummaryBucket
    fill_swing: FillSwingBucket
    holding_by_pnl: PnlHoldingSummary
    addon: AddonSummary
    sell_fly: SellFlySummary
    reason_coverage: ReasonCoverage
    ranking: RankingSummary
    pending: dict[str, Any]


@dataclass(slots=True)
class InsightsPage:
    period: str
    period_basis: str
    definition_version: str
    bucket_basis: str
    data_as_of: date | None
    ranking_as_of: date | None
    benchmark_id: str
    basis: str
    item_total: int
    summary: InsightsSummary | None
    items: list[InsightItem]
    next_cursor: str | None
    has_more: bool
    missing: list[str]
    assumptions: list[str]
    disclaimer: str
    flag_glossary: list[FlagDefinition] = field(default_factory=list)


@dataclass(slots=True)
class RoundInsightDetail:
    round_id: str
    security_code: str
    security_name: str
    status: str
    open_date: date
    close_date: date | None
    holding_days_legacy: int
    holding_trading_days: int | None
    bucket: str
    pnl_units: int
    return_rate_units: int
    buy_trade: TradeSideView | None
    sell_trade: TradeSideView | None
    fills: list[dict[str, Any]]
    flags: list[str]
    quality: list[str]
    ranking: dict[str, Any]
    disclaimer: str
    flag_glossary: list[FlagDefinition] = field(default_factory=list)
    trade_flag_glossary: list[TradeFlagDefinition] = field(default_factory=list)


@dataclass(slots=True)
class RefreshResult:
    computed: int
    skipped: int
    pending: int
    no_calendar: int
    no_bar: int
    corporate_action: int
    divisor_missing: int
    stale_price: int
    data_as_of: date | None


DISCLAIMER = (
    "以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益；"
    "相对基准为 hs300_tr ETF 代理；缺失/公司行动显示 —，不显示 0%。"
)

CURSOR_BASE = "/api/v1/rounds/insights"
_SORT_FIELDS = {
    "combined": "combined_units",
    "buy_excess": "buy_excess_units",
    "sell_avoid": "sell_avoid_units",
    "pnl": "pnl_units",
    "close_date": "close_date",
}


def _pct(units: int | None) -> str | None:
    return None if units is None else Ratio(units).as_pct(2)


def _money(units: int | None) -> str | None:
    return None if units is None else str(Money(units))


def _days_x10_display(x10: int | None) -> str | None:
    """交易日 × 10 定点整数 → 一位小数展示串（整数拆分，不引入 float）。"""
    return None if x10 is None else f"{x10 // 10}.{x10 % 10}"


def _missed_presentation(units: int | None) -> tuple[str | None, str | None, str | None]:
    """卖飞金额 → (绝对值展示串, 中文措辞, 英文措辞)。

    符号 → 措辞（与归因 `factor_conclusion` 同思路）：非负表示「本该赚到却没赚到」→
    少赚；负值表示卖后下跌、卖出反而避免了回撤 → 多赚。零归入「少赚」侧（零既不是
    额外收益，也不宜渲染成「多赚」，取保守措辞）。`None` 三项全空，前端只渲染「—」。
    """
    if units is None:
        return None, None, None
    label, label_en = ("少赚", "missed gain") if units >= 0 else ("多赚", "extra gain")
    return str(Money(abs(units))), label, label_en


def _price_6dp(units: int | None) -> str | None:
    """1e-6 价格标度整数 → 6 位小数展示串（成交价口径）。"""
    return None if units is None else str(Price(units))


_CLOSE_DISPLAY_QUANT = Decimal("0.01")


def _price_2dp(units: int | None) -> str | None:
    """1e-6 收盘价标度整数 → 元、2 位小数展示串（收盘价口径）。

    不能用 `_money`：`Money` 的 scale=2 会把 1e-6 单位当成「分」，数值放大 10⁴。
    """
    if units is None:
        return None
    value = Price(units).to_decimal().quantize(_CLOSE_DISPLAY_QUANT, rounding=ROUND_HALF_UP)
    return f"{value:f}"


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": str(offset)}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return int(data["o"])
    except (binascii.Error, ValueError, UnicodeDecodeError, KeyError, TypeError) as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标格式错误") from exc


def _resolve_period(period: str | None, clock: Clock) -> Period:
    label = period or clock.today().strftime("%Y-%m")
    return parse_period_label(label, today=clock.today())


def _bar_version(bars: Sequence[Bar]) -> str:
    """区间内 daily_bar 的等价版本量：最大交易日 + 条数 + 内容摘要。

    行情端口没有 `updated_at`，用 `(max(trade_date), count, 内容 sha256)` 等价表达
    “区间内新增/修订”：任一日线新增、删除或收盘价修订都会改变摘要。
    """
    if not bars:
        return "empty"
    digest = hashlib.sha256()
    for bar in sorted(bars, key=lambda item: (item.symbol, item.trade_date)):
        digest.update(f"{bar.symbol}:{bar.trade_date.isoformat()}:{bar.close_units};".encode())
    latest = max(bar.trade_date for bar in bars)
    return f"{latest.isoformat()}|{len(bars)}|{digest.hexdigest()}"


def _fingerprint(calendar: Sequence[date], bars_version: str, benchmark_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(benchmark_id.encode())
    digest.update(
        f"|{calendar[0] if calendar else ''}|{calendar[-1] if calendar else ''}|"
        f"{bars_version}|".encode()
    )
    return digest.hexdigest()


def _to_view(result: ForwardResult) -> ForwardView:
    return ForwardView(
        horizon=result.horizon,
        status=result.status,
        base_shifted=result.base_shifted,
        b0_pct=_pct(result.ret_b0_units),
        b0_net_pct=_pct(result.ret_b0_net_units),
        b1_pct=_pct(result.ret_b1_units),
        b2_pct=_pct(result.ret_b2_units),
        b3_pct=_pct(result.ret_b3_units),
        max_close_display=_price_2dp(result.max_close_units),
        min_close_display=_price_2dp(result.min_close_units),
        price_jump_review=result.price_jump_review,
        ret_b3_units=result.ret_b3_units,
        ret_b2_units=result.ret_b2_units,
    )


@dataclass(slots=True)
class _MarketData:
    calendar: tuple[date, ...]
    bars_by_symbol: dict[str, tuple[tuple[date, int], ...]]
    bench_series: tuple[tuple[date, int], ...]
    data_as_of: date | None
    benchmark_id: str
    bars_version: str


async def _fetch_bars(
    market_data: MarketDataSource,
    symbols: Sequence[str],
    *,
    start: date,
    end: date,
) -> list[Bar]:
    """批量取区间日线；单个标的 `NOT_FOUND` 时逐只降级，不让整页 404。

    数据源对不支持/未收录的标的抛 `ApiError(NOT_FOUND)`（live 不在支持范围时带
    `market_failure=not_supported`；replay 夹具没有该标的）。与
    `application.valuation.load_prices` 同一降级口径：整批失败后按标的重试，取不到
    的标的按「无行情」处理 → 该标的前瞻收益显式 `no_bar` + null，其余标的与回合照常
    返回。非 `NOT_FOUND` 的故障（行情源不可用/坏数据）仍然上抛，不伪装成缺数据。
    """
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


async def _load_market(
    market_data: MarketDataSource,
    symbols: Iterable[str],
    *,
    start: date,
    end: date,
) -> _MarketData:
    benchmark_id = DEFAULT_BENCHMARK
    bench_symbol = benchmark_fetch_symbol(benchmark_id, market_data)
    wanted = sorted({symbol for symbol in symbols if symbol} | {bench_symbol})
    bars = await _fetch_bars(market_data, wanted, start=start, end=end)
    grouped: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol].append((bar.trade_date, bar.close_units))
    bench_bars = sorted(grouped.get(bench_symbol, []))
    calendar = tuple(day for day, _ in bench_bars)
    bench_series = tuple(
        (bar.trade_date, bar.return_units) for bar in bars if bar.symbol == bench_symbol
    )
    bench_series = tuple(sorted(bench_series))
    data_as_of = max((bar.trade_date for bar in bars), default=None)
    return _MarketData(
        calendar=calendar,
        bars_by_symbol={key: tuple(sorted(value)) for key, value in grouped.items()},
        bench_series=bench_series,
        data_as_of=data_as_of,
        benchmark_id=benchmark_id,
        bars_version=_bar_version(bars),
    )


def _trade_facts(trade: Trade) -> ForwardTrade:
    return ForwardTrade(
        dedupe_key=trade.dedupe_key,
        trade_id=trade.id,
        version=trade.version,
        side=trade.side,
        d0=trade.trade_date,
        price_units=trade.price_units,
        amount_units=trade.amount_units,
        quantity_units=trade.quantity_units,
    )


def _corporate_action_dates(trades: Sequence[Trade]) -> dict[str, list[date]]:
    out: dict[str, list[date]] = defaultdict(list)
    for trade in trades:
        if is_corporate_action(
            category=trade.category,
            summary=trade.summary,
            quantity_units=trade.quantity_units,
            amount_units=trade.amount_units,
        ):
            out[trade.security_code].append(trade.trade_date)
    return {key: sorted(set(value)) for key, value in out.items()}


def _has_action_between(dates: Sequence[date], start: date, end: date) -> bool:
    return any(start < day <= end for day in dates)


def _compute_trade_forward(
    trade: Trade,
    *,
    horizon: int,
    market: _MarketData,
    corporate_dates: Sequence[date],
    data_as_of: date | None,
) -> ForwardResult:
    index = cal.base_index(market.calendar, trade.trade_date)
    target = cal.nth_after(market.calendar, index, horizon) if index is not None else None
    action = target is not None and _has_action_between(corporate_dates, trade.trade_date, target)
    return compute_forward(
        _trade_facts(trade),
        horizon=horizon,
        calendar=market.calendar,
        bars=market.bars_by_symbol.get(trade.security_code, ()),
        benchmark_series=market.bench_series,
        data_as_of=data_as_of,
        corporate_action=action,
    )


@dataclass(slots=True)
class _PositionState:
    qty_before: int = 0
    avg_cost_before: int | None = None


def _trade_order_key(trade: Trade) -> tuple[date, int, str, int, int, str]:
    """与 FIFO 引擎一致的稳定序（PRD 附录 A7）。

    同日多笔必须以**文件行号**破平局：`Trade.id` 是 ULID 随机段，与流水发生顺序无关。
    只按 `(trade_date, id)` 排序会把同日的「先卖后买」（清仓后重新建仓）误排成
    「先买后卖」，导致新回合的首笔买入被算成加仓。
    """
    return (
        trade.trade_date,
        trade.line_no or 0,
        trade.summary or "",
        trade.quantity_units,
        trade.price_units,
        trade.id,
    )


def _position_states(trades: Sequence[Trade]) -> dict[str, _PositionState]:
    """按 FIFO 稳定序推演加权平均成本；公司行动（送转）增加份额、不增成本。"""
    states: dict[str, _PositionState] = {}
    qty: dict[str, int] = defaultdict(int)
    cost: dict[str, int] = defaultdict(int)
    for trade in sorted(trades, key=_trade_order_key):
        states[trade.id] = _PositionState(
            qty_before=qty[trade.security_code],
            avg_cost_before=(cost[trade.security_code] * 10**8 // qty[trade.security_code])
            if qty[trade.security_code] > 0
            else None,
        )
        if trade.side == "buy" and trade.quantity_units > 0:
            qty[trade.security_code] += trade.quantity_units
            # `amount_units` 已是含费净现金流（解析层统一），不能再叠加费用字段。
            cost[trade.security_code] += abs(trade.amount_units)
        elif trade.side == "sell" and trade.quantity_units > 0:
            held = qty[trade.security_code]
            if held > 0:
                sold = min(trade.quantity_units, held)
                cost[trade.security_code] -= cost[trade.security_code] * sold // held
                qty[trade.security_code] = held - sold
        elif trade.quantity_units > 0 and trade.amount_units == 0:
            qty[trade.security_code] += trade.quantity_units
    return states


def _round_buy_trades(
    round_row: Round,
    ordered_trades: Sequence[Trade],
    order_index: dict[str, int],
) -> list[Trade]:
    """回合内的全部买入笔（开仓笔 + 周期内加仓笔），按 FIFO 稳定序返回。

    回合是「持仓 0→非 0→0」的完整周期，因此 `[开仓, 平仓)` 区间内该证券的所有买入
    都归属本回合；区间外（上一个/下一个周期）的买入不会混入。没有加仓笔时只有开仓笔。
    """
    open_index = order_index.get(round_row.open_trade_id)
    if open_index is None:
        return []
    close_index = (
        order_index.get(round_row.close_trade_id)
        if round_row.close_trade_id
        else len(ordered_trades)
    )
    return [
        trade
        for trade in ordered_trades[open_index:close_index]
        if trade.security_code == round_row.security_code and trade.side == "buy"
    ]


def _recent_prices(
    trades: Sequence[Trade],
    *,
    side: str,
    d0: date,
    calendar: Sequence[date],
) -> list[int]:
    """同持仓 60 交易日内、同一方向且发生在仓位 epoch 内的净价序列。

    `trades` 必须已按 `_trade_order_key` 排好序（调用方只排一次，避免每回合重复排序）。
    """
    cutoff_index = max(0, (cal.base_index(calendar, d0) or 0) - _ADDON_LOOKBACK_TRADING_DAYS)
    cutoff = calendar[cutoff_index] if calendar else d0
    prices: list[int] = []
    for trade in trades:
        if trade.side != side or trade.quantity_units <= 0:
            continue
        if trade.trade_date < cutoff or trade.trade_date > d0:
            continue
        net = price_net(_trade_facts(trade))
        if net is not None:
            prices.append(net)
    return prices


async def _load_trades(session: AsyncSession, user_id: uuid.UUID, *, end: date) -> list[Trade]:
    return list(
        await session.scalars(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.trade_date <= end,
                Trade.deleted_at.is_(None),
            )
        )
    )


async def _load_rounds(
    session: AsyncSession, user_id: uuid.UUID, *, start: date, end: date
) -> list[Round]:
    return list(
        await session.scalars(
            select(Round).where(
                Round.user_id == user_id,
                Round.close_date.is_not(None),
                Round.close_date >= start,
                Round.close_date <= end,
            )
        )
    )


async def _load_fills(
    session: AsyncSession, user_id: uuid.UUID, round_ids: Sequence[str]
) -> list[RoundFill]:
    """批量读取当前筛选集合的 fill（fill 级波段配对，设计 §3.4）。"""
    if not round_ids:
        return []
    return list(
        await session.scalars(
            select(RoundFill).where(
                RoundFill.user_id == user_id,
                RoundFill.round_id.in_(list(round_ids)),
            )
        )
    )


async def _cached_forwards(
    session: AsyncSession, user_id: uuid.UUID, keys: Sequence[str]
) -> dict[tuple[str, int], TradeForwardReturn]:
    if not keys:
        return {}
    rows = await session.scalars(
        select(TradeForwardReturn).where(
            TradeForwardReturn.user_id == user_id,
            TradeForwardReturn.trade_dedupe_key.in_(list(set(keys))),
            TradeForwardReturn.definition_version == DEFINITION_VERSION,
        )
    )
    return {(row.trade_dedupe_key, row.horizon): row for row in rows}


def _cached_result(row: TradeForwardReturn) -> ForwardResult:
    return ForwardResult(
        horizon=row.horizon,
        status=row.status,
        base_shifted=row.base_shifted,
        price_gross_units=row.price_gross_units,
        price_net_units=row.price_net_units,
        c0_units=row.c0_units,
        cn_units=row.cn_units,
        bench_ret_units=row.bench_ret_n_units,
        ret_b0_units=row.ret_b0_units,
        ret_b0_net_units=row.ret_b0_net_units,
        ret_b1_units=row.ret_b1_units,
        ret_b2_units=row.ret_b2_units,
        ret_b3_units=row.ret_b3_units,
        max_close_units=row.max_close_units,
        min_close_units=row.min_close_units,
        price_jump_review=row.price_jump_review,
    )


def _resolve_forward(
    trade: Trade,
    *,
    horizon: int,
    market: _MarketData,
    corporate_dates: Sequence[date],
    cached: dict[tuple[str, int], TradeForwardReturn],
    fingerprint: str,
) -> ForwardResult:
    row = cached.get((trade.dedupe_key, horizon))
    if (
        row is not None
        and row.trade_version == trade.version
        and row.bars_fingerprint == fingerprint
    ):
        return _cached_result(row)
    return _compute_trade_forward(
        trade,
        horizon=horizon,
        market=market,
        corporate_dates=corporate_dates,
        data_as_of=market.data_as_of,
    )


def _side_view(
    trade: Trade | None,
    *,
    reason_map: dict[str, TradeReason],
    cached: dict[tuple[str, int], TradeForwardReturn],
    market: _MarketData,
    corporate: dict[str, list[date]],
    fingerprint: str,
    flag_map: dict[str, list[TradeFlagRecord]] | None = None,
) -> TradeSideView | None:
    if trade is None:
        return None
    row = reason_map.get(trade.dedupe_key)
    dto = ReasonDTO.from_row(row) if row is not None else None
    forward: dict[int, ForwardView] = {}
    for horizon in HORIZONS:
        result = _resolve_forward(
            trade,
            horizon=horizon,
            market=market,
            corporate_dates=corporate.get(trade.security_code, ()),
            cached=cached,
            fingerprint=fingerprint,
        )
        forward[horizon] = _to_view(result)
    net_price = price_net(_trade_facts(trade))
    return TradeSideView(
        trade_id=trade.id,
        date=trade.trade_date,
        price_display=_price_6dp(net_price),
        quantity_display=format_quantity(trade.quantity_units),
        amount_display=_money(trade.amount_units),
        reason=dto,
        reason_label=(REASON_LABELS.get(dto.reason_code) if dto else None),
        forward=forward,
        trade_flags=list((flag_map or {}).get(trade.dedupe_key, [])),
    )


@dataclass(slots=True)
class _RoundFlagResult:
    flags: list[str]
    quality: list[str]
    missed_worst: int | None
    missed_end: int | None
    addon_b2_units: int | None = None


def _round_flags(
    *,
    round_row: Round,
    buy: Trade | None,
    sell: Trade | None,
    states: dict[str, _PositionState],
    ordered_trades: Sequence[Trade],
    order_index: dict[str, int],
    market: _MarketData,
    forwards: dict[tuple[str, int], TradeForwardReturn],
    fingerprint: str,
    corporate: dict[str, list[date]],
) -> _RoundFlagResult:
    flags: list[str] = []
    quality: list[str] = []
    missed_worst: int | None = None
    missed_end: int | None = None
    if buy is None or sell is None:
        return _RoundFlagResult(flags, quality, missed_worst, missed_end)
    # 买入侧判定统一落在「本回合最后一笔买入」：无加仓时就是开仓笔（既有口径不变），
    # 有加仓时就是最后一笔加仓笔（设计 §3.5：加仓 = 买入前同标的持仓 > 0）。
    # 不能用开仓笔：回合从 0 股开启，开仓笔的买入前持仓恒为 0，永远不可能是加仓。
    round_buys = _round_buy_trades(round_row, ordered_trades, order_index)
    buy_eval = round_buys[-1] if round_buys else buy
    state = states.get(buy_eval.id)
    is_addon = state is not None and classify.is_addon(state.qty_before)
    if is_addon:
        flags.append("addon")

    if is_addon and state is not None and state.avg_cost_before:
        prices = _recent_prices(
            ordered_trades, side="buy", d0=buy_eval.trade_date, calendar=market.calendar
        )
        if classify.is_dip_sequence(prices):
            flags.append("dip_sequence")

    # 抄底/加仓：买后 20D 是否继续下跌（只认 status=ok 的 20D 样本）
    buy_fwd20 = _resolve_forward(
        buy_eval,
        horizon=20,
        market=market,
        corporate_dates=corporate.get(buy_eval.security_code, ()),
        cached=forwards,
        fingerprint=fingerprint,
    )
    if is_addon and state is not None and state.avg_cost_before:
        net = price_net(_trade_facts(buy_eval))
        if net is not None:
            dip = classify.dip_depth_units(net, state.avg_cost_before)
            if dip is not None:
                flags.append("below_avg")
    if buy_fwd20.status == ForwardReturnStatus.OK:
        net = price_net(_trade_facts(buy_eval))
        if net is not None and classify.continued_decline(
            buy_fwd20.ret_b2_units, buy_fwd20.min_close_units, net
        ):
            flags.append("continued_decline")
        if buy_fwd20.cn_units is not None and net is not None:
            missed_worst = classify.additional_loss_units(
                buy_eval.quantity_units, net, buy_fwd20.min_close_units or net
            )
            missed_end = classify.additional_loss_units(
                buy_eval.quantity_units, net, buy_fwd20.cn_units
            )
    if buy_fwd20.price_jump_review:
        quality.append("price_jump_review")
    addon_b2_units = (
        buy_fwd20.ret_b2_units if is_addon and buy_fwd20.status == ForwardReturnStatus.OK else None
    )

    # 卖飞：盈利型 + 20D 最高收盘高于净卖价 3%
    sell_state = states.get(sell.id)
    avg_before = sell_state.avg_cost_before if sell_state else None
    sell_net = price_net(_trade_facts(sell))
    sell_fwd20 = _resolve_forward(
        sell,
        horizon=20,
        market=market,
        corporate_dates=corporate.get(sell.security_code, ()),
        cached=forwards,
        fingerprint=fingerprint,
    )
    profit = (
        avg_before is not None
        and sell_net is not None
        and classify.is_profit_taking(sell_net, avg_before)
    )
    if profit:
        flags.append("profit_taking")
    if sell_net is not None and classify.sold_too_early(sell_fwd20.max_close_units, sell_net):
        if profit:
            flags.append("profit_take_fly")
            if sell_fwd20.max_close_units is not None:
                missed_worst = classify.additional_loss_units(
                    sell.quantity_units, sell_net, sell_fwd20.max_close_units
                )
            if sell_fwd20.cn_units is not None:
                missed_end = classify.additional_loss_units(
                    sell.quantity_units, sell_net, sell_fwd20.cn_units
                )
        else:
            flags.append("stop_loss_rebound")
    prices = _recent_prices(
        ordered_trades, side="sell", d0=sell.trade_date, calendar=market.calendar
    )
    if classify.is_rise_sequence(prices):
        flags.append("rising_sell_sequence")
    if round_row.quantity_units and sell.quantity_units >= round_row.quantity_units:
        flags.append("full_exit")
    else:
        flags.append("partial_exit")
    if sell.trade_date == round_row.open_date:
        flags.append("same_day")
    return _RoundFlagResult(flags, quality, missed_worst, missed_end, addon_b2_units)


def _round_bucket(round_row: Round, holding: int | None) -> str:
    return classify.bucket_of(holding, status=round_row.status)


def _build_item(
    *,
    round_row: Round,
    buy: Trade | None,
    sell: Trade | None,
    reasons_map: dict[str, TradeReason],
    forwards: dict[tuple[str, int], TradeForwardReturn],
    market: _MarketData,
    corporate: dict[str, list[date]],
    states: dict[str, _PositionState],
    ordered_trades: Sequence[Trade],
    order_index: dict[str, int],
    fingerprint: str,
) -> InsightItem:
    holding = (
        cal.holding_trading_days(market.calendar, round_row.open_date, round_row.close_date)
        if round_row.close_date is not None
        else None
    )
    flag_result = _round_flags(
        round_row=round_row,
        buy=buy,
        sell=sell,
        states=states,
        ordered_trades=ordered_trades,
        order_index=order_index,
        market=market,
        forwards=forwards,
        fingerprint=fingerprint,
        corporate=corporate,
    )
    flags, quality = flag_result.flags, flag_result.quality
    missed_worst, missed_end = flag_result.missed_worst, flag_result.missed_end
    buy_view = _side_view(
        buy,
        reason_map=reasons_map,
        cached=forwards,
        market=market,
        corporate=corporate,
        fingerprint=fingerprint,
    )
    sell_view = _side_view(
        sell,
        reason_map=reasons_map,
        cached=forwards,
        market=market,
        corporate=corporate,
        fingerprint=fingerprint,
    )
    buy_excess = None
    sell_avoid = None
    if buy_view and buy_view.forward[20].status == ForwardReturnStatus.OK:
        buy_excess = buy_view.forward[20].ret_b3_units
    if sell_view and sell_view.forward[20].status == ForwardReturnStatus.OK:
        sell_avoid = (
            None
            if sell_view.forward[20].ret_b3_units is None
            else -sell_view.forward[20].ret_b3_units
        )
    combined = agg.combined_20(buy_excess, sell_avoid)
    worst_abs, worst_label, worst_label_en = _missed_presentation(missed_worst)
    end_abs, end_label, end_label_en = _missed_presentation(missed_end)
    item = InsightItem(
        round_id=round_row.id,
        security_code=round_row.security_code,
        security_name=round_row.security_name,
        status=round_row.status,
        open_date=round_row.open_date,
        close_date=round_row.close_date,
        holding_days_legacy=round_row.holding_days,
        holding_trading_days=holding,
        bucket=_round_bucket(round_row, holding),
        pnl_units=round_row.pnl_units,
        return_rate_units=round_row.return_rate_units,
        buy_trade=buy_view,
        sell_trade=sell_view,
        buy_excess_units=buy_excess,
        sell_avoid_units=sell_avoid,
        combined_units=combined,
        rank=None,
        quadrant=agg.quadrant(buy_excess, sell_avoid),
        flags=flags,
        quality=quality,
        missed_profit_worst_units=missed_worst,
        missed_profit_end_units=missed_end,
        missed_profit_worst_abs_display=worst_abs,
        missed_profit_worst_label=worst_label,
        missed_profit_worst_label_en=worst_label_en,
        missed_profit_end_abs_display=end_abs,
        missed_profit_end_label=end_label,
        missed_profit_end_label_en=end_label_en,
        addon_b2_units=flag_result.addon_b2_units,
    )
    for side in (buy_view, sell_view):
        if side is not None:
            for forward in side.forward.values():
                if forward.status != ForwardReturnStatus.OK:
                    item.quality.append(forward.status)
    return item


def _assign_ranks(items: list[InsightItem], *, sort: str) -> tuple[list[InsightItem], int]:
    """按指定字段排序并赋 rank；仅 `combined` 有“可排名”门禁。"""
    key_name = _SORT_FIELDS.get(sort)
    if key_name is None:
        raise ApiError(ErrorCode.INVALID_SORT_FIELD, detail=f"不支持排序字段 {sort!r}")
    eligible = [
        item for item in items if item.combined_units is not None or key_name != "combined_units"
    ]
    if sort == "combined":
        eligible = [item for item in items if item.combined_units is not None]
    eligible.sort(
        key=lambda item: (
            getattr(item, key_name) is not None,
            getattr(item, key_name) if getattr(item, key_name) is not None else 0,
            item.close_date,
            item.round_id,
        ),
        reverse=True,
    )
    for index, item in enumerate(eligible, start=1):
        item.rank = index
    remainder = [item for item in items if item.rank is None]
    return eligible + remainder, len(eligible)


def _summary(
    items: Sequence[InsightItem],
    *,
    total_rounds: int,
    annotated: int,
    fill_stats: agg.FillSwingStats,
) -> InsightsSummary:
    closed = [item for item in items if item.status == "closed"]
    pnls = [item.pnl_units for item in closed]
    win = sum(1 for pnl in pnls if pnl > 0)
    holdings = [
        item.holding_trading_days for item in closed if item.holding_trading_days is not None
    ]
    swing = SummaryBucket(
        closed=len(closed),
        scalp=sum(1 for item in closed if item.bucket == "scalp"),
        short=sum(1 for item in closed if item.bucket == "short"),
        position=sum(1 for item in closed if item.bucket == "position"),
        long=sum(1 for item in closed if item.bucket == "long"),
        avg_holding_trading_days=(f"{sum(holdings) / len(holdings):.1f}" if holdings else None),
        win_rate_pct=(f"{win * 100 / len(pnls):.2f}%" if pnls else None),
        avg_pnl_display=_money(sum(pnls) // len(pnls) if pnls else None),
        total_pnl_display=_money(sum(pnls) if pnls else None),
    )
    fill_pnls = fill_stats.pnl_units
    holding_by_pnl = PnlHoldingSummary(
        flat_tolerance_display=str(Money(agg.PNL_FLAT_TOLERANCE_UNITS)),
        classes=[
            PnlHoldingClass(
                code=group.code,
                label=PNL_CLASS_LABELS[group.code],
                count=group.count,
                avg_holding_trading_days=_days_x10_display(group.avg_holding_trading_days_x10),
                avg_holding_trading_days_x10=group.avg_holding_trading_days_x10,
            )
            for group in agg.holding_by_pnl(
                [(item.pnl_units, item.holding_trading_days) for item in closed]
            )
        ],
    )
    fill_swing = FillSwingBucket(
        pairs=fill_stats.pairs,
        win_rate_pct=(
            f"{fill_stats.wins * 100 / fill_stats.pairs:.2f}%" if fill_stats.pairs else None
        ),
        avg_holding_trading_days=(
            f"{sum(fill_stats.holding_trading_days) / len(fill_stats.holding_trading_days):.1f}"
            if fill_stats.holding_trading_days
            else None
        ),
        avg_pnl_display=_money(sum(fill_pnls) // len(fill_pnls) if fill_pnls else None),
        total_pnl_display=_money(sum(fill_pnls) if fill_pnls else None),
    )
    addon_items = [item for item in items if "addon" in item.flags]
    sell_fly_items = [item for item in items if "profit_take_fly" in item.flags]
    rebound_items = [item for item in items if "stop_loss_rebound" in item.flags]
    sell_items = [item for item in items if item.sell_trade is not None]
    declined_addon = sum(1 for item in addon_items if "continued_decline" in item.flags)
    # avg_dip：取加仓笔（本回合最后一笔买入）status=ok 的 20 日 B2；缺样本时显式 null + 覆盖度。
    dip_samples = [item.addon_b2_units for item in addon_items if item.addon_b2_units is not None]
    addon_summary = AddonSummary(
        count=len(addon_items),
        declined_after_addon=declined_addon,
        declined_pct=(f"{declined_addon * 100 / len(addon_items):.2f}%" if addon_items else None),
        avg_dip_pct=_pct(agg.weighted_mean([(value, 1) for value in dip_samples])),
        avg_dip_coverage=f"{len(dip_samples)}/{len(addon_items)}",
        rounds=len(addon_items),
        share_pct=f"{len(addon_items) * 100 / len(closed):.2f}%" if closed else None,
    )
    worst_units = (
        sum(item.missed_profit_worst_units or 0 for item in sell_fly_items)
        if sell_fly_items
        else None
    )
    end_units = (
        sum(item.missed_profit_end_units or 0 for item in sell_fly_items)
        if sell_fly_items
        else None
    )
    worst_abs, worst_label, worst_label_en = _missed_presentation(worst_units)
    end_abs, end_label, end_label_en = _missed_presentation(end_units)
    sell_fly_summary = SellFlySummary(
        sell_count=len(sell_items),
        profit_take_fly=len(sell_fly_items),
        stop_loss_rebound=len(rebound_items),
        fly_pct=f"{len(sell_fly_items) * 100 / len(sell_items):.2f}%" if sell_items else None,
        missed_profit_worst_display=_money(worst_units),
        missed_profit_end_display=_money(end_units),
        rounds=len(sell_fly_items),
        share_pct=f"{len(sell_fly_items) * 100 / len(closed):.2f}%" if closed else None,
        missed_profit_worst_abs_display=worst_abs,
        missed_profit_worst_label=worst_label,
        missed_profit_worst_label_en=worst_label_en,
        missed_profit_end_abs_display=end_abs,
        missed_profit_end_label=end_label,
        missed_profit_end_label_en=end_label_en,
    )
    combined_values = [item.combined_units for item in items if item.combined_units is not None]
    ranked = sorted(combined_values)
    quadrant_counts: dict[str, int] = defaultdict(int)
    for item in items:
        if item.quadrant:
            quadrant_counts[item.quadrant] += 1
    per_stock: dict[str, list[InsightItem]] = defaultdict(list)
    for item in items:
        per_stock[item.security_code].append(item)
    stock_rows = []
    for code, rows in per_stock.items():
        if len(rows) < 3:
            continue
        buys = [item.buy_excess_units for item in rows if item.buy_excess_units is not None]
        sells = [item.sell_avoid_units for item in rows if item.sell_avoid_units is not None]
        stock_rows.append(
            {
                "security_code": code,
                "security_name": rows[0].security_name,
                "n_rounds": len(rows),
                "buy_excess_pct": _pct(agg.weighted_mean([(value, 1) for value in buys])),
                "sell_avoid_pct": _pct(agg.weighted_mean([(value, 1) for value in sells])),
            }
        )
    reason_counts: dict[str, int] = defaultdict(int)
    for item in items:
        for side in (item.buy_trade, item.sell_trade):
            if side and side.reason is not None:
                reason_counts[side.reason.reason_code] += 1
    by_code = [
        ReasonAggregate(
            code=code,
            label=REASON_LABELS.get(code, code),
            count=count,
            share_pct=f"{count * 100 / annotated:.2f}%" if annotated else "0.00%",
        )
        for code, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]
    n_eligible = len(combined_values)
    return InsightsSummary(
        swing=swing,
        fill_swing=fill_swing,
        holding_by_pnl=holding_by_pnl,
        addon=addon_summary,
        sell_fly=sell_fly_summary,
        reason_coverage=ReasonCoverage(
            annotated=annotated,
            total_rounds=total_rounds,
            coverage_pct=f"{annotated * 100 / total_rounds:.2f}%" if total_rounds else None,
            by_code=by_code,
        ),
        ranking=RankingSummary(
            n_sample=len(items),
            n_eligible=n_eligible,
            sample_status=agg.sample_status(n_eligible),
            combined_median_pct=_pct(ranked[len(ranked) // 2]) if ranked else None,
            combined_avg_pct=_pct(sum(ranked) // len(ranked)) if ranked else None,
            quadrant_counts=dict(quadrant_counts),
            per_stock=stock_rows,
        ),
        pending={
            "status_pending": sum(
                1
                for item in items
                for side in (item.buy_trade, item.sell_trade)
                if side and side.forward[20].status == ForwardReturnStatus.PENDING
            ),
            "status_no_calendar": sum(
                1
                for item in items
                for side in (item.buy_trade, item.sell_trade)
                if side and side.forward[20].status == ForwardReturnStatus.NO_CALENDAR
            ),
            "status_no_bar": sum(
                1
                for item in items
                for side in (item.buy_trade, item.sell_trade)
                if side and side.forward[20].status == ForwardReturnStatus.NO_BAR
            ),
            "status_stale_price": sum(
                1
                for item in items
                for side in (item.buy_trade, item.sell_trade)
                if side and side.forward[20].status == ForwardReturnStatus.STALE_PRICE
            ),
            "status_corporate_action": sum(
                1
                for item in items
                for side in (item.buy_trade, item.sell_trade)
                if side and side.forward[20].status == ForwardReturnStatus.CORPORATE_ACTION
            ),
        },
    )


def _side_statuses(item: InsightItem) -> list[str]:
    return [
        view.status
        for side in (item.buy_trade, item.sell_trade)
        if side is not None
        for view in side.forward.values()
    ]


def _matches_flag(item: InsightItem, flag: str) -> bool:
    """`GET /rounds/insights?flag=` 词表（设计 §6.3）→ 内部旗标/状态的映射。

    未知旗标按「不匹配」处理（不抛错，词表之外的无结果）；组合含义：
    * `addon_decline`：加仓 **且** 加仓后 20D 继续下跌（AC-D3）；
    * `sold_too_early`：价格层面卖飞，含止盈型与止损后反弹型（AC-D5）；
    * `reason_missing`：买卖两侧均无理由（与 `has_reason=false` 同义）；
    * `horizon_pending`：任一前瞻为待算（不足 5/10/20 交易日）；
    * `corporate_action`：任一前瞻因公司行动 fail-closed。
    """
    if flag == "addon_decline":
        return "addon" in item.flags and "continued_decline" in item.flags
    if flag == "sold_too_early":
        return "profit_take_fly" in item.flags or "stop_loss_rebound" in item.flags
    if flag == "reason_missing":
        return (item.buy_trade is None or item.buy_trade.reason is None) and (
            item.sell_trade is None or item.sell_trade.reason is None
        )
    if flag == "horizon_pending":
        return ForwardReturnStatus.PENDING in _side_statuses(item)
    if flag == "corporate_action":
        return ForwardReturnStatus.CORPORATE_ACTION in _side_statuses(item)
    return flag in item.flags


def _security_type(code: str, name: str, money_fund_codes: set[str]) -> str:
    """证券类型（`security_type` 筛选口径）：stock / fund / convertible_bond / other。

    * 代码区段是单一真相（`stock_platform.symbols`）：A 股 → stock，场内基金 → fund，
      可转债 → convertible_bond，其余（国债/企业债/无法识别）→ other，保证四类互斥完备；
    * 货币基金代码（如 940018）不在任何区段：由 `trade.money_fund_balance_units` 非空
      或名称含「货币」识别，归入 fund（货币基金是基金的一种）。
    """
    if code in money_fund_codes or "货币" in name:
        return SECURITY_TYPE_FUND
    if is_convertible_bond(code):
        return SECURITY_TYPE_CONVERTIBLE_BOND
    kind = classify_symbol(code)
    if kind == "stock":
        return SECURITY_TYPE_STOCK
    if kind == "fund":
        return SECURITY_TYPE_FUND
    return SECURITY_TYPE_OTHER


async def get_insights_page(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    market_data: MarketDataSource,
    clock: Clock,
    period: str | None = None,
    bucket: str | None = None,
    flags: Sequence[str] = (),
    has_reason: bool | None = None,
    reason_code: str | None = None,
    security_code: str | None = None,
    security_types: Sequence[str] = (),
    sort: str = "combined",
    sort_dir: str = "desc",
    include_summary: bool = True,
    limit: int = 50,
    cursor: str | None = None,
) -> InsightsPage:
    resolved = _resolve_period(period, clock)
    if sort not in _SORT_FIELDS:
        raise ApiError(ErrorCode.INVALID_SORT_FIELD, detail=f"不支持排序字段 {sort!r}")
    if sort_dir not in ("asc", "desc"):
        raise ApiError(ErrorCode.INVALID_SORT_FIELD, detail="sort_dir 只能是 asc/desc")
    unknown_types = [value for value in security_types if value not in SECURITY_TYPES]
    if unknown_types:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR, detail=f"非法的 security_type: {unknown_types[0]}"
        )

    rounds = await _load_rounds(session, user_id, start=resolved.start, end=resolved.end)
    symbol_trade_ids = {round_row.open_trade_id for round_row in rounds} | {
        round_row.close_trade_id for round_row in rounds if round_row.close_trade_id
    }
    trades = await _load_trades(session, user_id, end=resolved.end)
    by_id = {trade.id: trade for trade in trades}
    symbols = {trade.security_code for trade in trades}
    market = await _load_market(
        market_data,
        symbols,
        start=min((trade.trade_date for trade in trades), default=resolved.start),
        end=max(clock.today(), resolved.end),
    )
    missing_calendar = not market.calendar
    fingerprint = _fingerprint(market.calendar, market.bars_version, market.benchmark_id)
    corporate = _corporate_action_dates(trades)
    # 稳定序只排一次：持仓推演、回合内买入归属（加仓判定）、最近买卖价序列共用。
    ordered_trades = sorted(trades, key=_trade_order_key)
    order_index = {trade.id: index for index, trade in enumerate(ordered_trades)}
    states = _position_states(ordered_trades)

    reason_map = await reasons.active_by_dedupe(
        session, user_id, [trade.dedupe_key for trade in trades if trade.id in symbol_trade_ids]
    )
    wanted_reason_keys = {trade.dedupe_key for trade in trades if trade.id in symbol_trade_ids}
    cached = await _cached_forwards(session, user_id, list(wanted_reason_keys))

    items: list[InsightItem] = []
    for round_row in rounds:
        buy = by_id.get(round_row.open_trade_id)
        sell = by_id.get(round_row.close_trade_id) if round_row.close_trade_id else None
        items.append(
            _build_item(
                round_row=round_row,
                buy=buy,
                sell=sell,
                reasons_map=reason_map,
                forwards=cached,
                market=market,
                corporate=corporate,
                states=states,
                ordered_trades=ordered_trades,
                order_index=order_index,
                fingerprint=fingerprint,
            )
        )

    if bucket:
        items = [item for item in items if item.bucket == bucket]
    if flags:
        wanted = [flag.strip() for flag in flags if flag.strip()]
        items = [item for item in items if all(_matches_flag(item, flag) for flag in wanted)]
    if security_code:
        items = [item for item in items if item.security_code == security_code]
    if security_types:
        # 货基代码没有稳定的代码区段，只能用流水上的货基标记兜底识别。
        money_fund_codes = {
            trade.security_code for trade in trades if trade.money_fund_balance_units is not None
        }
        wanted_types = set(security_types)
        items = [
            item
            for item in items
            if _security_type(item.security_code, item.security_name, money_fund_codes)
            in wanted_types
        ]
    if has_reason is not None:
        items = [
            item
            for item in items
            if (
                (item.buy_trade and item.buy_trade.reason is not None)
                or (item.sell_trade and item.sell_trade.reason is not None)
            )
            == has_reason
        ]
    if reason_code:
        items = [
            item
            for item in items
            if any(
                side and side.reason and side.reason.reason_code == reason_code
                for side in (item.buy_trade, item.sell_trade)
            )
        ]

    items, _n_eligible = _assign_ranks(items, sort=sort)
    if sort_dir == "asc":
        ranked = [item for item in items if item.rank is not None]
        unranked = [item for item in items if item.rank is None]
        ranked.reverse()
        for index, item in enumerate(ranked, start=1):
            item.rank = index
        items = ranked + unranked

    annotated = sum(
        1
        for item in items
        if (item.buy_trade and item.buy_trade.reason)
        or (item.sell_trade and item.sell_trade.reason)
    )
    summary = None
    if include_summary:
        fills = await _load_fills(session, user_id, [item.round_id for item in items])
        summary = _summary(
            items,
            total_rounds=len(items),
            annotated=annotated,
            fill_stats=agg.swing_fill_pairs(
                [(row.open_date, row.close_date, row.pnl_units) for row in fills],
                market.calendar,
            ),
        )

    total = len(items)
    offset = _decode_cursor(cursor) if cursor else 0
    if offset < 0 or offset > total:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标越界")
    page_items = items[offset : offset + limit]
    next_cursor = _encode_cursor(offset + limit) if offset + limit < total else None
    missing: list[str] = []
    if missing_calendar:
        missing.append("BENCHMARK_CALENDAR_MISSING")
    if market.data_as_of is None:
        missing.append("MARKET_DATA_MISSING")
    return InsightsPage(
        period=resolved.label,
        period_basis="close_date",
        definition_version=DEFINITION_VERSION,
        bucket_basis="round",
        data_as_of=market.data_as_of,
        ranking_as_of=market.data_as_of,
        benchmark_id=market.benchmark_id,
        basis=agg.RANKING_BASIS,
        item_total=total,
        summary=summary,
        items=page_items,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        missing=missing,
        assumptions=[
            "交易日历取基准日线日期集合",
            "收益基于未复权日线，公司行动窗口内降级不排名",
        ],
        disclaimer=DISCLAIMER,
        flag_glossary=list(FLAG_DEFINITIONS),
    )


async def get_round_insight(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    round_id: str,
    market_data: MarketDataSource,
    clock: Clock,
) -> RoundInsightDetail:
    round_row = await session.scalar(
        select(Round).where(Round.user_id == user_id, Round.id == round_id)
    )
    if round_row is None:
        raise ApiError(ErrorCode.NOT_FOUND, detail=f"round {round_id} 不存在")
    trades = await _load_trades(session, user_id, end=round_row.close_date or clock.today())
    by_id = {trade.id: trade for trade in trades}
    buy = by_id.get(round_row.open_trade_id)
    sell = by_id.get(round_row.close_trade_id) if round_row.close_trade_id else None
    market = await _load_market(
        market_data,
        {trade.security_code for trade in trades},
        start=min((trade.trade_date for trade in trades), default=round_row.open_date),
        end=max(clock.today(), round_row.close_date or round_row.open_date),
    )
    fingerprint = _fingerprint(market.calendar, market.bars_version, market.benchmark_id)
    corporate = _corporate_action_dates(trades)
    ordered_trades = sorted(trades, key=_trade_order_key)
    order_index = {trade.id: index for index, trade in enumerate(ordered_trades)}
    states = _position_states(ordered_trades)
    wanted = {trade.dedupe_key for trade in trades}
    reason_map = await reasons.active_by_dedupe(session, user_id, wanted)
    flag_map = await load_trade_flags(session, user_id, wanted)
    cached = await _cached_forwards(session, user_id, list(wanted))
    buy_view = _side_view(
        buy,
        reason_map=reason_map,
        cached=cached,
        market=market,
        corporate=corporate,
        fingerprint=fingerprint,
        flag_map=flag_map,
    )
    sell_view = _side_view(
        sell,
        reason_map=reason_map,
        cached=cached,
        market=market,
        corporate=corporate,
        fingerprint=fingerprint,
        flag_map=flag_map,
    )
    item = _build_item(
        round_row=round_row,
        buy=buy,
        sell=sell,
        reasons_map=reason_map,
        forwards=cached,
        market=market,
        corporate=corporate,
        states=states,
        ordered_trades=ordered_trades,
        order_index=order_index,
        fingerprint=fingerprint,
    )
    fills = []
    for fill in await session.scalars(
        select(RoundFill).where(RoundFill.user_id == user_id, RoundFill.round_id == round_id)
    ):
        buy_trade = by_id.get(fill.buy_trade_id)
        sell_trade = by_id.get(fill.sell_trade_id)
        held = cal.holding_trading_days(market.calendar, fill.open_date, fill.close_date)
        fills.append(
            {
                "fill_id": fill.id,
                "sequence": fill.sequence,
                "buy": _side_view(
                    buy_trade,
                    reason_map=reason_map,
                    cached=cached,
                    market=market,
                    corporate=corporate,
                    fingerprint=fingerprint,
                    flag_map=flag_map,
                ),
                "sell": _side_view(
                    sell_trade,
                    reason_map=reason_map,
                    cached=cached,
                    market=market,
                    corporate=corporate,
                    fingerprint=fingerprint,
                    flag_map=flag_map,
                ),
                "quantity_display": format_quantity(fill.quantity_units),
                "pnl_display": _money(fill.pnl_units),
                "open_date": fill.open_date,
                "close_date": fill.close_date,
                "holding_trading_days": held,
                "bucket": classify.bucket_of(held, status="closed"),
            }
        )
    return RoundInsightDetail(
        round_id=round_row.id,
        security_code=round_row.security_code,
        security_name=round_row.security_name,
        status=round_row.status,
        open_date=round_row.open_date,
        close_date=round_row.close_date,
        holding_days_legacy=round_row.holding_days,
        holding_trading_days=item.holding_trading_days,
        bucket=item.bucket,
        pnl_units=round_row.pnl_units,
        return_rate_units=round_row.return_rate_units,
        buy_trade=buy_view,
        sell_trade=sell_view,
        fills=fills,
        flags=item.flags,
        quality=item.quality,
        ranking={
            "rank": item.rank,
            "buy_excess_pct": _pct(item.buy_excess_units),
            "sell_avoid_pct": _pct(item.sell_avoid_units),
            "combined_pct": _pct(item.combined_units),
            "quadrant": item.quadrant,
            "basis": agg.RANKING_BASIS,
            "definition_version": DEFINITION_VERSION,
        },
        disclaimer=DISCLAIMER,
        flag_glossary=list(FLAG_DEFINITIONS),
        trade_flag_glossary=trade_flag_glossary(),
    )


async def refresh_forwards(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    market_data: MarketDataSource,
    clock: Clock,
    period: str | None = None,
    scope: str = "missing",
) -> RefreshResult:
    resolved = _resolve_period(period, clock)
    trades = [
        trade
        for trade in await _load_trades(session, user_id, end=resolved.end)
        if resolved.start <= trade.trade_date <= resolved.end
    ]
    all_trades = await _load_trades(session, user_id, end=max(clock.today(), resolved.end))
    symbols = {trade.security_code for trade in all_trades}
    market = await _load_market(
        market_data,
        symbols,
        start=min((trade.trade_date for trade in all_trades), default=resolved.start),
        end=max(clock.today(), resolved.end),
    )
    corporate = _corporate_action_dates(all_trades)
    existing = await _cached_forwards(session, user_id, [trade.dedupe_key for trade in trades])
    fingerprint = _fingerprint(market.calendar, market.bars_version, market.benchmark_id)

    computed = 0
    skipped = 0
    counts: dict[str, int] = defaultdict(int)
    rows: list[TradeForwardReturn] = []
    for trade in trades:
        for horizon in HORIZONS:
            cached = existing.get((trade.dedupe_key, horizon))
            if (
                scope == "missing"
                and cached is not None
                and cached.trade_version == trade.version
                and cached.bars_fingerprint == fingerprint
            ):
                skipped += 1
                continue
            result = _compute_trade_forward(
                trade,
                horizon=horizon,
                market=market,
                corporate_dates=corporate.get(trade.security_code, ()),
                data_as_of=market.data_as_of,
            )
            counts[result.status] += 1
            rows.append(
                TradeForwardReturn(
                    id=new_id("fwd"),
                    user_id=user_id,
                    trade_dedupe_key=trade.dedupe_key,
                    trade_id=trade.id,
                    side=trade.side,
                    d0=trade.trade_date,
                    horizon=horizon,
                    status=result.status,
                    base_shifted=result.base_shifted,
                    data_as_of=market.data_as_of,
                    calendar_basis="benchmark",
                    benchmark_id=market.benchmark_id,
                    definition_version=DEFINITION_VERSION,
                    trade_version=trade.version,
                    price_gross_units=result.price_gross_units,
                    price_net_units=result.price_net_units,
                    c0_units=result.c0_units,
                    cn_units=result.cn_units,
                    ret_b0_units=result.ret_b0_units,
                    ret_b0_net_units=result.ret_b0_net_units,
                    ret_b1_units=result.ret_b1_units,
                    ret_b2_units=result.ret_b2_units,
                    ret_b3_units=result.ret_b3_units,
                    bench_ret_n_units=result.bench_ret_units,
                    max_close_units=result.max_close_units,
                    min_close_units=result.min_close_units,
                    price_jump_review=result.price_jump_review,
                    bars_fingerprint=fingerprint,
                    computed_at=clock.now(),
                )
            )
            computed += 1
    if rows:
        values = [
            {
                column.name: getattr(row, column.name)
                for column in TradeForwardReturn.__table__.columns
                if column.name not in ("created_at", "updated_at")
            }
            for row in rows
        ]
        await session.execute(
            pg_insert(TradeForwardReturn)
            .values(values)
            .on_conflict_do_update(
                constraint="uq_trade_forward_return_key",
                set_={
                    column.name: getattr(pg_insert(TradeForwardReturn).excluded, column.name)
                    for column in TradeForwardReturn.__table__.columns
                    if column.name not in ("id", "created_at", "updated_at")
                },
            )
        )
    return RefreshResult(
        computed=computed,
        skipped=skipped,
        pending=counts[ForwardReturnStatus.PENDING],
        no_calendar=counts[ForwardReturnStatus.NO_CALENDAR],
        no_bar=counts[ForwardReturnStatus.NO_BAR],
        corporate_action=counts[ForwardReturnStatus.CORPORATE_ACTION],
        divisor_missing=counts[ForwardReturnStatus.DIVISOR_MISSING],
        stale_price=counts[ForwardReturnStatus.STALE_PRICE],
        data_as_of=market.data_as_of,
    )
