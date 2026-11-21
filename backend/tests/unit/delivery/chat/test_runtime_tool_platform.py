from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest
from stock_contracts.chat.dto import Citation, ToolResult
from stock_contracts.chat.enums import ChatStatus, ToolName
from stock_contracts.clock import FrozenClock
from stock_delivery.chat.graph import build_graph
from stock_delivery.chat.provider import FakeProvider
from stock_delivery.chat.runtime import ChatRuntime
from stock_delivery.chat.tools import ToolExecutor, ToolRegistry
from stock_review.application.chat.provider import BUILTIN_SPECS, BuiltinToolProvider

pytestmark = pytest.mark.no_db


class _Repo:
    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def list_messages(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def update_summary(self, **kwargs: Any) -> None:
        return None

    async def record_tool_call(self, **kwargs: Any) -> None:
        self.tool_calls.append(kwargs)

    async def record_llm_call(self, **kwargs: Any) -> None:
        self.llm_calls.append(kwargs)

    async def append_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return {"id": "evt_1", **kwargs}

    async def append_message(self, **kwargs: Any) -> dict[str, Any]:
        self.messages.append(kwargs)
        return {"id": "msg_1"}

    async def update_turn(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def increment_turn_count(self, **kwargs: Any) -> None:
        return None

    async def update_focus(self, **kwargs: Any) -> None:
        return None


class _Data:
    async def invoke(self, tool: ToolName, args: Mapping[str, Any]) -> ToolResult:
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
                    value_display="1,234.56",
                    source_endpoint="GET /api/v1/dashboard",
                    data_as_of=date(2026, 11, 18),
                ),
            ),
            data_as_of=date(2026, 11, 18),
            row_count=1,
        )


async def test_runtime_can_execute_via_tool_platform(settings: Any) -> None:
    settings.llm_provider = "glm"
    settings.glm_api_key = "test"
    settings.tool_platform_enabled = True
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
                    "answer_markdown": "本月收益 1,234.56 元 [[1]]",
                    "claims": [
                        {
                            "claim_id": "k1",
                            "text": "本月收益 1,234.56 元",
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
    data = _Data()
    builtin = BuiltinToolProvider(data)
    registry = ToolRegistry()
    for spec in BUILTIN_SPECS:
        registry.register(spec, builtin)
    repo = _Repo()
    runtime = ChatRuntime(
        repo=repo,  # type: ignore[arg-type]
        data_port=data,
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
        tool_registry=registry,
        tool_executor=ToolExecutor(max_calls_per_turn=6),
    )
    final = await build_graph(runtime).ainvoke({"question": "本月赚了多少"})
    assert final["status"] == "ok"
    assert "1,234.56" in final["answer"]["answer_markdown"]
    assert len(repo.tool_calls) == 2
    assert {item["tool_name"] for item in repo.tool_calls} == {
        "assets_query",
        "portfolio_snapshot",
    }
    kinds = [event["kind"] for event in runtime.events()]
    assert kinds[0] == "turn.started"
    assert "understanding.started" in kinds
    assert "plan.created" in kinds
    assert "tool.call.started" in kinds
    assert "tool.call.succeeded" in kinds
    assert "answer.started" in kinds
    assert kinds[-1] == "turn.completed"
    assert len(repo.events) == len(runtime.events())
