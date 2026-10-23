"""core/nav 单元测试（Period2-03）：份额法 TWR + XIRR/MWR + back_solve。

测试原则（避免过拟合）：
- 手算 fixture 与公式推导对齐，不绑定具体 ID/时间戳；
- 不变量优先：TWR 与外部现金流择时无关（现金流立即投入时，相同持仓涨幅路径 → 相同 TWR）；
- 边界：空序列、单点、降序报错、全零现金流；
- XIRR 用独立公式（(终值/初值)^(365/n) - 1）对拍。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from stock_core.nav import (
    CashflowPoint,
    back_solve_initial_cash,
    build_nav_series,
    compute_xirr,
)


def cents(s: str) -> int:
    return int(Decimal(s) * 100)


def ratio(s: str) -> int:
    """百分比字符串 → scale=8 标度整数。'10.00' → 10_000_000 (0.1)。"""
    return int(Decimal(s) / Decimal(100) * Decimal(10**8))


# 与 nav._XIRR_HIGH=100 对齐（scale=8）
_XIRR_HIGH_UNITS = 100 * 10**8


# ── 份额法 TWR ──────────────────────────────────────────────


def test_twr_basic_no_cashflow_matches_geometric() -> None:
    """无外部现金流时，TWR = 几何累计收益。"""
    points = [
        (date(2024, 1, 1), cents("10000.00"), 0),
        (date(2024, 1, 2), cents("11000.00"), 0),  # +10%
        (date(2024, 1, 3), cents("9900.00"), 0),  # -10%
        (date(2024, 1, 4), cents("10890.00"), 0),  # +10%
    ]
    s = build_nav_series(points)
    # 手算：1.1 * 0.9 * 1.1 - 1 = 0.089
    assert s.twr_units == pytest.approx(ratio("8.90"), rel=1e-6)
    # 日收益率
    assert s.points[0].return_units == 0
    assert s.points[1].return_units == ratio("10.00")
    assert s.points[2].return_units == ratio("-10.00")
    assert s.points[3].return_units == ratio("10.00")


def test_twr_cashflow_timing_does_not_affect_twr() -> None:
    """份额法核心不变量：现金流立即投入时，相同持仓涨幅路径 → TWR 相同。

    场景 A：全仓，无现金流。
    场景 B：Day1 末入金 5000 并立即投入股票（不留现金）。
    两者持仓涨幅都是 +10%, +10%，TWR 应相同。
    """
    # 场景 A：全仓
    s_a = build_nav_series(
        [
            (date(2024, 1, 1), cents("10000.00"), 0),
            (date(2024, 1, 2), cents("11000.00"), 0),
            (date(2024, 1, 3), cents("12100.00"), 0),
        ]
    )
    # 场景 B：Day1 入金 5000 立即买入 → Day1 总资产 16000（持仓11000+新买入5000）
    # Day2 持仓涨 10%：16000 * 1.1 = 17600
    s_b = build_nav_series(
        [
            (date(2024, 1, 1), cents("10000.00"), 0),
            (date(2024, 1, 2), cents("16000.00"), cents("5000.00")),
            (date(2024, 1, 3), cents("17600.00"), 0),
        ]
    )
    # TWR 应相同（21%）
    assert s_a.twr_units == s_b.twr_units
    assert s_a.twr_units == pytest.approx(ratio("21.00"), rel=1e-6)


def test_twr_withdrawal_immediate() -> None:
    """出金后剩余持仓继续涨，TWR 只反映持仓涨幅（出金不扭曲收益率）。"""
    # Day0: 持仓 10000
    # Day1: 持仓涨到 11000，出金 5500（卖一半），剩余持仓 5500，总资产 5500
    # Day2: 剩余持仓涨 10% → 6050
    points = [
        (date(2024, 1, 1), cents("10000.00"), 0),
        (date(2024, 1, 2), cents("5500.00"), cents("-5500.00")),
        (date(2024, 1, 3), cents("6050.00"), 0),
    ]
    s = build_nav_series(points)
    # 持仓涨幅：Day1 +10%, Day2 +10% → TWR = 1.1 * 1.1 - 1 = 0.21
    assert s.twr_units == pytest.approx(ratio("21.00"), rel=1e-6)
    # Day1 出金：shares 从 1 减到 0.5（卖出一半）
    assert s.points[1].shares_units == ratio("50.00")  # 0.5


def test_twr_cashflow_no_price_change_keeps_nav() -> None:
    """出/入金当天若持仓价格不变，NAV 不变（现金流只改变份额）。"""
    points = [
        (date(2024, 1, 1), cents("10000.00"), 0),
        # Day2 价格不变（持仓仍 10000），出金 5000 → 总资产 5000
        (date(2024, 1, 2), cents("5000.00"), cents("-5000.00")),
    ]
    s = build_nav_series(points)
    assert s.points[1].nav_units == s.points[0].nav_units
    assert s.points[1].return_units == 0


def test_twr_nav_excludes_cashflow_from_return() -> None:
    """NAV_t = (V_t - CF_t) / shares：入金不计入投资收益，NAV 不变。"""
    points = [
        (date(2024, 1, 1), cents("10000.00"), 0),
        # Day2 入金 5000 立即投入：持仓 10000 + 新买入 5000 = 15000，总资产 15000
        (date(2024, 1, 2), cents("15000.00"), cents("5000.00")),
    ]
    s = build_nav_series(points)
    # 持仓价格不变 → NAV 不变，r=0
    assert s.points[1].nav_units == s.points[0].nav_units
    assert s.points[1].return_units == 0


def test_twr_empty_returns_empty() -> None:
    s = build_nav_series([])
    assert s.points == ()
    assert s.twr_units == 0
    assert s.annualized_twr_units == 0
    assert s.natural_days == 0


def test_twr_single_point_zero_return() -> None:
    points = [(date(2024, 1, 1), cents("10000.00"), 0)]
    s = build_nav_series(points)
    assert len(s.points) == 1
    assert s.points[0].return_units == 0
    assert s.twr_units == 0
    assert s.annualized_twr_units == 0
    assert s.natural_days == 0


def test_twr_rejects_descending_dates() -> None:
    with pytest.raises(ValueError, match="升序"):
        build_nav_series(
            [
                (date(2024, 1, 3), cents("10000.00"), 0),
                (date(2024, 1, 1), cents("11000.00"), 0),
            ]
        )


def test_twr_annualized_uses_natural_days() -> None:
    """年化 TWR = (1+TWR)^(365/n) - 1。365 天涨 10% → 年化 ≈ 10%。"""
    points = [
        (date(2024, 1, 1), cents("10000.00"), 0),
        (date(2024, 12, 31), cents("11000.00"), 0),
    ]
    s = build_nav_series(points)
    assert s.twr_units == pytest.approx(ratio("10.00"), rel=1e-6)
    assert s.annualized_twr_units == pytest.approx(ratio("10.00"), rel=1e-3)
    assert s.natural_days == 365


def test_twr_invariants() -> None:
    """不变量：Σ CF = total_cashflow；ending_value = 最后一日资产；points 升序。"""
    points = [
        (date(2024, 1, 1), cents("10000.00"), 0),
        (date(2024, 1, 2), cents("11000.00"), cents("1000.00")),
        (date(2024, 1, 3), cents("12000.00"), cents("-500.00")),
    ]
    s = build_nav_series(points)
    assert s.total_cashflow_units == cents("500.00")
    assert s.ending_value_units == points[-1][1]
    assert s.initial_value_units == points[0][1]
    for i in range(1, len(s.points)):
        assert s.points[i].trade_date > s.points[i - 1].trade_date


# ── XIRR / MWR ──────────────────────────────────────────────


def test_xirr_one_year_10pct() -> None:
    """1 年投入 10000 收回 11000 → XIRR ≈ 10%（365 天口径）。"""
    cfs = [
        CashflowPoint(date(2024, 1, 1), cents("-10000.00")),
        CashflowPoint(date(2025, 1, 1), cents("11000.00")),
    ]
    mwr = compute_xirr(cfs)
    # 手算：(11000/10000)^(365/366) - 1 ≈ 0.0997（2024 闰年 366 天）
    assert mwr / 1e8 == pytest.approx(0.0997, rel=1e-3)


def test_xirr_no_solution_returns_zero() -> None:
    """全为正或全为负的现金流 → 无解 → 返回 0。"""
    assert compute_xirr([CashflowPoint(date(2024, 1, 1), cents("10000.00"))]) == 0
    assert compute_xirr([CashflowPoint(date(2024, 1, 1), cents("-10000.00"))]) == 0


def test_xirr_empty_returns_zero() -> None:
    assert compute_xirr([]) == 0


def test_xirr_rejects_descending_dates() -> None:
    with pytest.raises(ValueError, match="升序"):
        compute_xirr(
            [
                CashflowPoint(date(2024, 1, 3), cents("-10000.00")),
                CashflowPoint(date(2024, 1, 1), cents("11000.00")),
            ]
        )


def test_xirr_with_intermediate_cashflow() -> None:
    """中间有现金流时，XIRR 仍应在合理范围。"""
    cfs = [
        CashflowPoint(date(2024, 1, 1), cents("-10000.00")),
        CashflowPoint(date(2024, 7, 1), cents("-5000.00")),
        CashflowPoint(date(2024, 12, 31), cents("17000.00")),
    ]
    mwr = compute_xirr(cfs)
    # 总投入 15000，收回 17000，盈利 2000；年化应在 8~25%
    assert ratio("8.00") < mwr < ratio("25.00")


def test_xirr_negative_return() -> None:
    """亏损场景：投入 10000，收回 9000 → XIRR 为负。"""
    cfs = [
        CashflowPoint(date(2024, 1, 1), cents("-10000.00")),
        CashflowPoint(date(2025, 1, 1), cents("9000.00")),
    ]
    mwr = compute_xirr(cfs)
    assert mwr < 0
    assert mwr / 1e8 == pytest.approx(-0.1, rel=1e-2)


def test_xirr_extreme_short_period_returns_boundary() -> None:
    """极短周期（数日）真实解超出 [-0.99, 100] 区间 → 返回边界近似。"""
    # 3 天涨 76%，年化远超 10000%，解在区间外
    cfs = [
        CashflowPoint(date(2024, 1, 1), cents("-10000.00")),
        CashflowPoint(date(2024, 1, 4), cents("17600.00")),
    ]
    mwr = compute_xirr(cfs)
    # 应返回上界 100（10000%）
    assert mwr == _XIRR_HIGH_UNITS


def test_xirr_total_loss_returns_lower_boundary() -> None:
    """几乎全亏（收回 1 元）→ 真实解 < -0.99 → 返回下界。"""
    cfs = [
        CashflowPoint(date(2024, 1, 1), cents("-10000.00")),
        CashflowPoint(date(2025, 1, 1), cents("1.00")),
    ]
    mwr = compute_xirr(cfs)
    # 真实解接近 -1（-100%），超出下界 -0.99 → 返回 -0.99
    assert mwr == _from_decimal_units("-0.99")


def _from_decimal_units(s: str) -> int:
    return int(Decimal(s) * Decimal(10**8))


# ── back_solve ──────────────────────────────────────────────


def test_back_solve_subtracts_first_amount() -> None:
    """期初现金 = 首行余额 - 首行发生金额。"""
    assert back_solve_initial_cash(cents("10500.00"), cents("500.00")) == cents("10000.00")


def test_back_solve_withdrawal() -> None:
    assert back_solve_initial_cash(cents("9500.00"), cents("-500.00")) == cents("10000.00")
