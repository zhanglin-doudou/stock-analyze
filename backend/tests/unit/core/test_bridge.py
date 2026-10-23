"""桥接恒等式 `compute_bridge`（INTERFACE §2.5）单测：公式、容差、确定性 hash。"""

from __future__ import annotations

import re
from dataclasses import replace

from stock_core.fifo.bridge import BridgeLedger, compute_bridge

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_balanced_identity_residual_zero() -> None:
    ledger = BridgeLedger(
        initial_cash_units=100_000,
        initial_open_cost_units=50_000,
        ending_cash_units=190_000,
        cashflows_units=20_000,
        trading_pnl_units=15_000,
        dividends_units=3_000,
        interest_units=100,
        fees_units=200,
        other_units=-1_900,
        open_cost_units=0,
    )
    identity = compute_bridge(ledger)
    # 左边 = 100000 + 50000 + 20000 + 15000 + 3000 + 100 - 200 - 1900 = 186000
    # ending_value = 190000 + 0 = 190000 → residual = 186000 - 190000 = -4000
    assert identity.ending_value_units == 190_000
    assert identity.residual_units == -4_000
    assert identity.balanced is False


def test_balanced_identity_exact() -> None:
    ledger = BridgeLedger(
        initial_cash_units=100_000,
        initial_open_cost_units=50_000,
        ending_cash_units=186_000,
        cashflows_units=20_000,
        trading_pnl_units=15_000,
        dividends_units=3_000,
        interest_units=100,
        fees_units=200,
        other_units=-1_900,
        open_cost_units=0,
    )
    identity = compute_bridge(ledger)
    assert identity.residual_units == 0
    assert identity.balanced is True
    assert identity.ending_value_units == 186_000
    assert identity.tolerance_units == 1
    assert identity.initial_cash_units == 100_000
    assert identity.initial_open_cost_units == 50_000
    assert identity.cashflows_units == 20_000
    assert identity.trading_pnl_units == 15_000
    assert identity.dividends_units == 3_000
    assert identity.interest_units == 100
    assert identity.fees_units == 200
    assert identity.other_units == -1_900


def test_ending_value_includes_open_cost() -> None:
    ledger = BridgeLedger(
        initial_cash_units=0,
        ending_cash_units=0,
        open_cost_units=123_456,
    )
    identity = compute_bridge(ledger)
    assert identity.ending_value_units == 123_456
    assert identity.residual_units == -123_456
    assert identity.balanced is False


def test_tolerance_boundary_inclusive() -> None:
    exact = BridgeLedger(initial_cash_units=0, ending_cash_units=3)
    assert compute_bridge(exact, tolerance_units=3).balanced is True  # |residual| == 3
    assert compute_bridge(exact, tolerance_units=2).balanced is False  # |residual| == 3 > 2
    assert compute_bridge(exact, tolerance_units=0).balanced is False


def test_negative_residual_uses_absolute_value() -> None:
    ledger = BridgeLedger(initial_cash_units=0, ending_cash_units=5)
    identity = compute_bridge(ledger, tolerance_units=5)
    assert identity.residual_units == -5
    assert identity.balanced is True


def test_default_tolerance_is_one_cent() -> None:
    ledger = BridgeLedger(initial_cash_units=0, ending_cash_units=1)
    assert compute_bridge(ledger).balanced is True
    assert compute_bridge(replace(ledger, ending_cash_units=2)).balanced is False


def test_inputs_hash_is_deterministic_and_sensitive() -> None:
    ledger = BridgeLedger(
        initial_cash_units=100,
        initial_open_cost_units=200,
        ending_cash_units=300,
        cashflows_units=400,
        trading_pnl_units=500,
        dividends_units=600,
        interest_units=700,
        fees_units=800,
        other_units=900,
        ending_cash_source="statement",
        open_cost_units=1000,
    )
    first = compute_bridge(ledger)
    second = compute_bridge(ledger)
    assert first.inputs_hash == second.inputs_hash
    assert _HEX64.match(first.inputs_hash) is not None

    variants = [
        replace(ledger, initial_cash_units=101),
        replace(ledger, initial_open_cost_units=201),
        replace(ledger, ending_cash_units=301),
        replace(ledger, cashflows_units=401),
        replace(ledger, trading_pnl_units=501),
        replace(ledger, dividends_units=601),
        replace(ledger, interest_units=701),
        replace(ledger, fees_units=801),
        replace(ledger, other_units=901),
        replace(ledger, open_cost_units=1001),
        replace(ledger, ending_cash_source="derived"),
    ]
    hashes = {first.inputs_hash}
    for variant in variants:
        variant_hash = compute_bridge(variant).inputs_hash
        assert variant_hash not in hashes
        hashes.add(variant_hash)

    # 容差是输入参数，同样进入 hash（影响 balanced 判定）
    assert compute_bridge(ledger, tolerance_units=0).inputs_hash != first.inputs_hash


def test_derived_ending_cash_source_is_preserved_in_hash_only() -> None:
    ledger = BridgeLedger(initial_cash_units=10, ending_cash_units=10, ending_cash_source="derived")
    identity = compute_bridge(ledger)
    assert identity.residual_units == 0
    assert identity.balanced is True
