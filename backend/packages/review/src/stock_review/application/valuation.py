"""资产估值共享原语（nav 与资产表共用，避免口径漂移）。

抽取自 `application/nav.py` 的纯计算/取数原语（Period2-16 / D3）：
- 分类与持仓影响：`category` / `is_cash_balance` / `holding_delta`；
- 收盘价阶梯：`closing_price_ladder` / `close_on_or_before` / `total_market_value`；
- 行情取数与失败分类：`load_prices` / `market_failure_cause` / `blocked_price_reason`。

硬约束：
- 金额 / 价格 / 数量 / 比率全用标度整数（无 float）：数量 scale=4、价格 scale=6、
  金额 scale=2，故 `qty_units * close_units // QTY_PRICE_SCALE` 得金额（逐标的 floor）；
- 不读系统时钟（时间由调用方以 `Clock` 注入，纯原语不涉及"当前时间"）；
- 不做 I/O 之外的副作用：只有 `load_prices` 访问行情端口，且缺行情时**降级**为逐日
  blocked（不让整段计算失败）。

`initial_cash_anchor` 仍复用 `application.bridge._initial_cash`（期初现金锚点反推逻辑
与对账/桥接同源），因此本模块不 import bridge，避免循环依赖。
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from datetime import date, timedelta

from stock_contracts.errors import ApiError
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketDataSource

# 持仓影响类：这些 category 会改变持仓数量（buy/bonus/rights/new_stock 加，sell 减）。
HOLDING_CATEGORIES: frozenset[str] = frozenset({"buy", "sell", "bonus", "rights", "new_stock"})
# 外部现金流：transfer（银证转账）。
CASHFLOW_CATEGORIES: frozenset[str] = frozenset({"transfer"})
# 增加持仓成本的类别：buy/rights/new_stock 的 amount_units 为负（带费净额），取负即含费成本。
# 不得再叠加 commission/stamp_tax/other_fee 字段（解析器已折算进净额，重复计会虚高成本）。
COST_ADD_CATEGORIES: frozenset[str] = frozenset({"buy", "rights", "new_stock"})
# 数量标度（scale=4）× 价格标度（scale=6）→ 金额标度（scale=2）需除 1e8。
QTY_PRICE_SCALE = 10**8
# 取数回看窗口：估值基准日需要「当日或之前最近收盘」，假期/周末需往前找。
PRICE_LOOKBACK = timedelta(days=45)

# 行情失败类别（适配器写在 `ApiError.metadata["market_failure"]`；不新增错误码）。
_MARKET_FAILURE_KEY = "market_failure"
_MARKET_FAILURE_LABELS: dict[str, str] = {
    "unavailable": "（行情源暂时不可用，可重试）",
    "not_supported": "（标的不在支持范围，仅支持 A 股主板/科创/创业）",
    "no_data": "（该区间无行情，可能未上市/停牌）",
}
_MARKET_FAILURE_PRIORITY: tuple[str, ...] = ("unavailable", "not_supported", "no_data")


def category(trade: Trade) -> str:
    """ledger trade 的业务分类（规范化：trim + lower；空/NULL 返回空串）。"""
    return (trade.category or "").strip().lower()


def is_cash_balance(trade: Trade) -> bool:
    """该行是否带现金余额（`cash_balance_units` 非空即为对账余额行）。"""
    return trade.cash_balance_units is not None


def holding_delta(category: str, quantity_units: int) -> int:
    """单笔 trade 对持仓数量的增量（buy/bonus/rights/new_stock 加，sell 减）。"""
    if category in {"buy", "bonus", "rights", "new_stock"}:
        return quantity_units
    if category == "sell":
        return -quantity_units
    return 0


def closing_price_ladder(
    bars: Sequence[Bar], symbols: list[str]
) -> dict[str, list[tuple[date, int]]]:
    """Bar 序列 → {symbol: [(trade_date, close_units)] 升序}。

    用「价格阶梯」而非精确日期查表：非交易日（周末/节假日/月末口径）取**当日或之前
    最近一个收盘价**，否则任何非交易日的净值都会因「当日无 bar」被误判阻断。
    """
    ladder: dict[str, list[tuple[date, int]]] = {symbol: [] for symbol in symbols}
    for bar in bars:
        ladder.setdefault(bar.symbol, []).append((bar.trade_date, bar.close_units))
    for entries in ladder.values():
        entries.sort(key=lambda item: item[0])
    return ladder


def close_on_or_before(
    ladder: dict[str, list[tuple[date, int]]], symbol: str, trade_date: date
) -> int | None:
    """该标的在 trade_date 当日或之前最近一个收盘价（scale=6）；无则 None。"""
    entries = ladder.get(symbol)
    if not entries:
        return None
    index = bisect.bisect_right([d for d, _ in entries], trade_date)
    if index == 0:
        return None
    return entries[index - 1][1]


def total_market_value(
    holdings: dict[str, int],
    ladder: dict[str, list[tuple[date, int]]],
    trade_date: date,
) -> tuple[int, list[str]]:
    """当日持仓市值（scale=2）+ 缺失行情的标的列表。

    市值 = Σ qty_units * close_units / 1e8（qty scale=4 × price scale=6 → scale=2）；
    **逐标的 floor**（先乘后整除），与设计 §4.1 口径一致。
    """
    total = 0
    missing: list[str] = []
    for symbol, qty in holdings.items():
        if qty == 0:
            continue
        close = close_on_or_before(ladder, symbol, trade_date)
        if close is None:
            missing.append(symbol)
            continue
        total += qty * close // QTY_PRICE_SCALE
    return total, missing


def market_failure_cause(exc: ApiError) -> str:
    """从 `ApiError.metadata["market_failure"]` 取可读失败类别（未知/缺失返回空串）。"""
    raw = exc.metadata.get(_MARKET_FAILURE_KEY)
    return raw if isinstance(raw, str) and raw in _MARKET_FAILURE_LABELS else ""


def blocked_price_reason(prefix: str, symbols: list[str], causes: dict[str, str]) -> str:
    """`缺行情数据：000651` + 三类可读后缀（不可用可重试 / 不在支持范围 / 无行情）。"""
    base = f"{prefix}：{','.join(sorted(symbols))}"
    present = {causes[symbol] for symbol in symbols if symbol in causes}
    for cause in _MARKET_FAILURE_PRIORITY:
        if cause in present:
            return base + _MARKET_FAILURE_LABELS[cause]
    return base


async def load_prices(
    market_data: MarketDataSource,
    symbols: list[str],
    start: date,
    end: date,
) -> tuple[dict[str, list[tuple[date, int]]], dict[str, str]]:
    """取收盘价阶梯 + 每标的失败类别；缺行情时**降级为逐日阻断**，不让整段请求失败。

    行情端口对未知标的会抛 `ApiError`（回放夹具没有该标的 / live 不在支持范围）。
    批量取数失败后按标的逐个重试，取不到的标的跳过 —— 后续逐日推演会把持有该标的
    的日期判为阻断（缺行情数据），而不是把整段计算变成 404。返回的 `causes` 用于
    把阻断原因细化为三类（见 `docs/architecture/2026-10-22-live-market-data-source.md` §6）。
    """
    if not symbols:
        return {}, {}
    causes: dict[str, str] = {}
    try:
        bars: Sequence[Bar] = await market_data.get_daily_bars(symbols, start, end)
    except ApiError:
        collected: list[Bar] = []
        for symbol in symbols:
            try:
                collected.extend(await market_data.get_daily_bars([symbol], start, end))
            except ApiError as exc:
                cause = market_failure_cause(exc)
                if cause:
                    causes[symbol] = cause
                continue  # 该标的无行情 → 持有它的日期阻断
        ladder = closing_price_ladder(collected, symbols)
    else:
        ladder = closing_price_ladder(bars, symbols)
    # 请求了但区间内没有任何 bar：视为 no_data（适配器不必为此造 404）。
    for symbol in symbols:
        if not ladder.get(symbol):
            causes.setdefault(symbol, "no_data")
    return ladder, causes


__all__ = [
    "CASHFLOW_CATEGORIES",
    "COST_ADD_CATEGORIES",
    "HOLDING_CATEGORIES",
    "PRICE_LOOKBACK",
    "QTY_PRICE_SCALE",
    "blocked_price_reason",
    "category",
    "close_on_or_before",
    "closing_price_ladder",
    "holding_delta",
    "is_cash_balance",
    "load_prices",
    "market_failure_cause",
    "total_market_value",
]
