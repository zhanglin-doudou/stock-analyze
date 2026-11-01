"""板块 replay 数据源（读 `fixtures/market/sectors.json`，懒加载、fail-closed）。

口径（docs/architecture/2026-10-31-sector-data.md §4.3）：

* 夹具只读：``as_of`` 取文件字段，**不读系统时间**；
* ``as_of < first_sync_on`` 的查询返回空 → 应用层给出 ``SECTOR_HISTORY_NOT_COVERED``；
* ``provenance`` 带文件路径 + sha256，便于对账；
* 不实现 ``sync_to_cache``（replay 没有外呼同步语义）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from stock_contracts.clock import Clock
from stock_core.sector import SectorNode, resolve_level
from stock_core.values import Price, Ratio

from stock_platform.config import Settings, get_settings
from stock_platform.market_models import TAXONOMY_CONCEPT, TAXONOMY_INDUSTRY
from stock_platform.obs import get_logger
from stock_platform.ports import (
    BenchmarkMember,
    SectorBar,
    SectorCapabilities,
    SectorInfo,
    SectorMembership,
)
from stock_platform.sector_fixture import (
    AS_OF,
    BENCHMARK,
    CLASSIFICATION,
    FIRST_SYNC_ON,
    generate_sector_fixture,
)

log = get_logger("stock_platform.sector_replay")

DEFAULT_RELATIVE_PATH = Path("market") / "sectors.json"
TAXONOMIES = (TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT)


@dataclass(frozen=True, slots=True)
class _Fixture:
    as_of: date
    first_sync_on: date
    classification: dict[str, str]
    sectors: tuple[SectorInfo, ...]
    memberships: tuple[SectorMembership, ...]
    benchmark_members: tuple[BenchmarkMember, ...]
    series: dict[str, tuple[SectorBar, ...]]


def _parse_date(raw: Any, *, field: str) -> date:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"板块夹具 {field} 不是合法日期: {raw!r}") from exc


def _load_fixture(path: Path) -> _Fixture:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError(f"板块夹具 schema_version 不支持: {payload.get('schema_version')!r}")
    as_of = _parse_date(payload.get("as_of"), field="as_of")
    first_sync_on = _parse_date(payload.get("first_sync_on"), field="first_sync_on")
    classification = {str(k): str(v) for k, v in dict(payload.get("classification", {})).items()}

    sectors: list[SectorInfo] = []
    for row in payload.get("sectors", []):
        taxonomy = str(row["taxonomy"])
        sectors.append(
            SectorInfo(
                taxonomy=taxonomy,
                sector_code=str(row["sector_code"]),
                name=str(row["name"]),
                level=int(row["level"]),
                parent_code=None if row.get("parent_code") is None else str(row["parent_code"]),
                classification_version=classification.get(taxonomy, ""),
            )
        )

    memberships: list[SectorMembership] = []
    for row in payload.get("memberships", []):
        # 夹具只有「当前有效区间」；非当前区间由应用层按 effective_from 过滤。
        if _parse_date(row.get("effective_from"), field="memberships.effective_from") > as_of:
            continue
        memberships.append(
            SectorMembership(
                taxonomy=str(row["taxonomy"]),
                sector_code=str(row["sector_code"]),
                symbol=str(row["symbol"]),
            )
        )

    benchmark_members = tuple(
        BenchmarkMember(
            benchmark_id=str(row["benchmark_id"]),
            symbol=str(row["symbol"]),
            weight_units=Ratio.from_str(str(row["weight"])).units,
            weight_as_of=_parse_date(row.get("weight_as_of"), field="weight_as_of"),
        )
        for row in payload.get("benchmark_members", [])
    )

    series: dict[str, tuple[SectorBar, ...]] = {}
    for code, rows in dict(payload.get("series", {})).items():
        taxonomy = _taxonomy_of({item.sector_code: item.taxonomy for item in sectors}, str(code))
        series[str(code)] = tuple(
            SectorBar(
                taxonomy=taxonomy,
                sector_code=str(code),
                trade_date=_parse_date(row.get("date"), field="series.date"),
                close_units=Price.from_str(str(row["close"])).units,
                return_units=Ratio.from_str(str(row.get("return", "0"))).units,
            )
            for row in rows
        )

    return _Fixture(
        as_of=as_of,
        first_sync_on=first_sync_on,
        classification=classification,
        sectors=tuple(sectors),
        memberships=tuple(memberships),
        benchmark_members=benchmark_members,
        series=series,
    )


def _taxonomy_of(catalog: dict[str, str], sector_code: str) -> str:
    taxonomy = catalog.get(sector_code)
    if taxonomy is None:
        raise ValueError(f"板块夹具 series 引用未知板块: {sector_code}")
    return taxonomy


class ReplaySectorSource:
    """确定性夹具数据源（``SECTOR_MODE=replay``）。"""

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock,
        capabilities_taxonomies: tuple[str, ...] = TAXONOMIES,
    ) -> None:
        self._path = path
        self._clock = clock
        self._capabilities_taxonomies = capabilities_taxonomies
        self._fixture: _Fixture | None = None
        self._digest: str | None = None

    # ── 懒加载 ──────────────────────────────────────────
    def _ensure(self) -> _Fixture:
        if self._fixture is None:
            if not self._path.is_file():
                raise FileNotFoundError(
                    f"板块 replay 夹具不存在: {self._path}（先执行 `make seed-demo`）"
                )
            self._fixture = _load_fixture(self._path)
            self._digest = hashlib.sha256(self._path.read_bytes()).hexdigest()
            log.info(
                "sector.fixture.loaded",
                path=str(self._path),
                as_of=self._fixture.as_of.isoformat(),
                sha256=self._digest,
            )
        return self._fixture

    # ── 能力声明 ────────────────────────────────────────
    @property
    def capabilities(self) -> SectorCapabilities:
        fixture = self._ensure()
        return SectorCapabilities(
            mode="replay",
            taxonomies=self._capabilities_taxonomies,
            benchmark_ids=tuple(
                sorted({member.benchmark_id for member in fixture.benchmark_members})
            ),
            history_start=fixture.first_sync_on,
        )

    @property
    def as_of(self) -> date:
        return self._ensure().as_of

    @property
    def provenance(self) -> str:
        fixture = self._ensure()
        return f"replay:{self._path.name}@{fixture.as_of.isoformat()}#sha256:{self._digest}"

    # ── 读取 ────────────────────────────────────────────
    async def get_catalog(self, taxonomy: str | None = None) -> tuple[SectorInfo, ...]:
        fixture = self._ensure()
        if taxonomy is None:
            return fixture.sectors
        return tuple(item for item in fixture.sectors if item.taxonomy == taxonomy)

    async def get_memberships(
        self, taxonomy: str, *, as_of: date | None = None, level: int | None = None
    ) -> tuple[SectorMembership, ...]:
        fixture = self._ensure()
        target = as_of or fixture.as_of
        # fail-closed：夹具只覆盖 first_sync_on 之后的历史。
        if target < fixture.first_sync_on:
            return ()
        members = tuple(item for item in fixture.memberships if item.taxonomy == taxonomy)
        if level is None:
            return members
        # 行业成分只存最深层（多为 L3），L1/L2 由 parent_code 树上 rollup。
        if taxonomy == TAXONOMY_INDUSTRY:
            catalog = {
                item.sector_code: SectorNode(item.sector_code, item.level, item.parent_code)
                for item in fixture.sectors
                if item.taxonomy == TAXONOMY_INDUSTRY
            }
            resolved = resolve_level(
                {item.symbol: item.sector_code for item in members}, catalog, level
            )
            return tuple(
                SectorMembership(taxonomy=taxonomy, sector_code=code, symbol=symbol)
                for symbol, code in resolved.items()
            )
        allowed = {
            item.sector_code
            for item in fixture.sectors
            if item.taxonomy == taxonomy and item.level == level
        }
        return tuple(item for item in members if item.sector_code in allowed)

    async def get_board_bars(
        self,
        sector_keys: Sequence[tuple[str, str]],
        start: date,
        end: date,
    ) -> Sequence[SectorBar]:
        fixture = self._ensure()
        out: list[SectorBar] = []
        for taxonomy, sector_code in sector_keys:
            for bar in fixture.series.get(sector_code, ()):
                if bar.taxonomy == taxonomy and start <= bar.trade_date <= end:
                    out.append(bar)
        return tuple(out)

    async def get_benchmark_members(
        self, benchmark_id: str, *, as_of: date | None = None
    ) -> tuple[BenchmarkMember, ...]:
        fixture = self._ensure()
        target = as_of or fixture.as_of
        if target < fixture.first_sync_on:
            return ()
        return tuple(
            member
            for member in fixture.benchmark_members
            if member.benchmark_id == benchmark_id and member.weight_as_of <= target
        )


def resolve_fixture_path(settings: Settings) -> Path:
    """显式 `SECTOR_FIXTURES_FILE` 优先，否则 `fixtures/market/sectors.json`。"""
    if settings.sector_fixtures_file is not None:
        return settings.sector_fixtures_file
    return settings.fixtures_dir / DEFAULT_RELATIVE_PATH


def build_replay_sector_source(
    settings: Settings | None = None, *, clock: Clock
) -> ReplaySectorSource:
    return ReplaySectorSource(resolve_fixture_path(settings or get_settings()), clock=clock)


def write_replay_fixture(settings: Settings | None = None) -> Path:
    """把确定性夹具写入 `fixtures/market/sectors.json`（seed-demo 调用）。"""
    path = resolve_fixture_path(settings or get_settings())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = generate_sector_fixture()
    if path.is_file() and path.read_bytes() == payload:
        return path
    path.write_bytes(payload)
    return path


__all__ = [
    "AS_OF",
    "BENCHMARK",
    "CLASSIFICATION",
    "DEFAULT_RELATIVE_PATH",
    "FIRST_SYNC_ON",
    "ReplaySectorSource",
    "build_replay_sector_source",
    "resolve_fixture_path",
    "write_replay_fixture",
]
