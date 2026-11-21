"""回答校验：数字白名单、引用覆盖、禁建议词、HTML 清洗。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_contracts.chat.dto import Citation

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")
_HTML = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://\S+|www\.\S+")
_MARKER = re.compile(r"\[\[(c\d+(?:_\d+)?|\d+)\]\]")
_BANNED = (
    "建议买入",
    "建议卖出",
    "目标价",
    "必涨",
    "必跌",
    "稳赚",
    "保证收益",
    "全仓",
)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def sanitize(text: str) -> str:
    text = _HTML.sub("", text)
    text = _URL.sub("[外链已移除]", text)
    return text.strip()


def normalize_number(value: str) -> str:
    """把后端定点字符串与模型自然表述归一为同一数字。

    后端常给 ``215.300000`` / ``78000.00``，模型会写 ``215.30`` / ``78000``；
    如果按原字符串比较会被误判为 unknown_number。
    """
    cleaned = value.replace(",", "").replace("元", "").removesuffix("%").strip()
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return cleaned
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def validate(
    *,
    answer: str,
    claims: Sequence[Any],
    citations: Sequence[Citation],
    allow_tokens: Iterable[str] = (),
) -> ValidationResult:
    errors: list[str] = []
    clean = sanitize(answer)
    if not clean:
        errors.append("empty_answer")
    numeric_text = _MARKER.sub(" ", clean)
    allowed = {normalize_number(item.value_display) for item in citations}
    allowed |= {normalize_number(item) for item in allow_tokens}
    for token in _NUMBER.findall(numeric_text):
        normalized = normalize_number(token)
        if normalized not in allowed:
            errors.append(f"unknown_number:{token}")
    if any(word in clean for word in _BANNED):
        errors.append("banned_advice")
    markers = set(_MARKER.findall(clean))
    citation_ids = {item.citation_id for item in citations}
    citation_numbers = {str(item.number) for item in citations if item.number is not None}
    for marker in markers:
        if (
            marker not in citation_ids
            and marker not in citation_numbers
            and f"c{marker}" not in citation_ids
        ):
            errors.append(f"unknown_citation:{marker}")
    for claim in claims:
        if isinstance(claim, dict):
            text = str(claim.get("text") or "")
            ids = claim.get("citation_ids") or ()
            claim_id = claim.get("claim_id") or "?"
        else:
            text = getattr(claim, "text", "")
            ids = getattr(claim, "citation_ids", ())
            claim_id = getattr(claim, "claim_id", "?")
        if _NUMBER.search(text) and not ids:
            errors.append(f"claim_without_citation:{claim_id}")
    return ValidationResult(ok=not errors, errors=errors[:10])
