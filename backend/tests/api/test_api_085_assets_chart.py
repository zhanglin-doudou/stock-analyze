"""API-085 验收：`GET /assets/chart` 资产日线 chart-ready 视图。

覆盖设计（前端重构 D3）：
* happy：points 按日期升序、v 为 [0,1] 定点字符串、amount_label 后端定点串；
* blocked 行不进入序列；未构建/全 blocked → n_points=0；
* 跨用户空序列、admin 403、无 token 401；limit 0/501→422、未知 query→400；
* 只读：调用前后 review.asset_daily 行数不变。
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_api.version import API_PREFIX

pytestmark = pytest.mark.api

_V_RE = re.compile(r"^(?:0|1)\.\d{4}$")
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_ASSET_DEFINITION_VERSION = "1.0"


async def _insert_asset_points(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    points: list[tuple[date, int, str]],
) -> None:
    """直插资产日线：points=(trade_date, total_assets_units, status)。"""
    async with engine.begin() as conn:
        for trade_date, total_units, status in points:
            await conn.execute(
                text(
                    "insert into review.asset_daily "
                    "(id, user_id, trade_date, cash_units, market_value_units, "
                    " total_assets_units, holding_cost_units, unrealized_pnl_units, "
                    " holding_count, cashflow_units, status, status_reason, "
                    " definition_version, rebuilt_at) "
                    "values (:id, :uid, :d, 0, :total, :total, 0, 0, 1, 0, :status, "
                    "        :reason, :version, now())"
                ),
                {
                    "id": f"ast_{uuid.uuid4().hex[:32]}",
                    "uid": user_id,
                    "d": trade_date,
                    "total": total_units,
                    "status": status,
                    "reason": "测试阻断" if status == "blocked" else None,
                    "version": _ASSET_DEFINITION_VERSION,
                },
            )


async def _insert_build_state(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    data_as_of: date,
    rows_count: int,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into review.asset_build_state "
                "(user_id, start_date, last_built_through, last_built_at, data_as_of, "
                " last_status, last_mode, definition_version, rows_count, "
                " last_rows_written, version) "
                "values (:uid, :start, :through, now(), :as_of, 'ok', 'full', :version, "
                "        :rows, :rows, 1)"
            ),
            {
                "uid": user_id,
                "start": date(2026, 6, 28),
                "through": data_as_of,
                "as_of": data_as_of,
                "version": _ASSET_DEFINITION_VERSION,
                "rows": rows_count,
            },
        )


@pytest.mark.asyncio
async def test_api_085_happy_returns_normalized_ascending_points(
    client: httpx.AsyncClient,
    user: Any,
    owner_engine: AsyncEngine,
) -> None:
    points = [
        (date(2026, 6, 28), 10_000_000, "ok"),
        (date(2026, 6, 29), 10_200_000, "ok"),
        (date(2026, 6, 30), 10_500_000, "ok"),
    ]
    await _insert_asset_points(owner_engine, user_id=user.user_id, points=points)
    await _insert_build_state(
        owner_engine, user_id=user.user_id, data_as_of=date(2026, 6, 30), rows_count=3
    )

    resp = await client.get(
        f"{API_PREFIX}/assets/chart", headers=user.headers, params={"limit": 200}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_points"] == 3
    assert body["data_as_of"] == "2026-06-30"
    assert body["definition_version"] == _ASSET_DEFINITION_VERSION
    assert body["build_state"] in {"current", "stale"}
    assert [p["date"] for p in body["points"]] == [
        "2026-06-28",
        "2026-06-29",
        "2026-06-30",
    ]
    assert [p["v"] for p in body["points"]] == ["0.0000", "0.4000", "1.0000"]
    assert body["points"][0]["label"] == "06-28"
    assert _MONEY_RE.match(body["points"][0]["amount_label"])
    assert body["points"][-1]["amount_label"] == "105000.00"
    for point in body["points"]:
        assert _V_RE.match(point["v"]), point


@pytest.mark.asyncio
async def test_api_085_blocked_rows_are_excluded(
    client: httpx.AsyncClient,
    user: Any,
    owner_engine: AsyncEngine,
) -> None:
    points = [
        (date(2026, 6, 28), 10_000_000, "ok"),
        (date(2026, 6, 29), 0, "blocked"),
        (date(2026, 6, 30), 10_500_000, "ok"),
    ]
    await _insert_asset_points(owner_engine, user_id=user.user_id, points=points)
    await _insert_build_state(
        owner_engine, user_id=user.user_id, data_as_of=date(2026, 6, 30), rows_count=3
    )

    resp = await client.get(f"{API_PREFIX}/assets/chart", headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_points"] == 2
    assert [p["date"] for p in body["points"]] == ["2026-06-28", "2026-06-30"]
    assert body["points"][0]["v"] == "0.0000"
    assert body["points"][1]["v"] == "1.0000"


@pytest.mark.asyncio
async def test_api_085_not_built_returns_empty_points(
    client: httpx.AsyncClient,
    user: Any,
) -> None:
    resp = await client.get(f"{API_PREFIX}/assets/chart", headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["points"] == []
    assert body["n_points"] == 0
    assert body["build_state"] == "missing"


@pytest.mark.asyncio
async def test_api_085_isolation_admin_and_auth(
    client: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    user: Any,
    admin: Any,
    account_factory: Any,
) -> None:
    await _insert_asset_points(
        owner_engine,
        user_id=user.user_id,
        points=[(date(2026, 6, 30), 10_500_000, "ok")],
    )
    await _insert_build_state(
        owner_engine, user_id=user.user_id, data_as_of=date(2026, 6, 30), rows_count=1
    )

    other = await account_factory()
    other_resp = await client.get(f"{API_PREFIX}/assets/chart", headers=other.headers)
    assert other_resp.status_code == 200
    assert other_resp.json()["points"] == []

    admin_resp = await client.get(f"{API_PREFIX}/assets/chart", headers=admin.headers)
    assert admin_resp.status_code == 403, admin_resp.text

    unauth = await client.get(f"{API_PREFIX}/assets/chart")
    assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_api_085_query_validation(
    client: httpx.AsyncClient,
    user: Any,
) -> None:
    for limit in (0, 501):
        resp = await client.get(
            f"{API_PREFIX}/assets/chart", headers=user.headers, params={"limit": limit}
        )
        assert resp.status_code == 422, (limit, resp.text)
    unknown = await client.get(
        f"{API_PREFIX}/assets/chart", headers=user.headers, params={"foo": "1"}
    )
    assert unknown.status_code == 400, unknown.text
    assert unknown.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_api_085_is_read_only(
    client: httpx.AsyncClient,
    user: Any,
    owner_engine: AsyncEngine,
) -> None:
    await _insert_asset_points(
        owner_engine,
        user_id=user.user_id,
        points=[(date(2026, 6, 30), 10_500_000, "ok")],
    )
    await _insert_build_state(
        owner_engine, user_id=user.user_id, data_as_of=date(2026, 6, 30), rows_count=1
    )
    async with owner_engine.connect() as conn:
        before = (await conn.execute(text("select count(*) from review.asset_daily"))).scalar_one()
    resp = await client.get(f"{API_PREFIX}/assets/chart", headers=user.headers)
    assert resp.status_code == 200
    async with owner_engine.connect() as conn:
        after = (await conn.execute(text("select count(*) from review.asset_daily"))).scalar_one()
    assert before == after
