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
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
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


__all__ = ["SCHEMA", "DailyBar", "PullRange"]
