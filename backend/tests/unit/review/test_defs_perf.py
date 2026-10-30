"""perf 域 7 个生产 def 的单测（Period2-07 S4）：happy / 空样本 / 结构性无定义。"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from stock_review.metrics.defs import (
    perf_concentration,
    perf_expectancy,
    perf_fee,
    perf_fee_breakeven,
    perf_holding_days,
    perf_pnl_ratio,
    perf_win_rate,
)

from support.metrics_factories import (
    make_asset_day,
    make_context,
    make_fill,
    make_round,
    make_trade,
)


def _round(index: int, pnl_units: int, **kwargs):
    return dataclasses.replace(make_round(index), pnl_units=pnl_units, **kwargs)


def _rounds_8w4l() -> tuple:
    wins = tuple(_round(i, 100_000_000) for i in range(8))
    losses = tuple(_round(100 + i, -50_000_000) for i in range(4))
    return wins + losses


# —— perf.win_rate ——


def test_win_rate_happy() -> None:
    value = perf_win_rate.DEF.compute(make_context(rounds=_rounds_8w4l()))
    assert value.value_units == 66666667  # 8/12，HALF_UP
    assert value.n == 12
    decomp = value.decomposition
    assert decomp["n_closed"] == 12
    assert decomp["wins"] == 8
    assert decomp["n_zero"] == 0
    assert decomp["n_open"] == 0
    assert decomp["n_data_error"] == 0
    assert decomp["n_all"] == 12
    assert decomp["win_rate_all"] == 66666667
    assert value.ci_sample is not None
    assert len(value.ci_sample) == 12
    assert value.ci_sample == tuple(sorted(value.ci_sample))  # 升序约定
    assert value.ci_sample[0] == (0,) and value.ci_sample[-1] == (1,)


def test_win_rate_open_rounds_only_in_all_sample() -> None:
    opened = dataclasses.replace(make_round(200), pnl_units=80, status="open", close_date=None)
    errored = dataclasses.replace(make_round(201), data_error=True)
    ctx = make_context(rounds=(*_rounds_8w4l(), opened, errored))
    value = perf_win_rate.DEF.compute(ctx)
    assert value.n == 12
    decomp = value.decomposition
    assert decomp["n_open"] == 1
    assert decomp["n_data_error"] == 1
    assert decomp["n_all"] == 13
    assert decomp["win_rate_all"] is not None


def test_win_rate_empty() -> None:
    value = perf_win_rate.DEF.compute(make_context(rounds=()))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_closed"] == 0
    assert value.ci_sample is None or value.ci_sample == ()


# —— perf.pnl_ratio ——


def test_pnl_ratio_happy() -> None:
    rounds = (_round(0, 200), _round(1, 100), _round(2, -50))
    value = perf_pnl_ratio.DEF.compute(make_context(rounds=rounds))
    assert value.value_units == 300000000  # 150 / 50
    assert value.n == 3
    decomp = value.decomposition
    assert decomp["n_win"] == 2
    assert decomp["n_loss"] == 1
    assert decomp["avg_win"] == 150
    assert decomp["avg_loss"] == 50
    assert decomp["median_win"] == 150
    assert decomp["median_loss"] == 50
    assert decomp["pnl_ratio_median"] == 300000000


def test_pnl_ratio_all_wins_or_empty_is_undefined() -> None:
    all_wins = perf_pnl_ratio.DEF.compute(make_context(rounds=(_round(0, 200), _round(1, 100))))
    assert all_wins.value_units is None
    assert all_wins.n == 2

    empty = perf_pnl_ratio.DEF.compute(make_context(rounds=()))
    assert empty.value_units is None
    assert empty.n == 0


# —— perf.holding_days ——


def test_holding_days_happy() -> None:
    rounds = (
        dataclasses.replace(_round(0, 100), holding_days=3),
        dataclasses.replace(_round(1, 100), holding_days=5),
        dataclasses.replace(_round(2, 100), holding_days=10),
    )
    fills = (
        make_fill("rnd_0000", buy_cost_units=100, holding_days=3),
        make_fill("rnd_0002", buy_cost_units=300, holding_days=10),
    )
    ctx = dataclasses.replace(make_context(rounds=rounds), fills=fills)
    value = perf_holding_days.DEF.compute(ctx)
    assert value.value_units == 600  # mean 6 天 ×100
    assert value.n == 3
    decomp = value.decomposition
    assert decomp["median_holding_days"] == 500
    assert decomp["mean_natural_days"] == 900  # 6-01 → 6-10 均为 9 个自然日
    assert decomp["median_natural_days"] == 900
    assert decomp["capital_weighted_days"] == 825  # (3×100+10×300)/(100+300) ×100


def test_holding_days_empty() -> None:
    value = perf_holding_days.DEF.compute(make_context(rounds=()))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["median_holding_days"] is None


# —— perf.concentration ——


def test_concentration_happy() -> None:
    rounds = (
        _round(0, 400),
        _round(1, 300),
        _round(2, 200),
        _round(3, 100),
        _round(4, -50),
    )
    value = perf_concentration.DEF.compute(make_context(rounds=rounds))
    assert value.value_units == 90000000  # top3=900 / 1000
    assert value.n == 4
    decomp = value.decomposition
    assert decomp["cr1"] == 40000000
    assert decomp["gini"] == 25000000
    assert decomp["n_pos"] == 4
    assert decomp["top3_k"] == 3
    assert decomp["top3_amount"] == 900
    assert decomp["positive_total"] == 1000


def test_concentration_no_positive_round() -> None:
    value = perf_concentration.DEF.compute(make_context(rounds=(_round(0, -50),)))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_pos"] == 0
    assert value.decomposition["gini"] is None


# —— perf.fee ——


def _fee_trades() -> tuple:
    return (
        make_trade(1, date(2026, 6, 2), commission_units=300),
        make_trade(
            2,
            date(2026, 6, 3),
            side="sell",
            category="sell",
            amount_units=10_0000,
            commission_units=300,
            stamp_tax_units=200,
        ),
    )


def test_fee_happy() -> None:
    ctx = dataclasses.replace(make_context(), trades=_fee_trades())
    value = perf_fee.DEF.compute(ctx)
    assert value.value_units == 400000  # 800 / 200000
    assert value.n == 2
    decomp = value.decomposition
    assert decomp["fee_amount"] == 800
    assert decomp["commission"] == 600
    assert decomp["stamp_tax"] == 200
    assert decomp["other_fee"] == 0
    assert decomp["standalone_fee"] == 0
    assert decomp["turnover_gross"] == 200000
    assert decomp["turnover_net"] == 200000
    assert decomp["fee_ratio_net"] == 400000
    assert decomp["n_fee_rows"] == 0


def test_fee_standalone_fee_row() -> None:
    standalone = make_trade(
        3,
        date(2026, 6, 4),
        side="buy",
        category="fee",
        quantity_units=0,
        price_units=0,
        amount_units=-100,
    )
    ctx = dataclasses.replace(make_context(), trades=(*_fee_trades(), standalone))
    value = perf_fee.DEF.compute(ctx)
    assert value.decomposition["fee_amount"] == 900
    assert value.decomposition["standalone_fee"] == 100
    assert value.decomposition["n_fee_rows"] == 1
    assert value.n == 2  # fee 行不算成交腿


def test_fee_empty() -> None:
    value = perf_fee.DEF.compute(make_context())
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["turnover_gross"] == 0


# —— perf.fee_breakeven ——


def _fee_breakeven_ctx(rows: tuple) -> object:
    trades = (make_trade(1, date(2026, 6, 2), commission_units=1000),)
    return dataclasses.replace(make_context(), trades=trades, asset_daily=rows)


def test_fee_breakeven_happy() -> None:
    rows = tuple(
        make_asset_day(date(2026, 6, 1) + timedelta(days=i), daily_return_units=0)
        for i in range(10)
    )
    value = perf_fee_breakeven.DEF.compute(_fee_breakeven_ctx(rows))
    # 决策 5：期间费用 1000 ÷ 期间平均总资产 1,500,000 = 0.00066667（不年化）。
    assert value.value_units == 66667
    assert value.n == 10
    decomp = value.decomposition
    assert decomp["fee_amount"] == 1000
    assert decomp["avg_total_assets"] == 1_500_000
    assert decomp["annualized_fee"] == 12167
    assert decomp["annualized_fee_ratio"] == 811133
    assert decomp["twr"] == 0
    assert decomp["twr_eaten_pct"] is None  # twr=0 不出
    assert decomp["n_blocked_days"] == 0


def test_fee_breakeven_blocked_row_suppresses_value() -> None:
    rows = tuple(make_asset_day(date(2026, 6, 1) + timedelta(days=i)) for i in range(10))
    rows = (*rows[:3], dataclasses.replace(rows[3], status="blocked"), *rows[4:])
    value = perf_fee_breakeven.DEF.compute(_fee_breakeven_ctx(rows))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_blocked_days"] == 1


def test_fee_breakeven_no_rows() -> None:
    value = perf_fee_breakeven.DEF.compute(_fee_breakeven_ctx(()))
    assert value.value_units is None
    assert value.n == 0


# —— perf.expectancy ——


def test_expectancy_happy() -> None:
    rounds = (_round(0, 200), _round(1, 100), _round(2, -150))
    value = perf_expectancy.DEF.compute(make_context(rounds=rounds))
    # p=2/3, W=150, L=150 → E = 100 − 50 = 50（分）
    assert value.value_units == 50
    assert value.n == 3
    decomp = value.decomposition
    assert decomp["mean_pnl"] == 50
    assert decomp["p"] == 66666667
    assert decomp["win_loss_ratio"] == 100000000
    assert decomp["p_star"] == 50000000  # L/(W+L) = 0.5
    assert decomp["n_win"] == 2 and decomp["n_loss"] == 1 and decomp["n_zero"] == 0
    assert value.ci_stat_id == "mean"
    assert value.ci_sample == ((-150,), (100,), (200,))


def test_expectancy_degenerate_p_star() -> None:
    all_wins = perf_expectancy.DEF.compute(make_context(rounds=(_round(0, 200), _round(1, 100))))
    assert all_wins.value_units == 150
    assert all_wins.decomposition["p_star"] is None

    all_losses = perf_expectancy.DEF.compute(make_context(rounds=(_round(0, -200),)))
    assert all_losses.value_units == -200
    assert all_losses.decomposition["p_star"] == 100000000


def test_expectancy_empty() -> None:
    value = perf_expectancy.DEF.compute(make_context(rounds=()))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["p_star"] is None
