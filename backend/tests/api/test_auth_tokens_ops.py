"""一期端点验收：认证（API-001~006）、PAT（API-007~009）、运维（API-071~076）与全局契约。

每个 API-ID 至少三条：happy path / 越权或边界 / 关键错误码。
命名约定 `test_api_0NN_*`，统一 `@pytest.mark.api`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from stock_contracts.errors import CATALOG, ErrorCode

pytestmark = pytest.mark.api

API = "/api/v1"


# ══ API-001 注册 ═══════════════════════════════════════════


async def test_api_001_register_happy_path(client, api_prefix) -> None:
    resp = await client.post(
        f"{api_prefix}/auth/register",
        json={
            "email": "alice@example.com",
            "password": "Str0ng-Passw0rd!",
            "accept_tos": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["role"] == "user"
    assert body["access_token"] and body["refresh_token"]


async def test_api_001_register_must_accept_tos(client, api_prefix) -> None:
    resp = await client.post(
        f"{api_prefix}/auth/register",
        json={"email": "bob@example.com", "password": "Str0ng-Passw0rd!", "accept_tos": False},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == ErrorCode.VALIDATION_ERROR.value
    assert body["errors"][0]["field"] == "accept_tos"


async def test_api_001_register_weak_password(client, api_prefix) -> None:
    resp = await client.post(
        f"{api_prefix}/auth/register",
        json={"email": "weak@example.com", "password": "123456789012", "accept_tos": True},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.WEAK_PASSWORD.value


async def test_api_001_register_password_shorter_than_12_rejected(client, api_prefix) -> None:
    """契约要求 12~128：8~11 位即使复杂度够也必须 422（锁死 password 策略）。"""
    for password in ("Ab1!abcd", "Ab1!abcd123"):
        resp = await client.post(
            f"{api_prefix}/auth/register",
            json={
                "email": f"short-{len(password)}@example.com",
                "password": password,
                "accept_tos": True,
            },
        )
        assert resp.status_code == 422, password
        body = resp.json()
        assert body["code"] == ErrorCode.VALIDATION_ERROR.value, password
        assert any(err["field"] == "password" for err in body["errors"]), body


async def test_api_001_register_email_taken(client, api_prefix) -> None:
    payload = {"email": "dup@example.com", "password": "Str0ng-Passw0rd!", "accept_tos": True}
    assert (await client.post(f"{api_prefix}/auth/register", json=payload)).status_code == 201
    resp = await client.post(f"{api_prefix}/auth/register", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.EMAIL_TAKEN.value


# ══ API-002 登录 ═══════════════════════════════════════════


async def test_api_002_login_happy_path(client, api_prefix, account_factory) -> None:
    acc = await account_factory(email="login@example.com")
    resp = await client.post(
        f"{api_prefix}/auth/login",
        json={"email": acc.email, "password": acc.password},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == acc.email


async def test_api_002_login_wrong_password(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    resp = await client.post(
        f"{api_prefix}/auth/login", json={"email": acc.email, "password": "wrong-password-x"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == ErrorCode.INVALID_CREDENTIALS.value


async def test_api_002_login_rate_limited(client, api_prefix, account_factory) -> None:
    """5 次失败 → 429 LOGIN_RATE_LIMITED 且带 Retry-After。"""
    acc = await account_factory()
    last = None
    for _ in range(5):
        last = await client.post(
            f"{api_prefix}/auth/login", json={"email": acc.email, "password": "nope-nope-nope"}
        )
    assert last is not None
    assert last.status_code == 429
    assert last.json()["code"] == ErrorCode.LOGIN_RATE_LIMITED.value
    assert last.headers["Retry-After"] == "300"


# ══ API-003 刷新轮换 ═══════════════════════════════════════


async def test_api_003_refresh_rotates(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    resp = await client.post(
        f"{api_prefix}/auth/refresh", json={"refresh_token": acc.refresh_token}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["refresh_token"] != acc.refresh_token
    assert resp.json()["access_token"]


async def test_api_003_refresh_reuse_revokes_family(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    rotated = await client.post(
        f"{api_prefix}/auth/refresh", json={"refresh_token": acc.refresh_token}
    )
    new_refresh = rotated.json()["refresh_token"]
    # 复用旧令牌 → 401 TOKEN_REUSED 并整族撤销
    reuse = await client.post(
        f"{api_prefix}/auth/refresh", json={"refresh_token": acc.refresh_token}
    )
    assert reuse.status_code == 401
    assert reuse.json()["code"] == ErrorCode.TOKEN_REUSED.value
    # 轮换出的新令牌同族 → 也已被撤销
    after = await client.post(f"{api_prefix}/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401
    assert after.json()["code"] == ErrorCode.TOKEN_REUSED.value


async def test_api_003_refresh_unknown_token(client, api_prefix) -> None:
    resp = await client.post(f"{api_prefix}/auth/refresh", json={"refresh_token": "x" * 32})
    assert resp.status_code == 401
    assert resp.json()["code"] == ErrorCode.TOKEN_REVOKED.value


# ══ API-004 登出 ═══════════════════════════════════════════


async def test_api_004_logout_revokes_sessions(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    resp = await client.post(f"{api_prefix}/auth/logout", headers=acc.headers)
    assert resp.status_code == 204
    after = await client.post(
        f"{api_prefix}/auth/refresh", json={"refresh_token": acc.refresh_token}
    )
    assert after.status_code == 401
    assert after.json()["code"] == ErrorCode.TOKEN_REUSED.value


async def test_api_004_logout_requires_credential(client, api_prefix) -> None:
    resp = await client.post(f"{api_prefix}/auth/logout")
    assert resp.status_code == 401


async def test_api_004_logout_unknown_refresh_is_noop(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    resp = await client.post(
        f"{api_prefix}/auth/logout", headers=acc.headers, json={"refresh_token": "y" * 32}
    )
    assert resp.status_code == 204


# ══ API-005 /api/v1/auth/me ═════════════════════════════════


async def test_api_005_me_happy_path(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/auth/me", headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == user.email
    assert "benchmark_id" in body["settings"]


async def test_api_005_me_requires_credential(client, api_prefix) -> None:
    resp = await client.get(f"{api_prefix}/auth/me")
    assert resp.status_code == 401


async def test_api_005_me_rejects_garbage_token(client, api_prefix) -> None:
    resp = await client.get(f"{api_prefix}/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


# ══ API-006 注销账号 ═══════════════════════════════════════


async def test_api_006_delete_account_happy_path(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    resp = await client.delete(
        f"{api_prefix}/account",
        headers={**acc.headers, "Idempotency-Key": "delete-key-0001"},
    )
    assert resp.status_code == 204
    after = await client.post(
        f"{api_prefix}/auth/login", json={"email": acc.email, "password": acc.password}
    )
    assert after.status_code == 401


async def test_api_006_delete_account_requires_idempotency_key(client, api_prefix, user) -> None:
    resp = await client.delete(f"{api_prefix}/account", headers=user.headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_api_006_delete_account_requires_write(client, api_prefix, account_factory) -> None:
    """只读 PAT 不能注销账号 → 403。"""
    acc = await account_factory()
    created = await client.post(
        f"{api_prefix}/tokens",
        headers=acc.headers,
        json={"name": "ro", "scopes": ["read"]},
    )
    pat = created.json()["token"]["token"]
    resp = await client.delete(
        f"{api_prefix}/account",
        headers={"x-api-key": pat, "Idempotency-Key": "delete-key-0002"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.INSUFFICIENT_SCOPE.value


# ══ API-007 创建 PAT ═══════════════════════════════════════


async def test_api_007_create_pat_plaintext_once(client, api_prefix, user) -> None:
    resp = await client.post(
        f"{api_prefix}/tokens",
        headers=user.headers,
        json={"name": "script", "scopes": ["read", "write"]},
    )
    assert resp.status_code == 201, resp.text
    meta = resp.json()["token"]
    assert meta["token"].startswith("pat_")
    assert meta["token"].startswith(meta["prefix"])
    assert meta["token_id"]
    # 列表不再暴露明文
    listed = await client.get(f"{api_prefix}/tokens", headers=user.headers)
    assert all("token" not in item for item in listed.json()["items"])


async def test_api_007_pat_cannot_create_pat(client, api_prefix, account_factory) -> None:
    acc = await account_factory()
    created = await client.post(
        f"{api_prefix}/tokens", headers=acc.headers, json={"name": "p", "scopes": ["write"]}
    )
    pat = created.json()["token"]["token"]
    resp = await client.post(
        f"{api_prefix}/tokens",
        headers={"x-api-key": pat},
        json={"name": "p2", "scopes": ["read"]},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.INSUFFICIENT_SCOPE.value


async def test_api_007_create_pat_expiry_bounds(client, api_prefix, user) -> None:
    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    resp = await client.post(
        f"{api_prefix}/tokens",
        headers=user.headers,
        json={"name": "past", "scopes": ["read"], "expires_at": past},
    )
    assert resp.status_code == 422
    far = (datetime.now(tz=UTC) + timedelta(days=400)).isoformat()
    resp2 = await client.post(
        f"{api_prefix}/tokens",
        headers=user.headers,
        json={"name": "far", "scopes": ["read"], "expires_at": far},
    )
    assert resp2.status_code == 422


# ══ API-008 PAT 列表 ═══════════════════════════════════════


async def test_api_008_list_only_exposes_prefix(client, api_prefix, user) -> None:
    await client.post(
        f"{api_prefix}/tokens", headers=user.headers, json={"name": "a", "scopes": ["read"]}
    )
    resp = await client.get(f"{api_prefix}/tokens", headers=user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == len(body["items"]) == 1
    assert "token" not in body["items"][0]


async def test_api_008_list_isolated_between_users(
    client, api_prefix, user, account_factory
) -> None:
    other = await account_factory()
    await client.post(
        f"{api_prefix}/tokens", headers=other.headers, json={"name": "b", "scopes": ["read"]}
    )
    resp = await client.get(f"{api_prefix}/tokens", headers=user.headers)
    assert resp.json()["total"] == 0


async def test_api_008_list_requires_credential(client, api_prefix) -> None:
    assert (await client.get(f"{api_prefix}/tokens")).status_code == 401


# ══ API-009 撤销 PAT ════════════════════════════════════════


async def test_api_009_revoke_pat(client, api_prefix, user) -> None:
    created = await client.post(
        f"{api_prefix}/tokens", headers=user.headers, json={"name": "c", "scopes": ["read"]}
    )
    meta = created.json()["token"]
    resp = await client.delete(f"{api_prefix}/tokens/{meta['token_id']}", headers=user.headers)
    assert resp.status_code == 204
    after = await client.get(f"{api_prefix}/auth/me", headers={"x-api-key": meta["token"]})
    assert after.status_code == 401
    assert after.json()["code"] == ErrorCode.TOKEN_REVOKED.value


async def test_api_009_revoke_unknown_token(client, api_prefix, user) -> None:
    resp = await client.delete(f"{api_prefix}/tokens/pat_unknown_id", headers=user.headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


async def test_api_009_revoke_other_users_token_is_404(
    client, api_prefix, user, account_factory
) -> None:
    other = await account_factory()
    created = await client.post(
        f"{api_prefix}/tokens", headers=other.headers, json={"name": "d", "scopes": ["read"]}
    )
    token_id = created.json()["token"]["token_id"]
    resp = await client.delete(f"{api_prefix}/tokens/{token_id}", headers=user.headers)
    assert resp.status_code == 404


# ══ 运维（API-071~076）与全局契约 ═══════════════════════════


async def test_healthz_and_readyz_root(client) -> None:
    health = await client.get("/healthz")
    assert health.status_code == 200, health.text
    assert health.json()["status"] == "ok"
    ready = await client.get("/readyz")
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"


async def test_version_at_root_and_prefix(client, api_prefix) -> None:
    root = await client.get("/version")
    assert root.status_code == 200
    pref = await client.get(f"{api_prefix}/version")
    assert pref.status_code == 200
    assert pref.json()["app_version"] == root.json()["app_version"]


async def test_errors_catalog_is_complete(client, api_prefix) -> None:
    resp = await client.get(f"{api_prefix}/errors")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == len(list(ErrorCode))
    codes = {item["code"] for item in body["items"]}
    assert codes == {code.value for code in ErrorCode}
    assert set(CATALOG) == set(ErrorCode)


async def test_problem_json_envelope_fields(client) -> None:
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    for field in ("code", "user_message", "what_to_do", "retryable", "request_id"):
        assert field in body, field
    assert body["code"] == ErrorCode.NOT_FOUND.value


async def test_unknown_query_param_is_400(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/trades?bogus=1", headers=user.headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == ErrorCode.UNKNOWN_QUERY_PARAM.value


# ══ PAT 角色继承（安全回归：P0 admin PAT 越权）═══════════════


async def test_admin_pat_inherits_admin_role_and_cannot_read_trades(
    client, api_prefix, admin
) -> None:
    """admin 的 PAT 必须继承 admin 角色：否则可绕过 ADMIN_CANNOT_READ_TRADES。"""
    created = await client.post(
        f"{api_prefix}/tokens",
        headers=admin.headers,
        json={"name": "ops-script", "scopes": ["read", "write"]},
    )
    assert created.status_code == 201, created.text
    pat = created.json()["token"]["token"]

    # 同一 admin 的 JWT 被拒；PAT 也必须被拒（修复前这里是 200）。
    via_jwt = await client.get(f"{api_prefix}/trades", headers=admin.headers)
    assert via_jwt.status_code == 403
    assert via_jwt.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value

    via_pat = await client.get(f"{api_prefix}/trades", headers={"Authorization": f"Bearer {pat}"})
    assert via_pat.status_code == 403, via_pat.text
    assert via_pat.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_user_pat_inherits_user_role_and_can_read_own_trades(
    client, api_prefix, user
) -> None:
    """普通用户的 PAT 仍是 user：可读自己的流水（防止修复 admin 时把 user 也锁死）。"""
    created = await client.post(
        f"{api_prefix}/tokens",
        headers=user.headers,
        json={"name": "script", "scopes": ["read"]},
    )
    assert created.status_code == 201, created.text
    pat = created.json()["token"]["token"]
    headers = {"Authorization": f"Bearer {pat}"}

    me = await client.get(f"{api_prefix}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["user"]["role"] == "user"

    trades = await client.get(f"{api_prefix}/trades", headers=headers)
    assert trades.status_code == 200, trades.text
    assert trades.json()["items"] == []
