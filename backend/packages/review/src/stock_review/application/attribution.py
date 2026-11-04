"""五因子归因编排（设计 §5.3，API-036 唯一应用层入口）。

分层（§5.1）：本模块只做**取数编排 + DTO 组装**，口径全部在
`stock_review.attribution`（纯装配）与 `stock_core.attribution`（纯函数）：

1. 校验（model/benchmark/lookahead）→ 422；
2. `resolve_period`（复用 metrics.context；月/年/自定义 ≤366 天）；
3. 资产窗口（§3.2 D-01 MTM）`resolve_asset_window`，blocked 短路（不读行情/板块）；
4. 桥接对账（§3.2.1）：`|MTM − (bridge_profit + Δunreal)| > 1 分` → `BRIDGE_MISMATCH`；
5. PIT（§4.3 D-07）：`classification_as_of = min(period_end, membership_as_of)`，
   `period_end < first_sync_on` 且未开 look-ahead → blocked；
6. FIFO 重放 ×2（≤T1 / <T0）→ 公司行动识别 → 行情（只读缓存）→ 行业映射/指数；
7. 镜像推演（资产表同口径）→ `build_symbol_plans`（两级分摊）→ `split_residual`
   （六桶）→ `decompose`（Σfactors + residual == total_pnl）→ coverage/状态机/文案；
8. `AttributionResult`（金额 int，API 层转定点字符串）。

读路径不联网：行情 `load_prices(cached_only=True)`、板块走 `SectorDataSource`
端口（live 读 `market.*` 缓存 / replay 读夹具）；缺数据降级 200 + `missing[]`。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.attribution import (
    FACTOR_CODES,
    FACTOR_NAMES,
    FactorCode,
    decompose,
)
from stock_core.attribution.ranking import (
    RATIO_SCALE,
    rank_factors,
    ratio_na_reason,
    ratio_of,
    shares_of,
    total_abs_amounts,
)
from stock_core.fifo import build_rounds
from stock_core.values import Money, Ratio
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository
from stock_platform.ports import MarketDataSource, SectorDataSource
from stock_platform.repositories.sectors import SectorRepository
from stock_platform.symbols import is_bond

from stock_review.application.assets import (
    ASSET_DEFINITION_VERSION,
    get_asset_build_state,
    last_trading_day,
    latest_workday,
    valuation_end,
)
from stock_review.application.bond_alias import build_bond_alias, canonical_code
from stock_review.application.bridge import bridge_for_period
from stock_review.application.metrics import KNOWN_BENCHMARKS
from stock_review.application.rebuild import trades_to_events
from stock_review.application.valuation import (
    PRICE_LOOKBACK,
    QTY_PRICE_SCALE,
    bond_face_divisors,
    bond_value_units,
    category,
    close_on_or_before,
    holding_delta,
    initial_money_fund_balances,
    is_money_fund_row,
    load_prices,
    new_stock_pending_codes,
)
from stock_review.attribution.context import MissingEntry, resolve_asset_window
from stock_review.attribution.corporate import corporate_action_symbols
from stock_review.attribution.coverage import (
    MIN_SAMPLE_SEGMENTS,
    Coverage,
    build_coverage,
)
from stock_review.attribution.presentation import (
    FactorPresentation,
    assert_copy_compliance,
    build_summary,
    factor_conclusion,
    factor_presentation,
    summary_na_reason,
)
from stock_review.attribution.residual import (
    BUCKET_CORPORATE,
    BUCKET_DIVIDEND,
    BUCKET_GAP,
    BUCKET_INTEREST,
    BUCKET_OTHER,
    BUCKET_UNATTRIBUTED,
    ResidualSplit,
    split_residual,
)
from stock_review.attribution.segments import (
    REASON_BAR,
    REASON_CORPORATE,
    REASON_DATA_ERROR,
    REASON_FEE,
    REASON_MAPPING,
    REASON_PRICE,
    build_symbol_plans,
)
from stock_review.metrics.context import Period, resolve_period
from stock_review.metrics.hash import inputs_hash
from stock_review.models import AssetDaily
from stock_review.repositories.assets import AssetRepository

__all__ = [
    "ATTRIBUTION_DEFINITION_VERSION",
    "AttributionFactor",
    "AttributionResult",
    "AttributionSample",
    "build_attribution",
]

# 归因口径版本（§5.6；口径变更需 docs/migrations/ATTRIBUTION-* 并 bump）。
ATTRIBUTION_DEFINITION_VERSION: Final[str] = "1.0"
# 公开 taxonomy 值（application/sectors.py 的 `_DB_TAXONOMY` 是私有的，不跨模块 import）。
_DB_TAXONOMY_INDUSTRY: Final[str] = "industry_sw"
# 归因读的同步状态数据集：live 同步链写 `sector`（sector_live.DATASET_KEY）；
# 描述性板块看板用 `sector_all`（sectors.DATASET_KEY），两者互不覆盖。
_SECTOR_DATASET_KEY: Final[str] = "sector"
# 桥接容差（§3.2.1：|MTM − 桥接式| ≤ 1 分）。
_BRIDGE_TOLERANCE_UNITS: Final[int] = 1
# 残差容差比例（单一口径来源，§3.6/§4.4）：期末总资产 × 0.2%。
# 单位：比例（非百分点）；`Decimal` 避免 float；期末总资产取
# `review.asset_daily.total_assets_units`（区间末行，标度整数分）。
RESIDUAL_TOLERANCE_PCT: Final[Decimal] = Decimal("0.002")
# NOT_PIT 判定（§4.3）：快照日距期末 >90 天 → degraded。
_NOT_PIT_DAYS: Final[int] = 90
# 缺行业映射文案（zh/en × warning/info）：warning = 数据质量缺口（参与本期且缺映射）；
# info 分支为防御保留（装配层已让「本期不参与」标的提前跳过、不产生 missing[]）。
# `lang` 只换文案不换数字。
_MAPPING_COPY: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("zh", "warning"): ("缺行业归属（数据质量缺口）", "同步成分/确认 A 股"),
    ("zh", "info"): ("无行业归属（已退市或不在可归因范围内，不影响归因口径）", "无需处理"),
    (
        "en",
        "warning",
    ): ("missing industry mapping (data-quality gap)", "sync sector members / confirm A-share"),
    (
        "en",
        "info",
    ): (
        "missing industry mapping (delisted or outside attributable universe; "
        "does not affect attribution)",
        "no action needed",
    ),
}

# factors[].code 的固定产品名（FACTOR_NAMES 键为 str，FactorCode 是 StrEnum）。


@dataclass(frozen=True, slots=True)
class AttributionFactor:
    """`factors[]` 单项（§6.2）。

    blocked 缺省语义（§6.3 评审 P0-7，冻结）：`amount/ratio/share` 为 ``None``
    且 API 层**省略键**（amount 缺省；ratio/share 亦缺省），`conclusion=None`
    显式输出 null，`presentation` 给 `(None, None, "flat")`；`code/name/rank` 保留。
    """

    code: str
    name: str
    amount_units: int | None
    ratio_units: int | None
    share_units: int | None
    rank: int
    conclusion: str | None
    presentation: FactorPresentation


@dataclass(frozen=True, slots=True)
class AttributionSample:
    """`sample` 块（§6.2）：样本规模与结论门禁状态（金额照给，只影响 summary）。"""

    n_segments: int
    n_attributed_segments: int
    n_rounds: int
    n_symbols: int
    min_n: int
    sample_status: str  # "ok" | "insufficient"


@dataclass(frozen=True, slots=True)
class ResidualReconciliation:
    """残差对账（§4.4）：标的合计 + 账户级桶合计 == residual（全部为标度整数分）。

    - `unattributed_symbols_total`：`split.per_symbol` 合计（= corporate_action_pnl +
      unattributed_price_pnl 中落到标的的部分，含被 5 条展示上限截断的标的）；
    - `account_level_total`：不按标的分摊的桶（interest / other_cashflow /
      dividend_without_position）+ valuation_gap；
    - `listed_total` / `others_count` / `others_total`：明细展示前 `display_limit`
      条的金额合计、其后标的数与其金额合计；`listed_total + others_total ==
      unattributed_symbols_total`；
    - 等式由构造保证：residual = Σ五桶 + valuation_gap。
    """

    unattributed_symbols_total_units: int
    account_level_total_units: int
    residual_units: int
    listed_units: int
    others_count: int
    others_units: int
    display_limit: int


@dataclass(frozen=True, slots=True)
class AttributionResult:
    """`GET /attribution` 应用层结果（§6.2；金额标度整数，API 层格式化）。

    `None` 的语义区分（§6.3 blocked 缺省冻结）：
    - `total_pnl/base_capital/residual_units/residual_breakdown/coverage` 为
      ``None`` = **blocked，API 层省略整个键**；
    - `ratio_units/share_units/ratio_na_reason` 为 ``None`` = 显式 null（ratio
      不可给时附 `ratio_na_reason` 机读原因）。
    """

    model: str
    ratio_basis: str
    period: str
    period_start: date
    period_end: date
    status: str  # "ok" | "degraded" | "blocked"
    definition_version: str
    total_pnl_basis: str
    total_pnl_units: int | None
    ratio_na_reason: str | None
    base_capital_units: int | None
    factors: tuple[AttributionFactor, ...]
    summary: str | None
    summary_na_reason: str | None
    residual_units: int | None
    residual_ratio_units: int | None
    residual_breakdown: Mapping[str, int] | None
    sample: AttributionSample
    coverage: Coverage | None
    missing: tuple[MissingEntry, ...]
    assumptions: Mapping[str, Any]
    data_as_of: date | None
    inputs_hash: str
    links: Mapping[str, str]
    residual_reconciliation: ResidualReconciliation | None = None


@dataclass(frozen=True, slots=True)
class _Pit:
    """PIT 解析结果（§4.3；`blocked=True` 时 `classification_as_of` 无意义）。"""

    classification_as_of: date | None
    membership_as_of: date | None
    lookahead_used: bool
    stale: bool  # 快照日距期末 >90 天 → SECTOR_CLASSIFICATION_NOT_PIT
    blocked: bool


@dataclass(frozen=True, slots=True)
class _Mirror:
    """镜像推演快照（资产表同口径；供 `split_residual` / coverage 使用）。

    - `holdings`：{code: (begin_qty, end_qty)}（split_residual 的 `holdings_at`）；
    - `bucket_mv`：{code: (begin_mv, end_mv)}——仅收**两端都可估值**的标的
      （市值或成本兜底）；任一端不可估值 → 不进桶，由 valuation_gap 吸收；
    - `money_funds`：{code: (begin_bal, end_bal)}——begin 为首条窗口货基行之前的
      余额（`initial_money_fund_balances` 口径），end 为 end_date 前最后余额；
    - `anchors`：{code: 期初锚}（有价按市值、无价按净成本；coverage 资本分母）；
    - `cost_fallback`：期初按成本兜底估值的标的（coverage.base_basis="mixed"）。
    """

    holdings: dict[str, tuple[int, int]]
    bucket_mv: dict[str, tuple[int, int]]
    money_funds: dict[str, tuple[int, int]]
    anchors: dict[str, int]
    cost_fallback: frozenset[str]
    # 资产口径（比例成本）区间已实现损益：卖出净额 − 结转成本。与 `assets._build` 同原语，
    # 供 §3.2.1 对账使用；货基行不结转成本（余额变化在 total_pnl 中自行抵消）。
    realized_units: int


def _mirror_view(
    trades: Sequence[Trade],
    *,
    begin_date: date,
    end_date: date,
    ladder: dict[str, list[tuple[date, int]]],
    canonical: Callable[[str], str],
    bond_divisors: Mapping[str, int],
    new_stocks: set[str],
    begin_balances: Mapping[str, int],
) -> _Mirror:
    """单遍扫描 trades，在 `begin_date`/`end_date` 两个时点快照（资产表同口径）。

    持仓/成本推演与 `assets._build` 循环一致：货基行只覆盖余额、可转债发债代码
    归一、卖出按数量比例结转成本、新股中签通知行不建仓。
    """
    holdings: dict[str, int] = {}
    costs: dict[str, int] = {}
    mf_bal: dict[str, int] = {}
    begin_snap: tuple[dict[str, int], dict[str, int], dict[str, int]] | None = None
    end_snap: tuple[dict[str, int], dict[str, int], dict[str, int]] | None = None
    realized = 0

    def capture() -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        return (dict(holdings), dict(costs), dict(mf_bal))

    for trade in trades:
        if begin_snap is None and trade.trade_date > begin_date:
            begin_snap = capture()
        if end_snap is None and trade.trade_date > end_date:
            end_snap = capture()
            break
        if is_money_fund_row(trade):
            code = (trade.security_code or "").strip()
            if code:
                mf_bal[code] = max(0, int(trade.money_fund_balance_units or 0))
            continue
        raw = (trade.security_code or "").strip()
        if not raw:
            continue
        code = canonical(raw)
        trade_category = category(trade)
        delta = holding_delta(trade_category, trade.quantity_units, amount_units=trade.amount_units)
        if delta:
            holdings[code] = holdings.get(code, 0) + delta
        if trade_category in {"buy", "rights", "new_stock"} and trade.amount_units != 0:
            costs[code] = costs.get(code, 0) + (-trade.amount_units)
        elif trade_category == "sell":
            old_qty = holdings.get(code, 0) - delta
            released = 0
            if old_qty > 0 and delta < 0:
                old_cost = costs.get(code, 0)
                new_cost = max(0, old_cost + (old_cost * delta) // old_qty)
                costs[code] = new_cost
                released = old_cost - new_cost
            if begin_date < trade.trade_date <= end_date:
                # 资产表比例成本口径的已实现损益：卖出净额 − 结转成本。
                realized += trade.amount_units - released
    if begin_snap is None:
        begin_snap = capture()
    if end_snap is None:
        end_snap = capture()
    held_b, cost_b, _mf_b = begin_snap
    held_e, cost_e, mf_e = end_snap

    def value_at(
        held: Mapping[str, int], cost_snap: Mapping[str, int], on: date
    ) -> tuple[dict[str, int], set[str]]:
        """持仓 → {code: 市值}；缺价债券/待上市新股按成本兜底；其余缺价省略。"""
        values: dict[str, int] = {}
        fallback: set[str] = set()
        for code, qty in held.items():
            if qty == 0:
                continue
            close = close_on_or_before(ladder, code, on)
            if close is None:
                cost = max(0, cost_snap.get(code, 0))
                if cost and (is_bond(code) or code in new_stocks):
                    values[code] = cost
                    fallback.add(code)
                continue
            if is_bond(code):
                values[code] = bond_value_units(code, qty, close, bond_divisors)
            else:
                values[code] = qty * close // QTY_PRICE_SCALE
        return values, fallback

    mv_b, fb_b = value_at(held_b, cost_b, begin_date)
    mv_e, _fb_e = value_at(held_e, cost_e, end_date)

    holdings_map: dict[str, tuple[int, int]] = {}
    bucket_mv: dict[str, tuple[int, int]] = {}
    anchors: dict[str, int] = {}
    codes = (set(held_b) | set(held_e)) - {"CASH"}
    for code in sorted(codes):
        begin_qty = held_b.get(code, 0)
        end_qty = held_e.get(code, 0)
        if begin_qty == 0 and end_qty == 0:
            continue
        holdings_map[code] = (begin_qty, end_qty)
        if begin_qty != 0 and code not in mv_b:
            continue  # 期初不可估值 → 整标的进 valuation_gap（不进 MV 桶）
        if end_qty != 0 and code not in mv_e:
            continue  # 期末不可估值 → 同上
        bucket_mv[code] = (
            mv_b.get(code, 0) if begin_qty else 0,
            mv_e.get(code, 0) if end_qty else 0,
        )
        anchor = mv_b.get(code, 0) if begin_qty else 0
        if anchor:
            anchors[code] = anchor

    # 货基余额（begin = 首条窗口行之前；end = end_date 前最后余额）。
    mf_codes = (set(begin_balances) | set(mf_e)) - {""}
    money_funds = {
        code: (begin_balances.get(code, 0), mf_e.get(code, 0))
        for code in sorted(mf_codes)
        if begin_balances.get(code, 0) != 0 or mf_e.get(code, 0) != 0
    }
    return _Mirror(
        holdings=holdings_map,
        bucket_mv=bucket_mv,
        money_funds=money_funds,
        anchors=anchors,
        cost_fallback=frozenset(fb_b),
        realized_units=realized,
    )


def _blocked_factors() -> tuple[AttributionFactor, ...]:
    """blocked 因子骨架：固定 5 项 + 固定序 rank，数值/文案全缺省（§6.3）。"""
    return tuple(
        AttributionFactor(
            code=str(code),
            name=FACTOR_NAMES[str(code)],
            amount_units=None,
            ratio_units=None,
            share_units=None,
            rank=index + 1,
            conclusion=None,
            presentation=FactorPresentation(
                bar_pct=None, ratio_pct=None, share_pct=None, tone="flat"
            ),
        )
        for index, code in enumerate(FACTOR_CODES)
    )


def _assumptions(
    *,
    beginning_value_source: str,
    classification_version: str | None,
    classification_as_of: date | None,
    lookahead_used: bool,
    board_bar_as_of: date | None,
    benchmark_id: str,
    data_sources: Mapping[str, date | None],
    data_starts: Mapping[str, date | None] | None = None,
    sector_bar_fallback: Mapping[str, Any] | None = None,
    residual_tolerance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`assumptions` 键集（§6.2；全部进 `inputs_hash`）。"""
    return {
        "total_pnl_basis": "asset_daily_mtm",
        "beginning_value_source": beginning_value_source,
        "cashflow_basis": "transfer_rows",
        "fee_basis": "commission+stamp_tax+other_fee",
        "dividend_basis": "cash_ledger_rows_net_after_tax",
        "sector_basis": "ths_industry_index",
        "classification_version": classification_version,
        "classification_as_of": classification_as_of,
        "classification_lookahead": lookahead_used,
        "board_bar_as_of": board_bar_as_of,
        "benchmark_id": benchmark_id,
        "benchmark_total_return_proxy": True,
        "ratio_basis": "amount/total_pnl",
        "attributed_universe": "a_share_stock",
        "corporate_action_policy": "exclude_symbol",
        "rounding": "scaled_int_half_up_1e-8; residual_plug",
        "adjust": "none",
        "data_sources": dict(data_sources),
        "data_starts": dict(data_starts or {}),
        "sector_bar_fallback": dict(
            sector_bar_fallback or {"count": 0, "symbols": [], "first_bar_date": None}
        ),
        "residual_tolerance": dict(
            residual_tolerance or {"amount": None, "pct": str(_tolerance_pct_ratio()), "base": None}
        ),
    }


def _fingerprints(
    trades: Sequence[Trade], assets: Sequence[AssetDaily]
) -> tuple[int, datetime | None, date | None, date | None, int, datetime | None]:
    """(trades 行数, max updated_at, 资产首日, 资产末日, 资产行数, max rebuilt_at)。"""
    trade_updated = max((t.updated_at for t in trades if t.updated_at is not None), default=None)
    asset_first = assets[0].trade_date if assets else None
    asset_last = assets[-1].trade_date if assets else None
    asset_rebuilt = max((a.rebuilt_at for a in assets if a.rebuilt_at is not None), default=None)
    return (
        len(trades),
        trade_updated,
        asset_first,
        asset_last,
        len(assets),
        asset_rebuilt,
    )


def _hash_payload(
    *,
    model: str,
    period: Period,
    benchmark_id: str,
    lookahead_used: bool,
    trade_count: int,
    trade_updated: Any,
    asset_first: date | None,
    asset_last: date | None,
    asset_rows: int,
    asset_rebuilt: Any,
    market_as_of: date | None,
    sector: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """`inputs_hash` 输入（§5.6；规范化 JSON sha256）。"""
    payload: dict[str, Any] = {
        "model": model,
        "definition_version": ATTRIBUTION_DEFINITION_VERSION,
        "period": period.label,
        "benchmark": benchmark_id,
        "classification_lookahead": lookahead_used,
        "trades": {"count": trade_count, "max_updated_at": trade_updated},
        "assets": {
            "first": asset_first,
            "last": asset_last,
            "rows": asset_rows,
            "max_rebuilt_at": asset_rebuilt,
        },
        "market_as_of": market_as_of,
    }
    if sector is not None:
        payload["sector"] = dict(sector)
    return payload


def _blocked_result(
    *,
    period: Period,
    missing: Sequence[MissingEntry],
    benchmark_id: str,
    trades: Sequence[Trade],
    assets: Sequence[AssetDaily],
    asset_data_as_of: date | None,
    market_as_of: date | None,
    sector_as_of: date | None,
    lookahead_used: bool,
    classification_version: str | None,
    classification_as_of: date | None,
    beginning_value_source: str,
    sector_fingerprint: Mapping[str, Any] | None,
    sample: AttributionSample | None = None,
) -> AttributionResult:
    """blocked 组装（金额键全缺省，§6.3；三处短路共用）。"""
    assumptions = _assumptions(
        beginning_value_source=beginning_value_source,
        classification_version=classification_version,
        classification_as_of=classification_as_of,
        lookahead_used=lookahead_used,
        board_bar_as_of=None,
        benchmark_id=benchmark_id,
        data_sources={
            "asset_daily": asset_data_as_of,
            "market": market_as_of,
            "sector": sector_as_of,
        },
    )
    trade_count, trade_updated, asset_first, asset_last, asset_rows, asset_rebuilt = _fingerprints(
        trades, assets
    )
    payload = _hash_payload(
        model="five_factor",
        period=period,
        benchmark_id=benchmark_id,
        lookahead_used=lookahead_used,
        trade_count=trade_count,
        trade_updated=trade_updated,
        asset_first=asset_first,
        asset_last=asset_last,
        asset_rows=asset_rows,
        asset_rebuilt=asset_rebuilt,
        market_as_of=market_as_of,
        sector=sector_fingerprint,
    )
    as_of_candidates = [d for d in (asset_data_as_of, market_as_of, sector_as_of) if d is not None]
    return AttributionResult(
        model="five_factor",
        ratio_basis="amount/total_pnl",
        period=period.label,
        period_start=period.start,
        period_end=period.end,
        status="blocked",
        definition_version=ATTRIBUTION_DEFINITION_VERSION,
        total_pnl_basis="asset_daily_mtm",
        total_pnl_units=None,
        ratio_na_reason=None,
        base_capital_units=None,
        factors=_blocked_factors(),
        summary=None,
        summary_na_reason="区间数据不可用（blocked），详见数据质量缺失项。",
        residual_units=None,
        residual_ratio_units=None,
        residual_breakdown=None,
        sample=sample
        or AttributionSample(
            n_segments=0,
            n_attributed_segments=0,
            n_rounds=0,
            n_symbols=0,
            min_n=MIN_SAMPLE_SEGMENTS,
            sample_status="insufficient",
        ),
        coverage=None,
        missing=tuple(missing),
        assumptions=assumptions,
        data_as_of=min(as_of_candidates) if as_of_candidates else None,
        inputs_hash=inputs_hash(payload),
        links={
            "dashboard": "/dashboard",
            "sectors": "/sectors",
            "rounds": "/rounds",
            "reports": "/reports",
        },
    )


def _sector_start_fallback_missing(
    fallbacks: Mapping[str, tuple[date, date]], *, lang: str = "zh"
) -> MissingEntry:
    """期初行业指数回退披露（info；节假日缺口，不影响归因口径）。"""
    codes = sorted(fallbacks)
    sample_code, (missing_date, actual_date) = sorted(fallbacks.items())[0]
    if lang == "en":
        reason = (
            f"{len(codes)} symbol(s) missing sector index bar at period start (holiday); "
            f"fell back to the next available close (e.g. {sample_code}: "
            f"{missing_date} → {actual_date}): {','.join(codes[:8])}"
        )
        action = "no action needed (holiday gap; fallback close used)"
    else:
        reason = (
            f"{len(codes)} 个标的的行业指数期初缺 bar（节假日），"
            f"回退取参考日之后首个可得收盘（如 {sample_code}：{missing_date} → "
            f"{actual_date}）：{','.join(codes[:8])}"
        )
        action = "无需处理（节假日缺口，已回退取最近可得收盘）"
    return MissingEntry(
        field="factors",
        code="SECTOR_BAR_FALLBACK",
        reason=reason,
        what_to_do=action,
        severity="info",
    )


def _tolerance_pct_ratio() -> Ratio:
    """容差比例的 API 定点表示（唯一来源仍是 `RESIDUAL_TOLERANCE_PCT`）。"""
    return Ratio(int(RESIDUAL_TOLERANCE_PCT * RATIO_SCALE))


def _aggregate_status(
    missing: Sequence[MissingEntry],
    *,
    unattributed_units: int,
    tolerance_units: int,
) -> str:
    """ok/degraded 聚合（§4.4；blocked 由短路路径决定，此处不负责）。

    只有 `severity=info` 的提示（如 look-ahead 披露、板块指数期初回退、容差内残差）
    不影响归因口径，不降级；任一 warning/blocker、或 |未归因金额| 超容差 → degraded。
    """
    if any(item.severity != "info" for item in missing) or (
        abs(unattributed_units) > tolerance_units
    ):
        return "degraded"
    return "ok"


def _display_name(raw: str | None) -> str | None:
    """账单名称的展示形态：压缩空白（账单里可能含换行），空白视为缺失 → None。"""
    if not raw:
        return None
    return " ".join(raw.split()) or None


def _residual_reconciliation(
    *,
    split: ResidualSplit,
    coverage: Coverage,
    gap_units: int,
    residual_units: int,
) -> ResidualReconciliation:
    """残差对账（§4.4，标度整数）：Σper_symbol + (dividend+interest+other+gap) == residual。

    `per_symbol` 是 corporate/unattributed 两桶中**落到标的**的部分（含被展示上限
    截断的标的），因此恒等于两桶之和；等式由 `residual = Σ五桶 + gap` 保证。
    `listed + others` 由 `coverage` 的已展示/未展示分组给出，同样恒等于 Σper_symbol。
    """
    return ResidualReconciliation(
        unattributed_symbols_total_units=sum(split.per_symbol.values()),
        account_level_total_units=(
            split.buckets[BUCKET_DIVIDEND]
            + split.buckets[BUCKET_INTEREST]
            + split.buckets[BUCKET_OTHER]
            + gap_units
        ),
        residual_units=residual_units,
        listed_units=coverage.unattributed_listed_total_units,
        others_count=coverage.unattributed_others_count,
        others_units=coverage.unattributed_others_total_units,
        display_limit=coverage.unattributed_display_limit,
    )


def _residual_missing(*, unattributed_units: int, tolerance_units: int) -> MissingEntry | None:
    """残差披露（§4.4）：超容差 → `RESIDUAL_LARGE`(warning)；容差内非零 → info。

    `unattributed_units` = `decomposition.residual_units`（各桶合计，含估值差），
    取绝对值与容差比较；为 0 时不产生条目（避免无意义提示）。
    """
    magnitude = abs(unattributed_units)
    amount = Money(magnitude)
    tolerance = Money(tolerance_units)
    if magnitude > tolerance_units:
        return MissingEntry(
            field="residual",
            code="RESIDUAL_LARGE",
            reason=f"未归因金额 {amount} 元（绝对值）超过容差 {tolerance} 元（期末总资产的 {_tolerance_pct_ratio().as_pct()}）",
            what_to_do="看 residual_breakdown",
        )
    if unattributed_units != 0:
        return MissingEntry(
            field="residual",
            code="RESIDUAL_WITHIN_TOLERANCE",
            severity="info",
            reason=f"未归因 {amount} 元，低于期末资产 {_tolerance_pct_ratio().as_pct()}（阈值 {tolerance} 元）",
            what_to_do="无需处理",
        )
    return None


def _exclusion_missing(
    reason: str,
    codes: Sequence[str],
    *,
    lang: str = "zh",
    severity: str = "warning",
) -> MissingEntry | None:
    """标的剔除 → `missing[]` 披露条目（资产类别排除不进 missing[]）。

    `severity`：缺行业映射按「是否参与本期」分级——参与本期（有持仓/区间内交易）
    为 `warning`（数据质量缺口）；`info` 分支为防御保留（正常路径已提前跳过）。
    `lang` 只切换该码的中英文案，不改变数字（§5.5）；其他码暂保持中文。
    """
    shown = sorted(codes)[:8]
    if reason == REASON_MAPPING:
        copy_key = ("en" if lang == "en" else "zh", "warning" if severity == "warning" else "info")
        note, action = _MAPPING_COPY[copy_key]
        if lang == "en":
            suffix = f" (+{len(codes) - len(shown)} more)" if len(codes) > len(shown) else ""
            reason_text = f"{len(codes)} symbol(s) {note}: {','.join(shown)}{suffix}"
        else:
            suffix = f" 等 {len(codes)} 只" if len(codes) > len(shown) else ""
            reason_text = f"{len(codes)} 个标的{note}：{','.join(shown)}{suffix}"
        return MissingEntry(
            field="factors",
            code=reason,
            reason=reason_text,
            what_to_do=action,
            severity=severity,
        )
    label = {
        REASON_BAR: ("行业指数日线缺失", "运行 make sync-sectors ARGS='--start …'"),
        REASON_PRICE: ("参考价缺失", "补行情"),
        REASON_CORPORATE: ("区间内送转/配股", "等待复权能力（P2）"),
        REASON_FEE: ("费用口径未知", "人工核对流水"),
        REASON_DATA_ERROR: ("FIFO 数据异常", "对账/修正流水"),
    }.get(reason)
    if label is None:
        return None
    suffix = f" 等 {len(codes)} 只" if len(codes) > len(shown) else ""
    return MissingEntry(
        field="factors",
        code=reason,
        reason=f"{len(codes)} 个标的（{label[0]}）：{','.join(shown)}{suffix}",
        what_to_do=label[1],
        severity=severity,
    )


def _pit_view(
    *,
    membership_as_of: date | None,
    period_end: date,
    lookahead_requested: bool,
    first_sync_on: date | None,
) -> _Pit:
    """PIT 算法（§4.3，冻结）：first_sync_on / membership_as_of → classification_as_of。"""
    not_covered = membership_as_of is None or (
        first_sync_on is not None and period_end < first_sync_on
    )
    if not_covered:
        if lookahead_requested:
            # 最早可用快照 + degraded 披露（SECTOR_CLASSIFICATION_LOOKAHEAD）。
            return _Pit(
                classification_as_of=membership_as_of,
                membership_as_of=membership_as_of,
                lookahead_used=True,
                stale=False,
                blocked=False,
            )
        return _Pit(
            classification_as_of=None,
            membership_as_of=membership_as_of,
            lookahead_used=False,
            stale=False,
            blocked=True,
        )
    assert membership_as_of is not None  # not_covered=False 蕴含非 None
    classification_as_of = min(period_end, membership_as_of)
    stale = abs((period_end - classification_as_of).days) > _NOT_PIT_DAYS
    return _Pit(
        classification_as_of=classification_as_of,
        membership_as_of=membership_as_of,
        lookahead_used=False,
        stale=stale,
        blocked=False,
    )


async def build_attribution(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    period: str | None,
    model: str = "five_factor",
    benchmark: str | None = None,
    classification_lookahead: bool = False,
    clock: Clock,
    market_data: MarketDataSource,
    sector_data: SectorDataSource,
    lang: str = "zh",
    lookahead_enabled: bool = False,
) -> AttributionResult:
    """装配五因子归因结果（§5.3 步骤 1-8；降级不抛 5xx，校验失败 422）。"""
    # ── 1. 请求校验（§6.1）──────────────────────────────────────
    if model != "five_factor":
        raise ApiError(ErrorCode.MODEL_NOT_AVAILABLE, detail=f"未知归因模型: {model}")
    if classification_lookahead and not lookahead_enabled:
        raise ApiError(
            ErrorCode.MODEL_NOT_AVAILABLE,
            detail="classification_lookahead 需要部署开启 SECTOR_LOOKAHEAD_ENABLED",
        )
    if benchmark is not None and benchmark not in KNOWN_BENCHMARKS:
        raise ApiError(ErrorCode.UNKNOWN_BENCHMARK, detail=f"未知 benchmark: {benchmark}")
    benchmark_id = benchmark or "hs300_tr"

    # ── 2. 区间解析（复用 metrics.context）────────────────────────
    resolved = resolve_period(
        period=period, date_from=None, date_to=None, today=clock.business_today()
    )

    # ── 3. 取数 + 资产窗口（§3.2；blocked 短路，不读行情/板块）─────
    # 稳定序（日期 → 行号 → 补差行优先 → id）：`_mirror_view` 的期初/期末快照按顺序截取，
    # 与资产表 `_build` 同序；`list_all` 无序会导致持仓/成本错位（验收 P0-B）。
    trades = await TradeRepository(session).balances_ordered(user_id)
    assets = await AssetRepository(session).series(
        user_id, definition_version=ASSET_DEFINITION_VERSION
    )
    build_state, _definition = await get_asset_build_state(
        session, user_id=user_id, clock=clock, market_data=market_data
    )
    asset_data_as_of = build_state.data_as_of
    valuation_end_date = valuation_end(clock)
    window = resolve_asset_window(
        assets,
        t0=resolved.start,
        t1=resolved.end,
        data_as_of=asset_data_as_of,
        valuation_end_date=valuation_end_date,
    )
    trade_count, trade_updated, asset_first, asset_last, asset_rows, asset_rebuilt = _fingerprints(
        trades, assets
    )

    if window.status == "blocked":
        return _blocked_result(
            period=resolved,
            missing=window.missing,
            benchmark_id=benchmark_id,
            trades=trades,
            assets=assets,
            asset_data_as_of=asset_data_as_of,
            market_as_of=market_data.as_of,
            sector_as_of=None,
            lookahead_used=False,
            classification_version=None,
            classification_as_of=None,
            beginning_value_source=window.beginning_value_source,
            sector_fingerprint=None,
        )

    assert window.total_pnl_units is not None and window.begin_date is not None
    assert window.end_date is not None

    # ── 4. 桥接对账（§3.2.1）：取桥接分类现金流；与资产口径已实现损益在镜像后比对 ──
    missing: list[MissingEntry] = list(window.missing)
    bridge = await bridge_for_period(
        session,
        user_id=user_id,
        period_start=resolved.start,
        period_end=resolved.end,
        clock=clock,
    )

    # ── 5. PIT（§4.3）：blocked 短路（不读行情/板块）──────────────
    # 区间终点是自然日（月末常落在周末/节假日），板块成分按交易日发布；先对齐到
    # 「≤ 区间末日的最后一个交易日」（如 2026-10-31 周六 → 2026-10-30），
    # 否则以 10-31 查 SCD2 成分会漏掉以 10-30 关闭/新增的行。
    sync_state = await SectorRepository(session).sync_state(dataset_key=_SECTOR_DATASET_KEY)
    membership_as_of = (
        sync_state.membership_as_of
        if sync_state is not None and sync_state.membership_as_of is not None
        else sector_data.as_of
    )
    classification_period_end = await last_trading_day(session, resolved.end)
    # live 数据源的 capabilities 依赖一次异步刷新才知历史下界；未刷新时用 DB
    # `MIN(effective_from)` 兜底，避免历史区间被误判为「已覆盖」而取不到成分。
    first_sync_on = sector_data.capabilities.history_start_for(_DB_TAXONOMY_INDUSTRY)
    if first_sync_on is None:
        first_sync_on = await SectorRepository(session).first_member_effective_from(
            taxonomy=_DB_TAXONOMY_INDUSTRY
        )
    pit = _pit_view(
        membership_as_of=membership_as_of,
        period_end=classification_period_end,
        lookahead_requested=classification_lookahead,
        first_sync_on=first_sync_on,
    )
    sector_fingerprint: dict[str, Any] = {
        "checksum": sync_state.checksum if sync_state is not None else None,
        "membership_as_of": pit.membership_as_of,
        "bar_as_of": sync_state.bar_as_of if sync_state is not None else None,
    }
    if pit.blocked:
        missing.append(
            MissingEntry(
                field="classification_as_of",
                code="SECTOR_HISTORY_NOT_COVERED",
                reason="板块成分首次同步日晚于区间终点（无时点行业归属）",
                what_to_do="缩短区间 / 人工批准 look-ahead",
            )
        )
        return _blocked_result(
            period=resolved,
            missing=missing,
            benchmark_id=benchmark_id,
            trades=trades,
            assets=assets,
            asset_data_as_of=asset_data_as_of,
            market_as_of=market_data.as_of,
            sector_as_of=pit.membership_as_of,
            lookahead_used=False,
            classification_version=None,
            classification_as_of=None,
            beginning_value_source=window.beginning_value_source,
            sector_fingerprint=sector_fingerprint,
        )
    if pit.lookahead_used:
        if lang == "en":
            reason = (
                "industry membership uses the latest snapshot "
                f"({pit.classification_as_of}) for a period earlier than its first sync; "
                "may differ from the membership in effect at the time; review-only"
            )
            action = "review-only; verify with point-in-time membership for evaluation"
        else:
            reason = (
                f"行业归属使用最新快照（{pit.classification_as_of}）回溯历史区间，"
                "区间早于成分首次同步日，可能与当时实际归属不符；仅用于历史回看"
            )
            action = "仅用于回看历史；考核用途请以当时时点成分复核"
        missing.append(
            MissingEntry(
                field="classification_as_of",
                code="SECTOR_CLASSIFICATION_LOOKAHEAD",
                reason=reason,
                what_to_do=action,
                severity="info",
            )
        )
    elif pit.stale:
        missing.append(
            MissingEntry(
                field="classification_as_of",
                code="SECTOR_CLASSIFICATION_NOT_PIT",
                reason=f"行业快照日 {pit.classification_as_of.isoformat() if pit.classification_as_of else '?'} 距期末超过 90 天",
                what_to_do="同步最新成分",
            )
        )

    # ── 6. FIFO 重放 ×2 + 公司行动（§5.3 步骤 3-4）────────────────
    bond_alias, _unresolved = build_bond_alias(
        (trade.security_code, trade.security_name) for trade in trades
    )
    canonical = partial(canonical_code, bond_alias)
    # 名称来源：仓库无独立证券主数据表，`security_name` 随账单落在 `ledger.trade`；
    # 按 canonical 代码记最新非空名称（与 nav/dashboard 同口径），缺失即 None。
    code_names: dict[str, str] = {}
    for trade in trades:
        name = _display_name(trade.security_name)
        if name:
            code_names[canonical(trade.security_code)] = name
    res_all = build_rounds(
        trades_to_events([t for t in trades if t.trade_date <= resolved.end], bond_alias=bond_alias)
    )
    res_pre = build_rounds(
        trades_to_events(
            [t for t in trades if t.trade_date < resolved.start], bond_alias=bond_alias
        )
    )
    corporate = corporate_action_symbols(
        trades, t0=resolved.start, t1=resolved.end, canonical=canonical
    )

    # ── 7. 行情（只读缓存；P0 参考日可早于 T0）────────────────────
    universe = {
        canonical(trade.security_code)
        for trade in trades
        if (trade.security_code or "").strip() and not is_money_fund_row(trade)
    }
    universe.discard("CASH")
    universe.discard("")
    price_start = min(resolved.start, window.begin_date) - PRICE_LOOKBACK
    ladder, _market_causes = await load_prices(
        market_data, sorted(universe), price_start, resolved.end, cached_only=True
    )

    # ── 8. 行业映射 / 目录 / 指数日线（§4.1；端口注入，replay 读夹具）─
    memberships_rows = await sector_data.get_memberships(
        _DB_TAXONOMY_INDUSTRY, as_of=pit.classification_as_of, level=1
    )
    memberships = {row.symbol: row.sector_code for row in memberships_rows}
    catalog_rows = await sector_data.get_catalog(_DB_TAXONOMY_INDUSTRY)
    classification_version = catalog_rows[0].classification_version if catalog_rows else None
    sector_codes = sorted({memberships[symbol] for symbol in universe if symbol in memberships})
    bars = await sector_data.get_board_bars(
        [(_DB_TAXONOMY_INDUSTRY, code) for code in sector_codes], price_start, resolved.end
    )
    sector_ladder: dict[str, list[tuple[date, int]]] = {}
    for bar in bars:
        sector_ladder.setdefault(bar.sector_code, []).append((bar.trade_date, bar.close_units))
    for entries in sector_ladder.values():
        entries.sort(key=lambda item: item[0])
    board_bar_as_of = max(
        (entry[0] for entries in sector_ladder.values() for entry in entries), default=None
    )

    # ── 9. 镜像推演 + 股息汇总（资产表同口径）──────────────────────
    mirror = _mirror_view(
        trades,
        begin_date=window.begin_date,
        end_date=window.end_date,
        ladder=ladder,
        canonical=canonical,
        bond_divisors=bond_face_divisors(trades),
        new_stocks={canonical(code) for code in new_stock_pending_codes(trades)},
        begin_balances={
            canonical(code): balance
            for code, balance in initial_money_fund_balances(trades, resolved.start).items()
        },
    )
    dividends: dict[str, int] = {}
    for trade in trades:
        if not (resolved.start <= trade.trade_date <= resolved.end):
            continue
        if category(trade) != "dividend" or is_money_fund_row(trade):
            continue
        code = canonical(trade.security_code)
        if not code or code == "CASH":
            continue
        dividends[code] = dividends.get(code, 0) + trade.amount_units

    # 桥接对账（§3.2.1）：`trading_pnl` 必须与 `asset_daily` 同一成本原语（比例成本），
    # 不能用 API-027 的 FIFO 已实现损益与资产表浮盈亏混算（验收 P0-A）。
    if (
        bridge.status != "blocked"
        and window.unrealized_begin_units is not None
        and window.unrealized_end_units is not None
    ):
        asset_profit = (
            mirror.realized_units
            + bridge.dividends_units
            + bridge.interest_units
            - bridge.fees_units
            + bridge.other_units
        )
        # 货基按余额覆盖（资产口径）：Σ行金额 + Δ余额 即现金流与持仓之间的口径修正项
        # （余额由份额×净值回推，与行金额存在尾差，验收 P0-A 的 40.76 元即此项）。
        mf_amounts = sum(
            trade.amount_units
            for trade in trades
            if is_money_fund_row(trade) and window.begin_date < trade.trade_date <= window.end_date
        )
        mf_delta = sum(end - start for start, end in mirror.money_funds.values())
        asset_profit += mf_amounts + mf_delta
        delta_unreal = window.unrealized_end_units - window.unrealized_begin_units
        if abs(window.total_pnl_units - (asset_profit + delta_unreal)) > _BRIDGE_TOLERANCE_UNITS:
            missing.append(
                MissingEntry(
                    field="total_pnl",
                    code="BRIDGE_MISMATCH",
                    reason="MTM 损益与资产口径桥接式差额超过 1 分（对账断链）",
                    what_to_do="对账页检查断链",
                )
            )

    # ── 10. 段装配 → 残差拆桶 → 五因子分解（§3.3/§3.4/§3.6）────────
    bundle = build_symbol_plans(
        trades=trades,
        t0=resolved.start,
        t1=resolved.end,
        res_all=res_all,
        res_pre=res_pre,
        ladder=ladder,
        memberships=memberships,
        sector_ladder=sector_ladder,
        corporate=corporate,
        canonical=canonical,
        expected_p0_date=latest_workday(resolved.start - timedelta(days=1)),
        expected_p1_date=latest_workday(min(valuation_end_date, resolved.end)),
        dividends=dividends,
        fallback_anchors=mirror.anchors,
    )
    split = split_residual(
        plans=bundle.plans,
        trades=trades,
        t0=resolved.start,
        t1=resolved.end,
        begin_date=window.begin_date,
        end_date=window.end_date,
        bucket_mv=mirror.bucket_mv,
        money_fund_balances=mirror.money_funds,
        holdings_at=mirror.holdings,
        corporate=corporate,
        canonical=canonical,
    )
    all_segments = [segment for plan in bundle.plans for segment in plan.segments]
    decomposition = decompose(
        all_segments,
        window.total_pnl_units,
        extra_fee_units=split.fee_bucketed_units + split.fee_indep_units,
    )
    gap_units = decomposition.residual_units - sum(split.buckets.values())
    assert window.v_end_units is not None
    # 容差 = 期末总资产（`asset_daily.total_assets_units`）× `RESIDUAL_TOLERANCE_PCT`
    # （标度整数分，向下取整）。
    residual_tolerance_units = int(Decimal(window.v_end_units) * RESIDUAL_TOLERANCE_PCT)

    # ── 11. 状态机（§4.4）：存在候选但零可归因段（板块数据全缺）→ blocked
    stock_plans = [plan for plan in bundle.plans if plan.symbol_class == "stock"]
    sector_cause_only = all(
        plan.reason_code in (REASON_MAPPING, REASON_BAR)
        for plan in stock_plans
        if not plan.attributed
    )
    sample = AttributionSample(
        n_segments=decomposition.n_segments,
        n_attributed_segments=decomposition.n_attributed_segments,
        n_rounds=len(res_all.rounds),
        n_symbols=len(bundle.plans),
        min_n=MIN_SAMPLE_SEGMENTS,
        sample_status=(
            "ok" if decomposition.n_attributed_segments >= MIN_SAMPLE_SEGMENTS else "insufficient"
        ),
    )
    if decomposition.n_attributed_segments == 0 and bundle.has_candidate and sector_cause_only:
        missing.append(
            MissingEntry(
                field="factors",
                code="SECTOR_DATA_MISSING",
                reason="存在 A 股候选标的但零个可归因段（行业映射/指数数据全缺）",
                what_to_do="运行 make sync-sectors",
            )
        )
        return _blocked_result(
            period=resolved,
            missing=missing,
            benchmark_id=benchmark_id,
            trades=trades,
            assets=assets,
            asset_data_as_of=asset_data_as_of,
            market_as_of=market_data.as_of,
            sector_as_of=board_bar_as_of or pit.membership_as_of or sector_data.as_of,
            lookahead_used=pit.lookahead_used,
            classification_version=classification_version,
            classification_as_of=pit.classification_as_of,
            beginning_value_source=window.beginning_value_source,
            sector_fingerprint=sector_fingerprint,
            sample=sample,
        )

    # ── 12. coverage / missing 汇总 ───────────────────────────────
    excluded_codes = {plan.security_code for plan in bundle.plans if not plan.attributed}
    base_basis = "mixed" if mirror.cost_fallback & excluded_codes else "market"
    coverage = build_coverage(
        plans=bundle.plans,
        decomposition=decomposition,
        per_symbol=split.per_symbol,
        base_basis=base_basis,
        names=code_names,
    )
    plan_by_code = {plan.security_code: plan for plan in bundle.plans}
    for reason, codes in bundle.excluded_by_reason.items():
        if reason == REASON_MAPPING:
            # 按「是否属于可归因 universe」分级：退市/非在交易 → info；
            # 仍在交易的 A 股缺行业映射 → warning（数据质量缺口）。
            info_codes = [code for code in codes if not plan_by_code[code].in_universe]
            warn_codes = [code for code in codes if plan_by_code[code].in_universe]
        else:
            info_codes, warn_codes = [], list(codes)
        if info_codes:
            entry = _exclusion_missing(reason, info_codes, lang=lang, severity="info")
            if entry is not None:
                missing.append(entry)
        if warn_codes:
            entry = _exclusion_missing(reason, warn_codes, lang=lang)
            if entry is not None:
                missing.append(entry)
    if bundle.price_fallback_symbols:
        missing.append(
            MissingEntry(
                field="factors",
                code="PRICE_REF_FALLBACK",
                reason=f"{len(bundle.price_fallback_symbols)} 个标的的参考价用了 45 天窗口内的前收："
                f"{','.join(sorted(bundle.price_fallback_symbols)[:8])}",
                what_to_do="补行情",
            )
        )
    if bundle.sector_start_fallbacks:
        missing.append(_sector_start_fallback_missing(bundle.sector_start_fallbacks, lang=lang))
    other_sector_fallback = [
        code for code in bundle.sector_fallback_symbols if code not in bundle.sector_start_fallbacks
    ]
    if other_sector_fallback:
        missing.append(
            MissingEntry(
                field="factors",
                code="SECTOR_BAR_FALLBACK",
                reason=f"{len(other_sector_fallback)} 个标的的行业指数参考日与个股参考日不一致："
                f"{','.join(sorted(other_sector_fallback)[:8])}",
                what_to_do="补板块日线",
            )
        )
    residual_entry = _residual_missing(
        unattributed_units=decomposition.residual_units,
        tolerance_units=residual_tolerance_units,
    )
    if residual_entry is not None:
        missing.append(residual_entry)

    status = _aggregate_status(
        missing,
        unattributed_units=decomposition.residual_units,
        tolerance_units=residual_tolerance_units,
    )

    # ── 13. 因子 / 结论 / assumptions / hash ──────────────────────
    factor_amounts: dict[FactorCode, int] = {
        code: decomposition.factors[code] for code in FACTOR_CODES
    }
    ranked_codes = rank_factors(factor_amounts)
    shares = shares_of(factor_amounts)
    total_abs = total_abs_amounts(factor_amounts)
    na_reason = ratio_na_reason(window.total_pnl_units, total_abs)

    factors: list[AttributionFactor] = []
    for code in FACTOR_CODES:
        amount = factor_amounts[code]
        rank = ranked_codes.index(code) + 1
        ratio = ratio_of(amount, window.total_pnl_units) if na_reason is None else None
        share = shares[code]
        factors.append(
            AttributionFactor(
                code=str(code),
                name=FACTOR_NAMES[str(code)],
                amount_units=amount,
                ratio_units=ratio,
                share_units=share,
                rank=rank,
                conclusion=factor_conclusion(amount, lang=lang),
                presentation=factor_presentation(
                    amount_units=amount,
                    ratio_units=ratio,
                    share_units=share,
                    sector_bar_fallback=(
                        str(code) == "sector" and bool(bundle.sector_start_fallbacks)
                    ),
                ),
            )
        )
    # 比率域 residual 是 plug（与金额域同构）：Σratio + residual_ratio == 1e8 精确（§7.2 ③），
    # 逐项 half-up 的尾差由 residual_ratio 吸收。
    residual_ratio = (
        RATIO_SCALE - sum(f.ratio_units for f in factors if f.ratio_units is not None)
        if na_reason is None
        else None
    )
    has_trade_rows = any(
        resolved.start <= trade.trade_date <= resolved.end and category(trade) in {"buy", "sell"}
        for trade in trades
    )
    ranked = [
        (FACTOR_NAMES[str(code)], factor_amounts[code], shares[code]) for code in ranked_codes
    ]
    summary = build_summary(
        total_pnl_units=window.total_pnl_units,
        ranked=ranked,
        residual_units=decomposition.residual_units,
        n_attributed_segments=decomposition.n_attributed_segments,
        capital_total_units=coverage.capital_total_units,
        capital_ratio_units=coverage.capital_ratio_units,
        valuation_gap_units=gap_units,
        has_trade_rows=has_trade_rows,
        lang=lang,
    )
    assert_copy_compliance(summary, *(factor.conclusion for factor in factors))
    summary_na = (
        None
        if summary is not None
        else summary_na_reason(
            total_pnl_units=window.total_pnl_units,
            n_attributed_segments=decomposition.n_attributed_segments,
            capital_total_units=coverage.capital_total_units,
            capital_ratio_units=coverage.capital_ratio_units,
            valuation_gap_units=gap_units,
            lang=lang,
        )
    )

    sector_as_of = board_bar_as_of or pit.membership_as_of or sector_data.as_of
    market_start = min((entries[0][0] for entries in ladder.values() if entries), default=None)
    sector_start = min(
        (entries[0][0] for entries in sector_ladder.values() if entries), default=None
    )
    sector_fallback_marks = {
        "count": len(bundle.sector_start_fallbacks),
        "symbols": sorted(bundle.sector_start_fallbacks),
        "first_bar_date": min(
            (actual for _, actual in bundle.sector_start_fallbacks.values()), default=None
        ),
    }
    assumptions = _assumptions(
        beginning_value_source=window.beginning_value_source,
        classification_version=classification_version,
        classification_as_of=pit.classification_as_of,
        lookahead_used=pit.lookahead_used,
        board_bar_as_of=board_bar_as_of,
        benchmark_id=benchmark_id,
        data_sources={
            "asset_daily": asset_data_as_of,
            "market": market_data.as_of,
            "sector": sector_as_of,
        },
        data_starts={
            "period": resolved.start,
            "asset_daily": asset_first,
            "market": market_start,
            "sector": sector_start,
        },
        sector_bar_fallback=sector_fallback_marks,
        residual_tolerance={
            "amount": str(Money(residual_tolerance_units)),
            "pct": str(_tolerance_pct_ratio()),
            "base": str(Money(window.v_end_units)),
        },
    )
    payload = _hash_payload(
        model="five_factor",
        period=resolved,
        benchmark_id=benchmark_id,
        lookahead_used=pit.lookahead_used,
        trade_count=trade_count,
        trade_updated=trade_updated,
        asset_first=asset_first,
        asset_last=asset_last,
        asset_rows=asset_rows,
        asset_rebuilt=asset_rebuilt,
        market_as_of=market_data.as_of,
        sector=sector_fingerprint,
    )
    as_of_candidates = [
        d for d in (asset_data_as_of, market_data.as_of, sector_as_of) if d is not None
    ]
    breakdown = {
        BUCKET_CORPORATE: split.buckets[BUCKET_CORPORATE],
        BUCKET_UNATTRIBUTED: split.buckets[BUCKET_UNATTRIBUTED],
        BUCKET_DIVIDEND: split.buckets[BUCKET_DIVIDEND],
        BUCKET_INTEREST: split.buckets[BUCKET_INTEREST],
        BUCKET_OTHER: split.buckets[BUCKET_OTHER],
        BUCKET_GAP: gap_units,
    }
    # 对账行（§4.4）：标的合计 + 账户级桶合计 = residual（后端派生，前端零口径）。
    reconciliation = _residual_reconciliation(
        split=split,
        coverage=coverage,
        gap_units=gap_units,
        residual_units=decomposition.residual_units,
    )
    return AttributionResult(
        model="five_factor",
        ratio_basis="amount/total_pnl",
        period=resolved.label,
        period_start=resolved.start,
        period_end=resolved.end,
        status=status,
        definition_version=ATTRIBUTION_DEFINITION_VERSION,
        total_pnl_basis="asset_daily_mtm",
        total_pnl_units=window.total_pnl_units,
        ratio_na_reason=na_reason,
        base_capital_units=decomposition.base_capital_units,
        factors=tuple(factors),
        summary=summary,
        summary_na_reason=summary_na,
        residual_units=decomposition.residual_units,
        residual_ratio_units=residual_ratio,
        residual_breakdown=breakdown,
        sample=sample,
        coverage=coverage,
        missing=tuple(missing),
        assumptions=assumptions,
        data_as_of=min(as_of_candidates) if as_of_candidates else None,
        inputs_hash=inputs_hash(payload),
        links={
            "dashboard": "/dashboard",
            "sectors": "/sectors",
            "rounds": "/rounds",
            "reports": "/reports",
        },
        residual_reconciliation=reconciliation,
    )
