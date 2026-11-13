"""回合洞察旗标词表（设计 2026-11-04 §6.3/§6.4；2026-11-12 回合状态重构）。

单一真相：`items[].flags` 的 8 个回合旗标 + `?flag=` 查询词表的 4 个别名。
文案按本系统实际判定口径编写（不是通用金融词典），中英文各一份，供 API 直接透出、
前端零口径渲染。

计数型状态（抄底 / 止损卖飞 / 止盈卖飞）在 `status_counts` 里给出次数，`flags` 只做
「是否命中」的布尔投影；其余状态只出现在 `flags` 中。
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
        code="bottom_buy",
        label_zh="抄底",
        label_en="Bottom buy",
        description_zh=(
            "回合内命中「即时抄底」或「短期抄底」账单旗标的买入账单个数；"
            "同一笔账单同时命中两者只计 1 次。"
        ),
        description_en=(
            "Number of buy bills in the round flagged as either an instant bottom or a "
            "short-term bottom; a bill hitting both is counted once."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="stop_loss_sold_early",
        label_zh="止损卖飞",
        label_en="Stop-loss sold too early",
        description_zh=(
            "回合内带「短期卖飞」账单旗标、且含费净卖价低于（含等于）卖出前持仓含费均价的"
            "卖出账单次数。"
        ),
        description_en=(
            "Number of sell bills in the round flagged as short-term sold-early whose "
            "fee-inclusive net sell price is at or below the position's fee-inclusive "
            "average cost before the sale."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="profit_sold_early",
        label_zh="止盈卖飞",
        label_en="Profit sold too early",
        description_zh=(
            "回合内带「短期卖飞」账单旗标、且含费净卖价高于卖出前持仓含费均价的卖出账单次数。"
        ),
        description_en=(
            "Number of sell bills in the round flagged as short-term sold-early whose "
            "fee-inclusive net sell price is above the position's fee-inclusive average "
            "cost before the sale."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="drawdown",
        label_zh="收益回撤",
        label_en="Give-back",
        description_zh=(
            "回合内按数量加权的平均卖出价，低于「首笔买入日（含）至末笔卖出日（不含）」区间"
            "最高收盘价的 95%（严格低于），即卖出位置较区间高点回撤超过 5%。"
        ),
        description_en=(
            "The quantity-weighted average sell price in the round is strictly more than 5% "
            "below the highest close between the first buy date (inclusive) and the last "
            "sell date (exclusive)."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="intraday_t",
        label_zh="日内做T",
        label_en="Intraday T",
        description_zh=("同一交易日内先部分卖出（卖出数量严格小于卖出前持仓）再买入同一证券。"),
        description_en=(
            "Within one trading day, a partial sell (sold quantity strictly below the "
            "position before the sale) of the same security is followed by a buy."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="swing_t",
        label_zh="波段做T",
        label_en="Swing T",
        description_zh=(
            "回合内部分卖出后，在后续交易日再买入同一证券；同一笔卖出当日已配对买入的"
            "（日内做T）不重复计入。"
        ),
        description_en=(
            "A partial sell in the round is followed by a buy of the same security on a "
            "later trading day; sells already paired with a same-day buy (intraday T) are "
            "not counted again."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="intraday_rebalance_out",
        label_zh="日内调仓卖出",
        label_en="Intraday rebalance out",
        description_zh=(
            "回合内先卖出当前证券、同日再买入其它证券。范围为当前用户同日全部流水"
            "（不按账户切分，所有账户合并判定）。"
        ),
        description_en=(
            "Inside the round the current security is sold and another security is bought "
            "later on the same day. Scope is all of the user's same-day trades combined "
            "across accounts (no per-account split)."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="intraday_rebalance_in",
        label_zh="日内调仓买入",
        label_en="Intraday rebalance in",
        description_zh=(
            "回合内先卖出其它证券、同日再买入当前证券。范围为当前用户同日全部流水"
            "（不按账户切分，所有账户合并判定）。"
        ),
        description_en=(
            "Inside the round another security is sold and the current security is bought "
            "later on the same day. Scope is all of the user's same-day trades combined "
            "across accounts (no per-account split)."
        ),
        kind="item_flag",
    ),
    FlagDefinition(
        code="sold_too_early",
        label_zh="卖飞",
        label_en="Sold too early (any)",
        description_zh=(
            "查询别名：命中 `stop_loss_sold_early` 或 `profit_sold_early`，即回合内任一卖出"
            "账单带「短期卖飞」旗标的情形（含止损侧与止盈侧）。"
        ),
        description_en=(
            "Query alias matching `stop_loss_sold_early` or `profit_sold_early`: any sell bill "
            "in the round flagged as short-term sold-early, on either the stop-loss or the "
            "profit-taking side."
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
