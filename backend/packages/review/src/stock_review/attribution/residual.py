"""归因装配层：残差拆桶（设计 §3.6，六桶互斥、Σ桶 == residual）。

桶代数（已由独立整数原型 + 手算 golden 验证）：

- **标的级桶**（仅 bucketed 标的：非 CASH、非货基、非归因，且期初/期末有持仓或窗口有
  持仓类行）：
  `bucket_x = (MV_x(d_end) − MV_x(d_begin)) + Σ_{x 窗口行} amount + Σ_{x 窗口行} 三费列`
  ——加三费列是因为账单「发生金额」已含费（买 = −(成交额+费)、卖 = +(成交额−费)），
  桶是**毛损益**；费用由 fee 因子集中承接（`F_u`），避免双计。
- **货基桶**（镜像资产表余额覆盖制）：`(bal_end − bal_begin) + Σ_{mf 行} amount`。
- **CASH 不进标的级桶**（避免与 interest/other_cashflow 双计）。
- **归因标的**：段因子已覆盖其全部窗口损益；仅其独立 `fee` 行（红利税）进 `F_indep`。
- **F_u** = Σ bucketed 三费列（fee 因子承接，桶内已还原毛额）；**F_indep** =
  −Σ 非 bucketed 标的 `category=fee` 行 amount（CASH 费用行与归因标的红利税行）；
  `extra_fee_units = F_u + F_indep`。
- **dividend_without_position / interest / other_cashflow**：非 bucket 非归因标的的
  对应行（bucketed 标的的这些行已在其桶内）。
- **无法估值的 bucketed 标的**（无行情且无成本兜底）：不进桶 → 行金额由
  `valuation_gap`（plug）吸收（设计 §3.6 明示）。
- `valuation_gap = residual − Σ前五桶`（plug）在应用层收口。

transfer 行不进任何桶（`CF_period` 已从 total_pnl 扣除）。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from stock_ledger.models import Trade
from stock_review.application.valuation import category, is_money_fund_row
from stock_review.attribution.segments import SymbolPlan

# 桶键（§3.6 固定六桶；valuation_gap 为 plug 由应用层计算）。
BUCKET_CORPORATE = "corporate_action_pnl"
BUCKET_UNATTRIBUTED = "unattributed_price_pnl"
BUCKET_DIVIDEND = "dividend_without_position"
BUCKET_INTEREST = "interest"
BUCKET_OTHER = "other_cashflow"
BUCKET_GAP = "valuation_gap"
BUCKET_KEYS: tuple[str, ...] = (
    BUCKET_CORPORATE,
    BUCKET_UNATTRIBUTED,
    BUCKET_DIVIDEND,
    BUCKET_INTEREST,
    BUCKET_OTHER,
    BUCKET_GAP,
)

# 已归集到固定桶/因子的类别；之外的行落 other_cashflow（打新冻结/补差/other/NULL）。
_CLASSIFIED = frozenset(
    {
        "buy",
        "sell",
        "dividend",
        "interest",
        "bonus",
        "rights",
        "new_stock",
        "transfer",
        "fee",
    }
)
_POSITION_CATEGORIES = frozenset({"buy", "sell", "bonus", "rights", "new_stock"})
_CASH_CODE = "CASH"


@dataclass(slots=True)
class ResidualSplit:
    """残差拆桶结果（application 层消费；valuation_gap 由调用方 plug）。"""

    buckets: dict[str, int]  # 前五桶（不含 valuation_gap）
    fee_bucketed_units: int  # F_u：bucketed 标的三费列合计（fee 因子承接）
    fee_indep_units: int  # F_indep：非 bucketed 独立 fee 行（正数）
    per_symbol: dict[str, int] = field(default_factory=dict)  # excluded 标的桶值
    unvalued_symbols: list[str] = field(default_factory=list)  # 无法估值的 bucketed 标的


def split_residual(
    *,
    plans: Sequence[SymbolPlan],
    trades: Sequence[Trade],
    t0: date,
    t1: date,
    begin_date: date,
    end_date: date,
    bucket_mv: Mapping[str, tuple[int, int]],
    money_fund_balances: Mapping[str, tuple[int, int]],
    holdings_at: Mapping[str, tuple[int, int]],
    corporate: Mapping[str, str],
    canonical: Callable[[str], str],
) -> ResidualSplit:
    """按 §3.6 桶公式拆分（纯内存；`residual` 本身由 decompose 给出）。"""
    attributed_codes = {plan.security_code for plan in plans if plan.attributed}
    money_fund_codes = {
        canonical(trade.security_code) for trade in trades if is_money_fund_row(trade)
    }
    money_fund_codes.discard("")
    money_fund_codes |= set(money_fund_balances)

    buckets: dict[str, int] = {key: 0 for key in BUCKET_KEYS if key != BUCKET_GAP}
    split = ResidualSplit(buckets=buckets, fee_bucketed_units=0, fee_indep_units=0)

    rows_by_code: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        if not (t0 <= trade.trade_date <= t1):
            continue
        if category(trade) == "transfer":
            continue  # 外部现金流已从 total_pnl 扣除，不进桶
        code = canonical(trade.security_code)
        if code:
            rows_by_code[code].append(trade)

    for code in sorted(set(rows_by_code) | set(holdings_at)):
        rows = rows_by_code.get(code, [])
        if code in attributed_codes:
            # 归因标的：段因子已覆盖；仅独立 fee 行（红利税）进 F_indep。
            for trade in rows:
                if category(trade) == "fee":
                    split.fee_indep_units += -trade.amount_units
            continue
        if code == _CASH_CODE or code in money_fund_codes:
            # CASH / 货基：不进标的级 MV 桶。
            if code in money_fund_codes:
                begin_bal, end_bal = money_fund_balances.get(code, (0, 0))
                value = (end_bal - begin_bal) + sum(t.amount_units for t in rows)
                buckets[BUCKET_UNATTRIBUTED] += value
                split.per_symbol[code] = value
            else:
                _cash_rows(rows, buckets, split)
            continue
        begin_qty, end_qty = holdings_at.get(code, (0, 0))
        has_position = begin_qty != 0 or end_qty != 0
        has_trade = any(category(t) in _POSITION_CATEGORIES for t in rows)
        if not (has_position or has_trade):
            # 无持仓无交易：行按类别落固定桶（无持仓股息/利息/其他）。
            _positionless_rows(rows, buckets, split)
            continue
        # bucketed 标的：毛损益 = ΔMV + Σamount + Σ三费列。
        fees = sum(t.commission_units + t.stamp_tax_units + t.other_fee_units for t in rows)
        amounts = sum(t.amount_units for t in rows)
        if code in bucket_mv:
            begin_mv, end_mv = bucket_mv[code]
            value = (end_mv - begin_mv) + amounts + fees
        else:
            # 无法估值（无行情且无成本兜底）：不进桶，由 valuation_gap 吸收。
            split.unvalued_symbols.append(code)
            value = amounts + fees
        key = BUCKET_CORPORATE if code in corporate else BUCKET_UNATTRIBUTED
        buckets[key] += value
        split.per_symbol[code] = value
        split.fee_bucketed_units += fees
    return split


def _cash_rows(rows: Sequence[Trade], buckets: dict[str, int], split: ResidualSplit) -> None:
    """CASH 行：interest / other_cashflow / fee（F_indep）/ 无持仓股息。"""
    for trade in rows:
        kind = category(trade)
        if kind == "fee":
            split.fee_indep_units += -trade.amount_units  # F_indep 吸收（fee 因子）
            continue
        if kind == "interest":
            buckets[BUCKET_INTEREST] += trade.amount_units
        elif kind == "dividend":
            buckets[BUCKET_DIVIDEND] += trade.amount_units
        elif kind not in _CLASSIFIED:
            buckets[BUCKET_OTHER] += trade.amount_units


def _positionless_rows(
    rows: Sequence[Trade], buckets: dict[str, int], split: ResidualSplit
) -> None:
    """无持仓无交易标的的行：fee 行进 F_indep，其余落固定桶。"""
    for trade in rows:
        kind = category(trade)
        if kind == "fee":
            split.fee_indep_units += -trade.amount_units  # F_indep 统一吸收
            continue
        if kind == "dividend":
            buckets[BUCKET_DIVIDEND] += trade.amount_units
        elif kind == "interest":
            buckets[BUCKET_INTEREST] += trade.amount_units
        elif kind not in _CLASSIFIED:
            buckets[BUCKET_OTHER] += trade.amount_units


__all__ = [
    "BUCKET_CORPORATE",
    "BUCKET_DIVIDEND",
    "BUCKET_GAP",
    "BUCKET_INTEREST",
    "BUCKET_KEYS",
    "BUCKET_OTHER",
    "BUCKET_UNATTRIBUTED",
    "ResidualSplit",
    "split_residual",
]
