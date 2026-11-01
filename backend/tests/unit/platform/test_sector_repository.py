"""`market.*` 板块仓储（设计 §5.1）：区间命中、索引唯一性、同步写入幂等。

`market` schema 是跨用例共享的公共缓存，不进全局清表范围；本文件自清理
`market.sector*` 六张表，避免污染其它用例（与 `test_market_cache.py` 同法）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_platform.market_models import (
    BenchmarkMember,
    Sector,
    SectorBar,
    SectorMember,
    SectorSyncState,
)
from stock_platform.repositories.sectors import SectorRepository, new_snapshot_id

_NOW = datetime(2026, 6, 30, 15, 0, tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def _clean_market(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE market.sector_bar, market.sector_pull_range, "
                "market.sector_sync_state, market.benchmark_member, "
                "market.sector_member, market.sector RESTART IDENTITY CASCADE"
            )
        )
    yield


def _sector(
    code: str, *, level: int, name: str, parent: str | None = None, active: bool = True
) -> Sector:
    return Sector(
        taxonomy="industry_sw",
        sector_code=code,
        level=level,
        name=name,
        parent_code=parent,
        classification_version="SW2021",
        source="sw",
        is_active=active,
        sort_order=0,
        pulled_at=_NOW,
    )


async def _seed_catalog(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session:
        session.add_all(
            [
                _sector("801120.SI", level=1, name="食品饮料"),
                _sector("801050.SI", level=1, name="银行"),
                _sector("801123.SI", level=2, name="饮料制造", parent="801120.SI"),
                _sector("850121.SI", level=3, name="白酒", parent="801123.SI"),
            ]
        )
        await session.commit()


async def _seed_members(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session:
        session.add_all(
            [
                SectorMember(
                    taxonomy="industry_sw",
                    sector_code="850121.SI",
                    symbol="600519",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    observed_at=_NOW,
                    source="sw",
                ),
                # 已关闭的历史区间：as_of 在区间内仍命中，之后不再命中。
                SectorMember(
                    taxonomy="industry_sw",
                    sector_code="801050.SI",
                    symbol="600519",
                    effective_from=date(2025, 1, 1),
                    effective_to=date(2026, 1, 1),
                    observed_at=_NOW,
                    source="sw",
                ),
            ]
        )
        await session.commit()


async def test_list_catalog_filters_taxonomy_level_and_active(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        all_rows = await repo.list_catalog(taxonomy="industry_sw")
        assert [(row.sector_code, row.level) for row in all_rows] == [
            ("801050.SI", 1),
            ("801120.SI", 1),
            ("801123.SI", 2),
            ("850121.SI", 3),
        ]
        level1 = await repo.list_catalog(taxonomy="industry_sw", level=1)
        assert {row.sector_code for row in level1} == {"801050.SI", "801120.SI"}
        assert level1[0].classification_version == "SW2021"


async def test_list_catalog_excludes_inactive_by_default(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    async with AsyncSession(owner_engine) as session:
        session.add(_sector("801999.SI", level=1, name="旧板块", active=False))
        await session.commit()
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        active = await repo.list_catalog(taxonomy="industry_sw", level=1)
        every = await repo.list_catalog(taxonomy="industry_sw", level=1, active_only=False)
        assert "801999.SI" not in {row.sector_code for row in active}
        assert "801999.SI" in {row.sector_code for row in every}


async def test_memberships_at_half_open_interval(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    await _seed_members(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        inside_history = await repo.memberships_at(taxonomy="industry_sw", as_of=date(2025, 6, 1))
        assert {(row.sector_code, row.symbol) for row in inside_history} == {
            ("801050.SI", "600519")
        }
        current = await repo.memberships_at(taxonomy="industry_sw", as_of=date(2026, 6, 30))
        assert {(row.sector_code, row.symbol) for row in current} == {("850121.SI", "600519")}
        # 半开区间：effective_to 当天已不生效。
        boundary = await repo.memberships_at(taxonomy="industry_sw", as_of=date(2026, 1, 1))
        assert {(row.sector_code, row.symbol) for row in boundary} == {("850121.SI", "600519")}
        # level 过滤走目录表 join。
        level1 = await repo.memberships_at(taxonomy="industry_sw", as_of=date(2025, 6, 1), level=1)
        assert [row.sector_code for row in level1] == ["801050.SI"]


async def test_memberships_at_filters_symbols(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    await _seed_members(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        rows = await repo.memberships_at(
            taxonomy="industry_sw", as_of=date(2026, 6, 30), symbols=["000001"]
        )
        assert rows == []


async def test_industry_current_unique_index_blocks_double_membership(
    owner_engine: AsyncEngine,
) -> None:
    await _seed_catalog(owner_engine)
    async with AsyncSession(owner_engine) as session:
        session.add(
            SectorMember(
                taxonomy="industry_sw",
                sector_code="850121.SI",
                symbol="600519",
                effective_from=date(2026, 1, 1),
                effective_to=None,
                observed_at=_NOW,
                source="sw",
            )
        )
        await session.flush()
        session.add(
            SectorMember(
                taxonomy="industry_sw",
                sector_code="801123.SI",
                symbol="600519",
                effective_from=date(2026, 6, 1),
                effective_to=None,
                observed_at=_NOW,
                source="sw",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_benchmark_weights_at_and_weight_as_of(owner_engine: AsyncEngine) -> None:
    async with AsyncSession(owner_engine) as session:
        session.add_all(
            [
                BenchmarkMember(
                    benchmark_id="hs300",
                    symbol="600519",
                    effective_from=date(2026, 6, 30),
                    weight_units=5_320_000,
                    weight_as_of=date(2026, 6, 30),
                    source="csindex",
                    pulled_at=_NOW,
                ),
                BenchmarkMember(
                    benchmark_id="hs300",
                    symbol="000001",
                    effective_from=date(2026, 6, 30),
                    weight_units=2_100_000,
                    weight_as_of=date(2026, 6, 30),
                    source="csindex",
                    pulled_at=_NOW,
                ),
            ]
        )
        await session.commit()

    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        weights = await repo.benchmark_weights_at(benchmark_id="hs300", as_of=date(2026, 6, 30))
        assert weights == {"600519": 5_320_000, "000001": 2_100_000}
        assert await repo.benchmark_weight_as_of(
            benchmark_id="hs300", as_of=date(2026, 6, 30)
        ) == date(2026, 6, 30)
        assert await repo.benchmark_weights_at(benchmark_id="hs300", as_of=date(2026, 5, 1)) == {}
        assert await repo.benchmark_weights_at(benchmark_id="nope", as_of=date(2026, 6, 30)) == {}


async def test_board_bars_range_and_keys(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    async with AsyncSession(owner_engine) as session:
        session.add_all(
            [
                SectorBar(
                    taxonomy="industry_sw",
                    sector_code="850121.SI",
                    trade_date=date(2026, 6, 29),
                    close_units=1_000_000,
                    return_units=0,
                    source="sw",
                    pulled_at=_NOW,
                ),
                SectorBar(
                    taxonomy="industry_sw",
                    sector_code="850121.SI",
                    trade_date=date(2026, 6, 30),
                    close_units=1_010_000,
                    return_units=10_000_000,
                    source="sw",
                    pulled_at=_NOW,
                ),
                SectorBar(
                    taxonomy="industry_sw",
                    sector_code="801050.SI",
                    trade_date=date(2026, 6, 30),
                    close_units=900_000,
                    return_units=0,
                    source="sw",
                    pulled_at=_NOW,
                ),
            ]
        )
        await session.commit()

    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        rows = await repo.board_bars(
            keys=[("industry_sw", "850121.SI")],
            start=date(2026, 6, 29),
            end=date(2026, 6, 30),
        )
        assert [(row.trade_date, row.close_units) for row in rows] == [
            (date(2026, 6, 29), 1_000_000),
            (date(2026, 6, 30), 1_010_000),
        ]
        narrow = await repo.board_bars(
            keys=[("industry_sw", "850121.SI")], start=date(2026, 6, 30), end=date(2026, 6, 30)
        )
        assert len(narrow) == 1
        assert await repo.board_bars(keys=[], start=date(2026, 1, 1), end=date(2026, 12, 31)) == []


async def test_sync_state_upsert_is_idempotent(owner_engine: AsyncEngine) -> None:
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        await repo.upsert_sync_state(
            dataset_key="sector_all",
            status="succeeded",
            as_of=date(2026, 6, 30),
            snapshot_id=new_snapshot_id(),
            catalog_as_of=date(2026, 6, 30),
            membership_as_of=date(2026, 6, 30),
            weight_as_of=date(2026, 6, 30),
            classification_version="SW2021",
            entity_count=4,
            member_count=2,
            source="sw",
            checksum="a" * 64,
            pulled_at=_NOW,
        )
        await session.commit()
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        row = await repo.sync_state(dataset_key="sector_all")
        assert row is not None
        assert row.status == "succeeded"
        assert row.checksum == "a" * 64
        await repo.upsert_sync_state(
            dataset_key="sector_all",
            status="failed",
            as_of=date(2026, 6, 30),
            snapshot_id=None,
            catalog_as_of=None,
            membership_as_of=None,
            weight_as_of=None,
            classification_version=None,
            entity_count=0,
            member_count=0,
            source="sw",
            checksum="b" * 64,
            pulled_at=_NOW,
            error_detail="上游超时",
        )
        await session.commit()
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        row = await repo.sync_state(dataset_key="sector_all")
        assert row is not None
        assert row.status == "failed"
        assert row.error_detail == "上游超时"
        assert row.checksum == "b" * 64
        # upsert 不是「插入第二行」。
        total = await session.scalar(text("select count(*) from market.sector_sync_state"))
        assert total == 1


async def test_close_members_keeps_unchanged_and_closes_removed(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    async with AsyncSession(owner_engine) as session:
        session.add_all(
            [
                SectorMember(
                    taxonomy="industry_sw",
                    sector_code="850121.SI",
                    symbol="600519",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    observed_at=_NOW,
                    source="sw",
                ),
                SectorMember(
                    taxonomy="industry_sw",
                    sector_code="850121.SI",
                    symbol="000858",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    observed_at=_NOW,
                    source="sw",
                ),
            ]
        )
        await session.commit()

    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        closed = await repo.close_members(
            taxonomy="industry_sw",
            codes=["850121.SI"],
            keep={("850121.SI", "600519")},
            effective_to=date(2026, 6, 30),
        )
        await session.commit()
        assert closed == 1

    async with AsyncSession(owner_engine) as session:
        current = await SectorRepository(session).memberships_at(
            taxonomy="industry_sw", as_of=date(2026, 6, 30)
        )
        assert {(row.symbol) for row in current} == {"600519"}


async def test_close_benchmark_members_and_insert(owner_engine: AsyncEngine) -> None:
    async with AsyncSession(owner_engine) as session:
        session.add_all(
            [
                BenchmarkMember(
                    benchmark_id="hs300",
                    symbol="600519",
                    effective_from=date(2026, 1, 1),
                    weight_units=5_000_000,
                    weight_as_of=date(2026, 1, 1),
                    source="csindex",
                    pulled_at=_NOW,
                ),
                BenchmarkMember(
                    benchmark_id="hs300",
                    symbol="000858",
                    effective_from=date(2026, 1, 1),
                    weight_units=2_000_000,
                    weight_as_of=date(2026, 1, 1),
                    source="csindex",
                    pulled_at=_NOW,
                ),
            ]
        )
        await session.commit()

    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        closed = await repo.close_benchmark_members(
            benchmark_id="hs300", keep={"600519"}, effective_to=date(2026, 6, 30)
        )
        await session.commit()
        assert closed == 1
        await repo.insert_benchmark_members(
            [
                BenchmarkMember(
                    benchmark_id="hs300",
                    symbol="000858",
                    effective_from=date(2026, 6, 30),
                    weight_units=1_980_000,
                    weight_as_of=date(2026, 6, 30),
                    source="csindex",
                    pulled_at=_NOW,
                )
            ]
        )
        await session.commit()

    async with AsyncSession(owner_engine) as session:
        weights = await SectorRepository(session).benchmark_weights_at(
            benchmark_id="hs300", as_of=date(2026, 6, 30)
        )
        assert weights == {"600519": 5_000_000, "000858": 1_980_000}


async def test_upsert_sectors_merges_existing_rows(owner_engine: AsyncEngine) -> None:
    await _seed_catalog(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = SectorRepository(session)
        renamed = _sector("801120.SI", level=1, name="食品饮料(新)")
        await repo.upsert_sectors([renamed], now=_NOW)
        await session.commit()
    async with AsyncSession(owner_engine) as session:
        rows = await SectorRepository(session).list_catalog(taxonomy="industry_sw", level=1)
        name = {row.sector_code: row.name for row in rows}
        assert name["801120.SI"] == "食品饮料(新)"

    async with AsyncSession(owner_engine) as session:
        total = await session.scalar(text("select count(*) from market.sector"))
        assert total == 4


async def test_new_snapshot_id_is_prefixed_and_unique() -> None:
    first = new_snapshot_id()
    second = new_snapshot_id()
    assert first.startswith("snap_")
    assert len(first) == len("snap_") + 24
    assert first != second


async def test_sync_state_model_roundtrip_defaults(owner_engine: AsyncEngine) -> None:
    async with AsyncSession(owner_engine) as session:
        row = SectorSyncState(
            dataset_key="sector_all",
            status="succeeded",
            source="sw",
            checksum="c" * 64,
            pulled_at=_NOW,
        )
        session.add(row)
        await session.commit()
    async with AsyncSession(owner_engine) as session:
        loaded = await SectorRepository(session).sync_state(dataset_key="sector_all")
        assert loaded is not None
        assert loaded.entity_count == 0
        assert loaded.member_count == 0
