"""摘要规则端点（API-022~025）：系统默认规则 + 用户自定义规则的 CRUD。

系统规则（`user_id IS NULL`）是代码资产，对所有账号生效、**不可编辑/删除**；用户删除
自己的规则后自然回退到系统规则（API-025）。跨账号规则与系统规则的写操作一律 404
（不泄露存在性），PATCH 用 `If-Match: W/"<version>"` 乐观锁（旧版本 → 409 VERSION_CONFLICT）。

`trade.category` 的合法取值与 ledger 分类口径一致；非法 category / 无法编译的 regex
→ 422 VALIDATION_ERROR（不落库脏规则）。
"""

from __future__ import annotations

import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_ledger.models import SummaryRule
from stock_ledger.repositories.rules import SummaryRuleRepository, seed_system_rules
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    SummaryRuleCreateIn,
    SummaryRuleOut,
    SummaryRulePage,
    SummaryRulePatchIn,
)

router = APIRouter(tags=["summary-rules"], route_class=StrictQueryRoute)

#: `trade.category` 合法取值（FIFO EventKind 的全部业务分类）。
ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {
        "buy",
        "sell",
        "bonus",
        "dividend",
        "rights",
        "interest",
        "fee",
        "transfer",
        "new_stock",
        "other",
    }
)

#: 用户规则码：服务端生成 `usr_` + 8 hex（API-023）；与系统码 `sys_` 区分。
RULE_CODE_PREFIX = "usr_"
_RULE_CODE_BYTES = 4


def _rule_out(rule: SummaryRule) -> SummaryRuleOut:
    """ORM `SummaryRule` → API 边界模型。"""
    return SummaryRuleOut(
        rule_id=rule.id,
        code=rule.code,
        name=rule.name,
        category=rule.category,
        side=rule.side,
        match_type=rule.match_type,
        pattern=rule.pattern,
        priority=rule.priority,
        enabled=rule.enabled,
        is_system=rule.is_system,
        version=rule.version,
        created_at=rule.created_at,
    )


def _etag(version: int) -> str:
    """弱 ETag（§0.8）：`W/"<version>"`。"""
    return f'W/"{version}"'


def _parse_if_match(if_match: str | None) -> int:
    """PATCH 必填 `If-Match`；缺失/非法 → 422 VALIDATION_ERROR。"""
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
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="If-Match 必须是版本号") from exc


def _code_exists(code: str, rules: list[SummaryRule]) -> bool:
    return any(rule.code == code for rule in rules)


def _generate_code(rules: list[SummaryRule]) -> str:
    """`usr_` + 8 hex；极小概率撞码时重新生成（同用户 code 唯一）。"""
    for _ in range(8):
        candidate = f"{RULE_CODE_PREFIX}{secrets.token_hex(_RULE_CODE_BYTES)}"
        if not _code_exists(candidate, rules):
            return candidate
    raise ApiError(ErrorCode.INTERNAL_ERROR, detail="规则码生成失败，请重试")


def _validate_category(category: str) -> None:
    if category not in ALLOWED_CATEGORIES:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail=f"非法 category: {category}",
            errors=[
                FieldError("category", "invalid", f"category 必须属于 {sorted(ALLOWED_CATEGORIES)}")
            ],
        )


def _validate_matcher(match_type: str, pattern: str) -> None:
    """regex 规则必须可编译，否则会在分类时静默失配。"""
    if match_type != "regex":
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="pattern 不是合法正则",
            errors=[FieldError("pattern", "invalid_regex", str(exc))],
        ) from exc


def _duplicate_rule(
    rules: list[SummaryRule], *, side: str, match_type: str, pattern: str
) -> SummaryRule | None:
    """同 side/match_type/pattern 的用户规则视为重复（避免多条规则互相遮蔽）。"""
    for rule in rules:
        if (
            not rule.is_system
            and rule.side == side
            and rule.match_type == match_type
            and rule.pattern == pattern
        ):
            return rule
    return None


@router.get("/summary-rules", response_model=SummaryRulePage, operation_id="listSummaryRules")
async def list_summary_rules(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> SummaryRulePage:
    """API-022：规则列表（系统规则 + 本人规则，含 disabled），按优先级升序。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        # 系统规则是代码资产：任何入口先幂等自愈，保证默认规则存在且词表与代码一致。
        await seed_system_rules(session)
        rules = await SummaryRuleRepository(session).list_for_user(user_id)
    items = [_rule_out(rule) for rule in rules]
    return SummaryRulePage(items=items, total=len(items))


@router.post(
    "/summary-rules",
    status_code=201,
    response_model=SummaryRuleOut,
    operation_id="createSummaryRule",
)
async def create_summary_rule(
    payload: SummaryRuleCreateIn,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> SummaryRuleOut:
    """API-023：新建用户规则（`code` 服务端生成；重复/非法 category → 422）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    _validate_category(payload.category)
    _validate_matcher(payload.match_type, payload.pattern)

    async with state.db.session(user_id=user_id, begin=True) as session:
        await seed_system_rules(session)
        repo = SummaryRuleRepository(session)
        existing = await repo.list_for_user(user_id)
        duplicate = _duplicate_rule(
            existing, side=payload.side, match_type=payload.match_type, pattern=payload.pattern
        )
        if duplicate is not None:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail=f"已存在相同匹配模式的规则 {duplicate.id}",
            )
        rule = await repo.create(
            user_id,
            code=_generate_code(existing),
            name=payload.name,
            category=payload.category,
            side=payload.side,
            match_type=payload.match_type,
            pattern=payload.pattern,
            priority=payload.priority,
            enabled=payload.enabled,
        )
        out = _rule_out(rule)
    return out


@router.patch(
    "/summary-rules/{rule_id}", response_model=SummaryRuleOut, operation_id="updateSummaryRule"
)
async def update_summary_rule(
    rule_id: str,
    payload: SummaryRulePatchIn,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> SummaryRuleOut:
    """API-024：修改用户规则（If-Match 乐观锁；旧版本 409；系统/他人规则 404）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    expected_version = _parse_if_match(if_match)
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="没有可修改的字段")
    if "category" in changes:
        _validate_category(str(changes["category"]))

    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = SummaryRuleRepository(session)
        rule = await repo.by_id(user_id, rule_id)
        if rule is None or rule.is_system or rule.user_id != user_id:
            raise NotFoundError(detail=f"规则 {rule_id} 不存在")
        if rule.version != expected_version:
            raise ApiError(
                ErrorCode.VERSION_CONFLICT,
                detail=f"规则 {rule_id} 版本冲突：当前 {rule.version}，提交 {expected_version}",
            )
        merged_match_type = str(changes.get("match_type", rule.match_type))
        merged_pattern = str(changes.get("pattern", rule.pattern))
        _validate_matcher(merged_match_type, merged_pattern)
        updated = await repo.update_fields(user_id, rule_id, changes=changes)
        out = _rule_out(updated)
    response.headers["ETag"] = _etag(updated.version)
    return out


@router.delete("/summary-rules/{rule_id}", status_code=204, operation_id="deleteSummaryRule")
async def delete_summary_rule(
    rule_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> Response:
    """API-025：删除用户规则（204）；系统/他人规则 404（删除后自然回退系统规则）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = SummaryRuleRepository(session)
        rule = await repo.by_id(user_id, rule_id)
        if rule is None or rule.is_system or rule.user_id != user_id:
            raise NotFoundError(detail=f"规则 {rule_id} 不存在")
        await repo.delete(user_id, rule_id)
    return Response(status_code=204)


__all__ = ["ALLOWED_CATEGORIES", "RULE_CODE_PREFIX", "router"]
