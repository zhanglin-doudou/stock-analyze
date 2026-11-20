"""LangGraph 单轮状态（只放 JSON 可序列化/纯 DTO；DB/model 在 Runtime）。"""

from __future__ import annotations

from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    question: str
    conversation: dict[str, Any]
    context: dict[str, Any]
    understanding: dict[str, Any]
    results: list[dict[str, Any]]
    answer: dict[str, Any]
    status: str
    degrade_reason: str | None
    validation_errors: list[str]
    retry_count: int
