"""账单级旗标词表（独立命名空间，与回合旗标 `insights.flags` 互不引用）。

中英文案按本系统实际判定口径编写，API 直接透出，前端零口径渲染。

文案是**模板**：阈值/窗口用 `{window}`/`{span}`/`{near_pct}`/`{chase_pct}`/
`{stop_pct}`/`{short_pct}` 占位，由 `render_description(config)` 按用户生效配置渲染。
默认配置（1.4 常量）渲染结果与 1.4 文案逐字一致（黄金文本测试锁定）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from stock_core.trade_flags.constants import DEFAULT_TRADE_FLAG_CONFIG, TradeFlagConfig


@dataclass(frozen=True, slots=True)
class TradeFlagDefinition:
    code: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    side: Literal["buy", "sell"]
    lookahead: bool = False


TRADE_FLAG_DEFINITIONS: tuple[TradeFlagDefinition, ...] = (
    # ── 买入侧 ─────────────────────────────────────────────
    TradeFlagDefinition(
        code="first_entry",
        label_zh="首次建仓",
        label_en="First entry",
        description_zh=(
            "买入前该标的持仓为 0，本笔建立新持仓。与「均价加仓」「抄底加仓」「追涨加仓」互斥；"
            "「无持仓成本」的买入（已有持仓但成本不可得）不属于首次建仓。"
        ),
        description_en=(
            "The position before the buy was zero, so this buy opens a new position. Mutually "
            "exclusive with the three add-on states; buys with no cost basis are not first entries."
        ),
        side="buy",
    ),
    TradeFlagDefinition(
        code="addon_average",
        label_zh="均价加仓",
        label_en="Add-on at average cost",
        description_zh=(
            "本笔不是首次建仓，且含费净买价落在买入前持仓含费均价的上下 {near_pct}% 以内。"
            "与「抄底加仓」「追涨加仓」互斥。"
        ),
        description_en=(
            "Not the first entry, and the fee-inclusive net buy price is within ±{near_pct}% of the "
            "position's fee-inclusive average cost before the buy. Mutually exclusive with "
            "dip add-on and chase add-on."
        ),
        side="buy",
    ),
    TradeFlagDefinition(
        code="addon_dip",
        label_zh="抄底加仓",
        label_en="Dip add-on",
        description_zh=(
            "本笔不是首次建仓，且含费净买价低于买入前持仓含费均价 {near_pct}% 以上（摊低成本）。"
        ),
        description_en=(
            "Not the first entry, and the fee-inclusive net buy price is more than {near_pct}% below "
            "the position's fee-inclusive average cost before the buy (averaging down)."
        ),
        side="buy",
    ),
    TradeFlagDefinition(
        code="addon_chase",
        label_zh="追涨加仓",
        label_en="Chase add-on",
        description_zh=(
            "本笔不是首次建仓，且含费净买价高于买入前持仓含费均价 {near_pct}% 以上（抬升成本加仓）。"
        ),
        description_en=(
            "Not the first entry, and the fee-inclusive net buy price is more than {near_pct}% above "
            "the position's fee-inclusive average cost before the buy (averaging up)."
        ),
        side="buy",
    ),
    TradeFlagDefinition(
        code="chase",
        label_zh="追涨买入",
        label_en="Chasing the rally",
        description_zh=(
            "含费净买价高于买入日前 {window} 个行情日最高收盘价的 {chase_pct}% 以上。"
            "（行情源只提供收盘价，极值按收盘价计。）"
        ),
        description_en=(
            "The fee-inclusive net buy price is more than {chase_pct}% above the highest close of the "
            "{window} trading days before the buy. (Only closing prices are available, so extremes "
            "are computed on closes.)"
        ),
        side="buy",
    ),
    TradeFlagDefinition(
        code="instant_bottom",
        label_zh="即时抄底",
        label_en="Buying the dip",
        description_zh=(
            "含费净买价距买入日前 {window} 个行情日最低收盘价 {short_pct}% 以内。"
            "（行情源只提供收盘价，极值按收盘价计。）"
        ),
        description_en=(
            "The fee-inclusive net buy price is within {short_pct}% of the lowest close of the "
            "{window} trading days before the buy. (Only closing prices are available.)"
        ),
        side="buy",
    ),
    TradeFlagDefinition(
        code="short_term_bottom",
        label_zh="短期抄底",
        label_en="Short-term bottom",
        description_zh=(
            "含费净买价距买入日前后各 {window} 个行情日（不含当日，共 {span} 个）"
            "最低收盘价 {short_pct}% 以内。"
            "使用未来行情，属事后指标；未来 {window} 个行情日未走完时状态为「待算」。"
        ),
        description_en=(
            "The fee-inclusive net buy price is within {short_pct}% of the lowest close across the "
            "{window} trading days before and after the buy ({span} days, excluding the day). Uses "
            "future bars, so it is an ex-post metric and stays pending until the future "
            "window is complete."
        ),
        side="buy",
        lookahead=True,
    ),
    TradeFlagDefinition(
        code="short_term_underwater",
        label_zh="短期被套",
        label_en="Short-term underwater",
        description_zh=(
            "买入后连续 {window} 个行情日（不含当日）的每日收盘价都低于含费净买价，"
            "即窗口内收益持续为负。"
            "任一日收盘价不低于净买价（持平也算）即未命中；使用未来行情，属事后指标；"
            "未来 {window} 个行情日未走完时状态为「待算」。"
        ),
        description_en=(
            "Every close in the {window} trading days after the buy is below the fee-inclusive net buy "
            "price, i.e. the position stays underwater for the whole window. A single close at "
            "or above the net buy price means a miss. Uses future bars, so it is an ex-post "
            "metric and stays pending until the future window is complete."
        ),
        side="buy",
        lookahead=True,
    ),
    # ── 卖出侧 ─────────────────────────────────────────────
    TradeFlagDefinition(
        code="reduce_breakeven",
        label_zh="回本减仓",
        label_en="Break-even reduction",
        description_zh=(
            "未一次性清仓，且含费净卖价落在卖出前持仓含费均价的上下 {near_pct}% 以内。"
            "与「止盈减仓」「止损减仓」互斥。"
        ),
        description_en=(
            "Not a full exit, and the fee-inclusive net sell price is within ±{near_pct}% of the "
            "position's fee-inclusive average cost before the sale. Mutually exclusive with "
            "profit-taking and stop-loss reductions."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="reduce_profit",
        label_zh="止盈减仓",
        label_en="Profit-taking reduction",
        description_zh=("未一次性清仓，且含费净卖价高于卖出前持仓含费均价 {near_pct}% 以上。"),
        description_en=(
            "Not a full exit, and the fee-inclusive net sell price is more than {near_pct}% above the "
            "position's fee-inclusive average cost before the sale."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="reduce_loss",
        label_zh="止损减仓",
        label_en="Loss-cutting reduction",
        description_zh=("未一次性清仓，且含费净卖价低于卖出前持仓含费均价 {near_pct}% 以上。"),
        description_en=(
            "Not a full exit, and the fee-inclusive net sell price is more than {near_pct}% below the "
            "position's fee-inclusive average cost before the sale."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="full_exit",
        label_zh="清仓",
        label_en="Full exit",
        description_zh=(
            "本笔卖出数量覆盖卖出前的全部持仓；此时不再给出「回本/止盈/止损减仓」，"
            "改由「回本清仓」「止盈清仓」「止损清仓」三态接管，且三者互斥。"
        ),
        description_en=(
            "The sold quantity covers the entire position before the sale; the break-even / "
            "profit-taking / stop-loss reduction labels are then replaced by the three "
            "full-exit states, which are mutually exclusive."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="full_exit_breakeven",
        label_zh="回本清仓",
        label_en="Break-even full exit",
        description_zh=(
            "一次性清空持仓，且含费净卖价落在卖出前持仓含费均价的上下 {near_pct}% 以内。"
            "与「止盈清仓」「止损清仓」互斥，与减仓三态互斥。"
        ),
        description_en=(
            "A full exit where the fee-inclusive net sell price is within ±{near_pct}% of the position's "
            "fee-inclusive average cost before the sale. Mutually exclusive with the other two "
            "full-exit states and with the reduction states."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="full_exit_profit",
        label_zh="止盈清仓",
        label_en="Profit-taking full exit",
        description_zh=("一次性清空持仓，且含费净卖价高于卖出前持仓含费均价 {near_pct}% 以上。"),
        description_en=(
            "A full exit where the fee-inclusive net sell price is more than {near_pct}% above the "
            "position's fee-inclusive average cost before the sale."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="full_exit_loss",
        label_zh="止损清仓",
        label_en="Loss-cutting full exit",
        description_zh=("一次性清空持仓，且含费净卖价低于卖出前持仓含费均价 {near_pct}% 以上。"),
        description_en=(
            "A full exit where the fee-inclusive net sell price is more than {near_pct}% below the "
            "position's fee-inclusive average cost before the sale."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="stop_loss",
        label_zh="止损卖出",
        label_en="Stop-loss exit",
        description_zh=(
            "含费净卖价低于卖出日前 {window} 个行情日最低收盘价的 {stop_pct}% 以上，"
            "且同时严格低于卖出前持仓含费均价。"
            "（行情源只提供收盘价，极值按收盘价计；恰等于均价不归止损，归入「回撤」。"
            "零成本持仓的净卖价不可能低于 0，故本旗标恒不命中。）"
        ),
        description_en=(
            "The fee-inclusive net sell price is more than {stop_pct}% below the lowest close of the "
            "{window} trading days before the sale, and strictly below the position's fee-inclusive "
            "average cost. Exactly equal to the average cost counts as drawdown, not stop-loss."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="sell_drawdown",
        label_zh="回撤卖出",
        label_en="Drawdown",
        description_zh=(
            "含费净卖价低于卖出日前 {window} 个行情日最低收盘价的 {stop_pct}% 以上，"
            "但净卖价不低于卖出前持仓含费均价（价格已破近期低点，仍在成本线上方）。"
            "恰等于均价归入本旗标；零成本持仓视为有效成本，破位即归入本旗标；"
            "无持仓或成本为负时状态为「无持仓成本」。"
        ),
        description_en=(
            "The fee-inclusive net sell price is more than {stop_pct}% below the lowest close of the "
            "{window} trading days before the sale, but not below the position's fee-inclusive "
            "average cost. Exactly equal to the average cost counts as drawdown."
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="instant_top",
        label_zh="即时逃顶",
        label_en="Selling near the top",
        description_zh=(
            "含费净卖价距卖出日前 {window} 个行情日最高收盘价 {short_pct}% 以内。"
            "（行情源只提供收盘价，极值按收盘价计。）"
        ),
        description_en=(
            "The fee-inclusive net sell price is within {short_pct}% of the highest close of the "
            "{window} trading days before the sale. (Only closing prices are available.)"
        ),
        side="sell",
    ),
    TradeFlagDefinition(
        code="short_term_top",
        label_zh="短期逃顶",
        label_en="Short-term top exit",
        description_zh=(
            "含费净卖价距卖出日前后各 {window} 个行情日（不含当日，共 {span} 个）"
            "最高收盘价 {short_pct}% 以内。"
            "使用未来行情，属事后指标；未来 {window} 个行情日未走完时状态为「待算」。"
        ),
        description_en=(
            "The fee-inclusive net sell price is within {short_pct}% of the highest close across the "
            "{window} trading days before and after the sale ({span} days, excluding the day). Uses "
            "future bars, so it is an ex-post metric and stays pending until the future "
            "window is complete."
        ),
        side="sell",
        lookahead=True,
    ),
    TradeFlagDefinition(
        code="short_term_sold_early",
        label_zh="短期卖飞",
        label_en="Sold too early",
        description_zh=(
            "含费净卖价低于卖出后第 {window} 个行情日收盘价的 {short_pct}% 以上"
            "（卖出后价格又涨上去）。"
            "恰低 {short_pct}% 不计命中；使用未来行情，属事后指标；"
            "未来 {window} 个行情日未走完时状态为「待算」。"
        ),
        description_en=(
            "The fee-inclusive net sell price is more than {short_pct}% below the close of the "
            "{window_ord} trading day after the sale (the price rallied after selling). Exactly "
            "{short_pct}% is not a hit. Uses future bars, so it is an ex-post metric and stays "
            "pending until the future window is complete."
        ),
        side="sell",
        lookahead=True,
    ),
)

TRADE_FLAG_BY_CODE: dict[str, TradeFlagDefinition] = {
    item.code: item for item in TRADE_FLAG_DEFINITIONS
}
TRADE_FLAG_CODES: tuple[str, ...] = tuple(item.code for item in TRADE_FLAG_DEFINITIONS)
TRADE_FLAG_LOOKAHEAD_CODES: tuple[str, ...] = tuple(
    item.code for item in TRADE_FLAG_DEFINITIONS if item.lookahead
)

BUY_FLAG_CODES: tuple[str, ...] = tuple(
    item.code for item in TRADE_FLAG_DEFINITIONS if item.side == "buy"
)
SELL_FLAG_CODES: tuple[str, ...] = tuple(
    item.code for item in TRADE_FLAG_DEFINITIONS if item.side == "sell"
)

# 看板趋势口径：买卖各取三个代表性状态旗标（先买后卖，保持图表系列顺序）。
# 只决定看板展示哪些码，不参与判定；20 码全量仍由 `TRADE_FLAG_CODES` 维护。
TREND_FLAG_CODES: tuple[str, ...] = (
    "chase",
    "instant_bottom",
    "short_term_bottom",
    "short_term_underwater",
    "stop_loss",
    "sell_drawdown",
    "instant_top",
    "short_term_top",
    "short_term_sold_early",
)

# 看板柱状图口径：买卖两侧的互斥状态（先买后卖，保持堆叠顺序）。
# 买侧 = 首次建仓 + 加仓三态；卖侧 = 减仓三态 + 清仓三态；清仓与减仓互斥。
# 例外：已有持仓但成本不可得（no_cost_basis）的买入既非首次建仓也非加仓三态，
# 会成为买侧残余，不并入任何一类；本元组只决定展示，不参与判定。
STACKED_STATE_FLAG_CODES: tuple[str, ...] = (
    "first_entry",
    "addon_average",
    "addon_dip",
    "addon_chase",
    "reduce_breakeven",
    "reduce_profit",
    "reduce_loss",
    "full_exit_breakeven",
    "full_exit_profit",
    "full_exit_loss",
)


def _compact_pct(bps: int) -> str:
    """bps → 紧凑百分比（100→"1"、250→"2.5"、125→"1.25"），只做整数运算。"""
    whole, frac = divmod(bps, 100)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:02d}".rstrip("0")


def _ordinal(value: int) -> str:
    suffix = "th" if 10 <= value % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def render_description(
    config: TradeFlagConfig = DEFAULT_TRADE_FLAG_CONFIG,
) -> tuple[TradeFlagDefinition, ...]:
    """按生效配置渲染旗标文案（默认配置结果与 1.4 逐字一致）。"""
    values = {
        "window": str(config.window_trading_days),
        "span": str(config.window_trading_days * 2),
        "window_ord": _ordinal(config.window_trading_days),
        "near_pct": _compact_pct(config.near_equal_tolerance_bps),
        "chase_pct": _compact_pct(config.chase_margin_bps),
        "stop_pct": _compact_pct(config.stop_loss_margin_bps),
        "short_pct": _compact_pct(config.short_term_margin_bps),
    }
    return tuple(
        replace(
            item,
            description_zh=item.description_zh.format(**values),
            description_en=item.description_en.format(**values),
        )
        for item in TRADE_FLAG_DEFINITIONS
    )
