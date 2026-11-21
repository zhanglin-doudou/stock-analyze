"""智能分析助手的会话/消息/轮次/调用仓储（单会话）。

所有查询显式收 `user_id`；RLS 是第二道防线。工具/LLM 调用原样落 JSONB，
供 `GET /chat/turns/{id}/trace` 与回放使用。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform.ids import new_id
from stock_platform.models import (
    ChatConversation,
    ChatLlmCall,
    ChatMessage,
    ChatToolCall,
    ChatTurn,
    ChatTurnEvent,
)


def _conversation_dict(row: ChatConversation) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": str(row.user_id),
        "title": row.title,
        "summary": row.summary,
        "summary_tokens": row.summary_tokens,
        "summary_message_id": row.summary_message_id,
        "summary_version": row.summary_version,
        "focus": row.focus or {},
        "window_turns": row.window_turns,
        "turn_count": row.turn_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _message_dict(row: ChatMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "turn_id": row.turn_id,
        "role": row.role,
        "content": row.content,
        "content_json": row.content_json,
        "token_count": row.token_count,
        "status": row.status,
        "created_at": row.created_at,
    }


def _event_dict(row: ChatTurnEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "turn_id": row.turn_id,
        "seq": row.seq,
        "kind": row.kind,
        "phase": row.phase,
        "payload": row.payload or {},
        "created_at": row.created_at,
    }


def _turn_dict(row: ChatTurn) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "user_message_id": row.user_message_id,
        "assistant_message_id": row.assistant_message_id,
        "question": row.question,
        "resolved_query": row.resolved_query,
        "understanding": row.understanding,
        "context_manifest": row.context_manifest,
        "status": row.status,
        "degrade_reason": row.degrade_reason,
        "llm_used": row.llm_used,
        "fallback": row.fallback,
        "latency_ms": row.latency_ms,
        "created_at": row.created_at,
    }


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_conversation(
        self, user_id: uuid.UUID, *, window_turns: int = 10
    ) -> tuple[dict[str, Any], bool]:
        row = await self.session.scalar(
            select(ChatConversation).where(
                ChatConversation.user_id == user_id,
                ChatConversation.deleted_at.is_(None),
            )
        )
        if row is not None:
            return _conversation_dict(row), False
        row = ChatConversation(
            id=new_id("cnv"),
            user_id=user_id,
            window_turns=window_turns,
        )
        self.session.add(row)
        await self.session.flush()
        return _conversation_dict(row), True

    async def get_conversation(
        self, user_id: uuid.UUID, *, for_update: bool = False
    ) -> dict[str, Any] | None:
        stmt = select(ChatConversation).where(
            ChatConversation.user_id == user_id,
            ChatConversation.deleted_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        return None if row is None else _conversation_dict(row)

    async def list_messages(
        self,
        user_id: uuid.UUID,
        conversation_id: str,
        *,
        limit: int = 50,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.deleted_at.is_(None),
            )
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        if before_id:
            pivot = (
                await self.session.execute(
                    select(ChatMessage.created_at, ChatMessage.id).where(
                        ChatMessage.id == before_id, ChatMessage.user_id == user_id
                    )
                )
            ).first()
            if pivot is not None:
                # created_at 在同一轮内可能完全相同，必须用 id 做稳定游标。
                stmt = stmt.where(
                    or_(
                        ChatMessage.created_at < pivot.created_at,
                        and_(
                            ChatMessage.created_at == pivot.created_at,
                            ChatMessage.id < pivot.id,
                        ),
                    )
                )
        rows = list(await self.session.scalars(stmt))
        return [_message_dict(row) for row in reversed(rows)]

    async def get_message(self, user_id: uuid.UUID, message_id: str) -> dict[str, Any] | None:
        row = await self.session.scalar(
            select(ChatMessage).where(
                ChatMessage.user_id == user_id,
                ChatMessage.id == message_id,
                ChatMessage.deleted_at.is_(None),
            )
        )
        return None if row is None else _message_dict(row)

    async def recent_turns(
        self, user_id: uuid.UUID, conversation_id: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        rows = list(
            await self.session.scalars(
                select(ChatTurn)
                .where(
                    ChatTurn.user_id == user_id,
                    ChatTurn.conversation_id == conversation_id,
                )
                .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
                .limit(limit)
            )
        )
        return [_turn_dict(row) for row in reversed(rows)]

    async def create_turn(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: str,
        question: str,
        user_message_id: str | None = None,
        context_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        turn = ChatTurn(
            id=new_id("trn"),
            conversation_id=conversation_id,
            user_id=user_id,
            user_message_id=user_message_id,
            question=question,
            context_manifest=context_manifest or {},
            status="ok",
        )
        self.session.add(turn)
        await self.session.flush()
        return _turn_dict(turn)

    async def append_message(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: str,
        turn_id: str,
        role: str,
        content: str,
        content_json: dict[str, Any] | None = None,
        token_count: int = 0,
        status: str = "ok",
    ) -> dict[str, Any]:
        row = ChatMessage(
            id=new_id("msg"),
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            role=role,
            content=content,
            content_json=content_json,
            token_count=token_count,
            status=status,
        )
        self.session.add(row)
        await self.session.flush()
        return _message_dict(row)

    async def update_turn(
        self,
        user_id: uuid.UUID,
        turn_id: str,
        **fields: Any,
    ) -> None:
        allowed = {
            "resolved_query",
            "understanding",
            "context_manifest",
            "status",
            "degrade_reason",
            "llm_used",
            "fallback",
            "latency_ms",
            "assistant_message_id",
            "user_message_id",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        await self.session.execute(
            update(ChatTurn)
            .where(ChatTurn.user_id == user_id, ChatTurn.id == turn_id)
            .values(**values)
        )

    async def record_tool_call(
        self,
        *,
        user_id: uuid.UUID,
        turn_id: str,
        seq: int,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        status: str,
        error_code: str | None = None,
        row_count: int = 0,
        result_bytes: int = 0,
        latency_ms: int = 0,
    ) -> str:
        row = ChatToolCall(
            id=new_id("tcl"),
            turn_id=turn_id,
            user_id=user_id,
            seq=seq,
            tool_name=tool_name,
            args=args,
            result=result,
            status=status,
            error_code=error_code,
            row_count=row_count,
            result_bytes=result_bytes,
            latency_ms=latency_ms,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def record_llm_call(
        self,
        *,
        user_id: uuid.UUID,
        turn_id: str,
        seq: int,
        purpose: str,
        provider: str,
        model: str,
        prompt_version: str | None,
        request: dict[str, Any],
        response: dict[str, Any],
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        latency_ms: int,
        status: str,
        error_code: str | None = None,
    ) -> str:
        row = ChatLlmCall(
            id=new_id("llm"),
            turn_id=turn_id,
            user_id=user_id,
            seq=seq,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            request=request,
            response=response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def append_event(
        self,
        *,
        user_id: uuid.UUID,
        turn_id: str,
        seq: int,
        kind: str,
        phase: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        row = ChatTurnEvent(
            id=new_id("evt"),
            turn_id=turn_id,
            user_id=user_id,
            seq=seq,
            kind=kind,
            phase=phase,
            payload=payload,
        )
        self.session.add(row)
        await self.session.flush()
        return _event_dict(row)

    async def list_events(
        self,
        user_id: uuid.UUID,
        turn_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = list(
            await self.session.scalars(
                select(ChatTurnEvent)
                .where(
                    ChatTurnEvent.user_id == user_id,
                    ChatTurnEvent.turn_id == turn_id,
                    ChatTurnEvent.seq > after_seq,
                )
                .order_by(ChatTurnEvent.seq)
                .limit(limit)
            )
        )
        return [_event_dict(row) for row in rows]

    async def delete_events_before(self, before: datetime) -> int:
        result = await self.session.execute(
            delete(ChatTurnEvent).where(ChatTurnEvent.created_at < before)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def get_trace(
        self, user_id: uuid.UUID, turn_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
        turn = await self.session.scalar(
            select(ChatTurn).where(ChatTurn.user_id == user_id, ChatTurn.id == turn_id)
        )
        if turn is None:
            return None
        tools = list(
            await self.session.scalars(
                select(ChatToolCall)
                .where(ChatToolCall.user_id == user_id, ChatToolCall.turn_id == turn_id)
                .order_by(ChatToolCall.seq)
            )
        )
        llms = list(
            await self.session.scalars(
                select(ChatLlmCall)
                .where(ChatLlmCall.user_id == user_id, ChatLlmCall.turn_id == turn_id)
                .order_by(ChatLlmCall.seq)
            )
        )
        return (
            _turn_dict(turn),
            [
                {
                    "id": row.id,
                    "seq": row.seq,
                    "tool": row.tool_name,
                    "args": row.args,
                    "result": row.result,
                    "status": row.status,
                    "error_code": row.error_code,
                    "row_count": row.row_count,
                    "result_bytes": row.result_bytes,
                    "latency_ms": row.latency_ms,
                    "created_at": row.created_at,
                }
                for row in tools
            ],
            [
                {
                    "id": row.id,
                    "seq": row.seq,
                    "purpose": row.purpose,
                    "provider": row.provider,
                    "model": row.model,
                    "prompt_version": row.prompt_version,
                    "request": row.request,
                    "response": row.response,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "total_tokens": row.total_tokens,
                    "latency_ms": row.latency_ms,
                    "status": row.status,
                    "error_code": row.error_code,
                    "created_at": row.created_at,
                }
                for row in llms
            ],
        )

    async def update_summary(
        self,
        *,
        user_id: uuid.UUID,
        summary: str,
        summary_tokens: int,
        summary_message_id: str | None,
        summary_version: int,
    ) -> None:
        await self.session.execute(
            update(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .values(
                summary=summary,
                summary_tokens=summary_tokens,
                summary_message_id=summary_message_id,
                summary_version=summary_version,
                updated_at=func.now(),
            )
        )

    async def update_focus(self, *, user_id: uuid.UUID, focus: dict[str, Any]) -> None:
        await self.session.execute(
            update(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .values(focus=focus, updated_at=func.now())
        )

    async def increment_turn_count(self, *, user_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .values(turn_count=ChatConversation.turn_count + 1, updated_at=func.now())
        )

    async def delete_conversation(self, *, user_id: uuid.UUID) -> bool:
        """用户删除：硬删会话（含已软删行），FK cascade 清消息/轮次/调用/事件。"""
        conversation = await self.session.scalar(
            select(ChatConversation).where(ChatConversation.user_id == user_id)
        )
        if conversation is None:
            return False
        await self.session.execute(
            delete(ChatConversation).where(ChatConversation.user_id == user_id)
        )
        return True

    async def reset_conversation(self, *, user_id: uuid.UUID) -> None:
        conversation = await self.get_conversation(user_id)
        if conversation is None:
            return
        await self.session.execute(
            update(ChatMessage)
            .where(ChatMessage.user_id == user_id, ChatMessage.deleted_at.is_(None))
            .values(deleted_at=func.now())
        )
        await self.session.execute(
            update(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .values(
                summary="",
                summary_tokens=0,
                summary_message_id=None,
                summary_version=0,
                focus={},
                turn_count=0,
                updated_at=func.now(),
            )
        )
