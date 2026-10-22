"""可观测性：结构化日志、request_id、Prometheus 指标（NFR-9/10）。

金额与明文交易一律不进日志（NFR-1）。
"""

from __future__ import annotations

import contextvars
import logging
import sys
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from prometheus_client import Counter, Gauge, Histogram

from stock_platform.ids import new_request_id

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("sa_request_id", default="-")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("sa_user_id", default="-")

# ── 指标 ───────────────────────────────────────────────────
HTTP_REQUESTS = Counter("sa_http_requests_total", "HTTP 请求总数", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "sa_http_request_duration_seconds",
    "HTTP 请求耗时",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
HTTP_INFLIGHT = Gauge("sa_http_inflight", "处理中的请求数")
JOBS_TOTAL = Counter("sa_jobs_total", "任务总数", ["kind", "status"])
JOB_DURATION = Histogram("sa_job_duration_seconds", "任务耗时", ["kind"])
SSE_CONNECTIONS = Gauge("sa_sse_connections", "SSE 连接数")
RECONCILE_BREAKS = Counter("sa_reconciliation_breaks_total", "对账断链条数")


def new_request_id_ctx() -> str:
    return new_request_id()


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id or "-")


def get_user_id() -> str:
    return _user_id.get()


def _add_context(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    event_dict.setdefault("request_id", get_request_id())
    event_dict.setdefault("user_id", get_user_id())
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool | None = None) -> None:
    """本地人类可读 + prod JSON（NFR-9）。"""
    numeric = getattr(logging, level.upper(), logging.INFO)
    use_json = json_output if json_output is not None else False
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric, force=True)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_context,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


class JobTimeoutError(Exception):
    """任务超时（单飞守卫）。"""


async def timed_await[T](awaitable: Awaitable[T], *, timeout: float) -> T:
    import asyncio

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except TimeoutError as exc:
        raise JobTimeoutError(f"操作超时 {timeout}s") from exc


class Timer:
    """上下文计时器：with Timer() as t: ... t.seconds。"""

    __slots__ = ("seconds", "started")

    def __init__(self) -> None:
        self.started = 0.0
        self.seconds = 0.0

    def __enter__(self) -> Timer:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.seconds = time.perf_counter() - self.started


def observe_job(
    kind: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """任务指标装饰器。"""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception:
                JOBS_TOTAL.labels(kind=kind, status="failed").inc()
                raise
            else:
                JOBS_TOTAL.labels(kind=kind, status="succeeded").inc()
                return result
            finally:
                JOB_DURATION.labels(kind=kind).observe(time.perf_counter() - started)

        return wrapper

    return decorator
