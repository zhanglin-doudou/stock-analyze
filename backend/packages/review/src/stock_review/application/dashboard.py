"""Dashboard 聚合视图（Period2-12 / API-084）：资产概览 + 历史概览。

口径（docs/architecture/2026-10-28-dashboard.md）：
* 资产概览 headline = 最新 ``asset_daily`` 行（``trade_date <= today``），**不自动构建**；
* 历史概览 = 全历史：已平仓回合计数/最赚最亏 + ``asset_daily`` 日收益的回撤；
* 旧五卡是月度口径，这里只返回 ``legacy_period``（最近有流水/回合的月份），不内嵌指标；
* 所有金额/比率保持标度整数，格式化只在 API 边界做；blocked/missing 一律 ``None``，
  绝不用 0 冒充缺失。

本模块只读（不写表、不触发 job）；时间只来自注入的 ``Clock``。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, cast

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_core.stats.drawdown import DrawdownFamily, drawdown_family
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository

from stock_review.application.assets import (
    STATE_BLOCKED,
    STATE_MISSING,
    STATE_STALE,
    get_asset_build_state,
)
from stock_review.application.bridge import bridge_for_period
from stock_review.application.valuation import (
    category,
    close_on_or_before,
    holding_delta,
    load_prices,
)
from stock_review.metrics.defs._common import fee_breakdown
from stock_review.metrics.views import TradeView
from stock_review.models import AssetDaily, Round
from stock_review.repositories.assets import AssetRepository
from stock_review.repositories.rounds import RoundRepository

if TYPE_CHECKING:
    from stock_platform.ports import MarketDataSource

DASHBOARD_VERSION = "1.0"
HOLDINGS_MAX = 200
MDD_MIN_OBS = 60
# 当前持仓估值只看最近 45 天行情（与资产表安全窗同源）。
PRICE_WINDOW_DAYS = 45

STATUS_BLOCKED = "blocked"
STATUS_MISSING = "missing"
STATUS_INSUFFICIENT = "insufficient"
STATUS_STALE = "stale"
STATUS_DEGRADED = "degraded"
STATUS_OK = "ok"

# 页面状态优先级：blocked > missing > insufficient > stale > degraded > ok。
_STATUS_PRIORITY: dict[str, int] = {
    STATUS_BLOCKED: 0,
    STATUS_MISSING: 1,
    STATUS_INSUFFICIENT: 2,
    STATUS_STALE: 3,
    STATUS_DEGRADED: 4,
    STATUS_OK: 5,
}

# 缺失项错误码（前端/验收逐项断言；未知码按文案展示）。
MISSING_BUILD = "DASHBOARD_ASSET_MISSING"
MISSING_BLOCKED = "DASHBOARD_ASSET_BLOCKED"
MISSING_STALE = "DASHBOARD_STALE"
MISSING_TOTAL_PROFIT = "DASHBOARD_TOTAL_PROFIT_UNAVAILABLE"
MISSING_MAX_INVESTED = "DASHBOARD_MAX_INVESTED_COST_MISSING"
MISSING_MDD_INSUFFICIENT = "DASHBOARD_MDD_INSUFFICIENT"
MISSING_MDD_UNRELIABLE = "DASHBOARD_MDD_UNRELIABLE"
MISSING_MDD_GAPS = "DASHBOARD_MDD_GAPS"
MISSING_HOLDINGS_TRUNCATED = "DASHBOARD_HOLDINGS_TRUNCATED"
MISSING_HOLDING_PRICE = "DASHBOARD_HOLDING_PRICE_MISSING"
MISSING_HOLDINGS_MISMATCH = "DASHBOARD_HOLDINGS_MISMATCH"
MISSING_NO_CLOSED_ROUNDS = "DASHBOARD_NO_CLOSED_ROUNDS"


@dataclass(slots=True, frozen=True)
class DashboardMissing:
    """一个缺失/降级字段（``null`` 的机读原因）。"""

    field: str
    code: str
    reason: str
    what_to_do: str


@dataclass(slots=True, frozen=True)
class DashboardHolding:
    """当前持仓（证券维度；现金不在列表内）。"""

    security_code: str
    security_name: str
    quantity_units: int
    cost_units: int
    market_value_units: int | None
    weight_ratio_units: int | None


@dataclass(slots=True, frozen=True)
class DashboardRoundBrief:
    """最赚/最亏列表的一行。"""

    round_id: str
    security_code: str
    security_name: str
    close_date: date
    pnl_units: int
    return_rate_units: int
    holding_days: int


@dataclass(slots=True, frozen=True)
class DashboardMaxDrawdown:
    """全历史最大回撤区间（有效段内；blocked/degraded 不跨断链拼接）。"""

    from_date: date | None
    to_date: date | None
    duration_days: int | None
    duration_obs: int | None
    recovery_days: int | None
    recovery_censored: bool
    mdd_units: int | None
    n_obs: int
    sample_status: str
    reason: str | None


@dataclass(slots=True, frozen=True)
class DashboardAssetOverview:
    status: str
    holdings: list[DashboardHolding]
    market_value_units: int | None
    cash_units: int | None
    total_assets_units: int | None
    position_ratio_units: int | None
    total_profit_units: int | None
    max_invested_cost_units: int | None
    max_cost_return_ratio_units: int | None
    total_fees_units: int | None


@dataclass(slots=True, frozen=True)
class DashboardHistoryOverview:
    status: str
    closed_security_count: int
    closed_round_count: int
    win_count: int
    loss_count: int
    flat_count: int
    win_ratio_units: int | None
    loss_ratio_units: int | None
    flat_ratio_units: int | None
    max_profit_units: int | None
    max_profit_round: DashboardRoundBrief | None
    max_profit_cost_ratio_units: int | None
    top_rounds: list[DashboardRoundBrief]
    bottom_rounds: list[DashboardRoundBrief]
    max_drawdown: DashboardMaxDrawdown


@dataclass(slots=True, frozen=True)
class DashboardResult:
    """API-084 应用层结果（全部标度整数/日期）。"""

    as_of: date
    data_as_of: date | None
    legacy_period: str
    status: str
    build_state: str
    definition_version: str
    asset: DashboardAssetOverview
    history: DashboardHistoryOverview
    missing: list[DashboardMissing]
    inputs_hash: str
    assumptions: dict[str, str] = field(default_factory=dict)


def _ratio_units(numerator: int, denominator: int) -> int | None:
    """标度比率（scale=8，四舍五入）；分母 <= 0 → None（不伪造 0）。"""
    if denominator <= 0:
        return None
    value = (Decimal(numerator) / Decimal(denominator)).scaleb(8)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _overall_status(*statuses: str) -> str:
    return min(statuses, key=lambda item: _STATUS_PRIORITY.get(item, 99))


def _legacy_period(trades: list[Trade], rounds: list[Round], clock: Clock) -> str:
    """最近有流水/回合的月份（demo=2026-06）；无任何数据时回退当前月。"""
    candidates: list[date] = [trade.trade_date for trade in trades]
    candidates.extend(
        round_.close_date or round_.open_date
        for round_ in rounds
        if (round_.close_date or round_.open_date) is not None
    )
    latest = max(candidates) if candidates else clock.today()
    return f"{latest.year:04d}-{latest.month:02d}"


def _round_brief(round_: Round) -> DashboardRoundBrief:
    return DashboardRoundBrief(
        round_id=round_.id,
        security_code=round_.security_code,
        security_name=round_.security_name,
        close_date=round_.close_date or round_.open_date,
        pnl_units=round_.pnl_units,
        return_rate_units=round_.return_rate_units,
        holding_days=round_.holding_days,
    )


async def _holdings_at(
    trades: list[Trade],
    *,
    as_of: date,
    market_data: MarketDataSource,
) -> tuple[list[DashboardHolding], list[str], bool]:
    """按资产表同一成本模型（移动平均）重建 ``as_of`` 收盘持仓。

    返回 ``(holdings, 缺价标的, 是否截断)``；成本与 ``asset_daily.holding_cost`` 同源：
    买入/配股/打新累加 ``-amount``，卖出按数量比例结转。
    """
    holdings: dict[str, int] = {}
    cost_basis: dict[str, int] = {}
    names: dict[str, str] = {}

    for trade in trades:
        if trade.trade_date > as_of:
            break
        code = (trade.security_code or "").strip()
        if not code:
            continue
        trade_category = category(trade)
        delta = holding_delta(trade_category, trade.quantity_units)
        if delta:
            holdings[code] = holdings.get(code, 0) + delta
            names[code] = trade.security_name or code
        if trade_category in {"buy", "rights", "new_stock"} and trade.amount_units != 0:
            cost_basis[code] = cost_basis.get(code, 0) + (-trade.amount_units)
        elif trade_category == "sell":
            old_qty = holdings.get(code, 0) - delta
            if old_qty > 0 and delta < 0:
                old_cost = cost_basis.get(code, 0)
                cost_basis[code] = max(0, old_cost + (old_cost * delta) // old_qty)

    held = {code: qty for code, qty in holdings.items() if qty != 0}
    if not held:
        return [], [], False

    symbols = sorted(held)
    ladder, _causes = await load_prices(
        market_data,
        symbols,
        as_of - timedelta(days=PRICE_WINDOW_DAYS),
        as_of,
    )

    rows: list[tuple[int, DashboardHolding]] = []
    missing_prices: list[str] = []
    for code in symbols:
        quantity = held[code]
        close = close_on_or_before(ladder, code, as_of)
        if close is None:
            missing_prices.append(code)
            market_value = None
        else:
            market_value = quantity * close // 100_000_000
        rows.append(
            (
                market_value if market_value is not None else -1,
                DashboardHolding(
                    security_code=code,
                    security_name=names.get(code, code),
                    quantity_units=quantity,
                    cost_units=max(0, cost_basis.get(code, 0)),
                    market_value_units=market_value,
                    weight_ratio_units=None,
                ),
            )
        )

    # 持仓权重：mv / Σmv（行内和 100%），与 position_pct（mv/total_assets）分开。
    total_mv = sum(item.market_value_units or 0 for _, item in rows)
    weighted: list[DashboardHolding] = []
    for _, item in rows:
        weight = (
            _ratio_units(item.market_value_units, total_mv)
            if item.market_value_units is not None
            else None
        )
        weighted.append(
            DashboardHolding(
                security_code=item.security_code,
                security_name=item.security_name,
                quantity_units=item.quantity_units,
                cost_units=item.cost_units,
                market_value_units=item.market_value_units,
                weight_ratio_units=weight,
            )
        )

    weighted.sort(key=lambda item: (-(item.market_value_units or 0), item.security_code))
    truncated = len(weighted) > HOLDINGS_MAX
    return weighted[:HOLDINGS_MAX], missing_prices, truncated


def _drawdown_segments(
    series: list[AssetDaily],
) -> tuple[list[tuple[list[date], list[int]]], bool, int]:
    """把资产序列切成连续有效段：``(segments, has_gaps, valid_obs)``。

    只接受 ``status=ok`` 且 ``daily_return`` 非 NULL；blocked/degraded/NULL 行以及
    日期不连续（缺一天）都会断链，绝不跨断链拼接。
    """
    segments: list[tuple[list[date], list[int]]] = []
    dates: list[date] = []
    returns: list[int] = []
    has_gaps = False
    previous: date | None = None
    valid_obs = 0

    for row in series:
        usable = row.status == "ok" and row.daily_return_units is not None
        if not usable:
            has_gaps = True
            if dates:
                segments.append((dates, returns))
                dates, returns = [], []
            previous = None
            continue
        if previous is not None and row.trade_date != previous + timedelta(days=1):
            has_gaps = True
            if dates:
                segments.append((dates, returns))
                dates, returns = [], []
        dates.append(row.trade_date)
        returns.append(int(row.daily_return_units or 0))
        valid_obs += 1
        previous = row.trade_date
    if dates:
        segments.append((dates, returns))
    return segments, has_gaps, valid_obs


def _max_drawdown(
    series: list[AssetDaily],
) -> tuple[DashboardMaxDrawdown, list[DashboardMissing], bool]:
    """全历史最大回撤 + 缺失项；返回 ``(结果, missing, has_gaps)``。"""
    segments, has_gaps, valid_obs = _drawdown_segments(series)

    if valid_obs == 0:
        return (
            DashboardMaxDrawdown(
                from_date=None,
                to_date=None,
                duration_days=None,
                duration_obs=None,
                recovery_days=None,
                recovery_censored=False,
                mdd_units=None,
                n_obs=0,
                sample_status=STATUS_BLOCKED,
                reason="没有可用的有效日收益（资产表未构建或全部阻断）",
            ),
            [
                DashboardMissing(
                    field="history_overview.max_drawdown",
                    code=MISSING_MDD_INSUFFICIENT,
                    reason="没有有效日收益，无法计算历史回撤。",
                    what_to_do="先到资产页构建资产表，并修复阻断日期。",
                )
            ],
            has_gaps,
        )

    if valid_obs < MDD_MIN_OBS:
        return (
            DashboardMaxDrawdown(
                from_date=None,
                to_date=None,
                duration_days=None,
                duration_obs=None,
                recovery_days=None,
                recovery_censored=False,
                mdd_units=None,
                n_obs=valid_obs,
                sample_status=STATUS_INSUFFICIENT,
                reason=f"有效观测 {valid_obs} 天 < {MDD_MIN_OBS} 天门槛",
            ),
            [
                DashboardMissing(
                    field="history_overview.max_drawdown",
                    code=MISSING_MDD_INSUFFICIENT,
                    reason=f"有效观测仅 {valid_obs} 天，不足 {MDD_MIN_OBS} 天，不冒充历史回撤。",
                    what_to_do="补充更长历史账单或等待资产表积累更多交易日。",
                )
            ],
            has_gaps,
        )

    best: tuple[int, list[date], list[int], DrawdownFamily] | None = None
    for dates, returns in segments:
        if len(returns) < 2:
            continue
        family = drawdown_family(returns, natural_days=len(returns))
        if family.mdd_units is None:
            continue
        if best is None or family.mdd_units > best[0]:
            best = (family.mdd_units, dates, returns, family)

    if best is None:
        return (
            DashboardMaxDrawdown(
                from_date=None,
                to_date=None,
                duration_days=None,
                duration_obs=None,
                recovery_days=None,
                recovery_censored=False,
                mdd_units=None,
                n_obs=valid_obs,
                sample_status=STATUS_INSUFFICIENT,
                reason="有效段太短，无法计算回撤",
            ),
            [
                DashboardMissing(
                    field="history_overview.max_drawdown",
                    code=MISSING_MDD_INSUFFICIENT,
                    reason="有效段太短，无法计算回撤。",
                    what_to_do="补充更长历史账单后重试。",
                )
            ],
            has_gaps,
        )

    mdd_units, dates, _returns, family = best

    # index[0] 是首个观测前的高点（无对应日期）；其余 index[i] → dates[i-1]。
    def date_at(index: int | None) -> date | None:
        if index is None or index <= 0:
            return None
        return dates[index - 1] if index - 1 < len(dates) else None

    peak_date = date_at(family.peak_index)
    trough_date = date_at(family.trough_index)
    recovery_date = date_at(family.recovery_index)
    duration_days = (trough_date - peak_date).days if peak_date and trough_date else None
    recovery_days = (recovery_date - trough_date).days if recovery_date and trough_date else None

    missing: list[DashboardMissing] = []
    if has_gaps:
        missing.append(
            DashboardMissing(
                field="history_overview.max_drawdown",
                code=MISSING_MDD_GAPS,
                reason="历史资产序列存在阻断/降级/缺日，回撤按最长连续有效段计算。",
                what_to_do="修复阻断日期后重新构建资产表可得到完整回撤。",
            )
        )

    if family.unreliable:
        return (
            DashboardMaxDrawdown(
                from_date=peak_date,
                to_date=trough_date,
                duration_days=duration_days,
                duration_obs=family.mdd_duration_obs,
                recovery_days=recovery_days,
                recovery_censored=family.recovery_censored,
                mdd_units=None,
                n_obs=valid_obs,
                sample_status="unreliable",
                reason="存在 |日收益| > 100% 的异常行，回撤不可信",
            ),
            [
                *missing,
                DashboardMissing(
                    field="history_overview.max_drawdown.mdd_pct",
                    code=MISSING_MDD_UNRELIABLE,
                    reason="存在异常日收益（|r|>100%），回撤不可信。",
                    what_to_do="检查超卖/负市值等数据错误后重新构建资产表。",
                ),
            ],
            has_gaps,
        )

    return (
        DashboardMaxDrawdown(
            from_date=peak_date,
            to_date=trough_date,
            duration_days=duration_days,
            duration_obs=family.mdd_duration_obs,
            recovery_days=recovery_days,
            recovery_censored=family.recovery_censored,
            mdd_units=mdd_units,
            n_obs=valid_obs,
            sample_status=STATUS_OK,
            reason=None,
        ),
        missing,
        has_gaps,
    )


async def build_dashboard(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
) -> DashboardResult:
    """组装 Dashboard（纯读；无任何写操作/job）。"""
    today = clock.today()
    build_view, definition_version = await get_asset_build_state(
        session, user_id=user_id, clock=clock, market_data=market_data
    )

    asset_repo = AssetRepository(session)
    latest = await asset_repo.latest(
        user_id, definition_version=definition_version, on_or_before=today
    )
    series = await asset_repo.series(user_id, definition_version=definition_version)
    trades = list(
        await TradeRepository(session).balances_ordered(
            user_id, period_end=latest.trade_date if latest is not None else today
        )
    )
    rounds = await RoundRepository(session).closed_rounds(user_id)

    as_of = latest.trade_date if latest is not None else today
    legacy_period = _legacy_period(trades, rounds, clock)
    missing: list[DashboardMissing] = []

    # ── 资产概览 ─────────────────────────────────────────────
    asset_blocked = build_view.state in {STATE_MISSING, STATE_BLOCKED} or (
        latest is not None and latest.status == "blocked"
    )
    if build_view.state == STATE_MISSING:
        missing.append(
            DashboardMissing(
                field="asset_overview",
                code=MISSING_BUILD,
                reason="资产表尚未构建，没有当前持仓/资产快照。",
                what_to_do="到资产页构建资产表（或先导入账单）。",
            )
        )
    elif build_view.state == STATE_BLOCKED:
        missing.append(
            DashboardMissing(
                field="asset_overview",
                code=MISSING_BLOCKED,
                reason=build_view.status_reason or "资产表被阻断：缺少期初锚点或行情。",
                what_to_do="到资产页查看阻断原因并修复后重新构建。",
            )
        )
    elif latest is None:
        missing.append(
            DashboardMissing(
                field="asset_overview",
                code=MISSING_BUILD,
                reason="没有可用资产快照。",
                what_to_do="到资产页构建资产表。",
            )
        )

    max_invested: int | None = None
    for row in series:
        if row.status == "blocked":
            continue
        if max_invested is None or row.holding_cost_units > max_invested:
            max_invested = row.holding_cost_units
    if max_invested is None:
        missing.append(
            DashboardMissing(
                field="asset_overview.max_invested_cost",
                code=MISSING_MAX_INVESTED,
                reason="没有非阻断的持仓成本记录。",
                what_to_do="构建资产表并修复阻断日期。",
            )
        )

    if asset_blocked or latest is None:
        holdings: list[DashboardHolding] = []
        market_value_units = None
        cash_units = None
        total_assets_units = None
        position_ratio_units = None
    else:
        holdings, missing_prices, truncated = await _holdings_at(
            trades, as_of=latest.trade_date, market_data=market_data
        )
        market_value_units = latest.market_value_units
        cash_units = latest.cash_units
        total_assets_units = latest.total_assets_units
        position_ratio_units = _ratio_units(market_value_units, total_assets_units)
        if truncated:
            missing.append(
                DashboardMissing(
                    field="asset_overview.holdings",
                    code=MISSING_HOLDINGS_TRUNCATED,
                    reason=f"持仓超过 {HOLDINGS_MAX} 行，已按市值截断。",
                    what_to_do="到资产页查看或导出完整持仓。",
                )
            )
        if missing_prices:
            missing.append(
                DashboardMissing(
                    field="asset_overview.holdings.market_value",
                    code=MISSING_HOLDING_PRICE,
                    reason="部分持仓缺少行情：" + "、".join(missing_prices),
                    what_to_do="检查行情数据覆盖后重新构建资产表。",
                )
            )
        holdings_total = sum(item.market_value_units or 0 for item in holdings)
        if not missing_prices and abs(holdings_total - market_value_units) > 1:
            missing.append(
                DashboardMissing(
                    field="asset_overview.holdings",
                    code=MISSING_HOLDINGS_MISMATCH,
                    reason="持仓市值合计与最新资产快照不一致。",
                    what_to_do="到资产页重新构建资产表后再刷新。",
                )
            )

    total_fees_units: int | None = None
    if not asset_blocked:
        trade_slice = [trade for trade in trades if trade.trade_date <= as_of]
        total_fees_units = fee_breakdown(cast("list[TradeView]", trade_slice)).fee_amount

    total_profit_units: int | None = None
    if not asset_blocked and latest is not None and trades:
        bridge = await bridge_for_period(
            session,
            user_id=user_id,
            period_start=trades[0].trade_date,
            period_end=latest.trade_date,
            clock=clock,
        )
        bridge_ok = (
            bridge.status == STATUS_OK
            and bridge.balanced
            and abs(bridge.residual_units) <= bridge.tolerance_units
        )
        if bridge_ok and build_view.state != STATE_STALE:
            total_profit_units = (
                latest.total_assets_units
                - bridge.initial_cash_units
                - bridge.initial_open_cost_units
                - bridge.cashflows_units
                - bridge.other_units
            )
    if total_profit_units is None:
        missing.append(
            DashboardMissing(
                field="asset_overview.total_profit",
                code=MISSING_TOTAL_PROFIT,
                reason="全历史桥接不平或资产表过期，不硬算总收益。",
                what_to_do="到资产页更新资产表；若仍不平，到对账页检查断链。",
            )
        )

    max_cost_return = (
        _ratio_units(total_profit_units, max_invested)
        if total_profit_units is not None and max_invested
        else None
    )

    if build_view.state == STATE_STALE:
        missing.append(
            DashboardMissing(
                field="asset_overview",
                code=MISSING_STALE,
                reason="有新流水/新行情，当前快照非最新。",
                what_to_do="到资产页更新资产表。",
            )
        )

    degraded_asset = any(row.status != "ok" for row in series) or any(
        item.code in {MISSING_HOLDINGS_MISMATCH, MISSING_TOTAL_PROFIT} for item in missing
    )
    if build_view.state == STATE_BLOCKED or (latest is not None and latest.status == "blocked"):
        asset_status = STATUS_BLOCKED
    elif build_view.state == STATE_MISSING or latest is None:
        asset_status = STATUS_MISSING
    elif build_view.state == STATE_STALE:
        asset_status = STATUS_STALE
    elif degraded_asset:
        asset_status = STATUS_DEGRADED
    else:
        asset_status = STATUS_OK

    # ── 历史概览 ─────────────────────────────────────────────
    closed_count = len(rounds)
    win_count = sum(1 for round_ in rounds if round_.pnl_units > 0)
    loss_count = sum(1 for round_ in rounds if round_.pnl_units < 0)
    flat_count = closed_count - win_count - loss_count
    win_ratio = _ratio_units(win_count, closed_count) if closed_count else None
    loss_ratio = _ratio_units(loss_count, closed_count) if closed_count else None
    flat_ratio = _ratio_units(flat_count, closed_count) if closed_count else None
    if closed_count == 0:
        missing.append(
            DashboardMissing(
                field="history_overview",
                code=MISSING_NO_CLOSED_ROUNDS,
                reason="暂无已平仓回合。",
                what_to_do="先导入账单并构建回合。",
            )
        )

    def sort_key(round_: Round) -> tuple[int, date, str]:
        return (-round_.pnl_units, round_.close_date or round_.open_date, round_.id)

    ranked = sorted(rounds, key=sort_key)
    max_profit_round = _round_brief(ranked[0]) if ranked else None
    max_profit = ranked[0].pnl_units if ranked else None
    top_rounds = [_round_brief(item) for item in ranked[:3]]
    bottom_rounds = [
        _round_brief(item)
        for item in sorted(rounds, key=lambda r: (r.pnl_units, r.close_date or r.open_date, r.id))[
            :3
        ]
    ]
    max_profit_cost = (
        _ratio_units(max_profit, max_invested) if max_profit is not None and max_invested else None
    )

    max_drawdown, mdd_missing, mdd_gaps = _max_drawdown(series)
    missing.extend(mdd_missing)

    if not series and not rounds:
        history_status = STATUS_MISSING
    elif max_drawdown.sample_status in {STATUS_INSUFFICIENT, "unreliable"} or mdd_gaps:
        history_status = STATUS_DEGRADED
    else:
        history_status = STATUS_OK

    overall = _overall_status(asset_status, history_status)
    if max_drawdown.sample_status == STATUS_INSUFFICIENT:
        overall = _overall_status(overall, STATUS_INSUFFICIENT)

    inputs_hash = _inputs_hash(
        as_of=as_of,
        data_as_of=build_view.data_as_of,
        definition_version=definition_version,
        latest_id=latest.id if latest is not None else None,
        closed_count=closed_count,
        closed_pnl=sum(round_.pnl_units for round_ in rounds),
        trade_fingerprint=(len(trades), sum(trade.version for trade in trades)),
    )

    return DashboardResult(
        as_of=as_of,
        data_as_of=build_view.data_as_of,
        legacy_period=legacy_period,
        status=overall,
        build_state=build_view.state,
        definition_version=definition_version,
        asset=DashboardAssetOverview(
            status=asset_status,
            holdings=holdings,
            market_value_units=market_value_units,
            cash_units=cash_units,
            total_assets_units=total_assets_units,
            position_ratio_units=position_ratio_units,
            total_profit_units=total_profit_units,
            max_invested_cost_units=max_invested,
            max_cost_return_ratio_units=max_cost_return,
            total_fees_units=total_fees_units,
        ),
        history=DashboardHistoryOverview(
            status=history_status,
            closed_security_count=len({round_.security_code for round_ in rounds}),
            closed_round_count=closed_count,
            win_count=win_count,
            loss_count=loss_count,
            flat_count=flat_count,
            win_ratio_units=win_ratio,
            loss_ratio_units=loss_ratio,
            flat_ratio_units=flat_ratio,
            max_profit_units=max_profit,
            max_profit_round=max_profit_round,
            max_profit_cost_ratio_units=max_profit_cost,
            top_rounds=top_rounds,
            bottom_rounds=bottom_rounds,
            max_drawdown=max_drawdown,
        ),
        missing=missing,
        inputs_hash=inputs_hash,
        assumptions={
            "calendar": "weekday_proxy",
            "cost_model": "moving_average",
            "total_profit_basis": "full_history_bridge",
            "mdd": "longest_valid_segment",
        },
    )


def _inputs_hash(
    *,
    as_of: date,
    data_as_of: date | None,
    definition_version: str,
    latest_id: str | None,
    closed_count: int,
    closed_pnl: int,
    trade_fingerprint: tuple[int, int],
) -> str:
    payload = {
        "schema_version": DASHBOARD_VERSION,
        "as_of": as_of.isoformat(),
        "data_as_of": data_as_of.isoformat() if data_as_of is not None else None,
        "definition_version": definition_version,
        "latest_id": latest_id,
        "closed_count": closed_count,
        "closed_pnl": closed_pnl,
        "trade_count": trade_fingerprint[0],
        "trade_version_sum": trade_fingerprint[1],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "DASHBOARD_VERSION",
    "DashboardAssetOverview",
    "DashboardHistoryOverview",
    "DashboardHolding",
    "DashboardMaxDrawdown",
    "DashboardMissing",
    "DashboardResult",
    "DashboardRoundBrief",
    "build_dashboard",
]
