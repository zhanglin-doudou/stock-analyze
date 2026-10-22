"""运维端点（API-071~076）：存活/就绪/版本/指标/错误码表。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from stock_contracts.errors import ErrorCode, problem_catalog
from stock_platform.obs import get_logger

from stock_api.deps import get_state
from stock_api.errors import PROBLEM_CONTENT_TYPE, problem_body
from stock_api.schemas import ErrorCatalogOut, HealthOut, ReadyOut, VersionOut
from stock_api.version import API_VERSION, APP_VERSION, SCHEMA_VERSION

log = get_logger("stock_api.ops")

# 前缀下（/api/v1/...）
router = APIRouter(tags=["ops"])
# 根路径（探针按容器惯例不带前缀）
root_router = APIRouter(tags=["ops"])

API_SNAPSHOT_VERSION = "3.0.0"
APP_VERSION_DATE = "2026-10-19"


@router.get("/version", response_model=VersionOut, operation_id="getVersion")
async def get_version() -> VersionOut:
    return VersionOut(
        app_version=APP_VERSION,
        api_version=API_VERSION,
        schema_version=SCHEMA_VERSION,
        api_snapshot_version=API_SNAPSHOT_VERSION,
        app_version_date=APP_VERSION_DATE,
    )


@root_router.get("/version", response_model=VersionOut, operation_id="getVersionRoot")
async def get_version_root() -> VersionOut:
    return await get_version()


@root_router.get("/healthz", response_model=HealthOut, operation_id="healthz")
async def healthz() -> HealthOut:
    """存活探针：不查 DB（DB 故障不应导致容器重启循环）。"""
    return HealthOut(service="api", version=APP_VERSION)


@root_router.get("/readyz", response_model=ReadyOut, operation_id="readyz")
async def readyz(request: Request) -> Response:
    """就绪探针：DB 可达 + schema 版本一致（API-072）。"""
    from sqlalchemy import text

    state = get_state(request)
    checks: dict[str, str] = {}
    ready = True
    timeout = state.settings.readyz_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            async with state.db.engine.connect() as conn:
                await conn.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.error("readyz.db_failed", error=str(exc))
        checks["database"] = "failed"
        ready = False
    try:
        async with state.db.session(begin=True) as session:
            applied = await session.scalar(
                text("select version from platform.schema_version where id = 1")
            )
        if applied == SCHEMA_VERSION:
            checks["schema"] = "ok"
        else:
            checks["schema"] = f"mismatch:{applied}"
            ready = False
    except Exception as exc:  # pragma: no cover - 迁移缺失
        log.error("readyz.schema_failed", error=str(exc))
        checks["schema"] = "unavailable"
        ready = False

    if ready:
        return Response(
            content=ReadyOut(status="ready", checks=checks).model_dump_json(),
            media_type="application/json",
        )
    body = problem_body(
        request,
        ErrorCode.DB_UNAVAILABLE
        if checks.get("database") != "ok"
        else ErrorCode.SCHEMA_VERSION_MISMATCH,
    )
    body["status"] = 503
    body["checks"] = checks
    return Response(content=json.dumps(body), status_code=503, media_type=PROBLEM_CONTENT_TYPE)


@root_router.get("/metrics", operation_id="metricsText")
async def metrics_text() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/errors", response_model=ErrorCatalogOut, operation_id="getErrors")
async def get_errors() -> ErrorCatalogOut:
    """机器可读错误码表（API-076）：前端与脚本据此生成本地化文案。"""
    items = [
        {
            "code": p.code.value,
            "http_status": p.http_status,
            "title": p.title,
            "user_message": p.user_message,
            "what_to_do": p.what_to_do,
            "retryable": p.retryable,
        }
        for p in sorted(problem_catalog(), key=lambda item: item.code.value)
    ]
    return ErrorCatalogOut(count=len(items), items=items)


@root_router.get("/", include_in_schema=False)
async def root_index() -> dict[str, str]:
    return {
        "service": "stock-analyze-api",
        "version": APP_VERSION,
        "openapi": "/api/v1/openapi.json",
        "time": datetime.now(tz=UTC).isoformat(),
    }
