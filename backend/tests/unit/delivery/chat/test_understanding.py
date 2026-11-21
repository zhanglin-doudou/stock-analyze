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


def test_low_quality_model_output_falls_back_instead_of_clarifying() -> None:
    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    result = service.parse(
        '{"intents":["performance"],"confidence":0.0,"period":{"kind":"month","value":null},'
        '"entities":[],"resolved_query":"","needs_clarification":false}',
        question="本月赚了多少？",
        focus={},
    )
    assert result.needs_clarification is False
    assert result.period is not None and result.period.value == "2026-11"
    assert result.intents[0].value == "portfolio"


def test_profit_question_routes_to_portfolio_assets() -> None:
    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    result = service.fallback_understanding("本月赚了多少？", {})
    assert result.intents[0].value == "portfolio"
    assert result.period is not None and result.period.value == "2026-11"


def test_explicit_portfolio_intent_overrides_model_performance() -> None:
    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    result = service.parse(
        '{"intents":["performance"],"confidence":0.9,"period":{"kind":"month","value":"2026-11"},'
        '"entities":[],"resolved_query":"本月赚了多少","needs_clarification":false}',
        question="本月赚了多少？",
        focus={},
    )
    assert result.intents[0].value == "portfolio"


def test_top_loss_question_routes_to_rounds() -> None:
    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    result = service.fallback_understanding("这个月亏损最多的股票是哪一个", {})
    assert result.intents[0].value == "rounds"


def test_object_shaped_intents_are_repaired_and_logged() -> None:
    from structlog.testing import capture_logs

    service = UnderstandingService(FakeProvider(), FrozenClock(datetime(2026, 11, 20, tzinfo=UTC)))
    with capture_logs() as logs:
        result = service.parse(
            '{"intents":[{"name":"trades","params":{"filter":"loss"}}],'
            '"confidence":0.9,"period":{"kind":"month","value":"2026-11"},'
            '"entities":[],"resolved_query":"本月亏损最多","needs_clarification":false}',
            question="这个月亏损最多的股票是哪一个",
            focus={},
        )
    assert result.intents[0].value == "rounds"
    assert any(
        item.get("event") == "chat.llm.bad_response" and item.get("recovered") is True
        for item in logs
    )
