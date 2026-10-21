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
    DIAGNOSTICS_SELECTION = "diagnostics_selection"
    REPORT_GENERATE = "report_generate"
    EXPORT_CREATE = "export_create"
    BACKUP = "backup"
    RESTORE_DRILL = "restore_drill"


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


class WarningCode(StrEnum):
    MISSING_CASH_BALANCE = "MISSING_CASH_BALANCE"
    FIRST_ROW_NOT_BUY = "FIRST_ROW_NOT_BUY"
    UNKNOWN_SUMMARY = "UNKNOWN_SUMMARY"
    DUPLICATE_ROWS = "DUPLICATE_ROWS"
    MISSING_REQUIRED_COLUMN = "MISSING_REQUIRED_COLUMN"
    DATE_FORMAT_AMBIGUOUS = "DATE_FORMAT_AMBIGUOUS"
