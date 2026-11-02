"""stock_core.clock 仅是 contracts.clock 的重导出面。

覆盖 L1 铁律：core 不得自持实现，只经 contracts 暴露 Clock。
"""

from __future__ import annotations

from datetime import date

import stock_core.clock as core_clock
from stock_contracts import clock as contracts_clock
from stock_contracts.clock import FrozenClock


def test_core_clock_reexports_contracts() -> None:
    assert core_clock.Clock is contracts_clock.Clock
    assert core_clock.FrozenClock is contracts_clock.FrozenClock
    assert core_clock.SystemClock is contracts_clock.SystemClock
    assert core_clock.SHANGHAI is contracts_clock.SHANGHAI
    assert core_clock.require_clock is contracts_clock.require_clock


def test_business_today_rolls_back_weekend_to_friday() -> None:
    """``business_today()`` 在周末回退到上一交易日（板块 SCD2 effective_from 口径）。"""
    # 2026-10-30 周五 / 2026-10-31 周六 / 2026-11-01 周日 / 2026-11-02 周一
    assert FrozenClock("2026-10-30T12:00:00+08:00").business_today() == date(2026, 10, 30)
    assert FrozenClock("2026-10-31T12:00:00+08:00").business_today() == date(2026, 10, 30)
    assert FrozenClock("2026-11-01T12:00:00+08:00").business_today() == date(2026, 10, 30)
    assert FrozenClock("2026-11-02T12:00:00+08:00").business_today() == date(2026, 11, 2)
