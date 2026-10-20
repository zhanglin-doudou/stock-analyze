"""确定性账单生成器（用 FrozenClock，返回 bytes）。

用途：本地演示 / 集成测试需要真实感但又完全可复现的账单。函数只返回 bytes，
落盘与否由调用方决定（例如写入 `settings.fixtures_dir/market/`）。
生成内容刻意包含：买卖混合、资金余额链、1~2 行完全重复行（演示隔离区）、
1 行未知摘要（演示分类隔离），以及一条可选的断链用于对账演示。
"""

from __future__ import annotations

import io
import random
from datetime import timedelta
from decimal import Decimal

from openpyxl import Workbook  # type: ignore[import-untyped]  # 第三方 openpyxl 无类型桩
from stock_contracts.clock import FrozenClock
from stock_core.values import Money, Price, Qty, money_mul

HEADERS: tuple[str, ...] = (
    "成交日期",
    "证券代码",
    "证券名称",
    "买卖标志",
    "成交数量",
    "成交价格",
    "成交金额",
    "佣金",
    "印花税",
    "其他费",
    "资金余额",
    "摘要",
)

_SECURITIES: tuple[tuple[str, str, int], ...] = (
    ("600519", "贵州茅台", 168_000),  # 价格以“分”为单位（1680.00）
    ("000001", "平安银行", 1_250),
    ("300750", "宁德时代", 21_300),
    ("601318", "中国平安", 4_800),
    ("000858", "五粮液", 14_600),
)

_KNOWN_SUMMARIES: tuple[str, ...] = ("证券买入", "证券卖出", "红利入账", "利息归本", "银行转证券")
_UNKNOWN_SUMMARY = "未知业务XYZ"

_ANCHOR = "2026-06-30T08:00:00+08:00"  # 冻结时点：生成结果与真实时间无关


def _amount(qty: int, price_cents: int) -> Money:
    return money_mul(Qty.from_decimal(Decimal(qty)), Price.from_decimal(Decimal(price_cents) / 100))


def _fees(amount: Money, side: str) -> tuple[Money, Money, Money]:
    commission = Money.from_decimal(amount.to_decimal() * Decimal("0.00025"))
    stamp_tax = (
        Money.from_decimal(amount.to_decimal() * Decimal("0.0005"))
        if side == "sell"
        else Money.zero()
    )
    return commission, stamp_tax, Money.zero()


def _build_rows(seed: int, months: int, count: int, *, break_at: int | None) -> list[list[str]]:
    rng = random.Random(seed)
    clock = FrozenClock(_ANCHOR)
    today = clock.business_today()
    span_days = 30 * months
    start = today - timedelta(days=span_days)

    balance = Money.from_str("100000.00")
    rows: list[list[str]] = []
    for index in range(count):
        offset = int(index * span_days / max(count - 1, 1))
        trade_date = start + timedelta(days=offset)
        if trade_date > today:
            trade_date = today
        code, name, base_cents = _SECURITIES[index % len(_SECURITIES)]
        price_cents = base_cents + rng.randint(-40, 40)
        side = "sell" if rng.random() < 0.4 else "buy"
        side_label = "证券卖出" if side == "sell" else "证券买入"
        quantity = rng.choice((100, 200, 300, 500, 1_000))
        amount = _amount(quantity, price_cents)
        commission, stamp_tax, other_fee = _fees(amount, side)

        fees = commission + stamp_tax + other_fee
        delta = Money(0) - (amount + fees) if side == "buy" else amount - fees
        balance = balance + delta
        shown_balance = balance
        if break_at is not None and index == break_at:
            shown_balance = balance + Money.from_str("100.00")
            balance = shown_balance

        if index == count - 3:
            summary = _UNKNOWN_SUMMARY
        else:
            summary = _KNOWN_SUMMARIES[index % len(_KNOWN_SUMMARIES)]

        rows.append(
            [
                trade_date.strftime("%Y%m%d"),
                code,
                name,
                side_label,
                str(quantity),
                str(Money(price_cents)),
                str(amount),
                str(commission),
                str(stamp_tax),
                str(other_fee),
                str(shown_balance),
                summary,
            ]
        )

    # 完全重复行（1~2 行）：解析阶段应被隔离为 DUPLICATE_IN_FILE
    if rows:
        duplicate_count = 1 + (seed % 2)
        for offset in range(duplicate_count):
            source = rows[min(offset, len(rows) - 1)]
            rows.append(list(source))

    return rows


def _to_xlsx_bytes(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "对账单"
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def generate_huatai_bill(seed: int, *, months: int = 3, count: int = 120) -> bytes:
    """生成账单（xlsx bytes）：买卖混合 + 资金余额链 + 重复行 + 未知摘要。"""
    return _to_xlsx_bytes(_build_rows(seed, months, count, break_at=None))


def generate_broken_bill(seed: int, *, months: int = 1, count: int = 30) -> bytes:
    """生成对账断链账单：在中间某行把资金余额写错 100.00，用于对账演示。"""
    return _to_xlsx_bytes(_build_rows(seed, months, count, break_at=max(1, count // 2)))


__all__ = ["HEADERS", "generate_broken_bill", "generate_huatai_bill"]
