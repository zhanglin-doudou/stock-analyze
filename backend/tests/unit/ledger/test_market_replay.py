"""G4：行情 replay 适配器与确定性夹具单测（离线、无 DB 语义依赖）。

覆盖：
- 夹具生成纯确定性（两次调用字节一致；tracked 文件 == 生成器输出；无时刻戳/无 float）；
- replay 读取与夹具逐行一致（日期/价格/收益，走定点字符串 → 标度整数）；
- 按日期区间过滤（闭区间）、不返回 `as_of` 之后的数据；
- 夹具含未来行 / 非法定点 / 非法日期 → fail-closed；缺文件 / JSON 坏 → fail-closed；
- 遗留 `series` 数组结构兼容；
- live 模式显式 NotImplementedError；工厂不在装配期读文件。

注：pytest 的父级 `conftest.py` 有 autouse 清库 fixture，因此运行需
`STOCK_DB_NAME=stock_p2_g4 STOCK_DB_HOST=127.0.0.1 STOCK_DB_PORT=5432`。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.values import Price, Ratio
from stock_ledger.fixtures import generate_market_quotes
from stock_ledger.market import ReplayMarketDataSource, create_market_source
from stock_platform.config import REPO_ROOT, Settings
from stock_platform.ports import MarketDataSource

MARKET_FIXTURE = REPO_ROOT / "fixtures" / "market" / "quotes.json"
_TIMESTAMP_KEYS = frozenset({"generated_at", "created_at", "timestamp", "fetched_at", "updated_at"})


def _native_payload() -> dict[str, Any]:
    payload: Any = json.loads(generate_market_quotes().decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_payload(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "quotes.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _collect(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = [(path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_collect(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_collect(item, f"{path}[{index}]"))
    return found


# ── 夹具生成：确定性 ────────────────────────────────────────────


def test_generate_market_quotes_is_byte_deterministic() -> None:
    first = generate_market_quotes()
    second = generate_market_quotes()
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_tracked_fixture_matches_generator() -> None:
    """tracked 的 quotes.json 必须就是生成器输出（防止手改/漂移）。"""
    assert MARKET_FIXTURE.read_bytes() == generate_market_quotes()


def test_fixture_has_no_timestamps_and_no_float() -> None:
    payload = _native_payload()
    for path, value in _collect(payload):
        assert not isinstance(value, float), f"{path} 出现 float（全链路禁 float）"
        key = path.rsplit(".", 1)[-1]
        assert key not in _TIMESTAMP_KEYS, f"{path} 出现运行时刻戳字段"
    # 日期固定且升序（无运行时间参与）
    dates = [row["date"] for row in payload["series"]["hs300_tr"]]
    assert dates == sorted(dates)
    assert payload["as_of"] == dates[-1]


def test_cli_write_market_fixtures_is_repeatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cli.write_market_fixtures 复用生成器：连续两次写出字节一致。"""
    from stock_api import cli

    monkeypatch.setattr(cli, "MARKET_DIR", tmp_path)
    first = cli.write_market_fixtures()["quotes"].read_bytes()
    second = cli.write_market_fixtures()["quotes"].read_bytes()
    assert first == second
    assert first == generate_market_quotes()


# ── replay：正常读取与过滤 ──────────────────────────────────────


async def test_replay_reads_tracked_fixture_row_by_row() -> None:
    source = ReplayMarketDataSource(MARKET_FIXTURE)
    assert isinstance(source, MarketDataSource)
    payload = _native_payload()
    for symbol, rows in payload["series"].items():
        bars = await source.get_daily_bars([symbol], date(2026, 1, 1), date(2026, 12, 31))
        assert [bar.symbol for bar in bars] == [symbol] * len(rows)
        assert [bar.trade_date.isoformat() for bar in bars] == [row["date"] for row in rows]
        assert [bar.close_units for bar in bars] == [
            Price.from_str(row["close"]).units for row in rows
        ]
        assert [bar.return_units for bar in bars] == [
            Ratio.from_str(row["return"]).units for row in rows
        ]
    assert source.as_of == date(2026, 9, 30)
    assert source.provenance.startswith("replay:deterministic-fixtures/v1#sha256:")
    assert source.capabilities.mode == "replay"
    assert source.capabilities.supports_symbol("hs300_tr")
    assert not source.capabilities.supports_symbol("999999")


async def test_range_filter_is_inclusive_and_sorted() -> None:
    source = ReplayMarketDataSource(MARKET_FIXTURE)
    bars = await source.get_daily_bars(["hs300_tr", "600519"], date(2026, 4, 30), date(2026, 6, 30))
    expected_dates = [date(2026, 4, 30), date(2026, 5, 29), date(2026, 6, 30)]
    # 日期升序、同日按 symbol 升序（确定性输出）
    assert [bar.trade_date for bar in bars] == [
        trade_date for trade_date in expected_dates for _ in ("600519", "hs300_tr")
    ]
    assert all(date(2026, 4, 30) <= bar.trade_date <= date(2026, 6, 30) for bar in bars)


async def test_no_data_after_as_of() -> None:
    source = ReplayMarketDataSource(MARKET_FIXTURE)
    bars = await source.get_daily_bars(["hs300_tr"], date(2026, 1, 1), date(2027, 12, 31))
    assert bars
    assert max(bar.trade_date for bar in bars) <= source.as_of
    # 请求窗口完全在 as_of 之后 → 空（不回退、不抛未来数据）
    assert await source.get_daily_bars(["hs300_tr"], date(2026, 10, 1), date(2027, 12, 31)) == ()


async def test_latest_quotes_match_last_bar() -> None:
    source = ReplayMarketDataSource(MARKET_FIXTURE)
    quotes = await source.get_latest_quotes(["hs300_tr", "600519"])
    assert [quote.symbol for quote in quotes] == ["hs300_tr", "600519"]
    for quote in quotes:
        bars = await source.get_daily_bars([quote.symbol], source.as_of, source.as_of)
        assert len(bars) == 1
        assert quote.as_of == bars[-1].trade_date
        assert quote.close_units == bars[-1].close_units


async def test_unknown_symbol_and_empty_symbols_fail_closed() -> None:
    source = ReplayMarketDataSource(MARKET_FIXTURE)
    with pytest.raises(ApiError) as unknown:
        await source.get_daily_bars(["999999"], date(2026, 1, 1), date(2026, 12, 31))
    assert unknown.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ApiError) as empty:
        await source.get_daily_bars([], date(2026, 1, 1), date(2026, 12, 31))
    assert empty.value.code is ErrorCode.VALIDATION_ERROR


async def test_start_after_end_is_validation_error() -> None:
    source = ReplayMarketDataSource(MARKET_FIXTURE)
    with pytest.raises(ApiError) as invalid:
        await source.get_daily_bars(["hs300_tr"], date(2026, 9, 30), date(2026, 1, 30))
    assert invalid.value.code is ErrorCode.VALIDATION_ERROR


# ── replay：非法夹具 fail-closed ────────────────────────────────


def test_missing_fixture_fails_closed(tmp_path: Path) -> None:
    source = ReplayMarketDataSource(tmp_path / "missing" / "quotes.json")
    with pytest.raises(ApiError) as missing:
        _ = source.capabilities
    assert missing.value.code is ErrorCode.INTERNAL_ERROR
    assert "不存在" in (missing.value.detail or "")


def test_corrupt_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "quotes.json"
    path.write_text('{"series": {', encoding="utf-8")
    source = ReplayMarketDataSource(path)
    with pytest.raises(ApiError) as corrupt:
        _ = source.as_of
    assert corrupt.value.code is ErrorCode.INTERNAL_ERROR
    assert "JSON" in (corrupt.value.detail or "")


def test_fixture_rows_after_as_of_are_rejected(tmp_path: Path) -> None:
    payload = _native_payload()
    payload["as_of"] = "2026-06-30"
    source = ReplayMarketDataSource(_write_payload(tmp_path, payload))
    with pytest.raises(ApiError) as future:
        _ = source.as_of
    assert future.value.code is ErrorCode.INTERNAL_ERROR
    assert "as_of" in (future.value.detail or "")


@pytest.mark.parametrize(
    "bad_close",
    ["3180.1234567", "abc", "3180.55e2", 3180.55, "92233720368547758.08"],
    ids=["too_many_decimals", "not_a_number", "scientific", "float", "bigint_overflow"],
)
def test_invalid_fixed_point_close_is_rejected(tmp_path: Path, bad_close: Any) -> None:
    payload = _native_payload()
    payload["series"]["hs300_tr"][0]["close"] = bad_close
    source = ReplayMarketDataSource(_write_payload(tmp_path, payload))
    with pytest.raises(ApiError) as invalid:
        _ = source.capabilities
    assert invalid.value.code is ErrorCode.INTERNAL_ERROR


def test_return_beyond_ratio_scale_is_rejected(tmp_path: Path) -> None:
    payload = _native_payload()
    payload["series"]["hs300_tr"][0]["return"] = "0.000000001"  # 9 位 > scale=8
    source = ReplayMarketDataSource(_write_payload(tmp_path, payload))
    with pytest.raises(ApiError) as invalid:
        _ = source.capabilities
    assert invalid.value.code is ErrorCode.INTERNAL_ERROR
    assert "标度" in (invalid.value.detail or "")


@pytest.mark.parametrize(
    "bad_date",
    ["2026/09/01", "20260901", "2026-02-30"],
    ids=["slash", "basic_iso", "impossible_day"],
)
def test_invalid_date_is_rejected(tmp_path: Path, bad_date: str) -> None:
    payload = _native_payload()
    payload["series"]["hs300_tr"][0]["date"] = bad_date
    source = ReplayMarketDataSource(_write_payload(tmp_path, payload))
    with pytest.raises(ApiError) as invalid:
        _ = source.capabilities
    assert invalid.value.code is ErrorCode.INTERNAL_ERROR


# ── 兼容 / 工厂 / live ──────────────────────────────────────────


async def test_legacy_list_series_is_supported(tmp_path: Path) -> None:
    legacy = {
        "mode": "replay",
        "benchmark": "hs300_tr",
        "note": "一期遗留结构",
        "series": [
            {"date": "2026-09-01", "close": "3180.55", "return": "0.00000000"},
            {"date": "2026-09-15", "close": "3221.30", "return": "0.01281028"},
        ],
    }
    source = ReplayMarketDataSource(_write_payload(tmp_path, legacy))
    bars = await source.get_daily_bars(["hs300_tr"], date(2026, 9, 1), date(2026, 9, 30))
    assert [(bar.trade_date.isoformat(), bar.close_units) for bar in bars] == [
        ("2026-09-01", Price.from_str("3180.55").units),
        ("2026-09-15", Price.from_str("3221.30").units),
    ]
    # 无显式 as_of → 取序列最大日期（fail-closed 的覆盖边界）
    assert source.as_of == date(2026, 9, 15)


def test_factory_replay_is_lazy_until_first_use(tmp_path: Path) -> None:
    source = create_market_source(Settings(market_mode="replay", fixtures_dir=tmp_path))
    assert isinstance(source, ReplayMarketDataSource)
    with pytest.raises(ApiError) as missing:
        _ = source.as_of  # 首次访问才读文件
    assert missing.value.code is ErrorCode.INTERNAL_ERROR


def test_factory_live_mode_is_explicitly_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        create_market_source(Settings(market_mode="live"))
