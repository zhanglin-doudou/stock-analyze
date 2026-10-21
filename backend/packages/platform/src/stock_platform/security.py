"""认证基元：Argon2id 口令、access JWT、refresh 轮换、PAT（Architecture §14.1）。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from stock_contracts.errors import ApiError, ErrorCode, FieldError

PAT_ALPHABET: Final[str] = string.ascii_letters + string.digits
PAT_SECRET_LENGTH: Final[int] = 43
REFRESH_SECRET_BYTES: Final[int] = 32
MIN_PASSWORD_LENGTH: Final[int] = 12
MAX_PASSWORD_LENGTH: Final[int] = 128


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str
    jti: str
    exp: datetime
    iat: datetime


class PasswordHasherPort:
    """Argon2id（NFR-14）；测试可用快速替身。"""

    def __init__(self, time_cost: int = 2) -> None:
        self._hasher = PasswordHasher(time_cost=time_cost, memory_cost=64 * 1024, parallelism=2)

    def hash(self, raw: str) -> str:
        return self._hasher.hash(raw)

    def verify(self, hashed: str, raw: str) -> bool:
        try:
            return self._hasher.verify(hashed, raw)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def needs_rehash(self, hashed: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(hashed)
        except InvalidHashError:  # pragma: no cover - 损坏数据
            return True


def validate_password_strength(raw: str) -> list[FieldError]:
    """12~128 位且至少 3 类字符（API-001 断言③）。"""
    problems: list[FieldError] = []
    if len(raw) < MIN_PASSWORD_LENGTH:
        problems.append(
            FieldError(field="password", code="too_short", message=f"至少 {MIN_PASSWORD_LENGTH} 位")
        )
    if len(raw) > MAX_PASSWORD_LENGTH:
        problems.append(FieldError(field="password", code="too_long", message="密码过长"))
    classes = sum(
        1
        for ok in (
            any(c.islower() for c in raw),
            any(c.isupper() for c in raw),
            any(c.isdigit() for c in raw),
            any(not c.isalnum() for c in raw),
        )
        if ok
    )
    if classes < 3:
        problems.append(
            FieldError(
                field="password", code="too_weak", message="需包含大小写、数字、符号中的至少 3 类"
            )
        )
    return problems


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_token(raw: str, *, pepper: str = "") -> str:
    if pepper:
        return hmac.new(pepper.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return sha256_hex(raw)


def generate_refresh_token() -> tuple[str, str]:
    """返回 (明文, sha256 哈希)；明文只出现在响应里，DB 只存哈希。"""
    raw = secrets.token_urlsafe(REFRESH_SECRET_BYTES)
    return raw, sha256_hex(raw)


def generate_pat() -> tuple[str, str, str]:
    """返回 (明文, prefix, 哈希)。明文形如 `pat_<43 位字母数字>`（API-007 断言①）。"""
    secret = "".join(secrets.choice(PAT_ALPHABET) for _ in range(PAT_SECRET_LENGTH))
    raw = f"pat_{secret}"
    return raw, raw[:8], sha256_hex(raw)


class TokenService:
    """access JWT 签发/校验（HS256，sub/role/tenant/jti）。"""

    def __init__(self, secret: str, algorithm: str = "HS256", ttl_seconds: int = 900) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def issue_access(
        self, user_id: uuid.UUID, role: str, *, now: datetime | None = None
    ) -> tuple[str, datetime]:
        issued = now or datetime.now(tz=UTC)
        expires = issued + timedelta(seconds=self._ttl)
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "role": role,
            "tenant": str(user_id),
            "jti": str(uuid.uuid4()),
            "iat": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm), expires

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise ApiError(ErrorCode.TOKEN_EXPIRED) from exc
        except jwt.PyJWTError as exc:
            raise ApiError(ErrorCode.TOKEN_REVOKED, detail=str(exc)) from exc
        try:
            return AccessClaims(
                user_id=uuid.UUID(str(payload["sub"])),
                role=str(payload["role"]),
                jti=str(payload["jti"]),
                exp=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
                iat=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
            )
        except (KeyError, ValueError) as exc:  # pragma: no cover - 伪造载荷
            raise ApiError(ErrorCode.TOKEN_REVOKED, detail="载荷不完整") from exc


def hash_ip(raw: str | None) -> str | None:
    """IP 只存哈希（NFR-1 隐私）。"""
    return sha256_hex(raw)[:32] if raw else None


@lru_cache(maxsize=8)
def hasher_for(time_cost: int = 2) -> PasswordHasherPort:
    """口令哈希器单例（Argon2 参数化开销较高）。"""
    return PasswordHasherPort(time_cost=time_cost)
