from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import pytest
from stock_contracts.chat.dto import Citation, ToolResult
from stock_contracts.chat.enums import ChatStatus
from stock_contracts.clock import FrozenClock
from stock_delivery.chat.graph import build_graph
from stock_delivery.chat.provider import FakeProvider
from stock_delivery.chat.runtime import ChatRuntime

pytestmark = pytest.mark.no_db


class _Repo:
    def __init__(self) -> None:
        self.tool_calls = []
        self.llm_calls = []
        self.messages = []

    async def list_messages(self, *args, **kwargs):
        return []

    async def update_summary(self, **kwargs):
        return None

    async def record_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs)

    async def record_llm_call(self, **kwargs):
        self.llm_calls.append(kwargs)

    async def append_message(self, **kwargs):
        self.messages.append(kwargs)
        return {"id": "msg_1"}

    async def update_turn(self, *args, **kwargs):
        return None

    async def increment_turn_count(self, **kwargs):
        return None

    async def update_focus(self, **kwargs):
        return None


class _Data:
    async def invoke(self, tool, args):
        return ToolResult(
            tool=tool,
            status=ChatStatus.OK,
            data={
                "asset": {"total_assets": "100,000.00", "total_profit": "1,234.56"},
                "history": {"win_ratio_pct": "55.00%", "max_drawdown": {"mdd_pct": "8.00%"}},
            },
            citations=(
                Citation(
                    citation_id="c1_1",
                    label="本月收益",
                    value_display="-1234.56",
                    source_endpoint="GET /api/v1/dashboard",
                    data_as_of=date(2026, 11, 18),
                ),
            ),
            data_as_of=date(2026, 11, 18),
            row_count=1,
        )


async def test_runtime_template_and_llm_path(settings) -> None:
    settings.llm_provider = "glm"
    settings.glm_api_key = "test"
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "intents": ["portfolio"],
                    "confidence": 0.9,
                    "period": {"kind": "month", "value": "2026-11", "label": "本月"},
                    "entities": [],
                    "resolved_query": "本月赚了多少",
                    "needs_clarification": False,
                }
            ),
            json.dumps(
                {
                    "answer_markdown": "本月收益 -1,234.56 元 [[1]]",
                    "claims": [
                        {
                            "claim_id": "k1",
                            "text": "本月收益 -1,234.56 元",
                            "citation_ids": ["c1_1"],
                        }
                    ],
                    "used_citations": ["c1_1"],
                    "confidence": "high",
                    "followups": [],
                }
            ),
        ]
    )
    repo = _Repo()
    runtime = ChatRuntime(
        repo=repo,  # type: ignore[arg-type]
        data_port=_Data(),
        llm=provider,
        clock=FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)),
        settings=settings,
        user_id=uuid.uuid4(),
        conversation={
            "id": "cnv_1",
            "summary": "",
            "focus": {},
            "window_turns": 10,
            "summary_version": 0,
        },
        turn_id="trn_1",
        question="本月赚了多少",
    )
    final = await build_graph(runtime).ainvoke({"question": "本月赚了多少"})
    assert final["status"] == "ok"
    assert "1,234.56" in final["answer"]["answer_markdown"]
    assert len(repo.tool_calls) >= 1
    assert len(repo.llm_calls) >= 2
    assert runtime.assistant_message_id == "msg_1"


async def test_llm_answer_without_numbers_keeps_template_numbers(settings) -> None:
    settings.llm_provider = "glm"
    settings.glm_api_key = "test"
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "intents": ["portfolio"],
                    "confidence": 0.9,
                    "period": {"kind": "month", "value": "2026-11", "label": "本月"},
                    "entities": [],
                    "resolved_query": "本月赚了多少",
                    "needs_clarification": False,
                }
            ),
            json.dumps(
                {
                    "answer_markdown": "整体表现平稳，建议继续关注。",
                    "claims": [],
                    "used_citations": [],
                    "confidence": "medium",
                    "followups": [],
                }
            ),
        ]
    )
    repo = _Repo()
    runtime = ChatRuntime(
        repo=repo,  # type: ignore[arg-type]
        data_port=_Data(),
        llm=provider,
        clock=FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)),
        settings=settings,
        user_id=uuid.uuid4(),
        conversation={
            "id": "cnv_1",
            "summary": "",
            "focus": {},
            "window_turns": 10,
            "summary_version": 0,
        },
        turn_id="trn_1",
        question="本月赚了多少",
    )
    final = await build_graph(runtime).ainvoke({"question": "本月赚了多少"})
    assert final["status"] == "ok"
    assert "100,000.00" in final["answer"]["answer_markdown"]
    assert "整体表现平稳" in final["answer"]["answer_markdown"]


async def test_invalid_understanding_json_falls_back_without_500(settings) -> None:
    settings.llm_provider = "glm"
    settings.glm_api_key = "test"
    provider = FakeProvider(
        [
            '{"intents":[{"name":"trades"}],"confidence":0.9}',
            json.dumps(
                {
                    "answer_markdown": "本月收益 -1,234.56 元 [[1]]",
                    "claims": [],
                    "used_citations": ["c1_1"],
                    "confidence": "medium",
                    "followups": [],
                }
            ),
        ]
    )
    repo = _Repo()
    runtime = ChatRuntime(
        repo=repo,  # type: ignore[arg-type]
        data_port=_Data(),
        llm=provider,
        clock=FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)),
        settings=settings,
        user_id=uuid.uuid4(),
        conversation={
            "id": "cnv_1",
            "summary": "",
            "focus": {},
            "window_turns": 10,
            "summary_version": 0,
        },
        turn_id="trn_1",
        question="本月赚了多少",
    )
    final = await build_graph(runtime).ainvoke({"question": "本月赚了多少"})
    assert final["understanding"]["intents"][0] == "portfolio"
    assert final["degrade_reason"] == "LLM_BAD_RESPONSE"
    assert len(repo.llm_calls) == 2
