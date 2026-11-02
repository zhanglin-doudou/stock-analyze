"""装配层黄金向量（设计 §3.8 手算向量 fx_attr_mixed 核心链；08c 验收）。

纯函数链（无 DB / 无时钟）：`resolve_asset_window` → `build_symbol_plans`
→ `decompose` → `split_residual` → `build_coverage` → ranking/presentation。
断言分层（§3.8）：受控 fixture 下五因子逐分等于手算值 + `residual==0`
（真正的 golden）；残差桶与 coverage 独立复算。

手算向量（§3.8，金额分）：区间 2025-03，T0=03-01（周六）、T1=03-31（周一）；
P0=02-28 收盘 9.50；600000/行业 S1。

| 段 | q | base | 板块 | 买点 | 卖点 | 费用 | 股息 | 选股 |
|---|---|---|---|---|---|---|---|---|
| A_sell（区间前 lot 全卖） | 600 | 5700.00 | −57.00 | 0 | 120.00 | 6.60 | 120.00 | 1077.00 |
| B_sell（新 lot 部分卖） | 200 | 2420.00 | 121.00 | 20.00 | 0 | 3.80 | 0 | 59.00 |
| B_hold（新 lot 持有） | 200 | 2420.00 | 242.00 | 20.00 | 0 | 1.20 | 0 | 138.00 |

合计：selection=1274.00 / sector=306.00 / buy=40.00 / sell=120.00 / fee=−11.60，
Σ=1728.40=total_pnl（V_end 7428.40 − V_begin 5700.00），`residual==0`。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest
from stock_core.attribution import FACTOR_CODES, FactorCode, decompose
from stock_core.attribution.ranking import (
    rank_factors,
    ratio_na_reason,
    ratio_of,
    shares_of,
    total_abs_amounts,
)
from stock_core.fifo import build_rounds
from stock_ledger.models import Trade
from stock_review.application.attribution import _mirror_view
from stock_review.application.rebuild import trades_to_events
from stock_review.attribution.context import (
    BEGIN_SOURCE_STATEMENT,
    resolve_asset_window,
)
from stock_review.attribution.coverage import MIN_SAMPLE_SEGMENTS, build_coverage
from stock_review.attribution.presentation import (
    assert_copy_compliance,
    build_summary,
    factor_conclusion,
)
from stock_review.attribution.residual import (
    BUCKET_CORPORATE,
    BUCKET_DIVIDEND,
    BUCKET_INTEREST,
    BUCKET_OTHER,
    BUCKET_UNATTRIBUTED,
    split_residual,
)
from stock_review.attribution.segments import (
    REASON_MAPPING,
    build_symbol_plans,
)
from stock_review.models import AssetDaily

USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000f1")

T0 = date(2025, 3, 1)  # 周六
T1 = date(2025, 3, 31)  # 周一
P0_DATE = date(2025, 2, 28)  # 周五（T0 前最后工作日）

# 600000 日线（scale=6）：§3.8 场景收盘序列。
LADDER: dict[str, list[tuple[date, int]]] = {
    "600000": [
        (date(2025, 2, 28), 9_500_000),  # 9.50
        (date(2025, 3, 5), 12_100_000),  # 12.10（bB 建仓日收盘）
        (date(2025, 3, 20), 11_000_000),  # 11.00（sA 卖出日收盘）
        (date(2025, 3, 25), 13_000_000),  # 13.00（sB 卖出日收盘）
        (date(2025, 3, 31), 14_000_000),  # 14.00（P1）
    ]
}

# 行业指数 S1（点位 scale=6）：02-28=1000 / 03-05=1020 / 03-20=990 / 03-25=1071 / 03-31=1122。
SECTOR_LADDER: dict[str, list[tuple[date, int]]] = {
    "S1": [
        (P0_DATE, 1_000_000_000),
        (date(2025, 3, 5), 1_020_000_000),
        (date(2025, 3, 20), 990_000_000),
        (date(2025, 3, 25), 1_071_000_000),
        (date(2025, 3, 31), 1_122_000_000),
    ]
}

MEMBERSHIPS = {"600000": "S1"}


def _trade(**overrides: object) -> Trade:
    """未落库 Trade（买入 amount 为负、卖出为正的现金流符号，见 test_mapping）。"""
    values: dict[str, object] = {
        "id": "trd_" + uuid.uuid4().hex[:12],
        "user_id": USER_ID,
        "trade_date": date(2025, 3, 5),
        "security_code": "600000",
        "security_name": "浦发银行",
        "side": "buy",
        "quantity_units": 0,
        "price_units": 0,
        "amount_units": 0,
        "commission_units": 0,
        "stamp_tax_units": 0,
        "other_fee_units": 0,
        "summary": "",
        "category": "buy",
        "line_no": 1,
        "dedupe_key": "dk",
    }
    values.update(overrides)
    return Trade(**values)


def _golden_trades() -> list[Trade]:
    """§3.8 场景五条流水（bA 区间前 / bB / d1 / sA / sB）。"""
    return [
        _trade(
            trade_date=date(2025, 1, 10),
            side="buy",
            category="buy",
            quantity_units=6_000_000,  # 600 股
            price_units=10_000_000,  # 10.00
            amount_units=-600_300,  # −6003.00（含买费 3.00）
            commission_units=300,
            summary="证券买入",
        ),
        _trade(
            trade_date=date(2025, 3, 5),
            side="buy",
            category="buy",
            quantity_units=4_000_000,  # 400 股
            price_units=12_000_000,  # 12.00
            amount_units=-480_240,  # −4802.40（含买费 2.40）
            commission_units=240,
            summary="证券买入",
        ),
        _trade(
            trade_date=date(2025, 3, 14),
            side="",
            category="dividend",
            amount_units=12_000,  # +120.00 股息（税后）
            summary="红利入账",
        ),
        _trade(
            trade_date=date(2025, 3, 20),
            side="sell",
            category="sell",
            quantity_units=6_000_000,  # 600 股
            price_units=11_200_000,  # 11.20
            amount_units=671_340,  # +6713.40（净卖出，费 6.60）
            commission_units=660,
            summary="证券卖出",
        ),
        _trade(
            trade_date=date(2025, 3, 25),
            side="sell",
            category="sell",
            quantity_units=2_000_000,  # 200 股
            price_units=13_000_000,  # 13.00
            amount_units=259_740,  # +2597.40（净卖出，费 2.60）
            commission_units=260,
            summary="证券卖出",
        ),
    ]


def _asset_row(
    trade_date: date, *, total: int, cash: int, market_value: int, status: str = "ok"
) -> AssetDaily:
    return AssetDaily(
        id=f"ast_{trade_date.isoformat()}",
        user_id=USER_ID,
        trade_date=trade_date,
        cash_units=cash,
        money_fund_units=0,
        market_value_units=market_value,
        total_assets_units=total,
        holding_cost_units=0,
        unrealized_pnl_units=0,
        holding_count=1,
        cashflow_units=0,
        prev_total_assets_units=None,
        daily_pnl_units=None,
        daily_return_units=None,
        status=status,
        status_reason=None,
        remark=None,
        definition_version="1.7",
        rebuilt_at=datetime(2025, 4, 1, 8, 0, tzinfo=UTC),
    )


def _build_bundle(trades: list[Trade], *, memberships: dict[str, str]):  # type: ignore[no-untyped-def]
    """两次 FIFO 重放 + build_symbol_plans（与应用层编排同参）。"""
    res_all = build_rounds(trades_to_events([t for t in trades if t.trade_date <= T1]))
    res_pre = build_rounds(trades_to_events([t for t in trades if t.trade_date < T0]))
    return build_symbol_plans(
        trades=trades,
        t0=T0,
        t1=T1,
        res_all=res_all,
        res_pre=res_pre,
        ladder=LADDER,
        memberships=memberships,
        sector_ladder=SECTOR_LADDER,
        corporate={},
        canonical=lambda c: c,
        expected_p0_date=P0_DATE,
        expected_p1_date=T1,
        dividends={"600000": 12_000},
        fallback_anchors={},
    )


def test_golden_asset_window_mtm() -> None:
    """资产窗口（D-01 MTM）：V_begin=5700.00、V_end=7428.40、CF=0、total_pnl=1728.40。"""
    assets = [
        _asset_row(P0_DATE, total=570_000, cash=0, market_value=570_000),
        _asset_row(T1, total=742_840, cash=462_840, market_value=280_000),
    ]
    window = resolve_asset_window(assets, t0=T0, t1=T1, data_as_of=T1, valuation_end_date=T1)
    assert window.status == "ok"
    assert window.missing == ()
    assert window.v_begin_units == 570_000
    assert window.v_end_units == 742_840
    assert window.cf_period_units == 0
    assert window.total_pnl_units == 172_840
    assert window.begin_date == P0_DATE
    assert window.end_date == T1
    assert window.beginning_value_source == BEGIN_SOURCE_STATEMENT


def test_mirror_view_uses_asset_basis_and_window_snapshots() -> None:
    """镜像 = 资产表比例成本口径：期初 600 股 / 期末 200 股，已实现 666.48 元（P0-A）。"""
    mirror = _mirror_view(
        _golden_trades(),
        begin_date=P0_DATE,
        end_date=T1,
        ladder=LADDER,
        canonical=lambda code: code,
        bond_divisors={},
        new_stocks=set(),
        begin_balances={},
    )
    assert mirror.holdings["600000"] == (6_000_000, 2_000_000)
    # 两笔卖出净额 6713.40 / 2597.40，比例结转成本 6483.24 / 2161.08 → 230.16 / 436.32。
    assert mirror.realized_units == 23_016 + 43_632


def test_golden_segments_and_five_factors() -> None:
    """§3.8 黄金向量：段 q/base/因子逐分等于手算；Σfactors + residual == total_pnl。"""
    trades = _golden_trades()
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)

    assert len(bundle.plans) == 1
    plan = bundle.plans[0]
    assert plan.security_code == "600000"
    assert plan.symbol_class == "stock"
    assert plan.attributed and plan.reason_code is None
    assert bundle.has_candidate is True
    assert not plan.price_fallback and not plan.sector_fallback

    # 段结构：A_sell(closed 600) / B_sell(closed 200) / B_hold(open 200)。
    seg_a, seg_b_sell, seg_b_hold = plan.segments
    assert (seg_a.kind, seg_a.quantity_units, seg_a.base_units) == ("closed", 6_000_000, 570_000)
    assert (seg_b_sell.kind, seg_b_sell.quantity_units, seg_b_sell.base_units) == (
        "closed",
        2_000_000,
        242_000,
    )
    assert (seg_b_hold.kind, seg_b_hold.quantity_units, seg_b_hold.base_units) == (
        "open",
        2_000_000,
        242_000,
    )
    # 段终点市值：A=600×11.00、B_sell=200×13.00、B_hold=200×14.00。
    assert (seg_a.end_value_units, seg_b_sell.end_value_units, seg_b_hold.end_value_units) == (
        660_000,
        260_000,
        280_000,
    )
    # 股息按段 base 分摊（§5 步骤 6 冻结口径），Σ = 标的净股息 120.00。
    dividend_shares = [seg.dividend_units for seg in plan.segments]
    assert sum(dividend_shares) == 12_000
    assert dividend_shares[0] == max(dividend_shares)  # A 段 base 最大 → 份额最大

    decomposition = decompose(plan.segments, 172_840, extra_fee_units=0)
    expected = {
        FactorCode.SELECTION: 127_400,
        FactorCode.SECTOR: 30_600,
        FactorCode.BUY_TIMING: 4_000,
        FactorCode.SELL_TIMING: 12_000,
        FactorCode.FEE: -1_160,
    }
    assert dict(decomposition.factors) == expected
    assert decomposition.residual_units == 0
    assert decomposition.n_segments == 3
    assert decomposition.n_attributed_segments == 3
    assert decomposition.base_capital_units == 1_054_000
    assert decomposition.base_capital_attributed_units == 1_054_000


def test_golden_ratio_share_rank_oracle() -> None:
    """ratio/share（Σ==1e8 精确）与 rank 序；selection ratio 用 Decimal 独立复算。"""
    trades = _golden_trades()
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)
    decomposition = decompose(bundle.plans[0].segments, 172_840, extra_fee_units=0)
    factors = decomposition.factors

    total_abs = total_abs_amounts(factors)
    assert total_abs == 127_400 + 30_600 + 4_000 + 12_000 + 1_160
    assert ratio_na_reason(172_840, total_abs) is None

    ranked = rank_factors(factors)
    assert ranked == (
        FactorCode.SELECTION,
        FactorCode.SECTOR,
        FactorCode.SELL_TIMING,
        FactorCode.BUY_TIMING,
        FactorCode.FEE,
    )

    ratios = {code: ratio_of(factors[code], 172_840) for code in FACTOR_CODES}
    # 独立 oracle：逐项 Decimal half-up（不依赖 div_round 实现）。
    for code, amount in factors.items():
        expected = int(
            (Decimal(amount) / Decimal(172_840) * Decimal(100_000_000)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        assert ratios[code] == expected
    # 逐项 half-up 的 Σ 尾差有界（≤ 项数）；application 层以 residual_ratio 为 plug 吸收，
    # 保证响应 Σratio + residual_ratio == 1e8 精确（§7.2 ③）。
    assert abs(100_000_000 - sum(v for v in ratios.values() if v is not None)) <= len(FACTOR_CODES)

    shares = shares_of(factors)
    assert shares is not None
    assert sum(v for v in shares.values() if v is not None) == 100_000_000


def test_golden_residual_buckets_and_coverage() -> None:
    """残差六桶全 0（无剔除标的）、valuation_gap 独立复算 = 0；coverage 全归因。"""
    trades = _golden_trades()
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)
    plan = bundle.plans[0]
    decomposition = decompose(plan.segments, 172_840, extra_fee_units=0)

    split = split_residual(
        plans=bundle.plans,
        trades=trades,
        t0=T0,
        t1=T1,
        begin_date=P0_DATE,
        end_date=T1,
        bucket_mv={"600000": (570_000, 280_000)},
        money_fund_balances={},
        holdings_at={"600000": (6_000_000, 2_000_000)},
        corporate={},
        canonical=lambda c: c,
    )
    assert split.buckets == {
        BUCKET_DIVIDEND: 0,
        BUCKET_INTEREST: 0,
        BUCKET_CORPORATE: 0,
        BUCKET_UNATTRIBUTED: 0,
        BUCKET_OTHER: 0,
    }
    assert split.fee_bucketed_units == 0 and split.fee_indep_units == 0
    assert split.per_symbol == {} and split.unvalued_symbols == []
    # valuation_gap = residual − Σ桶 = 0（独立复算：ΔMV + Σamount == total_pnl）。
    delta_mv = 280_000 - 570_000
    rows_amount = sum(t.amount_units for t in trades if T0 <= t.trade_date <= T1)
    assert delta_mv + rows_amount == 172_840
    gap = decomposition.residual_units - sum(split.buckets.values())
    assert gap == 0

    coverage = build_coverage(
        plans=bundle.plans,
        decomposition=decomposition,
        per_symbol=split.per_symbol,
        base_basis="market",
    )
    assert coverage.capital_total_units == 1_054_000
    assert coverage.capital_attributed_units == 1_054_000
    assert coverage.capital_ratio_units == 100_000_000
    assert coverage.base_basis == "market"
    assert (coverage.segments_total, coverage.segments_attributed) == (3, 3)
    assert (coverage.symbols_total, coverage.symbols_attributed) == (1, 1)
    assert coverage.unattributed == ()


def test_golden_summary_insufficient_sample() -> None:
    """3 段 < 门禁 10 段 → 只给事实句；文案过裸术语/建议门禁。"""
    trades = _golden_trades()
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)
    decomposition = decompose(bundle.plans[0].segments, 172_840, extra_fee_units=0)
    ranked = rank_factors(decomposition.factors)
    summary = build_summary(
        total_pnl_units=172_840,
        ranked=[(str(code), decomposition.factors[code], None) for code in ranked],
        residual_units=0,
        n_attributed_segments=3,
        capital_total_units=1_054_000,
        capital_ratio_units=100_000_000,
        valuation_gap_units=0,
        has_trade_rows=True,
    )
    assert summary == "本期可归因交易样本不足（3 段），暂不生成结论。"
    assert MIN_SAMPLE_SEGMENTS > 3
    conclusions = [factor_conclusion(decomposition.factors[code]) for code in ranked]
    assert conclusions[0] == "贡献 1,274.00 元"
    assert conclusions[-1] == "拖累 11.60 元"
    assert_copy_compliance(summary, *conclusions)


def test_mapping_missing_symbol_is_candidate_and_excluded() -> None:
    """行业映射缺失：整标的剔除（SECTOR_MAPPING_MISSING）且仍是候选（§4.4 blocked 输入）。"""
    trades = _golden_trades()
    bundle = _build_bundle(trades, memberships={})
    assert len(bundle.plans) == 1
    plan = bundle.plans[0]
    assert not plan.attributed
    assert plan.reason_code == REASON_MAPPING
    assert plan.segments == ()
    assert plan.bucket_kind == "unattributed_price_pnl"
    # 修复回归：stock 类因缺口剔除仍是候选（零可归因段 → blocked 而非 ok）。
    assert bundle.has_candidate is True
    assert bundle.excluded_by_reason == {REASON_MAPPING: ["600000"]}


def test_p0_blocked_row_blocks_window() -> None:
    """期初锚行 blocked → 整窗 blocked（不把 0 当 V_begin；fx_attr_p0_blocked）。"""
    assets = [
        _asset_row(P0_DATE, total=0, cash=0, market_value=0, status="blocked"),
        _asset_row(T1, total=742_840, cash=462_840, market_value=280_000),
    ]
    window = resolve_asset_window(assets, t0=T0, t1=T1, data_as_of=T1, valuation_end_date=T1)
    assert window.status == "blocked"
    assert window.v_begin_units is None and window.total_pnl_units is None
    assert [m.code for m in window.missing] == ["ASSET_ROW_BLOCKED"]


# ── 08e 结论文案（§5.5 模板/阈值/边界）────────────────────────


def _summary(
    *,
    total_pnl_units: int = -320_000,
    ranked: tuple[tuple[str, int, int | None], ...] = (
        ("买点", -190_000, 59_400_000),
        ("费用", -41_000, None),
    ),
    residual_units: int = 12_000,
    n_attributed_segments: int = 12,
    capital_total_units: int = 1_054_000,
    capital_ratio_units: int | None = 100_000_000,
    valuation_gap_units: int = 0,
    has_trade_rows: bool = True,
    lang: str = "zh",
) -> str | None:
    return build_summary(
        total_pnl_units=total_pnl_units,
        ranked=ranked,
        residual_units=residual_units,
        n_attributed_segments=n_attributed_segments,
        capital_total_units=capital_total_units,
        capital_ratio_units=capital_ratio_units,
        valuation_gap_units=valuation_gap_units,
        has_trade_rows=has_trade_rows,
        lang=lang,
    )


def test_summary_dominant_factor_and_residual_zh() -> None:
    """§5.5 排序句：rank=1 且 share≥0.20 点名 + 次因子 + 未归属（负 total 措辞）。"""
    summary = _summary()
    assert summary == (
        "本期亏损 3,200.00 元，主要来自「买点」（-1,900.00 元，占构成 59.4%）；"
        "其次为「费用」（-410.00 元）。未归属 120.00 元，见残差明细。"
    )
    assert_copy_compliance(summary)


def test_summary_dispersed_when_share_below_threshold() -> None:
    """share < 0.20 → 不点名因子，用"贡献分散"。"""
    summary = _summary(ranked=(("买点", -100_000, 19_000_000), ("费用", -90_000, None)))
    assert summary is not None and "，贡献分散" in summary
    assert "买点" not in summary


def test_summary_gates_return_none() -> None:
    """D-09 门禁：capital_ratio<0.90 或 gap_ratio>5% → 不出结论（None）。"""
    assert _summary(capital_ratio_units=89_000_000) is None
    # gap 32000 分 / |total|=320000 分 = 10% > 5%
    assert _summary(valuation_gap_units=32_000) is None


def test_summary_zero_capital_fixed_sentences() -> None:
    """capital_total==0 → 固定句（空账户 / 无可归因 A 股，中英两版）。"""
    cases = (
        ("zh", "本期无交易/无持仓变化。", "本期无可归因 A 股。"),
        (
            "en",
            "No trades or position changes this period.",
            "No attributable A-share holdings this period.",
        ),
    )
    for lang, no_rows, a_share in cases:
        assert _summary(capital_total_units=0, has_trade_rows=False, lang=lang) == no_rows
        assert _summary(capital_total_units=0, has_trade_rows=True, lang=lang) == a_share


def test_summary_english_switches_copy_not_numbers() -> None:
    """`Accept-Language` 只换文案不换数字（§5.5）。"""
    zh = _summary()
    en = _summary(lang="en")
    assert en is not None and zh is not None
    assert en == (
        'Lost 3,200.00 CNY this period, mainly from "买点" (-1,900.00 CNY, 59.4% of composition); '
        'next was "费用" (-410.00 CNY). 120.00 CNY unattributed; see residual breakdown.'
    )
    # 数字串（金额/百分比）完全一致。
    for number in ("3,200.00", "-1,900.00", "59.4%", "-410.00", "120.00"):
        assert number in zh and number in en
    assert_copy_compliance(zh, en)


def test_factor_conclusion_bilingual_and_minor() -> None:
    assert factor_conclusion(127_400) == "贡献 1,274.00 元"
    assert factor_conclusion(-1_160) == "拖累 11.60 元"
    assert factor_conclusion(0) == "影响较小"
    assert factor_conclusion(127_400, lang="en") == "Added 1,274.00 CNY"
    assert factor_conclusion(-1_160, lang="en") == "Detracted 11.60 CNY"
    assert factor_conclusion(0, lang="en") == "Minor impact"
    assert_copy_compliance(
        factor_conclusion(127_400),
        factor_conclusion(-1_160),
        factor_conclusion(0),
        factor_conclusion(127_400, lang="en"),
    )


def test_copy_compliance_rejects_jargon_and_advice() -> None:
    """词表/建议门禁必须能拦住违规文案（防模板回归）。"""
    with pytest.raises(ValueError, match="裸术语"):
        assert_copy_compliance("本期 BHAR 贡献 1 元")
    with pytest.raises(ValueError, match="买卖建议"):
        assert_copy_compliance("建议买入 600519")
