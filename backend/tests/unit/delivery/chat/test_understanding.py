from __future__ import annotations

from datetime import UTC, datetime

import pytest
from stock_contracts.clock import FrozenClock
from stock_delivery.chat.provider import FakeProvider
from stock_delivery.chat.understanding import UnderstandingService

pytestmark = pytest.mark.no_db


async def test_rule_fallback_period_and_intent() -> None:
    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    result = service.fallback_understanding("本月为什么亏？", {})
    assert result.resolved_query == "本月为什么亏？"
    assert result.period is not None and result.period.value == "2026-11"
    assert result.intents


async def test_parse_resolves_focus_entity() -> None:
    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    result = service.parse(
        '{"intents":["round_detail"],"confidence":0.9,"period":{"kind":"month","value":"2026-11"},'
        '"entities":[{"mention":"它","type":"round","resolved_id":"rnd_1","source":"focus",'
        '"confidence":0.9}],"resolved_query":"2026-11 回合 rnd_1 为什么亏","needs_clarification":false}',
        question="它为什么亏",
        focus={"round_id": "rnd_1"},
    )
    assert result.entities[0].resolved_id == "rnd_1"
    assert result.needs_clarification is False
