"""G3 规则词表单测：真实账单摘要全部命中、优先级正确、规则短码可落 `trade.summary_code`。

覆盖两类真实账单形态：普通证券账户（华泰 mock）与货币基金账户（申赎确认/红利发放）。
纯函数（不连库）：用 `SYSTEM_RULES` 静电数据构造未持久化规则对象，直接驱动 `match_rule`。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from stock_contracts.clock import FrozenClock
from stock_ledger.application.classify import match_rule
from stock_ledger.models import SummaryRule
from stock_ledger.parsers.columns import (
    NON_TRADE_SUMMARY_KEYWORDS,
    TRANSFER_SUMMARY_KEYWORDS,
)
from stock_ledger.parsers.huatai import ParsedRow, parse_bill
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
        ("股息入帐", "sell", "dividend", "sys_div_bonus_1"),
        # 红利税按分红口径入账（负数冲减分红收入），不再计入费用。
        ("股息红利税\n补缴", "buy", "dividend", "sys_div_tax"),
        ("银行转存", "sell", "transfer", "sys_transfer_in"),
        ("银行转取", "buy", "transfer", "sys_transfer_out"),
        ("转存管转入", "unknown", "transfer", "sys_custody_in"),
        ("转存管转出", "unknown", "transfer", "sys_custody_out"),
        ("资管转让资\n金上账", "sell", "sell", "sys_asset_in"),
    ],
)
def test_real_bill_summaries_all_match(summary: str, side: str, category: str, code: str) -> None:
    rule = match_rule(_rules(), summary)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


@pytest.mark.parametrize(
    ("summary", "side", "category", "code"),
    [
        # 基金账单（`test-bill.xlsx`）出现过的摘要：赎回确认此前无规则命中 → 被误隔离。
        ("赎回确认", "sell", "sell", "sys_redeem"),
        ("申购确认", "buy", "buy", "sys_fund_buy"),
        ("红利发放", "unknown", "dividend", "sys_div_paid"),
    ],
)
def test_fund_summaries_all_match(summary: str, side: str, category: str, code: str) -> None:
    rule = match_rule(_rules(), summary)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


def test_fund_rules_beat_generic_rules_without_swallowing_others() -> None:
    rules = _rules()
    # 「申购确认」必须命中基金买入：新股规则只认「新股申购*」，不会抢走货基申购确认。
    buy_confirm = match_rule(rules, "申购确认")
    assert buy_confirm is not None and buy_confirm.code == "sys_fund_buy"
    # 新股申购（无「确认」）仍归打新建仓，不被基金规则抢走。
    new_apply = match_rule(rules, "新股申购")
    assert new_apply is not None and new_apply.code == "sys_new_apply"
    # 只认「赎回确认」形态：赎回费属于费用，不能被赎回规则误吞。
    redeem_fee = match_rule(rules, "赎回费")
    assert redeem_fee is not None and redeem_fee.code == "sys_fee"


def test_fund_rules_reuse_parser_vocabulary() -> None:
    """G3：基金申赎确认摘要，分类规则必须命中（防词汇漂移）。方向由 Category 决定。"""
    rules = _rules()
    # 申购确认 → buy，赎回确认 → sell
    fund_keywords = (("申购确认", "buy"), ("赎回确认", "sell"))
    for keyword, category in fund_keywords:
        rule = match_rule(rules, keyword)
        assert rule is not None, keyword
        assert rule.pattern == keyword
        assert rule.category == category


@pytest.mark.parametrize(
    ("summary", "side", "category", "code"),
    [
        # 真实对账单摘要会被券商折行；匹配前必须归一，否则整片误隔离。
        ("[省心投]红\n利发放", "unknown", "dividend", "sys_div_paid"),
        # [省心投]申购确认视同银行转出（设计 D14）：exact 规则优先于货基「申购确认」。
        ("[省心投]申\n购确认", "buy", "transfer", "sys_sxt_out"),
        ("[省心投]转\n托管确认", "unknown", "broker_action", "sys_brk_rehold"),
        ("托管转出", "unknown", "broker_action", "sys_brk_hold_out"),
        ("证券清理", "unknown", "broker_action", "sys_brk_cleanup"),
        # 打新缴款折行后含「申购确认」，必须仍归新股建仓而不是货基买入。
        ("新股申购确\n认缴款", "buy", "new_stock", "sys_new_stock"),
        # 中签/入账是券商登记动作：不扣款、不建仓、不计成本。
        ("市值申购中\n签", "unknown", "broker_action", "sys_brk_win"),
        ("新股入帐", "unknown", "broker_action", "sys_brk_arrive_t"),
    ],
)
def test_wrapped_real_statement_summaries_all_match(
    summary: str, side: str, category: str, code: str
) -> None:
    rule = match_rule(_rules(), summary)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


def test_new_stock_rule_beats_fund_buy_rule_for_ipo_payment() -> None:
    """「新股申购确认缴款」含「申购确认」子串，新股建仓规则(14)必须早于货基(15)。"""
    rules = _rules()
    rule = match_rule(rules, "新股申购确认缴款")
    assert rule is not None and rule.code == "sys_new_stock"
    fund = match_rule(rules, "申购确认")
    assert fund is not None and fund.code == "sys_fund_buy"


@pytest.mark.parametrize(
    ("summary", "side", "category", "code"),
    [
        # 新口径：只有真正扣款的「新股/认购/申购」建仓；上市后卖出统一走 sell。
        ("新股卖出", "sell", "sell", "sys_new_sell"),
        # 券商行为：份额锁定与登记，不扣款、不影响持仓、不计成本。
        ("申购中签", "unknown", "broker_action", "sys_brk_win"),
        ("申购还款", "both", "broker_action", "sys_brk_repay"),
        ("新股入账", "unknown", "broker_action", "sys_brk_arrive"),
        ("新股入帐", "unknown", "broker_action", "sys_brk_arrive_t"),
        ("证券清理", "unknown", "broker_action", "sys_brk_cleanup"),
        # 未中签/未成交的资金腿：不建仓，归资金划转（只有现金进出，没有持仓与成本）。
        ("申购冻结", "both", "transfer", "sys_sub_freeze"),
        ("申购解冻", "both", "transfer", "sys_sub_unfreeze"),
        ("申购退款", "both", "transfer", "sys_sub_refund"),
        ("中签退款", "both", "transfer", "sys_win_refund"),
        # 市值认购资金扣收：无证券代码的缴款腿，归资金划转；不能被「认购」抢成 new_stock。
        ("市值认购资金扣收", "buy", "transfer", "sys_mkt_sub_out"),
        ("市值认购资金扣收", "unknown", "transfer", "sys_mkt_sub_out"),
        # 配股（含配股缴款）既有股票变更也有扣费：归 rights，不被新股/申购规则抢走。
        ("配股缴款", "buy", "rights", "sys_rights"),
        # 真实扣款的打新/新股认购仍是特殊买入，按缴款金额（含费）建仓。
        ("新股申购确认缴款", "buy", "new_stock", "sys_new_stock"),
        ("新股认购", "buy", "new_stock", "sys_new_sub"),
        ("新股申购", "both", "new_stock", "sys_new_apply"),
    ],
)
def test_new_stock_narrowed_to_winning_lots(
    summary: str, side: str, category: str, code: str
) -> None:
    rule = match_rule(_rules(), summary)
    assert rule is not None, summary
    assert rule.category == category
    assert rule.code == code


def test_registration_summaries_go_to_broker_action_without_preempting_payments() -> None:
    """「认购」「上市」的登记行归券商行为，且不能抢占真正扣款、配股与资金划转。"""
    cases = {
        # 不扣款的登记/行情事件：归券商行为
        "可转债认购": ("broker_action", "sys_brk_cb_sub"),
        "新股上市": ("broker_action", "sys_brk_new_list"),
        "认购": ("broker_action", "sys_brk_sub"),
        "上市": ("broker_action", "sys_brk_list"),
        # 既有口径不退化
        "市值认购资金扣收": ("transfer", "sys_mkt_sub_out"),
        "新股申购确认缴款": ("new_stock", "sys_new_stock"),
        "新股申购": ("new_stock", "sys_new_apply"),
        "新股认购": ("new_stock", "sys_new_sub"),
        "配股缴款": ("rights", "sys_rights"),
    }
    for summary, expected in cases.items():
        rule = match_rule(_rules(), summary)
        assert rule is not None, summary
        assert (rule.category, rule.code) == expected, summary


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
    rule = match_rule(_rules(), summary)
    assert rule is not None, summary
    assert rule.category == category


def test_unknown_summary_does_not_match() -> None:
    assert match_rule(_rules(), "未知业务XYZ") is None
    assert match_rule(_rules(), None) is None
    assert match_rule(_rules(), "") is None


def test_fee_rule_is_lowest_priority_and_does_not_swallow_other_summaries() -> None:
    rules = _rules()
    fee_rule = next(rule for rule in rules if rule.code == "sys_fee")
    assert fee_rule.priority == max(rule.priority for rule in rules)
    # 语义明确的摘要不会被 contains「费」的宽泛规则吃掉。
    div_tax = match_rule(rules, "股息红利税\n补缴")
    assert div_tax is not None and div_tax.code == "sys_div_tax"
    transfer = match_rule(rules, "银行转存手续费")
    assert transfer is not None and transfer.code == "sys_transfer_in"
    # 只有「费」类摘要才落到 sys_fee。
    fee = match_rule(rules, "佣金及其他费用")
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
    parser_keywords = set(NON_TRADE_SUMMARY_KEYWORDS)
    shared = parser_keywords & set(TRANSFER_SUMMARY_KEYWORDS)
    assert shared == {
        "银行转存",
        "银行转取",
        "转存管转入",
        "转存管转出",
        "资管转让资金上账",
        "银证转入",
        "银证转出",
    }


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
    unknown = [row for row in bill.rows if match_rule(_rules(), row.summary) is None]
    # G4 若调整生成器/夹具内容，只要求「未命中的都是有意保留的未知摘要演示行」。
    assert all(row.summary == "未知业务XYZ" for row in unknown)


def test_fund_fixture_parse_and_classify_zero_amount_quarantined() -> None:
    """货基账单全链路（解析→分类）：6 行入账，零金额红利发放按 INVALID_AMOUNT 隔离。"""
    fixture = Path(__file__).parents[2] / "api" / "华泰账单_货基_mock.xlsx"
    bill = parse_bill(
        fixture.read_bytes(),
        fixture.name,
        mapping=None,
        date_format=None,
        clock=FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC)),
    )
    assert len(bill.rows) == 6
    assert [(item.line_no, item.reason_code) for item in bill.quarantined] == [
        (19, "INVALID_AMOUNT")
    ]

    rules = _rules()
    categories: dict[str, set[str]] = {}
    for row in bill.rows:
        rule = match_rule(rules, row.summary)
        assert rule is not None, row.summary
        categories.setdefault(row.summary or "", set()).add(rule.category)
    assert categories == {
        "赎回确认": {"sell"},
        "申购确认": {"buy"},
        "证券买入": {"buy"},
        "银行转存": {"transfer"},
    }


def test_real_statement_excerpt_parse_and_classify() -> None:
    """真实对账单节选：折行摘要全部命中，持仓/声明区块被忽略；全零行统一隔离。"""
    fixture = Path(__file__).parents[2] / "api" / "华泰账单_真实节选_mock.xlsx"
    bill = parse_bill(
        fixture.read_bytes(),
        fixture.name,
        mapping=None,
        date_format=None,
        clock=FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC)),
    )
    assert len(bill.rows) == 5
    assert sorted(item.line_no for item in bill.quarantined) == [17, 18, 19, 20, 25]
    assert {item.reason_code for item in bill.quarantined} == {"INVALID_AMOUNT"}

    rules = _rules()
    categories: dict[str, set[str]] = {}
    for row in bill.rows:
        rule = match_rule(rules, row.summary)
        assert rule is not None, row.summary
        categories.setdefault(row.summary or "", set()).add(rule.category)
    assert categories == {
        # [省心投]申购确认视同银行转出（设计 D14）→ 资金划转。摘要已在解析边界归一。
        "[省心投]申购确认": {"transfer"},
        "托管转出": {"broker_action"},
        "新股申购确认缴款": {"new_stock"},
        "新股入帐": {"broker_action"},
        "赎回确认": {"sell"},
    }


# ── 零金额登记动作降级 ──────────────────────────────────────────────────────────
#
# 规则表只管 pattern + category；发生金额=0 的登记动作在 classify 层按
# _CATEGORY_ZERO_AMOUNT_FALLBACK 集中降级，避免把不扣款的登记行误判为建仓。


@pytest.mark.parametrize(
    ("category", "amount_units", "expected"),
    [
        # new_stock 是唯一注册在 fallback 表里的 category
        ("new_stock", 0, "broker_action"),
        ("new_stock", -500000, "new_stock"),  # -5000.00 元 → 真扣款，保留 new_stock
        ("new_stock", 1000, "new_stock"),  # 正金额（罕见）也不降级
        # 不在 fallback 表里的 category 即使 amount=0 也原样返回
        ("buy", 0, "buy"),
        ("sell", 0, "sell"),
        ("broker_action", 0, "broker_action"),
        ("transfer", 0, "transfer"),
    ],
)
def test_category_after_amount_fallback_degrades_new_stock_on_zero(
    category: str, amount_units: int, expected: str
) -> None:
    from stock_ledger.application.classify import category_after_amount_fallback

    assert category_after_amount_fallback(category, amount_units) == expected


def test_resolve_type_category_zero_amount_degrades_rule_hit() -> None:
    """规则命中 new_stock 但 amount=0 → resolve_type_category 返回 broker_action。"""
    from stock_ledger.application.classify import resolve_type_category
    from stock_ledger.parsers.mapping import normalize_type_key

    rules = _rules()
    # 摘要"新股申购"命中 sys_new_apply（priority 14）
    category, summary_code, discarded = resolve_type_category(
        normalize_type_key("新股申购"),
        known_types={},
        rules=rules,
        summary="新股申购",
        amount_units=0,
    )
    assert discarded is False
    assert category == "broker_action"
    assert summary_code == "sys_new_apply"  # 短码仍保留，方便追溯原始规则


def test_resolve_type_category_nonzero_amount_keeps_new_stock() -> None:
    """规则命中 new_stock 且 amount<0 → 保留 new_stock（真扣款）。"""
    from stock_ledger.application.classify import resolve_type_category
    from stock_ledger.parsers.mapping import normalize_type_key

    rules = _rules()
    category, summary_code, discarded = resolve_type_category(
        normalize_type_key("新股申购缴款"),
        known_types={},
        rules=rules,
        summary="新股申购缴款",
        amount_units=-500000,  # -5000.00 元
    )
    assert discarded is False
    assert category == "new_stock"
    assert summary_code == "sys_new_apply"


def test_resolve_type_category_zero_amount_degrades_known_type_too() -> None:
    """用户类型记忆确认了 new_stock 但 amount=0 → 仍降级为 broker_action。

    设计意图：记忆是 type_key 级别的（同一摘要的历史确认），但零金额登记行可能
    出现在任何文件里；在单行粒度做 final 降级比让用户记住"选了 new_stock 就不
    会被降级"更安全——误判为建仓的代价远高于自动降级。
    """
    # resolve_type_category 只访问 known.category 和 known.discarded，用
    # types.SimpleNamespace 构造轻量替身即可，不必实例化完整 SQLAlchemy ORM。
    from types import SimpleNamespace

    from stock_ledger.application.classify import resolve_type_category
    from stock_ledger.parsers.mapping import normalize_type_key

    known_type = SimpleNamespace(category="new_stock", discarded=False)
    known_types = {normalize_type_key("新股上市"): known_type}

    category, _summary_code, discarded = resolve_type_category(
        normalize_type_key("新股上市"),
        known_types=known_types,
        rules=_rules(),
        summary="新股上市",
        amount_units=0,
    )
    assert discarded is False
    assert category == "broker_action"


def test_side_from_degraded_category_is_unknown() -> None:
    """new_stock 降级为 broker_action 后 side 变为 unknown（而非 buy）。"""
    from stock_ledger.application.classify import side_from_category

    assert side_from_category("broker_action") == "unknown"
    assert side_from_category("new_stock") == "buy"


# ── preview 建议层（aggregate_types）也必须走零金额降级 ─────────────────────
# 上一轮只修了单行粒度的 legacy commit / 复算 / 隔离行回填，漏了 preview 的
# aggregate_types，导致用户在 types 步骤看到的 suggested_category 还是 new_stock。
# 这里把 preview 层面的三种分支都覆盖掉——规则命中、已知类型记忆、真扣款保留。


def _fake_row(summary: str, amount_units: int) -> ParsedRow:
    """构造最小可用的 ParsedRow（dedupe_key 用摘要+金额，其他字段填 0/空）。"""
    return ParsedRow(
        line_no=1,
        trade_date=date(2026, 11, 10),
        security_code="600001",
        security_name="测试",
        side="unknown",
        quantity_units=0,
        price_units=0,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=None,
        cash_balance_available=False,
        summary=summary,
        summary_code=None,
        account_masked=None,
        occurrence_idx=0,
        dedupe_key=f"{summary}|{amount_units}",
        raw={},
    )


def test_aggregate_types_zero_amount_new_stock_degrades_preview_suggestion() -> None:
    """preview types 步骤：摘要命中新股申购规则，但 group 全零金额 → 建议应为 broker_action。

    这是用户实际观测到的漏网点：之前只在 legacy commit / 复算 / 隔离行回填三条单行粒度
    路径加了 fallback，漏掉了 preview 层面的 aggregate_types——用户在 types 步骤看到的
    suggested_category 还是 new_stock，但实际提交时 legacy 路径又会降级。
    """

    from stock_ledger.application.import_analysis import aggregate_types
    from stock_ledger.parsers.huatai import ParsedBill

    bill = ParsedBill(
        rows=[
            _fake_row("新股申购", 0),
            _fake_row("新股申购", 0),
        ]
    )
    groups = aggregate_types(bill, known_types={}, rules=_rules())
    assert len(groups) == 1
    group = next(iter(groups.values()))
    # 规则 sys_new_apply 命中（priority 14），但 group.amount_units==0 降级
    assert group.source == "summary_rule"
    assert group.suggested_category == "broker_action"
    assert group.category == "broker_action"
    assert group.suggested_rule_id is not None  # 短码仍保留，方便追溯原始规则
    # group.amount_units 是两行 0 累加 → 0
    assert group.amount_units == 0


def test_aggregate_types_nonzero_amount_keeps_new_stock_suggestion() -> None:
    """preview types 步骤：摘要含'新股'且真扣款 → suggested_category 应为 new_stock。"""

    from stock_ledger.application.import_analysis import aggregate_types
    from stock_ledger.parsers.huatai import ParsedBill

    bill = ParsedBill(
        rows=[
            # -500000 units = -5000.00 元（Money scale 2）
            _fake_row("新股申购缴款确认", -500000),
        ]
    )
    groups = aggregate_types(bill, known_types={}, rules=_rules())
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert group.source == "summary_rule"
    assert group.suggested_category == "new_stock"
    assert group.category == "new_stock"


def test_aggregate_types_known_type_zero_amount_also_degrades() -> None:
    """preview types 步骤：历史记忆已确认 new_stock，但本文件里该 type_key 全零金额 → 仍降级。"""

    from types import SimpleNamespace

    from stock_ledger.application.import_analysis import aggregate_types
    from stock_ledger.parsers.huatai import ParsedBill
    from stock_ledger.parsers.mapping import normalize_type_key

    bill = ParsedBill(
        rows=[
            _fake_row("中签登记新股001", 0),
            _fake_row("中签登记新股001", 0),
        ]
    )
    # 用户历史对同一 type_key 确认过 new_stock（过往真扣款文件）
    known_types = {
        normalize_type_key("中签登记新股001"): SimpleNamespace(
            category="new_stock", discarded=False
        ),
    }
    groups = aggregate_types(bill, known_types=known_types, rules=_rules())
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert group.source == "import_type"
    # 历史记忆是 new_stock，但本文件全是零金额登记行 → 降级
    assert group.suggested_category == "broker_action"
    assert group.is_new is False  # 仍然是历史记忆，不是新类型


def test_aggregate_types_discarded_known_type_gives_no_suggestion() -> None:
    """preview types 步骤：历史记忆已废弃 → 不提供任何建议，让用户显式决定。"""

    from types import SimpleNamespace

    from stock_ledger.application.import_analysis import aggregate_types
    from stock_ledger.parsers.huatai import ParsedBill
    from stock_ledger.parsers.mapping import normalize_type_key

    bill = ParsedBill(rows=[_fake_row("中签登记新股001", 0)])
    known_types = {
        normalize_type_key("中签登记新股001"): SimpleNamespace(category=None, discarded=True),
    }
    groups = aggregate_types(bill, known_types=known_types, rules=_rules())
    group = next(iter(groups.values()))
    assert group.discard is True
    assert group.category is None
    assert group.suggested_category is None
