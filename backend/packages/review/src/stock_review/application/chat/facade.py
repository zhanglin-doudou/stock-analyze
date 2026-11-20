"""`ChatQueryFacade`：助手工具的唯一只读入口。

所有工具显式带 user_id；不做写操作、不触发构建。工具异常收敛为 `ToolResult(error)`，
由编排层决定降级，不让单个工具拖垮整轮。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.chat.dto import ToolResult
from stock_contracts.chat.enums import ToolName
from stock_contracts.clock import Clock
from stock_platform.config import Settings
from stock_platform.ports import MarketDataSource, SectorDataSource

from . import assets, attribution, metrics, nav, portfolio, quality, rounds, sectors, trades
from ._common import ToolContext, failed


class ChatQueryFacade:
    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        clock: Clock,
        market_data: MarketDataSource,
        sector_data: SectorDataSource,
        settings: Settings,
    ) -> None:
        self._ctx = ToolContext(
            session=session,
            user_id=user_id,
            clock=clock,
            market_data=market_data,
            sector_data=sector_data,
            settings=settings,
        )
        self._seq = 0

    def _next_prefix(self) -> str:
        self._seq += 1
        return f"c{self._seq}_"

    async def invoke(self, tool: ToolName, args: Mapping[str, Any]) -> ToolResult:
        prefix = self._next_prefix()
        try:
            if tool is ToolName.ASSETS_QUERY:
                return await assets.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.TRADES_QUERY:
                return await trades.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.ROUNDS_QUERY:
                return await rounds.run_list(self._ctx, args, prefix=prefix)
            if tool is ToolName.ROUND_DETAIL:
                return await rounds.run_detail(self._ctx, args, prefix=prefix)
            if tool is ToolName.PORTFOLIO_SNAPSHOT:
                return await portfolio.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.PERFORMANCE_METRICS:
                return await metrics.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.ATTRIBUTION_SUMMARY:
                return await attribution.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.NAV_SUMMARY:
                return await nav.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.SECTOR_EXPOSURE:
                return await sectors.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.DATA_QUALITY:
                return await quality.run(self._ctx, args, prefix=prefix)
            if tool is ToolName.METRIC_EXPLAIN:
                return await metrics.run_explain(self._ctx, args, prefix=prefix)
            raise ValueError(f"未注册的工具: {tool}")
        except Exception as exc:
            return failed(tool, exc)
