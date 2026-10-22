"""分类应用层：按摘要规则给明细打业务分类，并把未命中行送进隔离区。

为什么需要它：券商摘要文本不统一，业务分类必须可复算。命中规则 → 写 `trade.category`；
未命中 → 落隔离区 `UNKNOWN_SUMMARY` 并给出建议规则，用户在确认单上补规则后重跑。
`seed_system_rules` 提供一期开箱即用的默认规则。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import JobStatus, QuarantineReason
from stock_platform.jobs import JobQueue

from stock_ledger.models import SummaryRule
from stock_ledger.parsers.columns import normalize_summary
from stock_ledger.repositories.quarantine import QuarantineRepository
from stock_ledger.repositories.rules import (
    SummaryRuleRepository,
    rule_summary_code,
    seed_system_rules,
)
from stock_ledger.repositories.trades import TradeRepository


async def load_rules(session: AsyncSession, user_id: uuid.UUID) -> list[SummaryRule]:
    """确保系统默认规则存在，并返回该用户生效规则（预览 / 提交 / 复算共用）。

    为什么在应用层自愈：系统规则是代码资产，新环境或老库若没跑过 CLI seed，分类会全量
    未命中（真实账单整片误隔离）。`seed_system_rules` 幂等 upsert + 清理废弃码，
    因此任何入口先调它都能保证词表与代码一致，且重复调用无副作用。
    """
    await seed_system_rules(session)
    return await SummaryRuleRepository(session).list_enabled(user_id)


def _matches(rule: SummaryRule, summary: str, side: str) -> bool:
    if rule.side != "both" and rule.side != side:
        return False
    if rule.match_type == "regex":
        # 正则按原文匹配（用户可能显式依赖换行/空格）；其余匹配类型统一去折行。
        try:
            return re.search(rule.pattern, summary) is not None
        except re.error:
            return False
    text = normalize_summary(summary)
    pattern = normalize_summary(rule.pattern)
    if rule.match_type == "exact":
        return text == pattern
    if rule.match_type == "prefix":
        return text.startswith(pattern)
    if rule.match_type == "contains":
        return pattern in text
    return False


def match_rule(rules: list[SummaryRule], summary: str | None, side: str) -> SummaryRule | None:
    """按优先级顺序返回首个命中规则（rules 已按 priority 升序）。"""
    if not summary:
        return None
    for rule in rules:
        if _matches(rule, summary, side):
            return rule
    return None


def suggest_rule(rules: list[SummaryRule], summary: str | None, side: str) -> str | None:
    """给未命中摘要推荐一条“最接近”的规则 id（无则 None）。"""
    if not summary:
        return None
    text = normalize_summary(summary)
    best: SummaryRule | None = None
    for rule in rules:
        if rule.side != "both" and rule.side != side:
            continue
        if rule.match_type not in ("prefix", "contains"):
            continue
        pattern = normalize_summary(rule.pattern)
        if (text.startswith(pattern) or pattern in text) and (
            best is None or rule.priority < best.priority
        ):
            best = rule
    return best.id if best is not None else None


async def reclassify_user(
    queue: JobQueue, session: AsyncSession, job: dict[str, Any], clock: Clock
) -> dict[str, Any]:
    """复算某用户的业务分类；未命中送隔离区。

    `job.params.batch_id` 存在时只复算该导入批次的明细（API-016 按批次重跑分类），
    否则全量复算（增量规则生效后的兜底路径）。
    """
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params = job.get("params") or {}
    batch_id = params.get("batch_id")
    rules_repo = SummaryRuleRepository(session)
    trade_repo = TradeRepository(session)
    quarantine_repo = QuarantineRepository(session)

    rules = await load_rules(session, user_id)
    trades = await trade_repo.list_all(user_id)
    if batch_id is not None:
        # 只动目标批次，避免一次规则调整把历史其它批次的行一起重写。
        trades = [trade for trade in trades if trade.batch_id == batch_id]
    existing_quarantine = await quarantine_repo.page(user_id, resolved=False, limit=200)
    already_flagged = {
        str(row.raw.get("trade_id")) for row in existing_quarantine if row.raw.get("trade_id")
    }

    total = max(len(trades), 1)
    updated = 0
    quarantined = 0
    for index, trade in enumerate(trades):
        rule = match_rule(rules, trade.summary, trade.side)
        if rule is not None:
            summary_code = rule_summary_code(rule)
            if trade.category != rule.category or trade.summary_code != summary_code:
                trade.category = rule.category
                trade.summary_code = summary_code
                updated += 1
        elif trade.id not in already_flagged:
            await quarantine_repo.add(
                user_id,
                reason_code=QuarantineReason.UNKNOWN_SUMMARY.value,
                raw={"trade_id": trade.id, "summary": trade.summary},
                batch_id=trade.batch_id,
                suggested_rule_id=suggest_rule(rules, trade.summary, trade.side),
            )
            already_flagged.add(trade.id)
            quarantined += 1
        if index % 25 == 0:
            percent = min(90, 5 + int(index / total * 85))
            await queue.progress(
                session, job_id, percent=percent, stage="recomputing", message=None
            )

    backfilled = await _backfill_resolved(user_id, rules_repo, trade_repo, quarantine_repo)
    await session.flush()

    result = {"updated": updated, "quarantined": quarantined, "backfilled": backfilled}
    await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
    return result


async def _backfill_resolved(
    user_id: uuid.UUID,
    rules_repo: SummaryRuleRepository,
    trade_repo: TradeRepository,
    quarantine_repo: QuarantineRepository,
) -> int:
    """已 resolve 的隔离行：把建议规则的分类回填到对应明细。"""
    resolved_rows = await quarantine_repo.page(user_id, resolved=True, limit=200)
    backfilled = 0
    for row in resolved_rows:
        if not row.resolved_trade_id or not row.suggested_rule_id:
            continue
        rule = await rules_repo.by_id(user_id, row.suggested_rule_id)
        if rule is None:
            continue
        trade = await trade_repo.by_id(user_id, row.resolved_trade_id)
        if trade is None or trade.category is not None:
            continue
        trade.category = rule.category
        backfilled += 1
    return backfilled


__all__ = [
    "load_rules",
    "match_rule",
    "reclassify_user",
    "seed_system_rules",
    "suggest_rule",
]
