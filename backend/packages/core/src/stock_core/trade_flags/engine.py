"""账单级旗标纯判定引擎。

输入全部是标度整数与日期，无 I/O、无 wall-clock；调用方负责取行情/日历/持仓推演。
比例比较统一放大到整数域（`value * BPS_DENOMINATOR`），全程不出现 float。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from stock_core.trade_flags.constants import (
    BPS_DENOMINATOR,
    CHASE_MARGIN_BPS,
    INSTANT_MARGIN_BPS,
    NEAR_EQUAL_TOLERANCE_BPS,
    SHORT_TERM_MARGIN_BPS,
    STATUS_HIT,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_MISS,
    STATUS_NO_BAR,
    STATUS_NO_CALENDAR,
    STATUS_NO_COST_BASIS,
    STATUS_NOT_APPLICABLE,
    STATUS_PENDING,
    STOP_LOSS_MARGIN_BPS,
    WINDOW_TRADING_DAYS,
)
from stock_core.trade_flags.definitions import (
    BUY_FLAG_CODES,
    SELL_FLAG_CODES,
    TRADE_FLAG_BY_CODE,
)

# 行情点：(交易日, 收盘价标度整数 scale=1e6)。
BarPoint = tuple[date, int]


@dataclass(frozen=True, slots=True)
class TradeContext:
    """单笔账单的判定输入（`avg_cost_units` 为含费均价，scale=1e6）。"""

    side: str
    trade_date: date
    net_price_units: int
    quantity_units: int
    quantity_before_units: int
    avg_cost_units: int | None


@dataclass(frozen=True, slots=True)
class FlagResult:
    code: str
    status: str
    lookahead: bool = False
    reason: str | None = None


def _gt(value: int, base: int, bps: int) -> bool:
    return value * BPS_DENOMINATOR > base * (BPS_DENOMINATOR + bps)


def _lt(value: int, base: int, bps: int) -> bool:
    return value * BPS_DENOMINATOR < base * (BPS_DENOMINATOR - bps)


def _le_within(value: int, base: int, bps: int) -> bool:
    """`value` 不高于 `base` 上方 bps 以内（含）。"""
    return value * BPS_DENOMINATOR <= base * (BPS_DENOMINATOR + bps)


def _ge_within(value: int, base: int, bps: int) -> bool:
    """`value` 不低于 `base` 下方 bps 以内（含）。"""
    return value * BPS_DENOMINATOR >= base * (BPS_DENOMINATOR - bps)


def _result(code: str, status: str, reason: str | None = None) -> FlagResult:
    return FlagResult(
        code=code,
        status=status,
        lookahead=TRADE_FLAG_BY_CODE[code].lookahead,
        reason=reason,
    )


def _past_status(
    bars: Sequence[BarPoint], trade_date: date, calendar: Sequence[date]
) -> str | None:
    """前向窗口可用性：返回 None 表示 5 个行情日齐备。"""
    if not calendar:
        return STATUS_NO_CALENDAR
    if not bars:
        return STATUS_NO_BAR
    count = sum(1 for day, _ in bars if day < trade_date)
    if count < WINDOW_TRADING_DAYS:
        return STATUS_INSUFFICIENT_HISTORY
    return None


def _future_status(
    bars: Sequence[BarPoint],
    trade_date: date,
    calendar: Sequence[date],
    data_as_of: date | None,
) -> str | None:
    """未来窗口可用性：返回 None 表示 5 个行情日齐备。"""
    if not calendar:
        return STATUS_NO_CALENDAR
    count = sum(1 for day, _ in bars if day > trade_date)
    if count >= WINDOW_TRADING_DAYS:
        return None
    pos = bisect_right(calendar, trade_date)
    required_index = pos + WINDOW_TRADING_DAYS - 1
    if required_index >= len(calendar):
        return STATUS_PENDING
    required = calendar[required_index]
    if data_as_of is None or required > data_as_of:
        return STATUS_PENDING
    return STATUS_INSUFFICIENT_HISTORY


def _past_closes(bars: Sequence[BarPoint], trade_date: date) -> list[int]:
    closes = [close for day, close in bars if day < trade_date]
    return closes[-WINDOW_TRADING_DAYS:]


def _future_closes(bars: Sequence[BarPoint], trade_date: date) -> list[int]:
    closes = [close for day, close in bars if day > trade_date]
    return closes[:WINDOW_TRADING_DAYS]


def _price_bucket(price: int, avg: int) -> str:
    """含费净价相对持仓均价的桶：`above` / `below` / `near`（±1% 以内）。"""
    diff = price - avg
    if diff * BPS_DENOMINATOR > avg * NEAR_EQUAL_TOLERANCE_BPS:
        return "above"
    if diff * BPS_DENOMINATOR < -avg * NEAR_EQUAL_TOLERANCE_BPS:
        return "below"
    return "near"


def _state_flags(ctx: TradeContext) -> list[FlagResult]:
    """买入侧「首次建仓」+「加仓状态」三选一（四者互斥）。"""
    codes = ("addon_average", "addon_dip", "addon_chase")
    if ctx.quantity_before_units <= 0:
        return [
            _result("first_entry", STATUS_HIT),
            *[_result(code, STATUS_NOT_APPLICABLE, "first_buy") for code in codes],
        ]
    first = _result("first_entry", STATUS_MISS)
    avg = ctx.avg_cost_units
    if avg is None or avg <= 0:
        return [
            first,
            *[_result(code, STATUS_NO_COST_BASIS, "no_cost_basis") for code in codes],
        ]
    bucket = _price_bucket(ctx.net_price_units, avg)
    if bucket == "above":
        statuses = (STATUS_MISS, STATUS_MISS, STATUS_HIT)
    elif bucket == "below":
        statuses = (STATUS_MISS, STATUS_HIT, STATUS_MISS)
    else:
        statuses = (STATUS_HIT, STATUS_MISS, STATUS_MISS)
    return [first, *[_result(code, status) for code, status in zip(codes, statuses, strict=True)]]


def _exit_statuses(bucket: str) -> tuple[str, str, str]:
    """清仓三态（回本 / 止盈 / 止损）的命中组合。"""
    if bucket == "above":
        return (STATUS_MISS, STATUS_HIT, STATUS_MISS)
    if bucket == "below":
        return (STATUS_MISS, STATUS_MISS, STATUS_HIT)
    return (STATUS_HIT, STATUS_MISS, STATUS_MISS)


def _reduce_flags(ctx: TradeContext) -> list[FlagResult]:
    """卖出侧「减仓状态」与「清仓状态」互斥：清仓时减仓三态不适用。"""
    codes = ("reduce_breakeven", "reduce_profit", "reduce_loss")
    exit_codes = ("full_exit_breakeven", "full_exit_profit", "full_exit_loss")
    full = ctx.quantity_before_units > 0 and ctx.quantity_units >= ctx.quantity_before_units
    avg = ctx.avg_cost_units

    if full:
        reduced = [_result(code, STATUS_NOT_APPLICABLE, "full_exit") for code in codes]
        if avg is None or avg <= 0:
            exits = [_result(code, STATUS_NO_COST_BASIS, "no_cost_basis") for code in exit_codes]
        else:
            statuses = _exit_statuses(_price_bucket(ctx.net_price_units, avg))
            exits = [
                _result(code, status) for code, status in zip(exit_codes, statuses, strict=True)
            ]
        return [*reduced, _result("full_exit", STATUS_HIT), *exits]

    exits = [_result(code, STATUS_NOT_APPLICABLE, "not_full_exit") for code in exit_codes]
    if ctx.quantity_before_units <= 0:
        reduced = [_result(code, STATUS_NOT_APPLICABLE, "no_position") for code in codes]
        return [*reduced, _result("full_exit", STATUS_MISS), *exits]
    if avg is None or avg <= 0:
        reduced = [_result(code, STATUS_NO_COST_BASIS, "no_cost_basis") for code in codes]
        return [*reduced, _result("full_exit", STATUS_MISS), *exits]
    bucket = _price_bucket(ctx.net_price_units, avg)
    # 减仓与清仓的桶→三态映射一致：near→回本、above→止盈、below→止损。
    statuses = _exit_statuses(bucket)
    reduced = [_result(code, status) for code, status in zip(codes, statuses, strict=True)]
    return [*reduced, _result("full_exit", STATUS_MISS), *exits]


def _price_flag(code: str, status: str | None, hit: bool) -> FlagResult:
    if status is not None:
        return _result(code, status, _status_reason(status))
    return _result(code, STATUS_HIT if hit else STATUS_MISS)


def _status_reason(status: str) -> str:
    if status == STATUS_PENDING:
        return "future_window_incomplete"
    if status == STATUS_INSUFFICIENT_HISTORY:
        return "price_history_incomplete"
    if status == STATUS_NO_BAR:
        return "no_daily_bars"
    return "no_trading_calendar"


def _buy_price_flags(
    ctx: TradeContext,
    bars: Sequence[BarPoint],
    calendar: Sequence[date],
    data_as_of: date | None,
) -> list[FlagResult]:
    past_status = _past_status(bars, ctx.trade_date, calendar)
    future_status = _future_status(bars, ctx.trade_date, calendar, data_as_of)
    past = _past_closes(bars, ctx.trade_date) if past_status is None else []
    future = _future_closes(bars, ctx.trade_date) if future_status is None else []

    chase_hit = past_status is None and _gt(ctx.net_price_units, max(past), CHASE_MARGIN_BPS)
    bottom_hit = past_status is None and _le_within(
        ctx.net_price_units, min(past), INSTANT_MARGIN_BPS
    )

    if past_status is not None:
        short_status = past_status
    elif future_status is not None:
        short_status = future_status
    else:
        short_status = None
    short_hit = short_status is None and _le_within(
        ctx.net_price_units, min([*past, *future]), SHORT_TERM_MARGIN_BPS
    )

    return [
        _price_flag("chase", past_status, chase_hit),
        _price_flag("instant_bottom", past_status, bottom_hit),
        _price_flag("short_term_win", short_status, short_hit),
    ]


def _sell_price_flags(
    ctx: TradeContext,
    bars: Sequence[BarPoint],
    calendar: Sequence[date],
    data_as_of: date | None,
) -> list[FlagResult]:
    past_status = _past_status(bars, ctx.trade_date, calendar)
    future_status = _future_status(bars, ctx.trade_date, calendar, data_as_of)
    past = _past_closes(bars, ctx.trade_date) if past_status is None else []
    future = _future_closes(bars, ctx.trade_date) if future_status is None else []

    stop_hit = past_status is None and _lt(ctx.net_price_units, min(past), STOP_LOSS_MARGIN_BPS)
    top_hit = past_status is None and _ge_within(ctx.net_price_units, max(past), INSTANT_MARGIN_BPS)

    if past_status is not None:
        short_status = past_status
    elif future_status is not None:
        short_status = future_status
    else:
        short_status = None
    short_hit = short_status is None and _ge_within(
        ctx.net_price_units, max([*past, *future]), SHORT_TERM_MARGIN_BPS
    )

    return [
        _price_flag("stop_loss", past_status, stop_hit),
        _price_flag("instant_top", past_status, top_hit),
        _price_flag("short_term_top", short_status, short_hit),
    ]


def evaluate_trade_flags(
    ctx: TradeContext,
    *,
    bars: Sequence[BarPoint],
    calendar: Sequence[date],
    data_as_of: date | None,
) -> list[FlagResult]:
    """评估单笔账单的全部旗标（按词表顺序返回，含 miss/not_applicable 状态）。"""
    if ctx.side == "buy":
        state = _state_flags(ctx)
        price = _buy_price_flags(ctx, bars, calendar, data_as_of)
    else:
        state = _reduce_flags(ctx)
        price = _sell_price_flags(ctx, bars, calendar, data_as_of)

    by_code = {item.code: item for item in [*state, *price]}
    codes = BUY_FLAG_CODES if ctx.side == "buy" else SELL_FLAG_CODES
    return [by_code[code] for code in codes]
