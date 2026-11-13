"""账单级旗标纯判定单测（100% 覆盖 stock_core.trade_flags）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from stock_core.trade_flags import (
    SHORT_TERM_MARGIN_BPS,
    STATUS_HIT,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_MISS,
    STATUS_NO_BAR,
    STATUS_NO_CALENDAR,
    STATUS_NO_COST_BASIS,
    STATUS_NOT_APPLICABLE,
    STATUS_PENDING,
    TRADE_FLAG_BY_CODE,
    TRADE_FLAG_CODES,
    TradeContext,
    evaluate_trade_flags,
)

TRADE = date(2026, 9, 15)
CALENDAR = tuple(date(2026, 9, 1) + timedelta(days=i) for i in range(30))
PRICE = 100_000_000  # 100.0，scale=1e6


def _bars(closes: list[int], start_index: int = 0) -> list[tuple[date, int]]:
    return [(CALENDAR[start_index + i], close) for i, close in enumerate(closes)]


def _flat(count: int, price: int = PRICE, start_index: int = 0) -> list[tuple[date, int]]:
    return _bars([price] * count, start_index)


def _buy(**overrides: object) -> TradeContext:
    base: dict[str, object] = {
        "side": "buy",
        "trade_date": TRADE,
        "net_price_units": PRICE,
        "quantity_units": 100_0000,
        "quantity_before_units": 0,
        "avg_cost_units": None,
    }
    base.update(overrides)
    return TradeContext(**base)  # type: ignore[arg-type]


def _sell(**overrides: object) -> TradeContext:
    return _buy(side="sell", **overrides)


def _by_code(results: list) -> dict[str, str]:
    return {item.code: item.status for item in results}


def _status(results: list, code: str) -> str:
    return next(item.status for item in results if item.code == code)


def test_flag_codes_are_unique_and_ordered() -> None:
    assert len(TRADE_FLAG_CODES) == len(set(TRADE_FLAG_CODES))
    assert TRADE_FLAG_CODES[0] == "first_entry"


def test_buy_first_entry_marks_state_not_applicable() -> None:
    results = evaluate_trade_flags(
        _buy(), bars=_flat(30), calendar=CALENDAR, data_as_of=CALENDAR[-1]
    )
    statuses = _by_code(results)
    assert statuses["first_entry"] == STATUS_HIT
    assert statuses["addon_average"] == STATUS_NOT_APPLICABLE
    assert statuses["addon_dip"] == STATUS_NOT_APPLICABLE
    assert statuses["addon_chase"] == STATUS_NOT_APPLICABLE
    assert statuses["chase"] == STATUS_MISS
    assert statuses["instant_bottom"] == STATUS_HIT
    assert statuses["short_term_bottom"] == STATUS_HIT
    assert statuses["short_term_underwater"] == STATUS_MISS
    assert [item.code for item in results][-1] == "short_term_underwater"


def test_buy_addon_average() -> None:
    results = evaluate_trade_flags(
        _buy(quantity_before_units=1000, avg_cost_units=PRICE),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "first_entry") == STATUS_MISS
    assert _status(results, "addon_average") == STATUS_HIT
    assert _status(results, "addon_dip") == STATUS_MISS
    assert _status(results, "addon_chase") == STATUS_MISS


def test_buy_addon_dip() -> None:
    results = evaluate_trade_flags(
        _buy(
            quantity_before_units=1000,
            avg_cost_units=PRICE,
            net_price_units=98_900_000,
        ),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "addon_dip") == STATUS_HIT
    assert _status(results, "addon_average") == STATUS_MISS


def test_buy_addon_chase() -> None:
    results = evaluate_trade_flags(
        _buy(
            quantity_before_units=1000,
            avg_cost_units=PRICE,
            net_price_units=101_100_000,
        ),
        bars=_flat(30, price=110_000_000),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "addon_chase") == STATUS_HIT
    assert _status(results, "addon_average") == STATUS_MISS


def test_buy_without_cost_basis() -> None:
    results = evaluate_trade_flags(
        _buy(quantity_before_units=1000, avg_cost_units=None),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    # 已有持仓但成本不可得：既非首次建仓，也不属于加仓三态（买侧残余）。
    assert _status(results, "first_entry") == STATUS_MISS
    assert _status(results, "addon_average") == STATUS_NO_COST_BASIS
    assert _status(results, "addon_dip") == STATUS_NO_COST_BASIS
    assert _status(results, "addon_chase") == STATUS_NO_COST_BASIS


def test_buy_zero_cost_basis_treated_as_missing() -> None:
    results = evaluate_trade_flags(
        _buy(quantity_before_units=1000, avg_cost_units=0),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "addon_average") == STATUS_NO_COST_BASIS


def test_buy_chase_and_instant_states() -> None:
    # 前 5 日：90,95,100,98,97 → max=100；净买价 101.1 高于 max 的 1%。
    past = [90_000_000, 95_000_000, 100_000_000, 98_000_000, 97_000_000]
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _buy(net_price_units=101_100_000),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "chase") == STATUS_HIT
    assert _status(results, "instant_bottom") == STATUS_MISS
    assert _status(results, "short_term_bottom") == STATUS_MISS


def test_buy_instant_bottom() -> None:
    past = [90_000_000, 95_000_000, 100_000_000, 98_000_000, 97_000_000]
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _buy(net_price_units=94_500_000),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "instant_bottom") == STATUS_HIT


def test_short_term_flags_are_lookahead() -> None:
    results = evaluate_trade_flags(
        _buy(), bars=_flat(30), calendar=CALENDAR, data_as_of=CALENDAR[-1]
    )
    by_code = {item.code: item for item in results}
    assert by_code["short_term_bottom"].lookahead is True
    assert by_code["chase"].lookahead is False


def test_no_calendar_marks_all_price_checks() -> None:
    results = evaluate_trade_flags(_buy(), bars=_flat(30), calendar=(), data_as_of=CALENDAR[-1])
    for code in ("chase", "instant_bottom", "short_term_bottom", "short_term_underwater"):
        assert _status(results, code) == STATUS_NO_CALENDAR


def test_no_bars_marks_all_price_checks() -> None:
    results = evaluate_trade_flags(_buy(), bars=[], calendar=CALENDAR, data_as_of=CALENDAR[-1])
    for code in ("chase", "instant_bottom", "short_term_bottom", "short_term_underwater"):
        assert _status(results, code) == STATUS_NO_BAR


def test_insufficient_past_history() -> None:
    bars = [*_bars([PRICE, PRICE], 8), (TRADE, PRICE)]
    results = evaluate_trade_flags(_buy(), bars=bars, calendar=CALENDAR, data_as_of=CALENDAR[-1])
    assert _status(results, "chase") == STATUS_INSUFFICIENT_HISTORY
    assert _status(results, "short_term_bottom") == STATUS_INSUFFICIENT_HISTORY


def test_future_window_pending_when_data_not_elapsed() -> None:
    bars = [*_flat(10, start_index=5), (TRADE, PRICE), *_flat(1, start_index=11)]
    results = evaluate_trade_flags(
        _buy(),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[11],
    )
    assert _status(results, "short_term_bottom") == STATUS_PENDING


def test_future_window_pending_when_calendar_exhausted() -> None:
    calendar = CALENDAR[:11]
    results = evaluate_trade_flags(
        _buy(), bars=_flat(11), calendar=calendar, data_as_of=calendar[-1]
    )
    assert _status(results, "short_term_bottom") == STATUS_PENDING


def test_future_window_pending_when_as_of_missing() -> None:
    bars = [*_flat(10, start_index=5), (TRADE, PRICE)]
    results = evaluate_trade_flags(_buy(), bars=bars, calendar=CALENDAR, data_as_of=None)
    assert _status(results, "short_term_bottom") == STATUS_PENDING


def test_future_window_insufficient_when_suspended() -> None:
    bars = [*_flat(10, start_index=5), (TRADE, PRICE)]
    results = evaluate_trade_flags(_buy(), bars=bars, calendar=CALENDAR, data_as_of=CALENDAR[-1])
    assert _status(results, "short_term_bottom") == STATUS_INSUFFICIENT_HISTORY


def test_sell_full_exit_replaces_reduction_states() -> None:
    results = evaluate_trade_flags(
        _sell(quantity_before_units=1000, quantity_units=1000, avg_cost_units=PRICE),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "reduce_breakeven") == STATUS_NOT_APPLICABLE
    assert _status(results, "reduce_profit") == STATUS_NOT_APPLICABLE
    assert _status(results, "reduce_loss") == STATUS_NOT_APPLICABLE
    assert _status(results, "full_exit") == STATUS_HIT
    # 净卖价 == 均价：回本清仓命中，止盈/止损清仓不命中。
    assert _status(results, "full_exit_breakeven") == STATUS_HIT
    assert _status(results, "full_exit_profit") == STATUS_MISS
    assert _status(results, "full_exit_loss") == STATUS_MISS


def test_sell_full_exit_profit_and_loss_states() -> None:
    args = {"quantity_before_units": 1000, "quantity_units": 1000, "avg_cost_units": PRICE}
    profit = evaluate_trade_flags(
        _sell(net_price_units=101_100_000, **args),
        bars=_flat(30, price=110_000_000),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(profit, "full_exit_profit") == STATUS_HIT
    assert _status(profit, "full_exit_breakeven") == STATUS_MISS
    assert _status(profit, "full_exit_loss") == STATUS_MISS
    loss = evaluate_trade_flags(
        _sell(net_price_units=98_900_000, **args),
        bars=_flat(30, price=90_000_000),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(loss, "full_exit_loss") == STATUS_HIT
    assert _status(loss, "full_exit_breakeven") == STATUS_MISS
    assert _status(loss, "full_exit_profit") == STATUS_MISS


def test_sell_full_exit_without_cost_basis() -> None:
    results = evaluate_trade_flags(
        _sell(quantity_before_units=1000, quantity_units=1000, avg_cost_units=None),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "reduce_breakeven") == STATUS_NOT_APPLICABLE
    assert _status(results, "full_exit") == STATUS_HIT
    for code in ("full_exit_breakeven", "full_exit_profit", "full_exit_loss"):
        assert _status(results, code) == STATUS_NO_COST_BASIS


def test_sell_breakeven_reduction() -> None:
    results = evaluate_trade_flags(
        _sell(quantity_before_units=1000, quantity_units=500, avg_cost_units=PRICE),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "reduce_breakeven") == STATUS_HIT
    assert _status(results, "full_exit") == STATUS_MISS
    for code in ("full_exit_breakeven", "full_exit_profit", "full_exit_loss"):
        assert _status(results, code) == STATUS_NOT_APPLICABLE


def test_sell_profit_and_loss_reductions() -> None:
    args = {"quantity_before_units": 1000, "quantity_units": 500, "avg_cost_units": PRICE}
    profit = evaluate_trade_flags(
        _sell(net_price_units=101_100_000, **args),
        bars=_flat(30, price=110_000_000),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(profit, "reduce_profit") == STATUS_HIT
    loss = evaluate_trade_flags(
        _sell(net_price_units=98_900_000, **args),
        bars=_flat(30, price=90_000_000),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(loss, "reduce_loss") == STATUS_HIT


def test_sell_without_position() -> None:
    results = evaluate_trade_flags(
        _sell(quantity_before_units=0, quantity_units=500, avg_cost_units=None),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "reduce_breakeven") == STATUS_NOT_APPLICABLE
    assert _status(results, "full_exit") == STATUS_MISS
    for code in ("full_exit_breakeven", "full_exit_profit", "full_exit_loss"):
        assert _status(results, code) == STATUS_NOT_APPLICABLE
    # 卖出前无持仓：止损/回撤的 reason 明确为 no_position，而非笼统的 no_cost_basis。
    for code in ("stop_loss", "sell_drawdown"):
        assert _status(results, code) == STATUS_NO_COST_BASIS
        assert next(item.reason for item in results if item.code == code) == "no_position"


def test_sell_without_cost_basis() -> None:
    results = evaluate_trade_flags(
        _sell(quantity_before_units=1000, quantity_units=500, avg_cost_units=None),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "reduce_breakeven") == STATUS_NO_COST_BASIS
    assert _status(results, "full_exit") == STATUS_MISS
    for code in ("full_exit_breakeven", "full_exit_profit", "full_exit_loss"):
        assert _status(results, code) == STATUS_NOT_APPLICABLE


def test_sell_stop_loss() -> None:
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=PRICE,
            net_price_units=98_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "stop_loss") == STATUS_HIT
    assert _status(results, "sell_drawdown") == STATUS_MISS
    # 极值按前 5 日最高收盘价（100）计：98.9 仍在 5% 带内，故「即时逃顶」也可命中。
    assert _status(results, "instant_top") == STATUS_HIT


def test_sell_stop_loss_requires_price_below_avg_cost() -> None:
    # 同样破位（98.9 < 100×99%），但卖价高于均价 98：止损不成立，由「回撤」承接。
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=98_000_000,
            net_price_units=98_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "stop_loss") == STATUS_MISS
    assert _status(results, "sell_drawdown") == STATUS_HIT


def test_sell_drawdown_boundary_equal_avg_cost() -> None:
    # 恰等于均价（98.9）不算止损（需严格低于），归入回撤。
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=98_900_000,
            net_price_units=98_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "stop_loss") == STATUS_MISS
    assert _status(results, "sell_drawdown") == STATUS_HIT


def test_sell_stop_loss_and_drawdown_need_cost_basis() -> None:
    # 无持仓成本：两个旗标都无法判定，透出 no_cost_basis 而不是伪造命中。
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=None,
            net_price_units=98_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "stop_loss") == STATUS_NO_COST_BASIS
    assert _status(results, "sell_drawdown") == STATUS_NO_COST_BASIS
    assert next(item.reason for item in results if item.code == "stop_loss") == "no_cost_basis"


def test_sell_zero_cost_stop_loss_misses_and_drawdown_hits() -> None:
    # 零成本持仓（送转/红利再投）视为有效成本：净卖价必然 ≥ 0 = 均价，止损恒不命中。
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=0,
            net_price_units=98_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "stop_loss") == STATUS_MISS
    assert _status(results, "sell_drawdown") == STATUS_HIT
    # 零成本同样是止盈型减仓（任何正卖价都在成本线上方）。
    assert _status(results, "reduce_profit") == STATUS_HIT


def test_sell_zero_cost_full_exit_is_profit() -> None:
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=1000,
            avg_cost_units=0,
            net_price_units=101_000_000,
        ),
        bars=_flat(30),
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "full_exit_profit") == STATUS_HIT
    assert _status(results, "full_exit_breakeven") == STATUS_MISS
    assert _status(results, "full_exit_loss") == STATUS_MISS


def test_sell_negative_cost_is_invalid() -> None:
    # 负成本是数据异常：不参与止盈/止损判定，reason 明确为 invalid_cost_basis。
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=-1,
            net_price_units=98_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "reduce_profit") == STATUS_NO_COST_BASIS
    for code in ("stop_loss", "sell_drawdown"):
        assert _status(results, code) == STATUS_NO_COST_BASIS
        assert next(item.reason for item in results if item.code == code) == "invalid_cost_basis"


def test_sell_instant_top() -> None:
    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars([PRICE] * 5, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=PRICE,
            net_price_units=99_500_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "instant_top") == STATUS_HIT
    assert _status(results, "stop_loss") == STATUS_MISS


def test_sell_short_term_top_uses_future_bars() -> None:
    past = [100_000_000] * 5
    future = [120_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE), *_bars(future, 16)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=PRICE,
            net_price_units=114_500_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(results, "short_term_top") == STATUS_HIT


def test_sell_short_term_flags_carry_window_status() -> None:
    ctx = _sell(quantity_before_units=0)
    no_bars = evaluate_trade_flags(ctx, bars=[], calendar=CALENDAR, data_as_of=CALENDAR[-1])
    assert _status(no_bars, "short_term_top") == STATUS_NO_BAR

    past = [100_000_000] * 5
    bars = [*_bars(past, 5), (TRADE, PRICE)]
    incomplete_future = evaluate_trade_flags(
        ctx, bars=bars, calendar=CALENDAR, data_as_of=CALENDAR[-1]
    )
    assert _status(incomplete_future, "short_term_top") == STATUS_INSUFFICIENT_HISTORY


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_sell_lookahead_marker(side: str) -> None:
    ctx = _buy() if side == "buy" else _sell(quantity_before_units=0)
    results = evaluate_trade_flags(ctx, bars=_flat(30), calendar=CALENDAR, data_as_of=CALENDAR[-1])
    code = "short_term_bottom" if side == "buy" else "short_term_top"
    assert next(item.lookahead for item in results if item.code == code) is True


def test_short_term_win_renamed_to_bottom() -> None:
    assert "short_term_win" not in TRADE_FLAG_CODES
    renamed = TRADE_FLAG_BY_CODE["short_term_bottom"]
    assert (renamed.label_zh, renamed.label_en) == ("短期抄底", "Short-term bottom")
    assert renamed.lookahead is True


def test_buy_short_term_underwater_hits_when_window_stays_below() -> None:
    past = [PRICE] * 5
    future = [99_000_000, 98_000_000, 97_000_000, 96_000_000, 95_000_000]
    bars = [*_bars(past, 9), (TRADE, PRICE), *_bars(future, 15)]
    results = evaluate_trade_flags(_buy(), bars=bars, calendar=CALENDAR, data_as_of=CALENDAR[-1])
    assert _status(results, "short_term_underwater") == STATUS_HIT


def test_buy_short_term_underwater_miss_on_equal_close() -> None:
    # 最后一日收盘价 == 净买价：收益为 0 不算负，严格口径不命中。
    past = [PRICE] * 5
    future = [99_000_000, 98_000_000, 97_000_000, 96_000_000, PRICE]
    bars = [*_bars(past, 9), (TRADE, PRICE), *_bars(future, 15)]
    results = evaluate_trade_flags(_buy(), bars=bars, calendar=CALENDAR, data_as_of=CALENDAR[-1])
    assert _status(results, "short_term_underwater") == STATUS_MISS


def test_buy_short_term_underwater_pending_without_future_window() -> None:
    bars = [*_flat(5, start_index=9), (TRADE, PRICE), *_flat(4, start_index=15)]
    results = evaluate_trade_flags(
        _buy(), bars=bars, calendar=CALENDAR[:19], data_as_of=CALENDAR[18]
    )
    assert _status(results, "short_term_underwater") == STATUS_PENDING


def test_sell_short_term_sold_early_hits_against_last_window_close() -> None:
    past = [100_000_000] * 5
    future = [110_000_000, 112_000_000, 115_000_000, 118_000_000, 120_000_000]
    bars = [*_bars(past, 9), (TRADE, PRICE), *_bars(future, 15)]
    results = evaluate_trade_flags(
        _sell(
            quantity_before_units=1000,
            quantity_units=500,
            avg_cost_units=PRICE,
            net_price_units=113_900_000,
        ),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    # 113.9 比第 5 个未来行情日收盘 120 低 5.08%，严格超过 5%。
    assert _status(results, "short_term_sold_early") == STATUS_HIT


def test_sell_short_term_sold_early_boundary_is_strict() -> None:
    past = [100_000_000] * 5
    # 前四日很高、最后一日回落到 110：判据只看窗口最后一日收盘价。
    future = [130_000_000, 130_000_000, 130_000_000, 130_000_000, 110_000_000]
    bars = [*_bars(past, 9), (TRADE, PRICE), *_bars(future, 15)]
    args = {"quantity_before_units": 1000, "quantity_units": 500, "avg_cost_units": PRICE}
    # 恰低 5%（110 → 104.5）不命中；低 5.09% 命中。
    at_boundary = evaluate_trade_flags(
        _sell(net_price_units=104_500_000, **args),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(at_boundary, "short_term_sold_early") == STATUS_MISS
    below = evaluate_trade_flags(
        _sell(net_price_units=104_400_000, **args),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(below, "short_term_sold_early") == STATUS_HIT


def test_instant_flags_share_short_term_margin() -> None:
    # 即时类改用 SHORT_TERM_MARGIN_BPS 后，仍保持「5% 边界含、越界不含」的原行为。
    assert SHORT_TERM_MARGIN_BPS == 500
    past = [PRICE] * 5
    bars = [*_bars(past, 9), (TRADE, PRICE), *_flat(5, start_index=15)]
    args = {"quantity_before_units": 1000, "quantity_units": 500, "avg_cost_units": PRICE}
    at_boundary = evaluate_trade_flags(
        _sell(net_price_units=95_000_000, **args),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(at_boundary, "instant_top") == STATUS_HIT
    outside = evaluate_trade_flags(
        _sell(net_price_units=94_990_000, **args),
        bars=bars,
        calendar=CALENDAR,
        data_as_of=CALENDAR[-1],
    )
    assert _status(outside, "instant_top") == STATUS_MISS


def test_status_reason_covers_no_cost_basis_label() -> None:
    """`no_cost_basis` 的文案映射由 `_status_reason` 统一产出。"""
    from stock_core.trade_flags.engine import _status_reason

    assert _status_reason(STATUS_NO_COST_BASIS) == "no_cost_basis"
