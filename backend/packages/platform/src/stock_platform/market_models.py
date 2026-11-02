"""行情缓存 ORM（`market` schema：公共数据，无 RLS、无 user_id）。

设计见 `docs/architecture/2026-10-22-live-market-data-source.md` §3：

- 行情是跨租户公共数据，因此不设 `user_id`、不启用 RLS；写路径只允许内部装配
  （不开放 HTTP 端点），读取方是 `MarketDataSource` 端口实现；
- `daily_bar`：按 `(symbol, adjust, trade_date)` 幂等 upsert 的日线缓存；
- `pull_range`：覆盖判定元数据（**不是取数来源**），`range_end` 是“覆盖上界”
  而非“上游最后交易日”；`data_end` 才记录上游实际返回的最后交易日；
- `market` schema 是可再生缓存：不进备份/恢复演练的正确性断言。

金额/价格一律标度整数（`*_units`，BigInteger），禁止 float/Numeric。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from stock_platform.db import Base

SCHEMA = "market"


class DailyBar(Base):
    """单标的单日收盘（scale=6）与由 close 序列派生的收益（scale=8）。

    列与迁移 `0005_market_cache` 逐列一致（`migrate-check` 依赖这一点）。
    """

    __tablename__ = "daily_bar"
    # 主键 (symbol, adjust, trade_date) 已提供按标的+日期区间扫描的索引，不再重复建索引。
    __table_args__ = ({"schema": SCHEMA},)

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    adjust: Mapped[str] = mapped_column(String(8), primary_key=True, server_default="")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    return_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PullRange(Base):
    """某 `(symbol, adjust, adjust_version)` 已拉取并覆盖的日期区间。

    - `range_start..range_end`：覆盖判定上界（命中条件）；
    - `data_end`：上游实际返回的最后交易日（仅观测，可空）；
    - `requested_end`：发起该次拉取时的请求上界；
    - `complete`：`range_end >= requested_end`；
    - `bar_count = 0`：负缓存（该区间无行情），用短 TTL 自动过期；
    - `checksum`：逐 bar `date:close_units` 的 sha256 前 32 位，满足来源留痕。
    """

    __tablename__ = "pull_range"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "adjust",
            "adjust_version",
            "range_start",
            "range_end",
            name="uq_pull_range_scope",
        ),
        Index("ix_pull_range_symbol_pulled", "symbol", "adjust", "pulled_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    adjust: Mapped[str] = mapped_column(String(8), nullable=False)
    adjust_version: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_end: Mapped[date] = mapped_column(Date, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


TAXONOMY_INDUSTRY = "industry_sw"
TAXONOMY_CONCEPT = "concept_em"


class Sector(Base):
    """板块目录（行业 L1/L2/L3 树 + 概念扁平列表）。

    列与迁移 `0011_market_sectors` 逐列一致；`taxonomy` 区分行业/概念，
    `parent_code` 只对行业 L2/L3 生效（概念恒为 NULL）。
    """

    __tablename__ = "sector"
    __table_args__ = (
        CheckConstraint("level BETWEEN 0 AND 3", name="ck_sector_level_range"),
        CheckConstraint(
            "(taxonomy = 'concept_em' AND level = 0 AND parent_code IS NULL) "
            "OR (taxonomy = 'industry_sw' AND level BETWEEN 1 AND 3)",
            name="ck_sector_taxonomy_level",
        ),
        Index("ix_sector_parent", "taxonomy", "parent_code"),
        Index("ix_sector_level", "taxonomy", "level", "is_active"),
        {"schema": SCHEMA},
    )

    taxonomy: Mapped[str] = mapped_column(String(24), primary_key=True)
    sector_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classification_version: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SectorMember(Base):
    """板块成分时点区间（半开区间 `[effective_from, effective_to)`）。"""

    __tablename__ = "sector_member"
    __table_args__ = (
        ForeignKeyConstraint(
            ["taxonomy", "sector_code"],
            ["market.sector.taxonomy", "market.sector.sector_code"],
            name="fk_sector_member_sector",
        ),
        Index("ix_sector_member_symbol", "symbol", "effective_from"),
        Index(
            "ix_sector_member_current",
            "taxonomy",
            "sector_code",
            postgresql_where=text("effective_to IS NULL"),
        ),
        Index(
            "uq_sector_member_industry_current",
            "symbol",
            unique=True,
            postgresql_where=text("taxonomy = 'industry_sw' AND effective_to IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    taxonomy: Mapped[str] = mapped_column(String(24), primary_key=True)
    sector_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    vendor_symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    upstream_effective_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)


class BenchmarkMember(Base):
    """基准成分权重时点区间（`weight_units` scale=8，存上游原值）。"""

    __tablename__ = "benchmark_member"
    __table_args__ = (
        CheckConstraint("weight_units >= 0", name="ck_benchmark_member_weight_nonneg"),
        Index(
            "ix_benchmark_member_current",
            "benchmark_id",
            postgresql_where=text("effective_to IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    benchmark_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    weight_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    weight_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SectorBar(Base):
    """板块指数日线（`close_units` scale=6、`return_units` scale=8）。"""

    __tablename__ = "sector_bar"
    __table_args__ = (
        CheckConstraint("close_units > 0", name="ck_sector_bar_close_positive"),
        ForeignKeyConstraint(
            ["taxonomy", "sector_code"],
            ["market.sector.taxonomy", "market.sector.sector_code"],
            name="fk_sector_bar_sector",
        ),
        {"schema": SCHEMA},
    )

    taxonomy: Mapped[str] = mapped_column(String(24), primary_key=True)
    sector_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    return_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SectorPullRange(Base):
    """板块行情覆盖元数据（语义逐条对齐 `market.pull_range`）。"""

    __tablename__ = "sector_pull_range"
    __table_args__ = (
        UniqueConstraint(
            "taxonomy",
            "sector_code",
            "range_start",
            "range_end",
            name="uq_sector_pull_range_scope",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    taxonomy: Mapped[str] = mapped_column(String(24), nullable=False)
    sector_code: Mapped[str] = mapped_column(String(32), nullable=False)
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_end: Mapped[date] = mapped_column(Date, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SectorSyncState(Base):
    """板块数据新鲜度/最近成功快照元数据。"""

    __tablename__ = "sector_sync_state"
    __table_args__ = ({"schema": SCHEMA},)

    dataset_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="succeeded")
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    catalog_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    membership_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    bar_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    weight_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    classification_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)


__all__ = [
    "SCHEMA",
    "TAXONOMY_CONCEPT",
    "TAXONOMY_INDUSTRY",
    "BenchmarkMember",
    "DailyBar",
    "PullRange",
    "Sector",
    "SectorBar",
    "SectorMember",
    "SectorPullRange",
    "SectorSyncState",
]
