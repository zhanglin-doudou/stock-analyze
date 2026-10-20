"""去重键计算（纯函数）。

去重键是“同一笔业务只进一次”的唯一依据。它把业务要素 + `occurrence_idx` 一起哈希：
- 同要素同序号 → 同键（重复导入被唯一约束拦下）；
- 同要素不同序号 → 不同键（同一天同价同量的合法多笔得以保留）。
因此键必须确定性序列化，且不得掺入任何 float。
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Protocol, runtime_checkable


@runtime_checkable
class HasDedupeFields(Protocol):
    """能参与去重键计算的行（解析器 ParsedRow 满足该结构）。"""

    trade_date: date
    security_code: str
    side: str
    quantity_units: int
    price_units: int
    amount_units: int
    cash_balance_units: int | None
    account_masked: str | None
    occurrence_idx: int


def _blank(value: object) -> str:
    return "" if value is None else str(value)


def dedupe_key(row: HasDedupeFields) -> str:
    """业务要素 → sha256 去重键（64 hex 字符）。"""
    payload = "|".join(
        (
            row.trade_date.isoformat(),
            row.security_code,
            row.side,
            str(row.quantity_units),
            str(row.price_units),
            str(row.amount_units),
            _blank(row.cash_balance_units),
            _blank(row.account_masked),
            str(row.occurrence_idx),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class CoreSignatureFields(Protocol):
    """不含序号的业务要素（用于统计同要素出现次数以分配 occurrence_idx）。"""

    trade_date: date
    security_code: str
    side: str
    quantity_units: int
    price_units: int
    amount_units: int
    cash_balance_units: int | None
    account_masked: str | None


def core_signature(row: CoreSignatureFields) -> str:
    """去掉 occurrence_idx 的“业务指纹”，同指纹的行按出现顺序编号。"""
    return "|".join(
        (
            row.trade_date.isoformat(),
            row.security_code,
            row.side,
            str(row.quantity_units),
            str(row.price_units),
            str(row.amount_units),
            _blank(row.cash_balance_units),
            _blank(row.account_masked),
        )
    )


__all__ = ["CoreSignatureFields", "HasDedupeFields", "core_signature", "dedupe_key"]
