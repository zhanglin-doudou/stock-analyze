"""归因装配层：资产窗口解析（设计 §3.2，D-01 MTM 口径）。

`AssetWindow` 承载 `total_pnl` 的全部输入（V_begin / V_end / CF_period / 浮盈亏锚点）
与 blocked 判定。全部金额为标度整数（分）；本模块零 I/O、零 wall-clock（期望工作日与
data_as_of 由调用方传入）。

blocked 硬约束（§3.2，任一命中即 blocked 短路）：
- 无 `V_end` 行 / 区间内无资产行 → `ASSET_SERIES_MISSING`；
- 边界锚行（严格早于 T0 的最后一行）或区间内任一行 / `V_end` 行 `blocked` → `ASSET_ROW_BLOCKED`
  （数值全 0 与真实空账户不可区分，禁止用 0 兜底）；
- 区间内首行 `prev_total IS NULL` 且非真实空账户起点 → `ASSET_ROW_BLOCKED`（断链）；
- `T1 > data_as_of` 或 `V_end` 早于 `latest_workday(min(valuation_end, T1))` →
  `ASSET_SERIES_STALE`（周末月末不误判，§3.2 交易日对齐）。

`beginning_value_source` 三态（assumptions 披露，§6.2）：`statement_row`（V_begin 取自
严格早于 T0 的资产行）/ `prev_total`（无更早行，回退区间首行 prev_total）/ `zero`
（真实空账户起点：无更早行且期初价值为 0——`prev_total==0` 或首行 ok 且 total==0）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from stock_review.application.assets import latest_workday
from stock_review.models import AssetDaily

# `missing[]` 固定文案（what_to_do 与 §4.4 码表一致，供 API 层直接透出）。
_WTD_ASSET_BUILD = "先构建资产表（POST /assets/build）"
_WTD_ASSET_REBUILD = "重建资产表 / 补行情到期末"
_WTD_ASSET_FIX = "修复缺行情 / 现金锚点后重建资产表"

# assumptions.beginning_value_source 三态。
BEGIN_SOURCE_STATEMENT = "statement_row"
BEGIN_SOURCE_PREV_TOTAL = "prev_total"
BEGIN_SOURCE_ZERO = "zero"


@dataclass(frozen=True, slots=True)
class MissingEntry:
    """`missing[]` 条目（响应字段，不是 `ErrorCode`；§4.4 码表）。"""

    field: str
    code: str
    reason: str
    what_to_do: str


@dataclass(frozen=True, slots=True)
class AssetWindow:
    """资产窗口解析结果（§3.2）。

    `status == "blocked"` 时金额字段全 `None`（blocked 缺省语义，§6.3）；
    `missing` 含 ASSET_ROW_DEGRADED 等披露条目（status 仍可为 ok）。
    """

    status: str  # "ok" | "blocked"
    missing: tuple[MissingEntry, ...]
    v_begin_units: int | None
    v_end_units: int | None
    cf_period_units: int
    total_pnl_units: int | None
    # 桥接对账锚点（§3.2.1）：锚行 unrealized（任一端为 None 则跳过对账）。
    unrealized_begin_units: int | None
    unrealized_end_units: int | None
    begin_date: date | None  # d_begin：V_begin 锚行日期（桶 MV 起点，= P0 参考日）
    end_date: date | None  # d_end：V_end 行日期（桶 MV 终点）
    beginning_value_source: str
    degraded_rows: int  # 区间内 degraded 行数（披露 ASSET_ROW_DEGRADED）


def _blocked(missing: list[MissingEntry]) -> AssetWindow:
    return AssetWindow(
        status="blocked",
        missing=tuple(missing),
        v_begin_units=None,
        v_end_units=None,
        cf_period_units=0,
        total_pnl_units=None,
        unrealized_begin_units=None,
        unrealized_end_units=None,
        begin_date=None,
        end_date=None,
        beginning_value_source=BEGIN_SOURCE_STATEMENT,
        degraded_rows=0,
    )


def resolve_asset_window(
    assets: Sequence[AssetDaily],
    *,
    t0: date,
    t1: date,
    data_as_of: date | None,
    valuation_end_date: date,
) -> AssetWindow:
    """解析 `[T0, T1]` 资产窗口（纯内存推演，无 I/O）。

    `data_as_of`：资产构建水位线（`AssetBuildState.data_as_of`）；`valuation_end_date`：
    `latest_workday(min(valuation_end(clock), T1))` 由调用方算好传入（零时钟）。
    """
    ordered = sorted(assets, key=lambda row: row.trade_date)
    in_rows = [row for row in ordered if t0 <= row.trade_date <= t1]
    if not in_rows:
        return _blocked(
            [
                MissingEntry(
                    field="total_pnl",
                    code="ASSET_SERIES_MISSING",
                    reason=f"区间 [{t0.isoformat()}, {t1.isoformat()}] 内无资产日行",
                    what_to_do=_WTD_ASSET_BUILD,
                )
            ]
        )

    # V_end：≤T1 的最后一行（in_rows 末位）；行 blocked → blocked。
    end_row = in_rows[-1]
    degraded_rows = sum(1 for row in in_rows if row.status == "degraded")

    # V_begin：严格早于 T0 的最后一行；该行 blocked 即 blocked（fx_attr_p0_blocked）。
    prior_rows = [row for row in ordered if row.trade_date < t0]
    if prior_rows:
        anchor_row = prior_rows[-1]
        if anchor_row.status == "blocked":
            return _blocked(
                [
                    MissingEntry(
                        field="total_pnl",
                        code="ASSET_ROW_BLOCKED",
                        reason=f"期初锚行 {anchor_row.trade_date.isoformat()} 为 blocked，无法取 V_begin",
                        what_to_do=_WTD_ASSET_FIX,
                    )
                ]
            )
        v_begin = anchor_row.total_assets_units
        begin_date: date = anchor_row.trade_date
        unrealized_begin: int | None = anchor_row.unrealized_pnl_units
        begin_source = BEGIN_SOURCE_STATEMENT
    else:
        # 无更早行：回退区间内首行 prev_total（必须非 NULL，否则断链 blocked）。
        first = in_rows[0]
        if first.prev_total_assets_units is not None:
            v_begin = first.prev_total_assets_units
            begin_date = first.trade_date
            unrealized_begin = first.unrealized_pnl_units
            begin_source = BEGIN_SOURCE_PREV_TOTAL if v_begin != 0 else BEGIN_SOURCE_ZERO
        elif first.status == "ok" and first.total_assets_units == 0:
            # 真实空账户起点（账户存在但期初无资产；§3.2 允许，禁止用于兜底 blocked 行）。
            v_begin = 0
            begin_date = first.trade_date
            unrealized_begin = first.unrealized_pnl_units
            begin_source = BEGIN_SOURCE_ZERO
        else:
            return _blocked(
                [
                    MissingEntry(
                        field="total_pnl",
                        code="ASSET_ROW_BLOCKED",
                        reason="区间首行 prev_total 为 NULL（资产链路断链）",
                        what_to_do=_WTD_ASSET_FIX,
                    )
                ]
            )

    # 区间内任一行 blocked → blocked（数值全 0 与空账户不可区分）。
    blocked_rows = [row for row in in_rows if row.status == "blocked"]
    if blocked_rows:
        return _blocked(
            [
                MissingEntry(
                    field="total_pnl",
                    code="ASSET_ROW_BLOCKED",
                    reason=f"区间内 {len(blocked_rows)} 行 blocked（首见 {blocked_rows[0].trade_date.isoformat()}）",
                    what_to_do=_WTD_ASSET_FIX,
                )
            ]
        )

    # 交易日对齐 stale 判定（§3.2）：周末/节假日月末不误判。
    expected_end = latest_workday(min(valuation_end_date, t1))
    stale_missing: list[MissingEntry] = []
    if data_as_of is not None and t1 > data_as_of:
        stale_missing.append(
            MissingEntry(
                field="total_pnl",
                code="ASSET_SERIES_STALE",
                reason=f"区间终点 {t1.isoformat()} 晚于资产构建水位线 {data_as_of.isoformat()}",
                what_to_do=_WTD_ASSET_REBUILD,
            )
        )
    if end_row.trade_date < expected_end:
        stale_missing.append(
            MissingEntry(
                field="total_pnl",
                code="ASSET_SERIES_STALE",
                reason=(
                    f"V_end 行 {end_row.trade_date.isoformat()} 早于期望工作日 "
                    f"{expected_end.isoformat()}"
                ),
                what_to_do=_WTD_ASSET_REBUILD,
            )
        )
    if stale_missing:
        return _blocked(stale_missing)

    cf_period = sum(row.cashflow_units for row in in_rows)
    total_pnl = end_row.total_assets_units - v_begin - cf_period
    missing: list[MissingEntry] = []
    if degraded_rows:
        missing.append(
            MissingEntry(
                field="total_pnl",
                code="ASSET_ROW_DEGRADED",
                reason=f"区间内 {degraded_rows} 行 degraded（看资产页原因，结论可能失真）",
                what_to_do="查看资产页 degraded 原因",
            )
        )
    return AssetWindow(
        status="ok",
        missing=tuple(missing),
        v_begin_units=v_begin,
        v_end_units=end_row.total_assets_units,
        cf_period_units=cf_period,
        total_pnl_units=total_pnl,
        unrealized_begin_units=unrealized_begin,
        unrealized_end_units=end_row.unrealized_pnl_units,
        begin_date=begin_date,
        end_date=end_row.trade_date,
        beginning_value_source=begin_source,
        degraded_rows=degraded_rows,
    )


__all__ = [
    "BEGIN_SOURCE_PREV_TOTAL",
    "BEGIN_SOURCE_STATEMENT",
    "BEGIN_SOURCE_ZERO",
    "AssetWindow",
    "MissingEntry",
    "resolve_asset_window",
]
