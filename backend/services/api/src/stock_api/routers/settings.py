"""用户偏好设置端点（API-055 GET / API-096 PATCH，设计 2026-11-11）。

一个读取入口 + 一个通用局部更新入口：typed 列只更新出现键、JSONB 走 RFC 7396
合并；`If-Match: W/"<version>"` 必填（CAS 乐观锁），`Idempotency-Key` 可选。
admin 与普通用户一样只能管理**自己的**偏好（不调用 `forbid_admin_trades`）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.preferences import (
    PreferenceValidationError,
    apply_patch,
    canonical_json,
    resolve,
)
from stock_platform import audit
from stock_platform.idempotency import IdempotencyStore
from stock_platform.market_models import TAXONOMY_CONCEPT, Sector
from stock_platform.models import UserSetting
from stock_platform.repositories.users import UserSettingRepository
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    AppearancePrefsOut,
    BillFlagsPrefsOut,
    ConceptExcludedOut,
    ConceptFiltersPrefsOut,
    MarketSessionsPrefsOut,
    SettingsOut,
    SettingsPatchIn,
)

router = APIRouter(tags=["settings"], route_class=StrictQueryRoute)

_ENDPOINT_PATCH = "settings.patch"

#: `platform.user_setting` 参与偏好解析的列（只读回显列不在此列）。
_SETTING_COLUMNS: tuple[str, ...] = (
    "display_prefs",
    "market_session_prefs",
    "concept_filter_prefs",
    "bill_near_equal_tolerance_bps",
    "bill_chase_margin_bps",
    "bill_stop_loss_margin_bps",
    "bill_short_term_margin_bps",
    "bill_window_trading_days",
)


def _etag(version: int) -> str:
    return f'W/"{version}"'


def _parse_if_match(if_match: str | None) -> int:
    """PATCH 必填整数版本；缺失/`*`/非法 → 422。"""
    if if_match is None:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="PATCH 必须携带 If-Match 版本",
            errors=[FieldError("If-Match", "required", "缺少 If-Match")],
        )
    cleaned = if_match.strip()
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:]
    cleaned = cleaned.strip('"')
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="If-Match 必须是版本号",
            errors=[FieldError("If-Match", "invalid", 'If-Match 必须形如 W/"1"')],
        ) from exc


def _stored_of(setting: UserSetting | None) -> dict[str, Any]:
    if setting is None:
        return {}
    return {column: getattr(setting, column) for column in _SETTING_COLUMNS}


async def _resolved_codes(session: AsyncSession, codes: Iterable[str]) -> set[str]:
    """按当前概念目录判定被排除概念是否存在（未知 code 只标 `resolved=false`）。"""
    wanted = sorted(set(codes))
    if not wanted:
        return set()
    rows = await session.scalars(
        select(Sector.sector_code).where(
            Sector.taxonomy == TAXONOMY_CONCEPT,
            Sector.sector_code.in_(wanted),
        )
    )
    return set(rows)


async def _settings_out(session: AsyncSession, setting: UserSetting | None) -> SettingsOut:
    stored = _stored_of(setting)
    effective = resolve(stored)
    excluded = effective.concept_filters["excluded"]
    resolved = await _resolved_codes(session, [str(item["code"]) for item in excluded])
    return SettingsOut(
        version=setting.version if setting is not None else 1,
        updated_at=setting.updated_at if setting is not None else None,
        appearance=AppearancePrefsOut(**effective.appearance),
        bill_flags=BillFlagsPrefsOut(**effective.bill_flags),
        market_sessions=MarketSessionsPrefsOut(**effective.market_sessions),
        concept_filters=ConceptFiltersPrefsOut(
            mode=str(effective.concept_filters["mode"]),
            excluded=[
                ConceptExcludedOut(
                    code=str(item["code"]),
                    name=str(item["name"]),
                    resolved=str(item["code"]) in resolved,
                )
                for item in excluded
            ],
        ),
        benchmark_id=setting.benchmark_id if setting is not None else "hs300_tr",
        privacy_flags=setting.privacy_flags if setting is not None else {},
    )


@router.get("/settings", response_model=SettingsOut, operation_id="getSettings")
async def get_settings(
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> SettingsOut | Response:
    """API-055：四组偏好生效值 + 只读回显 + `version`/ETag；无行返回默认视图。"""
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        setting = await UserSettingRepository(session).get_for_read(user_id)
        version = setting.version if setting is not None else 1
        etag = _etag(version)
        if if_none_match is not None and if_none_match.strip() == etag:
            return Response(status_code=304, headers={"ETag": etag})
        out = await _settings_out(session, setting)
    response.headers["ETag"] = etag
    return out


@router.patch("/settings", response_model=SettingsOut, operation_id="patchSettings")
async def patch_settings(
    payload: SettingsPatchIn,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SettingsOut | JSONResponse:
    """API-096：局部更新偏好（typed 局部列 + JSONB merge patch，CAS 版本）。"""
    user_id = ctx.require_write()
    state = get_state(request)
    expected_version = _parse_if_match(if_match)
    patch = payload.model_dump(exclude_unset=True)
    if not patch:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="没有可修改的字段",
            errors=[FieldError("body", "empty", "请至少提供一个配置组")],
        )
    if idempotency_key is not None and not 16 <= len(idempotency_key) <= 128:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("Idempotency-Key", "invalid", "幂等键长度必须为 16~128")],
        )

    request_hash = hashlib.sha256(
        canonical_json(
            {
                "method": "PATCH",
                "path": "/settings",
                "if_match": if_match,
                "body": patch,
            }
        ).encode("utf-8")
    ).hexdigest()

    async with state.db.session(user_id=user_id, begin=True) as session:
        if idempotency_key is not None:
            record = await state.idempotency.begin(
                session,
                user_id=user_id,
                endpoint=_ENDPOINT_PATCH,
                key=idempotency_key,
                request_hash=request_hash,
            )
            if record is not None:
                replayed = IdempotencyStore.payload_of(record) or {}
                replay_version = int(replayed.get("version", expected_version))
                return JSONResponse(
                    content=replayed,
                    status_code=IdempotencyStore.status_of(record),
                    headers={
                        "ETag": _etag(replay_version),
                        "Idempotency-Replayed": "true",
                        "X-Settings-Changed": str(replay_version > expected_version).lower(),
                    },
                )

        repo = UserSettingRepository(session)
        setting = await repo.get_for_read(user_id)
        try:
            result = apply_patch(_stored_of(setting), patch)
        except PreferenceValidationError as exc:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail=exc.message,
                errors=[FieldError(exc.field, "invalid", exc.message)],
            ) from exc

        changed = bool(result.changed)
        if not changed:
            out = await _settings_out(session, setting)
            body = out.model_dump(mode="json")
            if idempotency_key is not None:
                await state.idempotency.complete(
                    session,
                    user_id=user_id,
                    endpoint=_ENDPOINT_PATCH,
                    key=idempotency_key,
                    status_code=200,
                    body=body,
                )
            version = setting.version if setting is not None else 1
        else:
            updated = await repo.update_patch(
                user_id, columns=result.columns, expected_version=expected_version
            )
            if updated is None:
                raise ApiError(
                    ErrorCode.VERSION_CONFLICT,
                    detail=f"设置版本冲突：提交 {expected_version}",
                )
            await audit.record(
                session,
                action="settings.patch",
                actor_id=user_id,
                target_type="user_setting",
                target_id=str(user_id),
                meta={
                    "changed": result.changed,
                    "from_version": expected_version,
                    "to_version": updated.version,
                },
                request_id=request.headers.get("x-request-id"),
                source="api",
            )
            out = await _settings_out(session, updated)
            body = out.model_dump(mode="json")
            if idempotency_key is not None:
                await state.idempotency.complete(
                    session,
                    user_id=user_id,
                    endpoint=_ENDPOINT_PATCH,
                    key=idempotency_key,
                    status_code=200,
                    body=body,
                )
            version = updated.version

    response.headers["ETag"] = _etag(version)
    response.headers["X-Settings-Changed"] = str(changed).lower()
    return out


__all__ = ["router"]
