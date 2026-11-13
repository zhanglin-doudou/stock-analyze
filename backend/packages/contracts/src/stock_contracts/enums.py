"""跨上下文共享枚举。`code` 一经发布不得改名或复用。"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"


class TokenScope(StrEnum):
    READ = "read"
    WRITE = "write"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"
    # 对账自动补差行：代表余额链里缺失的一笔资金流，由 reconcile 生成，非账单原始行。
    MISSING = "missing"


class JobType(StrEnum):
    IMPORT_COMMIT = "import_commit"
    IMPORT_REVOKE = "import_revoke"
    IMPORT_RECLASSIFY = "reclassify"
    REBUILD_ROUNDS = "rebuild_rounds"
    REBUILD_NAV = "rebuild_nav"
    # Period2-16 资产表（每日资产快照）构建/增量更新任务。
    BUILD_ASSETS = "build_assets"
    DIAGNOSTICS_SELECTION = "diagnostics_selection"
    REPORT_GENERATE = "report_generate"
    EXPORT_CREATE = "export_create"
    BACKUP = "backup"
    RESTORE_DRILL = "restore_drill"
    # Period2-09（设计 2026-11-04）：回合洞察前瞻收益批量重算。
    REFRESH_ROUND_INSIGHTS = "refresh_round_insights"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class JobStage(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    WRITING = "writing"
    RECOMPUTING = "recomputing"
    RENDERING = "rendering"
    FINALIZING = "finalizing"


class MetricStatus(StrEnum):
    OK = "ok"
    STALE = "stale"
    BLOCKED = "blocked"
    UNRELIABLE = "unreliable"


class SampleStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT = "insufficient"
    PENDING_WINDOW = "pending_window"
    BLOCKED = "blocked"


class ScopeType(StrEnum):
    GLOBAL = "global"
    PERIOD = "period"
    ROUND = "round"
    SECURITY = "security"


class ImportBatchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REVOKED = "revoked"


class TradeSource(StrEnum):
    IMPORT = "import"
    MANUAL = "manual"
    # 系统自愈写入（对账补差行），非用户导入/手工录入。
    SYSTEM = "system"


class ReconciliationStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class QuarantineReason(StrEnum):
    UNKNOWN_SUMMARY = "UNKNOWN_SUMMARY"
    UNMAPPED_COLUMN = "UNMAPPED_COLUMN"
    INVALID_ROW = "INVALID_ROW"
    DUPLICATE_IN_FILE = "DUPLICATE_IN_FILE"
    # 金额字段全为 0（佣金/印花税/其他费/发生金额/资金余额）：无资金流的噪音行。
    INVALID_AMOUNT = "INVALID_AMOUNT"
    # 申购中签/新股入账/托管转出、[省心投]红利发放（省心投账户内部份额通知，与主账户
    # 资金无关）等纯通知行：无资金意义，不进明细（可审计可恢复）。
    ZERO_AMOUNT_INFO = "ZERO_AMOUNT_INFO"


class WarningCode(StrEnum):
    MISSING_CASH_BALANCE = "MISSING_CASH_BALANCE"
    FIRST_ROW_NOT_BUY = "FIRST_ROW_NOT_BUY"
    UNKNOWN_SUMMARY = "UNKNOWN_SUMMARY"
    DUPLICATE_ROWS = "DUPLICATE_ROWS"
    MISSING_REQUIRED_COLUMN = "MISSING_REQUIRED_COLUMN"
    DATE_FORMAT_AMBIGUOUS = "DATE_FORMAT_AMBIGUOUS"
    # 导入收尾对账发现余额链缺口，已自动写入 side=missing 补差行：提示账单存在缺失。
    RECONCILIATION_AUTOFILLED = "RECONCILIATION_AUTOFILLED"
    # 可转债「数量(手) × 10 × 单张价」与发生金额偏差超容差：口径异常留痕（不阻断）。
    CBOND_AMOUNT_INCONSISTENT = "CBOND_AMOUNT_INCONSISTENT"


# ── 回合洞察（Period2-09，设计 2026-11-04）────────────────────────


class TradeReasonCode(StrEnum):
    """买卖理由码；买入/卖出集合分别由 BUY_REASON_CODES / SELL_REASON_CODES 校验。"""

    PLAN_BUILD = "plan_build"
    ADD_ON = "add_on"
    BOTTOM_FISH = "bottom_fish"
    CHASE = "chase"
    REBOUND = "rebound"
    DCA = "dca"
    NEWS = "news"
    EMOTION = "emotion"
    PLAN_REDUCE = "plan_reduce"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    BREAK_EVEN = "break_even"
    REBALANCE = "rebalance"
    FEAR = "fear"
    OTHER = "other"


# 理由码与买卖方向绑定（同一 code 不允许同时出现在两侧）。
BUY_REASON_CODES: frozenset[str] = frozenset(
    {
        TradeReasonCode.PLAN_BUILD,
        TradeReasonCode.ADD_ON,
        TradeReasonCode.BOTTOM_FISH,
        TradeReasonCode.CHASE,
        TradeReasonCode.REBOUND,
        TradeReasonCode.DCA,
        TradeReasonCode.NEWS,
        TradeReasonCode.EMOTION,
        TradeReasonCode.OTHER,
    }
)
SELL_REASON_CODES: frozenset[str] = frozenset(
    {
        TradeReasonCode.PLAN_REDUCE,
        TradeReasonCode.TAKE_PROFIT,
        TradeReasonCode.STOP_LOSS,
        TradeReasonCode.BREAK_EVEN,
        TradeReasonCode.REBALANCE,
        TradeReasonCode.FEAR,
        TradeReasonCode.NEWS,
        TradeReasonCode.OTHER,
    }
)


class EmotionCode(StrEnum):
    CALM = "calm"
    ANXIOUS = "anxious"
    GREEDY = "greedy"
    FEAR = "fear"
    IMPULSIVE = "impulsive"
    OTHER = "other"


class ForwardReturnStatus(StrEnum):
    OK = "ok"
    PENDING = "pending"
    NO_CALENDAR = "no_calendar"
    NO_BAR = "no_bar"
    STALE_PRICE = "stale_price"
    CORPORATE_ACTION = "corporate_action"
    DIVISOR_MISSING = "divisor_missing"


class RoundBucket(StrEnum):
    SCALP = "scalp"
    SHORT = "short"
    POSITION = "position"
    LONG = "long"
    UNKNOWN = "unknown"


class InsightQualityFlag(StrEnum):
    """洞察行质量标记（响应字段，不改变 HTTP 状态）。"""

    PRICE_JUMP_REVIEW = "price_jump_review"
    COST_BASIS_WARNING = "cost_basis_warning"
    NO_CALENDAR = "no_calendar"
    NO_BAR = "no_bar"
    PENDING = "pending"
    STALE_PRICE = "stale_price"
    CORPORATE_ACTION = "corporate_action"
    DIVISOR_MISSING = "divisor_missing"
    ADDON_SEQUENCE = "addon_sequence"
    RISING_SELL_SEQUENCE = "rising_sell_sequence"
