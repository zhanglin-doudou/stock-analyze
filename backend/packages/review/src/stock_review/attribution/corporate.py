"""公司行动识别（设计 D-12：区间内送转/配股 → 整标的剔除）。

M1 不做复权/数量调整：某标的在 `[T0, T1]` 内存在任一 `bonus`（送转）/`rights`（配股）
事件（含影响区间前 lot 的送转/配股）→ 该标的不进五因子，其区间损益进残差桶
`corporate_action_pnl`，`missing[]` 标 `CORPORATE_ACTION_ADJUSTMENT_MISSING`。
P2 在引擎暴露 lot 级数量事件后再做数量调整（设计 §10.6）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date

from stock_ledger.models import Trade

from stock_review.application.rights import RightsResolution

_CORPORATE_CATEGORIES = frozenset({"bonus", "rights"})


def corporate_action_symbols(
    trades: Iterable[Trade],
    *,
    t0: date,
    t1: date,
    canonical: Callable[[str], str],
    rights: RightsResolution | None = None,
) -> dict[str, str]:
    """窗口内有送转/配股事件的标的集合：`canonical_code -> 事件摘要`。

    `canonical` 为可转债发债代码归一函数（`bond_alias.canonical_code` 的偏函数）。
    `rights` 为配股有效分类：零金额且无持仓的配股已降级为券商行为，不算公司行动。
    摘要形如 `送转/配股`，供 `missing[]` reason 使用。
    """
    rights = rights or RightsResolution(frozenset())
    hits: dict[str, str] = {}
    for trade in trades:
        if not (t0 <= trade.trade_date <= t1):
            continue
        kind = (rights.category(trade) or "").strip().lower()
        if kind not in _CORPORATE_CATEGORIES:
            continue
        code = canonical(trade.security_code)
        if not code:
            continue
        label = "送转" if kind == "bonus" else "配股"
        hits.setdefault(code, label)
    return hits


__all__ = ["corporate_action_symbols"]
