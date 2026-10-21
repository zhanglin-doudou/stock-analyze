"""API 边界 DTO（请求/响应模型）。

金额/数量/价格一律用 **定点字符串**（后端返回标度整数转换后的字符串），
字段名严格对齐 API-Catalog §2/§4。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── 认证（API-001~006）──────────────────────────────────────
class RegisterIn(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=12, max_length=128)
    display_name: str | None = Field(default=None, max_length=40)
    accept_tos: bool


class UserBrief(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: Literal["user", "admin"]
    email_verified: bool = False
    created_at: datetime


class AuthOut(BaseModel):
    """注册/登录统一返回体（API-001 201 / API-002 200）。"""

    user: UserBrief
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class LoginIn(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)
    device_name: str | None = Field(default=None, max_length=64)


class RefreshIn(StrictModel):
    refresh_token: str = Field(min_length=16, max_length=256)


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class LogoutIn(StrictModel):
    refresh_token: str | None = Field(default=None, max_length=256)


class MeOut(BaseModel):
    user: UserBrief
    settings: dict[str, Any]


# ── PAT（API-007~009）───────────────────────────────────────
class PatIn(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[Literal["read", "write"]] = Field(min_length=1)
    expires_at: datetime | None = None


class PatMeta(BaseModel):
    token_id: str
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class PatCreateResponse(BaseModel):
    """明文 `token` 仅在此响应中出现一次（API-007 断言①）。"""

    token: PatMetaWithSecret


class PatMetaWithSecret(PatMeta):
    token: str


class PatListOut(BaseModel):
    items: list[PatMeta]
    total: int


# ── 导入（API-010~016）──────────────────────────────────────
class PreviewColumn(BaseModel):
    index: int
    name: str
    sample: list[str] = Field(default_factory=list)
    suggested_field: str | None = None
    confidence: float = 0.0


class PreviewWarning(BaseModel):
    code: str
    row: int | None = None
    column: str | None = None
    message: str


class PreviewEstimated(BaseModel):
    parsed: int
    inserted: int
    skipped: int
    quarantined: int


class PreviewFile(BaseModel):
    name: str
    sha256: str
    bytes: int


class PreviewOut(BaseModel):
    preview_id: str
    file: PreviewFile
    columns: list[PreviewColumn]
    date_format: str | None = None
    warnings: list[PreviewWarning] = Field(default_factory=list)
    estimated: PreviewEstimated
    cash_balance_available: bool
    can_commit: bool
    assumptions: dict[str, Any] = Field(default_factory=dict)
    sample: list[dict[str, Any]] = Field(default_factory=list)
    expires_at: datetime


class ImportRange(StrictModel):
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ImportCommitIn(StrictModel):
    preview_id: str = Field(min_length=4, max_length=40)
    mode: Literal["full", "replace_range"] = "full"
    range: ImportRange | None = None
    mapping: dict[str, str] | None = None
    note: str | None = Field(default=None, max_length=200)


class JobLinks(BaseModel):
    self: str
    events: str


class JobAcceptedOut(BaseModel):
    job_id: str
    type: str
    batch_id: str | None = None
    status: Literal["pending"] = "pending"
    links: JobLinks


class BatchCounts(BaseModel):
    parsed: int
    inserted: int
    skipped: int
    quarantined: int


class BatchOut(BaseModel):
    batch_id: str
    status: Literal["pending", "running", "succeeded", "failed", "revoked"]
    mode: Literal["full", "replace_range"]
    broker: str
    file_name: str
    counts: BatchCounts
    # 导入收尾对账自动写入的 side=missing 补差行数（撤销后归零）。
    filled_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    finished_at: datetime | None = None
    revoked_at: datetime | None = None
    can_revoke: bool = False


class BatchFill(BaseModel):
    """系统补差行：导入对账发现账单缺少一笔资金流时自动补出的流水。"""

    trade_id: str
    trade_date: date
    line_no: int | None = None
    amount: str
    cash_balance: str | None = None
    summary: str | None = None


class BatchDetailOut(BatchOut):
    fills: list[BatchFill] = Field(default_factory=list)


class BatchPage(BaseModel):
    items: list[BatchOut]
    next_cursor: str | None = None
    total_estimate: int | None = None


class QuarantineRaw(BaseModel):
    date: str | None = None
    security_code: str | None = None
    summary: str | None = None
    quantity: str | None = None
    price: str | None = None
    amount: str | None = None
    cash_balance: str | None = None


class QuarantineItem(BaseModel):
    line_no: int | None = None
    raw: QuarantineRaw
    reason_code: str
    suggested_rule_id: str | None = None
    resolved: bool = False
    created_at: datetime | None = None


class QuarantinePage(BaseModel):
    items: list[QuarantineItem]
    next_cursor: str | None = None
    total_estimate: int | None = None


class RevokeIn(StrictModel):
    reason: str | None = Field(default=None, max_length=200)


class AffectedMetric(BaseModel):
    metric_id: str
    status: Literal["stale", "blocked", "unreliable", "ok"]


class RevokeOut(BaseModel):
    batch_id: str
    status: Literal["revoked"] = "revoked"
    reverted: dict[str, int]
    revoked_at: datetime
    affected_metrics: list[AffectedMetric] = Field(default_factory=list)


# ── 任务（API-061~064）──────────────────────────────────────
class JobOut(BaseModel):
    job_id: str
    type: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    progress: int
    stage: str
    message: str | None = None
    result_ref: str | None = None
    error: dict[str, Any] | None = None
    attempt: int
    max_attempts: int
    available_at: datetime
    heartbeat_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: str | None = None
    backlog: dict[str, int] = Field(default_factory=dict)


# ── 流水（API-017~021、080）─────────────────────────────────
class TradeOut(BaseModel):
    trade_id: str
    trade_date: date
    security_code: str
    security_name: str
    side: Literal["buy", "sell", "unknown", "missing"]
    quantity: str
    price: str
    amount: str
    commission: str
    stamp_tax: str
    other_fee: str
    cash_balance: str | None = None
    cash_balance_available: bool
    account_masked: str | None = None
    summary: str | None = None
    summary_code: str | None = None
    category: str | None = None
    dedupe_key: str
    version: int
    source: Literal["import", "manual", "system"] = "import"
    batch_id: str | None = None
    note: str | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    provenance: dict[str, Any] | None = None


class TradePage(BaseModel):
    items: list[TradeOut]
    next_cursor: str | None = None
    has_more: bool = False


class TradePatchIn(StrictModel):
    version: int | None = Field(default=None, ge=1)
    summary_code: str | None = Field(default=None, max_length=16)
    trade_date: date | None = None
    quantity: str | None = None
    price: str | None = None
    amount: str | None = None
    commission: str | None = None
    stamp_tax: str | None = None
    other_fee: str | None = None
    cash_balance: str | None = None
    note: str | None = Field(default=None, max_length=200)


class TradeMutationOut(BaseModel):
    trade: TradeOut
    affected_metrics: list[AffectedMetric] = Field(default_factory=list)


class TradeRestoreOut(BaseModel):
    trade: TradeOut
    recompute_job_id: str | None = None
    status: Literal["restored"] = "restored"


class TradeHistoryItem(BaseModel):
    version: int
    op: Literal["create", "update", "delete", "restore"]
    changed_fields: list[str] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    actor_id: uuid.UUID | None = None
    at: datetime


class TradeHistoryOut(BaseModel):
    trade_id: str
    items: list[TradeHistoryItem]


# ── 摘要规则与对账（API-022~026）────────────────────────────
class SummaryRuleIn(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=24)
    side: Literal["buy", "sell", "both"] = "both"
    match_type: Literal["exact", "prefix", "contains", "regex"] = "exact"
    pattern: str = Field(min_length=1, max_length=128)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class SummaryRuleOut(BaseModel):
    rule_id: str
    code: str
    name: str
    category: str
    side: str
    match_type: str
    pattern: str
    priority: int
    enabled: bool
    is_system: bool
    version: int
    created_at: datetime


class SummaryRulePage(BaseModel):
    items: list[SummaryRuleOut]
    total: int


class ReconciliationBreak(BaseModel):
    trade_id: str
    line_no: int | None = None
    trade_date: date | None = None
    expected: str
    actual: str
    diff: str
    # 断链是否已由系统补差行（side=missing）填平；filled_trade_id 指向补差流水。
    filled: bool = False
    filled_trade_id: str | None = None


class ReconciliationOut(BaseModel):
    period: str
    status: Literal["ok", "degraded", "blocked"]
    break_count: int
    breaks: list[ReconciliationBreak] = Field(default_factory=list)
    checked_rows: int
    # 本次对账自动插入的补差流水条数（幂等：链闭合后再次调用为 0）。
    filled_count: int = 0
    epsilon: str = "0.01"
    data_as_of: datetime
    generated_at: datetime


# ── 运维（API-071~076）──────────────────────────────────────
class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class VersionOut(BaseModel):
    app_version: str
    api_version: str
    schema_version: str
    api_snapshot_version: str
    app_version_date: str


class ErrorCatalogOut(BaseModel):
    count: int
    items: list[dict[str, Any]]
