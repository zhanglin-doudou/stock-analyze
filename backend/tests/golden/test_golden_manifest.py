"""golden 清单完整性（S5-3）：18 个注册指标 == golden 目录集合，且目录/文件归属一致。"""

from __future__ import annotations

import pytest
from stock_review.metrics.registry import build_registry

from golden.loader import GOLDEN_ROOT, load_all

pytestmark = pytest.mark.golden


def _golden_dirs() -> set[str]:
    return {
        path.parent.name
        for path in GOLDEN_ROOT.glob("**/golden*.json")
    }


def test_golden_dirs_cover_registry() -> None:
    registry_ids = set(build_registry().ids())
    golden_ids = _golden_dirs()
    assert golden_ids == registry_ids, (
        f"golden 目录与注册指标不一致：多 {sorted(golden_ids - registry_ids)} / "
        f"少 {sorted(registry_ids - golden_ids)}"
    )


def test_every_case_belongs_to_its_dir() -> None:
    for case in load_all():
        assert case.metric_id == case.path.parent.name, (
            f"{case.path}: metric_id={case.metric_id!r} ≠ 目录={case.path.parent.name!r}"
        )
