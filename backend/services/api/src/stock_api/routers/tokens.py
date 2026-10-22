"""PAT 端点（API-007~009）：明文只出现一次，列表只暴露 prefix。"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from stock_contracts.constants import PAT_MAX_DAYS
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_platform import audit
from stock_platform.ids import token_id as new_token_id
from stock_platform.repositories import ApiTokenRepository
from stock_platform.security import generate_pat
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import PatCreateResponse, PatIn, PatListOut, PatMeta, PatMetaWithSecret

router = APIRouter(tags=["tokens"], route_class=StrictQueryRoute)

DEFAULT_TTL_DAYS = 90


def _meta(token: object) -> PatMeta:
    return PatMeta(
        token_id=token.id,  # type: ignore[attr-defined]
        name=token.name,  # type: ignore[attr-defined]
        prefix=token.token_prefix,  # type: ignore[attr-defined]
        scopes=list(token.scopes),  # type: ignore[attr-defined]
        expires_at=token.expires_at,  # type: ignore[attr-defined]
        created_at=token.created_at,  # type: ignore[attr-defined]
        last_used_at=token.last_used_at,  # type: ignore[attr-defined]
        revoked_at=token.revoked_at,  # type: ignore[attr-defined]
    )


@router.post(
    "/tokens", status_code=201, response_model=PatCreateResponse, operation_id="createToken"
)
async def create_token(
    payload: PatIn,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> PatCreateResponse:
    """只允许会话 Bearer 创建（PAT 不能自我复制，§14.2）。"""
    user_id = ctx.require_user()
    if ctx.credential is None or ctx.credential.kind != "access":
        raise ApiError(ErrorCode.INSUFFICIENT_SCOPE, detail="创建令牌需要会话登录凭证")
    state = get_state(request)
    now = state.clock.now()

    expires_at = payload.expires_at
    if expires_at is None:
        expires_at = now + timedelta(days=DEFAULT_TTL_DAYS)
    if expires_at <= now:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("expires_at", "in_past", "有效期必须晚于当前时间")],
        )
    if expires_at > now + timedelta(days=PAT_MAX_DAYS):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("expires_at", "too_far", f"有效期最长 {PAT_MAX_DAYS} 天")],
        )

    raw, prefix, digest = generate_pat()
    async with state.db.session(user_id=user_id, begin=True) as session:
        record = await ApiTokenRepository(session).create(
            token_id=new_token_id(),
            user_id=user_id,
            name=payload.name,
            token_prefix=prefix,
            token_hash=digest,
            scopes=list(payload.scopes),
            ttl_seconds=int((expires_at - now).total_seconds()),
            now=now,
        )
        await audit.record(
            session,
            action="token.create",
            actor_id=user_id,
            target_type="api_token",
            target_id=str(record.id),
            meta={"name": payload.name, "scopes": list(payload.scopes), "prefix": prefix},
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
        secret = PatMetaWithSecret(**_meta(record).model_dump(), token=raw)
    return PatCreateResponse(token=secret)


@router.get("/tokens", response_model=PatListOut, operation_id="listTokens")
async def list_tokens(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> PatListOut:
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        rows = await ApiTokenRepository(session).list_for_user(user_id)
        items = [_meta(row) for row in rows]
    return PatListOut(items=items, total=len(items))


@router.delete("/tokens/{token_id}", status_code=204, operation_id="revokeToken")
async def revoke_token(
    token_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> Response:
    """幂等撤销（API-009）：撤销事件进审计（action=="token.revoke"）。"""
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ApiTokenRepository(session)
        record = await repo.by_id(user_id, token_id)
        if record is None:
            raise NotFoundError(detail="找不到该令牌。")
        await repo.revoke(record, now=state.clock.now())
        await audit.record(
            session,
            action="token.revoke",
            actor_id=user_id,
            target_type="api_token",
            target_id=str(token_id),
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
    return Response(status_code=204)
