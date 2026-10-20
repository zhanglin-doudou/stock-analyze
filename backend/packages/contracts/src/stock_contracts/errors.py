"""稳定错误码总表（API-Catalog §3）+ 领域异常。

`user_message` / `what_to_do` 为必填人话字段（NFR-12）。
`code` 为大写蛇形、稳定、可枚举；发布后不得改名或复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from stock_contracts.enums import ImportBatchStatus


class ErrorCode(StrEnum):
    # ── 认证 / 授权 ───────────────────────────────────────
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_REVOKED = "TOKEN_REVOKED"
    TOKEN_REUSED = "TOKEN_REUSED"
    MULTIPLE_CREDENTIALS = "MULTIPLE_CREDENTIALS"
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    ADMIN_CANNOT_READ_TRADES = "ADMIN_CANNOT_READ_TRADES"
    FORBIDDEN = "FORBIDDEN"
    FOREIGN_RESOURCE_IN_BATCH = "FOREIGN_RESOURCE_IN_BATCH"
    # ── 资源 ──────────────────────────────────────────────
    NOT_FOUND = "NOT_FOUND"
    METRIC_NOT_FOUND = "METRIC_NOT_FOUND"
    EMAIL_TAKEN = "EMAIL_TAKEN"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    # ── 幂等 / 任务 ───────────────────────────────────────
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    JOB_ALREADY_RUNNING = "JOB_ALREADY_RUNNING"
    JOB_NOT_CANCELLABLE = "JOB_NOT_CANCELLABLE"
    JOB_LEASE_EXPIRED = "JOB_LEASE_EXPIRED"
    # ── 导入 ──────────────────────────────────────────────
    IMPORT_ALREADY_REVOKED = "IMPORT_ALREADY_REVOKED"
    IMPORT_COLUMNS_UNRESOLVED = "IMPORT_COLUMNS_UNRESOLVED"
    IMPORT_UNSUPPORTED_FILE = "IMPORT_UNSUPPORTED_FILE"
    IMPORT_FILE_TOO_LARGE = "IMPORT_FILE_TOO_LARGE"
    IMPORT_PREVIEW_EXPIRED = "IMPORT_PREVIEW_EXPIRED"
    IMPORT_PREVIEW_NOT_FOUND = "IMPORT_PREVIEW_NOT_FOUND"
    # ── 报告 / 导出 / 分享 ────────────────────────────────
    REPORT_NOT_READY = "REPORT_NOT_READY"
    EXPORT_EMPTY = "EXPORT_EMPTY"
    EXPORT_EXPIRED = "EXPORT_EXPIRED"
    SHARE_EXPIRED = "SHARE_EXPIRED"
    # ── 参数 / 校验 ───────────────────────────────────────
    WEAK_PASSWORD = "WEAK_PASSWORD"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_PERIOD = "INVALID_PERIOD"
    UNKNOWN_BENCHMARK = "UNKNOWN_BENCHMARK"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    INVALID_GRANULARITY = "INVALID_GRANULARITY"
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    INVALID_SORT_FIELD = "INVALID_SORT_FIELD"
    INVALID_CURSOR = "INVALID_CURSOR"
    INVALID_FIELD_SELECTION = "INVALID_FIELD_SELECTION"
    UNKNOWN_QUERY_PARAM = "UNKNOWN_QUERY_PARAM"
    # ── 限流 / 配额 ───────────────────────────────────────
    RATE_LIMITED = "RATE_LIMITED"
    LOGIN_RATE_LIMITED = "LOGIN_RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    # ── 系统 ──────────────────────────────────────────────
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    # ── 200 降级 / 警告码 ─────────────────────────────────
    SAMPLE_INSUFFICIENT = "SAMPLE_INSUFFICIENT"
    NAV_UNRELIABLE = "NAV_UNRELIABLE"
    IMPORT_MISSING_CASH_BALANCE = "IMPORT_MISSING_CASH_BALANCE"


@dataclass(frozen=True, slots=True)
class Problem:
    """错误码的机器可读定义（GET /api/v1/errors 直接返回本结构）。"""

    code: ErrorCode
    http_status: int
    user_message: str
    what_to_do: str
    retryable: bool
    title: str = ""


def _p(
    code: ErrorCode,
    status: int,
    user_message: str,
    what_to_do: str,
    retryable: bool,
    title: str = "",
) -> tuple[ErrorCode, Problem]:
    return code, Problem(code, status, user_message, what_to_do, retryable, title or code.value)


CATALOG: dict[ErrorCode, Problem] = dict(
    [
        _p(
            ErrorCode.INVALID_CREDENTIALS,
            401,
            "邮箱或密码不对。",
            "检查后重试；连续失败会触发限速。",
            False,
        ),
        _p(
            ErrorCode.TOKEN_EXPIRED,
            401,
            "访问凭证已过期。",
            "用 refresh_token 换新 access，或重建 PAT。",
            False,
        ),
        _p(ErrorCode.TOKEN_REVOKED, 401, "这个令牌已被撤销。", "创建新令牌并更新脚本。", False),
        _p(
            ErrorCode.TOKEN_REUSED,
            401,
            "登录状态已失效，请重新登录。",
            "重新登录；该令牌族已被安全撤销。",
            False,
        ),
        _p(
            ErrorCode.MULTIPLE_CREDENTIALS,
            401,
            "一个请求只能用一种凭证。",
            "只保留一种令牌。",
            False,
        ),
        _p(
            ErrorCode.INSUFFICIENT_SCOPE,
            403,
            "当前令牌只有只读权限。",
            "创建带 write scope 的 PAT。",
            False,
        ),
        _p(
            ErrorCode.ADMIN_CANNOT_READ_TRADES,
            403,
            "管理员账号不能查看交易明细。",
            "用数据所有者账号访问。",
            False,
        ),
        _p(ErrorCode.FORBIDDEN, 403, "没有权限执行该操作。", "使用具备权限的账号。", False),
        _p(
            ErrorCode.FOREIGN_RESOURCE_IN_BATCH,
            403,
            "请求中包含不属于你的数据。",
            "移除无关 ID 后重试。",
            False,
        ),
        _p(ErrorCode.NOT_FOUND, 404, "找不到该资源。", "确认 ID；跨账号资源按不存在处理。", False),
        _p(ErrorCode.METRIC_NOT_FOUND, 404, "没有这个指标。", "从指标目录选择有效指标。", False),
        _p(ErrorCode.EMAIL_TAKEN, 409, "该邮箱已注册。", "直接登录或找回密码。", False),
        _p(
            ErrorCode.VERSION_CONFLICT,
            409,
            "这条数据刚被改过。",
            "重新获取最新版本后再提交。",
            False,
        ),
        _p(
            ErrorCode.IDEMPOTENCY_IN_PROGRESS,
            409,
            "这个请求正在处理中。",
            "用返回的 job_id 轮询结果。",
            True,
        ),
        _p(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            422,
            "幂等键被用于了不同请求。",
            "换一个新的 Idempotency-Key。",
            False,
        ),
        _p(
            ErrorCode.JOB_ALREADY_RUNNING,
            409,
            "已有一个相同任务在跑。",
            "复用返回的 job_id 或等待结束。",
            True,
        ),
        _p(
            ErrorCode.JOB_NOT_CANCELLABLE,
            409,
            "任务已结束或不可取消。",
            "等待终态或查看结果。",
            False,
        ),
        _p(
            ErrorCode.JOB_LEASE_EXPIRED,
            500,
            "任务执行中断了。",
            "系统会自动重试；如持续失败请联系支持。",
            True,
        ),
        _p(
            ErrorCode.IMPORT_ALREADY_REVOKED, 409, "这次导入已经撤销过了。", "无需重复操作。", False
        ),
        _p(
            ErrorCode.IMPORT_COLUMNS_UNRESOLVED,
            422,
            "账单缺少必需列。",
            "手动指定列映射后重试。",
            False,
        ),
        _p(
            ErrorCode.IMPORT_UNSUPPORTED_FILE,
            415,
            "不支持这个文件格式。",
            "上传 .xls/.xlsx/.csv。",
            False,
        ),
        _p(ErrorCode.IMPORT_FILE_TOO_LARGE, 413, "文件太大了。", "拆分账单后分批导入。", False),
        _p(ErrorCode.IMPORT_PREVIEW_EXPIRED, 410, "预览已过期。", "重新上传并预览。", True),
        _p(ErrorCode.IMPORT_PREVIEW_NOT_FOUND, 404, "找不到这次预览。", "重新上传并预览。", False),
        _p(ErrorCode.REPORT_NOT_READY, 409, "报告还在生成中。", "稍后重试或订阅任务事件。", True),
        _p(ErrorCode.EXPORT_EMPTY, 422, "当前没有可导出的数据。", "先导入交易记录。", False),
        _p(ErrorCode.EXPORT_EXPIRED, 410, "下载链接已过期。", "重新创建导出任务。", True),
        _p(ErrorCode.SHARE_EXPIRED, 410, "分享链接已失效。", "向分享者索取新链接。", False),
        _p(
            ErrorCode.WEAK_PASSWORD,
            422,
            "密码太简单。",
            "至少 12 位并包含大小写与数字。",
            False,
        ),
        _p(
            ErrorCode.VALIDATION_ERROR,
            422,
            "请求参数有问题。",
            "按 errors[] 修正字段后重试。",
            False,
        ),
        _p(
            ErrorCode.INVALID_PERIOD,
            422,
            "时间区间不合法。",
            "用 YYYY-MM 或 from/to（半开区间）。",
            False,
        ),
        _p(
            ErrorCode.UNKNOWN_BENCHMARK,
            422,
            "不支持这个基准。",
            "使用 hs300_tr 或目录内基准。",
            False,
        ),
        _p(ErrorCode.MODEL_NOT_AVAILABLE, 422, "该归因模型暂未开放。", "使用 five_factor。", False),
        _p(
            ErrorCode.INVALID_GRANULARITY, 422, "趋势粒度不支持。", "使用 month 或 quarter。", False
        ),
        _p(ErrorCode.MESSAGE_TOO_LONG, 422, "消息太长了。", "精简后再发送。", False),
        _p(
            ErrorCode.INVALID_SORT_FIELD, 400, "不支持这个排序字段。", "使用文档列出的字段。", False
        ),
        _p(ErrorCode.INVALID_CURSOR, 400, "分页游标无效。", "从第一页重新翻页。", False),
        _p(ErrorCode.INVALID_FIELD_SELECTION, 400, "字段裁剪参数无效。", "只用已登记字段。", False),
        _p(ErrorCode.UNKNOWN_QUERY_PARAM, 400, "请求包含未知参数。", "移除未知参数后重试。", False),
        _p(ErrorCode.RATE_LIMITED, 429, "请求太频繁。", "按 Retry-After 等待后重试。", True),
        _p(ErrorCode.LOGIN_RATE_LIMITED, 429, "尝试次数过多。", "5 分钟后再试或重置密码。", True),
        _p(ErrorCode.QUOTA_EXCEEDED, 429, "同时运行的任务已达上限。", "等运行中的任务结束。", True),
        _p(
            ErrorCode.INTERNAL_ERROR,
            500,
            "服务器出错了，已记录。",
            "带 request_id 联系支持。",
            True,
        ),
        _p(ErrorCode.DB_UNAVAILABLE, 503, "服务暂时不可用。", "等待后重试；持续则联系运维。", True),
        _p(ErrorCode.SCHEMA_VERSION_MISMATCH, 503, "服务正在升级。", "30s 后重试。", True),
        _p(
            ErrorCode.LLM_UNAVAILABLE,
            503,
            "智能问答暂时不可用。",
            "稍后重试；金额类问题可用模板回答。",
            True,
        ),
        _p(
            ErrorCode.SAMPLE_INSUFFICIENT,
            200,
            "样本太少，先给你原始值。",
            "继续记录交易，样本≥10 后给结论。",
            False,
        ),
        _p(
            ErrorCode.NAV_UNRELIABLE,
            200,
            "这段时间的数据不敢给你算收益。",
            "补全资金余额列或修正首行后重算。",
            True,
        ),
        _p(
            ErrorCode.IMPORT_MISSING_CASH_BALANCE,
            200,
            "账单缺少资金余额，暂时无法对账。",
            "上传含资金余额列的账单。",
            True,
        ),
    ]
)

# 降级/警告码：HTTP 200，只作为 body 的一部分出现
DEGRADED_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.SAMPLE_INSUFFICIENT,
        ErrorCode.NAV_UNRELIABLE,
        ErrorCode.IMPORT_MISSING_CASH_BALANCE,
    }
)

SEED_ERROR_COUNT = 47


def problem_catalog() -> list[Problem]:
    """机器可读错误码总表（GET /api/v1/errors）。"""
    return list(CATALOG.values())


def problem_for(code: ErrorCode) -> Problem:
    return CATALOG[code]


@dataclass(slots=True)
class FieldError:
    field: str
    code: str
    message: str


@dataclass(slots=True)
class ApiError(Exception):
    """领域/应用层统一抛出的错误；由 API 层转成 RFC 7807 problem+json。"""

    code: ErrorCode
    errors: list[FieldError] = field(default_factory=list)
    detail: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def problem(self) -> Problem:
        return CATALOG[self.code]

    @property
    def http_status(self) -> int:
        return self.problem.http_status

    def __str__(self) -> str:  # pragma: no cover - 仅供日志
        return f"{self.code.value}: {self.detail or self.problem.user_message}"


class NotFoundError(ApiError):
    def __init__(self, detail: str | None = None, code: ErrorCode = ErrorCode.NOT_FOUND) -> None:
        super().__init__(code=code, detail=detail)


class ForbiddenError(ApiError):
    def __init__(self, code: ErrorCode = ErrorCode.FORBIDDEN, detail: str | None = None) -> None:
        super().__init__(code=code, detail=detail)


class ValidationFailedError(ApiError):
    def __init__(
        self,
        errors: list[FieldError] | None = None,
        code: ErrorCode = ErrorCode.VALIDATION_ERROR,
        detail: str | None = None,
    ) -> None:
        super().__init__(code=code, errors=errors or [], detail=detail)


__all__ = [
    "CATALOG",
    "DEGRADED_CODES",
    "SEED_ERROR_COUNT",
    "ApiError",
    "ErrorCode",
    "FieldError",
    "ForbiddenError",
    "ImportBatchStatus",
    "NotFoundError",
    "Problem",
    "ValidationFailedError",
    "problem_catalog",
    "problem_for",
]
