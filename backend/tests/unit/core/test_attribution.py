"""core/attribution 单元测试：§3.8 手算 golden 向量 + 原语 + 属性不变式。

金额单位：分（scale=2）。§3.8 手算表（v1.0 已人工批准）是 golden 的唯一来源。
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from stock_core.attribution import (
    FACTOR_CODES,
    FactorCode,
    FiveFactorModel,
    allocate,
    decompose,
    decompose_segment,
    rank_factors,
    ratio_na_reason,
    ratio_of,
    sector_amount_units,
    sector_return_units,
    share_of,
    shares_of,
    total_abs_amounts,
)
from stock_core.attribution.types import SegmentInput

# ── §3.8 手算向量（分）────────────────────────────────────────
# A_sell（区间前 lot，全卖）：base 570000、end 660000、sector −5700、
# 卖费 660、gross_sell 672000、股息全给 A（12000）。
# B_sell（新建 lot，卖 200）：base 242000、end 260000、sector 12100、
# gross_buy 240000、gross_sell 260000、买费分摊 120 + 卖费 260。
# B_hold（新建 lot，持有 200）：base 242000、end 280000、sector 24200、
# gross_buy 240000、买费分摊 120。
_A_SELL = SegmentInput(
    security_code="600000",
    kind="closed",
    quantity_units=600_0000,
    base_units=570_000,
    end_value_units=660_000,
    gross_buy_units=None,
    gross_sell_units=672_000,
    fee_units=660,
    sector_amount_units=-5_700,
    dividend_units=12_000,
)
_B_SELL = SegmentInput(
    security_code="600000",
    kind="closed",
    quantity_units=200_0000,
    base_units=242_000,
    end_value_units=260_000,
    gross_buy_units=240_000,
    gross_sell_units=260_000,
    fee_units=380,
    sector_amount_units=12_100,
)
_B_HOLD = SegmentInput(
    security_code="600000",
    kind="open",
    quantity_units=200_0000,
    base_units=242_000,
    end_value_units=280_000,
    gross_buy_units=240_000,
    gross_sell_units=None,
    fee_units=120,
    sector_amount_units=24_200,
)
_TOTAL_PNL = 172_840  # 1728.40


def _golden_segments() -> list[SegmentInput]:
    return [_A_SELL, _B_SELL, _B_HOLD]


def _segment_identity_total(seg: SegmentInput) -> int:
    """段级恒等式期望值（设计 §3.4）。

    终点锚：closed 段 = gross_sell（缺失回落 end_value）/ open 段 = end_value；
    起点锚：区间内建仓 = gross_buy / 区间前 lot = base（buy_timing=0，§3.4"仅 d_b ≥ T0"）。
    """
    end_anchor = seg.end_value_units if seg.gross_sell_units is None else seg.gross_sell_units
    start_anchor = seg.base_units if seg.gross_buy_units is None else seg.gross_buy_units
    return end_anchor - start_anchor + seg.dividend_units - seg.fee_units


class TestGoldenVector:
    """设计 §3.8 手算向量：五因子逐项 == 手算值、residual == 0。"""

    def test_five_factors_match_hand_computed(self) -> None:
        result = decompose(_golden_segments(), _TOTAL_PNL)
        assert result.factors[FactorCode.SELECTION] == 127_400  # 1274.00
        assert result.factors[FactorCode.SECTOR] == 30_600  # 306.00
        assert result.factors[FactorCode.BUY_TIMING] == 4_000  # 40.00
        assert result.factors[FactorCode.SELL_TIMING] == 12_000  # 120.00
        assert result.factors[FactorCode.FEE] == -1_160  # −11.60

    def test_residual_zero_and_identity(self) -> None:
        result = decompose(_golden_segments(), _TOTAL_PNL)
        assert result.residual_units == 0
        assert sum(result.factors.values()) + result.residual_units == _TOTAL_PNL

    def test_sample_counts_and_base_capital(self) -> None:
        result = decompose(_golden_segments(), _TOTAL_PNL)
        assert result.n_segments == 3
        assert result.n_attributed_segments == 3
        assert result.base_capital_units == 1_054_000  # 5700+2420+2420
        assert result.base_capital_attributed_units == 1_054_000

    def test_per_segment_identity_self_check(self) -> None:
        """段级恒等式（实现一致性自检，设计 §3.4）。"""
        for seg in _golden_segments():
            factors = decompose_segment(seg)
            total = (
                factors.selection_units
                + factors.sector_units
                + factors.buy_timing_units
                + factors.sell_timing_units
                - factors.fee_units
            )
            assert total == _segment_identity_total(seg)

    def test_model_protocol_entry(self) -> None:
        from stock_core.attribution import AttributionInput

        model = FiveFactorModel()
        assert model.model_id == "five_factor"
        assert model.definition_version == "1.0"
        result = model.decompose(
            AttributionInput(segments=tuple(_golden_segments()), total_pnl_units=_TOTAL_PNL)
        )
        assert result.factors[FactorCode.SELECTION] == 127_400

    def test_extra_fee_units_land_in_fee_factor(self) -> None:
        """独立费用行/未归因标的费用经 extra_fee_units 进 fee 因子（§3.4 fee 行）。

        契约：total_pnl 必须已含这些费用的现金效应（MTM 口径天然如此），
        residual 不受影响；调用方漏算时 residual 等额变大（不一致的显式信号，
        装配层落 valuation_gap 桶）。
        """
        # 正确用法：区间多付 500 独立费用 → total_pnl −500，fee 因子吸收，residual 不变
        result = decompose(_golden_segments(), _TOTAL_PNL - 500, extra_fee_units=500)
        assert result.factors[FactorCode.FEE] == -1_660
        assert result.residual_units == 0
        # 漏算对照：total_pnl 未含该费用 → residual = +500
        result_missing = decompose(_golden_segments(), _TOTAL_PNL, extra_fee_units=500)
        assert result_missing.factors[FactorCode.FEE] == -1_660
        assert result_missing.residual_units == 500


class TestUnattributedSegments:
    def test_unattributed_path_goes_to_residual(self) -> None:
        seg = SegmentInput(
            security_code="019547",
            kind="open",
            quantity_units=100_0000,
            base_units=10_000,
            end_value_units=10_500,
            gross_buy_units=None,
            gross_sell_units=None,
            fee_units=0,
            sector_amount_units=None,
        )
        result = decompose([seg], total_pnl_units=500)
        assert all(result.factors[code] == 0 for code in FACTOR_CODES)
        assert result.residual_units == 500
        assert result.n_segments == 1
        assert result.n_attributed_segments == 0
        assert result.base_capital_units == 10_000
        assert result.base_capital_attributed_units == 0

    def test_mixed_attributed_and_not(self) -> None:
        attributed = SegmentInput(
            security_code="600000",
            kind="open",
            quantity_units=100_0000,
            base_units=10_000,
            end_value_units=10_500,
            gross_buy_units=None,
            gross_sell_units=None,
            fee_units=7,
            sector_amount_units=30,
        )
        unattributed = SegmentInput(
            security_code="510300",
            kind="open",
            quantity_units=100_0000,
            base_units=20_000,
            end_value_units=19_000,
            gross_buy_units=None,
            gross_sell_units=None,
            fee_units=3,
            sector_amount_units=None,
        )
        # MTM total_pnl：归因段净损益 493（=ΔMV 500 − 费 7）+ 未归因段 −1003（=−1000 − 3）
        result = decompose([attributed, unattributed], total_pnl_units=-510, extra_fee_units=3)
        # attributed: selection = 500−30, sector 30
        assert result.factors[FactorCode.SELECTION] == 470
        assert result.factors[FactorCode.SECTOR] == 30
        # fee = −(7 + 3)：含未归因标的费用（§3.4 fee 行 / PRD FR-R5.4）
        assert result.factors[FactorCode.FEE] == -10
        # residual = −510 − 490 = −1000 = 未归因段**无费**市值损益
        # （fee 因子承担 −3 与 total 中的 −3 抵消）
        assert result.residual_units == -1_000
        assert result.n_segments == 2
        assert result.n_attributed_segments == 1
        assert result.base_capital_units == 30_000
        assert result.base_capital_attributed_units == 10_000
        # 装配层桶口径（评审 P1-#7 已裁决 fee=区间费用合计）：§3.6 字面桶（含费）
        # = residual − 未归因费用 = −1003；装配层加回费用后 bucket = residual = −1000，
        # 费用统一由 fee 因子呈现（残差桶为"无费视角"，否则 F_u 系统性落 valuation_gap）。
        assert result.residual_units - 3 == -1_003


class TestAllocate:
    def test_exact_split(self) -> None:
        assert allocate(100, [1, 1]) == (50, 50)

    def test_tail_to_last(self) -> None:
        assert allocate(100, [1, 2]) == (33, 67)
        assert allocate(100, [3]) == (100,)

    def test_sum_preserved(self) -> None:
        assert sum(allocate(101, [2, 3, 7])) == 101

    def test_empty_and_zero_weights(self) -> None:
        assert allocate(100, []) == ()
        assert allocate(100, [0, 0]) == ()
        assert allocate(100, [-1, 2]) == (0, 100)

    def test_zero_total(self) -> None:
        assert allocate(0, [3, 4]) == (0, 0)


class TestSectorAmount:
    def test_single_step_rounding(self) -> None:
        # 1 亿持仓、指数 1001→1050：单步 489510490（对抗用例 #19）。
        assert sector_amount_units(10_000_000_000, 1001, 1050) == 489_510_490

    def test_negative_return(self) -> None:
        assert sector_amount_units(570_000, 1000, 990) == -5_700

    def test_invalid_index_start(self) -> None:
        assert sector_amount_units(100, 0, 10) is None
        assert sector_amount_units(100, -1, 10) is None

    def test_sector_return_units(self) -> None:
        assert sector_return_units(1000, 1122) == 12_200_000  # +12.2% (1e-8)
        assert sector_return_units(0, 10) is None


class TestRanking:
    def test_rank_by_abs_desc_with_fixed_tiebreak(self) -> None:
        factors = {
            FactorCode.SELECTION: 100,
            FactorCode.SECTOR: -500,
            FactorCode.BUY_TIMING: 500,
            FactorCode.SELL_TIMING: 0,
            FactorCode.FEE: -10,
        }
        ranked = rank_factors(factors)
        # |500| 并列：SECTOR < BUY_TIMING（固定因子序）
        assert ranked == (
            FactorCode.SECTOR,
            FactorCode.BUY_TIMING,
            FactorCode.SELECTION,
            FactorCode.FEE,
            FactorCode.SELL_TIMING,
        )

    def test_ratio_of_allows_negative_denominator(self) -> None:
        assert ratio_of(-50, -100) == 50_000_000
        assert ratio_of(50, -100) == -50_000_000
        assert ratio_of(50, 0) is None
        assert ratio_of(1, 3) == 33_333_333  # half-up

    def test_share_of(self) -> None:
        assert share_of(30, 100) == 30_000_000
        assert share_of(-70, 100) == 70_000_000
        assert share_of(10, 0) is None

    def test_ratio_na_reason_threshold(self) -> None:
        # |total_pnl| < max(100, 0.5%×Σ|amount|)
        assert ratio_na_reason(99, 0) == "PNL_NEAR_ZERO"
        assert ratio_na_reason(100, 0) is None
        assert ratio_na_reason(300, 100_000) == "PNL_NEAR_ZERO"  # 0.5%×1e5=500
        assert ratio_na_reason(500, 100_000) is None
        assert ratio_na_reason(-99, 10) == "PNL_NEAR_ZERO"


class TestShares:
    def test_sum_exact_1e8(self) -> None:
        # 三因子均分 1/3：逐项 half-up 会差 1，shares_of 补差给 |amount| 最大者
        factors = {
            FactorCode.SELECTION: 100,
            FactorCode.SECTOR: 100,
            FactorCode.BUY_TIMING: 100,
            FactorCode.SELL_TIMING: 0,
            FactorCode.FEE: 0,
        }
        shares = shares_of(factors)
        assert sum(shares.values()) == 100_000_000  # type: ignore[arg-type]
        # SELECTION 排固定序首位 → 尾差归它
        assert shares[FactorCode.SELECTION] == 33_333_334

    def test_all_zero_returns_none(self) -> None:
        factors = dict.fromkeys(FACTOR_CODES, 0)
        assert all(share is None for share in shares_of(factors).values())

    def test_single_dominant(self) -> None:
        factors = {code: (1000 if code == FactorCode.FEE else 1) for code in FACTOR_CODES}
        shares = shares_of(factors)
        assert sum(shares.values()) == 100_000_000  # type: ignore[arg-type]
        assert shares[FactorCode.FEE] is not None and shares[FactorCode.FEE] > 99_000_000


@st.composite
def _segment_batches(draw: st.DrawFn) -> tuple[list[SegmentInput], int]:
    n = draw(st.integers(min_value=1, max_value=8))
    segments: list[SegmentInput] = []
    for _ in range(n):
        attributed = draw(st.booleans())
        base = draw(st.integers(min_value=0, max_value=50_000_000))
        path = draw(st.integers(min_value=-5_000_000, max_value=5_000_000))
        fee = draw(st.integers(min_value=0, max_value=10_000))
        dividend = draw(st.integers(min_value=0, max_value=20_000))
        gross_buy = (
            draw(st.integers(min_value=0, max_value=50_000_000)) if draw(st.booleans()) else None
        )
        segments.append(
            SegmentInput(
                security_code="600000",
                kind="closed",
                quantity_units=draw(st.integers(min_value=1, max_value=1_000_000)),
                base_units=base,
                end_value_units=base + path,
                gross_buy_units=gross_buy,
                gross_sell_units=base + path if gross_buy is not None else None,
                fee_units=fee,
                sector_amount_units=draw(st.integers(min_value=-1_000_000, max_value=1_000_000))
                if attributed
                else None,
                dividend_units=dividend if attributed else 0,
            )
        )
    total_pnl = draw(st.integers(min_value=-100_000_000, max_value=100_000_000))
    return segments, total_pnl


class TestProperties:
    """属性不变式（设计 §7.2 ①②④⑤ 的 core 部分）。"""

    @settings(
        max_examples=300,
        # mutmut 的 stats/clean-tests/变异体三阶段各自独立进程执行同一用例，
        # hypothesis 的 differing_executors 会误判为「跨执行器复用」而中止整个变异跑。
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.differing_executors],
        deadline=None,
    )
    @given(_segment_batches())
    def test_identity_and_rank_continuity(self, case: tuple[list[SegmentInput], int]) -> None:
        segments, total_pnl = case
        result = decompose(segments, total_pnl)
        # ② Σfactors + residual == total_pnl（整数精确）
        assert sum(result.factors.values()) + result.residual_units == total_pnl
        # ① 段级恒等自检（attributed 段：因子和 == 终点锚 − 起点锚 + D − F）
        for seg in segments:
            if seg.attributed:
                factors = decompose_segment(seg)
                total = (
                    factors.selection_units
                    + factors.sector_units
                    + factors.buy_timing_units
                    + factors.sell_timing_units
                    - factors.fee_units
                )
                assert total == _segment_identity_total(seg)
        # ⑤ rank 连续（rank_factors 返回全部 5 因子的一个排列）
        ranked = rank_factors(result.factors)
        assert sorted(ranked, key=lambda code: FACTOR_CODES.index(code)) == list(FACTOR_CODES)
        # ④ Σshare == 1e8（shares_of 精确补差；Σ|amount| = 0 → 全 None）
        shares = shares_of(result.factors)
        if total_abs_amounts(result.factors) > 0:
            assert sum(shares.values()) == 100_000_000  # type: ignore[arg-type]
        else:
            assert all(share is None for share in shares.values())
