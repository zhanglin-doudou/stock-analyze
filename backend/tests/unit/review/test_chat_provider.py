from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from stock_contracts.chat.dto import ToolResult
from stock_contracts.chat.enums import ChatStatus, ToolName, ToolSource, ToolTrust
from stock_contracts.chat.tools import ToolCall, ToolContext
from stock_review.application.chat.provider import BUILTIN_SPECS, BuiltinToolProvider

pytestmark = pytest.mark.no_db


class _Data:
    def __init__(self) -> None:
        self.calls: list[tuple[ToolName, Mapping[str, Any]]] = []

    async def invoke(self, tool: ToolName, args: Mapping[str, Any]) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(
            tool=tool,
            status=ChatStatus.OK,
            data={"items": []},
            row_count=0,
        )


def _ctx() -> ToolContext:
    return ToolContext(
        user_id=uuid.uuid4(),
        request_id="req_1",
        turn_id="trn_1",
        scopes=frozenset({"read"}),
        deadline=10_000.0,
    )


def test_builtin_specs_are_unique_and_read_only() -> None:
    ids = [spec.id for spec in BUILTIN_SPECS]
    names = [spec.name for spec in BUILTIN_SPECS]
    assert len(ids) == 11
    assert len(set(ids)) == 11
    assert len(set(names)) == 11
    assert all(spec.side_effect == "read" for spec in BUILTIN_SPECS)
    assert all(spec.source is ToolSource.BUILTIN for spec in BUILTIN_SPECS)
    assert all(spec.trust is ToolTrust.LOCAL for spec in BUILTIN_SPECS)
    assert all(spec.mcp_exposed for spec in BUILTIN_SPECS)


async def test_builtin_provider_invokes_facade() -> None:
    data = _Data()
    provider = BuiltinToolProvider(data)
    call = ToolCall(
        call_id="call_1",
        tool_id="builtin.trades_query",
        args={"period": "2026-11"},
    )
    result = await provider.invoke(call, _ctx())
    assert data.calls == [(ToolName.TRADES_QUERY, {"period": "2026-11"})]
    assert result.tool_id == "builtin.trades_query"
    assert result.tool == "trades_query"
    assert result.title == "流水查询"
    assert result.source is ToolSource.BUILTIN
    assert result.trust is ToolTrust.LOCAL


async def test_builtin_provider_rejects_unknown_tool() -> None:
    provider = BuiltinToolProvider(_Data())
    result = await provider.invoke(
        ToolCall(call_id="call_1", tool_id="builtin.nope", args={}),
        _ctx(),
    )
    assert result.status is ChatStatus.ERROR
    assert result.error_code == "TOOL_NOT_ALLOWED"
