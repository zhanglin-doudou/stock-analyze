"""邮箱验证码生命周期服务（注册 + 忘记密码）。

策略：每邮箱每用途只保留一条当前码；发送前做 IP/邮箱冷却与配额；验证时
行锁 + 原子失败计数；第 max_attempts 次失败直接锁定并返回“过期”语义。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform import audit
from stock_platform.config import Settings
from stock_platform.repositories.email_codes import (
    EmailCodeThrottleRepository,
    EmailVerificationRepository,
    Reservation,
    throttle_key,
)
from stock_platform.security import EmailCodeHasher, sha256_hex

CodePurpose = Literal["register", "reset_password"]
VerifyStatus = Literal["ok", "invalid", "expired"]


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


@dataclass(frozen=True, slots=True)
class PreparedCode:
    email: str
    purpose: CodePurpose
    code: str
    expires_at: datetime
    ttl_seconds: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    status: VerifyStatus
    remaining_attempts: int = 0
    attempts_exceeded: bool = False

    @classmethod
    def ok(cls) -> VerifyOutcome:
        return cls(status="ok")

    @classmethod
    def invalid(cls, remaining: int) -> VerifyOutcome:
        return cls(status="invalid", remaining_attempts=max(0, remaining))

    @classmethod
    def expired(cls, *, attempts_exceeded: bool = False) -> VerifyOutcome:
        return cls(status="expired", attempts_exceeded=attempts_exceeded)


class EmailCodeThrottledError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("email code send throttled")
        self.retry_after = max(1, retry_after)


class EmailVerificationService:
    def __init__(
        self,
        settings: Settings,
        hasher: EmailCodeHasher,
        *,
        code_factory: Callable[[], str] = generate_code,
    ) -> None:
        self._settings = settings
        self._hasher = hasher
        self._code_factory = code_factory

    # ── 发送 ─────────────────────────────────────────────
    async def issue_registration(
        self,
        session: AsyncSession,
        *,
        email: str,
        ip_hash: str,
        request_id: str | None,
        now: datetime,
    ) -> PreparedCode:
        throttle = EmailCodeThrottleRepository(session)
        await self._reserve_ip(throttle, ip_hash=ip_hash, now=now)
        reserved = await throttle.reserve(
            scope="email",
            key_hash=throttle_key("email:register", email),
            now=now,
            cooldown_seconds=self._settings.email_code_resend_seconds,
            hour_limit=self._settings.email_code_email_hourly_limit,
            day_limit=self._settings.email_code_email_daily_limit,
        )
        if not reserved.allowed:
            raise EmailCodeThrottledError(reserved.retry_after)
        return await self._issue(
            session,
            email=email,
            purpose="register",
            ip_hash=ip_hash,
            request_id=request_id,
            now=now,
        )

    async def issue_password_reset(
        self,
        session: AsyncSession,
        *,
        email: str,
        ip_hash: str,
        request_id: str | None,
        now: datetime,
        account_active: bool,
    ) -> PreparedCode | None:
        throttle = EmailCodeThrottleRepository(session)
        await self._reserve_ip(throttle, ip_hash=ip_hash, now=now)
        if not account_active:
            # IP 冷却已消耗；不泄露账号不存在/禁用。
            return None
        reserved = await throttle.reserve(
            scope="email",
            key_hash=throttle_key("email:reset_password", email),
            now=now,
            cooldown_seconds=self._settings.email_code_resend_seconds,
            hour_limit=self._settings.email_code_email_hourly_limit,
            day_limit=self._settings.email_code_email_daily_limit,
        )
        if not reserved.allowed:
            # 防枚举：静默不发，仍返回通用 200。
            return None
        return await self._issue(
            session,
            email=email,
            purpose="reset_password",
            ip_hash=ip_hash,
            request_id=request_id,
            now=now,
        )

    async def _reserve_ip(
        self, throttle: EmailCodeThrottleRepository, *, ip_hash: str, now: datetime
    ) -> Reservation:
        reserved = await throttle.reserve(
            scope="ip",
            key_hash=throttle_key("ip", ip_hash),
            now=now,
            cooldown_seconds=self._settings.email_code_resend_seconds,
            hour_limit=self._settings.email_code_ip_hourly_limit,
            day_limit=self._settings.email_code_ip_daily_limit,
        )
        if not reserved.allowed:
            raise EmailCodeThrottledError(reserved.retry_after)
        return reserved

    async def _issue(
        self,
        session: AsyncSession,
        *,
        email: str,
        purpose: CodePurpose,
        ip_hash: str,
        request_id: str | None,
        now: datetime,
    ) -> PreparedCode:
        code = self._code_factory()
        expires_at = now + timedelta(seconds=self._settings.email_code_ttl_seconds)
        await EmailVerificationRepository(session).upsert(
            email=email,
            purpose=purpose,
            code_hash=self._hasher.hash(code),
            now=now,
            expires_at=expires_at,
            max_attempts=self._settings.email_code_max_attempts,
            ip_hash=ip_hash,
            request_id=request_id,
        )
        await audit.record(
            session,
            action="auth.email_code.send",
            actor_id=None,
            target_type="email",
            target_id=sha256_hex(email),
            meta={
                "purpose": purpose,
                "expires_at": expires_at.isoformat(),
                "provider": self._settings.email_provider,
            },
            request_id=request_id,
            ip_hash=ip_hash,
            source="api",
        )
        return PreparedCode(
            email=email,
            purpose=purpose,
            code=code,
            expires_at=expires_at,
            ttl_seconds=self._settings.email_code_ttl_seconds,
            max_attempts=self._settings.email_code_max_attempts,
        )

    # ── 验证 ─────────────────────────────────────────────
    async def verify(
        self,
        session: AsyncSession,
        *,
        email: str,
        purpose: CodePurpose,
        code: str,
        now: datetime,
    ) -> VerifyOutcome:
        repo = EmailVerificationRepository(session)
        row = await repo.get_for_update(email=email, purpose=purpose)
        if row is None:
            return VerifyOutcome.expired()
        if row.status != "active" or row.expires_at <= now or row.attempts >= row.max_attempts:
            if row.status == "active":
                await repo.mark_terminal(email=email, purpose=purpose, status="expired", now=now)
            return VerifyOutcome.expired()
        if not self._hasher.verify(row.code_hash, code):
            outcome = await repo.record_failure(email=email, purpose=purpose, now=now)
            if outcome is None:
                return VerifyOutcome.expired()
            if outcome.attempts >= row.max_attempts:
                return VerifyOutcome.expired(attempts_exceeded=True)
            return VerifyOutcome.invalid(row.max_attempts - outcome.attempts)
        consumed = await repo.consume(email=email, purpose=purpose, now=now)
        return VerifyOutcome.ok() if consumed else VerifyOutcome.expired()


__all__ = [
    "CodePurpose",
    "EmailCodeThrottledError",
    "EmailVerificationService",
    "PreparedCode",
    "VerifyOutcome",
    "generate_code",
]
