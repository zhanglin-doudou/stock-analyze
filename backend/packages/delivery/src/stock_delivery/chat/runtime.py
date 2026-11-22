"""单轮 LangGraph 运行时：上下文 → 理解 → 工具 → 模板/LLM → 校验 → 持久化。"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator
from stock_contracts.chat.dto import (
    AnswerDraft,
    Citation,
    Claim,
    ContextUsage,
    LlmResult,
    ToolResult,
    Understanding,
)
from stock_contracts.chat.enums import ChatStatus, DegradeReason, Intent, ToolName
from stock_contracts.chat.ports import ChatDataPort
from stock_contracts.chat.tools import ToolCall, ToolContext
from stock_contracts.clock import Clock
from stock_platform.config import Settings
from stock_platform.obs import get_logger
from stock_platform.repositories.chat import ChatRepository

from .composer import compose
from .context import ContextBundle, ContextManager
from .prompts import ANSWER_SYSTEM, COMPRESS_SYSTEM
from .provider import LlmError, LlmProvider
from .state import ChatState
from .tools import RulePlanner, ToolExecutor, ToolRegistry, ToolTraceSink
from .understanding import UnderstandingService
from .validator import sanitize, validate


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


log = get_logger("stock_delivery.chat.runtime")


class ChatRuntime:
    def __init__(
        self,
        *,
        repo: ChatRepository,
        data_port: ChatDataPort,
        llm: LlmProvider,
        clock: Clock,
        settings: Settings,
        user_id: uuid.UUID,
        conversation: Mapping[str, Any],
        turn_id: str,
        question: str,
        user_message_id: str | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        llm_enabled: bool | None = None,
    ) -> None:
        self.repo = repo
        self.data_port = data_port
        self.llm = llm
        self.clock = clock
        self.settings = settings
        #: 是否允许调用 LLM（按请求解析；缺省沿用 settings.llm_enabled）。
        self.llm_enabled = settings.llm_enabled if llm_enabled is None else llm_enabled
        self.user_id = user_id
        self.conversation = dict(conversation)
        self.turn_id = turn_id
        self.user_message_id = user_message_id
        self.question = question
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self._on_event = on_event
        self.context_manager = ContextManager(repo, settings)
        self.understanding_service = UnderstandingService(llm, clock)
        self._bundle: ContextBundle | None = None
        self._understanding: Understanding | None = None
        self._template: AnswerDraft | None = None
        self._citations: list[Citation] = []
        self._llm_seq = 0
        self._tool_seq = 0
        self._tool_logs: list[dict[str, Any]] = []
        self._llm_logs: list[dict[str, Any]] = []
        self._llm_used = False
        self._degrade_reason: DegradeReason | None = None
        self._started = time.monotonic()
        self.assistant_message_id: str | None = None
        self._events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._step = 1

    # ── nodes ──────────────────────────────────────────────
    async def load_context(self, state: ChatState) -> dict[str, Any]:
        self._emit("turn.started", "start", {"question_length": len(self.question)})
        bundle = await self.context_manager.build(
            self.user_id,
            self.conversation,
            self.question,
            compressor=self._compress if self.llm_enabled else None,
            exclude_message_id=self.user_message_id,
        )
        self._bundle = bundle
        return {"context": to_jsonable(bundle.usage)}

    async def understand(self, state: ChatState) -> dict[str, Any]:
        assert self._bundle is not None
        self._emit("understanding.started", "understand", {})
        if not self.llm_enabled:
            understanding = self.understanding_service.fallback_understanding(
                self.question, self._bundle.focus
            )
            self._understanding = understanding
            self._emit_understanding_completed(understanding)
            return {"understanding": to_jsonable(understanding)}

        messages = self.context_manager.understanding_messages(self._bundle, self.question)
        try:
            result = await self._complete_with_retry(
                purpose="understanding",
                messages=messages,
                max_tokens=self.settings.llm_understanding_max_tokens,
                prompt_version="chat.understand.v1",
            )
            self._llm_used = True
            understanding = self.understanding_service.parse(
                result.content, question=self.question, focus=self._bundle.focus
            )
            self._understanding = understanding
            self._emit_understanding_completed(understanding)
            return {"understanding": to_jsonable(understanding)}
        except LlmError as exc:
            self._degrade_reason = exc.reason
            understanding = self.understanding_service.fallback_understanding(
                self.question, self._bundle.focus
            )
            self._understanding = understanding
            self._emit_understanding_completed(understanding)
            return {
                "understanding": to_jsonable(understanding),
                "degrade_reason": exc.reason.value,
            }
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            log.error(
                "chat.llm.bad_response",
                purpose="understanding",
                model=self.llm.model,
                error=str(exc)[:300],
            )
            self._degrade_reason = DegradeReason.LLM_BAD_RESPONSE
            understanding = self.understanding_service.fallback_understanding(
                self.question, self._bundle.focus
            )
            self._understanding = understanding
            self._emit_understanding_completed(understanding)
            return {
                "understanding": to_jsonable(understanding),
                "degrade_reason": DegradeReason.LLM_BAD_RESPONSE.value,
            }

    def route_after_understand(self, state: ChatState) -> str:
        understanding = self._understanding
        if understanding is None or understanding.needs_clarification:
            return "clarify"
        if not understanding.intents or understanding.intents[0] is Intent.UNKNOWN:
            return "tools"
        return "tools"

    async def execute_tools(self, state: ChatState) -> dict[str, Any]:
        assert self._understanding is not None
        if (
            self.settings.tool_platform_enabled
            and self.tool_registry is not None
            and self.tool_executor is not None
        ):
            platform_results = await self._execute_via_tool_platform()
            if platform_results:
                self._citations = _number_citations(
                    [citation for result in platform_results for citation in result.citations]
                )
                return {"results": [to_jsonable(result) for result in platform_results]}
        tools = _tools_for(self._understanding)
        self._emit(
            "plan.created",
            "plan",
            {
                "planner": "legacy",
                "calls": [
                    {"tool_id": f"builtin.{tool.value}", "tool": tool.value, "source": "builtin"}
                    for tool, _ in tools
                ],
            },
        )
        results: list[ToolResult] = []
        for tool, args in tools:
            self._tool_seq += 1
            self._emit(
                "tool.call.started",
                "execute",
                {
                    "call_id": f"call_{self._tool_seq}",
                    "tool_id": f"builtin.{tool.value}",
                    "tool": tool.value,
                    "title": tool.value,
                    "source": "builtin",
                },
            )
            started = time.monotonic()
            result = await self.data_port.invoke(tool, args)
            latency = int((time.monotonic() - started) * 1000)
            result = ToolResult(
                tool=result.tool,
                status=result.status,
                data=result.data,
                citations=result.citations,
                data_as_of=result.data_as_of,
                definition_version=result.definition_version,
                missing=result.missing,
                truncated=result.truncated,
                row_count=result.row_count,
                latency_ms=latency,
                error_code=result.error_code,
            )
            self._record_tool(tool, args, result, latency)
            self._emit(
                "tool.call.succeeded"
                if result.status is not ChatStatus.ERROR
                else "tool.call.failed",
                "execute",
                {
                    "call_id": f"call_{self._tool_seq}",
                    "tool_id": f"builtin.{tool.value}",
                    "tool": tool.value,
                    "title": tool.value,
                    "source": "builtin",
                    "status": result.status.value,
                    "row_count": result.row_count,
                    "latency_ms": latency,
                    "error_code": result.error_code,
                },
            )
            results.append(result)
        self._citations = _number_citations(
            [citation for result in results for citation in result.citations]
        )
        return {"results": [to_jsonable(result) for result in results]}

    async def _execute_via_tool_platform(self) -> list[ToolResult]:
        assert self._understanding is not None
        assert self.tool_registry is not None
        assert self.tool_executor is not None
        ctx = ToolContext(
            user_id=self.user_id,
            request_id=self.turn_id,
            turn_id=self.turn_id,
            scopes=frozenset({"read"}),
            deadline=time.monotonic() + self.settings.chat_turn_deadline_seconds,
        )
        catalog = await self.tool_registry.snapshot(ctx, include_external=False)
        # P0 只接规则 Planner；CHAT_PLANNER_MODE=auto 的 LLM Planner 在 P1 接入。
        plan = RulePlanner().plan(self._understanding, catalog)
        if plan.action != "call" or not plan.calls:
            return []
        self._emit(
            "plan.created",
            "plan",
            {
                "planner": "rules",
                "calls": [
                    {
                        "tool_id": call.tool_id,
                        "args_summary": _args_summary(call.args),
                        "source": "builtin",
                    }
                    for call in plan.calls
                ],
            },
        )
        trace = _RuntimeToolTrace(self)
        return await self.tool_executor.execute(plan.calls, catalog, ctx, trace=trace)

    async def compose(self, state: ChatState) -> dict[str, Any]:
        assert self._understanding is not None and self._bundle is not None
        self._emit("answer.started", "answer", {"template": True})
        results = state.get("results") or []
        parsed = [_result_from_dict(item) for item in results]
        template = compose(
            question=self.question,
            understanding=self._understanding,
            results=parsed,
            context=self._bundle,
        )
        self._template = template
        return {"answer": to_jsonable(template), "status": template.status.value}

    def route_after_compose(self, state: ChatState) -> str:
        if not self.llm_enabled or self._understanding is None:
            return "validate"
        if self._understanding.needs_clarification:
            return "validate"
        return "answer"

    async def answer(self, state: ChatState) -> dict[str, Any]:
        assert self._understanding is not None and self._template is not None
        evidence = {
            "question": self.question,
            "resolved_query": self._understanding.resolved_query,
            "intents": [item.value for item in self._understanding.intents],
            "status": self._template.status.value,
            "results": _compact_results_for_llm(state.get("results") or []),
            "citations": [to_jsonable(item) for item in self._citations],
        }
        repair = state.get("validation_errors") or []
        user = json.dumps(evidence, ensure_ascii=False)
        if repair:
            user += "\n上次输出未通过校验：" + "; ".join(repair) + "\n请修正后重试。"
        try:
            result = await self._complete_with_retry(
                purpose="answer",
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=self.settings.llm_max_output_tokens,
                prompt_version="chat.answer.v1",
            )
            parsed = _parse_answer(result.content)
            claims = tuple(
                Claim(
                    claim_id=str(item.get("claim_id") or f"k{index + 1}"),
                    text=str(item.get("text") or ""),
                    citation_ids=tuple(item.get("citation_ids") or ()),
                )
                for index, item in enumerate(parsed.get("claims") or [])
            )
            answer_markdown = sanitize(str(parsed.get("answer_markdown") or ""))
            if not answer_markdown.strip() and claims:
                # 小模型偶尔只填 claims 不填 answer_markdown；仅在有实质 claim 时兜底。
                useful = [claim for claim in claims if claim.text.strip() != self.question.strip()]
                if useful:
                    number_of = {
                        citation.citation_id: citation.number for citation in self._citations
                    }
                    answer_markdown = "\n".join(
                        claim.text.strip()
                        + "".join(
                            f"[[{number_of[cid]}]]"
                            for cid in claim.citation_ids
                            if cid in number_of
                        )
                        for claim in useful
                        if claim.text.strip()
                    )
            draft = AnswerDraft(
                answer_markdown=answer_markdown,
                claims=claims,
                used_citations=tuple(parsed.get("used_citations") or ()),
                confidence=cast(
                    Literal["high", "medium", "low"],
                    parsed.get("confidence")
                    if parsed.get("confidence") in {"high", "medium", "low"}
                    else "medium",
                ),
                followups=tuple(parsed.get("followups") or ()),
                status=self._template.status,
                fallback=False,
                llm_used=True,
                context_usage=self._bundle.usage if self._bundle else None,
            )
            template_text = self._template.answer_markdown
            if (
                template_text
                and re.search(r"\d", template_text)
                and not re.search(r"\d", draft.answer_markdown)
            ):
                # 小模型常把数字替换成引用标记；保留模板数字，再接 LLM 解读。
                draft = replace(
                    draft,
                    answer_markdown=template_text + "\n\n" + draft.answer_markdown,
                    claims=self._template.claims + draft.claims,
                )
            self._llm_used = True
            return {"answer": to_jsonable(draft), "status": draft.status.value}
        except LlmError as exc:
            self._degrade_reason = exc.reason
            draft = self._fallback_draft(exc.reason)
            return {
                "answer": to_jsonable(draft),
                "degrade_reason": exc.reason.value,
                "status": ChatStatus.DEGRADED.value,
            }
        except (json.JSONDecodeError, ValueError) as exc:
            log.error(
                "chat.llm.bad_response",
                purpose="answer",
                model=self.llm.model,
                error=str(exc)[:300],
            )
            self._degrade_reason = DegradeReason.LLM_BAD_RESPONSE
            draft = self._fallback_draft(DegradeReason.LLM_BAD_RESPONSE)
            return {
                "answer": to_jsonable(draft),
                "degrade_reason": DegradeReason.LLM_BAD_RESPONSE.value,
                "validation_errors": [str(exc)[:200]],
                "status": ChatStatus.DEGRADED.value,
            }

    async def validate(self, state: ChatState) -> dict[str, Any]:
        self._emit("validation.started", "validate", {"retry": int(state.get("retry_count") or 0)})
        answer = state.get("answer") or {}
        text = str(answer.get("answer_markdown") or "")
        allowed = _allowed_numbers(state.get("results") or [])
        if self._understanding and self._understanding.period and self._understanding.period.value:
            allowed.add(self._understanding.period.value)
        claims = answer.get("claims") or []
        result = validate(
            answer=text,
            claims=claims,
            citations=self._citations,
            allow_tokens=allowed,
        )
        template_text = str(self._template.answer_markdown) if self._template else ""
        numeric_answer = re.sub(r"\[\[[^\]]+\]\]", "", text)
        warnings = list(result.warnings)
        if re.search(r"\d", template_text) and not re.search(r"\d", numeric_answer):
            warnings.append("missing_numbers")
        if result.ok:
            if warnings:
                log.warning(
                    "chat.llm.validation_warning",
                    purpose="answer",
                    model=self.llm.model,
                    warnings=warnings[:8],
                )
            return {"status": answer.get("status") or ChatStatus.OK.value}
        retries = int(state.get("retry_count") or 0)
        can_retry = (
            retries == 0
            and self.llm_enabled
            and self._understanding is not None
            and not self._understanding.needs_clarification
        )
        if can_retry:
            self._emit(
                "validation.failed",
                "validate",
                {"errors": result.errors[:8], "retry_count": retries, "will_retry": True},
            )
            log.error(
                "chat.llm.validation_failed",
                purpose="answer",
                model=self.llm.model,
                errors=result.errors[:8],
                retry_count=retries,
                will_retry=True,
            )
            return {"validation_errors": result.errors, "retry_count": 1}
        self._emit(
            "validation.failed",
            "validate",
            {"errors": result.errors[:8], "retry_count": retries, "will_retry": False},
        )
        log.error(
            "chat.llm.validation_failed",
            purpose="answer",
            model=self.llm.model,
            errors=result.errors[:8],
            warnings=warnings[:8],
            retry_count=retries,
            will_retry=False,
        )
        return {
            "validation_errors": result.errors,
            "retry_count": retries + 1,
            "status": ChatStatus.DEGRADED.value,
            "degrade_reason": DegradeReason.LLM_BAD_RESPONSE.value,
        }

    def route_after_validate(self, state: ChatState) -> str:
        errors = state.get("validation_errors") or []
        if not errors:
            return "persist"
        answer = state.get("answer") or {}
        if (
            self.llm_enabled
            and int(state.get("retry_count") or 0) == 1
            and not answer.get("fallback", False)
        ):
            return "retry"
        return "fallback"

    def _fallback_draft(self, reason: DegradeReason) -> AnswerDraft:
        template = self._template
        if template is None:
            return AnswerDraft(
                answer_markdown="智能分析服务暂时不可用；你可以用固定问题查看已有数据。",
                status=ChatStatus.DEGRADED,
                degrade_reason=reason,
                degrade_message=_degrade_message(reason),
                what_to_do="稍后重试，或缩小时间范围。",
                retryable=True,
                fallback=True,
                context_usage=self._bundle.usage if self._bundle else None,
            )
        return AnswerDraft(
            answer_markdown=template.answer_markdown,
            claims=template.claims,
            used_citations=template.used_citations,
            confidence=template.confidence,
            followups=template.followups,
            status=ChatStatus.DEGRADED,
            degrade_reason=reason,
            degrade_message=_degrade_message(reason),
            what_to_do="稍后重试；金额与引用仍来自本地结构化数据。",
            retryable=True,
            fallback=True,
            llm_used=self._llm_used,
            context_usage=self._bundle.usage if self._bundle else None,
        )

    async def fallback(self, state: ChatState) -> dict[str, Any]:
        template = self._template
        reason = self._degrade_reason or DegradeReason.LLM_BAD_RESPONSE
        self._emit(
            "fallback.triggered",
            "validate",
            {"degrade_reason": reason.value, "message": _degrade_message(reason)},
        )
        if template is None:
            draft = AnswerDraft(
                answer_markdown="智能分析服务暂时不可用；你可以用固定问题查看已有数据。",
                status=ChatStatus.DEGRADED,
                degrade_reason=reason,
                degrade_message="模型或数据处理失败，已返回降级提示。",
                what_to_do="稍后重试，或缩小时间范围。",
                retryable=True,
                fallback=True,
                context_usage=self._bundle.usage if self._bundle else None,
            )
        else:
            draft = AnswerDraft(
                answer_markdown=template.answer_markdown,
                claims=template.claims,
                used_citations=template.used_citations,
                confidence=template.confidence,
                followups=template.followups,
                status=ChatStatus.DEGRADED,
                degrade_reason=reason,
                degrade_message=_degrade_message(reason),
                what_to_do="稍后重试；金额与引用仍来自本地结构化数据。",
                retryable=True,
                fallback=True,
                llm_used=self._llm_used,
                context_usage=self._bundle.usage if self._bundle else None,
            )
        return {
            "answer": to_jsonable(draft),
            "status": draft.status.value,
            "degrade_reason": reason.value,
        }

    async def persist(self, state: ChatState) -> dict[str, Any]:
        for log in self._tool_logs:
            await self.repo.record_tool_call(
                user_id=self.user_id,
                turn_id=self.turn_id,
                **log,
            )
        for log in self._llm_logs:
            await self.repo.record_llm_call(
                user_id=self.user_id,
                turn_id=self.turn_id,
                **log,
            )
        answer = state.get("answer") or {}
        text = str(answer.get("answer_markdown") or "")
        status = str(answer.get("status") or state.get("status") or ChatStatus.OK.value)
        degrade = answer.get("degrade_reason") or state.get("degrade_reason")
        latency = int((time.monotonic() - self._started) * 1000)
        self._emit(
            "turn.failed" if status == ChatStatus.ERROR.value else "turn.completed",
            "done",
            {
                "status": status,
                "total_ms": latency,
                "steps": self._step,
                "tool_calls": len(self._tool_logs),
                "llm_calls": len(self._llm_logs),
            },
        )
        append_event = getattr(self.repo, "append_event", None)
        if append_event is not None:
            for event in self._events:
                await append_event(
                    user_id=self.user_id,
                    turn_id=self.turn_id,
                    seq=event["seq"],
                    kind=event["kind"],
                    phase=event["phase"],
                    payload=event["payload"],
                )
        message = await self.repo.append_message(
            user_id=self.user_id,
            conversation_id=str(self.conversation["id"]),
            turn_id=self.turn_id,
            role="assistant",
            content=text,
            content_json=answer,
            status=status,
        )
        self.assistant_message_id = str(message["id"])
        await self.repo.update_turn(
            self.user_id,
            self.turn_id,
            assistant_message_id=message["id"],
            resolved_query=self._understanding.resolved_query if self._understanding else None,
            understanding=to_jsonable(self._understanding) if self._understanding else None,
            context_manifest=to_jsonable(self._bundle.usage) if self._bundle else None,
            status=status,
            degrade_reason=degrade,
            llm_used=bool(answer.get("llm_used") or self._llm_used),
            fallback=bool(answer.get("fallback")),
            latency_ms=latency,
        )
        await self.repo.increment_turn_count(user_id=self.user_id)
        if self._understanding:
            focus = _next_focus(self._bundle.focus if self._bundle else {}, self._understanding)
            if focus:
                await self.repo.update_focus(user_id=self.user_id, focus=focus)
        return {"status": status}

    # ── helpers ────────────────────────────────────────────
    def _emit(self, kind: str, phase: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.settings.chat_timeline_enabled:
            return {}
        self._event_seq += 1
        event = {
            "seq": self._event_seq,
            "kind": kind,
            "phase": phase,
            "turn_id": self.turn_id,
            "at": self.clock.now().isoformat(),
            "payload": to_jsonable(dict(payload)),
        }
        self._events.append(event)
        if self._on_event is not None:
            self._on_event(event)
        return event

    def _emit_understanding_completed(self, understanding: Understanding) -> None:
        period = understanding.period.value if understanding.period else None
        self._emit(
            "understanding.completed",
            "understand",
            {
                "intents": [item.value for item in understanding.intents],
                "period": period,
                "confidence": understanding.confidence,
                "needs_clarification": understanding.needs_clarification,
                "ambiguities": list(understanding.ambiguities),
            },
        )

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def timeline_summary(self) -> dict[str, Any]:
        return {
            "events": len(self._events),
            "steps": self._step,
            "tool_calls": len(self._tool_logs),
            "total_ms": int((time.monotonic() - self._started) * 1000),
            "complete": True,
        }

    @staticmethod
    def _retryable(exc: LlmError) -> bool:
        """超时、429、5xx、坏响应等都重试；鉴权/未配置/内容过滤不重试。"""
        return exc.reason not in {
            DegradeReason.LLM_NOT_CONFIGURED,
            DegradeReason.LLM_AUTH_FAILED,
            DegradeReason.LLM_CONTENT_FILTER,
        }

    async def _complete_with_retry(
        self,
        *,
        purpose: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        prompt_version: str,
    ) -> LlmResult:
        """统一 LLM 重试：每次失败落 chat_llm_call，成功后落成功记录。"""
        max_retries = max(0, int(self.settings.llm_retry_on_429))
        self._emit(
            "model.thinking.started",
            "think",
            {"purpose": purpose, "model": self.llm.model},
        )
        thinking_started = time.monotonic()
        for attempt in range(max_retries + 1):
            self._llm_seq += 1
            try:
                result = await self.llm.complete_messages(
                    purpose=purpose,
                    messages=messages,
                    max_tokens=max_tokens,
                    json_mode=True,
                )
            except LlmError as exc:
                self._record_llm_error(purpose, exc, prompt_version=prompt_version)
                self._emit(
                    "model.thinking.completed",
                    "think",
                    {
                        "purpose": purpose,
                        "status": "error",
                        "error_code": exc.reason.value,
                        "latency_ms": int((time.monotonic() - thinking_started) * 1000),
                    },
                )
                if not self._retryable(exc) or attempt >= max_retries:
                    raise
                self._emit(
                    "model.thinking.started",
                    "think",
                    {"purpose": purpose, "model": self.llm.model, "retry": True},
                )
                thinking_started = time.monotonic()
                delay = self.settings.llm_retry_backoff_seconds * (2**attempt)
                log.warning(
                    "chat.llm.retry",
                    purpose=purpose,
                    attempt=attempt + 1,
                    max_attempts=max_retries,
                    error_code=exc.reason.value,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
                continue
            self._record_llm(purpose, result, prompt_version=prompt_version, status="ok")
            self._emit(
                "model.thinking.completed",
                "think",
                {
                    "purpose": purpose,
                    "status": "ok",
                    "latency_ms": int((time.monotonic() - thinking_started) * 1000),
                    "tokens": result.total_tokens,
                },
            )
            return result
        raise AssertionError("unreachable")

    # ── helpers ────────────────────────────────────────────
    async def _compress(self, summary: str, old: Sequence[dict[str, str]]) -> str:
        self._llm_seq += 1
        payload = {"summary": summary, "turns": list(old)}
        result = await self.llm.complete_messages(
            purpose="compression",
            messages=[
                {"role": "system", "content": COMPRESS_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=2048,
            json_mode=True,
        )
        self._record_llm("compression", result, prompt_version="chat.compress.v1", status="ok")
        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError:
            return str(result.content)[:2000]
        return str(parsed.get("summary") or "")[:2000]

    def _record_tool(
        self, tool: str | ToolName, args: Mapping[str, Any], result: ToolResult, latency: int
    ) -> None:
        # 先在内存留痕，graph 结束后由 persist 统一短事务落库，避免 LLM 等待期间持有长事务。
        self._tool_logs = getattr(self, "_tool_logs", [])
        self._tool_logs.append(
            {
                "seq": self._tool_seq,
                "tool_name": getattr(tool, "value", str(tool)),
                "args": to_jsonable(args),
                "result": to_jsonable(result),
                "status": result.status.value,
                "error_code": result.error_code,
                "row_count": result.row_count,
                "result_bytes": len(
                    json.dumps(to_jsonable(result), ensure_ascii=False).encode("utf-8")
                ),
                "latency_ms": latency,
            }
        )

    def _record_llm(self, purpose: str, result: Any, *, prompt_version: str, status: str) -> None:
        self._llm_logs = getattr(self, "_llm_logs", [])
        self._llm_logs.append(
            {
                "seq": self._llm_seq,
                "purpose": purpose,
                "provider": result.provider,
                "model": result.model,
                "prompt_version": prompt_version,
                "request": to_jsonable(result.raw_request),
                "response": to_jsonable(result.raw_response),
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "latency_ms": result.latency_ms or 0,
                "status": status,
                "error_code": None,
            }
        )

    def _record_llm_error(self, purpose: str, exc: LlmError, *, prompt_version: str) -> None:
        self._llm_logs = getattr(self, "_llm_logs", [])
        self._llm_logs.append(
            {
                "seq": self._llm_seq,
                "purpose": purpose,
                "provider": self.llm.provider,
                "model": self.llm.model,
                "prompt_version": prompt_version,
                "request": to_jsonable(exc.raw_request),
                "response": to_jsonable(exc.raw_response),
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "latency_ms": 0,
                "status": "error",
                "error_code": exc.reason.value,
            }
        )

    @property
    def understanding(self) -> Understanding | None:
        return self._understanding

    @property
    def citations(self) -> list[Citation]:
        return list(self._citations)

    @property
    def context_usage(self) -> ContextUsage | None:
        return self._bundle.usage if self._bundle else None

    def tool_logs(self) -> list[dict[str, Any]]:
        return list(getattr(self, "_tool_logs", []))

    def llm_logs(self) -> list[dict[str, Any]]:
        return list(getattr(self, "_llm_logs", []))


class _RuntimeToolTrace(ToolTraceSink):
    """把执行器留痕接回 ChatRuntime 的内存日志；事件在 P0-4 再接 SSE。"""

    def __init__(self, runtime: ChatRuntime) -> None:
        self.runtime = runtime

    async def record_tool_call(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> None:
        self.runtime._tool_seq += 1
        self.runtime._record_tool(result.tool, call.args, result, result.latency_ms or 0)

    async def emit(self, event: Mapping[str, Any]) -> None:
        kind = str(event.get("kind") or "tool.call")
        payload = {key: value for key, value in event.items() if key != "kind"}
        self.runtime._emit(kind, "execute", payload)


def _args_summary(args: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "period",
        "security",
        "round_id",
        "metric_id",
        "taxonomy",
        "sort",
        "sort_dir",
        "limit",
        "flag",
    }
    return {key: value for key, value in args.items() if key in allowed}


def _tools_for(understanding: Understanding) -> list[tuple[ToolName, dict[str, Any]]]:
    period = (
        understanding.period.value if understanding.period and understanding.period.value else None
    )
    args: dict[str, Any] = {"period": period} if period else {}
    security = next(
        (item.resolved_id for item in understanding.entities if item.type == "security"), None
    )
    round_id = next(
        (item.resolved_id for item in understanding.entities if item.type == "round"), None
    )
    metric = next(
        (item.resolved_id for item in understanding.entities if item.type == "metric"), None
    )
    if security:
        args["security"] = security
    tools: list[tuple[ToolName, dict[str, Any]]] = []
    for intent in understanding.intents[:2]:
        if intent in {Intent.ASSETS, Intent.PORTFOLIO}:
            tools.append((ToolName.ASSETS_QUERY, {**args, "limit": 1000}))
            tools.append((ToolName.PORTFOLIO_SNAPSHOT, {}))
        elif intent is Intent.PERFORMANCE:
            tools.append((ToolName.PERFORMANCE_METRICS, {**args}))
        elif intent is Intent.ATTRIBUTION:
            tools.append((ToolName.ATTRIBUTION_SUMMARY, {**args}))
        elif intent is Intent.ROUNDS:
            round_args: dict[str, Any] = {**args, "limit": 1000}
            if any(
                word in understanding.resolved_query for word in ("亏损最多", "亏得最多", "最亏")
            ):
                round_args.update({"sort": "pnl", "sort_dir": "asc"})
            elif any(
                word in understanding.resolved_query for word in ("盈利最多", "赚得最多", "最赚")
            ):
                round_args.update({"sort": "pnl", "sort_dir": "desc"})
            tools.append((ToolName.ROUNDS_QUERY, round_args))
        elif intent is Intent.ROUND_DETAIL:
            if round_id:
                tools.append((ToolName.ROUND_DETAIL, {"round_id": round_id}))
            else:
                tools.append((ToolName.ROUNDS_QUERY, {**args, "limit": 1000}))
        elif intent is Intent.TRADES:
            tools.append((ToolName.TRADES_QUERY, {**args, "limit": 1000}))
        elif intent is Intent.NAV:
            tools.append((ToolName.NAV_SUMMARY, {**args}))
        elif intent is Intent.SECTORS:
            tools.append((ToolName.SECTOR_EXPOSURE, {**args, "taxonomy": "industry"}))
        elif intent is Intent.METRIC_EXPLAIN:
            tools.append((ToolName.METRIC_EXPLAIN, {**args, "metric_id": metric or "win_rate"}))
        elif intent is Intent.DATA_QUALITY:
            tools.append((ToolName.DATA_QUALITY, {}))
        else:
            tools.append((ToolName.DATA_QUALITY, {}))
            tools.append((ToolName.PORTFOLIO_SNAPSHOT, {}))
    return _dedupe(tools)[:3]


def _dedupe(tools: list[tuple[ToolName, dict[str, Any]]]) -> list[tuple[ToolName, dict[str, Any]]]:
    seen: set[ToolName] = set()
    out: list[tuple[ToolName, dict[str, Any]]] = []
    for tool, args in tools:
        if tool in seen:
            continue
        seen.add(tool)
        out.append((tool, args))
    return out


def _number_citations(values: Sequence[Citation]) -> list[Citation]:
    return [
        Citation(
            citation_id=item.citation_id,
            label=item.label,
            value_display=item.value_display,
            source_endpoint=item.source_endpoint,
            source_params=item.source_params,
            definition_version=item.definition_version,
            data_as_of=item.data_as_of,
            number=index,
        )
        for index, item in enumerate(values, start=1)
    ]


def _result_from_dict(data: Mapping[str, Any]) -> ToolResult:
    return ToolResult(
        tool=ToolName(data["tool"]),
        status=ChatStatus(data["status"]),
        data=data.get("data") or {},
        citations=tuple(
            Citation(**{**item, "source_params": item.get("source_params") or {}})
            for item in data.get("citations") or []
        ),
        data_as_of=data.get("data_as_of"),
        definition_version=data.get("definition_version"),
        missing=(),
        truncated=bool(data.get("truncated")),
        row_count=int(data.get("row_count") or 0),
        latency_ms=data.get("latency_ms"),
        error_code=data.get("error_code"),
    )


class _ClaimIn(BaseModel):
    claim_id: str = ""
    text: str = ""
    citation_ids: list[str] = Field(default_factory=list)

    @field_validator("citation_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]


class _AnswerIn(BaseModel):
    answer_markdown: str = ""
    claims: list[_ClaimIn] = Field(default_factory=list)
    used_citations: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    followups: list[str] = Field(default_factory=list)

    @field_validator("answer_markdown", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("used_citations", "followups", mode="before")
    @classmethod
    def _strings(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]


def _parse_answer(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        from json_repair import repair_json

        parsed = json.loads(repair_json(content))
    if not isinstance(parsed, dict):
        raise ValueError("answer 不是 JSON object")
    return _AnswerIn.model_validate(parsed).model_dump()


def _compact_results_for_llm(
    results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """给 LLM 的证据包瘦身：保留行数据/引用，去掉长词表，最多 10 行。"""
    compacted: list[dict[str, Any]] = []
    for result in results:
        data = result.get("data")
        if not isinstance(data, dict):
            compacted.append(dict(result))
            continue
        slim = dict(data)
        slim.pop("flag_glossary", None)
        slim.pop("flag_config", None)
        items = slim.get("items")
        if isinstance(items, list) and len(items) > 10:
            slim["items"] = items[:10]
            slim["items_truncated_for_llm"] = True
        compacted.append({**result, "data": slim})
    return compacted


def _allowed_numbers(results: Sequence[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?%?", value):
                out.add(token.replace(",", "").removesuffix("%"))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out.add(str(value))

    walk(results)
    return out


def _next_focus(focus: Mapping[str, Any], understanding: Understanding) -> dict[str, Any]:
    out = dict(focus)
    # 清理旧版本可能写入的空证券/期间词焦点，避免污染下一轮给模型的 focus。
    for key in ("security_code", "security_name", "round_id", "metric_id"):
        if not out.get(key):
            out.pop(key, None)
    if out.get("security_name") in {"本月", "这个月", "上个月", "今年"} and not out.get(
        "security_code"
    ):
        out.pop("security_name", None)
    for entity in understanding.entities:
        if entity.type == "security" and (entity.resolved_id or entity.resolved_name):
            out["security_code"] = entity.resolved_id
            out["security_name"] = entity.resolved_name
        elif entity.type == "round" and entity.resolved_id:
            out["round_id"] = entity.resolved_id
        elif entity.type == "metric" and entity.resolved_id:
            out["metric_id"] = entity.resolved_id
    if understanding.period and understanding.period.value:
        out["period"] = understanding.period.value
    return out


def _degrade_message(reason: DegradeReason) -> str:
    return {
        DegradeReason.LLM_TIMEOUT: "模型响应超时，已返回结构化摘要。",
        DegradeReason.LLM_RATE_LIMITED: "免费模型额度/频率受限，已返回结构化摘要。",
        DegradeReason.LLM_AUTH_FAILED: "模型服务鉴权失败。",
        DegradeReason.LLM_BAD_RESPONSE: "模型输出格式异常，已回退结构化摘要。",
        DegradeReason.LLM_CONTENT_FILTER: "该问题被模型安全策略拦截，已回退结构化摘要。",
        DegradeReason.LLM_UNAVAILABLE: "智能分析服务暂时不可用。",
        DegradeReason.CONTEXT_OVERFLOW: "对话上下文过长，已压缩。",
    }.get(reason, "智能分析服务降级。")
