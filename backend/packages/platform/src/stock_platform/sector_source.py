"""板块数据源装配（`SECTOR_MODE` → replay / live+cache）。

设计：docs/architecture/2026-10-31-sector-data.md §4.1。装配放 platform（不依赖 ledger）；
`AppState.sector_data` 与 worker 使用同一入口，避免「读一套、写一套」。
"""

from __future__ import annotations

from stock_contracts.clock import Clock

from stock_platform.config import Settings
from stock_platform.market_live import SessionFactory
from stock_platform.ports import SectorDataSource
from stock_platform.sector_live import (
    AkshareSectorSource,
    CachedSectorSource,
    akshare_available,
)
from stock_platform.sector_replay import ReplaySectorSource, resolve_fixture_path


def create_sector_source(
    settings: Settings, *, session_factory: SessionFactory, clock: Clock
) -> SectorDataSource:
    """按 `SECTOR_MODE` 装配端口实现（live 缺 akshare 时启动期 fail-fast）。"""
    if settings.sector_mode == "replay":
        return ReplaySectorSource(resolve_fixture_path(settings), clock=clock)
    if not akshare_available():
        raise RuntimeError(
            "SECTOR_MODE=live 需要 akshare（pip install -e 'backend[live]'）；"
            "本地演示请保持 SECTOR_MODE=replay"
        )
    inner = AkshareSectorSource(settings, clock=clock, session_factory=session_factory)
    return CachedSectorSource(
        inner, settings=settings, session_factory=session_factory, clock=clock
    )


def create_live_sector_source(
    settings: Settings, *, session_factory: SessionFactory, clock: Clock
) -> AkshareSectorSource:
    """构造可同步的 live 源（`make sync-sectors` 专用）。"""
    if not akshare_available():
        raise RuntimeError(
            "sync-sectors 需要 akshare（pip install -e 'backend[live]'）；replay 模式无需同步"
        )
    return AkshareSectorSource(settings, clock=clock, session_factory=session_factory)


__all__ = ["create_live_sector_source", "create_sector_source"]
