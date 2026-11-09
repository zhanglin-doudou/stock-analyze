"""账单（trade）级旗标常量（设计：2026-11-09 账单旗标）。

所有阈值集中在此，计算层禁止散落魔法数。比例统一用 **基点（bps）** 表示，
比较时先放大到整数域（`value * BPS_DENOMINATOR`），不引入 float。
"""

from __future__ import annotations

# 词表/判定口径版本：随判定逻辑变化递增，参与幂等唯一键。
# 1.1（2026-11-09）：新增 `first_entry` 与三个清仓状态（`full_exit_*`）。
DEFINITION_VERSION = "1.1"

# 比例分母（1 bps = 1/10000）。
BPS_DENOMINATOR = 10_000

# 近似相等的容差：以「持仓成本均价」为基准上下 1%。
NEAR_EQUAL_TOLERANCE_BPS = 100

# 追涨：净买价高于前 N 个行情日最高价的 1%。
CHASE_MARGIN_BPS = 100

# 止损：净卖价低于前 N 个行情日最低价的 1%。
STOP_LOSS_MARGIN_BPS = 100

# 即时抄底 / 即时逃顶：距前 N 个行情日极值 5% 以内。
INSTANT_MARGIN_BPS = 500

# 短期成功 / 短期逃顶：距前后各 N 个行情日极值 5% 以内（含未来行情）。
SHORT_TERM_MARGIN_BPS = 500

# 行情日窗口（不含当日）：前后各 5 个行情日。
WINDOW_TRADING_DAYS = 5

# ── 评估状态 ───────────────────────────────────────────────
STATUS_HIT = "hit"
STATUS_MISS = "miss"
STATUS_PENDING = "pending"
STATUS_NO_BAR = "no_bar"
STATUS_NO_CALENDAR = "no_calendar"
STATUS_INSUFFICIENT_HISTORY = "insufficient_history"
STATUS_NO_COST_BASIS = "no_cost_basis"
STATUS_NOT_APPLICABLE = "not_applicable"

# 事后指标标记文案（由后端单点维护，前端不写副本）。
LOOKAHEAD_LABEL_ZH = "事后指标（含未来行情）"

STATUS_LABELS: dict[str, str] = {
    STATUS_HIT: "命中",
    STATUS_MISS: "未命中",
    STATUS_PENDING: "待算（未来行情未满）",
    STATUS_NO_BAR: "无行情",
    STATUS_NO_CALENDAR: "无交易日历",
    STATUS_INSUFFICIENT_HISTORY: "行情不足/停牌/上市不足",
    STATUS_NO_COST_BASIS: "无持仓成本",
    STATUS_NOT_APPLICABLE: "不适用",
}

# 详情页只透出这些状态（`miss`/`not_applicable` 不展示，`hit` 以旗标呈现）。
DISPLAY_STATUSES: tuple[str, ...] = (
    STATUS_HIT,
    STATUS_PENDING,
    STATUS_NO_BAR,
    STATUS_NO_CALENDAR,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_NO_COST_BASIS,
)
