"""认证端点：注册/登录/刷新/登出/me/注销 + 邮箱验证码/忘记密码。

- 注册验证码与找回密码共用 `EmailVerificationService`；
- 发送验证码的节流/配额在 PG 中原子完成，邮件发送在 DB 事务外；
- 注册/重置失败时先提交失败计数再抛错，避免“抛错回滚掉 attempts”。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.constants import (
    EMAIL_CODE_LENGTH,
    LOGIN_FAILURE_MAX_ATTEMPTS,
    LOGIN_FAILURE_WINDOW_SECONDS,
)
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.preferences import effective_appearance
from stock_platform import audit
from stock_platform.db import set_tenant
from stock_platform.email_templates import (
    render_password_reset_email,
    render_registration_email,
)
from stock_platform.email_verification import (
    CodePurpose,
    EmailCodeThrottledError,
    PreparedCode,
    VerifyOutcome,
)
from stock_platform.mail import EmailMessage
from stock_platform.obs import get_logger
from stock_platform.repositories import (
    ApiTokenRepository,
    EmailCodeThrottleRepository,
    EmailVerificationRepository,
    RefreshTokenRepository,
    UserRepository,
    UserSettingRepository,
)
from stock_platform.repositories.email_codes import throttle_key
from stock_platform.security import (
    generate_refresh_token,
    hash_token,
    hasher_for,
    validate_password_strength,
)
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.request_ip import client_ip_hash
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    AuthOut,
    LoginIn,
    LogoutIn,
    MeOut,
    PasswordResetIn,
    RefreshIn,
    RefreshOut,
    RegisterIn,
    SendEmailCodeIn,
    SendEmailCodeOut,
    UserBrief,
    VerificationPolicyOut,
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


def _normalize_email(value: Any) -> str:
    return str(value).strip().lower()


def _verification_error(outcome: VerifyOutcome) -> ApiError:
    if outcome.status == "expired":
        message = (
            "错误次数已达上限，验证码已失效。"
            if outcome.attempts_exceeded
            else "验证码已过期，请重新获取。"
        )
        return ApiError(
            ErrorCode.EMAIL_CODE_EXPIRED,
            errors=[FieldError("email_code", "expired", message)],
        )
    return ApiError(
        ErrorCode.EMAIL_CODE_INVALID,
        errors=[
            FieldError(
                "email_code",
                "invalid",
                f"验证码不正确，还可尝试 {outcome.remaining_attempts} 次。",
            )
        ],
    )


def _code_out(
    prepared: PreparedCode | None,
    *,
    email: str,
    purpose: CodePurpose,
    settings: Any,
    now: Any,
) -> SendEmailCodeOut:
    ttl = prepared.ttl_seconds if prepared is not None else settings.email_code_ttl_seconds
    expires_at = (
        prepared.expires_at
        if prepared is not None
        else now + timedelta(seconds=settings.email_code_ttl_seconds)
    )
    return SendEmailCodeOut(
        email=email,
        purpose=purpose,
        expires_in=ttl,
        resend_after=settings.email_code_resend_seconds,
        max_attempts=(
            prepared.max_attempts if prepared is not None else settings.email_code_max_attempts
        ),
        code_length=EMAIL_CODE_LENGTH,
        expires_at=expires_at,
    )


def _message_for(prepared: PreparedCode, settings: Any) -> EmailMessage:
    if prepared.purpose == "reset_password":
        content = render_password_reset_email(
            code=prepared.code,
            ttl_seconds=prepared.ttl_seconds,
            brand=settings.email_brand,
            site_host=settings.email_site_host,
        )
        tag = "email_code_reset_password"
    else:
        content = render_registration_email(
            code=prepared.code,
            ttl_seconds=prepared.ttl_seconds,
            brand=settings.email_brand,
            site_host=settings.email_site_host,
        )
        tag = "email_code_register"
    return EmailMessage(
        to=prepared.email,
        subject=content.subject,
        html_body=content.html,
        text_body=content.text,
        tag_name=tag,
        code=prepared.code,
    )


async def _mark_failed(state: Any, *, email: str, purpose: str, now: Any, exc: Exception) -> None:
    try:
        async with state.db.session(begin=True) as session:
            await EmailVerificationRepository(session).mark_terminal(
                email=email, purpose=purpose, status="failed", now=now
            )
    except Exception:  # pragma: no cover - 标记失败不覆盖主错误
        log.exception("auth.email_code.mark_failed_failed", purpose=purpose)
    log.error(
        "auth.email_code.send_failed",
        purpose=purpose,
        provider_code=getattr(exc, "provider_code", "unknown"),
    )


@router.get(
    "/auth/verification-policy",
    response_model=VerificationPolicyOut,
    operation_id="getVerificationPolicy",
)
async def get_verification_policy(request: Request) -> VerificationPolicyOut:
    """公开只读策略：注册是否要求验证码、是否开放找回密码。"""
    settings = get_state(request).settings
    return VerificationPolicyOut(
        email_verification_required=settings.email_verification_enabled,
        password_reset_enabled=settings.password_reset_enabled,
        code_length=EMAIL_CODE_LENGTH,
        code_ttl_seconds=settings.email_code_ttl_seconds,
        resend_after_seconds=settings.email_code_resend_seconds,
        max_attempts=settings.email_code_max_attempts,
    )


@router.post(
    "/auth/email-codes",
    response_model=SendEmailCodeOut,
    operation_id="sendEmailVerificationCode",
)
async def send_email_code(payload: SendEmailCodeIn, request: Request) -> SendEmailCodeOut:
    """发送注册/找回验证码；找回对存在性统一通用 200。"""
    state = get_state(request)
    settings = state.settings
    email = _normalize_email(payload.email)
    now = state.clock.now()
    ip_hash = client_ip_hash(request, settings)
    request_id = request.headers.get("x-request-id")
    prepared: PreparedCode | None = None

    if payload.purpose == "register":
        if not settings.email_verification_enabled:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail="当前部署未启用注册邮箱验证。",
            )
        async with state.db.session(begin=True) as session:
            if await UserRepository(session).by_email(email) is not None:
                raise ApiError(ErrorCode.EMAIL_TAKEN)
            try:
                prepared = await state.email_codes.issue_registration(
                    session,
                    email=email,
                    ip_hash=ip_hash,
                    request_id=request_id,
                    now=now,
                )
            except EmailCodeThrottledError as exc:
                raise ApiError(
                    ErrorCode.RATE_LIMITED,
                    metadata={"retry_after": exc.retry_after},
                ) from exc
        assert prepared is not None
        try:
            await state.email_sender.send(_message_for(prepared, settings))
        except Exception as exc:
            await _mark_failed(state, email=email, purpose="register", now=now, exc=exc)
            raise ApiError(
                ErrorCode.EMAIL_SEND_FAILED,
                headers={"Retry-After": "60"},
            ) from exc
        log.info(
            "auth.email_code.sent",
            purpose="register",
            expires_in=prepared.ttl_seconds,
        )
        return _code_out(prepared, email=email, purpose="register", settings=settings, now=now)

    if not settings.password_reset_enabled:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="当前部署未开放邮箱找回密码。",
        )

    prepared = None
    async with state.db.session(begin=True) as session:
        user = await UserRepository(session).by_email(email)
        account_active = bool(user is not None and user.status == "active")
        try:
            prepared = await state.email_codes.issue_password_reset(
                session,
                email=email,
                ip_hash=ip_hash,
                request_id=request_id,
                now=now,
                account_active=account_active,
            )
        except EmailCodeThrottledError as exc:
            raise ApiError(
                ErrorCode.RATE_LIMITED,
                metadata={"retry_after": exc.retry_after},
            ) from exc

    if prepared is not None:
        try:
            await state.email_sender.send(_message_for(prepared, settings))
        except Exception as exc:
            await _mark_failed(state, email=email, purpose="reset_password", now=now, exc=exc)
            # 找回路径保持通用 200，避免枚举账号/供应商状态。
    return _code_out(prepared, email=email, purpose="reset_password", settings=settings, now=now)


@router.post(
    "/auth/register",
    status_code=201,
    response_model=AuthOut,
    operation_id="registerAccount",
)
async def register(payload: RegisterIn, request: Request) -> AuthOut:
    state = get_state(request)
    settings = state.settings
    if not payload.accept_tos:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("accept_tos", "must_accept", "必须同意服务条款")],
        )
    weak = validate_password_strength(payload.password)
    if weak:
        raise ApiError(ErrorCode.WEAK_PASSWORD, errors=weak)
    if settings.email_verification_enabled and payload.email_code is None:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("email_code", "required", "请输入邮箱验证码")],
        )

    email = _normalize_email(payload.email)
    now = state.clock.now()
    password_hasher = hasher_for(settings.argon2_time_cost)
    async with state.db.session(begin=True) as session:
        users = UserRepository(session)
        if await users.by_email(email) is not None:
            raise ApiError(ErrorCode.EMAIL_TAKEN)

        verified = False
        if settings.email_verification_enabled:
            outcome = await state.email_codes.verify(
                session,
                email=email,
                purpose="register",
                code=payload.email_code or "",
                now=now,
            )
            if outcome.status != "ok":
                await session.commit()  # 提交失败计数/锁定
                raise _verification_error(outcome)
            verified = True

        user = await users.create(
            email=email,
            password_hash=password_hasher.hash(payload.password),
            display_name=payload.display_name,
            email_verified_at=now if verified else None,
        )
        # G7：user_setting 已启 RLS，系统 session 必须先注入新用户租户再读写默认设置。
        await set_tenant(session, user.id)
        await UserSettingRepository(session).get(user.id)
        return await _issue_session(
            session,
            user=user,
            settings=settings,
            clock=state.clock,
            device_name=None,
            request=request,
            action="auth.register",
        )


@router.post(
    "/auth/password-reset",
    status_code=204,
    operation_id="resetPassword",
)
async def reset_password(payload: PasswordResetIn, request: Request) -> Response:
    """验证找回码后改密，并撤销全部 refresh token / PAT；不自动登录。"""
    state = get_state(request)
    settings = state.settings
    if not settings.password_reset_enabled:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="当前部署未开放邮箱找回密码。",
        )
    weak = validate_password_strength(payload.new_password)
    if weak:
        raise ApiError(ErrorCode.WEAK_PASSWORD, errors=weak)

    email = _normalize_email(payload.email)
    now = state.clock.now()
    password_hasher = hasher_for(settings.argon2_time_cost)
    async with state.db.session(begin=True) as session:
        user = await UserRepository(session).by_email(email)
        if user is None or user.status != "active":
            # 统一过期语义，不泄露账号存在/禁用。
            raise ApiError(ErrorCode.EMAIL_CODE_EXPIRED)
        if password_hasher.verify(user.password_hash, payload.new_password):
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                errors=[
                    FieldError(
                        "new_password",
                        "same_as_current",
                        "新密码不能与当前密码相同",
                    )
                ],
            )
        outcome = await state.email_codes.verify(
            session,
            email=email,
            purpose="reset_password",
            code=payload.email_code,
            now=now,
        )
        if outcome.status != "ok":
            await session.commit()
            raise _verification_error(outcome)

        promoted = user.email_verified_at is None
        user.password_hash = password_hasher.hash(payload.new_password)
        if promoted:
            user.email_verified_at = now
        refresh_revoked = await RefreshTokenRepository(session).revoke_all_for_user(
            user.id, now=now
        )
        pat_revoked = await ApiTokenRepository(session).revoke_all_for_user(user.id, now=now)
        reset_user_id = user.id
        await EmailVerificationRepository(session).delete_for_email(email=email)
        await audit.record(
            session,
            action="auth.password_reset",
            actor_id=user.id,
            target_type="user",
            target_id=str(user.id),
            meta={
                "refresh_revoked": refresh_revoked,
                "pat_revoked": pat_revoked,
                "email_verified_promoted": promoted,
            },
            request_id=request.headers.get("x-request-id"),
            ip_hash=client_ip_hash(request, settings),
            source="api",
        )
    log.info("auth.password_reset", user_id=str(reset_user_id))
    return Response(status_code=204)


@router.post("/auth/login", response_model=AuthOut, operation_id="login")
async def login(payload: LoginIn, request: Request) -> AuthOut:
    """错密码累计到阈值 → 429（API-002 断言②）。"""
    state = get_state(request)
    ip_hash = client_ip_hash(request, state.settings)
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
            # 与 GET /settings 同口径：返回生效值（形状不变），避免 raw vs effective 双口径。
            "display_prefs": effective_appearance({"display_prefs": setting.display_prefs}),
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
        user = await UserRepository(session).by_id(user_id)
        if user is not None:
            email = _normalize_email(user.email)
            await EmailVerificationRepository(session).delete_for_email(email=email)
            throttle = EmailCodeThrottleRepository(session)
            await throttle.delete_for_key(
                scope="email", key_hash=throttle_key("email:register", email)
            )
            await throttle.delete_for_key(
                scope="email", key_hash=throttle_key("email:reset_password", email)
            )
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


__all__ = ["router"]
