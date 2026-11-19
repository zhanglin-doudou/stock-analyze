"""配股前置校验：无持仓的零金额配股按「券商行为」处理（读时归一，纯内存）。

口径：
- `category=rights`、`amount_units == 0`，且该证券在该笔之前**没有正持仓** → 这不是真实
  的配股缴款，只是券商登记/入账动作，按 `broker_action` 处理：不计持仓、不计 FIFO、
  不产生回合。零金额意味着现金侧本来就没有变动，因此不改现金、不留对账缺口。
- 有持仓的配股照旧按 `rights` 处理（加股、成本不变）。
- `amount_units != 0` 的配股一律照旧按 `rights` 处理，即使无持仓也不改判——仍由引擎
  报 `unmatched_rights` 数据异常，保留人工核对入口。

判定顺序与 FIFO 引擎**同一个稳定序**（`FifoEvent.stable_key`），按事件流推进运行中的
持仓量，不看未来持仓；同日多笔由 `(line_no, sort_rank, summary, 数量, 价格, trade_id)`
决定先后，与引擎完全一致。

所有消费方（回合、资产表、净值、归因、指标）都读本模块的 `RightsResolution`，
避免各层各自过滤导致口径分叉。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from stock_core.fifo import EventKind, FifoEvent

from stock_review.application.valuation import category as trade_category

_BROKER_ACTION = "broker_action"


@dataclass(frozen=True, slots=True)
class RightsResolution:
    """一笔配股流水是否被降级为「券商行为」的判定结果。"""

    demoted: frozenset[str]
    """被降级的 `trade.id` 集合（零金额且无前置持仓的配股）。"""

    def category(self, trade: object) -> str:
        """该流水的有效分类：降级的配股返回 `broker_action`，其余按原分类。"""
        if getattr(trade, "id", None) in self.demoted:
            return _BROKER_ACTION
        return trade_category(trade)  # type: ignore[arg-type]


def zero_amount_rights_codes(trades: Iterable[object]) -> frozenset[str]:
    """窗口内可能被降级的代码集（零金额配股涉及的 `security_code`）。

    只有零金额配股会改判，且持仓判定按代码独立推进，因此读时只需取这些代码的历史；
    集合为空时调用方可以完全不发查询（早退）。
    """
    codes: set[str] = set()
    for trade in trades:
        code = getattr(trade, "security_code", None)
        if not code:
            continue
        if (
            getattr(trade, "amount_units", 0) == 0 and trade_category(trade) == "rights"  # type: ignore[arg-type]
        ):
            codes.add(code)
    return frozenset(codes)


def resolve_rights(events: Iterable[FifoEvent]) -> RightsResolution:
    """按引擎稳定序扫描事件，返回需要降级的配股流水 ID。

    只依赖事件流本身（纯内存、无 I/O、无 wall-clock），对同一批事件的任意输入顺序
    结果一致（内部按 `stable_key` 排序）。
    """
    held: dict[str, int] = defaultdict(int)
    demoted: set[str] = set()
    for event in sorted(events, key=lambda item: item.stable_key):
        code = event.security_code
        if event.kind == EventKind.RIGHTS:
            # 零金额 + 无前置持仓：券商登记动作，不加股、不建仓。
            if event.amount_units == 0 and held[code] <= 0:
                demoted.add(event.trade_id)
                continue
            held[code] += event.quantity_units
        elif event.kind in (EventKind.BUY, EventKind.BONUS):
            held[code] += event.quantity_units
        elif event.kind == EventKind.NEW_STOCK:
            # 零金额新股登记不建仓（与 `valuation.holding_delta` 同口径）。
            if event.amount_units != 0:
                held[code] += event.quantity_units
        elif event.kind == EventKind.SELL:
            # 超卖部分按 0 截断，不产生负持仓（与资产表一致）。
            held[code] = max(0, held[code] - event.quantity_units)
    return RightsResolution(demoted=frozenset(demoted))


__all__ = ["RightsResolution", "resolve_rights", "zero_amount_rights_codes"]
