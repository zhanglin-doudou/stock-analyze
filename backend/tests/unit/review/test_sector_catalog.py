"""API-088 公共目录应用层（`list_sector_catalog`，设计 §7.2）。

无用户维度；目录未同步 → 200 + items=[] + SECTOR_DATA_MISSING；分页/过滤/游标
损坏一律 `INVALID_CURSOR`。用桩会话替代 DB（本函数只调用 `sync_state`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pytest
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.cursor import encode_cursor
from stock_platform.ports import (
    BenchmarkMember,
    SectorBar,
    SectorCapabilities,
    SectorInfo,
    SectorMembership,
)
from stock_review.application.sectors import list_sector_catalog

_AS_OF = date(2026, 6, 30)
_IND = "industry_sw"
_PROVENANCE = "replay:sectors.json@2026-06-30#sha256:" + "0123456789abcdef" * 4


class StubSectorData:
    def __init__(
        self,
        *,
        catalog: Sequence[SectorInfo],
        memberships: Sequence[SectorMembership],
        provenance: str = _PROVENANCE,
    ) -> None:
        self._catalog = tuple(catalog)
        self._memberships = tuple(memberships)
        self._provenance = provenance

    @property
    def capabilities(self) -> SectorCapabilities:
        return SectorCapabilities(mode="replay", taxonomies=(_IND,), history_start=_AS_OF)

    @property
    def as_of(self) -> date:
        return _AS_OF

    @property
    def provenance(self) -> str:
        return self._provenance

    async def get_catalog(self, taxonomy: str | None = None) -> tuple[SectorInfo, ...]:
        if taxonomy is None:
            return self._catalog
        return tuple(row for row in self._catalog if row.taxonomy == taxonomy)

    async def get_memberships(
        self, taxonomy: str, *, as_of: date | None = None, level: int | None = None
    ) -> tuple[SectorMembership, ...]:
        return tuple(row for row in self._memberships if row.taxonomy == taxonomy)

    async def get_board_bars(
        self, sector_keys: Sequence[tuple[str, str]], start: date, end: date
    ) -> tuple[SectorBar, ...]:
        return ()

    async def get_benchmark_members(
        self, benchmark_id: str, *, as_of: date | None = None
    ) -> tuple[BenchmarkMember, ...]:
        return ()


@dataclass(slots=True)
class _State:
    checksum: str
    status: str = "succeeded"


class FakeSession:
    def __init__(self, state: _State | None) -> None:
        self._state = state

    async def get(self, _model: object, _key: str) -> _State | None:
        return self._state


_CATALOG = (
    SectorInfo(_IND, "801120.SI", "食品饮料", 1, None, "SW2021"),
    SectorInfo(_IND, "801050.SI", "银行", 1, None, "SW2021"),
    SectorInfo(_IND, "801123.SI", "饮料制造", 2, "801120.SI", "SW2021"),
    SectorInfo(_IND, "850121.SI", "白酒", 3, "801123.SI", "SW2021"),
    SectorInfo(_IND, "850511.SI", "国有大型银行", 3, "801050.SI", "SW2021"),
)
_MEMBERSHIPS = (
    SectorMembership(_IND, "850121.SI", "600519"),
    SectorMembership(_IND, "850121.SI", "000858"),
    SectorMembership(_IND, "850511.SI", "000001"),
)


async def _catalog(*, state: _State | None = _State("0123456789abcdef" + "f" * 48)):
    return await list_sector_catalog(
        FakeSession(state),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
        level=1,
    )


async def test_catalog_items_path_and_rollup_member_count() -> None:
    result = await _catalog()
    assert result.taxonomy == "industry"
    assert result.sector_data_state == "current"
    assert result.catalog_version == "0123456789abcdef"
    assert result.next_cursor is None and result.has_more is False
    # 默认只取 L1（level=1）。
    assert [item.sector_code for item in result.items] == ["801050.SI", "801120.SI"]
    by_code = {item.sector_code: item for item in result.items}
    # L1 成分数 = 后代 L3 成分去重求和。
    assert by_code["801120.SI"].member_count == 2
    assert by_code["801050.SI"].member_count == 1
    assert [node.sector_code for node in by_code["801120.SI"].path] == ["801120.SI"]
    assert by_code["801120.SI"].source == "sw"
    assert by_code["801120.SI"].classification_version == "SW2021"
    assert by_code["801120.SI"].is_active is True
    assert result.missing == []


async def test_catalog_level3_path_has_full_chain() -> None:
    result = await list_sector_catalog(
        FakeSession(_State("0123456789abcdef" + "f" * 48)),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
        level=3,
    )
    by_code = {item.sector_code: item for item in result.items}
    assert by_code["850121.SI"].path[0].sector_name == "食品饮料"
    assert [node.sector_code for node in by_code["850121.SI"].path] == [
        "801120.SI",
        "801123.SI",
        "850121.SI",
    ]
    assert by_code["850121.SI"].member_count == 2


async def test_catalog_parent_and_q_filters() -> None:
    result = await list_sector_catalog(
        FakeSession(None),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
        level=2,
        parent="801120.SI",
    )
    assert [item.sector_code for item in result.items] == ["801123.SI"]

    searched = await list_sector_catalog(
        FakeSession(None),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
        level=1,
        q="银行",
    )
    assert [item.sector_code for item in searched.items] == ["801050.SI"]


async def test_catalog_pagination_and_invalid_cursor() -> None:
    page1 = await list_sector_catalog(
        FakeSession(None),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
        level=3,
        limit=1,
    )
    assert [item.sector_code for item in page1.items] == ["850121.SI"]
    assert page1.has_more is True
    assert page1.next_cursor is not None

    page2 = await list_sector_catalog(
        FakeSession(None),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
        level=3,
        limit=1,
        cursor=page1.next_cursor,
    )
    assert [item.sector_code for item in page2.items] == ["850511.SI"]
    assert page2.has_more is False

    with pytest.raises(ApiError) as broken:
        await list_sector_catalog(
            FakeSession(None),  # type: ignore[arg-type]
            StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
            taxonomy="industry",
            level=3,
            cursor="not-a-cursor",
        )
    assert broken.value.code == ErrorCode.INVALID_CURSOR

    with pytest.raises(ApiError) as out_of_range:
        await list_sector_catalog(
            FakeSession(None),  # type: ignore[arg-type]
            StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
            taxonomy="industry",
            level=3,
            cursor=encode_cursor({"o": "99"}),
        )
    assert out_of_range.value.code == ErrorCode.INVALID_CURSOR


async def test_catalog_unsynced_returns_empty_not_404() -> None:
    result = await list_sector_catalog(
        FakeSession(None),  # type: ignore[arg-type]
        StubSectorData(catalog=(), memberships=()),
        taxonomy="industry",
    )
    assert result.items == []
    assert result.catalog_version is None
    assert result.sector_data_state == "missing"
    assert [item.code for item in result.missing] == ["SECTOR_DATA_MISSING"]


async def test_catalog_sync_failed_flag_is_reported() -> None:
    result = await list_sector_catalog(
        FakeSession(_State("0" * 64, status="failed")),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
    )
    assert any(item.code == "SECTOR_SYNC_FAILED" for item in result.missing)


async def test_catalog_version_falls_back_to_provenance_sha() -> None:
    result = await list_sector_catalog(
        FakeSession(None),  # type: ignore[arg-type]
        StubSectorData(catalog=_CATALOG, memberships=_MEMBERSHIPS),
        taxonomy="industry",
    )
    assert result.catalog_version == "0123456789abcdef"
