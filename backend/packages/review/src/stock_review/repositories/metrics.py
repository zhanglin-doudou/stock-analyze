"""指标物化仓储 + 视图数据查询（Period2-05/06）。

两类职责拆开：
- `MetricResultRepository`：读缓存行、UPSERT 结果、替换血缘（只清当前版本）；
- `MetricDatasetRepository`：按 period 读回合/明细/流水/资产视图，并提供 §6.4 的
  聚合指纹（单视图 ≤2 条 SQL）。

所有方法首参显式 `user_id`（RLS 是第二道防线）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_ledger.models import Trade
from stock_platform.market_models import DailyBar

from stock_review.metrics.hash import canonical_json, sha256_hex
from stock_review.models import (
    AssetBuildState,
    AssetDaily,
    MetricDependency,
    MetricResult,
    Round,
    RoundBuildState,
    RoundFill,
)
from stock_review.repositories.rounds import RoundRepository

_UPSERT_COLUMNS: tuple[str, ...] = (
    "params",
    "domain",
    "scope",
    "status",
    "sample_status",
    "value_units",
    "unit",
    "precision",
    "n",
    "n_pending",
    "ci_low_units",
    "ci_high_units",
    "decomposition",
    "assumptions",
    "reason",
    "inputs_hash",
    "data_as_of",
    "computed_at",
)


class MetricResultRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rows_for_period(
        self, user_id: uuid.UUID, *, period: str, metric_ids: Sequence[str]
    ) -> dict[tuple[str, str, str], MetricResult]:
        """一次取回候选指标的全部历史版本/参数组合行（按唯一键索引）。"""
        if not metric_ids:
            return {}
        rows = await self._session.scalars(
            select(MetricResult).where(
                MetricResult.user_id == user_id,
                MetricResult.period == period,
                MetricResult.metric_id.in_(sorted(set(metric_ids))),
            )
        )
        return {(row.metric_id, row.definition_version, row.params_hash): row for row in rows}

    async def upsert(self, user_id: uuid.UUID, values: Mapping[str, object]) -> None:
        """按唯一键 UPSERT；`updated_at` 必须显式更新（ON CONFLICT 不触发 onupdate）。"""
        payload = {"user_id": user_id, **values}
        statement = pg_insert(MetricResult).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=[
                MetricResult.user_id,
                MetricResult.metric_id,
                MetricResult.period,
                MetricResult.definition_version,
                MetricResult.params_hash,
            ],
            set_={name: getattr(statement.excluded, name) for name in _UPSERT_COLUMNS}
            | {"updated_at": func.now()},
        )
        await self._session.execute(statement)

    async def replace_dependencies(
        self,
        user_id: uuid.UUID,
        *,
        metric_id: str,
        period: str,
        definition_version: str,
        rows: Sequence[Mapping[str, object]],
    ) -> None:
        """只删当前 `(user, metric, period, version)` 的血缘，再插入本次消费的依赖。"""
        await self._session.execute(
            delete(MetricDependency).where(
                MetricDependency.user_id == user_id,
                MetricDependency.metric_id == metric_id,
                MetricDependency.period == period,
                MetricDependency.definition_version == definition_version,
            )
        )
        if rows:
            await self._session.execute(
                insert(MetricDependency),
                [{"user_id": user_id, **row} for row in rows],
            )

    async def count(self, user_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(MetricResult)
                .where(MetricResult.user_id == user_id)
            )
            or 0
        )


class MetricDatasetRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 视图数据 ────────────────────────────────────────────
    async def rounds_in_period(self, user_id: uuid.UUID, start: date, end: date) -> list[Round]:
        return await RoundRepository(self._session).in_period(user_id, start, end)

    async def fills_in_period(self, user_id: uuid.UUID, start: date, end: date) -> list[RoundFill]:
        return await RoundRepository(self._session).fills_in_period(user_id, start, end)

    async def trades_in_period(self, user_id: uuid.UUID, start: date, end: date) -> list[Trade]:
        rows = await self._session.scalars(
            select(Trade)
            .where(Trade.user_id == user_id, Trade.trade_date >= start, Trade.trade_date <= end)
            .order_by(Trade.trade_date, Trade.line_no, Trade.id)
        )
        return list(rows)

    async def asset_daily_in_period(
        self, user_id: uuid.UUID, start: date, end: date
    ) -> list[AssetDaily]:
        rows = await self._session.scalars(
            select(AssetDaily)
            .where(
                AssetDaily.user_id == user_id,
                AssetDaily.trade_date >= start,
                AssetDaily.trade_date <= end,
            )
            .order_by(AssetDaily.trade_date)
        )
        return list(rows)

    async def asset_build_state(self, user_id: uuid.UUID) -> AssetBuildState | None:
        return await self._session.scalar(
            select(AssetBuildState).where(AssetBuildState.user_id == user_id)
        )

    # ── 指纹聚合（§6.4）─────────────────────────────────────
    async def rounds_fingerprint_parts(self, user_id: uuid.UUID) -> dict[str, object]:
        count, max_rebuilt, max_id = (
            await self._session.execute(
                select(
                    func.count(),
                    func.max(Round.rebuilt_at),
                    func.max(Round.id),
                ).where(Round.user_id == user_id)
            )
        ).one()
        state = await self._session.scalar(
            select(RoundBuildState).where(RoundBuildState.user_id == user_id)
        )
        return {
            "count": int(count or 0),
            "max_rebuilt_at": max_rebuilt,
            "max_id": max_id,
            "state_last_rebuilt_at": state.last_rebuilt_at if state is not None else None,
        }

    async def fills_fingerprint_parts(self, user_id: uuid.UUID) -> dict[str, object]:
        count, max_id = (
            await self._session.execute(
                select(func.count(), func.max(RoundFill.id)).where(RoundFill.user_id == user_id)
            )
        ).one()
        state = await self._session.scalar(
            select(RoundBuildState).where(RoundBuildState.user_id == user_id)
        )
        return {
            "count": int(count or 0),
            "max_id": max_id,
            "state_last_rebuilt_at": state.last_rebuilt_at if state is not None else None,
        }

    async def trades_fingerprint_parts(self, user_id: uuid.UUID) -> dict[str, object]:
        count, deleted, max_updated, max_id = (
            await self._session.execute(
                select(
                    func.count(),
                    func.count(Trade.deleted_at),
                    func.max(Trade.updated_at),
                    func.max(Trade.id),
                ).where(Trade.user_id == user_id)
            )
        ).one()
        return {
            "count": int(count or 0),
            "deleted_count": int(deleted or 0),
            "max_updated_at": max_updated,
            "max_id": max_id,
        }

    async def market_bars_fingerprint(self, symbols: Sequence[str], start: date, end: date) -> str:
        """行情缓存内容指纹（只看本地 ``daily_bar``，不触发任何第三方请求）。"""
        ordered = tuple(sorted(set(symbols)))
        if not ordered or start > end:
            return sha256_hex(canonical_json({"symbols": [], "bars": []}))
        stmt = (
            select(
                DailyBar.symbol,
                DailyBar.trade_date,
                DailyBar.close_units,
                DailyBar.return_units,
            )
            .where(
                DailyBar.adjust == "",
                DailyBar.symbol.in_(ordered),
                DailyBar.trade_date >= start,
                DailyBar.trade_date <= end,
            )
            .order_by(DailyBar.symbol, DailyBar.trade_date)
        )
        rows = (await self._session.execute(stmt)).all()
        return sha256_hex(
            canonical_json(
                {
                    "symbols": list(ordered),
                    "bars": [[row[0], row[1].isoformat(), row[2], row[3]] for row in rows],
                }
            )
        )

    async def asset_daily_fingerprint_parts(self, user_id: uuid.UUID) -> dict[str, object]:
        count, max_updated, max_id = (
            await self._session.execute(
                select(
                    func.count(),
                    func.max(AssetDaily.updated_at),
                    func.max(AssetDaily.id),
                ).where(AssetDaily.user_id == user_id)
            )
        ).one()
        state = await self._session.scalar(
            select(AssetBuildState).where(AssetBuildState.user_id == user_id)
        )
        return {
            "count": int(count or 0),
            "max_updated_at": max_updated,
            "max_id": max_id,
            "state_updated_at": state.updated_at if state is not None else None,
            "state_version": state.version if state is not None else None,
            "state_definition_version": state.definition_version if state is not None else None,
        }


def now_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = ["MetricDatasetRepository", "MetricResultRepository"]
