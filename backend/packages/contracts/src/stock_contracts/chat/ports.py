"""助手端口：delivery 只依赖这些协议，不依赖 review/DB/LangChain。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from stock_contracts.chat.dto import LlmResult, ToolResult
from stock_contracts.chat.enums import ToolName


class ChatDataPort(Protocol):
    """工具数据的只读门面；实现由 stock_review 提供，user_id 由调用方注入。"""

    async def invoke(self, tool: ToolName, args: Mapping[str, Any]) -> ToolResult: ...


class LlmClient(Protocol):
    """单次 LLM 调用；实现由 delivery.provider 提供。"""

    async def complete(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int,
        json_mode: bool = True,
    ) -> LlmResult: ...

    async def complete_messages(
        self,
        *,
        purpose: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        json_mode: bool = True,
    ) -> LlmResult: ...
