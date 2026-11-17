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
from stock_contracts.enums import BillCategory, JobStatus, QuarantineReason, Side
from stock_platform.jobs import JobQueue

from stock_ledger.models import ImportBillType, SummaryRule
from stock_ledger.parsers.columns import normalize_summary
from stock_ledger.parsers.mapping import derive_type_key, normalize_type_key
from stock_ledger.repositories.import_mapping import ImportBillTypeRepository
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


#: Category → 买卖方向：只有 buy/new_stock 是买入、sell 是卖出，其余方向 unknown。
_CATEGORY_TO_SIDE: dict[str, str] = {
    BillCategory.BUY.value: Side.BUY.value,
    BillCategory.NEW_STOCK.value: Side.BUY.value,
    BillCategory.SELL.value: Side.SELL.value,
}


def side_from_category(category: str | None) -> str:
    """按业务分类派生买卖方向（buy/new_stock→buy，sell→sell，其余→unknown）。"""
    if category is None:
        return Side.UNKNOWN.value
    return _CATEGORY_TO_SIDE.get(category, Side.UNKNOWN.value)


#: 规则命中的 category 若在零金额 fallback 表里且 amount_units==0，降级到右侧 category。
# 设计意图：pattern 匹配只管"摘要文字说什么"，amount 分流管"这笔有没有实际资金进出"；
# 两者解耦避免把规则表搞成条件组合引擎（运维改 pattern 还要记得带 amount 条件）。
# 后期加新规则只需在这里追加一行，规则表 pattern 保持纯语义。
_CATEGORY_ZERO_AMOUNT_FALLBACK: dict[str, str] = {
    BillCategory.NEW_STOCK.value: BillCategory.BROKER_ACTION.value,
}


def category_after_amount_fallback(category: str, amount_units: int) -> str:
    """零金额登记动作的分类降级；amount_units!=0 时原样返回。"""
    if amount_units == 0:
        return _CATEGORY_ZERO_AMOUNT_FALLBACK.get(category, category)
    return category


def _matches(rule: SummaryRule, summary: str) -> bool:
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


def match_rule(rules: list[SummaryRule], summary: str | None) -> SummaryRule | None:
    """按优先级顺序返回首个命中规则（rules 已按 priority 升序）。方向由 Category 决定，不再按 side 过滤。"""
    if not summary:
        return None
    for rule in rules:
        if _matches(rule, summary):
            return rule
    return None


def resolve_type_category(
    type_key: str,
    *,
    known_types: dict[str, ImportBillType],
    rules: list[SummaryRule],
    summary: str | None,
    amount_units: int = 0,
) -> tuple[str | None, str | None, bool]:
    """返回 `(category, summary_code, discarded)`；用户类型记忆优先于摘要规则。

    category 在返回前统一走 `category_after_amount_fallback`，零金额登记动作自动降级
    到 broker_action（由 `_CATEGORY_ZERO_AMOUNT_FALLBACK` 集中维护）。
    """
    known = known_types.get(normalize_type_key(type_key))
    rule = match_rule(rules, summary)
    summary_code = rule_summary_code(rule) if rule is not None else None
    if known is not None:
        if known.discarded:
            return None, None, True
        # known.discarded=False → known.category 非 None（DB CheckConstraint 保证）
        assert known.category is not None, "ImportBillType: non-discarded 行必须有 category"
        final = category_after_amount_fallback(known.category, amount_units)
        return final, summary_code, False
    if rule is not None:
        final = category_after_amount_fallback(rule.category, amount_units)
        return final, summary_code, False
    return None, None, False


def suggest_rule(rules: list[SummaryRule], summary: str | None) -> str | None:
    """给未命中摘要推荐一条“最接近”的规则 id（无则 None）。"""
    if not summary:
        return None
    text = normalize_summary(summary)
    best: SummaryRule | None = None
    for rule in rules:
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
    type_keys = {
        trade.id: normalize_type_key(trade.summary)
        or derive_type_key(trade.amount_units, trade.cash_balance_units or 0)
        for trade in trades
    }
    known_types = await ImportBillTypeRepository(session).by_keys(
        user_id, sorted(set(type_keys.values()))
    )
    existing_quarantine = await quarantine_repo.page(user_id, resolved=False, limit=200)
    already_flagged = {
        str(row.raw.get("trade_id")) for row in existing_quarantine if row.raw.get("trade_id")
    }

    total = max(len(trades), 1)
    updated = 0
    quarantined = 0
    for index, trade in enumerate(trades):
        category, summary_code, discarded = resolve_type_category(
            type_keys[trade.id],
            known_types=known_types,
            rules=rules,
            summary=trade.summary,
            amount_units=trade.amount_units,
        )
        if discarded:
            # 废弃只影响后续导入；复算不删除、不改写已经存在的历史流水。
            continue
        if category is not None:
            derived_side = side_from_category(category)
            if (
                trade.category != category
                or trade.summary_code != summary_code
                or trade.side != derived_side
            ):
                trade.category = category
                trade.summary_code = summary_code
                trade.side = derived_side
                updated += 1
        elif trade.id not in already_flagged:
            await quarantine_repo.add(
                user_id,
                reason_code=QuarantineReason.UNKNOWN_SUMMARY.value,
                raw={"trade_id": trade.id, "summary": trade.summary},
                batch_id=trade.batch_id,
                suggested_rule_id=suggest_rule(rules, trade.summary),
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
        trade.category = category_after_amount_fallback(rule.category, trade.amount_units)
        backfilled += 1
    return backfilled


__all__ = [
    "category_after_amount_fallback",
    "load_rules",
    "match_rule",
    "reclassify_user",
    "resolve_type_category",
    "seed_system_rules",
    "suggest_rule",
]
