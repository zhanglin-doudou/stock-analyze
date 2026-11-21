"""智能分析助手的稳定枚举（契约层，零依赖）。"""

from __future__ import annotations

from enum import StrEnum


class ChatStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"
    ERROR = "error"


class DegradeReason(StrEnum):
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
    LLM_BAD_RESPONSE = "LLM_BAD_RESPONSE"
    LLM_CONTENT_FILTER = "LLM_CONTENT_FILTER"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    TOOL_PARTIAL = "TOOL_PARTIAL"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"


class ToolSource(StrEnum):
    BUILTIN = "builtin"
    MCP = "mcp"


class ToolTrust(StrEnum):
    LOCAL = "local"
    EXTERNAL = "external"


class ToolName(StrEnum):
    ASSETS_QUERY = "assets_query"
    TRADES_QUERY = "trades_query"
    ROUNDS_QUERY = "rounds_query"
    ROUND_DETAIL = "round_detail"
    PORTFOLIO_SNAPSHOT = "portfolio_snapshot"
    PERFORMANCE_METRICS = "performance_metrics"
    ATTRIBUTION_SUMMARY = "attribution_summary"
    NAV_SUMMARY = "nav_summary"
    SECTOR_EXPOSURE = "sector_exposure"
    DATA_QUALITY = "data_quality"
    METRIC_EXPLAIN = "metric_explain"


class Intent(StrEnum):
    PORTFOLIO = "portfolio"
    PERFORMANCE = "performance"
    ATTRIBUTION = "attribution"
    ROUNDS = "rounds"
    ROUND_DETAIL = "round_detail"
    TRADES = "trades"
    ASSETS = "assets"
    NAV = "nav"
    SECTORS = "sectors"
    DATA_QUALITY = "data_quality"
    METRIC_EXPLAIN = "metric_explain"
    ADVICE = "advice"
    UNKNOWN = "unknown"
