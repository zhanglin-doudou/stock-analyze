"""API-117/118/119：邮箱验证码、认证策略、忘记密码。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from stock_api.app import create_app
from stock_contracts.clock import FrozenClock
from stock_platform.mail import EmailDeliveryError, EmailMessage, SendReceipt

pytestmark = pytest.mark.api

API = "/api/v1"


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.fail = False

    async def send(self, message: EmailMessage) -> SendReceipt:
        if self.fail:
            raise EmailDeliveryError("fake failure", provider_code="FakeError")
        self.messages.append(message)
        return SendReceipt(provider="fake", request_id="fake-request")

    @property
    def last_code(self) -> str:
        assert self.messages
        code = self.messages[-1].code
        assert code is not None
        return code


@dataclass
class EmailHarness:
    app: Any
    sender: FakeEmailSender
    clock: FrozenClock


@pytest.fixture
def email_harness(settings) -> EmailHarness:
    sender = FakeEmailSender()
    clock = FrozenClock(datetime.now(tz=UTC))
    email_settings = settings.model_copy(
        update={
            "email_verification_enabled": True,
            "password_reset_enabled": True,
            "email_provider": "log",
            "email_dm_account_name": "noreply@example.com",
            "email_code_pepper": "test-email-code-pepper-0123456789abcdef",
        }
    )
    app = create_app(email_settings, clock=clock, email_sender=sender)
    return EmailHarness(app=app, sender=sender, clock=clock)


@pytest.fixture
async def email_client(email_harness: EmailHarness):
    transport = httpx.ASGITransport(app=email_harness.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac


async def test_api_118_verification_policy(email_client: httpx.AsyncClient) -> None:
    resp = await email_client.get(f"{API}/auth/verification-policy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email_verification_required"] is True
    assert body["password_reset_enabled"] is True
    assert body["code_ttl_seconds"] == 600
    assert body["resend_after_seconds"] == 60


async def test_api_117_send_register_code_and_ip_throttle(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    first = await email_client.post(f"{API}/auth/email-codes", json={"email": "new@example.com"})
    assert first.status_code == 200, first.text
    assert first.json()["expires_in"] == 600
    assert len(email_harness.sender.messages) == 1

    second = await email_client.post(f"{API}/auth/email-codes", json={"email": "other@example.com"})
    assert second.status_code == 429, second.text
    assert 1 <= int(second.headers["Retry-After"]) <= 60


async def test_api_117_send_register_email_taken(
    email_client: httpx.AsyncClient, account_factory
) -> None:
    acc = await account_factory()
    resp = await email_client.post(f"{API}/auth/email-codes", json={"email": acc.email})
    assert resp.status_code == 409
    assert resp.json()["code"] == "EMAIL_TAKEN"


async def test_api_117_reset_code_is_generic_for_unknown_email(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    resp = await email_client.post(
        f"{API}/auth/email-codes",
        json={"email": "missing@example.com", "purpose": "reset_password"},
    )
    assert resp.status_code == 200, resp.text
    assert email_harness.sender.messages == []


async def test_api_001_register_with_code_and_consume(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    email = "code-register@example.com"
    sent = await email_client.post(f"{API}/auth/email-codes", json={"email": email})
    assert sent.status_code == 200, sent.text
    code = email_harness.sender.last_code

    registered = await email_client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "Str0ng-Passw0rd!",
            "accept_tos": True,
            "email_code": code,
        },
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["user"]["email_verified"] is True

    duplicate = await email_client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "Str0ng-Passw0rd!",
            "accept_tos": True,
            "email_code": code,
        },
    )
    assert duplicate.status_code == 409


async def test_api_001_wrong_code_locks_on_tenth(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    email = "code-wrong@example.com"
    sent = await email_client.post(f"{API}/auth/email-codes", json={"email": email})
    assert sent.status_code == 200, sent.text
    payload = {
        "email": email,
        "password": "Str0ng-Passw0rd!",
        "accept_tos": True,
        "email_code": "000000",
    }
    for index in range(9):
        resp = await email_client.post(f"{API}/auth/register", json=payload)
        assert resp.status_code == 422, (index, resp.text)
        assert resp.json()["code"] == "EMAIL_CODE_INVALID"
    tenth = await email_client.post(f"{API}/auth/register", json=payload)
    assert tenth.status_code == 410, tenth.text
    assert tenth.json()["code"] == "EMAIL_CODE_EXPIRED"

    correct = await email_client.post(
        f"{API}/auth/register", json={**payload, "email_code": email_harness.sender.last_code}
    )
    assert correct.status_code == 410


async def test_api_001_pre_errors_do_not_consume_code(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    email = "code-preerror@example.com"
    sent = await email_client.post(f"{API}/auth/email-codes", json={"email": email})
    assert sent.status_code == 200, sent.text
    code = email_harness.sender.last_code

    weak = await email_client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "123456789012",
            "accept_tos": True,
            "email_code": code,
        },
    )
    assert weak.status_code == 422
    assert weak.json()["code"] == "WEAK_PASSWORD"

    ok = await email_client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "Str0ng-Passw0rd!",
            "accept_tos": True,
            "email_code": code,
        },
    )
    assert ok.status_code == 201, ok.text


async def test_api_001_ttl_expiry(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    email = "code-expired@example.com"
    sent = await email_client.post(f"{API}/auth/email-codes", json={"email": email})
    assert sent.status_code == 200, sent.text
    code = email_harness.sender.last_code
    email_harness.clock.advance(seconds=601)
    resp = await email_client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": "Str0ng-Passw0rd!",
            "accept_tos": True,
            "email_code": code,
        },
    )
    assert resp.status_code == 410, resp.text


async def test_api_119_password_reset_revokes_credentials(
    email_client: httpx.AsyncClient, email_harness: EmailHarness, account_factory, client
) -> None:
    acc = await account_factory()
    pat_resp = await client.post(
        f"{API}/tokens", headers=acc.headers, json={"name": "script", "scopes": ["read"]}
    )
    assert pat_resp.status_code == 201, pat_resp.text
    pat = pat_resp.json()["token"]["token"]

    sent = await email_client.post(
        f"{API}/auth/email-codes",
        json={"email": acc.email, "purpose": "reset_password"},
    )
    assert sent.status_code == 200, sent.text
    code = email_harness.sender.last_code

    reset = await email_client.post(
        f"{API}/auth/password-reset",
        json={
            "email": acc.email,
            "email_code": code,
            "new_password": "N3w-Str0ng-Passw0rd!",
        },
    )
    assert reset.status_code == 204, reset.text

    old_login = await email_client.post(
        f"{API}/auth/login", json={"email": acc.email, "password": acc.password}
    )
    assert old_login.status_code == 401
    new_login = await email_client.post(
        f"{API}/auth/login",
        json={"email": acc.email, "password": "N3w-Str0ng-Passw0rd!"},
    )
    assert new_login.status_code == 200, new_login.text

    old_refresh = await email_client.post(
        f"{API}/auth/refresh", json={"refresh_token": acc.refresh_token}
    )
    assert old_refresh.status_code == 401
    old_pat = await email_client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {pat}"})
    assert old_pat.status_code == 401


async def test_api_119_unknown_email_is_expired(
    email_client: httpx.AsyncClient,
) -> None:
    resp = await email_client.post(
        f"{API}/auth/password-reset",
        json={
            "email": "nobody@example.com",
            "email_code": "123456",
            "new_password": "Str0ng-Passw0rd!",
        },
    )
    assert resp.status_code == 410, resp.text
    assert resp.json()["code"] == "EMAIL_CODE_EXPIRED"


async def test_api_117_register_send_failure_returns_503(
    email_client: httpx.AsyncClient, email_harness: EmailHarness
) -> None:
    email_harness.sender.fail = True
    resp = await email_client.post(
        f"{API}/auth/email-codes", json={"email": "smtp-fail@example.com"}
    )
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After") == "60"
    assert resp.json()["code"] == "EMAIL_SEND_FAILED"


async def test_api_117_reset_send_failure_stays_generic_200(
    email_client: httpx.AsyncClient, email_harness: EmailHarness, account_factory
) -> None:
    acc = await account_factory()
    email_harness.sender.fail = True
    resp = await email_client.post(
        f"{API}/auth/email-codes",
        json={"email": acc.email, "purpose": "reset_password"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["purpose"] == "reset_password"
    assert email_harness.sender.messages == []
