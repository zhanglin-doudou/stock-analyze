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
    __table_args__ = (
        Index("ix_nav_daily_user_date", "user_id", "trade_date", unique=True),
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


# 供迁移/RLS 门禁/测试 TRUNCATE 引用的表名清单（避免手写遗漏）
REVIEW_TABLES: tuple[str, ...] = (
    Round.__tablename__,
    RoundFill.__tablename__,
    RoundBuildState.__tablename__,
    NavDaily.__tablename__,
)

__all__ = [
    "NAV_STATUSES",
    "REVIEW_TABLES",
    "ROUND_STATUSES",
    "NavDaily",
    "Round",
    "RoundBuildState",
    "RoundFill",
]
