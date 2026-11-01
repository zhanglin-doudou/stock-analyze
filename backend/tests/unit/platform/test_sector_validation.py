"""板块同步快照校验（设计 §4.4，纯函数）。

锁定「漏返回不得写成剔除」的核心闸门：
* 同层代码/名称唯一、父级必须存在且恰为上一层、概念无父级；
* 行业当前成分同一 symbol 只能归属一个板块；
* 目录成份个数 vs 实际拉取行数（容差由调用方给出）。
"""

from __future__ import annotations

import pytest
from stock_platform.ports import SectorInfo
from stock_platform.sector_validation import (
    SectorValidationError,
    catalog_row_counts,
    validate_catalog,
    validate_member_totals,
    validate_unique_industry,
)


def _info(code: str, name: str, level: int, parent: str | None = None) -> SectorInfo:
    return SectorInfo(
        taxonomy="industry_sw" if level > 0 else "concept_em",
        sector_code=code,
        name=name,
        level=level,
        parent_code=parent,
        classification_version="SW2021" if level > 0 else "EM-2026-06",
    )


_CATALOG = (
    _info("801120.SI", "食品饮料", 1),
    _info("801050.SI", "银行", 1),
    _info("801123.SI", "饮料制造", 2, "801120.SI"),
    _info("801051.SI", "国有大型银行", 2, "801050.SI"),
    _info("850121.SI", "白酒", 3, "801123.SI"),
)


def test_validate_catalog_accepts_clean_tree() -> None:
    validate_catalog(_CATALOG, level=3)
    assert catalog_row_counts(_CATALOG) == {1: 2, 2: 2, 3: 1}


def test_validate_catalog_rejects_duplicate_code_in_same_level() -> None:
    with pytest.raises(SectorValidationError, match="重复"):
        validate_catalog([*_CATALOG, _info("801120.SI", "食品饮料2", 1)], level=1)


def test_validate_catalog_rejects_duplicate_name_in_same_level() -> None:
    with pytest.raises(SectorValidationError, match="名称重复"):
        validate_catalog([*_CATALOG, _info("801990.SI", "银行", 1)], level=1)


def test_validate_catalog_requires_existing_parent_of_previous_level() -> None:
    broken = (*_CATALOG, _info("850999.SI", "孤儿", 3, "801000.SI"))
    with pytest.raises(SectorValidationError, match="父级"):
        validate_catalog(broken, level=3)

    wrong_level = (*_CATALOG, _info("850998.SI", "越级", 3, "801120.SI"))
    with pytest.raises(SectorValidationError, match="不是 L2"):
        validate_catalog(wrong_level, level=3)


def test_validate_catalog_rejects_missing_parent() -> None:
    with pytest.raises(SectorValidationError, match="缺少父级"):
        validate_catalog([_info("850121.SI", "白酒", 3, None)], level=3)


def test_validate_catalog_concept_has_no_parent() -> None:
    concept = [_info("BK0999", "人工智能", 0), _info("BK1010", "新能源", 0)]
    validate_catalog(concept, level=0)
    with pytest.raises(SectorValidationError, match="不应有父级"):
        validate_catalog([_info("BK0999", "人工智能", 0, "X")], level=0)


def test_validate_unique_industry_rejects_double_membership() -> None:
    validate_unique_industry([("850121.SI", "600519"), ("850121.SI", "000858")])
    with pytest.raises(SectorValidationError, match="互斥"):
        validate_unique_industry([("850121.SI", "600519"), ("850511.SI", "600519")])


def test_validate_member_totals_rejects_missing_upstream_rows() -> None:
    """上游少返回一个板块 → 必须整批失败（不能被当作「剔除成分」写库）。"""
    with pytest.raises(SectorValidationError, match="未被拉取"):
        validate_member_totals(catalog_counts={"A": 10, "B": 5}, fetched_counts={"A": 10})


def test_validate_member_totals_tolerance_is_explicit() -> None:
    with pytest.raises(SectorValidationError, match="差异超过容差"):
        validate_member_totals(catalog_counts={"A": 10}, fetched_counts={"A": 9}, tolerance=0)
    # 已知缺口（如 L3 未分类）由调用方显式传入容差，而不是代码写死。
    validate_member_totals(catalog_counts={"A": 10}, fetched_counts={"A": 9}, tolerance=1)


def test_validate_member_totals_rejects_negative_tolerance() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        validate_member_totals(catalog_counts={}, fetched_counts={}, tolerance=-1)
