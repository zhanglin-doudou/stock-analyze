"""P0 板块同步性能治理单测：全局限速 / WAF 熔断 / 板级并发 / 重试分层。

覆盖（docs/architecture/2026-10-31-sector-data.md §4.4 性能修订）：

- ``_TokenBucketRateLimiter``：按固定间隔放行；rate<=0 时不 sleep；
- ``_WafBreaker``：连续 403/429 达阈值即熔断、成功一次清零；
- ``_ths_get``：403 连续触发后快速抛 ``SectorWafBlockedError``（不再跑满页重试）；
- ``_call``：仅传输层失败重试，语义失败（完整性/解析）不整板重试；
- ``_fetch_members_boards``：板级并发（Semaphore 生效）、概念单板失败跳过、
  全部失败整批失败、WAF 熔断不被「单板跳过」吞掉。

全程桩，不联网。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from stock_contracts.clock import FrozenClock
from stock_platform import sector_live
from stock_platform.config import Settings
from stock_platform.market_models import TAXONOMY_CONCEPT, TAXONOMY_INDUSTRY
from stock_platform.ports import SectorInfo
from stock_platform.sector_live import (
    AkshareSectorSource,
    _TokenBucketRateLimiter,
    _WafBreaker,
)
from stock_platform.sector_validation import SectorValidationError, SectorWafBlockedError

_COOKIE = "user=abc; sess_tk=def"


class _Resp:
    """requests.Response 的最小替身。"""

    def __init__(self, text: str, *, status_code: int = 200, url: str = "") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url


class _FakeSession:
    def __init__(self, handler: Any, calls: list[dict[str, Any]]) -> None:
        self._handler = handler
        self._calls = calls

    def get(self, url: str, headers: Any = None, timeout: float | None = None) -> _Resp:
        self._calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        return self._handler(url, len(self._calls))


def _install_session(
    source: AkshareSectorSource, monkeypatch: pytest.MonkeyPatch, handler: Any
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_new_session() -> _FakeSession:
        return _FakeSession(handler, calls)

    monkeypatch.setattr(source, "_new_http_session", fake_new_session)
    return calls


def _make_source(*, rate_per_second: float = 0.0, concurrency: int = 4) -> AkshareSectorSource:
    settings = Settings(
        sector_ths_cookie=_COOKIE,
        sector_ths_rate_limit_per_second=rate_per_second,
        sector_live_max_concurrency=concurrency,
        sector_live_timeout_seconds=10.0,
    )
    return AkshareSectorSource(
        settings,
        clock=FrozenClock("2026-11-03T09:00:00+08:00"),
        session_factory=lambda **_kwargs: None,
    )


@pytest.fixture(autouse=True)
def _stub_akshare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sector_live, "_import_akshare", lambda: object())


def _catalog(taxonomy: str, codes: list[str]) -> tuple[SectorInfo, ...]:
    level = 1 if taxonomy == TAXONOMY_INDUSTRY else 0
    return tuple(
        SectorInfo(
            taxonomy=taxonomy,
            sector_code=code,
            name=f"board-{code}",
            level=level,
            parent_code=None,
            classification_version="THS",
        )
        for code in codes
    )


# ── 全局限速器 ──────────────────────────────────────────────────


def test_rate_limiter_spaces_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monotonic = {"value": 100.0}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        monotonic["value"] += seconds  # 模拟时间随 sleep 前进

    monkeypatch.setattr(sector_live.time, "monotonic", lambda: monotonic["value"])
    monkeypatch.setattr(sector_live.time, "sleep", fake_sleep)
    limiter = _TokenBucketRateLimiter(10.0)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert sleeps == pytest.approx([0.1, 0.1])


def test_rate_limiter_disabled_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_seconds: float) -> None:
        raise AssertionError("rate<=0 时不应 sleep")

    monkeypatch.setattr(sector_live.time, "sleep", boom)
    _TokenBucketRateLimiter(0.0).acquire()


# ── WAF 熔断器 ──────────────────────────────────────────────────


def test_waf_breaker_trips_after_threshold() -> None:
    breaker = _WafBreaker(threshold=3)
    breaker.record_blocked(403)
    breaker.record_blocked(429)
    with pytest.raises(SectorWafBlockedError):
        breaker.record_blocked(403)
    with pytest.raises(SectorWafBlockedError):
        breaker.check()


def test_waf_breaker_success_resets_streak() -> None:
    breaker = _WafBreaker(threshold=2)
    breaker.record_blocked(403)
    breaker.record_success()
    breaker.record_blocked(403)  # 不连续，不熔断
    breaker.check()


def test_ths_get_trips_breaker_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续 403 达到阈值即抛熔断，不再把 4 次页重试耗在同一条路上。"""
    monkeypatch.setattr(sector_live, "_THS_WAF_BREAKER_THRESHOLD", 2)
    monkeypatch.setattr(sector_live.time, "sleep", lambda _seconds: None)
    source = _make_source()
    calls = _install_session(
        source, monkeypatch, lambda _url, _index: _Resp("blocked", status_code=403)
    )
    with pytest.raises(SectorWafBlockedError, match="WAF"):
        source._ths_get(
            "http://q.10jqka.com.cn/gn/detail/code/300001/page/1/", cookie=_COOKIE, referer=""
        )
    assert len(calls) == 2


# ── 重试分层 ────────────────────────────────────────────────────


async def test_call_does_not_retry_semantic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sector_live.asyncio, "sleep", noop)
    source = _make_source()
    calls = {"count": 0}

    def fail() -> None:
        calls["count"] += 1
        raise SectorValidationError("页面结构变更")

    with pytest.raises(SectorValidationError, match="页面结构变更"):
        await source._call(fail)
    assert calls["count"] == 1


async def test_call_retries_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sector_live.asyncio, "sleep", noop)
    source = _make_source()
    calls = {"count": 0}

    def flaky() -> None:
        calls["count"] += 1
        raise OSError("network down")

    with pytest.raises(SectorValidationError, match="akshare 调用失败"):
        await source._call(flaky)
    assert calls["count"] == source._settings.sector_live_retry_attempts + 1


# ── 板级并发 + 概念跳过语义 ─────────────────────────────────────


async def test_industry_members_fetched_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source(concurrency=4)
    catalog = _catalog(TAXONOMY_INDUSTRY, [f"88{index:04d}" for index in range(6)])
    inflight = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_members(code: str, _taxonomy: str) -> tuple[tuple[str, str], ...]:
        with lock:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
        time.sleep(0.05)
        with lock:
            inflight["now"] -= 1
        return ((code, f"60{code[-4:]}"),)

    monkeypatch.setattr(source, "_ths_board_members", fake_members)
    members = await source._fetch_industry_members(catalog)
    assert inflight["max"] >= 2  # Semaphore 生效：请求确实重叠
    assert len(members) == len(catalog)


async def test_concept_members_skip_single_board(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source()
    catalog = _catalog(TAXONOMY_CONCEPT, ["300001", "300002", "300003"])

    def fake_members(code: str, _taxonomy: str) -> tuple[tuple[str, str], ...]:
        if code == "300002":
            raise SectorValidationError("上游不可用")
        return ((code, "600519"),)

    monkeypatch.setattr(source, "_ths_board_members", fake_members)
    members = await source._fetch_concept_members(catalog)
    assert {sector_code for sector_code, _symbol in members} == {"300001", "300003"}


async def test_concept_members_all_skipped_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source()
    catalog = _catalog(TAXONOMY_CONCEPT, ["300001", "300002"])

    def fake_members(_code: str, _taxonomy: str) -> tuple[tuple[str, str], ...]:
        raise SectorValidationError("上游不可用")

    monkeypatch.setattr(source, "_ths_board_members", fake_members)
    with pytest.raises(SectorValidationError, match="全部"):
        await source._fetch_concept_members(catalog)


async def test_concept_members_reraises_waf_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source()
    catalog = _catalog(TAXONOMY_CONCEPT, ["300001", "300002"])

    def fake_members(_code: str, _taxonomy: str) -> tuple[tuple[str, str], ...]:
        raise SectorWafBlockedError("WAF 熔断")

    monkeypatch.setattr(source, "_ths_board_members", fake_members)
    with pytest.raises(SectorWafBlockedError):
        await source._fetch_concept_members(catalog)
