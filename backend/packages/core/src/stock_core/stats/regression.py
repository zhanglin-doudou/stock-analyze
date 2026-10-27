"""OLS 回归族（Period2-07）：β/α/IR/TE + 超额算术/几何年化。

输入约定：所有日收益为**标度整数**（默认 scale=8，即 1e-8），中间计算用 Decimal(prec=28)，
出口统一量化到目标 scale。不引入 float、不读时钟、不做 I/O。

公式来源：§5.5（OLS 超额收益回归；α 已在 y 中扣 rf，不再重复减 rf）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Final

from stock_contracts.constants import A_SHUFFLE_DAYS, RF_ANNUAL_DEFAULT, SCALE_RATIO

_MIN_RF_SCALE: Final[int] = 12  # rf_d 日化精度：(1+0.015)^(1/244)−1 ≈ 6e-8，scale=12 保精度


def rf_daily_units(
    rf_annual: Decimal = RF_ANNUAL_DEFAULT,
    *,
    scale: int = _MIN_RF_SCALE,
    annual_base: int = A_SHUFFLE_DAYS,
) -> int:
    """日化无风险利率（整数近似）：(1+rf_a)^(1/A) − 1。

    默认 scale=12（比比率 scale=8 更细，供后续精确扣减）；返回值可直接在
    高 precision 下从日收益中减去。
    """
    with localcontext() as ctx:
        ctx.prec = 40
        daily = (Decimal(1) + rf_annual) ** (Decimal(1) / Decimal(annual_base)) - Decimal(1)
        quantum = Decimal(1).scaleb(-scale)
        return int(daily.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))


@dataclass(frozen=True)
class BetaAlpha:
    """OLS 回归输出（全部为标度整数，scale 见调用约定）。"""

    beta_units: int | None
    alpha_daily_units: int | None
    alpha_annual_units: int | None
    se_alpha_units: int | None
    t_alpha_units: int | None  # scale=4（z 值精度）
    te_units: int | None
    ir_units: int | None
    excess_arith_units: int | None
    excess_geo_units: int | None
    n_obs: int
    # 状态码（07 指标层按此判 status）
    blocked: bool = False  # Sxx=0（基准恒定）
    unreliable: bool = False  # 方差为 0 / 输入异常
    insufficient: bool = False  # n < 3（OLS 至少需要 3 个观测）


def beta_alpha(
    y_units: list[int],
    x_units: list[int],
    *,
    rf_daily_units: int = 0,
    scale: int = SCALE_RATIO,
    z_scale: int = 4,
    annual_base: int = A_SHUFFLE_DAYS,
) -> BetaAlpha:
    """在**超额收益空间**做 OLS：y_t = r_p,t − rf_d, x_t = r_b,t − rf_d。

    输入 y_units / x_units 是**原始**日收益（scale=8），函数内部先在 Decimal 域扣 rf，
    再闭式求回归，最后量化到目标 scale。rf_daily_units 默认 0 表示不再扣减。

    校验示例（scale=8）：
      r_b = [40000, -30000, 90000, 20000, -60000, 70000]   # 0.0040 等
      r_p = [55000, -20000, 110000, 30000, -85000, 60000]
      rf_a = 0.015, A = 244
      → beta=1.15984016, α_ann=−0.00078896, t=−0.0045, TE=0.02690477, IR=3.0230079
    """
    n = len(y_units)
    if n != len(x_units):
        raise ValueError("y_units and x_units must have same length")
    if n < 3:
        return BetaAlpha(
            beta_units=None,
            alpha_daily_units=None,
            alpha_annual_units=None,
            se_alpha_units=None,
            t_alpha_units=None,
            te_units=None,
            ir_units=None,
            excess_arith_units=None,
            excess_geo_units=None,
            n_obs=n,
            insufficient=True,
        )

    scale_factor = Decimal(10) ** scale
    Decimal(10) ** z_scale

    # rf_d 在 y/x scale 下的等效值（两者同 scale=8）
    with localcontext() as ctx:
        ctx.prec = 40
        if rf_daily_units != 0:
            rf_d_dec = Decimal(rf_daily_units) / (Decimal(10) ** _MIN_RF_SCALE)
        else:
            rf_d_dec = Decimal(0)

        # 扣 rf → 超额序列（Decimal，无 scale 语义，都是真实数）
        y_dec = [Decimal(y) / scale_factor - rf_d_dec for y in y_units]
        x_dec = [Decimal(x) / scale_factor - rf_d_dec for x in x_units]

        # OLS 闭式
        sum_x = sum(x_dec)
        sum_y = sum(y_dec)
        sum_xx = sum(x * x for x in x_dec)
        sum_xy = sum(x_dec[i] * y_dec[i] for i in range(n))
        sum_yy = sum(y * y for y in y_dec)

        # n·Σxx − (Σx)²  — 若为 0 则基准恒定
        sxx = Decimal(n) * sum_xx - sum_x * sum_x
        sxy = Decimal(n) * sum_xy - sum_x * sum_y
        syy = Decimal(n) * sum_yy - sum_y * sum_y

        if sxx == 0:
            return BetaAlpha(
                beta_units=None,
                alpha_daily_units=None,
                alpha_annual_units=None,
                se_alpha_units=None,
                t_alpha_units=None,
                te_units=None,
                ir_units=None,
                excess_arith_units=None,
                excess_geo_units=None,
                n_obs=n,
                blocked=True,
            )

        # β、α_d
        beta = sxy / sxx
        mean_x = sum_x / Decimal(n)
        mean_y = sum_y / Decimal(n)
        alpha_daily = mean_y - beta * mean_x

        # SSE / s² / SE(α)
        sse = (syy - sxy * sxy / sxx) / Decimal(n)
        if sse <= 0:
            sse = Decimal("1e-28")  # 完美拟合 → 哨兵值避免除零
        s_sq = sse / Decimal(n - 2)
        var_alpha = s_sq * (Decimal(1) / Decimal(n) + Decimal(n) * mean_x * mean_x / sxx)
        se_alpha = var_alpha.sqrt()
        t_alpha = alpha_daily / se_alpha

        # 超额序列（y_t − β·x_t − α_d = 残差 = excess return for TE/IR）
        # 注：excess return = y_t − β·x_t（即 CAPM 意义下的 α 残差）
        excess = [y_dec[i] - beta * x_dec[i] for i in range(n)]
        mean_excess = sum(excess) / Decimal(n)
        var_excess = sum((e - mean_excess) ** 2 for e in excess) / Decimal(n - 1)
        if var_excess <= 0:
            var_excess = Decimal("1e-28")
        stdev_excess = var_excess.sqrt()

        annual_base_dec = Decimal(annual_base)
        te = stdev_excess * annual_base_dec.sqrt()
        ir = mean_excess / stdev_excess * annual_base_dec.sqrt()

        # 年化 α
        alpha_annual = alpha_daily * annual_base_dec

        # 超额算术年化 / 几何年化（从原始 y / x 序列计算总收益）
        total_return_p = Decimal(1)
        total_return_b = Decimal(1)
        for yt in y_units:
            total_return_p *= Decimal(1) + Decimal(yt) / scale_factor
        for xt in x_units:
            total_return_b *= Decimal(1) + Decimal(xt) / scale_factor
        excess_arith = mean_excess * annual_base_dec
        if total_return_b > 0:
            excess_geo = (
                (total_return_p / total_return_b) ** (annual_base_dec / Decimal(n))
            ) - Decimal(1)
        else:
            excess_geo = Decimal("0")

        # 量化出口（ROUND_HALF_UP）
        quantum = Decimal(1).scaleb(-scale)
        z_quantum = Decimal(1).scaleb(-z_scale)

        def q(value: Decimal) -> int:
            return int(value.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))

        def qz(value: Decimal) -> int:
            return int(value.quantize(z_quantum, rounding=ROUND_HALF_UP).scaleb(z_scale))

        return BetaAlpha(
            beta_units=q(beta),
            alpha_daily_units=q(alpha_daily),
            alpha_annual_units=q(alpha_annual),
            se_alpha_units=q(se_alpha),
            t_alpha_units=qz(t_alpha),
            te_units=q(te),
            ir_units=q(ir),
            excess_arith_units=q(excess_arith),
            excess_geo_units=q(excess_geo),
            n_obs=n,
        )


def stdev_units(values: list[int], *, scale: int = SCALE_RATIO, ddof: int = 1) -> int | None:
    """样本标准差（ddof=1），标度整数出入。"""
    n = len(values)
    if n < 2 or n <= ddof:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        decs = [Decimal(v) / sf for v in values]
        mean = sum(decs) / Decimal(n)
        var = sum((d - mean) ** 2 for d in decs) / Decimal(n - ddof)
        if var <= 0:
            return 0
        sd = var.sqrt()
        quantum = Decimal(1).scaleb(-scale)
        return int(sd.quantize(quantum, rounding=ROUND_HALF_UP).scaleb(scale))
