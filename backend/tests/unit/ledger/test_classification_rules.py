"""G3 规则词表单测：真实账单摘要全部命中、优先级正确、规则短码可落 `trade.summary_code`。

覆盖两类真实账单形态：普通证券账户（华泰 mock）与货币基金账户（申赎确认/红利发放）。
纯函数（不连库）：用 `SYSTEM_RULES` 静电数据构造未持久化规则对象，直接驱动 `match_rule`。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from stock_contracts.clock import FrozenClock
from stock_ledger.application.classify import match_rule
from stock_ledger.models import SummaryRule
from stock_ledger.parsers.columns import (
    FUND_SUMMARY_SIDE,
    NON_TRADE_SUMMARY_SIDE,
    TRANSFER_SUMMARY_KEYWORDS,
)
from stock_ledger.parsers.huatai import parse_bill
from stock_ledger.repositories.rules import (
    SUMMARY_CODE_MAX_LENGTH,
    SYSTEM_RULES,
    rule_summary_code,
)


def _rules() -> list[SummaryRule]:
    """模拟 DB 顺序：priority 升序（同优先级按 id 升序），与 list_enabled 一致。"""
    rules = [
        SummaryRule(
            id=f"rule_{index:03d}",
            user_id=None,
            code=code,
            name=name,
            category=category,
            side=side,
            match_type=match_type,
            pattern=pattern,
            priority=priority,
            enabled=True,
            is_system=True,
            version=1,
        )
        for index, (code, name, category, side, match_type, pattern, priority) in enumerate(
            SYSTEM_RULES
        )
    ]
    return sorted(rules, key=lambda rule: (rule.priority, rule.id))


@pytest.mark.parametrize(
    ("summary", "side", "category", "code"),
    [
        # 真实账单（华泰 mock）出现过的全部摘要（9 种）都必须命中，且分类/短码稳定。
        ("证券买入", "buy", "buy", "sys_buy_stock"),
        ("证券卖出", "sell", "sell", "sys_sell_stock"),
        ("利息归本", "sell", "interest", "sys_interest"),
        ("股息入帐", "sell", "dividend", "sys_div_bonus"),
        ("股息红利税\n补缴", "buy", "fee", "sys_div_tax"),
        ("银行转存", "sell", "transfer", "sys_transfer_in"),
        ("银行转取", "buy", "transfer", "sys_transfer_out"),
        ("转存管转入", "unknown", "transfer", "sys_custody_in"),
        ("转存管转出", "unknown", "transfer", "sys_custody_out"),
    ],
)
def test_real_bill_summaries_all_match(summary: str, side: str, category: str, code: str) -> None:
    rule = match_rule(_rules(), summary, side)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


@pytest.mark.parametrize(
    ("summary", "side", "category", "code"),
    [
        # 货币基金账单（`test-bill.xlsx`）出现过的摘要：赎回确认此前无规则命中 → 被误隔离。
        ("赎回确认", "sell", "sell", "sys_redeem"),
        ("申购确认", "buy", "buy", "sys_fund_buy"),
        ("红利发放", "unknown", "dividend", "sys_div_paid"),
    ],
)
def test_money_fund_summaries_all_match(summary: str, side: str, category: str, code: str) -> None:
    rule = match_rule(_rules(), summary, side)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


def test_money_fund_rules_beat_generic_rules_without_swallowing_others() -> None:
    rules = _rules()
    # 「申购确认」必须命中货基买入，而不是被通用 contains「申购」→新股的规则吃掉。
    buy_confirm = match_rule(rules, "申购确认", "buy")
    assert buy_confirm is not None and buy_confirm.code == "sys_fund_buy"
    # 新股申购（无「确认」）仍归打新，不被货基规则抢走。
    new_apply = match_rule(rules, "申购", "buy")
    assert new_apply is not None and new_apply.code == "sys_new_apply"
    # 只认「赎回确认」形态：赎回费属于费用，不能被赎回规则误吞。
    redeem_fee = match_rule(rules, "赎回费", "buy")
    assert redeem_fee is not None and redeem_fee.code == "sys_fee"


def test_fund_rules_reuse_parser_vocabulary() -> None:
    """G3：解析器能识别方向的基金申赎摘要，分类规则必须同样命中（防词汇漂移）。"""
    rules = _rules()
    for keyword, side in FUND_SUMMARY_SIDE:
        rule = match_rule(rules, keyword, side)
        assert rule is not None, keyword
        assert rule.pattern == keyword
        assert rule.category == side


@pytest.mark.parametrize(
    ("summary", "side", "category", "code"),
    [
        # 真实对账单摘要会被券商折行；匹配前必须归一，否则整片误隔离。
        ("[省心投]红\n利发放", "unknown", "dividend", "sys_div_paid"),
        ("[省心投]申\n购确认", "buy", "buy", "sys_fund_buy"),
        ("[省心投]转\n托管确认", "unknown", "transfer", "sys_rehold"),
        ("托管转出", "unknown", "transfer", "sys_hold_out"),
        ("证券清理", "unknown", "other", "sys_cleanup"),
        # 打新缴款折行后含「申购确认」，必须仍归新股而不是货基买入。
        ("新股申购确\n认缴款", "buy", "new_stock", "sys_new_stock"),
        ("市值申购中\n签", "unknown", "new_stock", "sys_new_win"),
        ("新股入帐", "unknown", "new_stock", "sys_new_stock"),
    ],
)
def test_wrapped_real_statement_summaries_all_match(
    summary: str, side: str, category: str, code: str
) -> None:
    rule = match_rule(_rules(), summary, side)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


def test_new_stock_rule_beats_fund_buy_rule_for_ipo_payment() -> None:
    """「新股申购确认缴款」含「申购确认」子串，新股规则优先级必须更高。"""
    rules = _rules()
    rule = match_rule(rules, "新股申购确认缴款", "buy")
    assert rule is not None and rule.code == "sys_new_stock"
    fund = match_rule(rules, "申购确认", "buy")
    assert fund is not None and fund.code == "sys_fund_buy"


@pytest.mark.parametrize(
    ("summary", "side", "category"),
    [
        ("红利入账", "buy", "dividend"),
        ("红利入账", "sell", "dividend"),
        ("银行转证券", "buy", "transfer"),
        ("证券转银行", "sell", "transfer"),
        # 合成夹具摘要与买卖标志随机组合（可能自相矛盾）：分类由摘要决定，不应误隔离。
        ("证券买入", "sell", "buy"),
        ("利息归本", "buy", "interest"),
    ],
)
def test_synthetic_fixture_summaries_match(summary: str, side: str, category: str) -> None:
    rule = match_rule(_rules(), summary, side)
    assert rule is not None, summary
    assert rule.category == category


def test_unknown_summary_does_not_match() -> None:
    assert match_rule(_rules(), "未知业务XYZ", "buy") is None
    assert match_rule(_rules(), None, "buy") is None
    assert match_rule(_rules(), "", "buy") is None


def test_fee_rule_is_lowest_priority_and_does_not_swallow_other_summaries() -> None:
    rules = _rules()
    fee_rule = next(rule for rule in rules if rule.code == "sys_fee")
    assert fee_rule.priority == max(rule.priority for rule in rules)
    # 语义明确的摘要不会被 contains「费」的宽泛规则吃掉。
    div_tax = match_rule(rules, "股息红利税\n补缴", "buy")
    assert div_tax is not None and div_tax.code == "sys_div_tax"
    transfer = match_rule(rules, "银行转存手续费", "sell")
    assert transfer is not None and transfer.code == "sys_transfer_in"
    # 只有「费」类摘要才落到 sys_fee。
    fee = match_rule(rules, "佣金及其他费用", "both")
    assert fee is not None and fee.code == "sys_fee"


def test_system_rule_codes_fit_trade_summary_code_column() -> None:
    codes = [code for code, *_ in SYSTEM_RULES]
    assert len(codes) == len(set(codes)), "系统规则 code 必须唯一（api-check/契约要求）"
    assert all(len(code) <= SUMMARY_CODE_MAX_LENGTH for code in codes)
    rules = _rules()
    for rule in rules:
        assert rule_summary_code(rule) == rule.code


def test_transfer_rules_reuse_parser_vocabulary() -> None:
    """分类规则的资金划转词表与解析器非交易词汇同源（防 P1-8 词汇漂移）。"""
    # 资金划转主规则由 TRANSFER_SUMMARY_KEYWORDS 逐词生成；托管划转（转托管/托管转出）
    # 是后补的独立业务类型，不在共享词表内，这里按词表过滤后再比对。
    transfer_rules = [rule for rule in _rules() if rule.pattern in TRANSFER_SUMMARY_KEYWORDS]
    assert [rule.pattern for rule in transfer_rules] == list(TRANSFER_SUMMARY_KEYWORDS)
    parser_keywords = {keyword for keyword, _ in NON_TRADE_SUMMARY_SIDE}
    shared = parser_keywords & set(TRANSFER_SUMMARY_KEYWORDS)
    assert shared == {"银行转存", "银行转取", "转存管转入", "转存管转出"}


def test_synthetic_fixture_rows_classified_except_intentional_unknown() -> None:
    """合成夹具只为演示隔离保留 1 条未知摘要；其余摘要（含摘要/买卖标志错位行）必须全部命中。"""
    fixture = Path(__file__).parents[4] / "fixtures" / "market" / "bill_huatai.xlsx"
    bill = parse_bill(
        fixture.read_bytes(),
        fixture.name,
        mapping=None,
        date_format=None,
        clock=FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC)),
    )
    unknown = [row for row in bill.rows if match_rule(_rules(), row.summary, row.side) is None]
    # G4 若调整生成器/夹具内容，只要求「未命中的都是有意保留的未知摘要演示行」。
    assert all(row.summary == "未知业务XYZ" for row in unknown)


def test_money_fund_fixture_parse_and_classify_zero_quarantine() -> None:
    """货基账单全链路（解析→分类）：7 行全部命中，0 行进隔离区（test-bill.xlsx 回归）。"""
    fixture = Path(__file__).parents[2] / "api" / "华泰账单_货基_mock.xlsx"
    bill = parse_bill(
        fixture.read_bytes(),
        fixture.name,
        mapping=None,
        date_format=None,
        clock=FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC)),
    )
    assert len(bill.rows) == 7
    assert bill.quarantined == [], bill.quarantined

    rules = _rules()
    categories: dict[str, set[str]] = {}
    for row in bill.rows:
        rule = match_rule(rules, row.summary, row.side)
        assert rule is not None, row.summary
        categories.setdefault(row.summary or "", set()).add(rule.category)
    assert categories == {
        "赎回确认": {"sell"},
        "申购确认": {"buy"},
        "红利发放": {"dividend"},
        "证券买入": {"buy"},
        "银行转存": {"transfer"},
    }


def test_real_statement_excerpt_parse_and_classify() -> None:
    """真实对账单节选：折行摘要全部命中，持仓/声明区块被忽略，只有重复行进隔离区。"""
    fixture = Path(__file__).parents[2] / "api" / "华泰账单_真实节选_mock.xlsx"
    bill = parse_bill(
        fixture.read_bytes(),
        fixture.name,
        mapping=None,
        date_format=None,
        clock=FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC)),
    )
    assert len(bill.rows) == 8
    assert all(item.reason_code == "DUPLICATE_IN_FILE" for item in bill.quarantined)

    rules = _rules()
    categories: dict[str, set[str]] = {}
    for row in bill.rows:
        rule = match_rule(rules, row.summary, row.side)
        assert rule is not None, row.summary
        categories.setdefault(row.summary or "", set()).add(rule.category)
    assert categories == {
        "[省心投]红\n利发放": {"dividend"},
        "[省心投]转\n托管确认": {"transfer"},
        "[省心投]申\n购确认": {"buy"},
        "托管转出": {"transfer"},
        "新股申购确\n认缴款": {"new_stock"},
        "新股入帐": {"new_stock"},
        "证券清理": {"other"},
        "赎回确认": {"sell"},
    }
