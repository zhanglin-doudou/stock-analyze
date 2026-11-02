"""五因子归因的类型与数据契约（设计 §5.2，冻结形状）。

全部金额字段为标度整数（Money 分，scale=2）；数量为 Qty（scale=4）。
本模块零 I/O、零 wall-clock。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal


class FactorCode(StrEnum):
    """五因子编码（固定顺序见 `FACTOR_CODES`；rank tie-break 也按该序）。"""

    SELECTION = "selection"
    SECTOR = "sector"
    BUY_TIMING = "buy_timing"
    SELL_TIMING = "sell_timing"
    FEE = "fee"


# API factors[] 的固定顺序（§6.2：固定 5 项、固定顺序）。
FACTOR_CODES: Final[tuple[FactorCode, ...]] = (
    FactorCode.SELECTION,
    FactorCode.SECTOR,
    FactorCode.BUY_TIMING,
    FactorCode.SELL_TIMING,
    FactorCode.FEE,
)

# 产品名（FR-D3 人话层；不出现裸术语）。
FACTOR_NAMES: Final[Mapping[str, str]] = {
    FactorCode.SELECTION.value: "选股",
    FactorCode.SECTOR.value: "板块配置",
    FactorCode.BUY_TIMING.value: "买点",
    FactorCode.SELL_TIMING.value: "卖点",
    FactorCode.FEE.value: "费用",
}

SegmentKind = Literal["closed", "open"]


@dataclass(frozen=True, slots=True)
class SegmentInput:
    """一个 FIFO lot 在统计区间内的可归属片段（设计 §3.3）。

    - `base_units`：段基准本金 b（区间前 lot 用 P0、区间内建仓用建仓日收盘，
      经「标的级 floor → 段两级分摊」后落段）。
    - `end_value_units`：段终点市值（closed 段 = q×C_s、open 段 = q×P1，两级分摊后）。
    - `gross_buy_units`：段毛买入额（= 段净成本 − 买费分摊）；区间前 lot 为 ``None``。
    - `gross_sell_units`：段毛卖出额（= 段净卖出 + 卖费分摊）；仅 closed 段非 ``None``。
    - `fee_units`：本段区间内费用（正数；买费+卖费分摊之和）。
    - `sector_amount_units`：板块因子金额；``None`` = 本段不可归因（整段路径进残差）。
    - `dividend_units`：标的区间股息（税后净额）按段 base 分摊后的份额。
    """

    security_code: str
    kind: SegmentKind
    quantity_units: int
    base_units: int
    end_value_units: int
    gross_buy_units: int | None
    gross_sell_units: int | None
    fee_units: int
    sector_amount_units: int | None
    dividend_units: int = 0

    @property
    def attributed(self) -> bool:
        return self.sector_amount_units is not None


@dataclass(frozen=True, slots=True)
class SegmentFactors:
    """单段五因子分解结果（`decompose_segment` 输出；实现一致性自检用）。"""

    path_units: int
    selection_units: int
    sector_units: int
    buy_timing_units: int
    sell_timing_units: int
    fee_units: int
    attributed: bool


@dataclass(frozen=True, slots=True)
class Decomposition:
    """五因子分解汇总（设计 §3.4/§5.2）。

    不变式：`Σfactors + residual_units == total_pnl_units`（residual 为 plug）；
    `Σ持有段 end_value == 期末市值`、`Σ段 base == 标的级期初市值` 由装配层保证。
    """

    factors: Mapping[FactorCode, int]
    residual_units: int
    n_segments: int
    n_attributed_segments: int
    base_capital_units: int
    base_capital_attributed_units: int


@dataclass(frozen=True, slots=True)
class AttributionInput:
    """`AttributionModel.decompose` 的输入包（EP-6 协议对齐；M2 可扩展字段）。

    `extra_fee_units`：独立费用行 + 未归因标的费用（设计 §3.4 fee 行；
    PRD FR-R5.4「费用=区间费用合计」），由装配层汇总后传入。
    """

    segments: tuple[SegmentInput, ...]
    total_pnl_units: int
    extra_fee_units: int = 0
    model_id: str = "five_factor"
    definition_version: str = "1.0"


__all__ = [
    "FACTOR_CODES",
    "FACTOR_NAMES",
    "AttributionInput",
    "Decomposition",
    "FactorCode",
    "SegmentFactors",
    "SegmentInput",
    "SegmentKind",
]
