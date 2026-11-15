"""导入批次与预览仓储（两阶段导入的持久化）。

批次仓储负责终态收口与可撤销元数据；预览仓储负责 24h TTL 与“消费即失效”的幂等锚点。
两者都强制 `user_id` 过滤：跨账号的 preview_id / batch_id 一律按不存在处理。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.enums import ImportBatchStatus
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.ids import batch_id as new_batch_id
from stock_platform.ids import preview_id as new_preview_id

from stock_ledger.models import ImportBatch, ImportPreview
from stock_ledger.repositories.trades import decode_cursor, encode_cursor


class ImportBatchRepository:
    """导入批次：创建 → 执行 → 终态 / 撤销。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        preview_id: str | None,
        mode: str,
        broker: str,
        file_name: str,
        file_sha256: str,
        file_bytes: int,
        columns: dict[str, Any],
        date_format: str | None,
        assumptions: dict[str, Any],
        warnings: Sequence[Any],
        status: ImportBatchStatus = ImportBatchStatus.RUNNING,
        replaced_batch_id: str | None = None,
        template_id: str | None = None,
        signature: str | None = None,
        header_row_index: int | None = None,
        mapping_fingerprint: str | None = None,
        mapping_items: Sequence[Any] = (),
        type_resolutions: Sequence[Any] = (),
        mapping_source: str = "legacy",
        discarded_count: int = 0,
        remap_required: bool = False,
    ) -> ImportBatch:
        batch = ImportBatch(
            id=new_batch_id(),
            user_id=user_id,
            preview_id=preview_id,
            status=status.value,
            mode=mode,
            broker=broker,
            file_name=file_name,
            file_sha256=file_sha256,
            file_bytes=file_bytes,
            columns=columns,
            date_format=date_format,
            assumptions=assumptions,
            warnings=list(warnings),
            ranges={},
            replaced_batch_id=replaced_batch_id,
            template_id=template_id,
            signature=signature,
            header_row_index=header_row_index,
            mapping_fingerprint=mapping_fingerprint,
            mapping_items=list(mapping_items),
            type_resolutions=list(type_resolutions),
            mapping_source=mapping_source,
            discarded_count=discarded_count,
            remap_required=remap_required,
        )
        self._session.add(batch)
        await self._session.flush()
        return batch

    async def finish(
        self,
        user_id: uuid.UUID,
        batch_id: str,
        *,
        status: ImportBatchStatus,
        parsed_count: int,
        inserted_count: int,
        skipped_count: int,
        quarantined_count: int,
        ranges: dict[str, Any],
        at: datetime,
        error_code: str | None = None,
    ) -> ImportBatch:
        batch = await self._require(user_id, batch_id)
        batch.status = status.value
        batch.parsed_count = parsed_count
        batch.inserted_count = inserted_count
        batch.skipped_count = skipped_count
        batch.quarantined_count = quarantined_count
        batch.ranges = ranges
        batch.error_code = error_code
        if status is not ImportBatchStatus.PENDING:
            batch.finished_at = at
        await self._session.flush()
        return batch

    async def revoke(
        self,
        user_id: uuid.UUID,
        batch_id: str,
        *,
        reason: str,
        at: datetime,
    ) -> ImportBatch:
        batch = await self._require(user_id, batch_id)
        batch.status = ImportBatchStatus.REVOKED.value
        batch.revoke_reason = reason
        batch.revoked_at = at
        await self._session.flush()
        return batch

    async def undone(self, user_id: uuid.UUID, batch_id: str) -> bool:
        """该批次的写入是否已被撤销（幂等守卫：已撤销不应重复撤销）。"""
        batch = await self._require(user_id, batch_id)
        return batch.revoked_at is not None or batch.status == ImportBatchStatus.REVOKED.value

    async def by_id(self, user_id: uuid.UUID, batch_id: str) -> ImportBatch | None:
        return await self._session.scalar(
            select(ImportBatch).where(ImportBatch.user_id == user_id, ImportBatch.id == batch_id)
        )

    async def by_preview(self, user_id: uuid.UUID, preview_id: str) -> ImportBatch | None:
        """幂等锚点：同一次预览只允许落一个批次。"""
        return await self._session.scalar(
            select(ImportBatch)
            .where(ImportBatch.user_id == user_id, ImportBatch.preview_id == preview_id)
            .order_by(ImportBatch.created_at.asc())
        )

    async def has_mapping_conflict(
        self, user_id: uuid.UUID, file_sha256: str, mapping_fingerprint: str
    ) -> bool:
        """同文件（sha256）曾用不同映射成功导入且未撤销 → 重导冲突提示。"""
        found = await self._session.scalar(
            select(ImportBatch.id)
            .where(
                ImportBatch.user_id == user_id,
                ImportBatch.file_sha256 == file_sha256,
                ImportBatch.status == ImportBatchStatus.SUCCEEDED.value,
                ImportBatch.revoked_at.is_(None),
                ImportBatch.mapping_fingerprint.is_not(None),
                ImportBatch.mapping_fingerprint != mapping_fingerprint,
            )
            .limit(1)
        )
        return found is not None

    async def latest_active_by_signature(
        self, user_id: uuid.UUID, signature: str, *, exclude_batch_id: str
    ) -> ImportBatch | None:
        """撤销增量批次后回退模板 `last_batch_id`：最近一个未撤销的同签名批次。"""
        return await self._session.scalar(
            select(ImportBatch)
            .where(
                ImportBatch.user_id == user_id,
                ImportBatch.signature == signature,
                ImportBatch.id != exclude_batch_id,
                ImportBatch.revoked_at.is_(None),
                ImportBatch.status != ImportBatchStatus.REVOKED.value,
            )
            .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
            .limit(1)
        )

    async def list_with_cursor(
        self, user_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> tuple[list[ImportBatch], str | None]:
        capped = max(1, min(limit, 200))
        stmt = select(ImportBatch).where(ImportBatch.user_id == user_id)
        if cursor is not None:
            payload = decode_cursor(cursor)
            try:
                cursor_at = datetime.fromisoformat(payload["c"])
                cursor_id = payload["i"]
            except (KeyError, ValueError) as exc:
                raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from exc
            stmt = stmt.where(
                (ImportBatch.created_at < cursor_at)
                | ((ImportBatch.created_at == cursor_at) & (ImportBatch.id < cursor_id))
            )
        stmt = stmt.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc()).limit(capped + 1)
        rows = list(await self._session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"c": last.created_at.isoformat(), "i": last.id})
        return rows, next_cursor

    async def aggregate_stats(self, user_id: uuid.UUID) -> dict[str, int]:
        """按状态聚合计数（导入历史页的汇总条）。"""
        rows = await self._session.execute(
            select(ImportBatch.status, func.count())
            .where(ImportBatch.user_id == user_id)
            .group_by(ImportBatch.status)
        )
        stats = {status.value: 0 for status in ImportBatchStatus}
        for status_value, count in rows:
            stats[str(status_value)] = int(count)
        return stats

    async def _require(self, user_id: uuid.UUID, batch_id: str) -> ImportBatch:
        batch = await self.by_id(user_id, batch_id)
        if batch is None:
            raise NotFoundError(detail=f"导入批次 {batch_id} 不存在")
        return batch


class ImportPreviewRepository:
    """上传预览：TTL 24h、消费即失效。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        file_name: str,
        file_sha256: str,
        file_bytes: int,
        object_key: str,
        columns: dict[str, Any],
        mapping: dict[str, Any],
        date_format: str | None,
        warnings: Sequence[Any],
        sample: Sequence[Any],
        row_count: int,
        parsed_count: int,
        quarantined_count: int,
        unresolved_columns: Sequence[str],
        expires_at: datetime,
        broker: str = "generic",
        signature: str | None = None,
        header_row_index: int | None = None,
        mapping_fingerprint: str | None = None,
        stage: str = "mapping",
        mapping_items: Sequence[Any] = (),
        ignored_columns: Sequence[str] = (),
        mapping_version: int = 0,
        template_id: str | None = None,
        verification: dict[str, Any] | None = None,
        discarded_count: int = 0,
        type_resolutions: Sequence[Any] = (),
        quarantine_rows: Sequence[Any] = (),
    ) -> ImportPreview:
        preview = ImportPreview(
            id=new_preview_id(),
            user_id=user_id,
            file_name=file_name,
            file_sha256=file_sha256,
            file_bytes=file_bytes,
            object_key=object_key,
            columns=columns,
            mapping=mapping,
            date_format=date_format,
            warnings=list(warnings),
            sample=list(sample),
            row_count=row_count,
            parsed_count=parsed_count,
            quarantined_count=quarantined_count,
            unresolved_columns=list(unresolved_columns),
            expires_at=expires_at,
            broker=broker,
            signature=signature,
            header_row_index=header_row_index,
            mapping_fingerprint=mapping_fingerprint,
            stage=stage,
            mapping_items=list(mapping_items),
            ignored_columns=list(ignored_columns),
            mapping_version=mapping_version,
            template_id=template_id,
            verification=verification or {},
            discarded_count=discarded_count,
            type_resolutions=list(type_resolutions),
            quarantine_rows=list(quarantine_rows),
        )
        self._session.add(preview)
        await self._session.flush()
        return preview

    async def by_id(
        self, user_id: uuid.UUID, preview_id: str, *, now: datetime | None = None
    ) -> ImportPreview:
        """取预览；不存在 → NOT_FOUND，已过期 → PREVIEW_EXPIRED（410，可重传）。"""
        preview = await self._session.scalar(
            select(ImportPreview).where(
                ImportPreview.user_id == user_id, ImportPreview.id == preview_id
            )
        )
        if preview is None:
            raise ApiError(ErrorCode.IMPORT_PREVIEW_NOT_FOUND, detail=f"预览 {preview_id} 不存在")
        if preview.expires_at <= (now or datetime.now(tz=UTC)):
            raise ApiError(ErrorCode.IMPORT_PREVIEW_EXPIRED, detail="预览已过期")
        return preview

    async def lock_by_id(
        self, user_id: uuid.UUID, preview_id: str, *, now: datetime | None = None
    ) -> ImportPreview:
        """事务内 `SELECT ... FOR UPDATE`：两步编辑的并发写只允许一个赢。"""
        preview = await self._session.scalar(
            select(ImportPreview)
            .where(ImportPreview.user_id == user_id, ImportPreview.id == preview_id)
            .with_for_update()
        )
        if preview is None:
            raise ApiError(ErrorCode.IMPORT_PREVIEW_NOT_FOUND, detail=f"预览 {preview_id} 不存在")
        if preview.expires_at <= (now or datetime.now(tz=UTC)):
            raise ApiError(ErrorCode.IMPORT_PREVIEW_EXPIRED, detail="预览已过期")
        return preview

    async def mark_consumed(
        self, user_id: uuid.UUID, preview_id: str, *, at: datetime
    ) -> ImportPreview:
        preview = await self._require(user_id, preview_id)
        preview.consumed_at = at
        await self._session.flush()
        return preview

    async def purge_expired(self, user_id: uuid.UUID, *, now: datetime) -> int:
        """清理过期且未消费的预览（对象存储回收由调用方负责）。"""
        result = await self._session.execute(
            delete(ImportPreview).where(
                ImportPreview.user_id == user_id,
                ImportPreview.expires_at < now,
                ImportPreview.consumed_at.is_(None),
            )
        )
        rowcount = getattr(result, "rowcount", 0)
        return int(rowcount or 0)

    async def _require(self, user_id: uuid.UUID, preview_id: str) -> ImportPreview:
        preview = await self._session.scalar(
            select(ImportPreview).where(
                ImportPreview.user_id == user_id, ImportPreview.id == preview_id
            )
        )
        if preview is None:
            raise NotFoundError(detail=f"预览 {preview_id} 不存在")
        return preview


__all__ = ["ImportBatchRepository", "ImportPreviewRepository"]
