"""归因模型协议（EP-6 对齐；M2 `brinson_industry` 实现同一协议即可接入）。

实现形态修订（设计 D-13/C-5）：M1 不注册 `MetricDef`、不建 `MetricGroup`，
族不变式（Σ=total_pnl）由端点/装配层守门。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stock_core.attribution.types import AttributionInput, Decomposition


@runtime_checkable
class AttributionModel(Protocol):
    """归因模型协议：`model_id` / `definition_version` / `decompose`。"""

    model_id: str
    definition_version: str

    def decompose(self, data: AttributionInput) -> Decomposition: ...


__all__ = ["AttributionModel"]
