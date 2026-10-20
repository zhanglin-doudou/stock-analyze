"""摘要分类规则仓储 + 一期系统默认规则静电数据。

规则存在的原因：券商摘要文本千人千面，业务分类不能硬编码在代码里。系统规则
（`user_id IS NULL`）开箱即用，用户可再叠加自己的规则或调整优先级。
`seed_system_rules` 幂等：重复调用只补缺，不会重复写入（NULLS NOT DISTINCT 唯一约束兜底）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.ids import rule_id as new_rule_id

from stock_ledger.models import SummaryRule

# 一期系统默认规则：(code, name, category, side, match_type, pattern, priority)
SYSTEM_RULES: tuple[tuple[str, str, str, str, str, str, int], ...] = (
    ("sys_buy_stock", "证券买入→买入", "buy", "buy", "exact", "证券买入", 10),
    ("sys_sell_stock", "证券卖出→卖出", "sell", "sell", "exact", "证券卖出", 10),
    ("sys_buy_plain", "买入→买入", "buy", "buy", "exact", "买入", 20),
    ("sys_sell_plain", "卖出→卖出", "sell", "sell", "exact", "卖出", 20),
    ("sys_dividend_cash", "红利入账→分红", "dividend", "both", "exact", "红利入账", 30),
    ("sys_dividend_bonus", "股息→分红", "dividend", "both", "contains", "股息", 40),
    ("sys_transfer_bank_in", "银行转证券→资金划转", "transfer", "both", "exact", "银行转证券", 30),
    ("sys_transfer_bank_out", "证券转银行→资金划转", "transfer", "both", "exact", "证券转银行", 30),
    ("sys_new_apply", "申购→新股", "new_stock", "both", "contains", "申购", 50),
    ("sys_new_win", "中签→新股", "new_stock", "both", "exact", "中签", 40),
    ("sys_new_stock", "新股→新股", "new_stock", "both", "contains", "新股", 60),
    ("sys_interest", "利息归本→利息", "interest", "both", "contains", "利息", 50),
    ("sys_rights", "配股→配股", "rights", "both", "contains", "配股", 60),
    ("sys_fee", "费用→手续费", "fee", "both", "contains", "费", 90),
)

_MUTABLE_FIELDS: frozenset[str] = frozenset(
    {"name", "category", "side", "match_type", "pattern", "priority", "enabled"}
)


class SummaryRuleRepository:
    """用户分类规则读写（含系统默认规则）。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_enabled(self, user_id: uuid.UUID) -> list[SummaryRule]:
        """按优先级升序返回生效规则：系统规则 + 用户规则（用户规则同优先级后置）。"""
        stmt = (
            select(SummaryRule)
            .where(
                SummaryRule.enabled.is_(True),
                (SummaryRule.user_id == user_id) | (SummaryRule.user_id.is_(None)),
            )
            .order_by(
                SummaryRule.priority.asc(),
                SummaryRule.is_system.asc(),
                SummaryRule.id.asc(),
            )
        )
        return list(await self._session.scalars(stmt))

    async def by_id(self, user_id: uuid.UUID, rule_id: str) -> SummaryRule | None:
        """系统规则对所有人可见；用户规则仅归属者可见。"""
        return await self._session.scalar(
            select(SummaryRule).where(
                SummaryRule.id == rule_id,
                (SummaryRule.user_id == user_id) | (SummaryRule.user_id.is_(None)),
            )
        )

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        code: str,
        name: str,
        category: str,
        side: str = "both",
        match_type: str = "exact",
        pattern: str,
        priority: int = 100,
        enabled: bool = True,
    ) -> SummaryRule:
        if side not in ("buy", "sell", "both"):
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="side 非法")
        if match_type not in ("exact", "prefix", "contains", "regex"):
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="match_type 非法")
        rule = SummaryRule(
            id=new_rule_id(),
            user_id=user_id,
            code=code,
            name=name,
            category=category,
            side=side,
            match_type=match_type,
            pattern=pattern,
            priority=priority,
            enabled=enabled,
            is_system=False,
        )
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def update_fields(
        self, user_id: uuid.UUID, rule_id: str, *, changes: dict[str, Any]
    ) -> SummaryRule:
        rule = await self._session.scalar(
            select(SummaryRule).where(SummaryRule.id == rule_id, SummaryRule.user_id == user_id)
        )
        if rule is None:
            raise NotFoundError(detail=f"规则 {rule_id} 不存在")
        unknown = set(changes) - _MUTABLE_FIELDS
        if unknown:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"不可修改字段: {sorted(unknown)}")
        for key, value in changes.items():
            setattr(rule, key, value)
        rule.version += 1
        await self._session.flush()
        return rule

    async def delete(self, user_id: uuid.UUID, rule_id: str) -> None:
        rule = await self._session.scalar(
            select(SummaryRule).where(SummaryRule.id == rule_id, SummaryRule.user_id == user_id)
        )
        if rule is None:
            raise NotFoundError(detail=f"规则 {rule_id} 不存在")
        await self._session.delete(rule)
        await self._session.flush()


async def seed_system_rules(session: AsyncSession) -> int:
    """写入/补齐一期系统默认规则；返回本次新写入的条数。"""
    rows: Sequence[dict[str, Any]] = [
        {
            "id": new_rule_id(),
            "user_id": None,
            "code": code,
            "name": name,
            "category": category,
            "side": side,
            "match_type": match_type,
            "pattern": pattern,
            "priority": priority,
            "enabled": True,
            "is_system": True,
            "version": 1,
        }
        for code, name, category, side, match_type, pattern, priority in SYSTEM_RULES
    ]
    stmt = (
        pg_insert(SummaryRule)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["user_id", "code"])
        .returning(SummaryRule.code)
    )
    inserted = list(await session.scalars(stmt))
    return len(inserted)


__all__ = ["SYSTEM_RULES", "SummaryRuleRepository", "seed_system_rules"]
