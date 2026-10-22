"""两套成本口径桥接恒等式（Period2-01，INTERFACE §2.5；PRD FR-R2.4）。

    residual = (initial_cash + initial_open_cost + cashflows + trading_pnl
                + dividends + interest - fees + other) - ending_value
    ending_value = ending_cash + open_cost

`inputs_hash` 为规范化 JSON（键排序、紧凑分隔符）的 sha256，纯确定性：同一输入必得同值。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

_HASH_SCHEMA_VERSION: Final[str] = "period2-bridge-v1"


@dataclass(frozen=True, slots=True)
class BridgeLedger:
    """桥接输入：期初/期末现金锚点 + 区间内分类现金流。全部标度整数（分）。"""

    initial_cash_units: int
    initial_open_cost_units: int = 0
    ending_cash_units: int = 0
    cashflows_units: int = 0
    trading_pnl_units: int = 0
    dividends_units: int = 0
    interest_units: int = 0
    fees_units: int = 0
    other_units: int = 0
    ending_cash_source: str = "statement"
    open_cost_units: int = 0


@dataclass(frozen=True, slots=True)
class BridgeIdentity:
    """桥接结果（API-027 的 identity 段）。"""

    initial_cash_units: int
    initial_open_cost_units: int
    cashflows_units: int
    trading_pnl_units: int
    dividends_units: int
    interest_units: int
    fees_units: int
    other_units: int
    open_cost_units: int
    ending_value_units: int
    residual_units: int
    balanced: bool
    tolerance_units: int
    inputs_hash: str


def compute_bridge(ledger: BridgeLedger, *, tolerance_units: int = 1) -> BridgeIdentity:
    """按 §2.5 公式计算桥接恒等式。容差默认 1 分。"""
    ending_value_units = ledger.ending_cash_units + ledger.open_cost_units
    residual_units = (
        ledger.initial_cash_units
        + ledger.initial_open_cost_units
        + ledger.cashflows_units
        + ledger.trading_pnl_units
        + ledger.dividends_units
        + ledger.interest_units
        - ledger.fees_units
        + ledger.other_units
        - ending_value_units
    )
    return BridgeIdentity(
        initial_cash_units=ledger.initial_cash_units,
        initial_open_cost_units=ledger.initial_open_cost_units,
        cashflows_units=ledger.cashflows_units,
        trading_pnl_units=ledger.trading_pnl_units,
        dividends_units=ledger.dividends_units,
        interest_units=ledger.interest_units,
        fees_units=ledger.fees_units,
        other_units=ledger.other_units,
        open_cost_units=ledger.open_cost_units,
        ending_value_units=ending_value_units,
        residual_units=residual_units,
        balanced=abs(residual_units) <= tolerance_units,
        tolerance_units=tolerance_units,
        inputs_hash=_inputs_hash(ledger, tolerance_units),
    )


def _inputs_hash(ledger: BridgeLedger, tolerance_units: int) -> str:
    """确定性 inputs_hash：规范化 JSON 组件的 sha256。"""
    components: dict[str, int | str] = {
        "schema_version": _HASH_SCHEMA_VERSION,
        "initial_cash_units": ledger.initial_cash_units,
        "initial_open_cost_units": ledger.initial_open_cost_units,
        "ending_cash_units": ledger.ending_cash_units,
        "ending_cash_source": ledger.ending_cash_source,
        "cashflows_units": ledger.cashflows_units,
        "trading_pnl_units": ledger.trading_pnl_units,
        "dividends_units": ledger.dividends_units,
        "interest_units": ledger.interest_units,
        "fees_units": ledger.fees_units,
        "other_units": ledger.other_units,
        "open_cost_units": ledger.open_cost_units,
        "tolerance_units": tolerance_units,
    }
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
