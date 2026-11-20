from __future__ import annotations

import pytest
from stock_delivery.chat.context import ContextManager, estimate_tokens

pytestmark = pytest.mark.no_db


class _Repo:
    def __init__(self, messages):
        self.messages = messages
        self.updated = None

    async def list_messages(self, *args, **kwargs):
        return self.messages

    async def update_summary(self, **kwargs):
        self.updated = kwargs


def test_estimate_tokens_counts_cjk_and_ascii() -> None:
    assert estimate_tokens("本月收益 1234.56") > 5
    assert estimate_tokens("hello world") > 0


async def test_build_keeps_recent_and_truncates(settings) -> None:
    settings.llm_context_window_tokens = 1000
    settings.llm_context_compress_ratio = 0.5
    settings.llm_max_output_tokens = 100
    repo = _Repo(
        [
            {"role": "user", "content": "旧问题" * 100, "turn_id": "t1"},
            {"role": "assistant", "content": "旧回答" * 100, "turn_id": "t1"},
            {"role": "user", "content": "新问题", "turn_id": "t2"},
            {"role": "assistant", "content": "新回答", "turn_id": "t2"},
        ]
    )
    manager = ContextManager(repo, settings)  # type: ignore[arg-type]
    bundle = await manager.build(
        "user",
        {"id": "cnv", "summary": "", "focus": {}, "window_turns": 10, "summary_version": 0},
        "现在呢",
    )
    assert bundle.usage.compressed is True
    assert len(bundle.history) <= 4
    assert bundle.usage.estimated_input_tokens <= manager.input_budget()
