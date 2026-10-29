"""FIFO 回合引擎（Period2-01，INTERFACE §2.2；PRD FR-R2.3）。

纯函数 `build_rounds(events) -> FifoResult`：按稳定序配对买卖、按除息日归属股息、
处理送转/配股、超卖记 data_error（绝不产生负持仓）。

回合定义（仓位周期）
--------------------
一个回合 = 某证券从 0 股持仓**首次买入**开始，历经可能的分批买入、
做T式部分卖出、再买回……直到**持仓重新回到 0 股**为止的完整周期。
中途的部分卖出只是消耗 lot（产生 Fill），但不关闭回合；只有当所有 lot 清空时
才产出 `status="closed"` 的回合。期末未归零的持仓产出 `status="open"` 的回合。

关键约定
--------
* 稳定序 = `(trade_date, line_no, summary, quantity_units, price_units, trade_id)`；
  排序后按 `security_code` 分组，不同证券互不影响。
* 整数分摊尾差一律归**最后一个** chunk/lot，保证 `Σ chunk == 事件金额` 精确成立。
* 送转只放大数量、总成本不变；配股缴款计入成本、不新增回合。
* 所有量为标度整数；本模块零 I/O、零 wall-clock、无全局态。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from stock_core.fifo.calendar import business_days_between
from stock_core.fifo.types import (
    DataError,
    EventKind,
    FifoEvent,
    FifoResult,
    Fill,
    OpenLot,
    Round,
)
from stock_core.values import Ratio

_RATIO_SCALE = 100_000_000


@dataclass(slots=True)
class _Lot:
    """引擎内部可变 lot（不对外暴露；不变量：quantity_units > 0，cost >= 0）。"""

    buy_trade_id: str
    security_name: str
    open_date: date
    quantity_units: int
    cost_units: int
    dividend_units: int = 0


@dataclass(slots=True)
class _RoundState:
    """引擎内部一个进行中的仓位周期（从 0→非0 开始，到 非0→0 结束）。"""

    security_code: str
    security_name: str  # 周期开启时第一个 BUY 的名称
    open_trade_id: str
    open_date: date
    fills: list[Fill] = field(default_factory=list)
    last_sell_trade_id: str | None = None
    last_sell_date: date | None = None
    last_sell_security_name: str | None = None
    # 聚合缓存（每次追加 Fill 时累加，避免 snapshot 时重算）。
    matched_units: int = 0
    cost_units: int = 0
    proceeds_units: int = 0
    dividend_units: int = 0
    oversell_units: int = 0


def build_rounds(events: Iterable[FifoEvent]) -> FifoResult:
    """把事件序列构建为 FIFO 回合、成交明细、未平仓与数据异常（确定性、纯函数）。"""
    ordered = sorted(events, key=lambda event: event.stable_key)
    grouped: dict[str, list[FifoEvent]] = {}
    for event in ordered:
        grouped.setdefault(event.security_code, []).append(event)

    closed_rounds: list[Round] = []
    open_rounds: list[Round] = []
    open_lots: list[OpenLot] = []
    data_errors: list[DataError] = []
    for security_code in sorted(grouped):
        closed, opens, lots, errors = _process_security(security_code, grouped[security_code])
        closed_rounds.extend(closed)
        open_rounds.extend(opens)
        open_lots.extend(lots)
        data_errors.extend(errors)

    # 回合排序：closed（含 data_error）按 (close_date, key)，open 按 (open_date, key)，closed 在前。
    sorted_closed = sorted(
        closed_rounds, key=lambda round_: (round_.close_date or date.min, round_.key)
    )
    sorted_open = sorted(open_rounds, key=lambda round_: (round_.open_date, round_.key))
    rounds = tuple(sorted_closed) + tuple(sorted_open)
    fills = tuple(fill for round_ in rounds for fill in round_.fills)
    return FifoResult(
        rounds=rounds,
        fills=fills,
        open_lots=tuple(open_lots),
        data_errors=tuple(data_errors),
    )


def _process_security(
    security_code: str, events: Sequence[FifoEvent]
) -> tuple[list[Round], list[Round], list[OpenLot], list[DataError]]:
    """处理单一证券的事件流（事件已按稳定序排列）。

    回合边界：持仓 0→非0 开启，非0→0 关闭；中途做T式 SELL 只追加 Fill 不关闭。
    """
    lots: list[_Lot] = []
    closed: list[Round] = []
    errors: list[DataError] = []
    current: _RoundState | None = None

    for event in events:
        if event.kind is EventKind.BUY:
            if event.quantity_units <= 0:
                errors.append(_invalid_event(event, security_code, detail="买入数量必须为正"))
                continue
            if current is None:
                # 0→非0：开启新周期
                current = _RoundState(
                    security_code=security_code,
                    security_name=event.security_name,
                    open_trade_id=event.trade_id,
                    open_date=event.trade_date,
                )
            lots.append(
                _Lot(
                    buy_trade_id=event.trade_id,
                    security_name=event.security_name,
                    open_date=event.trade_date,
                    quantity_units=event.quantity_units,
                    cost_units=abs(event.amount_units),
                )
            )
        elif event.kind is EventKind.SELL:
            if event.quantity_units <= 0:
                errors.append(_invalid_event(event, security_code, detail="卖出数量必须为正"))
                continue
            fills, oversell_units = _apply_sell(event, security_code, lots, errors)
            # 无可配对 lot（持 0 股卖出等）：跳过 fill 追加，lot 状态不变
            if fills is None:
                continue
            # 有配对 Fill → current 必定存在（BUY 先建 lot，lot 消耗产生 fill）
            assert current is not None, "fills 非空意味着 lot 被消耗，lot 来自 BUY → current 必存在"
            # 超卖存在：累积到当前周期（正常配对路径也可能有部分超卖）
            if oversell_units > 0:
                current.oversell_units += oversell_units
            # 正常配对：追加 fills 到当前周期（sequence 全局递增）
            sequence_base = len(current.fills)
            for offset, fill in enumerate(fills, start=1):
                current.fills.append(
                    Fill(
                        buy_trade_id=fill.buy_trade_id,
                        sell_trade_id=fill.sell_trade_id,
                        sequence=sequence_base + offset,
                        quantity_units=fill.quantity_units,
                        buy_cost_units=fill.buy_cost_units,
                        sell_proceeds_units=fill.sell_proceeds_units,
                        dividend_units=fill.dividend_units,
                        pnl_units=fill.pnl_units,
                        open_date=fill.open_date,
                        close_date=fill.close_date,
                        holding_days=fill.holding_days,
                    )
                )
            current.last_sell_trade_id = event.trade_id
            current.last_sell_date = event.trade_date
            if event.security_name:
                current.last_sell_security_name = event.security_name
            # 累加聚合
            current.matched_units += sum(f.quantity_units for f in fills)
            current.cost_units += sum(f.buy_cost_units for f in fills)
            current.proceeds_units += sum(f.sell_proceeds_units for f in fills)
            current.dividend_units += sum(f.dividend_units for f in fills)

            # 持仓归零：关闭周期
            if not lots:
                closed.append(
                    _snapshot_closed(
                        current,
                        oversell_units=current.oversell_units,
                        data_error=current.oversell_units > 0,
                    )
                )
                current = None
        elif event.kind is EventKind.DIVIDEND:
            _apply_dividend(event, lots)
        elif event.kind is EventKind.BONUS:
            _apply_bonus(event, security_code, lots, errors)
        elif event.kind is EventKind.RIGHTS:
            _apply_rights(event, security_code, lots, errors)
        # 其余事件（interest/fee/transfer/new_stock/other）只在 bridge 中使用，不参与配对。

    # 期末仍有 lot → 周期 open（含中间做T的 fills）
    open_rounds: list[Round] = []
    if current is not None and lots:
        open_rounds.append(_snapshot_open(current, lots))
    # OpenLot：期末仍持有的 lot 列表（与 open round 同属一个 0→0 周期）
    open_lots = [_to_open_lot(security_code, lot) for lot in lots]
    return closed, open_rounds, open_lots, errors


def _apply_sell(
    event: FifoEvent,
    security_code: str,
    lots: list[_Lot],
    errors: list[DataError],
) -> tuple[list[Fill] | None, int]:
    """FIFO 消耗 lot，返回 (fills, oversell_units)；fills=None 表示无可配对 lot。

    上层已保证 quantity_units > 0，此函数只处理 lot 消耗逻辑。
    """
    # FIFO 取可用 lot；超出部分只记 data_error，绝不产生负持仓。
    # 货基（allow_oversell）：红利发放行被隔离未入账，赎回份额略多于申购是常态，不记错。
    remaining = event.quantity_units
    chunks: list[tuple[_Lot, int]] = []
    for lot in lots:
        if remaining == 0:
            break
        take = min(remaining, lot.quantity_units)
        chunks.append((lot, take))
        remaining -= take
    oversell_units = remaining
    if not chunks:
        if event.allow_oversell:
            # 货基全额超卖：账单期前本金赎回，无成本信息——静默跳过，不记错、不计损益。
            return None, 0
        errors.append(
            DataError(
                trade_id=event.trade_id,
                security_code=security_code,
                trade_date=event.trade_date,
                kind="oversell",
                quantity_units=oversell_units,
                detail="卖出数量超过可用持仓，无可配对买入",
            )
        )
        return None, oversell_units

    matched_units = sum(take for _, take in chunks)
    # 卖出现金按各 chunk 数量比例分摊，尾差归最后一个 chunk。
    if oversell_units:
        proceeds_total = event.amount_units * matched_units // event.quantity_units
    else:
        proceeds_total = event.amount_units
    proceeds_shares = _allocate(proceeds_total, [take for _, take in chunks])
    fills: list[Fill] = []
    for sequence, ((lot, take), proceeds_units) in enumerate(
        zip(chunks, proceeds_shares, strict=True), start=1
    ):
        if take == lot.quantity_units:
            buy_cost_units = lot.cost_units
            dividend_units = lot.dividend_units
        else:
            buy_cost_units = lot.cost_units * take // lot.quantity_units
            dividend_units = lot.dividend_units * take // lot.quantity_units
        lot.quantity_units -= take
        lot.cost_units -= buy_cost_units
        lot.dividend_units -= dividend_units
        holding_days = business_days_between(lot.open_date, event.trade_date)
        fills.append(
            Fill(
                buy_trade_id=lot.buy_trade_id,
                sell_trade_id=event.trade_id,
                sequence=sequence,
                quantity_units=take,
                buy_cost_units=buy_cost_units,
                sell_proceeds_units=proceeds_units,
                dividend_units=dividend_units,
                pnl_units=proceeds_units - buy_cost_units + dividend_units,
                open_date=lot.open_date,
                close_date=event.trade_date,
                holding_days=holding_days,
            )
        )

    if oversell_units:
        if event.allow_oversell:
            # 货基部分超卖：超出份额 = 被隔离的红利再投利息，零成本——卖款全额计为收益。
            interest_proceeds = event.amount_units - proceeds_total
            fills.append(
                Fill(
                    buy_trade_id="",
                    sell_trade_id=event.trade_id,
                    sequence=len(fills) + 1,
                    quantity_units=oversell_units,
                    buy_cost_units=0,
                    sell_proceeds_units=interest_proceeds,
                    dividend_units=0,
                    pnl_units=interest_proceeds,
                    open_date=event.trade_date,
                    close_date=event.trade_date,
                    holding_days=0,
                )
            )
            oversell_units = 0
        else:
            errors.append(
                DataError(
                    trade_id=event.trade_id,
                    security_code=security_code,
                    trade_date=event.trade_date,
                    kind="oversell",
                    quantity_units=oversell_units,
                    detail="卖出数量超过可用持仓，超出部分未配对",
                )
            )
    lots[:] = [lot for lot in lots if lot.quantity_units > 0]
    return fills, oversell_units


def _round_display_name(state: _RoundState) -> str:
    """回合展示名：最后一次 SELL 的名称优先，回落周期开启时的 BUY 名称。"""
    if state.last_sell_security_name:
        return state.last_sell_security_name
    return state.security_name


def _snapshot_closed(state: _RoundState, *, oversell_units: int, data_error: bool) -> Round:
    """把一个刚归零的周期状态 snapshot 为 closed Round。"""
    weighted_days = sum(f.holding_days * f.quantity_units for f in state.fills)
    holding_days = (
        max((weighted_days + state.matched_units // 2) // state.matched_units, 1)
        if state.matched_units
        else 1
    )
    pnl_units = state.proceeds_units - state.cost_units + state.dividend_units
    close_trade_id = state.last_sell_trade_id
    close_date = state.last_sell_date
    key = f"{state.open_trade_id}:{close_trade_id or 'close'}"
    return Round(
        key=key,
        security_code=state.security_code,
        security_name=_round_display_name(state),
        status="data_error" if data_error else "closed",
        open_trade_id=state.open_trade_id,
        close_trade_id=close_trade_id,
        open_date=state.open_date,
        close_date=close_date,
        quantity_units=state.matched_units,
        cost_units=state.cost_units,
        proceeds_units=state.proceeds_units,
        dividend_units=state.dividend_units,
        pnl_units=pnl_units,
        return_rate_units=_return_rate_units(pnl_units, state.cost_units),
        holding_days=holding_days,
        data_error=data_error,
        oversell_units=oversell_units,
        fills=tuple(state.fills),
    )


def _snapshot_open(state: _RoundState, lots: list[_Lot]) -> Round:
    """把期末仍在进行的周期 snapshot 为 open Round（含累计 fills）。

    恒等式不变量：pnl = proceeds - cost + dividend（open / closed 通用）。
    - quantity_units 取 matched_units（已 FIFO 配对的股数）；
    - cost_units = 已消耗 lot 成本（剩余 lot 成本在 open_lots 独立展示，避免重复）；
    - dividend_units = 已消耗 lot 股息（剩余 lot 股息也在 open_lots 独立展示）。
    """
    pnl_units = state.proceeds_units - state.cost_units + state.dividend_units
    key = f"{state.open_trade_id}:open"
    return Round(
        key=key,
        security_code=state.security_code,
        security_name=_round_display_name(state),
        status="open",
        open_trade_id=state.open_trade_id,
        close_trade_id=None,
        open_date=state.open_date,
        close_date=None,
        quantity_units=state.matched_units,
        cost_units=state.cost_units,
        proceeds_units=state.proceeds_units,
        dividend_units=state.dividend_units,
        pnl_units=pnl_units,
        return_rate_units=_return_rate_units(pnl_units, state.cost_units or 1),
        holding_days=0,
        data_error=False,
        oversell_units=0,
        fills=tuple(state.fills),
    )


def _apply_dividend(event: FifoEvent, lots: list[_Lot]) -> None:
    """股息按除息日各 open lot 持股数量比例分配；无持仓不分配，且不改成本。"""
    if not lots:
        return
    net_units = event.amount_units - event.dividend_tax_units
    shares = _allocate(net_units, [lot.quantity_units for lot in lots])
    for lot, share in zip(lots, shares, strict=True):
        lot.dividend_units += share


def _apply_bonus(
    event: FifoEvent,
    security_code: str,
    lots: list[_Lot],
    errors: list[DataError],
) -> None:
    """送转：按比例放大数量、总成本不变。"""
    total_quantity = sum(lot.quantity_units for lot in lots)
    if event.ratio_units > 0:
        added_units = (total_quantity * event.ratio_units + _RATIO_SCALE // 2) // _RATIO_SCALE
    else:
        added_units = event.quantity_units
    if not lots:
        if added_units > 0:
            errors.append(_invalid_event(event, security_code, detail="送转事件无可用持仓"))
        return
    if added_units <= 0:
        return
    shares = _allocate(added_units, [lot.quantity_units for lot in lots])
    for lot, share in zip(lots, shares, strict=True):
        lot.quantity_units += share


def _apply_rights(
    event: FifoEvent,
    security_code: str,
    lots: list[_Lot],
    errors: list[DataError],
) -> None:
    """配股缴款：按当前 open lot 持股比例增加数量与成本（不新增回合）。"""
    added_units = event.quantity_units
    if added_units <= 0:
        errors.append(_invalid_event(event, security_code, detail="配股数量必须为正"))
        return
    if not lots:
        errors.append(
            DataError(
                trade_id=event.trade_id,
                security_code=security_code,
                trade_date=event.trade_date,
                kind="unmatched_rights",
                quantity_units=added_units,
                detail="配股缴款无对应持仓",
            )
        )
        return
    weights = [lot.quantity_units for lot in lots]
    quantity_shares = _allocate(added_units, weights)
    payment_shares = _allocate(abs(event.amount_units), weights)
    for lot, quantity_share, payment_share in zip(
        lots, quantity_shares, payment_shares, strict=True
    ):
        lot.quantity_units += quantity_share
        lot.cost_units += payment_share


def _to_open_lot(security_code: str, lot: _Lot) -> OpenLot:
    return OpenLot(
        security_code=security_code,
        buy_trade_id=lot.buy_trade_id,
        open_date=lot.open_date,
        quantity_units=lot.quantity_units,
        cost_units=lot.cost_units,
        dividend_units=lot.dividend_units,
    )


def _allocate(total: int, weights: Sequence[int]) -> list[int]:
    """按权重整数分摊，尾差归最后一个元素，保证 `Σ result == total` 精确成立。

    调用方保证 `weights` 非空且权重和为正（open lot 数量恒 > 0）。
    """
    total_weight = sum(weights)
    shares: list[int] = []
    allocated = 0
    for weight in weights[:-1]:
        share = total * weight // total_weight
        shares.append(share)
        allocated += share
    shares.append(total - allocated)
    return shares


def _return_rate_units(pnl_units: int, cost_units: int) -> int:
    """收益率 = pnl / cost（Ratio 1e-8，四舍五入）；cost=0 → 0。"""
    if cost_units == 0:
        return 0
    return Ratio.from_decimal(Decimal(pnl_units) / Decimal(cost_units)).units


def _invalid_event(event: FifoEvent, security_code: str, *, detail: str) -> DataError:
    return DataError(
        trade_id=event.trade_id,
        security_code=security_code,
        trade_date=event.trade_date,
        kind="invalid_event",
        quantity_units=event.quantity_units,
        detail=detail,
    )
