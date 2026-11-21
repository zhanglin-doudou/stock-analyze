from __future__ import annotations

from datetime import UTC, datetime

import pytest
from stock_api.routers.chat import _sse_events, _synthetic_timeline, _timeline_out
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
