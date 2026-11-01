"""板块 replay 夹具的确定性生成器（无运行时刻戳，重复生成 bytes 一致）。

夹具覆盖 demo 持仓（600519/000001/300750/601318/000858）的行业与概念归属，
并固定 2 个 L1 行业、4 个 L3 行业、3 个概念、1 只「无概念」股票、2 只「多概念」股票。
示例映射只是**夹具**，真实分类以同步数据为准。
"""

from __future__ import annotations

import json
from typing import Any

AS_OF = "2026-06-30"
FIRST_SYNC_ON = "2026-06-30"
CLASSIFICATION = {"industry_sw": "SW2021", "concept_em": "EM-2026-06"}
BENCHMARK = {"id": "hs300", "as_of": "2026-06-30"}


def _sector(taxonomy: str, code: str, level: int, name: str, parent: str | None) -> dict[str, Any]:
    return {
        "taxonomy": taxonomy,
        "sector_code": code,
        "level": level,
        "name": name,
        "parent_code": parent,
    }


_SECTORS: tuple[dict[str, Any], ...] = (
    # 行业树：L1 → L2 → L3
    _sector("industry_sw", "801120.SI", 1, "食品饮料", None),
    _sector("industry_sw", "801050.SI", 1, "银行", None),
    _sector("industry_sw", "801080.SI", 1, "电子", None),
    _sector("industry_sw", "801180.SI", 1, "非银金融", None),
    _sector("industry_sw", "801123.SI", 2, "饮料制造", "801120.SI"),
    _sector("industry_sw", "801051.SI", 2, "国有大型银行", "801050.SI"),
    _sector("industry_sw", "801081.SI", 2, "半导体", "801080.SI"),
    _sector("industry_sw", "801191.SI", 2, "保险", "801180.SI"),
    _sector("industry_sw", "850121.SI", 3, "白酒", "801123.SI"),
    _sector("industry_sw", "850511.SI", 3, "国有大型银行", "801051.SI"),
    _sector("industry_sw", "850811.SI", 3, "半导体", "801081.SI"),
    _sector("industry_sw", "850911.SI", 3, "保险", "801191.SI"),
    # 概念：扁平（level=0）
    _sector("concept_em", "BK0999", 0, "人工智能", None),
    _sector("concept_em", "BK1010", 0, "新能源", None),
    _sector("concept_em", "BK1100", 0, "白酒概念", None),
)

_INDUSTRY_MEMBERS: tuple[tuple[str, str], ...] = (
    ("600519", "850121.SI"),
    ("000858", "850121.SI"),
    ("000001", "850511.SI"),
    ("300750", "850811.SI"),
    ("601318", "850911.SI"),
)

_CONCEPT_MEMBERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("600519", ("BK0999", "BK1100")),
    ("000858", ("BK1100",)),
    ("300750", ("BK0999", "BK1010")),
    ("601318", ("BK1010",)),
    # 000001 无概念（用于「无标签/覆盖度」边界）
)

_BENCHMARK_WEIGHTS: tuple[tuple[str, str], ...] = (
    ("600519", "0.05320000"),
    ("000858", "0.01980000"),
    ("000001", "0.02100000"),
    ("300750", "0.03650000"),
    ("601318", "0.04020000"),
)


def generate_sector_fixture() -> bytes:
    """返回确定性 JSON bytes（固定排序、固定日期、无运行时刻戳）。"""
    memberships = [
        {
            "taxonomy": "industry_sw",
            "sector_code": code,
            "symbol": symbol,
            "effective_from": FIRST_SYNC_ON,
        }
        for symbol, code in _INDUSTRY_MEMBERS
    ]
    memberships.extend(
        {
            "taxonomy": "concept_em",
            "sector_code": code,
            "symbol": symbol,
            "effective_from": FIRST_SYNC_ON,
        }
        for symbol, codes in _CONCEPT_MEMBERS
        for code in codes
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "as_of": AS_OF,
        "first_sync_on": FIRST_SYNC_ON,
        "classification": CLASSIFICATION,
        "benchmark": BENCHMARK,
        "sectors": list(_SECTORS),
        "memberships": memberships,
        "benchmark_members": [
            {
                "benchmark_id": "hs300",
                "symbol": symbol,
                "weight": weight,
                "weight_as_of": AS_OF,
                "effective_from": FIRST_SYNC_ON,
            }
            for symbol, weight in _BENCHMARK_WEIGHTS
        ],
        "series": {},
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


__all__ = ["AS_OF", "BENCHMARK", "CLASSIFICATION", "FIRST_SYNC_ON", "generate_sector_fixture"]
