"""智能分析助手端点（API-077/078/112~115）。

单会话：一个用户只有一条 `chat_conversation`；工具/LLM 调用由 ChatRuntime
在 graph 结束后短事务落库，消息/轮次在请求事务内提交。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from stock_contracts.chat.enums import ToolName
from stock_contracts.errors import ApiError, ErrorCode
from stock_delivery.chat.graph import build_graph
from stock_delivery.chat.runtime import ChatRuntime, to_jsonable
from stock_platform.repositories.chat import ChatRepository
from stock_platform.tenant import TenantContext
from stock_review.application.chat.facade import ChatQueryFacade

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    ChatCitationOut,
    ChatClaimOut,
    ChatContextUsageOut,
    ChatConversationOut,
    ChatHistoryMessageOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatReplayIn,
    ChatReplayOut,
    ChatSessionOut,
    ChatTraceOut,
)

router = APIRouter(tags=["chat"], route_class=StrictQueryRoute)


@router.post("/chat/sessions", response_model=ChatSessionOut, operation_id="createChatSession")
async def create_session(
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> ChatSessionOut:
    """API-077：确保当前用户单会话存在（首次 201，已存在 200）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ChatRepository(session)
        conversation, created = await repo.get_or_create_conversation(
            user_id, window_turns=state.settings.llm_history_turns
        )
    response.status_code = 201 if created else 200
    return ChatSessionOut(
        conversation_id=str(conversation["id"]),
        created=created,
        title=str(conversation["title"]),
        window_turns=int(conversation["window_turns"]),
    )


@router.post(
    "/chat/sessions/{session_id}/messages",
    response_model=ChatMessageOut,
    operation_id="sendChatMessage",
)
async def send_message(
    session_id: str,
    payload: ChatMessageIn,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ChatMessageOut | StreamingResponse:
    """API-078：发消息；工具/LLM 过程落库。

    `Accept: text/event-stream` 时返回 SSE（meta/delta/done）；默认仍返回 JSON，
    供回放、测试和旧客户端使用。
    """
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    question = payload.message.strip()
    if not question:
        raise ApiError(ErrorCode.MESSAGE_TOO_LONG, detail="消息不能为空")

    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ChatRepository(session)
        conversation = await repo.get_conversation(user_id, for_update=True)
        if conversation is None or str(conversation["id"]) != session_id:
            raise ApiError(ErrorCode.NOT_FOUND, detail="对话不存在")

        allowed, retry_after = await state.limiter.hit(
            session,
            f"chat:{user_id}",
            limit=state.settings.chat_rpm,
            window_seconds=60,
        )
        if not allowed:
            raise ApiError(
                ErrorCode.RATE_LIMITED,
                detail="对话请求过于频繁",
                headers={"Retry-After": str(retry_after)},
            )
        daily_ok, daily_retry = await state.limiter.hit(
            session,
            f"chat-day:{user_id}",
            limit=state.settings.chat_daily_turns,
            window_seconds=86400,
        )
        if not daily_ok:
            raise ApiError(
                ErrorCode.QUOTA_EXCEEDED,
                detail="今日对话次数已达上限",
                headers={"Retry-After": str(daily_retry)},
            )

        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "message": question,
                    "context": payload.context.model_dump() if payload.context else None,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        cached = None
        if idempotency_key:
            if not 16 <= len(idempotency_key) <= 128:
                raise ApiError(ErrorCode.VALIDATION_ERROR, detail="Idempotency-Key 长度非法")
            cached = await state.idempotency.begin(
                session,
                user_id=user_id,
                endpoint="chat.message",
                key=idempotency_key,
                request_hash=request_hash,
            )
            if cached is not None:
                body = state.idempotency.payload_of(cached) or {}
                cached_result = ChatMessageOut.model_validate(body)
                return _stream_or_json(cached_result, request)

        turn = await repo.create_turn(
            user_id=user_id,
            conversation_id=str(conversation["id"]),
            question=question,
        )
        user_message = await repo.append_message(
            user_id=user_id,
            conversation_id=str(conversation["id"]),
            turn_id=str(turn["id"]),
            role="user",
            content=question,
            content_json={"context": payload.context.model_dump() if payload.context else None},
        )
        await repo.update_turn(user_id, str(turn["id"]), user_message_id=user_message["id"])

        facade = ChatQueryFacade(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=state.market_data,
            sector_data=state.sector_data,
            settings=state.settings,
        )
        runtime = ChatRuntime(
            repo=repo,
            data_port=facade,
            llm=state.llm,
            clock=state.clock,
            settings=state.settings,
            user_id=user_id,
            conversation=conversation,
            turn_id=str(turn["id"]),
            question=question,
            user_message_id=str(user_message["id"]),
        )
        graph = build_graph(runtime)
        try:
            final = await graph.ainvoke({"question": question})
        except Exception as exc:
            await repo.update_turn(
                user_id,
                str(turn["id"]),
                status="error",
                degrade_reason="TOOL_UNAVAILABLE",
                latency_ms=0,
            )
            raise ApiError(ErrorCode.LLM_UNAVAILABLE, detail=str(exc)[:200]) from exc

    result = _message_out(
        runtime=runtime,
        conversation_id=str(conversation["id"]),
        turn_id=str(turn["id"]),
        final=final,
    )
    if idempotency_key:
        async with state.db.session(user_id=user_id, begin=True) as idem_session:
            await state.idempotency.complete(
                idem_session,
                user_id=user_id,
                endpoint="chat.message",
                key=idempotency_key,
                status_code=200,
                body=result.model_dump(mode="json"),
            )
    return _stream_or_json(result, request)


@router.get(
    "/chat/conversation", response_model=ChatConversationOut, operation_id="getChatConversation"
)
async def get_conversation(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: Annotated[str | None, Query()] = None,
) -> ChatConversationOut:
    """API-112：单会话 + 分页消息。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ChatRepository(session)
        conversation = await repo.get_conversation(user_id)
        if conversation is None:
            raise ApiError(ErrorCode.NOT_FOUND, detail="尚无对话，请先创建")
        messages = await repo.list_messages(
            user_id, str(conversation["id"]), limit=limit + 1, before_id=before_id
        )
    has_more = len(messages) > limit
    if has_more:
        messages = messages[-limit:]
    next_cursor = str(messages[0]["id"]) if has_more else None
    return ChatConversationOut(
        conversation_id=str(conversation["id"]),
        title=str(conversation["title"]),
        summary=str(conversation["summary"]),
        turn_count=int(conversation["turn_count"]),
        window_turns=int(conversation["window_turns"]),
        next_cursor=next_cursor,
        messages=[
            ChatHistoryMessageOut(
                message_id=str(item["id"]),
                turn_id=item.get("turn_id"),
                role=item["role"],
                content=str(item["content"]),
                content_json=item.get("content_json"),
                status=str(item["status"]),
                created_at=item["created_at"],
            )
            for item in messages
        ],
    )


@router.get("/chat/turns/{turn_id}/trace", response_model=ChatTraceOut, operation_id="getChatTrace")
async def get_trace(
    turn_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> ChatTraceOut:
    """API-113：完整回放轨迹。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ChatRepository(session)
        trace = await repo.get_trace(user_id, turn_id)
        if trace is None:
            raise ApiError(ErrorCode.NOT_FOUND, detail="回合不存在")
        turn, tools, llms = trace
        answer: dict[str, Any] = {}
        if turn.get("assistant_message_id"):
            message = await repo.get_message(user_id, str(turn["assistant_message_id"]))
            if message is not None:
                answer = message.get("content_json") or {"answer": message.get("content")}
    return ChatTraceOut(turn=turn, tool_calls=tools, llm_calls=llms, answer=answer)


@router.post(
    "/chat/turns/{turn_id}/replay", response_model=ChatReplayOut, operation_id="replayChatTurn"
)
async def replay_turn(
    turn_id: str,
    payload: ChatReplayIn,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> ChatReplayOut:
    """API-114：回放（deterministic/llm/full）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        repo = ChatRepository(session)
        trace = await repo.get_trace(user_id, turn_id)
        if trace is None:
            raise ApiError(ErrorCode.NOT_FOUND, detail="回合不存在")
        _turn, tools, llms = trace
        diffs: list[dict[str, Any]] = []
        replayed: dict[str, Any] = {}
        if payload.mode in {"deterministic", "full"}:
            facade = ChatQueryFacade(
                session,
                user_id=user_id,
                clock=state.clock,
                market_data=state.market_data,
                sector_data=state.sector_data,
                settings=state.settings,
            )
            replay_tools = []
            for item in tools:
                try:
                    tool = ToolName(item["tool"])
                except ValueError:
                    continue
                result = await facade.invoke(tool, item.get("args") or {})
                value = to_jsonable(result)
                replay_tools.append(value)
                expected = json.dumps(
                    _strip_latency(item.get("result") or {}), sort_keys=True, ensure_ascii=False
                )
                actual = json.dumps(_strip_latency(value), sort_keys=True, ensure_ascii=False)
                if expected != actual:
                    diffs.append(
                        {
                            "kind": "tool_result",
                            "tool": item["tool"],
                            "seq": item["seq"],
                            "expected": item.get("result"),
                            "actual": value,
                        }
                    )
            replayed["tool_calls"] = replay_tools
        if payload.mode in {"llm", "full"}:
            answer_call = next(
                (item for item in reversed(llms) if item["purpose"] == "answer"), None
            )
            if answer_call is None:
                diffs.append({"kind": "llm_call", "error": "没有 answer LLM 调用"})
            else:
                try:
                    result = await state.llm.complete_messages(
                        purpose="answer",
                        messages=(answer_call.get("request") or {}).get("messages") or [],
                        max_tokens=state.settings.llm_max_output_tokens,
                        json_mode=True,
                    )
                    replayed["llm"] = {"content": result.content}
                    expected = str((answer_call.get("response") or {}).get("content") or "")
                    if result.content != expected:
                        diffs.append(
                            {
                                "kind": "llm_content",
                                "expected": expected,
                                "actual": result.content,
                            }
                        )
                except Exception as exc:
                    return ChatReplayOut(
                        mode=payload.mode,
                        match=False,
                        diffs=diffs,
                        replayed=replayed,
                        status="failed",
                        error=str(exc)[:200],
                    )
    return ChatReplayOut(mode=payload.mode, match=not diffs, diffs=diffs, replayed=replayed)


@router.delete("/chat/conversation", status_code=204, operation_id="deleteChatConversation")
async def delete_conversation(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> Response:
    """API-115：清空对话（软删消息，保留回放数据）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        await ChatRepository(session).reset_conversation(user_id=user_id)
    return Response(status_code=204)


def _wants_sse(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    return "text/event-stream" in accept


def _stream_or_json(result: ChatMessageOut, request: Request) -> ChatMessageOut | StreamingResponse:
    if not _wants_sse(request):
        return result
    return StreamingResponse(
        _sse_events(result),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_events(result: ChatMessageOut) -> AsyncIterator[str]:
    yield _sse_event(
        "meta",
        {
            "turn_id": result.turn_id,
            "conversation_id": result.conversation_id,
            "status": result.status,
            "llm_used": result.llm_used,
            "fallback": result.fallback,
            "degrade_reason": result.degrade_reason,
            "degrade_message": result.degrade_message,
        },
    )
    text = result.answer or ""
    for offset in range(0, len(text), 48):
        yield _sse_event("delta", {"text": text[offset : offset + 48]})
        # 让 ASGI 有机会按 chunk flush，前端才能看到增量而不是一帧全量。
        await asyncio.sleep(0.02)
    yield _sse_event("done", {"message": result.model_dump(mode="json")})


def _strip_latency(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_latency(item)
            for key, item in value.items()
            if key not in {"latency_ms", "created_at"}
        }
    if isinstance(value, list):
        return [_strip_latency(item) for item in value]
    return value


def _message_out(
    *,
    runtime: ChatRuntime,
    conversation_id: str,
    turn_id: str,
    final: dict[str, Any],
) -> ChatMessageOut:
    answer = final.get("answer") or {}
    citations = [
        ChatCitationOut(
            number=int(item.number or index),
            citation_id=item.citation_id,
            label=item.label,
            value=item.value_display,
            source_endpoint=item.source_endpoint,
            source_params=dict(item.source_params),
            definition_version=item.definition_version,
            data_as_of=item.data_as_of.isoformat() if item.data_as_of else None,
        )
        for index, item in enumerate(runtime.citations, start=1)
    ]
    claims = [
        ChatClaimOut(
            claim_id=str(item.get("claim_id") or f"k{index + 1}"),
            text=str(item.get("text") or ""),
            citation_ids=list(item.get("citation_ids") or []),
        )
        for index, item in enumerate(answer.get("claims") or [])
    ]
    usage = answer.get("context_usage") or (
        to_jsonable(runtime.context_usage) if runtime.context_usage else None
    )
    understanding = to_jsonable(runtime.understanding) if runtime.understanding else {}
    return ChatMessageOut(
        conversation_id=conversation_id,
        turn_id=turn_id,
        message_id=runtime.assistant_message_id or "",
        answer=str(answer.get("answer_markdown") or ""),
        citations=citations,
        claims=claims,
        status=str(answer.get("status") or final.get("status") or "ok"),
        degrade_reason=answer.get("degrade_reason") or final.get("degrade_reason"),
        degrade_message=answer.get("degrade_message"),
        what_to_do=answer.get("what_to_do"),
        retryable=bool(answer.get("retryable")),
        llm_used=bool(answer.get("llm_used")),
        fallback=bool(answer.get("fallback")),
        understanding=understanding,
        context_usage=ChatContextUsageOut(**usage) if isinstance(usage, dict) else None,
    )
