"""独立 oracle 对拍（Testing §9.3）与手算 golden。

* 28 条边界场景与 `tests/oracle/naive_fifo.py`（独立朴素实现）对拍：
  `|pnl 差| ≤ 1 分`，数量/成本/现金/股息同在 1 分容差内。
* ≥4 条手算 golden：期望值直接写死在测试里，不由被测代码生成。
"""

from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from stock_core.fifo import EventKind, FifoEvent, build_rounds

from oracle import naive_fifo
from oracle.naive_fifo import OracleEvent
from oracle.naive_fifo import build_rounds as oracle_build_rounds

QTY_PER_SHARE = 10_000
ONE_CENT = Decimal(1)
HALF_QTY_UNIT = Decimal("0.00005")


# ── 事件构造（标度整数）─────────────────────────────────


def shares(count: int | Decimal) -> int:
    return int(Decimal(count) * QTY_PER_SHARE)


def cents(yuan: str) -> int:
    return int(Decimal(yuan) * 100)


def price_of(yuan: str) -> int:
    return int(Decimal(yuan) * 1_000_000)


def buy(
    trade_id: str,
    day: date,
    count: int,
    price: str = "10.00",
    *,
    code: str = "600000",
    name: str = "浦发银行",
    line_no: int = 1,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.BUY,
        security_code=code,
        security_name=name,
        quantity_units=shares(count),
        price_units=price_of(price),
        amount_units=-(count * cents(price)),
        line_no=line_no,
    )


def sell(
    trade_id: str,
    day: date,
    count: int,
    price: str = "12.00",
    *,
    code: str = "600000",
    name: str = "浦发银行",
    line_no: int = 1,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.SELL,
        security_code=code,
        security_name=name,
        quantity_units=shares(count),
        price_units=price_of(price),
        amount_units=count * cents(price),
        line_no=line_no,
    )


def dividend(
    trade_id: str,
    day: date,
    gross_units: int,
    tax_units: int = 0,
    *,
    code: str = "600000",
    line_no: int = 1,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.DIVIDEND,
        security_code=code,
        security_name="浦发银行",
        amount_units=gross_units,
        dividend_tax_units=tax_units,
        line_no=line_no,
    )


def bonus(
    trade_id: str,
    day: date,
    *,
    ratio: str = "0.3",
    count: int = 0,
    code: str = "600000",
    line_no: int = 1,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.BONUS,
        security_code=code,
        security_name="浦发银行",
        quantity_units=shares(count),
        ratio_units=int(Decimal(ratio) * 100_000_000),
        line_no=line_no,
    )


def rights(
    trade_id: str,
    day: date,
    count: int,
    payment_cents: int,
    *,
    code: str = "600000",
    line_no: int = 1,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.RIGHTS,
        security_code=code,
        security_name="浦发银行",
        quantity_units=shares(count),
        amount_units=-payment_cents,
        line_no=line_no,
    )


def cashflow(trade_id: str, day: date, kind: EventKind, amount_units: int) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=kind,
        security_code="600000",
        amount_units=amount_units,
        line_no=1,
    )


# ── oracle 对接 ─────────────────────────────────────────


def to_oracle(event: FifoEvent) -> OracleEvent:
    """生产事件 → oracle 自然单位（股 / 元 / 比例），不共享任何实现代码。"""
    return OracleEvent(
        trade_id=event.trade_id,
        trade_date=event.trade_date,
        kind=str(event.kind.value),
        security_code=event.security_code,
        quantity=Decimal(event.quantity_units) / Decimal(QTY_PER_SHARE),
        price=Decimal(event.price_units) / Decimal(1_000_000),
        amount=Decimal(event.amount_units) / Decimal(100),
        dividend_tax=Decimal(event.dividend_tax_units) / Decimal(100),
        ratio=Decimal(event.ratio_units) / Decimal(100_000_000),
        line_no=event.line_no,
        summary=event.summary,
    )


def assert_oracle_agrees(events: list[FifoEvent]) -> None:
    result = build_rounds(events)
    expected = oracle_build_rounds([to_oracle(event) for event in events])

    produced = [round_ for round_ in result.rounds if round_.status != "open"]
    assert len(produced) == len(expected.rounds), "回合数量与 oracle 不一致"
    for round_, oracle_round in zip(produced, expected.rounds, strict=True):
        assert round_.key == oracle_round.key
        assert Decimal(round_.quantity_units) / Decimal(QTY_PER_SHARE) == oracle_round.quantity
        assert abs(Decimal(round_.cost_units) - oracle_round.cost * 100) <= ONE_CENT
        assert abs(Decimal(round_.proceeds_units) - oracle_round.proceeds * 100) <= ONE_CENT
        assert abs(Decimal(round_.dividend_units) - oracle_round.dividend * 100) <= ONE_CENT
        # 核心验收：pnl 差 ≤ 1 分
        assert abs(Decimal(round_.pnl_units) - oracle_round.pnl * 100) <= ONE_CENT
        # 回合内不变量
        assert sum(fill.quantity_units for fill in round_.fills) == round_.quantity_units
        assert round_.pnl_units == (
            round_.proceeds_units - round_.cost_units + round_.dividend_units
        )

    produced_lots = {lot.buy_trade_id: lot for lot in result.open_lots}
    expected_lots = {lot.buy_trade_id: lot for lot in expected.open_lots}
    assert set(produced_lots) == set(expected_lots), "未平仓 lot 集合与 oracle 不一致"
    for trade_id, lot in produced_lots.items():
        oracle_lot = expected_lots[trade_id]
        assert (
            abs(Decimal(lot.quantity_units) / Decimal(QTY_PER_SHARE) - oracle_lot.quantity)
            <= HALF_QTY_UNIT
        )
        assert abs(Decimal(lot.cost_units) - oracle_lot.cost * 100) <= ONE_CENT
        assert abs(Decimal(lot.dividend_units) - oracle_lot.dividend * 100) <= ONE_CENT
    assert len(result.open_rounds) == len(result.open_lots)


# ── 28 条边界场景（Testing §9.2 交易结构矩阵）───────────


D = date


SCENARIOS = [
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            buy("b2", D(2024, 1, 3), 200, "11.00", line_no=2),
            sell("s1", D(2024, 1, 10), 150, "12.00", line_no=3),
        ],
        id="01_add_position_two_lots",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 300, "10.00", line_no=1),
            sell("s1", D(2024, 1, 5), 100, "11.00", line_no=2),
            sell("s2", D(2024, 1, 8), 100, "12.00", line_no=3),
            sell("s3", D(2024, 1, 10), 100, "13.00", line_no=4),
        ],
        id="02_partial_reduce_three_sells",
    ),
    pytest.param(
        [
            buy("b2", D(2024, 1, 2), 100, "11.00", line_no=5),
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=3),
            sell("s1", D(2024, 1, 10), 150, "12.00", line_no=9),
        ],
        id="03_same_day_multi_fill",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            sell("s1", D(2024, 1, 10), 150, "13.00", line_no=2),
        ],
        id="04_oversell_negative_qty",
    ),
    pytest.param(
        [
            sell("s0", D(2024, 1, 2), 100, "12.00"),
            buy("b1", D(2024, 1, 3), 100, "10.00"),
            sell("s1", D(2024, 1, 10), 100, "11.00", line_no=2),
        ],
        id="05_first_row_sell_no_holding",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            dividend("d1", D(2024, 1, 5), 400),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=2),
        ],
        id="06_cash_dividend_single_lot",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            dividend("d1", D(2024, 1, 10), 1_000, 200),
            sell("s1", D(2024, 1, 20), 100, "11.00", line_no=2),
        ],
        id="07_dividend_tax_20pct_30d",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            dividend("d1", D(2024, 3, 10), 1_000, 100),
            sell("s1", D(2024, 3, 20), 100, "11.00", line_no=2),
        ],
        id="08_dividend_tax_10pct_60d",
    ),
    pytest.param(
        [
            buy("b1", D(2023, 1, 3), 100, "10.00"),
            dividend("d1", D(2024, 3, 1), 1_000, 0),
            sell("s1", D(2024, 3, 20), 100, "11.00", line_no=2),
        ],
        id="09_dividend_tax_0pct_over_365d",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            bonus("e1", D(2024, 1, 3)),
            sell("s1", D(2024, 1, 10), 130, "8.00", line_no=2),
        ],
        id="10_bonus_share_10_for_3",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            bonus("e1", D(2024, 1, 3), ratio="0", count=30),
            sell("s1", D(2024, 1, 10), 130, "8.00", line_no=2),
        ],
        id="11_bonus_quantity_fallback",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            buy("b2", D(2024, 1, 2), 200, "10.00", line_no=2),
            bonus("e1", D(2024, 1, 3), line_no=3),
            sell("s1", D(2024, 1, 10), 300, "8.00", line_no=4),
        ],
        id="12_bonus_multi_lot",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            rights("r1", D(2024, 1, 3), 20, cents("200.00"), line_no=2),
            sell("s1", D(2024, 1, 10), 120, "11.00", line_no=3),
        ],
        id="13_rights_issue_payment",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            buy("b2", D(2024, 1, 2), 200, "12.00", line_no=2),
            rights("r1", D(2024, 1, 3), 30, cents("300.00"), line_no=3),
            sell("s1", D(2024, 1, 10), 330, "11.00", line_no=4),
        ],
        id="14_rights_multi_lot",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            dividend("d1", D(2024, 1, 5), 500),
            bonus("e1", D(2024, 1, 6)),
            sell("s1", D(2024, 1, 10), 130, "10.00", line_no=2),
        ],
        id="15_ex_div_consistency",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 1, "10.00", line_no=1),
            buy("b2", D(2024, 1, 2), 2, "10.00", line_no=2),
            dividend("d1", D(2024, 1, 3), 100, line_no=3),
            sell("s1", D(2024, 1, 10), 1, "12.00", line_no=4),
            sell("s2", D(2024, 1, 11), 2, "12.00", line_no=5),
        ],
        id="16_dividend_remainder_split",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            sell("s1", D(2024, 1, 5), 40, "11.00", line_no=2),
        ],
        id="17_partial_sell_then_open",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            dividend("d1", D(2024, 1, 5), 300, line_no=2),
            bonus("e1", D(2024, 1, 6), line_no=3),
        ],
        id="18_suspended_still_open_with_dividend",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            cashflow("n1", D(2024, 1, 3), EventKind.NEW_STOCK, 0),
            cashflow("i1", D(2024, 1, 3), EventKind.INTEREST, 50),
            cashflow("o1", D(2024, 1, 4), EventKind.OTHER, 0),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=2),
        ],
        id="19_ipo_three_flows",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            cashflow("i1", D(2024, 1, 3), EventKind.INTEREST, 25),
            cashflow("t1", D(2024, 1, 4), EventKind.TRANSFER, -1_000),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=2),
        ],
        id="20_interest_vs_transfer",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            cashflow("o1", D(2024, 1, 3), EventKind.OTHER, 777),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=2),
        ],
        id="21_unknown_summary_quarantine",
    ),
    pytest.param(
        [
            FifoEvent(
                trade_id="bad",
                trade_date=D(2024, 1, 2),
                kind=EventKind.BUY,
                security_code="600000",
                quantity_units=0,
                line_no=1,
            ),
            buy("b1", D(2024, 1, 3), 100, "10.00", line_no=2),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=3),
        ],
        id="22_invalid_buy_recover",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            FifoEvent(
                trade_id="bad",
                trade_date=D(2024, 1, 3),
                kind=EventKind.SELL,
                security_code="600000",
                quantity_units=0,
                line_no=2,
            ),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=3),
        ],
        id="23_invalid_sell_qty_recover",
    ),
    pytest.param(
        [
            rights("r1", D(2024, 1, 2), 20, cents("200.00")),
            buy("b1", D(2024, 1, 3), 100, "10.00", line_no=2),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=3),
        ],
        id="24_rights_no_holding",
    ),
    pytest.param(
        [
            bonus("e1", D(2024, 1, 2)),
            buy("b1", D(2024, 1, 3), 100, "10.00", line_no=2),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=3),
        ],
        id="25_bonus_no_holding",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            buy("b2", D(2024, 1, 2), 100, "10.00", line_no=2),
            sell("s1", D(2024, 1, 10), 200, "12.00", line_no=3),
        ],
        id="26_duplicate_rows_bag_count",
    ),
    pytest.param(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            sell("s1", D(2024, 1, 2), 100, "12.00", line_no=2),
        ],
        id="27_same_day_round_trip",
    ),
    pytest.param(
        [
            buy("b1", D(2022, 6, 1), 100, "12.00"),
            dividend("d1", D(2023, 6, 1), 300, 0, line_no=2),
            sell("s1", D(2024, 5, 10), 100, "10.00", line_no=3),
        ],
        id="28_multi_year_loss_round",
    ),
]


@pytest.mark.parametrize("events", SCENARIOS)
def test_scenario_matches_independent_oracle(events: list[FifoEvent]) -> None:
    assert_oracle_agrees(events)


def test_oracle_module_does_not_import_production_code() -> None:
    """oracle 独立铁律：源码不得 import 本仓任何包（尤其 stock_core.fifo）。"""
    source = Path(naive_fifo.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    forbidden = [name for name in imported if name.startswith(("stock_", "tests", "oracle"))]
    assert forbidden == []
    assert "stock_core" not in imported


# ── 手算 golden（期望值写死，不由被测代码生成）─────────


def test_golden_1_simple_round_trip() -> None:
    """买 100 股 @10.00（1000.00 元），2024-01-10 卖 100 股 @12.00。

    手算：cost=1000.00、proceeds=1200.00、pnl=200.00、
    return=200/1000=0.2、holding=(01-02,01-10] 工作日 = 6。
    """
    result = build_rounds(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=2),
        ]
    )
    round_ = result.rounds[0]
    assert round_.key == "b1:s1"
    assert round_.quantity_units == 1_000_000
    assert round_.cost_units == 100_000
    assert round_.proceeds_units == 120_000
    assert round_.dividend_units == 0
    assert round_.pnl_units == 20_000
    assert round_.return_rate_units == 20_000_000
    assert round_.holding_days == 6
    assert result.realized_pnl_units == 20_000
    assert result.trading_pnl_units == 20_000


def test_golden_2_dividend_net_included_in_pnl() -> None:
    """买 100 @10.00；除息日毛股息 5.00、红利税 1.00（净 4.00）；卖 100 @10.50。

    手算：proceeds=1050.00、cost=1000.00、dividend=4.00、pnl=54.00、
    return=54/1000=0.054、holding=(01-02,01-10]=6。
    """
    result = build_rounds(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            dividend("d1", D(2024, 1, 5), 500, 100),
            sell("s1", D(2024, 1, 10), 100, "10.50", line_no=2),
        ]
    )
    round_ = result.rounds[0]
    assert round_.cost_units == 100_000
    assert round_.proceeds_units == 105_000
    assert round_.dividend_units == 400
    assert round_.pnl_units == 5_400
    assert round_.return_rate_units == 5_400_000
    assert round_.holding_days == 6
    assert result.dividend_units == 400
    assert result.trading_pnl_units == 5_000


def test_golden_3_bonus_share_quantity_scaled_cost_unchanged() -> None:
    """买 100 @10.00（成本 1000.00）；10 送 3 → 130 股；卖 130 @8.00（1040.00）。

    手算：数量 ×1.3、总成本不变 1000.00；pnl = 1040.00 - 1000.00 = 40.00；
    return = 40/1000 = 0.04。
    """
    result = build_rounds(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            bonus("e1", D(2024, 1, 3)),
            sell("s1", D(2024, 1, 10), 130, "8.00", line_no=2),
        ]
    )
    round_ = result.rounds[0]
    assert round_.quantity_units == 1_300_000
    assert round_.cost_units == 100_000  # 总成本不变
    assert round_.proceeds_units == 104_000
    assert round_.pnl_units == 4_000
    assert round_.return_rate_units == 4_000_000


def test_golden_4_rights_payment_added_to_cost() -> None:
    """买 100 @10.00（1000.00）；配股 20 股缴款 200.00；卖 120 @11.00（1320.00）。

    手算：cost = 1000 + 200 = 1200.00；pnl = 1320 - 1200 = 120.00；
    return = 120/1200 = 0.1；配股不新增回合 → 仅 1 个回合。
    """
    result = build_rounds(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00"),
            rights("r1", D(2024, 1, 3), 20, cents("200.00"), line_no=2),
            sell("s1", D(2024, 1, 10), 120, "11.00", line_no=3),
        ]
    )
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.quantity_units == 1_200_000
    assert round_.cost_units == 120_000
    assert round_.proceeds_units == 132_000
    assert round_.pnl_units == 12_000
    assert round_.return_rate_units == 10_000_000


def test_golden_5_two_rounds_plus_open_lot() -> None:
    """买 100 @10.00（01-02）；卖 100 @12.00（01-10）；买 100 @9.00（01-12）；
    卖 50 @13.00（01-15）。

    手算：
    - 回合1：cost=1000.00、proceeds=1200.00、pnl=200.00、return=0.2；
    - 回合2：50 股，cost=450.00、proceeds=650.00、pnl=200.00、return=200/450=0.44444444…；
    - 期末 open：50 股，cost=450.00、pnl=0。
    """
    result = build_rounds(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            sell("s1", D(2024, 1, 10), 100, "12.00", line_no=2),
            buy("b2", D(2024, 1, 12), 100, "9.00", line_no=3),
            sell("s2", D(2024, 1, 15), 50, "13.00", line_no=4),
        ]
    )
    assert [round_.key for round_ in result.rounds] == ["b1:s1", "b2:s2", "b2:open"]
    first, second, open_round = result.rounds
    assert (first.cost_units, first.proceeds_units, first.pnl_units) == (100_000, 120_000, 20_000)
    assert first.return_rate_units == 20_000_000
    assert (second.cost_units, second.proceeds_units, second.pnl_units) == (
        45_000,
        65_000,
        20_000,
    )
    # 200/450 = 0.4444444444… → Ratio HALF_UP → 44_444_444
    assert second.return_rate_units == 44_444_444
    assert open_round.status == "open"
    assert open_round.quantity_units == 500_000
    assert open_round.cost_units == 45_000
    assert open_round.pnl_units == 0
    assert result.realized_pnl_units == 40_000
    assert len(result.open_lots) == 1


def test_golden_6_weighted_holding_days() -> None:
    """买 100 @10.00（01-02）、买 100 @10.00（01-08）、卖 200 @11.00（01-10）。

    手算：holding = (6×100 + 2×100)/200 = 4；cost=2000.00、proceeds=2200.00、
    pnl=200.00、return=0.1。
    """
    result = build_rounds(
        [
            buy("b1", D(2024, 1, 2), 100, "10.00", line_no=1),
            buy("b2", D(2024, 1, 8), 100, "10.00", line_no=2),
            sell("s1", D(2024, 1, 10), 200, "11.00", line_no=3),
        ]
    )
    round_ = result.rounds[0]
    assert [fill.holding_days for fill in round_.fills] == [6, 2]
    assert round_.holding_days == 4
    assert round_.cost_units == 200_000
    assert round_.proceeds_units == 220_000
    assert round_.pnl_units == 20_000
    assert round_.return_rate_units == 10_000_000
    assert result.open_lots == ()
