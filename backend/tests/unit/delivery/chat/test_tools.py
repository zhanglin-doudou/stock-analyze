from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence

import pytest
from stock_contracts.chat.dto import Citation, PeriodSpec, ResolvedEntity, ToolResult, Understanding
from stock_contracts.chat.enums import ChatStatus, Intent, ToolSource
from stock_contracts.chat.tools import ProviderHealth, ToolCall, ToolContext, ToolSpec
from stock_delivery.chat.runtime import _tools_for
from stock_delivery.chat.tools import RulePlanner, ToolExecutor, ToolRegistry

pytestmark = pytest.mark.no_db


def _ctx() -> ToolContext:
    return ToolContext(
        user_id=uuid.uuid4(),
        request_id="req_1",
        turn_id="trn_1",
        scopes=frozenset({"read"}),
        deadline=asyncio.get_event_loop().time() + 5,
    )


class _Provider:
    source = ToolSource.BUILTIN

    def __init__(self, specs: Sequence[ToolSpec], *, delay: float = 0.0) -> None:
        self._specs = tuple(specs)
        self._delay = delay

    async def list_tools(self, ctx: ToolContext) -> Sequence[ToolSpec]:
        return self._specs

    async def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        spec = next(item for item in self._specs if item.id == call.tool_id)
        return ToolResult(
            tool=spec.name,
            status=ChatStatus.OK,
            data={"ok": True},
            citations=(
                Citation(
                    citation_id="c1",
                    label="测试",
                    value_display="1.00",
                    source_endpoint="GET /api/v1/test",
                ),
            ),
            row_count=1,
        )

    async def health(self, ctx: ToolContext) -> ProviderHealth:
        return ProviderHealth(ok=True)


class _Sink:
    def __init__(self) -> None:
        self.records: list[tuple[ToolCall, ToolResult]] = []
        self.events: list[Mapping[str, object]] = []

    async def record_tool_call(
        self, call: ToolCall, result: ToolResult, ctx: ToolContext
    ) -> None:
        self.records.append((call, result))

    async def emit(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


def _spec(
    name: str,
    capability: str,
    schema: Mapping[str, object] | None = None,
    *,
    timeout_ms: int = 8_000,
) -> ToolSpec:
    return ToolSpec(
        id=f"builtin.{name}",
        name=name,
        title=name,
        description=name,
        when_to_use=name,
        input_schema=schema or {"type": "object", "additionalProperties": True},
        capabilities=(capability,),
        source=ToolSource.BUILTIN,
        timeout_ms=timeout_ms,
    )


def _catalog(specs: Sequence[ToolSpec], provider: _Provider) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec, provider)
    return registry


async def test_registry_rejects_duplicate_id() -> None:
    provider = _Provider(())
    registry = ToolRegistry()
    spec = _spec("assets_query", "assets.read")
    registry.register(spec, provider)
    with pytest.raises(ValueError):
        registry.register(spec, provider)


async def test_executor_validates_args_and_emits_trace() -> None:
    spec = _spec(
        "trades_query",
        "trades.read",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"period": {"type": "string"}},
        },
    )
    provider = _Provider((spec,))
    registry = _catalog((spec,), provider)
    ctx = _ctx()
    catalog = await registry.snapshot(ctx)
    sink = _Sink()
    executor = ToolExecutor()
    results = await executor.execute(
        (
            ToolCall(call_id="c1", tool_id=spec.id, args={"period": "2026-11"}),
            ToolCall(call_id="c2", tool_id=spec.id, args={"unknown": 1}),
        ),
        catalog,
        ctx,
        trace=sink,
    )
    assert results[0].status is ChatStatus.OK
    assert results[0].tool_id == spec.id
    assert results[0].title == spec.name
    assert results[1].error_code == "TOOL_ARGUMENT_INVALID"
    assert len(sink.records) == 1
    assert [event["kind"] for event in sink.events] == [
        "tool.call.started",
        "tool.call.succeeded",
    ]


async def test_executor_timeout_and_dedup() -> None:
    spec = _spec("slow", "slow.read", timeout_ms=10)
    provider = _Provider((spec,), delay=0.05)
    registry = _catalog((spec,), provider)
    ctx = _ctx()
    catalog = await registry.snapshot(ctx)
    executor = ToolExecutor()
    results = await executor.execute(
        (
            ToolCall(call_id="c1", tool_id=spec.id, args={}),
            ToolCall(call_id="c2", tool_id=spec.id, args={}),
        ),
        catalog,
        ctx,
    )
    assert results[0].error_code == "TOOL_TIMEOUT"
    assert results[1].cache_hit is True
    assert results[1].error_code == "TOOL_TIMEOUT"


async def test_rule_planner_matches_legacy_tool_sets() -> None:
    # 只使用内置 11 个 spec，避免依赖数据库/外部 Provider。
    from stock_review.application.chat.provider import BUILTIN_SPECS

    provider = _Provider(BUILTIN_SPECS)
    registry = _catalog(BUILTIN_SPECS, provider)
    ctx = _ctx()
    catalog = await registry.snapshot(ctx)
    cases = (
        Understanding(
            intents=(Intent.PORTFOLIO,),
            confidence=0.9,
            period=PeriodSpec(kind="month", value="2026-11", label="本月"),
            entities=(),
            resolved_query="本月赚了多少",
        ),
        Understanding(
            intents=(Intent.PERFORMANCE,),
            confidence=0.9,
            period=None,
            entities=(),
            resolved_query="胜率怎么样",
        ),
        Understanding(
            intents=(Intent.ROUNDS,),
            confidence=0.9,
            period=None,
            entities=(),
            resolved_query="最亏的回合",
        ),
        Understanding(
            intents=(Intent.ROUND_DETAIL,),
            confidence=0.9,
            period=None,
            entities=(ResolvedEntity("它", "round", "rnd_1"),),
            resolved_query="这个回合详情",
        ),
        Understanding(
            intents=(Intent.ROUND_DETAIL,),
            confidence=0.9,
            period=None,
            entities=(),
            resolved_query="回合详情",
        ),
        Understanding(
            intents=(Intent.METRIC_EXPLAIN,),
            confidence=0.9,
            period=None,
            entities=(),
            resolved_query="胜率怎么算",
        ),
        Understanding(
            intents=(Intent.UNKNOWN,),
            confidence=0.5,
            period=None,
            entities=(),
            resolved_query="随便看看",
        ),
    )
    for understanding in cases:
        plan = RulePlanner().plan(understanding, catalog)
        assert plan.action == "call"
        planned = {call.tool_id for call in plan.calls}
        legacy = {f"builtin.{tool.value}" for tool, _ in _tools_for(understanding)}
        assert planned == legacy, understanding.resolved_query
