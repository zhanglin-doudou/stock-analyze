"""单轮 LangGraph 运行时：上下文 → 理解 → 工具 → 模板/LLM → 校验 → 持久化。"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import ValidationError
from stock_contracts.chat.dto import (
    AnswerDraft,
    Citation,
    Claim,
    ContextUsage,
    ToolResult,
    Understanding,
)
from stock_contracts.chat.enums import ChatStatus, DegradeReason, Intent, ToolName
from stock_contracts.chat.ports import ChatDataPort
from stock_contracts.clock import Clock
from stock_platform.config import Settings
from stock_platform.obs import get_logger
from stock_platform.repositories.chat import ChatRepository

from .composer import compose
from .context import ContextBundle, ContextManager
from .prompts import ANSWER_SYSTEM, COMPRESS_SYSTEM
from .provider import LlmError, LlmProvider
from .state import ChatState
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
    ) -> None:
        self.repo = repo
        self.data_port = data_port
        self.llm = llm
        self.clock = clock
        self.settings = settings
        self.user_id = user_id
        self.conversation = dict(conversation)
        self.turn_id = turn_id
        self.user_message_id = user_message_id
        self.question = question
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

    # ── nodes ──────────────────────────────────────────────
    async def load_context(self, state: ChatState) -> dict[str, Any]:
        bundle = await self.context_manager.build(
            self.user_id,
            self.conversation,
            self.question,
            compressor=self._compress if self.settings.llm_enabled else None,
            exclude_message_id=self.user_message_id,
        )
        self._bundle = bundle
        return {"context": to_jsonable(bundle.usage)}

    async def understand(self, state: ChatState) -> dict[str, Any]:
        assert self._bundle is not None
        if not self.settings.llm_enabled:
            understanding = self.understanding_service.fallback_understanding(
                self.question, self._bundle.focus
            )
            self._understanding = understanding
            return {"understanding": to_jsonable(understanding)}

        self._llm_seq += 1
        messages = self.context_manager.understanding_messages(self._bundle, self.question)
        try:
            result = await self.llm.complete_messages(
                purpose="understanding",
                messages=messages,
                max_tokens=self.settings.llm_understanding_max_tokens,
                json_mode=True,
            )
            # 先记录成功调用：即使模型输出形状不合法，trace 也应能看到这次调用。
            self._record_llm(
                "understanding",
                result,
                prompt_version="chat.understand.v1",
                status="ok",
            )
            self._llm_used = True
            understanding = self.understanding_service.parse(
                result.content, question=self.question, focus=self._bundle.focus
            )
            self._understanding = understanding
            return {"understanding": to_jsonable(understanding)}
        except LlmError as exc:
            self._record_llm_error("understanding", exc, prompt_version="chat.understand.v1")
            self._degrade_reason = exc.reason
            understanding = self.understanding_service.fallback_understanding(
                self.question, self._bundle.focus
            )
            self._understanding = understanding
            return {
                "understanding": to_jsonable(understanding),
                "degrade_reason": exc.reason.value,
            }
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            log.error(
                "chat.llm.bad_response",
                purpose="understanding",
                model=self.settings.glm_model,
                error=str(exc)[:300],
            )
            self._degrade_reason = DegradeReason.LLM_BAD_RESPONSE
            understanding = self.understanding_service.fallback_understanding(
                self.question, self._bundle.focus
            )
            self._understanding = understanding
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
        tools = _tools_for(self._understanding)
        results: list[ToolResult] = []
        for tool, args in tools:
            self._tool_seq += 1
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
            results.append(result)
        self._citations = _number_citations(
            [citation for result in results for citation in result.citations]
        )
        return {"results": [to_jsonable(result) for result in results]}

    async def compose(self, state: ChatState) -> dict[str, Any]:
        assert self._understanding is not None and self._bundle is not None
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
        if not self.settings.llm_enabled or self._understanding is None:
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
        self._llm_seq += 1
        try:
            result = await self.llm.complete_messages(
                purpose="answer",
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=self.settings.llm_max_output_tokens,
                json_mode=True,
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
                confidence=parsed.get("confidence") or "medium",
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
            self._record_llm("answer", result, prompt_version="chat.answer.v1", status="ok")
            self._llm_used = True
            return {"answer": to_jsonable(draft), "status": draft.status.value}
        except LlmError as exc:
            self._record_llm_error("answer", exc, prompt_version="chat.answer.v1")
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
                model=self.settings.glm_model,
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
        if re.search(r"\d", template_text) and not re.search(r"\d", numeric_answer):
            result = type(result)(
                ok=False,
                errors=[*result.errors, "missing_numbers"],
            )
        if result.ok:
            return {"status": answer.get("status") or ChatStatus.OK.value}
        retries = int(state.get("retry_count") or 0)
        can_retry = (
            retries == 0
            and self.settings.llm_enabled
            and self._understanding is not None
            and not self._understanding.needs_clarification
        )
        if can_retry:
            log.error(
                "chat.llm.validation_failed",
                purpose="answer",
                model=self.settings.glm_model,
                errors=result.errors[:8],
                retry_count=retries,
                will_retry=True,
            )
            return {"validation_errors": result.errors, "retry_count": 1}
        log.error(
            "chat.llm.validation_failed",
            purpose="answer",
            model=self.settings.glm_model,
            errors=result.errors[:8],
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
            self.settings.llm_enabled
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
        latency = int((time.monotonic() - self._started) * 1000)
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
        self, tool: ToolName, args: Mapping[str, Any], result: ToolResult, latency: int
    ) -> None:
        # 先在内存留痕，graph 结束后由 persist 统一短事务落库，避免 LLM 等待期间持有长事务。
        self._tool_logs = getattr(self, "_tool_logs", [])
        self._tool_logs.append(
            {
                "seq": self._tool_seq,
                "tool_name": tool.value,
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
                "provider": self.settings.llm_provider,
                "model": self.settings.glm_model,
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


def _parse_answer(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        from json_repair import repair_json

        parsed = json.loads(repair_json(content))
    if not isinstance(parsed, dict):
        raise ValueError("answer 不是 JSON object")
    return parsed


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
    for entity in understanding.entities:
        if entity.type == "security":
            out["security_code"] = entity.resolved_id
            out["security_name"] = entity.resolved_name
        elif entity.type == "round":
            out["round_id"] = entity.resolved_id
        elif entity.type == "metric":
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
