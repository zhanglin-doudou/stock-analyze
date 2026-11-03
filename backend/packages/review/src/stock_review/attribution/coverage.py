"""归因装配层：coverage 覆盖率与未归因清单（设计 §4.4）。

```text
capital_total      = Σ_{全部标的} base（无价标的用净成本兜底，base_basis="mixed"）
capital_attributed = Σ_{可归因标的} base
capital_ratio      = div_round(capital_attributed, capital_total, scale=8)   # total=0 → None
unattributed[]     = 最多 5 条 {security_code, symbol_class, reason_code, amount}（按 |amount| 降序）
```

- 可归因标的的 base = `Σ段 base`（两级分摊后，含区间内建仓 lot）；
  被剔除/不可归因标的的 base = 期初锚（有价按 P0 市值、无价按净成本兜底），
  由应用层镜像推演后经 `fallback_anchors` 传入（`segments._excluded` 落在
  `SymbolPlan.begin_anchor_units`）。
- 样本门禁常量 `MIN_SAMPLE_SEGMENTS = 10`（D-09；`sample.sample_status` 由应用层
  依据 `decomposition.n_attributed_segments` 判定）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stock_core.attribution import Decomposition
from stock_core.stats.numeric import div_round

from stock_review.attribution.segments import SymbolPlan

# 结论样本门禁（FR-R8 / D-09）：可归因段 ≥10 才允许给 summary。
MIN_SAMPLE_SEGMENTS = 10
# `unattributed[]` 条数上限（§4.4）。
UNATTRIBUTED_LIMIT = 5


@dataclass(frozen=True, slots=True)
class UnattributedEntry:
    """一条未归因标的披露（amount = 其残差桶金额，可为 0）。"""

    security_code: str
    symbol_class: str
    reason_code: str | None  # 资产类别排除为 None
    amount_units: int


@dataclass(frozen=True, slots=True)
class Coverage:
    """`GET /attribution` 的 `coverage` 块（§4.4 / §6.2）。"""

    capital_total_units: int
    capital_attributed_units: int
    capital_ratio_units: int | None  # scale=8；total==0 → None
    base_basis: str  # market | mixed
    segments_total: int
    segments_attributed: int
    symbols_total: int
    symbols_attributed: int
    unattributed: tuple[UnattributedEntry, ...]


def build_coverage(
    *,
    plans: Sequence[SymbolPlan],
    decomposition: Decomposition,
    per_symbol: Mapping[str, int],
    base_basis: str,
) -> Coverage:
    """汇总覆盖率（纯内存；`plans` 已含全部持仓/交易标的，见 `build_symbol_plans`）。"""
    capital_attributed = decomposition.base_capital_attributed_units
    # 被剔除标的的期初锚（fallback_anchors 经 _excluded 落在 begin_anchor_units）。
    capital_excluded = sum(plan.begin_anchor_units or 0 for plan in plans if not plan.attributed)
    capital_total = capital_attributed + capital_excluded
    capital_ratio = (
        div_round(capital_attributed, capital_total, scale=8) if capital_total != 0 else None
    )

    plan_by_code = {plan.security_code: plan for plan in plans if not plan.attributed}
    entries: list[UnattributedEntry] = []
    seen: set[str] = set()
    for code, plan in plan_by_code.items():
        seen.add(code)
        entries.append(
            UnattributedEntry(
                security_code=code,
                symbol_class=plan.symbol_class,
                reason_code=plan.reason_code,
                amount_units=per_symbol.get(code, 0),
            )
        )
    # 残差桶里存在、但不在计划内的标的（被动持有的不可归因标的等）也披露。
    for code, amount in per_symbol.items():
        if code in seen:
            continue
        entries.append(
            UnattributedEntry(
                security_code=code,
                symbol_class="pseudo",
                reason_code=None,
                amount_units=amount,
            )
        )
    entries.sort(key=lambda e: (-abs(e.amount_units), e.security_code))
    return Coverage(
        capital_total_units=capital_total,
        capital_attributed_units=capital_attributed,
        capital_ratio_units=capital_ratio,
        base_basis=base_basis,
        segments_total=decomposition.n_segments,
        segments_attributed=decomposition.n_attributed_segments,
        symbols_total=len(plans),
        symbols_attributed=sum(1 for plan in plans if plan.attributed),
        unattributed=tuple(entries[:UNATTRIBUTED_LIMIT]),
    )


__all__ = [
    "MIN_SAMPLE_SEGMENTS",
    "UNATTRIBUTED_LIMIT",
    "Coverage",
    "UnattributedEntry",
    "build_coverage",
]
