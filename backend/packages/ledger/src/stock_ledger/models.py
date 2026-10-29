"""ledger schema 的 ORM 模型（Architecture §9.2）。

所有金额/数量/价格列一律以 BigInteger 存标度整数（金额 2 位、价格 6 位、数量 4 位），
列名以 `_units` 结尾；禁止在库里出现 float 或定点字符串——跨边界用 `stock_core.values` 的
`Money/Price/Qty` 现算现转。这样做的原因是：浮点误差会在对账/收益复算里累积成“假断链”。
枚举列一律用 text 存储 + 应用层校验，新增取值不需要迁移（§2.4 兼容规则）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

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

SCHEMA = "ledger"

USER_FK = "platform.user.id"


class ImportBatch(Base):
    """一次导入提交（两阶段导入的第二阶段）。

    存在的原因：导入必须可撤销、可审计、可幂等重放。`ranges` 记录本次写入覆盖的
    日期区间，`replaced_batch_id` 记录被 replace_range 模式覆盖的旧批次，撤销时据此
    精确定位影响面，而不是“删掉用户所有数据”。
    """

    __tablename__ = "import_batch"
    __table_args__ = (
        Index("ix_import_batch_user_created", "user_id", "created_at"),
        Index("ix_import_batch_preview", "preview_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    preview_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="append")
    broker: Mapped[str] = mapped_column(String(16), nullable=False, default="huatai")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    parsed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    columns: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    date_format: Mapped[str | None] = mapped_column(String(32))
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    ranges: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(48))
    replaced_batch_id: Mapped[str | None] = mapped_column(String(40))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportPreview(Base):
    """上传预检（两阶段导入的第一阶段）。

    硬约束：预览态**不写任何业务行**，只落原始文件对象键 + 列映射 + 预检计数。
    这样“先看清楚再决定写不写”，避免半截脏数据；TTL 24h 后由 purge_expired 清理。
    """

    __tablename__ = "import_preview"
    __table_args__ = (
        Index("ix_import_preview_user", "user_id"),
        Index("ix_import_preview_expires", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    columns: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    date_format: Mapped[str | None] = mapped_column(String(32))
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    sample: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unresolved_columns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Trade(Base):
    """交易明细（软删 + 版本乐观锁 + 租户唯一去重键）。

    存在的原因：明细是所有收益/归因/报告的源数据，必须支持人工修正（version 乐观锁）、
    误删恢复（deleted_at 软删）、重复导入幂等（(user_id, dedupe_key) 仅在有效行上唯一）。
    """

    __tablename__ = "trade"
    __table_args__ = (
        Index("ix_trade_user_date_id", "user_id", "trade_date", "id"),
        Index("ix_trade_user_batch", "user_id", "batch_id"),
        # 资产表增量脏查询（updated_at 水位线）专用：index-only 覆盖
        # `WHERE user_id = ? AND updated_at >= ? ORDER BY updated_at`。
        Index("ix_trade_user_updated_at", "user_id", "updated_at", "trade_date"),
        Index(
            "uq_trade_user_dedupe",
            "user_id",
            "dedupe_key",
            "occurrence_idx",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("version >= 1", name="ck_trade_version"),
        {"schema": SCHEMA},
    )
    # 修正/软删/恢复是 UPDATE：`updated_at` 的 onupdate 默认值在 UPDATE 后会被 SQLAlchemy
    # 标记为 expired，异步会话下一旦被 DTO 读取就会触发同步 IO（MissingGreenlet）。
    # 打开 eager_defaults 让 UPDATE 直接 RETURNING 取回该值，避免惰性刷新。
    # 注意：SQLAlchemy 声明式基类把 `__mapper_args__` 声明为实例属性（`Any`），
    # 因此这里不能加 `ClassVar`（会触发 mypy「实例变量被类变量覆盖」）；运行时仍是类属性。
    # 该 dict 由 SQLAlchemy 读取、非我方共享可变状态，故对本行豁免 RUF012。
    __mapper_args__: Any = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str | None] = mapped_column(String(40))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    security_code: Mapped[str] = mapped_column(String(16), nullable=False)
    security_name: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(64))
    summary_code: Mapped[str | None] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    stamp_tax_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    other_fee_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cash_balance_units: Mapped[int | None] = mapped_column(BigInteger)
    cash_balance_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 货币基金余额（该行发生后该标的的份额余额，金额 scale=2）。非空即「货基行」标记：
    # 货基没有可估值净值（单位净值恒 1.0000），按余额计入资产，不请求行情。
    money_fund_balance_units: Mapped[int | None] = mapped_column(BigInteger)
    category: Mapped[str | None] = mapped_column(String(24))
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    occurrence_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_masked: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="import")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    line_no: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TradeHistory(Base):
    """交易变更留痕（append-only）。

    存在的原因：人工修正/删除/恢复都要可追溯、可回滚定位；snapshot 存整行、
    changed_fields 存差异，二者合起来才能“看清楚改了什么”。
    """

    __tablename__ = "trade_history"
    __table_args__ = (
        Index("ix_trade_history_trade_version", "trade_id", "version"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.trade.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    changed_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuarantineRow(Base):
    """隔离区行（解析/分类失败但不丢数据）。

    存在的原因：脏行不能进明细污染统计，也不能直接丢弃（用户要能回溯修正），
    因此旁存原始内容 + 原因码 + 建议规则，修正后可用 resolved_trade_id 回指。
    """

    __tablename__ = "quarantine_row"
    __table_args__ = (
        Index("ix_quarantine_user_batch", "user_id", "batch_id"),
        Index("ix_quarantine_user_resolved", "user_id", "resolved"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(40))
    line_no: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    suggested_rule_id: Mapped[str | None] = mapped_column(String(40))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_trade_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SummaryRule(Base):
    """摘要 → 业务分类的匹配规则。

    存在的原因：券商摘要文本不统一，业务分类必须可配置；系统默认规则 `user_id IS NULL`
    对所有人生效，用户可叠加/覆盖自己的规则。`(user_id, code)` 用 NULLS NOT DISTINCT 唯一，
    是为了让系统规则（user_id 为 NULL）之间也能互斥去重。
    """

    __tablename__ = "summary_rule"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "code", name="uq_summary_rule_user_code", postgresql_nulls_not_distinct=True
        ),
        Index("ix_summary_rule_user_enabled", "user_id", "enabled"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="both")
    match_type: Mapped[str] = mapped_column(String(16), nullable=False, default="exact")
    pattern: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Reconciliation(Base):
    """资金余额链对账结果（按用户 + 周期 upsert）。

    存在的原因：对账断链必须能定位到具体行（breaks 存行号/交易 ID/期望值/实际值），
    否则“资金对不上”只是一个无法行动的结论。
    """

    __tablename__ = "reconciliation"
    __table_args__ = (
        UniqueConstraint("user_id", "period_start", "period_end", name="uq_reconciliation_period"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    break_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    epsilon_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    breaks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# 供迁移/RLS 脚本引用的表名清单（避免手写遗漏）
LEDGER_TABLES: tuple[str, ...] = (
    ImportBatch.__tablename__,
    ImportPreview.__tablename__,
    Trade.__tablename__,
    TradeHistory.__tablename__,
    QuarantineRow.__tablename__,
    SummaryRule.__tablename__,
    Reconciliation.__tablename__,
)

__all__ = [
    "LEDGER_TABLES",
    "SCHEMA",
    "ImportBatch",
    "ImportPreview",
    "QuarantineRow",
    "Reconciliation",
    "SummaryRule",
    "Trade",
    "TradeHistory",
]
