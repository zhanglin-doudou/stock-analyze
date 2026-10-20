"""端口定义（Architecture §8）：platform 侧横切端口。

core/domain 只依赖 Protocol，不依赖实现；infrastructure 只被 api/worker 装配。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
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
