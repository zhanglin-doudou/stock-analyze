"""板块纯函数（零 I/O、零时钟、零随机）。

设计见 `docs/architecture/2026-10-31-sector-data.md` §6：

- 行业是**互斥分区**（Σw=1，HHI 标准口径）；概念是**可重叠标签**（Σexposure≥1，
  HHI_tag 只是辅助描述），两套函数不得混用；
- 所有函数输入/输出为标度整数（比率 scale=8），除法统一 `div_round`（HALF_UP）。
"""

from __future__ import annotations

from stock_core.sector.concentration import (
    effective_n_units,
    sector_hhi_from_values,
    topk_weights,
)
from stock_core.sector.concepts import ConceptExposure, concept_exposure
from stock_core.sector.grouping import SectorNode, resolve_level, sum_by_sector
from stock_core.sector.preference import deviation_units
from stock_core.sector.status import (
    STATUS_DEGRADED,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    coverage_status,
)
from stock_core.sector.weights import active_weights, normalize_weights, weights_from_values

__all__ = [
    "STATUS_DEGRADED",
    "STATUS_INSUFFICIENT",
    "STATUS_OK",
    "ConceptExposure",
    "SectorNode",
    "active_weights",
    "concept_exposure",
    "coverage_status",
    "deviation_units",
    "effective_n_units",
    "normalize_weights",
    "resolve_level",
    "sector_hhi_from_values",
    "sum_by_sector",
    "topk_weights",
    "weights_from_values",
]
