"""Clock 端口（跨上下文）：core/domain 零 wall-clock，时间一律注入。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

SHANGHAI = timezone(timedelta(hours=8))


def _rollback_to_business_day(d: date) -> date:
    """把周末回退到最近一个周五（周一至周五原样返回）。

    板块成分 SCD2 的 ``effective_from`` 取「同步业务日」：上游只给当前快照、
    周末/节假日没有新成分变更，因此同步日落在周末时生效日应回退到上一交易日，
    否则资产快照（周五）查询 ``as_of`` 会落在 ``effective_from`` 之前而 fail-closed。
    节假日口径由上层数据源负责，core 保持零 I/O（仅按周一至周五计工作日）。
    """
    weekday = d.weekday()
    if weekday == 5:  # Saturday
        return d - timedelta(days=1)
    if weekday == 6:  # Sunday
        return d - timedelta(days=2)
    return d


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
        return _rollback_to_business_day(self.today())


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
        return _rollback_to_business_day(self.today())

    def advance(self, **kwargs: float) -> None:
        self._now = self._now + timedelta(**kwargs)


def require_clock(clock: Clock | None) -> Clock:
    """显式取时钟：禁止在 core/domain 里读系统时间。"""
    if clock is None:
        raise ValueError("缺少 Clock：core/domain 不得读取系统时间")
    return clock
