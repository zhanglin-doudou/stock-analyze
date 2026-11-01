"""独立 oracle：perf / behavior 域朴素实现（§8.5 三方对拍）。

铁律（与 ``naive_stats`` 一致）：
- 只 import 标准库；**禁止** import stock_contracts / stock_core / stock_review / stock_ledger；
- 不接受生产 DTO 类型：入参是鸭子类型 ``ctx``（只要具备同名属性），由测试层直接
  把 ``MetricContextData`` 传进来，oracle 代码里看不到任何生产类型；
- 公式独立重写、自然单位直算（元 / 股 / 比例），最后才折算到展示标度；
- Gini 用成对差公式（生产用加权和化简），DE 用 Fraction 逐计数。

输出约定：``OracleResult.value`` 是**自然单位**的 ``Fraction``（比例即 0.5 这类，
金额即元），测试层按指标 precision 折算为标度整数；``decomposition`` 直接给
**存储标度整数**（与生产 decomposition 同标度），便于逐键精确比对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any

RATIO_SCALE = 8
AMOUNT_SCALE = 2
PRICE_SCALE = 6
A_SHUFFLE_DAYS = 244
QTY_PRICE_TO_PRICE8 = 10**8  # 数量 scale=4 × 价格 scale=6 → 金额 scale=2
_ADD_CATEGORIES = frozenset({"buy", "bonus", "rights", "new_stock"})
_LEG_CATEGORIES = frozenset({"buy", "sell"})


@dataclass(frozen=True)
class OracleResult:
    value: Fraction | None
    n: int
    decomposition: dict[str, int | str | None] = field(default_factory=dict)


# ── 标度与舍入 ─────────────────────────────────────────────────────────────


def round_units(value: Fraction, scale: int) -> int:
    """half-up（远离零）到 ``scale`` 位小数的标度整数（与生产 div_round 同规）。"""
    scaled = value * 10**scale
    numerator, denominator = scaled.numerator, scaled.denominator
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def units_of(value: Fraction | None, precision: int) -> int | None:
    """自然单位 → 展示标度整数（测试层唯一入口）。"""
    if value is None:
        return None
    return round_units(value, precision)


def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return round_units(Fraction(ordered[middle - 1] + ordered[middle], 2), 0)


def _mean_int(values: list[int]) -> int:
    return round_units(Fraction(sum(values), len(values)), 0)


def _decimal_units(value: Decimal, scale: int) -> int:
    scaled = value.scaleb(scale)
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _decimal_fraction(units: int, scale: int) -> Decimal:
    return Decimal(units).scaleb(-scale)


# ── 基础读取（鸭子类型） ───────────────────────────────────────────────────


def _rounds(ctx: Any) -> tuple[Any, ...]:
    return tuple(ctx.rounds)


def closed_rounds(ctx: Any) -> tuple[Any, ...]:
    return tuple(
        item
        for item in _rounds(ctx)
        if item.status == "closed"
        and not item.data_error
        and ctx.period_start <= item.close_date <= ctx.period_end
    )


def open_rounds(ctx: Any) -> tuple[Any, ...]:
    return tuple(
        item
        for item in _rounds(ctx)
        if item.status == "open" and not item.data_error and item.open_date <= ctx.period_end
    )


def data_error_count(ctx: Any) -> int:
    return sum(1 for item in _rounds(ctx) if item.data_error)


def period_trades(ctx: Any) -> tuple[Any, ...]:
    return tuple(
        item
        for item in ctx.trades
        if item.deleted_at is None and ctx.period_start <= item.trade_date <= ctx.period_end
    )


def trades_until_period_end(ctx: Any) -> tuple[Any, ...]:
    return tuple(
        sorted(
            (
                item
                for item in ctx.trades
                if item.deleted_at is None and item.trade_date <= ctx.period_end
            ),
            key=lambda item: (item.trade_date, item.line_no, item.id),
        )
    )


def asset_days(ctx: Any) -> tuple[Any, ...]:
    return tuple(
        sorted(
            (
                item
                for item in ctx.asset_daily
                if ctx.period_start <= item.trade_date <= ctx.period_end
            ),
            key=lambda item: item.trade_date,
        )
    )


def close_ladders(ctx: Any) -> dict[str, tuple[tuple[date, ...], tuple[int, ...]]]:
    per_symbol: dict[str, dict[date, int]] = {}
    for bar in ctx.bars:
        per_symbol.setdefault(bar.symbol, {})[bar.trade_date] = bar.close_units
    return {
        symbol: (tuple(sorted(points)), tuple(points[day] for day in sorted(points)))
        for symbol, points in per_symbol.items()
    }


def close_on_or_before(
    ladders: dict[str, tuple[tuple[date, ...], tuple[int, ...]]],
    symbol: str,
    day: date,
) -> int | None:
    points = ladders.get(symbol)
    if points is None:
        return None
    dates, closes = points
    found: int | None = None
    for index, trade_date in enumerate(dates):
        if trade_date > day:
            break
        found = closes[index]
    return found


def benchmark_rows(ctx: Any) -> tuple[Any, ...]:
    per_date: dict[date, Any] = {}
    for item in ctx.benchmark_series:
        if not item.available:
            continue
        per_date[item.trade_date] = item
    return tuple(per_date[day] for day in sorted(per_date))


def money_units(quantity_units: int, price_units: int) -> int:
    return quantity_units * price_units // QTY_PRICE_TO_PRICE8


def fee_totals(trades: tuple[Any, ...]) -> dict[str, int]:
    commission = sum(item.commission_units for item in trades)
    stamp_tax = sum(item.stamp_tax_units for item in trades)
    other_fee = sum(item.other_fee_units for item in trades)
    n_fee_rows = 0
    standalone_fee = 0
    for item in trades:
        if item.category != "fee":
            continue
        n_fee_rows += 1
        if item.commission_units == 0 and item.stamp_tax_units == 0 and item.other_fee_units == 0:
            standalone_fee += abs(item.amount_units)
    return {
        "commission": commission,
        "stamp_tax": stamp_tax,
        "other_fee": other_fee,
        "standalone_fee": standalone_fee,
        "fee_amount": commission + stamp_tax + other_fee + standalone_fee,
        "n_fee_rows": n_fee_rows,
    }


def _wide_to_price6(cost_units: int, quantity_units: int) -> int:
    return round_units(Fraction(cost_units * QTY_PRICE_TO_PRICE8, quantity_units), 0)


def compound_return_units(returns: tuple[int, ...]) -> int:
    """连乘 (1+r)，返回 scale=8 标度整数（相当于 1+IRR）。"""
    with localcontext() as ctx:
        ctx.prec = 40
        product = Decimal(1)
        for item in returns:
            product *= Decimal(1) + _decimal_fraction(item, RATIO_SCALE)
        return _decimal_units(product - Decimal(1), RATIO_SCALE)


def annualize_units(twr_units: int, span_days: int) -> int | None:
    """(1+twr)^(365/span) − 1，保留 scale=8；span<=0 或底数<=0 → None。"""
    if span_days <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        base = Decimal(1) + _decimal_fraction(twr_units, RATIO_SCALE)
        if base <= 0:
            return None
        value = base ** (Decimal(365) / Decimal(span_days)) - Decimal(1)
        return _decimal_units(value, RATIO_SCALE)


# ── perf.win_rate ──────────────────────────────────────────────────────────


def win_rate(ctx: Any) -> OracleResult:
    closed = closed_rounds(ctx)
    opened = open_rounds(ctx)
    wins = sum(1 for item in closed if item.pnl_units > 0)
    zeros = sum(1 for item in closed if item.pnl_units == 0)
    all_rounds = closed + opened
    wins_all = sum(1 for item in all_rounds if item.pnl_units > 0)
    decomposition: dict[str, int | str | None] = {
        "n_closed": len(closed),
        "n_open": len(opened),
        "n_data_error": data_error_count(ctx),
        "wins": wins,
        "n_zero": zeros,
        "n_all": len(all_rounds),
        "win_rate_all": None,
    }
    if all_rounds:
        decomposition["win_rate_all"] = round_units(
            Fraction(wins_all, len(all_rounds)), RATIO_SCALE
        )
    if not closed:
        return OracleResult(None, 0, decomposition)
    return OracleResult(Fraction(wins, len(closed)), len(closed), decomposition)


# ── perf.pnl_ratio ─────────────────────────────────────────────────────────


def pnl_ratio(ctx: Any) -> OracleResult:
    closed = closed_rounds(ctx)
    decomposition: dict[str, int | str | None] = {
        "n_win": 0,
        "n_loss": 0,
        "n_zero": 0,
        "avg_win": None,
        "avg_loss": None,
        "median_win": None,
        "median_loss": None,
        "pnl_ratio_median": None,
    }
    if not closed:
        return OracleResult(None, 0, decomposition)
    wins = [item.pnl_units for item in closed if item.pnl_units > 0]
    losses = [-item.pnl_units for item in closed if item.pnl_units < 0]
    zeros = sum(1 for item in closed if item.pnl_units == 0)
    decomposition.update(
        n_win=len(wins),
        n_loss=len(losses),
        n_zero=zeros,
    )
    if not wins or not losses:
        if wins:
            decomposition["avg_win"] = _mean_int(wins)
            decomposition["median_win"] = _median_int(wins)
        if losses:
            decomposition["avg_loss"] = _mean_int(losses)
            decomposition["median_loss"] = _median_int(losses)
        return OracleResult(None, len(closed), decomposition)

    avg_win = Fraction(sum(wins), len(wins))
    avg_loss = Fraction(sum(losses), len(losses))
    median_win = _median_int(wins)
    median_loss = _median_int(losses)
    decomposition.update(
        avg_win=_mean_int(wins),
        avg_loss=_mean_int(losses),
        median_win=median_win,
        median_loss=median_loss,
        pnl_ratio_median=round_units(Fraction(median_win, median_loss), RATIO_SCALE),
    )
    return OracleResult(avg_win / avg_loss, len(closed), decomposition)


# ── perf.expectancy ────────────────────────────────────────────────────────


def expectancy(ctx: Any) -> OracleResult:
    closed = closed_rounds(ctx)
    decomposition: dict[str, int | str | None] = {
        "mean_pnl": None,
        "p": None,
        "win_loss_ratio": None,
        "p_star": None,
        "n_win": 0,
        "n_loss": 0,
        "n_zero": 0,
    }
    if not closed:
        return OracleResult(None, 0, decomposition)
    wins = [item.pnl_units for item in closed if item.pnl_units > 0]
    losses = [-item.pnl_units for item in closed if item.pnl_units < 0]
    zeros = sum(1 for item in closed if item.pnl_units == 0)
    count = len(closed)
    p = Fraction(len(wins), count)
    decomposition.update(
        mean_pnl=_mean_int([item.pnl_units for item in closed]),
        p=round_units(p, RATIO_SCALE),
        n_win=len(wins),
        n_loss=len(losses),
        n_zero=zeros,
    )
    mean_win = Fraction(sum(wins), len(wins)) if wins else Fraction(0)
    mean_loss = Fraction(sum(losses), len(losses)) if losses else Fraction(0)
    if wins and losses:
        decomposition["win_loss_ratio"] = round_units(
            Fraction(sum(wins) * len(losses), sum(losses) * len(wins)), RATIO_SCALE
        )
    if not wins:
        decomposition["p_star"] = 10**RATIO_SCALE
    elif losses:
        decomposition["p_star"] = round_units(mean_loss / (mean_win + mean_loss), RATIO_SCALE)
    expectation_cents = p * mean_win - (1 - p) * mean_loss
    return OracleResult(expectation_cents / 100, count, decomposition)


# ── perf.concentration ─────────────────────────────────────────────────────


def concentration(ctx: Any) -> OracleResult:
    winners = sorted(
        (item.pnl_units for item in closed_rounds(ctx) if item.pnl_units > 0),
        reverse=True,
    )
    decomposition: dict[str, int | str | None] = {
        "cr1": None,
        "gini": None,
        "n_pos": len(winners),
        "top3_k": 0,
        "top3_amount": 0,
        "positive_total": 0,
    }
    if not winners:
        return OracleResult(None, 0, decomposition)

    total = sum(winners)
    top = winners[:3]
    top_amount = sum(top)
    decomposition.update(
        cr1=round_units(Fraction(winners[0], total), RATIO_SCALE),
        top3_amount=top_amount,
        positive_total=total,
        top3_k=len(top),
    )
    if len(winners) >= 2 and total > 0:
        ascending = sorted(winners)
        pairwise = sum(abs(left - right) for left in ascending for right in ascending)
        decomposition["gini"] = round_units(
            Fraction(pairwise, 2 * len(ascending) * total), RATIO_SCALE
        )
    return OracleResult(Fraction(top_amount, total), len(winners), decomposition)


# ── perf.holding_days ──────────────────────────────────────────────────────


def holding_days(ctx: Any) -> OracleResult:
    closed = closed_rounds(ctx)
    decomposition: dict[str, int | str | None] = {
        "median_holding_days": None,
        "mean_natural_days": None,
        "median_natural_days": None,
        "capital_weighted_days": None,
    }
    if not closed:
        return OracleResult(None, 0, decomposition)

    trading = [item.holding_days for item in closed]
    natural = [(item.close_date - item.open_date).days for item in closed]
    ids = {item.id for item in closed}
    fills = [item for item in ctx.fills if item.round_id in ids]
    capital = sum(item.buy_cost_units for item in fills)
    capital_days = sum(item.holding_days * item.buy_cost_units for item in fills)
    decomposition.update(
        median_holding_days=_median_int([item * 100 for item in trading]),
        mean_natural_days=_mean_int([item * 100 for item in natural]),
        median_natural_days=_median_int([item * 100 for item in natural]),
        capital_weighted_days=(
            round_units(Fraction(capital_days * 100, capital), 0) if capital > 0 else None
        ),
    )
    return OracleResult(Fraction(sum(trading), len(trading)), len(closed), decomposition)


# ── perf.fee ───────────────────────────────────────────────────────────────


def fee(ctx: Any) -> OracleResult:
    trades = period_trades(ctx)
    totals = fee_totals(trades)
    legs = [item for item in trades if item.category in _LEG_CATEGORIES]
    turnover_gross = sum(money_units(item.quantity_units, item.price_units) for item in legs)
    turnover_net = sum(abs(item.amount_units) for item in legs)
    decomposition: dict[str, int | str | None] = {
        "fee_amount": totals["fee_amount"],
        "commission": totals["commission"],
        "stamp_tax": totals["stamp_tax"],
        "other_fee": totals["other_fee"],
        "standalone_fee": totals["standalone_fee"],
        "turnover_gross": turnover_gross,
        "turnover_net": turnover_net,
        "fee_ratio_net": (
            round_units(Fraction(totals["fee_amount"], turnover_net), RATIO_SCALE)
            if turnover_net > 0
            else None
        ),
        "n_fee_rows": totals["n_fee_rows"],
    }
    value = Fraction(totals["fee_amount"], turnover_gross) if turnover_gross > 0 else None
    return OracleResult(value, len(legs), decomposition)


# ── perf.fee_breakeven ─────────────────────────────────────────────────────


def fee_breakeven(ctx: Any) -> OracleResult:
    rows = asset_days(ctx)
    totals = fee_totals(period_trades(ctx))
    n_blocked = sum(1 for item in rows if item.status == "blocked")
    decomposition: dict[str, int | str | None] = {
        "fee_amount": totals["fee_amount"],
        "n_blocked_days": n_blocked,
    }
    if n_blocked > 0 or not rows:
        return OracleResult(None, 0, decomposition)

    natural_days = (ctx.period_end - ctx.period_start).days + 1
    annualized_fee = round_units(Fraction(totals["fee_amount"] * 365, natural_days), 0)
    avg_total_assets = _mean_int([item.total_assets_units for item in rows])
    returns = tuple(item.daily_return_units for item in rows if item.daily_return_units is not None)
    twr = compound_return_units(returns) if returns else None
    span_days = (rows[-1].trade_date - rows[0].trade_date).days
    annualized_twr = annualize_units(twr, span_days) if twr is not None else None
    annualized_fee_ratio = (
        round_units(Fraction(annualized_fee, avg_total_assets), RATIO_SCALE)
        if avg_total_assets > 0
        else None
    )

    twr_eaten: int | None = None
    if annualized_twr is not None and twr is not None and twr > 0:
        twr_amount = round_units(Fraction(avg_total_assets * annualized_twr, 10**RATIO_SCALE), 0)
        denominator = abs(twr_amount) + annualized_fee
        if denominator > 0:
            twr_eaten = round_units(Fraction(annualized_fee, denominator), RATIO_SCALE)

    decomposition.update(
        avg_total_assets=avg_total_assets,
        annualized_fee=annualized_fee,
        annualized_fee_ratio=annualized_fee_ratio,
        twr=twr,
        annualized_twr=annualized_twr,
        twr_eaten_pct=twr_eaten,
        n_days=len(rows),
    )
    value = Fraction(totals["fee_amount"], avg_total_assets) if avg_total_assets > 0 else None
    return OracleResult(value, len(rows), decomposition)


# ── behavior.turnover ──────────────────────────────────────────────────────


def turnover(ctx: Any) -> OracleResult:
    legs = [item for item in period_trades(ctx) if item.category in _LEG_CATEGORIES]
    num = sum(abs(item.amount_units) for item in legs)
    buy_amount = sum(abs(item.amount_units) for item in legs if item.category == "buy")
    sell_amount = sum(abs(item.amount_units) for item in legs if item.category == "sell")
    rows = asset_days(ctx)
    n_blocked = sum(1 for item in rows if item.status == "blocked")
    used = [item for item in rows if item.status != "blocked"]
    n_used = len(used)
    avg_mv = _mean_int([item.market_value_units for item in used]) if n_used else 0
    raw = round_units(Fraction(num, avg_mv), RATIO_SCALE) if avg_mv > 0 else None
    decomposition: dict[str, int | str | None] = {
        "double_sided_amount": num,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "avg_market_value": avg_mv,
        "raw_turnover": raw,
        "n_trading_days": sum(1 for item in used if item.trade_date.weekday() < 5),
        "n_natural_days_used": n_used,
        "n_blocked_days": n_blocked,
    }
    if avg_mv == 0 and num > 0:
        value = None
    elif avg_mv == 0 or n_used == 0:
        value = Fraction(0)
    else:
        value = Fraction(num * A_SHUFFLE_DAYS, avg_mv * n_used)
    return OracleResult(value, n_used, decomposition)


# ── behavior.averaging_down ────────────────────────────────────────────────


def averaging_down(ctx: Any) -> OracleResult:
    positions: dict[str, list[int]] = {}
    candidates: list[tuple[int, int]] = []
    depths: list[Fraction] = []
    n_first_build = 0
    n_zero_cost = 0
    n_invalid_price = 0

    for trade in trades_until_period_end(ctx):
        symbol = trade.security_code
        quantity_before, cost_before = positions.get(symbol, [0, 0])
        in_period = trade.trade_date >= ctx.period_start
        if trade.category in _ADD_CATEGORIES:
            if trade.category == "buy" and in_period:
                if quantity_before <= 0:
                    n_first_build += 1
                elif cost_before <= 0:
                    n_zero_cost += 1
                elif trade.price_units <= 0:
                    n_invalid_price += 1
                else:
                    average_price6 = _wide_to_price6(cost_before, quantity_before)
                    candidates.append((average_price6, trade.price_units))
                    if trade.price_units < average_price6:
                        depths.append(Fraction(average_price6 - trade.price_units, average_price6))
            positions[symbol] = [
                quantity_before + trade.quantity_units,
                cost_before + abs(trade.amount_units),
            ]
        elif trade.category == "sell":
            quantity_after = quantity_before - trade.quantity_units
            if quantity_before > 0:
                removed = min(trade.quantity_units, quantity_before)
                cost_after = cost_before - cost_before * removed // quantity_before
            else:
                cost_after = cost_before
            if quantity_after <= 0:
                quantity_after, cost_after = 0, 0
            positions[symbol] = [quantity_after, cost_after]

    decomposition: dict[str, int | str | None] = {
        "n_avg_down": len(depths),
        "n_addon": len(candidates),
        "avg_depth": (
            round_units(sum(depths, Fraction(0)) / len(depths), RATIO_SCALE) if depths else None
        ),
        "fwd20_excess_mean": None,
        "n_first_build": n_first_build,
        "n_zero_cost": n_zero_cost,
        "n_invalid_price": n_invalid_price,
    }
    value = Fraction(len(depths), len(candidates)) if candidates else None
    return OracleResult(value, len(candidates), decomposition)


# ── behavior.de ────────────────────────────────────────────────────────────


def behavior_de(ctx: Any) -> OracleResult:
    closed = closed_rounds(ctx)
    opened = open_rounds(ctx)
    sold_gain = sum(1 for item in closed if item.pnl_units > 0)
    sold_loss = sum(1 for item in closed if item.pnl_units < 0)
    paper_gain = sum(1 for item in opened if item.pnl_units > 0)
    paper_loss = sum(1 for item in opened if item.pnl_units < 0)
    n_zero = len(closed) - sold_gain - sold_loss
    gain_total = sold_gain + paper_gain
    loss_total = sold_loss + paper_loss

    decomposition: dict[str, int | str | None] = {
        "n_sold_gain": sold_gain,
        "n_sold_loss": sold_loss,
        "n_paper_gain": paper_gain,
        "n_paper_loss": paper_loss,
        "n_zero": n_zero,
        "n_data_error": data_error_count(ctx),
        "pgr": (round_units(Fraction(sold_gain, gain_total), RATIO_SCALE) if gain_total else None),
        "plr": (round_units(Fraction(sold_loss, loss_total), RATIO_SCALE) if loss_total else None),
        "holding_days_diff": None,
    }
    gain_items = [item for item in closed if item.status == "closed" and item.pnl_units > 0]
    loss_items = [item for item in closed if item.status == "closed" and item.pnl_units < 0]
    if gain_items and loss_items:
        mean_gain = _mean_int([item.holding_days * 100 for item in gain_items])
        mean_loss = _mean_int([item.holding_days * 100 for item in loss_items])
        decomposition["holding_days_diff"] = mean_loss - mean_gain

    if gain_total == 0 or loss_total == 0:
        value = None
    else:
        value = Fraction(sold_gain, gain_total) - Fraction(sold_loss, loss_total)
    return OracleResult(value, gain_total + loss_total, decomposition)


# ── behavior.hhi ───────────────────────────────────────────────────────────


def hhi(ctx: Any) -> OracleResult:
    ladders = close_ladders(ctx)
    trades = trades_until_period_end(ctx)
    totals: list[int] = []
    equities: list[int] = []
    counts: list[int] = []
    cash_weights: list[int] = []
    n_blocked = 0
    n_oversell = 0
    n_missing = 0
    n_recon = 0
    n_empty = 0

    for row in asset_days(ctx):
        quantity: dict[str, int] = {}
        for trade in trades:
            if trade.trade_date > row.trade_date:
                break
            symbol = trade.security_code
            if trade.category in _ADD_CATEGORIES:
                quantity[symbol] = quantity.get(symbol, 0) + trade.quantity_units
            elif trade.category == "sell":
                quantity[symbol] = quantity.get(symbol, 0) - trade.quantity_units
        if row.status == "blocked":
            n_blocked += 1
            continue
        if any(value < 0 for value in quantity.values()):
            n_oversell += 1
            continue
        market_values: list[int] = []
        missing = False
        for symbol, held in quantity.items():
            if held == 0:
                continue
            close = close_on_or_before(ladders, symbol, row.trade_date)
            if close is None:
                missing = True
                break
            market_values.append(money_units(held, close))
        if missing:
            n_missing += 1
            continue
        sum_mv = sum(market_values)
        if sum_mv != row.market_value_units:
            n_recon += 1
            continue
        if not market_values:
            n_empty += 1
        total_assets = sum_mv + row.cash_units
        if total_assets != 0:
            hhi_total = Fraction(
                sum(value * value for value in market_values) + row.cash_units * row.cash_units,
                total_assets * total_assets,
            )
        else:
            hhi_total = Fraction(0)
        totals.append(round_units(hhi_total, RATIO_SCALE))
        if sum_mv > 0:
            equities.append(
                round_units(
                    Fraction(sum(value * value for value in market_values), sum_mv * sum_mv),
                    RATIO_SCALE,
                )
            )
        counts.append(len(market_values))
        if total_assets > 0:
            cash_weights.append(round_units(Fraction(row.cash_units, total_assets), RATIO_SCALE))

    decomposition: dict[str, int | str | None] = {
        "hhi_total_max": max(totals) if totals else None,
        "hhi_total_end": totals[-1] if totals else None,
        "hhi_eq_mean": _mean_int(equities) if equities else None,
        "hhi_eq_max": max(equities) if equities else None,
        "hhi_eq_end": equities[-1] if equities else None,
        "avg_holding_count": _mean_int([value * 100 for value in counts]) if counts else None,
        "avg_cash_weight": _mean_int(cash_weights) if cash_weights else None,
        "n_days_used": len(totals),
        "n_blocked_days": n_blocked,
        "n_days_empty": n_empty,
        "n_recon_mismatch": n_recon,
        "n_missing_price": n_missing,
        "n_oversell": n_oversell,
    }
    if not totals:
        return OracleResult(None, 0, decomposition)
    value = Fraction(sum(totals), len(totals) * 10**RATIO_SCALE)
    return OracleResult(value, len(totals), decomposition)


__all__ = [
    "OracleResult",
    "asset_days",
    "averaging_down",
    "behavior_de",
    "benchmark_rows",
    "close_ladders",
    "close_on_or_before",
    "closed_rounds",
    "compound_return_units",
    "concentration",
    "data_error_count",
    "expectancy",
    "fee",
    "fee_breakeven",
    "fee_totals",
    "hhi",
    "holding_days",
    "money_units",
    "open_rounds",
    "period_trades",
    "pnl_ratio",
    "round_units",
    "trades_until_period_end",
    "turnover",
    "units_of",
    "win_rate",
]
