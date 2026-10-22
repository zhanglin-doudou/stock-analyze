"""审计留痕（FR-A4）：所有写操作必须留痕，且不含敏感明文。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform.models import AuditLog

# 敏感字段名（永不落审计）
_REDACT_KEYS = {
    "password",
    "password_hash",
    "token",
    "refresh_token",
    "access_token",
    "plaintext",
    "authorization",
    "secret",
}


def redact(meta: dict[str, Any]) -> dict[str, Any]:
    """剔除敏感字段（NFR-1：日志/审计不出现明文凭证与明文交易）。"""
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if key.lower() in _REDACT_KEYS:
            out[key] = "[redacted]"
        elif isinstance(value, dict):
            out[key] = redact(value)
        else:
            out[key] = value
    return out


async def record(
    session: AsyncSession,
    *,
    action: str,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    meta: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_hash: str | None = None,
    source: str | None = None,
) -> None:
    """与业务写操作同事务：失败即回滚（保证状态与留痕一致）。"""
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=redact(meta or {}),
            request_id=request_id,
            ip_hash=ip_hash,
            source=source,
        )
    )
