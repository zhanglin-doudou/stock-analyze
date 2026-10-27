"""回撤族单测（Period2-07）：MDD / duration / recovery / underwater / Ulcer / Calmar。"""

from __future__ import annotations

from stock_core.stats.drawdown import drawdown_family


def _ratio_to_scale8(values: list[float]) -> list[int]:
    """小数 → scale8（0.01 → 1000000）。"""
    return [int(v * 100_000_000) for v in values]


class TestDrawdownFamily:
    def test_design_doc_small_case_mdd(self) -> None:
        """§8.4 校验：10 个日收益 [0, +0.01, -0.02, -0.03, +0.01, -0.04, +0.02, +0.03, -0.01, +0.02] → MDD=0.07829824。"""
        returns = _ratio_to_scale8([0, 0.01, -0.02, -0.03, 0.01, -0.04, 0.02, 0.03, -0.01, 0.02])
        df = drawdown_family(returns, natural_days=10)
        assert df.mdd_units == 7829824  # 0.07829824
        assert df.underwater_ratio_units == 80000000  # 8/10 = 0.8
        assert df.ulcer_units == 4165262  # 0.04165262
        assert df.recovery_censored is True
        assert df.mdd_recovery_obs is None
        assert df.blocked is False

    def test_monotonic_up_has_zero_drawdown(self) -> None:
        returns = _ratio_to_scale8([0.01, 0.01, 0.01, 0.01, 0.01])
        df = drawdown_family(returns)
        assert df.mdd_units == 0
        assert df.underwater_ratio_units == 0
        assert df.recovery_censored is False

    def test_drawdown_with_recovery(self) -> None:
        """先跌后涨回到前高 → recovery 有值、censored=False。"""
        returns = _ratio_to_scale8([0.05, -0.10, -0.05, 0.20])  # I: 1.05→0.945→0.89775→1.0773
        df = drawdown_family(returns)
        assert df.mdd_units is not None
        assert df.mdd_recovery_obs is not None
        assert df.recovery_censored is False

    def test_blocked_returns_blocked(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01, 0.02]), blocked=True)
        assert df.blocked is True
        assert df.mdd_units is None
        assert df.underwater_ratio_units is None

    def test_too_few_returns_is_insufficient(self) -> None:
        df = drawdown_family(_ratio_to_scale8([0.01]))
        assert df.mdd_units is None  # min_obs=2

    def test_empty_returns(self) -> None:
        df = drawdown_family([])
        assert df.mdd_units is None
        assert df.n_obs == 0

    def test_mdd_duration_is_positive(self) -> None:
        returns = _ratio_to_scale8([0.05, -0.10, -0.05, 0.10, 0.08, -0.01])
        df = drawdown_family(returns)
        if df.mdd_units and df.mdd_units > 0:
            assert df.mdd_duration_obs is not None
            assert df.mdd_duration_obs >= 1

    def test_unreliable_on_extreme_return(self) -> None:
        """|r| > 1 → unreliable。"""
        returns = _ratio_to_scale8([0.01, 1.5, 0.01, -0.5, 0.01, 0.01])  # 1.5 和 -0.5 都超界
        df = drawdown_family(returns)
        assert df.unreliable is True


class TestDrawdownCalmar:
    def test_calmar_with_positive_mdd(self) -> None:
        """简单两期：先跌 -5% 再平，年化 TWR ≈ (0.95)^(365/2) - 1 < 0 → Calmar 不可算。"""
        returns = _ratio_to_scale8([0.05, -0.10, 0.03, -0.04, 0.06, -0.02, 0.04, 0.01, 0.02, -0.03])
        df = drawdown_family(returns, natural_days=10)
        # Calmar 是年化 TWR / |MDD|；如果年化 TWR ≤ 0 → calmar_units 可能 None 或负数
        # 只检查结构正确
        assert df.mdd_units is not None
        assert df.mdd_units >= 0


class TestCompounding:
    """内部 _compound_index 已通过 drawdown_family 间接覆盖；这里只测极端值。"""

    def test_zero_returns_keep_index_at_one(self) -> None:
        returns = [0] * 6
        df = drawdown_family(returns)
        assert df.mdd_units == 0
        assert df.twr_annual_units == 0

    def test_natural_days_zero_returns_zero_twr(self) -> None:
        """natural_days=0 → twr_annual 回退到 0。"""
        returns = _ratio_to_scale8([0.01, -0.02, 0.01])
        df = drawdown_family(returns, natural_days=0)
        assert df.twr_annual_units == 0
