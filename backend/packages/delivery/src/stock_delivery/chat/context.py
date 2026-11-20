"""上下文窗口、token 预算与压缩（10 轮 / 200K*80%）。"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from stock_contracts.chat.dto import ContextUsage
from stock_platform.config import Settings
from stock_platform.repositories.chat import ChatRepository

from .prompts import UNDERSTANDING_SYSTEM


def estimate_tokens(text: str) -> int:
    """保守估算：CJK ~1 token/字，ASCII ~3.5 字/token，另加 10% 安全系数。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_count = max(0, len(text) - cjk)
    raw = cjk + ascii_count / 3.5
    return math.ceil(raw * 1.1) + 8


@dataclass(slots=True)
class ContextBundle:
    summary: str
    focus: Mapping[str, Any]
    history: list[dict[str, str]]
    usage: ContextUsage


class ContextManager:
    def __init__(self, repo: ChatRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    def input_budget(self) -> int:
        window = self.settings.llm_context_window_tokens
        ratio = self.settings.llm_context_compress_ratio
        return max(1, int(window * ratio) - self.settings.llm_max_output_tokens)

    async def build(
        self,
        user_id: Any,
        conversation: Mapping[str, Any],
        question: str,
        *,
        window_turns: int | None = None,
        compressor: Callable[[str, Sequence[dict[str, str]]], Awaitable[str]] | None = None,
        exclude_message_id: str | None = None,
    ) -> ContextBundle:
        turns = max(
            1,
            int(
                window_turns or conversation.get("window_turns") or self.settings.llm_history_turns
            ),
        )
        messages = await self.repo.list_messages(
            user_id,
            str(conversation["id"]),
            limit=turns * 2,
        )
        if exclude_message_id:
            messages = [item for item in messages if str(item.get("id")) != exclude_message_id]
        history = [
            {"role": str(item["role"]), "content": str(item["content"] or "")} for item in messages
        ]
        summary = str(conversation.get("summary") or "")
        usage = self._usage(conversation, history, question, summary)
        compressed = False
        steps: list[str] = []

        # 1) 超预算先丢弃最旧消息，保留至少最近 2 轮。
        if usage.estimated_input_tokens > self.input_budget():
            compressed = True
            steps.append("drop_oldest_messages")
            keep = max(4, len(history) // 2)
            history = history[-keep:]
            usage = self._usage(conversation, history, question, summary)
            if usage.estimated_input_tokens > self.input_budget():
                steps.append("truncate_message_text")
                history = [{**item, "content": item["content"][:8000]} for item in history]
                usage = self._usage(conversation, history, question, summary)

        # 2) 仍超预算且有压缩器：把最旧的一半压成摘要（由 runtime 注入 LLM 调用）。
        if (
            usage.estimated_input_tokens > self.input_budget()
            and compressor is not None
            and history
        ):
            steps.append("summarize_oldest")
            half = max(1, len(history) // 2)
            old, history = history[:half], history[half:]
            try:
                new_summary = await compressor(summary, old)
            except Exception:
                steps.append("compression_fallback")
                new_summary = _deterministic_summary(summary, old)
            summary = new_summary
            compressed = True
            usage = self._usage(conversation, history, question, summary)
            await self.repo.update_summary(
                user_id=user_id,
                summary=summary,
                summary_tokens=estimate_tokens(summary),
                summary_message_id=None,
                summary_version=int(conversation.get("summary_version") or 0) + 1,
            )

        # 3) 硬截断兜底：保留最近 2 条 + 摘要，并按预算裁剪单条文本。
        if usage.estimated_input_tokens > self.input_budget():
            steps.append("hard_truncate")
            history = history[-2:]
            per_message = max(
                80, (self.input_budget() - estimate_tokens(summary) - 100) // max(1, len(history))
            )
            history = [{**item, "content": item["content"][:per_message]} for item in history]
            usage = self._usage(conversation, history, question, summary)
            compressed = True

        usage = ContextUsage(
            window_turns=turns,
            included_turns=len(history),
            estimated_input_tokens=usage.estimated_input_tokens,
            compressed=compressed,
            context_truncated="hard_truncate" in steps,
            summary_version=int(conversation.get("summary_version") or 0),
            compression_steps=tuple(steps),
        )
        return ContextBundle(
            summary=summary,
            focus=dict(conversation.get("focus") or {}),
            history=history,
            usage=usage,
        )

    def understanding_messages(self, bundle: ContextBundle, question: str) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": UNDERSTANDING_SYSTEM}]
        if bundle.summary:
            messages.append({"role": "system", "content": f"对话摘要：{bundle.summary}"})
        if bundle.focus:
            messages.append({"role": "system", "content": f"当前焦点：{bundle.focus}"})
        messages.extend(bundle.history)
        messages.append({"role": "user", "content": question})
        return messages

    def _usage(
        self,
        conversation: Mapping[str, Any],
        history: Sequence[dict[str, str]],
        question: str,
        summary: str,
    ) -> ContextUsage:
        text = "\n".join(
            [UNDERSTANDING_SYSTEM, summary, question] + [item["content"] for item in history]
        )
        return ContextUsage(
            window_turns=int(conversation.get("window_turns") or self.settings.llm_history_turns),
            included_turns=len(history),
            estimated_input_tokens=estimate_tokens(text),
            summary_version=int(conversation.get("summary_version") or 0),
        )


def _deterministic_summary(old_summary: str, old: Sequence[dict[str, str]]) -> str:
    """压缩器不可用时的保守摘要：保留最近用户问题与助手结论。"""
    lines = [old_summary] if old_summary else []
    for item in old:
        content = item["content"].strip().replace("\n", " ")
        if not content:
            continue
        prefix = "用户" if item["role"] == "user" else "助手"
        lines.append(f"{prefix}：{content[:200]}")
    return "\n".join(lines)[-2000:]
