"""归因装配层：lot + fill → `SegmentInput`（设计 §3.3/§3.4，两级分摊）。

口径钉死（v1.0 已批准）：
- **段来源**：应用层 `build_rounds(trades ≤ T1)` 现算（D-15，不读 `review.round*`）。
- **两级分摊**（§3.3）：标的级期初锚 `floor(Σq0×P0)`（区间前 lot）/ lot 级
  `floor(entry_qty×C_b)`（新 lot）→ 按段数量 `allocate`（尾差给最后），保证
  `Σ段 base == 标的级锚`；期末锚 `floor(Σq_open×P1)` 标的级一次 allocate 到全部 open 段。
- **区间前 lot**：`gross_buy=None`、`buy_timing=0`、**买费不进本期**（黄金表 §3.8 要点）；
  base 参考价 = P0（`close_on_or_before(T0−1)` 实际取到的日期）。
- **新 lot**：`gross_buy = 段净成本 − 买费分摊`；买费分母 = 该 lot 全部段数量
  （closed fills + T1 剩余）；base 参考价 = 建仓日收盘 C_b。
- **closed 段**：每 fill 一段；`gross_sell = fill.sell_proceeds + 卖费分摊`；卖费分母 =
  该卖 trade 的全部 matched fills 数量（卖 trade 的 close_date 唯一 → fills 全在窗口）；
  end = `floor(q×C_s)`（卖出日参考收盘）。
- **数量一致性断言**：区间前段数量之和必须等于 `res_pre.open_lots` 的 T0 剩余；
  买费分母为 0 → `SEGMENT_DATA_ERROR`（不静默把尾差塞进买点）。
- **板块**：`I_start` = base 参考日指数、`I_end` = end 参考日指数（个股参考日 = 引擎
  实际取到的日期）；指数实际日期与个股参考日不一致 → `SECTOR_BAR_FALLBACK`
  （degraded 披露，不剔除）；指数覆盖缺口 → 整标的剔除 `SECTOR_BAR_MISSING`。
- **剔除优先级**：corporate > SEGMENT_DATA_ERROR > 非 stock 类（reason=None，仅
  coverage 列出）> SECTOR_MAPPING_MISSING > SECTOR_BAR_MISSING > PRICE_REF_MISSING
  > FEE_BASIS_UNKNOWN。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from stock_core.attribution import SegmentInput, SegmentKind, allocate, sector_amount_units
from stock_core.fifo import FifoResult, Fill, OpenLot
from stock_ledger.models import Trade
from stock_platform.symbols import classify_symbol

from stock_review.application.valuation import (
    PRICE_LOOKBACK,
    QTY_PRICE_SCALE,
    category,
    is_money_fund_row,
    new_stock_pending_codes,
)

# 剔除码（`missing[]` 响应字段；资产类别排除不进 missing[]，仅 coverage 列出）。
REASON_CORPORATE = "CORPORATE_ACTION_ADJUSTMENT_MISSING"
REASON_DATA_ERROR = "SEGMENT_DATA_ERROR"
REASON_MAPPING = "SECTOR_MAPPING_MISSING"
REASON_BAR = "SECTOR_BAR_MISSING"
REASON_PRICE = "PRICE_REF_MISSING"
REASON_FEE = "FEE_BASIS_UNKNOWN"
# 资产类别排除在 `excluded_by_reason` 里的占位键。
REASON_ASSET_CLASS = ""

# 有建仓语义的分类（与资产表 COST_ADD_CATEGORIES 同口径；金额≠0 才建仓）。
_COST_ADD = frozenset({"buy", "rights", "new_stock"})
_BUY_SELL = frozenset({"buy", "sell"})


# 期初板块指数缺 bar 的回退窗口（自然日）：覆盖元旦/春节等长假。
# 期末方向不启用回退（避免引入未来信息）。
SECTOR_START_FALLBACK_DAYS = 5


class _BarMissingError(Exception):
    """板块指数覆盖缺口（内部控制流；整标的剔除）。"""


@dataclass(slots=True)
class SymbolPlan:
    """单个标的的归因计划（attributed=True 时含段；False 时含剔除原因）。"""

    security_code: str
    symbol_class: str
    attributed: bool
    reason_code: str | None  # 剔除码；资产类别排除为 None
    segments: tuple[SegmentInput, ...] = ()
    begin_anchor_units: int | None = None  # 标的级期初市值锚（coverage 资本分母）
    end_anchor_units: int | None = None  # 期末持有市值锚（= floor(Σq_open×P1)）
    fees_units: int = 0  # 该标的区间费用合计（买+卖分摊；归因标的）
    trades_amount_units: int = 0  # 窗口行 amount 合计（残差桶复算用）
    dividend_units: int = 0
    price_fallback: bool = False  # 参考价用了前收（≤45 天窗口）
    sector_fallback: bool = False  # 指数参考日与个股参考日不一致
    bucket_kind: str = "none"  # corporate_action_pnl | unattributed_price_pnl | none
    # 是否属于可归因 universe：A 股 stock 类且期末附近仍有行情。
    # 退市/长期停牌（期末 45 天内无任何 bar）为 False——缺行业映射时降级为 info，
    # 因为这类标的本就不参与归因（§4.4）。
    in_universe: bool = True
    # 期初回退明细（缺失参考日, 实际取到的日期）；None 表示期末严格匹配。
    sector_start_fallback: tuple[date, date] | None = None


@dataclass(slots=True)
class PlanBundle:
    """`build_symbol_plans` 的输出包（application 层消费）。"""

    plans: list[SymbolPlan]
    # reason_code -> 标的清单（REASON_ASSET_CLASS = 资产类别排除，不进 missing[]）。
    excluded_by_reason: dict[str, list[str]] = field(default_factory=dict)
    price_fallback_symbols: list[str] = field(default_factory=list)
    sector_fallback_symbols: list[str] = field(default_factory=list)
    # code -> (期初缺失参考日, 实际回退取到的日期)；仅期初回退（期末严格）。
    sector_start_fallbacks: dict[str, tuple[date, date]] = field(default_factory=dict)
    # 理论可归因候选（A 股 stock 类，或因数据缺口被剔除的 stock 类）：
    # 「存在候选但零可归因段」→ blocked + SECTOR_DATA_MISSING（§4.4 优先级 1）。
    has_candidate: bool = False


@dataclass(slots=True)
class _SegDraft:
    """段草稿（allocate 前的可变中间态）。"""

    kind: SegmentKind  # closed | open
    quantity_units: int
    is_new: bool  # 新 lot（open_date >= T0）
    base_ref_date: date  # base 参考日（P0 实际日 / C_b 实际日）
    end_ref_date: date  # end 参考日（C_s 实际日 / P1 实际日）
    end_price_units: int  # end 参考价（个股；closed=C_s、open=P1）
    cost_share: int = 0  # 含费成本分摊（closed=fill.buy_cost、open=lot.cost）
    proceeds_share: int = 0  # 净卖出分摊（closed=fill.sell_proceeds）
    buy_fee_share: int = 0
    sell_fee_share: int = 0
    base_units: int = 0
    end_value_units: int = 0


def _close_entry(
    ladder: Mapping[str, list[tuple[date, int]]], symbol: str, trade_date: date
) -> tuple[date, int] | None:
    """`≤ trade_date` 最近收盘：返回（实际日期, 单位价 scale=6）；无则 None。"""
    entries = ladder.get(symbol)
    if not entries:
        return None
    index = bisect_right([d for d, _ in entries], trade_date)
    if index == 0:
        return None
    return entries[index - 1]


def _in_universe(
    ladder: Mapping[str, list[tuple[date, int]]], symbol: str, expected_p1_date: date
) -> bool:
    """是否仍在可归因 universe 内（期末 `PRICE_LOOKBACK` 窗口内仍有行情）。

    退市/长期停牌的标的期末附近没有任何 bar（或最后一根早于 45 天窗口）→ False：
    这类标的即使缺行业映射也不影响归因口径，`missing[]` 降为 info（§4.4）。
    """
    entry = _close_entry(ladder, symbol, expected_p1_date)
    return entry is not None and (expected_p1_date - entry[0]) <= PRICE_LOOKBACK


def _first_entry_within(
    ladder: Mapping[str, list[tuple[date, int]]],
    symbol: str,
    start_ref: date,
    max_days: int,
) -> tuple[date, int] | None:
    """`start_ref` 之后 `max_days` 个自然日内的首个收盘（期初回退用）；无则 None。"""
    entries = ladder.get(symbol)
    if not entries:
        return None
    index = bisect_left([d for d, _ in entries], start_ref)
    if index >= len(entries):
        return None
    entry = entries[index]
    return entry if (entry[0] - start_ref) <= timedelta(days=max_days) else None


def _fee_basis_unknown(trade: Trade) -> bool:
    """费用三列全 0 但净额无法反解（§3.5：禁止比例估算）。

    buy/sell 且 价>0、量>0、amount≠0；`|amount|` 与 `floor(qty×price)` 差 >2 分。
    """
    if category(trade) not in _BUY_SELL:
        return False
    if trade.commission_units or trade.stamp_tax_units or trade.other_fee_units:
        return False
    if trade.price_units <= 0 or trade.quantity_units <= 0 or trade.amount_units == 0:
        return False
    gross = trade.quantity_units * trade.price_units // QTY_PRICE_SCALE
    return abs(abs(trade.amount_units) - gross) > 2


def _symbol_class(code: str, trades_sym: Sequence[Trade], new_stocks: set[str]) -> str:
    """`symbol_class`：stock/fund/bond/money_fund/new_stock/pseudo（§4.4）。"""
    if any(is_money_fund_row(t) for t in trades_sym):
        return "money_fund"
    if code in new_stocks:
        return "new_stock"
    kind = classify_symbol(code)
    return kind if kind else "pseudo"


def _fee_total(trade: Trade) -> int:
    """买卖费用三列合计（佣金+印花税+其他费；红利税独立列不进买卖费）。"""
    return trade.commission_units + trade.stamp_tax_units + trade.other_fee_units


def build_symbol_plans(
    *,
    trades: Sequence[Trade],
    t0: date,
    t1: date,
    res_all: FifoResult,
    res_pre: FifoResult,
    ladder: Mapping[str, list[tuple[date, int]]],
    memberships: Mapping[str, str],
    sector_ladder: Mapping[str, list[tuple[date, int]]],
    corporate: Mapping[str, str],
    canonical: Callable[[str], str],
    expected_p0_date: date,
    expected_p1_date: date,
    dividends: Mapping[str, int],
    fallback_anchors: Mapping[str, int],
) -> PlanBundle:
    """构建全部标的的归因计划（纯内存，无 I/O；应用层编排调用）。

    `expected_p0_date = latest_workday(T0−1)`、`expected_p1_date =
    latest_workday(min(valuation_end, T1))`（PRICE_REF_FALLBACK 判定基准，
    由调用方算好传入，本模块零时钟）。
    """
    trade_by_id = {t.id: t for t in trades}
    new_stocks = {canonical(c) for c in new_stock_pending_codes(trades)}
    data_error_codes = {canonical(de.security_code) for de in res_all.data_errors}
    data_error_codes.discard("")

    # fill / open lot / 窗口行按 canonical code 分组（fill 无 security_code，经买 trade 归属）。
    fills_by_code: dict[str, list[Fill]] = defaultdict(list)
    for fill in res_all.fills:
        trade = trade_by_id.get(fill.buy_trade_id)
        if trade is not None:
            fills_by_code[canonical(trade.security_code)].append(fill)
    open_by_code: dict[str, list[OpenLot]] = defaultdict(list)
    for lot in res_all.open_lots:
        open_by_code[lot.security_code].append(lot)
    rows_by_code: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        if t0 <= trade.trade_date <= t1 and not is_money_fund_row(trade):
            code = canonical(trade.security_code)
            if code:
                rows_by_code[code].append(trade)

    # 候选标的：FIFO 涉及标的 ∪ 窗口内有持仓类行（buy/sell/bonus/rights/new_stock）的标的。
    codes: set[str] = set(fills_by_code) | set(open_by_code)
    for trade in trades:
        if t0 <= trade.trade_date <= t1 and category(trade) in _COST_ADD | {"sell", "bonus"}:
            code = canonical(trade.security_code)
            if code:
                codes.add(code)
    codes.discard("CASH")

    bundle = PlanBundle(plans=[])
    for code in sorted(codes):
        trades_sym = rows_by_code.get(code, [])
        klass = _symbol_class(code, trades_sym, new_stocks)
        plan = _plan_for_symbol(
            code=code,
            klass=klass,
            trades_sym=trades_sym,
            fills_sym=fills_by_code.get(code, []),
            open_sym=open_by_code.get(code, []),
            data_error=code in data_error_codes,
            trade_by_id=trade_by_id,
            res_pre=res_pre,
            t0=t0,
            t1=t1,
            ladder=ladder,
            memberships=memberships,
            sector_ladder=sector_ladder,
            corporate=corporate,
            canonical=canonical,
            expected_p0_date=expected_p0_date,
            expected_p1_date=expected_p1_date,
            dividends=dividends,
            fallback_anchors=fallback_anchors,
        )
        bundle.plans.append(plan)
        if plan.attributed and klass == "stock":
            bundle.has_candidate = True
        if not plan.attributed and plan.reason_code is not None and klass == "stock":
            # stock 类因数据缺口剔除仍是候选（存在候选但零可归因段 → blocked，
            # §4.4：板块数据全缺/全部缺映射时零可归因段必须 blocked 而非 ok）。
            bundle.has_candidate = True
        if plan.price_fallback:
            bundle.price_fallback_symbols.append(code)
        if plan.sector_fallback:
            bundle.sector_fallback_symbols.append(code)
        if plan.sector_start_fallback is not None:
            bundle.sector_start_fallbacks[code] = plan.sector_start_fallback
        if not plan.attributed:
            reason = plan.reason_code or REASON_ASSET_CLASS
            bundle.excluded_by_reason.setdefault(reason, []).append(code)
    return bundle


def _plan_for_symbol(
    *,
    code: str,
    klass: str,
    trades_sym: Sequence[Trade],
    fills_sym: Sequence[Fill],
    open_sym: Sequence[OpenLot],
    data_error: bool,
    trade_by_id: Mapping[str, Trade],
    res_pre: FifoResult,
    t0: date,
    t1: date,
    ladder: Mapping[str, list[tuple[date, int]]],
    memberships: Mapping[str, str],
    sector_ladder: Mapping[str, list[tuple[date, int]]],
    corporate: Mapping[str, str],
    canonical: Callable[[str], str],
    expected_p0_date: date,
    expected_p1_date: date,
    dividends: Mapping[str, int],
    fallback_anchors: Mapping[str, int],
) -> SymbolPlan:
    """单标的计划：先按优先级判剔除，再两级分摊构段（§3.3）。"""
    trades_amount = sum(t.amount_units for t in trades_sym)
    in_universe = klass == "stock" and _in_universe(ladder, code, expected_p1_date)

    def _excluded(reason: str | None, bucket: str) -> SymbolPlan:
        return SymbolPlan(
            security_code=code,
            symbol_class=klass,
            attributed=False,
            reason_code=reason,
            begin_anchor_units=fallback_anchors.get(code),
            end_anchor_units=None,
            fees_units=0,
            trades_amount_units=trades_amount,
            dividend_units=0,
            bucket_kind=bucket,
            in_universe=in_universe,
        )

    bucket = "corporate_action_pnl" if code in corporate else "unattributed_price_pnl"
    if code in corporate:
        return _excluded(REASON_CORPORATE, bucket)
    if data_error:
        return _excluded(REASON_DATA_ERROR, bucket)
    if klass != "stock":
        return _excluded(None, "unattributed_price_pnl")
    if code not in memberships:
        return _excluded(REASON_MAPPING, bucket)

    closed_fills = [f for f in fills_sym if t0 <= f.close_date <= t1]
    open_lots = list(open_sym)
    if not closed_fills and not open_lots:
        # 窗口内无段（零金额中签/送转通知行标的等）：不产生段，行金额由残差桶吸收。
        return _excluded(None, "unattributed_price_pnl")
    if any(_fee_basis_unknown(t) for t in trades_sym):
        return _excluded(REASON_FEE, bucket)

    pre_closed = [f for f in closed_fills if f.open_date < t0]
    new_closed = [f for f in closed_fills if f.open_date >= t0]
    pre_open = [lot for lot in open_lots if lot.open_date < t0]
    new_open = [lot for lot in open_lots if lot.open_date >= t0]

    # 区间前数量一致性：段数量之和 == res_pre 的 T0 剩余（同一 FIFO 路径断言）。
    q0_total = 0
    for lot in res_pre.open_lots:
        trade = trade_by_id.get(lot.buy_trade_id)
        if trade is not None and canonical(trade.security_code) == code:
            q0_total += lot.quantity_units
    pre_qty = sum(f.quantity_units for f in pre_closed) + sum(
        lot.quantity_units for lot in pre_open
    )
    if pre_qty != q0_total:
        return _excluded(REASON_DATA_ERROR, bucket)

    # 参考价：P0（有区间前段才需要）、C_b（逐新 lot 建仓日）、C_s（逐 closed fill）、P1。
    price_fallback = False
    p0_entry = _close_entry(ladder, code, t0 - timedelta(days=1)) if pre_qty else None
    if pre_qty and p0_entry is None:
        return _excluded(REASON_PRICE, bucket)
    if p0_entry is not None and p0_entry[0] < expected_p0_date:
        price_fallback = True
    p1_entry = _close_entry(ladder, code, t1) if open_lots else None
    if open_lots and p1_entry is None:
        return _excluded(REASON_PRICE, bucket)
    if p1_entry is not None and p1_entry[0] < expected_p1_date:
        price_fallback = True

    sector_code = memberships[code]
    sector_fallback = False
    start_fallback: tuple[date, date] | None = None

    def _sector_amount(base_units: int, start_ref: date, end_ref: date) -> int | None:
        """段板块金额（单步取整）；指数缺口 → `_BarMissingError`（整标的剔除）。

        期初：优先 `≤ start_ref` 最近收盘；取不到时允许 `start_ref` 之后
        `SECTOR_START_FALLBACK_DAYS` 个自然日内首个收盘（节假日回退，记 info 披露）。
        期末：严格 `≤ end_ref`，不回退（避免引入未来信息）。
        """
        nonlocal sector_fallback, start_fallback
        start_entry = _close_entry(sector_ladder, sector_code, start_ref)
        if start_entry is None:
            start_entry = _first_entry_within(
                sector_ladder, sector_code, start_ref, SECTOR_START_FALLBACK_DAYS
            )
            if start_entry is None:
                raise _BarMissingError
            if start_fallback is None:
                start_fallback = (start_ref, start_entry[0])
            sector_fallback = True
        end_entry = _close_entry(sector_ladder, sector_code, end_ref)
        if end_entry is None:
            raise _BarMissingError
        if start_entry[0] != start_ref or end_entry[0] != end_ref:
            sector_fallback = True
        return sector_amount_units(base_units, start_entry[1], end_entry[1])

    # ── 段草稿（构建顺序 = allocate 顺序；尾差给最后）─────────────────
    drafts: list[_SegDraft] = []
    fill_drafts: dict[int, _SegDraft] = {}  # id(fill) -> draft（卖费分摊回填用）
    if p0_entry is not None:
        for fill in pre_closed:
            sell_entry = _close_entry(ladder, code, fill.close_date)
            if sell_entry is None:
                return _excluded(REASON_PRICE, bucket)
            if sell_entry[0] < fill.close_date:
                price_fallback = True
            draft = _SegDraft(
                kind="closed",
                quantity_units=fill.quantity_units,
                is_new=False,
                base_ref_date=p0_entry[0],
                end_ref_date=sell_entry[0],
                end_price_units=sell_entry[1],
                cost_share=fill.buy_cost_units,
                proceeds_share=fill.sell_proceeds_units,
            )
            drafts.append(draft)
            fill_drafts[id(fill)] = draft
        for lot in pre_open:
            if p1_entry is None:  # 防御（open_lots 非空 ⇒ p1_entry 非 None）
                return _excluded(REASON_PRICE, bucket)
            drafts.append(
                _SegDraft(
                    kind="open",
                    quantity_units=lot.quantity_units,
                    is_new=False,
                    base_ref_date=p0_entry[0],
                    end_ref_date=p1_entry[0],
                    end_price_units=p1_entry[1],
                    cost_share=lot.cost_units,
                )
            )
    n_pre = len(drafts)

    # 新 lot 按 buy_trade_id 分组（fills + T1 open，一个 buy trade 最多一个 open lot）。
    new_fills: dict[str, list[Fill]] = defaultdict(list)
    new_opens: dict[str, list[OpenLot]] = defaultdict(list)
    for fill in new_closed:
        new_fills[fill.buy_trade_id].append(fill)
    for lot in new_open:
        new_opens[lot.buy_trade_id].append(lot)
    for buy_trade_id in sorted(set(new_fills) | set(new_opens)):
        buy_trade = trade_by_id[buy_trade_id]
        buy_entry = _close_entry(ladder, code, buy_trade.trade_date)
        if buy_entry is None:
            return _excluded(REASON_PRICE, bucket)
        if buy_entry[0] < buy_trade.trade_date:
            price_fallback = True
        group_fills = new_fills.get(buy_trade_id, ())
        for fill in group_fills:
            sell_entry = _close_entry(ladder, code, fill.close_date)
            if sell_entry is None:
                return _excluded(REASON_PRICE, bucket)
            if sell_entry[0] < fill.close_date:
                price_fallback = True
            draft = _SegDraft(
                kind="closed",
                quantity_units=fill.quantity_units,
                is_new=True,
                base_ref_date=buy_entry[0],
                end_ref_date=sell_entry[0],
                end_price_units=sell_entry[1],
                cost_share=fill.buy_cost_units,
                proceeds_share=fill.sell_proceeds_units,
            )
            drafts.append(draft)
            fill_drafts[id(fill)] = draft
        open_lot: OpenLot | None = new_opens[buy_trade_id][0] if buy_trade_id in new_opens else None
        if open_lot is not None:
            if p1_entry is None:
                return _excluded(REASON_PRICE, bucket)
            drafts.append(
                _SegDraft(
                    kind="open",
                    quantity_units=open_lot.quantity_units,
                    is_new=True,
                    base_ref_date=buy_entry[0],
                    end_ref_date=p1_entry[0],
                    end_price_units=p1_entry[1],
                    cost_share=open_lot.cost_units,
                )
            )
        # 买费 + lot 级 base：一次 allocate（分母 = 该 lot 全部段数量；尾差给该 lot 末段）。
        group_drafts = drafts[len(drafts) - len(group_fills) - (1 if open_lot else 0) :]
        entry_qty = sum(d.quantity_units for d in group_drafts)
        if entry_qty <= 0:
            return _excluded(REASON_DATA_ERROR, bucket)
        lot_anchor = entry_qty * buy_entry[1] // QTY_PRICE_SCALE
        bases = allocate(lot_anchor, [d.quantity_units for d in group_drafts])
        fee_shares = allocate(_fee_total(buy_trade), [d.quantity_units for d in group_drafts])
        for draft, base, fee_share in zip(group_drafts, bases, fee_shares, strict=True):
            draft.base_units = base
            draft.buy_fee_share = fee_share

    # 区间前段 base：标的级 `floor(Σq0×P0)` 一次 allocate（尾差给最后一段）。
    if n_pre and p0_entry is not None:
        pre_anchor = pre_qty * p0_entry[1] // QTY_PRICE_SCALE
        bases = allocate(pre_anchor, [d.quantity_units for d in drafts[:n_pre]])
        for draft, base in zip(drafts[:n_pre], bases, strict=True):
            draft.base_units = base

    # 卖费：按卖 trade 全部 matched fills 分摊（allocate 尾差给该 trade 末一 fill）。
    sell_groups: dict[str, list[_SegDraft]] = defaultdict(list)
    for fill in closed_fills:
        sell_groups[fill.sell_trade_id].append(fill_drafts[id(fill)])
    for sell_trade_id, group in sell_groups.items():
        sell_trade = trade_by_id[sell_trade_id]
        fee_shares = allocate(_fee_total(sell_trade), [d.quantity_units for d in group])
        for draft, fee_share in zip(group, fee_shares, strict=True):
            draft.sell_fee_share = fee_share

    # closed 段 end = floor(q×C_s)；open 段 end = 标的级 floor(Σq×P1) 一次 allocate。
    for draft in drafts:
        if draft.kind == "closed":
            draft.end_value_units = draft.quantity_units * draft.end_price_units // QTY_PRICE_SCALE
    open_drafts = [d for d in drafts if d.kind == "open"]
    if open_drafts and p1_entry is not None:
        open_qty = sum(d.quantity_units for d in open_drafts)
        end_anchor = open_qty * p1_entry[1] // QTY_PRICE_SCALE
        ends = allocate(end_anchor, [d.quantity_units for d in open_drafts])
        for draft, end_value in zip(open_drafts, ends, strict=True):
            draft.end_value_units = end_value

    # 板块金额（逐段；指数缺口 → 整标的剔除 SECTOR_BAR_MISSING）。
    try:
        sector_amounts = [
            _sector_amount(d.base_units, d.base_ref_date, d.end_ref_date) for d in drafts
        ]
    except _BarMissingError:
        return _excluded(REASON_BAR, bucket)

    # 股息：标的区间净额（税后）按段 base 分摊（Σ=D；§3.4.1 现金制）。
    dividend_units = dividends.get(code, 0)
    dividend_shares = allocate(dividend_units, [d.base_units for d in drafts])

    segments = tuple(
        SegmentInput(
            security_code=code,
            kind=draft.kind,
            quantity_units=draft.quantity_units,
            base_units=draft.base_units,
            end_value_units=draft.end_value_units,
            gross_buy_units=draft.cost_share - draft.buy_fee_share if draft.is_new else None,
            gross_sell_units=(
                draft.proceeds_share + draft.sell_fee_share if draft.kind == "closed" else None
            ),
            fee_units=draft.buy_fee_share + draft.sell_fee_share,
            sector_amount_units=amount,
            dividend_units=share,
        )
        for draft, amount, share in zip(drafts, sector_amounts, dividend_shares, strict=True)
    )
    return SymbolPlan(
        security_code=code,
        symbol_class=klass,
        attributed=True,
        reason_code=None,
        segments=segments,
        begin_anchor_units=sum(d.base_units for d in drafts),
        end_anchor_units=sum(d.end_value_units for d in open_drafts),
        fees_units=sum(d.buy_fee_share + d.sell_fee_share for d in drafts),
        trades_amount_units=trades_amount,
        dividend_units=dividend_units,
        price_fallback=price_fallback,
        sector_fallback=sector_fallback,
        bucket_kind="none",
        in_universe=in_universe,
        sector_start_fallback=start_fallback,
    )


__all__ = [
    "REASON_ASSET_CLASS",
    "REASON_BAR",
    "REASON_CORPORATE",
    "REASON_DATA_ERROR",
    "REASON_FEE",
    "REASON_MAPPING",
    "REASON_PRICE",
    "PlanBundle",
    "SymbolPlan",
    "build_symbol_plans",
]
