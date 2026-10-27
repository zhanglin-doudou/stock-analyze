"""回撤族（Period2-07）：MDD / duration / recovery / underwater / Ulcer / Calmar。

输入：日收益序列（标度整数，scale=8；正值/负值均可）；blocked 行由调用方提前剔除并
通过 `blocked` 标志传递——本模块不拼接 blocked 断点（§5.6）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import SCALE_RATIO


@dataclass(frozen=True)
class DrawdownFamily:
    """回撤族指标（全部标度整数，scale=8，除说明外）。"""

    mdd_units: int | None
    mdd_duration_obs: int | None  # 峰→谷，以"日收益观测数"计（非自然日）
    mdd_recovery_obs: int | None  # 谷→首次回到前高；未恢复 → None + recovery_censored=True
    recovery_censored: bool
    underwater_ratio_units: int | None  # 水下天数 / 总观测数
    ulcer_units: int | None
    calmar_units: int | None  # 年化 TWR / |MDD|
    twr_annual_units: int | None  # 年化 TWR
    n_obs: int
    n_missing: int = 0  # NULL 洞数（已剔除）
    # 状态
    blocked: bool = False  # blocked 断点（调用方标记）
    unreliable: bool = False  # |r|>1 或其他异常


def _compound_index(
    daily_returns_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> list[Decimal]:
    """复利指数 I_t = I_{t-1} × (1 + r_t)，I_0=1。返回 Decimal 序列（长度=len+1，I_0 在前）。"""
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        index: list[Decimal] = [Decimal(1)]
        for r_units in daily_returns_units:
            r = Decimal(r_units) / sf
            index.append(index[-1] * (Decimal(1) + r))
        return index


def drawdown_family(
    daily_returns_units: list[int],
    *,
    natural_days: int | None = None,  # 区间自然日数（用于年化 TWR）；None 时用 len(returns)
    blocked: bool = False,
    scale: int = SCALE_RATIO,
    min_obs: int = 2,
) -> DrawdownFamily:
    """计算回撤族全部成员。

    Args:
        daily_returns_units: 逐自然日日收益（标度整数，scale=8）；NULL 行**已剔除**。
        natural_days: 区间自然日总数（用于年化 TWR 分母 365/natural_days）；
                      None 时退化用 len(returns)（调用方通常传自然日差）。
        blocked: 区间含 blocked 行（断链）——直接 blocked 返回。
        scale: 比率精度（默认 8）。
        min_obs: 最小观测数（默认 2，太少不可算）。
    """
    n = len(daily_returns_units)

    if blocked:
        return DrawdownFamily(
            mdd_units=None,
            mdd_duration_obs=None,
            mdd_recovery_obs=None,
            recovery_censored=False,
            underwater_ratio_units=None,
            ulcer_units=None,
            calmar_units=None,
            twr_annual_units=None,
            n_obs=n,
            blocked=True,
        )

    if n < min_obs:
        return DrawdownFamily(
            mdd_units=None,
            mdd_duration_obs=None,
            mdd_recovery_obs=None,
            recovery_censored=False,
            underwater_ratio_units=None,
            ulcer_units=None,
            calmar_units=None,
            twr_annual_units=None,
            n_obs=n,
        )

    # 检查异常日收益（|r|>1 暗示负市值/超卖）
    scale_factor = 10**scale
    max_abs_r = scale_factor  # r=1 → 1e8 单位
    unreliable = any(abs(r) > max_abs_r for r in daily_returns_units)

    with localcontext() as ctx:
        ctx.prec = 40
        Decimal(scale_factor)
        index = _compound_index(daily_returns_units, scale=scale)

        # —— 逐点峰、回撤、MDD 扫描 ——
        # index 长度 = n+1（I_0..I_n）
        peaks: list[Decimal] = [Decimal(0)] * (n + 1)
        dds: list[Decimal] = [Decimal(0)] * (n + 1)  # (I_t - peak_t) / peak_t
        peaks[0] = index[0]
        dds[0] = Decimal(0)
        max_dd = Decimal(0)
        mdd_idx = 0
        for t in range(1, n + 1):
            pt = peaks[t - 1]
            if index[t] > pt:
                pt = index[t]
            peaks[t] = pt
            dd_t = (index[t] - pt) / pt
            dds[t] = dd_t
            abs_dd = -dd_t if dd_t < 0 else Decimal(0)
            if abs_dd > max_dd:
                max_dd = abs_dd
                mdd_idx = t

        # —— MDD duration: 从峰开始到谷的观测数 ——
        # 找到 mdd_idx 之前最近的 peak 切换点（index 首次达到该峰的时刻）
        peak_at_mdd = peaks[mdd_idx]
        # 向后搜索：从 mdd_idx 往前找第一个 index[t] == peak_at_mdd 的 t
        peak_start_idx = 0
        for t in range(mdd_idx - 1, -1, -1):
            if index[t] >= peak_at_mdd:
                peak_start_idx = t
                break
        mdd_duration = mdd_idx - peak_start_idx  # 观测数（日收益条数）

        # —— MDD recovery: 从 mdd_idx 往后找首次回到 peak_at_mdd ——
        recovery_obs: int | None = None
        recovery_censored = False
        for t in range(mdd_idx + 1, n + 1):
            if index[t] >= peak_at_mdd:
                recovery_obs = t - mdd_idx
                break
        if recovery_obs is None:
            recovery_censored = True

        # —— underwater_ratio: dd_t < 0 的观测占比（t=1..n） ——
        underwater_count = sum(1 for t in range(1, n + 1) if dds[t] < 0)
        underwater_ratio = Decimal(underwater_count) / Decimal(n)

        # —— Ulcer: sqrt(Σ dd_t² / n) ——
        # n_obs = 日收益观测数（不含 I_0 重复计数），即 `n`
        sum_sq = sum(dds[t] * dds[t] for t in range(1, n + 1))
        ulcer = (sum_sq / Decimal(n)).sqrt()

        # —— 年化 TWR: (I_end)^(365 / natural_days) − 1 ——
        natural = natural_days if natural_days is not None else n
        if natural <= 0 or index[-1] <= 0:
            twr_annual = Decimal(0)
        else:
            # 365/natural 指数
            exponent = Decimal(365) / Decimal(natural)
            twr_annual = index[-1] ** exponent - Decimal(1)

        # —— Calmar: 年化 TWR / |MDD|（MDD=0 → None）——
        if max_dd == 0:
            calmar_dec: Decimal | None = None
        else:
            calmar_dec = twr_annual / max_dd

        # 量化出口
        quantum = Decimal(1).scaleb(-scale)

        def q(value: Decimal) -> int:
            return int(value.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))

        mdd_u = q(max_dd)
        underwater_u = q(underwater_ratio)
        ulcer_u = q(ulcer)
        twr_annual_u = q(twr_annual)
        calmar_u = q(calmar_dec) if calmar_dec is not None else None

    return DrawdownFamily(
        mdd_units=mdd_u,
        mdd_duration_obs=mdd_duration if max_dd > 0 else None,
        mdd_recovery_obs=recovery_obs,
        recovery_censored=recovery_censored,
        underwater_ratio_units=underwater_u,
        ulcer_units=ulcer_u,
        calmar_units=calmar_u,
        twr_annual_units=twr_annual_u,
        n_obs=n,
        unreliable=unreliable,
    )
