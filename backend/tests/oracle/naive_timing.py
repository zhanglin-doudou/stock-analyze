"""独立 oracle：timing 域朴素实现（§8.5）。

- chase / fly：逐笔事件后按 bar 日期升序取窗口，直接扫最大收盘价；
- beta：中心化 OLS（Σ(x-x̄)(y-ȳ)/Σ(x-x̄)²），生产用 n·Σxy−Σx·Σy 展开式；
- drawdown：峰-谷-恢复**暴力扫描**（枚举所有 (i, j) 组合求最大回撤），
  生产用单遍 running-peak 更新。

只 import 标准库；入参为鸭子类型 ctx。value 为自然单位比例。
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any

from .naive_metrics import (
    A_SHUFFLE_DAYS,
    RATIO_SCALE,
    OracleResult,
    asset_days,
    benchmark_rows,
    close_ladders,
    period_trades,
    round_units,
)

_THRESHOLD_FACTOR = Fraction(103, 100)  # 1 + 3%：追涨 / 卖飞阈值
_POST_WINDOW = 20
_FLY_WINDOWS = (5, 10)


def _price_break(post_max_units: int, entry_units: int, factor: Fraction) -> bool:
    return Fraction(post_max_units) > Fraction(entry_units) * factor


def _forward_bars(ctx: Any, symbol: str, day: date, window: int) -> tuple[int, ...]:
    """严格晚于 ``day`` 且不晚于 ``as_of`` 的前 ``window`` 根 bar 收盘价。"""
    points = close_ladders(ctx).get(symbol)
    if points is None:
        return ()
    dates, closes = points
    later = [closes[index] for index, item in enumerate(dates) if day < item <= ctx.as_of]
    return tuple(later[:window])


def _events(ctx: Any, category: str) -> tuple[Any, ...]:
    return tuple(
        sorted(
            (item for item in period_trades(ctx) if item.category == category),
            key=lambda item: (item.trade_date, item.line_no, item.id),
        )
    )


def _share(ctx: Any, *, category: str, window: int) -> tuple[Fraction | None, int, int]:
    """返回 (突破占比, 有效事件数, pending 数)；按单个窗口独立统计。"""
    events = _events(ctx, category)
    pairs: list[tuple[int, int]] = []
    n_pending = 0
    for trade in events:
        if trade.price_units <= 0:
            n_pending += 1
            continue
        closes = _forward_bars(ctx, trade.security_code, trade.trade_date, window)
        if len(closes) < window:
            n_pending += 1
            continue
        pairs.append((trade.price_units, max(closes)))
    if not pairs:
        return None, 0, n_pending
    hits = sum(1 for entry, post in pairs if _price_break(post, entry, _THRESHOLD_FACTOR))
    return Fraction(hits, len(pairs)), len(pairs), n_pending


def chase(ctx: Any) -> OracleResult:
    value, n_valid, n_pending = _share(ctx, category="buy", window=_POST_WINDOW)
    decomposition: dict[str, int | str | None] = {
        "n_events": len(_events(ctx, "buy")),
        "n_valid": n_valid,
        "n_pending": n_pending,
        "post_window": _POST_WINDOW,
    }
    return OracleResult(value, n_valid, decomposition)


def fly(ctx: Any) -> OracleResult:
    n_events = len(_events(ctx, "sell"))
    decomposition: dict[str, int | str | None] = {
        "n_events": n_events,
        "n_valid": 0,
        "n_pending": 0,
        "fly_share_h5": None,
        "fly_share_h10": None,
        "benchmark_id": ctx.benchmark,
    }
    bench = benchmark_rows(ctx)
    if ctx.benchmark is None or not bench:
        return OracleResult(None, 0, decomposition)

    value, n_valid, n_pending = _share(ctx, category="sell", window=_POST_WINDOW)
    share_h5, _, _ = _share(ctx, category="sell", window=_FLY_WINDOWS[0])
    share_h10, _, _ = _share(ctx, category="sell", window=_FLY_WINDOWS[1])
    decomposition.update(
        n_valid=n_valid,
        n_pending=n_pending,
        fly_share_h5=units_of_ratio(share_h5),
        fly_share_h10=units_of_ratio(share_h10),
    )
    return OracleResult(value, n_valid, decomposition)


def units_of_ratio(value: Fraction | None) -> int | None:
    if value is None:
        return None
    return round_units(value, RATIO_SCALE)


# ── timing.beta ────────────────────────────────────────────────────────────


def _decimal(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def _q(value: Decimal, scale: int) -> int:
    return int(value.scaleb(scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def beta(ctx: Any) -> OracleResult:
    base: dict[str, int | str | None] = {
        "benchmark_id": ctx.benchmark,
        "rf_rate": str(ctx.rf_annual),
        "a_base": A_SHUFFLE_DAYS,
    }
    period_rows = asset_days(ctx)
    n_blocked = sum(1 for item in period_rows if item.status == "blocked")
    n_degraded = sum(1 for item in period_rows if item.status == "degraded")
    if n_blocked or n_degraded:
        return OracleResult(
            None,
            0,
            {**base, "n_blocked_rows": n_blocked, "n_degraded_rows": n_degraded},
        )
    bench = benchmark_rows(ctx)
    if ctx.benchmark is None or not bench:
        return OracleResult(None, 0, {**base, "n_obs": 0, "n_missing": len(period_rows)})
    bench_map = {item.trade_date: item.return_units for item in bench}

    candidates = sorted(
        (
            item
            for item in ctx.asset_daily
            if item.trade_date <= ctx.period_end and item.status != "blocked"
        ),
        key=lambda item: item.trade_date,
    )
    pairs = [
        (item.daily_return_units, bench_map[item.trade_date])
        for item in candidates
        if item.daily_return_units is not None and item.trade_date in bench_map
    ]
    n_missing = len(candidates) - len(pairs)
    window = pairs[-60:]
    decomposition: dict[str, int | str | None] = {
        **base,
        "n_obs": len(window),
        "n_missing": n_missing,
        "sxx_zero": 0,
    }
    if not window:
        return OracleResult(None, 0, decomposition)
    if any(abs(item[0]) > 10**RATIO_SCALE for item in window):
        decomposition["unreliable"] = 1
        return OracleResult(None, len(window), decomposition)

    with localcontext() as lc:
        lc.prec = 50
        rf_units = _q(
            (Decimal(1) + Decimal(str(ctx.rf_annual))) ** (Decimal(1) / Decimal(A_SHUFFLE_DAYS))
            - Decimal(1),
            12,
        )
        rf_daily = _decimal(Fraction(rf_units, 10**12))
        ys = [_decimal(Fraction(item[0], 10**RATIO_SCALE)) - rf_daily for item in window]
        xs = [_decimal(Fraction(item[1], 10**RATIO_SCALE)) - rf_daily for item in window]
        count = len(window)
        if count < 3:
            return OracleResult(None, count, {**decomposition, "sxx_zero": 0})

        mean_x = sum(xs) / count
        mean_y = sum(ys) / count
        sxx = sum((value - mean_x) ** 2 for value in xs)
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
        syy = sum((value - mean_y) ** 2 for value in ys)
        if sxx <= 0:
            decomposition["sxx_zero"] = 1
            return OracleResult(None, count, decomposition)

        slope = sxy / sxx
        intercept = mean_y - slope * mean_x
        rss = syy - sxy * sxy / sxx
        sse = rss / count
        if sse <= 0:
            sse = Decimal("1e-28")
        s_sq = sse / (count - 2)
        var_intercept = s_sq * (Decimal(1) / count + mean_x * mean_x / sxx)
        se_intercept = var_intercept.sqrt()
        t_intercept = intercept / se_intercept

        excess = [y - slope * x for x, y in zip(xs, ys, strict=True)]
        mean_excess = sum(excess) / count
        var_excess = sum((value - mean_excess) ** 2 for value in excess) / (count - 1)
        if var_excess <= 0:
            var_excess = Decimal("1e-28")
        stdev_excess = var_excess.sqrt()

        base_dec = Decimal(A_SHUFFLE_DAYS)
        te = stdev_excess * base_dec.sqrt()
        ir = mean_excess / stdev_excess * base_dec.sqrt()

        product_p = Decimal(1)
        product_b = Decimal(1)
        for item in window:
            product_p *= Decimal(1) + _decimal(Fraction(item[0], 10**RATIO_SCALE))
            product_b *= Decimal(1) + _decimal(Fraction(item[1], 10**RATIO_SCALE))
        excess_geo = (
            (product_p / product_b) ** (base_dec / Decimal(count)) - Decimal(1)
            if product_b > 0
            else Decimal(0)
        )

        decomposition.update(
            alpha_annual=_q(intercept * base_dec, RATIO_SCALE),
            alpha_t=_q(t_intercept, 4),
            ir=_q(ir, RATIO_SCALE),
            tracking_error=_q(te, RATIO_SCALE),
            excess_arith=_q(mean_excess * base_dec, RATIO_SCALE),
            excess_geo=_q(excess_geo, RATIO_SCALE),
        )
        return OracleResult(Fraction(_q(slope, RATIO_SCALE), 10**RATIO_SCALE), count, decomposition)


# ── timing.drawdown ────────────────────────────────────────────────────────


def _compound_index(returns: tuple[int, ...]) -> list[Decimal]:
    with localcontext() as lc:
        lc.prec = 50
        indices = [Decimal(1)]
        for item in returns:
            indices.append(indices[-1] * (Decimal(1) + Decimal(item).scaleb(-RATIO_SCALE)))
        return indices


def _walk(indices: list[Decimal], n: int) -> dict[str, int | bool | None]:
    """暴力枚举所有 (峰, 谷) 组合；恢复点按峰后首个回到峰值的交易日。"""
    with localcontext() as lc:
        lc.prec = 50
        peaks = [Decimal(0)] * (n + 1)
        peaks[0] = indices[0]
        for index in range(1, n + 1):
            peaks[index] = max(peaks[index - 1], indices[index])

        max_dd = Decimal(0)
        mdd_idx = 0
        for i in range(n + 1):
            if indices[i] <= 0:
                continue
            for j in range(i + 1, n + 1):
                drawdown = (indices[i] - indices[j]) / indices[i]
                if drawdown > max_dd or (drawdown == max_dd and drawdown > 0 and j < mdd_idx):
                    max_dd = drawdown
                    mdd_idx = j

        underwater = 0
        ulcer_sum = Decimal(0)
        for index in range(1, n + 1):
            drawdown = (
                (peaks[index] - indices[index]) / peaks[index] if peaks[index] > 0 else Decimal(0)
            )
            if drawdown > 0:
                underwater += 1
            ulcer_sum += drawdown * drawdown
        ulcer = (ulcer_sum / Decimal(n)).sqrt()

        peak_value = peaks[mdd_idx]
        peak_start = mdd_idx
        for index in range(mdd_idx - 1, -1, -1):
            if indices[index] >= peak_value:
                peak_start = index
                break
        recovery_idx: int | None = None
        for index in range(mdd_idx + 1, n + 1):
            if indices[index] >= peak_value:
                recovery_idx = index
                break

        twr = indices[-1] - Decimal(1)
        return {
            "max_dd_units": _q(max_dd, RATIO_SCALE),
            "underwater_units": _q(Decimal(underwater) / Decimal(n), RATIO_SCALE),
            "ulcer_units": _q(ulcer, RATIO_SCALE),
            "twr_units": _q(twr, RATIO_SCALE),
            "duration": (mdd_idx - peak_start) if max_dd > 0 else None,
            "recovery": (recovery_idx - mdd_idx) if recovery_idx is not None else None,
            "recovered": recovery_idx is not None,
        }


def drawdown(ctx: Any) -> OracleResult:
    rows = asset_days(ctx)
    n_blocked = sum(1 for item in rows if item.status == "blocked")
    n_degraded = sum(1 for item in rows if item.status == "degraded")
    if n_blocked or n_degraded:
        return OracleResult(
            None,
            0,
            {"n_blocked_rows": n_blocked, "n_degraded_rows": n_degraded},
        )
    usable = [item for item in rows if item.daily_return_units is not None]
    n_missing = len(rows) - len(usable)
    returns = tuple(item.daily_return_units for item in usable)
    natural_days = (ctx.period_end - ctx.period_start).days + 1
    n_trading_days = sum(1 for item in usable if item.trade_date.weekday() < 5)
    n_obs = len(returns)
    if n_obs < 2:
        return OracleResult(
            None,
            n_obs,
            {
                "underwater_ratio": None,
                "ulcer": None,
                "calmar": None,
                "twr": None,
                "ann_twr": None,
                "n_obs": n_obs,
                "n_missing": n_missing,
                "n_trading_days": n_trading_days,
            },
        )

    indices = _compound_index(returns)
    walk = _walk(indices, n_obs)
    with localcontext() as lc:
        lc.prec = 50
        if natural_days > 0 and indices[-1] > 0:
            annual = indices[-1] ** (Decimal(365) / Decimal(natural_days)) - Decimal(1)
            ann_twr = _q(annual, RATIO_SCALE)
        else:
            annual = Decimal(0)
            ann_twr = 0
        max_dd = Decimal(walk["max_dd_units"]).scaleb(-RATIO_SCALE)
        calmar = _q(annual / max_dd, RATIO_SCALE) if max_dd > 0 else None

    decomposition: dict[str, int | str | None] = {
        "mdd_duration_td": walk["duration"],
        "mdd_recovery_td": walk["recovery"],
        "recovery_censored": 0 if walk["recovered"] else 1,
        "underwater_ratio": walk["underwater_units"],
        "ulcer": walk["ulcer_units"],
        "calmar": calmar,
        "twr": walk["twr_units"],
        "ann_twr": ann_twr,
        "n_obs": n_obs,
        "n_missing": n_missing,
        "n_trading_days": n_trading_days,
    }
    value = Fraction(walk["max_dd_units"], 10**RATIO_SCALE)
    return OracleResult(value, n_obs, decomposition)


__all__ = ["beta", "chase", "drawdown", "fly"]
