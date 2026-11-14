"""API-102~105：管理员维护导入列规则（全局规则，`user_id IS NULL`）。

管理员**不能**读取用户模板/预览/批次（越界读业务数据仍 403 `ADMIN_CANNOT_READ_TRADES`）；
本路由只开规则表的读写，且系统内置行只能禁用、不能删除或改 `target_field`（设计 §6.6）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_contracts.import_fields import AMOUNT_OPTION_KEYS, FIELD_KEYS
from stock_ledger.parsers.mapping import normalize_header_v2
from stock_ledger.repositories.import_mapping import (
    ImportColumnRuleRepository,
    bump_rule_version,
)
from stock_platform.audit import record
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    ImportRuleIn,
    ImportRuleOut,
    ImportRulePage,
    ImportRulePatchIn,
)

router = APIRouter(tags=["admin"], route_class=StrictQueryRoute)

_ADMIN_PREFIX = "/admin/import-column-rules"


def _rule_out(rule: Any) -> ImportRuleOut:
    return ImportRuleOut(
        rule_id=rule.id,
        broker=rule.broker,
        header_norm=rule.header_norm,
        match_type=rule.match_type,
        target_field=rule.target_field,
        options=dict(rule.options or {}),
        priority=rule.priority,
        enabled=rule.enabled,
        is_system=rule.is_system,
        is_fingerprint=rule.is_fingerprint,
        weight=rule.weight,
        version=rule.version,
        note=rule.note,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _validate_rule_fields(target_field: str, options: dict[str, Any]) -> None:
    if target_field not in FIELD_KEYS:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("target_field", "unknown", f"字段 {target_field} 不在目录中")],
        )
    extra = set(options) - AMOUNT_OPTION_KEYS
    if extra:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("options", "unsupported", f"不支持的选项：{sorted(extra)}")],
        )
    if options and target_field != "amount":
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("options", "invalid", "选项只允许用于 amount 列")],
        )


def _if_match_or_422(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("If-Match", "required", "该操作必须携带 If-Match 版本号")],
        )
    try:
        return int(raw.strip().strip('"'))
    except ValueError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("If-Match", "invalid", "If-Match 必须是整数版本号")],
        ) from exc


@router.get(_ADMIN_PREFIX, response_model=ImportRulePage, operation_id="listImportColumnRules")
async def list_import_column_rules(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    broker: Annotated[str | None, Query(max_length=16)] = None,
    enabled: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> ImportRulePage:
    """API-102：管理员查看系统规则（全局 + 用户覆盖，按创建时间倒序）。"""
    ctx.require_admin()
    state = get_state(request)
    async with state.db.session(user_id=ctx.require_user(), begin=True) as session:
        rows, next_cursor = await ImportColumnRuleRepository(session).list_admin(
            broker=broker, enabled=enabled, q=q, cursor=cursor, limit=limit
        )
    return ImportRulePage(items=[_rule_out(row) for row in rows], next_cursor=next_cursor)


@router.post(
    _ADMIN_PREFIX,
    response_model=ImportRuleOut,
    status_code=201,
    operation_id="createImportColumnRule",
)
async def create_import_column_rule(
    request: Request,
    payload: ImportRuleIn,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> ImportRuleOut:
    """API-103：新建全局规则；重复 `(broker, header_norm, target_field)` → 409。"""
    ctx.require_admin()
    user_id = ctx.require_user()
    state = get_state(request)
    _validate_rule_fields(payload.target_field, payload.options)
    async with state.db.session(user_id=user_id, begin=True) as session:
        try:
            rule = await ImportColumnRuleRepository(session).create(
                broker=payload.broker,
                header_norm=normalize_header_v2(payload.header_norm),
                match_type=payload.match_type,
                target_field=payload.target_field,
                options=dict(payload.options),
                priority=payload.priority,
                enabled=payload.enabled,
                is_fingerprint=payload.is_fingerprint,
                weight=payload.weight,
                note=payload.note,
                user_id=None,
                is_system=False,
            )
        except IntegrityError as exc:
            raise ApiError(
                ErrorCode.IMPORT_RULE_CONFLICT,
                detail="同一 broker/表头/字段的规则已存在",
            ) from exc
        await record(
            session,
            action="import_rule.created",
            actor_id=user_id,
            target_type="import_column_rule",
            target_id=rule.id,
            meta={
                "id": rule.id,
                "broker": rule.broker,
                "header_norm": rule.header_norm,
                "target_field": rule.target_field,
                "version": rule.version,
            },
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
    return _rule_out(rule)


@router.patch(
    f"{_ADMIN_PREFIX}/{{rule_id}}",
    response_model=ImportRuleOut,
    operation_id="updateImportColumnRule",
)
async def update_import_column_rule(
    rule_id: str,
    request: Request,
    payload: ImportRulePatchIn,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ImportRuleOut:
    """API-104：修改/禁用规则（`If-Match` 乐观锁；系统行不可改 `target_field`）。"""
    ctx.require_admin()
    user_id = ctx.require_user()
    state = get_state(request)
    expected_version = _if_match_or_422(if_match)
    values = payload.model_dump(exclude_none=True)

    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ImportColumnRuleRepository(session)
        rule = await repo.by_id(rule_id)
        if rule is None:
            raise NotFoundError(detail=f"规则 {rule_id} 不存在")
        if "target_field" in values:
            if rule.is_system:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR,
                    errors=[
                        FieldError(
                            "target_field",
                            "immutable",
                            "系统内置规则不可修改目标字段，请禁用后新建",
                        )
                    ],
                )
            _validate_rule_fields(str(values["target_field"]), {})
        if "options" in values:
            _validate_rule_fields(rule.target_field, dict(values["options"] or {}))
        affected = await bump_rule_version(
            session,
            rule_id,
            expected_version=expected_version,
            values=values,
            now=state.clock.now(),
        )
        if affected == 0:
            raise ApiError(ErrorCode.VERSION_CONFLICT, detail="规则已被修改，请刷新后重试")
        await session.refresh(rule)
        await record(
            session,
            action="import_rule.updated",
            actor_id=user_id,
            target_type="import_column_rule",
            target_id=rule.id,
            meta={
                "id": rule.id,
                "broker": rule.broker,
                "header_norm": rule.header_norm,
                "target_field": rule.target_field,
                "version": rule.version,
            },
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
    return _rule_out(rule)


@router.delete(
    f"{_ADMIN_PREFIX}/{{rule_id}}", status_code=204, operation_id="deleteImportColumnRule"
)
async def delete_import_column_rule(
    rule_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> Response:
    """API-105：删除自定义规则；系统内置行 → 422（请禁用）。"""
    ctx.require_admin()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ImportColumnRuleRepository(session)
        rule = await repo.by_id(rule_id)
        if rule is None:
            raise NotFoundError(detail=f"规则 {rule_id} 不存在")
        if rule.is_system:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                errors=[FieldError("rule_id", "immutable", "系统内置规则不可删除，请禁用")],
            )
        await session.delete(rule)
        await record(
            session,
            action="import_rule.deleted",
            actor_id=user_id,
            target_type="import_column_rule",
            target_id=rule.id,
            meta={
                "id": rule.id,
                "broker": rule.broker,
                "header_norm": rule.header_norm,
                "target_field": rule.target_field,
                "version": rule.version,
            },
            request_id=request.headers.get("x-request-id"),
            source="api",
        )
    return Response(status_code=204)
