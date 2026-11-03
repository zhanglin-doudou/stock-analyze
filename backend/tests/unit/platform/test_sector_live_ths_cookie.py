"""THS 登录态（SECTOR_THS_COOKIE）与成分完整性闸门单测（全程桩，不联网）。

覆盖：cookie 缺失 fail-fast、登录页/401 失效、全分页齐全、动态排序重复触发的
整板重抓、完整性闸门拒绝、单页重试退避、日线抓取带 cookie、cookie 脱敏。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from stock_contracts.clock import FrozenClock
from stock_platform import sector_live
from stock_platform.config import Settings
from stock_platform.market_models import TAXONOMY_INDUSTRY
from stock_platform.sector_live import AkshareSectorSource, mask_cookie
from stock_platform.sector_validation import (
    SectorCookieError,
    SectorMemberIncompleteError,
)

_COOKIE = "user=abc; sess_tk=def"


class _Resp:
    """requests.Response 的最小替身。"""

    def __init__(self, text: str, *, status_code: int = 200, url: str = "") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url
        self.history: list[Any] = []


def _page_markup(rows: list[str], current: int, total: int) -> str:
    cells = "".join(
        f"<tr><td>{index + 1}</td><td>{code}</td><td>n</td></tr>" for index, code in enumerate(rows)
    )
    return (
        "<html><body><table>"
        "<tr><th>序号</th><th>代码</th><th>名称</th></tr>"
        f"{cells}</table>"
        f'<span class="page_info">{current}/{total}</span>'
        "</body></html>"
    )


def _codes(start: int, count: int) -> list[str]:
    return [f"{start + offset:06d}" for offset in range(count)]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """记录退避序列，避免单测真的睡 1/2/4 秒。"""
    sleeps: list[float] = []
    monkeypatch.setattr(sector_live.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


@pytest.fixture
def make_source(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(sector_live, "_import_akshare", lambda: object())

    def _make(*, cookie: str | None = _COOKIE) -> AkshareSectorSource:
        settings = Settings(
            sector_ths_cookie=cookie,
            sector_ths_page_interval_seconds=0.0,
            sector_live_timeout_seconds=10.0,
        )
        return AkshareSectorSource(
            settings,
            clock=FrozenClock("2026-11-03T09:00:00+08:00"),
            session_factory=lambda **_kwargs: None,
        )

    return _make


def _install_get(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, headers: Any = None, timeout: float | None = None) -> _Resp:
        calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        return handler(url, len(calls))

    monkeypatch.setattr(sector_live.requests, "get", fake_get)
    return calls


def _page_number(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[-1])


# ── cookie 缺失 / 失效 ────────────────────────────────────────────


def test_cookie_missing_fails_fast_without_network(
    make_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(cookie=None)

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("cookie 缺失时不应发起任何网络请求")

    monkeypatch.setattr(sector_live.requests, "get", boom)
    with pytest.raises(SectorCookieError, match="SECTOR_THS_COOKIE"):
        source._ths_board_members("881117", TAXONOMY_INDUSTRY)


def test_login_wall_page_fails_fast(make_source: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_source()
    calls = _install_get(
        monkeypatch,
        lambda _url, _index: _Resp("请登录后访问", url="https://passport.10jqka.com.cn/login"),
    )
    with pytest.raises(SectorCookieError, match="登录页"):
        source._ths_board_members("881117", TAXONOMY_INDUSTRY)
    assert len(calls) == 1  # 登录墙不重试


def test_401_retries_with_backoff_then_cookie_error(
    make_source: Any, monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    source = make_source()
    calls = _install_get(monkeypatch, lambda _url, _index: _Resp("", status_code=401))
    with pytest.raises(SectorCookieError, match="401"):
        source._ths_board_members("881117", TAXONOMY_INDUSTRY)
    assert len(calls) == source._THS_PAGE_RETRIES + 1
    assert _no_sleep == [1.0, 2.0, 4.0]


# ── 全分页与完整性闸门 ──────────────────────────────────────────


def test_full_pagination_returns_all_deduped_rows(
    make_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source()
    pages = {1: _codes(600000, 20), 2: _codes(600020, 20), 3: _codes(600040, 14)}
    _install_get(
        monkeypatch,
        lambda url, _index: _Resp(_page_markup(pages[_page_number(url)], _page_number(url), 3)),
    )
    members = source._ths_board_members("881117", TAXONOMY_INDUSTRY)
    assert len(members) == 54
    assert len({symbol for _, symbol in members}) == 54


def test_duplicates_trigger_whole_board_refetch(
    make_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首轮动态排序造成 2 只重复/缺失 → 去重后 52 < 期望 54 → 整板重抓一轮。"""
    source = make_source()
    clean = {1: _codes(600000, 20), 2: _codes(600020, 20), 3: _codes(600040, 14)}
    dirty = {1: clean[1], 2: clean[1][:2] + clean[2][2:], 3: clean[3]}
    pass_no = {"value": 0}

    def handler(url: str, _index: int) -> _Resp:
        page = _page_number(url)
        if page == 1:
            pass_no["value"] += 1
        rows = (dirty if pass_no["value"] == 1 else clean)[page]
        return _Resp(_page_markup(rows, page, 3))

    calls = _install_get(monkeypatch, handler)
    members = source._ths_board_members("881117", TAXONOMY_INDUSTRY)
    assert len(members) == 54
    assert len(calls) == 6  # 3 页 × 2 轮


def test_gate_rejects_after_all_passes(make_source: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_source()
    page1 = _codes(600000, 20)
    page2 = page1[:2] + _codes(600018, 18)  # 2 重复 + 2 缺失 → 去重后 52 < 期望 54
    pages = {1: page1, 2: page2, 3: _codes(600040, 14)}

    def handler(url: str, _index: int) -> _Resp:
        page = _page_number(url)
        return _Resp(_page_markup(pages[page], page, 3))

    calls = _install_get(monkeypatch, handler)
    with pytest.raises(SectorMemberIncompleteError, match="期望 54 行"):
        source._ths_board_members("881117", TAXONOMY_INDUSTRY)
    assert len(calls) == 9  # 3 轮 × 3 页


# ── 行业指数日线（sync-sector-bars）也带 cookie ──────────────────


def test_bars_fetch_sends_cookie_and_parses_jsonp(
    make_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source()
    payload = {"data": ("20261029,1,2,3,4000.000,10,20;20261030,1,2,3,4010.000,10,21;")}
    body = f"quotebridge_v4_line_bk_881117_01_2026({json.dumps(payload)})"
    calls = _install_get(monkeypatch, lambda _url, _index: _Resp(body))
    bars = source._ths_board_bars("881117", date(2026, 10, 29), date(2026, 10, 30))
    assert [bar.trade_date for bar in bars] == [date(2026, 10, 29), date(2026, 10, 30)]
    assert calls[0]["headers"]["Cookie"] == _COOKIE
    assert "User-Agent" in calls[0]["headers"]


def test_bars_cookie_missing_fails_fast(make_source: Any) -> None:
    source = make_source(cookie=None)
    with pytest.raises(SectorCookieError, match="SECTOR_THS_COOKIE"):
        source._ths_board_bars("881117", date(2026, 10, 29), date(2026, 10, 30))


def test_mask_cookie_never_leaks_plaintext() -> None:
    masked = mask_cookie(_COOKIE)
    assert _COOKIE not in masked
    assert str(len(_COOKIE)) in masked
