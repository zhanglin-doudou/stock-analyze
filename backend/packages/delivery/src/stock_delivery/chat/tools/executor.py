"""唯一工具调用口：授权、schema、超时、去重、预算、留痕。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from stock_contracts.chat.dto import MissingEntry, ToolResult
from stock_contracts.chat.enums import ChatStatus, ToolSource
from stock_contracts.chat.tools import ToolCall, ToolContext, ToolSpec

from .registry import ToolCatalog
from .schema import validate_args


class ToolTraceSink(Protocol):
    """执行器不直接写 DB；聊天/ MCP 各自注入留痕实现。"""

    async def record_tool_call(
        self, call: ToolCall, result: ToolResult, ctx: ToolContext
    ) -> None: ...

    async def emit(self, event: Mapping[str, Any]) -> None: ...


class ToolExecutor:
    def __init__(self, *, max_calls_per_turn: int = 6) -> None:
        self.max_calls_per_turn = max_calls_per_turn

    async def execute(
        self,
        calls: Sequence[ToolCall],
        catalog: ToolCatalog,
        ctx: ToolContext,
        *,
        trace: ToolTraceSink | None = None,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        seen: dict[tuple[str, str], ToolResult] = {}
        for index, call in enumerate(calls):
            if index >= self.max_calls_per_turn:
                budget_tool = catalog.get(call.tool_id)
                results.append(
                    _error(
                        budget_tool.spec if budget_tool else None,
                        call,
                        "TURN_BUDGET_EXCEEDED",
                        f"单轮工具调用超过 {self.max_calls_per_turn} 次",
                    )
                )
                continue
            key = (call.tool_id, _canonical(call.args))
            if key in seen:
                results.append(replace(seen[key], cache_hit=True))
                continue
            registered = catalog.get(call.tool_id)
            if registered is None:
                results.append(_error(None, call, "TOOL_NOT_ALLOWED", "工具不在本轮目录中"))
                continue
            spec = registered.spec
            errors = validate_args(spec.input_schema, call.args)
            if errors:
                results.append(
                    _error(spec, call, "TOOL_ARGUMENT_INVALID", "; ".join(errors[:5]))
                )
                continue
            if spec.side_effect != "read":
                results.append(_error(spec, call, "TOOL_NOT_ALLOWED", "只允许只读工具"))
                continue
            if trace is not None:
                await trace.emit(
                    {
                        "kind": "tool.call.started",
                        "call_id": call.call_id,
                        "tool_id": spec.id,
                        "tool": spec.name,
                        "title": spec.title,
                        "source": spec.source.value,
                    }
                )
            started = time.monotonic()
            try:
                timeout = min(spec.timeout_ms / 1000, max(0.1, ctx.deadline - time.monotonic()))
                async with asyncio.timeout(timeout):
                    result = await registered.provider.invoke(call, ctx)
            except TimeoutError:
                result = _error(spec, call, "TOOL_TIMEOUT", "工具响应超时", retryable=True)
            except Exception as exc:
                result = _error(
                    spec,
                    call,
                    "TOOL_UNAVAILABLE",
                    str(exc)[:200] or "工具执行失败",
                    retryable=True,
                )
            latency = int((time.monotonic() - started) * 1000)
            result = replace(
                result,
                tool_id=spec.id,
                tool=spec.name,
                title=spec.title,
                source=spec.source,
                trust=spec.trust,
                latency_ms=latency,
            )
            if result.error_code is None and result.status is ChatStatus.ERROR:
                result = replace(result, error_code="TOOL_UNAVAILABLE", error_message="工具执行失败")
            seen[key] = result
            results.append(result)
            if trace is not None:
                await trace.emit(
                    {
                        "kind": (
                            "tool.call.succeeded"
                            if result.status is not ChatStatus.ERROR
                            else "tool.call.failed"
                        ),
                        "call_id": call.call_id,
                        "tool_id": spec.id,
                        "tool": spec.name,
                        "title": spec.title,
                        "source": spec.source.value,
                        "status": result.status.value,
                        "row_count": result.row_count,
                        "latency_ms": latency,
                        "error_code": result.error_code,
                    }
                )
                await trace.record_tool_call(call, result, ctx)
        return results


def _canonical(args: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(args), sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(args)


def _error(
    spec: ToolSpec | None,
    call: ToolCall,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> ToolResult:
    return ToolResult(
        tool=spec.name if spec else call.tool_id.rsplit(".", 1)[-1],
        tool_id=call.tool_id,
        title=spec.title if spec else call.tool_id,
        source=spec.source if spec else ToolSource.BUILTIN,
        status=ChatStatus.ERROR,
        data={},
        missing=(MissingEntry(code=code, message=message),),
        error_code=code,
        error_message=message,
        retryable=retryable,
    )
