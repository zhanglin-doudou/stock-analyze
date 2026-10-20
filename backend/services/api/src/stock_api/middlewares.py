"""中间件链（API-Catalog §0.2）：请求上下文 → 安全头 → 观测（纯 ASGI，保证 SSE 不被缓冲）。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from stock_platform.ids import new_request_id
from stock_platform.obs import (
    HTTP_INFLIGHT,
    HTTP_LATENCY,
    HTTP_REQUESTS,
    get_logger,
    set_request_id,
    set_user_id,
)

log = get_logger("stock_api.http")

ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[None]], Callable[..., Awaitable[None]]],
    Awaitable[None],
]

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class RequestContextMiddleware:
    """注入 request_id（透传来自客戶端/网关的 X-Request-ID）+ 访问日志 + 指标。"""

    def __init__(self, app: ASGIApp, *, trust_inbound_request_id: bool = True) -> None:
        self.app = app
        self.trust_inbound = trust_inbound_request_id

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        request_id = (
            headers.get("x-request-id") if self.trust_inbound else None
        ) or new_request_id()
        set_request_id(request_id)
        set_user_id(None)

        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode()))
                if scope.get("path", "").startswith("/api"):
                    for key, value in SECURITY_HEADERS.items():
                        raw.append((key.lower().encode(), value.encode()))
                message = {**message, "headers": raw}
            await send(message)

        method = scope.get("method", "-")
        HTTP_INFLIGHT.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            HTTP_INFLIGHT.dec()
            route = scope.get("path", "-")
            elapsed = time.perf_counter() - started
            HTTP_REQUESTS.labels(
                method=method, route=route, status=str(status_holder["status"])
            ).inc()
            HTTP_LATENCY.labels(method=method, route=route).observe(elapsed)
            log.info(
                "http.request",
                method=method,
                path=route,
                status=status_holder["status"],
                duration_ms=round(elapsed * 1000, 1),
            )
