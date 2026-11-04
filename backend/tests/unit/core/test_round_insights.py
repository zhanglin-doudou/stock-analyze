"""回合洞察纯函数单测（设计 2026-11-04 §3/§5；AC-F1~F12、AC-S1、AC-D1~D5、AC-K1~K5）。

覆盖 `stock_core.insights` 四个模块的公式与边界：真实交易日历、B0~B3、
公司行动/停牌/无日历降级、加仓抄底/卖飞判定、20D B3 排序与样本门槛。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from stock_contracts.enums import ForwardReturnStatus, RoundBucket
from stock_core.insights import aggregate as agg
from stock_core.insights import calendar as cal
from stock_core.insights import classify
from stock_core.insights.forward import (
    ForwardTrade,
    _execution_gap,
    _price_jump,
    compute_forward,
    price_net,
)

CAL = tuple(date(2026, 1, 5) + timedelta(days=offset) for offset in range(30))  # 30 个“交易日”


def _buy() -> ForwardTrade:
    """附录 A.1：1000 股，毛价 10.00，费用 5.00（amount=-10005.00）。"""
    return ForwardTrade(
        dedupe_key="600519:buy",
        trade_id="trd_buy",
        version=1,
        side="buy",
        d0=CAL[0],
        price_units=10_000_000,
        amount_units=-1_000_500,
        quantity_units=10_000_000,
    )


def _sell() -> ForwardTrade:
    """附录 A.3：500 股，毛价 20.00，费用 10.00（amount=9990.00）。"""
    return ForwardTrade(
        dedupe_key="600519:sell",
        trade_id="trd_sell",
        version=1,
        side="sell",
        d0=CAL[0],
        price_units=20_000_000,
        amount_units=999_000,
        quantity_units=5_000_000,
    )


# ── calendar ─────────────────────────────────────────────────


def test_normalize_dedupes_and_sorts() -> None:
    raw = [CAL[2], CAL[0], CAL[1], CAL[0]]
    assert cal.normalize(raw) == (CAL[0], CAL[1], CAL[2])


def test_holding_trading_days() -> None:
    assert cal.holding_trading_days((), CAL[0], CAL[1]) is None
    # open_date 晚于日历最后一天 → None（行情未覆盖，不误算 0）
    assert cal.holding_trading_days(CAL, date(2027, 1, 1), date(2027, 1, 2)) is None
    # 同日/倒序 → 1（回合语义）
    assert cal.holding_trading_days(CAL, CAL[0], CAL[0]) == 1
    assert cal.holding_trading_days(CAL, CAL[2], CAL[0]) == 1
    # (open, close] = 2 天
    assert cal.holding_trading_days(CAL, CAL[1], CAL[3]) == 2


def test_base_index_and_nth_after() -> None:
    assert cal.base_index(CAL, CAL[0]) == 0
    assert cal.base_index(CAL, CAL[0]) is not None
    assert cal.base_index(CAL, date(2026, 1, 4)) is None  # 早于日历
    assert cal.nth_after(CAL, 0, 5) == CAL[5]
    assert cal.nth_after(CAL, len(CAL) - 1, 5) is None
    assert cal.nth_after(CAL, -2, 1) is None


def test_trading_days_after_and_count_between() -> None:
    assert cal.trading_days_after(CAL, CAL[0]) == len(CAL) - 1
    assert cal.count_between(CAL, CAL[3], CAL[0]) == 0
    assert cal.count_between(CAL, CAL[0], CAL[3]) == 3
    assert cal.count_between(CAL, CAL[0], date(2026, 1, 7)) == 2  # 端点不在日历


def test_is_late_annotated() -> None:
    assert cal.is_late_annotated((), CAL[0], CAL[5]) is False
    assert cal.is_late_annotated(CAL, date(2026, 1, 4), CAL[5]) is False
    # 20 个交易日后的截止日 = CAL[20]；晚于它才算事后标注
    assert cal.is_late_annotated(CAL, CAL[0], CAL[21]) is True
    assert cal.is_late_annotated(CAL, CAL[0], CAL[20]) is False
    # 日历不足 20 日 → 截止日不存在，无法判定 → False
    short = CAL[:10]
    assert cal.is_late_annotated(short, short[0], date(2027, 1, 1)) is False


# ── forward：价格/公式 ───────────────────────────────────────


def test_price_net_uses_amount_over_quantity() -> None:
    assert price_net(_buy()) == 10_005_000  # 含费净价 10.005
    assert price_net(_sell()) == 19_980_000
    assert price_net(ForwardTrade("k", "t", 1, "buy", CAL[0], 10_000_000, 0, 10_000_000)) is None
    assert price_net(ForwardTrade("k", "t", 1, "buy", CAL[0], 10_000_000, -1, 0)) is None


def test_execution_gap_guards() -> None:
    assert _execution_gap("buy", None, 10_000_000) is None
    assert _execution_gap("buy", 0, 10_000_000) is None
    assert _execution_gap("buy", 10_000_000, 0) is None
    assert _execution_gap("buy", 10_000_000, 10_000_000) == 0
    assert _execution_gap("sell", 20_000_000, 20_000_000) == 0


def test_price_jump_ignores_zero_previous() -> None:
    assert _price_jump([(CAL[0], 0), (CAL[1], 5_000_000)]) is False
    assert _price_jump([(CAL[0], 10_000_000), (CAL[1], 13_000_000)]) is True
    assert _price_jump([(CAL[0], 10_000_000), (CAL[1], 10_500_000)]) is False


# ── forward：状态矩阵 ───────────────────────────────────────


def test_forward_no_calendar() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=(),
        bars=(),
        benchmark_series=None,
        data_as_of=None,
    )
    assert result.status == ForwardReturnStatus.NO_CALENDAR
    assert result.ret_b1_units is None


def test_forward_no_calendar_when_d0_before_start() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=(CAL[3], CAL[4]),
        bars=(),
        benchmark_series=None,
        data_as_of=None,
    )
    assert result.status == ForwardReturnStatus.NO_CALENDAR


def test_forward_b0_b1_b2_b3_match_appendix_a1() -> None:
    bars = [(CAL[0], 10_000_000), (CAL[5], 10_500_000)]
    bench = [(day, 0) for day in CAL[1:5]] + [(CAL[5], 2_000_000)]
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=bars,
        benchmark_series=bench,
        data_as_of=CAL[10],
    )
    assert result.status == ForwardReturnStatus.OK
    assert result.c0_units == 10_000_000
    assert result.cn_units == 10_500_000
    assert result.ret_b0_units == 0  # (10−10)/10
    assert result.ret_b0_net_units == -49_975  # (10−10.005)/10.005
    assert result.ret_b1_units == 5_000_000  # +5.00%
    assert result.ret_b2_units == 4_947_526  # +4.947526%
    assert result.ret_b3_units == 3_000_000  # +5% − 2%
    assert result.max_close_units == 10_500_000
    assert result.min_close_units == 10_500_000
    assert result.price_jump_review is False


def test_forward_sell_side_directions() -> None:
    bars = [(CAL[0], 20_000_000), (CAL[5], 21_000_000)]
    result = compute_forward(
        _sell(),
        horizon=5,
        calendar=CAL,
        bars=bars,
        benchmark_series=[(CAL[5], 2_000_000)],
        data_as_of=CAL[10],
    )
    assert result.ret_b0_units == 0
    assert result.ret_b0_net_units == -100_000  # 19.98 低于当日收盘 20.00
    assert result.ret_b1_units == 5_000_000
    assert result.ret_b3_units == 3_000_000


def test_forward_pending_when_horizon_or_as_of_short() -> None:
    bars = [(CAL[0], 10_000_000)]
    short_result = compute_forward(
        _buy(),
        horizon=20,
        calendar=CAL[:10],
        bars=bars,
        benchmark_series=None,
        data_as_of=CAL[9],
    )
    assert short_result.status == ForwardReturnStatus.PENDING

    future_result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=bars,
        benchmark_series=None,
        data_as_of=CAL[2],
    )
    assert future_result.status == ForwardReturnStatus.PENDING


def test_forward_corporate_action_fail_closed() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000)],
        benchmark_series=None,
        data_as_of=CAL[10],
        corporate_action=True,
    )
    assert result.status == ForwardReturnStatus.CORPORATE_ACTION
    assert result.ret_b1_units is None


def test_forward_no_bar_when_close_missing() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=(),
        benchmark_series=None,
        data_as_of=CAL[10],
    )
    assert result.status == ForwardReturnStatus.NO_BAR


def test_forward_stale_price_and_no_bar_when_stale_too_long() -> None:
    # 只有 d0 收盘：目标日 5 个交易日后 → stale_price（停牌 ≤5 日）
    stale = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000)],
        benchmark_series=None,
        data_as_of=CAL[10],
    )
    assert stale.status == ForwardReturnStatus.STALE_PRICE
    assert stale.max_close_units is None
    assert stale.min_close_units is None

    # 目标日 6 个交易日后 → 超过停牌容忍 → no_bar
    too_stale = compute_forward(
        _buy(),
        horizon=6,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000)],
        benchmark_series=None,
        data_as_of=CAL[10],
    )
    assert too_stale.status == ForwardReturnStatus.NO_BAR


def test_forward_zero_close_is_no_bar() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 0), (CAL[1], 5_000_000)],
        benchmark_series=None,
        data_as_of=CAL[10],
    )
    # 当日收盘为 0 属脏数据：直接 no_bar，不做除零
    assert result.status == ForwardReturnStatus.NO_BAR


def test_forward_ok_without_quantity_yields_null_net_fields() -> None:
    trade = ForwardTrade("k", "t", 1, "buy", CAL[0], 10_000_000, 0, 0)
    result = compute_forward(
        trade,
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000), (CAL[5], 10_500_000)],
        benchmark_series=[(CAL[5], 2_000_000)],
        data_as_of=CAL[10],
    )
    assert result.status == ForwardReturnStatus.OK
    assert result.price_net_units is None
    assert result.ret_b0_net_units is None
    assert result.ret_b2_units is None


def test_forward_divisor_missing_when_benchmark_legs_empty() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000), (CAL[5], 10_500_000)],
        benchmark_series=[(CAL[10], 1_000_000)],
        data_as_of=CAL[10],
    )
    assert result.status == ForwardReturnStatus.DIVISOR_MISSING
    assert result.bench_ret_units is None


def test_forward_divisor_missing_when_benchmark_absent() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000), (CAL[5], 10_500_000)],
        benchmark_series=None,
        data_as_of=CAL[10],
    )
    assert result.status == ForwardReturnStatus.DIVISOR_MISSING
    assert result.ret_b1_units == 5_000_000
    assert result.ret_b3_units is None


def test_forward_price_jump_flagged() -> None:
    result = compute_forward(
        _buy(),
        horizon=5,
        calendar=CAL,
        bars=[(CAL[0], 10_000_000), (CAL[1], 9_000_000), (CAL[5], 13_000_000)],
        benchmark_series=[(CAL[5], 0)],
        data_as_of=CAL[10],
    )
    assert result.price_jump_review is True


# ── classify ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("td", "status", "expected"),
    [
        (None, "closed", RoundBucket.UNKNOWN),
        (3, "open", RoundBucket.UNKNOWN),
        (1, "closed", RoundBucket.SHORT),
        (2, "closed", RoundBucket.SWING),
        (20, "closed", RoundBucket.SWING),
        (21, "closed", RoundBucket.TREND),
    ],
)
def test_bucket_of(td: int | None, status: str, expected: str) -> None:
    assert classify.bucket_of(td, status=status) == expected


def test_addon_and_below_avg() -> None:
    assert classify.is_addon(0) is False
    assert classify.is_addon(1) is True
    assert classify.is_below_avg(9_000_000, 10_000_000) is True
    assert classify.is_below_avg(10_000_000, 10_000_000) is False
    assert classify.is_below_avg(9_000_000, 0) is False


def test_dip_depth_units() -> None:
    assert classify.dip_depth_units(9_000_000, 0) is None
    assert classify.dip_depth_units(9_000_000, 10_000_000) == 10_000_000  # 10%


def test_dip_and_rise_sequences() -> None:
    assert classify.is_dip_sequence([]) is False
    assert classify.is_dip_sequence([9_000_000]) is False
    assert classify.is_dip_sequence([10_000_000, 9_000_000, 8_000_000]) is True
    assert classify.is_dip_sequence([10_000_000, 10_000_000]) is False

    assert classify.is_rise_sequence([]) is False
    assert classify.is_rise_sequence([10_000_000, 11_000_000]) is True
    assert classify.is_rise_sequence([10_000_000, 9_500_000]) is False


def test_profit_taking_and_sold_too_early() -> None:
    assert classify.is_profit_taking(20_000_000, 0) is False
    assert classify.is_profit_taking(20_000_000, 19_000_000) is True
    assert classify.is_profit_taking(19_000_000, 19_000_000) is False

    assert classify.sold_too_early(None, 19_980_000) is False
    assert classify.sold_too_early(22_000_000, 0) is False
    # 22.00 × 100 > 19.98 × 103 = 2057.94 → 2200 > 2057.94 → 卖飞
    assert classify.sold_too_early(22_000_000, 19_980_000) is True
    # 边界：max 恰为 1.03 倍 → 不触发（严格大于）
    pnet = 10_000_000
    assert classify.sold_too_early(10_300_000, pnet) is False


def test_continued_decline_thresholds() -> None:
    assert classify.continued_decline(-3_000_000, None, 10_000_000) is True  # B2 = −3%
    assert classify.continued_decline(None, None, 10_000_000) is False
    assert classify.continued_decline(0, 9_500_000, 10_000_000) is True  # 最差 −5%
    assert classify.continued_decline(0, 9_600_000, 10_000_000) is False
    assert classify.continued_decline(0, 9_000_000, 0) is False  # 净价缺失不做阈值


def test_additional_loss_units() -> None:
    # 800 股 × (860.00 − 902.00) = −33,600.00 → Money 标度 2 = −3,360,000
    assert classify.additional_loss_units(8_000_000, 902_000_000, 860_000_000) == -3_360_000
    # 800 股 × (830.00 − 902.00) = −57,600.00
    assert classify.additional_loss_units(8_000_000, 902_000_000, 830_000_000) == -5_760_000


# ── aggregate ───────────────────────────────────────────────


def test_weighted_mean() -> None:
    assert agg.weighted_mean([]) is None
    assert agg.weighted_mean([(None, 100)]) is None
    assert agg.weighted_mean([(1_000_000, 0)]) is None
    assert agg.weighted_mean([(1_000_000, 100), (3_000_000, 100)]) == 2_000_000
    assert agg.weighted_mean([(1_000_000, 100), (None, 100)]) == 1_000_000


def test_combined_20() -> None:
    assert agg.combined_20(None, None) is None
    assert agg.combined_20(8_000_000, None) == 8_000_000
    assert agg.combined_20(None, 5_000_000) == 5_000_000
    assert agg.combined_20(8_000_000, 5_000_000) == 6_500_000


@pytest.mark.parametrize(
    ("n", "expected"),
    [(30, "ok"), (31, "ok"), (29, "insufficient"), (10, "insufficient"), (9, "no_conclusion")],
)
def test_sample_status(n: int, expected: str) -> None:
    assert agg.sample_status(n) == expected


def test_quadrant() -> None:
    assert agg.quadrant(None, 1) is None
    assert agg.quadrant(1, None) is None
    assert agg.quadrant(1, 1) == "both_positive"
    assert agg.quadrant(0, 0) == "both_positive"  # 0 归入正侧
    assert agg.quadrant(1, -1) == "buy_positive_sell_negative"
    assert agg.quadrant(-1, 1) == "buy_negative_sell_positive"
    assert agg.quadrant(-1, -1) == "both_negative"
