"""智能分析助手 API 验收（API-077/078/112~115）。"""

from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.api


async def _session(client: httpx.AsyncClient, headers: dict[str, str]) -> dict:
    resp = await client.post("/api/v1/chat/sessions", headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def test_chat_session_singleton(client: httpx.AsyncClient, user) -> None:
    first = await _session(client, user.headers)
    second = await _session(client, user.headers)
    assert first["conversation_id"] == second["conversation_id"]
    assert first["window_turns"] == 10


async def test_chat_template_fallback_without_llm(client: httpx.AsyncClient, user, app) -> None:
    app.state.sa.settings.llm_provider = "none"
    conversation = await _session(client, user.headers)
    resp = await client.post(
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers=user.headers,
        json={"message": "数据可信吗？"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback"] is True
    assert body["status"] in ("ok", "degraded")
    assert body["turn_id"]


async def test_chat_trace_and_idempotency(client: httpx.AsyncClient, user, app) -> None:
    app.state.sa.settings.llm_provider = "none"
    conversation = await _session(client, user.headers)
    key = "chat-test-idempotency-0001"
    payload = {"message": "数据可信吗？"}
    first = await client.post(
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers={**user.headers, "Idempotency-Key": key},
        json=payload,
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers={**user.headers, "Idempotency-Key": key},
        json=payload,
    )
    assert second.status_code == 200, second.text
    assert first.json()["turn_id"] == second.json()["turn_id"]

    trace = await client.get(
        f"/api/v1/chat/turns/{first.json()['turn_id']}/trace", headers=user.headers
    )
    assert trace.status_code == 200, trace.text
    assert trace.json()["turn"]["id"] == first.json()["turn_id"]


async def test_chat_cross_user_404(client: httpx.AsyncClient, user, account_factory) -> None:
    conversation = await _session(client, user.headers)
    other = await account_factory()
    resp = await client.post(
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers=other.headers,
        json={"message": "数据可信吗？"},
    )
    assert resp.status_code == 404


async def test_chat_admin_forbidden(client: httpx.AsyncClient, account_factory) -> None:
    admin = await account_factory(role="admin")
    resp = await client.post("/api/v1/chat/sessions", headers=admin.headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "ADMIN_CANNOT_READ_TRADES"


async def test_chat_llm_path_with_fake_provider(client: httpx.AsyncClient, user, app) -> None:
    from stock_delivery.chat.provider import FakeProvider

    app.state.sa.settings.llm_provider = "glm"
    app.state.sa.settings.glm_api_key = "test"
    app.state.sa.llm = FakeProvider(
        [
            json.dumps(
                {
                    "intents": ["data_quality"],
                    "confidence": 0.9,
                    "period": {"kind": "none"},
                    "entities": [],
                    "resolved_query": "数据可信吗？",
                    "needs_clarification": False,
                }
            ),
            json.dumps(
                {
                    "answer_markdown": "资产表状态可用。",
                    "claims": [],
                    "used_citations": [],
                    "confidence": "high",
                    "followups": [],
                }
            ),
        ]
    )
    conversation = await _session(client, user.headers)
    resp = await client.post(
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers=user.headers,
        json={"message": "数据可信吗？"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["llm_used"] is True
    assert body["fallback"] is False


async def test_chat_stream_sse(client: httpx.AsyncClient, user, app) -> None:
    """API-078 的 SSE 变体：meta/delta/done 事件完整，done 带最终消息。"""
    app.state.sa.settings.llm_provider = "none"
    conversation = await _session(client, user.headers)
    events: list[tuple[str, dict[str, object]]] = []
    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers={**user.headers, "Accept": "text/event-stream"},
        json={"message": "数据可信吗？"},
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        assert "text/event-stream" in (resp.headers.get("content-type") or "")
        event_name = ""
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                events.append((event_name, json.loads(line[6:])))

    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert "delta" in names
    assert names[-1] == "done"
    done = next(data for name, data in events if name == "done")
    message = done["message"]
    assert isinstance(message, dict)
    assert message["turn_id"]
    assert message["answer"]


async def test_chat_needs_clarification_persists(client: httpx.AsyncClient, user, app) -> None:
    """状态 `needs_clarification` 必须能写入 32 宽的状态列（0021 回归）。"""
    from stock_delivery.chat.provider import FakeProvider

    app.state.sa.settings.llm_provider = "glm"
    app.state.sa.settings.glm_api_key = "test"
    app.state.sa.llm = FakeProvider(
        [
            json.dumps(
                {
                    "intents": ["unknown"],
                    "confidence": 0.4,
                    "period": {"kind": "none"},
                    "entities": [],
                    "resolved_query": "它怎么了",
                    "needs_clarification": True,
                    "clarification": "你指的是哪只证券？",
                }
            )
        ]
    )
    conversation = await _session(client, user.headers)
    resp = await client.post(
        f"/api/v1/chat/sessions/{conversation['conversation_id']}/messages",
        headers=user.headers,
        json={"message": "它怎么了"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "needs_clarification"
    assert body["answer"] == "你指的是哪只证券？"
