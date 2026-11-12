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
        (0, "closed", RoundBucket.SCALP),
        (1, "closed", RoundBucket.SCALP),
        (3, "closed", RoundBucket.SCALP),
        (4, "closed", RoundBucket.SHORT),
        (10, "closed", RoundBucket.SHORT),
        (11, "closed", RoundBucket.POSITION),
        (63, "closed", RoundBucket.POSITION),
        (126, "closed", RoundBucket.POSITION),
        (127, "closed", RoundBucket.LONG),
        (252, "closed", RoundBucket.LONG),
    ],
)
def test_bucket_of(td: int | None, status: str, expected: str) -> None:
    assert classify.bucket_of(td, status=status) == expected


def test_addon() -> None:
    assert classify.is_addon(0) is False
    assert classify.is_addon(1) is True


def test_sold_early_side() -> None:
    # 净卖价严格低于均价 → 止损侧；等号也归止损侧（用户确认口径）。
    assert classify.sold_early_side(9_000_000, 10_000_000) == classify.SOLD_EARLY_STOP_LOSS
    assert classify.sold_early_side(10_000_000, 10_000_000) == classify.SOLD_EARLY_STOP_LOSS
    assert classify.sold_early_side(11_000_000, 10_000_000) == classify.SOLD_EARLY_PROFIT
    # 零成本是有效成本：任意正卖价归止盈侧。
    assert classify.sold_early_side(1_000_000, 0) == classify.SOLD_EARLY_PROFIT
    # 成本缺失或负成本无法分侧。
    assert classify.sold_early_side(9_000_000, None) is None
    assert classify.sold_early_side(9_000_000, -1) is None


def test_is_round_drawdown() -> None:
    # 严格低于 95%：100 vs 105.27 → 95% 线 100.0065，100 低于它 → 回撤。
    assert classify.is_round_drawdown(100_000_000, 105_270_000) is True
    # 恰为 95% → 不触发（严格）。
    assert classify.is_round_drawdown(95_000_000, 100_000_000) is False
    assert classify.is_round_drawdown(94_990_000, 100_000_000) is True
    assert classify.is_round_drawdown(None, 100_000_000) is False
    assert classify.is_round_drawdown(100_000_000, None) is False
    assert classify.is_round_drawdown(0, 100_000_000) is False
    assert classify.is_round_drawdown(100_000_000, 0) is False


def test_is_partial_sell() -> None:
    assert classify.is_partial_sell(0, 10) is False
    assert classify.is_partial_sell(10, 10) is False  # 清仓，不是部分卖出
    assert classify.is_partial_sell(10, 11) is True
    assert classify.is_partial_sell(11, 10) is False  # 超卖（异常）不判定


def test_detect_t_patterns() -> None:
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    # 日内：先部分卖 30（持仓 100）再买 30。
    assert classify.detect_t_patterns([(d1, "sell", 30, 100), (d1, "buy", 30, 0)]) == (True, False)
    # 清仓后同日再买：不是部分卖出 → 都不算。
    assert classify.detect_t_patterns([(d1, "sell", 100, 100), (d1, "buy", 100, 0)]) == (
        False,
        False,
    )
    # 先买后卖：顺序不符 → 不算。
    assert classify.detect_t_patterns([(d1, "buy", 30, 10), (d1, "sell", 30, 40)]) == (False, False)
    # 跨日：部分卖后次日买 → 波段做T，不计日内。
    assert classify.detect_t_patterns([(d1, "sell", 30, 100), (d2, "buy", 30, 0)]) == (False, True)
    # 同一笔卖出已被同日买入配对 → 不再计入波段（即使后续还有买入）。
    assert classify.detect_t_patterns(
        [(d1, "sell", 30, 100), (d1, "buy", 10, 30), (d2, "buy", 20, 0)]
    ) == (True, False)
    # 两笔独立卖出：一笔日内、一笔跨日 → 两个状态都命中。
    assert classify.detect_t_patterns(
        [
            (d1, "sell", 30, 100),
            (d1, "buy", 30, 0),
            (d2, "sell", 20, 100),
            (date(2026, 6, 3), "buy", 20, 0),
        ]
    ) == (True, True)


def test_has_same_day_after() -> None:
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    assert classify.has_same_day_after([(d1, 5)], [(d1, 6)]) is True
    assert classify.has_same_day_after([(d1, 5)], [(d1, 5)]) is False
    assert classify.has_same_day_after([(d1, 5)], [(d1, 4)]) is False
    assert classify.has_same_day_after([(d1, 5)], [(d2, 6)]) is False


def test_rebalance_counterparty_only_accepts_real_security_trades() -> None:
    """日内调仓对手方：只认二级市场证券成交（类别词表复用 EventKind）。"""
    real = {"security_code": "002049", "category": "buy", "is_money_fund": False}
    assert classify.is_rebalance_counterparty(**real) is True
    assert classify.is_rebalance_counterparty(**{**real, "category": " SELL "}) is True
    # 资金伪代码、公司行动、新股申购、货基申赎都不是「买入/卖出其它证券」。
    assert classify.is_rebalance_counterparty(**{**real, "security_code": "CASH"}) is False
    for category in ("fee", "transfer", "interest", "dividend", "bonus", "rights", "new_stock"):
        assert classify.is_rebalance_counterparty(**{**real, "category": category}) is False, (
            category
        )
    assert classify.is_rebalance_counterparty(**{**real, "is_money_fund": True}) is False


def test_profit_taking_and_sold_too_early() -> None:
    # 零成本持仓：任意正卖价都算止盈；成本缺失/负成本不判定。
    assert classify.is_profit_taking(20_000_000, 0) is True
    assert classify.is_profit_taking(20_000_000, None) is False
    assert classify.is_profit_taking(20_000_000, -1) is False
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


def test_swing_fill_pairs_range_and_no_additivity() -> None:
    """AC-S3：fill 级波段配对仅 2–20 TD；与回合级统计独立（不可相加）。"""
    beyond = CAL[-1] + timedelta(days=1)
    fills = [
        (CAL[0], CAL[0], 100),  # 同日 → 1 TD（超短，排除）
        (CAL[0], CAL[1], 200),  # 1 TD（排除）
        (CAL[0], CAL[2], 300),  # 2 TD（计入）
        (CAL[0], CAL[20], -400),  # 20 TD（计入）
        (CAL[0], CAL[21], 500),  # 21 TD（趋势，排除）
        (beyond, beyond + timedelta(days=2), 600),  # 日历未覆盖 open_date → 排除
    ]
    stats = agg.swing_fill_pairs(fills, CAL)
    assert stats.pairs == 2
    assert stats.wins == 1
    assert stats.pnl_units == (300, -400)
    assert stats.holding_trading_days == (2, 20)
    assert agg.swing_fill_pairs([], CAL).pairs == 0


def test_pnl_class_tolerance_boundary() -> None:
    """保本容差取后端常量（100.00 元），边界含在内。"""
    tol = agg.PNL_FLAT_TOLERANCE_UNITS
    assert tol == 10_000
    assert agg.pnl_class(0) == agg.PNL_CLASS_FLAT
    assert agg.pnl_class(tol) == agg.PNL_CLASS_FLAT
    assert agg.pnl_class(-tol) == agg.PNL_CLASS_FLAT
    assert agg.pnl_class(tol + 1) == agg.PNL_CLASS_WIN
    assert agg.pnl_class(-tol - 1) == agg.PNL_CLASS_LOSS
    # 容差可调：仅供调用方显式覆盖，默认值不变。
    assert agg.pnl_class(0, tolerance_units=0) == agg.PNL_CLASS_FLAT
    assert agg.pnl_class(1, tolerance_units=0) == agg.PNL_CLASS_WIN


def test_holding_by_pnl_groups_average_and_missing_holding() -> None:
    """三类恒定三条：均值是「交易日 × 10」定点整数；缺持有日的回合计次不入均值。"""
    rows = [
        (15_000, 4),  # 盈利
        (12_000, 5),  # 盈利
        (0, 2),  # 保本
        (-20_000, 10),  # 亏损
        (-50_000, None),  # 亏损但缺持有交易日 → 计入 count，不参与均值
    ]
    groups = agg.holding_by_pnl(rows)
    assert [group.code for group in groups] == ["loss", "flat", "win"]
    by_code = {group.code: group for group in groups}
    assert (by_code["loss"].count, by_code["loss"].avg_holding_trading_days_x10) == (2, 100)
    assert (by_code["flat"].count, by_code["flat"].avg_holding_trading_days_x10) == (1, 20)
    # (4 + 5) / 2 = 4.5 日 → 45
    assert (by_code["win"].count, by_code["win"].avg_holding_trading_days_x10) == (2, 45)


def test_holding_by_pnl_empty_keeps_three_groups() -> None:
    groups = agg.holding_by_pnl([])
    assert [group.code for group in groups] == ["loss", "flat", "win"]
    assert [(group.count, group.avg_holding_trading_days_x10) for group in groups] == [
        (0, None),
        (0, None),
        (0, None),
    ]


def test_flag_glossary_covers_every_enumerated_flag() -> None:
    """旗标词表必须覆盖全部枚举值，且每个值都带中英文标签与说明（API-092/093）。"""
    from stock_core.insights.flags import (
        FILTER_FLAG_CODES,
        FLAG_DEFINITIONS,
        ITEM_FLAG_CODES,
    )

    codes = [entry.code for entry in FLAG_DEFINITIONS]
    assert len(codes) == len(set(codes)) == 12
    # items[].flags 的 8 个回合旗标（与 service 实际追加的字符串一一对应）。
    assert set(ITEM_FLAG_CODES) == {
        "bottom_buy",
        "stop_loss_sold_early",
        "profit_sold_early",
        "drawdown",
        "intraday_t",
        "swing_t",
        "intraday_rebalance_out",
        "intraday_rebalance_in",
    }
    # ?flag= 查询词表的 4 个别名。
    assert set(FILTER_FLAG_CODES) == {
        "sold_too_early",
        "reason_missing",
        "horizon_pending",
        "corporate_action",
    }
    for entry in FLAG_DEFINITIONS:
        assert entry.kind in {"item_flag", "filter_alias"}
        assert entry.label_zh and entry.label_en
        assert entry.description_zh and entry.description_en
