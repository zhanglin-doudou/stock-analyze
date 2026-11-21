"""语义理解、意图/期间/实体抽取、指代替换与规则兜底。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Any

from json_repair import repair_json
from pydantic import BaseModel, Field, ValidationError
from stock_contracts.chat.dto import PeriodSpec, ResolvedEntity, Understanding
from stock_contracts.chat.enums import Intent
from stock_contracts.clock import Clock
from stock_platform.obs import get_logger

from .provider import LlmError, LlmProvider

log = get_logger("stock_delivery.chat.understanding")


class _PeriodIn(BaseModel):
    kind: str = "none"
    value: str | None = None
    label: str | None = None


class _EntityIn(BaseModel):
    mention: str
    type: str = "security"
    resolved_id: str = ""
    resolved_name: str | None = None
    source: str = "focus"
    confidence: float = 0.0


class _UnderstandingIn(BaseModel):
    intents: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    period: _PeriodIn | None = None
    entities: list[_EntityIn] = Field(default_factory=list)
    resolved_query: str = ""
    needs_clarification: bool = False
    clarification: str | None = None


_FIXED: tuple[tuple[tuple[str, ...], Intent], ...] = (
    (("为什么", "归因", "拖累", "贡献"), Intent.ATTRIBUTION),
    (("胜率", "盈亏比", "回撤", "持有", "夏普", "收益", "赚", "亏", "费用"), Intent.PERFORMANCE),
    (("最赚", "最亏", "回合", "交易次数"), Intent.ROUNDS),
    (
        ("流水", "交易明细", "交易", "操作", "买卖", "成交", "旗标", "追涨", "卖飞", "抄底"),
        Intent.TRADES,
    ),
    (("资产", "现金", "仓位", "持仓"), Intent.ASSETS),
    (("净值", "年化", "twr", "xirr"), Intent.NAV),
    (("板块", "行业", "概念", "集中"), Intent.SECTORS),
    (("数据", "可信", "断链", "缺失", "对账"), Intent.DATA_QUALITY),
    (("指标怎么算", "口径", "定义"), Intent.METRIC_EXPLAIN),
)
_PRONOUNS = ("它", "这个", "那个", "那笔", "这只", "那只", "该股", "该回合")

# “赚了多少 / 赚了还是亏了”问的是资产表的累计收益，不是指标页的收益率。
_ROUNDS_PATTERNS = (
    "亏损最多",
    "亏得最多",
    "最亏",
    "盈利最多",
    "赚得最多",
    "最赚",
)

_PORTFOLIO_PATTERNS = (
    "赚了多少",
    "赚了还是亏了",
    "赚还是亏",
    "是赚是亏",
    "赚了没",
    "亏了没",
    "总收益",
    "累计收益",
    "累计盈亏",
    "盈利多少",
    "亏损多少",
)


def _explicit_intents(question: str) -> tuple[Intent, ...] | None:
    if any(pattern in question for pattern in _ROUNDS_PATTERNS):
        return (Intent.ROUNDS,)
    if not any(pattern in question for pattern in _PORTFOLIO_PATTERNS):
        return None
    intents: list[Intent] = [Intent.PORTFOLIO]
    if "收益率" in question or "胜率" in question or "回撤" in question:
        intents.append(Intent.PERFORMANCE)
    return tuple(intents)


class UnderstandingService:
    def __init__(self, llm: LlmProvider, clock: Clock) -> None:
        self.llm = llm
        self.clock = clock

    async def understand(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        question: str,
        focus: Mapping[str, Any],
    ) -> Understanding:
        try:
            result = await self.llm.complete_messages(
                purpose="understanding",
                messages=messages,
                max_tokens=2048,
                json_mode=True,
            )
            return self.parse(result.content, question=question, focus=focus)
        except (LlmError, ValidationError, ValueError, json.JSONDecodeError):
            return self.fallback_understanding(question, focus)

    def parse(self, content: str, *, question: str, focus: Mapping[str, Any]) -> Understanding:
        return self._normalize(self._parse(content), question=question, focus=focus)

    def fallback_understanding(self, question: str, focus: Mapping[str, Any]) -> Understanding:
        return self._fallback(question, focus)

    def _parse(self, content: str) -> _UnderstandingIn:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = json.loads(repair_json(content))
        if not isinstance(raw, dict):
            raise ValueError("understanding 不是 JSON object")
        intents = raw.get("intents")
        if isinstance(intents, list) and any(not isinstance(item, str) for item in intents):
            repaired: list[str] = []
            for item in intents:
                if isinstance(item, str):
                    repaired.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("intent") or item.get("type")
                    if isinstance(name, str) and name:
                        repaired.append(name)
            log.error(
                "chat.llm.bad_response",
                purpose="understanding",
                error="intents 不是字符串数组，已按 name 规范化",
                recovered=True,
                raw_intents=intents[:4],
            )
            raw = {**raw, "intents": repaired}
        return _UnderstandingIn.model_validate(raw)

    def _normalize(
        self, raw: _UnderstandingIn, *, question: str, focus: Mapping[str, Any]
    ) -> Understanding:
        # glm-4-flash 等小模型会返回 confidence=0、resolved_query="" 但 intents 正确；
        # 这种“结构化失败”不能直接当成低置信度澄清，否则每个问题都只回“请补充”。
        if raw.confidence <= 0.0 or not raw.resolved_query.strip():
            return self._fallback(question, focus)
        intents = (
            _explicit_intents(question)
            or tuple(_intent(value) for value in raw.intents[:2])
            or (Intent.UNKNOWN,)
        )
        entities = tuple(_entity(item) for item in raw.entities)
        needs_clarification = raw.needs_clarification or raw.confidence < 0.6
        resolved_query = raw.resolved_query.strip() or question
        # 指代只允许从 focus 解析；没有唯一焦点就要求澄清。
        if any(token in resolved_query for token in _PRONOUNS) and not entities:
            needs_clarification = True
        period = _period(raw.period)
        if period is None or period.value is None:
            rule_period = _rule_period(question, self.clock.today())
            if rule_period.value:
                # 模型常返回 {"kind":"month","value":null,"label":"本月"}；用规则补全，
                # 否则 _tools_for 不会传 period，工具会退化成默认区间。
                period = replace(
                    period or rule_period,
                    kind=rule_period.kind,
                    value=rule_period.value,
                    label=period.label if period and period.label else rule_period.label,
                )
        return Understanding(
            intents=intents,
            confidence=max(0.0, min(1.0, raw.confidence)),
            period=period,
            entities=entities,
            resolved_query=resolved_query,
            needs_clarification=needs_clarification,
            clarification=raw.clarification,
            raw=raw.model_dump(),
        )

    def _fallback(self, question: str, focus: Mapping[str, Any]) -> Understanding:
        intents = _explicit_intents(question) or tuple(
            intent for words, intent in _FIXED if any(word in question for word in words)
        )
        if not intents:
            intents = (Intent.UNKNOWN,)
        period = _rule_period(question, self.clock.today())
        entities: list[ResolvedEntity] = []
        if any(token in question for token in _PRONOUNS):
            entity = _focus_entity(focus)
            if entity is not None:
                entities.append(entity)
        needs = len(intents) > 1 and intents[0] is Intent.UNKNOWN
        return Understanding(
            intents=intents[:2],
            confidence=0.55,
            period=period,
            entities=tuple(entities),
            resolved_query=question,
            needs_clarification=needs,
            clarification="你问的是哪只证券/哪个回合？" if needs else None,
            raw={"fallback": True},
        )


def _intent(value: str) -> Intent:
    try:
        return Intent(value)
    except ValueError:
        return Intent.UNKNOWN


def _entity(item: _EntityIn) -> ResolvedEntity:
    entity_type = (
        item.type if item.type in {"security", "round", "metric", "sector", "flag"} else "security"
    )
    source = (
        item.source
        if item.source in {"focus", "recent_tool", "db_match", "user_explicit"}
        else "focus"
    )
    return ResolvedEntity(
        mention=item.mention,
        type=entity_type,  # type: ignore[arg-type]
        resolved_id=item.resolved_id,
        resolved_name=item.resolved_name,
        source=source,  # type: ignore[arg-type]
        confidence=max(0.0, min(1.0, item.confidence)),
    )


def _period(value: _PeriodIn | None) -> PeriodSpec | None:
    if value is None:
        return None
    kind = (
        value.kind
        if value.kind in {"month", "year", "range", "all", "relative", "none"}
        else "none"
    )
    return PeriodSpec(kind=kind, value=value.value, label=value.label)  # type: ignore[arg-type]


def _focus_entity(focus: Mapping[str, Any]) -> ResolvedEntity | None:
    for key, kind in (
        ("round_id", "round"),
        ("security_code", "security"),
        ("metric_id", "metric"),
    ):
        value = focus.get(key)
        if value:
            return ResolvedEntity(
                mention="它",
                type=kind,  # type: ignore[arg-type]
                resolved_id=str(value),
                resolved_name=str(focus.get("security_name") or focus.get("metric_name") or ""),
                source="focus",
                confidence=0.9,
            )
    return None


def _rule_period(question: str, today: date) -> PeriodSpec:
    match = re.search(r"(20\d{2})-(\d{2})", question)
    if match:
        return PeriodSpec(kind="month", value=match.group(0), label=match.group(0))
    match = re.search(r"(20\d{2})", question)
    if match:
        return PeriodSpec(kind="year", value=match.group(1), label=match.group(1))
    if "上个月" in question:
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        value = f"{year:04d}-{month:02d}"
        return PeriodSpec(kind="month", value=value, label="上个月")
    if "今年" in question:
        return PeriodSpec(kind="year", value=str(today.year), label="今年")
    if "本月" in question or "这个月" in question:
        value = f"{today.year:04d}-{today.month:02d}"
        return PeriodSpec(kind="month", value=value, label="本月")
    return PeriodSpec(kind="none")
