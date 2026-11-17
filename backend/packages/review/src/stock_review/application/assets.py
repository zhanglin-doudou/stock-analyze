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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import SHANGHAI, Clock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_contracts.preferences import DEFAULT_MARKET_SESSIONS
from stock_core.fifo import build_rounds
from stock_ledger.models import Trade
from stock_platform.jobs import JobQueue
from stock_platform.market_models import DailyBar
from stock_platform.obs import get_logger
from stock_platform.ports import MarketDataSource, SyncableMarketSource
from stock_platform.repositories.users import UserSettingRepository
from stock_platform.symbols import needs_quote

from stock_review.application.bond_alias import build_bond_alias, canonical_code
from stock_review.application.bridge import _initial_cash
from stock_review.application.money_fund import (
    money_fund_balances,
    money_fund_codes,
    money_fund_value_units,
)
from stock_review.application.rebuild import rights_resolution, trades_to_events
from stock_review.application.rights import RightsResolution
from stock_review.application.valuation import (
    CASHFLOW_CATEGORIES,
    PRICE_LOOKBACK,
    blocked_price_reason,
    holding_delta,
    is_cash_balance,
    load_prices,
    new_stock_pending_codes,
    total_market_value,
)
from stock_review.models import AssetBuildState as AssetBuildStateRow
from stock_review.models import AssetDaily
from stock_review.repositories.assets import AssetRepository

log = get_logger("stock_review.assets")

# 资产表口径版本：bump 后旧 state 触发全量重建。
# 1.2：可转债发债代码归并上市代码（持仓/行情按 canonical），可转债市值固定 1 手=10 张。
# 1.4：新股中签/入帐通知行（金额 0）不再计持仓；待上市新股缺行情按认购成本兜底 degraded。
# 1.6：新股按认购成本兜底 / 可转债按买入价兜底 → 不再 degraded（视为正常估值），
#      新增 remark 列标注「当日资产含可转债」；仅非可转债按成本兜底仍 degraded。
# 1.7：周六/周日不写资产行（A 股无交易，去除周末噪声行）；周末资金划转并入下一交易日
#      现金流，prev_total 取上一已写工作日。
# 1.8：`new_stock` 语义收窄为「新股/可转债中签」——只有中签缴款（金额非 0）建仓，申购冻结/
#      解冻/还款/退款归资金划转（不建仓、不进打新收益）；上市后卖出归 `sell`。分类口径变化
#      影响持仓/成本/回合，bump 触发按新分类全量重建。
# 1.9：`new_stock` 进一步收窄为「新股/认购/申购」真正扣款行；新增 `broker_action`（券商行为：
#      申购中签/申购还款/新股入账/证券清理/托管等登记动作），不影响持仓与成本，金额非 0 时
#      按外部现金流处理。分类口径再次变化，bump 触发重建。
# 2.0：资产口径 = 现金 + 持仓市值（移除货币基金余额列）：货基按普通持仓计成本与市值；
#      超卖部分视同资金划转，不再标 degraded/data_error。口径变化，bump 触发全量重建。
# 2.1：只有 A 股/场内基金（`needs_quote`）读取行情；债券与伪代码（A40006/970172/119924 等）
#      不再请求行情、不阻断、不降级，统一按含费成本（买入价）估值并在 remark 留痕。
#      资产表/净值/指标/归因共用同一口径，bump 触发全量重建。
# 2.2：当日存在超卖（卖出数量超过可用持仓）时该日标 degraded，资产表页提供人工修订入口；
#      修订后该日重算恢复 ok。口径变化，bump 触发全量重建。
# 2.3：货币基金（现金等价物）不进 FIFO/回合/持仓/市值，余额并入现金列（总资产不变）；
#      识别见 `application.money_fund`。口径变化，bump 触发全量重建。
# 2.4：配股前置校验——零金额且该证券此前无持仓的 `rights` 流水按 `broker_action` 处理
#      （不计持仓/成本/FIFO/回合）；有持仓的配股与金额非 0 的配股不变。口径变化，bump 触发全量重建。
ASSET_DEFINITION_VERSION = "2.4"
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
FIFO_ERROR_REASON = "FIFO 数据异常（配股/送转等无可用持仓）"
OVERSELL_REASON = "超卖（卖出数量超过可用持仓），需人工补录成本"
MARKET_UNCOVERED_REASON = "行情未覆盖，按最近收盘估值"
MISSING_PRICE_PREFIX = "缺行情数据"
# 缺行情补拉一次后仍未取到：给用户的最终提示（构建整体原因 + 行级原因后缀）。
MISSING_QUOTE_FINAL_REASON = "缺少行情数据，请联系技术支持解决"
MISSING_QUOTE_HINT = "，请联系技术支持解决"
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
    remark: str | None
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
    # 当日交易时段提示（`market_open` 未收盘 / `sync_pending` 已收盘但行情未同步）；
    # 非工作日或无需提示时为 None。只读展示，不参与 stale 判定。
    session_notice: str | None = None


@dataclass(slots=True)
class _DailyValuations:
    """逐日推演结果：窗口内待写行 + 全局阻断信息 + 缺行情标的的受影响区间。"""

    rows: list[dict[str, object]]
    global_blocked: bool
    global_reason: str | None
    # 缺行情（`blocked` 行）的标的 → (首个受影响日期, 最后一个受影响日期)。
    # 只有当日持仓非零且「当日及之前无任何收盘价」的标的才计入；空表示没有 blocked 行。
    missing_quotes: dict[str, tuple[date, date]]


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


def _state_reason(last_status: str, *, fifo_error: bool, oversell: bool = False) -> str | None:
    """全局状态原因：全局 blocked 必有；FIFO 异常 / 超卖导致的整体 degraded 记录原因。"""
    if last_status == BUILD_STATUS_BLOCKED:
        return NO_ANCHOR_REASON
    if last_status != BUILD_STATUS_DEGRADED:
        return None
    reasons: list[str] = []
    if fifo_error:
        reasons.append(FIFO_ERROR_REASON)
    if oversell:
        reasons.append(OVERSELL_REASON)
    return "；".join(reasons) if reasons else None


def _row_status_reason(
    *,
    fifo_error: bool,
    uncovered: bool,
    oversell: bool = False,
) -> str | None:
    """行级 degraded 原因（FIFO 异常 / 超卖 / 行情未覆盖）。

    按成本估值的非行情持仓（债券、伪代码）与新股/可转债的认购成本兜底都视为正常估值，
    不产生 degraded，只在 remark 留痕。都不命中返回 NULL。
    """
    reasons: list[str] = []
    if fifo_error:
        reasons.append(FIFO_ERROR_REASON)
    if oversell:
        reasons.append(OVERSELL_REASON)
    if uncovered:
        reasons.append(MARKET_UNCOVERED_REASON)
    return "；".join(reasons) if reasons else None


def _with_missing_quote_hint(reason: str) -> str:
    """行级缺行情原因后追加最终提示（列宽 256 内；过长时截断标的清单而非提示本身）。"""
    return reason[: _REASON_MAX - len(MISSING_QUOTE_HINT)] + MISSING_QUOTE_HINT


def _cost_fallback_remark(symbols: list[str]) -> str | None:
    """按买入价估值的持仓（债券/伪代码）留痕；只写 remark，不改 status。"""
    if not symbols:
        return None
    return f"按买入价估值：{','.join(sorted(symbols))}"


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
    fifo_error_codes: set[str],
    rebuilt_at: datetime,
    bond_alias: dict[str, str] | None = None,
    oversell_proceeds: dict[str, int] | None = None,
    quotes_pull_attempted: bool = False,
    rights: RightsResolution | None = None,
) -> _DailyValuations:
    """内存重放 `[start, end]`，为 `[rebuild_from, end]` 生成资产日行（纯计算，无 I/O）。

    逐日推演口径见设计 §5.3：现金（余额行覆盖优先）/ 持仓数量 / 含费成本 / 外部现金流 /
    逐标的 floor 市值；缺行情 → 该日 blocked（数值 0、prev/pnl/return NULL）；FIFO 异常、
    当日存在超卖或 `date > as_of` 且仍有持仓 → degraded（按最近收盘估值）。**周六/周日不写行**
    （A 股无交易），周末的外部现金流/超卖降级标记并入下一交易日；`prev_total` 取上一**已写
    工作日**的非 blocked 总资产，全量首行取期初现金锚点，增量首行用落库的 `prev_row`。
    持仓/成本键按 `bond_alias` 归一为上市代码（发债代码不请求行情，设计 D3）。
    货币基金（现金等价物，2.3）：不进持仓/成本/市值/行情，余额并入写库的 `cash_units`，
    `total_assets = 现金(含货基) + 持仓市值` 不变；申赎是现金内部转换，红利/再投计收益。

    `quotes_pull_attempted=True` 表示调用方已为缺行情标的补拉过行情（设计 §6.5）：仍
    blocked 的行在原因后追加「请联系技术支持解决」；受影响标的与日期区间由
    `missing_quotes` 返回，供调用方做**一次**定向补拉。
    """
    alias = bond_alias or {}
    # 配股前置校验：零金额且无前置持仓的配股按 broker_action 处理（与 FIFO 同源）。
    rights = rights or RightsResolution(frozenset())
    # 新股待上市（认购缴款已入库、尚无行情）：按认购成本兜底估值，不阻断（设计 D11）。
    pending_codes = {canonical_code(alias, code) for code in new_stock_pending_codes(trades)}
    initial = _initial_cash(trades, start)
    if initial is None:
        return _DailyValuations(
            rows=[], global_blocked=True, global_reason=NO_ANCHOR_REASON, missing_quotes={}
        )
    initial_cash, _assumption = initial
    # 超卖部分的净卖款已随卖出流水进入现金余额；这里只把它从「收益」里剔除，
    # 不再并入期初（并入期初会让期初资产虚高，且超卖当日仍会虚增当日收益）。
    # 剔除只在日收益口径生效：`cashflow_units` 仍只记真实外部现金流，不把它标成资金划转。
    oversell_proceeds_by_trade = oversell_proceeds or {}
    # 货币基金 = 现金等价物：不进持仓/成本/市值/行情，余额并入现金列（2.3 口径）。
    fund_codes = money_fund_codes(trades)
    fund_balances: dict[str, int] = dict(money_fund_balances(trades, before=start))
    initial_total = initial_cash + sum(fund_balances.values())

    by_date = _group_trades_by_date(trades)
    cash_units = initial_cash
    holdings: dict[str, int] = {}
    cost_basis: dict[str, int] = {}
    # 上一**已写入行**（跳过周末后即上一交易日）的非 blocked 总资产；
    # None 表示首日之前或上一行 blocked。
    prior_total: int | None = None
    rows: list[dict[str, object]] = []
    missing_quotes: dict[str, tuple[date, date]] = {}
    window_started = False
    # 周末不写行，但周末发生的资金划转仍需计入下一交易日的当日现金流。
    pending_cashflow = 0
    # 待剔除的超卖净卖款（周末顺延到下一交易日；只影响日收益，不进 cashflow 展示）。
    pending_oversell = 0
    # 周末/节假日顺延：未写行日期上的超卖合并到下一已写工作日的降级标记。
    pending_oversell_day = False

    cursor_date = start
    while cursor_date <= end:
        day_trades = by_date.get(cursor_date, [])
        cashflow_units = 0
        oversell_adjust = 0
        day_oversell = False
        day_cash_delta = 0
        balance_override: int | None = None

        for trade in day_trades:
            trade_category = rights.category(trade)
            code = trade.security_code
            if code and code in fund_codes:
                # 申赎金额照常进出券商现金，货基余额单独累计；两者之和即「现金余额」。
                # 红利发放/再投行金额为 0、以份额增加余额 → 计入收益（不进外部现金流）。
                fund_balances[code] = max(
                    0, fund_balances.get(code, 0) + money_fund_value_units(trade)
                )
                day_cash_delta += trade.amount_units
                if is_cash_balance(trade):
                    balance_override = int(trade.cash_balance_units or 0)
                continue
            if code:
                code = canonical_code(alias, code)  # 发债代码归并上市代码（设计 D3）
                delta = holding_delta(
                    trade_category, trade.quantity_units, amount_units=trade.amount_units
                )
                if delta:
                    # 超卖部分视同资金划转：持仓按 0 截断，不产生负持仓/负市值。
                    old_qty = holdings.get(code, 0)
                    new_qty = max(0, old_qty + delta)
                    delta = new_qty - old_qty
                    holdings[code] = new_qty
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
            # 超卖净卖款：卖款已在现金里，只从当日收益中剔除，不算外部现金流。
            if trade.id in oversell_proceeds_by_trade:
                day_oversell = True
                oversell_adjust += oversell_proceeds_by_trade[trade.id]
            if is_cash_balance(trade):
                balance_override = int(trade.cash_balance_units or 0)

        if balance_override is not None:
            cash_units = balance_override
        else:
            cash_units += day_cash_delta
        pending_cashflow += cashflow_units
        pending_oversell += oversell_adjust
        pending_oversell_day = pending_oversell_day or day_oversell

        # 周六/周日不写资产行（A 股无交易，避免周末噪声行）；周末流水并入下一交易日。
        if cursor_date.weekday() >= 5:
            cursor_date += timedelta(days=1)
            continue

        market_value_units, missing, cost_fallback, _new_stock_fallback = total_market_value(
            holdings,
            ladder,
            cursor_date,
            cost_basis=cost_basis,
            new_stock_codes=pending_codes,
        )
        # 债券/伪代码按买入价估值（含费成本）：不读行情、不阻断、不降级，只在 remark 留痕。
        nonzero_holdings = sum(1 for qty in holdings.values() if qty != 0)

        # blocked 判定优先：缺行情 → 该日硬阻断（数值 0，prev/pnl/return NULL）。
        if missing:
            reason = blocked_price_reason(MISSING_PRICE_PREFIX, missing, market_causes)[
                :_REASON_MAX
            ]
            if quotes_pull_attempted:
                # 已按标的集合补拉过一次仍缺：行级原因给出最终提示（构建级见 state reason）。
                reason = _with_missing_quote_hint(reason)
            for symbol in missing:
                first, last = missing_quotes.get(symbol, (cursor_date, cursor_date))
                missing_quotes[symbol] = (min(first, cursor_date), max(last, cursor_date))
            if cursor_date >= rebuild_from:
                rows.append(
                    _blocked_row(
                        user_id=user_id,
                        trade_date=cursor_date,
                        reason=reason,
                        rebuilt_at=rebuilt_at,
                    )
                )
                # 窗口首行即 blocked：后续行 prev_total 必须为 NULL（链路在此断开）。
                window_started = True
            prior_total = None
            pending_cashflow = 0
            pending_oversell = 0
            pending_oversell_day = False
            cursor_date += timedelta(days=1)
            continue

        # 现金列 = 券商现金 + 货基余额（现金等价物），总资产恒等式不变。
        cash_total_units = cash_units + sum(fund_balances.values())
        total_assets_units = cash_total_units + market_value_units
        holding_cost_units = sum(
            cost for code, cost in cost_basis.items() if holdings.get(code, 0) != 0
        )
        unrealized_pnl_units = market_value_units - holding_cost_units
        uncovered = cursor_date > as_of and nonzero_holdings > 0
        # FIFO 异常只影响持有问题代码的那些天；对无 FIFO 问题代码持仓的其他天不降级。
        fifo_error_today = any(code in fifo_error_codes for code in holdings)
        # 超卖：当日有卖出数量超过可用持仓 → 该日降级并提示人工补录成本（修订后重算恢复）。
        oversell_today = pending_oversell_day
        status = (
            ROW_STATUS_DEGRADED
            if (fifo_error_today or oversell_today or uncovered)
            else ROW_STATUS_OK
        )
        status_reason = (
            _row_status_reason(
                fifo_error=fifo_error_today, uncovered=uncovered, oversell=oversell_today
            )
            if status == ROW_STATUS_DEGRADED
            else None
        )

        # prev_total：首行取期初锚点；否则取上一**已写入行**（周末已跳过；上一行 blocked → NULL）。
        # 增量窗口首行用落库的 prev_row（窗口外行不重写，但必须与旧行衔接）。
        if cursor_date >= rebuild_from:
            if not window_started:
                effective_prev = prev_row_total(
                    prev_row, cursor_date, start, rebuild_from, initial_total
                )
                window_started = True
            else:
                effective_prev = prior_total
            daily_pnl_units = (
                None
                if effective_prev is None
                else total_assets_units - pending_cashflow - pending_oversell - effective_prev
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
                    "cash_units": cash_total_units,
                    "market_value_units": market_value_units,
                    "total_assets_units": total_assets_units,
                    "holding_cost_units": holding_cost_units,
                    "unrealized_pnl_units": unrealized_pnl_units,
                    "holding_count": nonzero_holdings,
                    "cashflow_units": pending_cashflow,
                    "prev_total_assets_units": effective_prev,
                    "daily_pnl_units": daily_pnl_units,
                    "daily_return_units": daily_return_units,
                    "status": status,
                    "status_reason": status_reason,
                    "remark": _cost_fallback_remark(cost_fallback),
                    "definition_version": ASSET_DEFINITION_VERSION,
                    "rebuilt_at": rebuilt_at,
                }
            )

        prior_total = total_assets_units
        pending_cashflow = 0
        pending_oversell = 0
        pending_oversell_day = False
        cursor_date += timedelta(days=1)

    return _DailyValuations(
        rows=rows,
        global_blocked=False,
        global_reason=None,
        missing_quotes=missing_quotes,
    )


def prev_row_total(
    prev_row: AssetDaily | None,
    trade_date: date,
    start: date,
    rebuild_from: date,
    initial_total: int,
) -> int | None:
    """窗口首行的 `prev_total`：全量取期初锚点，增量衔接窗口外上一已写行。

    周末不写行，因此窗口首行可能是 `rebuild_from` 之后的第一个工作日；
    全量（`rebuild_from <= start`）一律取期初锚点，增量取落库的 `prev_row`
    （上一行 blocked → NULL，链路断开）。
    """
    if rebuild_from <= start:
        return initial_total
    if trade_date >= rebuild_from:
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
        "remark": None,
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


def _symbols(
    live: list[Trade],
    bond_alias: dict[str, str] | None = None,
    rights: RightsResolution | None = None,
) -> list[str]:
    """需要请求行情的持仓标的（`needs_quote`：A 股/场内基金）。

    债券与伪代码（A40006 等）不读行情、按买入价估值，因此不发起行情请求。发债代码
    归并为上市代码：行情请求只发 canonical 代码（申购代码无行情，设计 D3）。
    `rights` 提供配股有效分类：降级的零金额配股不产生持仓，也就不请求行情。
    """
    alias = bond_alias or {}
    rights = rights or RightsResolution(frozenset())
    fund_codes = money_fund_codes(live)
    return sorted(
        {
            canonical_code(alias, trade.security_code)
            for trade in live
            if trade.security_code
            and trade.security_code not in fund_codes
            and needs_quote(trade.security_code)
            and holding_delta(
                rights.category(trade), trade.quantity_units, amount_units=trade.amount_units
            )
            != 0
        }
    )


def latest_workday(day: date) -> date:
    """最近工作日（周六/周日回退到周五）；资产表周末不写行。

    构建终点、状态判定（本模块与 metrics 门禁）共用，保证「周末不判 stale」口径一致。
    """
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


# A 股默认关闭时刻（Asia/Shanghai）和盘后数据就绪时刻。
# 生产值可被用户偏好 `market_session_prefs` 覆盖——调用方从 user model 读出后注入。
DEFAULT_DATA_READY_AFTER = DEFAULT_MARKET_SESSIONS["data_ready_after"]  # "16:30"


def _parse_hour_min(clock_str: str) -> tuple[int, int]:
    """`"16:30"` → (16, 30)；非法回退默认 (16, 30)。"""
    try:
        h, m = clock_str.split(":")
        hh, mm = int(h), int(m)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except (AttributeError, ValueError):
        pass
    return 16, 30


DEFAULT_CLOSE_HOUR, _ = _parse_hour_min(DEFAULT_MARKET_SESSIONS["sessions"][-1]["close"])


async def _load_market_prefs(session: AsyncSession, user_id: uuid.UUID) -> tuple[int, str, bool]:
    """加载用户行情偏好并规整为 (close_hour, data_ready_after, session_notice_enabled)。

    优先读 user_setting.market_session_prefs；缺省回退 `DEFAULT_MARKET_SESSIONS`。
    被 `list_assets` / `get_asset_build_state` / 构建入口共用，避免每个调用方各自散查。
    """
    effective = await UserSettingRepository(session).get_effective(user_id)
    ms = effective.market_sessions
    # 从 sessions 末段 close 解析关闭时刻
    sessions = ms.get("sessions") if isinstance(ms, dict) else None
    close_hour = DEFAULT_CLOSE_HOUR
    if isinstance(sessions, list) and sessions:
        last = sessions[-1]
        if isinstance(last, dict):
            close, _ = _parse_hour_min(str(last.get("close", "15:00")))
            close_hour = close
    data_ready_after = (
        str(ms.get("data_ready_after", DEFAULT_DATA_READY_AFTER))
        if isinstance(ms, dict)
        else DEFAULT_DATA_READY_AFTER
    )
    enabled = ms.get("session_notice_enabled", True) if isinstance(ms, dict) else True
    return close_hour, data_ready_after, bool(enabled)


def valuation_end(
    clock: Clock,
    *,
    data_ready_after: str = DEFAULT_DATA_READY_AFTER,
) -> date:
    """资产表可估值的最近盘后就绪日。

    - 周末/节假日沿用「最近工作日」；
    - 工作日当前时间 < `data_ready_after`（默认 16:30）→ 前一工作日（当日盘后数据未就绪）；
    - ≥ `data_ready_after` → 当日。

    构建终点与 stale 判定（本模块与 metrics 门禁）共用本口径。
    """
    today = clock.today()
    end = latest_workday(today)
    close_h, close_m = _parse_hour_min(data_ready_after)
    now_sh = clock.now().astimezone(SHANGHAI)
    if end == today and (now_sh.hour, now_sh.minute) < (close_h, close_m):
        end = latest_workday(end - timedelta(days=1))
    return end


def _session_notice(
    clock: Clock,
    market_as_of: date | None,
    *,
    close_hour: int = DEFAULT_CLOSE_HOUR,
    data_ready_after: str = DEFAULT_DATA_READY_AFTER,
) -> str | None:
    """当日交易时段/数据就绪提示（只读展示，不参与判定）：

    - `market_open`：当日为工作日且未收盘——资产表不含当日数据；
    - `sync_pending`：当日为工作日、已收盘，但盘后数据未就绪（< data_ready_after）
      或行情未同步到当日——提示盘后稍等。
    """
    today = clock.today()
    if latest_workday(today) != today:
        return None
    now_sh = clock.now().astimezone(SHANGHAI)
    if now_sh.hour < close_hour:
        return "market_open"
    ready_h, ready_m = _parse_hour_min(data_ready_after)
    if (now_sh.hour, now_sh.minute) < (ready_h, ready_m):
        return "sync_pending"
    if market_as_of is None or market_as_of < today:
        return "sync_pending"
    return None


def align_to_trading_day(day: date, latest_bar_date: date | None) -> date:
    """把自然日对齐到 ≤ day 的最后一个交易日（纯函数，便于确定性单测）。

    `latest_bar_date` 为行情缓存（`market.daily_bar`）中 ≤ day 的最大交易日；
    空库/replay 无行情时回退 `latest_workday`（只剔除周末，不含法定节假日）。
    """
    return latest_bar_date if latest_bar_date is not None else latest_workday(day)


async def last_trading_day(session: AsyncSession, day: date) -> date:
    """≤ day 的最后一个交易日；判定源复用行情缓存的最大交易日，不新建日历。

    取 `market.daily_bar` 中 `trade_date <= day` 的最大值：该表只含真实交易日的
    收盘行，故最大值即交易日。数据尚未同步到 `day` 时返回更早的可用交易日
    （归因的板块时点取「可用」口径，偏保守）；表内无任何行时回退工作日判定。
    """
    latest = await session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date <= day)
    )
    return align_to_trading_day(day, latest)


async def _market_as_of(session: AsyncSession, market_data: MarketDataSource, clock: Clock) -> date:
    """行情覆盖日（构建与状态判定必须同口径，跨进程一致）。

    live+缓存模式（`CachedMarketDataSource`）的端口 `as_of` 是**进程内近似值**
    （未读过 bar 时退化为业务今天），API 与 worker 会得到不同结果；权威覆盖在
    `market.daily_bar` 的最大交易日。replay / 纯透传 live 没有本地缓存，直接用端口值。
    """
    if getattr(market_data, "get_cached_bars", None) is not None:
        latest = await session.scalar(
            select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date <= clock.today())
        )
        if latest is not None:
            return latest
    return market_data.as_of


# 构建前置：行情缓存完全没有覆盖资产表窗口时，API-082 直接返回 200-失败（不入队）。
@dataclass(frozen=True)
class MarketDataMissing:
    """缺行情提示（reason / what_to_do 由 API 边界原样透出，前端零口径）。"""

    reason: str
    what_to_do: str


async def precheck_market_data(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    data_ready_after: str | None = None,
) -> MarketDataMissing | None:
    """构建前置检查：行情缓存没有任何覆盖资产表窗口的交易日 → 返回缺行情提示。

    与 `build_assets` 的 `_market_as_of` 同源判定（`market.daily_bar` 最大交易日）：
    - 仅对 live+本地缓存端口生效；replay / 纯透传端口没有本地缓存，缺个别标的属于
      既有 blocked/degraded 行语义（设计 §4.1 AT-08），不在前置拦截；
    - 缓存全空/整段未覆盖时也不再拦：可补拉的行情源由 `build_assets` 在构建内补拉一次
      （设计 §6.5），补拉后仍缺才以 blocked 行 + 最终提示表达；只有「有本地缓存却无法
      补拉」的端口保留前置拦截；
    - 无有效流水（构建=清空 + reset）或构建窗口为空（时钟回拨）→ 不算缺行情；
    - 缓存最新交易日 ≥ 窗口起点 → 可建（个别日期/标的缺行情仍走既有降级语义）。

    `data_ready_after` 从用户偏好读取后注入；缺省用 `DEFAULT_DATA_READY_AFTER`。
    """
    if getattr(market_data, "get_cached_bars", None) is None:
        return None
    if isinstance(market_data, SyncableMarketSource):
        # 构建内会按缺失标的 + 缺失区间补拉一次，不再判成「doomed job」。
        return None
    repo = AssetRepository(session)
    trades_all = await repo.load_trades_including_deleted(user_id)
    live = [trade for trade in trades_all if trade.deleted_at is None]
    if not live:
        return None
    start = min(trade.trade_date for trade in live)
    if data_ready_after is None:
        _, data_ready_after, _ = await _load_market_prefs(session, user_id)
    end = valuation_end(clock, data_ready_after=data_ready_after)
    if end < start:
        return None
    latest = await session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date <= clock.today())
    )
    if latest is not None and latest >= start:
        return None
    coverage = (
        f"最新交易日 {latest.isoformat()}" if latest is not None else "没有任何已同步的交易日"
    )
    return MarketDataMissing(
        reason=(
            f"缺少行情数据：本地行情缓存{coverage}，未覆盖资产表窗口 "
            f"{start.isoformat()}~{end.isoformat()}，本次构建未执行。"
        ),
        what_to_do=MISSING_QUOTE_FINAL_REASON,
    )


async def _pull_missing_quotes(
    market_data: SyncableMarketSource,
    missing: dict[str, tuple[date, date]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按「缺失标的 + 缺失日期区间」补拉一次日线，返回 (补拉的标的, 失败的标的)。

    区间取「首个缺行情日期 − `PRICE_LOOKBACK`」到「最后一个缺行情日期」：估值只需要当日
    或之前最近一个收盘价，向前多留回溯窗是为了在停牌/节假日/上市首日之前也能找到可用
    的前收盘；不拉资产表窗口以外的更早区间。`sync_daily_bars` 内部只补真实缺口（已覆盖
    区间与「上游确认无数据」的负缓存都不会重复请求），因此退市/停牌标的不会被反复拉取。
    **每个标的至多调用一次，不重试**；单个标的失败不影响其他标的。
    """
    pulled: list[str] = []
    failed: list[str] = []
    for symbol, (first, last) in sorted(missing.items()):
        start = first - PRICE_LOOKBACK
        if start > last:
            continue
        pulled.append(symbol)
        try:
            _bars, symbol_failed = await market_data.sync_daily_bars([symbol], start, last)
        except ApiError as exc:
            # 单个标的的行情源异常（限流/不支持该代码等）不影响其余标的与本轮构建。
            failed.append(symbol)
            log.warning(
                "assets.quotes_pull_failed",
                symbol=symbol,
                start=start.isoformat(),
                end=last.isoformat(),
                code=str(exc.code),
                error=exc.detail,
            )
            continue
        if symbol_failed:
            failed.append(symbol)
    return tuple(pulled), tuple(failed)


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
    # 偏好只查一次：data_ready_after 控制 valuation_end 口径，close_hour / notice_enabled
    # 构建内不直接用（那是展示层），但加载成本极低也一并取，便于后续扩展。
    _, data_ready_after, _ = await _load_market_prefs(session, user_id)
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
    # 资产表只在已收盘的工作日写行（设计 §5.3 + 收盘口径）：末行日期对齐到最近
    # 已收盘工作日——周末/未收盘当日不写行，避免水位线停在没有收盘行情的日期。
    end = valuation_end(clock, data_ready_after=data_ready_after)

    # 可转债发债→上市代码别名（设计 D3）：回合/持仓/行情请求统一用 canonical 代码。
    bond_alias, bond_alias_unresolved = build_bond_alias(
        (trade.security_code, trade.security_name) for trade in live
    )
    if bond_alias_unresolved:
        log.warning(
            "assets.bond_alias_unresolved",
            user_id=str(user_id),
            codes=bond_alias_unresolved,
        )

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

    # 配股前置校验（2.4）：零金额且此前无持仓的配股按 broker_action 处理。
    # FIFO/回合、资产表、净值、归因共用同一判定结果，避免各层各自过滤。
    rights = rights_resolution(live, bond_alias=bond_alias)
    fifo_result = build_rounds(trades_to_events(live, bond_alias=bond_alias))
    # 超卖：卖款视同资金划转（计现金、不计收益），行级标 degraded 提供人工修订入口；
    # 它不再算「FIFO 异常」——其它 FIFO 数据异常（配股/送转无持仓等）语义不变。
    fifo_error_codes: set[str] = {
        e.security_code for e in fifo_result.data_errors if e.kind != "oversell"
    }
    fifo_error_now = bool(fifo_error_codes)
    oversell_now = any(e.kind == "oversell" for e in fifo_result.data_errors)
    market_as_of = await _market_as_of(session, market_data, clock)
    market_changed = state is not None and state.data_as_of != market_as_of
    # blocked 行是「缺行情占位行」，非终态：其分布可能覆盖历史任意日期，增量 45 天
    # 窗口无法保证全部重算；且行情补齐可能只补到已被全局 market_as_of 覆盖的日期
    # （market_as_of 不变 → 看起来无变化）。因此只要表里还有 blocked 行，就必须全量重建。
    had_blocked = state is not None and await repo.has_blocked_rows(user_id)

    # 3) 全量判定（§6.2）：任何无法安全定位的变更都收敛到全量。
    full = (
        mode == "full"
        or state is None
        or state.definition_version != ASSET_DEFINITION_VERSION
        or state.last_built_through is None
        or state.last_fifo_error != fifo_error_now
        or start != state.start_date
        or end < state.last_built_through
        or had_blocked
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
    #    周末不写行，窗口边界可能落在周六/周日，因此取「rebuild_from 之前最近的已写行」。
    prev_row: AssetDaily | None = None
    if not full and rebuild_from > start:
        prev_row = await repo.latest(
            user_id,
            definition_version=ASSET_DEFINITION_VERSION,
            on_or_before=rebuild_from - timedelta(days=1),
        )
        if prev_row is None:
            full = True
            rebuild_from = start

    # 6) 行情：全量拉 [start-45, end]；增量只拉窗口 [rebuild_from-45, end]。
    price_from = (start if full else rebuild_from) - PRICE_LOOKBACK
    symbols = _symbols(live, bond_alias, rights)
    ladder, market_causes = await load_prices(market_data, symbols, price_from, end)
    oversell_proceeds = {
        e.trade_id: e.proceeds_units for e in fifo_result.data_errors if e.kind == "oversell"
    }
    quotes_pull_attempted = False

    def _valuate() -> _DailyValuations:
        """用当前行情视图（`ladder` / `market_causes` / `market_as_of`）做一次纯内存推演。"""
        return compute_daily_valuations(
            live,
            user_id=user_id,
            start=start,
            end=end,
            rebuild_from=rebuild_from,
            ladder=ladder,
            market_causes=market_causes,
            as_of=market_as_of,
            prev_row=prev_row,
            fifo_error_codes=fifo_error_codes,
            rebuilt_at=clock.now(),
            bond_alias=bond_alias,
            oversell_proceeds=oversell_proceeds,
            quotes_pull_attempted=quotes_pull_attempted,
            rights=rights,
        )

    # 7) 内存逐日推演（无 I/O）；仅窗口行待写。
    valuations = _valuate()

    # 7b) 缺行情自愈（设计 §6.5）：先按 blocked 行定位「缺的标的 + 缺失日期区间」，向
    #     可补拉的行情源（live + 本地缓存）**补拉一次**，再用补拉后的行情重算一次。
    #     只补拉一次、不循环不递归；补拉后仍缺则 blocked 行与 state 原因给出最终提示。
    if valuations.missing_quotes and isinstance(market_data, SyncableMarketSource):
        quotes_pull_attempted = True
        pulled, pull_failed = await _pull_missing_quotes(market_data, valuations.missing_quotes)
        log.info(
            "assets.quotes_pull",
            user_id=str(user_id),
            symbols=pulled,
            failed=pull_failed,
        )
        if pulled:
            ladder, market_causes = await load_prices(market_data, symbols, price_from, end)
            # 补拉可能推进行情覆盖日：重算 as_of，避免把刚补齐的日期仍按「未覆盖」降级。
            market_as_of = await _market_as_of(session, market_data, clock)
            valuations = _valuate()

    build_mode = BUILD_MODE_FULL if full else BUILD_MODE_INCREMENTAL

    # 配股降级留痕：页面不展示，仅在构建日志里给一条 warning（笔数 + 涉及代码），
    # 便于运维判断是「券商登记行」还是账单分类需要修。
    if rights.demoted:
        demoted_codes = sorted(
            {
                canonical_code(bond_alias or {}, trade.security_code)
                for trade in live
                if trade.id in rights.demoted
            }
        )
        log.warning(
            "assets.rights_demoted",
            user_id=str(user_id),
            count=len(rights.demoted),
            codes=demoted_codes,
        )

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
    state_reason = _state_reason(overall_status, fifo_error=fifo_error_now, oversell=oversell_now)
    blocked_rows = sum(1 for row in valuations.rows if row["status"] == ROW_STATUS_BLOCKED)
    if blocked_rows and quotes_pull_attempted:
        # 补拉一次后仍缺行情：state 原因给用户最终提示（前端直接渲染后端文案，不做判断）。
        state_reason = (
            f"{state_reason}；{MISSING_QUOTE_FINAL_REASON}"
            if state_reason
            else MISSING_QUOTE_FINAL_REASON
        )

    # 监控点：每次构建都打 INFO 摘要；出现阻断/降级行再打 WARNING，附原因聚合与
    # 行情失败类别，无需复现即可从日志/告警直接定位（如退市标的缺口拉取失败）。
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
        blocked_reasons: dict[str, int] = {}
        degraded_reasons: dict[str, int] = {}
        for row in valuations.rows:
            status = row["status"]
            reason = str(row["status_reason"] or "unknown")
            if status == ROW_STATUS_BLOCKED:
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                continue
            if status != ROW_STATUS_DEGRADED:
                continue
            for part in reason.split("；"):
                degraded_reasons[part] = degraded_reasons.get(part, 0) + 1
        log.warning(
            "assets.build_degraded",
            user_id=str(user_id),
            mode=build_mode,
            start=start.isoformat(),
            end=end.isoformat(),
            blocked_rows=blocked_rows,
            degraded_rows=degraded_rows,
            blocked_reasons=blocked_reasons,
            degraded_reasons=degraded_reasons,
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
        remark=row.remark,
        definition_version=row.definition_version,
    )


def _missing_state(definition_version: str, session_notice: str | None = None) -> AssetBuildState:
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
        session_notice=session_notice,
    )


async def _build_state_view(
    repo: AssetRepository,
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    state: AssetBuildStateRow | None,
    close_hour: int = DEFAULT_CLOSE_HOUR,
    data_ready_after: str = DEFAULT_DATA_READY_AFTER,
    session_notice_enabled: bool = True,
) -> AssetBuildState:
    """`build.state` 判定链（设计 §7.1，按序 P1 修正版）。

    `close_hour` / `data_ready_after` 从用户偏好 `market_session_prefs` 解析后注入；
    未注入时用默认值（15:00 / 16:30），与 `effective_market_sessions` 一致。
    """
    # 当日时段提示（未收盘 / 已收盘但行情未同步）：所有分支都透出，与 state 判定正交。
    market_as_of = await _market_as_of(session, market_data, clock)
    notice = (
        _session_notice(
            clock,
            market_as_of,
            close_hour=close_hour,
            data_ready_after=data_ready_after,
        )
        if session_notice_enabled
        else None
    )
    if state is None:
        return _missing_state(ASSET_DEFINITION_VERSION, notice)
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
        session_notice=notice,
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
        return _missing_state(state.definition_version or ASSET_DEFINITION_VERSION, notice)
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
    # 6) 行情前进 + 盘后数据已就绪 → stale（并标记 has_new_market_data）。
    #    data_ready_after 前即使 market_as_of 前进也不判 stale（行情 bar 可能仍会更新）。
    now_sh = clock.now().astimezone(SHANGHAI)
    ready_h, ready_m = _parse_hour_min(data_ready_after)
    data_ready = (now_sh.hour, now_sh.minute) >= (ready_h, ready_m)
    if data_ready and (state.data_as_of is None or market_as_of > state.data_as_of):
        base.state = STATE_STALE
        base.has_new_market_data = True
        return base
    # 7) 有新的已收盘工作日（按 data_ready_after 口径）；周末/未就绪当日不判 stale。
    if valuation_end(clock, data_ready_after=data_ready_after) > state.last_built_through:
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
    close_hour, data_ready_after, notice_enabled = await _load_market_prefs(session, user_id)
    build = await _build_state_view(
        repo,
        session,
        user_id=user_id,
        clock=clock,
        market_data=market_data,
        state=state,
        close_hour=close_hour,
        data_ready_after=data_ready_after,
        session_notice_enabled=notice_enabled,
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
    close_hour, data_ready_after, notice_enabled = await _load_market_prefs(session, user_id)
    build = await _build_state_view(
        repo,
        session,
        user_id=user_id,
        clock=clock,
        market_data=market_data,
        state=state,
        close_hour=close_hour,
        data_ready_after=data_ready_after,
        session_notice_enabled=notice_enabled,
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
    "DEFAULT_CLOSE_HOUR",
    "DEFAULT_DATA_READY_AFTER",
    "FIFO_ERROR_REASON",
    "MARKET_UNCOVERED_REASON",
    "MISSING_PRICE_PREFIX",
    "MISSING_QUOTE_FINAL_REASON",
    "NO_ANCHOR_REASON",
    "OVERSELL_REASON",
    "SAFETY_DAYS",
    "WATERMARK_OVERLAP",
    "AssetBuildState",
    "AssetBuildSummary",
    "AssetPoint",
    "MarketDataMissing",
    "align_to_trading_day",
    "build_assets",
    "build_assets_job",
    "compute_daily_valuations",
    "last_trading_day",
    "list_assets",
    "precheck_market_data",
    "valuation_end",
]
