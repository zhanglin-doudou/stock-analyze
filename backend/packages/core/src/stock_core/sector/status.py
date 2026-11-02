"""覆盖率状态判定（阈值由调用方传入，默认取 `stock_core.metrics.policy` 常量）。

口径（docs/architecture/2026-10-31-sector-data.md §6.4）：覆盖率（scale=8）
≥ `SECTOR_COVERAGE_OK_MIN_UNITS` → `ok`；≥ `SECTOR_COVERAGE_DEGRADED_MIN_UNITS`
→ `degraded`；否则 `insufficient`。
"""

from __future__ import annotations

from stock_core.metrics.policy import (
    SECTOR_COVERAGE_DEGRADED_MIN_UNITS,
    SECTOR_COVERAGE_OK_MIN_UNITS,
)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_INSUFFICIENT = "insufficient"


def coverage_status(
    coverage_units: int,
    *,
    ok_min_units: int = SECTOR_COVERAGE_OK_MIN_UNITS,
    degraded_min_units: int = SECTOR_COVERAGE_DEGRADED_MIN_UNITS,
) -> str:
    """按覆盖率标度整数给出统计轴状态。"""
    if coverage_units >= ok_min_units:
        return STATUS_OK
    if coverage_units >= degraded_min_units:
        return STATUS_DEGRADED
    return STATUS_INSUFFICIENT


__all__ = [
    "STATUS_DEGRADED",
    "STATUS_INSUFFICIENT",
    "STATUS_OK",
    "coverage_status",
]
