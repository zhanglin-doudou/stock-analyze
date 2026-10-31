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


class AssetChartPointOut(StrictModel):
    """API-085：图表点（v 为后端归一化无量纲字符串，前端只做几何投影）。"""

    date: date
    # 归一化 [0,1]，4 位定点字符串（如 "0.4000"）；不是金额/业务比率。
    v: str
    label: str
    amount_label: str


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
    asset_overview: DashboardAssetOverviewOut
    history_overview: DashboardHistoryOverviewOut
    missing: list[DashboardMissingOut]
    links: DashboardLinksOut
    assumptions: dict[str, str]
    inputs_hash: str


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
