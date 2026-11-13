"""用户偏好在 API 边界的验收（API-055 GET / API-096 PATCH）。

覆盖：默认视图（无行）、局部更新 + CAS 版本、无变更不 bump、If-Match 校验、
409 版本冲突、空 body/未知键/只读键 422、阈值/窗口与 flags_hash、JSONB 整组重置、
行情时段跨字段校验、概念过滤 `resolved`、幂等重放、admin 可用、跨用户隔离。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_contracts.preferences import DEFAULT_FLAGS_HASH
from stock_core.preferences import flags_hash

pytestmark = pytest.mark.api

API_PREFIX = "/api/v1"
_SETTINGS = f"{API_PREFIX}/settings"


def _if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


async def _patch(
    client: httpx.AsyncClient, account: Any, body: dict[str, Any], version: int = 1
) -> httpx.Response:
    return await client.patch(
        _SETTINGS, json=body, headers={**account.headers, **_if_match(version)}
    )


# ── GET 默认视图 ───────────────────────────────────────────
async def test_get_settings_defaults_without_row(client: httpx.AsyncClient, user: Any) -> None:
    resp = await client.get(_SETTINGS, headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["updated_at"] is None
    assert body["appearance"] == {"theme": "light", "density": "cozy", "trend": "red-up"}
    assert body["bill_flags"] == {
        "near_equal_tolerance_pct": "1.00%",
        "chase_margin_pct": "1.00%",
        "stop_loss_margin_pct": "1.00%",
        "short_term_margin_pct": "5.00%",
        "window_trading_days": "5",
        "flags_hash": DEFAULT_FLAGS_HASH,
        "is_default": True,
    }
    assert body["market_sessions"]["timezone"] == "Asia/Shanghai"
    assert body["concept_filters"] == {"mode": "off", "excluded": []}
    assert body["benchmark_id"] == "hs300_tr"
    assert resp.headers["ETag"] == 'W/"1"'


async def test_get_settings_not_modified(client: httpx.AsyncClient, user: Any) -> None:
    resp = await client.get(_SETTINGS, headers={**user.headers, "If-None-Match": 'W/"1"'})
    assert resp.status_code == 304
    assert resp.content == b""


# ── PATCH：外观 / 版本 / ETag ───────────────────────────────
async def test_patch_appearance_creates_row_with_version_two(
    client: httpx.AsyncClient, user: Any
) -> None:
    resp = await _patch(client, user, {"appearance": {"density": "compact"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["updated_at"] is not None
    assert body["appearance"]["density"] == "compact"
    assert body["appearance"]["theme"] == "light"
    assert resp.headers["ETag"] == 'W/"2"'
    assert resp.headers["X-Settings-Changed"] == "true"

    again = await client.get(_SETTINGS, headers=user.headers)
    assert again.json()["appearance"]["density"] == "compact"
    assert again.json()["version"] == 2


async def test_patch_no_change_does_not_bump(client: httpx.AsyncClient, user: Any) -> None:
    await _patch(client, user, {"appearance": {"density": "compact"}})
    resp = await _patch(client, user, {"appearance": {"density": "compact"}}, version=2)
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2
    assert resp.headers["X-Settings-Changed"] == "false"
    assert resp.headers["ETag"] == 'W/"2"'


async def test_patch_version_conflict(client: httpx.AsyncClient, user: Any) -> None:
    await _patch(client, user, {"appearance": {"density": "compact"}})
    resp = await _patch(client, user, {"appearance": {"theme": "dark"}}, version=1)
    assert resp.status_code == 409
    assert resp.json()["code"] == "VERSION_CONFLICT"


async def test_patch_requires_integer_if_match(client: httpx.AsyncClient, user: Any) -> None:
    missing = await client.patch(
        _SETTINGS, json={"appearance": {"theme": "dark"}}, headers=user.headers
    )
    assert missing.status_code == 422
    wildcard = await client.patch(
        _SETTINGS,
        json={"appearance": {"theme": "dark"}},
        headers={**user.headers, "If-Match": "*"},
    )
    assert wildcard.status_code == 422
    garbage = await client.patch(
        _SETTINGS,
        json={"appearance": {"theme": "dark"}},
        headers={**user.headers, "If-Match": "v1"},
    )
    assert garbage.status_code == 422


async def test_patch_rejects_empty_and_unknown(client: httpx.AsyncClient, user: Any) -> None:
    empty = await _patch(client, user, {})
    assert empty.status_code == 422
    unknown = await _patch(client, user, {"unknown_group": {"a": 1}})
    assert unknown.status_code == 422
    readonly = await _patch(client, user, {"benchmark_id": "csi300"})
    assert readonly.status_code == 422
    nested_readonly = await _patch(client, user, {"bill_flags": {"flags_hash": "x"}})
    assert nested_readonly.status_code == 422
    bad_value = await _patch(client, user, {"appearance": {"theme": "neon"}})
    assert bad_value.status_code == 422


# ── PATCH：账单旗标 ────────────────────────────────────────
async def test_patch_bill_flags_updates_hash(client: httpx.AsyncClient, user: Any) -> None:
    resp = await _patch(
        client,
        user,
        {
            "bill_flags": {
                "chase_margin_pct": "2.00%",
                "short_term_margin_pct": "8.00%",
                "window_trading_days": "10",
            }
        },
    )
    assert resp.status_code == 200, resp.text
    flags = resp.json()["bill_flags"]
    assert flags["chase_margin_pct"] == "2.00%"
    assert flags["short_term_margin_pct"] == "8.00%"
    assert flags["window_trading_days"] == "10"
    assert flags["is_default"] is False
    assert flags["flags_hash"] != DEFAULT_FLAGS_HASH
    assert flags["flags_hash"] == flags_hash(
        {
            "bill_chase_margin_bps": 200,
            "bill_near_equal_tolerance_bps": 100,
            "bill_short_term_margin_bps": 800,
            "bill_stop_loss_margin_bps": 100,
            "bill_window_trading_days": 10,
        }
    )


async def test_patch_bill_flags_rejects_out_of_range(client: httpx.AsyncClient, user: Any) -> None:
    resp = await _patch(client, user, {"bill_flags": {"chase_margin_pct": "9.00%"}})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["field"] == "bill_flags.chase_margin_pct"


async def test_patch_group_null_resets_defaults(client: httpx.AsyncClient, user: Any) -> None:
    await _patch(client, user, {"appearance": {"theme": "dark"}})
    resp = await _patch(client, user, {"appearance": None}, version=2)
    assert resp.status_code == 200, resp.text
    assert resp.json()["appearance"]["theme"] == "light"
    assert resp.json()["version"] == 3


# ── PATCH：行情时段 / 概念过滤 ─────────────────────────────
async def test_patch_market_sessions_cross_field_validation(
    client: httpx.AsyncClient, user: Any
) -> None:
    ok = await _patch(
        client,
        user,
        {
            "market_sessions": {
                "sessions": [{"open": "09:30", "close": "15:00"}],
                "data_ready_after": "17:00",
            }
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["market_sessions"]["sessions"] == [{"open": "09:30", "close": "15:00"}]

    bad = await _patch(
        client,
        user,
        {"market_sessions": {"sessions": [{"open": "09:30", "close": "17:30"}]}},
        version=2,
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["field"] == "market_sessions.data_ready_after"


async def test_patch_concept_filters_and_resolved(
    client: httpx.AsyncClient,
    user: Any,
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into market.sector (taxonomy, sector_code, level, name, parent_code, "
                "classification_version, source, is_active, sort_order, pulled_at) values "
                "('concept_em', 'BK0999', 0, '人工智能', null, 'EM-2026-06', 'em', true, 0, now()) "
                "on conflict do nothing"
            )
        )
    resp = await _patch(
        client,
        user,
        {
            "concept_filters": {
                "mode": "hide",
                "excluded": [
                    {"code": "BK0999", "name": "人工智能"},
                    {"code": "BK9999", "name": "未同步概念"},
                ],
            }
        },
    )
    assert resp.status_code == 200, resp.text
    filters = resp.json()["concept_filters"]
    assert filters["mode"] == "hide"
    assert filters["excluded"] == [
        {"code": "BK0999", "name": "人工智能", "resolved": True},
        {"code": "BK9999", "name": "未同步概念", "resolved": False},
    ]


# ── 幂等 / 权限 / 隔离 ─────────────────────────────────────
async def test_patch_idempotency_replay(client: httpx.AsyncClient, user: Any) -> None:
    key = "settings-replay-0001"
    headers = {**user.headers, **_if_match(1), "Idempotency-Key": key}
    body = {"appearance": {"theme": "dark"}}
    first = await client.patch(_SETTINGS, json=body, headers=headers)
    assert first.status_code == 200, first.text
    second = await client.patch(_SETTINGS, json=body, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["version"] == first.json()["version"] == 2
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.headers["ETag"] == 'W/"2"'

    reused = await client.patch(_SETTINGS, json={"appearance": {"theme": "light"}}, headers=headers)
    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_patch_requires_write_scope(client: httpx.AsyncClient, user: Any) -> None:
    token_resp = await client.post(
        f"{API_PREFIX}/tokens",
        headers=user.headers,
        json={"name": "read-only", "scopes": ["read"]},
    )
    assert token_resp.status_code == 201, token_resp.text
    pat = token_resp.json()["token"]["token"]
    resp = await client.patch(
        _SETTINGS,
        json={"appearance": {"theme": "dark"}},
        headers={"Authorization": f"Bearer {pat}", **_if_match(1)},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INSUFFICIENT_SCOPE"


async def test_settings_are_per_user(
    client: httpx.AsyncClient,
    account_factory: Any,
) -> None:
    first = await account_factory()
    second = await account_factory()
    await _patch(client, first, {"appearance": {"theme": "dark"}})
    other = await client.get(_SETTINGS, headers=second.headers)
    assert other.json()["appearance"]["theme"] == "light"
    assert other.json()["version"] == 1


async def test_admin_can_manage_own_settings(client: httpx.AsyncClient, admin: Any) -> None:
    resp = await _patch(client, admin, {"appearance": {"trend": "green-up"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["appearance"]["trend"] == "green-up"


async def test_patch_writes_audit_log(
    client: httpx.AsyncClient,
    user: Any,
    owner_engine: AsyncEngine,
) -> None:
    await _patch(client, user, {"appearance": {"theme": "dark"}})
    async with owner_engine.begin() as conn:
        meta = await conn.scalar(
            text("select meta from platform.audit_log where action = 'settings.patch'")
        )
    assert meta["from_version"] == 1
    assert meta["to_version"] == 2
    assert meta["changed"] == ["appearance.theme"]
