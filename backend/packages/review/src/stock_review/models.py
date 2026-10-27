"""review schema 的 ORM 模型（Architecture §9.2 / DEV-PLAN Period2-02）。

回合（round）是 FIFO 配对的落库结果：一次卖出 + 其消耗的买入 lot 构成一个回合，
`review.round_fill` 保存回合内逐 chunk 的配对明细（谁买的、谁卖的、分摊了多少成本/现金/股息）。
`review.round_build_state` 记录每个用户最近一次 rebuild 的水位线，供脏窗口增量重算使用。

硬约束：
- 金额/数量/比率一律 BigInteger 标度整数（金额 2 位、数量 4 位、价格 6 位、比率 1e-8），
  列名以 `_units` 结尾；禁止 float 或 Numeric。
- 三张表都带 `user_id`，迁移 `0003_review_rounds` 同批启用 RLS tenant 策略
  （`scripts/ci/gate_rls_coverage.py` 强制覆盖 review: 3/3）。
- 回合 ID 为确定性值：`rnd_` + sha256(user_id|security_code|key)[:32]，
  重复 rebuild 的 ID/值不变（幂等）。枚举（status）用 text 存储 + 应用层校验。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from stock_platform.db import Base

SCHEMA = "review"
USER_FK = "platform.user.id"

# 回合状态：closed（已平仓） / open（期末仍持有） / data_error（超卖等数据错误，按 closed 落库）
ROUND_STATUSES: tuple[str, ...] = ("closed", "open", "data_error")

# 净值状态：ok（正常） / blocked（阻断，无资产值） / degraded（降级，有数据但不可靠）
NAV_STATUSES: tuple[str, ...] = ("ok", "blocked", "degraded")


class Round(Base):
    """FIFO 回合（一次卖出与被其消耗的买入 lot 的配对结果）。

    存在的原因：回合是收益归因的最小实体——没有它，“这笔卖出赚了多少”只能靠临时
    重算，无法分页、无法口径留痕、也无法让 API 稳定返回。主键确定性取值，
    保证同一用户同一交易结构重复 rebuild 时 ID 不变。
    """

    __tablename__ = "round"
    __table_args__ = (
        # 游标分页排序键：close_date DESC NULLS LAST, id DESC（open 回合排最后）。
        Index(
            "ix_round_user_close_id",
            "user_id",
            text("close_date DESC NULLS LAST"),
            text("id DESC"),
        ),
        Index("ix_round_user_security_close", "user_id", "security_code", "close_date"),
        Index("ix_round_user_status", "user_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    security_code: Mapped[str] = mapped_column(String(16), nullable=False)
    security_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    open_trade_id: Mapped[str] = mapped_column(String(40), nullable=False)
    close_trade_id: Mapped[str | None] = mapped_column(String(40))
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_date: Mapped[date | None] = mapped_column(Date)
    quantity_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proceeds_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dividend_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pnl_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    return_rate_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    holding_days: Mapped[int] = mapped_column(Integer, nullable=False)
    data_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oversell_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fills_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RoundFill(Base):
    """回合内逐 chunk 的 FIFO 配对明细（一个回合可消耗多个买入 lot）。

    存在的原因：回合级 pnl 无法自证——必须能下钻到“卖了哪几笔、每笔分摊多少成本/现金/股息”，
    才能核对 FIFO 配对是否正确、股息是否按持股数归属。
    """

    __tablename__ = "round_fill"
    __table_args__ = (
        UniqueConstraint("round_id", "sequence", name="uq_round_fill_round_sequence"),
        Index("ix_round_fill_user_round", "user_id", "round_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    round_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.round.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_trade_id: Mapped[str] = mapped_column(String(40), nullable=False)
    sell_trade_id: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buy_cost_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_proceeds_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dividend_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pnl_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    holding_days: Mapped[int] = mapped_column(Integer, nullable=False)


class RoundBuildState(Base):
    """每个用户的回合重建水位线（脏窗口增量重算的输入）。

    存在的原因：全量重建在大账单上不可接受；增量重建必须知道“上次重建时已看到哪些
    数据”，才能把窗口外被修改/撤销的流水所在证券也纳入重算，避免 FIFO 路径依赖导致
    的错误回合残留。
    """

    __tablename__ = "round_build_state"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(USER_FK, ondelete="CASCADE"),
        primary_key=True,
    )
    last_rebuilt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_trade_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    last_period_start: Mapped[date | None] = mapped_column(Date)
    last_period_end: Mapped[date | None] = mapped_column(Date)
    rounds_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class NavDaily(Base):
    """每日单位净值（Period2-03）：份额法 TWR 的落库结果。

    存在的原因：NAV 序列需要分页查询、口径留痕、与回合/指标联动；每次 GET /nav
    实时全量计算在大账单上不可接受。rebuild-rounds 时一并写入，stale 时重算。

    主键确定性：`nav_` + sha256(user_id|trade_date)[:32]，重复 rebuild 幂等。
    `blocked` 行的 nav/return/total_value 字段为 0（不输出资产值，API 层过滤）。
    """

    __tablename__ = "nav_daily"
    # 与迁移 0004 完全一致：唯一性由 UniqueConstraint 保证，另有一条普通索引
    # （此前模型写成 unique Index 导致 `alembic check` 报漂移，属历史债）。
    __table_args__ = (
        UniqueConstraint("user_id", "trade_date", name="uq_nav_daily_user_date"),
        Index("ix_nav_daily_user_date", "user_id", "trade_date"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    nav_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    return_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_value_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cashflow_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shares_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    blocked_reason: Mapped[str | None] = mapped_column(String(256))
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# 资产表行状态：ok（正常） / blocked（阻断：该日缺行情或全局无现金锚点） /
# degraded（降级：FIFO 数据异常、超卖负持仓、行情未覆盖按最近收盘估值）。
ASSET_STATUSES: tuple[str, ...] = ("ok", "blocked", "degraded")
# 构建整体状态（asset_build_state.last_status）：逐日 blocked 使整体为 degraded。
ASSET_BUILD_STATUSES: tuple[str, ...] = ("ok", "degraded", "blocked")
# 构建模式：full（全量）/ incremental（增量）/ noop（无变化只记录检查）。
ASSET_BUILD_MODES: tuple[str, ...] = ("full", "incremental", "noop")


class AssetDaily(Base):
    """每日资产快照（Period2-16）：一行 = 某用户某自然日收盘后的现金 + 持仓估值。

    存在的原因：资产变化与后续回撤族指标需要**全历史、逐自然日、可增量维护**的资产序列；
    `nav_daily` 按月稀疏且由 GET /nav 隐式写入，不适合做该数据源。

    硬约束（应用层断言 + DB CHECK）：
    - `total_assets_units = cash_units + market_value_units`；
    - `holding_count >= 0`；**不设** `market_value >= 0`（超卖日允许负市值，标 degraded）；
    - blocked 行数值列全 0、`prev_total/daily_pnl/daily_return` 为 NULL、`status_reason` 非空
      （API 层把 blocked 数值列输出为 null）。
    主键确定性：`ast_` + sha256(user_id|trade_date)[:32]，重复构建幂等。
    """

    __tablename__ = "asset_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "trade_date", name="uq_asset_daily_user_date"),
        CheckConstraint(
            "total_assets_units = cash_units + market_value_units",
            name="ck_asset_daily_total",
        ),
        CheckConstraint("holding_count >= 0", name="ck_asset_daily_holding_count"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    market_value_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_assets_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    holding_cost_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    unrealized_pnl_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    holding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cashflow_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prev_total_assets_units: Mapped[int | None] = mapped_column(BigInteger)
    daily_pnl_units: Mapped[int | None] = mapped_column(BigInteger)
    daily_return_units: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    status_reason: Mapped[str | None] = mapped_column(String(256))
    definition_version: Mapped[str] = mapped_column(String(16), nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AssetBuildState(Base):
    """每个用户的资产表构建水位线 + 摘要（增量更新与 build 摘要的输入）。

    存在的原因：增量必须知道“上次构建看到了哪些流水/哪个行情 as_of”，no-op 也要能区分
    “本次是检查还是重建”，否则前端无法展示「已是最新」；同时用 `version` 做状态 CAS，
    避免并发构建把水位线写乱。
    """

    __tablename__ = "asset_build_state"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(USER_FK, ondelete="CASCADE"),
        primary_key=True,
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    last_built_through: Mapped[date | None] = mapped_column(Date)
    last_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_count: Mapped[int | None] = mapped_column(BigInteger)
    last_trade_version_sum: Mapped[int | None] = mapped_column(BigInteger)
    data_as_of: Mapped[date | None] = mapped_column(Date)
    assumption: Mapped[str | None] = mapped_column(String(16))
    last_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    last_fifo_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_reason: Mapped[str | None] = mapped_column(String(256))
    last_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="full")
    last_rebuild_from: Mapped[date | None] = mapped_column(Date)
    last_rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    definition_version: Mapped[str] = mapped_column(String(16), nullable=False)
    rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# 指标状态（MetricStatus）与样本状态（SampleStatus）取值，供 DB CHECK + 门禁引用。
METRIC_STATUSES: tuple[str, ...] = ("ok", "stale", "blocked", "unreliable")
SAMPLE_STATUSES: tuple[str, ...] = ("ok", "insufficient", "pending_window", "blocked")
METRIC_UNITS: tuple[str, ...] = ("CNY", "ratio", "days", "count", "z")


class MetricResult(Base):
    """指标物化结果（Period2-05）：一行 = 某用户某指标在某 period/口径版本下的最近一次成功计算。

    存在的原因：GET /metrics 需要「读即命中」而不是每次全量重算；report/trends 也需要
    历史可读的指标行。主键确定性（`mtr_` + sha256(user|metric|period|version)[:32]），
    重复计算按唯一键 UPSERT 幂等；口径 bump 后新版本插入、旧版本保留（趋势断点可读历史段）。

    硬约束（应用层 + DB CHECK）：
    - 数值一律 BigInteger 标度整数（scale = 10**precision）；
    - `status != 'ok'` 时 `value_units` 必须为 NULL（非 ok 不出数字）；
    - `ci_low_units` 与 `ci_high_units` 必须成对出现。
    """

    __tablename__ = "metric_result"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "metric_id",
            "period",
            "definition_version",
            "params_hash",
            name="uq_metric_result_user_metric_period_version_params",
        ),
        CheckConstraint(
            "status IN ('ok','stale','blocked','unreliable')",
            name="ck_metric_result_status",
        ),
        CheckConstraint(
            "sample_status IN ('ok','insufficient','pending_window','blocked')",
            name="ck_metric_result_sample_status",
        ),
        CheckConstraint(
            "definition_version ~ '^[0-9]+[.][0-9]+$'",
            name="ck_metric_result_definition_version",
        ),
        CheckConstraint(
            r"period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_metric_result_period",
        ),
        CheckConstraint("unit IN ('CNY','ratio','days','count','z')", name="ck_metric_result_unit"),
        CheckConstraint("precision BETWEEN 0 AND 8", name="ck_metric_result_precision"),
        CheckConstraint("n >= 0 AND n_pending >= 0", name="ck_metric_result_n_nonneg"),
        CheckConstraint(
            "value_units IS NULL OR (status = 'ok' AND sample_status IN ('ok','insufficient'))",
            name="ck_metric_result_value_gate",
        ),
        CheckConstraint(
            "(ci_low_units IS NULL) = (ci_high_units IS NULL) "
            "AND (ci_low_units IS NULL OR (status = 'ok' AND sample_status = 'ok'))",
            name="ck_metric_result_ci_gate",
        ),
        CheckConstraint(
            "NOT (status = 'ok' AND sample_status = 'blocked') AND (status = 'ok' OR reason IS NOT NULL)",
            name="ck_metric_result_status_gate",
        ),
        Index("ix_metric_result_user_period", "user_id", "period"),
        Index("ix_metric_result_user_domain", "user_id", "domain"),
        Index("ix_metric_result_user_metric_period", "user_id", "metric_id", "period"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(16), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sample_status: Mapped[str] = mapped_column(String(16), nullable=False)
    value_units: Mapped[int | None] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    precision: Mapped[int] = mapped_column(Integer, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_pending: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ci_low_units: Mapped[int | None] = mapped_column(BigInteger)
    ci_high_units: Mapped[int | None] = mapped_column(BigInteger)
    decomposition: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    assumptions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(String(128))
    inputs_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    data_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MetricDependency(Base):
    """指标血缘（Period2-05）：一次物化实际消费的上游指标 / 数据集 + 各自的指纹。

    存在的原因：缓存命中/失效必须能解释「为什么这次重算」；lineage 审计需要知道
    某指标依赖了哪些数据集与上游指标。失效判定本身走运行时 DAG（注册表是静态代码），
    本表只做记录，不参与判定。
    """

    __tablename__ = "metric_dependency"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "metric_id",
            "period",
            "definition_version",
            "dep_kind",
            "dep_id",
            name="uq_metric_dependency_key",
        ),
        CheckConstraint("dep_kind IN ('metric','dataset')", name="ck_metric_dependency_kind"),
        Index("ix_metric_dependency_user_metric", "user_id", "metric_id", "period"),
        Index("ix_metric_dependency_user_dep", "user_id", "dep_kind", "dep_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(16), nullable=False)
    dep_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    dep_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dep_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_result_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey(f"{SCHEMA}.metric_result.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# 供迁移/RLS 门禁/测试 TRUNCATE 引用的表名清单（避免手写遗漏）
REVIEW_TABLES: tuple[str, ...] = (
    Round.__tablename__,
    RoundFill.__tablename__,
    RoundBuildState.__tablename__,
    NavDaily.__tablename__,
    AssetDaily.__tablename__,
    AssetBuildState.__tablename__,
    MetricResult.__tablename__,
    MetricDependency.__tablename__,
)

__all__ = [
    "ASSET_BUILD_MODES",
    "ASSET_BUILD_STATUSES",
    "ASSET_STATUSES",
    "METRIC_STATUSES",
    "METRIC_UNITS",
    "NAV_STATUSES",
    "REVIEW_TABLES",
    "ROUND_STATUSES",
    "SAMPLE_STATUSES",
    "AssetBuildState",
    "AssetDaily",
    "MetricDependency",
    "MetricResult",
    "NavDaily",
    "Round",
    "RoundBuildState",
    "RoundFill",
]
