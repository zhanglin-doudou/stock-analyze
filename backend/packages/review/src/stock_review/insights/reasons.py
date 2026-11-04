"""逐笔交易理由：仓储 + 应用服务（API-090/091，设计 §3.1）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import BUY_REASON_CODES, SELL_REASON_CODES, EmotionCode
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.insights.calendar import is_late_annotated
from stock_ledger.models import Trade
from stock_ledger.parsers.dedupe import dedupe_key as logical_dedupe_key
from stock_platform.audit import record as audit_record
from stock_platform.ids import new_id
from stock_review.models import TradeReason

REASON_LABELS: dict[str, str] = {
    "plan_build": "按计划建仓",
    "add_on": "按计划加仓",
    "bottom_fish": "抄底",
    "chase": "追涨",
    "rebound": "反弹",
    "dca": "定投",
    "news": "消息驱动",
    "emotion": "情绪驱动",
    "plan_reduce": "按计划减仓",
    "take_profit": "止盈",
    "stop_loss": "止损",
    "break_even": "保本卖出",
    "rebalance": "再平衡",
    "fear": "恐慌卖出",
    "other": "其他",
}
EMOTION_LABELS: dict[str, str] = {
    "calm": "平静",
    "anxious": "焦虑",
    "greedy": "贪婪",
    "fear": "恐惧",
    "impulsive": "冲动",
    "other": "其他",
}


def _new_reason_id() -> str:
    return new_id("rsn")


@dataclass(slots=True)
class ReasonDTO:
    trade_id: str
    side: str
    reason_code: str
    reason_text: str | None
    emotion_code: str | None
    tags: list[str]
    annotated_at: datetime
    late_annotated: bool
    version: int

    @classmethod
    def from_row(cls, row: TradeReason) -> ReasonDTO:
        return cls(
            trade_id=row.trade_id,
            side=row.side,
            reason_code=row.reason_code,
            reason_text=row.reason_text,
            emotion_code=row.emotion_code,
            tags=list(row.tags or []),
            annotated_at=row.annotated_at,
            late_annotated=row.late_annotated,
            version=row.version,
        )


async def active_by_dedupe(
    session: AsyncSession, user_id: uuid.UUID, dedupe_keys: Iterable[str]
) -> dict[str, TradeReason]:
    """按逻辑键批量取有效理由（重导入换 trade_id 不影响）。"""
    keys = list(dict.fromkeys(dedupe_keys))
    if not keys:
        return {}
    rows = await session.scalars(
        select(TradeReason).where(
            TradeReason.user_id == user_id,
            TradeReason.trade_dedupe_key.in_(keys),
            TradeReason.deleted_at.is_(None),
        )
    )
    return {row.trade_dedupe_key: row for row in rows}


async def active_by_trade_ids(
    session: AsyncSession, user_id: uuid.UUID, trade_ids: Iterable[str]
) -> dict[str, TradeReason]:
    ids = list(dict.fromkeys(trade_ids))
    if not ids:
        return {}
    rows = await session.scalars(
        select(TradeReason).where(
            TradeReason.user_id == user_id,
            TradeReason.trade_id.in_(ids),
            TradeReason.deleted_at.is_(None),
        )
    )
    return {row.trade_id: row for row in rows}


async def matching_dedupe_keys(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason_code: str | None = None,
) -> set[str]:
    """`has_reason`/`reason_code` 过滤用：返回有有效理由的逻辑键集合。"""
    stmt = select(TradeReason.trade_dedupe_key).where(
        TradeReason.user_id == user_id, TradeReason.deleted_at.is_(None)
    )
    if reason_code is not None:
        stmt = stmt.where(TradeReason.reason_code == reason_code)
    return set(await session.scalars(stmt))


def _validate_payload(
    side: str, reason_code: str, emotion_code: str | None, tags: Sequence[str]
) -> None:
    allowed = BUY_REASON_CODES if side == "buy" else SELL_REASON_CODES
    if reason_code not in allowed:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail=f"reason_code={reason_code!r} 不适用于 {side} 方向",
            errors=[FieldError("reason_code", "invalid_for_side", "该理由不适用于当前买卖方向")],
        )
    if emotion_code is not None and emotion_code not in set(EmotionCode):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail=f"emotion_code={emotion_code!r} 非法",
            errors=[FieldError("emotion_code", "invalid", "情绪码不在枚举内")],
        )
    if len(tags) > 8 or any(len(tag) > 16 for tag in tags):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="tags 最多 8 个、每个不超过 16 字",
            errors=[FieldError("tags", "too_many_or_too_long", "tags 超限")],
        )


async def put_reason(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    trade: Trade,
    reason_code: str,
    reason_text: str | None,
    emotion_code: str | None,
    tags: Sequence[str],
    expected_version: int | None,
    clock: Clock,
    calendar: Sequence[date] = (),
    request_id: str | None = None,
) -> tuple[ReasonDTO, bool]:
    """新建或更新理由；返回 `(dto, created)`。

    - 新建可省略 `If-Match`；已存在时必须携带且匹配（409 VERSION_CONFLICT）；
    - `annotated_at` / `late_annotated` 由服务端按 `d0 + 20` 交易日计算；
    - 不修改 `ledger.trade.version`（理由与成交版本解耦）。
    """
    reason_code_text = str(reason_code)
    emotion_text = str(emotion_code) if emotion_code is not None else None
    _validate_payload(trade.side, reason_code_text, emotion_text, tags)
    # 逻辑键按业务要素重算，保证与列表/详情/导入路径一致（不依赖可变的 trade_id）。
    logical_key = logical_dedupe_key(trade)

    existing = await session.scalar(
        select(TradeReason).where(
            TradeReason.user_id == user_id,
            TradeReason.trade_dedupe_key == logical_key,
            TradeReason.deleted_at.is_(None),
        )
    )
    now = clock.now()
    annotated_on = now.date()
    late = bool(calendar) and is_late_annotated(calendar, trade.trade_date, annotated_on)

    if existing is None:
        row = TradeReason(
            id=_new_reason_id(),
            user_id=user_id,
            trade_dedupe_key=logical_key,
            trade_id=trade.id,
            side=trade.side,
            reason_code=reason_code_text,
            reason_text=reason_text,
            emotion_code=emotion_text,
            tags=list(tags),
            annotated_at=now,
            late_annotated=late,
            version=1,
        )
        session.add(row)
        created = True
    else:
        if expected_version is None:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail="更新理由必须携带 If-Match（当前 version）",
                errors=[FieldError("If-Match", "required", "更新必须携带当前 version")],
            )
        if expected_version != existing.version:
            raise ApiError(
                ErrorCode.VERSION_CONFLICT,
                detail=f"理由已被更新（当前 version={existing.version}）",
            )
        existing.trade_id = trade.id
        existing.side = trade.side
        existing.reason_code = reason_code_text
        existing.reason_text = reason_text
        existing.emotion_code = emotion_text
        existing.tags = list(tags)
        existing.annotated_at = now
        existing.late_annotated = late
        existing.version += 1
        row = existing
        created = False

    await session.flush()
    await audit_record(
        session,
        action="trade_reason.write",
        actor_id=user_id,
        target_type="trade_reason",
        target_id=logical_key,
        meta={
            "trade_dedupe_key": logical_key,
            "reason_code": reason_code_text,
            "version": row.version,
            "side": trade.side,
        },
        request_id=request_id,
    )
    return ReasonDTO.from_row(row), created


async def delete_reason(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    trade: Trade,
    expected_version: int | None,
    clock: Clock,
    request_id: str | None = None,
) -> None:
    """软删理由；不存在/已删幂等返回（仍按 204 语义）。"""
    logical_key = logical_dedupe_key(trade)
    row = await session.scalar(
        select(TradeReason).where(
            TradeReason.user_id == user_id,
            TradeReason.trade_dedupe_key == logical_key,
            TradeReason.deleted_at.is_(None),
        )
    )
    if row is None:
        return
    if expected_version is not None and expected_version != row.version:
        raise ApiError(
            ErrorCode.VERSION_CONFLICT,
            detail=f"理由已被更新（当前 version={row.version}）",
        )
    row.deleted_at = clock.now()
    row.version += 1
    await session.flush()
    await audit_record(
        session,
        action="trade_reason.delete",
        actor_id=user_id,
        target_type="trade_reason",
        target_id=logical_key,
        meta={"trade_dedupe_key": logical_key, "version": row.version},
        request_id=request_id,
    )


__all__ = [
    "ReasonDTO",
    "active_by_dedupe",
    "active_by_trade_ids",
    "delete_reason",
    "matching_dedupe_keys",
    "put_reason",
]
