"""F8：回合前瞻缓存指纹纳入区间内 daily_bar 的等价版本量。"""

from __future__ import annotations

import dataclasses
from datetime import date

from stock_platform.ports import Bar
from stock_review.insights.service import _bar_version, _fingerprint

CAL = (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3))
BENCHMARK = "hs300_tr"


def _bars() -> list[Bar]:
    return [
        Bar(symbol="hs300_tr", trade_date=CAL[0], close_units=100_000_000, return_units=0),
        Bar(symbol="600519", trade_date=CAL[0], close_units=200_000_000, return_units=0),
        Bar(symbol="600519", trade_date=CAL[1], close_units=201_000_000, return_units=0),
    ]


def test_bar_version_is_max_date_count_and_content_digest() -> None:
    latest, count, digest = _bar_version(_bars()).split("|")
    assert latest == "2026-06-02"
    assert count == "3"
    assert len(digest) == 64


def test_fingerprint_stable_for_same_bars() -> None:
    version = _bar_version(_bars())
    assert _fingerprint(CAL, version, BENCHMARK) == _fingerprint(CAL, version, BENCHMARK)


def test_fingerprint_changes_when_interval_bar_revised() -> None:
    bars = _bars()
    revised = [dataclasses.replace(bars[2], close_units=199_000_000), *bars[:2]]
    assert _bar_version(revised) != _bar_version(bars)
    assert _fingerprint(CAL, _bar_version(revised), BENCHMARK) != _fingerprint(
        CAL, _bar_version(bars), BENCHMARK
    )


def test_fingerprint_changes_when_interval_bar_added() -> None:
    """区间内新增一根 bar：最大交易日与条数都变化 → 指纹变化（触发重算）。"""
    added = [
        *_bars(),
        Bar(symbol="600519", trade_date=CAL[2], close_units=202_000_000, return_units=0),
    ]
    assert _bar_version(added).split("|")[0] == "2026-06-03"
    assert _bar_version(added).split("|")[1] == "4"
    assert _fingerprint(CAL, _bar_version(added), BENCHMARK) != _fingerprint(
        CAL, _bar_version(_bars()), BENCHMARK
    )


def test_fingerprint_without_bars_is_explicit() -> None:
    assert _bar_version(()) == "empty"
    assert _fingerprint(CAL, "empty", BENCHMARK) != _fingerprint(
        CAL, _bar_version(_bars()), BENCHMARK
    )
