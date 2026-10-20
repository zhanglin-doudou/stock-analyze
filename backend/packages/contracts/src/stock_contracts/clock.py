"""Clock 端口（跨上下文）：core/domain 零 wall-clock，时间一律注入。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

SHANGHAI = timezone(timedelta(hours=8))


@runtime_checkable
class Clock(Protocol):
    """时间来源端口。生产用 SystemClock，测试用 FrozenClock。"""

    def now(self) -> datetime: ...

    def today(self) -> date: ...

    def business_today(self) -> date: ...


class SystemClock:
    """生产时钟（UTC 存储 / 东八区业务日）。"""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def today(self) -> date:
        return self.now().astimezone(SHANGHAI).date()

    def business_today(self) -> date:
        return self.today()


class FrozenClock:
    """确定性时钟：属性测试与 golden 夹具必用。"""

    __slots__ = ("_now",)

    def __init__(self, now: datetime | str) -> None:
        if isinstance(now, str):
            parsed = datetime.fromisoformat(now)
            now = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        self._now = now

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.astimezone(SHANGHAI).date()

    def business_today(self) -> date:
        return self.today()

    def advance(self, **kwargs: float) -> None:
        self._now = self._now + timedelta(**kwargs)


def require_clock(clock: Clock | None) -> Clock:
    """显式取时钟：禁止在 core/domain 里读系统时间。"""
    if clock is None:
        raise ValueError("缺少 Clock：core/domain 不得读取系统时间")
    return clock
