"""timing 域 4 个生产 def 的单测（Period2-07 S4）：happy / 空样本 / 缺基准。"""

from __future__ import annotations

import dataclasses
from datetime import date

from stock_review.metrics.defs import (
    timing_beta,
    timing_chase,
    timing_drawdown,
    timing_fly,
)
from stock_review.metrics.views import weekday_days

from support.metrics_factories import (
    PERIOD_END,
    PERIOD_START,
    make_asset_day,
    make_bar,
    make_benchmark,
    make_context,
    make_trade,
)

JUNE_DAYS = weekday_days(PERIOD_START, PERIOD_END)
WINDOW_DAYS = weekday_days(date(2026, 4, 1), PERIOD_END)[-60:]


def _bars(symbol: str, close: int, *, days=JUNE_DAYS, hit_day=None, hit_close: int = 0) -> tuple:
    return tuple(
        make_bar(day, security_code=symbol, close_units=hit_close if day == hit_day else close)
        for day in days
    )


def _bench_series(days=JUNE_DAYS, returns: list[int] | None = None) -> tuple:
    return tuple(
        make_benchmark(day, return_units=0 if returns is None else returns[i])
        for i, day in enumerate(days)
    )


# —— timing.chase ——


def test_chase_happy() -> None:
    bars = _bars("600519", 10_100_000, hit_day=JUNE_DAYS[5], hit_close=10_400_000)
    bars += _bars("000001", 20_100_000)
    trades = (
        make_trade(1, JUNE_DAYS[0], price_units=10_000_000),
        make_trade(
            2, JUNE_DAYS[0], security_code="000001", price_units=20_000_000, amount_units=-200000
        ),
    )
    ctx = dataclasses.replace(make_context(), trades=trades, bars=bars)
    value = timing_chase.DEF.compute(ctx)
    # 600519 后向窗口最高 10.4 > 10×1.03 → 命中；000001 最高 20.1 < 20×1.03 → 未命中
    assert value.value_units == 50000000
    assert value.n == 2
    assert value.n_pending == 0
    assert value.decomposition == {
        "n_events": 2,
        "n_valid": 2,
        "n_pending": 0,
        "post_window": 20,
    }


def test_chase_window_not_finished_is_pending() -> None:
    trades = (make_trade(1, date(2026, 6, 29)),)
    ctx = dataclasses.replace(make_context(), trades=trades, bars=_bars("600519", 10_100_000))
    value = timing_chase.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.n_pending == 1
    assert value.decomposition["n_pending"] == 1


def test_chase_empty() -> None:
    value = timing_chase.DEF.compute(make_context())
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_events"] == 0


# —— timing.fly ——


def _fly_ctx(*, benchmark: str | None, with_series: bool = True):
    trades = (
        make_trade(1, JUNE_DAYS[0], side="sell", category="sell", amount_units=100000),
    )
    bars = _bars("600519", 10_400_000)
    series = _bench_series() if with_series else ()
    return dataclasses.replace(
        make_context(benchmark=benchmark),
        trades=trades,
        bars=bars,
        benchmark_series=series,
    )


def test_fly_happy() -> None:
    value = timing_fly.DEF.compute(_fly_ctx(benchmark="000300.SH"))
    assert value.value_units == 100000000  # 唯一卖单后 20 根全部 ≥ +3%
    assert value.n == 1
    decomp = value.decomposition
    assert decomp["n_events"] == 1
    assert decomp["fly_share_h5"] == 100000000
    assert decomp["fly_share_h10"] == 100000000
    assert decomp["benchmark_id"] == "000300.SH"


def test_fly_missing_benchmark() -> None:
    no_config = timing_fly.DEF.compute(_fly_ctx(benchmark=None))
    assert no_config.value_units is None
    assert no_config.n == 0
    assert no_config.decomposition["benchmark_id"] is None
    assert no_config.decomposition["n_events"] == 1

    no_series = timing_fly.DEF.compute(_fly_ctx(benchmark="000300.SH", with_series=False))
    assert no_series.value_units is None
    assert no_series.n == 0


# —— timing.beta ——


def _beta_ctx(benchmark: str | None = "000300.SH", *, flat_bench: bool = False):
    returns = [1_000_000 if i % 2 == 0 else -1_000_000 for i in range(60)]
    rows = tuple(
        make_asset_day(day, daily_return_units=returns[i]) for i, day in enumerate(WINDOW_DAYS)
    )
    series = _bench_series(WINDOW_DAYS, [0] * 60 if flat_bench else returns)
    return dataclasses.replace(
        make_context(benchmark=benchmark), asset_daily=rows, benchmark_series=series
    )


def test_beta_happy_exact_one() -> None:
    value = timing_beta.DEF.compute(_beta_ctx())
    assert value.value_units == 100000000  # y == x → β 精确 1
    assert value.n == 60
    decomp = value.decomposition
    assert decomp["n_obs"] == 60
    assert decomp["n_missing"] == 0
    assert decomp["sxx_zero"] == 0
    assert decomp["benchmark_id"] == "000300.SH"
    assert decomp["rf_rate"] == "0.015"
    assert decomp["a_base"] == 244
    assert decomp["alpha_annual"] == 0
    assert decomp["excess_arith"] == 0


def test_beta_constant_benchmark_is_blocked_sxx() -> None:
    value = timing_beta.DEF.compute(_beta_ctx(flat_bench=True))
    assert value.value_units is None
    assert value.decomposition["sxx_zero"] == 1
    assert value.decomposition["alpha_annual"] is None


def test_beta_missing_benchmark() -> None:
    value = timing_beta.DEF.compute(_beta_ctx(benchmark=None))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["benchmark_id"] is None


def test_beta_degraded_row_suppresses_value() -> None:
    ctx = _beta_ctx()
    rows = list(ctx.asset_daily)
    rows[50] = dataclasses.replace(rows[50], status="degraded")
    ctx = dataclasses.replace(ctx, asset_daily=tuple(rows))
    value = timing_beta.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_degraded_rows"] == 1


# —— timing.drawdown ——


def test_drawdown_happy() -> None:
    returns = [-10_000_000] + [0] * 9
    rows = tuple(
        make_asset_day(JUNE_DAYS[i], daily_return_units=returns[i]) for i in range(10)
    )
    ctx = dataclasses.replace(make_context(), asset_daily=rows)
    value = timing_drawdown.DEF.compute(ctx)
    assert value.value_units == 10000000  # 首日 −10% 后走平 → MDD=10%
    assert value.n == 10
    decomp = value.decomposition
    assert decomp["twr"] == -10000000
    assert decomp["recovery_censored"] == 1
    assert decomp["mdd_duration_td"] == 1
    assert decomp["n_obs"] == 10
    assert decomp["n_missing"] == 0
    assert decomp["n_trading_days"] == 10


def test_drawdown_blocked_row_suppresses_value() -> None:
    rows = tuple(make_asset_day(JUNE_DAYS[i], daily_return_units=0) for i in range(10))
    rows = (*rows[:3], dataclasses.replace(rows[3], status="blocked"), *rows[4:])
    ctx = dataclasses.replace(make_context(), asset_daily=rows)
    value = timing_drawdown.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_blocked_rows"] == 1


def test_drawdown_empty() -> None:
    value = timing_drawdown.DEF.compute(make_context())
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_obs"] == 0
