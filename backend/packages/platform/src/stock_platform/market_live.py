"""akshare 行情适配器 + DB 缓存装饰器（`MARKET_MODE=live`）。

设计见 `docs/architecture/2026-10-22-live-market-data-source.md`：

- **估值口径**：v1 只落**不复权**日线（真实市价）。qfq 历史价时变、且会把分红折进
  价格，与账单中的分红现金流重复计入；hfq/因子快照留后续期次（ADR-0025）。
- **缓存**：`market.daily_bar`（幂等 upsert）+ `market.pull_range`（覆盖判定）。
  覆盖判定用**区间并集**；最近 `tail_refetch_days` 个自然日永远重拉（当月不漏新 bar）；
  正缓存 TTL 30 天、负缓存（空结果）短 TTL 1 天。
- **错误映射**：不新增错误码；瞬时故障重试耗尽后抛 `INTERNAL_ERROR` +
  `metadata={"market_failure": "unavailable"}`；不支持的标的抛 `NOT_FOUND` +
  `not_supported`；坏数据抛 `INTERNAL_ERROR` + `invalid_data`。NAV 侧据此把
  `blocked_reason` 分成三类。
- **禁止 float 进入内部**：akshare 数值列是 float64，进内部前一律
  `Decimal(str(x))` → 标度整数（`ROUND_HALF_UP`，超范围/NaN fail-closed）。

本模块只被 `stock_ledger.market.create_market_source()` 装配；**顶层不 import
akshare/pandas**（无 optional extra 时 replay 模式仍可 import）。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import SHANGHAI, Clock
from stock_contracts.constants import SCALE_PRICE, SCALE_RATIO
from stock_contracts.errors import ApiError, ErrorCode

from stock_platform.config import Settings
from stock_platform.market_models import DailyBar, PullRange
from stock_platform.obs import (
    MARKET_CACHE_HITS,
    MARKET_CACHE_STALE_FALLBACKS,
    MARKET_FETCH_ERRORS,
    MARKET_FETCH_SECONDS,
    MARKET_FETCH_TOTAL,
    get_logger,
)
from stock_platform.ports import Bar, MarketCapabilities, MarketDataSource, Quote
from stock_platform.symbols import (
    CLASS_BOND,
    CLASS_FUND,
    bond_symbol,
    classify_symbol,
    is_convertible_bond,
)

log = get_logger("stock_platform.market")

# ── 口径常量（v1 不复权；adjust/adjust_version 为后续 hfq/因子预留）─────
ADJUST: str = ""
ADJUST_VERSION: str = ""
SOURCE: str = "akshare"
FAILURE_KEY: str = "market_failure"
FAILURE_UNAVAILABLE: str = "unavailable"
FAILURE_NOT_SUPPORTED: str = "not_supported"
FAILURE_INVALID_DATA: str = "invalid_data"

_MAX_UNITS = 2**63 - 1
_SYMBOL_RE = re.compile(r"^\d{6}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")
# 品种分类（A 股/场内基金/债券）见 `stock_platform.symbols`：行情源与估值兜底共用同一
# 真相来源，避免「行情支持哪些品种」与「哪些品种可按买入价兜底」两处口径漂移。
# akshare/requests 的瞬时故障（可重试）异常名与模块根；不 import requests 也能识别。
_TRANSIENT_EXC_NAMES: frozenset[str] = frozenset(
    {
        "Timeout",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectionError",
        "RemoteDisconnected",
        "ProtocolError",
        "ChunkedEncodingError",
        "SSLError",
        "HTTPError",
        "JSONDecodeError",
        "IncompleteRead",
    }
)
_TRANSIENT_MODULES: frozenset[str] = frozenset(
    {"requests", "urllib3", "http", "socket", "curl_cffi", "ssl"}
)
_TRANSIENT_RETRY_SLEEP_BASE = 1.0

# `Database.session` 的形状（无租户系统的公共数据会话）。
SessionFactory = Callable[..., AbstractAsyncContextManager[AsyncSession]]


# ── 异常分类（适配器内部）──────────────────────────────────────
class _TransientMarketError(RuntimeError):
    """上游瞬时故障（超时/连接/5xx/JSON 解析），可重试。"""


class _InvalidMarketDataError(RuntimeError):
    """上游返回了不可用的数据（缺列/NaN/负价/解析失败），不可重试。"""


# ── 通用工具 ─────────────────────────────────────────────────
def _market_error(code: ErrorCode, *, reason: str, detail: str) -> ApiError:
    return ApiError(code, detail=detail, metadata={FAILURE_KEY: reason})


def _invalid_data(detail: str) -> _InvalidMarketDataError:
    return _InvalidMarketDataError(detail)


def _max_units_guard(units: int, *, where: str) -> int:
    if not -_MAX_UNITS <= units <= _MAX_UNITS:
        raise _invalid_data(f"{where}: 标度整数 {units} 超出 BigInteger 范围")
    return units


def _to_units(raw: object, *, scale: int, where: str) -> int:
    """上游数值 → 标度整数；float 只在边界经 `str()` 转 Decimal（禁止 float 进内部）。"""
    if raw is None or isinstance(raw, bool):
        raise _invalid_data(f"{where}: 缺失或非数值，实际 {raw!r}")
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise _invalid_data(f"{where}: 非有限数值 {raw!r}")
        text = str(raw)
    elif isinstance(raw, (int, str, Decimal)):
        text = str(raw).strip()
    else:
        raise _invalid_data(f"{where}: 非法数值类型 {type(raw).__name__}")
    try:
        value = Decimal(text)
    except DecimalException as exc:  # pragma: no cover - 防御分支
        raise _invalid_data(f"{where}: 无法解析数值 {raw!r}") from exc
    if not value.is_finite():
        raise _invalid_data(f"{where}: 非有限数值 {raw!r}")
    units = int(value.scaleb(scale).to_integral_value(rounding=ROUND_HALF_UP))
    return _max_units_guard(units, where=where)


def _ratio_units(previous_units: int, current_units: int) -> int:
    """相邻收盘的日收益（scale=8, ROUND_HALF_UP）。"""
    ratio = Decimal(current_units) / Decimal(previous_units) - Decimal(1)
    units = int(ratio.scaleb(SCALE_RATIO).to_integral_value(rounding=ROUND_HALF_UP))
    return _max_units_guard(units, where="return")


def _row_date(raw: object, *, where: str) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            if _ISO_DATE_RE.fullmatch(text):
                return date.fromisoformat(text)
            if _COMPACT_DATE_RE.fullmatch(text):
                return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
        except ValueError as exc:
            raise _invalid_data(f"{where}: 非法日期 {raw!r}") from exc
    raise _invalid_data(f"{where}: 非法日期 {raw!r}")


def validate_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    """校验入参并返回去重后的标的（保持输入顺序）。

    与 replay 一致：空/非法参数 → `VALIDATION_ERROR`；不在支持范围 → `NOT_FOUND`
    + `market_failure="not_supported"`（NAV 侧降级为 blocked，不让整段 404）。
    支持范围：A 股主板/科创板/创业板 + 场内基金（ETF/LOF/REITs，沪 5、深 15/16/18）
    + 沪深交易所债券（国债/企业债/公司债/可转债）。
    """
    if not symbols:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="symbols 不能为空")
    wanted: dict[str, None] = {}
    for raw in symbols:
        if not isinstance(raw, str) or not raw:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"非法 symbol: {raw!r}")
        symbol = raw.strip()
        if not _SYMBOL_RE.fullmatch(symbol) or classify_symbol(symbol) is None:
            raise _market_error(
                ErrorCode.NOT_FOUND,
                reason=FAILURE_NOT_SUPPORTED,
                detail=(
                    f"标的不在支持范围：{symbol}（支持 A 股主板/科创板/创业板、场内基金"
                    " ETF/LOF/REITs 与沪深债券；B 股/北交所/港美股暂不支持）"
                ),
            )
        wanted[symbol] = None
    return tuple(wanted)


def _symbol_to_sina(symbol: str) -> str:
    """601688 → sh601688（沪市 6/5 开头用 sh，其余深市用 sz）。

    股票（6→沪）与基金（5→沪、1→深）都适用；腾讯源 `_normalize_tx_symbol`
    同样接受 sh/sz 前缀，故该函数复用于腾讯兜底源。
    """
    if symbol.startswith(("6", "5")):
        return f"sh{symbol}"
    return f"sz{symbol}"


# 行情源列名：东财（eastmoney）为中文；新浪/腾讯/新浪基金/债券均为英文 date/close。
_SOURCE_COLUMNS: dict[str, tuple[str, str]] = {
    "eastmoney": ("日期", "收盘"),
    "sina": ("date", "close"),
    "tx": ("date", "close"),
    "fund_sina": ("date", "close"),
    "bond": ("date", "close"),
}


def _parse_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source: str = "eastmoney",
) -> tuple[list[tuple[date, int, int]], date | None]:
    """行情源行字典 → `[(trade_date, close_units, return_units)]`。

    列名按 `_SOURCE_COLUMNS` 映射；只依赖日期/收盘价两列；NaN/None/负价/重复日期
    一律 fail-closed。`return_units` 由 close 序列派生（首根 0），与 `close_units`
    严格自洽。
    """
    date_key, close_key = _SOURCE_COLUMNS[source]
    parsed: list[tuple[date, int]] = []
    for index, row in enumerate(rows):
        trade_date = _row_date(row.get(date_key), where=f"{source} row[{index}].{date_key}")
        close_units = _to_units(
            row.get(close_key), scale=SCALE_PRICE, where=f"{source} row[{index}].{close_key}"
        )
        if close_units <= 0:
            raise _invalid_data(
                f"{source} row[{index}].收盘: 收盘价必须为正，实际 {row.get(close_key)!r}"
            )
        parsed.append((trade_date, close_units))
    if not parsed:
        return [], None
    parsed.sort(key=lambda item: item[0])
    bars: list[tuple[date, int, int]] = []
    previous: int | None = None
    previous_date: date | None = None
    for trade_date, close_units in parsed:
        if previous_date is not None and trade_date <= previous_date:
            raise _invalid_data(f"{source} 上游日期非严格递增：{trade_date} <= {previous_date}")
        return_units = 0 if previous is None else _ratio_units(previous, close_units)
        bars.append((trade_date, close_units, return_units))
        previous, previous_date = close_units, trade_date
    return bars, bars[-1][0]


def _bar_checksum(bars: Sequence[tuple[date, int, int]]) -> str:
    payload = "|".join(
        f"{trade_date.isoformat()}:{close_units}" for trade_date, close_units, _ in bars
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_intervals(ranges: Sequence[tuple[date, date]]) -> list[tuple[date, date]]:
    ordered = sorted((start, end) for start, end in ranges if start <= end)
    merged: list[tuple[date, date]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + timedelta(days=1):
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def plan_fetch(
    ranges: Sequence[tuple[date, date]],
    *,
    start: date,
    end: date,
    today: date,
    tail_refetch_days: int,
) -> list[tuple[date, date]]:
    """计算需要从上游拉取的缺口（纯函数）。

    - 已覆盖区间先合并成并集；
    - 最近 `tail_refetch_days` 个自然日**永远算缺口**（当月不漏新 bar）；
    - 缺口合并成一次 `[gaps[0].start, gaps[-1].end]` 拉取（跨过已覆盖段，upsert 幂等）。
    """
    if end < start:
        return []
    tail_start = max(start, today - timedelta(days=max(0, tail_refetch_days)))
    head = [
        (range_start, min(range_end, tail_start - timedelta(days=1)))
        for range_start, range_end in _merge_intervals(ranges)
        if range_start < tail_start
    ]
    merged_head = _merge_intervals(head)
    gaps: list[tuple[date, date]] = []
    cursor = start
    for range_start, range_end in merged_head:
        if range_end < cursor:
            continue
        if range_start > end:
            break
        if range_start > cursor:
            gaps.append((cursor, min(range_start - timedelta(days=1), end)))
        cursor = max(cursor, range_end + timedelta(days=1))
        if cursor > end:
            break
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


# ── akshare 适配器 ───────────────────────────────────────────
def akshare_available() -> bool:
    """启动期检查（不触发 akshare 的秒级重 import）。"""
    return importlib.util.find_spec("akshare") is not None


def _import_akshare() -> Any:
    try:
        return importlib.import_module("akshare")
    except ImportError as exc:  # pragma: no cover - 由启动期检查兜底
        raise RuntimeError(
            "MARKET_MODE=live 需要 akshare：请执行 `uv sync --extra live` 后重启"
        ) from exc


def _akshare_version() -> str:
    try:
        module = _import_akshare()
    except RuntimeError:  # pragma: no cover - 启动期已拦截
        return "unavailable"
    return str(getattr(module, "__version__", "unknown"))


def _is_transient_failure(exc: BaseException) -> bool:
    for cls in type(exc).__mro__:
        if cls.__name__ in _TRANSIENT_EXC_NAMES:
            return True
        if cls.__module__.split(".")[0] in _TRANSIENT_MODULES:
            return True
    return False


class AkshareMarketDataSource:
    """akshare 纯适配（无 DB）。同步 API 用 `asyncio.to_thread` 隔离。"""

    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._settings = settings
        self._clock = clock
        self._timeout_seconds = settings.market_live_timeout_seconds
        self._attempts = max(1, settings.market_live_retry_attempts)
        self._backoff_seconds = max(0.0, settings.market_live_retry_backoff_seconds)
        self._semaphore: asyncio.Semaphore | None = None
        self._last_bar_date: date | None = None

    # ── 端口属性 ────────────────────────────────────────
    @property
    def capabilities(self) -> MarketCapabilities:
        # symbols=() 表示「无静态标的清单」：调用方不得据此拒绝标的。
        return MarketCapabilities(
            mode="live",
            symbols=(),
            supports_daily_bars=True,
            supports_latest_quote=True,
            supports_adjusted=False,
            max_lookback_days=None,
        )

    @property
    def as_of(self) -> date:
        # 同步 property 不查 DB/网络：本进程最近一次成功取数覆盖到的最后交易日；
        # 尚无成功取数时为「业务今天」（近似值，权威覆盖由 pull_range 判定）。
        return self._last_bar_date or self._clock.today()

    @property
    def provenance(self) -> str:
        return f"live:{SOURCE}@{_akshare_version()}"

    # ── 端口方法 ────────────────────────────────────────
    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        wanted = validate_symbols(symbols)
        if start > end:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"start({start}) 不能晚于 end({end})")
        if start > self._clock.today():
            return ()
        collected: list[Bar] = []
        results = await asyncio.gather(*(self._bars_for(symbol, start, end) for symbol in wanted))
        for bars in results:
            collected.extend(bars)
        return tuple(sorted(collected, key=lambda bar: (bar.trade_date, bar.symbol)))

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        wanted = validate_symbols(symbols)
        end = self._clock.today()
        bars = await self.get_daily_bars(wanted, end - timedelta(days=10), end)
        last: dict[str, Bar] = {}
        for bar in bars:
            last[bar.symbol] = bar  # bars 已按 (date, symbol) 升序，直接覆盖即最新
        return tuple(
            Quote(symbol=symbol, as_of=bar.trade_date, close_units=bar.close_units)
            for symbol, bar in sorted(last.items())
        )

    # ── 内部 ────────────────────────────────────────────
    async def _bars_for(self, symbol: str, start: date, end: date) -> tuple[Bar, ...]:
        rows = await self._fetch_with_retry(symbol, start, end)
        if not rows:
            return ()
        if self._last_bar_date is None or rows[-1][0] > self._last_bar_date:
            self._last_bar_date = rows[-1][0]
        return tuple(
            Bar(
                symbol=symbol,
                trade_date=trade_date,
                close_units=close_units,
                return_units=return_units,
            )
            for trade_date, close_units, return_units in rows
        )

    def _semaphore_value(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, self._settings.market_live_max_concurrency))
        return self._semaphore

    async def _fetch_with_retry(
        self, symbol: str, start: date, end: date
    ) -> list[tuple[date, int, int]]:
        last_error: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            started = time.perf_counter()
            try:
                async with self._semaphore_value():
                    rows, _ = await asyncio.to_thread(self._fetch_units, symbol, start, end)
            except _TransientMarketError as exc:
                last_error = exc
                MARKET_FETCH_TOTAL.labels(result="error").inc()
                MARKET_FETCH_ERRORS.labels(reason=FAILURE_UNAVAILABLE).inc()
                log.warning(
                    "market.fetch",
                    symbol=symbol,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    attempt=attempt,
                    result="error",
                    reason=FAILURE_UNAVAILABLE,
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    error=str(exc),
                )
                if attempt < self._attempts:
                    await asyncio.sleep(self._backoff_seconds * (2 ** (attempt - 1)))
                    continue
            except _InvalidMarketDataError as exc:
                MARKET_FETCH_TOTAL.labels(result="error").inc()
                MARKET_FETCH_ERRORS.labels(reason=FAILURE_INVALID_DATA).inc()
                log.error(
                    "market.fetch",
                    symbol=symbol,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    attempt=attempt,
                    result="error",
                    reason=FAILURE_INVALID_DATA,
                    error=str(exc),
                )
                raise _market_error(
                    ErrorCode.INTERNAL_ERROR,
                    reason=FAILURE_INVALID_DATA,
                    detail=f"行情数据非法（{symbol}）：{exc}",
                ) from exc
            else:
                result = "ok" if rows else "empty"
                MARKET_FETCH_TOTAL.labels(result=result).inc()
                MARKET_FETCH_SECONDS.labels(result=result).observe(time.perf_counter() - started)
                log.info(
                    "market.fetch",
                    symbol=symbol,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    attempt=attempt,
                    result=result,
                    bars=len(rows),
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                )
                return rows
        raise _market_error(
            ErrorCode.INTERNAL_ERROR,
            reason=FAILURE_UNAVAILABLE,
            detail=f"行情源暂时不可用（{symbol}）：{last_error}",
        )

    def _fetch_units(
        self, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        """在线程内执行：lazy import akshare → 按品种路由 → 多源 fallback → 行解析。

        - 股票：东财 → 新浪 → 腾讯。腾讯保留退市股历史（新浪对退市股返回空响应），
          是退市股的最终兜底。
        - 基金：东财 `fund_etf_hist_em` → 新浪 `fund_etf_hist_sina`（后者无起止参数，
          全量返回后本地过滤）。
        - 债券：可转债走 `bond_zh_hs_cov_daily`，其余走 `bond_zh_hs_daily`（均无起止
          参数，全量返回后本地过滤）。akshare 只此两接口，不做多源交叉验证。
        - 多源 fallback：瞬时故障（超时/TLS RST/JSON 解析）降级到下一源；空结果也交叉
          验证下一源（单源空可能是停牌/退市/源缺口，全部源一致空才返回空，走负缓存）；
          InvalidData 说明源返回了东西但口径坏了，不盲目降级（可能掩盖问题）。
        """
        try:
            module = _import_akshare()
        except RuntimeError as exc:  # 运行期兜底：启动期已用 find_spec 拦截
            raise _TransientMarketError(str(exc)) from exc
        kind = classify_symbol(symbol)
        if kind == CLASS_FUND:
            sources = [self._fetch_fund_eastmoney, self._fetch_fund_sina]
        elif kind == CLASS_BOND:
            sources = [self._fetch_bond]
        else:
            sources = [self._fetch_eastmoney, self._fetch_sina, self._fetch_tx]
        last_exc: Exception | None = None
        all_transient = True
        for fetcher in sources:
            try:
                rows, data_end = fetcher(module, symbol, start, end)
            except _TransientMarketError as exc:
                last_exc = exc
                continue
            all_transient = False
            if rows:
                return rows, data_end
        if all_transient and last_exc is not None:
            raise _TransientMarketError(f"所有行情源均不可用：{last_exc}") from last_exc
        return [], None

    def _fetch_eastmoney(
        self, module: Any, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        try:
            frame = module.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=ADJUST,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            if _is_transient_failure(exc):
                raise _TransientMarketError(f"eastmoney {type(exc).__name__}: {exc}") from exc
            raise _invalid_data(f"eastmoney 调用失败：{type(exc).__name__}: {exc}") from exc
        if frame is None:
            return [], None
        try:
            rows = frame.to_dict("records")
        except Exception as exc:
            raise _invalid_data(f"eastmoney 返回结构非法：{type(exc).__name__}") from exc
        if not rows:
            return [], None
        return _parse_rows(rows, source="eastmoney")

    def _fetch_sina(
        self, module: Any, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        sina_symbol = _symbol_to_sina(symbol)
        try:
            # adjust="" 不复权（与 eastmoney stock_zh_a_hist 默认口径一致）：
            # 分红现金流已在账单侧单独进 NAV，不复权避免双计。
            frame = module.stock_zh_a_daily(
                symbol=sina_symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
            )
        except Exception as exc:
            if _is_transient_failure(exc):
                raise _TransientMarketError(f"sina {type(exc).__name__}: {exc}") from exc
            raise _invalid_data(f"sina 调用失败：{type(exc).__name__}: {exc}") from exc
        if frame is None:
            return [], None
        try:
            rows = frame.to_dict("records")
        except Exception as exc:
            raise _invalid_data(f"sina 返回结构非法：{type(exc).__name__}") from exc
        if not rows:
            return [], None
        return _parse_rows(rows, source="sina")

    def _fetch_tx(
        self, module: Any, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        """腾讯证券日频（退市股兜底）：新浪对退市股返回空响应，腾讯保留历史。

        腾讯按年循环拉取；对「查无此票/无上市信息」抛 IndexError/KeyError，
        映射为空结果（走负缓存），不判 invalid_data。
        """
        try:
            frame = module.stock_zh_a_hist_tx(
                symbol=_symbol_to_sina(symbol),
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=ADJUST,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            if _is_transient_failure(exc):
                raise _TransientMarketError(f"tx {type(exc).__name__}: {exc}") from exc
            if isinstance(exc, (IndexError, KeyError)):
                return [], None
            raise _invalid_data(f"tx 调用失败：{type(exc).__name__}: {exc}") from exc
        if frame is None:
            return [], None
        try:
            rows = frame.to_dict("records")
        except Exception as exc:
            raise _invalid_data(f"tx 返回结构非法：{type(exc).__name__}") from exc
        if not rows:
            return [], None
        return _parse_rows(rows, source="tx")

    def _fetch_fund_eastmoney(
        self, module: Any, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        """东财基金接口：与 `stock_zh_a_hist` 同 kline 端点、同中文列名。

        `fund_etf_hist_em` 无 timeout 参数（内部硬编码 15s），不覆盖默认超时。
        """
        try:
            frame = module.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=ADJUST,
            )
        except Exception as exc:
            if _is_transient_failure(exc):
                raise _TransientMarketError(f"fund_eastmoney {type(exc).__name__}: {exc}") from exc
            raise _invalid_data(f"fund_eastmoney 调用失败：{type(exc).__name__}: {exc}") from exc
        if frame is None:
            return [], None
        try:
            rows = frame.to_dict("records")
        except Exception as exc:
            raise _invalid_data(f"fund_eastmoney 返回结构非法：{type(exc).__name__}") from exc
        if not rows:
            return [], None
        return _parse_rows(rows, source="eastmoney")

    def _fetch_fund_sina(
        self, module: Any, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        """新浪基金接口：无起止参数、全量返回，本地按 `[start, end]` 过滤。

        returns 先在全序列上派生再过滤，窗口首根保留真实收益（与东财按窗口拉取、
        首根 return=0 的口径不同，但更准）。
        """
        try:
            frame = module.fund_etf_hist_sina(symbol=_symbol_to_sina(symbol))
        except Exception as exc:
            if _is_transient_failure(exc):
                raise _TransientMarketError(f"fund_sina {type(exc).__name__}: {exc}") from exc
            raise _invalid_data(f"fund_sina 调用失败：{type(exc).__name__}: {exc}") from exc
        if frame is None:
            return [], None
        try:
            rows = frame.to_dict("records")
        except Exception as exc:
            raise _invalid_data(f"fund_sina 返回结构非法：{type(exc).__name__}") from exc
        if not rows:
            return [], None
        bars, _ = _parse_rows(rows, source="fund_sina")
        filtered = [bar for bar in bars if start <= bar[0] <= end]
        if not filtered:
            return [], None
        return filtered, filtered[-1][0]

    def _fetch_bond(
        self, module: Any, symbol: str, start: date, end: date
    ) -> tuple[list[tuple[date, int, int]], date | None]:
        """沪深交易所债券：可转债走 `bond_zh_hs_cov_daily`，其余走 `bond_zh_hs_daily`。

        两接口均**没有起止参数**（全量返回），本地按 `[start, end]` 过滤；returns 先在
        全序列上派生再过滤（与 `_fetch_fund_sina` 同口径）。不存在的代码抛
        `KeyError('date')`（或 IndexError），映射为空结果走负缓存。已到期/退市债券
        仍保留历史数据，不丢段。
        """
        bond_code = bond_symbol(symbol)
        convertible = is_convertible_bond(symbol)
        label = "bond_cov" if convertible else "bond"
        try:
            if convertible:
                frame = module.bond_zh_hs_cov_daily(symbol=bond_code)
            else:
                frame = module.bond_zh_hs_daily(symbol=bond_code)
        except Exception as exc:
            if _is_transient_failure(exc):
                raise _TransientMarketError(f"{label} {type(exc).__name__}: {exc}") from exc
            if isinstance(exc, (KeyError, IndexError)):
                return [], None
            raise _invalid_data(f"{label} 调用失败：{type(exc).__name__}: {exc}") from exc
        if frame is None:
            return [], None
        try:
            rows = frame.to_dict("records")
        except Exception as exc:
            raise _invalid_data(f"{label} 返回结构非法：{type(exc).__name__}") from exc
        if not rows:
            return [], None
        bars, _ = _parse_rows(rows, source="bond")
        filtered = [bar for bar in bars if start <= bar[0] <= end]
        if not filtered:
            return [], None
        return filtered, filtered[-1][0]


# ── DB 缓存装饰器 ───────────────────────────────────────────
class CachedMarketDataSource:
    """`inner` + `market` schema 缓存：先查覆盖、缺口才拉、拉到即 upsert。"""

    def __init__(
        self,
        inner: MarketDataSource,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        clock: Clock,
    ) -> None:
        self._inner = inner
        self._settings = settings
        self._session_factory = session_factory
        self._clock = clock
        self._last_known_as_of: date | None = None

    # ── 端口属性 ────────────────────────────────────────
    @property
    def capabilities(self) -> MarketCapabilities:
        return self._inner.capabilities

    @property
    def as_of(self) -> date:
        # 同步 property 不能查 DB：用内存近似（读/写缓存时更新），未命中过则委托 inner。
        return self._last_known_as_of or self._inner.as_of

    @property
    def provenance(self) -> str:
        return f"{self._inner.provenance}#cache"

    # ── 端口方法 ────────────────────────────────────────
    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        wanted = validate_symbols(symbols)
        if start > end:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"start({start}) 不能晚于 end({end})")
        if start > self._clock.today():
            return ()
        # 默认只读本地缓存：读路径不触发任何第三方请求。行情由收盘后
        # `make sync-market` 显式同步（见 sync_daily_bars）。
        if self._settings.market_live_cache_read_only:
            return await self.get_cached_bars(wanted, start, end)
        collected: list[Bar] = []
        # 逐标的处理：每个标的的缺口判定/拉取/写入都是独立短事务（不持有 nav 外层事务）。
        for symbol in wanted:
            collected.extend(await self._symbol_bars(symbol, start, end))
        return tuple(sorted(collected, key=lambda bar: (bar.trade_date, bar.symbol)))

    async def sync_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> tuple[int, tuple[str, ...]]:
        """收盘后显式把本地缺口补到 ``end``；返回 (可读 bar 数, 失败标的)。

        - 只补 `market.daily_bar` 缺失区间，已有数据不重复请求第三方；
        - 盘中（东八区 16:30 前）不拉当天，``end`` 最多截到昨天；
        - 失败标的记录后继续，避免一颗坏代码阻断整批同步。
        """
        wanted = validate_symbols(symbols)
        if start > end:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"start({start}) 不能晚于 end({end})")
        today = self._clock.today()
        now = self._clock.now().astimezone(SHANGHAI)
        effective_end = min(end, today)
        if (now.hour, now.minute) < (16, 30) and effective_end >= today:
            effective_end = today - timedelta(days=1)
        # 18:00 后上游日线已基本定型：空结果可覆盖当天，避免同一晚重复请求；
        # 16:30~18:00 之间仍留当天缺口，给晚发布留重试机会。
        final_ready = (now.hour, now.minute) >= (18, 0)
        if effective_end < start:
            return 0, ()
        failed: list[str] = []
        for symbol in wanted:
            ranges = await self._load_ranges(symbol, include_stale=True)
            # today=effective_end+1 让 plan_fetch 不再把尾段当缺口：本地已覆盖就不请求。
            gaps = plan_fetch(
                ranges,
                start=start,
                end=effective_end,
                today=effective_end + timedelta(days=1),
                tail_refetch_days=0,
            )
            if not gaps:
                continue
            fetch_start, fetch_end = gaps[0][0], gaps[-1][1]
            try:
                fetched = await self._inner.get_daily_bars([symbol], fetch_start, fetch_end)
            except ApiError as exc:
                failed.append(symbol)
                log.warning(
                    "market.sync",
                    symbol=symbol,
                    start=fetch_start.isoformat(),
                    end=fetch_end.isoformat(),
                    result="failed",
                    error=str(exc),
                )
                continue
            rows = [(bar.trade_date, bar.close_units, bar.return_units) for bar in fetched]
            data_end = rows[-1][0] if rows else None
            if fetch_end >= today:
                # 当天空结果/未发布：只覆盖到昨天，今天留缺口供下一次 sync 重试。
                if data_end is None:
                    if fetch_start < today:
                        await self._store(
                            symbol,
                            fetch_start,
                            today - timedelta(days=1),
                            [],
                            None,
                            negative_covers_end=True,
                        )
                    if final_ready:
                        await self._store(
                            symbol,
                            today,
                            today,
                            [],
                            None,
                            negative_covers_end=True,
                        )
                else:
                    await self._store(
                        symbol, fetch_start, fetch_end, rows, data_end, negative_covers_end=True
                    )
                    if data_end < today - timedelta(days=1):
                        await self._store(
                            symbol,
                            data_end + timedelta(days=1),
                            today - timedelta(days=1),
                            [],
                            None,
                            negative_covers_end=True,
                        )
                    if final_ready and data_end < today:
                        await self._store(
                            symbol,
                            today,
                            today,
                            [],
                            None,
                            negative_covers_end=True,
                        )
                log.info(
                    "market.sync",
                    symbol=symbol,
                    start=fetch_start.isoformat(),
                    end=fetch_end.isoformat(),
                    bars=len(rows),
                    result="current_day_gap_retained",
                )
                continue
            await self._store(
                symbol,
                fetch_start,
                fetch_end,
                rows,
                data_end,
                negative_covers_end=True,
            )
            log.info(
                "market.sync",
                symbol=symbol,
                start=fetch_start.isoformat(),
                end=fetch_end.isoformat(),
                bars=len(rows),
                result="ok",
            )
        total = 0
        latest: date | None = None
        for symbol in wanted:
            bars = await self._load_bars(symbol, start, effective_end)
            total += len(bars)
            if bars and (latest is None or bars[-1].trade_date > latest):
                latest = bars[-1].trade_date
        if latest is not None and (
            self._last_known_as_of is None or latest > self._last_known_as_of
        ):
            self._last_known_as_of = latest
        return total, tuple(failed)

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        wanted = validate_symbols(symbols)
        end = self._clock.today()
        bars = await self.get_daily_bars(wanted, end - timedelta(days=10), end)
        last: dict[str, Bar] = {}
        for bar in bars:
            last[bar.symbol] = bar
        return tuple(
            Quote(symbol=symbol, as_of=bar.trade_date, close_units=bar.close_units)
            for symbol, bar in sorted(last.items())
        )

    async def get_cached_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> Sequence[Bar]:
        """只读缓存中的日线（不触发尾部补拉）。

        看板持仓必须与 ``asset_daily`` 快照同价：快照写入时已把当时价格落库，
        此处直接复用，避免盘中再次拉取导致「持仓市值与快照不一致」的假告警。
        """
        wanted = validate_symbols(symbols)
        if start > end:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"start({start}) 不能晚于 end({end})")
        collected: list[Bar] = []
        for symbol in wanted:
            collected.extend(await self._load_bars(symbol, start, end))
        if collected:
            last_date = max(bar.trade_date for bar in collected)
            if self._last_known_as_of is None or last_date > self._last_known_as_of:
                self._last_known_as_of = last_date
        return tuple(sorted(collected, key=lambda bar: (bar.trade_date, bar.symbol)))

    # ── 内部 ────────────────────────────────────────────
    async def _symbol_bars(self, symbol: str, start: date, end: date) -> tuple[Bar, ...]:
        today = self._clock.today()
        ranges = await self._load_ranges(symbol)
        gaps = plan_fetch(
            ranges,
            start=start,
            end=end,
            today=today,
            tail_refetch_days=self._settings.market_live_tail_refetch_days,
        )
        if gaps:
            MARKET_CACHE_HITS.labels(cache="miss").inc()
            fetch_start, fetch_end = gaps[0][0], gaps[-1][1]
            try:
                fetched = await self._inner.get_daily_bars([symbol], fetch_start, fetch_end)
            except ApiError as exc:
                # 缺口拉取失败：缓存中已有的 bar 仍然有效，不能丢弃。曾导致真实事故：
                # 已退市标的（如 600077）的尾部缺口 [退市日+1, 今天] 永久拉取失败，
                # 异常向上传播使整个标的失去全部缓存历史价，持有它的日期全部被判阻断。
                # 这里降级复用缓存；失败缺口不写 pull_range，下次构建自动重试。
                cached = await self._load_bars(symbol, start, end)
                if not cached:
                    raise
                MARKET_CACHE_STALE_FALLBACKS.inc()
                log.warning(
                    "market.cache",
                    symbol=symbol,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    result="stale_fallback",
                    gap_start=fetch_start.isoformat(),
                    gap_end=fetch_end.isoformat(),
                    bars=len(cached),
                    error=str(exc),
                )
            else:
                rows = [(bar.trade_date, bar.close_units, bar.return_units) for bar in fetched]
                data_end = rows[-1][0] if rows else None
                await self._store(symbol, fetch_start, fetch_end, rows, data_end)
                cached = await self._load_bars(symbol, start, end)
        else:
            MARKET_CACHE_HITS.labels(cache="hit").inc()
            log.info(
                "market.cache",
                symbol=symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                result="hit",
            )
            cached = await self._load_bars(symbol, start, end)
        if cached:
            last_date = cached[-1].trade_date
            if self._last_known_as_of is None or last_date > self._last_known_as_of:
                self._last_known_as_of = last_date
        return cached

    async def _load_ranges(
        self, symbol: str, *, include_stale: bool = False
    ) -> list[tuple[date, date]]:
        now = self._clock.now()
        ttl_cutoff = now - timedelta(days=self._settings.market_live_cache_ttl_days)
        negative_cutoff = now - timedelta(days=self._settings.market_live_negative_ttl_days)
        conditions = [
            PullRange.symbol == symbol,
            PullRange.adjust == ADJUST,
            PullRange.adjust_version == ADJUST_VERSION,
        ]
        if not include_stale:
            conditions.append(
                or_(
                    and_(PullRange.bar_count > 0, PullRange.pulled_at >= ttl_cutoff),
                    and_(PullRange.bar_count == 0, PullRange.pulled_at >= negative_cutoff),
                )
            )
        stmt = select(PullRange.range_start, PullRange.range_end).where(*conditions)
        async with self._session_factory(begin=True) as session:
            rows = (await session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def _load_bars(self, symbol: str, start: date, end: date) -> tuple[Bar, ...]:
        stmt = (
            select(
                DailyBar.trade_date,
                DailyBar.close_units,
                DailyBar.return_units,
            )
            .where(
                DailyBar.symbol == symbol,
                DailyBar.adjust == ADJUST,
                DailyBar.trade_date >= start,
                DailyBar.trade_date <= end,
            )
            .order_by(DailyBar.trade_date)
        )
        async with self._session_factory(begin=True) as session:
            rows = (await session.execute(stmt)).all()
        return tuple(
            Bar(
                symbol=symbol,
                trade_date=row[0],
                close_units=row[1],
                return_units=row[2],
            )
            for row in rows
        )

    async def _store(
        self,
        symbol: str,
        fetch_start: date,
        requested_end: date,
        rows: Sequence[tuple[date, int, int]],
        data_end: date | None,
        *,
        negative_covers_end: bool = False,
    ) -> None:
        pulled_at = self._clock.now()
        today = self._clock.today()
        if requested_end < today:
            # 已结束区间：非交易日无 bar 属正常，覆盖到请求上界，避免每次重拉。
            range_end = requested_end
        else:
            # 含未来日期：只覆盖到上游实际数据；尾部由 tail_refetch_days 兜底刷新。
            range_end = data_end if data_end is not None else today - timedelta(days=1)
            # 已结束区间（周中补历史）空结果覆盖到请求上界；当天仍留缺口，
            # 让上游晚发布时下一次 sync 能重试（不把“今天暂无数据”永久固化）。
            if negative_covers_end and data_end is None and requested_end < today:
                range_end = requested_end
        range_end = max(fetch_start, min(range_end, requested_end))
        complete = range_end >= requested_end
        checksum = _bar_checksum(rows)
        row_id = hashlib.sha256(
            (
                f"{symbol}|{ADJUST}|{ADJUST_VERSION}|"
                f"{fetch_start.isoformat()}|{range_end.isoformat()}"
            ).encode()
        ).hexdigest()[:32]
        async with self._session_factory(begin=True) as session:
            if rows:
                stmt = pg_insert(DailyBar).values(
                    [
                        {
                            "symbol": symbol,
                            "adjust": ADJUST,
                            "trade_date": trade_date,
                            "close_units": close_units,
                            "return_units": return_units,
                            "source": SOURCE,
                            "pulled_at": pulled_at,
                        }
                        for trade_date, close_units, return_units in rows
                    ]
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["symbol", "adjust", "trade_date"],
                    set_={
                        "close_units": stmt.excluded.close_units,
                        "return_units": stmt.excluded.return_units,
                        "source": stmt.excluded.source,
                        "pulled_at": stmt.excluded.pulled_at,
                        "updated_at": pulled_at,
                    },
                )
                await session.execute(stmt)
            pull_stmt = pg_insert(PullRange).values(
                {
                    "id": row_id,
                    "symbol": symbol,
                    "adjust": ADJUST,
                    "adjust_version": ADJUST_VERSION,
                    "range_start": fetch_start,
                    "range_end": range_end,
                    "data_end": data_end,
                    "requested_end": requested_end,
                    "complete": complete,
                    "source": SOURCE,
                    "pulled_at": pulled_at,
                    "bar_count": len(rows),
                    "checksum": checksum,
                }
            )
            pull_stmt = pull_stmt.on_conflict_do_update(
                constraint="uq_pull_range_scope",
                set_={
                    "pulled_at": pull_stmt.excluded.pulled_at,
                    "requested_end": pull_stmt.excluded.requested_end,
                    "complete": pull_stmt.excluded.complete,
                    "data_end": pull_stmt.excluded.data_end,
                    "bar_count": pull_stmt.excluded.bar_count,
                    "checksum": pull_stmt.excluded.checksum,
                    "updated_at": pulled_at,
                },
            )
            await session.execute(pull_stmt)
        log.info(
            "market.store",
            symbol=symbol,
            start=fetch_start.isoformat(),
            end=requested_end.isoformat(),
            covered_to=range_end.isoformat(),
            bars=len(rows),
            complete=complete,
        )


__all__ = [
    "ADJUST",
    "FAILURE_INVALID_DATA",
    "FAILURE_KEY",
    "FAILURE_NOT_SUPPORTED",
    "FAILURE_UNAVAILABLE",
    "AkshareMarketDataSource",
    "CachedMarketDataSource",
    "SessionFactory",
    "akshare_available",
    "plan_fetch",
    "validate_symbols",
]
