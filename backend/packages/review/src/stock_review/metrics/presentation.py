"""指标呈现层：把「数值」翻译成「事实 / 解读 / 建议」（2026-10-30 P0-3）。

设计要点：
- 纯函数、零 I/O；输入是引擎 outcome + MetricDef，输出可直接进 API 的 dict；
- 数值一律由标度整数经 `format_units` 转定点字符串（前端零口径）；
- 经验区间只作为**方向提示**，并在 caveats 里注明“经验值、非权威阈值”；
- 样本不足/窗口未满不伪造结论：verdict=unknown/observation，progress 给出还差多少；
- 首批为 4 个试点指标配置区间（win_rate / pnl_ratio / de / drawdown），其余指标
  仍输出 display_name/direction/confidence/progress/verdict（通用），区间为空。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from stock_review.metrics.base import MetricDef, format_units

if TYPE_CHECKING:
    from stock_review.metrics.engine import MetricOutcome

Direction = Literal["higher_better", "lower_better", "neutral"]
BandLevel = Literal["good", "neutral", "watch"]

_RATIO = 10**8


@dataclass(frozen=True, slots=True)
class Band:
    level: BandLevel
    label_zh: str
    label_en: str
    low_units: int | None = None
    high_units: int | None = None


@dataclass(frozen=True, slots=True)
class Semantics:
    display_zh: str
    display_en: str
    direction: Direction
    bands: tuple[Band, ...] | None = None
    caveat_zh: str | None = None
    caveat_en: str | None = None
    metric_hint_zh: str | None = None
    metric_hint_en: str | None = None


# 经验区间（非权威阈值）：只用于 direction 提示；正式判定优先看 CI 是否跨过边界。
_WIN_RATE_BANDS = (
    Band("watch", "需关注", "Watch", None, int(0.40 * _RATIO)),
    Band("neutral", "中性", "Neutral", int(0.40 * _RATIO), int(0.55 * _RATIO)),
    Band("good", "良好", "Good", int(0.55 * _RATIO), None),
)
_PNL_RATIO_BANDS = (
    Band("watch", "需关注", "Watch", None, int(1.0 * _RATIO)),
    Band("neutral", "中性", "Neutral", int(1.0 * _RATIO), int(2.0 * _RATIO)),
    Band("good", "良好", "Good", int(2.0 * _RATIO), None),
)
_DE_BANDS = (
    Band("good", "良好", "Good", None, int(0.10 * _RATIO)),
    Band("neutral", "中性", "Neutral", int(0.10 * _RATIO), int(0.20 * _RATIO)),
    Band("watch", "需关注", "Watch", int(0.20 * _RATIO), None),
)
_DRAWDOWN_BANDS = (
    Band("good", "良好", "Good", None, int(0.10 * _RATIO)),
    Band("neutral", "中性", "Neutral", int(0.10 * _RATIO), int(0.25 * _RATIO)),
    Band("watch", "需关注", "Watch", int(0.25 * _RATIO), None),
)

# 呈现层口径版本：影响 display/bands/interpretation 文案时手动递增，参与 ETag。
PRESENTATION_VERSION = "2026-10-30"

SEMANTICS: dict[str, Semantics] = {
    "perf.win_rate": Semantics(
        "胜率",
        "Win rate",
        "higher_better",
        _WIN_RATE_BANDS,
        "胜率必须与盈亏比联读；经验区间非权威阈值。",
        "Read win rate together with payoff ratio; bands are rules of thumb.",
        "盈利平仓回合占比；高胜率常伴低赔率，单看会奖励“截断利润、放任亏损”。",
        "Share of profitable closed rounds; high win rate often pairs with low payoff.",
    ),
    "perf.pnl_ratio": Semantics(
        "盈亏比",
        "Payoff ratio",
        "higher_better",
        _PNL_RATIO_BANDS,
        "被 1–2 笔极端盈利拉高时均值会失真，建议同时看中位数/剔除 Top1。",
        "A few outsized wins can skew the mean; check median and ex-top1.",
        "平均盈利 ÷ 平均亏损；需与胜率联读。",
        "Average win over average loss; read with win rate.",
    ),
    "behavior.de": Semantics(
        "处置效应",
        "Disposition effect",
        "lower_better",
        _DE_BANDS,
        "小样本方差大，务必以 bootstrap 置信区间为准。",
        "High variance in small samples; trust the bootstrap interval.",
        "DE>0 表示倾向卖盈持亏；越大越明显。",
        "DE>0 means selling winners and holding losers; larger is stronger.",
    ),
    "timing.drawdown": Semantics(
        "最大回撤",
        "Max drawdown",
        "lower_better",
        _DRAWDOWN_BANDS,
        "回撤为截至期末 60 个交易日窗口；需结合同期基准与仓位解读。",
        "Drawdown over the trailing 60 trading days; compare with benchmark and exposure.",
        "峰谷最大跌幅；回撤小也可能因为低仓位。",
        "Peak-to-trough decline; a small drawdown may just mean low exposure.",
    ),
    "perf.fee": Semantics("费用占比", "Fee ratio", "lower_better"),
    "perf.fee_breakeven": Semantics("费用回本门槛", "Fee breakeven", "lower_better"),
    "perf.expectancy": Semantics("每笔期望", "Expectancy", "higher_better"),
    "perf.holding_days": Semantics("平均持仓天数", "Avg holding days", "neutral"),
    "perf.concentration": Semantics("盈利集中度", "Profit concentration", "neutral"),
    "timing.beta": Semantics("组合贝塔", "Portfolio beta", "neutral"),
    "timing.chase": Semantics("追涨倾向", "Chase tendency", "lower_better"),
    "timing.fly": Semantics("波段能力", "Swing capture", "neutral"),
    "behavior.turnover": Semantics("换手率", "Turnover", "lower_better"),
    "behavior.hhi": Semantics("持仓集中度", "Holding concentration", "lower_better"),
    "behavior.averaging_down": Semantics("越跌越买", "Averaging down", "neutral"),
    "selection.bhar_20": Semantics("买入后 20 日超额", "BHAR(20)", "higher_better"),
    "selection.bhar_60": Semantics("买入后 60 日超额", "BHAR(60)", "higher_better"),
    "selection.bhar_hit": Semantics("跑赢基准比例", "Benchmark hit rate", "higher_better"),
}

_REASON_TEXT = {
    "INSUFFICIENT_SAMPLE": ("样本不足", "insufficient sample"),
    "PENDING_WINDOW": ("观察窗口未满", "window not complete"),
    "BAR_DENSITY_LOW": ("行情覆盖不足", "low bar coverage"),
    "BENCHMARK_MISSING": ("基准数据缺失", "benchmark missing"),
    "ASSET_BUILD_BLOCKED": ("资产表未就绪", "asset build blocked"),
    "ASSET_BUILD_STALE": ("资产表过期", "asset build stale"),
    "RECON_BLOCKED": ("对账断链", "reconciliation blocked"),
    "DATA_DEGRADED": ("数据质量降级", "data degraded"),
    "DATA_OUT_OF_RANGE": ("数据异常", "data out of range"),
    "MARKET_ASOF_LAG": ("行情数据滞后", "market data lag"),
}


def _reason_text(reason: str | None, lang: str) -> str:
    if not reason:
        return "样本不足" if lang == "zh" else "insufficient sample"
    base = reason.split(":", 1)[0]
    if base == "DATASET_UNAVAILABLE":
        dataset = reason.split(":", 1)[1] if ":" in reason else "dataset"
        return f"数据不可用（{dataset}）" if lang == "zh" else f"dataset unavailable ({dataset})"
    if base == "UPSTREAM_BLOCKED":
        return "上游指标阻断" if lang == "zh" else "upstream metric blocked"
    zh, en = _REASON_TEXT.get(base, ("暂不可用", "unavailable"))
    return zh if lang == "zh" else en


def _fmt(outcome: MetricOutcome, units: int | None) -> str | None:
    return format_units(units, outcome.unit, outcome.precision)


def _band_for(sem: Semantics, value_units: int) -> Band | None:
    if not sem.bands:
        return None
    for band in sem.bands:
        low_ok = band.low_units is None or value_units >= band.low_units
        high_ok = band.high_units is None or value_units < band.high_units
        if low_ok and high_ok:
            return band
    return None


def _ci_crosses_band(
    sem: Semantics, value_units: int, ci_low: int | None, ci_high: int | None
) -> bool:
    if ci_low is None or ci_high is None or not sem.bands:
        return False
    current = _band_for(sem, value_units)
    low_band = _band_for(sem, ci_low)
    high_band = _band_for(sem, ci_high)
    return current is not None and (low_band != current or high_band != current)


def _confidence(outcome: MetricOutcome, lang: str) -> dict[str, str]:
    if outcome.status != "ok":
        return {
            "level": "unavailable",
            "label": "不可用" if lang == "zh" else "Unavailable",
            "reason": _reason_text(outcome.reason, lang),
        }
    if outcome.sample_status == "ok":
        return {
            "level": "high",
            "label": "可信" if lang == "zh" else "High confidence",
            "reason": "样本与窗口满足门槛" if lang == "zh" else "sample/window thresholds met",
        }
    if outcome.sample_status == "insufficient":
        return {
            "level": "low",
            "label": "参考" if lang == "zh" else "Reference only",
            "reason": "样本不足，仅展示原始值"
            if lang == "zh"
            else "insufficient sample; raw value only",
        }
    return {
        "level": "low",
        "label": "积累中" if lang == "zh" else "Accumulating",
        "reason": "观察窗口未满" if lang == "zh" else "observation window not complete",
    }


def _progress(outcome: MetricOutcome, definition: MetricDef, lang: str) -> dict[str, Any] | None:
    if outcome.status != "ok":
        return None
    policy = definition.sample_policy
    if policy.min_trading_days > 0:
        current = int(outcome.decomposition.get("n_trading_days") or outcome.n)
        required = policy.min_trading_days
        unit = "trading_days"
        unit_label = "个交易日" if lang == "zh" else "trading days"
    else:
        current = outcome.n
        required = policy.min_n
        unit = "samples"
        scope_unit = str(outcome.scope.get("unit", ""))
        zh_label = {"round": "笔已平仓回合", "trade": "笔交易", "day": "个资产日"}.get(
            scope_unit, "个样本"
        )
        unit_label = zh_label if lang == "zh" else "samples"
    remaining = max(0, required - current)
    if lang == "zh":
        label = f"还差 {remaining} {unit_label}可出结论" if remaining > 0 else "已满足样本门槛"
    else:
        label = f"{remaining} {unit_label} to a conclusion" if remaining > 0 else "Threshold met"
    return {
        "current": current,
        "required": required,
        "remaining": remaining,
        "unit": unit,
        "unit_label": unit_label,
        "label": label,
    }


def _verdict(
    outcome: MetricOutcome,
    sem: Semantics,
    lang: str,
) -> dict[str, str]:
    if outcome.status != "ok":
        return {
            "level": "unavailable",
            "label": "不可用" if lang == "zh" else "Unavailable",
            "rationale": _reason_text(outcome.reason, lang),
        }
    if not outcome.conclusion_available or outcome.value_units is None:
        return {
            "level": "unknown",
            "label": "观察中" if lang == "zh" else "Observing",
            "rationale": "样本/窗口未满足，暂不下结论"
            if lang == "zh"
            else "sample/window not met; no conclusion",
        }
    band = _band_for(sem, outcome.value_units)
    if band is None:
        return {
            "level": "neutral",
            "label": "中性" if lang == "zh" else "Neutral",
            "rationale": "该指标暂未配置经验区间，请结合基准/历史分位解读"
            if lang == "zh"
            else "No empirical bands; compare with benchmark/history.",
        }
    if _ci_crosses_band(sem, outcome.value_units, outcome.ci_low_units, outcome.ci_high_units):
        return {
            "level": "neutral",
            "label": "中性（区间跨档）" if lang == "zh" else "Neutral (CI crosses bands)",
            "rationale": "95% 置信区间跨过区间边界，点估计不足以定档"
            if lang == "zh"
            else "95% CI crosses band boundaries; point estimate is not decisive.",
        }
    return {
        "level": band.level,
        "label": band.label_zh if lang == "zh" else band.label_en,
        "rationale": f"按经验区间判定为{band.label_zh}（经验值，非权威阈值）"
        if lang == "zh"
        else f"Empirical band: {band.label_en} (rule of thumb, not a standard).",
    }


def _interpretation(
    outcome: MetricOutcome,
    definition: MetricDef,
    sem: Semantics,
    verdict: dict[str, str],
    progress: dict[str, Any] | None,
    lang: str,
) -> dict[str, str]:
    value = _fmt(outcome, outcome.value_units)
    n_text = f"n={outcome.n}"
    ci = (
        f"95%CI {_fmt(outcome, outcome.ci_low_units)}~{_fmt(outcome, outcome.ci_high_units)}"
        if outcome.ci_low_units is not None and outcome.ci_high_units is not None
        else None
    )
    display = sem.display_zh if lang == "zh" else sem.display_en
    if outcome.value_units is None:
        fact = (
            f"{display} 本期无有效值（{_reason_text(outcome.reason, lang)}）。"
            if lang == "zh"
            else f"{display}: no valid value this period ({_reason_text(outcome.reason, lang)})."
        )
    else:
        fact_parts = [f"{display} {value}"]
        if ci:
            fact_parts.append(ci)
        fact_parts.append(n_text)
        fact = "；".join(fact_parts) + "。"

    if outcome.status != "ok" or outcome.value_units is None:
        meaning = (
            f"本期无法给出结论：{_reason_text(outcome.reason, lang)}。"
            if lang == "zh"
            else f"No conclusion this period: {_reason_text(outcome.reason, lang)}."
        )
        meaning = meaning.replace("：。", "。")
    elif not outcome.conclusion_available:
        meaning = (
            f"{progress['label'] if progress else '样本不足'}，当前只有原始值，暂不判断好坏。"
            if lang == "zh"
            else f"{progress['label'] if progress else 'Insufficient sample'}; raw value only, no judgement yet."
        )
    else:
        meaning = verdict["rationale"]
        if sem.metric_hint_zh and lang == "zh":
            meaning = f"{meaning} {sem.metric_hint_zh}"
        elif sem.metric_hint_en and lang == "en":
            meaning = f"{meaning} {sem.metric_hint_en}"

    action = None
    if outcome.conclusion_available:
        action = (
            definition.explain.what_to_do_zh if lang == "zh" else definition.explain.what_to_do_en
        )
    if not action and progress and progress["remaining"] > 0:
        action = (
            f"再积累 {progress['remaining']}{progress['unit_label']}，或切换更长的滚动窗口后再看。"
            if lang == "zh"
            else f"Accumulate {progress['remaining']} {progress['unit_label']}, or switch to a longer rolling window."
        )
    if not action:
        action = (
            "查看下钻明细，确认异常是否集中在单一标的/时段。"
            if lang == "zh"
            else "Inspect the drilldown to see if outliers cluster."
        )
    return {"fact": fact, "meaning": meaning, "action": action}


def _caveats(outcome: MetricOutcome, definition: MetricDef, sem: Semantics, lang: str) -> list[str]:
    items: list[str] = []
    if sem.caveat_zh and lang == "zh":
        items.append(sem.caveat_zh)
    elif sem.caveat_en:
        items.append(sem.caveat_en)
    if outcome.ci_low_units is not None and outcome.ci_high_units is not None:
        width = outcome.ci_high_units - outcome.ci_low_units
        if outcome.unit == "ratio" and width >= int(0.30 * _RATIO):
            items.append(
                "置信区间较宽，点估计不宜过度解读。"
                if lang == "zh"
                else "Confidence interval is wide; avoid over-reading the point estimate."
            )
    if definition.benchmark_required and outcome.status == "ok":
        items.append(
            "基准暂用 ETF 代理（hs300_tr/zz500_tr/csi1000_tr），接入全收益指数后可能微调。"
            if lang == "zh"
            else "Benchmark currently uses ETF proxies; may shift after total-return indices are wired."
        )
    degraded_days = int(outcome.decomposition.get("n_degraded_rows") or 0)
    if degraded_days > 0:
        items.append(
            f"窗口含 {degraded_days} 个降级估值日，结果为近似值。"
            if lang == "zh"
            else f"Window contains {degraded_days} degraded valuation days; result is approximate."
        )
    return items


def build_presentation(
    outcome: MetricOutcome, definition: MetricDef, *, lang: str = "zh"
) -> dict[str, Any]:
    """构造 API 的 `presentation` 字段（纯函数、无 I/O）。"""
    lang = "en" if lang == "en" else "zh"
    sem = SEMANTICS.get(
        definition.id,
        Semantics(definition.id, definition.id, "neutral"),
    )
    progress = _progress(outcome, definition, lang)
    verdict = _verdict(outcome, sem, lang)
    period_kind = str(outcome.scope.get("period", "month"))
    window = (
        {
            "kind": "trailing_60td",
            "label": "截至期末 60 个交易日" if lang == "zh" else "trailing 60 trading days",
        }
        if definition.sample_policy.min_trading_days > 0
        else {
            "kind": {"month": "month", "year": "year"}.get(period_kind, "custom_range"),
            "label": outcome.period,
        }
    )
    bands = None
    if sem.bands:
        bands = [
            {
                "level": band.level,
                "label": band.label_zh if lang == "zh" else band.label_en,
                "from": _fmt(outcome, band.low_units),
                "to": _fmt(outcome, band.high_units),
            }
            for band in sem.bands
        ]
    return {
        "display_name": sem.display_zh if lang == "zh" else sem.display_en,
        "direction": sem.direction,
        "confidence": _confidence(outcome, lang),
        "progress": progress,
        "verdict": verdict,
        "bands": bands,
        "window": window,
        "interpretation": _interpretation(outcome, definition, sem, verdict, progress, lang),
        "caveats": _caveats(outcome, definition, sem, lang),
    }


__all__ = [
    "PRESENTATION_VERSION",
    "SEMANTICS",
    "Band",
    "Semantics",
    "build_presentation",
]
