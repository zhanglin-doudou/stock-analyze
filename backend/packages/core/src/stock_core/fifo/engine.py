"""FIFO 回合引擎（Period2-01，INTERFACE §2.2；PRD FR-R2.3）。

纯函数 `build_rounds(events) -> FifoResult`：按稳定序配对买卖、按除息日归属股息、
处理送转/配股、超卖记 data_error（绝不产生负持仓）。

关键约定
--------
* 稳定序 = `(trade_date, line_no, summary, quantity_units, price_units, trade_id)`；
  排序后按 `security_code` 分组，不同证券互不影响。
* 每个 SELL 事件生成一个 closed 回合（多批次买入可产生多个 Fill）。
* 整数分摊尾差一律归**最后一个** chunk/lot，保证 `Σ chunk == 事件金额` 精确成立。
* 送转只放大数量、总成本不变；配股缴款计入成本、不新增回合。
* 所有量为标度整数；本模块零 I/O、零 wall-clock、无全局态。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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


def build_rounds(events: Iterable[FifoEvent]) -> FifoResult:
    """把事件序列构建为 FIFO 回合、成交明细、未平仓与数据异常（确定性、纯函数）。"""
    ordered = sorted(events, key=lambda event: event.stable_key)
    grouped: dict[str, list[FifoEvent]] = {}
    for event in ordered:
        grouped.setdefault(event.security_code, []).append(event)

    closed_rounds: list[Round] = []
    open_pairs: list[tuple[Round, OpenLot]] = []
    data_errors: list[DataError] = []
    for security_code in sorted(grouped):
        closed, opens, errors = _process_security(security_code, grouped[security_code])
        closed_rounds.extend(closed)
        open_pairs.extend(opens)
        data_errors.extend(errors)

    # 回合排序：closed（含 data_error）按 (close_date, key)，open 按 (open_date, key)，closed 在前。
    sorted_closed = sorted(
        closed_rounds, key=lambda round_: (round_.close_date or date.min, round_.key)
    )
    sorted_open_pairs = sorted(open_pairs, key=lambda pair: (pair[0].open_date, pair[0].key))
    rounds = tuple(sorted_closed) + tuple(pair[0] for pair in sorted_open_pairs)
    fills = tuple(fill for round_ in rounds for fill in round_.fills)
    return FifoResult(
        rounds=rounds,
        fills=fills,
        open_lots=tuple(pair[1] for pair in sorted_open_pairs),
        data_errors=tuple(data_errors),
    )


def _process_security(
    security_code: str, events: Sequence[FifoEvent]
) -> tuple[list[Round], list[tuple[Round, OpenLot]], list[DataError]]:
    """处理单一证券的事件流（事件已按稳定序排列）。"""
    lots: list[_Lot] = []
    closed: list[Round] = []
    errors: list[DataError] = []

    for event in events:
        if event.kind is EventKind.BUY:
            _apply_buy(event, security_code, lots, errors)
        elif event.kind is EventKind.SELL:
            _apply_sell(event, security_code, lots, closed, errors)
        elif event.kind is EventKind.DIVIDEND:
            _apply_dividend(event, lots)
        elif event.kind is EventKind.BONUS:
            _apply_bonus(event, security_code, lots, errors)
        elif event.kind is EventKind.RIGHTS:
            _apply_rights(event, security_code, lots, errors)
        # 其余事件（interest/fee/transfer/new_stock/other）只在 bridge 中使用，不参与配对。

    open_pairs = [
        (_to_open_round(security_code, lot), _to_open_lot(security_code, lot)) for lot in lots
    ]
    return closed, open_pairs, errors


def _apply_buy(
    event: FifoEvent, security_code: str, lots: list[_Lot], errors: list[DataError]
) -> None:
    if event.quantity_units <= 0:
        errors.append(_invalid_event(event, security_code, detail="买入数量必须为正"))
        return
    lots.append(
        _Lot(
            buy_trade_id=event.trade_id,
            security_name=event.security_name,
            open_date=event.trade_date,
            quantity_units=event.quantity_units,
            cost_units=abs(event.amount_units),
        )
    )


def _apply_sell(
    event: FifoEvent,
    security_code: str,
    lots: list[_Lot],
    closed: list[Round],
    errors: list[DataError],
) -> None:
    if event.quantity_units <= 0:
        errors.append(_invalid_event(event, security_code, detail="卖出数量必须为正"))
        return

    # FIFO 取可用 lot；超出部分只记 data_error，绝不产生负持仓。
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
        return

    matched_units = sum(take for _, take in chunks)
    # 卖出现金按各 chunk 数量比例分摊，尾差归最后一个 chunk。
    # 正常配对时 Σ chunk == 事件金额；超卖时只把可配对数量对应的现金计入回合
    # （超出部分的现金随 oversell 一起进 data_error，不虚增 pnl）。
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

    cost_units = sum(fill.buy_cost_units for fill in fills)
    proceeds_units_total = sum(fill.sell_proceeds_units for fill in fills)
    dividend_units_total = sum(fill.dividend_units for fill in fills)
    # 数量加权平均持仓天数（整数四舍五入，最小 1）。
    weighted_days = sum(fill.holding_days * fill.quantity_units for fill in fills)
    holding_days = max((weighted_days + matched_units // 2) // matched_units, 1)
    first_lot = chunks[0][0]
    pnl_units = proceeds_units_total - cost_units + dividend_units_total
    closed.append(
        Round(
            key=f"{first_lot.buy_trade_id}:{event.trade_id}",
            security_code=security_code,
            security_name=event.security_name or first_lot.security_name,
            status="data_error" if oversell_units else "closed",
            open_trade_id=first_lot.buy_trade_id,
            close_trade_id=event.trade_id,
            open_date=first_lot.open_date,
            close_date=event.trade_date,
            quantity_units=matched_units,
            cost_units=cost_units,
            proceeds_units=proceeds_units_total,
            dividend_units=dividend_units_total,
            pnl_units=pnl_units,
            return_rate_units=_return_rate_units(pnl_units, cost_units),
            holding_days=holding_days,
            data_error=oversell_units > 0,
            oversell_units=oversell_units,
            fills=tuple(fills),
        )
    )
    if oversell_units:
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


def _to_open_round(security_code: str, lot: _Lot) -> Round:
    return Round(
        key=f"{lot.buy_trade_id}:open",
        security_code=security_code,
        security_name=lot.security_name,
        status="open",
        open_trade_id=lot.buy_trade_id,
        close_trade_id=None,
        open_date=lot.open_date,
        close_date=None,
        quantity_units=lot.quantity_units,
        cost_units=lot.cost_units,
        proceeds_units=0,
        dividend_units=lot.dividend_units,
        pnl_units=lot.dividend_units,
        return_rate_units=_return_rate_units(lot.dividend_units, lot.cost_units),
        holding_days=0,
        data_error=False,
        oversell_units=0,
        fills=(),
    )


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
