"""依赖注入（services/api/deps.py）：应用状态、租户上下文、事务会话、权限守卫。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import Role, TokenScope
from stock_contracts.errors import ApiError, ErrorCode, ForbiddenError
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_platform.idempotency import IdempotencyStore
from stock_platform.jobs import JobQueue
from stock_platform.ratelimit import RateLimiter
from stock_platform.repositories import ApiTokenRepository, UserRepository
from stock_platform.security import TokenService, hash_token
from stock_platform.tenant import Credential, TenantContext, set_context

PAT_PREFIX = "pat_"


@dataclass(slots=True)
class AppState:
    """进程级单例（app.state.sa）——本地单实例口径。"""

    settings: Settings
    db: Database
    clock: Clock
    queue: JobQueue
    limiter: RateLimiter
    idempotency: IdempotencyStore
    tokens: TokenService
    object_store: Any = None
    llm: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.sa
    return state


def get_settings(request: Request) -> Settings:
    return get_state(request).settings


def get_db(request: Request) -> Database:
    return get_state(request).db


def get_clock(request: Request) -> Clock:
    return get_state(request).clock


def get_queue(request: Request) -> JobQueue:
    return get_state(request).queue


def get_limiter(request: Request) -> RateLimiter:
    return get_state(request).limiter


def get_idempotency(request: Request) -> IdempotencyStore:
    return get_state(request).idempotency


def get_object_store(request: Request) -> Any:
    return get_state(request).object_store


async def get_system_session(request: Request) -> AsyncIterator[AsyncSession]:
    """无租户上下文的事务会话（认证类操作：注册/登录/刷新）。"""
    db = get_db(request)
    async with db.session(begin=True) as session:
        yield session


async def resolve_credential(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Credential | None:
    """凭证解析：access JWT 或 PAT。多个凭证 → 401 MULTIPLE_CREDENTIALS（§14.1）。"""
    if authorization and x_api_key:
        raise ApiError(ErrorCode.MULTIPLE_CREDENTIALS)
    raw = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise ApiError(ErrorCode.TOKEN_REVOKED, detail="Authorization 必须是 Bearer 形式")
        raw = parts[1].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    if not raw:
        return None
    state = get_state(request)
    if raw.startswith(PAT_PREFIX):
        return await _resolve_pat(request, raw)
    claims = state.tokens.decode_access(raw)
    return Credential(
        kind="access",
        user_id=claims.user_id,
        role=Role(claims.role),
        scopes=frozenset({TokenScope.READ.value, TokenScope.WRITE.value}),
        token_id=claims.jti,
    )


async def _resolve_pat(request: Request, raw: str) -> Credential:
    """PAT 查库（哈希比对）——读平台表，不需要租户上下文。

    PAT 必须继承所属用户的真实角色：`api_token` 本身不存角色，若在这里硬编码
    `Role.USER`，admin 用 JWT 建出的 PAT 会被降级成 user，从而绕过
    `ADMIN_CANNOT_READ_TRADES` 等身份越权防线。
    """
    state = get_state(request)
    async with state.db.session(begin=True) as session:
        token = await ApiTokenRepository(session).by_hash(hash_token(raw))
        if token is None:
            raise ApiError(ErrorCode.TOKEN_REVOKED, detail="令牌无效或已撤销")
        owner = await UserRepository(session).by_id(token.user_id)
        if owner is None or owner.status != "active":
            raise ApiError(ErrorCode.TOKEN_REVOKED, detail="令牌所属账号不可用")
        user_id = token.user_id
        role = Role(owner.role)
        scopes = frozenset(token.scopes)
        token_id = str(token.id)
        expired = token.expires_at
        revoked = token.revoked_at
    if revoked is not None:
        raise ApiError(ErrorCode.TOKEN_REVOKED)
    if expired < state.clock.now():
        raise ApiError(ErrorCode.TOKEN_EXPIRED)
    return Credential(kind="pat", user_id=user_id, role=role, scopes=scopes, token_id=token_id)


async def get_tenant(
    request: Request,
    credential: Annotated[Credential | None, Depends(resolve_credential)],
) -> TenantContext:
    ctx = TenantContext.of(credential) if credential else TenantContext.anonymous()
    set_context(ctx)
    return ctx


def tenant_ctx(request: Request) -> TenantContext:
    """从 request.state 取已注入的上下文（由 get_tenant 写入）。"""
    ctx = getattr(request.state, "sa_tenant", None)
    if ctx is None:  # pragma: no cover - 依赖顺序错误
        raise ApiError(ErrorCode.INTERNAL_ERROR, detail="租户上下文未注入")
    return ctx  # type: ignore[no-any-return]


async def get_session(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> AsyncIterator[AsyncSession]:
    """租户事务：`SET LOCAL app.user_id` 后 yield，退出即提交/回滚。"""
    request.state.sa_tenant = ctx
    db = get_db(request)
    async with db.session(user_id=ctx.user_id, begin=True) as session:
        yield session


async def get_optional_session(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> AsyncIterator[AsyncSession]:  # pragma: no cover - 与 get_session 等价，保留语义
    async with get_db(request).session(user_id=ctx.user_id, begin=True) as session:
        yield session


def require_user(ctx: Annotated[TenantContext, Depends(get_tenant)]) -> uuid.UUID:
    return ctx.require_user()


def require_write(ctx: Annotated[TenantContext, Depends(get_tenant)]) -> uuid.UUID:
    return ctx.require_write()


def require_admin(ctx: Annotated[TenantContext, Depends(get_tenant)]) -> uuid.UUID:
    return ctx.require_admin()


def forbid_admin_trades(ctx: Annotated[TenantContext, Depends(get_tenant)]) -> None:
    """admin 不可读业务明细（§14.2）。"""
    ctx.forbid_admin_trades()


def get_request_ip_hash(request: Request) -> str | None:
    from stock_platform.security import hash_ip

    forwarded = request.headers.get("x-forwarded-for")
    raw = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return hash_ip(raw)


def require_if_match(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    body_version: int | None = None,
) -> int:
    """乐观锁：If-Match 或 body.version 二者其一必填（§0.3）。"""
    if if_match is None and body_version is None:
        raise ForbiddenError(ErrorCode.VALIDATION_ERROR, detail="缺少 If-Match 或 version")
    if if_match is not None:
        cleaned = if_match.strip().strip('"')
        try:
            return int(cleaned)
        except ValueError as exc:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="If-Match 必须是版本号") from exc
    assert body_version is not None
    return body_version
