"""导入映射 v2 仓储：用户模板、账单类型记忆、系统/管理员列规则。

三张表的隔离边界：
- 模板/类型：全部按 `user_id` 显式过滤（RLS 兜底）；
- 列规则：`user_id IS NULL` 是全局规则（对所有租户可见），其余按 `user_id` 过滤。
  这是**显式例外**，与板块公共缓存同理，写进方法 docstring。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import NotFoundError
from stock_platform.ids import (
    import_bill_type_id,
    import_column_rule_id,
    import_template_id,
)

from stock_ledger.models import ImportBillType, ImportColumnRule, ImportTemplate
from stock_ledger.repositories.trades import decode_cursor, encode_cursor


class ImportTemplateRepository:
    """用户级文件签名模板（只存表头映射，不存行数据）。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_signature(self, user_id: uuid.UUID, signature: str) -> ImportTemplate | None:
        return await self._session.scalar(
            select(ImportTemplate).where(
                ImportTemplate.user_id == user_id, ImportTemplate.signature == signature
            )
        )

    async def by_id(self, user_id: uuid.UUID, template_id: str) -> ImportTemplate | None:
        return await self._session.scalar(
            select(ImportTemplate).where(
                ImportTemplate.user_id == user_id, ImportTemplate.id == template_id
            )
        )

    async def require(self, user_id: uuid.UUID, template_id: str) -> ImportTemplate:
        template = await self.by_id(user_id, template_id)
        if template is None:
            raise NotFoundError(detail=f"导入模板 {template_id} 不存在")
        return template

    async def list_with_cursor(
        self, user_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> tuple[list[ImportTemplate], str | None]:
        capped = max(1, min(limit, 200))
        stmt = select(ImportTemplate).where(ImportTemplate.user_id == user_id)
        if cursor is not None:
            payload = decode_cursor(cursor)
            cursor_at = datetime.fromisoformat(payload["c"])
            stmt = stmt.where(
                (ImportTemplate.updated_at < cursor_at)
                | ((ImportTemplate.updated_at == cursor_at) & (ImportTemplate.id < payload["i"]))
            )
        stmt = stmt.order_by(ImportTemplate.updated_at.desc(), ImportTemplate.id.desc()).limit(
            capped + 1
        )
        rows = list(await self._session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"c": last.updated_at.isoformat(), "i": last.id})
        return rows, next_cursor

    async def upsert(
        self,
        user_id: uuid.UUID,
        *,
        broker: str,
        signature: str,
        header_count: int,
        date_format: str | None,
        items: Sequence[dict[str, Any]],
        ignored: Sequence[str],
        origin_batch_id: str | None,
        last_batch_id: str | None,
    ) -> ImportTemplate:
        template = await self.by_signature(user_id, signature)
        if template is None:
            template = ImportTemplate(
                id=import_template_id(),
                user_id=user_id,
                broker=broker,
                signature=signature,
                header_count=header_count,
                date_format=date_format,
                items=list(items),
                ignored=list(ignored),
                origin_batch_id=origin_batch_id,
                last_batch_id=last_batch_id,
            )
            self._session.add(template)
        else:
            template.broker = broker
            template.header_count = header_count
            template.date_format = date_format
            template.items = list(items)
            template.ignored = list(ignored)
            template.last_batch_id = last_batch_id
            template.version += 1
        await self._session.flush()
        return template

    async def delete(self, user_id: uuid.UUID, template_id: str) -> bool:
        result = await self._session.execute(
            delete(ImportTemplate).where(
                ImportTemplate.user_id == user_id, ImportTemplate.id == template_id
            )
        )
        return bool(getattr(result, "rowcount", 0))


class ImportBillTypeRepository:
    """用户级「账单类型文本 → Category/废弃」记忆。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self, user_id: uuid.UUID) -> list[ImportBillType]:
        return list(
            await self._session.scalars(
                select(ImportBillType)
                .where(ImportBillType.user_id == user_id)
                .order_by(ImportBillType.updated_at.desc())
            )
        )

    async def by_keys(self, user_id: uuid.UUID, keys: Sequence[str]) -> dict[str, ImportBillType]:
        if not keys:
            return {}
        rows = await self._session.scalars(
            select(ImportBillType).where(
                ImportBillType.user_id == user_id, ImportBillType.type_key.in_(list(keys))
            )
        )
        return {row.type_key: row for row in rows}

    async def upsert_many(
        self,
        user_id: uuid.UUID,
        resolutions: Sequence[dict[str, Any]],
        *,
        batch_id: str | None,
        broker: str | None,
    ) -> tuple[int, int]:
        """把本次确认/复用的类型写回记忆。

        返回 `(changed_count, modified_existing_count)`：
        - `changed_count`：新增或改动的总条数；
        - `modified_existing_count`：**已存在**的类型被改动分类/废弃的条数——
          这类改动会影响历史已解析流水，调用方需触发全量 reclassify 订正。
        """
        keys = [str(item["type_key"]) for item in resolutions]
        existing = await self.by_keys(user_id, keys)
        changed = 0
        modified_existing = 0
        for item in resolutions:
            key = str(item["type_key"])
            record = existing.get(key)
            discarded = bool(item.get("discard", False))
            category = None if discarded else str(item.get("category") or "")
            if record is None:
                record = ImportBillType(
                    id=import_bill_type_id(),
                    user_id=user_id,
                    type_key=key,
                    type_label=str(item.get("type_label") or key)[:120],
                    category=category,
                    discarded=discarded,
                    broker=broker,
                    origin_batch_id=batch_id,
                    last_write_batch_id=batch_id,
                )
                self._session.add(record)
                changed += 1
                continue
            if record.category != category or record.discarded != discarded:
                record.category = category
                record.discarded = discarded
                record.type_label = str(item.get("type_label") or record.type_label)[:120]
                record.last_write_batch_id = batch_id
                changed += 1
                modified_existing += 1
        await self._session.flush()
        return changed, modified_existing

    async def delete_by_last_write(self, user_id: uuid.UUID, batch_id: str) -> int:
        """撤销时只删「本批次首次写入/修改」的类型记忆，复用的旧类型不动。"""
        result = await self._session.execute(
            delete(ImportBillType).where(
                ImportBillType.user_id == user_id,
                ImportBillType.last_write_batch_id == batch_id,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


class ImportColumnRuleRepository:
    """系统/管理员列规则。

    `user_id IS NULL` 的全局规则对所有租户可见——这是显式例外（与板块公共缓存同理），
    因此本仓储的读取方法**不要求** `user_id` 参与过滤，但必须由调用方显式传入当前
    `user_id` 以纳入用户级覆盖规则。
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_effective(
        self,
        user_id: uuid.UUID,
        *,
        broker: str | None = None,
        include_disabled: bool = False,
    ) -> list[ImportColumnRule]:
        stmt = select(ImportColumnRule).where(
            or_(ImportColumnRule.user_id.is_(None), ImportColumnRule.user_id == user_id)
        )
        if broker is not None:
            stmt = stmt.where(ImportColumnRule.broker == broker)
        if not include_disabled:
            stmt = stmt.where(ImportColumnRule.enabled.is_(True))
        stmt = stmt.order_by(ImportColumnRule.priority.asc(), ImportColumnRule.id.asc())
        return list(await self._session.scalars(stmt))

    async def list_admin(
        self,
        *,
        broker: str | None,
        enabled: bool | None,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[ImportColumnRule], str | None]:
        capped = max(1, min(limit, 200))
        stmt = select(ImportColumnRule)
        if broker is not None:
            stmt = stmt.where(ImportColumnRule.broker == broker)
        if enabled is not None:
            stmt = stmt.where(ImportColumnRule.enabled.is_(enabled))
        if q:
            stmt = stmt.where(ImportColumnRule.header_norm.contains(q.lower()))
        if cursor is not None:
            payload = decode_cursor(cursor)
            cursor_at = datetime.fromisoformat(payload["c"])
            stmt = stmt.where(
                (ImportColumnRule.created_at < cursor_at)
                | (
                    (ImportColumnRule.created_at == cursor_at)
                    & (ImportColumnRule.id < payload["i"])
                )
            )
        stmt = stmt.order_by(ImportColumnRule.created_at.desc(), ImportColumnRule.id.desc()).limit(
            capped + 1
        )
        rows = list(await self._session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"c": last.created_at.isoformat(), "i": last.id})
        return rows, next_cursor

    async def by_id(self, rule_id: str) -> ImportColumnRule | None:
        return await self._session.scalar(
            select(ImportColumnRule).where(ImportColumnRule.id == rule_id)
        )

    async def create(
        self,
        *,
        broker: str,
        header_norm: str,
        match_type: str,
        target_field: str,
        options: dict[str, Any],
        priority: int,
        enabled: bool,
        is_fingerprint: bool,
        weight: int,
        note: str | None,
        user_id: uuid.UUID | None,
        is_system: bool,
    ) -> ImportColumnRule:
        rule = ImportColumnRule(
            id=import_column_rule_id(),
            user_id=user_id,
            broker=broker,
            header_norm=header_norm,
            match_type=match_type,
            target_field=target_field,
            options=options,
            priority=priority,
            enabled=enabled,
            is_system=is_system,
            is_fingerprint=is_fingerprint,
            weight=weight,
            note=note,
        )
        self._session.add(rule)
        await self._session.flush()
        return rule


async def bump_rule_version(
    session: AsyncSession,
    rule_id: str,
    *,
    expected_version: int,
    values: dict[str, Any],
    now: datetime,
) -> int:
    """乐观锁更新：`version = expected` 才命中，返回受影响行数（0 即版本冲突）。"""
    result = await session.execute(
        update(ImportColumnRule)
        .where(
            ImportColumnRule.id == rule_id,
            ImportColumnRule.version == expected_version,
        )
        .values(**values, version=ImportColumnRule.version + 1, updated_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def seed_import_column_rules(session: AsyncSession) -> int:
    """自愈式系统规则 seed：只插缺、不覆盖管理员改动（唯一键 NULLS NOT DISTINCT）。

    通用别名（`broker='*'`）来自 `FIELD_ALIASES`；华泰 12 个精确表头进指纹规则。
    应用层在 preview/commit 前调用（与 `load_rules` 同款自愈）。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from stock_contracts.import_fields import (
        FIELD_ALIASES,
        HUATAI_FINGERPRINT_HEADERS,
        alias_options,
    )

    from stock_ledger.parsers.mapping import normalize_header_v2

    rows: list[dict[str, Any]] = []
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            rows.append(
                {
                    "id": import_column_rule_id(),
                    "user_id": None,
                    "broker": "*",
                    "header_norm": normalize_header_v2(alias),
                    "match_type": "exact",
                    "target_field": field,
                    "options": alias_options(field, alias),
                    "priority": 300,
                    "enabled": True,
                    "is_system": True,
                    "is_fingerprint": False,
                    "weight": 1,
                    "note": "通用别名（seed）",
                }
            )
    for alias in HUATAI_FINGERPRINT_HEADERS:
        mapped_field = next((key for key, names in FIELD_ALIASES.items() if alias in names), None)
        if mapped_field is None:
            continue
        rows.append(
            {
                "id": import_column_rule_id(),
                "user_id": None,
                "broker": "huatai",
                "header_norm": normalize_header_v2(alias),
                "match_type": "exact",
                "target_field": mapped_field,
                "options": alias_options(mapped_field, alias),
                "priority": 100,
                "enabled": True,
                "is_system": True,
                "is_fingerprint": True,
                "weight": 1,
                "note": "华泰指纹（seed）",
            }
        )
    if not rows:
        return 0
    stmt = (
        pg_insert(ImportColumnRule)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_import_column_rule")
    )
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "ImportBillTypeRepository",
    "ImportColumnRuleRepository",
    "ImportTemplateRepository",
    "bump_rule_version",
    "seed_import_column_rules",
]
