"""板块 replay 夹具的确定性生成器（无运行时刻戳，重复生成 bytes 一致）。

夹具覆盖 demo 持仓（600519/000001/300750/601318/000858）的行业与概念归属，
并固定 2 个 L1 行业、4 个 L3 行业、3 个概念、1 只「无概念」股票、2 只「多概念」股票。
示例映射只是**夹具**，真实分类以同步数据为准。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from stock_core.values import Price, Ratio

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

# ── 行业指数日线（五因子归因的 sector 数据源，设计 §4.2-4）──────────
# 覆盖账单全区间（2026-04-01~2026-06-30）+ 前向缓冲（至 2026-09-30）；起点回扩
# 至 2026-01-30（旧 quotes 夹具首日），与 quotes.json 同跨度同工作日序列，
# 保证行业指数与个股参考日一致）；first_sync_on 仍为 2026-06-30（PIT 边界只看
# 成分快照，不看行情）。
SERIES_START = "2026-01-30"
SERIES_END = "2026-09-30"
# 价格锚点：锚日及之后与旧夹具（2026-04-01 起）逐日一致，扩历史只反向贴新增点。
_PRICE_ANCHOR = "2026-04-01"


def _business_days(start: str, end: str) -> tuple[str, ...]:
    """工作日序列（周一~周五；夹具不考虑节假日，仅供确定性回放）。"""
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    days: list[str] = []
    while cursor <= last:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return tuple(days)


_SERIES_DATES: tuple[str, ...] = _business_days(SERIES_START, SERIES_END)

# (sector_code, 首日收盘, 日漂移)：各行业漂移互不相同、且与个股曲线刻意
# 不同步——保证归因的 sector 因子非 0。收盘按 THS 指数惯例保留 3 位小数。
_SECTOR_SERIES_SPEC: tuple[tuple[str, str, str], ...] = (
    ("850121.SI", "2850.000", "0.0011"),  # 白酒
    ("850511.SI", "1950.000", "-0.0004"),  # 国有大型银行
    ("850811.SI", "4120.000", "0.0018"),  # 半导体
    ("850911.SI", "1330.000", "0.0006"),  # 保险
)


def _sector_rows(anchor_close: str, daily_drift: str) -> list[dict[str, str]]:
    """按固定工作日序列推导一条日线序列（收盘价与环比收益，全定点字符串）。

    以 `_PRICE_ANCHOR` 为锚：锚日及之后按原逐日复利（与旧夹具逐位一致），锚日
    之前按同一漂移反向贴现——保证扩历史不改变既有日期价格。
    """
    factor = Decimal("1") + Decimal(daily_drift)
    anchor_index = _SERIES_DATES.index(_PRICE_ANCHOR)
    anchor = Price.from_str(anchor_close)
    forward: list[Price] = [anchor]
    for _ in range(anchor_index + 1, len(_SERIES_DATES)):
        forward.append(
            Price.from_decimal((forward[-1].to_decimal() * factor).quantize(Decimal("0.001")))
        )
    backward: list[Price] = []
    cursor = anchor
    for _ in range(anchor_index):
        cursor = Price.from_decimal((cursor.to_decimal() / factor).quantize(Decimal("0.001")))
        backward.append(cursor)
    closes: list[Price] = list(reversed(backward)) + forward

    rows: list[dict[str, str]] = []
    for index, (day, close) in enumerate(zip(_SERIES_DATES, closes, strict=True)):
        daily = (
            Ratio.zero()
            if index == 0
            else Ratio.from_decimal(
                close.to_decimal() / closes[index - 1].to_decimal() - Decimal("1")
            )
        )
        rows.append({"date": day, "close": str(close), "return": str(daily)})
    return rows


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
        "series": {
            sector_code: _sector_rows(first_close, daily_drift)
            for sector_code, first_close, daily_drift in _SECTOR_SERIES_SPEC
        },
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


__all__ = [
    "AS_OF",
    "BENCHMARK",
    "CLASSIFICATION",
    "FIRST_SYNC_ON",
    "SERIES_END",
    "SERIES_START",
    "generate_sector_fixture",
]
