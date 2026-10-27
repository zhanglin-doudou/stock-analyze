"""行情 replay 适配器（G4；P2-04 行情适配与校验的前置）。

契约见 `stock_platform.ports.MarketDataSource`；本模块只提供 replay 实现与工厂，
不触碰 DB / 网络 / 系统时间。

数据源：`fixtures/market/quotes.json`（由 `stock_ledger.fixtures.generate_market_quotes()`
纯确定性生成：固定日期与排序、无运行时刻戳，重复生成字节一致）。

错误码映射（fail-closed，绝不把「读不到/读坏了」伪装成空数据）：

=====================================  ==============================  ==================
场景                                   抛出的 ErrorCode                 理由
=====================================  ==============================  ==================
夹具缺失 / 非 UTF-8 / JSON 坏 / 结构非法   INTERNAL_ERROR (500)            服务端夹具或部署问题
日期非法 / 定点字符串非法 / 超标度          INTERNAL_ERROR (500)            夹具数据质量，必须修数据
夹具含 `as_of` 之后的行                    INTERNAL_ERROR (500)            未来数据泄漏，拒绝加载
`start > end` / `symbols` 为空/非法        VALIDATION_ERROR (422)          调用参数问题
请求的 symbol 不在夹具内                   NOT_FOUND (404)                 replay 无该标的数据，禁止静默空
=====================================  ==============================  ==================

未设置 `app.user_id` 之类的租户语义不属于本模块：行情端口只读公开市场数据。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stock_contracts.clock import Clock, SystemClock
from stock_contracts.constants import SCALE_PRICE, SCALE_RATIO
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.config import Settings
from stock_platform.ports import Bar, MarketCapabilities, MarketDataSource, Quote

if TYPE_CHECKING:  # 仅类型检查；避免 replay 模式加载 sqlalchemy 装配模块
    from stock_platform.market_live import SessionFactory

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FIXED_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")
_FIXTURE_ERROR = ErrorCode.INTERNAL_ERROR
_DEFAULT_PROVENANCE = "deterministic-fixtures/v1"
# 标度整数最终落 BigInteger 列：超出 ±(2^63-1) 的夹具值必须 fail-closed。
_MAX_UNITS = 2**63 - 1


def _fixture_error(detail: str) -> ApiError:
    """夹具/配置类问题统一 500 fail-closed（不静默降级为空数据）。"""
    return ApiError(_FIXTURE_ERROR, detail=detail)


def _as_object(raw: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fixture_error(f"{where}: 必须是 JSON 对象，实际 {type(raw).__name__}")
    return raw


def _parse_date(raw: Any, *, where: str) -> date:
    if not isinstance(raw, str) or not _ISO_DATE_RE.fullmatch(raw):
        raise _fixture_error(f"{where}: 日期必须是 YYYY-MM-DD 字符串，实际 {raw!r}")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise _fixture_error(f"{where}: 非法日期 {raw!r}") from exc


def _parse_units(raw: Any, *, scale: int, where: str) -> int:
    """定点字符串 → 标度整数；超标度一律拒绝（禁止静默舍入），且拒绝 JSON float。"""
    if not isinstance(raw, str) or not _FIXED_RE.fullmatch(raw):
        raise _fixture_error(f"{where}: 必须是定点字符串（禁 float），实际 {raw!r}")
    try:
        scaled = Decimal(raw).scaleb(scale)
    except DecimalException as exc:
        raise _fixture_error(f"{where}: 无法解析定点数 {raw!r}") from exc
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise _fixture_error(f"{where}: 超出标度 {scale}（不得静默舍入），实际 {raw!r}")
    units = int(scaled)
    if not -_MAX_UNITS <= units <= _MAX_UNITS:
        raise _fixture_error(f"{where}: 标度整数 {units} 超出 BigInteger 范围")
    return units


def _normalize_series(raw_series: Any, fallback_symbol: str) -> dict[str, list[Any]]:
    """兼容两种结构：`series` 为 {symbol: [row...]}（新）或 [row...]（一期遗留）。"""
    if isinstance(raw_series, dict):
        grouped: dict[str, list[Any]] = {}
        for symbol, rows in raw_series.items():
            if not isinstance(symbol, str) or not symbol:
                raise _fixture_error(f"series 的 symbol 必须是非空字符串，实际 {symbol!r}")
            if not isinstance(rows, list):
                raise _fixture_error(f"series[{symbol}] 必须是数组")
            grouped[symbol] = rows
        return grouped
    if isinstance(raw_series, list):
        # 一期遗留结构：series 是单个序列（行内可能没有 symbol），
        # 缺省归到顶层 benchmark，读取方复用同一套行校验。
        grouped = {}
        for index, row in enumerate(raw_series):
            row_obj = _as_object(row, where=f"series[{index}]")
            symbol = row_obj.get("symbol", fallback_symbol)
            if not isinstance(symbol, str) or not symbol:
                raise _fixture_error(f"series[{index}].symbol 必须是非空字符串")
            grouped.setdefault(symbol, []).append(row)
        return grouped
    raise _fixture_error("行情夹具缺少 series（数组或对象）")


def _parse_symbol_series(symbol: str, rows: list[Any]) -> tuple[Bar, ...]:
    if not rows:
        raise _fixture_error(f"标的 {symbol} 的行情为空")
    bars: list[Bar] = []
    previous: date | None = None
    for index, raw_row in enumerate(rows):
        where = f"series[{symbol}][{index}]"
        row = _as_object(raw_row, where=where)
        trade_date = _parse_date(row.get("date"), where=f"{where}.date")
        close_units = _parse_units(row.get("close"), scale=SCALE_PRICE, where=f"{where}.close")
        return_units = _parse_units(row.get("return"), scale=SCALE_RATIO, where=f"{where}.return")
        if close_units <= 0:
            raise _fixture_error(f"{where}.close: 收盘价必须为正，实际 {row.get('close')!r}")
        if previous is not None and trade_date <= previous:
            raise _fixture_error(
                f"{where}.date: 日期必须严格递增（固定排序），{trade_date} <= {previous}"
            )
        previous = trade_date
        bars.append(
            Bar(
                symbol=symbol,
                trade_date=trade_date,
                close_units=close_units,
                return_units=return_units,
            )
        )
    return tuple(bars)


@dataclass(frozen=True, slots=True)
class _LoadedFixture:
    as_of: date
    provenance: str
    capabilities: MarketCapabilities
    series: dict[str, tuple[Bar, ...]]


def _load_fixture(path: Path) -> _LoadedFixture:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise _fixture_error(f"行情夹具不存在：{path}（请执行 make seed-demo 生成）") from exc
    except OSError as exc:
        raise _fixture_error(f"行情夹具不可读：{path}（{exc}）") from exc

    try:
        payload: Any = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise _fixture_error(f"行情夹具不是 UTF-8：{path}") from exc
    except json.JSONDecodeError as exc:
        raise _fixture_error(f"行情夹具 JSON 非法：{path}（line {exc.lineno}: {exc.msg}）") from exc

    data = _as_object(payload, where="quotes.json")
    mode = data.get("mode", "replay")
    if mode != "replay":
        raise _fixture_error(f"行情夹具 mode 必须是 'replay'，实际 {mode!r}")

    raw_series = data.get("series")
    benchmark = data.get("benchmark", "hs300_tr")
    if not isinstance(benchmark, str) or not benchmark:
        raise _fixture_error("行情夹具的 benchmark 必须是非空字符串")
    grouped = _normalize_series(raw_series, benchmark)

    series = {symbol: _parse_symbol_series(symbol, rows) for symbol, rows in grouped.items()}
    max_date = max(bars[-1].trade_date for bars in series.values())

    declared_as_of = data.get("as_of")
    if declared_as_of is not None:
        as_of = _parse_date(declared_as_of, where="as_of")
        if as_of < max_date:
            raise _fixture_error(
                f"行情夹具含 as_of({as_of}) 之后的数据（最大 {max_date}）：拒绝未来数据"
            )
    else:
        as_of = max_date

    declared_provenance = data.get("provenance", _DEFAULT_PROVENANCE)
    if not isinstance(declared_provenance, str) or not declared_provenance:
        raise _fixture_error("provenance 必须是非空字符串")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    provenance = f"replay:{declared_provenance}#sha256:{digest[:16]}"

    capabilities = MarketCapabilities(
        mode="replay",
        symbols=tuple(sorted(series)),
        supports_daily_bars=True,
        supports_latest_quote=True,
        supports_adjusted=False,
        max_lookback_days=None,
    )
    return _LoadedFixture(
        as_of=as_of,
        provenance=provenance,
        capabilities=capabilities,
        series=series,
    )


class ReplayMarketDataSource:
    """`fixtures/market/quotes.json` 确定性回放（MARKET_MODE=replay）。

    懒加载：构造/import 时**不读文件**，首次访问端口能力/取数时才解析，
    因此 `create_market_source()` 可以安全地在应用装配期调用。
    """

    __slots__ = ("_loaded", "_path")

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._loaded: _LoadedFixture | None = None

    # ── 端口属性 ────────────────────────────────────────────
    @property
    def capabilities(self) -> MarketCapabilities:
        return self._fixture().capabilities

    @property
    def as_of(self) -> date:
        return self._fixture().as_of

    @property
    def provenance(self) -> str:
        return self._fixture().provenance

    # ── 端口方法 ────────────────────────────────────────────
    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        fixture = self._fixture()
        wanted = self._require_symbols(fixture, symbols)
        if start > end:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"start({start}) 不能晚于 end({end})")
        effective_end = min(end, fixture.as_of)
        if effective_end < start:
            return ()
        bars = [
            bar
            for symbol in wanted
            for bar in fixture.series[symbol]
            if start <= bar.trade_date <= effective_end
        ]
        return tuple(sorted(bars, key=lambda bar: (bar.trade_date, bar.symbol)))

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        fixture = self._fixture()
        wanted = self._require_symbols(fixture, symbols)
        quotes: list[Quote] = []
        for symbol in wanted:
            last = fixture.series[symbol][-1]
            quotes.append(Quote(symbol=symbol, as_of=last.trade_date, close_units=last.close_units))
        return tuple(quotes)

    # ── 内部 ────────────────────────────────────────────────
    def _fixture(self) -> _LoadedFixture:
        if self._loaded is None:
            self._loaded = _load_fixture(self._path)
        return self._loaded

    def _require_symbols(self, fixture: _LoadedFixture, symbols: Sequence[str]) -> tuple[str, ...]:
        if not symbols:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="symbols 不能为空")
        wanted: dict[str, None] = {}
        for raw in symbols:
            if not isinstance(raw, str) or not raw:
                raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"非法 symbol: {raw!r}")
            wanted[raw] = None
        missing = [symbol for symbol in wanted if symbol not in fixture.series]
        if missing:
            available = "、".join(fixture.capabilities.symbols)
            raise ApiError(
                ErrorCode.NOT_FOUND,
                detail=f"replay 夹具没有标的 {missing}；可用标的：{available}",
            )
        return tuple(wanted)


def create_market_source(
    settings: Settings,
    *,
    session_factory: SessionFactory | None = None,
    clock: Clock | None = None,
) -> MarketDataSource:
    """按 `MARKET_MODE` 装配行情源。

    - `replay`：读 `settings.fixtures_dir/market/quotes.json`（懒加载，fail-closed），
      忽略 `session_factory`；
    - `live` + `market_live_cache_enabled=True`：`CachedMarketDataSource` 包
      `AkshareMarketDataSource`，**必须**提供无租户的 `session_factory`（公共行情缓存）；
    - `live` + `market_live_cache_enabled=False`：纯 akshare 透传（调试用）；
    - akshare 未安装且 `live`：启动期 `RuntimeError`（部署错误，尽早暴露）。

    设计见 `docs/architecture/2026-10-22-live-market-data-source.md` §8。工厂留在
    ledger 的原因：`ReplayMarketDataSource` 在本层，platform 不能反向依赖 ledger（L1-L5）。
    """
    if settings.market_mode == "replay":
        bars_file = settings.market_bars_file or (settings.fixtures_dir / "market" / "quotes.json")
        return ReplayMarketDataSource(bars_file)

    from stock_platform.market_live import (
        AkshareMarketDataSource,
        CachedMarketDataSource,
        akshare_available,
    )

    if not akshare_available():
        raise RuntimeError("MARKET_MODE=live 需要 akshare：请执行 `uv sync --extra live` 后重启")
    resolved_clock = clock or SystemClock()
    inner = AkshareMarketDataSource(settings, resolved_clock)
    if not settings.market_live_cache_enabled:
        return inner
    if session_factory is None:
        raise ValueError(
            "MARKET_MODE=live 且缓存开启（MARKET_LIVE_CACHE_ENABLED=true）时必须传入 "
            "session_factory（无租户的 Database.session）"
        )
    return CachedMarketDataSource(
        inner,
        settings=settings,
        session_factory=session_factory,
        clock=resolved_clock,
    )


__all__ = ["ReplayMarketDataSource", "create_market_source"]
