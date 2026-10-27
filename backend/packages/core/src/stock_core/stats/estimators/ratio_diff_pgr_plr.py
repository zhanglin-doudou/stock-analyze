"""统计量 `ratio_diff_pgr_plr`：处置效应 DE = PGR − PLR 的 bootstrap 统计量（§7.3.1，D-20）。

观测编码（单元素元组）：0=已卖盈、1=已卖亏、2=纸面盈、3=纸面亏。
每个 replicate 重算 `PGR=G/(G+g)`、`PLR=L/(L+l)`、`DE=PGR−PLR`（与 def 同款一次舍入：
`DE=HALF_UP((G(L+l)−L(G+g))×1e8/((G+g)(L+l)))`）。

退化 replicate（`G+g=0` 或 `L+l=0`，分母为零）返回 0 并计入经验分布——`bootstrap_percentile`
无无效 replicate 丢弃机制，该近似已在 07 报告中披露（B_eff 不分流）。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_core.stats.estimators import register
from stock_core.stats.numeric import div_round


@register("ratio_diff_pgr_plr")
def ratio_diff_pgr_plr_stat(samples: Sequence[tuple[int, ...]]) -> int:
    """从 0/1/2/3 编码观测重算 DE（标度整数，scale=8）。"""
    if not samples:
        raise ValueError("samples must not be empty")
    sold_gain = sum(1 for observation in samples if observation[0] == 0)
    sold_loss = sum(1 for observation in samples if observation[0] == 1)
    paper_gain = sum(1 for observation in samples if observation[0] == 2)
    paper_loss = sum(1 for observation in samples if observation[0] == 3)
    gain_total = sold_gain + paper_gain
    loss_total = sold_loss + paper_loss
    if gain_total == 0 or loss_total == 0:
        return 0
    numerator = sold_gain * loss_total - sold_loss * gain_total
    return div_round(numerator, gain_total * loss_total, scale=8)
