"""OpenAPI 契约元数据 enrichment 与校验器（API-Catalog §7.4 强制项）。

本模块把「契约文档要求、FastAPI 自动生成时缺失」的部分补齐：

* 每个 operation 的 ``x-required-scope`` / ``x-idempotent`` / ``x-async`` /
  ``x-error-codes``（静态元数据，由一次性脚本从 ``API-Catalog.md`` §1/§2 生成，
  运行时不读取 Markdown，见 ``OPERATION_META``）；
* 每个 2xx 响应的确定性 ``example``（按 response schema 递归生成；金额/价格/数量
  为定点字符串、比率为小数、枚举取首值、array 给 1 项、nullable 给示例值，
  SSE / Prometheus 文本响应给文本示例）；
* 每个可能返回的错误 status 的 ``application/problem+json`` 响应（引用
  ``components.schemas.Error``），以及 ``Error`` 组件（RFC 7807 + 平台扩展字段）。

``validate_contract`` 为无副作用的契约校验器，返回违规列表（空列表即通过），
供 ``stock_api.cli api-check`` 以非 0 退出码阻断漂移。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from stock_contracts.errors import CATALOG as ERROR_CATALOG
from stock_contracts.errors import ErrorCode

# ── 常量 ──────────────────────────────────────────────────────

ERROR_SCHEMA = "Error"
PROBLEM_CONTENT_TYPE = "application/problem+json"
JSON_CONTENT_TYPE = "application/json"

REQUIRED_SCOPES = frozenset({"public", "read", "write", "admin"})

_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})
_MAX_DEPTH = 12

_REQUEST_ID = "01J9Z6ABCDEFGHJKMNPQRSTVWX"
_UUID = "018f6b1c-7d2e-7a4b-9c3f-0a1b2c3d4e5f"
_DATE = "2026-09-30"
_DATETIME = "2026-09-30T09:30:00Z"
_PERIOD = "2026-09"

# 金额 2 位 / 价格 6 位 / 数量 4 位 / 比率十进制小数（API-Catalog §0.8）。
MONEY_FIELDS = frozenset(
    {
        "amount",
        "commission",
        "stamp_tax",
        "other_fee",
        "cash_balance",
        "cashflow",
        "cashflows",
        "expected",
        "actual",
        "diff",
        "total_pnl",
        "initial_cash",
        "realized_pnl",
        "dividends",
        "fees",
        "ending_value",
        "residual",
        "total_amount",
        # Period2-02（API-027/031）：桥接恒等式与 FIFO 配对的金额项
        "initial_open_cost",
        "open_cost",
        "interest",
        "other",
        "cost",
        "proceeds",
        "dividend",
        "pnl",
        "buy_cost",
        "sell_proceeds",
        "tolerance",
        # Period2-16（API-081）：**只加资产表独有**金额字段。market_value/total_assets/
        # prev_total_assets/daily_pnl 与 nav 3 位示例共用同名，若登记会被强制 2 位而契约测试红。
        "cash",
        "holding_cost",
        "unrealized_pnl",
        # Period2-12（API-084）：Dashboard 独有金额字段（与 nav/资产表无同名冲突）。
        "total_profit",
        "max_invested_cost",
        "total_fees",
        "max_profit",
        # 2026-10-28（API-085）：图表点附带的展示金额串
        "amount_label",
        # 2026-11-02（API-036）：归因独有金额字段（amount/residual/total_pnl 已有）
        "base_capital",
        "valuation_gap",
        "corporate_action_pnl",
        "unattributed_price_pnl",
        "dividend_without_position",
        "other_cashflow",
        # 2026-11-04（API-036）：残差对账派生金额（与 residual 同 2 位）
        "unattributed_symbols_total",
        "account_level_total",
        "listed_total",
        "others_total",
    }
)
PRICE_FIELDS = frozenset({"price", "unit_nav"})
QUANTITY_FIELDS = frozenset({"quantity", "qty", "oversell_quantity"})
RATIO_FIELDS = frozenset(
    {
        "ratio",
        "confidence",
        "beta",
        "alpha",
        "alpha_annual",
        "annualized_twr",
        "ir",
        "mwr",
        "mwr_minus_twr",
        "nav_return",
        "tracking_error",
        "t_value",
        "twr",
        "twr_daily",
        "win_rate",
        "de",
        # Period2-02：回合收益率（Ratio 标度整数 → 定点字符串）
        "return_rate",
        # Period2-16（API-081）：资产表日收益率（scale=8 定点字符串）
        "daily_return",
        # 2026-10-31（API-087）：板块权重/暴露/集中度（scale=8 定点字符串）
        "weight",
        "benchmark_weight",
        "active_weight",
        "exposure",
        "coverage",
        "equity_position",
        "deviation",
        "hhi",
        "effective_n",
        "top1_weight",
        "top3_weight",
        "benchmark_hhi",
        "hhi_tag",
        "ratio_of_equity",
        # 2026-11-02（API-036）：残差占比与因子构成占比（scale=8 定点字符串）
        "residual_ratio",
        "share",
    }
)
# 2026-11-07（API-087）：偏好项 active_weight 与概念块 coverage/overlap/exposure 已改用
# `Ratio.as_decimal_str()`（3 位 HALF_UP + 去尾零），示例串同步收敛到 3 位。
RATIO_TRIM3_FIELDS = frozenset({"active_weight", "coverage", "overlap", "exposure"})
_MONEY_VALUE = "1234.56"
_PRICE_VALUE = "12.340000"
_QUANTITY_VALUE = "100"
_RATIO_VALUE = "0.1234"
_RATIO_TRIM3_VALUE = "0.123"
_RATIO_NUMBER = 0.1234

_DATE_FIELDS = frozenset(
    {
        "as_of",
        "date",
        "expires_at",
        "finished_at",
        "from",
        "from_date",
        "generated_at",
        "to",
        "to_date",
        "trade_date",
        "trade_date_from",
        "trade_date_to",
    }
)
_DATETIME_FIELDS = frozenset(
    {
        "available_at",
        "closed_at",
        "created_at",
        "data_as_of",
        "deleted_at",
        "expected_review_at",
        "heartbeat_at",
        "last_used_at",
        "rebuilt_at",
        "revoked_at",
        "started_at",
        "updated_at",
    }
)

_ID_PREFIXES = {
    "batch_id": "imp_",
    "event_id": "evt_",
    "export_id": "exp_",
    "job_id": "job_",
    "buy_trade_id": "trd_",
    "close_trade_id": "trd_",
    "message_id": "msg_",
    "open_trade_id": "trd_",
    "preview_id": "prv_",
    "sell_trade_id": "trd_",
    "prompt_id": "prm_",
    "recompute_job_id": "job_",
    "report_id": "rep_",
    "rule_id": "rul_",
    "session_id": "ses_",
    "share_id": "shr_",
    "token_id": "tok_",
    "trade_id": "trd_",
}
_ID_SUFFIX = "01J9Z6ABCDEFGHJKMNPQRSTVWX"

_STRING_EXAMPLES = {
    "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl",
    "account_masked": "6222****1234",
    "api_snapshot_version": "3.0.0",
    "api_version": "1.0.0",
    "app_version": "0.1.0",
    "app_version_date": "2026-10-19",
    "broker": "huatai",
    "category": "trade",
    "code": "MISSING_CASH_BALANCE",
    "date_format": "%Y-%m-%d",
    "dedupe_key": "ddk_01J9Z6ABCDEFGHJKMNPQRSTVWX",
    "download": "/api/v1/exports/exp_01J9Z6ABCDEFGHJKMNPQRSTVWX/download",
    "events": "/api/v1/jobs/job_01J9Z6ABCDEFGHJKMNPQRSTVWX/events",
    "file_name": "huatai_2026-09.xlsx",
    "message": "处理中",
    "metric_id": "win_rate",
    "name": "示例名称",
    "next_cursor": "cursor_01J9Z6ABCDEFGHJKMNPQRSTVWX",
    "note": "示例备注",
    "period": _PERIOD,
    "prefix": "pat_01J9",
    "reason": "示例原因",
    "reason_code": "UNKNOWN_SUMMARY",
    "refresh_token": "rt_01J9Z6ABCDEFGHJKMNPQRSTVWX",
    "result_ref": "/api/v1/imports/imp_01J9Z6ABCDEFGHJKMNPQRSTVWX",
    "schema_version": "0042",
    "security_code": "600519",
    "security_name": "贵州茅台",
    "self": "/api/v1/jobs/job_01J9Z6ABCDEFGHJKMNPQRSTVWX",
    "sha256": "a" * 64,
    "stage": "running",
    "suggested_field": "trade_date",
    "summary": "证券买入",
    "summary_code": "buy",
    "inputs_hash": "sha256:" + "a" * 64,
    "status": "ok",
    "token": "pat_" + "A" * 43,
    "type": "import_commit",
    "url": "https://stock-analyze.local/s/01J9Z6ABCDEFGHJKMNPQRSTVWX",
    # Period2-16（API-081）：后端算好的两位百分比串（前端零口径）。
    "daily_return_pct": "0.62%",
    # 2026-10-31（API-087/088）：板块展示串与目录字段。
    "coverage_pct": "96.00%",
    "weight_pct": "83.33%",
    "benchmark_weight_pct": "30.00%",
    "active_weight_pct": "53.33%",
    "exposure_pct": "80.00%",
    "multi_tag_pct": "36.00%",
    "equity_position_pct": "90.91%",
    "sector_code": "801030.SI",
    "sector_name": "基础化工",
    "taxonomy": "industry",
    "weight_mode": "additive",
    "coverage_basis": "mapped_equity_market_value / equity_market_value",
    "sector_dataset_version": "sw2021@2026-09-30",
    "catalog_version": "a1b2c3d4e5f60718",
    "classification_version": "SW2021",
    "definition_version": "1.0",
    "benchmark_id": "hs300",
    "method": "industry_single_membership",
    "direction": "over",
    "source": "sw",
    "sector_data_state": "current",
    "asset_build_state": "current",
    "caveat": "概念为多归属标签：暴露可重叠、不可加总。",
}
_INT_EXAMPLES = {
    "attempt": 1,
    "break_count": 0,
    "bytes": 1024,
    "checked_rows": 120,
    "count": 1,
    "fill_id": 1,
    "fills_count": 2,
    "holding_days": 18,
    # Period2-16（API-081/082）：资产表行数 / 构建摘要计数
    "holding_count": 1,
    "rows_count": 125,
    "last_rows_written": 45,
    "last_trade_count": 12,
    "sequence": 1,
    "total": 2,
    "expires_in": 900,
    "index": 0,
    "inserted": 118,
    "line_no": 7,
    "max_attempts": 3,
    "n": 12,
    "parsed": 120,
    "progress": 100,
    "quarantined": 1,
    "rank": 1,
    "skipped": 2,
    "version": 2,
    "_default": 1,
}
_BOOL_EXAMPLES = {
    "balanced": True,
    "can_commit": True,
    "can_revoke": True,
    "cash_balance_available": True,
    "data_error": False,
    "email_verified": True,
    "has_more": True,
}
_DICT_EXAMPLES: dict[str, dict[str, Any]] = {
    "assumptions": {"benchmark_id": "hs300_tr"},
    "backlog": {"pending": 0, "running": 0, "succeeded": 3},
    "build": {
        "state": "current",
        "last_status": "ok",
        "last_built_at": _DATETIME,
        "last_built_through": _DATE,
        "last_mode": "incremental",
        "last_rebuild_from": "2026-08-17",
        "last_rows_written": 45,
        "rows_count": 125,
        "assumption": "back_solved",
        "status_reason": None,
        "has_new_market_data": False,
        "definition_version": "1.0",
        "session_notice": "market_open",
    },
    "checks": {"db": "ok", "schema_version": "0042"},
    "reverted": {"inserted": 118, "updated": 0},
    "settings": {"benchmark_id": "hs300_tr", "version": 1},
}
_LIST_EXAMPLES: dict[str, list[str]] = {
    "changed_fields": ["summary_code"],
    "scopes": ["read"],
}

_STATUS_DESCRIPTIONS = {
    400: "请求参数错误",
    401: "未认证或凭证无效",
    403: "权限不足",
    404: "资源不存在",
    409: "资源状态冲突",
    410: "资源已过期",
    413: "上传文件过大",
    415: "不支持的文件类型",
    422: "语义校验失败",
    429: "请求过于频繁",
    500: "服务内部错误",
    503: "依赖服务不可用",
}
_STATUS_FALLBACK_CODES = {
    400: "VALIDATION_ERROR",
    401: "TOKEN_EXPIRED",
    403: "INSUFFICIENT_SCOPE",
    404: "NOT_FOUND",
    409: "VERSION_CONFLICT",
    410: "IMPORT_PREVIEW_EXPIRED",
    413: "IMPORT_FILE_TOO_LARGE",
    415: "IMPORT_UNSUPPORTED_FILE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "DB_UNAVAILABLE",
}
_FIELD_ERROR_CODES = frozenset({"IDEMPOTENCY_KEY_REUSED", "VALIDATION_ERROR", "WEAK_PASSWORD"})

# ── 静态元数据（一次性脚本从 API-Catalog §1/§2 生成；勿手改顺序）──

OPERATION_META: dict[str, dict[str, Any]] = {
    "buildAssets": {
        "api_id": "API-082",
        "scope": "write",
        "idempotent": True,
        "async": True,
        "error_codes": [
            "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_IN_PROGRESS",
            "JOB_ALREADY_RUNNING",
            "QUOTA_EXCEEDED",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "cancelJob": {
        "api_id": "API-064",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "NOT_FOUND",
            "JOB_NOT_CANCELLABLE",
            "VALIDATION_ERROR",
        ],
    },
    "commitImport": {
        "api_id": "API-011",
        "scope": "write",
        "idempotent": True,
        "async": True,
        "error_codes": [
            "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_IN_PROGRESS",
            "IMPORT_PREVIEW_EXPIRED",
            "IMPORT_COLUMNS_UNRESOLVED",
            "JOB_ALREADY_RUNNING",
            "QUOTA_EXCEEDED",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "createToken": {
        "api_id": "API-007",
        "scope": "write",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "VALIDATION_ERROR",
            "INSUFFICIENT_SCOPE",
            "RATE_LIMITED",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
        ],
    },
    "deleteAccount": {
        "api_id": "API-006",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "VALIDATION_ERROR",
        ],
    },
    "deleteTrade": {
        "api_id": "API-019",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
            "NOT_FOUND",
            "VALIDATION_ERROR",
        ],
    },
    "createSummaryRule": {
        "api_id": "API-023",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "deleteSummaryRule": {
        "api_id": "API-025",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getErrors": {
        "api_id": "API-076",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["INTERNAL_ERROR"],
    },
    "getImportBatch": {
        "api_id": "API-013",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "NOT_FOUND",
            "VALIDATION_ERROR",
        ],
    },
    "getJob": {
        "api_id": "API-062",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "NOT_FOUND",
            "VALIDATION_ERROR",
        ],
    },
    "getMe": {
        "api_id": "API-005",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "VALIDATION_ERROR",
        ],
    },
    "getReconciliation": {
        "api_id": "API-026",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_PERIOD",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getReconciliationBridge": {
        "api_id": "API-027",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_PERIOD",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getNav": {
        "api_id": "API-032",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_PERIOD",
            "UNKNOWN_BENCHMARK",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getRound": {
        "api_id": "API-030",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getTrade": {
        "api_id": "API-080",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "NOT_FOUND",
            "VALIDATION_ERROR",
        ],
    },
    "getTradeHistory": {
        "api_id": "API-021",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "NOT_FOUND",
            "VALIDATION_ERROR",
        ],
    },
    "getVersion": {
        "api_id": "API-073",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["INTERNAL_ERROR"],
    },
    "getVersionRoot": {
        "api_id": "API-073",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["INTERNAL_ERROR"],
    },
    "healthz": {
        "api_id": "API-071",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["INTERNAL_ERROR"],
    },
    "listImportQuarantine": {
        "api_id": "API-014",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "INVALID_CURSOR",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "listImports": {
        "api_id": "API-012",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "INVALID_CURSOR",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
        ],
    },
    "listAssets": {
        "api_id": "API-081",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_CURSOR",
            "INVALID_PERIOD",
            "INVALID_SORT_FIELD",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getDashboard": {
        "api_id": "API-084",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "UNKNOWN_QUERY_PARAM",
        ],
    },
    "getDashboardTradeFlags": {
        "api_id": "API-095",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
        ],
    },
    "getSectorsOverview": {
        "api_id": "API-087",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
            "UNKNOWN_BENCHMARK",
        ],
    },
    "listSectors": {
        "api_id": "API-088",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
            "INVALID_CURSOR",
        ],
    },
    "getAttribution": {
        "api_id": "API-036",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "UNKNOWN_QUERY_PARAM",
            "INVALID_PERIOD",
            "MODEL_NOT_AVAILABLE",
            "UNKNOWN_BENCHMARK",
            "VALIDATION_ERROR",
        ],
    },
    "getAssetsChart": {
        "api_id": "API-085",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
        ],
    },
    "listJobs": {
        "api_id": "API-061",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INVALID_CURSOR",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
        ],
    },
    "listRoundFills": {
        "api_id": "API-031",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "listRounds": {
        "api_id": "API-029",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_CURSOR",
            "INVALID_PERIOD",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "listSummaryRules": {
        "api_id": "API-022",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "listTokens": {
        "api_id": "API-008",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "VALIDATION_ERROR",
            "RATE_LIMITED",
        ],
    },
    "listTrades": {
        "api_id": "API-017",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_SORT_FIELD",
            "INVALID_CURSOR",
            "INVALID_FIELD_SELECTION",
            "UNKNOWN_QUERY_PARAM",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "login": {
        "api_id": "API-002",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["INVALID_CREDENTIALS", "LOGIN_RATE_LIMITED", "VALIDATION_ERROR"],
    },
    "logout": {
        "api_id": "API-004",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "VALIDATION_ERROR",
        ],
    },
    "metricsText": {
        "api_id": "API-074",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["INTERNAL_ERROR"],
    },
    "metrics_catalog": {
        "api_id": "API-086",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "UNKNOWN_QUERY_PARAM",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "metrics_batch": {
        "api_id": "API-033",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "METRIC_NOT_FOUND",
            "INVALID_PERIOD",
            "UNKNOWN_BENCHMARK",
            "VALIDATION_ERROR",
            "UNKNOWN_QUERY_PARAM",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "metrics_detail": {
        "api_id": "API-079",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "METRIC_NOT_FOUND",
            "INVALID_PERIOD",
            "UNKNOWN_BENCHMARK",
            "VALIDATION_ERROR",
            "UNKNOWN_QUERY_PARAM",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "metrics_drilldown": {
        "api_id": "API-035",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "METRIC_NOT_FOUND",
            "INVALID_PERIOD",
            "UNKNOWN_BENCHMARK",
            "INVALID_CURSOR",
            "VALIDATION_ERROR",
            "UNKNOWN_QUERY_PARAM",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "metrics_explain": {
        "api_id": "API-034",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "METRIC_NOT_FOUND",
            "INVALID_PERIOD",
            "UNKNOWN_BENCHMARK",
            "VALIDATION_ERROR",
            "UNKNOWN_QUERY_PARAM",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "patchTrade": {
        "api_id": "API-018",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "VERSION_CONFLICT",
            "NOT_FOUND",
            "VALIDATION_ERROR",
            "IDEMPOTENCY_KEY_REUSED",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "previewImport": {
        "api_id": "API-010",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "IMPORT_UNSUPPORTED_FILE",
            "IMPORT_FILE_TOO_LARGE",
            "VALIDATION_ERROR",
            "RATE_LIMITED",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "readyz": {
        "api_id": "API-072",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["DB_UNAVAILABLE", "SCHEMA_VERSION_MISMATCH"],
    },
    "refreshToken": {
        "api_id": "API-003",
        "scope": "public",
        "idempotent": False,
        "async": False,
        "error_codes": ["TOKEN_EXPIRED", "TOKEN_REVOKED", "TOKEN_REUSED", "VALIDATION_ERROR"],
    },
    "reclassifyImport": {
        "api_id": "API-016",
        "scope": "write",
        "idempotent": True,
        "async": True,
        "error_codes": [
            "NOT_FOUND",
            "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_IN_PROGRESS",
            "JOB_ALREADY_RUNNING",
            "QUOTA_EXCEEDED",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "rebuildPeriod": {
        "api_id": "API-083",
        "scope": "write",
        "idempotent": True,
        "async": True,
        "error_codes": [
            "INVALID_PERIOD",
            "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_IN_PROGRESS",
            "JOB_ALREADY_RUNNING",
            "QUOTA_EXCEEDED",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "rebuildRounds": {
        "api_id": "API-028",
        "scope": "write",
        "idempotent": True,
        "async": True,
        "error_codes": [
            "INVALID_PERIOD",
            "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_IN_PROGRESS",
            "JOB_ALREADY_RUNNING",
            "QUOTA_EXCEEDED",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "registerAccount": {
        "api_id": "API-001",
        "scope": "public",
        "idempotent": True,
        "async": False,
        "error_codes": ["EMAIL_TAKEN", "WEAK_PASSWORD", "VALIDATION_ERROR", "RATE_LIMITED"],
    },
    "restoreTrade": {
        "api_id": "API-020",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "VERSION_CONFLICT",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "revokeImport": {
        "api_id": "API-015",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "IMPORT_ALREADY_REVOKED",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "revokeToken": {
        "api_id": "API-009",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "NOT_FOUND",
            "VALIDATION_ERROR",
        ],
    },
    "updateSummaryRule": {
        "api_id": "API-024",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "VERSION_CONFLICT",
            "NOT_FOUND",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "streamJobEvents": {
        "api_id": "API-063",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "TOKEN_REVOKED",
            "RATE_LIMITED",
            "TOKEN_EXPIRED",
            "MULTIPLE_CREDENTIALS",
        ],
    },
    # 2026-11-05（API-090~094，FIFO 回合分析增强，设计 2026-11-04-fifo-round-insights.md）
    "upsertTradeReason": {
        "api_id": "API-090",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "VERSION_CONFLICT",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "deleteTradeReason": {
        "api_id": "API-091",
        "scope": "write",
        "idempotent": True,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "VERSION_CONFLICT",
            "VALIDATION_ERROR",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "listRoundInsights": {
        "api_id": "API-092",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "INVALID_PERIOD",
            "INVALID_SORT_FIELD",
            "INVALID_CURSOR",
            "UNKNOWN_QUERY_PARAM",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "getRoundInsight": {
        "api_id": "API-093",
        "scope": "read",
        "idempotent": False,
        "async": False,
        "error_codes": [
            "NOT_FOUND",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
    "refreshRoundInsights": {
        "api_id": "API-094",
        "scope": "write",
        "idempotent": True,
        "async": True,
        "error_codes": [
            "INVALID_PERIOD",
            "VALIDATION_ERROR",
            "FORBIDDEN",
            "IDEMPOTENCY_KEY_REUSED",
            "IDEMPOTENCY_IN_PROGRESS",
            "JOB_ALREADY_RUNNING",
            "QUOTA_EXCEEDED",
            "TOKEN_EXPIRED",
            "TOKEN_REVOKED",
            "MULTIPLE_CREDENTIALS",
            "INSUFFICIENT_SCOPE",
            "ADMIN_CANNOT_READ_TRADES",
        ],
    },
}

# 非 JSON 的 2xx 手工示例：SSE（API-063）与 Prometheus 文本（API-074）。
TEXT_RESPONSE_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "metricsText": {
        "200": {
            "description": "Prometheus 文本格式（NFR-15）",
            "content": {
                "text/plain": {
                    "schema": {"type": "string"},
                    "example": (
                        "# HELP stock_api_up 1 表示 API 进程存活\n"
                        "# TYPE stock_api_up gauge\n"
                        'stock_api_up{version="0.1.0"} 1\n'
                        "# TYPE stock_api_http_requests_total counter\n"
                        "stock_api_http_requests_total 42\n"
                    ),
                }
            },
        }
    },
    "streamJobEvents": {
        "200": {
            "description": "SSE 事件流（progress / done / error / 心跳，见 §5.3）",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        "id: 1\n"
                        "event: progress\n"
                        'data: {"job_id":"job_01J9Z6ABCDEFGHJKMNPQRSTVWX",'
                        '"progress":50,"stage":"parsing","message":"解析中",'
                        '"at":"2026-09-30T09:30:00Z"}\n\n'
                        ":keep-alive\n\n"
                    ),
                }
            },
        }
    },
}


def _trade_example() -> dict[str, Any]:
    """TradeOut 的高保真示例（金额定点字符串、日期 ISO、枚举首值）。"""
    return {
        "trade_id": "trd_01J9Z6ABCDEFGHJKMNPQRSTVWX",
        "trade_date": _DATE,
        "security_code": "600519",
        "security_name": "贵州茅台",
        "side": "buy",
        "quantity": _QUANTITY_VALUE,
        "price": _PRICE_VALUE,
        "amount": _MONEY_VALUE,
        "commission": "12.34",
        "stamp_tax": "0.00",
        "other_fee": "0.00",
        "cash_balance": "50000.00",
        "cash_balance_available": True,
        "account_masked": "6222****1234",
        "summary": "证券买入",
        "summary_code": "buy",
        "category": "trade",
        "dedupe_key": "ddk_01J9Z6ABCDEFGHJKMNPQRSTVWX",
        "version": 2,
        "source": "import",
        "batch_id": "imp_01J9Z6ABCDEFGHJKMNPQRSTVWX",
        "note": None,
        "deleted_at": None,
        "created_at": _DATETIME,
        "updated_at": _DATETIME,
        "provenance": {"row": 7},
    }


def _metric_item_example() -> dict[str, Any]:
    """MetricItemOut 的高保真示例（ratio 8 位定点字符串 + CI + 假设）。"""
    return {
        "metric_id": "perf.win_rate",
        "definition_version": "1.0",
        "domain": "perf",
        "scope": {"unit": "month", "cohort": "all", "period": "month"},
        "period": _PERIOD,
        "status": "ok",
        "sample_status": "ok",
        "reason": None,
        "value": "0.58333333",
        "unit": "ratio",
        "precision": 8,
        "n": 12,
        "n_pending": 0,
        "ci_low": "0.32000000",
        "ci_high": "0.81000000",
        "decomposition": {"wins": 7, "losses": 5},
        "assumptions": {
            "rf_rate": "0.015",
            "A": 244,
            "engine_version": "1.0",
            "calendar": "weekday_proxy",
            "period_basis": "natural_month",
            "adjust": "raw",
            "benchmark_id": "hs300_tr",
        },
        "data_as_of": _DATE,
        "inputs_hash": "sha256:" + "a" * 64,
        "computed_at": _DATETIME,
    }


# 关键端点手工示例（其余按 schema 递归生成；生成规则见 _example_for_schema）。
EXAMPLE_OVERRIDES: dict[str, dict[str, Any]] = {
    "metrics_batch": {
        "200": {
            "period": _PERIOD,
            "data_as_of": _DATE,
            "definition_version": "1.0",
            "assumptions": {
                "benchmark_id": "hs300_tr",
                "rf_rate": "0.015",
                "A": 244,
                "engine_version": "1.0",
            },
            "items": [_metric_item_example()],
        }
    },
    "metrics_detail": {"200": _metric_item_example()},
    "metrics_explain": {
        "200": {
            "metric_id": "perf.win_rate",
            "definition_version": "1.0",
            "period": _PERIOD,
            "conclusion": "本月的盈利回合略多于亏损回合，样本仍然偏少。",
            "method": "按回合统计盈利次数占比（胜率）。",
            "drilldown": {
                "endpoint": "/api/v1/metrics/perf.win_rate/drilldown",
                "params": {"period": _PERIOD},
                "description": "查看构成该胜率的每个回合。",
            },
            "status": "ok",
            "reason": None,
            "what_to_do": None,
            "assumptions": {"rf_rate": "0.015", "A": 244, "engine_version": "1.0"},
            "data_as_of": _DATE,
        }
    },
    "metrics_drilldown": {
        "200": {
            "metric_id": "perf.win_rate",
            "definition_version": "1.0",
            "period": _PERIOD,
            "status": "ok",
            "reason": None,
            "items": [
                {
                    "key": "rnd_01J9Z6ABCDEFGHJKMNPQRSTVWX",
                    "value": "0.08333333",
                    "unit": "ratio",
                    "label": "600519 回合收益",
                    "round_id": "rnd_01J9Z6ABCDEFGHJKMNPQRSTVWX",
                    "trade_id": None,
                    "event_date": "2026-06-12",
                    "security_code": "600519",
                }
            ],
            "total": 12,
            "source_endpoints": ["/api/v1/rounds"],
            "params": {"metric_id": "perf.win_rate", "period": _PERIOD},
            "next_cursor": None,
            "truncated": False,
            "assumptions": {"rf_rate": "0.015", "A": 244, "engine_version": "1.0"},
            "data_as_of": _DATE,
        }
    },
    "getErrors": {
        "200": {
            "count": 1,
            "items": [
                {
                    "code": "VALIDATION_ERROR",
                    "http_status": 422,
                    "user_message": "请求参数有问题。",
                    "what_to_do": "按 errors[] 修正字段后重试。",
                    "retryable": False,
                }
            ],
        }
    },
    "healthz": {"200": {"status": "ok", "service": "api", "version": "0.1.0"}},
    "readyz": {
        "200": {
            "status": "ready",
            "checks": {"db": "ok", "schema_version": "0042", "worker": "ok"},
        }
    },
    "login": {
        "200": {
            "user": {
                "id": _UUID,
                "email": "user@example.com",
                "role": "user",
                "email_verified": True,
                "created_at": _DATETIME,
            },
            "access_token": _STRING_EXAMPLES["access_token"],
            "refresh_token": _STRING_EXAMPLES["refresh_token"],
            "token_type": "Bearer",
            "expires_in": 900,
        }
    },
    "registerAccount": {
        "201": {
            "user": {
                "id": _UUID,
                "email": "user@example.com",
                "role": "user",
                "email_verified": False,
                "created_at": _DATETIME,
            },
            "access_token": _STRING_EXAMPLES["access_token"],
            "refresh_token": _STRING_EXAMPLES["refresh_token"],
            "token_type": "Bearer",
            "expires_in": 900,
        }
    },
    "listTrades": {
        "200": {
            "items": [_trade_example()],
            "next_cursor": "cursor_01J9Z6ABCDEFGHJKMNPQRSTVWX",
            "has_more": True,
        }
    },
    "getTrade": {"200": _trade_example()},
    # Period2-16（API-081）：资产表列表（blocked 行的数值字段是显式 null，示例给正常行）。
    "listAssets": {
        "200": {
            "items": [
                {
                    "date": "2026-06-30",
                    "cash": "-70000.00",
                    "market_value": "170620.00",
                    "total_assets": "100620.00",
                    "holding_cost": "170000.00",
                    "unrealized_pnl": "620.00",
                    "holding_count": 1,
                    "cashflow": "0.00",
                    "prev_total_assets": "100000.00",
                    "daily_pnl": "620.00",
                    "daily_return": "0.00620000",
                    "daily_return_pct": "0.62%",
                    "status": "ok",
                    "status_reason": None,
                }
            ],
            "next_cursor": None,
            "has_more": False,
            "build": {
                "state": "current",
                "last_status": "ok",
                "last_built_at": _DATETIME,
                "last_built_through": _DATE,
                "last_mode": "incremental",
                "last_rebuild_from": "2026-08-17",
                "last_rows_written": 45,
                "rows_count": 125,
                "assumption": "back_solved",
                "status_reason": None,
                "has_new_market_data": False,
                "definition_version": "1.0",
            },
            "definition_version": "1.0",
            "data_as_of": _DATE,
        }
    },
    # Period2-16（API-082）：202 专用响应（type 必须是 build_assets）。
    "buildAssets": {
        "202": {
            "job_id": "job_" + _ID_SUFFIX,
            "type": "build_assets",
            "status": "pending",
            "links": {
                "self": f"/api/v1/jobs/job_{_ID_SUFFIX}",
                "events": f"/api/v1/jobs/job_{_ID_SUFFIX}/events",
            },
        }
    },
    # Period2-07（API-083）：202 专用响应（type 必须是 rebuild_period）。
    "rebuildPeriod": {
        "202": {
            "job_id": "job_" + _ID_SUFFIX,
            "type": "rebuild_period",
            "status": "pending",
            "links": {
                "self": f"/api/v1/jobs/job_{_ID_SUFFIX}",
                "events": f"/api/v1/jobs/job_{_ID_SUFFIX}/events",
            },
        }
    },
    # 2026-10-28（API-085）：资产日线 chart-ready 视图；v 为后端归一化字符串。
    "getAssetsChart": {
        "200": {
            "points": [
                {
                    "date": "2026-06-29",
                    "v": "0.0000",
                    "label": "06-29",
                    "amount_label": "100000.00",
                    "cashflow": "10000.00",
                    "cashflow_direction": "in",
                },
                {
                    "date": "2026-06-30",
                    "v": "1.0000",
                    "label": "06-30",
                    "amount_label": "100620.00",
                    "cashflow": "0.00",
                    "cashflow_direction": "none",
                },
            ],
            "n_points": 2,
            "build_state": "current",
            "definition_version": "1.0",
            "data_as_of": _DATE,
        }
    },
    # Period2-12（API-084）：两板块聚合；blocked/missing 字段显式 null（示例取全可用态）。
    "getDashboard": {
        "200": {
            "as_of": "2026-06-30",
            "data_as_of": _DATE,
            "legacy_period": "2026-06",
            "metrics_period": "2026",
            "metrics_period_start": "2026-01-01",
            "metrics_period_end": "2026-06-30",
            "status": "ok",
            "build_state": "current",
            "definition_version": "1.0",
            "build": {
                "state": "current",
                "last_status": "ok",
                "last_built_at": _DATETIME,
                "last_built_through": _DATE,
                "last_mode": "incremental",
                "last_rebuild_from": "2026-08-17",
                "last_rows_written": 45,
                "rows_count": 125,
                "assumption": None,
                "status_reason": None,
                "has_new_market_data": False,
                "definition_version": "1.0",
                "session_notice": None,
            },
            "asset_overview": {
                "status": "ok",
                "holdings": [
                    {
                        "security_code": "600519",
                        "security_name": "贵州茅台",
                        "quantity": "100",
                        "cost": "150000.00",
                        "market_value": "160000.00",
                        "weight_pct": "1.00000000",
                        "cost_display": "150,000.00",
                        "market_value_display": "160,000.00",
                        "weight_display": "100.00%",
                    }
                ],
                "market_value": "160000.00",
                "cash": "40000.00",
                "total_assets": "200000.00",
                "position_pct": "80.00%",
                "total_profit": "25000.00",
                "max_invested_cost": "170000.00",
                "max_cost_return_pct": "14.71%",
                "total_fees": "320.00",
            },
            "history_overview": {
                "status": "ok",
                "closed_security_count": 6,
                "closed_round_count": 12,
                "win_count": 8,
                "loss_count": 4,
                "flat_count": 0,
                "win_pct": "66.67%",
                "loss_pct": "33.33%",
                "flat_pct": "0.00%",
                "max_profit": "12000.00",
                "max_profit_display": "12,000.00",
                "max_profit_round": {
                    "round_id": "rnd_" + _ID_SUFFIX,
                    "security_code": "600519",
                    "security_name": "贵州茅台",
                    "close_date": "2026-06-18",
                    "pnl": "12000.00",
                    "return_rate": "0.08000000",
                    "return_rate_pct": "8.00%",
                    "holding_days": 21,
                    "pnl_display": "12,000.00",
                },
                "max_profit_cost_pct": "7.06%",
                "top_rounds": [
                    {
                        "round_id": "rnd_" + _ID_SUFFIX,
                        "security_code": "600519",
                        "security_name": "贵州茅台",
                        "close_date": "2026-06-18",
                        "pnl": "12000.00",
                        "return_rate": "0.08000000",
                        "return_rate_pct": "8.00%",
                        "holding_days": 21,
                        "pnl_display": "12,000.00",
                    }
                ],
                "bottom_rounds": [
                    {
                        "round_id": "rnd_" + _ID_SUFFIX,
                        "security_code": "600519",
                        "security_name": "贵州茅台",
                        "close_date": "2026-06-18",
                        "pnl": "12000.00",
                        "return_rate": "0.08000000",
                        "return_rate_pct": "8.00%",
                        "holding_days": 21,
                        "pnl_display": "12,000.00",
                    }
                ],
                "top_holding_rounds": [
                    {
                        "round_id": "rnd_" + _ID_SUFFIX,
                        "security_code": "600519",
                        "security_name": "贵州茅台",
                        "close_date": "2026-06-18",
                        "pnl": "12000.00",
                        "return_rate": "0.08000000",
                        "return_rate_pct": "8.00%",
                        "holding_days": 21,
                        "pnl_display": "12,000.00",
                    }
                ],
                "top_trade_counts": [
                    {"security_code": "600519", "security_name": "贵州茅台", "trade_count": 7}
                ],
                "max_drawdown": {
                    "from_date": "2026-05-12",
                    "to_date": "2026-05-28",
                    "duration_days": 16,
                    "duration_obs": 16,
                    "recovery_days": 9,
                    "recovery_censored": False,
                    "mdd_pct": "8.42%",
                    "n_obs": 96,
                    "sample_status": "ok",
                    "reason": None,
                },
            },
            "missing": [],
            "links": {
                "assets": "/assets",
                "rounds": "/rounds",
                "metrics": "/metrics?period=2026",
                "reports": "/reports",
                "exports": "/exports",
                "attribution": "/attribution?period=2026-06",
            },
            "assumptions": {
                "calendar": "weekday_proxy",
                "cost_model": "moving_average",
                "total_profit_basis": "full_history_bridge",
                "mdd": "longest_valid_segment",
            },
            "inputs_hash": "0" * 64,
        }
    },
    "getAttribution": {
        "200": {
            "model": "five_factor",
            "ratio_basis": "amount/total_pnl",
            "period": "2026-06",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
            "status": "ok",
            "definition_version": "1.0",
            "total_pnl_basis": "asset_daily_mtm",
            "total_pnl": "1000.00",
            "ratio_na_reason": None,
            "base_capital": "5000.00",
            "factors": [
                {
                    "code": "selection",
                    "name": "选股",
                    "amount": "600.00",
                    "ratio": "0.60000000",
                    "share": "0.60000000",
                    "rank": 1,
                    "conclusion": "贡献 600.00 元",
                    "presentation": {"bar_pct": "60.0%", "ratio_pct": "60.0%", "tone": "positive"},
                },
                {
                    "code": "sector",
                    "name": "板块配置",
                    "amount": "250.00",
                    "ratio": "0.25000000",
                    "share": "0.25000000",
                    "rank": 2,
                    "conclusion": "贡献 250.00 元",
                    "presentation": {"bar_pct": "25.0%", "ratio_pct": "25.0%", "tone": "positive"},
                },
                {
                    "code": "buy_timing",
                    "name": "买点",
                    "amount": "100.00",
                    "ratio": "0.10000000",
                    "share": "0.10000000",
                    "rank": 3,
                    "conclusion": "贡献 100.00 元",
                    "presentation": {"bar_pct": "10.0%", "ratio_pct": "10.0%", "tone": "positive"},
                },
                {
                    "code": "sell_timing",
                    "name": "卖点",
                    "amount": "80.00",
                    "ratio": "0.08000000",
                    "share": "0.08000000",
                    "rank": 4,
                    "conclusion": "贡献 80.00 元",
                    "presentation": {"bar_pct": "8.0%", "ratio_pct": "8.0%", "tone": "positive"},
                },
                {
                    "code": "fee",
                    "name": "费用",
                    "amount": "-30.00",
                    "ratio": "-0.03000000",
                    "share": "-0.03000000",
                    "rank": 5,
                    "conclusion": "影响 -30.00 元",
                    "presentation": {"bar_pct": "3.0%", "ratio_pct": "-3.0%", "tone": "negative"},
                },
            ],
            "residual": "0.00",
            "residual_ratio": "0.00000000",
            "residual_breakdown": {
                "corporate_action_pnl": "0.00",
                "unattributed_price_pnl": "0.00",
                "dividend_without_position": "0.00",
                "interest": "0.00",
                "ipo_pnl": "0.00",
                "other_cashflow": "0.00",
                "valuation_gap": "0.00",
            },
            "sample": {
                "n_segments": 7,
                "n_attributed_segments": 7,
                "n_rounds": 1,
                "n_symbols": 1,
                "min_n": 10,
                "sample_status": "insufficient",
            },
            "coverage": {
                "capital_total": "5000.00",
                "capital_attributed": "5000.00",
                "capital_ratio": "1.00000000",
                "base_basis": "market",
                "segments_total": 7,
                "segments_attributed": 7,
                "symbols_total": 1,
                "symbols_attributed": 1,
                "symbols_participating": 1,
                "unattributed": [],
            },
            "missing": [],
            "assumptions": {
                "total_pnl_basis": "asset_daily_mtm",
                "beginning_value_source": "prev_total",
                "cashflow_basis": "transfer_rows",
                "fee_basis": "commission+stamp_tax+other_fee",
                "dividend_basis": "cash_ledger_rows_net_after_tax",
                "sector_basis": "ths_industry_index",
                "classification_version": "THS",
                "classification_as_of": "2026-06-30",
                "classification_lookahead": False,
                "board_bar_as_of": "2026-06-30",
                "benchmark_id": "hs300_tr",
                "benchmark_total_return_proxy": "index_close",
                "ratio_basis": "amount/total_pnl",
                "attributed_universe": "a_share_equity",
                "corporate_action_policy": "exclude_symbol",
                "rounding": "scaled_int_half_up_1e-8; residual_plug",
                "adjust": "none",
                "data_sources": {
                    "assets": "2026-06-30",
                    "market": "2026-06-30",
                    "sector": "2026-06-30",
                },
                "residual_tolerance": {
                    "amount": "1000.00",
                    "pct": "0.00200000",
                    "base": "500000.00",
                },
            },
            "data_as_of": "2026-06-30",
            "inputs_hash": "0" * 64,
            "links": {
                "dashboard": "/dashboard",
                "sectors": "/sectors",
                "rounds": "/rounds",
                "reports": "/reports",
            },
        }
    },
    # 2026-11-05（API-090）：逐笔理由；金额/取整/标签全部后端产出。
    "upsertTradeReason": {
        "200": {
            "trade_id": "trd_" + _ID_SUFFIX,
            "side": "buy",
            "reason_code": "bottom_fish",
            "reason_label": "抄底",
            "reason_text": "跌到年线附近，按计划分批",
            "emotion_code": "calm",
            "emotion_label": "平静",
            "tags": ["年线", "分批"],
            "annotated_at": _DATETIME,
            "late_annotated": False,
            "version": 1,
        }
    },
    # 2026-11-05（API-092）：回合洞察列表（含汇总结论；缺失为 null 不补 0）。
    "listRoundInsights": {
        "200": {
            "period": "2026-11",
            "period_basis": "close_date",
            "definition_version": "1.0",
            "bucket_basis": "round",
            "data_as_of": _DATE,
            "ranking_as_of": _DATE,
            "benchmark_id": "hs300_tr",
            "basis": "benchmark_excess_20d",
            "item_total": 1,
            "summary": {
                "swing": {
                    "closed": 1,
                    "short": 0,
                    "swing": 1,
                    "trend": 0,
                    "avg_holding_trading_days": "12.0",
                    "win_rate_pct": "100.0%",
                    "avg_pnl_display": "1234.56",
                    "total_pnl_display": "1234.56",
                },
                "fill_swing": {
                    "bucket_basis": "fill",
                    "pairs": 1,
                    "win_rate_pct": "100.0%",
                    "avg_holding_trading_days": "12.0",
                    "avg_pnl_display": "1234.56",
                    "total_pnl_display": "1234.56",
                },
                # 2026-11-10：盈亏 × 持有期（保本容差由后端常量判定，三类恒返三条）。
                "holding_by_pnl": {
                    "flat_tolerance_display": "100.00",
                    "classes": [
                        {
                            "code": "loss",
                            "label": "亏损",
                            "count": 0,
                            "avg_holding_trading_days": None,
                            "avg_holding_trading_days_x10": None,
                        },
                        {
                            "code": "flat",
                            "label": "保本",
                            "count": 0,
                            "avg_holding_trading_days": None,
                            "avg_holding_trading_days_x10": None,
                        },
                        {
                            "code": "win",
                            "label": "盈利",
                            "count": 1,
                            "avg_holding_trading_days": "12.0",
                            "avg_holding_trading_days_x10": 120,
                        },
                    ],
                },
                "addon": {
                    "count": 1,
                    "declined_after_addon": 0,
                    "declined_pct": "0.0%",
                    "avg_dip_pct": "-2.10%",
                },
                "sell_fly": {
                    "sell_count": 1,
                    "profit_take_fly": 1,
                    "stop_loss_rebound": 0,
                    "fly_pct": "100.0%",
                    "missed_profit_worst_display": "1010.00",
                    "missed_profit_end_display": "510.00",
                    "missed_profit_worst_abs_display": "1010.00",
                    "missed_profit_worst_label": "少赚",
                    "missed_profit_worst_label_en": "missed gain",
                    "missed_profit_end_abs_display": "510.00",
                    "missed_profit_end_label": "少赚",
                    "missed_profit_end_label_en": "missed gain",
                },
                "reason_coverage": {
                    "annotated": 1,
                    "total_rounds": 1,
                    "coverage_pct": "100.0%",
                    "by_code": [
                        {"code": "bottom_fish", "label": "抄底", "count": 1, "share_pct": "100.0%"}
                    ],
                },
                "ranking": {
                    "n_sample": 1,
                    "n_eligible": 1,
                    "sample_status": "no_conclusion",
                    "combined_median_pct": "1.65%",
                    "combined_avg_pct": "1.65%",
                    "quadrant_counts": {"double_positive": 1},
                    "per_stock": [],
                    "basis": "benchmark_excess_20d",
                    "definition_version": "1.0",
                },
                "pending": {"forward_recompute": 0, "reason_missing": 0},
            },
            "items": [
                {
                    "round_id": "rnd_" + _ID_SUFFIX,
                    "security_code": "000422",
                    "security_name": "湖北宜化",
                    "status": "closed",
                    "open_date": "2026-11-02",
                    "close_date": "2026-11-18",
                    "holding_days_legacy": 16,
                    "holding_trading_days": 12,
                    "bucket": "swing",
                    "pnl": "1234.56",
                    "return_rate_pct": "4.56%",
                    "buy_trade": {
                        "trade_id": "trd_" + _ID_SUFFIX,
                        "date": "2026-11-02",
                        "price_display": "10.005000",
                        "quantity_display": None,
                        "amount_display": "10005.00",
                        "reason": {
                            "trade_id": "trd_" + _ID_SUFFIX,
                            "side": "buy",
                            "reason_code": "bottom_fish",
                            "reason_label": "抄底",
                            "reason_text": "跌到年线附近，按计划分批",
                            "emotion_code": "calm",
                            "emotion_label": "平静",
                            "tags": ["年线"],
                            "annotated_at": _DATETIME,
                            "late_annotated": False,
                            "version": 1,
                        },
                        "reason_label": "抄底",
                        "forward": {
                            "5": {
                                "horizon": 5,
                                "status": "ok",
                                "base_shifted": False,
                                "b0_pct": "0.00%",
                                "b0_net_pct": "-0.05%",
                                "b1_pct": "5.00%",
                                "b2_pct": "4.95%",
                                "b3_pct": "3.00%",
                                "max_close_display": "10.80",
                                "min_close_display": "9.90",
                                "price_jump_review": False,
                            },
                            "10": {
                                "horizon": 10,
                                "status": "ok",
                                "base_shifted": False,
                                "b0_pct": "0.00%",
                                "b0_net_pct": "-0.05%",
                                "b1_pct": "10.00%",
                                "b2_pct": "9.95%",
                                "b3_pct": "6.00%",
                                "max_close_display": "11.20",
                                "min_close_display": "9.90",
                                "price_jump_review": False,
                            },
                            "20": {
                                "horizon": 20,
                                "status": "ok",
                                "base_shifted": False,
                                "b0_pct": "0.00%",
                                "b0_net_pct": "-0.05%",
                                "b1_pct": "20.00%",
                                "b2_pct": "19.94%",
                                "b3_pct": "12.00%",
                                "max_close_display": "12.30",
                                "min_close_display": "9.90",
                                "price_jump_review": False,
                            },
                        },
                    },
                    "sell_trade": {
                        "trade_id": "trd_" + _ID_SUFFIX,
                        "date": "2026-11-18",
                        "price_display": "19.980000",
                        "quantity_display": None,
                        "amount_display": "9990.00",
                        "reason": None,
                        "reason_label": None,
                        "forward": {
                            "5": {
                                "horizon": 5,
                                "status": "pending",
                                "base_shifted": False,
                                "b0_pct": None,
                                "b0_net_pct": None,
                                "b1_pct": None,
                                "b2_pct": None,
                                "b3_pct": None,
                                "max_close_display": None,
                                "min_close_display": None,
                                "price_jump_review": False,
                            },
                            "10": {
                                "horizon": 10,
                                "status": "pending",
                                "base_shifted": False,
                                "b0_pct": None,
                                "b0_net_pct": None,
                                "b1_pct": None,
                                "b2_pct": None,
                                "b3_pct": None,
                                "max_close_display": None,
                                "min_close_display": None,
                                "price_jump_review": False,
                            },
                            "20": {
                                "horizon": 20,
                                "status": "pending",
                                "base_shifted": False,
                                "b0_pct": None,
                                "b0_net_pct": None,
                                "b1_pct": None,
                                "b2_pct": None,
                                "b3_pct": None,
                                "max_close_display": None,
                                "min_close_display": None,
                                "price_jump_review": False,
                            },
                        },
                    },
                    "buy_excess_pct": "8.00%",
                    "sell_avoid_pct": None,
                    "combined_pct": None,
                    "rank": None,
                    "quadrant": None,
                    "flags": ["addon", "profit_taking", "full_exit"],
                    "quality": ["forward_incomplete"],
                    "missed_profit_worst_display": "1010.00",
                    "missed_profit_end_display": "510.00",
                    "missed_profit_worst_abs_display": "1010.00",
                    "missed_profit_worst_label": "少赚",
                    "missed_profit_worst_label_en": "missed gain",
                    "missed_profit_end_abs_display": "510.00",
                    "missed_profit_end_label": "少赚",
                    "missed_profit_end_label_en": "missed gain",
                }
            ],
            "next_cursor": None,
            "has_more": False,
            "missing": [],
            "assumptions": ["period_basis=close_date", "benchmark=hs300_tr"],
            "disclaimer": (
                "以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益；"
                "相对基准为 hs300_tr ETF 代理；缺失/公司行动显示 —，不显示 0%。"
            ),
            "flag_glossary": [
                {
                    "code": "addon",
                    "label_zh": "加仓",
                    "label_en": "Add-on",
                    "description_zh": (
                        "本回合内有买入发生在已有持仓之后（按流水发生顺序推演，"
                        "同日多笔以文件行号排序）；回合的第一笔建仓不算加仓。"
                    ),
                    "description_en": "A buy inside this round happened while the security was already held.",
                    "kind": "item_flag",
                },
                {
                    "code": "profit_take_fly",
                    "label_zh": "卖飞",
                    "label_en": "Sold too early",
                    "description_zh": "止盈卖出后 20 个交易日最高收盘 > 净卖价 × 1.03。",
                    "description_en": "After a profitable sale, the 20-day highest close exceeded the net sell price by more than 3%.",
                    "kind": "item_flag",
                },
                {
                    "code": "addon_decline",
                    "label_zh": "加仓后下跌",
                    "label_en": "Add-on then decline",
                    "description_zh": "查询别名：同时命中 `addon` 与 `continued_decline`。",
                    "description_en": "Query alias matching rounds flagged with both `addon` and `continued_decline`.",
                    "kind": "filter_alias",
                },
            ],
        }
    },
    # 2026-11-05（API-093）：单回合详情（逐笔理由/前瞻/旗标/排序）。
    "getRoundInsight": {
        "200": {
            "round_id": "rnd_" + _ID_SUFFIX,
            "security_code": "000422",
            "security_name": "湖北宜化",
            "status": "closed",
            "open_date": "2026-11-02",
            "close_date": "2026-11-18",
            "holding_days_legacy": 16,
            "holding_trading_days": 12,
            "bucket": "swing",
            "pnl": "1234.56",
            "return_rate_pct": "4.56%",
            "buy_trade": None,
            "sell_trade": None,
            "fills": [
                {
                    "fill_id": "fil_" + _ID_SUFFIX,
                    "sequence": 1,
                    "quantity_display": "100",
                    "pnl_display": "1234.56",
                    "open_date": "2026-11-02",
                    "close_date": "2026-11-18",
                    "holding_trading_days": 12,
                    "bucket": "swing",
                    "buy": None,
                    "sell": None,
                }
            ],
            "flags": ["swing", "profit_taking"],
            "quality": [],
            "ranking": {
                "rank": None,
                "buy_excess_pct": "8.00%",
                "sell_avoid_pct": "5.00%",
                "combined_pct": "6.50%",
                "quadrant": "double_positive",
                "basis": "benchmark_excess_20d",
                "definition_version": "1.0",
            },
            "disclaimer": (
                "以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益；"
                "相对基准为 hs300_tr ETF 代理；缺失/公司行动显示 —，不显示 0%。"
            ),
            "flag_glossary": [
                {
                    "code": "addon",
                    "label_zh": "加仓",
                    "label_en": "Add-on",
                    "description_zh": (
                        "本回合内有买入发生在已有持仓之后（按流水发生顺序推演，"
                        "同日多笔以文件行号排序）；回合的第一笔建仓不算加仓。"
                    ),
                    "description_en": "A buy inside this round happened while the security was already held.",
                    "kind": "item_flag",
                },
                {
                    "code": "profit_take_fly",
                    "label_zh": "卖飞",
                    "label_en": "Sold too early",
                    "description_zh": "止盈卖出后 20 个交易日最高收盘 > 净卖价 × 1.03。",
                    "description_en": "After a profitable sale, the 20-day highest close exceeded the net sell price by more than 3%.",
                    "kind": "item_flag",
                },
                {
                    "code": "addon_decline",
                    "label_zh": "加仓后下跌",
                    "label_en": "Add-on then decline",
                    "description_zh": "查询别名：同时命中 `addon` 与 `continued_decline`。",
                    "description_en": "Query alias matching rounds flagged with both `addon` and `continued_decline`.",
                    "kind": "filter_alias",
                },
            ],
        }
    },
    # 2026-11-05（API-094）：同步 200 与转作业 202 两条路径。
    "refreshRoundInsights": {
        "200": {
            "computed": 3,
            "skipped": 12,
            "pending": 2,
            "no_calendar": 0,
            "no_bar": 1,
            "corporate_action": 0,
            "divisor_missing": 0,
            "stale_price": 0,
            "data_as_of": _DATE,
        },
        "202": {
            "job_id": "job_" + _ID_SUFFIX,
            "type": "refresh_round_insights",
            "status": "pending",
            "links": {
                "self": f"/api/v1/jobs/job_{_ID_SUFFIX}",
                "events": f"/api/v1/jobs/job_{_ID_SUFFIX}/events",
            },
        },
    },
}


# ── 确定性示例生成 ────────────────────────────────────────────


def _error_schema() -> dict[str, Any]:
    field_error = {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "code", "message"],
        "properties": {
            "field": {"type": "string"},
            "code": {"type": "string"},
            "message": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "RFC 7807 问题详情 + 平台扩展字段（code/user_message/what_to_do/retryable/"
            "request_id/errors），对应 Content-Type: application/problem+json。"
        ),
        "required": [
            "type",
            "title",
            "status",
            "detail",
            "code",
            "user_message",
            "what_to_do",
            "retryable",
            "request_id",
        ],
        "properties": {
            "type": {"type": "string", "format": "uri"},
            "title": {"type": "string"},
            "status": {"type": "integer", "minimum": 400, "maximum": 599},
            "detail": {"type": "string"},
            "instance": {"type": "string"},
            "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
            "user_message": {"type": "string"},
            "what_to_do": {"type": "string"},
            "retryable": {"type": "boolean"},
            "request_id": {"type": "string"},
            "errors": {"type": "array", "items": field_error},
        },
    }


def _problem_example(code: str) -> dict[str, Any]:
    problem = ERROR_CATALOG[ErrorCode(code)]
    example: dict[str, Any] = {
        "type": f"https://stock-analyze.local/errors/{code}",
        "title": problem.title,
        "status": problem.http_status,
        "detail": problem.user_message,
        "code": code,
        "user_message": problem.user_message,
        "what_to_do": problem.what_to_do,
        "retryable": problem.retryable,
        "request_id": _REQUEST_ID,
    }
    if code in _FIELD_ERROR_CODES:
        example["errors"] = [
            {"field": "body.email", "code": "invalid", "message": "邮箱格式不正确。"}
        ]
    return example


def _ref_name(ref: str) -> str | None:
    prefix = "#/components/schemas/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def _id_example(field: str) -> str:
    if field == "id":
        return _UUID
    return _ID_PREFIXES.get(field, "obj_") + _ID_SUFFIX


def _string_example(field: str | None, fmt: str | None) -> str:
    if fmt == "date":
        return _DATE
    if fmt == "date-time":
        return _DATETIME
    if fmt == "uuid":
        return _UUID
    if fmt == "email":
        return "user@example.com"
    if fmt in ("uri", "url"):
        return "https://stock-analyze.local/example"
    if field is not None:
        if field in MONEY_FIELDS:
            return _MONEY_VALUE
        if field in PRICE_FIELDS:
            return _PRICE_VALUE
        if field in QUANTITY_FIELDS:
            return _QUANTITY_VALUE
        if field in RATIO_TRIM3_FIELDS:
            return _RATIO_TRIM3_VALUE
        if field in RATIO_FIELDS:
            return _RATIO_VALUE
        if field.endswith("_id") or field == "id":
            return _id_example(field)
        if field in _DATE_FIELDS:
            return _DATE
        if field in _DATETIME_FIELDS or field.endswith(("_at", "_as_of")):
            return _DATETIME
        if field in _STRING_EXAMPLES:
            return _STRING_EXAMPLES[field]
    return "示例"


def _example_for_schema(
    schema: Any,
    components: dict[str, Any],
    *,
    field: str | None = None,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> Any:
    """按 OpenAPI schema 递归生成确定性示例（字段名感知；循环引用安全）。"""
    if depth > _MAX_DEPTH or not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = _ref_name(ref)
        if name is None or name in seen:
            return {}
        return _example_for_schema(
            components.get(name), components, field=field, depth=depth + 1, seen=seen | {name}
        )
    if "const" in schema:
        return deepcopy(schema["const"])
    if schema.get("default") is not None:
        return deepcopy(schema["default"])
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        merged: dict[str, Any] = {}
        for part in all_of:
            if isinstance(part, dict):
                merged.update(part)
        return _example_for_schema(merged, components, field=field, depth=depth + 1, seen=seen)
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            non_null = [
                option
                for option in options
                if not (isinstance(option, dict) and option.get("type") == "null")
            ]
            return _example_for_schema(
                (non_null or options)[0], components, field=field, depth=depth + 1, seen=seen
            )
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return deepcopy(enum[0])
    schema_type = schema.get("type")
    if schema_type == "null":
        return None
    if schema_type == "string":
        return _string_example(field, schema.get("format"))
    if schema_type == "integer":
        return _INT_EXAMPLES.get(field or "", _INT_EXAMPLES["_default"])
    if schema_type == "number":
        return _RATIO_NUMBER if field in RATIO_FIELDS else 0.5
    if schema_type == "boolean":
        return _BOOL_EXAMPLES.get(field or "", False)
    if schema_type == "array":
        if field in _LIST_EXAMPLES:
            return deepcopy(_LIST_EXAMPLES[field])
        items = schema.get("items")
        if items is None:
            return []
        if field == "warnings" and isinstance(items, dict) and items.get("type") == "string":
            return ["NAV_UNRELIABLE"]
        if field == "sample" and isinstance(items, dict) and items.get("type") == "object":
            return [{"trade_date": _DATE, "security_code": "600519", "summary": "证券买入"}]
        item_example = _example_for_schema(
            items, components, field=field, depth=depth + 1, seen=seen
        )
        return [] if item_example is None else [item_example]
    if schema_type == "object" or "properties" in schema or "additionalProperties" in schema:
        if field is not None and field in _DICT_EXAMPLES:
            return deepcopy(_DICT_EXAMPLES[field])
        properties = schema.get("properties")
        if isinstance(properties, dict):
            return {
                name: _example_for_schema(
                    sub_schema, components, field=name, depth=depth + 1, seen=seen
                )
                for name, sub_schema in properties.items()
            }
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            value = _example_for_schema(
                additional, components, field=None, depth=depth + 1, seen=seen
            )
            return {} if value is None else {"key": value}
        return {}
    return {}


# ── 元数据 enrichment ─────────────────────────────────────────


def _status_of(code: str) -> int | None:
    try:
        return ERROR_CATALOG[ErrorCode(code)].http_status
    except (KeyError, ValueError):
        return None


def _as_status(raw_status: Any) -> int | None:
    try:
        return int(str(raw_status))
    except ValueError:
        return None


def _effective_error_codes(base_codes: list[str], responses: dict[str, Any]) -> list[str]:
    """静态错误码 + 既有响应 status 的反推码（保证 x-error-codes 覆盖所有错误 status）。"""
    codes = list(base_codes)
    for raw_status in responses:
        status = _as_status(raw_status)
        if status is None or status < 400:
            continue
        if any(_status_of(code) == status for code in codes):
            continue
        inferred = _STATUS_FALLBACK_CODES.get(status)
        if inferred is not None and inferred not in codes:
            codes.append(inferred)
    return codes


def _fallback_meta(operation_id: str, method: str, path: str) -> dict[str, Any]:
    """未登记 operation 的兜底元数据（新端点应先补 API-Catalog + OPERATION_META）。"""
    del operation_id
    public = path in {"/healthz", "/readyz", "/version", "/metrics", "/api/v1/errors"}
    scope = "public" if public else ("read" if method.upper() == "GET" else "write")
    codes = [] if public else ["TOKEN_EXPIRED", "TOKEN_REVOKED", "MULTIPLE_CREDENTIALS"]
    if scope == "write":
        codes.append("INSUFFICIENT_SCOPE")
    codes.append("VALIDATION_ERROR")
    return {
        "api_id": "UNREGISTERED",
        "scope": scope,
        "idempotent": False,
        "async": False,
        "error_codes": codes,
    }


def _enrich_operation(
    operation: dict[str, Any], schemas: dict[str, Any], method: str, path: str
) -> None:
    operation_id = str(operation.get("operationId") or "")
    meta = OPERATION_META.get(operation_id)
    if meta is None:
        meta = _fallback_meta(operation_id, method, path)
    responses: dict[str, Any] = operation.setdefault("responses", {})
    codes = _effective_error_codes(list(meta["error_codes"]), responses)
    operation["x-required-scope"] = meta["scope"]
    operation["x-idempotent"] = meta["idempotent"]
    operation["x-async"] = meta["async"]
    operation["x-error-codes"] = codes

    # 每个错误 status 追加 application/problem+json（同 status 用首个错误码做示例）。
    first_by_status: dict[int, str] = {}
    for code in codes:
        status = _status_of(code)
        if status is not None:
            first_by_status.setdefault(status, code)
    for status in sorted(first_by_status):
        response = responses.get(str(status))
        if not isinstance(response, dict):
            response = {"description": _STATUS_DESCRIPTIONS.get(status, "Error")}
            responses[str(status)] = response
        content = response.setdefault("content", {})
        if PROBLEM_CONTENT_TYPE not in content:
            content[PROBLEM_CONTENT_TYPE] = {
                "schema": {"$ref": f"#/components/schemas/{ERROR_SCHEMA}"},
                "example": _problem_example(first_by_status[status]),
            }

    # 每个 2xx 注入确定性 example（文本响应整段覆盖）。
    for status_key, response in responses.items():
        status_text = str(status_key)
        if not status_text.startswith("2") or not isinstance(response, dict):
            continue
        text_override = TEXT_RESPONSE_OVERRIDES.get(operation_id, {}).get(status_text)
        if text_override is not None:
            responses[status_key] = deepcopy(text_override)
            continue
        content = response.get("content")
        if not isinstance(content, dict) or not content:
            continue
        manual = EXAMPLE_OVERRIDES.get(operation_id, {}).get(status_text)
        for media_type, media in content.items():
            if not isinstance(media, dict):
                continue
            if "example" in media or "examples" in media:
                continue
            if manual is not None and media_type == JSON_CONTENT_TYPE:
                media["example"] = deepcopy(manual)
            else:
                media["example"] = _example_for_schema(media.get("schema"), schemas)


def apply_contract_metadata(spec: dict[str, Any]) -> dict[str, Any]:
    """返回补全 §7.4 契约元数据后的新 spec（纯函数、确定性、幂等）。

    - 不修改入参（deepcopy）；
    - 不改 path / operationId / status 语义，只追加 x-*、example 与 problem+json；
    - 相同输入重复调用（或对输出再调用）结果一致。
    """
    enriched: dict[str, Any] = deepcopy(spec)
    components = enriched.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas[ERROR_SCHEMA] = _error_schema()
    paths = enriched.get("paths")
    if not isinstance(paths, dict):
        return enriched
    for path in sorted(paths):
        item = paths[path]
        if not isinstance(item, dict):
            continue
        for method in sorted(item):
            if method not in _METHODS:
                continue
            operation = item[method]
            if isinstance(operation, dict):
                _enrich_operation(operation, schemas, method=method, path=path)
    return enriched


# ── 契约校验 ──────────────────────────────────────────────────

_ERROR_REQUIRED_FIELDS = (
    "type",
    "title",
    "status",
    "detail",
    "code",
    "user_message",
    "what_to_do",
    "retryable",
    "request_id",
)


def _validate_error_schema(spec: dict[str, Any], problems: list[str]) -> None:
    schemas = spec.get("components", {}).get("schemas", {})
    error_schema = schemas.get(ERROR_SCHEMA) if isinstance(schemas, dict) else None
    if not isinstance(error_schema, dict):
        problems.append(f"components.schemas.{ERROR_SCHEMA} 缺失（RFC 7807 错误体组件）")
        return
    properties = error_schema.get("properties")
    if not isinstance(properties, dict):
        problems.append(f"{ERROR_SCHEMA} schema 缺少 properties")
        return
    required = error_schema.get("required")
    required_set = set(required) if isinstance(required, list) else set()
    for name in _ERROR_REQUIRED_FIELDS:
        if name not in properties:
            problems.append(f"{ERROR_SCHEMA} 缺少字段 {name}")
        elif name not in required_set:
            problems.append(f"{ERROR_SCHEMA}.required 未包含 {name}")
    errors_prop = properties.get("errors")
    if not isinstance(errors_prop, dict) or errors_prop.get("type") != "array":
        problems.append(f"{ERROR_SCHEMA}.errors 必须是 array")
        return
    item = errors_prop.get("items")
    item_props = item.get("properties") if isinstance(item, dict) else None
    item_required = item.get("required") if isinstance(item, dict) else None
    item_required_set = set(item_required) if isinstance(item_required, list) else set()
    for name in ("field", "code", "message"):
        if not isinstance(item_props, dict) or name not in item_props:
            problems.append(f"{ERROR_SCHEMA}.errors[].{name} 缺失")
        elif name not in item_required_set:
            problems.append(f"{ERROR_SCHEMA}.errors[].required 未包含 {name}")


def _has_example(response: dict[str, Any]) -> bool:
    content = response.get("content")
    if not isinstance(content, dict) or not content:
        return True  # 204/304 等无 body 的 2xx/非错误响应不要求示例
    for media in content.values():
        if not isinstance(media, dict):
            continue
        if "example" in media or "examples" in media:
            return True
        schema = media.get("schema")
        if isinstance(schema, dict) and ("example" in schema or "examples" in schema):
            return True
    return False


def _validate_operation_x(operation: dict[str, Any], label: str, problems: list[str]) -> None:
    scope = operation.get("x-required-scope")
    if scope not in REQUIRED_SCOPES:
        problems.append(f"{label}: x-required-scope 缺失或非法（{scope!r}）")
    for key in ("x-idempotent", "x-async"):
        if not isinstance(operation.get(key), bool):
            problems.append(f"{label}: {key} 缺失或不是 bool")
    codes = operation.get("x-error-codes")
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        problems.append(f"{label}: x-error-codes 缺失或不是字符串列表")
        return
    for code in codes:
        if _status_of(code) is None:
            problems.append(f"{label}: x-error-codes 含未登记错误码 {code}")


def _validate_operation(operation: dict[str, Any], label: str, problems: list[str]) -> None:
    _validate_operation_x(operation, label, problems)
    responses = operation.get("responses")
    if not isinstance(responses, dict) or not responses:
        problems.append(f"{label}: responses 缺失")
        return
    codes = operation.get("x-error-codes")
    expected_statuses: set[int] = set()
    if isinstance(codes, list):
        for code in codes:
            if isinstance(code, str):
                status = _status_of(code)
                if status is not None:
                    expected_statuses.add(status)
    for raw_status, response in responses.items():
        status_text = str(raw_status)
        status = _as_status(raw_status)
        if not isinstance(response, dict):
            problems.append(f"{label}: responses[{status_text}] 不是对象")
            continue
        if status_text.startswith("2"):
            if not _has_example(response):
                problems.append(f"{label}: 2xx {status_text} 缺少 example")
            continue
        if status is None or status < 400:
            continue
        content = response.get("content")
        problem = content.get(PROBLEM_CONTENT_TYPE) if isinstance(content, dict) else None
        if not isinstance(problem, dict):
            problems.append(f"{label}: {status_text} 缺少 {PROBLEM_CONTENT_TYPE} 响应")
            continue
        schema = problem.get("schema")
        ref = schema.get("$ref") if isinstance(schema, dict) else None
        if not isinstance(ref, str) or not ref.endswith(f"/{ERROR_SCHEMA}"):
            problems.append(
                f"{label}: {status_text} problem+json 未引用 components.schemas.{ERROR_SCHEMA}"
            )
        if "example" not in problem and "examples" not in problem:
            problems.append(f"{label}: {status_text} problem+json 缺少 example")
    for expected in sorted(expected_statuses):
        if str(expected) not in responses:
            problems.append(f"{label}: x-error-codes 声明的 {expected} 缺少 responses 项")
    for raw_status in responses:
        status = _as_status(raw_status)
        if status is not None and status >= 400 and status not in expected_statuses:
            problems.append(f"{label}: responses {status} 未被 x-error-codes 覆盖")


def validate_contract(spec: dict[str, Any]) -> list[str]:
    """校验 §7.4 强制项；返回违规列表（空列表 = 通过）。无副作用。"""
    problems: list[str] = []
    _validate_error_schema(spec, problems)
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return [*problems, "spec.paths 缺失"]
    seen_operation_ids: dict[str, str] = {}
    for path in sorted(paths):
        item = paths[path]
        if not isinstance(item, dict):
            continue
        for method in sorted(item):
            if method not in _METHODS:
                continue
            operation = item[method]
            label = f"{method.upper()} {path}"
            if not isinstance(operation, dict):
                problems.append(f"{label}: operation 不是对象")
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                problems.append(f"{label}: operationId 缺失")
            elif operation_id in seen_operation_ids:
                problems.append(
                    f"{label}: operationId 重复（{operation_id}，已用于 "
                    f"{seen_operation_ids[operation_id]}）"
                )
            else:
                seen_operation_ids[operation_id] = label
            _validate_operation(operation, label, problems)
    return problems
