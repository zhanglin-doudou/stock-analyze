"""认证端点（API-001~006）：注册/登录/刷新轮换/登出/me/注销。

刷新令牌只存哈希；复用即整族撤销（§14.1）。
失败留痕与令牌签发在**同一已提交事务**内，避免“抛错回滚掉计数”的坑。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.constants import LOGIN_FAILURE_MAX_ATTEMPTS, LOGIN_FAILURE_WINDOW_SECONDS
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_platform import audit
from stock_platform.db import set_tenant
from stock_platform.obs import get_logger
from stock_platform.repositories import (
    RefreshTokenRepository,
    UserRepository,
    UserSettingRepository,
)
from stock_platform.security import (
    generate_refresh_token,
    hash_token,
    hasher_for,
    validate_password_strength,
)
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_system_session, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    AuthOut,
    LoginIn,
    LogoutIn,
    MeOut,
    RefreshIn,
    RefreshOut,
    RegisterIn,
    UserBrief,
)

log = get_logger("stock_api.auth")
router = APIRouter(tags=["auth"], route_class=StrictQueryRoute)


def _brief(user: Any) -> UserBrief:
    return UserBrief(
        id=user.id,
        email=user.email,
        role=user.role,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )


async def _issue_session(
    session: AsyncSession,
    *,
    user: Any,
    settings: Any,
    clock: Any,
    device_name: str | None,
    request: Request,
    action: str,
) -> AuthOut:
    access, _expires = _token_service(request).issue_access(user.id, user.role, now=clock.now())
    raw_refresh, refresh_hash = generate_refresh_token()
    await RefreshTokenRepository(session).issue(
        user_id=user.id,
        token_hash=refresh_hash,
        ttl_seconds=settings.refresh_ttl_seconds,
        device_name=device_name,
        now=clock.now(),
    )
    await audit.record(
        session,
        action=action,
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        meta={"device_name": device_name},
        request_id=request.headers.get("x-request-id"),
        source="api",
    )
    return AuthOut(
        user=_brief(user),
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_ttl_seconds,
    )


def _token_service(request: Request) -> Any:
    return get_state(request).tokens


@router.post(
    "/auth/register",
    status_code=201,
    response_model=AuthOut,
    operation_id="registerAccount",
)
async def register(
    payload: RegisterIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_system_session)],
) -> AuthOut:
    state = get_state(request)
    if not payload.accept_tos:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("accept_tos", "must_accept", "必须同意服务条款")],
        )
    weak = validate_password_strength(payload.password)
    if weak:
        raise ApiError(ErrorCode.WEAK_PASSWORD, errors=weak)

    users = UserRepository(session)
    if await users.by_email(str(payload.email)) is not None:
        raise ApiError(ErrorCode.EMAIL_TAKEN)

    user = await users.create(
        email=str(payload.email),
        password_hash=hasher_for(state.settings.argon2_time_cost).hash(payload.password),
        display_name=payload.display_name,
    )
    # G7：user_setting 已启 RLS，系统 session 必须先注入新用户租户再读写默认设置。
    await set_tenant(session, user.id)
    await UserSettingRepository(session).get(user.id)
    return await _issue_session(
        session,
        user=user,
        settings=state.settings,
        clock=state.clock,
        device_name=None,
        request=request,
        action="auth.register",
    )


@router.post("/auth/login", response_model=AuthOut, operation_id="login")
async def login(payload: LoginIn, request: Request) -> AuthOut:
    """错密码累计到阈值 → 429（API-002 断言②）。"""
    state = get_state(request)
    ip_hash = _ip_hash(request)
    outcome: AuthOut | None = None
    failures = 0

    async with state.db.session(begin=True) as session:
        users = UserRepository(session)
        user = await users.by_email(str(payload.email))
        verified = bool(
            user is not None
            and user.status == "active"
            and hasher_for(state.settings.argon2_time_cost).verify(
                user.password_hash, payload.password
            )
        )
        failures = await state.limiter.record_login_attempt(
            session, email=str(payload.email), ip_hash=ip_hash, success=verified
        )
        if verified and user is not None:
            outcome = await _issue_session(
                session,
                user=user,
                settings=state.settings,
                clock=state.clock,
                device_name=payload.device_name,
                request=request,
                action="auth.login",
            )

    if outcome is None:
        if failures >= LOGIN_FAILURE_MAX_ATTEMPTS:
            raise ApiError(
                ErrorCode.LOGIN_RATE_LIMITED,
                metadata={"retry_after": LOGIN_FAILURE_WINDOW_SECONDS},
                detail="尝试次数过多，请稍后再试。",
            )
        raise ApiError(ErrorCode.INVALID_CREDENTIALS)
    return outcome


@router.post("/auth/refresh", response_model=RefreshOut, operation_id="refreshToken")
async def refresh(payload: RefreshIn, request: Request) -> RefreshOut:
    """轮换 + 复用检测（API-003 断言②）：旧令牌再用 → 整族撤销。"""
    state = get_state(request)
    outcome: RefreshOut | None = None
    failure: ErrorCode | None = None

    async with state.db.session(begin=True) as session:
        repo = RefreshTokenRepository(session)
        record = await repo.by_hash(hash_token(payload.refresh_token))
        now = state.clock.now()
        if record is None:
            failure = ErrorCode.TOKEN_REVOKED
        elif record.revoked_at is not None:
            await repo.revoke_family(record.family_id, now=now)
            failure = ErrorCode.TOKEN_REUSED
        elif record.expires_at < now:
            failure = ErrorCode.TOKEN_EXPIRED
        else:
            user = await UserRepository(session).by_id(record.user_id)
            if user is None or user.status != "active":
                failure = ErrorCode.TOKEN_REVOKED
            else:
                access, _ = state.tokens.issue_access(user.id, user.role, now=now)
                raw, digest = generate_refresh_token()
                new_record = await repo.issue(
                    user_id=user.id,
                    token_hash=digest,
                    ttl_seconds=state.settings.refresh_ttl_seconds,
                    family_id=record.family_id,
                    device_name=record.device_name,
                    now=now,
                )
                await repo.revoke(record, now=now)
                record.replaced_by = new_record.id
                outcome = RefreshOut(
                    access_token=access,
                    refresh_token=raw,
                    expires_in=state.settings.access_ttl_seconds,
                )
    if failure is not None:
        raise ApiError(failure)
    assert outcome is not None
    return outcome


@router.post("/auth/logout", status_code=204, operation_id="logout")
async def logout(
    request: Request,
    payload: LogoutIn | None = None,
    ctx: Annotated[TenantContext, Depends(get_tenant)] = None,  # type: ignore[assignment]
) -> Response:
    """撤销全部会话（API-004：refresh 随后 401 TOKEN_REVOKED）。"""
    assert ctx is not None
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(begin=True) as session:
        repo = RefreshTokenRepository(session)
        if payload is not None and payload.refresh_token:
            record = await repo.by_hash(hash_token(payload.refresh_token))
            if record is not None and record.user_id == user_id:
                await repo.revoke_family(record.family_id, now=state.clock.now())
        else:
            await repo.revoke_all_for_user(user_id, now=state.clock.now())
        await audit.record(
            session,
            action="auth.logout",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
    return Response(status_code=204)


@router.get("/auth/me", response_model=MeOut, operation_id="getMe")
async def get_me(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> MeOut:
    user_id = ctx.require_user()
    state = get_state(request)
    # G7：user_setting 已启 RLS，me 的读取/懒创建都必须在租户会话内。
    async with state.db.session(user_id=user_id, begin=True) as session:
        user = await UserRepository(session).by_id(user_id)
        if user is None:
            raise ApiError(ErrorCode.TOKEN_REVOKED)
        setting = await UserSettingRepository(session).get(user_id)
        settings_payload = {
            "benchmark_id": setting.benchmark_id,
            "privacy_flags": setting.privacy_flags,
            "display_prefs": setting.display_prefs,
            "version": setting.version,
        }
        return MeOut(user=_brief(user), settings=settings_payload)


@router.delete("/account", status_code=204, operation_id="deleteAccount")
async def delete_account(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    """FR-L7：注销并物理删除该账号全部数据（审计除外，合规留存）。"""
    if not idempotency_key:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("Idempotency-Key", "required", "该操作必须携带幂等键")],
        )
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        await audit.record(
            session,
            action="account.delete",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            meta={"idempotency_key": idempotency_key},
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
        await UserRepository(session).hard_delete(user_id)
    log.info("auth.account_deleted", user_id=str(user_id))
    return Response(status_code=204)


def _ip_hash(request: Request) -> str | None:
    from stock_platform.security import hash_ip

    forwarded = request.headers.get("x-forwarded-for")
    raw = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return hash_ip(raw)


__all__ = ["router"]
