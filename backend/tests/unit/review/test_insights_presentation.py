"""回合洞察展示层：证券类型筛选口径与持仓推演。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from stock_review.insights.service import (
    SECURITY_TYPES,
    _position_states,
    _security_type,
    _trade_order_key,
)


def _trade(
    trade_id: str,
    *,
    trade_date: date,
    line_no: int,
    side: str,
    quantity_units: int = 0,
    price_units: int = 0,
    amount_units: int = 0,
    commission_units: int = 0,
) -> Any:
    """`_position_states` 只读这几个字段的轻量替身（不落库、不建 ORM 对象）。"""
    return SimpleNamespace(
        id=trade_id,
        trade_date=trade_date,
        line_no=line_no,
        summary="证券买入" if side == "buy" else "证券卖出",
        security_code="688126",
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=commission_units,
        stamp_tax_units=0,
        other_fee_units=0,
    )


def test_security_type_classification() -> None:
    """代码区段是单一真相；货基靠流水标记/名称兜底，其它债券归入 other。"""
    assert _security_type("600519", "贵州茅台", set()) == "stock"
    assert _security_type("159892", "恒生生物", set()) == "fund"
    assert _security_type("113708", "曙26转债", set()) == "convertible_bond"
    assert _security_type("019547", "16国债19", set()) == "other"
    assert _security_type("940018", "华泰紫金天天发货币", set()) == "fund"
    assert _security_type("940018", "天天发", {"940018"}) == "fund"
    assert _security_type("754019", "曙26发债", set()) == "other"


def test_security_type_covers_every_symbol() -> None:
    """完备性：任何标的都必须落在四类之一，不能因分类不出来而从筛选项消失。"""
    codes = (
        "600519",  # 沪市股票
        "002475",  # 深市股票
        "300009",  # 创业板
        "688181",  # 科创板
        "159892",  # 场内基金
        "513330",  # 沪市基金
        "113708",  # 可转债
        "019547",  # 国债
        "940018",  # 货币基金（代码区段外）
        "754019",  # 新债申购代码
        "CASH",  # 资金流水行
    )
    for code in codes:
        assert _security_type(code, code, set()) in SECURITY_TYPES


def test_position_states_same_day_sells_before_buy_marks_no_addon() -> None:
    """同日「先卖后买」：清仓后的重新建仓不得被判成加仓（真实误判 rnd_482e85…）。

    复现 688126 的 2026-05-12：流水行号 1709/1711 两笔卖出先把 3500 股清到 0，
    行号 1712 才买入 1900 股；但按 `(trade_date, id)` 排序时该买入的 ULID 最小，
    会被错误地排到两笔卖出之前 → `qty_before=3500` → 误打 `addon`/`below_avg`。
    """
    trades = [
        _trade(
            "trd_01M6BQPFVYTDFVDP61R27P68Y3",
            trade_date=date(2026, 4, 24),
            line_no=1696,
            side="buy",
            quantity_units=20_000_000,
            price_units=20_510_000,
            amount_units=-4_102_541,
        ),
        _trade(
            "trd_01M6BQPFVY72S9WPN0Z9XEMSKV",
            trade_date=date(2026, 4, 30),
            line_no=1702,
            side="buy",
            quantity_units=15_000_000,
            price_units=20_140_000,
            amount_units=-3_021_530,
        ),
        # 行号在前、ULID 随机段较大 → 旧的 `(trade_date, id)` 排序会把它们排到买入之后。
        _trade(
            "trd_01M6BQPFVYW399YA5X5NXXYKAM",
            trade_date=date(2026, 5, 12),
            line_no=1709,
            side="sell",
            quantity_units=17_500_000,
            price_units=26_440_000,
            amount_units=4_624_108,
        ),
        _trade(
            "trd_01M6BQPFVYS4AB9ARBN8QTQ5C2",
            trade_date=date(2026, 5, 12),
            line_no=1711,
            side="sell",
            quantity_units=17_500_000,
            price_units=25_100_000,
            amount_units=4_389_755,
        ),
        # 行号在后、ULID 随机段最小（`…VY3J…` < `…VYS4…` < `…VYW3…`）。
        _trade(
            "trd_01M6BQPFVY3JZJ2SZSKZF17QDE",
            trade_date=date(2026, 5, 12),
            line_no=1712,
            side="buy",
            quantity_units=19_000_000,
            price_units=25_140_000,
            amount_units=-4_777_197,
        ),
    ]
    reopening = trades[-1]
    # 稳定序必须把 1712 的买入排到 1709/1711 的卖出之后。
    assert [trade.line_no for trade in sorted(trades, key=_trade_order_key)] == [
        1696,
        1702,
        1709,
        1711,
        1712,
    ]
    state = _position_states(trades)[reopening.id]
    assert state.qty_before == 0
    assert state.avg_cost_before is None


def test_position_states_keeps_addon_when_position_really_held() -> None:
    """真加仓仍然命中：04-24 买入 2000 股后，04-30 的买入前持仓 > 0。"""
    addon = _trade(
        "trd_01M6BQPFVY72S9WPN0Z9XEMSKV",
        trade_date=date(2026, 4, 30),
        line_no=1702,
        side="buy",
        quantity_units=15_000_000,
        price_units=20_140_000,
        amount_units=-3_021_530,
    )
    trades = [
        _trade(
            "trd_01M6BQPFVYTDFVDP61R27P68Y3",
            trade_date=date(2026, 4, 24),
            line_no=1696,
            side="buy",
            quantity_units=20_000_000,
            price_units=20_510_000,
            amount_units=-4_102_541,
        ),
        addon,
    ]
    state = _position_states(trades)[addon.id]
    assert state.qty_before == 20_000_000
    assert state.avg_cost_before == 20_512_705  # 4102541×1e8//20000000，price 1e-6 标度


def test_position_states_counts_fee_once() -> None:
    """`amount_units` 已含费用：均价只按净现金流计一次，不能再叠加佣金字段。

    1000 股 @10.00，佣金 5.00 → 净现金流 -10005.00，均价应为 10.005（而非 10.010）。
    """
    buy = _trade(
        "trd_buy_fee",
        trade_date=date(2026, 4, 24),
        line_no=1,
        side="buy",
        quantity_units=10_000_000,
        price_units=10_000_000,
        amount_units=-1_000_500,
        commission_units=500,
    )
    sell = _trade(
        "trd_sell_fee",
        trade_date=date(2026, 4, 30),
        line_no=2,
        side="sell",
        quantity_units=1_000_000,
        price_units=10_100_000,
        amount_units=100_990,
    )
    state = _position_states([buy, sell])[sell.id]
    assert state.avg_cost_before == 10_005_000
