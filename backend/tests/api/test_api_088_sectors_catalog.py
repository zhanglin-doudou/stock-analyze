"""API-088 `GET /sectors` 验收（设计 §7.2）。

覆盖：happy（行业 L1/L3 + 概念）、q/parent 过滤、分页 cursor、空目录 200、
无 token 401、admin 403、422（taxonomy/level/limit）、400（游标损坏/未知参数）。
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import pytest_asyncio
from stock_api.app import create_app
from stock_contracts.clock import FrozenClock

pytestmark = pytest.mark.api

_CLOCK = "2026-07-01T09:00:00+00:00"
_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_CLOCK)


@pytest_asyncio.fixture
async def sector_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    app = create_app(settings, clock=frozen_clock)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_api_088_industry_level1_happy(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors", headers=acc.headers, params={"taxonomy": "industry", "level": 1}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["taxonomy"] == "industry"
    assert body["sector_data_state"] == "current"
    assert _HEX16_RE.match(body["catalog_version"])
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert body["missing"] == []
    assert [item["sector_code"] for item in body["items"]] == [
        "801050.SI",
        "801080.SI",
        "801120.SI",
        "801180.SI",
    ]
    by_code = {item["sector_code"]: item for item in body["items"]}
    assert by_code["801120.SI"]["sector_name"] == "食品饮料"
    assert by_code["801120.SI"]["source"] == "sw"
    assert by_code["801120.SI"]["classification_version"] == "SW2021"
    assert by_code["801120.SI"]["is_active"] is True
    assert by_code["801120.SI"]["member_count"] == 2
    assert [node["sector_code"] for node in by_code["801120.SI"]["path"]] == ["801120.SI"]


async def test_api_088_level3_path_and_member_count(
    sector_client: Any, account_factory: Any
) -> None:
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors", headers=acc.headers, params={"taxonomy": "industry", "level": 3}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_code = {item["sector_code"]: item for item in body["items"]}
    assert [node["sector_name"] for node in by_code["850121.SI"]["path"]] == [
        "食品饮料",
        "饮料制造",
        "白酒",
    ]
    assert by_code["850121.SI"]["member_count"] == 2
    assert by_code["850511.SI"]["member_count"] == 1


async def test_api_088_parent_filter(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors",
        headers=acc.headers,
        params={"taxonomy": "industry", "level": 2, "parent": "801120.SI"},
    )
    assert resp.status_code == 200, resp.text
    assert [item["sector_code"] for item in resp.json()["items"]] == ["801123.SI"]


async def test_api_088_name_search_and_pagination(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    searched = await sector_client.get(
        "/api/v1/sectors",
        headers=acc.headers,
        params={"taxonomy": "industry", "level": 1, "q": "银行"},
    )
    assert searched.status_code == 200
    assert [item["sector_code"] for item in searched.json()["items"]] == ["801050.SI"]

    page1 = await sector_client.get(
        "/api/v1/sectors",
        headers=acc.headers,
        params={"taxonomy": "industry", "level": 3, "limit": 2},
    )
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["has_more"] is True
    assert body1["next_cursor"]

    page2 = await sector_client.get(
        "/api/v1/sectors",
        headers=acc.headers,
        params={
            "taxonomy": "industry",
            "level": 3,
            "limit": 2,
            "cursor": body1["next_cursor"],
        },
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert body2["has_more"] is False
    codes = {item["sector_code"] for item in body1["items"]} | {
        item["sector_code"] for item in body2["items"]
    }
    assert codes == {"850121.SI", "850511.SI", "850811.SI", "850911.SI"}


async def test_api_088_concept_catalog(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors", headers=acc.headers, params={"taxonomy": "concept"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["taxonomy"] == "concept"
    assert [item["sector_code"] for item in body["items"]] == ["BK0999", "BK1010", "BK1100"]
    assert all(item["level"] == 0 and item["parent_code"] is None for item in body["items"])
    assert all(item["source"] == "em" for item in body["items"])


async def test_api_088_empty_catalog_is_200(sector_client: Any, account_factory: Any) -> None:
    """目录未同步 → 200 + items=[]，不是 404/500。"""
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors",
        headers=acc.headers,
        params={"taxonomy": "industry", "q": "不存在的板块名"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["has_more"] is False


async def test_api_088_requires_token(sector_client: Any) -> None:
    resp = await sector_client.get("/api/v1/sectors")
    assert resp.status_code == 401


async def test_api_088_admin_forbidden(sector_client: Any, account_factory: Any) -> None:
    admin = await account_factory(role="admin")
    resp = await sector_client.get("/api/v1/sectors", headers=admin.headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "ADMIN_CANNOT_READ_TRADES"


@pytest.mark.parametrize(
    "params",
    [
        {"taxonomy": "nope"},
        {"level": 4},
        {"taxonomy": "concept", "level": 1},
        {"limit": 201},
        {"q": ""},
    ],
)
async def test_api_088_validation_errors(
    sector_client: Any, account_factory: Any, params: dict[str, Any]
) -> None:
    acc = await account_factory()
    resp = await sector_client.get("/api/v1/sectors", headers=acc.headers, params=params)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_api_088_invalid_cursor(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors",
        headers=acc.headers,
        params={"taxonomy": "industry", "cursor": "not-a-cursor"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "INVALID_CURSOR"


async def test_api_088_unknown_query_param(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await sector_client.get("/api/v1/sectors", headers=acc.headers, params={"foo": "bar"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "UNKNOWN_QUERY_PARAM"
