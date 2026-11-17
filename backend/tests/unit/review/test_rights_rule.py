"""配股前置校验：零金额且无前置持仓的 `rights` 流水按「券商行为」处理（资产表口径 2.4）。

覆盖：
- 无持仓 + 金额 0 → 降级为 BROKER_ACTION，不建仓、不产出回合、无数据异常；
- 有持仓 + 金额 0 → 照旧 RIGHTS（加股、成本不变）；
- 同日多笔按 FIFO 稳定序判定（buy 在前 → 不降级；配股在前 → 降级且不被后续买入救回；
  `revision_of` 行 sort_rank=0 优先）；
- 金额非 0 + 无持仓 → 不降级，仍由引擎报 `unmatched_rights`（安全网）；
- 输入顺序不影响判定；
- 完全忽略降级行后，买卖仍能正常配对成回合。
"""

from __future__ import annotations

import random
import uuid
from datetime import date

from stock_core.fifo import EventKind, build_rounds
from stock_ledger.models import Trade
from stock_review.application.rebuild import rights_resolution, trades_to_events
from stock_review.application.rights import zero_amount_rights_codes

USER_ID = uuid.UUID("00000000-0000-4000-8000-00000000000b")
SHARE = 10_000  # 1 股（quantity 标度 1e-4）
CENT = 1  # 0.01 元


def _trade(
    *,
    trade_date: date = date(2026, 6, 30),
    security_code: str = "600519",
    category: str = "buy",
    side: str = "buy",
    quantity_units: int = 100 * SHARE,
    amount_units: int = -100_000 * CENT,
    line_no: int = 1,
    summary: str = "证券买入",
    revision_of: str | None = None,
) -> Trade:
    return Trade(
        id="trd_" + uuid.uuid4().hex[:12],
        user_id=USER_ID,
        trade_date=trade_date,
        security_code=security_code,
        security_name=security_code,
        side=side,
        quantity_units=quantity_units,
        price_units=0,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        summary=summary,
        category=category,
        line_no=line_no,
        revision_of=revision_of,
        dedupe_key=uuid.uuid4().hex,
    )  # type: ignore[arg-type]


def _rights(*, amount_units: int = 0, quantity_units: int = 30 * SHARE, **kwargs) -> Trade:
    return _trade(
        category="rights",
        side="buy",
        summary="配股入账",
        quantity_units=quantity_units,
        amount_units=amount_units,
        **kwargs,
    )


def test_zero_amount_rights_without_holding_is_broker_action() -> None:
    rights = _rights()
    resolution = rights_resolution([rights])
    events = trades_to_events([rights])
    result = build_rounds(events)

    assert resolution.demoted == {rights.id}
    assert resolution.category(rights) == "broker_action"
    assert events[0].kind == EventKind.BROKER_ACTION
    assert [event.kind for event in events] == [EventKind.BROKER_ACTION]
    assert result.rounds == ()
    assert result.data_errors == ()


def test_zero_amount_rights_with_holding_keeps_rights() -> None:
    buy = _trade(trade_date=date(2026, 6, 1))
    rights = _rights(trade_date=date(2026, 6, 30))
    resolution = rights_resolution([buy, rights])
    events = trades_to_events([buy, rights])

    assert resolution.demoted == frozenset()
    assert events[1].kind == EventKind.RIGHTS
    result = build_rounds(events)
    assert result.data_errors == ()
    assert len(result.rounds) == 1
    # 零金额配股只加股、不加成本。
    assert result.rounds[0].quantity_units == buy.quantity_units + rights.quantity_units


def test_same_day_buy_before_rights_not_demoted() -> None:
    buy = _trade(trade_date=date(2026, 6, 1), line_no=1)
    rights = _rights(trade_date=date(2026, 6, 1), line_no=2)

    assert rights_resolution([buy, rights]).demoted == frozenset()


def test_same_day_rights_before_buy_is_demoted_without_lookahead() -> None:
    rights = _rights(trade_date=date(2026, 6, 1), line_no=1)
    buy = _trade(trade_date=date(2026, 6, 1), line_no=2)

    # 同日后续买入不能把先到的配股「救回来」：判定只看之前的事件。
    assert rights_resolution([buy, rights]).demoted == {rights.id}


def test_revision_row_sorts_before_voucher_line() -> None:
    rights = _rights(trade_date=date(2026, 6, 1), line_no=1)
    revision_buy = _trade(trade_date=date(2026, 6, 1), line_no=1, revision_of="trd_orig")

    # 人工修订行 sort_rank=0，排在原账单行之前 → 配股判定时有持仓。
    assert rights_resolution([rights, revision_buy]).demoted == frozenset()


def test_nonzero_amount_rights_without_holding_stays_rights() -> None:
    rights = _rights(amount_units=-300_000 * CENT)
    resolution = rights_resolution([rights])
    events = trades_to_events([rights])
    result = build_rounds(events)

    assert resolution.demoted == frozenset()
    assert events[0].kind == EventKind.RIGHTS
    # 安全网保留：无持仓的真实缴款仍报数据异常，供人工核对。
    assert [error.kind for error in result.data_errors] == ["unmatched_rights"]


def test_resolution_is_independent_of_input_order() -> None:
    buy = _trade(trade_date=date(2026, 6, 1), line_no=2)
    rights_ok = _rights(trade_date=date(2026, 6, 3), line_no=1)
    rights_bad = _rights(trade_date=date(2026, 6, 2), line_no=1, security_code="000001")

    expected = rights_resolution([buy, rights_ok, rights_bad]).demoted
    assert rights_resolution([rights_bad, rights_ok, buy]).demoted == expected
    assert expected == {rights_bad.id}


def test_demoted_rights_does_not_block_sell_pairing() -> None:
    buy = _trade(trade_date=date(2026, 6, 1), quantity_units=100 * SHARE)
    # 另一只没有持仓的证券：该配股被降级，不应影响 600519 的买卖配对。
    rights = _rights(trade_date=date(2026, 6, 2), security_code="000001")
    sell = _trade(
        trade_date=date(2026, 6, 3),
        category="sell",
        side="sell",
        quantity_units=100 * SHARE,
        amount_units=120_000 * CENT,
        summary="证券卖出",
    )
    result = build_rounds(trades_to_events([buy, rights, sell]))

    assert result.data_errors == ()
    assert len(result.rounds) == 1
    assert result.rounds[0].status == "closed"
    assert result.rounds[0].quantity_units == 100 * SHARE


def test_narrowed_history_matches_full_history() -> None:
    """收窄取数（只取零金额配股涉及的代码）与全历史扫描结论逐条一致。

    覆盖三种容易出错的场景：期初持仓（买入在指标窗口之前）、同日先后、
    以及输入乱序（`resolve_rights` 内部按稳定序排序）。
    """
    held_before = _trade(trade_date=date(2026, 3, 2), security_code="600519")
    held_rights = _rights(trade_date=date(2026, 6, 3), security_code="600519")
    no_hold_rights = _rights(trade_date=date(2026, 6, 3), security_code="000001")
    same_day_buy = _trade(trade_date=date(2026, 6, 4), security_code="300001", line_no=1)
    same_day_rights = _rights(trade_date=date(2026, 6, 4), security_code="300001", line_no=2)
    late_rights = _rights(trade_date=date(2026, 6, 5), security_code="000002", line_no=1)
    late_buy = _trade(trade_date=date(2026, 6, 5), security_code="000002", line_no=2)
    # 与本规则无关的代码：收窄取数时会被排除，但不应影响结论。
    noise_buy = _trade(trade_date=date(2026, 5, 6), security_code="600000")
    all_trades = [
        held_before,
        held_rights,
        no_hold_rights,
        same_day_buy,
        same_day_rights,
        late_rights,
        late_buy,
        noise_buy,
    ]

    window = [trade for trade in all_trades if trade.trade_date >= date(2026, 6, 1)]
    candidates = zero_amount_rights_codes(window)
    assert candidates == frozenset({"600519", "000001", "300001", "000002"})
    narrowed = [trade for trade in all_trades if trade.security_code in candidates]

    full_demoted = rights_resolution(all_trades).demoted
    assert full_demoted == {no_hold_rights.id, late_rights.id}
    # 收窄版：与全历史逐条一致（期初持仓靠窗口前流水保住，噪音代码不影响）。
    assert rights_resolution(narrowed).demoted == full_demoted

    shuffled = list(all_trades)
    random.Random(7).shuffle(shuffled)
    assert rights_resolution(shuffled).demoted == full_demoted
