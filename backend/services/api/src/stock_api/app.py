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
from stock_delivery.chat.provider import LlmProviderFactory, build_provider
from stock_ledger.market import create_market_source
from stock_platform.config import DEFAULT_EMAIL_CODE_PEPPER, Settings, get_settings
from stock_platform.db import Database
from stock_platform.email_verification import EmailVerificationService
from stock_platform.idempotency import IdempotencyStore
from stock_platform.jobs import JobQueue
from stock_platform.llm_registry import load_llm_registry
from stock_platform.mail import EmailSender, create_email_sender
from stock_platform.obs import configure_logging, get_logger
from stock_platform.ratelimit import RateLimiter
from stock_platform.sector_source import create_sector_source
from stock_platform.security import TokenService, email_code_hasher_for
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
    admin_import_rules,
    assets,
    attribution,
    auth,
    chat,
    dashboard,
    imports,
    jobs,
    metrics,
    nav,
    ops,
    reconciliation,
    rounds,
    rules,
    sectors,
    tokens,
    trades,
)
from stock_api.routers import (
    settings as settings_api,
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


def _validate_email_settings(settings: Settings) -> None:
    """邮箱能力启动校验；不联网，只检查配置组合。"""
    if settings.env == "prod" and settings.email_provider == "log":
        raise RuntimeError("生产环境禁止 EMAIL_PROVIDER=log（会把验证码打进日志）")
    if not settings.email_enabled:
        return
    if settings.email_provider == "none":
        raise RuntimeError("邮箱验证/找回已启用但 EMAIL_PROVIDER=none")
    if len(settings.email_code_pepper) < 32:
        raise RuntimeError("EMAIL_CODE_PEPPER 至少 32 字符")
    if settings.env == "prod" and settings.email_code_pepper == DEFAULT_EMAIL_CODE_PEPPER:
        raise RuntimeError("生产环境必须替换 EMAIL_CODE_PEPPER")
    if settings.email_provider == "aliyun" and not settings.email_dm_account_name:
        raise RuntimeError("EMAIL_PROVIDER=aliyun 时必须配置 EMAIL_DM_ACCOUNT_NAME")
    if settings.email_code_ttl_seconds <= 0 or settings.email_code_max_attempts <= 0:
        raise RuntimeError("EMAIL_CODE_TTL_SECONDS / MAX_ATTEMPTS 必须为正")
    if settings.email_code_resend_seconds <= 0:
        raise RuntimeError("EMAIL_CODE_RESEND_SECONDS 必须为正")
    if (
        min(
            settings.email_code_ip_hourly_limit,
            settings.email_code_ip_daily_limit,
            settings.email_code_email_hourly_limit,
            settings.email_code_email_daily_limit,
        )
        < 0
    ):
        raise RuntimeError("邮箱验证码配额不能为负")


def build_state(
    settings: Settings,
    clock: Clock | None = None,
    *,
    email_sender: EmailSender | None = None,
) -> AppState:
    _validate_email_settings(settings)
    db = Database(settings, role="app")
    resolved_clock = clock or SystemClock()
    sender = email_sender or create_email_sender(settings)
    code_hasher = email_code_hasher_for(settings.email_code_pepper, settings.argon2_time_cost)
    # 行情源统一走工厂：live 分支需要无租户 session_factory（公共行情缓存）。
    market_data = create_market_source(settings, session_factory=db.session, clock=resolved_clock)
    # 板块数据源同口径（replay 读夹具；live 读 market.* 缓存，缺 akshare 启动期 fail-fast）。
    sector_data = create_sector_source(settings, session_factory=db.session, clock=resolved_clock)
    # 模型供应商目录（config/llm_providers.json）：启动期 fail-fast 加载一次。
    llm_registry = load_llm_registry(settings)
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
        market_data=market_data,
        sector_data=sector_data,
        email_sender=sender,
        email_codes=EmailVerificationService(settings, code_hasher),
        object_store=LocalObjectStore(settings.data_dir / "objects"),
        llm=build_provider(settings, llm_registry),
        llm_registry=llm_registry,
        llm_factory=LlmProviderFactory(settings, llm_registry),
    )


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    email_sender: EmailSender | None = None,
) -> FastAPI:
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
    app.state.sa = build_state(resolved, clock, email_sender=email_sender)

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    for module in (
        ops,
        auth,
        tokens,
        jobs,
        trades,
        reconciliation,
        imports,
        admin_import_rules,
        rules,
        rounds,
        nav,
        assets,
        metrics,
        dashboard,
        sectors,
        settings_api,
        attribution,
        chat,
    ):
        app.include_router(module.router, prefix=API_PREFIX)
    # 探针/根路由按容器惯例不带前缀（/healthz、/readyz、/version、/metrics、/）
    app.include_router(ops.root_router)
    # P0-5：把 §7.4 契约元数据（x-*/2xx example/problem+json）缓存进 openapi_schema，
    # 使 `app.openapi()`、`/docs`、`cli.api-snapshot` 与 `GET /api/v1/openapi.json` 同源。
    app.openapi_schema = apply_contract_metadata(app.openapi())
    return app
