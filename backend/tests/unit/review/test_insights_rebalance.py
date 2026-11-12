"""日内调仓判定：对手方只认真实二级市场证券成交（2026-11-12 口径收紧）。

回归背景：`_trade_day_index` 原先收录同日全部流水，导致「卖出股票后补缴红利税」
「卖出股票后申购货基」被当成「买入其它证券」，误报 `intraday_rebalance_out`。
用例覆盖三种同日序列：真实换股命中、资金/费用行不命中、货基申赎不命中；
`intraday_rebalance_in` 用同一索引，对称场景一并覆盖。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from stock_core.insights import classify
from stock_review.insights.service import _trade_day_index

DAY = date(2026, 6, 18)


def _trade(
    trade_id: str,
    *,
    side: str,
    security_code: str,
    category: str,
    line_no: int,
    summary_code: str = "",
    money_fund_balance_units: int | None = None,
) -> Any:
    """`_trade_day_index` 只读这几个字段的轻量替身。"""
    return SimpleNamespace(
        id=trade_id,
        trade_date=DAY,
        line_no=line_no,
        side=side,
        security_code=security_code,
        category=category,
        summary_code=summary_code,
        money_fund_balance_units=money_fund_balance_units,
    )


def _rebalance(
    trades: list[Any],
    *,
    security: str,
    round_sells: list[Any],
    round_buys: list[Any],
) -> tuple[bool, bool]:
    """复刻 `_round_flags` 的 out/in 判定（同一 `_trade_day_index` 与 core 纯函数）。"""
    index = {trade.id: i for i, trade in enumerate(trades)}
    day_index = _trade_day_index(trades)
    sell_days = {trade.trade_date for trade in round_sells}
    buy_days = {trade.trade_date for trade in round_buys}
    other_buys = [
        (day, i)
        for day in sell_days
        for code, side, i in day_index.get(day, ())
        if code != security and side == "buy"
    ]
    other_sells = [
        (day, i)
        for day in buy_days
        for code, side, i in day_index.get(day, ())
        if code != security and side == "sell"
    ]
    out = classify.has_same_day_after(
        [(trade.trade_date, index[trade.id]) for trade in round_sells], other_buys
    )
    inn = classify.has_same_day_after(
        other_sells, [(trade.trade_date, index[trade.id]) for trade in round_buys]
    )
    return out, inn


def test_rebalance_out_hits_on_real_switch() -> None:
    """卖出 300496 后买入真实标的 002049 → 命中（线上 rnd_b73ac0… 场景保留）。"""
    buy = _trade("t1", side="buy", security_code="300496", category="buy", line_no=1774)
    sell = _trade("t2", side="sell", security_code="300496", category="sell", line_no=1777)
    other = _trade("t3", side="buy", security_code="002049", category="buy", line_no=1778)
    assert _rebalance(
        [buy, sell, other], security="300496", round_sells=[sell], round_buys=[buy]
    ) == (
        True,
        False,
    )


def test_rebalance_out_ignores_cash_dividend_tax() -> None:
    """卖出后紧跟 `CASH 股息红利税补缴`（category=fee, side=buy）→ 不命中。"""
    buy = _trade("t1", side="buy", security_code="688181", category="buy", line_no=1875)
    sell = _trade("t2", side="sell", security_code="688181", category="sell", line_no=1900)
    tax = _trade("t3", side="buy", security_code="CASH", category="fee", line_no=1901)
    assert _rebalance(
        [buy, sell, tax], security="688181", round_sells=[sell], round_buys=[buy]
    ) == (
        False,
        False,
    )


def test_rebalance_out_ignores_money_fund_subscribe() -> None:
    """卖出后紧跟货基申购 → 不命中：货基余额标记与摘要码两种识别都要生效。"""
    buy = _trade("t1", side="buy", security_code="301031", category="buy", line_no=1907)
    sell = _trade("t2", side="sell", security_code="301031", category="sell", line_no=1908)
    marked = _trade(
        "t3",
        side="buy",
        security_code="940018",
        category="buy",
        line_no=1909,
        summary_code="sys_fund_buy",
        money_fund_balance_units=11_629_538,
    )
    assert _rebalance(
        [buy, sell, marked], security="301031", round_sells=[sell], round_buys=[buy]
    ) == (
        False,
        False,
    )
    # 旧批次可能没有货基余额标记：只用摘要码 `sys_fund_buy` 兜底。
    unmarked = _trade(
        "t4",
        side="buy",
        security_code="940018",
        category="buy",
        line_no=1910,
        summary_code="sys_fund_buy",
    )
    assert _rebalance(
        [buy, sell, unmarked], security="301031", round_sells=[sell], round_buys=[buy]
    ) == (
        False,
        False,
    )


def test_rebalance_out_ignores_new_stock_subscription() -> None:
    """卖出后紧跟打新缴款（category=new_stock, side=buy）→ 不命中。"""
    buy = _trade("t1", side="buy", security_code="300496", category="buy", line_no=1)
    sell = _trade("t2", side="sell", security_code="300496", category="sell", line_no=2)
    ipo = _trade("t3", side="buy", security_code="001237", category="new_stock", line_no=3)
    assert _rebalance(
        [buy, sell, ipo], security="300496", round_sells=[sell], round_buys=[buy]
    ) == (
        False,
        False,
    )


def test_rebalance_in_ignores_cash_transfer_and_hits_real_sell() -> None:
    """买入前的「其它证券卖出」只认真实卖出：CASH 银行转存不算，真实卖出才算。"""
    transfer = _trade("t1", side="sell", security_code="CASH", category="transfer", line_no=1)
    buy = _trade("t2", side="buy", security_code="600585", category="buy", line_no=2)
    cash_only = _rebalance([transfer, buy], security="600585", round_sells=[], round_buys=[buy])
    assert cash_only == (False, False)

    real_sell = _trade("t3", side="sell", security_code="002223", category="sell", line_no=1)
    with_real = _rebalance([real_sell, buy], security="600585", round_sells=[], round_buys=[buy])
    assert with_real == (False, True)
