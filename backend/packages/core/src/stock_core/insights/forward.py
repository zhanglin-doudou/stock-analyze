"""前瞻收益 B0~B3（设计 §3.3.2，评审 P0-1/P0-2/P0-5）。

公式（全部标度整数）：
- `P_gross = trade.price`；`P_net = |amount| / qty`（含费）；
- B0 毛：买 `(C0−Pg)/Pg`、卖 `(Pg−C0)/C0`；B0n 同式用 `P_net`；
- B1 = `CN/C0 − 1`；B2 = `CN/P_net − 1`（买卖同式，卖后正=价格高于卖出净价）；
- B3 = B1 − 基准 N 日收益（与 `selection.bhar_20` 同一锚定/复利规则）。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Any

from stock_contracts.constants import SCALE_RATIO
from stock_contracts.enums import ForwardReturnStatus

from stock_core.insights import calendar as cal
from stock_core.metrics.portfolio_index import (
    benchmark_adjusted_excess_units,
    compound_index,
)
from stock_core.stats.numeric import div_round

_ONE = 10**SCALE_RATIO
_MONEY_QTY_TO_PRICE = 10**8
_MAX_SHIFT_TRADING_DAYS = 1
_STALE_MAX_TRADING_DAYS = 5


@dataclass(frozen=True, slots=True)
class ForwardTrade:
    """计算所需的成交事实（与 ORM 解耦）。"""

    dedupe_key: str
    trade_id: str
    version: int
    side: str
    d0: date
    price_units: int
    amount_units: int
    quantity_units: int


@dataclass(frozen=True, slots=True)
class ForwardResult:
    horizon: int
    status: str
    base_shifted: bool
    price_gross_units: int | None
    price_net_units: int | None
    c0_units: int | None
    cn_units: int | None
    bench_ret_units: int | None
    ret_b0_units: int | None
    ret_b0_net_units: int | None
    ret_b1_units: int | None
    ret_b2_units: int | None
    ret_b3_units: int | None
    max_close_units: int | None
    min_close_units: int | None
    price_jump_review: bool


def price_net(trade: ForwardTrade) -> int | None:
    """含费净价（scale=6）；数量为 0 或成交额为 0 → None。"""
    if trade.quantity_units == 0 or trade.amount_units == 0:
        return None
    return abs(trade.amount_units) * _MONEY_QTY_TO_PRICE // trade.quantity_units


def compute_forward(
    trade: ForwardTrade,
    *,
    horizon: int,
    calendar: Sequence[date],
    bars: Sequence[tuple[date, int]],
    benchmark_series: Sequence[tuple[date, int]] | None,
    data_as_of: date | None,
    corporate_action: bool = False,
) -> ForwardResult:
    """单笔交易 × 单 horizon 的前瞻收益；缺失/降级一律显式 null + status。"""
    pnet = price_net(trade)

    def build(status: str, *, base_shifted: bool = False, **values: object) -> ForwardResult:
        payload: dict[str, Any] = {
            "horizon": horizon,
            "status": status,
            "base_shifted": base_shifted,
            "price_gross_units": trade.price_units,
            "price_net_units": pnet,
            "c0_units": None,
            "cn_units": None,
            "bench_ret_units": None,
            "ret_b0_units": None,
            "ret_b0_net_units": None,
            "ret_b1_units": None,
            "ret_b2_units": None,
            "ret_b3_units": None,
            "max_close_units": None,
            "min_close_units": None,
            "price_jump_review": False,
        }
        payload.update(values)
        return ForwardResult(**payload)

    if not calendar:
        return build(ForwardReturnStatus.NO_CALENDAR)

    index = cal.base_index(calendar, trade.d0)
    if index is None:
        return build(ForwardReturnStatus.NO_CALENDAR)
    base = calendar[index]
    base_shifted = base != trade.d0
    if base_shifted and cal.count_between(calendar, base, trade.d0) > _MAX_SHIFT_TRADING_DAYS:
        # 有序日历下 base 是 ≤d0 的最后一个交易日，两者之间不可能再有交易日；
        # 保留防御式判定（非有序/含重复日历时仍生效）。
        return build(ForwardReturnStatus.NO_BAR, base_shifted=True)  # pragma: no cover

    if corporate_action:
        return build(ForwardReturnStatus.CORPORATE_ACTION, base_shifted=base_shifted)

    target = cal.nth_after(calendar, index, horizon)
    if target is None or (data_as_of is not None and target > data_as_of):
        return build(ForwardReturnStatus.PENDING, base_shifted=base_shifted)

    c0 = _close_on_or_before(bars, trade.d0)
    if not c0:
        return build(ForwardReturnStatus.NO_BAR, base_shifted=base_shifted)

    window = [(day, close) for day, close in bars if trade.d0 < day <= target]
    max_close = max((close for _, close in window), default=None)
    min_close = min((close for _, close in window), default=None)
    # c0 存在 ⇒ 必然存在 ≤ target 的 bar，cn 与 last_day 都不会是 None。
    cn = _close_on_or_before(bars, target)
    assert cn is not None  # 上面已确认存在 ≤ target 的 bar
    last_day = max(day for day, _ in bars if day <= target)
    stale_days = cal.count_between(calendar, last_day, target)
    if stale_days > _STALE_MAX_TRADING_DAYS:
        return build(ForwardReturnStatus.NO_BAR, base_shifted=base_shifted)
    if stale_days > 0:
        # 停牌 ≤5 个交易日：沿用最近收盘但不出收益（显示 — + stale_days）。
        return build(
            ForwardReturnStatus.STALE_PRICE,
            base_shifted=base_shifted,
            c0_units=c0,
            cn_units=cn,
            max_close_units=max_close,
            min_close_units=min_close,
            price_jump_review=_price_jump(window),
        )

    bench_ret = _benchmark_return(benchmark_series, base, target)
    pnet = price_net(trade)
    b0 = _execution_gap(trade.side, trade.price_units, c0)
    b0n = _execution_gap(trade.side, pnet, c0) if pnet else None
    # 个股腿满值 CN/C0，B1 与 B3 都由它派生，和 selection.bhar_* 共用同一口径。
    stock_leg = div_round(cn, c0, scale=SCALE_RATIO)
    b1 = stock_leg - _ONE
    b2 = div_round(cn - pnet, pnet, scale=SCALE_RATIO) if pnet else None
    status = ForwardReturnStatus.OK
    b3: int | None = None
    if bench_ret is None:
        status = ForwardReturnStatus.DIVISOR_MISSING
    else:
        b3 = benchmark_adjusted_excess_units(stock_leg, bench_ret)

    return build(
        status,
        base_shifted=base_shifted,
        c0_units=c0,
        cn_units=cn,
        bench_ret_units=bench_ret,
        ret_b0_units=b0,
        ret_b0_net_units=b0n,
        ret_b1_units=b1,
        ret_b2_units=b2,
        ret_b3_units=b3,
        max_close_units=max_close,
        min_close_units=min_close,
        price_jump_review=_price_jump(window),
    )


def _benchmark_return(
    series: Sequence[tuple[date, int]] | None, base: date, target: date
) -> int | None:
    """`(base, target]` 的基准收益（与 BHAR 相同的复利，返回 gain 单位）。"""
    if series is None:
        return None
    legs = [ret for day, ret in series if base < day <= target]
    return compound_index(legs) if legs else None


def _execution_gap(side: str, price: int | None, c0: int) -> int | None:
    if price is None or price <= 0 or c0 <= 0:
        return None
    if side == "buy":
        return div_round(c0 - price, price, scale=SCALE_RATIO)
    return div_round(price - c0, c0, scale=SCALE_RATIO)


def _close_on_or_before(bars: Sequence[tuple[date, int]], day: date) -> int | None:
    dates = [item[0] for item in bars]
    index = bisect_right(dates, day) - 1
    return bars[index][1] if index >= 0 else None


def _price_jump(window: Sequence[tuple[date, int]]) -> bool:
    """窗口内单日收盘涨跌 >25% → 需人工复核（未知公司行动的代理信号）。"""
    for (_, prev), (_, curr) in pairwise(window):
        if prev > 0 and 4 * abs(curr - prev) > prev:
            return True
    return False
