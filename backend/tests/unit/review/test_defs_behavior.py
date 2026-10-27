"""behavior 域 4 个生产 def 的单测（Period2-07 S4）：happy / 空样本 / 剔除路径。"""

from __future__ import annotations

import dataclasses
from datetime import date

from stock_review.metrics.defs import (
    behavior_averaging_down,
    behavior_de,
    behavior_hhi,
    behavior_turnover,
)
from stock_review.metrics.views import weekday_days

from support.metrics_factories import (
    PERIOD_END,
    PERIOD_START,
    make_asset_day,
    make_bar,
    make_context,
    make_round,
    make_trade,
)

JUNE_DAYS = weekday_days(PERIOD_START, PERIOD_END)


def _round(index: int, pnl_units: int, **kwargs):
    return dataclasses.replace(make_round(index), pnl_units=pnl_units, **kwargs)


# —— behavior.de ——


def _de_rounds() -> tuple:
    gains = tuple(_round(i, 100, holding_days=3) for i in range(8))
    losses = tuple(_round(100 + i, -50, holding_days=10) for i in range(4))
    paper_gain = (_round(200, 80, status="open", close_date=None),)
    paper_loss = tuple(_round(201 + i, -60, status="open", close_date=None) for i in range(2))
    return gains + losses + paper_gain + paper_loss


def test_de_happy_golden() -> None:
    value = behavior_de.DEF.compute(make_context(rounds=_de_rounds()))
    # G=8, L=4, g=1, l=2 → DE = (8×6 − 4×9) / (9×6) = 12/54
    assert value.value_units == 22222222
    assert value.n == 15
    decomp = value.decomposition
    assert decomp["pgr"] == 88888889  # 8/9
    assert decomp["plr"] == 66666667  # 4/6
    assert decomp["n_sold_gain"] == 8
    assert decomp["n_sold_loss"] == 4
    assert decomp["n_paper_gain"] == 1
    assert decomp["n_paper_loss"] == 2
    assert decomp["n_zero"] == 0
    assert decomp["n_data_error"] == 0
    assert decomp["holding_days_diff"] == 700  # 亏损均持 10 − 盈利均持 3，×100
    assert value.ci_stat_id == "ratio_diff_pgr_plr"
    assert value.ci_sample is not None
    assert len(value.ci_sample) == 15
    assert value.ci_sample == tuple(sorted(value.ci_sample))
    assert value.ci_sample[0] == (0,) and value.ci_sample[-1] == (3,)


def test_de_one_side_missing_is_undefined() -> None:
    only_gains = behavior_de.DEF.compute(
        make_context(rounds=tuple(_round(i, 100) for i in range(3)))
    )
    assert only_gains.value_units is None
    assert only_gains.n == 3  # n = G+g+L+l 仍计数
    assert only_gains.decomposition["plr"] is None

    empty = behavior_de.DEF.compute(make_context(rounds=()))
    assert empty.value_units is None
    assert empty.n == 0


# —— behavior.turnover ——


def _turnover_ctx(*, with_rows: bool = True, with_trades: bool = True):
    trades = (
        make_trade(1, date(2026, 6, 2), amount_units=-120000),
        make_trade(2, date(2026, 6, 3), side="sell", category="sell", amount_units=80000),
    )
    rows = tuple(make_asset_day(day) for day in JUNE_DAYS[:10])
    return dataclasses.replace(
        make_context(),
        trades=trades if with_trades else (),
        asset_daily=rows if with_rows else (),
    )


def test_turnover_happy() -> None:
    value = behavior_turnover.DEF.compute(_turnover_ctx())
    # num=200000，avg_mv=1000000，N=10 → 200000×244 / (1000000×10) ×1e8
    assert value.value_units == 488000000
    assert value.n == 10
    decomp = value.decomposition
    assert decomp["double_sided_amount"] == 200000
    assert decomp["buy_amount"] == 120000
    assert decomp["sell_amount"] == 80000
    assert decomp["avg_market_value"] == 1000000
    assert decomp["raw_turnover"] == 20000000
    assert decomp["n_trading_days"] == 10
    assert decomp["n_natural_days_used"] == 10
    assert decomp["n_blocked_days"] == 0


def test_turnover_no_asset_rows_but_trades_is_undefined() -> None:
    value = behavior_turnover.DEF.compute(_turnover_ctx(with_rows=False))
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["raw_turnover"] is None


def test_turnover_no_trades_is_zero() -> None:
    value = behavior_turnover.DEF.compute(_turnover_ctx(with_trades=False))
    assert value.value_units == 0
    assert value.n == 10


# —— behavior.averaging_down ——


def test_averaging_down_happy() -> None:
    trades = (
        make_trade(1, date(2026, 5, 20), price_units=10_000_000, amount_units=-100000),
        make_trade(2, date(2026, 6, 5), price_units=9_000_000, amount_units=-90000),
    )
    ctx = dataclasses.replace(make_context(), trades=trades)
    value = behavior_averaging_down.DEF.compute(ctx)
    assert value.value_units == 100000000  # 唯一加仓买在成本（10 元）之下
    assert value.n == 1
    decomp = value.decomposition
    assert decomp["n_avg_down"] == 1
    assert decomp["n_addon"] == 1
    assert decomp["avg_depth"] == 10000000  # (10−9)/10
    assert decomp["fwd20_excess_mean"] is None  # M1 未实现
    assert decomp["n_first_build"] == 0  # 5-20 首建仓在 period 外，不计
    assert decomp["n_zero_cost"] == 0
    assert decomp["n_invalid_price"] == 0


def test_averaging_down_mixed_addons() -> None:
    trades = (
        make_trade(1, date(2026, 5, 20), price_units=10_000_000, amount_units=-100000),
        make_trade(2, date(2026, 6, 5), price_units=9_000_000, amount_units=-90000),
        make_trade(3, date(2026, 6, 6), price_units=11_000_000, amount_units=-110000),
    )
    ctx = dataclasses.replace(make_context(), trades=trades)
    value = behavior_averaging_down.DEF.compute(ctx)
    assert value.value_units == 50000000
    assert value.n == 2
    assert value.decomposition["n_avg_down"] == 1


def test_averaging_down_first_build_and_empty() -> None:
    first_build = behavior_averaging_down.DEF.compute(
        dataclasses.replace(make_context(), trades=(make_trade(1, date(2026, 6, 5)),))
    )
    assert first_build.value_units is None
    assert first_build.n == 0
    assert first_build.decomposition["n_first_build"] == 1

    empty = behavior_averaging_down.DEF.compute(make_context())
    assert empty.value_units is None
    assert empty.n == 0
    assert empty.decomposition["n_addon"] == 0


# —— behavior.hhi ——


def _hhi_ctx(rows: tuple) -> object:
    trades = (make_trade(1, date(2026, 5, 29), price_units=10_000_000, amount_units=-100000),)
    bars = tuple(make_bar(day, close_units=10_000_000) for day in JUNE_DAYS[:2])
    return dataclasses.replace(make_context(), trades=trades, bars=bars, asset_daily=rows)


def _hhi_rows() -> tuple:
    return tuple(
        make_asset_day(day, market_value_units=100000, cash_units=100000, total_assets_units=200000)
        for day in JUNE_DAYS[:2]
    )


def test_hhi_happy() -> None:
    value = behavior_hhi.DEF.compute(_hhi_ctx(_hhi_rows()))
    # 单一持仓 100000 + 现金 100000 → 0.5² + 0.5² = 0.5
    assert value.value_units == 50000000
    assert value.n == 2
    decomp = value.decomposition
    assert decomp["hhi_total_max"] == 50000000
    assert decomp["hhi_total_end"] == 50000000
    assert decomp["hhi_eq_mean"] == 100000000  # 单标的股票侧 HHI = 1
    assert decomp["avg_holding_count"] == 100  # 1.00 ×100
    assert decomp["avg_cash_weight"] == 50000000
    assert decomp["n_days_used"] == 2
    assert decomp["n_recon_mismatch"] == 0
    assert decomp["n_missing_price"] == 0
    assert decomp["n_oversell"] == 0


def test_hhi_recon_mismatch_skips_day() -> None:
    rows = _hhi_rows()
    rows = (*rows[:1], dataclasses.replace(rows[1], market_value_units=999))
    value = behavior_hhi.DEF.compute(_hhi_ctx(rows))
    assert value.value_units == 50000000
    assert value.n == 1
    assert value.decomposition["n_recon_mismatch"] == 1


def test_hhi_empty() -> None:
    value = behavior_hhi.DEF.compute(make_context())
    assert value.value_units is None
    assert value.n == 0
    assert value.decomposition["n_days_used"] == 0
