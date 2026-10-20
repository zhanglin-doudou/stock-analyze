"""core 的 Clock 面（实现在 contracts，避免 core→platform 反向依赖）。"""

from stock_contracts.clock import SHANGHAI, Clock, FrozenClock, SystemClock, require_clock

__all__ = ["SHANGHAI", "Clock", "FrozenClock", "SystemClock", "require_clock"]
