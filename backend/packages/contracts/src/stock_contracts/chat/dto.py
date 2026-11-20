"""智能分析助手的跨层 DTO（只做数据结构，不依赖 LangChain/DB）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from stock_contracts.chat.enums import ChatStatus, DegradeReason, Intent, ToolName


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    label: str
    value_display: str
    source_endpoint: str
    source_params: Mapping[str, Any] = field(default_factory=dict)
    definition_version: str | None = None
    data_as_of: date | None = None
    number: int | None = None


@dataclass(frozen=True, slots=True)
class MissingEntry:
    code: str
    message: str
    what_to_do: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: ToolName
    status: ChatStatus
    data: Mapping[str, Any]
    citations: tuple[Citation, ...] = ()
    data_as_of: date | None = None
    definition_version: str | None = None
    missing: tuple[MissingEntry, ...] = ()
    truncated: bool = False
    row_count: int = 0
    latency_ms: int | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    mention: str
    type: Literal["security", "round", "metric", "sector", "flag"]
    resolved_id: str
    resolved_name: str | None = None
    source: Literal["focus", "recent_tool", "db_match", "user_explicit"] = "focus"
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class PeriodSpec:
    kind: Literal["month", "year", "range", "all", "relative", "none"]
    value: str | None = None
    start: date | None = None
    end: date | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Understanding:
    intents: tuple[Intent, ...]
    confidence: float
    period: PeriodSpec | None
    entities: tuple[ResolvedEntity, ...]
    resolved_query: str
    needs_clarification: bool = False
    clarification: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextUsage:
    window_turns: int
    included_turns: int
    estimated_input_tokens: int
    actual_prompt_tokens: int | None = None
    actual_completion_tokens: int | None = None
    compressed: bool = False
    context_truncated: bool = False
    summary_version: int = 0
    compression_steps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    citation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    answer_markdown: str
    claims: tuple[Claim, ...] = ()
    used_citations: tuple[str, ...] = ()
    confidence: Literal["high", "medium", "low"] = "medium"
    followups: tuple[str, ...] = ()
    status: ChatStatus = ChatStatus.OK
    degrade_reason: DegradeReason | None = None
    degrade_message: str | None = None
    what_to_do: str | None = None
    retryable: bool = False
    fallback: bool = False
    llm_used: bool = False
    context_usage: ContextUsage | None = None


@dataclass(frozen=True, slots=True)
class LlmResult:
    content: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    raw_request: Mapping[str, Any] = field(default_factory=dict)
    raw_response: Mapping[str, Any] = field(default_factory=dict)
