from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import pytest
from stock_contracts.chat.dto import Citation, PeriodSpec, ToolResult, Understanding
from stock_contracts.chat.enums import ChatStatus
from stock_contracts.clock import FrozenClock
from stock_delivery.chat.graph import build_graph
from stock_delivery.chat.provider import FakeProvider
from stock_delivery.chat.runtime import ChatRuntime, _next_focus

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
    settings.llm_provider = "auto"
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
    settings.llm_provider = "auto"
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
    settings.llm_provider = "auto"
    provider = FakeProvider(
        [
            '{"intents":"trades","confidence":0.9}',
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
    # 理解失败但答案成功：persist 补「切换到 glm-4-flash」提示（fake-chat 非推荐模型）。
    assert "切换到 glm-4-flash" in (final["answer"].get("what_to_do") or "")


class _FlakyProvider:
    provider = "fake"
    model = "fake-chat"

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    async def complete_messages(self, **_kwargs):
        from stock_contracts.chat.dto import LlmResult

        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LlmResult(content=item, provider="fake", model="fake-chat")


async def test_timeout_retries_both_understanding_and_answer(settings) -> None:
    from stock_contracts.chat.enums import DegradeReason
    from stock_delivery.chat.provider import LlmError

    settings.llm_provider = "auto"
    settings.llm_retry_on_429 = 1
    settings.llm_retry_backoff_seconds = 0
    provider = _FlakyProvider(
        [
            LlmError(DegradeReason.LLM_TIMEOUT, "Request timed out"),
            json.dumps(
                {
                    "intents": ["portfolio"],
                    "confidence": 0.9,
                    "period": {"kind": "month", "value": "2026-11"},
                    "entities": [],
                    "resolved_query": "本月赚了多少",
                    "needs_clarification": False,
                }
            ),
            LlmError(DegradeReason.LLM_TIMEOUT, "Request timed out"),
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
    assert final["status"] == "ok"
    assert provider.calls == 4
    assert len(repo.llm_calls) == 4


def test_next_focus_drops_empty_and_period_security_fields() -> None:
    understanding = Understanding(
        intents=(),
        confidence=0.9,
        period=PeriodSpec(kind="month", value="2026-11"),
        entities=(),
        resolved_query="本月有哪些操作",
    )
    focus = _next_focus(
        {"period": "2026-10", "security_code": "", "security_name": "本月"},
        understanding,
    )
    assert focus == {"period": "2026-11"}


# ── 降级提示：切换到推荐模型（2026-11-22）───────────────────
def test_model_switch_hint_matrix() -> None:
    from stock_contracts.chat.enums import DegradeReason
    from stock_delivery.chat.runtime import RECOMMENDED_MODEL, _model_switch_hint

    assert RECOMMENDED_MODEL == "glm-4-flash"
    hint = _model_switch_hint(DegradeReason.LLM_RATE_LIMITED, "mimo-v2.6-pro")
    assert hint is not None
    assert "mimo-v2.6-pro" in hint
    assert "glm-4-flash" in hint
    assert "设置" in hint
    # 字符串原因（turn 级 degrade_reason）同样识别
    assert _model_switch_hint("LLM_TIMEOUT", "mimo-v2.6-flash") is not None
    # 推荐模型自身失败不提示切换到自己
    assert _model_switch_hint(DegradeReason.LLM_RATE_LIMITED, "glm-4-flash") is None
    # 非模型调用失败 / 未知原因 / 无原因不提示
    assert _model_switch_hint(DegradeReason.CONTEXT_OVERFLOW, "mimo-v2.6-pro") is None
    assert _model_switch_hint(DegradeReason.TOOL_UNAVAILABLE, "mimo-v2.6-pro") is None
    assert _model_switch_hint("NOPE", "mimo-v2.6-pro") is None
    assert _model_switch_hint(None, "mimo-v2.6-pro") is None


async def test_model_failure_answer_fallback_hints_switch(settings) -> None:
    """非推荐模型答案调用失败：降级草稿提示切换到 glm-4-flash。"""
    from stock_contracts.chat.enums import DegradeReason
    from stock_delivery.chat.provider import LlmError

    settings.llm_provider = "auto"
    understanding = json.dumps(
        {
            "intents": ["portfolio"],
            "confidence": 0.9,
            "period": {"kind": "month", "value": "2026-11", "label": "本月"},
            "entities": [],
            "resolved_query": "本月赚了多少",
            "needs_clarification": False,
        }
    )
    error = LlmError(DegradeReason.LLM_RATE_LIMITED, "rate limited", retryable=True)
    provider = _FlakyProvider([understanding, error, error, error])
    provider.model = "mimo-v2.6-pro"
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
    answer = final["answer"]
    assert answer["fallback"] is True
    assert answer["degrade_reason"] == "LLM_RATE_LIMITED"
    assert "切换到 glm-4-flash" in answer["what_to_do"]
    assert "mimo-v2.6-pro" in answer["what_to_do"]
