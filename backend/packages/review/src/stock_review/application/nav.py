"""净值与收益应用层（Period2-03 / API-032）：ledger 明细 → 每日 NAV → 落库。

口径（INTERFACE §3.7 / PRD FR-R2.3）：
- 每日总资产 = 现金余额 + Σ(持仓数量 × 当日收盘价)；
- 外部现金流 = `category=transfer` 的 amount 之和（入金正、出金负）；
- 份额法 TWR：shares_0=1，NAV_t = (V_t - CF_t) / shares_{t-1}，Δshares = CF_t / NAV_t；
- 期初资产用 bridge 的 `_initial_cash`（区间前最后余额 statement / 区间首条余额行反推 back_solved）+ 期初持仓市值；
- 阻断条件（任一即当日 `blocked`）：对账断链、缺行情数据、无期初资产锚点；
- FIFO `data_error` 降级为 `degraded`（仍输出资产值，但 status 降级）。

本模块不读系统时钟，时间一律从 `Clock` 注入。
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_core.fifo import build_rounds
from stock_core.nav import CashflowPoint, NavSeries, build_nav_series, compute_xirr
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository
from stock_platform.ports import MarketDataSource
from stock_platform.symbols import is_bond

from stock_review.application.bond_alias import build_bond_alias, canonical_code
from stock_review.application.bridge import _initial_cash
from stock_review.application.rebuild import trades_to_events
from stock_review.application.valuation import (
    CASHFLOW_CATEGORIES as _CASHFLOW_CATEGORIES,
)
from stock_review.application.valuation import (
    COST_ADD_CATEGORIES as _COST_ADD_CATEGORIES,
)
from stock_review.application.valuation import (
    PRICE_LOOKBACK as _PRICE_LOOKBACK,
)
from stock_review.application.valuation import (
    QTY_PRICE_SCALE as _QTY_PRICE_SCALE,
)
from stock_review.application.valuation import (
    blocked_price_reason as _blocked_reason,
)
from stock_review.application.valuation import (
    bond_face_divisors as _bond_face_divisors,
)
from stock_review.application.valuation import (
    bond_value_units as _bond_value_units,
)
from stock_review.application.valuation import (
    category as _category,
)
from stock_review.application.valuation import (
    close_on_or_before as _close_on_or_before,
)
from stock_review.application.valuation import (
    holding_delta as _holding_delta,
)
from stock_review.application.valuation import (
    initial_money_fund_balances as _initial_money_fund_balances,
)
from stock_review.application.valuation import (
    is_cash_balance as _is_cash_balance,
)
from stock_review.application.valuation import (
    is_money_fund_row as _is_money_fund_row,
)
from stock_review.application.valuation import (
    load_prices as _load_prices,
)
from stock_review.application.valuation import (
    money_fund_balance as _money_fund_balance,
)
from stock_review.application.valuation import (
    new_stock_pending_codes as _new_stock_pending_codes,
)
from stock_review.application.valuation import (
    total_market_value as _total_market_value,
)
from stock_review.models import NavDaily


@dataclass(slots=True)
class NavComputeResult:
    """一次 NAV 计算的结果（落库前的内存表示）。"""

    series: NavSeries | None
    mwr_units: int
    blocked_reasons: list[str]
    degraded: bool
    holdings_by_date: dict[date, list[_HoldingSnapshot]]
    assumption: str | None  # 期初现金锚点来源：statement | back_solved；blocked 时 None
    # 月度首日的「昨日资产」= 期初现金 + 期初持仓按上一交易日收盘价估值（scale=2）；
    # 仅用于 API 展示首日昨日资产/日收益额，不进入 TWR 序列；行情不足时为 None。
    prev_day_value_units: int | None = None


@dataclass(slots=True)
class _DailyState:
    """逐日推演时的中间状态。"""

    cash_units: int
    holdings: dict[str, int]  # security_code → quantity_units (scale=4)
    cost_basis: dict[str, int]  # security_code → 累计成本 (scale=2)
    money_fund: dict[str, int]  # security_code → 货币基金余额 (scale=2)
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


def _group_trades_by_date(trades: list[Trade]) -> dict[date, list[Trade]]:
    """按 trade_date 分组（trades 已按日期升序）。"""
    grouped: dict[date, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.trade_date].append(trade)
    return grouped


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
    str | None,  # assumption：期初现金锚点来源（statement | back_solved）
    int | None,  # prev_day_value_units：上一交易日收盘资产（首日「昨日资产」）
]:
    """计算区间内每日 NAV 输入 + 持仓快照。

    返回 (points, blocked_points, blocked_reasons, degraded, holdings_by_date, assumption,
    prev_day_value_units)。holdings_by_date 为 API 响应组装所需，不落库；
    assumption / prev_day_value_units 仅响应透出，不进入 TWR 序列或落库字段。
    """
    # 1. 期初现金锚点：区间前余额行直取（statement）或区间首条余额行反推（back_solved）；
    #    全历史无余额行 → 全局阻断。
    initial = _initial_cash(trades, period_start)
    if initial is None:
        return [], [], ["无现金余额锚点（无 cash_balance 行）"], False, {}, None, None
    initial_cash, assumption = initial

    # 可转债发债→上市代码别名（设计 D3）：从全量明细派生，期初持仓/区间交易/行情统一
    # 用 canonical 代码，避免「754019 申购持仓 + 113xxx 上市卖出」被当成两只证券。
    bond_alias, _unresolved = build_bond_alias((t.security_code, t.security_name) for t in trades)

    # 2. FIFO 结果用于检测 data_error（降级）
    fifo_all = build_rounds(trades_to_events(trades, bond_alias=bond_alias))
    degraded = any(r.status == "data_error" for r in fifo_all.rounds)

    # 3. 期初持仓（区间前 open lots）+ 成本；货基行不进持仓（按余额计）
    money_fund_codes = {t.security_code for t in trades if _is_money_fund_row(t)}
    initial_money_fund = _initial_money_fund_balances(trades, period_start)
    # 新股待上市（认购缴款已入库、尚无行情）：按认购成本兜底估值，不阻断（设计 D11）。
    pending_new_stock_codes = {
        canonical_code(bond_alias, code) for code in _new_stock_pending_codes(trades)
    }
    bond_divisors = _bond_face_divisors(trades)
    fifo_before = build_rounds(
        trades_to_events([t for t in trades if t.trade_date < period_start], bond_alias=bond_alias)
    )
    initial_holdings: dict[str, int] = defaultdict(int)
    initial_cost: dict[str, int] = defaultdict(int)
    code_names: dict[str, str] = {}
    for lot in fifo_before.open_lots:
        if lot.security_code in money_fund_codes:
            continue  # 货基份额不进持仓，余额由 initial_money_fund 承担
        initial_holdings[lot.security_code] += lot.quantity_units
        initial_cost[lot.security_code] += lot.cost_units
    # 从所有 trades 构建 code → name 映射（覆盖期初持仓 + 区间内新买入）；
    # 发债代码归并到上市代码，名称取最新一行（上市后为「xx转债」）。
    for trade in trades:
        if trade.security_code and trade.security_name:
            code_names[canonical_code(bond_alias, trade.security_code)] = trade.security_name

    # 4. 按日分组 trades（只取区间内；区间前的持仓/现金已由 initial_holdings / initial_cash 承担）
    all_trades_in_range = [t for t in trades if period_start <= t.trade_date <= period_end]
    by_date = _group_trades_by_date(all_trades_in_range)

    # 5. 交易日序列：区间内所有有 trade 的日期 + period_end
    trade_dates = sorted(by_date.keys())
    if not trade_dates:
        trade_dates = [period_end]
    if trade_dates[-1] < period_end:
        trade_dates.append(period_end)

    # 6. 需要行情的标的：**只取可能形成持仓的标的**（现金余额行/货基行不算）；
    #    发债代码归并为上市代码（申购代码无行情，只发 canonical 请求）。
    holding_symbols = {
        canonical_code(bond_alias, t.security_code)
        for t in all_trades_in_range
        if t.security_code
        and not _is_money_fund_row(t)
        and _holding_delta(_category(t), t.quantity_units, amount_units=t.amount_units) != 0
    }
    all_symbols = sorted(holding_symbols | set(initial_holdings.keys()))
    # 回看窗口：期初基准需要「期初当日或之前」的收盘价，否则第一天就判缺行情。
    ladder, market_causes = await _load_prices(
        market_data, all_symbols, period_start - _PRICE_LOOKBACK, trade_dates[-1]
    )
    initial_money_fund_units = sum(initial_money_fund.values())
    bond_fallback_any = False

    # 6a. 月度首日「昨日资产」：期初现金 + 货基余额 + 期初持仓按上一交易日收盘价估值。
    #     仅用于展示首日的昨日资产/日收益额（不进入 TWR 序列）。
    (
        prev_day_value,
        prev_day_missing,
        prev_day_fallback,
        prev_day_new_stock_fallback,
    ) = _total_market_value(
        dict(initial_holdings),
        ladder,
        period_start - timedelta(days=1),
        cost_basis=dict(initial_cost),
        bond_divisors=bond_divisors,
        new_stock_codes=pending_new_stock_codes,
    )
    if prev_day_fallback or prev_day_new_stock_fallback:
        bond_fallback_any = True
    prev_day_value_units: int | None = (
        None if prev_day_missing else initial_cash + initial_money_fund_units + prev_day_value
    )

    points: list[tuple[date, int, int]] = []
    holdings_by_date: dict[date, list[_HoldingSnapshot]] = {}

    # 6b. 期初基准点：区间首日之前的总资产（现金 + 货基 + 期初持仓按市价）。
    #     没有它，TWR 只能从首个交易日算起，整段收益会漏掉首日涨跌。
    baseline_date = period_start
    if trade_dates[0] > period_start:
        (
            baseline_value,
            baseline_missing,
            baseline_fallback,
            baseline_new_stock_fallback,
        ) = _total_market_value(
            dict(initial_holdings),
            ladder,
            baseline_date,
            cost_basis=dict(initial_cost),
            bond_divisors=bond_divisors,
            new_stock_codes=pending_new_stock_codes,
        )
        if baseline_fallback or baseline_new_stock_fallback:
            bond_fallback_any = True
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
                assumption,
                prev_day_value_units,
            )
        points.append((baseline_date, initial_cash + initial_money_fund_units + baseline_value, 0))
        holdings_by_date[baseline_date] = _build_holding_snapshots(
            dict(initial_holdings),
            dict(initial_cost),
            ladder,
            baseline_date,
            code_names,
            bond_divisors=bond_divisors,
        )

    # 7. 逐日推演
    state = _DailyState(
        cash_units=initial_cash,
        holdings=dict(initial_holdings),
        cost_basis=dict(initial_cost),
        money_fund=dict(initial_money_fund),
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
            if _is_money_fund_row(trade):
                # 货基：不进持仓/成本，只覆盖余额（同日以稳定序最后一行为准；负余额按 0 兜底）。
                state.money_fund[code] = _money_fund_balance(trade)
            elif code:
                code = canonical_code(bond_alias, code)  # 发债代码归并上市代码（设计 D3）
                # 持仓数量变化
                delta = _holding_delta(
                    category, trade.quantity_units, amount_units=trade.amount_units
                )
                if delta:
                    state.holdings[code] = state.holdings.get(code, 0) + delta
                # 成本变化：buy/rights/new_stock 增加成本，sell 按比例结转
                if category in _COST_ADD_CATEGORIES and trade.amount_units != 0:
                    state.cost_basis[code] = state.cost_basis.get(code, 0) + (-trade.amount_units)
                elif category == "sell":
                    old_qty = state.holdings.get(code, 0) - _holding_delta(
                        category, trade.quantity_units, amount_units=trade.amount_units
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

        # 当日市值（债券缺行情按买入价兜底，不阻断）
        market_value, missing, bond_fallback, new_stock_fallback = _total_market_value(
            state.holdings,
            ladder,
            d,
            cost_basis=state.cost_basis,
            bond_divisors=bond_divisors,
            new_stock_codes=pending_new_stock_codes,
        )
        if bond_fallback or new_stock_fallback:
            bond_fallback_any = True
        if missing:
            # 缺行情 → 当日阻断（继续推演后续日，不中断整段）
            blocked_points.append((d, _blocked_reason("缺行情数据", missing, market_causes)))
            continue

        money_fund_units = sum(state.money_fund.values())
        total_value = state.cash_units + money_fund_units + market_value
        points.append((d, total_value, cashflow))
        holdings_by_date[d] = _build_holding_snapshots(
            dict(state.holdings),
            dict(state.cost_basis),
            ladder,
            d,
            code_names,
            bond_divisors=bond_divisors,
        )

    return (
        points,
        blocked_points,
        [],
        degraded or bond_fallback_any,
        holdings_by_date,
        assumption,
        prev_day_value_units,
    )


def _build_holding_snapshots(
    holdings: dict[str, int],
    cost_basis: dict[str, int],
    ladder: dict[str, list[tuple[date, int]]],
    trade_date: date,
    code_names: dict[str, str],
    *,
    bond_divisors: dict[str, int] | None = None,
) -> list[_HoldingSnapshot]:
    """根据某日推演结束后的 holdings/cost_basis + 价格阶梯，构建持仓快照列表。

    过滤 qty=0 的标的；按 security_code 排序，保证确定性。债券价格按换算系数折算
    （akshare 净价是元/百元面值）；缺行情的债券在总资产里已按买入价兜底，快照跳过
    （避免展示 0 现价）。
    """
    divisors = bond_divisors or {}
    snapshots: list[_HoldingSnapshot] = []
    for code in sorted(holdings):
        qty = holdings[code]
        if qty == 0:
            continue
        close = _close_on_or_before(ladder, code, trade_date)
        if close is None:
            continue  # 缺行情的日期会被判 blocked / 债券已按买入价兜底，这里只是兜底过滤
        cost = cost_basis.get(code, 0)
        market_value = (
            _bond_value_units(code, qty, close, divisors)
            if is_bond(code)
            else qty * close // _QTY_PRICE_SCALE
        )
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

    (
        points,
        blocked_points,
        blocked_reasons,
        degraded,
        holdings_by_date,
        assumption,
        prev_day_value_units,
    ) = await compute_nav_points(trades, period_start, period_end, market_data)

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
            assumption=None,
            prev_day_value_units=None,
        )
    return NavComputeResult(
        series=series,
        mwr_units=mwr_units,
        blocked_reasons=[],
        degraded=degraded,
        holdings_by_date=holdings_by_date,
        assumption=assumption,
        prev_day_value_units=prev_day_value_units,
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
