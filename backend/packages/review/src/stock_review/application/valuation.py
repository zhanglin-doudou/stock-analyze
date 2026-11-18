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
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from stock_contracts.errors import ApiError
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketDataSource
from stock_platform.symbols import needs_quote

# 持仓影响类：这些 category 会改变持仓数量（buy/bonus/rights/new_stock 加，sell 减）。
# broker_action（申购中签/新股入账/证券清理等登记动作）不在其中：数量与市值都不受影响。
HOLDING_CATEGORIES: frozenset[str] = frozenset({"buy", "sell", "bonus", "rights", "new_stock"})
# 外部现金流：transfer（资金划转）与 broker_action（券商行为）。
# broker_action 按**金额**判定资金属性：登记行金额为 0 → 对现金零影响；若账单确实带了非 0
# 金额（如「申购还款」回款），按外部现金流从收益中剔除，避免虚增。
CASHFLOW_CATEGORIES: frozenset[str] = frozenset({"transfer", "broker_action"})
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


def new_stock_pending_codes(trades: Iterable[Trade]) -> set[str]:
    """新股认购缴款（`new_stock` 且发生金额非 0）的标的集合。

    这些标的在上市首日之前没有任何行情，估值时按认购成本兜底（设计 D11）。零金额的
    中签/入帐通知行不算（它们不建仓）。
    """
    return {
        trade.security_code
        for trade in trades
        if trade.security_code
        and (trade.category or "").strip().lower() == "new_stock"
        and trade.amount_units != 0
    }


def new_stock_codes_in_window(trades: Iterable[Trade], *, start: date, end: date) -> set[str]:
    """窗口 `[start, end]`（闭区间，与持仓/残差的行窗口一致）内新股认购缴款的标的。

    归因专用：只有本期窗口内出现过 `new_stock` 认购/中签缴款行的标的才按
    `new_stock` 排除出五因子；历史认购但本期无新股行的标的本期按普通 `stock` 归因。
    估值兜底（assets/nav/portfolio/归因镜像）仍用全历史的 `new_stock_pending_codes`。
    """
    return {
        trade.security_code
        for trade in trades
        if trade.security_code
        and (trade.category or "").strip().lower() == "new_stock"
        and trade.amount_units != 0
        and start <= trade.trade_date <= end
    }


def category(trade: Trade) -> str:
    """`ledger trade` 的业务分类（规范化：trim + lower；空/NULL 返回空串）。"""
    return (trade.category or "").strip().lower()


def is_cash_balance(trade: Trade) -> bool:
    """该行是否带现金余额（`cash_balance_units` 非空即为对账余额行）。"""
    return trade.cash_balance_units is not None


def is_cash_anchor(trade: Trade) -> bool:
    """该行的现金余额能否代表「券商现金」并被用作锚点/覆盖值。

    零金额但有份额的行（货基红利发放/红利再投、送转、非交易过户等）不改变券商现金，
    账单在这些行的「资金余额」列常写 0 或按另一账户口径记账；若拿它覆盖累计现金，
    会把当日真实余额抹掉（真实案例：2026-10-27 货基红利行 0 元，桥接残差 101.15 元）。
    这类行的正确口径是「上一笔余额顺延」，因此不作为锚点。
    """
    return trade.cash_balance_units is not None and not (
        trade.amount_units == 0 and trade.quantity_units != 0
    )


def holding_delta(category: str, quantity_units: int, *, amount_units: int = 0) -> int:
    """单笔 trade 对持仓数量的增量（buy/bonus/rights/new_stock 加，sell 减）。

    `broker_action`（券商行为：申购中签/新股入账/证券清理/托管等）与 `transfer` 一样
    不计持仓：它们是券商的份额登记/锁定动作，不是真实成交。
    新股例外：`new_stock` 且发生金额为 0 的行按通知行处理、不计持仓（新口径下 new_stock
    只匹配「新股/认购/申购」扣款行，此判断仅作防御，避免零金额行重复计数）。
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


def valued_position_units(
    symbol: str,
    quantity_units: int,
    close_units: int | None,
    cost_units: int | None,
    *,
    new_stock_codes: set[str] | None = None,
) -> tuple[int | None, str]:
    """单个持仓的市值（scale=2）与估值来源（资产表/净值/指标/归因共用）。

    * 非行情品种（`needs_quote` 为 False：债券、`A40006`/`970172`/`119924` 等伪代码）：
      直接按含费成本 `cost_units`（买入价）估值，source=`"cost"`；不请求行情、不算缺失；
    * 有当日或之前最近收盘：`qty × close ÷ 1e8`，source=`"market"`；
    * 缺收盘但是待上市新股（`new_stock_codes`）：按认购成本，source=`"new_stock_cost"`；
    * 其余：`(None, "")`，调用方按缺行情处理（阻断/未估值）。
    """
    if not needs_quote(symbol):
        return (cost_units, "cost") if cost_units is not None else (None, "")
    if close_units is not None:
        return quantity_units * close_units // QTY_PRICE_SCALE, "market"
    if cost_units is not None and new_stock_codes and symbol in new_stock_codes:
        return cost_units, "new_stock_cost"
    return None, ""


def total_market_value(
    holdings: dict[str, int],
    ladder: dict[str, list[tuple[date, int]]],
    trade_date: date,
    *,
    cost_basis: dict[str, int] | None = None,
    new_stock_codes: set[str] | None = None,
) -> tuple[int, list[str], list[str], list[str]]:
    """当日持仓市值（scale=2）+ 缺行情阻断标的 + 按成本估值的非行情标的 / 新股待上市标的。

    市值 = Σ qty_units * close_units / 1e8（qty scale=4 × price scale=6 → scale=2）；
    **逐标的 floor**（先乘后整除），与设计 §4.1 口径一致。

    非行情品种（`not needs_quote`：债券、`A40006`/`970172`/`119924` 这类伪代码）不读行情，
    直接按 `cost_basis[code]`（含费成本/买入价）估值，计入 `cost_fallback`；调用方只留痕，
    不标 degraded、不阻断。`cost_basis=None` 表示调用方不启用成本兜底（这类标的进 `missing`）。

    新股例外（设计 D11）：`new_stock_codes` 里的标的（认购缴款已入库、尚未上市）缺行情时
    按认购成本兜底计入 `new_stock_fallback`；上市后有了行情就回到市价口径。
    """
    total = 0
    missing: list[str] = []
    cost_fallback: list[str] = []
    stock_fallback: list[str] = []
    for symbol, qty in holdings.items():
        if qty == 0:
            continue
        close = close_on_or_before(ladder, symbol, trade_date) if needs_quote(symbol) else None
        value, source = valued_position_units(
            symbol,
            qty,
            close,
            None if cost_basis is None else cost_basis.get(symbol, 0),
            new_stock_codes=new_stock_codes,
        )
        if value is None:
            missing.append(symbol)
            continue
        total += value
        if source == "cost":
            cost_fallback.append(symbol)
        elif source == "new_stock_cost":
            stock_fallback.append(symbol)
    return total, missing, cost_fallback, stock_fallback


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
    *,
    cached_only: bool = False,
) -> tuple[dict[str, list[tuple[date, int]]], dict[str, str]]:
    """取收盘价阶梯 + 每标的失败类别；缺行情时**降级为逐日阻断**，不让整段请求失败。

    行情端口对未知标的会抛 `ApiError`（回放夹具没有该标的 / live 不在支持范围）。
    批量取数失败后按标的逐个重试，取不到的标的跳过 —— 后续逐日推演会把持有该标的
    的日期判为阻断（缺行情数据），而不是把整段计算变成 404。返回的 `causes` 用于
    把阻断原因细化为三类（见 `docs/architecture/2026-10-22-live-market-data-source.md` §6）。

    ``cached_only=True`` 时优先只读行情缓存（不触发尾部补拉），用于看板持仓与资产
    快照同价；行情源没有缓存能力（replay / 无缓存 live）时回退到正常取数。
    """
    if not symbols:
        return {}, {}
    causes: dict[str, str] = {}
    reader = getattr(market_data, "get_cached_bars", None) if cached_only else None
    fetch = reader or market_data.get_daily_bars
    try:
        bars: Sequence[Bar] = await fetch(symbols, start, end)
    except ApiError:
        collected: list[Bar] = []
        for symbol in symbols:
            try:
                collected.extend(await fetch([symbol], start, end))
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
    "is_cash_anchor",
    "is_cash_balance",
    "load_prices",
    "market_failure_cause",
    "new_stock_codes_in_window",
    "new_stock_pending_codes",
    "total_market_value",
    "valued_position_units",
]
