"""板块概览聚合（API-087，2026-10-31 sector-data 设计 §5.2/§6）。

* 只读：不写表、不建 job、不联网（板块数据源由调用方注入，默认缓存/replay）；
* 持仓市值复用 ``portfolio.holdings_at``，与 Dashboard / 资产表同一估值口径；
* 行业是互斥分区（分母 `E_map`，Σw=1）；概念是可重叠标签（分母 `E`，不可加总）；
* 全部标度整数，格式化只在 API 边界做；不可用一律 ``None`` + ``missing`` 码。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.metrics.policy import (
    SECTOR_BENCHMARK_MAX_AGE_DAYS,
    SECTOR_COVERAGE_DEGRADED_MIN_UNITS,
    SECTOR_COVERAGE_OK_MIN_UNITS,
    SECTOR_FLAT_ACTIVE_UNITS,
)
from stock_core.sector import (
    SectorNode,
    active_weights,
    concept_exposure,
    coverage_status,
    deviation_units,
    effective_n_units,
    normalize_weights,
    sector_hhi_from_values,
    sum_by_sector,
    topk_weights,
)
from stock_core.stats.numeric import div_round
from stock_ledger.repositories.trades import TradeRepository
from stock_platform.cursor import decode_cursor, encode_cursor
from stock_platform.ports import (
    MarketDataSource,
    SectorDataSource,
    SectorInfo,
    SectorMembership,
)
from stock_platform.repositories.sectors import SectorRepository
from stock_platform.symbols import CLASS_STOCK, classify_symbol

from stock_review.application.assets import (
    STATE_BLOCKED,
    STATE_MISSING,
    STATE_STALE,
    get_asset_build_state,
)
from stock_review.application.portfolio import holdings_at
from stock_review.repositories.assets import AssetRepository

TAXONOMY_INDUSTRY = "industry"
TAXONOMY_CONCEPT = "concept"
_DB_TAXONOMY = {TAXONOMY_INDUSTRY: "industry_sw", TAXONOMY_CONCEPT: "concept_em"}

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_INSUFFICIENT = "insufficient"
STATUS_BLOCKED = "blocked"

_STATUS_PRIORITY: dict[str, int] = {
    STATUS_BLOCKED: 0,
    STATUS_MISSING: 1,
    STATUS_INSUFFICIENT: 2,
    STATUS_STALE: 3,
    STATUS_DEGRADED: 4,
    STATUS_OK: 5,
}

STATE_CURRENT = "current"
STATE_STALE_DATA = "stale"
STATE_MISSING_DATA = "missing"

SECTOR_ASSET_BUILD_MISSING = "SECTOR_ASSET_BUILD_MISSING"
SECTOR_ASSET_BUILD_BLOCKED = "SECTOR_ASSET_BUILD_BLOCKED"
SECTOR_ASSET_BUILD_STALE = "SECTOR_ASSET_BUILD_STALE"
SECTOR_DATA_MISSING = "SECTOR_DATA_MISSING"
SECTOR_DATA_STALE = "SECTOR_DATA_STALE"
SECTOR_MEMBERSHIP_MISSING = "SECTOR_MEMBERSHIP_MISSING"
SECTOR_NO_STOCK_POSITIONS = "SECTOR_NO_STOCK_POSITIONS"
SECTOR_BENCHMARK_WEIGHTS_MISSING = "SECTOR_BENCHMARK_WEIGHTS_MISSING"
SECTOR_BENCHMARK_WEIGHTS_STALE = "SECTOR_BENCHMARK_WEIGHTS_STALE"
SECTOR_CONCEPT_MULTI_MEMBERSHIP = "SECTOR_CONCEPT_MULTI_MEMBERSHIP"
SECTOR_CONCEPT_COVERAGE_LOW = "SECTOR_CONCEPT_COVERAGE_LOW"
SECTOR_CONCEPT_HHI_UNAVAILABLE = "SECTOR_CONCEPT_HHI_UNAVAILABLE"
SECTOR_HISTORY_NOT_COVERED = "SECTOR_HISTORY_NOT_COVERED"
SECTOR_ASSET_MISMATCH = "SECTOR_ASSET_MISMATCH"
SECTOR_TRUNCATED = "SECTOR_TRUNCATED"
SECTOR_HOLDING_TRUNCATED = "SECTOR_HOLDING_TRUNCATED"
SECTOR_PRICE_MISSING = "SECTOR_PRICE_MISSING"
SECTOR_AS_OF_NOT_AVAILABLE = "SECTOR_AS_OF_NOT_AVAILABLE"
SECTOR_SYNC_FAILED = "SECTOR_SYNC_FAILED"
SECTOR_CONCEPT_FILTER_APPLIED = "SECTOR_CONCEPT_FILTER_APPLIED"
SECTOR_CONCEPT_FILTER_UNRESOLVED = "SECTOR_CONCEPT_FILTER_UNRESOLVED"

DEFINITION_VERSION = "1.0"
CONCEPT_CAVEAT = "概念为多归属标签：暴露可重叠、不可加总，也不代表资金权重。"
DEFAULT_LIMIT = 100
CATALOG_DEFAULT_LIMIT = 50
CATALOG_MAX_LIMIT = 200
DATASET_KEY = "sector_all"
# 概念覆盖度低提示阈值（scale=8，0.5）与多归属判定阈值（overlap > 1.0，scale=8）。
CONCEPT_COVERAGE_LOW_UNITS = 50_000_000
RATIO_ONE_UNITS = 100_000_000


@dataclass(slots=True, frozen=True)
class SectorMissing:
    field: str
    code: str
    reason: str
    what_to_do: str


@dataclass(slots=True, frozen=True)
class SectorPortfolio:
    total_assets_units: int | None
    holding_market_value_units: int | None
    equity_market_value_units: int | None
    mapped_market_value_units: int | None
    unclassified_market_value_units: int | None
    non_stock_market_value_units: int | None
    coverage_units: int | None
    equity_position_units: int | None
    holding_count: int
    classified_holding_count: int


@dataclass(slots=True, frozen=True)
class HoldingRef:
    """概念行下的持仓标的（symbol + 展示名；同一标的可出现在多个概念）。"""

    symbol: str
    name: str


@dataclass(slots=True, frozen=True)
class SectorRow:
    sector_code: str
    sector_name: str
    level: int
    parent_code: str | None
    market_value_units: int
    holding_count: int
    weight_units: int | None = None
    benchmark_weight_units: int | None = None
    active_weight_units: int | None = None
    direction: str | None = None
    exposure_units: int | None = None
    overlap: bool = False
    holdings: tuple[HoldingRef, ...] = ()


@dataclass(slots=True, frozen=True)
class SectorOthers:
    market_value_units: int
    holding_count: int
    weight_units: int | None = None
    exposure_units: int | None = None


@dataclass(slots=True, frozen=True)
class SectorUnclassified:
    market_value_units: int
    ratio_of_equity_units: int | None
    holding_count: int
    symbols: list[str]


@dataclass(slots=True, frozen=True)
class SectorConcentration:
    method: str
    status: str
    hhi_units: int | None
    effective_n_units: int | None
    top1_units: int | None
    top3_units: int | None
    benchmark_hhi_units: int | None
    n_sectors: int


@dataclass(slots=True, frozen=True)
class PreferenceItem:
    sector_code: str
    sector_name: str
    active_weight_units: int


@dataclass(slots=True, frozen=True)
class SectorPreference:
    deviation_units: int | None
    max_overweight: PreferenceItem | None
    max_underweight: PreferenceItem | None
    top_overweight: list[PreferenceItem]
    top_underweight: list[PreferenceItem]


@dataclass(slots=True, frozen=True)
class ConceptFilterItem:
    """用户排除的概念（code + 保存时的展示名）。"""

    code: str
    name: str


@dataclass(slots=True, frozen=True)
class ConceptFilter:
    """概念过滤偏好（设计 §4.6）；`off` 时对结果零影响。"""

    mode: str = "off"
    excluded: tuple[ConceptFilterItem, ...] = ()
    hash: str = ""

    @property
    def active(self) -> bool:
        return self.mode != "off"

    @property
    def excluded_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.excluded)


@dataclass(slots=True, frozen=True)
class ConceptFilterInfo:
    """响应里的 `concept.filters`（仅非 off 时输出）。"""

    mode: str
    applied: bool
    excluded: tuple[ConceptFilterItem, ...]
    unresolved: tuple[str, ...]


def concept_filter_from_preferences(
    concept_filters: Mapping[str, object], digest: str
) -> ConceptFilter:
    """把 `EffectivePreferences.concept_filters` 转成应用层过滤参数（不做校验/推断）。"""
    raw_mode = concept_filters.get("mode")
    mode = raw_mode if isinstance(raw_mode, str) else "off"
    raw_items = concept_filters.get("excluded")
    excluded: list[ConceptFilterItem] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            code = item.get("code")
            if not isinstance(code, str) or not code:
                continue
            name = item.get("name")
            excluded.append(
                ConceptFilterItem(code=code, name=name if isinstance(name, str) else code)
            )
    return ConceptFilter(mode=mode, excluded=tuple(excluded), hash=digest)


@dataclass(slots=True, frozen=True)
class ConceptBlock:
    coverage_units: int
    tagged_market_value_units: int
    multi_tag_market_value_units: int
    multi_tag_units: int
    overlap_units: int
    hhi_tag_units: int | None
    top1_concept: SectorRow | None
    top5_concepts: list[SectorRow]
    caveat: str | None
    #: 概念过滤（非 off 才非空）；`filtered_*` 仅 exclude 模式给出。
    filters: ConceptFilterInfo | None = None
    filtered_market_value_units: int | None = None
    filtered_holding_count: int | None = None


@dataclass(slots=True, frozen=True)
class SectorOverview:
    """API-087 应用层结果（全部标度整数/日期）。"""

    as_of: date
    taxonomy: str
    level: int
    status: str
    asset_build_state: str
    sector_data_state: str
    definition_version: str
    sector_dataset_version: str | None
    data_as_of: date | None
    sector_data_as_of: date | None
    benchmark_id: str | None
    benchmark_as_of: date | None
    weight_mode: str
    portfolio: SectorPortfolio
    sectors: list[SectorRow]
    others: SectorOthers | None
    unclassified: SectorUnclassified | None
    concentration: SectorConcentration
    preference: SectorPreference | None
    concept: ConceptBlock | None
    missing: list[SectorMissing]
    inputs_hash: str
    classification_version: str
    assumptions: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _Body:
    """行业/概念分支共用的响应主体（构造 `SectorOverview` 前可整体覆盖）。"""

    status: str
    portfolio: SectorPortfolio
    sectors: list[SectorRow]
    others: SectorOthers | None
    unclassified: SectorUnclassified | None
    concentration: SectorConcentration
    preference: SectorPreference | None
    concept: ConceptBlock | None
    benchmark_as_of: date | None
    missing: list[SectorMissing]
    assumptions: dict[str, str]


def _status_priority(statuses: Sequence[str]) -> str:
    return min(statuses, key=lambda item: _STATUS_PRIORITY.get(item, 99))


def _ratio_or_none(numerator: int | None, denominator: int | None) -> int | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return div_round(numerator, denominator, scale=8)


def _direction(active_units: int | None, flat_units: int) -> str | None:
    if active_units is None:
        return None
    if active_units > flat_units:
        return "over"
    if active_units < -flat_units:
        return "under"
    return "flat"


def _nodes(catalog: Sequence[SectorInfo]) -> dict[str, SectorNode]:
    return {
        item.sector_code: SectorNode(item.sector_code, item.level, item.parent_code)
        for item in catalog
    }


def _names(catalog: Sequence[SectorInfo]) -> dict[str, str]:
    return {item.sector_code: item.name for item in catalog}


def _under_parent(nodes: Mapping[str, SectorNode], code: str, parent: str) -> bool:
    current = nodes.get(code)
    while current is not None:
        if current.sector_code == parent:
            return True
        if current.parent_code is None:
            return False
        current = nodes.get(current.parent_code)
    return False


def _concept_memberships(rows: Sequence[SectorMembership]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row.symbol, []).append(row.sector_code)
    return result


def _inputs_hash(
    *,
    as_of: date | None,
    taxonomy: str,
    level: int,
    parent: str | None,
    provenance: str,
    definition_version: str,
    total_assets_units: int | None,
    holding_market_value_units: int | None,
    concept_filter_hash: str = "",
) -> str:
    parts = [
        as_of.isoformat() if as_of else "",
        taxonomy,
        str(level),
        parent or "",
        provenance,
        definition_version,
        str(total_assets_units),
        str(holding_market_value_units),
    ]
    # 设计 §4.6/§5.5：`off` 不进指纹（与旧 ETag 逐字一致），非 off 追加过滤指纹。
    if concept_filter_hash:
        parts.append(concept_filter_hash)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _empty_portfolio() -> SectorPortfolio:
    return SectorPortfolio(
        total_assets_units=None,
        holding_market_value_units=None,
        equity_market_value_units=None,
        mapped_market_value_units=None,
        unclassified_market_value_units=None,
        non_stock_market_value_units=None,
        coverage_units=None,
        equity_position_units=None,
        holding_count=0,
        classified_holding_count=0,
    )


def _empty_concentration(method: str) -> SectorConcentration:
    return SectorConcentration(
        method=method,
        status="unavailable",
        hhi_units=None,
        effective_n_units=None,
        top1_units=None,
        top3_units=None,
        benchmark_hhi_units=None,
        n_sectors=0,
    )


def _split_rows(
    rows: list[SectorRow],
    limit: int,
    total_units: int,
    *,
    concept: bool,
) -> tuple[list[SectorRow], SectorOthers | None]:
    """按 limit 截断并聚合 others（市值/权重或暴露/只数）。"""
    if limit <= 0 or len(rows) <= limit:
        return rows, None
    kept = rows[:limit]
    rest = rows[limit:]
    others_mv = sum(item.market_value_units for item in rest)
    others = SectorOthers(
        market_value_units=others_mv,
        holding_count=sum(item.holding_count for item in rest),
        weight_units=None if concept else _ratio_or_none(others_mv, total_units),
        exposure_units=_ratio_or_none(others_mv, total_units) if concept else None,
    )
    return kept, others


async def build_sector_overview(
    session: AsyncSession,
    user_id: uuid.UUID,
    sector_data: SectorDataSource,
    *,
    market_data: MarketDataSource,
    clock: Clock,
    as_of: date | None = None,
    taxonomy: str = TAXONOMY_INDUSTRY,
    level: int = 1,
    parent: str | None = None,
    benchmark_id: str = "hs300",
    limit: int = DEFAULT_LIMIT,
    coverage_ok_min_units: int = SECTOR_COVERAGE_OK_MIN_UNITS,
    coverage_degraded_min_units: int = SECTOR_COVERAGE_DEGRADED_MIN_UNITS,
    flat_active_units: int = SECTOR_FLAT_ACTIVE_UNITS,
    benchmark_max_age_days: int = SECTOR_BENCHMARK_MAX_AGE_DAYS,
    industry_stale_days: int = 35,
    concept_stale_days: int = 7,
    concept_filter: ConceptFilter | None = None,
) -> SectorOverview:
    """组装板块概览（纯读；行业/概念两条分支）。"""
    if taxonomy not in _DB_TAXONOMY:
        raise ValueError(f"unknown taxonomy: {taxonomy}")
    today = clock.today()
    if as_of is not None and as_of > today:
        raise ValueError("as_of 不能晚于今天")

    build_view, definition_version = await get_asset_build_state(
        session, user_id=user_id, clock=clock, market_data=market_data
    )
    asset_repo = AssetRepository(session)
    latest = await asset_repo.latest(
        user_id,
        definition_version=definition_version,
        on_or_before=as_of or today,
    )
    target_date = as_of or (latest.trade_date if latest is not None else None)

    missing: list[SectorMissing] = []
    db_taxonomy = _DB_TAXONOMY[taxonomy]
    catalog = tuple(await sector_data.get_catalog(db_taxonomy))
    sector_as_of: date | None = sector_data.as_of
    # 按 taxonomy 取历史下界：行业/概念首次同步日可能不同（概念晚同步时，
    # 用全局 MIN 会让概念请求在「行业已覆盖、概念未覆盖」的日期误走 insufficient）。
    history_start = sector_data.capabilities.history_start_for(db_taxonomy)
    classification_version = catalog[0].classification_version if catalog else ""
    # 校验请求层级在目录中实际存在：THS 行业为扁平 L1，申万才有 L1/L2/L3。
    # 若请求了目录不存在的层级，core 的 resolve_level 会抛 ValueError 变成 500，
    # 这里提前给出 422（catalog 为空时交给 SECTOR_DATA_MISSING 处理）。
    if taxonomy == TAXONOMY_INDUSTRY and catalog:
        catalog_levels = {item.level for item in catalog}
        if level not in catalog_levels:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail=(
                    f"行业分类当前仅支持 level {sorted(catalog_levels)}，请求 level={level} 不存在"
                ),
            )
    dataset_version = (
        f"{classification_version.lower()}@{sector_as_of.isoformat()}"
        if classification_version and sector_as_of
        else None
    )
    method = "industry_single_membership" if taxonomy == TAXONOMY_INDUSTRY else "concept_overlap"

    # ── 数据轴状态：missing / current / stale ─────────────────
    stale_days = industry_stale_days if taxonomy == TAXONOMY_INDUSTRY else concept_stale_days
    if not catalog or sector_as_of is None:
        sector_data_state = STATE_MISSING_DATA
        missing.append(
            SectorMissing(
                field="sectors",
                code=SECTOR_DATA_MISSING,
                reason="板块数据尚未同步。",
                what_to_do="运行 make sync-sectors（live 模式）后刷新。",
            )
        )
    elif target_date is not None and (target_date - sector_as_of).days > stale_days:
        sector_data_state = STATE_STALE_DATA
        missing.append(
            SectorMissing(
                field="sector_data_as_of",
                code=SECTOR_DATA_STALE,
                reason=f"板块快照（{sector_as_of}）落后超过 {stale_days} 天。",
                what_to_do="运行 make sync-sectors 更新板块数据。",
            )
        )
    else:
        sector_data_state = STATE_CURRENT

    # PIT fail-closed：请求日早于首次同步日一律不出数。
    history_blocked = (
        target_date is not None and history_start is not None and target_date < history_start
    )
    if history_blocked:
        missing.append(
            SectorMissing(
                field="sector_data_as_of",
                code=SECTOR_HISTORY_NOT_COVERED,
                reason=f"板块成分历史自 {history_start} 起可用；请求日更早。",
                what_to_do="把 as_of 调整到首次板块同步日之后，或升级数据源历史。",
            )
        )

    # ── 资产轴状态 ────────────────────────────────────────────
    asset_state = build_view.state
    if asset_state == STATE_MISSING:
        missing.append(
            SectorMissing(
                field="portfolio",
                code=SECTOR_ASSET_BUILD_MISSING,
                reason="资产表尚未构建。",
                what_to_do="到资产页构建资产表。",
            )
        )
    elif asset_state == STATE_BLOCKED:
        missing.append(
            SectorMissing(
                field="portfolio",
                code=SECTOR_ASSET_BUILD_BLOCKED,
                reason=build_view.status_reason or "资产表被阻断。",
                what_to_do="到资产页查看阻断原因并修复。",
            )
        )
    elif asset_state == STATE_STALE:
        missing.append(
            SectorMissing(
                field="data_as_of",
                code=SECTOR_ASSET_BUILD_STALE,
                reason="资产表落后于流水/行情。",
                what_to_do="到资产页重新构建资产表。",
            )
        )

    if asset_state in {STATE_MISSING, STATE_BLOCKED} or latest is None:
        if as_of is not None and asset_state not in {STATE_MISSING, STATE_BLOCKED}:
            missing.append(
                SectorMissing(
                    field="as_of",
                    code=SECTOR_AS_OF_NOT_AVAILABLE,
                    reason=f"{as_of} 没有资产快照。",
                    what_to_do="换一个资产快照日，或先构建资产表。",
                )
            )
        empty_status = STATUS_BLOCKED if asset_state == STATE_BLOCKED else STATUS_MISSING
        return SectorOverview(
            as_of=target_date or as_of or today,
            taxonomy=taxonomy,
            level=level,
            status=_status_priority(
                [empty_status, STATUS_BLOCKED if history_blocked else STATUS_OK]
            ),
            asset_build_state=asset_state,
            sector_data_state=sector_data_state,
            definition_version=DEFINITION_VERSION,
            sector_dataset_version=dataset_version,
            data_as_of=latest.trade_date if latest is not None else None,
            sector_data_as_of=sector_as_of,
            benchmark_id=benchmark_id if taxonomy == TAXONOMY_INDUSTRY else None,
            benchmark_as_of=None,
            weight_mode="additive" if taxonomy == TAXONOMY_INDUSTRY else "overlapping",
            portfolio=_empty_portfolio(),
            sectors=[],
            others=None,
            unclassified=None,
            concentration=_empty_concentration(method),
            preference=None,
            concept=None,
            missing=missing,
            inputs_hash=_inputs_hash(
                as_of=target_date,
                taxonomy=taxonomy,
                level=level,
                parent=parent,
                provenance=sector_data.provenance,
                definition_version=definition_version,
                total_assets_units=None,
                holding_market_value_units=None,
            ),
            classification_version=classification_version,
            assumptions={"non_stock_excluded": "true"},
        )

    assert latest is not None
    snapshot = latest.trade_date
    trades = list(await TradeRepository(session).balances_ordered(user_id, period_end=snapshot))
    holdings, missing_prices, _cost_fallback, truncated = await holdings_at(
        trades, as_of=snapshot, market_data=market_data
    )
    if truncated:
        missing.append(
            SectorMissing(
                field="portfolio.holding_count",
                code=SECTOR_HOLDING_TRUNCATED,
                reason="持仓超过上限，已按市值截断。",
                what_to_do="到资产页查看或导出完整持仓。",
            )
        )
    if missing_prices:
        missing.append(
            SectorMissing(
                field="portfolio.mapped_market_value",
                code=SECTOR_PRICE_MISSING,
                reason="部分持仓缺少行情：" + "、".join(missing_prices),
                what_to_do="检查行情覆盖后重新构建资产表。",
            )
        )

    # ── 权益 / 非股票拆分 ─────────────────────────────────────
    stock_mv: dict[str, int] = {}
    for item in holdings:
        if item.market_value_units is None:
            continue
        if classify_symbol(item.security_code) == CLASS_STOCK:
            stock_mv[item.security_code] = item.market_value_units
    stock_names = {
        item.security_code: item.security_name
        for item in holdings
        if item.security_code in stock_mv
    }
    equity_units = sum(stock_mv.values())
    holding_mv = latest.market_value_units
    total_assets = latest.total_assets_units
    non_stock_units: int | None = None
    if holding_mv is not None:
        non_stock_units = holding_mv - equity_units
        if non_stock_units < 0:
            non_stock_units = 0
            missing.append(
                SectorMissing(
                    field="portfolio.non_stock_market_value",
                    code=SECTOR_ASSET_MISMATCH,
                    reason="持仓重建市值高于资产表快照，非股票市值已夹紧为 0。",
                    what_to_do="到资产页重新构建资产表后复核。",
                )
            )

    statuses: list[str] = [asset_state]
    if sector_data_state == STATE_MISSING_DATA:
        statuses.append(STATUS_MISSING)
    elif sector_data_state == STATE_STALE_DATA:
        statuses.append(STATUS_STALE)
    if history_blocked:
        statuses.append(STATUS_BLOCKED)

    if equity_units <= 0:
        missing.append(
            SectorMissing(
                field="sectors",
                code=SECTOR_NO_STOCK_POSITIONS,
                reason="没有 A 股股票持仓。",
                what_to_do="导入股票流水并构建资产表。",
            )
        )
        body = _Body(
            status=_status_priority([*statuses, STATUS_MISSING]),
            portfolio=SectorPortfolio(
                total_assets_units=total_assets,
                holding_market_value_units=holding_mv,
                equity_market_value_units=0,
                mapped_market_value_units=None,
                unclassified_market_value_units=None,
                non_stock_market_value_units=non_stock_units,
                coverage_units=None,
                equity_position_units=None,
                holding_count=len(holdings),
                classified_holding_count=0,
            ),
            sectors=[],
            others=None,
            unclassified=None,
            concentration=_empty_concentration(method),
            preference=None,
            concept=None,
            benchmark_as_of=None,
            missing=[],
            assumptions={"non_stock_excluded": "true"},
        )
    else:
        equity_position = _ratio_or_none(equity_units, total_assets)
        if taxonomy == TAXONOMY_INDUSTRY:
            body = await _industry_overview(
                sector_data=sector_data,
                db_taxonomy=db_taxonomy,
                catalog=catalog,
                stock_mv=stock_mv,
                equity_units=equity_units,
                total_assets=total_assets,
                holding_mv=holding_mv,
                non_stock_units=non_stock_units,
                equity_position=equity_position,
                holding_count=len(holdings),
                snapshot=snapshot,
                level=level,
                parent=parent,
                benchmark_id=benchmark_id,
                limit=limit,
                benchmark_max_age_days=benchmark_max_age_days,
                coverage_ok_min_units=coverage_ok_min_units,
                coverage_degraded_min_units=coverage_degraded_min_units,
                flat_active_units=flat_active_units,
                classification_version=classification_version,
                statuses=statuses,
            )
        else:
            body = await _concept_overview(
                sector_data=sector_data,
                db_taxonomy=db_taxonomy,
                catalog=catalog,
                stock_mv=stock_mv,
                stock_names=stock_names,
                equity_units=equity_units,
                total_assets=total_assets,
                holding_mv=holding_mv,
                non_stock_units=non_stock_units,
                equity_position=equity_position,
                holding_count=len(holdings),
                snapshot=snapshot,
                limit=limit,
                coverage_ok_min_units=coverage_ok_min_units,
                coverage_degraded_min_units=coverage_degraded_min_units,
                statuses=statuses,
                concept_filter=concept_filter or ConceptFilter(),
            )

    if not history_blocked:
        missing.extend(body.missing)
    if history_blocked:
        # fail-closed：PIT 未覆盖 → blocked，数字一律不出（不输出 0 或占位百分比）。
        # 此时 body 的覆盖度类提示（如「补充概念同步数据」）是误导的，一并丢弃。
        body.status = STATUS_BLOCKED
        body.portfolio = _empty_portfolio()
        body.sectors = []
        body.others = None
        body.unclassified = None
        body.concentration = _empty_concentration(method)
        body.preference = None
        body.concept = None
        body.benchmark_as_of = None

    return SectorOverview(
        as_of=snapshot,
        taxonomy=taxonomy,
        level=level,
        status=body.status,
        asset_build_state=asset_state,
        sector_data_state=sector_data_state,
        definition_version=DEFINITION_VERSION,
        sector_dataset_version=dataset_version,
        data_as_of=snapshot,
        sector_data_as_of=sector_as_of,
        benchmark_id=benchmark_id if taxonomy == TAXONOMY_INDUSTRY else None,
        benchmark_as_of=body.benchmark_as_of,
        weight_mode="additive" if taxonomy == TAXONOMY_INDUSTRY else "overlapping",
        portfolio=body.portfolio,
        sectors=body.sectors,
        others=body.others,
        unclassified=body.unclassified,
        concentration=body.concentration,
        preference=body.preference,
        concept=body.concept,
        missing=missing,
        inputs_hash=_inputs_hash(
            as_of=snapshot,
            taxonomy=taxonomy,
            level=level,
            parent=parent,
            provenance=sector_data.provenance,
            definition_version=definition_version,
            total_assets_units=total_assets,
            holding_market_value_units=holding_mv,
            concept_filter_hash=(
                (concept_filter.hash or "")
                if taxonomy == TAXONOMY_CONCEPT and concept_filter is not None
                else ""
            ),
        ),
        classification_version=classification_version,
        assumptions=body.assumptions,
    )


async def _industry_overview(
    *,
    sector_data: SectorDataSource,
    db_taxonomy: str,
    catalog: Sequence[SectorInfo],
    stock_mv: Mapping[str, int],
    equity_units: int,
    total_assets: int | None,
    holding_mv: int | None,
    non_stock_units: int | None,
    equity_position: int | None,
    holding_count: int,
    snapshot: date,
    level: int,
    parent: str | None,
    benchmark_id: str,
    limit: int,
    benchmark_max_age_days: int,
    coverage_ok_min_units: int,
    coverage_degraded_min_units: int,
    flat_active_units: int,
    classification_version: str,
    statuses: Sequence[str],
) -> _Body:
    missing: list[SectorMissing] = []
    nodes = _nodes(catalog)
    names = _names(catalog)
    # 全量成分→板块映射（含非持仓）：基准权重聚合必须覆盖全部基准成分，
    # 否则非持仓基准板块会被丢弃，导致主动权重/偏离度失真（行业通用口径）。
    all_assignments = {
        row.symbol: row.sector_code
        for row in await sector_data.get_memberships(db_taxonomy, as_of=snapshot, level=level)
    }
    assignments = {symbol: code for symbol, code in all_assignments.items() if symbol in stock_mv}
    mapped_mv = sum_by_sector(assignments, stock_mv)
    mapped_total = sum(mapped_mv.values())
    unclassified_symbols = sorted(set(stock_mv) - set(assignments))
    unclassified_mv = sum(stock_mv[symbol] for symbol in unclassified_symbols)
    coverage_units = _ratio_or_none(mapped_total, equity_units)

    portfolio = SectorPortfolio(
        total_assets_units=total_assets,
        holding_market_value_units=holding_mv,
        equity_market_value_units=equity_units,
        mapped_market_value_units=mapped_total,
        unclassified_market_value_units=unclassified_mv,
        non_stock_market_value_units=non_stock_units,
        coverage_units=coverage_units,
        equity_position_units=equity_position,
        holding_count=holding_count,
        classified_holding_count=len(assignments),
    )

    current_statuses = list(statuses)
    if mapped_total == 0:
        missing.append(
            SectorMissing(
                field="unclassified",
                code=SECTOR_MEMBERSHIP_MISSING,
                reason="全部股票持仓均无行业归属。",
                what_to_do="运行 make sync-sectors 后重试。",
            )
        )
        current_statuses.append(STATUS_INSUFFICIENT)
    elif coverage_units is not None:
        current_statuses.append(
            coverage_status(
                coverage_units,
                ok_min_units=coverage_ok_min_units,
                degraded_min_units=coverage_degraded_min_units,
            )
        )

    # ── 基准权重（行业）──────────────────────────────────────
    benchmark_weights: dict[str, int] = {}
    benchmark_as_of: date | None = None
    for row in await sector_data.get_benchmark_members(benchmark_id, as_of=snapshot):
        code = all_assignments.get(row.symbol)
        if code is None:
            continue
        benchmark_weights[code] = benchmark_weights.get(code, 0) + row.weight_units
        benchmark_as_of = (
            row.weight_as_of if benchmark_as_of is None else max(benchmark_as_of, row.weight_as_of)
        )
    benchmark_available = sum(benchmark_weights.values()) > 0
    benchmark_weights_norm: dict[str, int] = {}
    if benchmark_available:
        benchmark_weights_norm = normalize_weights(benchmark_weights)
        if (
            benchmark_as_of is not None
            and (snapshot - benchmark_as_of).days > benchmark_max_age_days
        ):
            missing.append(
                SectorMissing(
                    field="benchmark_as_of",
                    code=SECTOR_BENCHMARK_WEIGHTS_STALE,
                    reason=f"基准权重发布日 {benchmark_as_of} 已过期。",
                    what_to_do="升级数据源并运行 make sync-sectors。",
                )
            )
            current_statuses.append(STATUS_DEGRADED)
    else:
        missing.append(
            SectorMissing(
                field="preference",
                code=SECTOR_BENCHMARK_WEIGHTS_MISSING,
                reason="基准板块权重不可得。",
                what_to_do="运行 make sync-sectors 拉取基准成分/权重。",
            )
        )
        current_statuses.append(STATUS_DEGRADED)

    weights: dict[str, int] = {
        code: _ratio_or_none(value, mapped_total) or 0 for code, value in mapped_mv.items()
    }
    active = active_weights(weights, benchmark_weights_norm) if benchmark_available else {}

    # ── 持仓行（并集：组合板块 ∪ 基准板块）────────────────────
    codes = set(mapped_mv) | (set(benchmark_weights_norm) if benchmark_available else set())
    if parent is not None:
        codes = {code for code in codes if _under_parent(nodes, code, parent)}
    holdings_count: dict[str, int] = {}
    for code in assignments.values():
        holdings_count[code] = holdings_count.get(code, 0) + 1

    rows = [
        SectorRow(
            sector_code=code,
            sector_name=names.get(code, code),
            level=nodes[code].level if code in nodes else level,
            parent_code=nodes[code].parent_code if code in nodes else None,
            market_value_units=mapped_mv.get(code, 0),
            holding_count=holdings_count.get(code, 0),
            weight_units=weights.get(code),
            benchmark_weight_units=(
                benchmark_weights_norm.get(code) if benchmark_available else None
            ),
            active_weight_units=active.get(code) if benchmark_available else None,
            direction=(
                _direction(active.get(code), flat_active_units) if benchmark_available else None
            ),
        )
        for code in codes
    ]
    rows.sort(key=lambda item: (-item.market_value_units, item.sector_code))
    kept, others = _split_rows(rows, limit, mapped_total, concept=False)
    if others is not None:
        missing.append(
            SectorMissing(
                field="others",
                code=SECTOR_TRUNCATED,
                reason=f"板块超过 {limit} 行，其余已聚合到 others。",
                what_to_do="在前端按板块筛选或缩小 parent 范围。",
            )
        )

    # ── 集中度 / 偏好 ─────────────────────────────────────────
    values = sorted(mapped_mv.values(), reverse=True)
    hhi = sector_hhi_from_values(values, mapped_total)
    bench_hhi = (
        sector_hhi_from_values(
            list(benchmark_weights_norm.values()), sum(benchmark_weights_norm.values())
        )
        if benchmark_weights_norm
        else None
    )
    concentration = SectorConcentration(
        method="industry_single_membership",
        status="ok" if mapped_total > 0 else "unavailable",
        hhi_units=hhi,
        effective_n_units=effective_n_units(hhi) if hhi is not None else None,
        top1_units=topk_weights(values, mapped_total, k=1),
        top3_units=topk_weights(values, mapped_total, k=3),
        benchmark_hhi_units=bench_hhi,
        n_sectors=len(mapped_mv),
    )
    preference: SectorPreference | None = None
    if benchmark_available:
        active_items = [
            PreferenceItem(
                sector_code=code,
                sector_name=names.get(code, code),
                active_weight_units=value,
            )
            for code, value in active.items()
        ]
        active_items.sort(key=lambda item: (-abs(item.active_weight_units), item.sector_code))
        over = [item for item in active_items if item.active_weight_units > 0][:5]
        under = [item for item in active_items if item.active_weight_units < 0][:5]
        preference = SectorPreference(
            deviation_units=deviation_units(active),
            max_overweight=over[0] if over else None,
            max_underweight=under[0] if under else None,
            top_overweight=over,
            top_underweight=under,
        )

    unclassified = SectorUnclassified(
        market_value_units=unclassified_mv,
        ratio_of_equity_units=_ratio_or_none(unclassified_mv, equity_units),
        holding_count=len(unclassified_symbols),
        symbols=unclassified_symbols,
    )

    return _Body(
        status=_status_priority(current_statuses),
        portfolio=portfolio,
        sectors=kept,
        others=others,
        unclassified=unclassified,
        concentration=concentration,
        preference=preference,
        concept=None,
        benchmark_as_of=benchmark_as_of,
        missing=missing,
        assumptions={
            "industry_standard": classification_version,
            "weight_basis": "mapped_equity_market_value",
            "benchmark_weight_source": f"csindex:{benchmark_id}",
            "non_stock_excluded": "true",
            "pit": "interval_effective_from_to",
        },
    )


async def _concept_overview(
    *,
    sector_data: SectorDataSource,
    db_taxonomy: str,
    catalog: Sequence[SectorInfo],
    stock_mv: Mapping[str, int],
    stock_names: Mapping[str, str] | None = None,
    equity_units: int,
    total_assets: int | None,
    holding_mv: int | None,
    non_stock_units: int | None,
    equity_position: int | None,
    holding_count: int,
    snapshot: date,
    limit: int,
    coverage_ok_min_units: int,
    coverage_degraded_min_units: int,
    statuses: Sequence[str],
    concept_filter: ConceptFilter,
) -> _Body:
    """概念分支：行/TopN/others 可按偏好过滤；覆盖度/重叠/多归属保持全量口径。"""
    missing: list[SectorMissing] = []
    names = _names(catalog)
    display_names = stock_names or {}
    memberships = _concept_memberships(
        await sector_data.get_memberships(db_taxonomy, as_of=snapshot)
    )
    concept = concept_exposure(stock_mv, memberships, total_units=equity_units)

    mv_by_concept: dict[str, int] = {}
    count_by_concept: dict[str, int] = {}
    overlap_concept: dict[str, bool] = {}
    holdings_by_concept: dict[str, list[HoldingRef]] = {}
    tagged_mv = 0
    multi_tag_mv = 0
    for symbol, value in stock_mv.items():
        tags = tuple(memberships.get(symbol, ()))
        if not tags:
            continue
        tagged_mv += value
        if len(tags) >= 2:
            multi_tag_mv += value
        for code in set(tags):
            mv_by_concept[code] = mv_by_concept.get(code, 0) + value
            count_by_concept[code] = count_by_concept.get(code, 0) + 1
            overlap_concept[code] = overlap_concept.get(code, False) or len(tags) >= 2
            holdings_by_concept.setdefault(code, []).append(
                HoldingRef(symbol=symbol, name=display_names.get(symbol, symbol))
            )
    for refs in holdings_by_concept.values():
        refs.sort(key=lambda ref: (-stock_mv[ref.symbol], ref.symbol))

    # ── 概念过滤（设计 §4.6）：只裁剪行/TopN/others/n_sectors ────────────
    # 覆盖度、重叠、多归属、tagged/unclassified 一律保持全量口径；
    # `off` 不做任何改动，输出与旧版本逐字一致（ETag 也不变）。
    filters_info: ConceptFilterInfo | None = None
    filtered_mv_units: int | None = None
    filtered_holding_count: int | None = None
    excluded_codes = set(concept_filter.excluded_codes)
    if concept_filter.active:
        unresolved = tuple(item.code for item in concept_filter.excluded if item.code not in names)
        excluded_existing = [code for code in excluded_codes if code in mv_by_concept]
        if concept_filter.mode == "exclude":
            filtered_mv_units = sum(mv_by_concept[code] for code in excluded_existing)
            filtered_holding_count = len(
                {
                    ref.symbol
                    for code in excluded_existing
                    for ref in holdings_by_concept.get(code, ())
                }
            )
        for code in excluded_codes:
            mv_by_concept.pop(code, None)
            count_by_concept.pop(code, None)
            overlap_concept.pop(code, None)
            holdings_by_concept.pop(code, None)
        filters_info = ConceptFilterInfo(
            mode=concept_filter.mode,
            applied=bool(excluded_codes),
            excluded=concept_filter.excluded,
            unresolved=unresolved,
        )
        if excluded_codes:
            missing.append(
                SectorMissing(
                    field="concept.filters",
                    code=SECTOR_CONCEPT_FILTER_APPLIED,
                    reason=f"已按偏好排除 {len(excluded_codes)} 个概念，行/TopN/others 不含这些概念。",
                    what_to_do="在设置页调整概念过滤后重新查看。",
                )
            )
        if unresolved:
            missing.append(
                SectorMissing(
                    field="concept.filters.excluded",
                    code=SECTOR_CONCEPT_FILTER_UNRESOLVED,
                    reason=f"有 {len(unresolved)} 个已排除概念在概念目录中不存在。",
                    what_to_do="在设置页移除失效概念，或重新同步概念目录（make sync-sectors）。",
                )
            )

    portfolio = SectorPortfolio(
        total_assets_units=total_assets,
        holding_market_value_units=holding_mv,
        equity_market_value_units=equity_units,
        mapped_market_value_units=None,
        unclassified_market_value_units=None,
        non_stock_market_value_units=non_stock_units,
        coverage_units=None,
        equity_position_units=equity_position,
        holding_count=holding_count,
        classified_holding_count=sum(1 for symbol in stock_mv if memberships.get(symbol)),
    )

    current_statuses = list(statuses)
    current_statuses.append(
        coverage_status(
            concept.coverage_units,
            ok_min_units=coverage_ok_min_units,
            degraded_min_units=coverage_degraded_min_units,
        )
    )
    if concept.coverage_units < CONCEPT_COVERAGE_LOW_UNITS:
        missing.append(
            SectorMissing(
                field="concept.coverage",
                code=SECTOR_CONCEPT_COVERAGE_LOW,
                reason="概念覆盖度低于 50%。",
                what_to_do="补充概念同步数据（make sync-sectors）后复核。",
            )
        )
    if concept.overlap_units > RATIO_ONE_UNITS:
        missing.append(
            SectorMissing(
                field="concept.overlap",
                code=SECTOR_CONCEPT_MULTI_MEMBERSHIP,
                reason="存在多概念归属，暴露重复计数。",
                what_to_do="查看概念明细时注意「多归属、不可加总」。",
            )
        )
    missing.append(
        SectorMissing(
            field="concentration.hhi",
            code=SECTOR_CONCEPT_HHI_UNAVAILABLE,
            reason="概念不满足互斥划分，不提供资金集中度。",
            what_to_do="参考 concept.hhi_tag（标签份额口径，独立描述）。",
        )
    )

    rows = [
        SectorRow(
            sector_code=code,
            sector_name=names.get(code, code),
            level=0,
            parent_code=None,
            market_value_units=value,
            holding_count=count_by_concept.get(code, 0),
            exposure_units=concept.exposure.get(code),
            overlap=overlap_concept.get(code, False),
            holdings=tuple(holdings_by_concept.get(code, ())),
        )
        for code, value in mv_by_concept.items()
    ]
    rows.sort(key=lambda item: (-item.market_value_units, item.sector_code))
    kept, others = _split_rows(rows, limit, equity_units, concept=True)
    if others is not None:
        missing.append(
            SectorMissing(
                field="others",
                code=SECTOR_TRUNCATED,
                reason=f"概念超过 {limit} 行，其余已聚合到 others。",
                what_to_do="在前端按概念筛选。",
            )
        )

    top5 = kept[:5]
    block = ConceptBlock(
        coverage_units=concept.coverage_units,
        tagged_market_value_units=tagged_mv,
        multi_tag_market_value_units=multi_tag_mv,
        multi_tag_units=concept.multi_tag_units,
        overlap_units=concept.overlap_units,
        hhi_tag_units=concept.hhi_tag_units,
        top1_concept=top5[0] if top5 else None,
        top5_concepts=top5,
        caveat=CONCEPT_CAVEAT if concept.overlap_units > RATIO_ONE_UNITS else None,
        filters=filters_info,
        filtered_market_value_units=filtered_mv_units,
        filtered_holding_count=filtered_holding_count,
    )
    unclassified = SectorUnclassified(
        market_value_units=equity_units - tagged_mv,
        ratio_of_equity_units=_ratio_or_none(equity_units - tagged_mv, equity_units),
        holding_count=sum(1 for symbol in stock_mv if not memberships.get(symbol)),
        symbols=sorted(symbol for symbol in stock_mv if not memberships.get(symbol)),
    )
    concentration = SectorConcentration(
        method="concept_overlap",
        status="unavailable",
        hhi_units=None,
        effective_n_units=None,
        top1_units=None,
        top3_units=None,
        benchmark_hhi_units=None,
        n_sectors=len(mv_by_concept),
    )
    return _Body(
        status=_status_priority(current_statuses),
        portfolio=portfolio,
        sectors=kept,
        others=others,
        unclassified=unclassified,
        concentration=concentration,
        preference=None,
        concept=block,
        benchmark_as_of=None,
        missing=missing,
        assumptions={
            "concept_assignment": "catalog_all",
            "weight_basis": "equity_market_value",
            "non_stock_excluded": "true",
            **({"concept_filter_hash": concept_filter.hash} if concept_filter.active else {}),
        },
    )


# ── API-088 公共目录 ──────────────────────────────────────────


SOURCE_BY_TAXONOMY = {TAXONOMY_INDUSTRY: "sw", TAXONOMY_CONCEPT: "em"}


@dataclass(slots=True, frozen=True)
class SectorPathNode:
    sector_code: str
    sector_name: str


@dataclass(slots=True, frozen=True)
class SectorCatalogItem:
    sector_code: str
    sector_name: str
    taxonomy: str
    level: int
    parent_code: str | None
    path: list[SectorPathNode]
    member_count: int
    source: str
    classification_version: str
    is_active: bool


@dataclass(slots=True, frozen=True)
class SectorCatalogResult:
    """API-088 应用层结果（无用户维度；`catalog_version` 为 16 hex 或 None）。"""

    as_of: date
    taxonomy: str
    catalog_version: str | None
    sector_data_state: str
    items: list[SectorCatalogItem]
    next_cursor: str | None
    has_more: bool
    missing: list[SectorMissing]


def _catalog_version(state: object, sector_data: SectorDataSource) -> str | None:
    checksum = getattr(state, "checksum", None)
    if isinstance(checksum, str) and checksum:
        return checksum[:16]
    provenance = sector_data.provenance
    if "sha256:" in provenance:
        digest = provenance.rsplit("sha256:", 1)[-1].split("#", 1)[0]
        return digest[:16] or None
    return None


def _cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        payload = decode_cursor(cursor)
        offset = int(payload["o"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from None
    if offset < 0:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标位置无效")
    return offset


def _member_counts(
    catalog: Sequence[SectorInfo], memberships: Sequence[SectorMembership]
) -> dict[str, set[str]]:
    """按目录树 rollup 成分数（同一股票在祖先板块只计一次）。"""
    nodes = _nodes(catalog)
    counts: dict[str, set[str]] = {}
    for membership in memberships:
        code: str | None = membership.sector_code
        while code is not None:
            counts.setdefault(code, set()).add(membership.symbol)
            node = nodes.get(code)
            code = node.parent_code if node is not None else None
    return counts


def _path_of(
    code: str, nodes: Mapping[str, SectorNode], names: Mapping[str, str]
) -> list[SectorPathNode]:
    chain: list[SectorPathNode] = []
    current: str | None = code
    while current is not None:
        chain.append(SectorPathNode(current, names.get(current, current)))
        node = nodes.get(current)
        current = node.parent_code if node is not None else None
    chain.reverse()
    return chain


async def list_sector_catalog(
    session: AsyncSession,
    sector_data: SectorDataSource,
    *,
    taxonomy: str = TAXONOMY_INDUSTRY,
    level: int | None = None,
    parent: str | None = None,
    q: str | None = None,
    active_only: bool = True,
    limit: int = CATALOG_DEFAULT_LIMIT,
    cursor: str | None = None,
) -> SectorCatalogResult:
    """公共目录（只读；目录未同步 → 200 + items=[] + SECTOR_DATA_MISSING）。"""
    db_taxonomy = _DB_TAXONOMY[taxonomy]
    state = await SectorRepository(session).sync_state(dataset_key=DATASET_KEY)
    catalog_rows = await sector_data.get_catalog(db_taxonomy)
    catalog_version = _catalog_version(state, sector_data)
    missing: list[SectorMissing] = []
    if state is not None and state.status == "failed":
        missing.append(
            SectorMissing(
                field="sector_data_as_of",
                code=SECTOR_SYNC_FAILED,
                reason="最近一次板块同步失败，目录可能落后于上游。",
                what_to_do="查看同步日志后执行 make sync-sectors --dry-run 复核。",
            )
        )
    if not catalog_rows:
        missing.append(
            SectorMissing(
                field="items",
                code=SECTOR_DATA_MISSING,
                reason="尚未同步任何板块目录。",
                what_to_do="执行 make sync-sectors 或 make seed-demo。",
            )
        )
        return SectorCatalogResult(
            as_of=sector_data.as_of,
            taxonomy=taxonomy,
            catalog_version=None,
            sector_data_state=STATE_MISSING_DATA,
            items=[],
            next_cursor=None,
            has_more=False,
            missing=missing,
        )

    memberships = await sector_data.get_memberships(db_taxonomy, as_of=sector_data.as_of)
    nodes = _nodes(catalog_rows)
    names = _names(catalog_rows)
    counts = _member_counts(catalog_rows, memberships)

    selected = [row for row in catalog_rows if level is None or row.level == level]
    if parent is not None:
        selected = [row for row in selected if row.parent_code == parent]
    if q:
        needle = q.casefold()
        selected = [row for row in selected if needle in row.name.casefold()]
    selected.sort(key=lambda row: (row.level, row.sector_code))

    offset = _cursor_offset(cursor)
    if offset > len(selected):
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标位置超出目录范围")
    page = selected[offset : offset + limit]
    has_more = offset + limit < len(selected)
    next_cursor = encode_cursor({"o": str(offset + limit)}) if has_more else None

    items = [
        SectorCatalogItem(
            sector_code=row.sector_code,
            sector_name=row.name,
            taxonomy=taxonomy,
            level=row.level,
            parent_code=row.parent_code,
            path=_path_of(row.sector_code, nodes, names),
            member_count=len(counts.get(row.sector_code, set())),
            source=SOURCE_BY_TAXONOMY[taxonomy],
            classification_version=row.classification_version,
            is_active=True,
        )
        for row in page
    ]
    return SectorCatalogResult(
        as_of=sector_data.as_of,
        taxonomy=taxonomy,
        catalog_version=catalog_version,
        sector_data_state=STATE_CURRENT,
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        missing=missing,
    )


__all__ = [
    "CATALOG_DEFAULT_LIMIT",
    "CATALOG_MAX_LIMIT",
    "TAXONOMY_CONCEPT",
    "TAXONOMY_INDUSTRY",
    "ConceptBlock",
    "HoldingRef",
    "PreferenceItem",
    "SectorCatalogItem",
    "SectorCatalogResult",
    "SectorConcentration",
    "SectorMissing",
    "SectorOthers",
    "SectorOverview",
    "SectorPathNode",
    "SectorPortfolio",
    "SectorPreference",
    "SectorRow",
    "SectorUnclassified",
    "build_sector_overview",
    "list_sector_catalog",
]
