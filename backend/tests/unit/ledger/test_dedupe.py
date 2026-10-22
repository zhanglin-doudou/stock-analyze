"""去重键单测：确定性、序号区分、任一要素变化即变化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hypothesis import given
from hypothesis import strategies as st
from stock_ledger.parsers.dedupe import dedupe_key


@dataclass(frozen=True, slots=True)
class Row:
    trade_date: date
    security_code: str
    side: str
    quantity_units: int
    price_units: int
    amount_units: int
    cash_balance_units: int | None
    account_masked: str | None
    occurrence_idx: int


def _row(**overrides: object) -> Row:
    base: dict[str, object] = {
        "trade_date": date(2026, 3, 4),
        "security_code": "600519",
        "side": "buy",
        "quantity_units": 100_0000,
        "price_units": 168_0000000,
        "amount_units": 168_000_00,
        "cash_balance_units": 100_000_00,
        "account_masked": "****1234",
        "occurrence_idx": 0,
    }
    base.update(overrides)
    return Row(**base)  # type: ignore[arg-type]


def test_same_fields_same_hash() -> None:
    assert dedupe_key(_row()) == dedupe_key(_row())


def test_occurrence_idx_changes_hash() -> None:
    assert dedupe_key(_row()) != dedupe_key(_row(occurrence_idx=1))


def test_any_business_field_changes_hash() -> None:
    base = dedupe_key(_row())
    assert base != dedupe_key(_row(security_code="000001"))
    assert base != dedupe_key(_row(side="sell"))
    assert base != dedupe_key(_row(quantity_units=200_0000))
    assert base != dedupe_key(_row(cash_balance_units=None))
    assert base != dedupe_key(_row(account_masked=None))


@given(
    idx_a=st.integers(min_value=0, max_value=5),
    idx_b=st.integers(min_value=0, max_value=5),
)
def test_property_occurrence_idx_equivalence(idx_a: int, idx_b: int) -> None:
    key_a = dedupe_key(_row(occurrence_idx=idx_a))
    key_b = dedupe_key(_row(occurrence_idx=idx_b))
    assert (key_a == key_b) == (idx_a == idx_b)


@given(
    qty=st.integers(min_value=0, max_value=10**9),
    price=st.integers(min_value=0, max_value=10**10),
)
def test_property_quantities_distinguish(qty: int, price: int) -> None:
    key_a = dedupe_key(_row(quantity_units=qty, price_units=price, occurrence_idx=0))
    key_b = dedupe_key(_row(quantity_units=qty + 1, price_units=price, occurrence_idx=0))
    assert key_a != key_b
