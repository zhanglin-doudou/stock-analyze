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
from stock_review.application.attribution import (
    _aggregate_status,
    _display_name,
    _exclusion_missing,
    _mirror_view,
    _residual_missing,
    _residual_reconciliation,
    _sector_start_fallback_missing,
    _valuation_gap_missing,
)
from stock_review.application.rebuild import trades_to_events
from stock_review.attribution.context import (
    BEGIN_SOURCE_STATEMENT,
    MissingEntry,
    resolve_asset_window,
)
from stock_review.attribution.coverage import (
    MIN_SAMPLE_SEGMENTS,
    UNATTRIBUTED_LIMIT,
    Coverage,
    build_coverage,
)
from stock_review.attribution.presentation import (
    GAP_RATIO_MAX,
    assert_copy_compliance,
    build_summary,
    factor_conclusion,
    gap_ratio_units,
)
from stock_review.attribution.residual import (
    BUCKET_CORPORATE,
    BUCKET_DIVIDEND,
    BUCKET_INTEREST,
    BUCKET_IPO,
    BUCKET_OTHER,
    BUCKET_UNATTRIBUTED,
    ResidualSplit,
    split_residual,
)
from stock_review.attribution.segments import (
    REASON_ASSET_CLASS,
    REASON_BAR,
    REASON_CORPORATE,
    REASON_MAPPING,
    REASON_PRICE,
    SymbolPlan,
    _first_entry_within,
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


def _build_bundle(
    trades: list[Trade],
    *,
    memberships: dict[str, str],
    ladder: dict[str, list[tuple[date, int]]] | None = None,
    sector_ladder: dict[str, list[tuple[date, int]]] | None = None,
    corporate: dict[str, str] | None = None,
):  # type: ignore[no-untyped-def]
    """两次 FIFO 重放 + build_symbol_plans（与应用层编排同参）。"""
    res_all = build_rounds(trades_to_events([t for t in trades if t.trade_date <= T1]))
    res_pre = build_rounds(trades_to_events([t for t in trades if t.trade_date < T0]))
    return build_symbol_plans(
        trades=trades,
        t0=T0,
        t1=T1,
        res_all=res_all,
        res_pre=res_pre,
        ladder=LADDER if ladder is None else ladder,
        memberships=memberships,
        sector_ladder=SECTOR_LADDER if sector_ladder is None else sector_ladder,
        corporate=corporate or {},
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
        BUCKET_IPO: 0,
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
    assert (
        coverage.symbols_total,
        coverage.symbols_participating,
        coverage.symbols_attributed,
    ) == (
        1,
        1,
        1,
    )
    assert coverage.unattributed == ()


def test_split_routes_new_stock_to_ipo_bucket() -> None:
    """`new_stock` 标的的本期盯市损益进 `ipo_pnl`（账户级），不进 `unattributed_price_pnl`；
    破发（负值）同样只进 ipo_pnl；其余非归因标的仍留在 unattributed。
    """
    plans = [
        SymbolPlan(
            security_code="001237",
            symbol_class="new_stock",
            attributed=False,
            reason_code=None,
            participating=True,
        ),
        SymbolPlan(
            security_code="110059",
            symbol_class="bond",
            attributed=False,
            reason_code=None,
            participating=True,
        ),
    ]
    trades = [
        _trade(
            trade_date=date(2025, 3, 20),
            security_code="001237",
            side="sell",
            category="sell",
            quantity_units=1_000_000,  # 100 股
            price_units=40_000_000,  # 40.00
            amount_units=400_000,  # +4,000.00
        ),
        _trade(
            trade_date=date(2025, 3, 25),
            security_code="110059",
            side="sell",
            category="sell",
            quantity_units=100_000,
            price_units=120_000_000,
            amount_units=1_200_000,  # +12,000.00
        ),
    ]
    split = split_residual(
        plans=plans,
        trades=trades,
        t0=T0,
        t1=T1,
        begin_date=P0_DATE,
        end_date=T1,
        # 001237：期初 9,000.00 → 期末 0，卖出 +4,000.00 ⇒ −5,000.00（破发）
        bucket_mv={"001237": (900_000, 0), "110059": (0, 0)},
        money_fund_balances={},
        holdings_at={"001237": (100_000, 0), "110059": (100_000, 0)},
        corporate={},
        canonical=lambda c: c,
    )
    assert split.buckets[BUCKET_IPO] == -500_000
    assert split.buckets[BUCKET_UNATTRIBUTED] == 1_200_000
    # 账户级桶不落 `per_symbol`（避免与 `account_level_total` 双计）。
    assert split.per_symbol == {"110059": 1_200_000}

    # 一致口径：`new_stock` 标的由 `ipo_pnl` 桶披露，不再进 `unattributed[]` 明细。
    coverage = build_coverage(
        plans=plans,
        decomposition=decompose([], 0, extra_fee_units=0),
        per_symbol=split.per_symbol,
        base_basis="market",
    )
    assert [entry.security_code for entry in coverage.unattributed] == ["110059"]
    assert coverage.unattributed[0].amount_units == 1_200_000


def test_split_ipo_bucket_is_zero_without_new_stock() -> None:
    """无 `new_stock` 标的时新桶为 0，不产生额外条目/金额（沿用缺省 0）。"""
    plans = [
        SymbolPlan(
            security_code="110059",
            symbol_class="bond",
            attributed=False,
            reason_code=None,
            participating=True,
        )
    ]
    split = split_residual(
        plans=plans,
        trades=[],
        t0=T0,
        t1=T1,
        begin_date=P0_DATE,
        end_date=T1,
        bucket_mv={"110059": (0, 0)},
        money_fund_balances={},
        holdings_at={"110059": (100_000, 0)},
        corporate={},
        canonical=lambda c: c,
    )
    assert split.buckets[BUCKET_IPO] == 0
    # 桶值 0 的标的仍出现在 per_symbol（金额 0），不产生新桶金额。
    assert split.per_symbol == {"110059": 0}


def test_coverage_unattributed_only_participating_with_names() -> None:
    """明细只披露本期参与标的（非参与的历史候选被过滤），并带代码对应名称。"""
    plans = [
        SymbolPlan(
            security_code="600000",
            symbol_class="stock",
            attributed=False,
            reason_code=REASON_MAPPING,
            participating=True,
        ),
        # 历史清仓/退市：本期无持仓、无窗口流水 → 不进明细（金额 0）。
        SymbolPlan(
            security_code="000009",
            symbol_class="stock",
            attributed=False,
            reason_code=None,
            participating=False,
        ),
        SymbolPlan(
            security_code="110059",
            symbol_class="bond",
            attributed=False,
            reason_code=None,
            participating=True,
        ),
    ]
    decomposition = decompose([], 0, extra_fee_units=0)
    coverage = build_coverage(
        plans=plans,
        decomposition=decomposition,
        per_symbol={"600000": -50_000, "110059": 120_000, "511990": 3_000},
        base_basis="market",
        names={
            "600000": "浦发银行",
            "110059": "建工转债",
            "511990": "华宝添益",
            "000009": "中国宝安",
        },
    )
    # 按 |amount| 降序；计划外但在窗口内有余额的 511990 以 pseudo 披露。
    assert [entry.security_code for entry in coverage.unattributed] == [
        "110059",
        "600000",
        "511990",
    ]
    assert [entry.security_name for entry in coverage.unattributed] == [
        "建工转债",
        "浦发银行",
        "华宝添益",
    ]
    assert coverage.unattributed[2].symbol_class == "pseudo"


def test_display_name_collapses_whitespace() -> None:
    """账单名称可能含换行/多空白 → 压成单空格；空串/None → None（前端只显示代码）。"""
    assert _display_name("华泰紫金天\n天发货币") == "华泰紫金天 天发货币"
    assert _display_name("   ") is None
    assert _display_name(None) is None


def test_coverage_unattributed_name_falls_back_to_none() -> None:
    """名称缺失 → None（前端只展示代码，不做占位错配）。"""
    plans = [
        SymbolPlan(
            security_code="600000",
            symbol_class="stock",
            attributed=False,
            reason_code=REASON_PRICE,
            participating=True,
        )
    ]
    decomposition = decompose([], 0, extra_fee_units=0)
    coverage = build_coverage(
        plans=plans,
        decomposition=decomposition,
        per_symbol={"600000": 0},
        base_basis="market",
        names={},
    )
    (entry,) = coverage.unattributed
    assert entry.security_name is None


@pytest.mark.parametrize(
    ("per_symbol", "bucket_values", "gap_units", "residual_units", "expect_account"),
    [
        # 2026-06 实测：标的 -5,712.00 + 账户级（interest 1.43）= -5,710.57
        (
            {"001237": -571_200},
            {BUCKET_UNATTRIBUTED: -571_200, BUCKET_INTEREST: 143},
            0,
            -571_057,
            143,
        ),
        # 2026-07：全 0
        ({}, {}, 0, 0, 0),
        # valuation_gap 非零 → 由账户级吸收
        ({}, {}, -12_345, -12_345, -12_345),
        # 明细为空但账户级有股息
        ({}, {BUCKET_DIVIDEND: 8_800}, 0, 8_800, 8_800),
        # 打新收益（破发为负）只进账户级：标的 120.00 + 账户级（-3.00 + 1.43）
        (
            {"110059": 120_000},
            {BUCKET_UNATTRIBUTED: 120_000, BUCKET_IPO: -300, BUCKET_INTEREST: 143},
            0,
            119_843,
            -157,
        ),
    ],
)
def test_residual_reconciliation_identity(
    per_symbol: dict[str, int],
    bucket_values: dict[str, int],
    gap_units: int,
    residual_units: int,
    expect_account: int,
) -> None:
    """对账等式严格成立（分）：标的合计 + 账户级桶合计 == residual。"""
    buckets = {
        BUCKET_CORPORATE: 0,
        BUCKET_UNATTRIBUTED: 0,
        BUCKET_DIVIDEND: 0,
        BUCKET_INTEREST: 0,
        BUCKET_IPO: 0,
        BUCKET_OTHER: 0,
        **bucket_values,
    }
    split = ResidualSplit(
        buckets=buckets, fee_bucketed_units=0, fee_indep_units=0, per_symbol=dict(per_symbol)
    )
    reconciliation = _residual_reconciliation(
        split=split,
        coverage=_coverage_for(per_symbol),
        gap_units=gap_units,
        residual_units=residual_units,
    )
    assert reconciliation.unattributed_symbols_total_units == sum(per_symbol.values())
    assert reconciliation.account_level_total_units == expect_account
    assert (
        reconciliation.unattributed_symbols_total_units + reconciliation.account_level_total_units
        == reconciliation.residual_units
    )
    # 展示切分是 additive 的：前 5 条 + 其余 = 标的合计（不受截断影响）。
    assert (
        reconciliation.listed_units + reconciliation.others_units
        == reconciliation.unattributed_symbols_total_units
    )
    assert reconciliation.display_limit == UNATTRIBUTED_LIMIT


def _coverage_for(per_symbol: dict[str, int]) -> Coverage:
    """仅用计划外（pseudo）标的构造 coverage：金额降序切分与展示上限由此覆盖。"""
    return build_coverage(
        plans=[],
        decomposition=decompose([], 0, extra_fee_units=0),
        per_symbol=per_symbol,
        base_basis="market",
    )


@pytest.mark.parametrize("count", [0, 1, 3, 5, 6, 20])
def test_coverage_unattributed_display_split(count: int) -> None:
    """明细展示前 `UNATTRIBUTED_LIMIT` 条，其余归入「其余 N 只」；两段之和恒等于合计。

    0 条 / 1–5 条（不足上限）/ 6 条（越界 1 只）/ 20 条（越界 15 只）四类场景。
    """
    per_symbol = {f"{index:06d}": (index + 1) * 1_000 for index in range(count)}
    coverage = _coverage_for(per_symbol)

    assert len(coverage.unattributed) == min(count, UNATTRIBUTED_LIMIT)
    assert coverage.unattributed_others_count == max(0, count - UNATTRIBUTED_LIMIT)
    assert coverage.unattributed_display_limit == UNATTRIBUTED_LIMIT
    assert coverage.unattributed_listed_total_units == sum(
        entry.amount_units for entry in coverage.unattributed
    )
    assert (
        coverage.unattributed_listed_total_units + coverage.unattributed_others_total_units
        == sum(per_symbol.values())
    )
    # 展示顺序按 |金额| 降序（大额在前），追加行汇总的正是被截断的小额标的。
    amounts = [entry.amount_units for entry in coverage.unattributed]
    assert amounts == sorted(amounts, reverse=True)
    if count > UNATTRIBUTED_LIMIT:
        assert coverage.unattributed_others_total_units == sum(
            sorted(per_symbol.values(), reverse=True)[UNATTRIBUTED_LIMIT:]
        )


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
    # 本期有未平仓 lot/区间内交易 → 参与本期 → 可归因 universe 内 → warning。
    assert plan.in_universe is True
    entry = _exclusion_missing(REASON_MAPPING, ["600000"])
    assert entry is not None
    assert entry.severity == "warning"
    assert "数据质量缺口" in entry.reason


def test_mapping_missing_non_participating_symbol_skips_missing() -> None:
    """已清仓/退市（本期无参与）标的缺行业映射：不产生 missing 条目（§4.4）。"""
    bundle = _build_bundle(_retired_trades(), memberships={}, ladder={})
    assert len(bundle.plans) == 1
    plan = bundle.plans[0]
    assert plan.attributed is False
    assert plan.reason_code is None
    assert plan.in_universe is False
    assert bundle.excluded_by_reason == {REASON_ASSET_CLASS: ["600636"]}
    # 装配层已把该标的落到资产类别占位键；应用层对未知码不出条目。
    assert _exclusion_missing(REASON_ASSET_CLASS, ["600636"]) is None


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
        "本期亏损 3,200.00 元，主要拖累「买点」（-1,900.00 元，占构成 59.4%）；"
        "其次为「费用」（-410.00 元）。未归属 120.00 元，见残差明细。"
    )
    assert_copy_compliance(summary)


def test_summary_wording_follows_dominant_amount_sign() -> None:
    """P1-1：正总损益下点名负贡献因子时必须用“主要拖累”，不能用“主要来自”。"""
    summary = _summary(
        total_pnl_units=841_984,
        ranked=(("板块配置", -912_280, 52_700_000), ("选股", 605_182, 34_900_000)),
        residual_units=109_603,
    )
    assert summary is not None
    assert "本期盈利 8,419.84 元" in summary
    assert "主要拖累「板块配置」（-9,122.80 元，占构成 52.7%）" in summary
    assert "主要来自「板块配置」" not in summary

    summary_pos = _summary(
        total_pnl_units=105_400,
        ranked=(("选股", 60_518, 57_400_000),),
        residual_units=0,
    )
    assert summary_pos is not None and "主要来自「选股」" in summary_pos


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
        'Lost 3,200.00 CNY this period, mainly dragged by "买点" (-1,900.00 CNY, 59.4% of composition); '
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


def test_sector_start_fallback_uses_next_close_within_holiday_window() -> None:
    """期初缺 bar 时允许取参考日后首个收盘（节日缺口，记 info 披露）。"""
    sector_ladder = {
        "S1": [
            (date(2025, 3, 5), 1_020_000_000),  # P0(02-28) 后第 5 个自然日
            (date(2025, 3, 20), 990_000_000),
            (date(2025, 3, 25), 1_071_000_000),
            (date(2025, 3, 31), 1_122_000_000),
        ]
    }
    bundle = _build_bundle(_golden_trades(), memberships=MEMBERSHIPS, sector_ladder=sector_ladder)
    plan = bundle.plans[0]
    assert plan.attributed is True
    assert plan.sector_start_fallback == (P0_DATE, date(2025, 3, 5))
    assert bundle.sector_start_fallbacks == {"600000": (P0_DATE, date(2025, 3, 5))}


def test_sector_start_fallback_outside_window_still_excludes() -> None:
    """首个可得收盘超出 5 个自然日窗口 → 仍按 SECTOR_BAR_MISSING 剔除。"""
    sector_ladder = {
        "S1": [
            (date(2025, 3, 10), 1_020_000_000),  # P0(02-28) 后第 10 个自然日
            (date(2025, 3, 20), 990_000_000),
            (date(2025, 3, 25), 1_071_000_000),
            (date(2025, 3, 31), 1_122_000_000),
        ]
    }
    bundle = _build_bundle(_golden_trades(), memberships=MEMBERSHIPS, sector_ladder=sector_ladder)
    plan = bundle.plans[0]
    assert plan.attributed is False
    assert plan.reason_code == REASON_BAR


def test_sector_end_bar_never_fallbacks_forward() -> None:
    """期末方向严格取 ≤ 参考日最近收盘：即使次日有 bar 也不回退（避免未来信息）。"""
    sector_ladder = {
        "S1": [
            (P0_DATE, 1_000_000_000),
            (date(2025, 4, 1), 1_130_000_000),  # 04-01：落在期初窗口内也不得用于期末
        ]
    }
    bundle = _build_bundle(_golden_trades(), memberships=MEMBERSHIPS, sector_ladder=sector_ladder)
    plan = bundle.plans[0]
    assert plan.attributed is True
    assert plan.sector_start_fallback is None
    # 辅助函数本身能取到 04-01，但期末路径不调用它（只走 _close_entry 的 ≤ 语义）。
    assert _first_entry_within(sector_ladder, "S1", date(2025, 3, 31), 5) == (
        date(2025, 4, 1),
        1_130_000_000,
    )


def test_sector_start_fallback_missing_copy_bilingual() -> None:
    """期初回退披露：info + 中英文案含缺失日/回退日，且不影响归因口径。"""
    fallbacks = {"600000": (P0_DATE, date(2025, 3, 5))}
    zh = _sector_start_fallback_missing(fallbacks)
    assert zh.code == "SECTOR_BAR_FALLBACK"
    assert zh.severity == "info"
    assert "2025-02-28 → 2025-03-05" in zh.reason
    assert zh.what_to_do.startswith("无需处理")

    en = _sector_start_fallback_missing(fallbacks, lang="en")
    assert en.severity == "info"
    assert "2025-02-28 → 2025-03-05" in en.reason
    assert "holiday" in en.reason
    assert en.what_to_do == "no action needed (holiday gap; fallback close used)"


def _info_entry(code: str = "SECTOR_CLASSIFICATION_LOOKAHEAD") -> MissingEntry:
    return MissingEntry(
        field="classification_as_of",
        code=code,
        reason="行业归属为最新快照回溯，可能与当时实际归属不符；仅用于历史回看",
        what_to_do="仅用于回看历史；考核用途请以当时时点成分复核",
        severity="info",
    )


def test_status_info_only_missing_is_ok() -> None:
    """只剩 info 提示（如 look-ahead 披露）时不降级：整页 ok（§4.4）。"""
    status = _aggregate_status(
        [_info_entry(), _info_entry("SECTOR_MAPPING_MISSING")],
        judgement_units=0,
        tolerance_units=100,
    )
    assert status == "ok"


def test_status_warning_missing_degrades() -> None:
    """任一 warning 级 missing → degraded（旧行为不变）。"""
    warn = MissingEntry(
        field="factors",
        code="SECTOR_BAR_MISSING",
        reason="缺行业指数日线",
        what_to_do="补板块日线",
    )
    assert _aggregate_status([warn], judgement_units=0, tolerance_units=100) == "degraded"


def test_status_residual_tolerance_boundary() -> None:
    """|未归因标的损益| 超容差 → degraded；等于容差或容差内 → ok。"""
    assert (
        _aggregate_status([_info_entry()], judgement_units=101, tolerance_units=100) == "degraded"
    )
    # 负值按绝对值比较。
    assert (
        _aggregate_status([_info_entry()], judgement_units=-101, tolerance_units=100) == "degraded"
    )
    # 等于容差（含）不降级。
    assert _aggregate_status([_info_entry()], judgement_units=100, tolerance_units=100) == "ok"
    assert _aggregate_status([_info_entry()], judgement_units=4_076, tolerance_units=10_000) == "ok"


def test_residual_missing_within_tolerance_is_info() -> None:
    """容差内非零 → info 提示（不降级），文案以「未归因标的损益」为主语。"""
    entry = _residual_missing(judgement_units=4_076, tolerance_units=10_000)
    assert entry is not None
    assert entry.code == "RESIDUAL_WITHIN_TOLERANCE"
    assert entry.severity == "info"
    assert entry.reason == ("未归因标的损益 40.76 元，低于期末资产 0.20%（阈值 100.00 元）")
    assert entry.what_to_do == "无需处理"


def test_residual_missing_over_tolerance_is_warning() -> None:
    """超容差（负值取绝对值）→ RESIDUAL_LARGE warning。"""
    entry = _residual_missing(judgement_units=-571_057, tolerance_units=10_000)
    assert entry is not None
    assert entry.code == "RESIDUAL_LARGE"
    assert entry.severity == "warning"
    assert "5710.57" in entry.reason
    assert "100.00" in entry.reason
    assert entry.what_to_do == "看未归因标的明细（unattributed[]）"


def test_residual_missing_zero_tolerance_edge() -> None:
    """期末总资产为 0 → 容差 0：零值不产生条目，非零即超容差。"""
    assert _residual_missing(judgement_units=0, tolerance_units=0) is None
    entry = _residual_missing(judgement_units=1, tolerance_units=0)
    assert entry is not None
    assert entry.code == "RESIDUAL_LARGE"


def _judgement_units(split: ResidualSplit) -> int:
    """降级判定基数（应用层同一表达式）：标的级两部分合计。"""
    return sum(split.per_symbol.values())


def test_judgement_basis_excludes_interest_bucket() -> None:
    """只有账户级 interest 非零、标的级为 0 → 不降级、不产生条目（§4.4 新口径）。"""
    split = split_residual(
        plans=[],
        trades=[
            _trade(
                trade_date=date(2025, 3, 14),
                security_code="CASH",
                side="",
                category="interest",
                amount_units=500_000,  # +5,000.00 利息
            )
        ],
        t0=T0,
        t1=T1,
        begin_date=P0_DATE,
        end_date=T1,
        bucket_mv={},
        money_fund_balances={},
        holdings_at={},
        corporate={},
        canonical=lambda c: c,
    )
    assert split.buckets[BUCKET_INTEREST] == 500_000
    judgement = _judgement_units(split)
    assert judgement == 0
    assert _residual_missing(judgement_units=judgement, tolerance_units=100) is None
    assert (
        _aggregate_status([_info_entry()], judgement_units=judgement, tolerance_units=100) == "ok"
    )


def test_judgement_basis_excludes_ipo_bucket() -> None:
    """只有 ipo_pnl（打新收益，破发为负）非零 → 不降级、不产生条目。"""
    plans = [
        SymbolPlan(
            security_code="001237",
            symbol_class="new_stock",
            attributed=False,
            reason_code=None,
            participating=True,
        )
    ]
    split = split_residual(
        plans=plans,
        trades=[
            _trade(
                trade_date=date(2025, 3, 20),
                security_code="001237",
                side="sell",
                category="sell",
                quantity_units=1_000_000,
                price_units=40_000_000,
                amount_units=400_000,  # +4,000.00
            )
        ],
        t0=T0,
        t1=T1,
        begin_date=P0_DATE,
        end_date=T1,
        bucket_mv={"001237": (900_000, 0)},  # 期初 9,000.00 → 期末 0 ⇒ −5,000.00
        money_fund_balances={},
        holdings_at={"001237": (100_000, 0)},
        corporate={},
        canonical=lambda c: c,
    )
    assert split.buckets[BUCKET_IPO] == -500_000
    judgement = _judgement_units(split)
    assert judgement == 0
    assert _residual_missing(judgement_units=judgement, tolerance_units=100) is None
    assert _aggregate_status([], judgement_units=judgement, tolerance_units=100) == "ok"


def test_valuation_gap_info_only_and_status_unchanged() -> None:
    """|gap| > 容差 → info 披露（不发 warning、不改变 status）。"""
    entry = _valuation_gap_missing(
        gap_units=50_000,
        tolerance_units=10_000,
        v_end_units=5_000_000,
        total_pnl_units=-200_000,
    )
    assert entry is not None
    assert entry.code == "VALUATION_GAP_ABOVE_TOLERANCE"
    assert entry.severity == "info"
    assert entry.field == "residual"
    assert "500.00" in entry.reason  # 估值差金额
    assert "100.00" in entry.reason  # 容差
    assert "50000.00" in entry.reason  # 期末总资产
    # info 条目不影响状态（即使标的级为 0）。
    assert _aggregate_status([entry], judgement_units=0, tolerance_units=10_000) == "ok"


def test_valuation_gap_at_or_below_tolerance_is_silent() -> None:
    """|gap| 等于/低于容差 → 不产生条目；0 → 不产生条目。"""
    base = {"v_end_units": 5_000_000, "total_pnl_units": -200_000, "tolerance_units": 10_000}
    assert _valuation_gap_missing(gap_units=10_000, **base) is None
    assert _valuation_gap_missing(gap_units=-10_000, **base) is None
    assert _valuation_gap_missing(gap_units=0, **base) is None


def test_valuation_gap_negative_uses_absolute() -> None:
    """负值按绝对值判定与展示（reason 不带负号）。"""
    entry = _valuation_gap_missing(
        gap_units=-50_000,
        tolerance_units=10_000,
        v_end_units=5_000_000,
        total_pnl_units=-200_000,
    )
    assert entry is not None
    assert "估值差 500.00 元" in entry.reason
    assert "-500" not in entry.reason


def test_valuation_gap_info_coexists_with_d09_gate() -> None:
    """D-09（gap 占比 >5% → 不出 summary）触发时，info 披露仍产生、二者不重复告警。"""
    gap_units, total_pnl_units = 600_000, 100_000
    assert gap_ratio_units(gap_units, total_pnl_units) > GAP_RATIO_MAX  # summary 被抑制
    entry = _valuation_gap_missing(
        gap_units=gap_units,
        tolerance_units=10_000,
        v_end_units=5_000_000,
        total_pnl_units=total_pnl_units,
    )
    assert entry is not None  # 门禁不产生码，info 独立披露
    assert entry.severity == "info"
    assert entry.code == "VALUATION_GAP_ABOVE_TOLERANCE"


def test_judgement_basis_ignores_valuation_gap() -> None:
    """`valuation_gap` 不参与判定（仅披露）：gap 非零也不产生降级/条目。"""
    split = split_residual(
        plans=[],
        trades=[],
        t0=T0,
        t1=T1,
        begin_date=P0_DATE,
        end_date=T1,
        bucket_mv={},
        money_fund_balances={},
        holdings_at={},
        corporate={},
        canonical=lambda c: c,
    )
    judgement = _judgement_units(split)
    gap_units = -300_000  # valuation_gap 由应用层 plug，不进入判定基数
    assert judgement == 0 and gap_units != 0
    assert _residual_missing(judgement_units=judgement, tolerance_units=0) is None
    assert _aggregate_status([], judgement_units=judgement, tolerance_units=0) == "ok"


def _retired_trades() -> list[Trade]:
    """600636：区间前买入并清仓（模拟已退市/本期无参与的历史标的）。"""
    return [
        _trade(
            trade_date=date(2025, 1, 10),
            security_code="600636",
            side="buy",
            category="buy",
            quantity_units=1_000_000,
            price_units=1_000_000,
            amount_units=-100_050,
            commission_units=50,
        ),
        _trade(
            trade_date=date(2025, 1, 20),
            security_code="600636",
            side="sell",
            category="sell",
            quantity_units=1_000_000,
            price_units=1_200_000,
            amount_units=119_950,
            commission_units=50,
        ),
    ]


def test_closed_before_period_symbol_skips_missing() -> None:
    """历史清仓/退市标的（本期无持仓、无区间内平仓/开仓）不进 missing[]。

    候选集来自全历史 FIFO，但「是否参与本期」优先于映射/价格/指数剔除判定。
    """
    trades = _golden_trades() + _retired_trades()  # 600636 无行情、无行业映射
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)
    plan = {p.security_code: p for p in bundle.plans}["600636"]
    assert plan.attributed is False
    assert plan.reason_code is None  # 不再产生 REASON_MAPPING
    assert plan.in_universe is False
    assert REASON_MAPPING not in bundle.excluded_by_reason
    assert "600636" in bundle.excluded_by_reason[REASON_ASSET_CLASS]
    # 应用层按 REASON_ASSET_CLASS 生成 missing 时返回 None → 不产生条目。
    assert _exclusion_missing(REASON_ASSET_CLASS, ["600636"]) is None


def test_suspended_held_symbol_still_warns_on_missing_mapping() -> None:
    """期末仍有未平仓 lot 的停牌股仍参与本期 → 缺映射按 warning 披露。"""
    trades = [*_golden_trades(), _retired_trades()[0]]  # 600636 只买不卖、无行情、无映射
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)
    plan = {p.security_code: p for p in bundle.plans}["600636"]
    assert plan.in_universe is True
    assert plan.reason_code == REASON_MAPPING
    assert bundle.excluded_by_reason[REASON_MAPPING] == ["600636"]
    entry = _exclusion_missing(REASON_MAPPING, ["600636"], severity="warning")
    assert entry is not None and entry.severity == "warning"


def test_active_symbol_missing_mapping_still_warns() -> None:
    """本期参与（区间内买卖平仓）的活跃股缺映射 → 仍按 warning 披露。"""
    trades = [
        *_golden_trades(),
        _trade(
            trade_date=date(2025, 3, 5),
            security_code="600636",
            side="buy",
            category="buy",
            quantity_units=100_000,
            price_units=1_000_000,
            amount_units=-10_005,
            commission_units=5,
        ),
        _trade(
            trade_date=date(2025, 3, 20),
            security_code="600636",
            side="sell",
            category="sell",
            quantity_units=100_000,
            price_units=1_200_000,
            amount_units=11_995,
            commission_units=5,
        ),
    ]
    bundle = _build_bundle(trades, memberships=MEMBERSHIPS)
    plan = {p.security_code: p for p in bundle.plans}["600636"]
    assert plan.in_universe is True
    assert plan.reason_code == REASON_MAPPING
    entry = _exclusion_missing(REASON_MAPPING, ["600636"], severity="warning")
    assert entry is not None and entry.severity == "warning"


def test_non_participating_symbol_skips_all_reason_codes() -> None:
    """无参与标的即使命中 corporate/mapping/bar/price 缺口，也一律不产生 missing[]。"""
    bundle = _build_bundle(
        _retired_trades(),
        memberships={},  # 缺映射
        ladder={},  # 缺个股行情（REASON_PRICE）
        sector_ladder={},  # 缺行业指数（REASON_BAR）
        corporate={"600636": "S1"},  # 命中区间内公司行动（REASON_CORPORATE）
    )
    plan = bundle.plans[0]
    assert plan.reason_code is None
    assert plan.in_universe is False
    # 仅落在资产类别占位键（应用层不生成 missing 条目）。
    assert set(bundle.excluded_by_reason) == {REASON_ASSET_CLASS}
    assert bundle.excluded_by_reason[REASON_ASSET_CLASS] == ["600636"]
    assert bundle.has_candidate is False
    assert _exclusion_missing(REASON_CORPORATE, ["600636"]) is not None  # 有缺口本会披露
    assert _exclusion_missing(REASON_BAR, ["600636"]) is not None
    assert _exclusion_missing(REASON_PRICE, ["600636"]) is not None


def _new_stock_symbol_trades(row_date: date) -> list[Trade]:
    """001237 型：`row_date` 新股认购缴款 + 本期普通卖出（无其他交易）。"""
    return [
        _trade(
            trade_date=row_date,
            security_code="001237",
            side="buy",
            category="new_stock",
            quantity_units=5_000_000,  # 500 股
            price_units=53_260_000,  # 53.26
            amount_units=-2_663_000,  # −26,630.00
            summary="新股申购确认缴款",
        ),
        _trade(
            trade_date=date(2025, 3, 20),
            security_code="001237",
            side="sell",
            category="sell",
            quantity_units=5_000_000,
            price_units=60_000_000,  # 60.00
            amount_units=3_000_000,  # +30,000.00
            summary="证券卖出",
        ),
    ]


@pytest.mark.parametrize("row_date", [date(2025, 1, 20), P0_DATE])
def test_new_stock_exclusion_is_window_scoped(row_date: date) -> None:
    """窗口外（历史中签/锚日当天）的新股行不排除：本期按普通 stock 参与归因。

    001237 型：历史中签、本期只有普通买卖 → `stock` 且 attributed；
    锚日（T0−1）行属于上一期窗口（[T0, T1] 闭区间），本期同样按 stock 参与。
    """
    ladder = {
        "001237": [
            (row_date if row_date <= P0_DATE else P0_DATE, 70_000_000),  # 70.00
            (P0_DATE, 70_000_000),
            (date(2025, 3, 20), 60_000_000),
            (T1, 62_000_000),
        ]
    }
    bundle = _build_bundle(
        _new_stock_symbol_trades(row_date),
        memberships={"001237": "S1"},
        ladder=ladder,
    )
    plan = bundle.plans[0]
    assert plan.symbol_class == "stock"
    assert plan.attributed is True
    assert plan.reason_code is None


def test_new_stock_row_inside_window_excludes_symbol() -> None:
    """本期窗口 [T0, T1] 内有新股缴款行 → 按 `new_stock` 排除（不参与五因子）。"""
    bundle = _build_bundle(
        _new_stock_symbol_trades(T0),
        memberships={"001237": "S1"},
        ladder={
            "001237": [(P0_DATE, 70_000_000), (date(2025, 3, 20), 60_000_000), (T1, 62_000_000)]
        },
    )
    plan = bundle.plans[0]
    assert plan.symbol_class == "new_stock"
    assert plan.attributed is False
    assert plan.reason_code is None  # 资产类别排除不产生 reason（仅 coverage 列出）
    assert plan.in_universe is False
    assert plan.participating is True
    assert bundle.excluded_by_reason[REASON_ASSET_CLASS] == ["001237"]
