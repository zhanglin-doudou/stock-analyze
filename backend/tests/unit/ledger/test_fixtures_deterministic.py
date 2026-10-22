"""G5：seed/demo 夹具必须字节确定，否则每次 seed 都会产生 git diff。"""

from __future__ import annotations

import hashlib

from stock_ledger.fixtures import (
    generate_broken_bill,
    generate_huatai_bill,
    generate_market_quotes,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_generate_huatai_bill_is_byte_deterministic() -> None:
    first = generate_huatai_bill(seed=42, months=3, count=120)
    second = generate_huatai_bill(seed=42, months=3, count=120)
    assert first == second
    assert len(first) > 0


def test_generate_broken_bill_is_byte_deterministic() -> None:
    assert generate_broken_bill(seed=7, months=1, count=30) == generate_broken_bill(
        seed=7, months=1, count=30
    )


def test_generate_market_quotes_is_byte_deterministic() -> None:
    assert _sha(generate_market_quotes()) == _sha(generate_market_quotes())
