"""货币基金（现金类产品）识别与余额推演（纯计算，无 I/O、无 wall-clock）。

口径（2026-11-16 人工确认）：

* 货基是**现金等价物**：不进 FIFO、不产生回合、不进持仓/持仓成本/市值，也不请求行情；
  资产表/净值把它并入「现金余额」，总资产恒等式仍为「现金 + 持仓市值」。
* 识别按**整只证券代码**判定，避免单行证据误伤普通基金：
  1. 强证据：证券名称含「货币」或「现金」；
  2. 兜底证据（同时满足）：非行情品种（`needs_quote` 为 False）、名称不含
     ETF/LOF/REIT、至少一笔数量非 0 的申赎、且该标的所有数量非 0 的行单位价格
     恰为 1.000000、三费全 0。
* 申赎方向看 `category`（导入行可能 `side=unknown`）：`sell` 减少余额，其余增加
  （红利发放/红利再投以份额形式增加余额）。
* 余额 = Σ 份额增减 × 单位价格；历史首笔就是赎回时按前缀最小负值反推期初余额
  （等价于「期初已持有至少这么多」），并对任何负余额按 0 兜底（账单缺流水场景）。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from stock_ledger.models import Trade
from stock_platform.symbols import needs_quote

# 货基单位净值：1.000000（价格标度 6）。
UNIT_PRICE_UNITS = 1_000_000
# 数量（scale=4）× 价格（scale=6）→ 金额（scale=2）。
QTY_PRICE_SCALE = 10**8
# 强证据：名称关键词（人工确认含「现金」，如「现金宝」「平安证券现金宝」）。
NAME_KEYWORDS: tuple[str, ...] = ("货币", "现金")
# 名称反证：交易型基金即使名称含「货币」也不按 1 元现金产品处理。
NON_CASH_NAME_KEYWORDS: tuple[str, ...] = ("ETF", "LOF", "REIT")


def _name(value: str | None) -> str:
    return (value or "").strip()


def _is_money_fund_name(name: str) -> bool:
    text = _name(name)
    if not text or any(keyword in text.upper() for keyword in NON_CASH_NAME_KEYWORDS):
        return False
    return any(keyword in text for keyword in NAME_KEYWORDS)


def _looks_like_cash_product(code: str, rows: list[Trade]) -> bool:
    """兜底证据：非行情品种 + 全部有效申赎都是 1 元零费。"""
    if needs_quote(code):
        return False
    quantity_rows = [row for row in rows if row.quantity_units != 0]
    if not quantity_rows:
        return False
    return all(
        row.price_units == UNIT_PRICE_UNITS
        and not row.commission_units
        and not row.stamp_tax_units
        and not row.other_fee_units
        for row in quantity_rows
    )


def money_fund_codes(rows: Iterable[Trade]) -> frozenset[str]:
    """整只证券代码维度的货基集合（强证据命中即命中，兜底证据需全量一致）。"""
    grouped: dict[str, list[Trade]] = {}
    for row in rows:
        code = _name(row.security_code)
        if code:
            grouped.setdefault(code, []).append(row)
    matched: set[str] = set()
    for code, items in grouped.items():
        if any(_is_money_fund_name(row.security_name) for row in items):
            matched.add(code)  # 强证据：名称含「货币」/「现金」
        elif _looks_like_cash_product(code, items):
            matched.add(code)  # 兜底证据：非行情品种 + 全部申赎 1 元零费
    return frozenset(matched)


def money_fund_value_units(row: Trade) -> int:
    """单笔货基流水对余额的金额影响（scale=2；申购买入为正、赎回卖出为负）。"""
    quantity = row.quantity_units
    if quantity == 0:
        return 0
    price = row.price_units if row.price_units > 0 else UNIT_PRICE_UNITS
    value = abs(quantity) * price // QTY_PRICE_SCALE
    return -value if (row.category or "").strip().lower() == "sell" else value


def money_fund_balances(rows: Iterable[Trade], *, before: date | None = None) -> dict[str, int]:
    """各货基在 `before`（不含）时点的余额（scale=2），无货基返回 `{}`。

    `before=None` 表示推演到全部流水之后。期初反推：若历史前缀出现负余额（首笔即
    赎回、账单缺申购流水），把该标的期初余额抬高到「前缀最小负值的绝对值」，保证
    区间内余额不为负、且赎回不会凭空变成收益。
    """
    ordered = list(rows)
    codes = money_fund_codes(ordered)
    if not codes:
        return {}
    running = dict.fromkeys(codes, 0)
    offset = dict.fromkeys(codes, 0)
    for row in ordered:
        code = _name(row.security_code)
        if code not in codes:
            continue
        running[code] += money_fund_value_units(row)
        if running[code] < 0:
            offset[code] = max(offset[code], -running[code])
    balances = dict(offset)
    for row in ordered:
        code = _name(row.security_code)
        if code not in codes:
            continue
        if before is not None and row.trade_date >= before:
            continue
        balances[code] = max(0, balances[code] + money_fund_value_units(row))
    return balances


def money_fund_total_units(rows: Iterable[Trade], *, before: date | None = None) -> int:
    """各货基在 `before`（不含）时点的余额合计（scale=2）。"""
    return sum(money_fund_balances(rows, before=before).values())


__all__ = [
    "NAME_KEYWORDS",
    "NON_CASH_NAME_KEYWORDS",
    "QTY_PRICE_SCALE",
    "UNIT_PRICE_UNITS",
    "money_fund_balances",
    "money_fund_codes",
    "money_fund_total_units",
    "money_fund_value_units",
]
