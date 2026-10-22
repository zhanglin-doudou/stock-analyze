"""跨上下文共享契约（DTO / 枚举 / 错误码 / 常量）。

分层铁律 L5：跨上下文只经 contracts DTO 或事件。
"""

from stock_contracts.constants import (
    ANNUALIZATION_BASE,
    EPS_AMT,
    EPS_CUM,
    EPS_R,
    MIN_CI_SAMPLE,
    MIN_CONCLUSION_SAMPLE,
    SCALE_AMOUNT,
    SCALE_PRICE,
    SCALE_QTY,
    SCALE_RATIO,
)
from stock_contracts.enums import (
    JobStatus,
    JobType,
    MetricStatus,
    Role,
    SampleStatus,
    ScopeType,
    Side,
    TokenScope,
)
from stock_contracts.errors import ApiError, ErrorCode, Problem, problem_catalog

__all__ = [
    "ANNUALIZATION_BASE",
    "EPS_AMT",
    "EPS_CUM",
    "EPS_R",
    "MIN_CI_SAMPLE",
    "MIN_CONCLUSION_SAMPLE",
    "SCALE_AMOUNT",
    "SCALE_PRICE",
    "SCALE_QTY",
    "SCALE_RATIO",
    "ApiError",
    "ErrorCode",
    "JobStatus",
    "JobType",
    "MetricStatus",
    "Problem",
    "Role",
    "SampleStatus",
    "ScopeType",
    "Side",
    "TokenScope",
    "problem_catalog",
]
