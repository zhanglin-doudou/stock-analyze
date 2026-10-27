"""选股域指标（Period2-07）：BHAR20 / BHAR60 / BHAR_hit。

- BHAR_t = mean(Σ_{τ=1}^{t} (r_{i,τ} − r_{m,τ}))   事件日后 t 日累计超额收益
- BHAR_hit = #(BHAR_{i,20} > threshold) / N_events

调用方负责传入已经计算好的逐事件 BHAR 值（本模块只做聚合）。
聚类 t 值复用 stats.tstat.cluster_t。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

from stock_contracts.constants import SCALE_RATIO

from stock_core.metrics import policy
from stock_core.stats.numeric import div_round
from stock_core.stats.tstat import cluster_t


def bhar_mean(
    bhar_units: list[int],
    *,
    scale: int = SCALE_RATIO,
) -> int | None:
    """事件日 BHAR 均值（聚合层纯函数）。"""
    if not bhar_units:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        sf = Decimal(10) ** scale
        mean_decimal = sum(Decimal(b) for b in bhar_units) / Decimal(len(bhar_units)) / sf
        return int((mean_decimal * sf).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def bhar_hit_rate(
    bhar_units: list[int],
    *,
    threshold_units: int = policy.BHAR_HIT_THRESHOLD_SCALE8,
    scale: int = SCALE_RATIO,
) -> int | None:
    """BHAR 命中率 = #(BHAR > threshold) / N。

    默认 threshold = 0（超额收益非负即命中）。
    """
    if not bhar_units:
        return None
    count = sum(1 for b in bhar_units if b > threshold_units)
    return div_round(count, len(bhar_units), scale=scale)


def bhar_cluster_t(
    events: Sequence[tuple[int, int]],
    *,
    scale: int = SCALE_RATIO,
    t_scale: int = 4,
) -> int | None:
    """事件日聚类 t 值（直接复用 stats.tstat.cluster_t）。

    Args:
        events: [(event_day_index, bhar_units), ...]
    """
    return cluster_t(list(events), scale=scale, t_scale=t_scale)
