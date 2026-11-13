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
    DEFAULT_TRADE_FLAG_CONFIG,
    STATUS_HIT,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_MISS,
    STATUS_NO_BAR,
    STATUS_NO_CALENDAR,
    STATUS_NO_COST_BASIS,
    STATUS_NOT_APPLICABLE,
    STATUS_PENDING,
    TradeFlagConfig,
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
    bars: Sequence[BarPoint],
    trade_date: date,
    calendar: Sequence[date],
    window: int,
) -> str | None:
    """前向窗口可用性：返回 None 表示前 `window` 个行情日齐备。"""
    if not calendar:
        return STATUS_NO_CALENDAR
    if not bars:
        return STATUS_NO_BAR
    count = sum(1 for day, _ in bars if day < trade_date)
    if count < window:
        return STATUS_INSUFFICIENT_HISTORY
    return None


def _future_status(
    bars: Sequence[BarPoint],
    trade_date: date,
    calendar: Sequence[date],
    data_as_of: date | None,
    window: int,
) -> str | None:
    """未来窗口可用性：返回 None 表示后 `window` 个行情日齐备。"""
    if not calendar:
        return STATUS_NO_CALENDAR
    count = sum(1 for day, _ in bars if day > trade_date)
    if count >= window:
        return None
    pos = bisect_right(calendar, trade_date)
    required_index = pos + window - 1
    if required_index >= len(calendar):
        return STATUS_PENDING
    required = calendar[required_index]
    if data_as_of is None or required > data_as_of:
        return STATUS_PENDING
    return STATUS_INSUFFICIENT_HISTORY


def _past_closes(bars: Sequence[BarPoint], trade_date: date, window: int) -> list[int]:
    closes = [close for day, close in bars if day < trade_date]
    return closes[-window:]


def _future_closes(bars: Sequence[BarPoint], trade_date: date, window: int) -> list[int]:
    closes = [close for day, close in bars if day > trade_date]
    return closes[:window]


def _price_bucket(price: int, avg: int, tolerance_bps: int) -> str:
    """含费净价相对持仓均价的桶：`above` / `below` / `near`（±容差以内）。"""
    diff = price - avg
    if diff * BPS_DENOMINATOR > avg * tolerance_bps:
        return "above"
    if diff * BPS_DENOMINATOR < -avg * tolerance_bps:
        return "below"
    return "near"


def _state_flags(ctx: TradeContext, config: TradeFlagConfig) -> list[FlagResult]:
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
    bucket = _price_bucket(ctx.net_price_units, avg, config.near_equal_tolerance_bps)
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


def _cost_gap_reason(ctx: TradeContext, avg: int | None) -> str:
    """成本不可用时的精确原因：无持仓 / 成本缺失 / 负成本异常。"""
    if avg is not None and avg < 0:
        return "invalid_cost_basis"
    if ctx.quantity_before_units <= 0:
        return "no_position"
    return "no_cost_basis"


def _reduce_flags(ctx: TradeContext, config: TradeFlagConfig) -> list[FlagResult]:
    """卖出侧「减仓状态」与「清仓状态」互斥：清仓时减仓三态不适用。

    成本口径：`avg > 0` 为正常成本，`avg == 0` 为零成本持仓（送转/红利再投），
    视为有效成本（任何正卖价都在成本线上方）；`avg is None` 与 `avg < 0` 不参与三态判定。
    """
    codes = ("reduce_breakeven", "reduce_profit", "reduce_loss")
    exit_codes = ("full_exit_breakeven", "full_exit_profit", "full_exit_loss")
    full = ctx.quantity_before_units > 0 and ctx.quantity_units >= ctx.quantity_before_units
    avg = ctx.avg_cost_units

    if full:
        reduced = [_result(code, STATUS_NOT_APPLICABLE, "full_exit") for code in codes]
        if avg is None or avg < 0:
            reason = _cost_gap_reason(ctx, avg)
            exits = [_result(code, STATUS_NO_COST_BASIS, reason) for code in exit_codes]
        else:
            statuses = _exit_statuses(
                _price_bucket(ctx.net_price_units, avg, config.near_equal_tolerance_bps)
            )
            exits = [
                _result(code, status) for code, status in zip(exit_codes, statuses, strict=True)
            ]
        return [*reduced, _result("full_exit", STATUS_HIT), *exits]

    exits = [_result(code, STATUS_NOT_APPLICABLE, "not_full_exit") for code in exit_codes]
    if ctx.quantity_before_units <= 0:
        reduced = [_result(code, STATUS_NOT_APPLICABLE, "no_position") for code in codes]
        return [*reduced, _result("full_exit", STATUS_MISS), *exits]
    if avg is None or avg < 0:
        reason = _cost_gap_reason(ctx, avg)
        reduced = [_result(code, STATUS_NO_COST_BASIS, reason) for code in codes]
        return [*reduced, _result("full_exit", STATUS_MISS), *exits]
    bucket = _price_bucket(ctx.net_price_units, avg, config.near_equal_tolerance_bps)
    # 减仓与清仓的桶→三态映射一致：near→回本、above→止盈、below→止损。
    statuses = _exit_statuses(bucket)
    reduced = [_result(code, status) for code, status in zip(codes, statuses, strict=True)]
    return [*reduced, _result("full_exit", STATUS_MISS), *exits]


def _price_flag(code: str, status: str | None, hit: bool, reason: str | None = None) -> FlagResult:
    if status is not None:
        return _result(code, status, reason or _status_reason(status))
    return _result(code, STATUS_HIT if hit else STATUS_MISS)


def _status_reason(status: str) -> str:
    if status == STATUS_PENDING:
        return "future_window_incomplete"
    if status == STATUS_INSUFFICIENT_HISTORY:
        return "price_history_incomplete"
    if status == STATUS_NO_BAR:
        return "no_daily_bars"
    if status == STATUS_NO_COST_BASIS:
        return "no_cost_basis"
    return "no_trading_calendar"


def _buy_price_flags(
    ctx: TradeContext,
    bars: Sequence[BarPoint],
    calendar: Sequence[date],
    data_as_of: date | None,
    config: TradeFlagConfig,
) -> list[FlagResult]:
    past_status = _past_status(bars, ctx.trade_date, calendar, config.window_trading_days)
    future_status = _future_status(
        bars, ctx.trade_date, calendar, data_as_of, config.window_trading_days
    )
    past = (
        _past_closes(bars, ctx.trade_date, config.window_trading_days)
        if past_status is None
        else []
    )
    future = (
        _future_closes(bars, ctx.trade_date, config.window_trading_days)
        if future_status is None
        else []
    )

    chase_hit = past_status is None and _gt(ctx.net_price_units, max(past), config.chase_margin_bps)
    bottom_hit = past_status is None and _le_within(
        ctx.net_price_units, min(past), config.short_term_margin_bps
    )

    if past_status is not None:
        short_status = past_status
    elif future_status is not None:
        short_status = future_status
    else:
        short_status = None
    short_hit = short_status is None and _le_within(
        ctx.net_price_units, min([*past, *future]), config.short_term_margin_bps
    )
    # 短期被套：窗口内每个交易日收盘价都低于含费净买价（收益持续为负）。
    underwater_hit = short_status is None and all(close < ctx.net_price_units for close in future)

    return [
        _price_flag("chase", past_status, chase_hit),
        _price_flag("instant_bottom", past_status, bottom_hit),
        _price_flag("short_term_bottom", short_status, short_hit),
        _price_flag("short_term_underwater", short_status, underwater_hit),
    ]


def _sell_price_flags(
    ctx: TradeContext,
    bars: Sequence[BarPoint],
    calendar: Sequence[date],
    data_as_of: date | None,
    config: TradeFlagConfig,
) -> list[FlagResult]:
    past_status = _past_status(bars, ctx.trade_date, calendar, config.window_trading_days)
    future_status = _future_status(
        bars, ctx.trade_date, calendar, data_as_of, config.window_trading_days
    )
    past = (
        _past_closes(bars, ctx.trade_date, config.window_trading_days)
        if past_status is None
        else []
    )
    future = (
        _future_closes(bars, ctx.trade_date, config.window_trading_days)
        if future_status is None
        else []
    )

    # 破位条件：净卖价低于前 N 个行情日最低价容差以上（原止损判据）。
    breakdown_hit = past_status is None and _lt(
        ctx.net_price_units, min(past), config.stop_loss_margin_bps
    )
    avg = ctx.avg_cost_units
    # 成本三态：`None` = 无成本数据（无持仓/超卖或成本缺失）、`0` = 零成本持仓（有效成本）、
    # `< 0` = 数据异常；仅 `0` 与正成本参与止损/回撤判定。
    if past_status is not None:
        cost_status = past_status
        cost_reason = None
    elif avg is not None and avg >= 0:
        cost_status = None
        cost_reason = None
    else:
        cost_status = STATUS_NO_COST_BASIS
        cost_reason = _cost_gap_reason(ctx, avg)
    # 止损：破位 + 净卖价严格低于持仓均价；恰等于均价归入「回撤」。
    # 零成本时净卖价不可能为负，止损恒不命中。
    stop_hit = breakdown_hit and avg is not None and avg > 0 and ctx.net_price_units < avg
    # 回撤：破位但净卖价不低于均价（仍在成本线上方，或恰好持平）；零成本恒成立。
    drawdown_hit = breakdown_hit and avg is not None and avg >= 0 and ctx.net_price_units >= avg
    top_hit = past_status is None and _ge_within(
        ctx.net_price_units, max(past), config.short_term_margin_bps
    )

    if past_status is not None:
        short_status = past_status
    elif future_status is not None:
        short_status = future_status
    else:
        short_status = None
    short_hit = short_status is None and _ge_within(
        ctx.net_price_units, max([*past, *future]), config.short_term_margin_bps
    )
    # 短期卖飞：净卖价低于未来窗口最后一个收盘价的容差以上（恰低容差不命中）。
    sold_early_hit = short_status is None and _lt(
        ctx.net_price_units, future[-1], config.short_term_margin_bps
    )

    return [
        _price_flag("stop_loss", cost_status, stop_hit, cost_reason),
        _price_flag("sell_drawdown", cost_status, drawdown_hit, cost_reason),
        _price_flag("instant_top", past_status, top_hit),
        _price_flag("short_term_top", short_status, short_hit),
        _price_flag("short_term_sold_early", short_status, sold_early_hit),
    ]


def evaluate_trade_flags(
    ctx: TradeContext,
    *,
    bars: Sequence[BarPoint],
    calendar: Sequence[date],
    data_as_of: date | None,
    config: TradeFlagConfig = DEFAULT_TRADE_FLAG_CONFIG,
) -> list[FlagResult]:
    """评估单笔账单的全部旗标（按词表顺序返回，含 miss/not_applicable 状态）。

    `config` 为用户级生效阈值；缺省等于 1.4 常量，默认配置下结果逐字段不变。
    """
    if ctx.side == "buy":
        state = _state_flags(ctx, config)
        price = _buy_price_flags(ctx, bars, calendar, data_as_of, config)
    else:
        state = _reduce_flags(ctx, config)
        price = _sell_price_flags(ctx, bars, calendar, data_as_of, config)

    by_code = {item.code: item for item in [*state, *price]}
    codes = BUY_FLAG_CODES if ctx.side == "buy" else SELL_FLAG_CODES
    return [by_code[code] for code in codes]
