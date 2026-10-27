"""事件日聚类 t 值（Period2-07，selection.BHAR 用）。

公式（§5.5）：
  - 事件日聚类：每个事件属于一个事件日（trade_date），同一日多个事件形成一个 cluster
  - G = 事件日去重数
  - u_g = Σ_{i∈g} (BHAR_i − mean)    （cluster g 的和中心化偏差）
  - SS = Σ_g u_g²
  - Var(mean) = G / (G−1) · SS / n²
  - t = mean · n · sqrt((G−1) / (G · SS))

边界：
  - G < 2 → 返回 None（无法聚类）
  - G == n（每个事件独占一日）→ 等价单样本 t（§5.5 "单例聚类时 t 等于单样本 t"）
  - SS = 0（所有事件值相同）→ 返回 0（无方差）
  - n == 0 → None

输入约定：events = [(event_day_index, bhar_units), ...]
  event_day_index: 事件所属的"基准日序号"（0-based int，如 0..N_BENCH-1）
  bhar_units: BHAR 标度整数（scale=8）
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import SCALE_RATIO


def cluster_t(
    events: Sequence[tuple[int, int]],
    *,
    scale: int = SCALE_RATIO,
    t_scale: int = 4,
) -> int | None:
    """事件日聚类 t 值；返回标度整数（t_scale=4，即 1e-4）。"""
    n = len(events)
    if n == 0:
        return None

    scale_factor = Decimal(10) ** scale

    with localcontext() as ctx:
        ctx.prec = 40
        # 均值
        total = sum(Decimal(bhar_u) / scale_factor for _, bhar_u in events)
        mean = total / Decimal(n)

        # 按 event_day_index 分 cluster
        clusters: dict[int, list[Decimal]] = {}
        for day_idx, bhar_u in events:
            clusters.setdefault(day_idx, []).append(Decimal(bhar_u) / scale_factor - mean)

        g = len(clusters)
        if g < 2:
            return None

        ss = sum(sum(u_list) ** 2 for u_list in clusters.values())
        if ss == 0:
            return 0  # 无方差，t 值 0

        # t = mean · n · sqrt((G−1) / (G · SS))
        t_dec = mean * Decimal(n) * (Decimal(g - 1) / (Decimal(g) * ss)).sqrt()

        t_quantum = Decimal(1).scaleb(-t_scale)
        return int(t_dec.quantize(t_quantum, rounding=ROUND_HALF_UP).scaleb(t_scale))
