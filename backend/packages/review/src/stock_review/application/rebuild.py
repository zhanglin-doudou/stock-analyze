"""回合重建应用层：ledger 明细 → FIFO 事件 → 回合落库（全量 / 脏窗口增量）。

设计要点：
- `trades_to_events` 是**纯映射**（可单测，无 I/O、无 wall-clock）：ledger 的
  `category`/`side` → `FifoEvent`；送转按摘要 + 数量 + 零金额识别为 BONUS。
- 回合 ID 确定性：`rnd_` + sha256(user_id|security_code|key)[:32]，重复 rebuild 的
  ID 与数值完全一致（幂等）。
- period 脏窗口：区间内 trade + 上次 rebuild 后 `updated_at` 变化的 trade 所在证券，
  安全回看 `LOOKBACK_DAYS=400` 天扩大窗口；再并入「区间起点仍持仓（open 回合）」的证券。
  对脏证券**重建其全历史**——FIFO 是路径依赖的，只从窗口起算会配对错买入 lot。
- 事务由调用方（worker 的 tenant tx）持有；本模块不自行 commit/rollback。
- 无 trade 时也要成功返回（full 清空全部回合，period 不动其他证券）。
"""

from __future__ import annotations

import calendar
import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import JobStatus, Side
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.fifo import EventKind, FifoEvent, build_rounds
from stock_core.fifo import Round as FifoRound
from stock_ledger.models import Trade
from stock_platform.jobs import JobQueue

from stock_review.models import Round, RoundBuildState
from stock_review.repositories.rounds import FillInput, RoundInput, RoundRepository

# 安全回看天数：覆盖「一次卖出可能消耗很久之前买入的 lot」的最坏情况。
# 400 自然日 ≈ 275 交易日，足以覆盖「持有超过一年」的常规持仓。
LOOKBACK_DAYS = 400

_VALID_SCOPES: frozenset[str] = frozenset({"full", "period"})

# ledger `trade.category` → FIFO 事件类型；未登记/空值按 OTHER 处理（bridge 计入 other）。
_KIND_BY_CATEGORY: dict[str, EventKind] = {
    "buy": EventKind.BUY,
    "sell": EventKind.SELL,
    "dividend": EventKind.DIVIDEND,
    "bonus": EventKind.BONUS,
    "rights": EventKind.RIGHTS,
    "interest": EventKind.INTEREST,
    "fee": EventKind.FEE,
    "transfer": EventKind.TRANSFER,
    "new_stock": EventKind.NEW_STOCK,
    "other": EventKind.OTHER,
}

# 送转识别关键词（券商摘要：送股/转增/送转），命中且零金额、正数量 → BONUS。
_BONUS_KEYWORDS: tuple[str, ...] = ("送股", "转增", "送转")


@dataclass(slots=True)
class RebuildResult:
    """一次 rebuild 的结果摘要（worker job result / 测试断言用）。"""

    scope: str
    securities: tuple[str, ...]
    rounds_total: int
    rounds_closed: int
    rounds_open: int
    fills: int
    data_errors: int
    window_from: date | None
    window_to: date | None
    rebuilt_at: datetime
    full: bool


def _is_bonus(trade: Trade) -> bool:
    """送转识别：`category=other` + 摘要含送转词 + 正数量 + 零金额。"""
    if trade.quantity_units <= 0 or trade.amount_units != 0:
        return False
    summary = trade.summary or ""
    return any(keyword in summary for keyword in _BONUS_KEYWORDS)


def _kind_for(trade: Trade) -> EventKind:
    """ledger trade → FIFO EventKind（映射规则见 INTERFACE §3.3）。"""
    # 对账自动补差行（side=missing）没有买卖方向语义，按 OTHER 处理（bridge 计入 other）。
    if trade.side == Side.MISSING.value:
        return EventKind.OTHER
    category = (trade.category or "").strip().lower()
    if category == "other" and _is_bonus(trade):
        return EventKind.BONUS
    return _KIND_BY_CATEGORY.get(category, EventKind.OTHER)


def trades_to_events(trades: list[Trade] | tuple[Trade, ...]) -> list[FifoEvent]:
    """ledger Trade → `FifoEvent` 的纯映射（不碰 session、不读系统时间）。

    口径：
    - `amount_units` 原样透传：ledger 里已是带符号净现金流（买入为负、卖出/股息/利息为正）；
    - `dividend_tax_units` 保持 0：红利税是独立 `category=fee` 行（bridge 计入 fees），
      DIVIDEND 的 `amount_units` 已是税后净额，不重复扣税；
    - 送转：`quantity_units` = 新增股数、`ratio_units` = 0（引擎按数量直接加股，成本不变）；
    - `line_no` 空值按 0 处理（稳定序需要 int）。
    """
    events: list[FifoEvent] = []
    for trade in trades:
        events.append(
            FifoEvent(
                trade_id=trade.id,
                trade_date=trade.trade_date,
                kind=_kind_for(trade),
                security_code=trade.security_code,
                security_name=trade.security_name,
                quantity_units=trade.quantity_units,
                price_units=trade.price_units,
                amount_units=trade.amount_units,
                commission_units=trade.commission_units,
                stamp_tax_units=trade.stamp_tax_units,
                other_fee_units=trade.other_fee_units,
                dividend_tax_units=0,
                ratio_units=0,
                line_no=trade.line_no or 0,
                summary=trade.summary or "",
            )
        )
    return events


def build_round_id(user_id: uuid.UUID, security_code: str, key: str) -> str:
    """确定性回合 ID：`rnd_` + sha256(user_id|security_code|key)[:32]。"""
    digest = hashlib.sha256(f"{user_id}|{security_code}|{key}".encode()).hexdigest()
    return f"rnd_{digest[:32]}"


def _round_input(user_id: uuid.UUID, item: FifoRound, rebuilt_at: datetime) -> RoundInput:
    """core `Round` → 落库输入（确定性 ID + 明细）。"""
    fills = tuple(
        FillInput(
            sequence=fill.sequence,
            buy_trade_id=fill.buy_trade_id,
            sell_trade_id=fill.sell_trade_id,
            quantity_units=fill.quantity_units,
            buy_cost_units=fill.buy_cost_units,
            sell_proceeds_units=fill.sell_proceeds_units,
            dividend_units=fill.dividend_units,
            pnl_units=fill.pnl_units,
            open_date=fill.open_date,
            close_date=fill.close_date,
            holding_days=fill.holding_days,
        )
        for fill in item.fills
    )
    return RoundInput(
        id=build_round_id(user_id, item.security_code, item.key),
        security_code=item.security_code,
        security_name=item.security_name,
        status=item.status,
        open_trade_id=item.open_trade_id,
        close_trade_id=item.close_trade_id,
        open_date=item.open_date,
        close_date=item.close_date,
        quantity_units=item.quantity_units,
        cost_units=item.cost_units,
        proceeds_units=item.proceeds_units,
        dividend_units=item.dividend_units,
        pnl_units=item.pnl_units,
        return_rate_units=item.return_rate_units,
        holding_days=item.holding_days,
        data_error=item.data_error,
        oversell_units=item.oversell_units,
        rebuilt_at=rebuilt_at,
        fills=fills,
    )


async def _load_trades(
    session: AsyncSession, user_id: uuid.UUID, *, codes: list[str] | None = None
) -> list[Trade]:
    """读取未删除明细（可按证券过滤）；稳定序由 FIFO 引擎内部负责，这里只保证确定性。"""
    stmt = select(Trade).where(Trade.user_id == user_id, Trade.deleted_at.is_(None))
    if codes is not None:
        if not codes:
            return []
        stmt = stmt.where(Trade.security_code.in_(codes))
    stmt = stmt.order_by(Trade.trade_date, Trade.line_no, Trade.id)
    return list(await session.scalars(stmt))


async def _dirty_securities(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    window_from: date,
) -> set[str]:
    """period 重建的脏证券集合（区间 trade + 窗口外更新 + 区间起点仍持仓）。"""
    codes: set[str] = set()

    def _add(rows: Any) -> None:
        codes.update(str(code) for code in rows)

    # 1) 安全回看窗口内的 trade 所在证券（含区间内的所有活动）。
    #    不排除软删行：撤销/删除一笔窗口内流水后，该证券的回合必须重算，否则残留旧回合。
    _add(
        await session.scalars(
            select(Trade.security_code)
            .where(
                Trade.user_id == user_id,
                Trade.trade_date >= window_from,
                Trade.trade_date <= period_end,
            )
            .distinct()
        )
    )

    # 2) 上次 rebuild 之后被修改/撤销的 trade 所在证券（可能在窗口之外，例如修正历史行）。
    state = await session.get(RoundBuildState, user_id)
    marker = state.last_trade_updated_at if state is not None else None
    # 同样不排除软删行：`deleted_at` 变化本身就会更新 `updated_at`，软删也可能发生在
    # 窗口之外的旧流水上，只按“未删除行”筛会永远漏掉它 → 旧回合永不消失。
    updated_stmt = select(Trade.security_code).where(Trade.user_id == user_id).distinct()
    if marker is not None:
        # `>`：marker 是上次重建时**已包含软删行**的最新 `updated_at`；同值行上次已处理，
        # 用 `>=` 会让含软删行的证券每次都被无谓重算。PG 的 `now()` 取事务开始时间，
        # 后提交的更新必然严格大于上一次已提交的 marker，不会漏。
        updated_stmt = updated_stmt.where(Trade.updated_at > marker)
    _add(await session.scalars(updated_stmt))

    # 3) 区间起点仍有未平仓回合的证券：其回合可能在本区间被卖出影响，必须整段重算。
    _add(
        await session.scalars(
            select(Round.security_code)
            .where(
                Round.user_id == user_id,
                Round.status == "open",
                Round.open_date < period_start,
            )
            .distinct()
        )
    )
    return codes


async def _upsert_build_state(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    rebuilt_at: datetime,
    scope: str,
    period_start: date | None,
    period_end: date | None,
    rounds_count: int,
) -> None:
    # marker 必须覆盖软删行：`deleted_at` 更新同样改了 `updated_at`，只有把它计入
    # 水位线，下一次 period 重建才不会把“已处理过的撤销”反复当作新变更。
    last_updated = await session.scalar(
        select(func.max(Trade.updated_at)).where(Trade.user_id == user_id)
    )
    stmt = (
        pg_insert(RoundBuildState)
        .values(
            user_id=user_id,
            last_rebuilt_at=rebuilt_at,
            last_trade_updated_at=last_updated,
            last_scope=scope,
            last_period_start=period_start,
            last_period_end=period_end,
            rounds_count=rounds_count,
            version=1,
        )
        .on_conflict_do_update(
            index_elements=[RoundBuildState.user_id],
            set_={
                "last_rebuilt_at": rebuilt_at,
                "last_trade_updated_at": last_updated,
                "last_scope": scope,
                "last_period_start": period_start,
                "last_period_end": period_end,
                "rounds_count": rounds_count,
                "version": RoundBuildState.version + 1,
            },
        )
    )
    await session.execute(stmt)


async def rebuild_rounds_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    scope: str = "full",
    period_start: date | None = None,
    period_end: date | None = None,
) -> RebuildResult:
    """全量或脏窗口重建该用户回合；由调用方持有事务。

    - full：删该用户全部回合（明细级联），按全量 trades 重建；
    - period：只删脏证券的回合，再按这些证券的**全历史**重建；
    - 重复执行同参数：确定性 ID + 全删后重插 → 结果值一致；
    - 无 trade：full 清空回合并成功返回，period 不动未受影响证券。
    """
    if scope not in _VALID_SCOPES:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"未知 rebuild scope: {scope}")
    rebuilt_at = clock.now()
    repo = RoundRepository(session)
    window_from: date | None = None
    window_to: date | None = None

    if scope == "full":
        trades = await _load_trades(session, user_id)
        securities = sorted({trade.security_code for trade in trades})
        await repo.delete_all(user_id)
    else:
        if period_start is None or period_end is None:
            raise ApiError(
                ErrorCode.INVALID_PERIOD, detail="period 模式必须提供 period_start/period_end"
            )
        if period_start > period_end:
            raise ApiError(ErrorCode.INVALID_PERIOD, detail="period_start 不能晚于 period_end")
        window_from = period_start - timedelta(days=LOOKBACK_DAYS)
        window_to = period_end
        dirty = await _dirty_securities(
            session,
            user_id,
            period_start=period_start,
            period_end=period_end,
            window_from=window_from,
        )
        securities = sorted(dirty)
        trades = await _load_trades(session, user_id, codes=securities)
        await repo.delete_for_securities(user_id, securities)

    result = build_rounds(trades_to_events(trades))
    rounds = [_round_input(user_id, item, rebuilt_at) for item in result.rounds]
    await repo.insert_batch(user_id, rounds)
    rounds_count = await repo.count(user_id)
    await _upsert_build_state(
        session,
        user_id=user_id,
        rebuilt_at=rebuilt_at,
        scope=scope,
        period_start=period_start,
        period_end=period_end,
        rounds_count=rounds_count,
    )
    await session.flush()
    return RebuildResult(
        scope=scope,
        securities=tuple(securities),
        rounds_total=len(result.rounds),
        rounds_closed=len(result.closed_rounds),
        rounds_open=len(result.open_rounds),
        fills=len(result.fills),
        data_errors=len(result.data_errors),
        window_from=window_from,
        window_to=window_to,
        rebuilt_at=rebuilt_at,
        full=scope == "full",
    )


def parse_period(period: str) -> tuple[date, date]:
    """`YYYY-MM` → (月初, 月末)；非法格式抛 `INVALID_PERIOD`。"""
    try:
        year_text, month_text = period.strip().split("-", 1)
        year, month = int(year_text), int(month_text)
        if year < 1970 or not 1 <= month <= 12:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as exc:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 非法: {period!r}") from exc
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _jsonable(value: Any) -> Any:
    """dataclass asdict → JSON 友好（date/datetime → isoformat，tuple → list）。"""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def rebuild_rounds_job(
    queue: JobQueue, session: AsyncSession, job: dict[str, Any], clock: Clock
) -> dict[str, Any]:
    """worker handler：progress(recomputing) → 检查 cancel → rebuild → finish(succeeded)。"""
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params: dict[str, Any] = dict(job.get("params") or {})
    scope = str(params.get("scope") or "full")
    period_start: date | None = None
    period_end: date | None = None
    if scope == "period":
        period = str(params.get("period") or "")
        if not period:
            raise ApiError(ErrorCode.INVALID_PERIOD, detail="scope=period 需要 period(YYYY-MM)")
        period_start, period_end = parse_period(period)

    await queue.progress(session, job_id, percent=10, stage="recomputing", message=None)
    if await queue.is_cancel_requested(session, job_id):
        await queue.finish(session, job_id, status=JobStatus.CANCELLED, result={"cancelled": True})
        return {"job_id": job_id, "cancelled": True}

    result = await rebuild_rounds_for_user(
        session,
        user_id=user_id,
        clock=clock,
        scope=scope,
        period_start=period_start,
        period_end=period_end,
    )
    payload: dict[str, Any] = _jsonable(asdict(result))
    await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=payload)
    return {"job_id": job_id, **payload}


__all__ = [
    "LOOKBACK_DAYS",
    "RebuildResult",
    "build_round_id",
    "parse_period",
    "rebuild_rounds_for_user",
    "rebuild_rounds_job",
    "trades_to_events",
]
