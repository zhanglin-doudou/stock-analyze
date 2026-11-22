from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
import stock_api.routers.chat as chat_router
from stock_api.routers.chat import (
    _sse_events,
    _stream_live_turn,
    _synthetic_timeline,
    _timeline_out,
)
from stock_api.schemas import ChatMessageOut, ChatTimelineEventOut, ChatTimelineSummaryOut

pytestmark = pytest.mark.no_db


def test_timeline_out_accepts_db_created_at() -> None:
    events = _timeline_out(
        [
            {
                "seq": 1,
                "kind": "understanding.started",
                "phase": "understand",
                "payload": {},
                "created_at": datetime(2026, 11, 21, tzinfo=UTC),
            }
        ]
    )
    assert events[0].seq == 1
    assert events[0].at == datetime(2026, 11, 21, tzinfo=UTC)


def test_synthetic_timeline_from_trace() -> None:
    items = _synthetic_timeline(
        [
            {
                "seq": 1,
                "tool": "trades_query",
                "status": "ok",
                "row_count": 2,
                "latency_ms": 120,
                "created_at": datetime(2026, 11, 21, tzinfo=UTC),
            }
        ],
        [
            {
                "purpose": "answer",
                "status": "ok",
                "latency_ms": 900,
                "created_at": datetime(2026, 11, 21, tzinfo=UTC),
            }
        ],
    )
    assert [item.kind for item in items] == ["tool.call.succeeded", "model.thinking.completed"]
    assert all(item.payload["synthetic"] is True for item in items)


async def test_sse_emits_step_events_before_done() -> None:
    message = ChatMessageOut(
        conversation_id="cnv_1",
        turn_id="trn_1",
        message_id="msg_1",
        answer="回答",
        status="ok",
        timeline=[
            ChatTimelineEventOut(
                seq=1,
                kind="understanding.started",
                phase="understand",
                at=datetime(2026, 11, 21, tzinfo=UTC),
                payload={},
            ),
            ChatTimelineEventOut(
                seq=2,
                kind="tool.call.succeeded",
                phase="execute",
                call_id="call_1",
                at=datetime(2026, 11, 21, tzinfo=UTC),
                payload={"title": "流水查询", "row_count": 2},
            ),
        ],
        timeline_summary=ChatTimelineSummaryOut(
            events=2, steps=1, tool_calls=1, total_ms=100, complete=True
        ),
    )
    chunks = [chunk async for chunk in _sse_events(message)]
    joined = "".join(chunks)
    assert "event: meta" in joined
    assert "event: step" in joined
    assert '"seq": 2' in joined or '"seq":2' in joined
    assert joined.index("event: step") < joined.index("event: delta")
    assert '"timeline": {"events": 2, "steps": 1, "tool_calls": 1, "total_ms": 100' in joined
    assert "event: done" in joined


class _FakeSession:
    pass


class _FakeDb:
    @asynccontextmanager
    async def session(self, **_kwargs: Any) -> AsyncIterator[_FakeSession]:
        yield _FakeSession()


class _FakeRuntime:
    def __init__(self, on_event: Any) -> None:
        self.on_event = on_event
        self.assistant_message_id = "msg_1"
        self.citations: list[Any] = []
        self.understanding = None
        self.context_usage = None
        self._events: list[dict[str, Any]] = []
        self._seq = 0

    def emit(self, kind: str) -> None:
        self._seq += 1
        event = {
            "seq": self._seq,
            "kind": kind,
            "phase": "execute",
            "turn_id": "trn_1",
            "at": "2026-11-21T10:00:00+00:00",
            "payload": {},
        }
        self._events.append(event)
        self.on_event(event)

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def timeline_summary(self) -> dict[str, Any]:
        return {
            "events": len(self._events),
            "steps": 1,
            "tool_calls": 0,
            "total_ms": 10,
            "complete": True,
        }


class _FakeGraph:
    def __init__(self, runtime: _FakeRuntime) -> None:
        self.runtime = runtime

    async def ainvoke(self, _state: Any) -> dict[str, Any]:
        self.runtime.emit("understanding.started")
        self.runtime.emit("turn.completed")
        return {"answer": {"answer_markdown": "回答", "status": "ok"}, "status": "ok"}


async def test_stream_live_turn_emits_step_before_done(monkeypatch: Any) -> None:
    runtime_holder: dict[str, _FakeRuntime] = {}

    def _make_runtime(**kwargs: Any) -> _FakeRuntime:
        runtime = _FakeRuntime(kwargs["on_event"])
        runtime_holder["runtime"] = runtime
        return runtime

    monkeypatch.setattr(chat_router, "_make_runtime", _make_runtime)
    monkeypatch.setattr(chat_router, "build_graph", lambda runtime: _FakeGraph(runtime))

    class _State:
        db = _FakeDb()

    chunks = [
        chunk
        async for chunk in _stream_live_turn(
            state=_State(),
            user_id="user_1",
            conversation={"id": "cnv_1"},
            turn_id="trn_1",
            user_message_id="msg_u",
            question="问题",
            idempotency_key=None,
        )
    ]
    joined = "".join(chunks)
    assert joined.index("event: meta") < joined.index("event: step")
    assert joined.index("event: step") < joined.index("event: delta")
    assert joined.index("event: delta") < joined.index("event: done")
    assert runtime_holder["runtime"].events()
