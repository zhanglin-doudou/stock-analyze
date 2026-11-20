"""确定性模板答案：无 LLM 也能给出金额/引用。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from stock_contracts.chat.dto import AnswerDraft, Citation, Claim, ToolResult, Understanding
from stock_contracts.chat.enums import ChatStatus, DegradeReason, Intent

from .context import ContextBundle


def _number(citations: Sequence[Citation]) -> list[Citation]:
    return [
        Citation(
            citation_id=item.citation_id,
            label=item.label,
            value_display=item.value_display,
            source_endpoint=item.source_endpoint,
            source_params=item.source_params,
            definition_version=item.definition_version,
            data_as_of=item.data_as_of,
            number=index,
        )
        for index, item in enumerate(citations, start=1)
    ]


def compose(
    *,
    question: str,
    understanding: Understanding,
    results: Sequence[ToolResult],
    context: ContextBundle,
) -> AnswerDraft:
    citations = _number([item for result in results for item in result.citations])
    if understanding.needs_clarification:
        answer = understanding.clarification or "请补充一下你问的是哪只证券或哪个时间范围。"
        return AnswerDraft(
            answer_markdown=answer,
            status=ChatStatus.NEEDS_CLARIFICATION,
            confidence="low",
            context_usage=context.usage,
        )

    lines: list[str] = []
    claims: list[Claim] = []
    data = _merge_data(results)
    status = _overall_status(results)
    if not results or status == ChatStatus.ERROR:
        return AnswerDraft(
            answer_markdown="暂时没有取到可用于回答的数据。你可以换一个时间范围，或先导入账单/构建资产表。",
            status=ChatStatus.DEGRADED,
            degrade_reason=DegradeReason.TOOL_UNAVAILABLE,
            degrade_message="数据工具暂不可用。",
            what_to_do="稍后重试；若持续失败，请查看数据质量。",
            retryable=True,
            fallback=True,
            context_usage=context.usage,
        )

    intent = understanding.intents[0] if understanding.intents else Intent.UNKNOWN
    if intent in {Intent.ASSETS, Intent.PORTFOLIO}:
        lines, claims = _portfolio(lines, claims, data, citations)
    elif intent is Intent.PERFORMANCE:
        lines, claims = _metrics(lines, claims, data, citations)
    elif intent is Intent.ATTRIBUTION:
        lines, claims = _attribution(lines, claims, data, citations)
    elif intent in {Intent.ROUNDS, Intent.ROUND_DETAIL}:
        lines, claims = _rounds(lines, claims, data, citations)
    elif intent is Intent.TRADES:
        lines, claims = _trades(lines, claims, data, citations)
    elif intent is Intent.NAV:
        lines, claims = _nav(lines, claims, data, citations)
    elif intent is Intent.SECTORS:
        lines, claims = _sectors(lines, claims, data, citations)
    elif intent is Intent.DATA_QUALITY:
        lines, claims = _quality(lines, claims, data, citations)
    else:
        lines, claims = _generic(lines, claims, data, citations)

    if not lines:
        lines = ["我先按当前数据给出一个简要结论。"]
    if any(result.missing for result in results):
        lines.append("部分数据缺失或降级，以上结论只基于可用数据。")
    answer = "\n".join(f"- {line}" for line in lines)
    return AnswerDraft(
        answer_markdown=answer,
        claims=tuple(claims),
        used_citations=tuple(item.citation_id for item in citations),
        status=status,
        fallback=True,
        context_usage=context.usage,
    )


def _merge_data(results: Iterable[ToolResult]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for result in results:
        out.setdefault(str(result.tool), {}).update(dict(result.data))
    return out


def _overall_status(results: Sequence[ToolResult]) -> ChatStatus:
    statuses = [result.status for result in results]
    if any(item is ChatStatus.BLOCKED for item in statuses):
        return ChatStatus.BLOCKED
    if any(item is ChatStatus.ERROR for item in statuses):
        return ChatStatus.DEGRADED
    if any(item is ChatStatus.DEGRADED for item in statuses):
        return ChatStatus.DEGRADED
    return ChatStatus.OK


def _citation(citations: Sequence[Citation], label: str) -> Citation | None:
    return next((item for item in citations if item.label == label), None)


def _claim(
    claims: list[Claim], text: str, citations: Sequence[Citation], labels: Sequence[str]
) -> None:
    ids = [item.citation_id for item in citations if item.label in labels]
    if ids:
        claims.append(Claim(claim_id=f"k{len(claims) + 1}", text=text, citation_ids=tuple(ids)))


def _portfolio(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("portfolio") or data.get("portfolio_snapshot") or {}
    asset = block.get("asset") or {}
    history = block.get("history") or {}
    total = asset.get("total_assets")
    profit = asset.get("total_profit")
    win = history.get("win_ratio_pct")
    mdd = (history.get("max_drawdown") or {}).get("mdd_pct")
    if total:
        lines.append(f"当前总资产 {total}。")
        _claim(claims, f"当前总资产 {total}", citations, ["总资产"])
    if profit:
        lines.append(f"累计已实现收益 {profit}。")
    if win:
        lines.append(f"历史平仓胜率 {win}。")
    if mdd:
        lines.append(f"历史最大回撤 {mdd}。")
    if not lines:
        lines.append("资产表暂无可展示数据。")
    return lines, claims


def _metrics(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    items = data.get("performance_metrics", {}).get("items", [])
    for item in items[:8]:
        if item.get("value") is not None:
            lines.append(f"{item['metric_id']}：{item['value']}（n={item.get('n')}）。")
            _claim(
                claims,
                f"{item['metric_id']} {item['value']}",
                citations,
                [f"指标 {item['metric_id']}"],
            )
    if not lines:
        lines.append("指标暂无可展示数值，可能是样本不足或数据未构建。")
    return lines, claims


def _attribution(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("attribution_summary", {})
    if block.get("summary"):
        lines.append(block["summary"])
    for factor in block.get("factors", [])[:5]:
        if factor.get("amount") is not None:
            lines.append(
                f"{factor['name']}：{factor['amount']}（占比 {factor.get('share_pct') or '—'}）。"
            )
    if not lines:
        lines.append("归因暂不可用，可能是资产表被阻断或区间数据不足。")
    return lines, claims


def _rounds(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("rounds_query", {})
    items = block.get("items", [])
    if block.get("flag_glossary"):
        lines.append("回合旗标解释已随结果返回。")
    for item in items[:8]:
        flags = "、".join(item.get("flags") or []) or "无旗标"
        lines.append(f"{item['security_name']}：盈亏 {item['pnl']}，旗标 {flags}。")
    if not items:
        lines.append("该区间没有符合条件的回合。")
    return lines, claims


def _trades(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("trades_query", {})
    items = block.get("items", [])
    lines.append(f"该区间共 {len(items)} 笔流水。")
    for item in items[:8]:
        flags = "、".join(flag["code"] for flag in item.get("flags") or []) or "无旗标"
        lines.append(
            f"{item['trade_date']} {item['security_name']} {item['side']} {item['amount']}，旗标 {flags}。"
        )
    if block.get("flag_glossary"):
        lines.append("旗标解释见 citations/flag_glossary。")
    return lines, claims


def _nav(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("nav_summary", {})
    summary = block.get("summary") or {}
    if summary.get("latest_total_assets"):
        lines.append(f"期末总资产 {summary['latest_total_assets']}。")
    if summary.get("twr_pct"):
        lines.append(f"区间 TWR {summary['twr_pct']}。")
    if summary.get("max_drawdown_pct"):
        lines.append(f"最大回撤 {summary['max_drawdown_pct']}。")
    if not lines:
        lines.append("资产序列暂不可用。")
    return lines, claims


def _sectors(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("sector_exposure", {})
    rows = block.get("sectors", [])
    for row in rows[:8]:
        weight = row.get("weight_pct") or row.get("exposure_pct") or "—"
        lines.append(f"{row['sector_name']}：{weight}。")
    if not rows:
        lines.append("板块数据暂不可用或尚未同步。")
    return lines, claims


def _quality(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    block = data.get("data_quality", {})
    build = block.get("build_state") or {}
    lines.append(f"资产表状态：{build.get('state')}，数据截至 {build.get('data_as_of') or '—'}。")
    rec = block.get("reconciliation") or {}
    if rec.get("break_count") is not None:
        lines.append(f"对账断链 {rec['break_count']} 处。")
    return lines, claims


def _generic(
    lines: list[str],
    claims: list[Claim],
    data: dict[str, Any],
    citations: Sequence[Citation],
) -> tuple[list[str], list[Claim]]:
    lines.append("我按当前可用数据给出摘要；你可以把问题缩到具体月份、证券或回合。")
    return lines, claims
