"""超卖卖出的人工修订：补录成本 → 注入两条流水 → 引擎重新配对。

背景
----
账单缺失（例如新股入账行被隔离）会让某个卖出没有可配对的 lot：卖出本身有现金流入，
FIFO 却只能记一条 `oversell` 数据异常。用户知道该笔卖出的成本，但系统不允许他直接
改历史账单（也不该改）。这里的做法是**新增**流水而不是修改流水：

- 腿 1「资金划转」：`CASH` +cost（抵消下面买入腿的现金流出，日终现金不变）；
- 腿 2「人工买入」：卖出代码 +未配对数量、金额 −cost、无费用（建立成本 lot）。

两条腿都落在**卖出当日**、`line_no` 沿用卖出行的行号，并在 FIFO 稳定序里
`sort_rank=0` 排在原账单行之前；因此重算后这笔卖出被完整配对，回合从 `oversell`
变为 `closed`，`pnl = 卖款 − (原有成本 + 修订成本)`。

数据约束（为什么这样打标）
--------------------------
- `source='manual'`、`batch_id=NULL`：不进入导入批次，批次撤销/区间替换都不会误删；
- `revision_of=<卖出 trade_id>`：可反查/覆盖/撤回，也让引擎把注入行排在账单行之前；
- `dedupe_key='mr:<sell_id>:cash|buy'`：独立命名空间，不与导入的 64 位 sha256 键冲突，
  同一卖出至多一条资金腿 + 一条买入腿；
- `cash_balance_units=NULL`：注入行不是真实账单，绝不能成为现金余额锚点。

不自动失效
----------
用户之后重新导入真实账单时，注入行仍然存在（区间替换会跳过注入行）。若真实账单本身
已经覆盖了这笔卖出，系统只把状态标成 `superseded`（页面提示待确认），**不自动删除**
注入行——自动改历史风险高于收益，由用户决定是否撤回。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import Side
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_core.fifo import EventKind, build_rounds
from stock_ledger.application.trades import trade_snapshot
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository
from stock_platform.audit import record
from stock_platform.ids import trade_id as new_trade_id
from stock_platform.outbox import publish

from stock_review.application.bond_alias import build_bond_alias, canonical_code
from stock_review.application.rebuild import kind_for_trade, trades_to_events
from stock_review.models import Round

# `ledger.trade.note` 与页面提示共用同一文案，避免两处漂移。
MANUAL_REVISION_NOTE = "人工修订，非真实成交账单"
_CASH_SUMMARY = "人工修订资金划转"
_BUY_SUMMARY = "人工修订买入"
_CASH_SECURITY_CODE = "CASH"
# price_units 是 1e-6 标度：amount(1e-2) / quantity(1e-4) → ×1e8。
_PRICE_SCALE = 100_000_000
_EVENT_REVISED = "ledger.trade.revised"

# 修订状态（API/前端展示口径）
STATUS_NONE = "none"  # 仍超卖，可修订
STATUS_REVISED = "revised"  # 已注入修订行
STATUS_SUPERSEDED = "superseded"  # 真实账单已覆盖该卖出，注入行待用户确认
STATUS_NOT_OVERSELL = "not_oversell"  # 本来就不超卖（无需修订）


@dataclass(slots=True)
class OversellRevision:
    """一次修订查询/写入的结果（API 出口 DTO）。"""

    sell: Trade
    status: str
    # 当前（含注入行）仍未配对的数量；修订生效后为 0。
    oversell_units: int
    # 剔除全部注入行后是否仍超卖：False 表示真实账单已覆盖。
    covered_by_actual: bool
    cash: Trade | None
    buy: Trade | None
    # 修订成本（正数 = 用户补录的总成本）；无修订行为 None。
    cost_units: int | None
    # 重算后该卖出对应的回合（未重算/未超卖为 None）。
    round_id: str | None


def _revision_dedupe_key(sell_id: str, leg: str) -> str:
    return f"mr:{sell_id}:{leg}"


async def _load_security_group(
    session: AsyncSession, user_id: uuid.UUID, security_code: str
) -> tuple[list[Trade], dict[str, str]]:
    """加载与该代码同属一只证券（按可转债别名归并）的全部存活流水。"""
    pairs = await session.execute(
        select(Trade.security_code, Trade.security_name)
        .where(Trade.user_id == user_id, Trade.deleted_at.is_(None))
        .distinct()
    )
    alias, _warnings = build_bond_alias([(code, name) for code, name in pairs.all()])
    canonical = canonical_code(alias, security_code)
    codes = {canonical} | {source for source, target in alias.items() if target == canonical}
    trades = await session.scalars(
        select(Trade)
        .where(
            Trade.user_id == user_id,
            Trade.deleted_at.is_(None),
            Trade.security_code.in_(codes),
        )
        .order_by(Trade.trade_date, Trade.line_no, Trade.id)
    )
    return list(trades), alias


def _oversell_units(trades: list[Trade], alias: dict[str, str], sell_id: str) -> int:
    """重放指定流水集合，返回该卖出仍未配对的数量（0 = 已完整配对）。"""
    result = build_rounds(trades_to_events(trades, bond_alias=alias))
    return sum(
        error.quantity_units
        for error in result.data_errors
        if error.kind == "oversell" and error.trade_id == sell_id
    )


async def _round_id_for_sell(session: AsyncSession, user_id: uuid.UUID, sell_id: str) -> str | None:
    """该卖出最近一次重算得到的回合（用于前端跳转/展示配对结果）。"""
    rows = await session.scalars(
        select(Round.id)
        .where(Round.user_id == user_id, Round.close_trade_id == sell_id)
        .order_by(Round.rebuilt_at.desc())
        .limit(1)
    )
    return rows.first()


async def _live_revision_rows(
    session: AsyncSession, user_id: uuid.UUID, sell_id: str
) -> tuple[Trade | None, Trade | None]:
    """该卖出当前生效的注入行（资金腿 `CASH` 不在证券分组里，必须单独查）。"""
    rows = await session.scalars(
        select(Trade)
        .where(
            Trade.user_id == user_id,
            Trade.revision_of == sell_id,
            Trade.deleted_at.is_(None),
        )
        .order_by(Trade.dedupe_key)
    )
    cash: Trade | None = None
    buy: Trade | None = None
    for row in rows:
        if row.dedupe_key.endswith(":cash"):
            cash = row
        else:
            buy = row
    return cash, buy


async def _build_revision(
    session: AsyncSession,
    user_id: uuid.UUID,
    sell: Trade,
    trades: list[Trade],
    alias: dict[str, str],
) -> OversellRevision:
    cash, buy = await _live_revision_rows(session, user_id, sell.id)
    actual = [trade for trade in trades if trade.revision_of is None]
    covered_by_actual = _oversell_units(actual, alias, sell.id) == 0
    has_revision = cash is not None and buy is not None
    if has_revision and covered_by_actual:
        status = STATUS_SUPERSEDED
    elif has_revision:
        status = STATUS_REVISED
    elif covered_by_actual:
        status = STATUS_NOT_OVERSELL
    else:
        status = STATUS_NONE
    return OversellRevision(
        sell=sell,
        status=status,
        oversell_units=_oversell_units(trades, alias, sell.id),
        covered_by_actual=covered_by_actual,
        cash=cash,
        buy=buy,
        cost_units=abs(buy.amount_units) if buy is not None else None,
        round_id=await _round_id_for_sell(session, user_id, sell.id),
    )


async def _load_sell(
    session: AsyncSession, user_id: uuid.UUID, sell_id: str, *, lock: bool
) -> Trade:
    stmt = select(Trade).where(
        Trade.user_id == user_id,
        Trade.id == sell_id,
        Trade.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    sell = await session.scalar(stmt)
    if sell is None:
        raise NotFoundError(detail=f"交易 {sell_id} 不存在")
    # 买卖口径与 FIFO 引擎一致（按 `category` 分类）：导入行常常只有类别没有方向
    # （`side=unknown`），用 `side` 把关会拒绝引擎已判为超卖卖出的流水。
    if kind_for_trade(sell) is not EventKind.SELL:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="只有卖出流水支持超卖人工修订")
    if sell.revision_of is not None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="人工修订注入行本身不能再被修订")
    if sell.quantity_units <= 0:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="卖出数量无效，无法补录成本")
    return sell


async def get_revision(
    session: AsyncSession, *, user_id: uuid.UUID, sell_trade_id: str
) -> OversellRevision:
    """查询该卖出的超卖与修订状态（只读，不触发重算）。"""
    sell = await _load_sell(session, user_id, sell_trade_id, lock=False)
    trades, alias = await _load_security_group(session, user_id, sell.security_code)
    return await _build_revision(session, user_id, sell, trades, alias)


def _injected_row(
    sell: Trade,
    *,
    quantity_units: int,
    side: str,
    security_code: str,
    security_name: str,
    summary: str,
    category: str,
    amount_units: int,
    price_units: int,
    leg: str,
) -> Trade:
    """构造一条注入行（字段口径见模块 docstring）。"""
    return Trade(
        id=new_trade_id(),
        user_id=sell.user_id,
        batch_id=None,
        trade_date=sell.trade_date,
        security_code=security_code,
        security_name=security_name,
        summary=summary,
        summary_code=None,
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=None,
        cash_balance_available=False,
        category=category,
        dedupe_key=_revision_dedupe_key(sell.id, leg),
        occurrence_idx=0,
        account_masked=sell.account_masked,
        note=MANUAL_REVISION_NOTE,
        source="manual",
        version=1,
        line_no=sell.line_no,
        revision_of=sell.id,
    )


def _apply_cost(trade: Trade, *, cost_units: int, quantity_units: int, leg: str) -> None:
    """把已有注入行改成新的成本（覆盖修订），口径与新建保持一致。"""
    if leg == "cash":
        trade.quantity_units = 0
        trade.price_units = 0
        trade.amount_units = cost_units
    else:
        trade.quantity_units = quantity_units
        trade.price_units = (cost_units * _PRICE_SCALE + quantity_units // 2) // quantity_units
        trade.amount_units = -cost_units


async def upsert_revision(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    sell_trade_id: str,
    cost_units: int,
    clock: Clock,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> OversellRevision:
    """补录/覆盖修订成本；同一卖出幂等（重复提交只会更新注入行，不叠加）。

    拒绝两类输入：非超卖卖出（包括真实账单已覆盖、无需修订的）与成本 ≤ 0。
    """
    if cost_units <= 0:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="修订成本必须大于 0")
    sell = await _load_sell(session, user_id, sell_trade_id, lock=True)
    trades, alias = await _load_security_group(session, user_id, sell.security_code)
    # 注入数量 = 真实账单下仍未配对的数量（纯超卖时等于卖出数量）。必须剔除已有注入行
    # 后再算：否则重复提交时「当前未配对」恒为 0，既无法更新成本，也会多出持仓。
    actual = [trade for trade in trades if trade.revision_of is None]
    quantity_units = _oversell_units(actual, alias, sell.id)
    if quantity_units == 0:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="该卖出已由真实账单完整配对，无需人工修订",
        )

    cash, buy = await _live_revision_rows(session, user_id, sell.id)
    repo = TradeRepository(session)
    at = clock.now()

    for trade, leg in ((cash, "cash"), (buy, "buy")):
        if trade is None:
            continue
        _apply_cost(trade, cost_units=cost_units, quantity_units=quantity_units, leg=leg)
        trade.version += 1
        await repo.add_history(
            user_id,
            trade.id,
            version=trade.version,
            op="update",
            snapshot=trade_snapshot(trade),
            changed_fields={
                "amount_units": trade.amount_units,
                "quantity_units": trade.quantity_units,
            },
            actor_id=actor_id,
            at=at,
        )

    created: list[Trade] = []
    if cash is None:
        cash = _injected_row(
            sell,
            quantity_units=0,
            side=Side.SELL.value,
            security_code=_CASH_SECURITY_CODE,
            security_name=_CASH_SUMMARY,
            summary=_CASH_SUMMARY,
            category="transfer",
            amount_units=cost_units,
            price_units=0,
            leg="cash",
        )
        session.add(cash)
        created.append(cash)
    if buy is None:
        buy = _injected_row(
            sell,
            quantity_units=quantity_units,
            side=Side.BUY.value,
            security_code=sell.security_code,
            security_name=sell.security_name,
            summary=_BUY_SUMMARY,
            category="buy",
            amount_units=-cost_units,
            price_units=(cost_units * _PRICE_SCALE + quantity_units // 2) // quantity_units,
            leg="buy",
        )
        session.add(buy)
        created.append(buy)
    if created:
        await session.flush()
        for trade in created:
            await repo.add_history(
                user_id,
                trade.id,
                version=trade.version,
                op="create",
                snapshot=trade_snapshot(trade),
                changed_fields=trade_snapshot(trade),
                actor_id=actor_id,
                at=at,
            )

    await record(
        session,
        action="trade.revision.upsert",
        actor_id=actor_id or user_id,
        target_type="trade",
        target_id=sell.id,
        meta={
            "cost_units": cost_units,
            "quantity_units": quantity_units,
            "cash_trade_id": cash.id,
            "buy_trade_id": buy.id,
        },
        request_id=request_id,
    )
    await publish(
        session,
        event_type=_EVENT_REVISED,
        aggregate="trade",
        aggregate_id=sell.id,
        user_id=user_id,
        payload={"cost_units": cost_units, "quantity_units": quantity_units},
    )
    trades, alias = await _load_security_group(session, user_id, sell.security_code)
    return await _build_revision(session, user_id, sell, trades, alias)


async def delete_revision(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    sell_trade_id: str,
    clock: Clock,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> OversellRevision:
    """撤回人工修订（软删两条注入行）；无注入行时幂等返回。"""
    sell = await _load_sell(session, user_id, sell_trade_id, lock=True)
    trades, alias = await _load_security_group(session, user_id, sell.security_code)
    cash, buy = await _live_revision_rows(session, user_id, sell.id)
    repo = TradeRepository(session)
    at = clock.now()
    for trade in (cash, buy):
        if trade is None:
            continue
        await repo.add_history(
            user_id,
            trade.id,
            version=trade.version + 1,
            op="delete",
            snapshot=trade_snapshot(trade),
            changed_fields={"deleted_at": at.isoformat()},
            actor_id=actor_id,
            at=at,
        )
        trade.deleted_at = at
        trade.version += 1
    if cash is not None or buy is not None:
        await session.flush()
        await record(
            session,
            action="trade.revision.delete",
            actor_id=actor_id or user_id,
            target_type="trade",
            target_id=sell.id,
            meta={
                "cash_trade_id": cash.id if cash else None,
                "buy_trade_id": buy.id if buy else None,
            },
            request_id=request_id,
        )
    trades, alias = await _load_security_group(session, user_id, sell.security_code)
    return await _build_revision(session, user_id, sell, trades, alias)
