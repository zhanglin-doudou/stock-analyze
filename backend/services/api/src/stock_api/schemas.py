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
class TradeReasonOut(BaseModel):
    """逐笔理由（API-090/091；`/trades` 内嵌同一结构）。"""

    trade_id: str
    side: str
    reason_code: str
    reason_label: str
    reason_text: str | None = None
    emotion_code: str | None = None
    emotion_label: str | None = None
    tags: list[str] = Field(default_factory=list)
    annotated_at: datetime
    late_annotated: bool
    version: int


class TradeReasonIn(StrictModel):
    """PUT 理由入参；`reason_text` 上限 500、tags<=8。"""

    reason_code: str = Field(min_length=1, max_length=24)
    reason_text: str | None = Field(default=None, max_length=500)
    emotion_code: str | None = Field(default=None, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=8)


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
    # 货币基金余额（该行发生后该标的的份额余额）；non-null 即货基行。
    money_fund_balance: str | None = None
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
    # additive：逐笔理由（未标注为 null）；详情 ETag 同时受 reason.version 影响。
    reason: TradeReasonOut | None = None


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
    money_fund_balance: str | None = None
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
    """API-083：一键构建编排（rounds → assets → nav → metrics）。

    `period` 支持三种 token：`YYYY-MM`（月）/ `YYYY`（年）/ `YYYY-MM-DD..YYYY-MM-DD`（自定义区间）。
    """

    period: str = Field(min_length=4, max_length=23)
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
    # 货币基金余额（单位净值恒 1.0000；不再作为持仓估值）
    money_fund: str | None = None
    # 资产余额 = 现金 + 货币基金（用户口径：页面「余额」统一显示资产余额）
    balance: str | None = None
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
    remark: str | None = None


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
    # 当日交易时段提示：market_open=工作日未收盘（资产表不含当日数据）；
    # sync_pending=工作日已收盘但行情未同步到当日（提示 make sync-market）；
    # 非工作日或无需提示时为 null。只读展示，不参与 stale 判定。
    session_notice: Literal["market_open", "sync_pending"] | None = None


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


class AssetBuildMissingOut(StrictModel):
    """API-082：缺行情提示项（字段名沿用归因/看板的 `missing[]` 风格）。"""

    field: str = "market_data"
    code: Literal["MARKET_DATA_MISSING"] = "MARKET_DATA_MISSING"
    reason: str
    what_to_do: str


class AssetBuildFailed(StrictModel):
    """API-082：200 专用响应——本次构建未执行（缺少行情数据），无 job 产生。

    `status="failed"` 沿用 job 状态语义（不是网络/权限错误），`job_id=null` 表示未入队。
    """

    job_id: None = None
    type: Literal["build_assets"] = "build_assets"
    status: Literal["failed"] = "failed"
    message: str
    missing: list[AssetBuildMissingOut]


class AssetChartPointOut(StrictModel):
    """API-085：图表点（v 为后端归一化无量纲字符串，前端只做几何投影）。

    cashflow 为当日银证转账净额（定点字符串，正数=净转入、负数=净转出）；
    cashflow_direction 是后端判定的展示方向，前端不解析符号、不做金额运算。
    """

    date: date
    # 归一化 [0,1]，4 位定点字符串（如 "0.4000"）；不是金额/业务比率。
    v: str
    label: str
    amount_label: str
    # 当日净银证转账（分→元定点字符串）；无转账为 "0.00"。
    cashflow: str
    cashflow_direction: Literal["in", "out", "none"]


class AssetChartOut(StrictModel):
    """API-085：资产日线 chart-ready 视图（只读；前端零解析）。"""

    points: list[AssetChartPointOut]
    n_points: int
    build_state: Literal["missing", "current", "stale", "blocked"]
    definition_version: str
    data_as_of: date | None = None


# ── Dashboard（API-084，Period2-12）─────────────────────────
DashboardStatus = Literal["ok", "degraded", "stale", "blocked", "missing", "insufficient"]
DashboardMissingCode = Literal[
    "DASHBOARD_ASSET_MISSING",
    "DASHBOARD_ASSET_BLOCKED",
    "DASHBOARD_STALE",
    "DASHBOARD_TOTAL_PROFIT_UNAVAILABLE",
    "DASHBOARD_MAX_INVESTED_COST_MISSING",
    "DASHBOARD_MDD_INSUFFICIENT",
    "DASHBOARD_MDD_UNRELIABLE",
    "DASHBOARD_MDD_GAPS",
    "DASHBOARD_HOLDINGS_TRUNCATED",
    "DASHBOARD_HOLDING_PRICE_MISSING",
    "DASHBOARD_HOLDINGS_MISMATCH",
    "DASHBOARD_NO_CLOSED_ROUNDS",
    "DASHBOARD_BILL_RANGE_MISSING",
    "DASHBOARD_INITIAL_CAPITAL_MISSING",
    "DASHBOARD_ANNUALIZED_RETURN_UNAVAILABLE",
]


class DashboardMissingOut(StrictModel):
    """一个缺失/降级字段（字段为 null 的机读原因 + 可执行指引）。"""

    field: str
    code: DashboardMissingCode
    reason: str
    what_to_do: str


class DashboardHoldingOut(StrictModel):
    """当前持仓行（证券维度；金额 2 位、数量 4 位、权重 scale=8）。"""

    security_code: str
    security_name: str
    quantity: str
    cost: str
    market_value: str | None = None
    weight_pct: str | None = None
    # 后端已格式化的展示串（千分位 / 百分比）；前端优先使用，保留 raw 兼容旧客户端。
    cost_display: str | None = None
    market_value_display: str | None = None
    weight_display: str | None = None


class DashboardRoundOut(StrictModel):
    """最赚/最亏列表行（pnl 2 位、收益率 scale=8）。"""

    round_id: str
    security_code: str
    security_name: str
    close_date: date
    pnl: str
    return_rate: str
    return_rate_pct: str
    holding_days: int
    # 千分位展示串（raw pnl 保留；前端优先 display）。
    pnl_display: str | None = None


class DashboardTradeCountOut(StrictModel):
    """交易次数 Top3 的一行（流水 buy/sell 行数 + 个股累计收益/总费用）。"""

    security_code: str
    security_name: str
    buy_count: int
    sell_count: int
    trade_count: int
    pnl: str | None = None
    pnl_display: str | None = None
    total_fees: str
    total_fees_display: str | None = None


class DashboardMaxDrawdownOut(StrictModel):
    """历史最大回撤区间；insufficient/blocked 时 mdd_pct 显式 null。"""

    from_date: date | None = None
    to_date: date | None = None
    duration_days: int | None = None
    duration_obs: int | None = None
    recovery_days: int | None = None
    recovery_censored: bool = False
    mdd_pct: str | None = None
    n_obs: int
    sample_status: Literal["ok", "insufficient", "unreliable", "blocked"]
    reason: str | None = None


class DashboardAssetOverviewOut(StrictModel):
    """资产概览：当前快照（最新资产日）+ 峰值转入成本/总收益/费用。

    `max_invested_cost` 语义为**银证转账峰值转入成本**（累计净转入的峰值），
    `max_cost_return_pct` = 总收益 / 峰值转入成本（字段名保留以兼容旧客户端）。
    """

    status: DashboardStatus
    holdings: list[DashboardHoldingOut]
    market_value: str | None = None
    cash: str | None = None
    # 货币基金余额 + 资产余额（= 现金 + 货币基金），页面「余额」统一用资产余额。
    money_fund: str | None = None
    balance: str | None = None
    total_assets: str | None = None
    position_pct: str | None = None
    total_profit: str | None = None
    max_invested_cost: str | None = None
    max_cost_return_pct: str | None = None
    total_fees: str | None = None
    # 账单覆盖区间（首末流水日）与桥接期初资金（期初现金 + 期初持仓成本）。
    bill_start_date: date | None = None
    bill_end_date: date | None = None
    initial_capital: str | None = None
    initial_capital_display: str | None = None
    # 全历史年化 TWR（时间加权；scale=8 小数串 + 两位百分比展示）。
    annualized_return: str | None = None
    annualized_return_pct: str | None = None
    # 后端已格式化的展示串（千分位金额）；前端优先使用，raw 保留兼容。
    market_value_display: str | None = None
    cash_display: str | None = None
    money_fund_display: str | None = None
    balance_display: str | None = None
    total_assets_display: str | None = None
    total_profit_display: str | None = None
    max_invested_cost_display: str | None = None
    total_fees_display: str | None = None


class DashboardHistoryOverviewOut(StrictModel):
    """历史概览：全历史已平仓计数/最赚最亏/持仓时长 Top3/交易次数 Top3 + 最大回撤。"""

    status: DashboardStatus
    closed_security_count: int
    closed_round_count: int
    win_count: int
    loss_count: int
    flat_count: int
    win_pct: str | None = None
    loss_pct: str | None = None
    flat_pct: str | None = None
    max_profit: str | None = None
    max_profit_display: str | None = None
    max_profit_round: DashboardRoundOut | None = None
    max_profit_cost_pct: str | None = None
    top_rounds: list[DashboardRoundOut]
    bottom_rounds: list[DashboardRoundOut]
    top_holding_rounds: list[DashboardRoundOut]
    top_trade_counts: list[DashboardTradeCountOut]
    max_drawdown: DashboardMaxDrawdownOut


class DashboardLinksOut(StrictModel):
    """下钻/占位页链接（年度指标按 `metrics_period` 懒加载）。"""

    assets: str
    rounds: str
    metrics: str
    reports: str
    exports: str
    attribution: str
    sectors: str


class DashboardOut(StrictModel):
    """API-084：Dashboard 两板块聚合（只读；金额/比率全部后端格式化）。"""

    as_of: date
    data_as_of: date | None = None
    legacy_period: str
    metrics_period: str
    metrics_period_start: date
    metrics_period_end: date
    status: DashboardStatus
    build_state: Literal["missing", "current", "stale", "blocked"]
    definition_version: str
    # 只读构建摘要（与 `GET /assets` 的 build 块同源，additive）：看板据此判定 stale
    # 并与资产页共用同一份跨页面去重指纹；不触发构建、不写库。
    build: AssetBuildStateOut
    asset_overview: DashboardAssetOverviewOut
    history_overview: DashboardHistoryOverviewOut
    missing: list[DashboardMissingOut]
    links: DashboardLinksOut
    assumptions: dict[str, str]
    inputs_hash: str


class DashboardTradeFlagSeriesOut(StrictModel):
    """看板图表的旗标系列（标签/方向/事后属性由后端提供，前端零口径）。"""

    code: str
    label_zh: str
    label_en: str
    side: Literal["buy", "sell"]
    lookahead: bool


class DashboardTradeFlagCountOut(StrictModel):
    code: str
    count: int


class DashboardMonthlyReturnOut(StrictModel):
    """单月组合收益：资产日表逐日收益（TWR）自然月复利，展示字段全部由后端给出。

    `value_bp` 为柱高（基点整数，2.35% → 235）；无可用日收益时显式 0（图上 0 高度），
    同时 `available=false`、`pct/display=null`，避免把「无数据」伪装成 0 收益。

    `amount` 为当月收益额（每日盈亏累加，**已剔除出入金**，含分红/打新等投资损益），
    与收益率同源；无数据时为 `null`。`amount_display` 为千分位展示串。
    """

    month: str
    value_bp: int
    pct: str | None
    display: str | None
    sign: Literal["pos", "neg", "flat", "none"]
    available: bool
    days: int
    partial: bool
    note: str | None
    amount: str | None
    amount_display: str | None


class DashboardTradeFlagMonthOut(StrictModel):
    """一个完整月的买卖笔数 + 折线趋势旗标 + 柱状图互斥状态旗标命中数 + 单月收益。"""

    month: str
    label: str
    buy_count: int
    sell_count: int
    flags: list[DashboardTradeFlagCountOut]
    states: list[DashboardTradeFlagCountOut]
    monthly_return: DashboardMonthlyReturnOut


class DashboardTradeFlagsOut(StrictModel):
    """API-095：账单旗标月度聚合（最近 6/12/24 个完整月）。"""

    window_months: int
    start_month: str
    end_month: str
    latest_complete_month: str
    months_basis: str
    definition_version: str
    has_data: bool
    has_flag_data: bool
    has_state_data: bool
    series: list[DashboardTradeFlagSeriesOut]
    # 柱状图口径：买卖两侧各三个互斥的加仓/减仓状态。
    state_series: list[DashboardTradeFlagSeriesOut]
    # 买卖笔数展示标签（side -> 文案），前端不再自造文案。
    total_labels: dict[str, str]
    items: list[DashboardTradeFlagMonthOut]
    # 区间内没有任何旗标记录的月份（可能未运行 build-trade-flags，或当月无交易）。
    months_without_data: list[str]
    # 单月收益（additive）：口径说明/图例文案由后端给，前端只渲染。
    has_return_data: bool
    return_basis: str
    return_labels: dict[str, str]
    # tooltip 口径标注：`return` = 收益（剔出入金），`amount` = 金额（含出入金）。
    return_metric_labels: dict[str, str]


# ── 板块（API-087/088，2026-10-31 sector-data）────────────────
SectorTaxonomy = Literal["industry", "concept"]
SectorStatus = Literal["ok", "degraded", "stale", "insufficient", "missing", "blocked"]
SectorDataState = Literal["current", "stale", "missing"]
SectorDirection = Literal["over", "under", "flat"]
SectorMissingCode = Literal[
    "SECTOR_ASSET_BUILD_MISSING",
    "SECTOR_ASSET_BUILD_BLOCKED",
    "SECTOR_ASSET_BUILD_STALE",
    "SECTOR_DATA_MISSING",
    "SECTOR_DATA_STALE",
    "SECTOR_SYNC_FAILED",
    "SECTOR_MEMBERSHIP_MISSING",
    "SECTOR_NON_STOCK_HOLDINGS",
    "SECTOR_NO_STOCK_POSITIONS",
    "SECTOR_BENCHMARK_WEIGHTS_MISSING",
    "SECTOR_BENCHMARK_WEIGHTS_STALE",
    "SECTOR_CONCEPT_MULTI_MEMBERSHIP",
    "SECTOR_CONCEPT_COVERAGE_LOW",
    "SECTOR_CONCEPT_HHI_UNAVAILABLE",
    "SECTOR_HISTORY_NOT_COVERED",
    "SECTOR_ASSET_MISMATCH",
    "SECTOR_TRUNCATED",
    "SECTOR_HOLDING_TRUNCATED",
    "SECTOR_PRICE_MISSING",
    "SECTOR_AS_OF_NOT_AVAILABLE",
]


class SectorMissingOut(StrictModel):
    """200 降级项（字段路径 + 机读码 + 人话 + 可执行指引）。"""

    field: str
    code: SectorMissingCode
    reason: str
    what_to_do: str


class SectorPortfolioOut(StrictModel):
    """持仓口径（金额 2 位、比率 scale=8；行业专属字段在概念请求下为 null）。"""

    total_assets: str | None = None
    total_assets_display: str | None = None
    holding_market_value: str | None = None
    equity_market_value: str | None = None
    mapped_market_value: str | None = None
    unclassified_market_value: str | None = None
    non_stock_market_value: str | None = None
    coverage: str | None = None
    coverage_pct: str | None = None
    coverage_basis: str | None = None
    equity_position: str | None = None
    equity_position_pct: str | None = None
    holding_count: int
    classified_holding_count: int


class SectorRowHoldingOut(StrictModel):
    """概念行下的持仓标的（多归属：同一标的会出现在多个概念行，各自计数）。"""

    symbol: str
    name: str


class SectorRowOut(StrictModel):
    """板块行：industry 用 weight/active_weight；concept 用 exposure（其余为 null）。"""

    sector_code: str
    sector_name: str
    level: int
    parent_code: str | None = None
    market_value: str | None = None
    market_value_display: str | None = None
    weight: str | None = None
    weight_pct: str | None = None
    holding_count: int
    benchmark_weight: str | None = None
    benchmark_weight_pct: str | None = None
    active_weight: str | None = None
    active_weight_pct: str | None = None
    direction: SectorDirection | None = None
    exposure: str | None = None
    exposure_pct: str | None = None
    overlap: bool | None = None
    # 概念行专属（additive）：该概念下的持仓标的明细，按市值降序；行业行为 null。
    holdings: list[SectorRowHoldingOut] | None = None


class SectorOthersOut(StrictModel):
    """limit 截断后的「其他板块」聚合（industry 带权重；concept 带暴露）。"""

    market_value: str
    weight: str | None = None
    weight_pct: str | None = None
    exposure: str | None = None
    exposure_pct: str | None = None
    holding_count: int


class SectorUnclassifiedOut(StrictModel):
    """未分类持仓（正常披露；分母为 equity_market_value）。"""

    market_value: str
    ratio_of_equity: str | None = None
    holding_count: int
    symbols: list[str]


class SectorConcentrationOut(StrictModel):
    """行业集中度（scale=8）；概念请求恒 unavailable。"""

    method: Literal["industry_single_membership", "concept_overlap"]
    status: str
    hhi: str | None = None
    effective_n: str | None = None
    top1_weight: str | None = None
    top3_weight: str | None = None
    benchmark_hhi: str | None = None
    n_sectors: int


class SectorPreferenceItemOut(StrictModel):
    """板块偏好一项（active_weight scale=8 + 两位百分比 + 方向）。"""

    sector_code: str
    sector_name: str
    active_weight: str
    active_weight_pct: str
    direction: SectorDirection | None = None


class SectorPreferenceOut(StrictModel):
    """板块主动偏离（Σ|Δ|/2，scale=8）。"""

    deviation: str | None = None
    max_overweight: SectorPreferenceItemOut | None = None
    max_underweight: SectorPreferenceItemOut | None = None
    top_overweight: list[SectorPreferenceItemOut]
    top_underweight: list[SectorPreferenceItemOut]


class SectorConceptTopOut(StrictModel):
    """概念 TopN（按 exposure 降序，不是 tag_share）。"""

    sector_code: str
    sector_name: str
    exposure: str
    exposure_pct: str


class SectorConceptBlockOut(StrictModel):
    """概念块（多归属；overlap 为 Σexposure 比率，非权重）。"""

    coverage: str
    coverage_pct: str
    coverage_basis: str
    tagged_market_value: str
    multi_tag_market_value: str
    multi_tag_pct: str
    overlap: str
    hhi_tag: str | None = None
    top1_concept: SectorConceptTopOut | None = None
    top5_concepts: list[SectorConceptTopOut]
    caveat: str | None = None


class SectorOverviewLinksOut(StrictModel):
    dashboard: str
    sectors: str
    attribution: str


class SectorOverviewOut(StrictModel):
    """API-087：当前快照的板块配置/偏好/集中度（只读；金额/比率后端格式化）。"""

    as_of: date
    taxonomy: SectorTaxonomy
    level: int
    status: SectorStatus
    asset_build_state: Literal["missing", "current", "stale", "blocked"]
    sector_data_state: SectorDataState
    definition_version: str
    sector_dataset_version: str | None = None
    data_as_of: date | None = None
    sector_data_as_of: date | None = None
    benchmark_id: str | None = None
    benchmark_as_of: date | None = None
    weight_mode: Literal["additive", "overlapping"]
    portfolio: SectorPortfolioOut
    sectors: list[SectorRowOut]
    others: SectorOthersOut | None = None
    unclassified: SectorUnclassifiedOut | None = None
    concentration: SectorConcentrationOut
    preference: SectorPreferenceOut | None = None
    concept: SectorConceptBlockOut | None = None
    missing: list[SectorMissingOut]
    links: SectorOverviewLinksOut
    assumptions: dict[str, str]
    inputs_hash: str


class SectorCatalogPathOut(StrictModel):
    sector_code: str
    sector_name: str


class SectorCatalogItemOut(StrictModel):
    sector_code: str
    sector_name: str
    taxonomy: SectorTaxonomy
    level: int
    parent_code: str | None = None
    path: list[SectorCatalogPathOut]
    member_count: int
    source: Literal["sw", "em"]
    classification_version: str
    is_active: bool


class SectorCatalogOut(StrictModel):
    """API-088：板块目录树（公共数据；未同步 → items=[] + missing）。"""

    as_of: date
    taxonomy: SectorTaxonomy
    catalog_version: str | None = None
    sector_data_state: SectorDataState
    items: list[SectorCatalogItemOut]
    next_cursor: str | None = None
    has_more: bool
    missing: list[SectorMissingOut]


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


class MetricConfidenceOut(BaseModel):
    level: Literal["high", "medium", "low", "unavailable"]
    label: str
    reason: str


class MetricProgressOut(BaseModel):
    current: int
    required: int
    remaining: int
    unit: str
    unit_label: str
    label: str


class MetricVerdictOut(BaseModel):
    level: Literal["good", "neutral", "watch", "unknown", "unavailable"]
    label: str
    rationale: str


class MetricWindowOut(BaseModel):
    kind: str
    label: str


class MetricInterpretationOut(BaseModel):
    fact: str
    meaning: str
    action: str


class MetricBandOut(BaseModel):
    level: Literal["good", "neutral", "watch"]
    label: str
    from_: str | None = Field(default=None, alias="from")
    """区间下界（定点字符串；None=无下界）。"""
    to: str | None = None


class MetricPresentationOut(BaseModel):
    """指标呈现层（2026-10-30 P0-3）：后端渲染，前端零口径展示。"""

    display_name: str
    direction: Literal["higher_better", "lower_better", "neutral"]
    confidence: MetricConfidenceOut
    progress: MetricProgressOut | None = None
    verdict: MetricVerdictOut
    bands: list[MetricBandOut] | None = None
    window: MetricWindowOut
    interpretation: MetricInterpretationOut
    caveats: list[str] = Field(default_factory=list)


class MetricCatalogItemOut(BaseModel):
    """单个指标的口径说明（API-086）：静态定义 + 经验区间，无用户数据。"""

    metric_id: str
    display_name: str
    display_name_en: str
    domain: str
    direction: Literal["higher_better", "lower_better", "neutral"]
    unit: Literal["CNY", "ratio", "days", "count", "z"]
    precision: int
    definition_version: str
    scope: dict[str, str]
    min_n: int
    ci_min_n: int
    ci: Literal["wilson", "bootstrap", "none"]
    min_trading_days: int
    window_kind: str
    bands: list[MetricBandOut] | None = None
    meaning: str
    method: str
    what_to_do: str | None = None
    caveat: str | None = None


class MetricCatalogOut(BaseModel):
    registry_version: str
    items: list[MetricCatalogItemOut] = Field(default_factory=list)


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
    presentation: MetricPresentationOut | None = None

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


class MetricExplainSample(BaseModel):
    """解释页的样本口径：当前样本量 + 该指标定义里的判定门槛。"""

    n: int
    n_pending: int
    sample_status: Literal["ok", "insufficient", "pending_window", "blocked"]
    min_n: int
    ci_min_n: int
    ci: Literal["wilson", "bootstrap", "none"]


class MetricExplainOut(BaseModel):
    metric_id: str
    definition_version: str
    period: str
    # meaning 恒非空（含 status!="ok"）：指标是什么、为什么重要；
    # conclusion 只在样本可用时给出本期结论（C-31 同源口径）。
    meaning: str
    conclusion: str | None = None
    method: str
    domain: str
    unit: Literal["CNY", "ratio", "days", "count", "z"]
    precision: int
    sample: MetricExplainSample
    drilldown: MetricExplainDrilldown
    status: Literal["ok", "blocked", "stale", "unreliable"]
    reason: str | None = None
    what_to_do: str | None = None
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_as_of: date
    presentation: MetricPresentationOut


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


# ── 归因（API-036，2026-11-02 五因子设计 §6.2）───────────────


class AttributionPresentationOut(StrictModel):
    """因子呈现三元组：`bar_pct` 由 share 取绝对值派生（前端直接作 CSS 宽度）。"""

    bar_pct: str | None = None
    ratio_pct: str | None = None
    share_pct: str | None = None
    tone: Literal["positive", "negative", "flat"]
    # 板块因子是否使用了期初回退价（节假日缺口；仅 sector 可能为 True）。
    sector_bar_fallback: bool = False


class AttributionFactorOut(StrictModel):
    """`factors[]` 单项：固定 5 项、固定顺序 [selection, sector, buy_timing, sell_timing, fee]。"""

    code: str
    name: str
    amount: str | None = None
    ratio: str | None = None
    share: str | None = None
    rank: int
    conclusion: str | None = None
    presentation: AttributionPresentationOut


class AttributionSampleOut(StrictModel):
    """样本规模与结论门禁（insufficient 只封 summary/conclusion，金额照给）。"""

    n_segments: int
    n_attributed_segments: int
    n_rounds: int
    n_symbols: int
    min_n: int
    sample_status: Literal["ok", "insufficient"]


class AttributionUnattributedOut(StrictModel):
    """一条未归因标的披露（amount = 其残差桶金额，可为 0）。

    仅披露本期参与标的；`security_name` 缺失时为 null（前端只展示代码）。
    """

    security_code: str
    security_name: str | None = None
    symbol_class: str
    reason_code: str | None = None
    amount: str


class AttributionCoverageOut(StrictModel):
    """§4.4 覆盖披露：资本 / 段 / 标的三层归因覆盖 + 未归因明细。"""

    capital_total: str
    capital_attributed: str
    capital_excluded: str
    capital_ratio: str | None = None
    capital_ratio_pct: str | None = None
    base_basis: Literal["market", "mixed"]
    segments_total: int
    segments_attributed: int
    symbols_total: int
    symbols_attributed: int
    symbols_participating: int
    unattributed: list[AttributionUnattributedOut] = Field(default_factory=list)


class AttributionMissingOut(StrictModel):
    """200 降级项（字段路径 + 机读码 + 人话 + 可执行指引；码不是 ErrorCode）。"""

    field: str
    field_label: str | None = None
    code: str
    severity: Literal["blocker", "warning", "info"] = "warning"
    reason: str
    what_to_do: str


class AttributionLinksOut(StrictModel):
    dashboard: str
    sectors: str
    rounds: str
    reports: str


class AttributionReconciliationOut(StrictModel):
    """残差对账（后端派生，前端零口径）：标的合计 + 账户级桶合计 == residual。

    `listed_total + others_total == unattributed_symbols_total`；`display_limit` 为
    明细展示上限（单一常量来源，前端不硬编码）。
    """

    unattributed_symbols_total: str
    listed_total: str
    others_count: int
    others_total: str
    display_limit: int
    account_level_total: str
    residual: str


class AttributionOut(StrictModel):
    """五因子归因响应（§6.2；金额 2 位 / 比率 scale=8 定点字符串）。

    blocked 缺省语义（§6.3 评审 P0-7 冻结）：`status="blocked"` 时
    `total_pnl/base_capital/residual/residual_ratio/residual_breakdown/coverage`
    `residual_reconciliation` 整键省略，`factors[].amount/ratio/share` 逐项省略
    （code/name/rank/conclusion 保留）；非 blocked 时 ratio=None 显式输出 null
    （PNL_NEAR_ZERO，附 `ratio_na_reason`）。
    """

    model: str
    ratio_basis: str
    period: str
    period_start: date
    period_end: date
    status: Literal["ok", "degraded", "blocked"]
    definition_version: str
    total_pnl_basis: str
    total_pnl: str | None = None
    ratio_na_reason: str | None = None
    base_capital: str | None = None
    factors: list[AttributionFactorOut] = Field(default_factory=list)
    summary: str | None = None
    summary_na_reason: str | None = None
    residual: str | None = None
    residual_ratio: str | None = None
    residual_ratio_pct: str | None = None
    residual_breakdown: dict[str, str] | None = None
    residual_breakdown_labels: dict[str, str] | None = None
    residual_reconciliation: AttributionReconciliationOut | None = None
    sample: AttributionSampleOut
    coverage: AttributionCoverageOut | None = None
    missing: list[AttributionMissingOut] = Field(default_factory=list)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_as_of: date | None = None
    inputs_hash: str
    links: AttributionLinksOut


# blocked 时整键省略的键（§6.3 评审 P0-7 冻结）。
_BLOCKED_OMIT_KEYS: tuple[str, ...] = (
    "total_pnl",
    "summary",
    "base_capital",
    "residual",
    "residual_ratio",
    "residual_breakdown",
    "residual_reconciliation",
    "coverage",
)


def attribution_payload(out: AttributionOut) -> dict[str, Any]:
    """序列化归因响应（逐层 `mode="json"` 转定点字符串/日期）。

    `blocked` 时整键省略（§6.3）：不渲染任何金额；`factors[]` 逐项省略
    amount/ratio/share，保留 code/name/rank/conclusion。放在路由层而非
    `@model_serializer`，否则 pydantic 会把 OpenAPI 响应退化成
    `additionalProperties: true`，前端 SDK 丢失全部字段。
    """
    payload = out.model_dump(mode="json")
    if out.status == "blocked":
        for key in _BLOCKED_OMIT_KEYS:
            payload.pop(key, None)
        for factor in payload["factors"]:
            factor.pop("amount", None)
            factor.pop("ratio", None)
            factor.pop("share", None)
    return payload


# ── 回合洞察（API-092~094，设计 2026-11-04）──────────────────
class ForwardReturnOut(BaseModel):
    """前瞻收益（B0~B3）；`*_pct`/`*_display` 均为后端格式化字符串。"""

    horizon: int
    status: Literal[
        "ok",
        "pending",
        "no_calendar",
        "no_bar",
        "stale_price",
        "corporate_action",
        "divisor_missing",
    ]
    base_shifted: bool
    b0_pct: str | None = None
    b0_net_pct: str | None = None
    b1_pct: str | None = None
    b2_pct: str | None = None
    b3_pct: str | None = None
    max_close_display: str | None = None
    min_close_display: str | None = None
    price_jump_review: bool = False


class TradeFlagOut(BaseModel):
    """账单级旗标（与回合旗标隔离）；前端只渲染码值与后端文案。"""

    code: str
    label_zh: str
    label_en: str
    status: str
    status_label: str
    lookahead: bool = False
    lookahead_label: str | None = None
    reason: str | None = None


class TradeFlagGlossaryOut(BaseModel):
    """账单级旗标说明（中英文单点维护）。"""

    code: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    side: Literal["buy", "sell"]
    lookahead: bool = False


class TradeSideInsightOut(BaseModel):
    trade_id: str
    date: date
    price_display: str | None = None
    quantity_display: str | None = None
    amount_display: str | None = None
    reason: TradeReasonOut | None = None
    reason_label: str | None = None
    forward: dict[str, ForwardReturnOut] = Field(default_factory=dict)
    trade_flags: list[TradeFlagOut] = Field(default_factory=list)


class RoundInsightItemOut(BaseModel):
    round_id: str
    security_code: str
    security_name: str
    status: str
    open_date: date
    close_date: date | None = None
    holding_days_legacy: int
    holding_trading_days: int | None = None
    bucket: Literal["scalp", "short", "position", "long", "unknown"]
    pnl: str
    return_rate_pct: str
    buy_trade: TradeSideInsightOut | None = None
    sell_trade: TradeSideInsightOut | None = None
    buy_excess_pct: str | None = None
    sell_avoid_pct: str | None = None
    combined_pct: str | None = None
    rank: int | None = None
    quadrant: str | None = None
    flags: list[str] = Field(default_factory=list)
    quality: list[str] = Field(default_factory=list)
    # 计数型状态：bottom_buy / stop_loss_sold_early / profit_sold_early 固定三键（可为 0）。
    status_counts: dict[str, int] = Field(default_factory=dict)
    missed_profit_worst_display: str | None = None
    missed_profit_end_display: str | None = None
    # additive：绝对值展示串 + 符号语义标签（少赚/多赚），前端只渲染不判符号。
    missed_profit_worst_abs_display: str | None = None
    missed_profit_worst_label: str | None = None
    missed_profit_worst_label_en: str | None = None
    missed_profit_end_abs_display: str | None = None
    missed_profit_end_label: str | None = None
    missed_profit_end_label_en: str | None = None


class InsightBucketSummaryOut(BaseModel):
    closed: int
    scalp: int
    short: int
    position: int
    long: int
    avg_holding_trading_days: str | None = None
    win_rate_pct: str | None = None
    avg_pnl_display: str | None = None
    total_pnl_display: str | None = None


class InsightReasonShareOut(BaseModel):
    code: str
    label: str
    count: int
    share_pct: str


class InsightReasonCoverageOut(BaseModel):
    annotated: int
    total_rounds: int
    coverage_pct: str | None = None
    by_code: list[InsightReasonShareOut] = Field(default_factory=list)


class InsightRankingOut(BaseModel):
    n_sample: int
    n_eligible: int
    sample_status: Literal["ok", "insufficient", "no_conclusion"]
    combined_median_pct: str | None = None
    combined_avg_pct: str | None = None
    quadrant_counts: dict[str, int] = Field(default_factory=dict)
    per_stock: list[dict[str, Any]] = Field(default_factory=list)
    basis: str
    definition_version: str


class InsightFillSwingOut(BaseModel):
    """fill 级波段配对（统计单位 fill，与回合级 swing 不可相加，AC-S3）。"""

    bucket_basis: str = "fill"
    pairs: int
    win_rate_pct: str | None = None
    avg_holding_trading_days: str | None = None
    avg_pnl_display: str | None = None
    total_pnl_display: str | None = None


class InsightAddonSummaryOut(BaseModel):
    """加仓统计（设计 §6.3）：`count` 与兼容字段 `rounds` 同值。

    `avg_dip_pct` 是加仓笔次 20 日 B2 均值，仅对 `status=ok` 样本取均值；
    `avg_dip_coverage` 形如 `"3/9"`，0 个有效样本时 `avg_dip_pct=null`。
    """

    count: int
    declined_after_addon: int
    declined_pct: str | None = None
    avg_dip_pct: str | None = None
    avg_dip_coverage: str
    rounds: int
    share_pct: str | None = None


class InsightSellFlySummaryOut(BaseModel):
    """卖飞统计：只算止盈型卖飞，止损后反弹单列不并入。"""

    sell_count: int
    profit_take_fly: int
    stop_loss_rebound: int
    fly_pct: str | None = None
    missed_profit_worst_display: str | None = None
    missed_profit_end_display: str | None = None
    # additive：绝对值展示串 + 符号语义标签（少赚/多赚），前端只渲染不判符号。
    missed_profit_worst_abs_display: str | None = None
    missed_profit_worst_label: str | None = None
    missed_profit_worst_label_en: str | None = None
    missed_profit_end_abs_display: str | None = None
    missed_profit_end_label: str | None = None
    missed_profit_end_label_en: str | None = None
    rounds: int
    share_pct: str | None = None


class InsightPnlHoldingClassOut(BaseModel):
    """单类盈亏的持有期（`code` 与文案均由后端给，前端不重复判定）。"""

    code: Literal["loss", "flat", "win"]
    label: str
    count: int
    # 一位小数展示串（如 "3.5"）；无可用样本为 null。
    avg_holding_trading_days: str | None = None
    # 交易日 × 10 的定点整数（35 → 3.5 日），供柱状图直接用，避免浮点到前端。
    avg_holding_trading_days_x10: int | None = None


class InsightPnlHoldingOut(BaseModel):
    """盈亏 × 持有期：亏损/保本/盈利三类恒定返回三条（计数为 0 时均值为 null）。"""

    # 「保本」容差展示串（默认 "100.00"，单位元），判定在 stock_core 常量。
    flat_tolerance_display: str
    classes: list[InsightPnlHoldingClassOut]


class InsightFlagRoundCountOut(BaseModel):
    """按回合旗标统计的回合数（v1.2 词表全部 item_flag）。

    口径是筛选后的全量回合（与分页无关），同一回合只计一次，不累加旗标次数。
    """

    flag: str
    count: int


class InsightsSummaryOut(BaseModel):
    swing: InsightBucketSummaryOut
    fill_swing: InsightFillSwingOut
    holding_by_pnl: InsightPnlHoldingOut
    addon: InsightAddonSummaryOut
    sell_fly: InsightSellFlySummaryOut
    reason_coverage: InsightReasonCoverageOut
    ranking: InsightRankingOut
    flag_round_counts: list[InsightFlagRoundCountOut] = Field(default_factory=list)
    pending: dict[str, int]


class FlagGlossaryOut(BaseModel):
    """旗标枚举值的解释（code + 中英文标签与说明），前端零口径直接渲染。"""

    code: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    kind: Literal["item_flag", "filter_alias"]


class RoundInsightsPageOut(BaseModel):
    period: str
    period_basis: str
    definition_version: str
    bucket_basis: str = "round"
    data_as_of: date | None = None
    ranking_as_of: date | None = None
    benchmark_id: str
    basis: str
    item_total: int
    summary: InsightsSummaryOut | None = None
    items: list[RoundInsightItemOut]
    next_cursor: str | None = None
    has_more: bool
    missing: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    disclaimer: str
    flag_glossary: list[FlagGlossaryOut] = Field(default_factory=list)


class RoundInsightDetailOut(BaseModel):
    round_id: str
    security_code: str
    security_name: str
    status: str
    open_date: date
    close_date: date | None = None
    holding_days_legacy: int
    holding_trading_days: int | None = None
    bucket: Literal["scalp", "short", "position", "long", "unknown"]
    pnl: str
    return_rate_pct: str
    buy_trade: TradeSideInsightOut | None = None
    sell_trade: TradeSideInsightOut | None = None
    fills: list[dict[str, Any]] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    quality: list[str] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
    ranking: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str
    flag_glossary: list[FlagGlossaryOut] = Field(default_factory=list)
    trade_flag_glossary: list[TradeFlagGlossaryOut] = Field(default_factory=list)


class RoundInsightsRefreshIn(StrictModel):
    period: str | None = Field(default=None, max_length=40)
    scope: Literal["missing", "all"] = "missing"


class RoundInsightsRefreshOut(BaseModel):
    computed: int
    skipped: int
    pending: int
    no_calendar: int
    no_bar: int
    corporate_action: int
    divisor_missing: int
    stale_price: int
    data_as_of: date | None = None
