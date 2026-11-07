"""公司行动判定（回合洞察共用，设计 D-12/评审 P0-4）。

前瞻收益必须在“未复权收盘价 + 账本公司行动”上计算：窗口内出现送转/配股/分红/拆并，
或 `other` 摘要含明确送转语义（如“10送10”），一律降级 `corporate_action`，不排名。
本模块只做纯判定、零 I/O；与归因的 `corporate_action_pnl` 口径无关，互不影响。
"""

from __future__ import annotations

_CORPORATE_CATEGORIES: frozenset[str] = frozenset(
    {"bonus", "rights", "dividend", "split", "merger"}
)
_STRONG_KEYWORDS: tuple[str, ...] = ("送股", "转增", "送转", "配股", "拆股", "合并")


def is_corporate_action(
    *,
    category: str | None,
    summary: str | None,
    quantity_units: int,
    amount_units: int,
) -> bool:
    """是否公司行动行；`other` 需零金额 + 正数量 + 强关键词才判定为送转。"""
    cat = (category or "").strip().lower()
    if cat in _CORPORATE_CATEGORIES:
        return True
    text = summary or ""
    if cat in ("", "other") and amount_units == 0 and quantity_units > 0:
        return any(keyword in text for keyword in _STRONG_KEYWORDS)
    return False


__all__ = ["is_corporate_action"]
