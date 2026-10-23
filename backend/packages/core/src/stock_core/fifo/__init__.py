"""FIFO 回合纯核心（Period2-01）公共入口：仅 re-export 冻结接口（INTERFACE §2）。"""

from __future__ import annotations

from stock_core.fifo.bridge import BridgeIdentity, BridgeLedger, compute_bridge
from stock_core.fifo.calendar import business_days_between
from stock_core.fifo.dividend import (
    DIVIDEND_TAX_TIERS,
    dividend_net_units,
    dividend_tax_rate_bp,
    dividend_tax_units,
)
from stock_core.fifo.engine import build_rounds
from stock_core.fifo.types import (
    DataError,
    EventKind,
    FifoEvent,
    FifoResult,
    Fill,
    OpenLot,
    Round,
)

__all__ = [
    "DIVIDEND_TAX_TIERS",
    "BridgeIdentity",
    "BridgeLedger",
    "DataError",
    "EventKind",
    "FifoEvent",
    "FifoResult",
    "Fill",
    "OpenLot",
    "Round",
    "build_rounds",
    "business_days_between",
    "compute_bridge",
    "dividend_net_units",
    "dividend_tax_rate_bp",
    "dividend_tax_units",
]
