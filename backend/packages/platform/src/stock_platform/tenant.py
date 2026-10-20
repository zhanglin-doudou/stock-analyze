"""租户上下文（Architecture §14.3）：只从凭证解析，禁止由请求参数覆盖。"""

from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass, replace
from typing import Final

from stock_contracts.enums import Role
from stock_contracts.errors import ApiError, ErrorCode

_current: Final[contextvars.ContextVar[TenantContext | None]] = contextvars.ContextVar(
    "sa_tenant_context", default=None
)


@dataclass(frozen=True, slots=True)
class Credential:
    """请求凭证：access JWT 或 PAT。"""

    kind: str  # "access" | "pat" | "anonymous"
    user_id: uuid.UUID
    role: Role
    scopes: frozenset[str]
    token_id: str | None = None

    @property
    def can_write(self) -> bool:
        return self.kind == "access" or "write" in self.scopes


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: uuid.UUID | None
    role: Role
    credential: Credential | None
    request_id: str | None = None

    @classmethod
    def anonymous(cls) -> TenantContext:
        return cls(user_id=None, role=Role.USER, credential=None)

    @classmethod
    def of(cls, credential: Credential, request_id: str | None = None) -> TenantContext:
        return cls(
            user_id=credential.user_id,
            role=credential.role,
            credential=credential,
            request_id=request_id,
        )

    def with_request_id(self, request_id: str) -> TenantContext:
        return replace(self, request_id=request_id)

    def require_user(self) -> uuid.UUID:
        if self.user_id is None:
            raise ApiError(ErrorCode.TOKEN_EXPIRED, detail="缺少认证凭证")
        return self.user_id

    def require_write(self) -> uuid.UUID:
        user_id = self.require_user()
        assert self.credential is not None
        if not self.credential.can_write:
            raise ApiError(ErrorCode.INSUFFICIENT_SCOPE)
        return user_id

    def require_admin(self) -> uuid.UUID:
        user_id = self.require_user()
        if self.role is not Role.ADMIN:
            raise ApiError(ErrorCode.FORBIDDEN)
        return user_id

    def forbid_admin_trades(self) -> None:
        """admin 不可读业务明细（§14.2 授权矩阵）。"""
        if self.role is Role.ADMIN:
            raise ApiError(ErrorCode.ADMIN_CANNOT_READ_TRADES)


def set_context(ctx: TenantContext) -> contextvars.Token[TenantContext | None]:
    return _current.set(ctx)


def reset_context(token: contextvars.Token[TenantContext | None]) -> None:
    _current.reset(token)


def current_context() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, detail="租户上下文缺失")
    return ctx
