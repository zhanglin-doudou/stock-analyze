"""08a：行业指数日线（``market.sector_bar``）生产链单测。

覆盖（设计 docs/architecture/2026-11-02-five-factor-attribution.md §4.2）：

- THS 指数日 K 解析：float 收盘 → scale=6 定点、环比收益 scale=8、
  窗口首行 return=0、坏行 fail-closed（SectorValidationError）；
- replay 板块夹具 series：bytes 确定性、覆盖 2026-04-01~2026-09-30 工作日、
  与 quotes.json 工作日序列对齐（行业指数须与个股参考日一致）；
- live 落盘（``_persist_bars`` / ``_persist``）：bar 主键幂等、
  ``SectorPullRange`` 区间并集（尾部推进各自成行）、``bar_as_of`` 只前进不回退、
  目录同步不抹掉既有 ``bar_as_of``。

DB 用例走 Docker 开发库（STOCK_DB_PORT=5433）；``market`` schema 是跨用例
共享的公共缓存，本文件自清理 ``market.sector*`` 六表（与 test_market_cache 同法）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.clock import FrozenClock
from stock_core.values import Price, Ratio
from stock_ledger.fixtures import generate_market_quotes
from stock_platform import sector_live
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_platform.market_models import (
    Sector,
    SectorBar,
    SectorPullRange,
    SectorSyncState,
)
from stock_platform.ports import SectorInfo
from stock_platform.repositories.sectors import SectorRepository
from stock_platform.sector_fixture import generate_sector_fixture
from stock_platform.sector_live import DATASET_KEY, AkshareSectorSource, _board_bar_records
from stock_platform.sector_validation import SectorValidationError

_NOW = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
_TODAY = date(2026, 7, 1)


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


@pytest_asyncio.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings, role="app")
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def live_source(settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch) -> Any:
    """AkshareSectorSource（akshare 桩掉：只测解析与落盘，不外呼）。"""
    monkeypatch.setattr(sector_live, "_import_akshare", lambda: object())
    return AkshareSectorSource(
        settings, clock=FrozenClock("2026-07-01T09:00:00+00:00"), session_factory=db.session
    )


async def _seed_sector(owner_engine: AsyncEngine, code: str = "850811.SI") -> None:
    """落 bar 前必须先有目录行（fk_sector_bar_sector）。"""
    async with AsyncSession(owner_engine) as session:
        session.add(
            Sector(
                taxonomy="industry_sw",
                sector_code=code,
                level=3,
                name="半导体",
                parent_code=None,
                classification_version="SW2021",
                source="sw",
                is_active=True,
                sort_order=0,
                pulled_at=_NOW,
            )
        )
        await session.commit()


async def _seed_state(owner_engine: AsyncEngine, *, bar_as_of: date | None) -> None:
    async with AsyncSession(owner_engine) as session:
        await SectorRepository(session).upsert_sync_state(
            dataset_key=DATASET_KEY,
            status="succeeded",
            as_of=date(2026, 6, 30),
            snapshot_id="snap_seed",
            catalog_as_of=date(2026, 6, 30),
            membership_as_of=date(2026, 6, 30),
            weight_as_of=date(2026, 6, 30),
            classification_version="THS",
            entity_count=1,
            member_count=1,
            source="ths",
            checksum="seed-checksum",
            pulled_at=_NOW,
            bar_as_of=bar_as_of,
        )
        await session.commit()


# ── THS 指数日 K 解析 ───────────────────────────────────────────


def test_board_bar_records_parses_ths_rows() -> None:
    records = [
        {"日期": "2026-03-02", "收盘价": 9048.101},
        {"日期": datetime(2026, 3, 3, tzinfo=UTC), "收盘价": 9100.5},
        {"日期": "2026-03-04", "收盘价": 9000.0},
    ]
    bars = _board_bar_records("industry_sw", "850811.SI", records)
    assert [bar.trade_date for bar in bars] == [
        date(2026, 3, 2),
        date(2026, 3, 3),
        date(2026, 3, 4),
    ]
    assert [bar.close_units for bar in bars] == [
        Price.from_str("9048.101").units,
        Price.from_str("9100.500").units,
        Price.from_str("9000.000").units,
    ]
    # 窗口首行无前收盘 → return 0；其余为环比（scale=8）。
    assert bars[0].return_units == 0
    assert (
        bars[1].return_units
        == Ratio.from_decimal(Decimal("9100.500") / Decimal("9048.101") - Decimal("1")).units
    )
    assert (
        bars[2].return_units
        == Ratio.from_decimal(Decimal("9000.000") / Decimal("9100.500") - Decimal("1")).units
    )
    assert all(bar.taxonomy == "industry_sw" for bar in bars)


@pytest.mark.parametrize(
    "bad",
    [None, "", "abc", -1.0, 0],
    ids=["none", "empty", "text", "negative", "zero"],
)
def test_board_bar_records_rejects_bad_close(bad: Any) -> None:
    with pytest.raises(SectorValidationError):
        _board_bar_records("industry_sw", "850811.SI", [{"日期": "2026-03-02", "收盘价": bad}])


@pytest.mark.parametrize(
    "bad",
    ["2026/03/02", "not-a-date", None],
    ids=["slash", "text", "none"],
)
def test_board_bar_records_rejects_bad_date(bad: Any) -> None:
    with pytest.raises(SectorValidationError):
        _board_bar_records("industry_sw", "850811.SI", [{"日期": bad, "收盘价": 10.0}])


# ── replay 板块夹具 series ──────────────────────────────────────


def _fixture_payload() -> dict[str, Any]:
    payload: Any = json.loads(generate_sector_fixture().decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _business_days(start: str, end: str) -> list[str]:
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    days: list[str] = []
    while cursor <= last:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def test_sector_fixture_series_is_deterministic_and_covers_span() -> None:
    assert generate_sector_fixture() == generate_sector_fixture()  # bytes 可复现
    payload = _fixture_payload()
    assert set(payload["series"]) == {"850121.SI", "850511.SI", "850811.SI", "850911.SI"}
    expected_dates = _business_days("2026-01-30", "2026-09-30")
    for rows in payload["series"].values():
        assert [row["date"] for row in rows] == expected_dates
        # 收盘为正的定点串（scale=6）；首行 return 0，其余环比 scale=8。
        assert all(Price.from_str(row["close"]).units > 0 for row in rows)
        assert rows[0]["return"] == "0.00000000"
        assert all(len(row["return"].split(".")[-1]) == 8 for row in rows)


def test_sector_series_dates_align_with_quotes_business_days() -> None:
    """行业指数参考日必须与个股行情参考日一致（设计 §4.2-5）。"""
    quotes = json.loads(generate_market_quotes().decode("utf-8"))
    quotes_dates = [row["date"] for row in quotes["series"]["hs300_tr"]]
    payload = _fixture_payload()
    for rows in payload["series"].values():
        assert [row["date"] for row in rows] == quotes_dates


def test_sector_series_closes_differ_across_boards() -> None:
    """各行业曲线互不相同（归因 sector 因子非 0 的前提）。"""
    payload = _fixture_payload()
    last_closes = {code: rows[-1]["close"] for code, rows in payload["series"].items()}
    assert len(set(last_closes.values())) == len(last_closes)


# ── live 落盘（DB）──────────────────────────────────────────────


async def test_persist_bars_idempotent_and_pull_range_union(
    owner_engine: AsyncEngine, live_source: Any
) -> None:
    await _seed_sector(owner_engine)
    await _seed_state(owner_engine, bar_as_of=date(2026, 5, 29))

    bars = _board_bar_records(
        "industry_sw",
        "850811.SI",
        [{"日期": "2026-06-29", "收盘价": 4000.0}, {"日期": "2026-06-30", "收盘价": 4040.0}],
    )
    bar_as_of = await live_source._persist_bars(
        bars, fetch_start=date(2026, 6, 1), requested_end=date(2026, 6, 30)
    )
    assert bar_as_of == date(2026, 6, 30)

    async with AsyncSession(owner_engine) as session:
        bar_rows = (
            (await session.execute(select(SectorBar).order_by(SectorBar.trade_date)))
            .scalars()
            .all()
        )
        assert [(row.trade_date, row.close_units, row.source) for row in bar_rows] == [
            (date(2026, 6, 29), Price.from_str("4000.000").units, "ths"),
            (date(2026, 6, 30), Price.from_str("4040.000").units, "ths"),
        ]
        ranges = (await session.execute(select(SectorPullRange))).scalars().all()
        assert len(ranges) == 1
        assert ranges[0].range_start == date(2026, 6, 1)
        assert ranges[0].range_end == date(2026, 6, 30)
        assert ranges[0].complete is True
        assert ranges[0].bar_count == 2

    # 区间推进 → 并集成新行（旧行保留）；close 修正按主键 upsert 覆盖。
    extended = _board_bar_records(
        "industry_sw",
        "850811.SI",
        [
            {"日期": "2026-06-29", "收盘价": 4000.0},
            {"日期": "2026-06-30", "收盘价": 4041.0},  # 上游修正
            {"日期": "2026-07-01", "收盘价": 4080.0},
        ],
    )
    bar_as_of = await live_source._persist_bars(
        extended, fetch_start=date(2026, 6, 1), requested_end=_TODAY
    )
    assert bar_as_of == date(2026, 7, 1)
    async with AsyncSession(owner_engine) as session:
        corrected = await session.get(SectorBar, ("industry_sw", "850811.SI", date(2026, 6, 30)))
        assert corrected is not None
        assert corrected.close_units == Price.from_str("4041.000").units
        ranges = (
            (await session.execute(select(SectorPullRange).order_by(SectorPullRange.range_end)))
            .scalars()
            .all()
        )
        assert [row.range_end for row in ranges] == [date(2026, 6, 30), date(2026, 7, 1)]

    # bar_as_of 只前进不回退：重放更早区间不把新鲜度拖回去。
    stale = _board_bar_records(
        "industry_sw", "850811.SI", [{"日期": "2026-06-02", "收盘价": 3950.0}]
    )
    bar_as_of = await live_source._persist_bars(
        stale, fetch_start=date(2026, 6, 2), requested_end=date(2026, 6, 2)
    )
    assert bar_as_of == date(2026, 7, 1)
    async with AsyncSession(owner_engine) as session:
        state = await session.get(SectorSyncState, DATASET_KEY)
        assert state is not None
        assert state.bar_as_of == date(2026, 7, 1)
        # 其余字段原样保留
        assert state.checksum == "seed-checksum"
        assert state.status == "succeeded"


async def test_persist_catalog_preserves_bar_as_of(
    owner_engine: AsyncEngine, live_source: Any
) -> None:
    """目录同步不得抹掉 bar_as_of（08a 修复点：_persist 透传既有值）。"""
    await _seed_state(owner_engine, bar_as_of=date(2026, 6, 30))
    snapshot = sector_live._Snapshot(
        catalog=(
            SectorInfo(
                taxonomy="industry_sw",
                sector_code="850999.SI",
                name="测试板块",
                level=1,
                parent_code=None,
                classification_version="THS",
            ),
        ),
        members={},
        benchmark_weights={},
        checksum="snapshot-1",
        concept_source="ths",
    )
    assert await live_source._persist(snapshot, today=_TODAY) is True
    # 同 checksum + 同 as_of 重跑 → no-op
    assert await live_source._persist(snapshot, today=_TODAY) is False
    async with AsyncSession(owner_engine) as session:
        state = await session.get(SectorSyncState, DATASET_KEY)
        assert state is not None
        assert state.bar_as_of == date(2026, 6, 30)
        assert state.checksum == "snapshot-1"


async def test_persist_bars_empty_is_noop(live_source: Any) -> None:
    assert (
        await live_source._persist_bars((), fetch_start=date(2026, 6, 1), requested_end=_TODAY)
        is None
    )


# ── 只回填行情（make sync-sector-bars）────────────────────────────


async def test_sync_bars_only_resumes_from_bar_as_of(
    owner_engine: AsyncEngine, live_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只回填日线：从 bar_as_of 次日续传，目录/成分原样不动。"""
    await _seed_sector(owner_engine)
    await _seed_state(owner_engine, bar_as_of=date(2026, 6, 29))

    async def fake_fetch(boards: Any, *, start: date, end: date) -> tuple[Any, list[Any]]:
        assert start == date(2026, 6, 30)
        assert [code for code, _ in boards] == ["850811.SI"]
        return (
            _board_bar_records(
                "industry_sw", "850811.SI", [{"日期": "2026-06-30", "收盘价": 4040.0}]
            ),
            [],
        )

    monkeypatch.setattr(live_source, "_fetch_board_bars", fake_fetch)
    report = await live_source.sync_bars_to_cache(today=_TODAY)
    assert report.status == "succeeded"
    assert "bar_as_of=2026-06-30" in report.message

    async with AsyncSession(owner_engine) as session:
        state = await session.get(SectorSyncState, DATASET_KEY)
        assert state is not None
        assert state.bar_as_of == date(2026, 6, 30)
        catalog = await SectorRepository(session).list_catalog(taxonomy="industry_sw")
        assert [(row.sector_code, row.level) for row in catalog] == [("850811.SI", 3)]


async def test_sync_bars_dry_run_has_no_writes(
    owner_engine: AsyncEngine, live_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run 只读配置/DB：不联网、不写 bar/水位。"""
    await _seed_sector(owner_engine)
    await _seed_state(owner_engine, bar_as_of=date(2026, 6, 29))

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run 不应联网拉行情")

    monkeypatch.setattr(live_source, "_fetch_board_bars", boom)
    report = await live_source.sync_bars_to_cache(today=_TODAY, dry_run=True)
    assert report.status == "skipped"
    assert "不联网、不写库" in report.message

    async with AsyncSession(owner_engine) as session:
        assert (await session.execute(select(SectorBar))).scalars().all() == []
        state = await session.get(SectorSyncState, DATASET_KEY)
        assert state is not None
        assert state.bar_as_of == date(2026, 6, 29)


async def test_sync_bars_requires_cached_catalog(live_source: Any) -> None:
    report = await live_source.sync_bars_to_cache(today=_TODAY, start=date(2026, 6, 1))
    assert report.status == "failed"
    assert "make sync-sector-industry" in report.message

