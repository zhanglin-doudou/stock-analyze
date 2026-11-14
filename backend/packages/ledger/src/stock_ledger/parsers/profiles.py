"""券商画像与自动识别（设计 §3.4 / D-09）。

识别只用「指纹规则 + 静态精确表头」，**绝不凭文件名/扩展名猜测**；置信度不足一律回落
`generic`，由用户显式选择画像。画像只影响解析策略（如省心投、货基余额），不改变列映射。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from stock_contracts.import_fields import HUATAI_FINGERPRINT_HEADERS, broker_label

from stock_ledger.parsers.mapping import normalize_header_v2
from stock_ledger.parsers.table import rule_matches

#: 判定 high 所需的最少指纹命中数（设计：精确命中 ≥ 6）。
HIGH_CONFIDENCE_HITS: Final[int] = 6
#: 判定 medium 所需的最少命中数；低于此值回落 generic。
MEDIUM_CONFIDENCE_HITS: Final[int] = 3


class FingerprintRuleLike(Protocol):
    @property
    def broker(self) -> str: ...

    @property
    def header_norm(self) -> str: ...

    @property
    def match_type(self) -> str: ...

    @property
    def is_fingerprint(self) -> bool: ...

    @property
    def enabled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class BrokerProfile:
    """运行期画像：解析器据此决定省心投等券商特有语义是否生效。"""

    code: str
    label: str
    apply_sxt: bool
    #: 货基行缺少「货基余额」映射时是否 fail-closed。
    require_money_fund_balance: bool


PROFILES: Final[dict[str, BrokerProfile]] = {
    "huatai": BrokerProfile(
        code="huatai",
        label=broker_label("huatai"),
        apply_sxt=True,
        require_money_fund_balance=False,
    ),
    "generic": BrokerProfile(
        code="generic",
        label=broker_label("generic"),
        apply_sxt=False,
        require_money_fund_balance=True,
    ),
}


@dataclass(slots=True)
class BrokerDetection:
    code: str
    confidence: str
    ambiguous: bool
    evidence: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _attr(rule: Any, name: str, default: Any = None) -> Any:
    if isinstance(rule, dict):
        return rule.get(name, default)
    return getattr(rule, name, default)


def detect_broker(
    headers: Sequence[str], rules: Sequence[FingerprintRuleLike] = ()
) -> BrokerDetection:
    """按指纹规则 + 静态精确表头打分；返回最高分画像（低置信回落 generic）。"""
    per_broker: dict[str, list[str]] = {}
    for rule in rules:
        if not _attr(rule, "enabled", True) or not _attr(rule, "is_fingerprint", False):
            continue
        broker = str(_attr(rule, "broker", ""))
        if not broker:
            continue
        for header in headers:
            if not header:
                continue
            if rule_matches(normalize_header_v2(header), rule):  # type: ignore[arg-type]
                per_broker.setdefault(broker, []).append(header)
    huatai_hits = [
        header
        for header in headers
        if normalize_header_v2(header)
        in {normalize_header_v2(item) for item in HUATAI_FINGERPRINT_HEADERS}
    ]
    if huatai_hits:
        per_broker.setdefault("huatai", []).extend(huatai_hits)
    scored = {
        broker: sorted(set(evidence))
        for broker, evidence in per_broker.items()
        if broker != "generic"
    }
    if not scored:
        return BrokerDetection(
            code="generic",
            confidence="low",
            ambiguous=False,
            evidence=[],
            candidates=[],
        )
    ranked = sorted(scored.items(), key=lambda pair: len(pair[1]), reverse=True)
    top_broker, top_evidence = ranked[0]
    hits = len(top_evidence)
    confidence = (
        "high"
        if hits >= HIGH_CONFIDENCE_HITS
        else "medium"
        if hits >= MEDIUM_CONFIDENCE_HITS
        else "low"
    )
    ambiguous = len(ranked) > 1 and hits >= MEDIUM_CONFIDENCE_HITS and hits - len(ranked[1][1]) <= 1
    code = top_broker if hits >= MEDIUM_CONFIDENCE_HITS else "generic"
    return BrokerDetection(
        code=code,
        confidence=confidence if code != "generic" else "low",
        ambiguous=ambiguous,
        evidence=top_evidence if code != "generic" else [],
        candidates=[
            {"broker": broker, "label": broker_label(broker), "hits": len(evidence)}
            for broker, evidence in ranked
        ],
    )


def profile_for(code: str) -> BrokerProfile:
    return PROFILES.get(code, PROFILES["generic"])


__all__ = [
    "HIGH_CONFIDENCE_HITS",
    "MEDIUM_CONFIDENCE_HITS",
    "PROFILES",
    "BrokerDetection",
    "BrokerProfile",
    "FingerprintRuleLike",
    "detect_broker",
    "profile_for",
]
