"""净值与收益应用层（Period2-03 / API-032）：ledger 明细 → 每日 NAV → 落库。

口径（INTERFACE §3.7 / PRD FR-R2.3）：
- 每日总资产 = 现金余额 + Σ(持仓数量 × 当日收盘价)；
- 外部现金流 = `category=transfer` 的 amount 之和（入金正、出金负）；
- 份额法 TWR：shares_0=1，NAV_t = (V_t - CF_t) / shares_{t-1}，Δshares = CF_t / NAV_t；
- 期初资产用 bridge 的 `_initial_cash`（区间前最后余额 / 首行反推）+ 期初持仓市值；
- 阻断条件（任一即当日 `blocked`）：对账断链、缺行情数据、无期初资产锚点；
- FIFO `data_error` 降级为 `degraded`（仍输出资产值，但 status 降级）。

本模块不读系统时钟，时间一律从 `Clock` 注入。
"""

from __future__ import annotations

import bisect
import hashlib
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError
from stock_core.fifo import build_rounds
from stock_core.nav import CashflowPoint, NavSeries, build_nav_series, compute_xirr
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository
from stock_platform.ports import Bar, MarketDataSource

from stock_review.application.bridge import (
    _category,
    _initial_cash,
    _is_cash_balance,
)
from stock_review.application.rebuild import trades_to_events
from stock_review.models import NavDaily

# 持仓影响类：这些 category 会改变持仓数量。
_HOLDING_CATEGORIES: frozenset[str] = frozenset({"buy", "sell", "bonus", "rights", "new_stock"})
# 外部现金流：transfer（银证转账）。
_CASHFLOW_CATEGORIES: frozenset[str] = frozenset({"transfer"})
# 数量标度（scale=4）× 价格标度（scale=6）→ 金额标度（scale=2）需除 1e8。
_QTY_PRICE_SCALE = 10**8
# 取数回看窗口：期初基准日需要「当日或之前最近收盘」，假期/周末需往前找。
_PRICE_LOOKBACK = timedelta(days=45)


@dataclass(slots=True)
class NavComputeResult:
    """一次 NAV 计算的结果（落库前的内存表示）。"""

    series: NavSeries | None
    mwr_units: int
    blocked_reasons: list[str]
    degraded: bool
    holdings_by_date: dict[date, list[_HoldingSnapshot]]


@dataclass(slots=True)
class _DailyState:
    """逐日推演时的中间状态。"""

    cash_units: int
    holdings: dict[str, int]  # security_code → quantity_units (scale=4)
    cost_basis: dict[str, int]  # security_code → 累计成本 (scale=2)
    cashflow_units: int  # 当日外部现金流（scale=2）


@dataclass(slots=True)
class _HoldingSnapshot:
    """单只标的在某日的持仓快照（供 API 响应组装，不落库）。"""

    security_code: str
    security_name: str
    quantity_units: int  # scale=4
    cost_units: int  # 累计成本 (scale=2)，=0 且 qty>0 表示纯送转
    close_units: int  # 当日或之前最近收盘价 (scale=6)
    market_value_units: int  # 市值 (scale=2)


def _nav_id(user_id: uuid.UUID, trade_date: date) -> str:
    """确定性主键：`nav_` + sha256(user_id|trade_date)[:32]。"""
    digest = hashlib.sha256(f"{user_id}|{trade_date.isoformat()}".encode()).hexdigest()
    return f"nav_{digest[:32]}"


def _holding_delta(category: str, quantity_units: int) -> int:
    """单笔 trade 对持仓数量的增量（buy/bonus/rights/new_stock 加，sell 减）。"""
    if category in {"buy", "bonus", "rights", "new_stock"}:
        return quantity_units
    if category == "sell":
        return -quantity_units
    return 0


def _group_trades_by_date(trades: list[Trade]) -> dict[date, list[Trade]]:
    """按 trade_date 分组（trades 已按日期升序）。"""
    grouped: dict[date, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.trade_date].append(trade)
    return grouped


def _closing_prices(bars: Sequence[Bar], symbols: list[str]) -> dict[str, list[tuple[date, int]]]:
    """Bar 序列 → {symbol: [(trade_date, close_units)] 升序}。

    用「价格阶梯」而非精确日期查表：非交易日（周末/节假日/月末口径）取**当日或之前
    最近一个收盘价**，否则任何非交易日的净值都会因「当日无 bar」被误判阻断。
    """
    ladder: dict[str, list[tuple[date, int]]] = {symbol: [] for symbol in symbols}
    for bar in bars:
        ladder.setdefault(bar.symbol, []).append((bar.trade_date, bar.close_units))
    for entries in ladder.values():
        entries.sort(key=lambda item: item[0])
    return ladder


def _close_on_or_before(
    ladder: dict[str, list[tuple[date, int]]], symbol: str, trade_date: date
) -> int | None:
    """该标的在 trade_date 当日或之前最近一个收盘价（scale=6）；无则 None。"""
    entries = ladder.get(symbol)
    if not entries:
        return None
    index = bisect.bisect_right([d for d, _ in entries], trade_date)
    if index == 0:
        return None
    return entries[index - 1][1]


def _total_market_value(
    holdings: dict[str, int],
    ladder: dict[str, list[tuple[date, int]]],
    trade_date: date,
) -> tuple[int, list[str]]:
    """当日持仓市值（scale=2）+ 缺失行情的标的列表。

    市值 = Σ qty_units * close_units / 1e8（qty scale=4 × price scale=6 → scale=2）。
    """
    total = 0
    missing: list[str] = []
    for symbol, qty in holdings.items():
        if qty == 0:
            continue
        close = _close_on_or_before(ladder, symbol, trade_date)
        if close is None:
            missing.append(symbol)
            continue
        total += qty * close // _QTY_PRICE_SCALE
    return total, missing


# 行情失败类别（适配器写在 `ApiError.metadata["market_failure"]`；D2=A：不新增错误码）。
_MARKET_FAILURE_KEY = "market_failure"
_MARKET_FAILURE_LABELS: dict[str, str] = {
    "unavailable": "（行情源暂时不可用，可重试）",
    "not_supported": "（标的不在支持范围，仅支持 A 股主板/科创/创业）",
    "no_data": "（该区间无行情，可能未上市/停牌）",
}
_MARKET_FAILURE_PRIORITY: tuple[str, ...] = ("unavailable", "not_supported", "no_data")


def _market_failure_cause(exc: ApiError) -> str:
    raw = exc.metadata.get(_MARKET_FAILURE_KEY)
    return raw if isinstance(raw, str) and raw in _MARKET_FAILURE_LABELS else ""


def _blocked_reason(prefix: str, symbols: list[str], causes: dict[str, str]) -> str:
    """`缺行情数据：000651` + 三类可读后缀（不可用可重试 / 不在支持范围 / 无行情）。"""
    base = f"{prefix}：{','.join(sorted(symbols))}"
    present = {causes[symbol] for symbol in symbols if symbol in causes}
    for cause in _MARKET_FAILURE_PRIORITY:
        if cause in present:
            return base + _MARKET_FAILURE_LABELS[cause]
    return base


async def _load_prices(
    market_data: MarketDataSource,
    symbols: list[str],
    start: date,
    end: date,
) -> tuple[dict[str, list[tuple[date, int]]], dict[str, str]]:
    """取收盘价阶梯 + 每标的失败类别；缺行情时**降级为当日阻断**，不让整段请求失败。

    行情端口对未知标的会抛 `ApiError`（回放夹具没有该标的 / live 不在支持范围）。
    批量取数失败后按标的逐个重试，取不到的标的跳过 —— 后续逐日推演会把持有该标的
    的日期判为阻断（缺行情数据），而不是把整段 NAV 变成 404。返回的 `causes` 用于
    把 `blocked_reason` 细化为三类（见 `docs/architecture/2026-10-22-live-market-data-source.md` §6）。
    """
    if not symbols:
        return {}, {}
    causes: dict[str, str] = {}
    try:
        bars: Sequence[Bar] = await market_data.get_daily_bars(symbols, start, end)
    except ApiError:
        collected: list[Bar] = []
        for symbol in symbols:
            try:
                collected.extend(await market_data.get_daily_bars([symbol], start, end))
            except ApiError as exc:
                cause = _market_failure_cause(exc)
                if cause:
                    causes[symbol] = cause
                continue  # 该标的无行情 → 持有它的日期阻断
        ladder = _closing_prices(collected, symbols)
    else:
        ladder = _closing_prices(bars, symbols)
    # 请求了但区间内没有任何 bar：视为 no_data（适配器不必为此造 404）。
    for symbol in symbols:
        if not ladder.get(symbol):
            causes.setdefault(symbol, "no_data")
    return ladder, causes


def investor_cashflows(series: NavSeries) -> list[CashflowPoint]:
    """NAV 序列 → 投资人视角现金流（XIRR 口径，PRD 附录 A6）。

    `Σ CF_i / (1+x)^((d_i−d_0)/365) = 0`，其中 CF **含期初投入（负）与期末市值（正）**；
    区间内银证转账按投资人方向取负号（转入 = 投资人出钱 = 流出）。
    少于 2 个点（无持有区间）时无法定义收益率，返回空。
    """
    points = series.points
    if len(points) < 2:
        return []
    flows: list[CashflowPoint] = []
    for idx, point in enumerate(points):
        amount = -point.cashflow_units
        if idx == 0:
            amount -= point.total_value_units  # 期初投入（负）
        flows.append(CashflowPoint(point.trade_date, amount))
    last = points[-1]
    flows[-1] = CashflowPoint(last.trade_date, flows[-1].amount_units + last.total_value_units)
    return flows


# 这些 category 的 trade 会增加 cost_basis（花钱买入更多持仓）。
# amount_units 对 buy/rights/new_stock 为负（现金流出），取负即得成本增量。
_COST_ADD_CATEGORIES: frozenset[str] = frozenset({"buy", "rights", "new_stock"})


async def compute_nav_points(
    trades: list[Trade],
    period_start: date,
    period_end: date,
    market_data: MarketDataSource,
) -> tuple[
    list[tuple[date, int, int]],  # (date, total_value_units, cashflow_units)
    list[tuple[date, str]],  # blocked_points
    list[str],  # blocked_reasons (global)
    bool,  # degraded
    dict[date, list[_HoldingSnapshot]],  # holdings_by_date (非 blocked 日)
]:
    """计算区间内每日 NAV 输入 + 持仓快照。

    返回 (points, blocked_points, blocked_reasons, degraded, holdings_by_date)。
    新增的 holdings_by_date 为 API 响应组装所需，不落库。
    """
    # 1. 期初现金锚点
    if not any(_is_cash_balance(t) for t in trades):
        return [], [], ["无现金余额锚点（无 cash_balance 行）"], False, {}
    initial_cash = _initial_cash(trades, period_start)
    if initial_cash is None:
        return [], [], ["无法确定期初现金（无区间前余额且首行非余额行）"], False, {}

    # 2. FIFO 结果用于检测 data_error（降级）
    fifo_all = build_rounds(trades_to_events(trades))
    degraded = any(r.status == "data_error" for r in fifo_all.rounds)

    # 3. 期初持仓（区间前 open lots）+ 成本
    fifo_before = build_rounds(trades_to_events([t for t in trades if t.trade_date < period_start]))
    initial_holdings: dict[str, int] = defaultdict(int)
    initial_cost: dict[str, int] = defaultdict(int)
    code_names: dict[str, str] = {}
    for lot in fifo_before.open_lots:
        initial_holdings[lot.security_code] += lot.quantity_units
        initial_cost[lot.security_code] += lot.cost_units
    # 从所有 trades 构建 code → name 映射（覆盖期初持仓 + 区间内新买入）
    for trade in trades:
        if trade.security_code and trade.security_name:
            code_names[trade.security_code] = trade.security_name

    # 4. 按日分组 trades（只取区间内；区间前的持仓/现金已由 initial_holdings / initial_cash 承担）
    all_trades_in_range = [t for t in trades if period_start <= t.trade_date <= period_end]
    by_date = _group_trades_by_date(all_trades_in_range)

    # 5. 交易日序列：区间内所有有 trade 的日期 + period_end
    trade_dates = sorted(by_date.keys())
    if not trade_dates:
        trade_dates = [period_end]
    if trade_dates[-1] < period_end:
        trade_dates.append(period_end)

    # 6. 需要行情的标的：**只取可能形成持仓的标的**（现金余额行的伪代码如 CASH 不算）
    holding_symbols = {
        t.security_code
        for t in all_trades_in_range
        if t.security_code and _holding_delta(_category(t), t.quantity_units) != 0
    }
    all_symbols = sorted(holding_symbols | set(initial_holdings.keys()))
    # 回看窗口：期初基准需要「期初当日或之前」的收盘价，否则第一天就判缺行情。
    ladder, market_causes = await _load_prices(
        market_data, all_symbols, period_start - _PRICE_LOOKBACK, trade_dates[-1]
    )
    points: list[tuple[date, int, int]] = []
    holdings_by_date: dict[date, list[_HoldingSnapshot]] = {}

    # 6b. 期初基准点：区间首日之前的总资产（现金 + 期初持仓按市价）。
    #     没有它，TWR 只能从首个交易日算起，整段收益会漏掉首日涨跌。
    baseline_date = period_start
    if trade_dates[0] > period_start:
        baseline_value, baseline_missing = _total_market_value(
            dict(initial_holdings), ladder, baseline_date
        )
        if baseline_missing:
            return (
                [],
                [
                    (
                        baseline_date,
                        _blocked_reason("缺期初行情数据", baseline_missing, market_causes),
                    )
                ],
                [],
                degraded,
                {},
            )
        points.append((baseline_date, initial_cash + baseline_value, 0))
        holdings_by_date[baseline_date] = _build_holding_snapshots(
            dict(initial_holdings), dict(initial_cost), ladder, baseline_date, code_names
        )

    # 7. 逐日推演
    state = _DailyState(
        cash_units=initial_cash,
        holdings=dict(initial_holdings),
        cost_basis=dict(initial_cost),
        cashflow_units=0,
    )
    blocked_points: list[tuple[date, str]] = []

    for d in trade_dates:
        day_trades = by_date.get(d, [])
        cashflow = 0
        day_cash_delta = 0
        balance_override: int | None = None

        for trade in day_trades:
            category = _category(trade)
            code = trade.security_code
            if code:
                # 持仓数量变化
                delta = _holding_delta(category, trade.quantity_units)
                if delta:
                    state.holdings[code] = state.holdings.get(code, 0) + delta
                # 成本变化：buy/rights/new_stock 增加成本，sell 按比例结转
                if category in _COST_ADD_CATEGORIES and trade.amount_units != 0:
                    state.cost_basis[code] = state.cost_basis.get(code, 0) + (-trade.amount_units)
                elif category == "sell":
                    old_qty = state.holdings.get(code, 0) - _holding_delta(
                        category, trade.quantity_units
                    )
                    if old_qty > 0 and delta < 0:
                        old_cost = state.cost_basis.get(code, 0)
                        state.cost_basis[code] = max(0, old_cost + (old_cost * delta) // old_qty)
                # 更新 code→name
                if trade.security_name:
                    code_names[code] = trade.security_name
            # 现金变化（所有 trade 的 amount 都影响现金）
            day_cash_delta += trade.amount_units
            # 外部现金流
            if category in _CASHFLOW_CATEGORIES:
                cashflow += trade.amount_units
            # 余额行覆盖
            if _is_cash_balance(trade):
                balance_override = int(trade.cash_balance_units or 0)

        state.cashflow_units = cashflow
        if balance_override is not None:
            state.cash_units = balance_override
        else:
            state.cash_units += day_cash_delta

        # 当日市值
        market_value, missing = _total_market_value(state.holdings, ladder, d)
        if missing:
            # 缺行情 → 当日阻断（继续推演后续日，不中断整段）
            blocked_points.append((d, _blocked_reason("缺行情数据", missing, market_causes)))
            continue

        total_value = state.cash_units + market_value
        points.append((d, total_value, cashflow))
        holdings_by_date[d] = _build_holding_snapshots(
            dict(state.holdings), dict(state.cost_basis), ladder, d, code_names
        )

    return points, blocked_points, [], degraded, holdings_by_date


def _build_holding_snapshots(
    holdings: dict[str, int],
    cost_basis: dict[str, int],
    ladder: dict[str, list[tuple[date, int]]],
    trade_date: date,
    code_names: dict[str, str],
) -> list[_HoldingSnapshot]:
    """根据某日推演结束后的 holdings/cost_basis + 价格阶梯，构建持仓快照列表。

    过滤 qty=0 的标的；按 security_code 排序，保证确定性。
    """
    snapshots: list[_HoldingSnapshot] = []
    for code in sorted(holdings):
        qty = holdings[code]
        if qty == 0:
            continue
        close = _close_on_or_before(ladder, code, trade_date)
        if close is None:
            continue  # 缺行情的日期会被判 blocked，这里只是兜底过滤
        cost = cost_basis.get(code, 0)
        market_value = qty * close // _QTY_PRICE_SCALE
        snapshots.append(
            _HoldingSnapshot(
                security_code=code,
                security_name=code_names.get(code, code),
                quantity_units=qty,
                cost_units=cost,
                close_units=close,
                market_value_units=market_value,
            )
        )
    return snapshots


async def compute_and_store_nav(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    period_start: date,
    period_end: date,
    market_data: MarketDataSource,
    clock: Clock,
) -> NavComputeResult:
    """计算区间 NAV 并落库 `review.nav_daily`（UPSERT）。

    阻断时不落库资产值（total_value/nav/return 均为 0，status=blocked）。
    逐日阻断（缺行情）的日期不计入 TWR/XIRR，只落 0 值 blocked 行。
    """
    trades = await TradeRepository(session).balances_ordered(user_id, period_end=period_end)

    points, blocked_points, blocked_reasons, degraded, holdings_by_date = await compute_nav_points(
        trades, period_start, period_end, market_data
    )

    rebuilt_at = clock.now()
    rows: list[dict[str, object]] = []

    if blocked_reasons:
        # 全局阻断：用 period_end 作为唯一 blocked 行（API 层据此返回 blocked 状态）
        rows.append(
            {
                "id": _nav_id(user_id, period_end),
                "user_id": user_id,
                "trade_date": period_end,
                "nav_units": 0,
                "return_units": 0,
                "total_value_units": 0,
                "cashflow_units": 0,
                "shares_units": 0,
                "status": "blocked",
                "blocked_reason": "; ".join(blocked_reasons),
                "rebuilt_at": rebuilt_at,
            }
        )
        series: NavSeries | None = None
        mwr_units = 0
    else:
        series = build_nav_series(points) if points else None
        cashflow_points = investor_cashflows(series) if series is not None else []
        mwr_units = compute_xirr(cashflow_points) if cashflow_points else 0

        # 逐日阻断行（不计入 TWR）
        for blocked_date, reason in blocked_points:
            rows.append(
                {
                    "id": _nav_id(user_id, blocked_date),
                    "user_id": user_id,
                    "trade_date": blocked_date,
                    "nav_units": 0,
                    "return_units": 0,
                    "total_value_units": 0,
                    "cashflow_units": 0,
                    "shares_units": 0,
                    "status": "blocked",
                    "blocked_reason": reason[:256],
                    "rebuilt_at": rebuilt_at,
                }
            )

        for p in series.points if series else []:
            rows.append(
                {
                    "id": _nav_id(user_id, p.trade_date),
                    "user_id": user_id,
                    "trade_date": p.trade_date,
                    "nav_units": p.nav_units,
                    "return_units": p.return_units,
                    "total_value_units": p.total_value_units,
                    "cashflow_units": p.cashflow_units,
                    "shares_units": p.shares_units,
                    "status": "degraded" if degraded else "ok",
                    "blocked_reason": None,
                    "rebuilt_at": rebuilt_at,
                }
            )
    # UPSERT（on conflict (id) do update）
    if rows:
        stmt = pg_insert(NavDaily).values(rows)
        update_cols = {
            "nav_units": stmt.excluded.nav_units,
            "return_units": stmt.excluded.return_units,
            "total_value_units": stmt.excluded.total_value_units,
            "cashflow_units": stmt.excluded.cashflow_units,
            "shares_units": stmt.excluded.shares_units,
            "status": stmt.excluded.status,
            "blocked_reason": stmt.excluded.blocked_reason,
            "rebuilt_at": stmt.excluded.rebuilt_at,
            "updated_at": rebuilt_at,
        }
        stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
        await session.execute(stmt)

    if blocked_reasons:
        return NavComputeResult(
            series=None,
            mwr_units=0,
            blocked_reasons=blocked_reasons,
            degraded=False,
            holdings_by_date={},
        )
    return NavComputeResult(
        series=series,
        mwr_units=mwr_units,
        blocked_reasons=[],
        degraded=degraded,
        holdings_by_date=holdings_by_date,
    )


async def fetch_nav_for_period(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> list[NavDaily]:
    """读取区间内已落库的 NAV 日点（按日期升序）。"""
    stmt = (
        select(NavDaily)
        .where(
            NavDaily.user_id == user_id,
            NavDaily.trade_date >= period_start,
            NavDaily.trade_date <= period_end,
        )
        .order_by(NavDaily.trade_date.asc())
    )
    return list(await session.scalars(stmt))
