"""邮箱验证码与发送节流仓储（认证前置表，显式无 user_id）。

与根 AGENTS 的「仓储方法显式收 user_id」是显式例外：注册/找回前尚无
user_id，只能按 email / IP 哈希访问；原因同 `platform.user`、`refresh_token`、
`login_attempt`。禁止后续在这些方法里隐式取租户上下文。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform.models import EmailCodeSendThrottle, EmailVerificationCode


def throttle_key(scope: str, key: str) -> str:
    """节流键哈希：不把原始 IP/邮箱落表。"""
    return hashlib.sha256(f"{scope}:{key}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    attempts: int
    status: str


@dataclass(frozen=True, slots=True)
class Reservation:
    allowed: bool
    retry_after: int = 0
    reason: str = ""

    @classmethod
    def denied(cls, retry_after: int, reason: str) -> Reservation:
        return cls(allowed=False, retry_after=max(1, retry_after), reason=reason)


class EmailVerificationRepository:
    """验证码当前行；构造函数收 session，方法不再重复传。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, *, email: str, purpose: str) -> EmailVerificationCode | None:
        return await self._session.scalar(
            select(EmailVerificationCode)
            .where(
                EmailVerificationCode.email == email,
                EmailVerificationCode.purpose == purpose,
            )
            .with_for_update()
        )

    async def upsert(
        self,
        *,
        email: str,
        purpose: str,
        code_hash: str,
        now: datetime,
        expires_at: datetime,
        max_attempts: int,
        ip_hash: str | None,
        request_id: str | None,
    ) -> EmailVerificationCode:
        stmt = (
            pg_insert(EmailVerificationCode)
            .values(
                email=email,
                purpose=purpose,
                code_hash=code_hash,
                status="active",
                attempts=0,
                max_attempts=max_attempts,
                sent_at=now,
                expires_at=expires_at,
                consumed_at=None,
                last_attempt_at=None,
                send_ip_hash=ip_hash,
                request_id=request_id,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    EmailVerificationCode.email,
                    EmailVerificationCode.purpose,
                ],
                set_={
                    "code_hash": code_hash,
                    "status": "active",
                    "attempts": 0,
                    "max_attempts": max_attempts,
                    "sent_at": now,
                    "expires_at": expires_at,
                    "consumed_at": None,
                    "last_attempt_at": None,
                    "send_ip_hash": ip_hash,
                    "request_id": request_id,
                    "updated_at": now,
                },
            )
            .returning(EmailVerificationCode)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def record_failure(
        self, *, email: str, purpose: str, now: datetime
    ) -> FailureOutcome | None:
        next_attempt = EmailVerificationCode.attempts + 1
        stmt = (
            update(EmailVerificationCode)
            .where(
                EmailVerificationCode.email == email,
                EmailVerificationCode.purpose == purpose,
                EmailVerificationCode.status == "active",
                EmailVerificationCode.expires_at > now,
                EmailVerificationCode.attempts < EmailVerificationCode.max_attempts,
            )
            .values(
                attempts=next_attempt,
                last_attempt_at=now,
                status=case(
                    (next_attempt >= EmailVerificationCode.max_attempts, "locked"),
                    else_=EmailVerificationCode.status,
                ),
                code_hash=case(
                    (next_attempt >= EmailVerificationCode.max_attempts, ""),
                    else_=EmailVerificationCode.code_hash,
                ),
                updated_at=now,
            )
            .returning(
                EmailVerificationCode.attempts,
                EmailVerificationCode.status,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return FailureOutcome(attempts=int(row[0]), status=str(row[1]))

    async def consume(self, *, email: str, purpose: str, now: datetime) -> bool:
        stmt = (
            update(EmailVerificationCode)
            .where(
                EmailVerificationCode.email == email,
                EmailVerificationCode.purpose == purpose,
                EmailVerificationCode.status == "active",
                EmailVerificationCode.expires_at > now,
                EmailVerificationCode.attempts < EmailVerificationCode.max_attempts,
            )
            .values(
                status="consumed",
                consumed_at=now,
                code_hash="",
                updated_at=now,
            )
            .returning(EmailVerificationCode.email)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def mark_terminal(
        self,
        *,
        email: str,
        purpose: str,
        status: str,
        now: datetime,
    ) -> None:
        await self._session.execute(
            update(EmailVerificationCode)
            .where(
                EmailVerificationCode.email == email,
                EmailVerificationCode.purpose == purpose,
            )
            .values(
                status=status,
                code_hash="",
                updated_at=now,
            )
        )

    async def delete_for_email(self, *, email: str) -> int:
        result = await self._session.execute(
            delete(EmailVerificationCode).where(EmailVerificationCode.email == email)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def purge(self, *, before: datetime) -> int:
        result = await self._session.execute(
            delete(EmailVerificationCode).where(EmailVerificationCode.expires_at < before)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class EmailCodeThrottleRepository:
    """IP/邮箱冷却与配额；同一事务内先锁行再判断。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self,
        *,
        scope: str,
        key_hash: str,
        now: datetime,
        cooldown_seconds: int,
        hour_limit: int,
        day_limit: int,
    ) -> Reservation:
        await self._session.execute(
            pg_insert(EmailCodeSendThrottle)
            .values(scope=scope, key_hash=key_hash)
            .on_conflict_do_nothing(
                index_elements=[
                    EmailCodeSendThrottle.scope,
                    EmailCodeSendThrottle.key_hash,
                ]
            )
        )
        row = await self._session.scalar(
            select(EmailCodeSendThrottle)
            .where(
                EmailCodeSendThrottle.scope == scope,
                EmailCodeSendThrottle.key_hash == key_hash,
            )
            .with_for_update()
        )
        assert row is not None

        if row.last_sent_at is not None and cooldown_seconds > 0:
            elapsed = int((now - row.last_sent_at).total_seconds())
            if elapsed < cooldown_seconds:
                return Reservation.denied(cooldown_seconds - elapsed, "cooldown")

        if row.hour_start is None or now >= row.hour_start + timedelta(hours=1):
            row.hour_start = now
            row.hour_count = 0
        if hour_limit > 0 and row.hour_count >= hour_limit:
            reset = row.hour_start + timedelta(hours=1)
            return Reservation.denied(int((reset - now).total_seconds()), "hour_quota")

        if row.day_start is None or now >= row.day_start + timedelta(days=1):
            row.day_start = now
            row.day_count = 0
        if day_limit > 0 and row.day_count >= day_limit:
            reset = row.day_start + timedelta(days=1)
            return Reservation.denied(int((reset - now).total_seconds()), "day_quota")

        row.last_sent_at = now
        row.hour_count += 1
        row.day_count += 1
        row.updated_at = now
        return Reservation(allowed=True)

    async def delete_for_key(self, *, scope: str, key_hash: str) -> int:
        result = await self._session.execute(
            delete(EmailCodeSendThrottle).where(
                EmailCodeSendThrottle.scope == scope,
                EmailCodeSendThrottle.key_hash == key_hash,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def purge(self, *, before: datetime) -> int:
        result = await self._session.execute(
            delete(EmailCodeSendThrottle).where(
                EmailCodeSendThrottle.last_sent_at.is_not(None),
                EmailCodeSendThrottle.last_sent_at < before,
                EmailCodeSendThrottle.day_start < before,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "EmailCodeThrottleRepository",
    "EmailVerificationRepository",
    "FailureOutcome",
    "Reservation",
    "throttle_key",
]
