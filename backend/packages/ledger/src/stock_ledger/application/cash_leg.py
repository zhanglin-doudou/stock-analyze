"""资金腿归属归一（导入落库前）：把「只有金额、没有份额」的买卖资金腿落到真实证券上。

背景（设计 D16）：华泰资管转让的货基赎回会拆成两条账单行——资金腿
（`资管转让资金上账`，只有发生金额，没有证券代码/数量/单价）与份额腿
（`非交易过户确认`，有证券代码/单价/数量，没有资金变更）。若资金腿按 `CASH`
伪代码落库，货基余额与券商现金会重复计入，当日收益被虚增。

归属顺序（2026-11-18 用户口径）：

1. **同日份额腿配对**：在本批次内找隐含金额与资金腿金额一致的份额行（容差 1 分），
   把证券代码/数量/单价写到资金腿上；扣减前校验该证券份额不为负，为负则放弃配对、
   转兜底（不做部分扣减）。
2. **货币基金兜底**：取该用户当日货基持仓，按余额从多到少依次增/减份额，单价恒 1；
   一只不够就轮到下一只，最后剩下才落现金余额。
3. **现金余额兜底**：现金余额扣完仍为负 → `IMPORT_CHAIN_DISCONTINUOUS` 报错
   （视为对账问题，本批次回滚，不静默 clamp）。

设计取舍：

* 放在**落库前**归一（写回 `TradeRow`），让 CSV、资产表、货基余额链、FIFO 看到
  同一份数据；放到估值/事件层会让导出与计算口径分叉。
* 配对**只在本批次内**做（同文件同日）：份额腿没有持久占用标记，跨批次复用会让
  两条资金腿抢同一条份额腿；跨批次场景走货基兜底。
* 收益不另算：货基按 1 元净值进资产表，非货基卖出交给既有 FIFO/回合与
  资产表「总资产 − 上日总资产 − 外部现金流」口径。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date
from typing import Any

from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.obs import get_logger

from stock_ledger.application.money_fund import (
    QTY_PRICE_SCALE,
    UNIT_PRICE_UNITS,
    money_fund_balances,
    money_fund_codes,
    money_fund_identity,
)
from stock_ledger.repositories.trades import TradeRepository, TradeRow

log = get_logger("stock_ledger.cash_leg")

# 资金腿：只有发生金额，没有证券代码/数量/单价。
_CASH_LEG_CATEGORIES: frozenset[str] = frozenset({"buy", "sell"})
# 份额腿隐含金额与资金腿金额的容差（scale=2，1 = ¥0.01，吸收分位四舍五入）。
_MATCH_TOLERANCE_UNITS = 1
# 金额（scale=2）→ 数量（scale=4）：单价恒 1 时数量 = 金额 × 100。
_QTY_PER_AMOUNT_UNIT = 100
# 份额增加的分类（用于非货基证券的份额校验）。
_ADD_CATEGORIES: frozenset[str] = frozenset({"buy", "bonus", "rights", "new_stock"})


def _category(row: Any) -> str:
    return (row.category or "").strip().lower()


def is_real_security(code: str | None) -> bool:
    """6 位数字证券代码；`CASH`、空串等伪代码一律不算真实证券。"""
    text = (code or "").strip()
    return len(text) == 6 and text.isdigit()


def is_cash_only_leg(row: Any) -> bool:
    """买卖资金腿：`category=buy/sell`、有发生金额、无数量/单价、代码是伪代码。"""
    return (
        _category(row) in _CASH_LEG_CATEGORIES
        and row.amount_units != 0
        and row.quantity_units == 0
        and row.price_units == 0
        and not is_real_security(row.security_code)
    )


def _is_share_leg(row: Any) -> bool:
    """份额腿：有真实证券代码、单价与数量，没有资金变更。"""
    return (
        is_real_security(row.security_code)
        and row.quantity_units != 0
        and row.price_units > 0
        and row.amount_units == 0
    )


def _implied_amount_units(row: Any) -> int:
    return abs(row.quantity_units) * row.price_units // QTY_PRICE_SCALE


class _FundIndex:
    """按日期缓存货基余额（`before` = 当日开始，同日流水不参与）。

    余额键是**归并身份键**（同名不同代码合并成一只）；`key()` 把交易行代码映射到
    身份键，配对校验和兜底分派都与估值侧共享同一套口径。
    """

    __slots__ = ("_cache", "_codes", "_identity", "_rows")

    def __init__(self, rows: Sequence[Any]) -> None:
        self._rows = rows
        self._codes = money_fund_codes(rows)
        self._identity = money_fund_identity(rows)
        self._cache: dict[date, dict[str, int]] = {}

    @property
    def codes(self) -> frozenset[str]:
        return self._codes

    def key(self, code: str | None) -> str | None:
        """交易行代码 → 归并身份键；非货基返回 `None`。"""
        return self._identity.get((code or "").strip())

    def balances(self, day: date) -> dict[str, int]:
        cached = self._cache.get(day)
        if cached is None:
            cached = {
                code: value
                for code, value in money_fund_balances(self._rows, before=day).items()
                if value > 0 and is_real_security(code)
            }
            self._cache[day] = cached
        return cached


def _share_quantity_before(rows: Sequence[Any], code: str, day: date) -> int:
    """非货基证券在 `day` 之前的份额合计（首笔即卖出的历史可能为负，保守拒绝配对）。"""
    total = 0
    for row in rows:
        if (row.security_code or "").strip() != code or row.trade_date >= day:
            continue
        category = _category(row)
        if category == "sell":
            total -= row.quantity_units
        elif category in _ADD_CATEGORIES:
            total += row.quantity_units
    return total


def _can_pair(leg: Any, share: Any, funds: _FundIndex, rows: Sequence[Any]) -> bool:
    """扣减前校验份额不为负；买入（增加份额）恒可配对。"""
    if _category(leg) != "sell":
        return True
    value = _implied_amount_units(share)
    if share.security_code in funds.codes:
        key = funds.key(share.security_code) or share.security_code
        return funds.balances(leg.trade_date).get(key, 0) >= value
    return _share_quantity_before(rows, share.security_code, leg.trade_date) >= abs(
        share.quantity_units
    )


def _find_share_leg(leg: Any, candidates: Sequence[Any], used: set[int]) -> Any | None:
    """同日份额腿：隐含金额最接近（容差 1 分），再按行号/代码稳定排序，且未被占用。"""
    amount = abs(leg.amount_units)
    best: tuple[tuple[int, int, str], Any] | None = None
    for candidate in candidates:
        # 只配对**同一交易日**的份额腿：跨日配对会把当天真实的持仓/收益挪到别的日期。
        if candidate.trade_date != leg.trade_date:
            continue
        if id(candidate) in used or candidate is leg:
            continue
        diff = abs(_implied_amount_units(candidate) - amount)
        if diff > _MATCH_TOLERANCE_UNITS:
            continue
        line_no = candidate.line_no if candidate.line_no is not None else 10**9
        key = (diff, line_no, candidate.security_code)
        if best is None or key < best[0]:
            best = (key, candidate)
    return None if best is None else best[1]


def _apply_pair(leg: Any, share: Any) -> int:
    """把份额腿的证券代码/数量/单价写到资金腿，返回带方向的份额变更数（仅日志用）。"""
    quantity = abs(share.quantity_units)
    leg.security_code = share.security_code
    leg.security_name = share.security_name or leg.security_name
    leg.quantity_units = quantity
    leg.price_units = share.price_units
    return -quantity if _category(leg) == "sell" else quantity


def _derived_dedupe_key(original: str, index: int) -> str:
    """拆行后的派生去重键：确定性，保证重复导入仍然幂等。"""
    return hashlib.sha256(f"{original}|cash-leg|{index}".encode()).hexdigest()


def _split_row(row: TradeRow, index: int) -> TradeRow:
    """复制一行用于多只货基/现金分摊；余额覆盖只保留在原行，避免同日重复覆盖。"""
    return replace(
        row,
        dedupe_key=_derived_dedupe_key(row.dedupe_key, index),
        cash_balance_units=None,
        cash_balance_available=False,
    )


def _last_cash_balance(rows: Iterable[Any], day: date) -> int | None:
    """取 `day`（含）之前最后一条带资金余额的流水余额（scale=2）。"""
    latest: tuple[date, int] | None = None
    balance: int | None = None
    for row in rows:
        if row.cash_balance_units is None or row.trade_date > day:
            continue
        line_no = row.line_no if row.line_no is not None else 10**9
        key = (row.trade_date, line_no)
        if latest is None or key >= latest:
            latest = key
            balance = row.cash_balance_units
    return balance


def _ensure_cash_covers(leg: Any, residual_units: int, rows: Sequence[Any]) -> None:
    """现金兜底：余额扣完不能为负；无余额列时无法核对，放行（不阻塞无余额账单）。"""
    balance = _last_cash_balance(rows, leg.trade_date)
    if balance is None:
        return
    if balance - residual_units < 0:
        raise ApiError(
            ErrorCode.IMPORT_CHAIN_DISCONTINUOUS,
            detail=(
                f"资金腿「{leg.summary or leg.security_code}」金额 {leg.amount_units / 100:.2f} "
                f"无法由货基持仓解释，现金余额 {balance / 100:.2f} 扣减 "
                f"{residual_units / 100:.2f} 后为负，疑似对账问题"
            ),
        )


def _fund_names(rows: Sequence[Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in rows:
        code = (row.security_code or "").strip()
        if is_real_security(code) and (row.security_name or "").strip():
            names[code] = row.security_name.strip()
    return names


def _apply_fallback(
    leg: TradeRow,
    *,
    funds: _FundIndex,
    names: dict[str, str],
    rows: Sequence[Any],
) -> list[TradeRow]:
    """货基兜底：按持仓份额从多到少增减，剩余落现金；现金扣成负则报错。"""
    signed_amount = leg.amount_units
    total = abs(signed_amount)
    sign = 1 if signed_amount > 0 else -1
    is_sell = _category(leg) == "sell"
    ranked = sorted(funds.balances(leg.trade_date).items(), key=lambda kv: (-kv[1], kv[0]))

    portions: list[tuple[str, int]] = []
    remaining = total
    for code, balance in ranked:
        if remaining <= 0:
            break
        take = min(remaining, balance) if is_sell else remaining
        if take <= 0:
            continue
        portions.append((code, take))
        remaining -= take

    out: list[TradeRow] = []
    # 先留一份未被改写的快照：第一段直接改原行（保留原去重键），后续拆分/现金残值都从快照复制。
    pristine = replace(leg)
    for index, (code, take) in enumerate(portions):
        row = leg if index == 0 else _split_row(pristine, index)
        row.security_code = code
        row.security_name = names.get(code) or row.security_name
        row.quantity_units = take * _QTY_PER_AMOUNT_UNIT
        row.price_units = UNIT_PRICE_UNITS
        row.amount_units = sign * take
        _log_fund_adjusted(leg, code, -take if is_sell else take, sign * take)
        out.append(row)

    if remaining > 0:
        # 卖出才要求「现金余额扣完不为负」；买入没落到货基只是现金流出，由资产表体现。
        if is_sell:
            _ensure_cash_covers(pristine, remaining, rows)
        if portions:
            row = _split_row(pristine, len(portions))
            row.amount_units = sign * remaining
            row.quantity_units = 0
            row.price_units = 0
            out.append(row)
        else:
            out.append(leg)
    return out


def _log_fund_adjusted(leg: Any, code: str, quantity_units: int, amount_units: int) -> None:
    log.info(
        "import.cash_leg.money_fund_adjusted",
        summary=leg.summary,
        security_code=code,
        quantity_units=quantity_units,
        amount_units=amount_units,
    )


def _stable_order(rows: Iterable[Any]) -> list[Any]:
    """与资产表一致的稳定排序：日期 → 行号 → side=missing 优先 → 分类权重。

    货基余额推演有「负余额按 0 兜底」，结果依赖流水顺序；导入侧必须与
    `review.assets` 使用同一顺序，否则兜底金额会与估值侧不一致。
    """
    weight = {"buy": 0, "bonus": 0, "rights": 0, "new_stock": 0, "sell": 1}

    def key(row: Any) -> tuple[date, int, int, int, str]:
        line_no = row.line_no if row.line_no is not None else -1
        side_rank = 0 if (getattr(row, "side", "") or "") == "missing" else 1
        # 本批次待插入行没有 id；同一毫秒内的 ULID 随机位不影响货基余额（同向累计
        # 与顺序无关），用空串占位即可。
        return (
            row.trade_date,
            line_no,
            side_rank,
            weight.get(_category(row), 2),
            getattr(row, "id", "") or "",
        )

    return sorted(rows, key=key)


def attribute_cash_legs(rows: Sequence[TradeRow], *, history: Sequence[Any] = ()) -> list[TradeRow]:
    """落库前归一：资金腿配对份额腿 → 货基兜底 → 现金兜底（纯函数，无 I/O）。

    `history` 是同一用户已落库的流水（用于货基持仓与现金余额）；本批次行优先。
    """
    if not any(is_cash_only_leg(row) for row in rows):
        return list(rows)

    all_rows = _stable_order([*history, *rows])
    funds = _FundIndex(all_rows)
    names = _fund_names(all_rows)
    candidates = [row for row in rows if _is_share_leg(row)]
    used: set[int] = set()
    out: list[TradeRow] = []

    for row in rows:
        if not is_cash_only_leg(row):
            out.append(row)
            continue
        share = _find_share_leg(row, candidates, used)
        if share is not None:
            if _can_pair(row, share, funds, all_rows):
                quantity = _apply_pair(row, share)
                used.add(id(share))
                log.info(
                    "import.cash_leg.share_leg_paired",
                    summary=row.summary,
                    security_code=share.security_code,
                    quantity_units=quantity,
                    amount_units=row.amount_units,
                )
                out.append(row)
                continue
            rejected_quantity = (
                -abs(share.quantity_units)
                if _category(row) == "sell"
                else abs(share.quantity_units)
            )
            log.warning(
                "import.cash_leg.share_leg_rejected",
                summary=row.summary,
                security_code=share.security_code,
                quantity_units=rejected_quantity,
                amount_units=row.amount_units,
            )
        out.extend(_apply_fallback(row, funds=funds, names=names, rows=all_rows))
    return out


async def attribute_cash_legs_from_db(
    session: Any, user_id: Any, rows: Sequence[TradeRow]
) -> list[TradeRow]:
    """提交阶段入口：有资金腿时补载该用户历史流水，再走纯函数归一。"""
    if not any(is_cash_only_leg(row) for row in rows):
        return list(rows)
    history = await TradeRepository(session).list_all(user_id)
    return attribute_cash_legs(rows, history=list(history))


__all__ = [
    "attribute_cash_legs",
    "attribute_cash_legs_from_db",
    "is_cash_only_leg",
    "is_real_security",
]
