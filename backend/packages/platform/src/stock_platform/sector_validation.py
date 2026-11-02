"""板块同步的纯校验（`make sync-sectors` 的快照闸门，§4.4）。

为什么单独抽纯函数：同步一旦「漏数据」被写成「剔除成分」，旧快照会被永久污染。
所有校验必须在写库前完成，失败即整批放弃（旧快照不动）。本模块零 I/O，便于单测。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from stock_platform.ports import SectorInfo


class SectorValidationError(ValueError):
    """快照校验失败（整批失败，不允许半截写入）。"""


def validate_catalog(catalog: Sequence[SectorInfo], *, level: int) -> None:
    """同一层级内：代码唯一、名称唯一、父级必须存在且恰为上一层。"""
    rows = [row for row in catalog if row.level == level]
    codes = [row.sector_code for row in rows]
    if len(codes) != len(set(codes)):
        raise SectorValidationError(f"L{level} 板块代码重复")
    names = [row.name for row in rows]
    if len(names) != len(set(names)):
        raise SectorValidationError(f"L{level} 板块名称重复")

    by_code = {row.sector_code: row for row in catalog}
    for row in rows:
        if level <= 1:
            # 概念（L0）与申万一级行业（L1）都是顶层，无父级。
            if row.parent_code is not None:
                raise SectorValidationError(f"L{level} 板块 {row.sector_code} 不应有父级")
            continue
        if row.parent_code is None:
            raise SectorValidationError(f"L{level} 板块 {row.sector_code} 缺少父级")
        parent = by_code.get(row.parent_code)
        if parent is None:
            raise SectorValidationError(
                f"L{level} 板块 {row.sector_code} 的父级 {row.parent_code} 不存在"
            )
        if parent.level != level - 1:
            raise SectorValidationError(
                f"L{level} 板块 {row.sector_code} 的父级层级 {parent.level} 不是 L{level - 1}"
            )


def validate_unique_industry(members: Sequence[tuple[str, str]]) -> None:
    """行业当前唯一：同一 symbol 不得同时归属两个 L3 板块。"""
    seen: dict[str, str] = {}
    for sector_code, symbol in members:
        previous = seen.get(symbol)
        if previous is not None and previous != sector_code:
            raise SectorValidationError(
                f"symbol {symbol} 同时归属 {previous} 与 {sector_code}（行业必须互斥）"
            )
        seen[symbol] = sector_code


def validate_member_totals(
    *, catalog_counts: Mapping[str, int], fetched_counts: Mapping[str, int], tolerance: int = 0
) -> None:
    """逐个板块比对「目录成份个数」与「实际拉取行数」；差异超过容差即失败。

    `tolerance` 是本次快照允许的已知缺口（如 L3 未分类），由调用方按快照推导后传入，
    不在代码里写死。
    """
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    for code, expected in catalog_counts.items():
        actual = fetched_counts.get(code)
        if actual is None:
            raise SectorValidationError(f"板块 {code} 的成分未被拉取（上游缺返回）")
        if abs(actual - expected) > tolerance:
            raise SectorValidationError(
                f"板块 {code} 成分数 {actual} 与目录 {expected} 差异超过容差 {tolerance}"
            )


def catalog_row_counts(catalog: Sequence[SectorInfo]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in catalog:
        counts[row.level] = counts.get(row.level, 0) + 1
    return counts


__all__ = [
    "SectorValidationError",
    "catalog_row_counts",
    "validate_catalog",
    "validate_member_totals",
    "validate_unique_industry",
]
