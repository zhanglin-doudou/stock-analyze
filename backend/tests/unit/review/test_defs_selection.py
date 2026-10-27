"""selection 域 3 个生产 def 的单测（Period2-07 S4）：happy / 空样本 / 缺基准 / 剔除。"""

from __future__ import annotations

import dataclasses
from datetime import date

from stock_review.metrics.defs import (
    selection_bhar_20,
    selection_bhar_60,
    selection_bhar_hit,
)
from stock_review.metrics.views import weekday_days

from support.metrics_factories import (
    PERIOD_END,
    PERIOD_START,
    make_bar,
    make_benchmark,
    make_context,
    make_trade,
)

JUNE_DAYS = weekday_days(PERIOD_START, PERIOD_END)  # 22 个工作日：j0=0(6-01) → j1=20(6-29)
LONG_DAYS = weekday_days(PERIOD_START, date(2026, 9, 1))  # bhar_60 需要 ≥61 个基准日


def _bench_series(days) -> tuple:
    return tuple(make_benchmark(day, return_units=0) for day in days)


def _bhar20_ctx(*, benchmark: str | None = "000300.SH", with_series: bool = True):
    bars = tuple(
        make_bar(day, close_units=11_000_000 if day == JUNE_DAYS[20] else 10_000_000)
        for day in JUNE_DAYS
    )
    trades = (make_trade(1, JUNE_DAYS[0]),)
    return dataclasses.replace(
        make_context(benchmark=benchmark),
        trades=trades,
        bars=bars,
        benchmark_series=_bench_series(JUNE_DAYS) if with_series else (),
    )


# —— selection.bhar_20 ——


def test_bhar20_happy() -> None:
    value = selection_bhar_20.DEF.compute(_bhar20_ctx())
    # 个股腿 11/10 = 1.1，基准腿 0 → BHAR = +10%
    assert value.value_units == 10000000
    assert value.n == 1
    decomp = value.decomposition
    assert decomp["n"] == 1
    assert decomp["median"] == 10000000
    assert decomp["hit_rate"] == 100000000
    assert decomp["n_pending"] == 0
    assert decomp["n_excluded_suspended"] == 0
    assert decomp["n_excluded_delisted"] == 0
    assert decomp["n_excluded_noprice"] == 0
    assert decomp["mean_benchmark_return"] == 0
    assert value.ci_stat_id == "mean"
    assert value.ci_sample == ((10000000,),)


def test_bhar20_missing_benchmark() -> None:
    no_config = selection_bhar_20.DEF.compute(_bhar20_ctx(benchmark=None))
    assert no_config.value_units is None
    assert no_config.n == 0
    assert no_config.decomposition["benchmark_id"] is None

    no_series = selection_bhar_20.DEF.compute(
        _bhar20_ctx(benchmark="000300.SH", with_series=False)
    )
    assert no_series.value_units is None
    assert no_series.n == 0


def test_bhar20_window_pending() -> None:
    ctx = dataclasses.replace(
        _bhar20_ctx(),
        trades=(make_trade(1, date(2026, 6, 29)),),
    )
    value = selection_bhar_20.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.n_pending == 1
    assert value.decomposition["n_pending"] == 1


def test_bhar20_suspended_excluded() -> None:
    ctx = dataclasses.replace(
        _bhar20_ctx(),
        bars=tuple(make_bar(day) for day in JUNE_DAYS[:5]),  # 覆盖率 5/20 < 50%
    )
    value = selection_bhar_20.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_excluded_suspended"] == 1


# —— selection.bhar_60 ——


def _bhar60_ctx(*, benchmark: str | None = "000300.SH"):
    bars = tuple(
        make_bar(day, close_units=12_000_000 if day == LONG_DAYS[60] else 10_000_000)
        for day in LONG_DAYS
    )
    trades = (make_trade(1, JUNE_DAYS[0]),)
    return dataclasses.replace(
        make_context(benchmark=benchmark, as_of=date(2026, 9, 1)),
        trades=trades,
        bars=bars,
        benchmark_series=_bench_series(LONG_DAYS),
    )


def test_bhar60_happy() -> None:
    value = selection_bhar_60.DEF.compute(_bhar60_ctx())
    # 个股腿 12/10 = 1.2，基准腿 0 → BHAR = +20%
    assert value.value_units == 20000000
    assert value.n == 1
    decomp = value.decomposition
    assert decomp["n"] == 1
    assert decomp["median"] == 20000000
    assert decomp["n_pending"] == 0
    assert value.ci_stat_id == "mean"
    assert value.ci_sample == ((20000000,),)


def test_bhar60_missing_benchmark() -> None:
    value = selection_bhar_60.DEF.compute(_bhar60_ctx(benchmark=None))
    assert value.value_units is None
    assert value.n == 0


def test_bhar60_window_pending_within_period() -> None:
    # as_of 留在期末 → 60 日窗口走不完 → pending
    ctx = dataclasses.replace(
        _bhar60_ctx(),
        as_of=PERIOD_END,
        bars=tuple(
            make_bar(day, close_units=12_000_000 if day == LONG_DAYS[60] else 10_000_000)
            for day in LONG_DAYS
        ),
        benchmark_series=_bench_series(JUNE_DAYS),
    )
    value = selection_bhar_60.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.n_pending == 1


# —— selection.bhar_hit ——


def _bhar_hit_ctx(*, benchmark: str | None = "000300.SH"):
    bars_a = tuple(
        make_bar(day, close_units=11_000_000 if day == JUNE_DAYS[20] else 10_000_000)
        for day in JUNE_DAYS
    )
    bars_b = tuple(
        make_bar(
            day, security_code="000001",
            close_units=19_000_000 if day == JUNE_DAYS[20] else 20_000_000,
        )
        for day in JUNE_DAYS
    )
    trades = (
        make_trade(1, JUNE_DAYS[0]),
        make_trade(
            2, JUNE_DAYS[0], security_code="000001", price_units=20_000_000, amount_units=-200000
        ),
    )
    return dataclasses.replace(
        make_context(benchmark=benchmark),
        trades=trades,
        bars=bars_a + bars_b,
        benchmark_series=_bench_series(JUNE_DAYS),
    )


def test_bhar_hit_happy() -> None:
    value = selection_bhar_hit.DEF.compute(_bhar_hit_ctx())
    # A 股 BHAR=+10%（命中），B 股 BHAR=−5%（未中）→ 1/2
    assert value.value_units == 50000000
    assert value.n == 2
    decomp = value.decomposition
    assert decomp["n_hit"] == 1
    assert decomp["mean"] == 2500000
    assert decomp["median"] == 2500000
    assert decomp["n_pending"] == 0
    assert value.ci_sample == ((0,), (1,))


def test_bhar_hit_missing_benchmark() -> None:
    value = selection_bhar_hit.DEF.compute(_bhar_hit_ctx(benchmark=None))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["benchmark_id"] is None


def test_bhar_hit_no_events() -> None:
    ctx = dataclasses.replace(_bhar_hit_ctx(), trades=())
    value = selection_bhar_hit.DEF.compute(ctx)
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_hit"] == 0
