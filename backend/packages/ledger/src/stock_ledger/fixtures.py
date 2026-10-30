"""确定性账单生成器（用 FrozenClock，返回 bytes）。

用途：本地演示 / 集成测试需要真实感但又完全可复现的账单。函数只返回 bytes，
落盘与否由调用方决定（例如写入 `settings.fixtures_dir/market/`）。
生成内容刻意包含：买卖混合、资金余额链、1~2 行完全重复行（演示隔离区）、
1 行未知摘要（演示分类隔离），以及一条可选的断链用于对账演示。
"""

from __future__ import annotations

import io
import json
import random
import re
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from openpyxl import Workbook  # type: ignore[import-untyped]  # 第三方 openpyxl 无类型桩
from stock_contracts.clock import FrozenClock
from stock_core.values import Money, Price, Qty, Ratio, money_mul

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


_XLSX_FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)  # 固定文档/zip 时间戳，保证字节可复现


def _to_xlsx_bytes(rows: list[list[str]]) -> bytes:
    """xlsx bytes：内容 + docProps + zip 条目时间戳全部固定，重复生成字节一致。

    openpyxl 默认把 `created/modified` 与 zip member 的 mtime 写成当前时间，
    会让 `seed-demo`/`demo-p1` 每次重写 fixtures 产生 git diff。这里统一固定，
    使夹具既是内容确定性的也是字节确定性的。
    """
    workbook = Workbook()
    workbook.properties.creator = "stock-analyze-fixtures"
    workbook.properties.created = _XLSX_FIXED_TIME
    workbook.properties.modified = _XLSX_FIXED_TIME
    sheet = workbook.active
    sheet.title = "对账单"
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)

    source = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    normalized = io.BytesIO()
    with zipfile.ZipFile(normalized, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = info.external_attr
            data = source.read(info.filename)
            if info.filename == "docProps/core.xml":
                # openpyxl 在 save() 时会把 modified 覆盖成当前时间，必须最后再钉死。
                data = re.sub(
                    rb"<dcterms:modified[^>]*>.*?</dcterms:modified>",
                    b'<dcterms:modified xmlns:dcterms="http://purl.org/dc/terms/" '
                    b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    b'xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:modified>',
                    data,
                )
            target.writestr(entry, data)
    source.close()
    return normalized.getvalue()


def generate_huatai_bill(seed: int, *, months: int = 3, count: int = 120) -> bytes:
    """生成账单（xlsx bytes）：买卖混合 + 资金余额链 + 重复行 + 未知摘要。"""
    return _to_xlsx_bytes(_build_rows(seed, months, count, break_at=None))


def generate_broken_bill(seed: int, *, months: int = 1, count: int = 30) -> bytes:
    """生成对账断链账单：在中间某行把资金余额写错 100.00，用于对账演示。"""
    return _to_xlsx_bytes(_build_rows(seed, months, count, break_at=max(1, count // 2)))


# ── 确定性行情夹具（G4：replay 适配器的唯一数据源）─────────────────
# 纯确定性：日期固定、收盘由固定「首日收盘 + 月度漂移」推导、排序固定、
# 不写任何运行时刻戳/随机数；同一解释器重复调用 bytes 完全一致。
# 价格/收益一律定点字符串（价格 10^-6 元、比率 10^-8），生成与读取全链路禁 float。

_MARKET_DATES: tuple[str, ...] = (
    "2026-01-30",
    "2026-02-27",
    "2026-03-31",
    "2026-04-30",
    "2026-05-29",
    "2026-06-30",
    "2026-07-31",
    "2026-08-31",
    "2026-09-30",
)
MARKET_AS_OF: str = _MARKET_DATES[-1]
MARKET_BENCHMARK: str = "hs300_tr"
MARKET_PROVENANCE: str = "deterministic-fixtures/v1"

# (symbol, 首日收盘, 月度漂移)：固定参数表，无随机、无时间依赖。
_MARKET_SERIES_SPEC: tuple[tuple[str, str, str], ...] = (
    (MARKET_BENCHMARK, "3150.20", "0.0042"),
    ("600519", "1680.00", "0.0031"),
    ("000001", "12.50", "-0.0018"),
    ("300750", "213.00", "0.0056"),
)


def _market_rows(first_close: str, monthly_drift: str) -> list[dict[str, str]]:
    """按固定日期序列推导一条日线序列（每周期的收盘价与环比收益）。"""
    factor = Decimal("1") + Decimal(monthly_drift)
    closes: list[Price] = [Price.from_str(first_close)]
    for _ in range(1, len(_MARKET_DATES)):
        grown = closes[-1].to_decimal() * factor
        closes.append(Price.from_decimal(grown.quantize(Decimal("0.01"))))

    rows: list[dict[str, str]] = []
    for index, (day, close) in enumerate(zip(_MARKET_DATES, closes, strict=True)):
        daily = (
            Ratio.zero()
            if index == 0
            else Ratio.from_decimal(
                close.to_decimal() / closes[index - 1].to_decimal() - Decimal("1")
            )
        )
        rows.append({"date": day, "close": str(close), "return": str(daily)})
    return rows


def generate_market_quotes() -> bytes:
    """生成确定性行情回放夹具（JSON bytes，可直接落盘 `fixtures/market/quotes.json`）。"""
    series = {
        symbol: _market_rows(first_close, monthly_drift)
        for symbol, first_close, monthly_drift in _MARKET_SERIES_SPEC
    }
    payload = {
        "schema_version": 1,
        "mode": "replay",
        "benchmark": MARKET_BENCHMARK,
        "as_of": MARKET_AS_OF,
        "provenance": MARKET_PROVENANCE,
        "note": "确定性行情回放夹具（价格/收益一律定点字符串；固定日期与排序，无运行时刻戳）",
        "series": series,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


__all__ = [
    "HEADERS",
    "MARKET_AS_OF",
    "MARKET_BENCHMARK",
    "generate_broken_bill",
    "generate_huatai_bill",
    "generate_market_quotes",
]
