"""stock_core.clock 仅是 contracts.clock 的重导出面。

覆盖 L1 铁律：core 不得自持实现，只经 contracts 暴露 Clock。
"""

from __future__ import annotations

import stock_core.clock as core_clock
from stock_contracts import clock as contracts_clock


def test_core_clock_reexports_contracts() -> None:
    assert core_clock.Clock is contracts_clock.Clock
    assert core_clock.FrozenClock is contracts_clock.FrozenClock
    assert core_clock.SystemClock is contracts_clock.SystemClock
    assert core_clock.SHANGHAI is contracts_clock.SHANGHAI
    assert core_clock.require_clock is contracts_clock.require_clock
