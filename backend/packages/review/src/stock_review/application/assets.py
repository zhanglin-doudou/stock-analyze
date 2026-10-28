"""每日资产快照应用层（Period2-16）：构建 / 增量 / 列表 / worker 编排。

权威口径：`docs/architecture/2026-10-25-asset-daily-table.md` §5（计算口径）与
§6（增量更新算法）。本模块只做内存推演 + 落库编排，行情取数与估值原语复用
`application.valuation`（与 nav 同源，避免口径漂移）。

关键规则（§6.3）：
1. **变更指纹** `(COUNT(*), SUM(version), MAX(updated_at))`（含软删）决定是否需要脏查询；
   脏查询下界取水位线 − `WATERMARK_OVERLAP`（1h），覆盖长事务晚提交竞态；指纹变了但
   查不到脏行 → 全量兜底。
2. **version>1 → 全量**（修正/软删/恢复/改日期更晚，无法用当前日期定位脏窗口）。
3. `start` 双向变化（前移/后移）、`end < last_built_through`、`definition_version` 升级、
   FIFO 异常集合（`last_fifo_error`）变化 → 全量。
4. 45 天安全窗；行情 `as_of` 变化（即使 end 未前进）也重算安全窗，跨度 >45 天 → 全量。
5. 回填早于首个余额行 → 全量（back_solved 锚点变化，窗口外行不会被重写）；
   增量窗口前一日缺行 → 全量。
6. no-op 只写 `last_mode/last_built_at/last_rows_written=0/last_rebuild_from=NULL`；
   数据/水位线/指纹不变。
7. 失败原子性：delete/insert/state 同属调用方事务，异常回滚即旧表与旧水位线完好。
8. 状态 CAS：`WHERE user_id AND version`；0 行 → `VERSION_CONFLICT`（job 可重试）。

本模块不读系统时钟（业务日与构建时刻均取注入的 `Clock`），不自行 commit / rollback。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.fifo import build_rounds
from stock_ledger.models import Trade
from stock_platform.jobs import JobQueue
from stock_platform.obs import get_logger
from stock_platform.ports import MarketDataSource

from stock_review.application.bridge import _initial_cash
from stock_review.application.rebuild import trades_to_events
from stock_review.application.valuation import (
    CASHFLOW_CATEGORIES,
    PRICE_LOOKBACK,
    blocked_price_reason,
    category,
    holding_delta,
    is_cash_balance,
    load_prices,
    total_market_value,
)
from stock_review.models import AssetBuildState as AssetBuildStateRow
from stock_review.models import AssetDaily
from stock_review.repositories.assets import AssetRepository

log = get_logger("stock_review.assets")

# 资产表口径版本：bump 后旧 state 触发全量重建。
ASSET_DEFINITION_VERSION = "1.0"
# 安全窗：每次有新数据/脏流水/行情 as_of 变化时重算最近 45 天。
SAFETY_DAYS = 45
# 水位线重叠窗：PG `now()` = 事务开始时间，覆盖「长事务晚提交」竞态。
WATERMARK_OVERLAP = timedelta(hours=1)

# 行状态（asset_daily.status）
ROW_STATUS_OK = "ok"
ROW_STATUS_BLOCKED = "blocked"
ROW_STATUS_DEGRADED = "degraded"
# 构建整体状态（asset_build_state.last_status / build.last_status）
BUILD_STATUS_OK = "ok"
BUILD_STATUS_DEGRADED = "degraded"
BUILD_STATUS_BLOCKED = "blocked"
# 构建模式（build.last_mode）
BUILD_MODE_FULL = "full"
BUILD_MODE_INCREMENTAL = "incremental"
BUILD_MODE_NOOP = "noop"
# build.state 判定值
STATE_MISSING = "missing"
STATE_CURRENT = "current"
STATE_STALE = "stale"
STATE_BLOCKED = "blocked"

# 统一原因文案（行级 / 全局 / 测试断言共用，避免漂移）。
NO_ANCHOR_REASON = "无现金余额锚点（无 cash_balance 行）"
FIFO_ERROR_REASON = "FIFO 数据异常（超卖等）"
MARKET_UNCOVERED_REASON = "行情未覆盖，按最近收盘估值"
MISSING_PRICE_PREFIX = "缺行情数据"
# blocked 行原因列上限（String(256)）
_REASON_MAX = 256


@dataclass(slots=True)
class AssetPoint:
    """一行资产快照（标度整数；blocked 行为 DB 数值 0，API 层转 null）。"""

    id: str
    trade_date: date
    cash_units: int
    market_value_units: int
    total_assets_units: int
    holding_cost_units: int
    unrealized_pnl_units: int
    holding_count: int
    cashflow_units: int
    prev_total_assets_units: int | None
    daily_pnl_units: int | None
    daily_return_units: int | None
    status: str
    status_reason: str | None
    definition_version: str


@dataclass(slots=True)
class AssetBuildSummary:
    """一次构建/检查的结果摘要（worker job.result 出口）。"""

    mode: str
    rebuild_from: date | None
    built_through: date | None
    rows_written: int
    rows_total: int
    status: str
    assumption: str | None
    data_as_of: date | None


@dataclass(slots=True)
class AssetBuildState:
    """`GET /assets` 的 build 摘要块（state 判定 + 最近构建信息）。

    注意与 ORM `models.AssetBuildState` 同名：本类是**只读视图**（API 出口），
    持久化模型在应用层内以 `AssetBuildStateRow` 别名使用。
    """

    state: str
    last_status: str | None
    last_built_at: datetime | None
    last_built_through: date | None
    last_mode: str | None
    last_rebuild_from: date | None
    last_rows_written: int
    rows_count: int
    assumption: str | None
    status_reason: str | None
    has_new_market_data: bool
    definition_version: str
    # 本次构建读取的 market_data.as_of（页面顶层 data_as_of 血缘）；未构建为 None。
    data_as_of: date | None = None


@dataclass(slots=True)
class _DailyValuations:
    """逐日推演结果：窗口内待写行 + 全局阻断信息。"""

    rows: list[dict[str, object]]
    global_blocked: bool
    global_reason: str | None


def _asset_id(user_id: uuid.UUID, trade_date: date) -> str:
    """确定性主键：`ast_` + sha256(user_id|trade_date)[:32]。"""
    digest = hashlib.sha256(f"{user_id}|{trade_date.isoformat()}".encode()).hexdigest()
    return f"ast_{digest[:32]}"


def _ratio_units(pnl_units: int, prev_total_units: int) -> int:
    """日收益率（scale=8）：`pnl / prev_total`，Decimal + ROUND_HALF_UP（与 core nav 一致）。"""
    value = (Decimal(pnl_units) / Decimal(prev_total_units)).scaleb(8)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _group_trades_by_date(trades: list[Trade]) -> dict[date, list[Trade]]:
    """按 trade_date 分组；trades 已按稳定序升序，组内顺序保持稳定序。"""
    grouped: dict[date, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.trade_date, []).append(trade)
    return grouped


def _fingerprint(trades: list[Trade]) -> tuple[int, int, datetime | None]:
    """变更指纹 `(COUNT(*), SUM(version), MAX(updated_at))`，**含软删**。"""
    count = len(trades)
    version_sum = sum(trade.version for trade in trades)
    max_updated = max((trade.updated_at for trade in trades), default=None)
    return count, version_sum, max_updated


def _state_reason(last_status: str, *, fifo_error: bool) -> str | None:
    """全局状态原因：全局 blocked 必有；FIFO 异常导致的整体 degraded 记录原因。"""
    if last_status == BUILD_STATUS_BLOCKED:
        return NO_ANCHOR_REASON
    if last_status == BUILD_STATUS_DEGRADED and fifo_error:
        return FIFO_ERROR_REASON
    return None


def _row_status_reason(*, fifo_error: bool, uncovered: bool) -> str | None:
    """行级 degraded 原因（FIFO 异常 / 行情未覆盖；都不命中返回 NULL）。"""
    reasons: list[str] = []
    if fifo_error:
        reasons.append(FIFO_ERROR_REASON)
    if uncovered:
        reasons.append(MARKET_UNCOVERED_REASON)
    return "；".join(reasons) if reasons else None


def compute_daily_valuations(
    trades: list[Trade],
    *,
    user_id: uuid.UUID,
    start: date,
    end: date,
    rebuild_from: date,
    ladder: dict[str, list[tuple[date, int]]],
    market_causes: dict[str, str],
    as_of: date,
    prev_row: AssetDaily | None,
    fifo_error: bool,
    rebuilt_at: datetime,
) -> _DailyValuations:
    """内存重放 `[start, end]`，为 `[rebuild_from, end]` 生成资产日行（纯计算，无 I/O）。

    逐日推演口径见设计 §5.3：现金（余额行覆盖优先）/ 持仓数量 / 含费成本 / 外部现金流 /
    逐标的 floor 市值；缺行情 → 该日 blocked（数值 0、prev/pnl/return NULL）；FIFO 异常或
    `date > as_of` 且仍有持仓 → degraded（按最近收盘估值）。`prev_total` 取**严格上一自然日**
    非 blocked 总资产，首行取期初现金锚点；`rebuild_from > start` 时首行用落库的 `prev_row`。
    """
    initial = _initial_cash(trades, start)
    if initial is None:
        return _DailyValuations(rows=[], global_blocked=True, global_reason=NO_ANCHOR_REASON)
    initial_cash, _assumption = initial

    by_date = _group_trades_by_date(trades)
    cash_units = initial_cash
    holdings: dict[str, int] = {}
    cost_basis: dict[str, int] = {}
    # 上一自然日的非 blocked 总资产；None 表示首日之前或上一日 blocked。
    prior_total: int | None = None
    rows: list[dict[str, object]] = []

    cursor_date = start
    while cursor_date <= end:
        day_trades = by_date.get(cursor_date, [])
        cashflow_units = 0
        day_cash_delta = 0
        balance_override: int | None = None

        for trade in day_trades:
            trade_category = category(trade)
            code = trade.security_code
            if code:
                delta = holding_delta(trade_category, trade.quantity_units)
                if delta:
                    holdings[code] = holdings.get(code, 0) + delta
                if trade_category in {"buy", "rights", "new_stock"} and trade.amount_units != 0:
                    cost_basis[code] = cost_basis.get(code, 0) + (-trade.amount_units)
                elif trade_category == "sell":
                    # 卖出按比例结转成本：delta<0 且原持仓>0 时 cost=old_cost+old_cost*delta//old_qty。
                    old_qty = holdings.get(code, 0) - delta
                    if old_qty > 0 and delta < 0:
                        old_cost = cost_basis.get(code, 0)
                        cost_basis[code] = max(0, old_cost + (old_cost * delta) // old_qty)
            day_cash_delta += trade.amount_units
            if trade_category in CASHFLOW_CATEGORIES:
                cashflow_units += trade.amount_units
            if is_cash_balance(trade):
                balance_override = int(trade.cash_balance_units or 0)

        if balance_override is not None:
            cash_units = balance_override
        else:
            cash_units += day_cash_delta

        market_value_units, missing = total_market_value(holdings, ladder, cursor_date)
        nonzero_holdings = sum(1 for qty in holdings.values() if qty != 0)

        # blocked 判定优先：缺行情 → 该日硬阻断（数值 0，prev/pnl/return NULL）。
        if missing:
            reason = blocked_price_reason(MISSING_PRICE_PREFIX, missing, market_causes)[
                :_REASON_MAX
            ]
            if cursor_date >= rebuild_from:
                rows.append(
                    _blocked_row(
                        user_id=user_id,
                        trade_date=cursor_date,
                        reason=reason,
                        rebuilt_at=rebuilt_at,
                    )
                )
            prior_total = None
            cursor_date += timedelta(days=1)
            continue

        total_assets_units = cash_units + market_value_units
        holding_cost_units = sum(
            cost for code, cost in cost_basis.items() if holdings.get(code, 0) != 0
        )
        unrealized_pnl_units = market_value_units - holding_cost_units
        uncovered = cursor_date > as_of and nonzero_holdings > 0
        status = ROW_STATUS_DEGRADED if (fifo_error or uncovered) else ROW_STATUS_OK
        status_reason = (
            _row_status_reason(fifo_error=fifo_error, uncovered=uncovered)
            if status == ROW_STATUS_DEGRADED
            else None
        )

        # prev_total：首行取期初锚点；否则严格上一自然日（上一日 blocked → NULL）。
        # 增量窗口首行用落库的 prev_row（窗口外行不重写，但必须与旧行衔接）。
        if cursor_date >= rebuild_from:
            effective_prev = (
                prev_row_total(prev_row, cursor_date, start, rebuild_from, initial_cash)
                if cursor_date == rebuild_from
                else prior_total
            )
            daily_pnl_units = (
                None
                if effective_prev is None
                else total_assets_units - cashflow_units - effective_prev
            )
            daily_return_units = (
                None
                if effective_prev is None or effective_prev <= 0 or daily_pnl_units is None
                else _ratio_units(daily_pnl_units, effective_prev)
            )
            rows.append(
                {
                    "id": _asset_id(user_id, cursor_date),
                    "user_id": user_id,
                    "trade_date": cursor_date,
                    "cash_units": cash_units,
                    "market_value_units": market_value_units,
                    "total_assets_units": total_assets_units,
                    "holding_cost_units": holding_cost_units,
                    "unrealized_pnl_units": unrealized_pnl_units,
                    "holding_count": nonzero_holdings,
                    "cashflow_units": cashflow_units,
                    "prev_total_assets_units": effective_prev,
                    "daily_pnl_units": daily_pnl_units,
                    "daily_return_units": daily_return_units,
                    "status": status,
                    "status_reason": status_reason,
                    "definition_version": ASSET_DEFINITION_VERSION,
                    "rebuilt_at": rebuilt_at,
                }
            )

        prior_total = total_assets_units
        cursor_date += timedelta(days=1)

    return _DailyValuations(rows=rows, global_blocked=False, global_reason=None)


def prev_row_total(
    prev_row: AssetDaily | None,
    trade_date: date,
    start: date,
    rebuild_from: date,
    initial_cash: int,
) -> int | None:
    """增量窗口首行的 `prev_total`：首行取期初锚点，否则落库上一日行（blocked → NULL）。"""
    if trade_date == start:
        return initial_cash
    if rebuild_from > start and trade_date == rebuild_from:
        if prev_row is None or prev_row.status == ROW_STATUS_BLOCKED:
            return None
        return prev_row.total_assets_units
    return None


def _blocked_row(
    *, user_id: uuid.UUID, trade_date: date, reason: str, rebuilt_at: datetime
) -> dict[str, object]:
    """blocked 行：数值列全 0、prev/daily_pnl/daily_return NULL、reason 非空。"""
    return {
        "id": _asset_id(user_id, trade_date),
        "user_id": user_id,
        "trade_date": trade_date,
        "cash_units": 0,
        "market_value_units": 0,
        "total_assets_units": 0,
        "holding_cost_units": 0,
        "unrealized_pnl_units": 0,
        "holding_count": 0,
        "cashflow_units": 0,
        "prev_total_assets_units": None,
        "daily_pnl_units": None,
        "daily_return_units": None,
        "status": ROW_STATUS_BLOCKED,
        "status_reason": reason,
        "definition_version": ASSET_DEFINITION_VERSION,
        "rebuilt_at": rebuilt_at,
    }


async def _persist_state(
    repo: AssetRepository,
    user_id: uuid.UUID,
    *,
    existing: AssetBuildStateRow | None,
    fields: dict[str, object],
) -> None:
    """写入构建状态：无 state 插入（version=1），有 state 走 CAS（version+1）。"""
    if existing is None:
        await repo.insert_state(user_id=user_id, version=1, **fields)
    else:
        await repo.update_state_cas(user_id, expected_version=existing.version, **fields)


async def _reset_state(
    repo: AssetRepository,
    user_id: uuid.UUID,
    *,
    clock: Clock,
    count_all: int,
    version_sum: int,
    max_updated: datetime | None,
    last_status: str,
    start_date: date | None,
    status_reason: str | None,
) -> None:
    """reset_state 统一语义（设计 §5.2）：清空水位线/血缘/摘要，只保留当前指纹快照。"""
    existing = await repo.get_state(user_id)
    fields: dict[str, object] = {
        "start_date": start_date,
        "last_built_through": None,
        "last_built_at": clock.now(),
        "last_trade_updated_at": max_updated,
        "last_trade_count": count_all,
        "last_trade_version_sum": version_sum,
        "data_as_of": None,
        "assumption": None,
        "last_status": last_status,
        "last_fifo_error": False,
        "status_reason": status_reason,
        "last_mode": BUILD_MODE_FULL,
        "last_rebuild_from": None,
        "last_rows_written": 0,
        "definition_version": ASSET_DEFINITION_VERSION,
        "rows_count": 0,
    }
    await _persist_state(repo, user_id, existing=existing, fields=fields)


def _symbols(live: list[Trade]) -> list[str]:
    """可能形成持仓的标的（现金余额伪代码等无持仓影响的类别不算）。"""
    return sorted(
        {
            trade.security_code
            for trade in live
            if trade.security_code and holding_delta(category(trade), trade.quantity_units) != 0
        }
    )


async def build_assets(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    mode: str = "auto",
) -> AssetBuildSummary:
    """构建/增量更新该用户资产表；事务由调用方持有（不 commit / rollback）。

    `mode="full"` 强制全量；`"auto"` 按指纹 + 水位线 + 安全窗增量（无变化 → no-op）。
    """
    if mode not in {"auto", "full"}:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"未知构建模式: {mode}")
    repo = AssetRepository(session)
    state = await repo.get_state(user_id)
    trades_all = await repo.load_trades_including_deleted(user_id)
    live = [trade for trade in trades_all if trade.deleted_at is None]
    count_all, version_sum, max_updated = _fingerprint(trades_all)

    # 1) 无有效流水：清空行 + reset state（无数据也是 ok）。
    if not live:
        await repo.delete_all(user_id)
        await _reset_state(
            repo,
            user_id,
            clock=clock,
            count_all=count_all,
            version_sum=version_sum,
            max_updated=max_updated,
            last_status=BUILD_STATUS_OK,
            start_date=None,
            status_reason=None,
        )
        return AssetBuildSummary(
            mode=BUILD_MODE_FULL,
            rebuild_from=None,
            built_through=None,
            rows_written=0,
            rows_total=0,
            status=BUILD_STATUS_OK,
            assumption=None,
            data_as_of=None,
        )

    start = min(trade.trade_date for trade in live)
    end = clock.today()

    # 2) 未来流水 / 时钟回拨：清空并 reset（不出现负 rows_count）。
    if end < start:
        await repo.delete_all(user_id)
        await _reset_state(
            repo,
            user_id,
            clock=clock,
            count_all=count_all,
            version_sum=version_sum,
            max_updated=max_updated,
            last_status=BUILD_STATUS_OK,
            start_date=None,
            status_reason=None,
        )
        return AssetBuildSummary(
            mode=BUILD_MODE_FULL,
            rebuild_from=None,
            built_through=None,
            rows_written=0,
            rows_total=0,
            status=BUILD_STATUS_OK,
            assumption=None,
            data_as_of=None,
        )

    fifo_error_now = any(
        round_.status == "data_error" for round_ in build_rounds(trades_to_events(live)).rounds
    )
    market_as_of = market_data.as_of
    market_changed = state is not None and state.data_as_of != market_as_of

    # 3) 全量判定（§6.2）：任何无法安全定位的变更都收敛到全量。
    full = (
        mode == "full"
        or state is None
        or state.definition_version != ASSET_DEFINITION_VERSION
        or state.last_built_through is None
        or state.last_fifo_error != fifo_error_now
        or start != state.start_date
        or end < state.last_built_through
    )
    if not full and state is not None and market_changed:
        old_as_of = state.data_as_of
        # 行情覆盖跨度超过安全窗 → 旧窗口不足以重算到当前 as_of。
        if old_as_of is None or (end - old_as_of).days > SAFETY_DAYS - 1:
            full = True

    dirty_rows: list[Trade] = []
    rebuild_from = start
    if not full and state is not None:
        last = state.last_built_through
        assert last is not None  # 上一步已排除 None
        fingerprint_changed = (count_all, version_sum, max_updated) != (
            state.last_trade_count,
            state.last_trade_version_sum,
            state.last_trade_updated_at,
        )
        if fingerprint_changed:
            watermark = state.last_trade_updated_at
            if watermark is None:
                # 指纹不一致但无时间戳可定位（并发长事务竞态）→ 全量兜底。
                full = True
            else:
                dirty_rows = await repo.dirty_trades(
                    user_id, since_updated_at=watermark - WATERMARK_OVERLAP
                )
                if any(trade.version > 1 for trade in dirty_rows):
                    full = True  # D10：修正/软删/恢复/改日期 → 无法按当前日期定位
                elif not dirty_rows:
                    full = True  # 指纹变了但查不到脏行 → 全量兜底
        if full:
            rebuild_from = start
        else:
            dirty_date = min((trade.trade_date for trade in dirty_rows), default=None)
            if end == last and not market_changed and dirty_date is None:
                # no-op：只记录“本次检查”，不动数据/水位线/指纹。
                await repo.update_state_cas(
                    user_id,
                    expected_version=state.version,
                    last_mode=BUILD_MODE_NOOP,
                    last_built_at=clock.now(),
                    last_rebuild_from=None,
                    last_rows_written=0,
                )
                return AssetBuildSummary(
                    mode=BUILD_MODE_NOOP,
                    rebuild_from=None,
                    built_through=last,
                    rows_written=0,
                    rows_total=state.rows_count,
                    status=state.last_status,
                    assumption=state.assumption,
                    data_as_of=state.data_as_of,
                )
            rebuild_from = max(
                start, min(last + timedelta(days=1), end - timedelta(days=SAFETY_DAYS - 1))
            )
            if dirty_date is not None:
                rebuild_from = min(rebuild_from, dirty_date)
    else:
        rebuild_from = start

    # 4) 回填/补差落在首个余额行之前会改变 back_solved 锚点 → 全量（窗口外行不会重写）。
    first_balance_date = min(
        (trade.trade_date for trade in live if is_cash_balance(trade)), default=None
    )
    if (
        not full
        and first_balance_date is not None
        and start < first_balance_date
        and rebuild_from <= first_balance_date
    ):
        full = True
    if full:
        rebuild_from = start

    # 5) 增量窗口前一日必须有落库行（提供 prev_total）；缺失 → 全量兜底。
    prev_row: AssetDaily | None = None
    if not full and rebuild_from > start:
        prev_row = await repo.get_row(user_id, rebuild_from - timedelta(days=1))
        if prev_row is None:
            full = True
            rebuild_from = start

    # 6) 行情：全量拉 [start-45, end]；增量只拉窗口 [rebuild_from-45, end]。
    price_from = (start if full else rebuild_from) - PRICE_LOOKBACK
    ladder, market_causes = await load_prices(market_data, _symbols(live), price_from, end)

    # 7) 内存逐日推演（无 I/O）；仅窗口行待写。
    valuations = compute_daily_valuations(
        live,
        user_id=user_id,
        start=start,
        end=end,
        rebuild_from=rebuild_from,
        ladder=ladder,
        market_causes=market_causes,
        as_of=market_as_of,
        prev_row=prev_row,
        fifo_error=fifo_error_now,
        rebuilt_at=clock.now(),
    )
    build_mode = BUILD_MODE_FULL if full else BUILD_MODE_INCREMENTAL

    # 8) 全局无锚点：不写日行，state 标 blocked。
    if valuations.global_blocked:
        await repo.delete_all(user_id)
        await _reset_state(
            repo,
            user_id,
            clock=clock,
            count_all=count_all,
            version_sum=version_sum,
            max_updated=max_updated,
            last_status=BUILD_STATUS_BLOCKED,
            start_date=None,
            status_reason=valuations.global_reason,
        )
        # 监控点：整表被阻断（如无现金余额锚点），人工需立即关注。
        log.warning(
            "assets.build_blocked",
            user_id=str(user_id),
            reason=valuations.global_reason,
        )
        return AssetBuildSummary(
            mode=build_mode,
            rebuild_from=None,
            built_through=None,
            rows_written=0,
            rows_total=0,
            status=BUILD_STATUS_BLOCKED,
            assumption=None,
            data_as_of=market_as_of,
        )

    # 9) 落库：delete + insert + delete_after 与 state 同事务（失败原子回滚）。
    if full:
        await repo.delete_all(user_id)
    else:
        await repo.delete_range(user_id, rebuild_from, end)
    await repo.insert_rows(valuations.rows)
    if not full:
        await repo.delete_after(user_id, end)
    rows_count = await repo.count(user_id)
    overall_status = await repo.overall_status(user_id)
    state_reason = _state_reason(overall_status, fifo_error=fifo_error_now)

    # 监控点：每次构建都打 INFO 摘要；出现阻断/降级行再打 WARNING，附原因聚合与
    # 行情失败类别，无需复现即可从日志/告警直接定位（如退市标的缺口拉取失败）。
    blocked_rows = sum(1 for row in valuations.rows if row["status"] == ROW_STATUS_BLOCKED)
    degraded_rows = sum(1 for row in valuations.rows if row["status"] == ROW_STATUS_DEGRADED)
    log.info(
        "assets.build",
        user_id=str(user_id),
        mode=build_mode,
        status=overall_status,
        start=start.isoformat(),
        end=end.isoformat(),
        rows_written=len(valuations.rows),
        rows_total=rows_count,
        blocked_rows=blocked_rows,
        degraded_rows=degraded_rows,
        data_as_of=market_as_of.isoformat(),
    )
    if blocked_rows or degraded_rows:
        reasons: dict[str, int] = {}
        for row in valuations.rows:
            if row["status"] == ROW_STATUS_BLOCKED:
                key = str(row["status_reason"] or "unknown")
                reasons[key] = reasons.get(key, 0) + 1
        log.warning(
            "assets.build_degraded",
            user_id=str(user_id),
            mode=build_mode,
            start=start.isoformat(),
            end=end.isoformat(),
            blocked_rows=blocked_rows,
            degraded_rows=degraded_rows,
            blocked_reasons=reasons,
            market_causes=market_causes,
            fifo_error=fifo_error_now,
        )

    await _persist_state(
        repo,
        user_id,
        existing=state,
        fields={
            "start_date": start,
            "last_built_through": end,
            "last_built_at": clock.now(),
            "last_trade_updated_at": max_updated,
            "last_trade_count": count_all,
            "last_trade_version_sum": version_sum,
            "data_as_of": market_as_of,
            "assumption": "back_solved",
            "last_status": overall_status,
            "last_fifo_error": fifo_error_now,
            "status_reason": state_reason,
            "last_mode": build_mode,
            "last_rebuild_from": rebuild_from,
            "last_rows_written": len(valuations.rows),
            "definition_version": ASSET_DEFINITION_VERSION,
            "rows_count": rows_count,
        },
    )
    return AssetBuildSummary(
        mode=build_mode,
        rebuild_from=rebuild_from,
        built_through=end,
        rows_written=len(valuations.rows),
        rows_total=rows_count,
        status=overall_status,
        assumption="back_solved",
        data_as_of=market_as_of,
    )


def _to_point(row: AssetDaily) -> AssetPoint:
    return AssetPoint(
        id=row.id,
        trade_date=row.trade_date,
        cash_units=row.cash_units,
        market_value_units=row.market_value_units,
        total_assets_units=row.total_assets_units,
        holding_cost_units=row.holding_cost_units,
        unrealized_pnl_units=row.unrealized_pnl_units,
        holding_count=row.holding_count,
        cashflow_units=row.cashflow_units,
        prev_total_assets_units=row.prev_total_assets_units,
        daily_pnl_units=row.daily_pnl_units,
        daily_return_units=row.daily_return_units,
        status=row.status,
        status_reason=row.status_reason,
        definition_version=row.definition_version,
    )


def _missing_state(definition_version: str) -> AssetBuildState:
    """missing 可空性（设计 §7.1）：除 state/rows_count/last_mode/has_new_market_data/
    definition_version 外，last_* / assumption / status_reason / data_as_of 全部 null。

    两种 entry 共用：① 无 state（从未构建）；② state 存在但 `last_built_through IS NULL`
    （无流水 / 全部软删 / `end < start` 的清空构建）——后者的 DB 行仍带 last_mode=full、
    last_built_at 等值，但 API 必须按“尚未构建”展示，不得透出。
    """
    return AssetBuildState(
        state=STATE_MISSING,
        last_status=None,
        last_built_at=None,
        last_built_through=None,
        last_mode=None,
        last_rebuild_from=None,
        last_rows_written=0,
        rows_count=0,
        assumption=None,
        status_reason=None,
        has_new_market_data=False,
        definition_version=definition_version,
        data_as_of=None,
    )


async def _build_state_view(
    repo: AssetRepository,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    state: AssetBuildStateRow | None,
) -> AssetBuildState:
    """`build.state` 判定链（设计 §7.1，按序 P1 修正版）。"""
    if state is None:
        return _missing_state(ASSET_DEFINITION_VERSION)
    base = AssetBuildState(
        state=STATE_CURRENT,
        last_status=state.last_status,
        last_built_at=state.last_built_at,
        last_built_through=state.last_built_through,
        last_mode=state.last_mode,
        last_rebuild_from=state.last_rebuild_from,
        last_rows_written=state.last_rows_written,
        rows_count=state.rows_count,
        assumption=state.assumption,
        status_reason=state.status_reason,
        has_new_market_data=False,
        definition_version=state.definition_version or ASSET_DEFINITION_VERSION,
        data_as_of=state.data_as_of,
    )
    # 2) 全局 blocked（last_status=blocked 且无构建终点）。
    if state.last_status == BUILD_STATUS_BLOCKED and state.last_built_through is None:
        base.state = STATE_BLOCKED
        base.last_built_at = None
        base.last_mode = None
        base.last_rebuild_from = None
        base.last_rows_written = 0
        base.rows_count = 0
        base.assumption = None
        return base
    # 3) 无流水/全部软删：尚未构建（同样按 missing 可空性，不透出 DB 里的 last_*）。
    if state.last_built_through is None:
        return _missing_state(state.definition_version or ASSET_DEFINITION_VERSION)
    # 4) 口径版本升级 → 旧行混排，必须重建。
    if state.definition_version != ASSET_DEFINITION_VERSION:
        base.state = STATE_STALE
        return base
    # 5) 流水指纹（含软删）不一致 → 有新流水/修正。
    fingerprint = await repo.trade_fingerprint(user_id)
    if fingerprint != (
        state.last_trade_count,
        state.last_trade_version_sum,
        state.last_trade_updated_at,
    ):
        base.state = STATE_STALE
        return base
    # 6) 行情前进 → stale（并标记 has_new_market_data）。
    if state.data_as_of is None or market_data.as_of > state.data_as_of:
        base.state = STATE_STALE
        base.has_new_market_data = True
        return base
    # 7) 有新自然日。
    if clock.today() > state.last_built_through:
        base.state = STATE_STALE
    return base


async def list_assets(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    limit: int,
    cursor: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str | None,
    sort: str = "-trade_date",
) -> tuple[list[AssetPoint], str | None, AssetBuildState]:
    """纯读资产表（不落库、不触发构建）；返回（行, 下一页游标, build 摘要）。"""
    repo = AssetRepository(session)
    state = await repo.get_state(user_id)
    definition_version = state.definition_version if state is not None else ASSET_DEFINITION_VERSION
    rows, next_cursor = await repo.page(
        user_id,
        definition_version=definition_version,
        limit=limit,
        cursor=cursor,
        date_from=date_from,
        date_to=date_to,
        status=status,
        sort=sort,
    )
    build = await _build_state_view(
        repo, user_id=user_id, clock=clock, market_data=market_data, state=state
    )
    return [_to_point(row) for row in rows], next_cursor, build


async def get_asset_build_state(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
) -> tuple[AssetBuildState, str]:
    """只读构建摘要 + 当前口径版本（Dashboard 复用，避免复制 state 判定链）。"""
    repo = AssetRepository(session)
    state = await repo.get_state(user_id)
    definition_version = state.definition_version if state is not None else ASSET_DEFINITION_VERSION
    build = await _build_state_view(
        repo, user_id=user_id, clock=clock, market_data=market_data, state=state
    )
    return build, definition_version


def _jsonable(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


async def build_assets_job(
    queue: JobQueue,
    session: AsyncSession,
    job: dict[str, Any],
    clock: Clock,
    market_data: MarketDataSource,
) -> dict[str, Any]:
    """worker handler：recomputing(10) → 取消检查 → writing(80) → finalizing(100) → finish。

    job.result 键固定为 `{mode, rebuild_from, rows_written, rows_total, built_through,
    status, assumption, data_as_of}`（不扩 GET /jobs 响应）。
    """
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params: dict[str, Any] = dict(job.get("params") or {})
    mode = str(params.get("mode") or "auto")

    await queue.progress(session, job_id, percent=10, stage="recomputing", message=None)
    if await queue.is_cancel_requested(session, job_id):
        await queue.finish(session, job_id, status=JobStatus.CANCELLED, result={"cancelled": True})
        return {"job_id": job_id, "cancelled": True}

    await queue.progress(session, job_id, percent=80, stage="writing", message=None)
    summary = await build_assets(
        session, user_id=user_id, clock=clock, market_data=market_data, mode=mode
    )
    await queue.progress(session, job_id, percent=100, stage="finalizing", message=None)

    payload: dict[str, Any] = {
        "mode": summary.mode,
        "rebuild_from": _jsonable(summary.rebuild_from),
        "rows_written": summary.rows_written,
        "rows_total": summary.rows_total,
        "built_through": _jsonable(summary.built_through),
        "status": summary.status,
        "assumption": summary.assumption,
        "data_as_of": _jsonable(summary.data_as_of),
    }
    await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=payload)
    return {"job_id": job_id, **payload}


__all__ = [
    "ASSET_DEFINITION_VERSION",
    "FIFO_ERROR_REASON",
    "MARKET_UNCOVERED_REASON",
    "MISSING_PRICE_PREFIX",
    "NO_ANCHOR_REASON",
    "SAFETY_DAYS",
    "WATERMARK_OVERLAP",
    "AssetBuildState",
    "AssetBuildSummary",
    "AssetPoint",
    "build_assets",
    "build_assets_job",
    "compute_daily_valuations",
    "list_assets",
]
