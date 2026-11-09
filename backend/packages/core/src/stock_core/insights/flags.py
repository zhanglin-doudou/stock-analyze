"""回合洞察旗标词表（设计 2026-11-04 §6.3/§6.4）。

单一真相：`items[].flags` 的 11 个回合旗标 + `?flag=` 查询词表的 5 个别名。
文案按本系统实际判定口径编写（不是通用金融词典），中英文各一份，供 API 直接透出、
前端零口径渲染。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class FlagDefinition:
    code: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    kind: Literal["item_flag", "filter_alias"]


FLAG_DEFINITIONS: tuple[FlagDefinition, ...] = (
    FlagDefinition(
        code="addon",
        label_zh="加仓",
        label_en="Add-on",
        description_zh=(
            "本回合内有买入发生在已有持仓之后（按流水发生顺序推演，同日多笔以文件行号排序）；"
            "回合的第一笔建仓不算加仓。"
        ),
        description_en=(
            "A buy inside this round happened while the security was already held "
            "(replayed in statement order, with same-day rows sorted by file line number). "
            "The first entry of the round is not an add-on."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="dip_sequence",
        label_zh="越跌越买",
        label_en="Buying lower",
        description_zh=(
            "同一证券近 60 个交易日内至少 2 次加仓，且每次净买入价都低于上一次，属于连续向下摊平。"
        ),
        description_en=(
            "At least two add-ons within the last 60 trading days for the same security, "
            "each at a lower fee-inclusive net buy price than the previous one — "
            "a continuous averaging-down sequence."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="below_avg",
        label_zh="低于均价加仓",
        label_en="Below average cost",
        description_zh=(
            "本回合加仓笔的含费净买入价低于该笔买入前的持仓含费均价（严格小于），即摊平买入。"
        ),
        description_en=(
            "The round's add-on was executed at a fee-inclusive net price strictly below "
            "the position's fee-inclusive average cost before that buy — an averaging-down purchase."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="continued_decline",
        label_zh="加仓后下跌",
        label_en="Decline after add-on",
        description_zh=(
            "买入后 20 个交易日继续下跌：B2 ≤ −3%，或 20 日内最差收盘 ≤ 含费净买价 × 95%"
            "（按收盘价计，不含盘中最低价）；无加仓时按回合开仓笔判定。"
        ),
        description_en=(
            "The price kept falling over the 20 trading days after the buy: B2 ≤ −3%, "
            "or the worst close within 20 days ≤ 95% of the fee-inclusive net buy price "
            "(closing prices only; intraday lows excluded). Without an add-on this is "
            "evaluated on the round's opening buy."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="profit_taking",
        label_zh="止盈",
        label_en="Profit-taking sale",
        description_zh=("卖出含费净价高于卖出前持仓的含费均价，本笔卖出实现盈利。"),
        description_en=(
            "The sell's fee-inclusive net price is above the position's fee-inclusive average "
            "cost before the sale, so this sale closes at a profit."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="profit_take_fly",
        label_zh="卖飞",
        label_en="Sold too early",
        description_zh=(
            "止盈卖出后 20 个交易日最高收盘 > 净卖价 × 1.03（高出 3% 以上），"
            "即卖出后仍有明显更高价，属「止盈型卖飞」。"
        ),
        description_en=(
            "After a profitable sale, the highest close in the following 20 trading days "
            "exceeded the net sell price by more than 3% — the price kept rising after "
            "the profit-taking exit."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="stop_loss_rebound",
        label_zh="止损后反弹",
        label_en="Rebound after stop-loss",
        description_zh=(
            "非止盈卖出（净卖价 ≤ 卖出前含费均价），但卖出后 20 个交易日最高收盘 > 净卖价 × 1.03；"
            "纪律性止损后出现反弹，属正常成本，不计入「卖飞」。"
        ),
        description_en=(
            "A non-profitable sale (net sell price at or below the prior average cost) after "
            "which the highest close in the next 20 trading days exceeded the net sell price "
            "by more than 3%. A disciplined exit followed by a rebound is a normal cost, "
            "not counted as sold too early."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="rising_sell_sequence",
        label_zh="越涨越卖",
        label_en="Selling into strength",
        description_zh=(
            "同一证券近 60 个交易日内至少 2 次卖出，且每次净卖出价都高于上一次，属于分批向上卖出。"
        ),
        description_en=(
            "At least two sells within the last 60 trading days for the same security, "
            "each at a higher fee-inclusive net sell price than the previous one — "
            "a scaling-out-into-strength pattern."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="full_exit",
        label_zh="清仓",
        label_en="Full exit",
        description_zh=(
            "本笔卖出数量覆盖整个回合的持仓数量（卖出数量 ≥ 回合总数量），该回合就此清仓。"
        ),
        description_en=(
            "This sell covers the entire round's quantity (sold quantity ≥ round quantity), "
            "closing the whole round."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="partial_exit",
        label_zh="部分卖出",
        label_en="Partial exit",
        description_zh=("本笔卖出数量小于整个回合的持仓数量，回合在卖出后仍有剩余仓位。"),
        description_en=(
            "This sell is smaller than the round's total quantity, so the round still has "
            "an open remainder after the sale."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="same_day",
        label_zh="当日回转",
        label_en="Same-day round trip",
        description_zh=(
            "本回合开仓日与平仓日是同一天（常见于 ETF、可转债等当日可卖的品种），"
            "持有交易日按 1 天计。"
        ),
        description_en=(
            "The round was opened and closed on the same day (typical for ETFs and "
            "convertible bonds, which can be sold on the day they are bought); "
            "the holding period counts as one trading day."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="addon_decline",
        label_zh="加仓后下跌",
        label_en="Add-on then decline",
        description_zh=(
            "查询别名：同时命中 `addon` 与 `continued_decline`，即含加仓且买入后 20 个交易日"
            "仍在下跌的回合。"
        ),
        description_en=(
            "Query alias matching rounds flagged with both `addon` and `continued_decline` — "
            "add-ons followed by a further decline within 20 trading days."
        ),
        kind="filter_alias",
    ),
    FlagDefinition(
        code="sold_too_early",
        label_zh="卖飞",
        label_en="Sold too early (any)",
        description_zh=(
            "查询别名：命中 `profit_take_fly` 或 `stop_loss_rebound`，即卖出后 20 日最高收盘"
            "高出净卖价 3% 以上的全部情形（含止盈型与止损后反弹型）。"
        ),
        description_en=(
            "Query alias matching `profit_take_fly` or `stop_loss_rebound`: any sale after "
            "which the 20-day highest close exceeded the net sell price by more than 3%, "
            "including both profit-taking and stop-loss exits."
        ),
        kind="filter_alias",
    ),
    FlagDefinition(
        code="reason_missing",
        label_zh="缺理由",
        label_en="Missing reason",
        description_zh=(
            "查询别名：回合的买入侧与卖出侧都没有标注操作理由（等价于 `has_reason=false`）。"
        ),
        description_en=(
            "Query alias matching rounds where neither the buy nor the sell side has a "
            "recorded reason (equivalent to `has_reason=false`)."
        ),
        kind="filter_alias",
    ),
    FlagDefinition(
        code="horizon_pending",
        label_zh="前瞻待算",
        label_en="Forward return pending",
        description_zh=("查询别名：任一 5 / 10 / 20 日前瞻收益因交易日不足而处于待算状态。"),
        description_en=(
            "Query alias matching rounds where any 5/10/20-day forward return is still "
            "pending because too few trading days have elapsed."
        ),
        kind="filter_alias",
    ),
    FlagDefinition(
        code="corporate_action",
        label_zh="公司行动",
        label_en="Corporate action",
        description_zh=(
            "查询别名：任一前瞻窗口内包含分红、送转、配股等公司行动，价格收益按未复权口径"
            "降级留空（不显示 0%）。"
        ),
        description_en=(
            "Query alias matching rounds whose forward window contains a corporate action "
            "(dividend, bonus shares, rights issue, etc.); price returns are left blank under "
            "the unadjusted-price policy instead of showing 0%."
        ),
        kind="filter_alias",
    ),
)

# 便于测试与调用方断言「词表覆盖全部枚举值」。
ITEM_FLAG_CODES: tuple[str, ...] = tuple(
    item.code for item in FLAG_DEFINITIONS if item.kind == "item_flag"
)
FILTER_FLAG_CODES: tuple[str, ...] = tuple(
    item.code for item in FLAG_DEFINITIONS if item.kind == "filter_alias"
)
