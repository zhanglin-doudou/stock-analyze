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
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal

from stock_contracts.errors import ApiError
from stock_core.values import Money, Price, Qty
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketDataSource
from stock_platform.symbols import is_bond, is_convertible_bond

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
    "not_supported": "（标的不在支持范围，支持 A 股主板/科创/创业、场内基金 ETF/LOF/REITs 与沪深债券）",
    "no_data": "（该区间无行情，可能未上市/停牌）",
}
_MARKET_FAILURE_PRIORITY: tuple[str, ...] = ("unavailable", "not_supported", "no_data")

# 债券无行情时的兜底估值口径文案（行级 degraded 原因，设计 §6.4）。
BOND_COST_FALLBACK_PREFIX = "债券按买入价估值（无行情"
# 新股待上市：认购缴款后至上市首日之前没有行情，按认购成本（含费缴款金额）兜底估值，
# 该日标 degraded 而非 blocked（设计 D11）。
NEW_STOCK_COST_FALLBACK_PREFIX = "新股待上市，按认购价估值（无行情"
# 货基单位净值恒 1.0000（价格列缺失时用于份额 → 金额换算）。
_MONEY_FUND_UNIT_PRICE_UNITS = Price.from_decimal(Decimal(1)).units
# 可转债：数量单位是手，1 手 = 10 张；行情/成交价格是单张金额（元/张），
# 市值 = qty(手) × close(元/张) × 10（设计 D2；不走成交金额反推）。
CBOND_LOT_SIZE = 10


def bond_value_units(
    symbol: str,
    qty_units: int,
    close_units: int,
    divisors: Mapping[str, int] | None,
) -> int:
    """单只债券市值（分）：可转债 qty(手) × close(元/张) × 10；其它债券按换算系数折算。

    非债券调用无意义（divisor=1 等价于股票口径，但调用方应自行区分）。
    """
    if is_convertible_bond(symbol):
        return qty_units * close_units * CBOND_LOT_SIZE // QTY_PRICE_SCALE
    return qty_units * close_units // QTY_PRICE_SCALE // (divisors or {}).get(symbol, 100)


def bond_fallback_reason(symbols: list[str]) -> str:
    """`债券按买入价估值（无行情：019547,113050）`。"""
    return f"{BOND_COST_FALLBACK_PREFIX}：{','.join(sorted(symbols))}）"


def new_stock_fallback_reason(symbols: list[str]) -> str:
    """`新股待上市，按认购价估值（无行情：001237）`。"""
    return f"{NEW_STOCK_COST_FALLBACK_PREFIX}：{','.join(sorted(symbols))}）"


def new_stock_pending_codes(trades: Iterable[Trade]) -> set[str]:
    """新股认购缴款（`new_stock` 且发生金额非 0）的标的集合。

    这些标的在上市首日之前没有任何行情，估值时按认购成本兜底（设计 D11）。零金额的
    中签/入帐通知行不算（它们不建仓）。
    """
    return {
        trade.security_code
        for trade in trades
        if trade.security_code
        and not is_money_fund_row(trade)
        and (trade.category or "").strip().lower() == "new_stock"
        and trade.amount_units != 0
    }


def category(trade: Trade) -> str:
    """ledger trade 的业务分类（规范化：trim + lower；空/NULL 返回空串）。"""
    return (trade.category or "").strip().lower()


def is_cash_balance(trade: Trade) -> bool:
    """该行是否带现金余额（`cash_balance_units` 非空即为对账余额行）。"""
    return trade.cash_balance_units is not None


def is_money_fund_row(trade: Trade) -> bool:
    """该行是否货币基金行（`money_fund_balance_units` 非空即标记）。

    货基单位净值恒 1.0000，没有可估值净值：这类行**不进持仓、不请求行情**，只把余额
    计入资产（设计 §5.5）。
    """
    return trade.money_fund_balance_units is not None


def money_fund_share_delta(trade: Trade) -> int:
    """货基行的余额增量（金额分；卖出取负，其余含「红利发放」红利再投取正）。

    份额（Qty scale=4）按单位净值（恒 1.0000）换算成金额（Money scale=2），
    与解析器 `_apply_money_fund_balances` 同一口径。
    """
    shares = -trade.quantity_units if category(trade) == "sell" else trade.quantity_units
    price_units = trade.price_units if trade.price_units > 0 else _MONEY_FUND_UNIT_PRICE_UNITS
    return Money.from_decimal(Qty(shares).to_decimal() * Price(price_units).to_decimal()).units


def initial_money_fund_balances(trades: list[Trade], period_start: date) -> dict[str, int]:
    """区间期初各货基标的的余额（scale=2）。

    口径对齐 `bridge._initial_cash`：区间前最后一条余额行直取（statement）；期初没有
    余额行时用区间内首条余额行反推（`余额 − 该行份额增量`，back_solved）。无货基行 → `{}`。
    """
    balances: dict[str, int] = {}
    for trade in trades:
        if trade.trade_date < period_start and is_money_fund_row(trade):
            balances[trade.security_code] = int(trade.money_fund_balance_units or 0)
    seen: set[str] = set(balances)
    for trade in trades:
        if trade.trade_date < period_start or not is_money_fund_row(trade):
            continue
        code = trade.security_code
        if code in seen:
            continue
        seen.add(code)
        balances[code] = int(trade.money_fund_balance_units or 0) - money_fund_share_delta(trade)
    return balances


def bond_face_divisors(trades: list[Trade]) -> dict[str, int]:
    """从成交金额反推债券「数量单位 × 报价单位」换算系数（∈{1,10,100}）。

    akshare 债券收盘是净价（元/百元面值）；券商对账单「成交数量」可能是张（系数 100）
    或手（系数 10）。用含费净额反推：`|amount| × 1e8 ≈ qty × price × divisor`，取首个
    残差 ≤2% 的档位；推不出（无买入行/残差异常）按 100 兜底（设计 D8）。

    可转债例外（设计 D2）：数量单位固定是手（1 手 = 10 张）、价格是单张金额，市值按
    `qty × close × 10` 折算（见 `bond_value_units`），不走反推 —— 故本函数跳过可转债。
    """
    divisors: dict[str, int] = {}
    for trade in trades:
        code = trade.security_code
        if not code or code in divisors or not is_bond(code) or is_convertible_bond(code):
            continue
        if category(trade) not in COST_ADD_CATEGORIES:
            continue
        qty, price = trade.quantity_units, trade.price_units
        amount = abs(trade.amount_units)
        if qty <= 0 or price <= 0 or amount == 0:
            continue
        for divisor in (1, 10, 100):
            # 市值 = qty × close ÷ 1e8 ÷ divisor ≈ |amount| → qty × close ≈ amount × 1e8 × divisor
            lhs = amount * QTY_PRICE_SCALE * divisor
            target = qty * price
            if target and abs(lhs - target) * 100 <= 2 * target:
                divisors[code] = divisor
                break
        else:
            divisors[code] = 100
    return divisors


def holding_delta(category: str, quantity_units: int, *, amount_units: int = 0) -> int:
    """单笔 trade 对持仓数量的增量（buy/bonus/rights/new_stock 加，sell 减）。

    新股例外：`new_stock` 且发生金额为 0 的行是「申购中签 / 新股入帐」通知行——同一批
    中签会先后出现中签、入帐、缴款三行，只有缴款行（金额非 0）才是真正的建仓，通知行
    一律不计持仓，否则持仓会被重复计数。
    """
    if category == "new_stock" and amount_units == 0:
        return 0
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
    *,
    cost_basis: dict[str, int] | None = None,
    bond_divisors: dict[str, int] | None = None,
    new_stock_codes: set[str] | None = None,
) -> tuple[int, list[str], list[str], list[str]]:
    """当日持仓市值（scale=2）+ 缺行情阻断标的 + 按成本兜底的债券 / 新股待上市标的。

    市值 = Σ qty_units * close_units / 1e8（qty scale=4 × price scale=6 → scale=2）；
    **逐标的 floor**（先乘后整除），与设计 §4.1 口径一致。

    债券例外（设计 D8/D9）：akshare 收盘是净价（元/百元面值），市值再除以换算系数
    `bond_divisors[code]`（默认 100）；缺行情时按 `cost_basis[code]`（含费成本）兜底，
    计入 `bond_fallback`（调用方标 degraded，不阻断）。

    新股例外（设计 D11）：`new_stock_codes` 里的标的（认购缴款已入库、尚未上市）同样
    缺行情，按认购成本兜底计入 `new_stock_fallback`；上市后有了行情就回到市价口径。
    `cost_basis=None` 表示调用方不启用任何兜底（缺行情一律进 `missing`）。
    """
    total = 0
    missing: list[str] = []
    bond_fallback: list[str] = []
    stock_fallback: list[str] = []
    for symbol, qty in holdings.items():
        if qty == 0:
            continue
        bond = is_bond(symbol)
        close = close_on_or_before(ladder, symbol, trade_date)
        if close is None:
            if cost_basis is not None:
                if bond:
                    bond_fallback.append(symbol)
                    total += cost_basis.get(symbol, 0)
                    continue
                if new_stock_codes and symbol in new_stock_codes:
                    stock_fallback.append(symbol)
                    total += cost_basis.get(symbol, 0)
                    continue
            missing.append(symbol)
            continue
        if bond:
            total += bond_value_units(symbol, qty, close, bond_divisors)
        else:
            total += qty * close // QTY_PRICE_SCALE
    return total, missing, bond_fallback, stock_fallback


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
    "BOND_COST_FALLBACK_PREFIX",
    "CASHFLOW_CATEGORIES",
    "CBOND_LOT_SIZE",
    "COST_ADD_CATEGORIES",
    "HOLDING_CATEGORIES",
    "NEW_STOCK_COST_FALLBACK_PREFIX",
    "PRICE_LOOKBACK",
    "QTY_PRICE_SCALE",
    "blocked_price_reason",
    "bond_face_divisors",
    "bond_fallback_reason",
    "bond_value_units",
    "category",
    "close_on_or_before",
    "closing_price_ladder",
    "holding_delta",
    "initial_money_fund_balances",
    "is_cash_balance",
    "is_money_fund_row",
    "load_prices",
    "market_failure_cause",
    "money_fund_share_delta",
    "new_stock_fallback_reason",
    "new_stock_pending_codes",
    "total_market_value",
]
