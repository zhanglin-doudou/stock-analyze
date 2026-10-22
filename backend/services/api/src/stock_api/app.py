"""FastAPI 应用装配（services/api/app.py）。"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from stock_contracts.clock import Clock, SystemClock
from stock_contracts.errors import ApiError
from stock_platform.config import Settings, get_settings
from stock_platform.db import Database
from stock_platform.idempotency import IdempotencyStore
from stock_platform.jobs import JobQueue
from stock_platform.obs import configure_logging, get_logger
from stock_platform.ratelimit import RateLimiter
from stock_platform.security import TokenService
from stock_platform.storage import LocalObjectStore

from stock_api.deps import AppState
from stock_api.errors import (
    api_error_handler,
    http_exception_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from stock_api.middlewares import RequestContextMiddleware
from stock_api.openapi_meta import apply_contract_metadata
from stock_api.routers import (
    auth,
    imports,
    jobs,
    ops,
    reconciliation,
    rounds,
    rules,
    tokens,
    trades,
)
from stock_api.version import API_PREFIX, APP_VERSION

log = get_logger("stock_api")


async def _check_schema_version(state: AppState) -> None:
    """C4：启动即校验 schema 版本，不匹配拒绝启动（防止代码/库结构漂移）。"""
    from sqlalchemy import text

    from stock_api.version import SCHEMA_VERSION

    try:
        async with state.db.session(begin=True) as session:
            applied = await session.scalar(
                text("select version from platform.schema_version where id = 1")
            )
    except Exception as exc:  # pragma: no cover - 库未迁移
        log.error("app.schema_check_failed", error=str(exc))
        raise RuntimeError("无法读取 platform.schema_version，请先执行 make migrate") from exc
    if applied != SCHEMA_VERSION:
        raise RuntimeError(
            f"SCHEMA_VERSION 不匹配：代码 {SCHEMA_VERSION} / 库 {applied}，请执行 make migrate"
        )


def build_state(settings: Settings, clock: Clock | None = None) -> AppState:
    db = Database(settings, role="app")
    resolved_clock = clock or SystemClock()
    return AppState(
        settings=settings,
        db=db,
        clock=resolved_clock,
        queue=JobQueue(db, resolved_clock),
        limiter=RateLimiter(resolved_clock),
        idempotency=IdempotencyStore(resolved_clock),
        tokens=TokenService(
            settings.jwt_secret, settings.jwt_algorithm, settings.access_ttl_seconds
        ),
        object_store=LocalObjectStore(settings.data_dir / "objects"),
    )


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, json_output=resolved.env == "prod")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _check_schema_version(app.state.sa)
        log.info("app.start", env=resolved.env, version=APP_VERSION)
        yield
        await app.state.sa.db.dispose()
        log.info("app.stop")

    app = FastAPI(
        title="个人交易复盘平台 API",
        version=APP_VERSION,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs" if resolved.env != "prod" else None,
        redoc_url=None,
        lifespan=lifespan,
        default_response_class=JSONResponse,
    )
    app.state.sa = build_state(resolved, clock)

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    for module in (ops, auth, tokens, jobs, trades, reconciliation, imports, rules, rounds):
        app.include_router(module.router, prefix=API_PREFIX)
    # 探针/根路由按容器惯例不带前缀（/healthz、/readyz、/version、/metrics、/）
    app.include_router(ops.root_router)
    # P0-5：把 §7.4 契约元数据（x-*/2xx example/problem+json）缓存进 openapi_schema，
    # 使 `app.openapi()`、`/docs`、`cli.api-snapshot` 与 `GET /api/v1/openapi.json` 同源。
    app.openapi_schema = apply_contract_metadata(app.openapi())
    return app
