"""板块 live 数据源：akshare 外呼适配器 + `market.*` 缓存装饰器。

口径（docs/architecture/2026-10-31-sector-data.md §4）：

* 读路径只读缓存，``SECTOR_CACHE_READ_ONLY=true``（默认）时**绝不外呼**；
* 写路径只在 ``make sync-sectors``（``SECTOR_MODE=live``）里显式调用 ``sync_to_cache``；
* 同步失败整批回滚：旧快照与旧 ``sector_sync_state`` 不推进，只留 ``status='failed'``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from bs4 import BeautifulSoup
from stock_contracts.clock import Clock
from stock_core.sector import SectorNode, resolve_level
from stock_core.values import Ratio

from stock_platform.config import Settings
from stock_platform.market_live import SessionFactory
from stock_platform.market_models import (
    TAXONOMY_CONCEPT,
    TAXONOMY_INDUSTRY,
    BenchmarkMember,
    Sector,
    SectorMember,
)
from stock_platform.obs import get_logger
from stock_platform.ports import (
    BenchmarkMember as BenchmarkMemberDTO,
)
from stock_platform.ports import (
    SectorBar,
    SectorCapabilities,
    SectorDataSource,
    SectorInfo,
    SectorMembership,
    SyncReport,
)
from stock_platform.repositories.sectors import SectorRepository, new_snapshot_id
from stock_platform.sector_validation import (
    SectorValidationError,
    validate_catalog,
    validate_unique_industry,
)
from stock_platform.symbols import classify_symbol

log = get_logger("stock_platform.sector_live")

DATASET_KEY = "sector"
BENCHMARK_ID = "hs300"
BENCHMARK_INDEX = "000300"
CLASSIFICATION_VERSION = "THS"
SOURCE_THS = "ths"
SOURCE_CSINDEX = "csindex"


def akshare_available() -> bool:
    try:
        import akshare  # noqa: F401
    except ImportError:
        return False
    return True


def _import_akshare() -> Any:
    try:
        import akshare
    except ImportError as exc:  # pragma: no cover - 依赖缺失由工厂 fail-fast 覆盖
        raise RuntimeError("未安装 akshare（pip install -e '.[live]'）") from exc
    return akshare


def _akshare_version() -> str:
    try:
        return str(_import_akshare().__version__)
    except Exception:  # pragma: no cover - 版本号读取失败不阻断
        return "unknown"


# ─────────────────────────────────────────────────────────────
# 缓存装饰器（读路径唯一入口）
# ─────────────────────────────────────────────────────────────


class CachedSectorSource:
    """读 `market.*` 缓存；默认只读，缺口返回空由调用方降级。"""

    def __init__(
        self,
        inner: SectorDataSource,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        clock: Clock,
    ) -> None:
        self._inner = inner
        self._settings = settings
        self._session_factory = session_factory
        self._clock = clock
        self._as_of: date | None = None

    @property
    def capabilities(self) -> SectorCapabilities:
        return self._inner.capabilities

    @property
    def as_of(self) -> date:
        # 同步 property 不能查 DB：由最近一次异步读取刷新；未读取过则委托 inner。
        return self._as_of or self._inner.as_of

    @property
    def provenance(self) -> str:
        return (
            f"cache:{self._inner.provenance}#{self._as_of.isoformat() if self._as_of else 'empty'}"
        )

    async def _refresh_state(self) -> None:
        async with self._session_factory(begin=True) as session:
            state = await SectorRepository(session).sync_state(dataset_key=DATASET_KEY)
        if state is not None and state.as_of is not None:
            self._as_of = state.as_of

    async def get_catalog(self, taxonomy: str | None = None) -> tuple[SectorInfo, ...]:
        await self._refresh_state()
        async with self._session_factory(begin=True) as session:
            rows = await SectorRepository(session).list_catalog(taxonomy=taxonomy)
        if rows or self._settings.sector_cache_read_only:
            return tuple(rows)
        # 仅调试路径：只读关闭且缓存为空时透传 inner（不写缓存）。
        return tuple(await self._inner.get_catalog(taxonomy))

    async def get_memberships(
        self, taxonomy: str, *, as_of: date | None = None, level: int | None = None
    ) -> tuple[SectorMembership, ...]:
        target = as_of or self.as_of
        async with self._session_factory(begin=True) as session:
            repo = SectorRepository(session)
            rows = await repo.memberships_at(taxonomy=taxonomy, as_of=target)
            catalog = await repo.list_catalog(taxonomy=taxonomy)
        if level is None or taxonomy != TAXONOMY_INDUSTRY:
            return tuple(rows)
        nodes = {
            item.sector_code: SectorNode(item.sector_code, item.level, item.parent_code)
            for item in catalog
        }
        resolved = resolve_level({item.symbol: item.sector_code for item in rows}, nodes, level)
        return tuple(
            SectorMembership(taxonomy=taxonomy, sector_code=code, symbol=symbol)
            for symbol, code in resolved.items()
        )

    async def get_board_bars(
        self,
        sector_keys: Sequence[tuple[str, str]],
        start: date,
        end: date,
    ) -> Sequence[SectorBar]:
        async with self._session_factory(begin=True) as session:
            rows = await SectorRepository(session).board_bars(
                keys=sector_keys, start=start, end=end
            )
        return tuple(
            SectorBar(
                taxonomy=row.taxonomy,
                sector_code=row.sector_code,
                trade_date=row.trade_date,
                close_units=row.close_units,
                return_units=row.return_units,
            )
            for row in rows
        )

    async def get_benchmark_members(
        self, benchmark_id: str, *, as_of: date | None = None
    ) -> tuple[BenchmarkMemberDTO, ...]:
        target = as_of or self.as_of
        async with self._session_factory(begin=True) as session:
            repo = SectorRepository(session)
            weights = await repo.benchmark_weights_at(benchmark_id=benchmark_id, as_of=target)
            weight_as_of = await repo.benchmark_weight_as_of(
                benchmark_id=benchmark_id, as_of=target
            )
        if weight_as_of is None:
            return ()
        return tuple(
            BenchmarkMemberDTO(
                benchmark_id=benchmark_id,
                symbol=symbol,
                weight_units=units,
                weight_as_of=weight_as_of,
            )
            for symbol, units in weights.items()
        )


# ─────────────────────────────────────────────────────────────
# akshare 外呼 + 同步
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Snapshot:
    catalog: tuple[SectorInfo, ...]
    members: dict[str, tuple[tuple[str, str], ...]]
    benchmark_weights: dict[str, int]
    checksum: str
    concept_source: str


def _norm_symbol(raw: object) -> str | None:
    """上游代码归一：去后缀，只保留 6 位 A 股；非股票返回 None。"""
    digits = str(raw or "").strip().split(".")[0].strip()
    if len(digits) != 6 or not digits.isdigit():
        return None
    if classify_symbol(digits) != "stock":
        return None
    return digits


def _weight_units(raw: object) -> int:
    """csindex 权重（'5.32%' 或 0.0532）→ ratio scale=8。"""
    text = str(raw or "").strip().replace("%", "")
    if not text:
        raise SectorValidationError("基准权重为空")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise SectorValidationError(f"基准权重无法解析: {raw!r}") from exc
    if value > 1:  # 百分数形态
        value = value / 100
    return Ratio.from_decimal(value).units


def _records(frame: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in frame.to_dict("records")]


class AkshareSectorSource:
    """akshare 外呼实现（`sync_to_cache` 写缓存；读方法供同步/排障使用）。"""

    def __init__(
        self, settings: Settings, *, clock: Clock, session_factory: SessionFactory
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._session_factory = session_factory
        self._semaphore = asyncio.Semaphore(settings.sector_live_max_concurrency)
        self._ak = _import_akshare()
        self._version = _akshare_version()
        # 行业与概念板块统一走同花顺（THS）：目录用 akshare，
        # 成分股因 akshare 无 THS 成分接口，改为抓取同花顺板块详情页分页表格。

    @property
    def capabilities(self) -> SectorCapabilities:
        return SectorCapabilities(
            mode="live",
            taxonomies=(TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT),
            benchmark_ids=(BENCHMARK_ID,),
        )

    @property
    def as_of(self) -> date:
        return self._clock.business_today()

    @property
    def provenance(self) -> str:
        return f"live:akshare@{self._version}"

    # ── 出站治理（并发上限 + 超时 + 有上限重试）───────
    async def _call(self, func: Any, *args: Any, interval: float = 0.0) -> Any:
        async with self._semaphore:
            if interval > 0:
                await asyncio.sleep(interval)
            last_error: Exception | None = None
            for attempt in range(self._settings.sector_live_retry_attempts + 1):
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(func, *args),
                        timeout=self._settings.sector_live_timeout_seconds,
                    )
                except Exception as exc:
                    last_error = exc
                    if attempt < self._settings.sector_live_retry_attempts:
                        await asyncio.sleep(
                            self._settings.sector_sync_retry_attempts * (attempt + 1)
                        )
            detail = str(last_error)
            lowered = detail.lower()
            if "proxy" in lowered:
                detail += (
                    "（代理连接失败：requests/akshare 会读取系统代理；"
                    "请关闭失效的本地代理，或检查 NO_PROXY 配置后重试）"
                )
            elif "remotedisconnected" in lowered or "remote end closed" in lowered:
                detail += (
                    "（对端主动断开：多为行情站点 WAF 临时封禁当前出口 IP——"
                    "请等待 10~30 分钟冷却后再试，或在代理客户端中为该域名配置节点分流）"
                )
            raise SectorValidationError(f"akshare 调用失败: {detail}") from last_error

    # ── 同花顺板块详情页成分股抓取（akshare 无 THS 成分接口）──
    _THS_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    _THS_MAX_PAGES = 50

    def _ths_board_url(self, code: str, taxonomy: str, page: int) -> str:
        path = "gn" if taxonomy == TAXONOMY_CONCEPT else "thshy"
        return f"http://q.10jqka.com.cn/{path}/detail/code/{code}/page/{page}/"

    def _ths_board_members(self, code: str, taxonomy: str) -> tuple[tuple[str, str], ...]:
        """抓取同花顺板块详情页成分股（同步，由 ``_call`` 放入线程池并限流）。

        表格首列为 ``代码``，必须用 BeautifulSoup 取 ``.text``（pandas ``read_html``
        会把 ``003032`` 转成 int 丢前导零）。分页靠 ``Referer`` 头，翻到 0 行即停。
        """
        members: list[tuple[str, str]] = []
        for page in range(1, self._THS_MAX_PAGES + 1):
            url = self._ths_board_url(code, taxonomy, page)
            headers = {"User-Agent": self._THS_USER_AGENT, "Referer": url}
            resp = requests.get(
                url, headers=headers, timeout=self._settings.sector_live_timeout_seconds
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table is None:
                break
            rows = table.find_all("tr")
            if len(rows) <= 1:
                break
            header = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            try:
                code_idx = header.index("代码")
            except ValueError as exc:
                raise SectorValidationError(
                    f"同花顺板块 {code} 详情页表头缺少「代码」列: {header}"
                ) from exc
            page_count = 0
            for tr in rows[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) <= code_idx:
                    continue
                symbol = _norm_symbol(cells[code_idx])
                if symbol is not None:
                    members.append((code, symbol))
                    page_count += 1
            if page_count == 0:
                break
        return tuple(members)

    # ── 目录 ────────────────────────────────────────────
    async def _fetch_industry_catalog(self) -> tuple[SectorInfo, ...]:
        # 同花顺行业为扁平 90 个板块（非申万三级树），统一 level=1 / 无父级。
        frame = await self._call(self._ak.stock_board_industry_name_ths)
        rows: list[SectorInfo] = []
        for rec in _records(frame):
            code = str(rec.get("code", "")).strip()
            name = str(rec.get("name", "")).strip()
            if not code or not name:
                raise SectorValidationError("同花顺行业板块代码/名称为空")
            rows.append(
                SectorInfo(
                    taxonomy=TAXONOMY_INDUSTRY,
                    sector_code=code,
                    name=name,
                    level=1,
                    parent_code=None,
                    classification_version=CLASSIFICATION_VERSION,
                )
            )
        return tuple(rows)

    async def _fetch_industry_members(
        self, catalog: tuple[SectorInfo, ...]
    ) -> tuple[tuple[str, str], ...]:
        interval = self._settings.sector_ths_call_interval_seconds
        members: list[tuple[str, str]] = []
        for row in catalog:
            rows = await self._call(
                self._ths_board_members,
                row.sector_code,
                TAXONOMY_INDUSTRY,
                interval=interval,
            )
            members.extend(rows)
        # 行业成员互斥（同花顺行业也应一只股票只属一个行业）。
        validate_unique_industry(members)
        return tuple(members)

    # ── 概念板块（同花顺 THS）────────────────────────────
    async def _fetch_concept_catalog(self) -> tuple[SectorInfo, ...]:
        frame = await self._call(self._ak.stock_board_concept_name_ths)
        rows: list[SectorInfo] = []
        for rec in _records(frame):
            code = str(rec.get("code", "")).strip()
            name = str(rec.get("name", "")).strip()
            if not code or not name:
                raise SectorValidationError("同花顺概念板块代码/名称为空")
            rows.append(
                SectorInfo(
                    taxonomy=TAXONOMY_CONCEPT,
                    sector_code=code,
                    name=name,
                    level=0,
                    parent_code=None,
                    classification_version=CLASSIFICATION_VERSION,
                )
            )
        return tuple(rows)

    async def _fetch_concept_members(
        self, catalog: tuple[SectorInfo, ...]
    ) -> tuple[tuple[str, str], ...]:
        interval = self._settings.sector_ths_call_interval_seconds
        members: list[tuple[str, str]] = []
        skipped: list[str] = []
        for row in catalog:
            try:
                rows = await self._call(
                    self._ths_board_members,
                    row.sector_code,
                    TAXONOMY_CONCEPT,
                    interval=interval,
                )
            except Exception as exc:
                # 概念板块为多归属、非互斥；单板块上游不可用（限流/下线）时跳过该板块，
                # 不让整批同步失败。已跳过的板块在响应中以 missing[] 形式透出由读路径降级。
                skipped.append(row.sector_code)
                log.warning(
                    "concept member fetch skipped",
                    source=SOURCE_THS,
                    sector_code=row.sector_code,
                    error=str(exc)[:120],
                )
                continue
            members.extend(rows)
        if skipped:
            log.info(
                "concept member fetch partial",
                source=SOURCE_THS,
                ok=len(catalog) - len(skipped),
                skipped=len(skipped),
            )
        return tuple(members)

    async def _fetch_benchmark(self) -> dict[str, int]:
        cons = await self._call(self._ak.index_stock_cons_csindex, BENCHMARK_INDEX)
        weights = await self._call(self._ak.index_stock_cons_weight_csindex, BENCHMARK_INDEX)
        universe = {
            symbol
            for rec in _records(cons)
            if (symbol := _norm_symbol(rec.get("成分券代码") or rec.get("证券代码")))
        }
        out: dict[str, int] = {}
        for rec in _records(weights):
            symbol = _norm_symbol(rec.get("成分券代码") or rec.get("证券代码"))
            if symbol is None or symbol not in universe:
                continue
            raw = rec.get("权重", rec.get("权重(%)"))
            out[symbol] = _weight_units(raw)
        return out

    # ── 端口读取（同步/排障用；不写缓存）──────────────────
    async def get_catalog(self, taxonomy: str | None = None) -> tuple[SectorInfo, ...]:
        out: list[SectorInfo] = []
        if taxonomy in (None, TAXONOMY_INDUSTRY):
            out.extend(await self._fetch_industry_catalog())
        if taxonomy in (None, TAXONOMY_CONCEPT):
            out.extend(await self._fetch_concept_catalog())
        return tuple(out)

    async def get_memberships(
        self, taxonomy: str, *, as_of: date | None = None, level: int | None = None
    ) -> tuple[SectorMembership, ...]:
        del as_of
        if taxonomy == TAXONOMY_INDUSTRY:
            catalog = await self._fetch_industry_catalog()
            rows = await self._fetch_industry_members(catalog)
            if level is None:
                return tuple(
                    SectorMembership(taxonomy=taxonomy, sector_code=code, symbol=symbol)
                    for code, symbol in rows
                )
            nodes = {
                row.sector_code: SectorNode(row.sector_code, row.level, row.parent_code)
                for row in catalog
            }
            resolved = resolve_level({symbol: code for code, symbol in rows}, nodes, level)
            return tuple(
                SectorMembership(taxonomy=taxonomy, sector_code=code, symbol=symbol)
                for symbol, code in resolved.items()
            )
        concept = await self._fetch_concept_catalog()
        rows = await self._fetch_concept_members(concept)
        return tuple(
            SectorMembership(taxonomy=taxonomy, sector_code=code, symbol=symbol)
            for code, symbol in rows
        )

    async def get_board_bars(
        self,
        sector_keys: Sequence[tuple[str, str]],
        start: date,
        end: date,
    ) -> Sequence[SectorBar]:
        del sector_keys, start, end
        return ()

    async def get_benchmark_members(
        self, benchmark_id: str, *, as_of: date | None = None
    ) -> tuple[BenchmarkMemberDTO, ...]:
        del as_of
        if benchmark_id != BENCHMARK_ID:
            return ()
        weights = await self._fetch_benchmark()
        return tuple(
            BenchmarkMemberDTO(
                benchmark_id=benchmark_id,
                symbol=symbol,
                weight_units=units,
                weight_as_of=self.as_of,
            )
            for symbol, units in weights.items()
        )

    # ── 同步写缓存（``make sync-sectors``）──────────────
    async def sync_to_cache(
        self,
        *,
        today: date,
        dry_run: bool = False,
        taxonomies: Sequence[str] | None = None,
    ) -> SyncReport:
        if dry_run:
            async with self._session_factory(begin=True) as session:
                await SectorRepository(session).list_catalog()
            wanted = ",".join(taxonomies) if taxonomies else "industry,concept"
            return SyncReport(
                as_of=today,
                status="skipped",
                message=(
                    f"dry-run: 配置/DB 可用；数据集={wanted}；目标 as_of={today.isoformat()}；"
                    "不联网、不写库"
                ),
            )
        try:
            snapshot = await self._build_snapshot()
        except (SectorValidationError, RuntimeError) as exc:
            await self._record_failure(today=today, detail=str(exc))
            return SyncReport(as_of=today, status="failed", message=str(exc))
        if taxonomies:
            snapshot = _filter_taxonomies(snapshot, taxonomies)

        try:
            written = await self._persist(snapshot, today=today)
        except Exception as exc:
            await self._record_failure(today=today, detail=f"persist failed: {exc}")
            return SyncReport(as_of=today, status="failed", message=str(exc))
        return SyncReport(
            as_of=today,
            status="succeeded" if written else "skipped",
            entity_count=len(snapshot.catalog),
            member_count=sum(len(items) for items in snapshot.members.values()),
            message=snapshot.checksum if written else "checksum unchanged（no-op）",
        )

    async def _build_snapshot(self) -> _Snapshot:
        industry = await self._fetch_industry_catalog()
        concepts = await self._fetch_concept_catalog()
        catalog = industry + concepts
        # 同花顺行业为扁平 L1（非申万 L1/L2/L3 树），只校验 level=1。
        validate_catalog(industry, level=1)
        validate_catalog(concepts, level=0)

        members: dict[str, tuple[tuple[str, str], ...]] = {}
        members[TAXONOMY_INDUSTRY] = await self._fetch_industry_members(industry)
        members[TAXONOMY_CONCEPT] = await self._fetch_concept_members(concepts)

        benchmark = await self._fetch_benchmark()
        checksum = _snapshot_checksum(catalog, members, benchmark)
        return _Snapshot(
            catalog=catalog,
            members=members,
            benchmark_weights=benchmark,
            checksum=checksum,
            concept_source=SOURCE_THS,
        )

    async def _persist(self, snapshot: _Snapshot, *, today: date) -> bool:
        pulled_at = self._clock.now()
        async with self._session_factory(begin=True) as session:
            repo = SectorRepository(session)
            state = await repo.sync_state(dataset_key=DATASET_KEY)
            if state is not None and state.checksum == snapshot.checksum and state.as_of == today:
                return False  # 同 checksum 重跑 → no-op

            for index, row in enumerate(snapshot.catalog):
                await session.merge(
                    Sector(
                        taxonomy=row.taxonomy,
                        sector_code=row.sector_code,
                        level=row.level,
                        name=row.name,
                        parent_code=row.parent_code,
                        classification_version=row.classification_version,
                        source=(
                            snapshot.concept_source
                            if row.taxonomy == TAXONOMY_CONCEPT
                            else SOURCE_THS
                        ),
                        is_active=True,
                        sort_order=index,
                        pulled_at=pulled_at,
                    )
                )
            await session.flush()

            for taxonomy, members in snapshot.members.items():
                current = await repo.memberships_at(taxonomy=taxonomy, as_of=today)
                current_keys = {(item.sector_code, item.symbol) for item in current}
                new_keys = set(members)
                await repo.close_members(
                    taxonomy=taxonomy,
                    codes=tuple({code for code, _ in new_keys}),
                    keep=new_keys,
                    effective_to=today,
                )
                # 先 close 后 insert：旧 current 行必须先落库关闭，否则撞行业当前唯一索引。
                await session.flush()
                for sector_code, symbol in members:
                    if (sector_code, symbol) in current_keys:
                        continue
                    session.add(
                        SectorMember(
                            taxonomy=taxonomy,
                            sector_code=sector_code,
                            symbol=symbol,
                            effective_from=today,
                            observed_at=pulled_at,
                            source=(
                                snapshot.concept_source
                                if taxonomy == TAXONOMY_CONCEPT
                                else SOURCE_THS
                            ),
                        )
                    )

            weights = snapshot.benchmark_weights
            await repo.close_benchmark_members(
                benchmark_id=BENCHMARK_ID, keep=set(weights), effective_to=today
            )
            await session.flush()
            for symbol, units in weights.items():
                session.add(
                    BenchmarkMember(
                        benchmark_id=BENCHMARK_ID,
                        symbol=symbol,
                        effective_from=today,
                        weight_units=units,
                        weight_as_of=today,
                        source=SOURCE_CSINDEX,
                        pulled_at=pulled_at,
                    )
                )

            await repo.upsert_sync_state(
                dataset_key=DATASET_KEY,
                status="succeeded",
                as_of=today,
                snapshot_id=new_snapshot_id(),
                catalog_as_of=today,
                membership_as_of=today,
                weight_as_of=today,
                classification_version=CLASSIFICATION_VERSION,
                entity_count=len(snapshot.catalog),
                member_count=sum(len(items) for items in snapshot.members.values()),
                source=snapshot.concept_source,
                checksum=snapshot.checksum,
                pulled_at=pulled_at,
            )
        return True

    async def _record_failure(self, *, today: date, detail: str) -> None:
        """失败只写状态（旧 as_of/checksum 不推进），供读路径提示 SECTOR_SYNC_FAILED。"""
        pulled_at = self._clock.now()
        async with self._session_factory(begin=True) as session:
            repo = SectorRepository(session)
            state = await repo.sync_state(dataset_key=DATASET_KEY)
            await repo.upsert_sync_state(
                dataset_key=DATASET_KEY,
                status="failed",
                as_of=state.as_of if state is not None else None,
                snapshot_id=state.snapshot_id if state is not None else None,
                catalog_as_of=state.catalog_as_of if state is not None else None,
                membership_as_of=state.membership_as_of if state is not None else None,
                weight_as_of=state.weight_as_of if state is not None else None,
                classification_version=(
                    state.classification_version if state is not None else None
                ),
                entity_count=state.entity_count if state is not None else 0,
                member_count=state.member_count if state is not None else 0,
                bar_as_of=state.bar_as_of if state is not None else None,
                source=state.source if state is not None else SOURCE_THS,
                checksum=state.checksum if state is not None else "",
                pulled_at=pulled_at,
                error_detail=detail[:512],
            )
        log.warning("sector.sync.failed", as_of=today.isoformat(), detail=detail[:200])


def _snapshot_checksum(
    catalog: tuple[SectorInfo, ...],
    members: dict[str, tuple[tuple[str, str], ...]],
    benchmark: dict[str, int],
) -> str:
    payload = {
        "catalog": sorted(
            (row.taxonomy, row.sector_code, row.level, row.name, row.parent_code or "")
            for row in catalog
        ),
        "members": {key: sorted(value) for key, value in members.items()},
        "benchmark": sorted(benchmark.items()),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _filter_taxonomies(snapshot: _Snapshot, taxonomies: Sequence[str]) -> _Snapshot:
    """按 `--taxonomies` 裁剪快照（未知值直接报错，避免静默写错集合）。"""
    wanted = set(taxonomies)
    unknown = wanted - {TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT}
    if unknown:
        raise SectorValidationError(f"未知 taxonomy: {', '.join(sorted(unknown))}")
    catalog = tuple(row for row in snapshot.catalog if row.taxonomy in wanted)
    members = {key: value for key, value in snapshot.members.items() if key in wanted}
    checksum = _snapshot_checksum(catalog, members, snapshot.benchmark_weights)
    return _Snapshot(
        catalog=catalog,
        members=members,
        benchmark_weights=snapshot.benchmark_weights,
        checksum=checksum,
        concept_source=snapshot.concept_source,
    )


__all__ = [
    "DATASET_KEY",
    "AkshareSectorSource",
    "CachedSectorSource",
    "akshare_available",
]
