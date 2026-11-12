"""回合旗标计数（「加仓与后续下跌」卡片柱状图的数据源）。

`GET /rounds/insights` 是游标分页的，前端只能看到当前页 `items`，累计会少计；
因此按旗标的回合数在 `summary.flag_round_counts` 里由后端在全量筛选集上聚合。
口径：覆盖 v1.2 词表全部 `item_flag`（顺序沿用词表定义），同一回合只计一次。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stock_core.insights.flags import ITEM_FLAG_CODES
from stock_review.insights.service import _flag_round_counts


def _item(*flags: str, **counts: int) -> Any:
    """`_flag_round_counts` 只读 `flags` 的轻量替身（`flags` 为布尔投影）。"""
    return SimpleNamespace(flags=list(flags), status_counts=counts)


def _by_flag(items: list[Any]) -> dict[str, int]:
    return {row.flag: row.count for row in _flag_round_counts(items)}


def test_empty_returns_all_item_flags_in_glossary_order() -> None:
    rows = _flag_round_counts([])
    assert [row.flag for row in rows] == list(ITEM_FLAG_CODES)
    assert [row.count for row in rows] == [0] * len(ITEM_FLAG_CODES)


def test_absent_flag_not_counted() -> None:
    assert all(count == 0 for count in _by_flag([_item()]).values())


def test_same_round_counts_once_regardless_of_times() -> None:
    """同一回合抄底 3 次仍只计 1 个回合（图例不按次数拆开）。"""
    assert _by_flag([_item("bottom_buy", bottom_buy=3)])["bottom_buy"] == 1


def test_each_item_flag_counted_independently() -> None:
    items = [
        _item("bottom_buy", "drawdown", "intraday_t"),
        _item("stop_loss_sold_early", "swing_t"),
        _item("profit_sold_early", "intraday_rebalance_out", "intraday_rebalance_in"),
        _item("drawdown"),
        _item(),
    ]
    assert _by_flag(items) == {
        "bottom_buy": 1,
        "stop_loss_sold_early": 1,
        "profit_sold_early": 1,
        "drawdown": 2,
        "intraday_t": 1,
        "swing_t": 1,
        "intraday_rebalance_out": 1,
        "intraday_rebalance_in": 1,
    }


def test_filter_aliases_and_unknown_codes_ignored() -> None:
    """数据质量/缺口三态是查询别名，不属于 `items[].flags`，不混进行为统计。"""
    rows = _flag_round_counts(
        [_item("reason_missing", "horizon_pending", "corporate_action", "sold_too_early")]
    )
    assert [row.flag for row in rows] == list(ITEM_FLAG_CODES)
    assert all(row.count == 0 for row in rows)
