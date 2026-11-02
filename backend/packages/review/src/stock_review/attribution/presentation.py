"""归因装配层：人话层文案与展示串（设计 §5.5 / §6.2，FR-D3 / FR-R8）。

规则（D-09 / §5.5）：
- **模板化、数据驱动**：summary/conclusion 只由
  `{code, amount, ratio, share, rank, status, coverage, valuation_gap}` 组装；
- **门禁**：`total_pnl` 可得 && `n_attributed_segments ≥ 10` && `capital_ratio ≥ 0.90`
  && `valuation_gap_ratio ≤ 5%`；`capital_total==0` 用固定句（"本期无交易/无持仓变化"
  或"本期无可归因 A 股"，判定=窗口内有 buy/sell 行）；`blocked` 必封（调用方传
  `None` 前置短路）；样本不足只给事实句不给行动建议；
- **无裸术语**：`DE/BHAR/PGR/PLR/TWR/MWR/FIFO/HHI/CR1/IR/OLS` 不得出现在文案
  （`assert_copy_compliance` 全量断言）；"板块/选股/买点/卖点/费用/残差"是产品词；
- **无买卖建议**：禁"建议买入/卖出/加仓/清仓/抄底"等。

展示串约定（与 dashboard `_money_display`/`Ratio.as_pct` 同风格）：
- `money_str`：分 → 千分位定点串（`"-1,900.00"`；ASCII 负号，防负零）；
- `pct_str`：scale=8 比率 → 一位小数百分比串（`"59.4%"`；`"-0.0%"` 归 `"0.0%"`）；
- `bar_pct`：share 取绝对值后同式（前端直接作 CSS 宽度）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stock_core.stats.numeric import div_round
from stock_core.values import Money

from stock_review.attribution.coverage import MIN_SAMPLE_SEGMENTS

# 结论门禁阈值（D-09；比率均为 scale=8）。
CAPITAL_RATIO_MIN = 90_000_000  # capital_ratio ≥ 0.90
GAP_RATIO_MAX = 5_000_000  # valuation_gap_ratio ≤ 5%
# 排序句点名阈值：rank=1 且 share ≥ 0.20。
DOMINANT_SHARE_MIN = 20_000_000
# `valuation_gap_ratio = |gap| / max(|total_pnl|, 1 元)`（§3.6；1 元 = 100 分）。
_GAP_DENOM_MIN = 100

# 文案门禁词表（§5.5；与 API-034 同一测试口径）。
BANNED_TERMS: tuple[str, ...] = (
    "DE",
    "BHAR",
    "PGR",
    "PLR",
    "TWR",
    "MWR",
    "FIFO",
    "HHI",
    "CR1",
    "IR",
    "OLS",
)
BANNED_ADVICE: tuple[str, ...] = ("建议买入", "建议卖出", "加仓", "清仓", "抄底")


def money_str(units: int) -> str:
    """标度整数（分）→ 千分位定点串（`-190000` → `"-1,900.00"`）。"""
    text = str(Money(units))
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    whole, _, fraction = text.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    grouped = ",".join(groups)
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


def pct_str(ratio_units: int | None) -> str | None:
    """scale=8 比率 → 一位小数百分比串（`59_400_000` → `"59.4%"`；None 透传）。"""
    if ratio_units is None:
        return None
    tenths = div_round(ratio_units, 100_000)  # 0.1% 精度
    if tenths == 0:
        return "0.0%"  # 防负零
    sign = "-" if tenths < 0 else ""
    tenths = abs(tenths)
    return f"{sign}{tenths // 10}.{tenths % 10}%"


def bar_pct(share_units: int | None) -> str | None:
    """share（0..1e8）→ 绝对百分比串（前端直接作 CSS 宽度，零口径）。"""
    if share_units is None:
        return None
    return pct_str(abs(share_units))


def tone(amount_units: int) -> str:
    """金额符号 → `positive`/`negative`/`flat`（§6.2 presentation.tone）。"""
    if amount_units > 0:
        return "positive"
    if amount_units < 0:
        return "negative"
    return "flat"


@dataclass(frozen=True, slots=True)
class FactorPresentation:
    """`factors[].presentation` 三元组（§6.2）。"""

    bar_pct: str | None
    ratio_pct: str | None
    tone: str


def factor_presentation(
    *, amount_units: int, ratio_units: int | None, share_units: int | None
) -> FactorPresentation:
    return FactorPresentation(
        bar_pct=bar_pct(share_units),
        ratio_pct=pct_str(ratio_units),
        tone=tone(amount_units),
    )


def factor_conclusion(amount_units: int, *, lang: str = "zh") -> str:
    """因子一句话结论（每因子一句；影响小 →"影响较小"）。"""
    en = lang == "en"
    if amount_units == 0:
        return "Minor impact" if en else "影响较小"
    if amount_units > 0:
        return (
            f"Added {money_str(amount_units)} CNY" if en else f"贡献 {money_str(amount_units)} 元"
        )
    return (
        f"Detracted {money_str(-amount_units)} CNY" if en else f"拖累 {money_str(-amount_units)} 元"
    )


def gap_ratio_units(valuation_gap_units: int, total_pnl_units: int) -> int:
    """`|gap| / max(|total_pnl|, 1 元)`（scale=8；§3.6 结论门禁分母）。"""
    denom = max(abs(total_pnl_units), _GAP_DENOM_MIN)
    return div_round(abs(valuation_gap_units), denom, scale=8)


def build_summary(
    *,
    total_pnl_units: int,
    ranked: Sequence[tuple[str, int, int | None]],
    residual_units: int,
    n_attributed_segments: int,
    capital_total_units: int,
    capital_ratio_units: int | None,
    valuation_gap_units: int,
    has_trade_rows: bool,
    lang: str = "zh",
) -> str | None:
    """summary 模板（D-09 门禁；返回 None = 本期不出结论）。

    `ranked`：`(name, amount, share)` 按 rank 序（rank=1 在前）；share 为 scale=8
    或 None（Σ|amount|==0）。`has_trade_rows`：窗口内是否有 buy/sell 流水行
    （`capital_total==0` 固定句的判定）。`lang` 只换文案不换数字（§5.5）。
    """
    en = lang == "en"
    if capital_total_units == 0:
        # 例外（§4.4 状态 3）：空账户/无可归因候选 → 固定句。
        if en:
            return (
                "No trades or position changes this period."
                if not has_trade_rows
                else "No attributable A-share holdings this period."
            )
        return "本期无交易/无持仓变化。" if not has_trade_rows else "本期无可归因 A 股。"
    if n_attributed_segments < MIN_SAMPLE_SEGMENTS:
        # 样本不足 → 只给事实句，不给行动建议。
        if en:
            return (
                f"Insufficient attributable trades ({n_attributed_segments} segments); "
                "no conclusion for this period."
            )
        return f"本期可归因交易样本不足（{n_attributed_segments} 段），暂不生成结论。"
    if capital_ratio_units is None or capital_ratio_units < CAPITAL_RATIO_MIN:
        return None
    if gap_ratio_units(valuation_gap_units, total_pnl_units) > GAP_RATIO_MAX:
        return None

    if total_pnl_units > 0:
        head = (
            f"Gained {money_str(total_pnl_units)} CNY this period"
            if en
            else f"本期盈利 {money_str(total_pnl_units)} 元"
        )
    elif total_pnl_units < 0:
        head = (
            f"Lost {money_str(-total_pnl_units)} CNY this period"
            if en
            else f"本期亏损 {money_str(-total_pnl_units)} 元"
        )
    else:
        head = "P&L flat this period" if en else "本期损益持平"

    sigma_abs = sum(abs(amount) for _, amount, _ in ranked)
    if ranked and sigma_abs > 0:
        name1, amount1, share1 = ranked[0]
        if share1 is not None and share1 >= DOMINANT_SHARE_MIN:
            head += (
                f', mainly from "{name1}" ({money_str(amount1)} CNY, '
                f"{pct_str(share1)} of composition)"
                if en
                else f"，主要来自「{name1}」（{money_str(amount1)} 元，占构成 {pct_str(share1)}）"
            )
        else:
            head += ", contributions are dispersed" if en else "，贡献分散"
        if len(ranked) >= 2 and ranked[1][1] != 0:
            name2, amount2, _ = ranked[1]
            head += (
                f'; next was "{name2}" ({money_str(amount2)} CNY)'
                if en
                else f"；其次为「{name2}」（{money_str(amount2)} 元）"
            )
    head += "." if en else "。"
    if residual_units != 0:
        head += (
            f" {money_str(abs(residual_units))} CNY unattributed; see residual breakdown."
            if en
            else f"未归属 {money_str(abs(residual_units))} 元，见残差明细。"
        )
    return head


def assert_copy_compliance(*texts: str | None) -> None:
    """文案门禁断言：无裸术语、无买卖建议（模板回归防线；违规即 bug）。"""
    for text in texts:
        if text is None:
            continue
        for term in BANNED_TERMS:
            if term in text:
                raise ValueError(f"文案出现裸术语 {term!r}: {text}")
        for advice in BANNED_ADVICE:
            if advice in text:
                raise ValueError(f"文案出现买卖建议 {advice!r}: {text}")


__all__ = [
    "BANNED_ADVICE",
    "BANNED_TERMS",
    "CAPITAL_RATIO_MIN",
    "DOMINANT_SHARE_MIN",
    "GAP_RATIO_MAX",
    "FactorPresentation",
    "assert_copy_compliance",
    "bar_pct",
    "build_summary",
    "factor_conclusion",
    "factor_presentation",
    "gap_ratio_units",
    "money_str",
    "pct_str",
    "tone",
]
