"""摘要分类规则仓储 + 一期系统默认规则静电数据。

规则存在的原因：券商摘要文本千人千面，业务分类不能硬编码在代码里。系统规则
（`user_id IS NULL`）开箱即用，用户可再叠加自己的规则或调整优先级。
`seed_system_rules` 幂等：系统规则是**代码资产**，重复调用只补缺 / 同步词表变更 /
清理废弃的系统码，不会重复写入（NULLS NOT DISTINCT 唯一约束兜底）。

词汇一致性（G3 / P1-8）：非交易资金业务的默认规则直接复用解析器 `parsers/columns.py`
的同一份词表（`TRANSFER_SUMMARY_KEYWORDS` 等），避免「解析器认识、分类器不认识」导致
真实账单误隔离。规则码（`code`）同时作为写入 `trade.summary_code` 的短码，因此系统码
一律 ≤16 字符（与 `Trade.summary_code String(16)` 对齐）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.ids import rule_id as new_rule_id

from stock_ledger.models import SummaryRule
from stock_ledger.parsers.columns import TRANSFER_SUMMARY_KEYWORDS

# `trade.summary_code` 列宽（String(16)）：规则码超长时宁可不写，也不截断制造歧义。
SUMMARY_CODE_MAX_LENGTH = 16

# 一期系统默认规则：(code, name, category, side, match_type, pattern, priority)
# 顺序即同一 priority 下的兜底顺序；`sys_fee` 的 contains "费" 优先级最低（90），
# 保证「股息红利税」「银行转存」等语义明确的摘要不会被宽泛的「费」吃掉。
# 买卖类规则统一 `side="both"`：分类由摘要决定（方向另有 Trade.side 字段承载），
# 避免账单侧记录不一致（如合成夹具摘要与买卖标志随机组合）把本可分类的行误隔离。
_BASE_SYSTEM_RULES: tuple[tuple[str, str, str, str, str, str, int], ...] = (
    ("sys_buy_stock", "证券买入→买入", "buy", "both", "exact", "证券买入", 10),
    ("sys_sell_stock", "证券卖出→卖出", "sell", "both", "exact", "证券卖出", 10),
    ("sys_buy_plain", "买入→买入", "buy", "both", "exact", "买入", 20),
    ("sys_sell_plain", "卖出→卖出", "sell", "both", "exact", "卖出", 20),
    # 股息红利税（含「股息红利税\n补缴」）优先于通用「股息」规则：它是资金流出/费用，不是分红收入。
    ("sys_div_tax", "股息红利税→费用", "fee", "both", "contains", "红利税", 25),
    ("sys_dividend", "红利入账→分红", "dividend", "both", "exact", "红利入账", 30),
    ("sys_div_bonus", "股息→分红", "dividend", "both", "contains", "股息", 40),
    ("sys_new_apply", "申购→新股", "new_stock", "both", "contains", "申购", 50),
    ("sys_new_win", "中签→新股", "new_stock", "both", "exact", "中签", 40),
    ("sys_new_stock", "新股→新股", "new_stock", "both", "contains", "新股", 60),
    ("sys_interest", "利息归本→利息", "interest", "both", "contains", "利息", 50),
    ("sys_rights", "配股→配股", "rights", "both", "contains", "配股", 60),
    ("sys_fee", "费用→手续费", "fee", "both", "contains", "费", 90),
)

# 资金划转（银证转账 / 转存管）规则：与解析器共用同一份关键词表，逐词生成、码短可落 summary_code。
_TRANSFER_RULE_CODES: tuple[str, ...] = (
    "sys_bank_in",
    "sys_bank_out",
    "sys_transfer_in",
    "sys_transfer_out",
    "sys_custody_in",
    "sys_custody_out",
)
_TRANSFER_RULE_NAMES: tuple[str, ...] = (
    "银行转证券→资金划转",
    "证券转银行→资金划转",
    "银行转存→资金划转",
    "银行转取→资金划转",
    "转存管转入→资金划转",
    "转存管转出→资金划转",
)

SYSTEM_RULES: tuple[tuple[str, str, str, str, str, str, int], ...] = (
    *_BASE_SYSTEM_RULES,
    *(
        (code, name, "transfer", "both", "contains", keyword, 30)
        for code, name, keyword in zip(
            _TRANSFER_RULE_CODES, _TRANSFER_RULE_NAMES, TRANSFER_SUMMARY_KEYWORDS, strict=True
        )
    ),
)

_SYSTEM_RULE_CODES: tuple[str, ...] = tuple(code for code, *_ in SYSTEM_RULES)


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


def rule_summary_code(rule: SummaryRule) -> str | None:
    """规则码 → `trade.summary_code` 的短码；超过列宽（16）返回 None，不截断失真。"""
    code = rule.code
    return code if len(code) <= SUMMARY_CODE_MAX_LENGTH else None


async def seed_system_rules(session: AsyncSession) -> int:
    """写入 / 同步系统默认规则；返回本次新写入 + 词表有变更 + 清理废弃码的条数。

    幂等语义：系统规则是代码资产（`user_id IS NULL`，用户不可编辑），所以：
      1. 缺的补写；
      2. 同名 code 的字段（pattern/priority/category…）与代码不一致 → 覆盖更新（词表升级）；
      3. 代码里已删除的废弃系统码 → 清理（避免旧词表继续命中/占位）。
    全部一致时返回 0（重复调用无副作用）。
    """
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
    insert_stmt = pg_insert(SummaryRule).values(list(rows))
    excluded = insert_stmt.excluded
    syncable = (
        "name",
        "category",
        "side",
        "match_type",
        "pattern",
        "priority",
        "enabled",
        "is_system",
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["user_id", "code"],
        set_={column: getattr(excluded, column) for column in syncable}
        | {"version": SummaryRule.version + 1},
        where=or_(
            *(getattr(SummaryRule, column) != getattr(excluded, column) for column in syncable)
        ),
    ).returning(SummaryRule.code)
    written = list(await session.scalars(stmt))

    pruned_rows = await session.scalars(
        delete(SummaryRule)
        .where(
            SummaryRule.user_id.is_(None),
            SummaryRule.is_system.is_(True),
            SummaryRule.code.not_in(_SYSTEM_RULE_CODES),
        )
        .returning(SummaryRule.code)
    )
    pruned = len(list(pruned_rows))
    return len(written) + pruned


__all__ = [
    "SUMMARY_CODE_MAX_LENGTH",
    "SYSTEM_RULES",
    "SummaryRuleRepository",
    "rule_summary_code",
    "seed_system_rules",
]
