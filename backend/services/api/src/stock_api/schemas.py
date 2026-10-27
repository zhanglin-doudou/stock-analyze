"""API 边界 DTO（请求/响应模型）。

金额/数量/价格一律用 **定点字符串**（后端返回标度整数转换后的字符串），
字段名严格对齐 API-Catalog §2/§4。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_serializer


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


# ── 规则写请求 / 回合 / 桥接（API-016、022~025、027~031）──────
class SummaryRuleCreateIn(SummaryRuleIn):
    """API-023：`code` 由服务端生成（`usr_`+8hex），客户端不可指定。"""


class SummaryRulePatchIn(StrictModel):
    """API-024：部分更新；未提供字段不动（PATCH 语义）。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    category: str | None = Field(default=None, min_length=1, max_length=24)
    side: Literal["buy", "sell", "both"] | None = None
    match_type: Literal["exact", "prefix", "contains", "regex"] | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int | None = Field(default=None, ge=0, le=1000)
    enabled: bool | None = None


class ReclassifyIn(StrictModel):
    """API-016：body 可空 `{}`；reason 仅用于审计备注。"""

    reason: str | None = Field(default=None, max_length=200)


class RebuildRoundsIn(StrictModel):
    """API-028：重建范围（full 全量 / period 脏窗口）。"""

    scope: Literal["full", "period"] = "full"
    period: str | None = None
    reason: str | None = Field(default=None, max_length=200)


class RebuildPeriodIn(StrictModel):
    """API-083：按月一键构建编排（rounds → assets → nav → metrics）。"""

    period: str = Field(min_length=7, max_length=7)
    include_exploratory: bool = False


class RoundOut(BaseModel):
    """API-029/030：回合（金额/数量/比率全部定点字符串）。"""

    round_id: str
    security_code: str
    security_name: str
    status: Literal["open", "closed", "data_error"]
    open_date: date
    close_date: date | None = None
    quantity: str
    cost: str
    proceeds: str
    dividend: str
    pnl: str
    return_rate: str
    holding_days: int
    open_trade_id: str
    close_trade_id: str | None = None
    data_error: bool
    oversell_quantity: str
    fills_count: int
    rebuilt_at: datetime


class RoundPage(BaseModel):
    items: list[RoundOut]
    next_cursor: str | None = None
    has_more: bool = False


class RoundFillOut(BaseModel):
    """API-031：FIFO 配对明细（一回合内 sequence 从 1 开始）。

    仅展示流水配对本身（买卖流水 ID、数量、日期），
    分摊收益/成本/股息/pnl 请在回合级（Round）查看。
    """

    fill_id: int
    sequence: int
    buy_trade_id: str
    sell_trade_id: str
    quantity: str
    open_date: date
    close_date: date
    holding_days: int


class RoundFillPage(BaseModel):
    items: list[RoundFillOut]
    total: int


class BridgeIdentityOut(BaseModel):
    """API-027：桥接恒等式各项（金额定点字符串，2 位小数）。"""

    initial_cash: str
    initial_open_cost: str
    cashflows: str
    realized_pnl: str
    dividends: str
    interest: str
    fees: str
    other: str
    open_cost: str
    ending_value: str
    residual: str


class ReconciliationBridgeOut(BaseModel):
    period: str
    balanced: bool
    identity: BridgeIdentityOut
    tolerance: str
    status: str
    data_as_of: datetime
    inputs_hash: str


# ── 净值与收益（API-032）──────────────────────────────────
class NavHoldingOut(BaseModel):
    """单只标的在某日的持仓快照（数量/价格/金额为最多三位小数字符串，去掉尾零）。"""

    security_code: str
    security_name: str
    quantity: str  # 持有份额（最多三位小数，如 "9.1"）
    # 含费买入均价 = 累计成本 ÷ 持仓数量；成本为净现金流出，已含佣金/印花税/其他费。
    avg_cost: str  # 含费买入均价（最多三位小数，去尾零）
    close_price: str  # 当日或之前最近收盘价（最多三位小数）
    market_value: str  # 持仓市值（最多三位小数）


class NavPointOut(BaseModel):
    """单日净值点（金额/净值/收益率均为定点字符串）。"""

    date: date
    unit_nav: str  # 单位净值（scale=6 → 后端返回完整精度）
    twr_daily: str  # 当日收益率（scale=8 → 后端返回完整精度）
    twr_daily_pct: str = "0.00%"  # 当日收益率百分比（两位小数，如 "0.62%"）
    cashflow: str  # 当日外部现金流（scale=2 → 后端返回完整精度）
    blocked: bool = False
    # blocked 日的可读原因（含失败类别）；非 blocked 日为 null。
    blocked_reason: str | None = None
    # 当日总资产（现金 + 持仓市值），三位小数字符串；blocked 日为 "0.000"。
    total_assets: str = "0.000"
    # 上一净值点的总资产（三位小数）；首个点取上一交易日收盘估值，
    # 缺行情或前一点 blocked 时为 null。
    prev_total_assets: str | None = None
    # 当日收益额 = 当日总资产 − 当日外部现金流 − 上一净值点总资产（三位小数）；
    # 无上一净值点时为 null。
    daily_pnl: str | None = None
    # 当日持仓明细（非 blocked 日填充，blocked 日为空列表）。
    holdings: list[NavHoldingOut] = []


class NavSummaryOut(BaseModel):
    """区间汇总指标（本期不含基准对比，beta/alpha/ir 等为 null）。"""

    mwr: str  # 资金加权收益率（XIRR，scale=8）
    twr: str  # 时间加权收益率（累计，scale=8）
    annualized_twr: str  # 年化 TWR（scale=8）
    mwr_minus_twr: str  # MWR − TWR（scale=8）
    beta: str | None = None
    alpha_annual: str | None = None
    t_value: str | None = None
    tracking_error: str | None = None
    ir: str | None = None
    n_days: int
    # 期初现金锚点来源：statement（区间前余额行直取）| back_solved（区间首条余额行反推）；
    # blocked 时为 null。
    assumption: Literal["statement", "back_solved"] | None = None


class NavOut(BaseModel):
    period: str
    points: list[NavPointOut]
    summary: NavSummaryOut
    status: Literal["ok", "blocked", "degraded"]
    blocked_ranges: list[dict[str, str]] = []
    benchmark_id: str = "hs300_tr"
    data_as_of: datetime
    definition_version: str = "1.0"


# ── 资产表（API-081/082，Period2-16）─────────────────────────
class AssetPointOut(StrictModel):
    """每日资产快照行（金额 2 位定点字符串；blocked 行数值字段**显式 null**）。"""

    date: date
    # blocked 行以下金额/比率字段一律 null（不是 "0.00"、也不是省略键）。
    cash: str | None = None
    market_value: str | None = None
    total_assets: str | None = None
    holding_cost: str | None = None
    unrealized_pnl: str | None = None
    # blocked 行强制 0（硬阻断不输出持仓明细）。
    holding_count: int = 0
    cashflow: str | None = None
    prev_total_assets: str | None = None
    daily_pnl: str | None = None
    daily_return: str | None = None
    # 后端算好的两位百分比串（如 "0.62%"），前端零口径。
    daily_return_pct: str | None = None
    status: Literal["ok", "blocked", "degraded"]
    status_reason: str | None = None


class AssetBuildStateOut(StrictModel):
    """资产表构建摘要（`GET /assets` 的 `build` 块）。"""

    state: Literal["missing", "current", "stale", "blocked"]
    last_status: Literal["ok", "degraded", "blocked"] | None = None
    last_built_at: datetime | None = None
    last_built_through: date | None = None
    last_mode: Literal["full", "incremental", "noop"] | None = None
    last_rebuild_from: date | None = None
    last_rows_written: int = 0
    rows_count: int = 0
    assumption: Literal["back_solved"] | None = None
    status_reason: str | None = None
    has_new_market_data: bool = False
    definition_version: str


class AssetPageOut(StrictModel):
    """API-081：资产表分页（游标 + 构建摘要 + 口径血缘）。"""

    items: list[AssetPointOut]
    next_cursor: str | None = None
    has_more: bool = False
    build: AssetBuildStateOut
    definition_version: str
    data_as_of: date | None = None


class AssetBuildIn(StrictModel):
    """API-082：构建模式（`auto` 增量/自动判定全量；`full` 强制全量）。"""

    mode: Literal["auto", "full"] = "auto"


class AssetBuildAccepted(StrictModel):
    """API-082：202 专用响应（不复用 `JobAcceptedOut`，避免多出 `batch_id:null`）。"""

    job_id: str
    type: Literal["build_assets"] = "build_assets"
    status: Literal["pending"] = "pending"
    links: JobLinks


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


# ── 指标（API-033/034/035/079，Period2-06）────────────────────


class MetricScopeOut(BaseModel):
    unit: str
    cohort: str
    period: str


class MetricItemOut(BaseModel):
    """单个指标结果；`value` 在非 ok / pending_window 时**整键省略**（C-31）。"""

    metric_id: str
    definition_version: str
    domain: str
    scope: MetricScopeOut
    period: str
    status: Literal["ok", "blocked", "stale", "unreliable"]
    sample_status: Literal["ok", "insufficient", "pending_window", "blocked"]
    reason: str | None = None
    value: str | None = None
    unit: Literal["CNY", "ratio", "days", "count", "z"]
    precision: int
    n: int
    n_pending: int
    ci_low: str | None = None
    ci_high: str | None = None
    decomposition: dict[str, Any] | None = None
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_as_of: date
    inputs_hash: str
    computed_at: datetime

    @model_serializer(mode="wrap")
    def _omit_empty_value(self, handler: Any) -> dict[str, Any]:
        payload: dict[str, Any] = handler(self)
        if self.status != "ok" or self.sample_status == "pending_window":
            payload.pop("value", None)
        return payload


class MetricBatchOut(BaseModel):
    period: str
    data_as_of: date
    definition_version: str
    assumptions: dict[str, Any] = Field(default_factory=dict)
    items: list[MetricItemOut] = Field(default_factory=list)


class MetricExplainDrilldown(BaseModel):
    endpoint: str
    params: dict[str, str] = Field(default_factory=dict)
    description: str


class MetricExplainOut(BaseModel):
    metric_id: str
    definition_version: str
    period: str
    conclusion: str | None = None
    method: str
    drilldown: MetricExplainDrilldown
    status: Literal["ok", "blocked", "stale", "unreliable"]
    reason: str | None = None
    what_to_do: str | None = None
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_as_of: date


class MetricDrilldownItemOut(BaseModel):
    key: str
    value: str | None = None
    unit: Literal["CNY", "ratio", "days", "count", "z"]
    label: str
    round_id: str | None = None
    trade_id: str | None = None
    event_date: date | None = None
    security_code: str | None = None


class MetricDrilldownOut(BaseModel):
    metric_id: str
    definition_version: str
    period: str
    status: Literal["ok", "blocked", "stale", "unreliable"]
    reason: str | None = None
    items: list[MetricDrilldownItemOut] = Field(default_factory=list)
    total: int = 0
    source_endpoints: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    next_cursor: str | None = None
    truncated: bool = False
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_as_of: date | None = None
