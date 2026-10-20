"""RFC 7807 错误响应（NFR-12）：永远带 code + user_message + what_to_do + retryable + request_id。"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from stock_contracts.errors import ApiError, ErrorCode, FieldError, problem_for
from stock_platform.obs import get_logger, get_request_id

log = get_logger("stock_api.errors")

PROBLEM_CONTENT_TYPE = "application/problem+json"

# FastAPI/Starlette 自带错误 → 我们的稳定错误码
_STATUS_MAP: dict[int, ErrorCode] = {
    401: ErrorCode.INVALID_CREDENTIALS,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_ERROR,
    413: ErrorCode.IMPORT_FILE_TOO_LARGE,
    415: ErrorCode.IMPORT_UNSUPPORTED_FILE,
    429: ErrorCode.RATE_LIMITED,
}


def problem_body(
    request: Request,
    code: ErrorCode,
    *,
    detail: str | None = None,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    problem = problem_for(code)
    body: dict[str, Any] = {
        "type": f"https://stock-analyze.local/errors/{code.value}",
        "title": problem.title,
        "status": problem.http_status,
        "detail": detail or problem.user_message,
        "code": code.value,
        "user_message": problem.user_message,
        "what_to_do": problem.what_to_do,
        "retryable": problem.retryable,
        "request_id": get_request_id(),
    }
    if errors:
        body["errors"] = [{"field": e.field, "code": e.code, "message": e.message} for e in errors]
    if headers:
        body["_headers"] = headers
    return body


def _response(
    request: Request,
    code: ErrorCode,
    *,
    detail: str | None = None,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = problem_body(request, code, detail=detail, errors=errors)
    body.pop("_headers", None)
    status = body["status"]
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers={"X-Request-ID": get_request_id(), **(headers or {})},
    )


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    if exc.detail is None and exc.metadata.get("detail"):  # pragma: no cover - 兼容
        exc.detail = str(exc.metadata["detail"])
    extra_headers = dict(exc.headers)
    if exc.code is ErrorCode.RATE_LIMITED or exc.code is ErrorCode.LOGIN_RATE_LIMITED:
        retry_after = exc.metadata.get("retry_after")
        if retry_after:
            extra_headers["Retry-After"] = str(retry_after)
    log.info(
        "api.error",
        code=exc.code.value,
        status=exc.http_status,
        path=request.url.path,
        detail=exc.detail,
    )
    return _response(request, exc.code, detail=exc.detail, errors=exc.errors, headers=extra_headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        FieldError(
            field=".".join(str(p) for p in err.get("loc", ())[1:]) or "body",
            code=str(err.get("type", "invalid")),
            message=str(err.get("msg", "无效值")),
        )
        for err in exc.errors()
    ]
    return _response(request, ErrorCode.VALIDATION_ERROR, errors=errors, detail="请求参数有问题。")


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_MAP.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    detail = exc.detail if isinstance(exc.detail, str) else None
    if exc.status_code == 404 and detail == "Not Found":
        detail = "找不到该资源。"
    return _response(
        request,
        code,
        detail=detail,
        headers=dict(getattr(exc, "headers", None) or {}),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("api.unhandled", path=request.url.path, error=type(exc).__name__)
    return _response(request, ErrorCode.INTERNAL_ERROR, detail="服务器出错了，已记录。")
