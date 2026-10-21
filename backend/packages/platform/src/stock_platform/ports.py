"""端口定义（Architecture §8）：platform 侧横切端口。

core/domain 只依赖 Protocol，不依赖实现；infrastructure 只被 api/worker 装配。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from stock_contracts.clock import Clock  # noqa: F401  (跨上下文端口，统一从 contracts 取)

JobHandler = Callable[["JobContext"], Awaitable[dict[str, Any] | None]]


@dataclass(slots=True)
class JobContext:
    """任务执行上下文（worker 注入）。"""

    job_id: str
    kind: str
    user_id: str
    params: dict[str, Any]
    attempt: int
    report: Callable[[int, str, str | None], Awaitable[None]]
    should_cancel: Callable[[], Awaitable[bool]]


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(
        self,
        *,
        kind: str,
        user_id: str,
        scope_key: str,
        params: dict[str, Any],
        priority: int = 100,
    ) -> tuple[str, bool]: ...

    async def claim(self) -> JobContext | None: ...


@runtime_checkable
class EventBus(Protocol):
    async def publish(
        self,
        *,
        event_type: str,
        aggregate: str,
        aggregate_id: str | None,
        payload: dict[str, Any],
    ) -> None: ...


@runtime_checkable
class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]: ...


@runtime_checkable
class ObjectStore(Protocol):
    async def put_bytes(self, key: str, data: bytes) -> None: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def list_prefix(self, prefix: str) -> Sequence[str]: ...


@runtime_checkable
class BackupTarget(Protocol):
    """备份落点（本地卷；端口预留对象存储）。"""

    async def write(self, name: str, data: bytes) -> str: ...

    async def read(self, name: str) -> bytes: ...

    async def verify(self, name: str, checksum: str) -> bool: ...


# ── 行情端口（G4 / P2-04 前置）───────────────────────────────────
# 价格/收益一律用标度整数：价格 10^-6 元（SCALE_PRICE）、比率 10^-8（SCALE_RATIO）。
# 端口层禁止 float；只有序列化到 API/夹具边界时才转定点字符串（stock_core.values.Price/Ratio）。


@dataclass(frozen=True, slots=True)
class MarketCapabilities:
    """数据源能力声明（NFR-9）：调用方据此决定取数与降级，不得猜。"""

    mode: str  # "replay" | "live"
    symbols: tuple[str, ...] = ()
    supports_daily_bars: bool = True
    supports_latest_quote: bool = True
    supports_adjusted: bool = False
    max_lookback_days: int | None = None

    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self.symbols


@dataclass(frozen=True, slots=True)
class Bar:
    """单标的一天日线。`close_units` 标度 10^-6 元；`return_units` 标度 10^-8。"""

    symbol: str
    trade_date: date
    close_units: int
    return_units: int


@dataclass(frozen=True, slots=True)
class Quote:
    """`as_of` 时点的最新收盘快照（`close_units` 标度同 `Bar`）。"""

    symbol: str
    as_of: date
    close_units: int


@runtime_checkable
class MarketDataSource(Protocol):
    """行情端口：replay / live 适配器实现同一契约，P2-04 起消费。"""

    @property
    def capabilities(self) -> MarketCapabilities: ...

    @property
    def as_of(self) -> date:
        """数据覆盖到的最后交易日；调用方据此判 stale，不得自行读系统时间。"""
        ...

    @property
    def provenance(self) -> str:
        """来源留痕（replay: 文件+sha256；live: source/version/fetched_at）。"""
        ...

    async def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> Sequence[Bar]: ...

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]: ...
