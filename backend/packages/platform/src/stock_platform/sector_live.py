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
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from stock_contracts.clock import Clock
from stock_core.sector import SectorNode, resolve_level
from stock_core.values import Price, Ratio

from stock_platform.config import Settings
from stock_platform.market_live import SessionFactory
from stock_platform.market_models import (
    TAXONOMY_CONCEPT,
    TAXONOMY_INDUSTRY,
    BenchmarkMember,
    Sector,
    SectorMember,
    SectorPullRange,
)
from stock_platform.market_models import (
    SectorBar as SectorBarRow,
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
    SectorCookieError,
    SectorMemberIncompleteError,
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
# 同花顺登录态（SECTOR_THS_COOKIE）缺失/失效时统一报此文案；不含 cookie 明文。
_THS_COOKIE_MESSAGE = (
    "同花顺 cookie 缺失或已失效：请通过环境变量 SECTOR_THS_COOKIE 注入浏览器登录后的"
    "完整 cookie 串（可选 SECTOR_THS_UA 覆盖 User-Agent）；本次同步整批放弃，不写库"
)


def mask_cookie(cookie: str | None) -> str:
    """cookie 脱敏：日志/异常只允许出现长度标记，禁止明文。"""
    return f"<masked,len={len(cookie)}>" if cookie else "<empty>"


def _is_login_wall(status_code: int, text: str, url: str = "") -> bool:
    """识别同花顺登录墙：401、跳登录页、或 ~192 字节的拦截页。"""
    if status_code == 401:
        return True
    lowered_url = url.lower()
    if "passport" in lowered_url or "/login" in lowered_url:
        return True
    body = (text or "").strip()
    return bool(len(body) <= 300 and ("登录" in body or "login" in body.lower()))


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
        self._history_start: date | None = None
        self._history_starts: tuple[tuple[str, date], ...] = ()

    @property
    def capabilities(self) -> SectorCapabilities:
        """合并 inner 能力与缓存实际历史下界（首次同步日）。

        live 源的 ``AkshareSectorSource`` 无法预知首次同步日，故由缓存层从
        ``MIN(sector_member.effective_from)`` 推导 ``history_start``，使
        ``as_of < 首次同步日`` 时 fail-closed 走 ``SECTOR_HISTORY_NOT_COVERED``
        （而非误导性的 ``SECTOR_MEMBERSHIP_MISSING``）。
        """
        inner_caps = self._inner.capabilities
        return SectorCapabilities(
            mode=inner_caps.mode,
            taxonomies=inner_caps.taxonomies,
            benchmark_ids=inner_caps.benchmark_ids,
            supports_membership=inner_caps.supports_membership,
            supports_board_bars=inner_caps.supports_board_bars,
            supports_benchmark_weights=inner_caps.supports_benchmark_weights,
            history_start=self._history_start or inner_caps.history_start,
            history_starts=self._history_starts or inner_caps.history_starts,
        )

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
            repo = SectorRepository(session)
            state = await repo.sync_state(dataset_key=DATASET_KEY)
            history_start = await session.scalar(select(func.min(SectorMember.effective_from)))
            per_taxonomy = (
                await session.execute(
                    select(
                        SectorMember.taxonomy,
                        func.min(SectorMember.effective_from),
                    ).group_by(SectorMember.taxonomy)
                )
            ).all()
        if state is not None and state.as_of is not None:
            self._as_of = state.as_of
        self._history_start = history_start
        # 各 taxonomy 的首次观测日可能不同（如概念比行业晚同步），分别记录以免
        # 概念查询在行业已覆盖的日期被误判为「insufficient 覆盖 0」而非 PIT blocked。
        self._history_starts = tuple(
            (str(taxonomy), first) for taxonomy, first in per_taxonomy if first is not None
        )

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


def _bar_date(raw: object, sector_code: str) -> date:
    """THS 指数「日期」列 → date（str / datetime / pandas Timestamp 均可）。"""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError as exc:
        raise SectorValidationError(f"行业指数 {sector_code} 日期无法解析: {raw!r}") from exc


def _bar_close(raw: object, sector_code: str, trade_date: date) -> Decimal:
    """THS 指数「收盘价」（float，如 9048.101）→ Decimal（经 str 取最短十进制表示）。"""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise SectorValidationError(f"行业指数 {sector_code} {trade_date} 收盘价为空")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise SectorValidationError(
            f"行业指数 {sector_code} {trade_date} 收盘价无法解析: {raw!r}"
        ) from exc
    if value <= 0:
        raise SectorValidationError(f"行业指数 {sector_code} {trade_date} 收盘价非正数: {raw!r}")
    return value


def _board_bar_records(
    taxonomy: str, sector_code: str, records: Sequence[dict[str, Any]]
) -> list[SectorBar]:
    """THS 行业指数日 K 行 → SectorBar（close scale=6 / return scale=8）。

    窗口首行无前收盘 → return 0；若日后回补更早历史，重拉会整体重算覆盖。
    """
    out: list[SectorBar] = []
    prev_close: Decimal | None = None
    for rec in records:
        trade_date = _bar_date(rec.get("日期"), sector_code)
        close = _bar_close(rec.get("收盘价"), sector_code, trade_date)
        daily = (
            Ratio.zero()
            if prev_close is None
            else Ratio.from_decimal(close / prev_close - Decimal("1"))
        )
        out.append(
            SectorBar(
                taxonomy=taxonomy,
                sector_code=sector_code,
                trade_date=trade_date,
                close_units=Price.from_decimal(close).units,
                return_units=daily.units,
            )
        )
        prev_close = close
    return out


def _bars_checksum(bars: Sequence[SectorBar]) -> str:
    payload = sorted(
        (bar.trade_date.isoformat(), bar.close_units, bar.return_units) for bar in bars
    )
    raw = json.dumps(payload, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ths_expected_rows(
    sector_code: str, page_counts: Sequence[int], total_pages: int | None
) -> int:
    """由 `page_info` 总页数与各页行数推导期望成分行数。

    缺 `page_info` 或实际页数与声明不一致 → 视为上游截断，抛完整性错误（整批失败）。
    期望值按首屏整页行数 × (总页数−1) + 末页行数；中途某页少行（动态排序缺失/
    分页异常）会让去重后行数小于期望值，由上层整板重抓而不是直接判死。
    少数板块（如小行业）页面无 ``page_info``，此时以各页实际行数之和为期望值：
    仍能通过「去重后 < 合计」发现跨页重复，但无法识别从未出现过的缺失行。
    """
    if not page_counts:
        return 0
    if total_pages is None or total_pages <= 0:
        return sum(page_counts)
    if len(page_counts) != total_pages:
        raise SectorMemberIncompleteError(
            f"同花顺板块 {sector_code} 实际抓取 {len(page_counts)} 页，"
            f"与 page_info 声明 {total_pages} 页不一致（疑似截断）"
        )
    if not page_counts:
        return 0
    if len(page_counts) == 1:
        return page_counts[0]
    return page_counts[0] * (total_pages - 1) + page_counts[-1]


def _parse_quote_bridge(text: str, sector_code: str, year: int) -> dict[str, Any]:
    """解析行业指数 JSONP（``quotebridge_v4_line_...({...})``）为字典。"""
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end <= start:
        raise SectorValidationError(
            f"行业指数 {sector_code} {year} 响应非 JSONP（{len(text or '')} 字节）"
        )
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise SectorValidationError(f"行业指数 {sector_code} {year} JSONP 无法解析: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise SectorValidationError(f"行业指数 {sector_code} {year} JSONP 缺少 data 字段")
    return payload


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
    async def _call(
        self, func: Any, *args: Any, interval: float = 0.0, timeout: float | None = None
    ) -> Any:
        async with self._semaphore:
            if interval > 0:
                await asyncio.sleep(interval)
            last_error: Exception | None = None
            for attempt in range(self._settings.sector_live_retry_attempts + 1):
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(func, *args),
                        timeout=timeout or self._settings.sector_live_timeout_seconds,
                    )
                except SectorCookieError:
                    # 登录态缺失/失效不重试：继续重试只会放大限流且必然失败。
                    raise
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
    # 概念页 10 行/页、行业页 20 行/页；同花顺超大概念（如 `300900` 融资融券
    # ~3887 只 / 389 页）页数远超小板块，上限需留足余量（600 页 ≈ 6000 只）。
    _THS_MAX_PAGES = 600
    # 连续多页拿不到任何新代码 → 上游分页重复返回（截断/反爬），立即显式失败，
    # 不再空转到页数上限。
    _THS_STALE_PAGES = 5
    # 单页请求失败重试 3 次（总尝试 4 次），退避 1/2/4s；401 同样走此重试。
    _THS_PAGE_RETRIES = 3
    _THS_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
    # 上游按涨跌幅动态排序会让同一集合在两次抓取中出现 1~4 只重复/缺失；
    # 整板重抓最多 3 轮，只有「去重后行数 == page_info 推导值」才接受。
    _THS_MEMBER_PASSES = 3
    # 单板块全分页抓取的总超时：页数上限 × 请求间隔 + 重试余量。
    _THS_BOARD_TIMEOUT_SECONDS = 900.0

    def _require_cookie(self) -> str:
        """读取登录 cookie；缺失即 fail-fast（不保留匿名半截路径）。"""
        cookie = (self._settings.sector_ths_cookie or "").strip()
        if not cookie:
            raise SectorCookieError(_THS_COOKIE_MESSAGE)
        return cookie

    @property
    def _ths_user_agent(self) -> str:
        return (self._settings.sector_ths_ua or "").strip() or self._THS_USER_AGENT

    def _ths_board_url(self, code: str, taxonomy: str, page: int) -> str:
        path = "gn" if taxonomy == TAXONOMY_CONCEPT else "thshy"
        return f"http://q.10jqka.com.cn/{path}/detail/code/{code}/page/{page}/"

    def _ths_get(self, url: str, *, cookie: str, referer: str) -> requests.Response:
        """带登录态的单次 GET：401/网络错误重试 3 次（1/2/4s），登录页立即失败。"""
        headers = {
            "User-Agent": self._ths_user_agent,
            "Referer": referer,
            "Cookie": cookie,
        }
        last_error = ""
        last_status = 0
        for attempt in range(self._THS_PAGE_RETRIES + 1):
            try:
                resp = requests.get(
                    url, headers=headers, timeout=self._settings.sector_live_timeout_seconds
                )
            except requests.RequestException as exc:
                last_status = 0
                last_error = str(exc)
            else:
                if resp.status_code == 401:
                    last_status = 401
                    last_error = "HTTP 401"
                elif _is_login_wall(resp.status_code, resp.text, resp.url):
                    # 192 字节登录拦截/跳转：cookie 已失效，重试无意义。
                    raise SectorCookieError(
                        f"{_THS_COOKIE_MESSAGE}（登录页拦截，{mask_cookie(cookie)}）"
                    )
                elif resp.status_code >= 400:
                    last_status = resp.status_code
                    last_error = f"HTTP {resp.status_code}"
                else:
                    return resp
            if attempt < self._THS_PAGE_RETRIES:
                time.sleep(self._THS_RETRY_BACKOFF_SECONDS[attempt])
        if last_status == 401:
            raise SectorCookieError(
                f"{_THS_COOKIE_MESSAGE}（401 重试 {self._THS_PAGE_RETRIES} 次仍失败，"
                f"{mask_cookie(cookie)}）"
            )
        raise SectorValidationError(
            f"同花顺请求失败（{url}）：{last_error}；重试 {self._THS_PAGE_RETRIES} 次仍失败"
        )

    def _ths_board_members(self, code: str, taxonomy: str) -> tuple[tuple[str, str], ...]:
        """抓取同花顺板块详情页全部分页成分股（同步，由 ``_call`` 放入线程池并限流）。

        概念详情页首屏通常有**两张表**：第 1 张是「概念解析」产业链/子行业，
        第 2 张才是成分股表（含 ``代码`` 列）。因此必须遍历所有表找带 ``代码`` 表头的
        那张，不能只取第一张。单元格一律用 BeautifulSoup 取 ``.text``
        （pandas ``read_html`` 会把 ``003032`` 转 int 丢前导零）。

        完整性：先由 ``page_info``（如 ``1/13``）取总页数，逐页抓完；期望行数 =
        (总页数−1)×整页行数 + 末页行数。动态排序导致的 1~4 只重复/缺失会让去重后
        行数少于期望值，此时整板重抓（最多 3 轮），仍不一致则整批失败、不落库。
        """
        cookie = self._require_cookie()
        last_expected = 0
        last_unique = 0
        for pass_index in range(self._THS_MEMBER_PASSES):
            try:
                unique, expected = self._ths_board_members_pass(code, taxonomy, cookie=cookie)
            except SectorMemberIncompleteError as exc:
                # 页数截断/分页重复返回：整板重抓比直接判死更有机会拿到完整集合。
                if pass_index + 1 >= self._THS_MEMBER_PASSES:
                    raise
                log.warning(
                    "ths member truncated, refetch",
                    sector_code=code,
                    error=str(exc)[:120],
                    attempt=pass_index + 1,
                )
                continue
            if expected > 0 and len(unique) == expected:
                # 闸门通过后再过滤非 A 股行（如北交所 92xxxx），避免误判缺行。
                filtered = (
                    (code, symbol) for raw in unique if (symbol := _norm_symbol(raw)) is not None
                )
                return tuple(dict.fromkeys(filtered))
            last_expected, last_unique = expected, len(unique)
            if pass_index + 1 < self._THS_MEMBER_PASSES:
                log.warning(
                    "ths member incomplete, refetch",
                    sector_code=code,
                    expected=expected,
                    unique=len(unique),
                    attempt=pass_index + 1,
                )
        raise SectorMemberIncompleteError(
            f"同花顺板块 {code} 成分完整性校验失败：page_info 期望 {last_expected} 行，"
            f"去重后 {last_unique} 行；整板重抓 {self._THS_MEMBER_PASSES} 轮仍不一致，"
            "整批放弃不落库"
        )

    def _ths_board_members_pass(
        self, code: str, taxonomy: str, *, cookie: str
    ) -> tuple[tuple[str, ...], int]:
        """抓取一遍全部分页，返回（去重后的原始代码, page_info 推导的期望行数）。"""
        members: list[str] = []
        counts: list[int] = []
        seen: set[str] = set()
        total_pages: int | None = None
        page = 1
        stale_pages = 0
        page_interval = self._settings.sector_ths_page_interval_seconds
        while page <= self._THS_MAX_PAGES:
            if page > 1 and page_interval > 0:
                time.sleep(page_interval)
            url = self._ths_board_url(code, taxonomy, page)
            resp = self._ths_get(url, cookie=cookie, referer=url)
            soup = BeautifulSoup(resp.text, "html.parser")
            info = self._ths_page_info(soup)
            if info is not None:
                if total_pages is None:
                    total_pages = info[1]
                elif total_pages != info[1]:
                    raise SectorValidationError(
                        f"同花顺板块 {code} 分页总页数漂移：{total_pages} → {info[1]}"
                    )
            tables = soup.find_all("table")
            parsed = self._ths_parse_member_table(tables) if tables else None
            if parsed is None:
                if page == 1:
                    # 首屏拿不到成分表：多为限流/反爬拦截页或页面结构变更，必须显式失败。
                    raise SectorValidationError(
                        f"同花顺板块 {code} 详情页无成分表（可能被限流或页面结构变更，"
                        f"响应 {len(resp.text)} 字节）"
                    )
                break
            if not parsed:
                if page == 1:
                    raise SectorValidationError(f"同花顺板块 {code} 详情页成分表为空")
                if total_pages is not None and page == total_pages:
                    # 末页只有「暂无成份股数据」占位行：page_info 把空页也计入总页数
                    # （如三胎概念 210 只 / 21 页，却声明 22 页）。按实际有数据页收口。
                    log.info(
                        "ths last page has no data",
                        sector_code=code,
                        declared_pages=total_pages,
                    )
                    total_pages = page - 1
                # 越界页/末页之后会返回只有表头的空表：视为翻页结束。
                break
            fresh = sum(1 for raw in parsed if raw not in seen)
            # 超大板块（数千只）下用集合判定，避免逐页 O(n²)。
            seen.update(parsed)
            stale_pages = 0 if fresh else stale_pages + 1
            if stale_pages >= self._THS_STALE_PAGES:
                raise SectorMemberIncompleteError(
                    f"同花顺板块 {code} 连续 {stale_pages} 页无新成分股（第 {page} 页），"
                    "疑似上游分页重复返回/截断，整批放弃不落库"
                )
            counts.append(len(parsed))
            members.extend(parsed)
            if total_pages is not None and page >= total_pages:
                break
            page += 1
        if page > self._THS_MAX_PAGES:
            raise SectorMemberIncompleteError(
                f"同花顺板块 {code} 分页超过上限 {self._THS_MAX_PAGES}，疑似上游截断"
            )
        unique = tuple(dict.fromkeys(members))
        expected = _ths_expected_rows(code, counts, total_pages)
        return unique, expected

    @staticmethod
    def _ths_page_info(soup: Any) -> tuple[int, int] | None:
        """解析 ``<span class="page_info">1/13</span>`` → (当前页, 总页数)。"""
        node = soup.find("span", class_="page_info")
        if node is None:
            return None
        matched = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", node.get_text(strip=True))
        if matched is None:
            return None
        return int(matched.group(1)), int(matched.group(2))

    @staticmethod
    def _ths_parse_member_table(tables: Any) -> list[str] | None:
        """在多张表中找出含「代码」表头的成分表，返回**原始** 6 位代码（含北交所）。

        完整性闸门按上游原始行计数/去重；股票过滤（``_norm_symbol``）放到闸门之后，
        避免把合法的非 A 股行（如 92xxxx 北交所）误判为「缺行」。
        没有匹配表时返回 ``None``（由调用方区分「无成分表」与「本页无成分」）。
        """
        for table in tables:
            rows = table.find_all("tr")
            if len(rows) <= 1:
                continue
            header = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])]
            if "代码" not in header:
                continue
            code_idx = header.index("代码")
            codes: list[str] = []
            for tr in rows[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) <= code_idx:
                    continue
                raw = cells[code_idx].strip()
                if len(raw) == 6 and raw.isdigit():
                    codes.append(raw)
            return codes
        return None

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
        total = len(catalog)
        log.info(
            "industry member fetch start",
            source=SOURCE_THS,
            total=total,
        )
        members: list[tuple[str, str]] = []
        for index, row in enumerate(catalog, start=1):
            rows = await self._call(
                self._ths_board_members,
                row.sector_code,
                TAXONOMY_INDUSTRY,
                interval=interval,
                timeout=self._THS_BOARD_TIMEOUT_SECONDS,
            )
            members.extend(rows)
            if index % 20 == 0 or index == total:
                log.info(
                    "industry member fetch progress",
                    source=SOURCE_THS,
                    done=index,
                    total=total,
                    members=len(members),
                )
        # 先去重：防止某板块分页泄漏到相邻板块（同 symbol 出现在多个行业代码下）。
        unique_members = tuple(dict.fromkeys(members))
        # 行业成员互斥（同花顺行业也应一只股票只属一个行业）。
        validate_unique_industry(unique_members)
        # 全部行业板块成分数为 0 → 上游异常；同上保护旧快照不被空数据覆盖。
        if not unique_members:
            raise SectorValidationError(
                f"全部 {total} 个行业板块未拉到任何成分，保留旧快照不写入空数据"
            )
        log.info(
            "industry member fetch done",
            source=SOURCE_THS,
            members=len(unique_members),
        )
        return unique_members

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
        total = len(catalog)
        log.info(
            "concept member fetch start",
            source=SOURCE_THS,
            total=total,
        )
        members: list[tuple[str, str]] = []
        skipped: list[str] = []
        for index, row in enumerate(catalog, start=1):
            try:
                rows = await self._call(
                    self._ths_board_members,
                    row.sector_code,
                    TAXONOMY_CONCEPT,
                    interval=interval,
                    timeout=self._THS_BOARD_TIMEOUT_SECONDS,
                )
            except (SectorCookieError, SectorMemberIncompleteError):
                # 登录态失效与完整性闸门是整批失败条件，不允许按「单板块跳过」处理。
                raise
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
            if index % 10 == 0 or index == total:
                log.info(
                    "concept member fetch progress",
                    source=SOURCE_THS,
                    done=index,
                    total=total,
                    members=len(members),
                    skipped=len(skipped),
                )
        log.info(
            "concept member fetch done",
            source=SOURCE_THS,
            ok=total - len(skipped),
            skipped=len(skipped),
            members=len(members),
        )
        # 全部板块拉取失败 → 上游整体不可用（限流/网络）；此时若以空 members 落库会把
        # 已有概念成分全部 close，造成不可逆的数据丢失。直接抛错让整批回滚，保留旧快照。
        if skipped and len(skipped) == total:
            raise SectorValidationError(
                f"全部 {total} 个概念板块成分拉取失败，保留旧快照不写入空数据"
            )
        # 概念成分跨板块天然可能重复（同 symbol 属多个概念），但同板块内不应重复。
        # 这里统一去重，防止上游分页重叠造成同 (sector_code, symbol) 出现两次。
        return tuple(dict.fromkeys(members))

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
        """按目录 name 拉取行业指数日线（同步/排障用；概念本期不回填，设计 §4.2-1）。

        单板块失败跳过不阻断（与成分同步同策略）；目录缺失的代码忽略，
        由读路径按覆盖判定降级。
        """
        codes = [code for taxonomy, code in sector_keys if taxonomy == TAXONOMY_INDUSTRY]
        if not codes:
            return ()
        catalog = await self._fetch_industry_catalog()
        names = {row.sector_code: row.name for row in catalog}
        boards = [(code, names[code]) for code in codes if code in names]
        bars, _skipped = await self._fetch_board_bars(boards, start=start, end=end)
        return bars

    def _ths_board_bars(self, sector_code: str, start: date, end: date) -> list[SectorBar]:
        """按年拉取行业指数日线（`d.10jqka.com.cn` JSONP），带登录 cookie 与 UA。"""
        cookie = self._require_cookie()
        records: list[dict[str, Any]] = []
        referer = f"http://q.10jqka.com.cn/thshy/detail/code/{sector_code}/"
        for year in range(start.year, end.year + 1):
            url = f"https://d.10jqka.com.cn/v4/line/bk_{sector_code}/01/{year}.js"
            resp = self._ths_get(url, cookie=cookie, referer=referer)
            payload = _parse_quote_bridge(resp.text, sector_code, year)
            for item in str(payload["data"]).split(";"):
                fields = item.split(",")
                if len(fields) < 5 or not fields[0].strip() or not fields[4].strip():
                    continue
                raw_date = fields[0].strip()
                try:
                    trade_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
                except (ValueError, IndexError) as exc:
                    raise SectorValidationError(
                        f"行业指数 {sector_code} 日期无法解析: {raw_date!r}"
                    ) from exc
                if start <= trade_date <= end:
                    records.append({"日期": trade_date, "收盘价": fields[4]})
        records.sort(key=lambda rec: rec["日期"])
        return _board_bar_records(TAXONOMY_INDUSTRY, sector_code, records)

    async def _fetch_board_bars(
        self, boards: Sequence[tuple[str, str]], *, start: date, end: date
    ) -> tuple[tuple[SectorBar, ...], list[str]]:
        """按 (sector_code, name) 拉取行业指数日线；单板块失败跳过（与成分同步同策略）。

        名称仅用于日志（接口按板块代码取数）；登录态缺失/失效整批失败，不跳过。
        """
        self._require_cookie()
        interval = self._settings.sector_ths_call_interval_seconds
        bars: list[SectorBar] = []
        skipped: list[str] = []
        for sector_code, name in boards:
            try:
                rows = await self._call(
                    self._ths_board_bars,
                    sector_code,
                    start,
                    end,
                    interval=interval,
                    timeout=self._THS_BOARD_TIMEOUT_SECONDS,
                )
            except SectorCookieError:
                raise
            except Exception as exc:
                skipped.append(sector_code)
                log.warning(
                    "industry bars fetch skipped",
                    sector_code=sector_code,
                    name=name,
                    error=str(exc)[:120],
                )
                continue
            bars.extend(rows)
        if skipped:
            log.warning(
                "industry bars fetch done",
                ok=len(boards) - len(skipped),
                skipped=len(skipped),
            )
        return tuple(bars), skipped

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
        bars_start: date | None = None,
    ) -> SyncReport:
        wanted = _wanted_taxonomies(taxonomies)
        if dry_run:
            async with self._session_factory(begin=True) as session:
                await SectorRepository(session).list_catalog()
            return SyncReport(
                as_of=today,
                status="skipped",
                message=(
                    f"dry-run: 配置/DB 可用；数据集="
                    f"{','.join(_taxonomy_label(t) for t in wanted)}；"
                    f"目标 as_of={today.isoformat()}；不联网、不写库"
                ),
            )
        try:
            snapshot = await self._build_snapshot(wanted)
        except (SectorValidationError, RuntimeError) as exc:
            await self._record_failure(today=today, detail=str(exc))
            return SyncReport(as_of=today, status="failed", message=str(exc))

        try:
            written = await self._persist(snapshot, today=today)
        except Exception as exc:
            await self._record_failure(today=today, detail=f"persist failed: {exc}")
            return SyncReport(as_of=today, status="failed", message=str(exc))

        status = "succeeded" if written else "skipped"
        message = snapshot.checksum if written else "checksum unchanged（no-op）"
        if bars_start is not None and TAXONOMY_INDUSTRY in wanted:
            # 板块日线回填（--start 生效，设计 §4.2-3）：只回填行业（概念 M1 不用）。
            # 与目录/成分相互独立：日线落盘失败不影响已成功提交的目录快照，下次重跑幂等续传。
            boards = [
                (row.sector_code, row.name)
                for row in snapshot.catalog
                if row.taxonomy == TAXONOMY_INDUSTRY
            ]
            try:
                bars, skipped = await self._fetch_board_bars(boards, start=bars_start, end=today)
                bar_as_of = await self._persist_bars(
                    bars, fetch_start=bars_start, requested_end=today
                )
            except Exception as exc:
                await self._record_failure(today=today, detail=f"persist bars failed: {exc}")
                return SyncReport(as_of=today, status="failed", message=f"板块日线落盘失败: {exc}")
            coverage = f"，bar_as_of={bar_as_of.isoformat()}" if bar_as_of else "（无数据落盘）"
            message += (
                f"；板块日线 {len(bars)} 根/{len({bar.sector_code for bar in bars})} 板块{coverage}"
            )
            if skipped:
                message += f"，单板块失败跳过 {len(skipped)} 个"
            if bars:
                status = "succeeded"
        return SyncReport(
            as_of=today,
            status=status,
            entity_count=len(snapshot.catalog),
            member_count=sum(len(items) for items in snapshot.members.values()),
            message=message,
        )

    async def sync_bars_to_cache(
        self, *, today: date, start: date | None = None, dry_run: bool = False
    ) -> SyncReport:
        """只同步行业板块指数日线（``make sync-sector-bars``），不重抓目录/成分。

        与 ``sync_to_cache`` 完全独立：目录/成分/基准权重一行不写，只 upsert
        ``market.sector_bar`` + ``sector_pull_range`` 并推进 ``bar_as_of``。
        ``start`` 缺省时从 ``bar_as_of`` 次日续传；行业目录缓存为空则拒绝执行
        （先 ``make sync-sector-industry``）。
        """
        async with self._session_factory(begin=True) as session:
            repo = SectorRepository(session)
            catalog = await repo.list_catalog(taxonomy=TAXONOMY_INDUSTRY)
            state = await repo.sync_state(dataset_key=DATASET_KEY)
        boards = [(row.sector_code, row.name) for row in catalog]
        if not boards:
            return SyncReport(
                as_of=today,
                status="failed",
                message="market.sector 无行业目录缓存：请先 make sync-sector-industry",
            )
        effective_start = start
        if effective_start is None and state is not None:
            effective_start = (
                state.bar_as_of + timedelta(days=1) if state.bar_as_of is not None else None
            )
        if effective_start is None:
            return SyncReport(
                as_of=today,
                status="failed",
                message="缺少起始日：无 bar_as_of 水位可续传，请显式传 --start",
            )
        if dry_run:
            return SyncReport(
                as_of=today,
                status="skipped",
                message=(
                    f"dry-run: 配置/DB 可用；行业板块 {len(boards)} 个；"
                    f"目标区间 {effective_start.isoformat()}~{today.isoformat()}；不联网、不写库"
                ),
            )
        if effective_start > today:
            return SyncReport(
                as_of=today,
                status="skipped",
                message=f"bar_as_of 已是最新（{today.isoformat()}），无需回填",
            )
        try:
            bars, skipped = await self._fetch_board_bars(boards, start=effective_start, end=today)
            bar_as_of = await self._persist_bars(
                bars, fetch_start=effective_start, requested_end=today
            )
        except Exception as exc:
            await self._record_failure(today=today, detail=f"persist bars failed: {exc}")
            return SyncReport(as_of=today, status="failed", message=f"板块日线落盘失败: {exc}")
        coverage = f"，bar_as_of={bar_as_of.isoformat()}" if bar_as_of else "（无数据落盘）"
        message = f"板块日线 {len(bars)} 根/{len({bar.sector_code for bar in bars})} 板块{coverage}"
        if skipped:
            message += f"，单板块失败跳过 {len(skipped)} 个"
        return SyncReport(as_of=today, status="succeeded" if bars else "skipped", message=message)

    async def _build_snapshot(self, wanted: tuple[str, ...]) -> _Snapshot:
        """按 `wanted` 只抓取需要的 taxonomy（行业/概念可分开同步，互不拖累）。"""
        # 成分抓取一律需要登录态：缺失即 fail-fast，避免先抓目录再半途失败。
        self._require_cookie()
        industry: tuple[SectorInfo, ...] = ()
        concepts: tuple[SectorInfo, ...] = ()
        if TAXONOMY_INDUSTRY in wanted:
            industry = await self._fetch_industry_catalog()
            # 同花顺行业为扁平 L1（非申万 L1/L2/L3 树），只校验 level=1。
            validate_catalog(industry, level=1)
        if TAXONOMY_CONCEPT in wanted:
            concepts = await self._fetch_concept_catalog()
            validate_catalog(concepts, level=0)

        members: dict[str, tuple[tuple[str, str], ...]] = {}
        if industry:
            members[TAXONOMY_INDUSTRY] = await self._fetch_industry_members(industry)
        if concepts:
            members[TAXONOMY_CONCEPT] = await self._fetch_concept_members(concepts)

        catalog = industry + concepts
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
                # 防御性去重：即使上游已去重，落库前再次确保同批次无重复。
                members = tuple(dict.fromkeys(members))
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
            # 基准成分权重按日全量快照：关闭上一版 current 行，再写入当日新行。
            # 关键：当日已存在 `effective_from=today` 的行（同日内重跑/分 taxonomy 多次同步）
            # 必须跳过 insert，否则撞主键 `(benchmark_id, symbol, effective_from)`。
            current_benchmark = (
                await session.scalars(
                    select(BenchmarkMember).where(
                        BenchmarkMember.benchmark_id == BENCHMARK_ID,
                        BenchmarkMember.effective_to.is_(None),
                    )
                )
            ).all()
            existing_today = {
                bm_row.symbol for bm_row in current_benchmark if bm_row.effective_from == today
            }
            # 关闭「非今日」的 current 行（跨日重跑需要替换）以及已退出基准的 symbol。
            for bm_row in current_benchmark:
                if bm_row.effective_from < today or bm_row.symbol not in weights:
                    bm_row.effective_to = today
            await session.flush()
            for symbol, units in weights.items():
                if symbol in existing_today:
                    continue
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
                # 目录同步不改写日线新鲜度（bar_as_of 只由 _persist_bars 推进）。
                bar_as_of=state.bar_as_of if state is not None else None,
            )
        return True

    async def _persist_bars(
        self,
        bars: Sequence[SectorBar],
        *,
        fetch_start: date,
        requested_end: date,
    ) -> date | None:
        """落盘行业指数日线 + 覆盖元数据（SectorPullRange 语义对齐 ``market.pull_range``）。

        幂等：bar 主键 ``(taxonomy, sector_code, trade_date)`` merge upsert；
        pull_range 同 ``(fetch_start, range_end)`` 刷新、区间推进各自成新行（并集）；
        ``bar_as_of`` 只前进不回退。返回最新 bar 日期（无数据返回 ``None``）。
        """
        if not bars:
            return None
        pulled_at = self._clock.now()
        by_board: dict[tuple[str, str], list[SectorBar]] = {}
        for bar in bars:
            by_board.setdefault((bar.taxonomy, bar.sector_code), []).append(bar)
        async with self._session_factory(begin=True) as session:
            repo = SectorRepository(session)
            state = await repo.sync_state(dataset_key=DATASET_KEY)
            max_bar_date: date | None = None
            for (taxonomy, sector_code), rows in sorted(by_board.items()):
                data_end = max(row.trade_date for row in rows)
                max_bar_date = data_end if max_bar_date is None else max(max_bar_date, data_end)
                # 只覆盖到上游实际数据；区间推进后旧行保留（并集），尾部由下次同步刷新。
                range_end = max(fetch_start, min(data_end, requested_end))
                await repo.upsert_bars(
                    [
                        SectorBarRow(
                            taxonomy=row.taxonomy,
                            sector_code=row.sector_code,
                            trade_date=row.trade_date,
                            close_units=row.close_units,
                            return_units=row.return_units,
                            source=SOURCE_THS,
                            pulled_at=pulled_at,
                        )
                        for row in rows
                    ]
                )
                await session.merge(
                    SectorPullRange(
                        id=hashlib.sha256(
                            f"{taxonomy}|{sector_code}|"
                            f"{fetch_start.isoformat()}|{range_end.isoformat()}".encode()
                        ).hexdigest()[:32],
                        taxonomy=taxonomy,
                        sector_code=sector_code,
                        range_start=fetch_start,
                        range_end=range_end,
                        data_end=data_end,
                        requested_end=requested_end,
                        complete=range_end >= requested_end,
                        bar_count=len(rows),
                        source=SOURCE_THS,
                        checksum=_bars_checksum(rows),
                        pulled_at=pulled_at,
                    )
                )
            bar_as_of: date | None = None
            if max_bar_date is not None:
                bar_as_of = (
                    max(max_bar_date, state.bar_as_of)
                    if state is not None and state.bar_as_of is not None
                    else max_bar_date
                )
            if state is not None:
                # 只推进 bar_as_of，其余字段原样保留（含 status/as_of/checksum）。
                await repo.upsert_sync_state(
                    dataset_key=DATASET_KEY,
                    status=state.status,
                    as_of=state.as_of,
                    snapshot_id=state.snapshot_id,
                    catalog_as_of=state.catalog_as_of,
                    membership_as_of=state.membership_as_of,
                    weight_as_of=state.weight_as_of,
                    classification_version=state.classification_version,
                    entity_count=state.entity_count,
                    member_count=state.member_count,
                    source=state.source,
                    checksum=state.checksum,
                    pulled_at=pulled_at,
                    error_detail=state.error_detail,
                    bar_as_of=bar_as_of,
                )
        return bar_as_of

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


def _wanted_taxonomies(taxonomies: Sequence[str] | None) -> tuple[str, ...]:
    """归一 `--taxonomies`：默认全部；未知值直接报错，避免静默写错集合。

    CLI/运维习惯用短名 ``industry`` / ``concept``，DB taxonomy 是
    ``industry_sw`` / ``concept_em``（沿用建表时的取值），两种写法都接受。
    """
    if not taxonomies:
        return (TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT)
    aliases = {
        "industry": TAXONOMY_INDUSTRY,
        "concept": TAXONOMY_CONCEPT,
        TAXONOMY_INDUSTRY: TAXONOMY_INDUSTRY,
        TAXONOMY_CONCEPT: TAXONOMY_CONCEPT,
    }
    wanted: set[str] = set()
    for item in taxonomies:
        resolved = aliases.get(item)
        if resolved is None:
            raise SectorValidationError(f"未知 taxonomy: {item}（可用 industry / concept）")
        wanted.add(resolved)
    return tuple(t for t in (TAXONOMY_INDUSTRY, TAXONOMY_CONCEPT) if t in wanted)


def _taxonomy_label(taxonomy: str) -> str:
    """DB taxonomy → 运维短名（日志/报告用）。"""
    return {
        TAXONOMY_INDUSTRY: "industry",
        TAXONOMY_CONCEPT: "concept",
    }.get(taxonomy, taxonomy)


__all__ = [
    "DATASET_KEY",
    "AkshareSectorSource",
    "CachedSectorSource",
    "akshare_available",
]
