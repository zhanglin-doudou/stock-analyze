"""货币基金（现金类产品）识别与余额推演（纯计算，无 I/O、无 wall-clock）。

口径（2026-11-16 人工确认）：

* 货基是**现金等价物**：不进 FIFO、不产生回合、不进持仓/持仓成本/市值，也不请求行情；
  资产表/净值把它并入「现金余额」，总资产恒等式仍为「现金 + 持仓市值」。
* 识别按**整只证券代码**判定，避免单行证据误伤普通基金：
  1. 强证据：证券名称含「货币」或「现金」；
  2. 兜底证据（同时满足）：非行情品种（`needs_quote` 为 False）、名称不含
     ETF/LOF/REIT、至少一笔数量非 0 的申赎、且该标的所有数量非 0 的行单位价格
     恰为 1.000000、三费全 0。
* 申赎方向看 `category`（导入行可能 `side=unknown`）：`sell` 减少余额，`buy`/
  `dividend` 等增加（红利发放/红利再投以份额形式增加余额）；`broker_action`
  （零金额登记/份额过户）一律不影响余额，避免与资金腿重复计入。
* 份额正推识别超卖：赎回份额超过当时余额时，区分两种缺口——
  - 此前**没有任何增持**（历史首笔即赎回）→ 期初已持有，计入反推期初余额；
  - 此前出现过申购/红利再投 → 超卖，券商以补记份额处理，当日按收益计入，
    **不进期初**（否则缺口会被摊进初始资产，申购前后的历史被一并改写）。

本模块放在 ledger 层：导入提交的「资金腿归一」需要在落库前复用同一份识别/余额口径，
review 侧通过 `stock_review.application.money_fund` 兼容转发。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from stock_platform.symbols import needs_quote

from stock_ledger.models import Trade

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
    """单笔货基流水对余额的金额影响（scale=2；申购买入为正、赎回卖出为负）。

    `broker_action` 是登记/过户类动作（如「非交易过户确认」），份额变动只用于
    核对，不代表余额增减；若同时存在资金腿，加回来会重复计入，故一律返回 0。
    """
    if (row.category or "").strip().lower() == "broker_action":
        return 0
    quantity = row.quantity_units
    if quantity == 0:
        return 0
    price = row.price_units if row.price_units > 0 else UNIT_PRICE_UNITS
    value = abs(quantity) * price // QTY_PRICE_SCALE
    return -value if (row.category or "").strip().lower() == "sell" else value


@dataclass(frozen=True, slots=True)
class MoneyFundOversell:
    """份额正推发现的货基超卖（赎回份额超过当时余额，券商以补记份额处理）。"""

    trade_id: str
    trade_date: date
    security_code: str
    # 超出当时余额的份额（scale=4，正数）；货基单位价格恒 1.000000，即超卖金额 ×100。
    quantity_units: int

    @property
    def value_units(self) -> int:
        """超卖份额折算的金额（scale=2），用于把缺口计入选当日收益。"""
        return _shares_to_value_units(self.quantity_units)


def _share_delta(row: Trade) -> int:
    """带方向份额增量（scale=4）：赎回为负，申购/红利再投为正，登记类为 0。"""
    quantity = row.quantity_units
    if quantity == 0:
        return 0
    category = (row.category or "").strip().lower()
    if category == "broker_action":
        return 0
    return -quantity if category == "sell" else quantity


def _shares_to_value_units(quantity_units: int) -> int:
    """份额（scale=4）→ 金额（scale=2）：货基单位价格恒 1.000000。"""
    return abs(quantity_units) * UNIT_PRICE_UNITS // QTY_PRICE_SCALE


def _forward_shares(
    rows: list[Trade], codes: frozenset[str]
) -> tuple[dict[str, int], tuple[MoneyFundOversell, ...]]:
    """全历史份额正推：返回 (期初反推份额, 超卖记录)。

    - 出现过增持（申购/红利再投）之前的赎回缺口 → 期初已持有，计入反推期初；
    - 出现过增持之后的赎回缺口 → 超卖，券商补记份额、当日计入收益，不进期初。
    """
    running: dict[str, int] = dict.fromkeys(codes, 0)
    offset: dict[str, int] = dict.fromkeys(codes, 0)
    increased: dict[str, bool] = dict.fromkeys(codes, False)
    oversells: list[MoneyFundOversell] = []
    for row in rows:
        code = _name(row.security_code)
        if code not in codes:
            continue
        delta = _share_delta(row)
        if delta == 0:
            continue
        if delta > 0:
            increased[code] = True
            running[code] += delta
            continue
        running[code] += delta
        if running[code] >= 0:
            continue
        deficit = -running[code]
        if increased[code]:
            oversells.append(
                MoneyFundOversell(
                    trade_id=row.id,
                    trade_date=row.trade_date,
                    security_code=code,
                    quantity_units=deficit,
                )
            )
        else:
            offset[code] += deficit
        running[code] += deficit
    return offset, tuple(oversells)


def money_fund_oversells(rows: Iterable[Trade]) -> tuple[MoneyFundOversell, ...]:
    """全历史份额正推得到的货基超卖记录（日期 / 证券代码 / 超出份额）。"""
    ordered = list(rows)
    codes = money_fund_codes(ordered)
    if not codes:
        return ()
    return _forward_shares(ordered, codes)[1]


def money_fund_balances(rows: Iterable[Trade], *, before: date | None = None) -> dict[str, int]:
    """各货基在 `before`（不含）时点的余额（scale=2），无货基返回 `{}`。

    期初反推只吸收「此前无任何增持」的赎回缺口（首笔即赎回、账单缺申购流水）；
    申购/红利再投之后出现的缺口是超卖，按当日收益处理，不抬升期初，保证时点
    余额与申购以前的历史不受未来超卖影响。任何时点的余额仍按 0 兜底（不出现负余额）。
    """
    ordered = list(rows)
    codes = money_fund_codes(ordered)
    if not codes:
        return {}
    offset, _oversells = _forward_shares(ordered, codes)
    shares = dict(offset)
    for row in ordered:
        code = _name(row.security_code)
        if code not in codes:
            continue
        if before is not None and row.trade_date >= before:
            continue
        delta = _share_delta(row)
        if delta == 0:
            continue
        shares[code] = max(0, shares[code] + delta)
    return {code: _shares_to_value_units(value) for code, value in shares.items()}


def money_fund_total_units(rows: Iterable[Trade], *, before: date | None = None) -> int:
    """各货基在 `before`（不含）时点的余额合计（scale=2）。"""
    return sum(money_fund_balances(rows, before=before).values())


__all__ = [
    "NAME_KEYWORDS",
    "NON_CASH_NAME_KEYWORDS",
    "QTY_PRICE_SCALE",
    "UNIT_PRICE_UNITS",
    "MoneyFundOversell",
    "money_fund_balances",
    "money_fund_codes",
    "money_fund_oversells",
    "money_fund_total_units",
    "money_fund_value_units",
]
