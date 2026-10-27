"""API-033/034/035/079 验收（Period2-06）。

覆盖：happy、ETag/304、参数错误码、解释三段式、下钻分页/游标、跨用户隔离。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_review.application.metrics import set_registry_for_tests
from stock_review.metrics.registry import build_registry
from stock_review.models import Round

pytestmark = pytest.mark.api

SUPPORT_DEFS = Path(__file__).resolve().parents[1] / "support" / "metric_defs"
WIN_RATE = "perf.win_rate_stub"
EXPECTANCY = "perf.expectancy_stub"
PERIOD = "2026-06"


@pytest.fixture(autouse=True)
def _stub_registry():
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    set_registry_for_tests(registry)
    yield
    set_registry_for_tests(None)


async def _seed_rounds(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    wins: int = 7,
    losses: int = 5,
) -> None:
    now = datetime(2026, 6, 30, 15, 0, tzinfo=UTC)
    async with AsyncSession(engine) as session:
        for index in range(wins + losses):
            profit = index < wins
            session.add(
                Round(
                    id=f"rnd_{uuid.uuid4().hex[:12]}",
                    user_id=user_id,
                    security_code="600519",
                    security_name="贵州茅台",
                    status="closed",
                    open_trade_id=f"trd_open_{index}",
                    close_trade_id=f"trd_close_{index}",
                    open_date=date(2026, 6, 1),
                    close_date=date(2026, 6, 10),
                    quantity_units=100_0000,
                    cost_units=1_000_000_000,
                    proceeds_units=1_100_000_000 if profit else 950_000_000,
                    dividend_units=0,
                    pnl_units=100_000_000 if profit else -50_000_000,
                    return_rate_units=10_000_000 if profit else -5_000_000,
                    holding_days=5,
                    data_error=False,
                    oversell_units=0,
                    fills_count=1,
                    rebuilt_at=now,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_api_033_batch_happy_etag_and_304(client, account_factory, owner_engine):
    acc = await account_factory()
    await _seed_rounds(owner_engine, user_id=acc.user_id)

    r = await client.get(
        "/api/v1/metrics",
        params={"ids": WIN_RATE, "period": PERIOD},
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == PERIOD
    assert body["definition_version"] == "1.0"
    assert body["data_as_of"] == "2026-06-10"
    assert "registry_version" in body["assumptions"]
    assert body["assumptions"]["A"] == 244

    item = body["items"][0]
    assert item["metric_id"] == WIN_RATE
    assert item["status"] == "ok"
    assert item["sample_status"] == "ok"
    assert item["value"] == "0.58333333"
    assert item["n"] == 12
    assert item["unit"] == "ratio"
    assert item["precision"] == 8
    assert item["inputs_hash"].startswith("sha256:")
    assert item["ci_low"] < item["value"] < item["ci_high"]
    assert item["scope"] == {"unit": "round", "cohort": "close_date", "period": "month"}

    etag = r.headers["etag"]
    assert etag.startswith('W/"')

    r2 = await client.get(
        "/api/v1/metrics",
        params={"ids": WIN_RATE, "period": PERIOD},
        headers={**acc.headers, "If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.content == b""


@pytest.mark.asyncio
async def test_api_033_domain_filter_and_multi_metric(client, account_factory, owner_engine):
    acc = await account_factory()
    await _seed_rounds(owner_engine, user_id=acc.user_id)

    r = await client.get(
        "/api/v1/metrics",
        params={"domain": "perf", "period": PERIOD},
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    ids = [item["metric_id"] for item in r.json()["items"]]
    perf_ids = sorted(
        item.id for item in build_registry(extra_paths=[SUPPORT_DEFS]).by_domain("perf")
    )
    assert ids == perf_ids


@pytest.mark.asyncio
async def test_api_033_error_codes(client, account_factory):
    acc = await account_factory()

    cases = [
        ({"ids": "perf.nope", "period": PERIOD}, 404, ErrorCode.METRIC_NOT_FOUND),
        ({"ids": WIN_RATE, "domain": "perf", "period": PERIOD}, 422, ErrorCode.VALIDATION_ERROR),
        ({"ids": "   ", "period": PERIOD}, 422, ErrorCode.VALIDATION_ERROR),
        ({"domain": "nope", "period": PERIOD}, 422, ErrorCode.VALIDATION_ERROR),
        ({"period": "2026-13"}, 422, ErrorCode.INVALID_PERIOD),
        ({"period": "2027-01"}, 422, ErrorCode.INVALID_PERIOD),
        ({"period": PERIOD, "from": "2026-06-01"}, 422, ErrorCode.VALIDATION_ERROR),
        ({"from": "2026-06-01"}, 422, ErrorCode.INVALID_PERIOD),
        (
            {"from": "2026-06-01", "to": "2026-06-20"},
            422,
            ErrorCode.INVALID_PERIOD,
        ),
        ({"period": PERIOD, "benchmark": "bad_index"}, 422, ErrorCode.UNKNOWN_BENCHMARK),
    ]
    for params, status, code in cases:
        r = await client.get("/api/v1/metrics", params=params, headers=acc.headers)
        assert r.status_code == status, (params, r.text)
        assert r.json()["code"] == code.value, (params, r.text)


@pytest.mark.asyncio
async def test_api_033_admin_forbidden(client, account_factory):
    admin = await account_factory(role="admin")
    r = await client.get("/api/v1/metrics", params={"period": PERIOD}, headers=admin.headers)
    assert r.status_code == 403
    assert r.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


@pytest.mark.asyncio
async def test_api_079_detail_matches_batch_item(client, account_factory, owner_engine):
    acc = await account_factory()
    await _seed_rounds(owner_engine, user_id=acc.user_id)

    batch = await client.get(
        "/api/v1/metrics", params={"ids": WIN_RATE, "period": PERIOD}, headers=acc.headers
    )
    detail = await client.get(
        f"/api/v1/metrics/{WIN_RATE}", params={"period": PERIOD}, headers=acc.headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json() == batch.json()["items"][0]
    assert detail.headers["etag"].startswith('W/"')

    r304 = await client.get(
        f"/api/v1/metrics/{WIN_RATE}",
        params={"period": PERIOD},
        headers={**acc.headers, "If-None-Match": detail.headers["etag"]},
    )
    assert r304.status_code == 304


@pytest.mark.asyncio
async def test_api_079_unknown_metric_and_invalid_path(client, account_factory):
    acc = await account_factory()
    for path in ("/api/v1/metrics/perf.nope", "/api/v1/metrics/not_dotted"):
        r = await client.get(path, params={"period": PERIOD}, headers=acc.headers)
        assert r.status_code == 404, r.text
        assert r.json()["code"] == ErrorCode.METRIC_NOT_FOUND.value


@pytest.mark.asyncio
async def test_api_034_explain_happy_and_insufficient(client, account_factory, owner_engine):
    acc = await account_factory()
    await _seed_rounds(owner_engine, user_id=acc.user_id)

    r = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/explain", params={"period": PERIOD}, headers=acc.headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conclusion"] is not None
    assert body["method"]
    assert body["drilldown"]["endpoint"] == "/api/v1/rounds"
    assert body["status"] == "ok"

    en = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/explain",
        params={"period": PERIOD},
        headers={**acc.headers, "Accept-Language": "en-US"},
    )
    assert en.status_code == 200
    assert (
        en.json()["method"]
        == "Share of closed rounds with positive return, with a confidence interval."
    )

    other = await account_factory()
    await _seed_rounds(owner_engine, user_id=other.user_id, wins=1, losses=1)
    small = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/explain", params={"period": PERIOD}, headers=other.headers
    )
    assert small.status_code == 200, small.text
    small_body = small.json()
    assert small_body["conclusion"] is None
    assert small_body["reason"] == "INSUFFICIENT_SAMPLE"
    assert small_body["what_to_do"]


@pytest.mark.asyncio
async def test_api_035_drilldown_pagination_and_filters(client, account_factory, owner_engine):
    acc = await account_factory()
    await _seed_rounds(owner_engine, user_id=acc.user_id)

    first = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/drilldown",
        params={"period": PERIOD, "limit": 2},
        headers=acc.headers,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 2
    assert body["total"] == 12
    assert body["next_cursor"]
    assert body["truncated"] is False
    assert body["items"][0]["value"].endswith("000000")

    second = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/drilldown",
        params={"period": PERIOD, "limit": 2, "cursor": body["next_cursor"]},
        headers=acc.headers,
    )
    assert second.status_code == 200, second.text
    keys_first = {item["key"] for item in body["items"]}
    keys_second = {item["key"] for item in second.json()["items"]}
    assert keys_first.isdisjoint(keys_second)

    bad = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/drilldown",
        params={"period": PERIOD, "limit": 2, "cursor": "not-a-cursor"},
        headers=acc.headers,
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == ErrorCode.INVALID_CURSOR.value

    filtered = await client.get(
        f"/api/v1/metrics/{WIN_RATE}/drilldown",
        params={"period": PERIOD, "security_code": "600519"},
        headers=acc.headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []


@pytest.mark.asyncio
async def test_api_033_tenant_isolation(client, account_factory, owner_engine):
    owner = await account_factory()
    await _seed_rounds(owner_engine, user_id=owner.user_id)
    other = await account_factory()

    r = await client.get(
        "/api/v1/metrics", params={"ids": WIN_RATE, "period": PERIOD}, headers=other.headers
    )
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["n"] == 0
    assert item["value"] is None
    assert item["sample_status"] == "insufficient"
