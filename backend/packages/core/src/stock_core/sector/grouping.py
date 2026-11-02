"""板块树聚合（行业 L3 → L2 → L1 的树上 rollup）。

硬约束：同一股票在**每个层级**最多出现一次；重复归属抛 `ValueError`，不静默去重。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SectorNode:
    """目录节点（`market.sector` 行的最小投影）。"""

    sector_code: str
    level: int
    parent_code: str | None = None


def resolve_level(
    assignments: Mapping[str, str],
    catalog: Mapping[str, SectorNode],
    level: int,
) -> dict[str, str]:
    """把「最深层归属」（通常 L3）解析为请求层级的板块代码。

    `assignments`: symbol -> 深层 sector_code；`catalog`: code -> 节点。
    目标层级必须 `<=` 节点自身层级（不能凭父码往下钻）；父链断裂抛 `ValueError`。
    """
    if level < 0:
        raise ValueError("level must be >= 0")
    resolved: dict[str, str] = {}
    for symbol, code in assignments.items():
        node = catalog.get(code)
        if node is None:
            raise ValueError(f"unknown sector_code: {code}")
        if node.level < level:
            raise ValueError(
                f"sector {code} level {node.level} cannot resolve to deeper level {level}"
            )
        current = node
        # 沿 parent_code 向上；每上层级减 1，直到命中请求层级。
        while current.level > level:
            if current.parent_code is None:
                raise ValueError(f"sector {code} has no parent to reach level {level}")
            parent = catalog.get(current.parent_code)
            if parent is None:
                raise ValueError(f"parent {current.parent_code} of {code} not in catalog")
            if parent.level != current.level - 1:
                raise ValueError(
                    f"parent {parent.sector_code} level {parent.level} is not "
                    f"{current.level - 1} for {current.sector_code}"
                )
            current = parent
        resolved[symbol] = current.sector_code
    return resolved


def sum_by_sector(
    assignments: Mapping[str, str],
    market_values: Mapping[str, int],
) -> dict[str, int]:
    """按板块代码汇总市值（同 scale 金额标度整数）。

    只有两边都存在的 symbol 才计入；缺市值（`None`/负数）由调用方过滤后传入。
    """
    totals: dict[str, int] = {}
    for symbol, code in assignments.items():
        value = market_values.get(symbol)
        if value is None:
            continue
        totals[code] = totals.get(code, 0) + value
    return totals
