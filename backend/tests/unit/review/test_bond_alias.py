"""可转债发债→上市代码名称归并纯函数单测（设计 2026-10-29 D3）。"""

from __future__ import annotations

import pytest
from stock_review.application.bond_alias import (
    build_bond_alias,
    canonical_code,
    extract_bond_stem,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("曙26发债", "曙26"),
        ("曙26转债", "曙26"),
        ("曙 26\n发债", "曙26"),  # 折行/空白归一
        ("贵州茅台", None),
        ("发债", None),  # 无 stem 的空壳名称
        ("", None),
    ],
)
def test_extract_bond_stem(name: str, expected: str | None) -> None:
    assert extract_bond_stem(name) == expected


def test_unique_mapping_aliased() -> None:
    alias, unresolved = build_bond_alias(
        [("754019", "曙26发债"), ("113050", "曙26转债"), ("600519", "贵州茅台")]
    )
    assert alias == {"754019": "113050"}
    assert unresolved == []


def test_conflict_not_aliased() -> None:
    """同一 stem 对应两只上市转债（历史重名）→ 不归并 + unresolved。"""
    alias, unresolved = build_bond_alias(
        [("754019", "曙26发债"), ("113050", "曙26转债"), ("123001", "曙26转债")]
    )
    assert alias == {}
    assert unresolved == ["754019"]


def test_no_listed_counterpart_not_aliased() -> None:
    """只有发债代码（尚未上市/账单里没有转债行）→ 不归并。"""
    alias, unresolved = build_bond_alias([("754019", "曙26发债")])
    assert alias == {}
    assert unresolved == ["754019"]


def test_listed_name_without_convertible_prefix_not_aliased() -> None:
    """名称以转债结尾但代码段不是可转债（如国债前缀）→ 不算上市对应。"""
    alias, unresolved = build_bond_alias([("754019", "曙26发债"), ("019547", "曙26转债")])
    assert alias == {}
    assert unresolved == ["754019"]


def test_non_bond_names_ignored() -> None:
    alias, unresolved = build_bond_alias([("600519", "贵州茅台"), ("511010", "国债ETF")])
    assert alias == {}
    assert unresolved == []


def test_canonical_code() -> None:
    assert canonical_code({"754019": "113050"}, "754019") == "113050"
    assert canonical_code({"754019": "113050"}, "600519") == "600519"
    assert canonical_code({}, "754019") == "754019"
