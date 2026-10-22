"""净值与收益纯核心（Period2-03）：份额法 TWR + XIRR/MWR。

## 份额法 TWR（单位净值法）

外部现金流（银证转账等）按发生时点的单位净值折成份额，
这样收益率不受出入金规模扭曲——衡量"策略本身"的表现。

算法（现金流日末发生假设，INTERFACE/PRD FR-R3）：
```
shares_0 = 1
NAV_0    = V_0                          # 第 0 天 = 期初资产快照
NAV_t    = (V_t - CF_t) / shares_{t-1}  # V_t 日末总资产，CF_t 日末外部现金流
Δshares  = CF_t / NAV_t                 # 现金流按当日 NAV 折份额
shares_t = shares_{t-1} + Δshares
r_t      = NAV_t / NAV_{t-1} - 1        # 日收益率
TWR      = Π(1 + r_t) - 1
年化TWR   = (1 + TWR)^(365 / 自然日数) - 1
```

其中 `NAV_t = (V_t - CF_t) / shares_{t-1}` 是因为 V_t 已包含入金现金，
必须减去 CF_t 才能得到"投资收益对应的资产"。

## MWR（XIRR）

从投资者视角的资金加权收益率：
```
Σ CF_i / (1 + x)^((d_i - d_0) / 365) = 0
```
- 期初投入为负、期末市值为正
- 期间外部现金流：入金负、出金正
- 解 x 即年化 MWR；用二分法（区间 [-0.99, 10]，精度 1e-8）。

## 标度

| 量 | scale | 说明 |
|---|---|---|
| 资产/现金流 | 2（分） | Money |
| 单位净值 | 6 | Price 级精度 |
| 收益率/TWR/MWR | 8 | Ratio |

零 I/O、零 wall-clock；时间通过 `Clock` 注入（当前模块不需要时钟，纯确定性）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# 标度常量（与 values.py 对齐）
_SCALE_MONEY = 2
_SCALE_NAV = 6
_SCALE_RATIO = 8

# XIRR 二分法参数。上限 100（10000% 年化）覆盖正常投资周期；
# 极短周期（数日）的真实解可能超出，此时返回边界近似。
_XIRR_LOW = Decimal("-0.99")
_XIRR_HIGH = Decimal("100")
_XIRR_TOL = Decimal("1e-8")
_XIRR_MAX_ITER = 200


@dataclass(frozen=True, slots=True)
class NavPoint:
    """单日净值点。

    `nav_units` 单位净值（scale=6）；`return_units` 日收益率（scale=8，相对上一日）；
    `cashflow_units` 当日外部现金流（scale=2，入金正、出金负）；
    `shares_units` 当日末份额（scale=8，精度足够避免累积误差）；
    `total_value_units` 当日日末总资产（scale=2，持仓市值+现金）。
    """

    trade_date: date
    nav_units: int
    return_units: int
    cashflow_units: int
    shares_units: int
    total_value_units: int


@dataclass(frozen=True, slots=True)
class NavSeries:
    """份额法净值序列 + 汇总指标。"""

    points: tuple[NavPoint, ...]
    twr_units: int  # 累计 TWR（scale=8）
    annualized_twr_units: int  # 年化 TWR（scale=8）
    initial_value_units: int  # 期初资产（分）
    ending_value_units: int  # 期末资产（分）
    total_cashflow_units: int  # 区间外部现金流净额（分）
    natural_days: int  # 区间自然日数


@dataclass(frozen=True, slots=True)
class CashflowPoint:
    """XIRR 现金流点（投资者视角：投入负、收回正）。"""

    trade_date: date
    amount_units: int  # 分


def _to_decimal(units: int, scale: int) -> Decimal:
    """标度整数 → Decimal（不引入 float）。"""
    return Decimal(units).scaleb(-scale)


def _from_decimal(value: Decimal, scale: int) -> int:
    """Decimal → 标度整数（四舍五入）。"""
    return int(value.scaleb(scale).to_integral_value(rounding=ROUND_HALF_UP))


def build_nav_series(
    daily_points: list[tuple[date, int, int]],
) -> NavSeries:
    """份额法 TWR 计算。

    Args:
        daily_points: 按 date 升序的 `(date, total_value_units, cashflow_units)` 序列。
            `total_value_units` 当日日末总资产（持仓市值+现金，分）；
            `cashflow_units` 当日外部现金流（入金正、出金负，分）。
            至少需要 1 个点（期初快照）；空序列返回空 NavSeries。

    Returns:
        NavSeries：逐日净值点 + TWR 汇总。

    不变量：
    - `points` 按 date 严格升序；
    - 第 0 个点的 `return_units == 0`（无前置日）；
    - `Σ cashflow_units == total_cashflow_units`；
    - `ending_value_units == daily_points[-1].total_value`。
    """
    if not daily_points:
        return NavSeries(
            points=(),
            twr_units=0,
            annualized_twr_units=0,
            initial_value_units=0,
            ending_value_units=0,
            total_cashflow_units=0,
            natural_days=0,
        )

    # 防御：按 date 升序（调用方应保证，这里不重排以保持稳定序，但校验）
    for i in range(1, len(daily_points)):
        if daily_points[i][0] < daily_points[i - 1][0]:
            raise ValueError("daily_points 必须按 date 升序")

    # shares 用 Decimal 精确计算（份额会被现金流按 NAV 折分，累积误差需控制）
    shares = Decimal(1)
    prev_nav: Decimal | None = None
    points: list[NavPoint] = []
    total_cf = 0

    for idx, (d, value_units, cf_units) in enumerate(daily_points):
        value = _to_decimal(value_units, _SCALE_MONEY)
        cf = _to_decimal(cf_units, _SCALE_MONEY)
        total_cf += cf_units

        if idx == 0:
            # 期初快照：shares=1，NAV = 期初资产
            nav = value
            ret = Decimal(0)
        else:
            # NAV_t = (V_t - CF_t) / shares_{t-1}；份额归 0 不应发生（防御）
            nav = value - cf if shares == 0 else (value - cf) / shares
            # 新增份额 = CF / NAV；若 NAV 为 0 则不折份额（极端防御）
            if nav != 0 and cf != 0:
                shares += cf / nav
            ret = nav / prev_nav - Decimal(1) if prev_nav not in (None, Decimal(0)) else Decimal(0)

        points.append(
            NavPoint(
                trade_date=d,
                nav_units=_from_decimal(nav, _SCALE_NAV),
                return_units=_from_decimal(ret, _SCALE_RATIO),
                cashflow_units=cf_units,
                shares_units=_from_decimal(shares, _SCALE_RATIO),
                total_value_units=value_units,
            )
        )
        prev_nav = nav

    # 累计 TWR = Π(1 + r_t) - 1（跳过第 0 天 r=0）
    twr = Decimal(1)
    for p in points[1:]:
        twr *= Decimal(1) + _to_decimal(p.return_units, _SCALE_RATIO)
    twr -= Decimal(1)

    # 年化 TWR = (1 + TWR)^(365 / 自然日) - 1
    natural_days = (points[-1].trade_date - points[0].trade_date).days
    if natural_days > 0:
        # Decimal 的幂运算：用 ln/exp 近似会丢精度；直接用 Decimal 幂
        annualized_twr = (Decimal(1) + twr) ** (Decimal(365) / Decimal(natural_days)) - Decimal(1)
    else:
        annualized_twr = Decimal(0)

    return NavSeries(
        points=tuple(points),
        twr_units=_from_decimal(twr, _SCALE_RATIO),
        annualized_twr_units=_from_decimal(annualized_twr, _SCALE_RATIO),
        initial_value_units=daily_points[0][1],
        ending_value_units=daily_points[-1][1],
        total_cashflow_units=total_cf,
        natural_days=natural_days,
    )


def compute_xirr(cashflows: list[CashflowPoint]) -> int:
    """XIRR 求解年化资金加权收益率（scale=8）。

    Args:
        cashflows: 投资者视角的现金流序列（投入负、收回正），含期初和期末。

    Returns:
        年化 MWR 的标度整数（scale=8）。若无有效现金流（全为 0）或无解，返回 0。

    算法：二分法。NPV(x) = Σ CF_i / (1+x)^((d_i-d_0)/365)。
    NPV 单调递减 → 二分唯一解。区间 [-0.99, 10] 覆盖常规收益率。
    """
    if not cashflows:
        return 0

    # 至少需要一正一负才能有解
    has_pos = any(c.amount_units > 0 for c in cashflows)
    has_neg = any(c.amount_units < 0 for c in cashflows)
    if not (has_pos and has_neg):
        return 0

    d0 = cashflows[0].trade_date
    for i in range(1, len(cashflows)):
        if cashflows[i].trade_date < cashflows[i - 1].trade_date:
            raise ValueError("cashflows 必须按 date 升序")

    def npv(rate: Decimal) -> Decimal:
        total = Decimal(0)
        for cf in cashflows:
            years = Decimal((cf.trade_date - d0).days) / Decimal(365)
            amount = _to_decimal(cf.amount_units, _SCALE_MONEY)
            total += amount / ((Decimal(1) + rate) ** years)
        return total

    low, high = _XIRR_LOW, _XIRR_HIGH
    npv_low = npv(low)
    npv_high = npv(high)

    # 若端点同号，说明解在区间外（极短周期的极端收益率）；返回较近的边界近似。
    if npv_low * npv_high > 0:
        # npv 是 rate 的单调递减函数；都为正 → 真实解 > high；都为负 → 真实解 < low。
        if abs(npv_low) < abs(npv_high):
            return _from_decimal(low, _SCALE_RATIO)
        return _from_decimal(high, _SCALE_RATIO)

    for _ in range(_XIRR_MAX_ITER):
        mid = (low + high) / Decimal(2)
        npv_mid = npv(mid)
        if abs(npv_mid) < _XIRR_TOL:
            return _from_decimal(mid, _SCALE_RATIO)
        if npv_low * npv_mid <= 0:
            high = mid
            npv_high = npv_mid
        else:
            low = mid
            npv_low = npv_mid

    return _from_decimal((low + high) / Decimal(2), _SCALE_RATIO)  # pragma: no cover


def back_solve_initial_cash(first_balance_units: int, first_amount_units: int) -> int:
    """期初资产反推（back_solved）：首行资金余额 - 首行发生金额。

    PRD FR-R2.1：当区间前无现金余额行时，用首行余额反推该行之前的现金。
    `first_amount_units` 为带符号净现金流（入金正、出金负）。

    Args:
        first_balance_units: 首行现金余额（分）
        first_amount_units: 首行发生金额（分，带符号）

    Returns:
        反推的期初现金（分）
    """
    return first_balance_units - first_amount_units


__all__ = [
    "CashflowPoint",
    "NavPoint",
    "NavSeries",
    "back_solve_initial_cash",
    "build_nav_series",
    "compute_xirr",
]
