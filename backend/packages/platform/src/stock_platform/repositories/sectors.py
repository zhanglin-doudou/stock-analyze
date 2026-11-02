"""`market.*` 板块公共数据仓储（无 `user_id`：公共参考数据，与 `DailyBar` 一致）。

设计：docs/architecture/2026-10-31-sector-data.md §5.1。只做读取/落库，不做业务判断；
`CachedSectorSource` 与同步 CLI 复用同一套 SQL 语义（单一读路径）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform.market_models import (
    BenchmarkMember,
    Sector,
    SectorBar,
    SectorMember,
    SectorSyncState,
)
from stock_platform.ports import SectorInfo, SectorMembership


class SectorRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 目录 ────────────────────────────────────────────
    async def list_catalog(
        self, *, taxonomy: str | None = None, level: int | None = None, active_only: bool = True
    ) -> list[SectorInfo]:
        conditions = []
        if taxonomy is not None:
            conditions.append(Sector.taxonomy == taxonomy)
        if level is not None:
            conditions.append(Sector.level == level)
        if active_only:
            conditions.append(Sector.is_active.is_(True))
        stmt = (
            select(Sector)
            .where(*conditions)
            .order_by(Sector.taxonomy, Sector.level, Sector.sort_order, Sector.sector_code)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            SectorInfo(
                taxonomy=row.taxonomy,
                sector_code=row.sector_code,
                name=row.name,
                level=row.level,
                parent_code=row.parent_code,
                classification_version=row.classification_version,
            )
            for row in rows
        ]

    # ── 成分（时点区间命中的「当前有效」行）────────────────
    async def memberships_at(
        self,
        *,
        taxonomy: str,
        as_of: date,
        level: int | None = None,
        symbols: Sequence[str] | None = None,
    ) -> list[SectorMembership]:
        conditions = [
            SectorMember.taxonomy == taxonomy,
            SectorMember.effective_from <= as_of,
        ]
        conditions.append(
            (SectorMember.effective_to.is_(None)) | (SectorMember.effective_to > as_of)
        )
        if symbols is not None:
            conditions.append(SectorMember.symbol.in_(tuple(symbols)))
        stmt = select(SectorMember)
        if level is not None:
            stmt = stmt.join(
                Sector,
                and_(
                    Sector.taxonomy == SectorMember.taxonomy,
                    Sector.sector_code == SectorMember.sector_code,
                ),
            ).where(Sector.level == level)
        rows = (await self._session.scalars(stmt.where(*conditions))).all()
        return [
            SectorMembership(taxonomy=row.taxonomy, sector_code=row.sector_code, symbol=row.symbol)
            for row in rows
        ]

    # ── 基准权重 ────────────────────────────────────────
    async def benchmark_weights_at(self, *, benchmark_id: str, as_of: date) -> dict[str, int]:
        conditions = [
            BenchmarkMember.benchmark_id == benchmark_id,
            BenchmarkMember.effective_from <= as_of,
            (BenchmarkMember.effective_to.is_(None)) | (BenchmarkMember.effective_to > as_of),
        ]
        stmt = select(BenchmarkMember.symbol, BenchmarkMember.weight_units).where(*conditions)
        rows = (await self._session.execute(stmt)).all()
        return {str(symbol): int(weight) for symbol, weight in rows}

    async def benchmark_weight_as_of(self, *, benchmark_id: str, as_of: date) -> date | None:
        stmt = (
            select(BenchmarkMember.weight_as_of)
            .where(
                BenchmarkMember.benchmark_id == benchmark_id,
                BenchmarkMember.effective_from <= as_of,
                (BenchmarkMember.effective_to.is_(None)) | (BenchmarkMember.effective_to > as_of),
            )
            .order_by(BenchmarkMember.effective_from.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    # ── 板块日线 ────────────────────────────────────────
    async def board_bars(
        self,
        *,
        keys: Sequence[tuple[str, str]],
        start: date,
        end: date,
    ) -> list[SectorBar]:
        wanted = [(taxonomy, code) for taxonomy, code in keys]
        if not wanted:
            return []
        conditions = [
            SectorBar.trade_date >= start,
            SectorBar.trade_date <= end,
        ]
        stmt = select(SectorBar).where(*conditions)
        rows = (await self._session.scalars(stmt)).all()
        allowed = set(wanted)
        return [row for row in rows if (row.taxonomy, row.sector_code) in allowed]

    # ── 同步状态 ────────────────────────────────────────
    async def sync_state(self, *, dataset_key: str) -> SectorSyncState | None:
        return await self._session.get(SectorSyncState, dataset_key)

    async def upsert_sync_state(
        self,
        *,
        dataset_key: str,
        status: str,
        as_of: date | None,
        snapshot_id: str | None,
        catalog_as_of: date | None,
        membership_as_of: date | None,
        weight_as_of: date | None,
        classification_version: str | None,
        entity_count: int,
        member_count: int,
        source: str,
        checksum: str,
        pulled_at: datetime,
        error_detail: str | None = None,
        bar_as_of: date | None = None,
    ) -> None:
        row = await self.sync_state(dataset_key=dataset_key)
        if row is None:
            row = SectorSyncState(dataset_key=dataset_key)
            self._session.add(row)
        row.status = status
        row.as_of = as_of
        row.snapshot_id = snapshot_id
        row.catalog_as_of = catalog_as_of
        row.membership_as_of = membership_as_of
        row.bar_as_of = bar_as_of
        row.weight_as_of = weight_as_of
        row.classification_version = classification_version
        row.entity_count = entity_count
        row.member_count = member_count
        row.source = source
        row.checksum = checksum
        row.pulled_at = pulled_at
        row.error_detail = error_detail

    # ── 同步写入（事务由调用方管理）──────────────────────
    async def upsert_sectors(self, rows: Sequence[Sector], *, now: datetime) -> None:
        del now  # 行对象自带 pulled_at；保留签名便于审计扩展
        for row in rows:
            await self._session.merge(row)

    async def close_members(
        self,
        *,
        taxonomy: str,
        codes: Sequence[str],
        keep: set[tuple[str, str]],
        effective_to: date,
    ) -> int:
        """把「当前有效但不在新快照」的成分区间关闭；返回关闭行数。"""
        conditions = [
            SectorMember.taxonomy == taxonomy,
            SectorMember.effective_to.is_(None),
        ]
        if codes:
            conditions.append(SectorMember.sector_code.in_(tuple(codes)))
        rows = (await self._session.scalars(select(SectorMember).where(*conditions))).all()
        closed = 0
        for row in rows:
            if (row.sector_code, row.symbol) in keep:
                continue
            row.effective_to = effective_to
            closed += 1
        return closed

    async def insert_members(self, rows: Sequence[SectorMember]) -> None:
        for row in rows:
            self._session.add(row)

    async def close_benchmark_members(
        self,
        *,
        benchmark_id: str,
        keep: set[str],
        effective_to: date,
    ) -> int:
        rows = (
            await self._session.scalars(
                select(BenchmarkMember).where(
                    BenchmarkMember.benchmark_id == benchmark_id,
                    BenchmarkMember.effective_to.is_(None),
                )
            )
        ).all()
        closed = 0
        for row in rows:
            if row.symbol in keep:
                continue
            row.effective_to = effective_to
            closed += 1
        return closed

    async def insert_benchmark_members(self, rows: Sequence[BenchmarkMember]) -> None:
        for row in rows:
            self._session.add(row)

    async def upsert_bars(self, rows: Sequence[SectorBar]) -> None:
        for row in rows:
            await self._session.merge(row)


def new_snapshot_id() -> str:
    """同步快照 ID（ULID 风格前缀 + uuid，仅供审计关联）。"""
    return f"snap_{uuid.uuid4().hex[:24]}"


__all__ = ["SectorRepository", "new_snapshot_id"]
