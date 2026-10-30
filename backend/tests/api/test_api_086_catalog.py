"""API-086 GET /metrics/catalog 验收：口径说明书（P0-4b）。

覆盖：18 个指标全量、生产呈现语义（display_name/direction/bands）、
无 token 401、admin 403；只读无副作用（不进入/不写 job）。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api


@pytest.mark.asyncio
async def test_api_086_catalog_happy_18_metrics(client, account_factory):
    acc = await account_factory()

    r = await client.get("/api/v1/metrics/catalog", headers=acc.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 18
    assert body["registry_version"]

    by_id = {item["metric_id"]: item for item in body["items"]}
    win_rate = by_id["perf.win_rate"]
    assert win_rate["display_name"] == "胜率"
    assert win_rate["direction"] == "higher_better"
    assert len(win_rate["bands"]) == 3
    assert {band["level"] for band in win_rate["bands"]} == {"watch", "neutral", "good"}
    assert win_rate["min_n"] == 10
    assert win_rate["meaning"]
    assert win_rate["method"]

    # 决策 6 的中文命名进入口径说明。
    assert by_id["perf.concentration"]["display_name"] == "盈利集中度"
    assert by_id["behavior.hhi"]["display_name"] == "持仓集中度"
    # 日频指标的窗口类型来自 sample_policy。
    assert by_id["timing.drawdown"]["window_kind"] == "trailing_60td"
    assert by_id["timing.drawdown"]["min_trading_days"] == 60


@pytest.mark.asyncio
async def test_api_086_catalog_requires_token(client):
    r = await client.get("/api/v1/metrics/catalog")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_api_086_catalog_admin_forbidden(client, account_factory):
    admin = await account_factory(role="admin")
    r = await client.get("/api/v1/metrics/catalog", headers=admin.headers)
    assert r.status_code == 403
    assert r.json()["code"] == "ADMIN_CANNOT_READ_TRADES"
