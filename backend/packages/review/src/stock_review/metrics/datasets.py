"""视图装配（Period2-05，§4.3/§6.4）：查仓储 + 行情 → 冻结 ViewSet + 指纹。

一次请求只装配一次，所有指标共享；视图在会话外（已冻结成 tuple），计算阶段零 I/O。
行情源不可用/缺符号不抛 500，而是转成 `flags` 让门禁产出 blocked（fail-closed 但不 5xx）。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.ports import MarketDataSource

from stock_review.application.assets import ASSET_DEFINITION_VERSION
from stock_review.metrics.views import (
    AssetBuildView,
    AssetDailyView,
    BarView,
    BenchmarkView,
    FillView,
    RoundView,
    TradeView,
    ViewSet,
    calendar_of,
)
from stock_review.models import AssetBuildState, AssetDaily, Round, RoundFill
from stock_review.repositories.metrics import MetricDatasetRepository

DEFAULT_BENCHMARK = "hs300_tr"
_SCALE_RATIO_BOUND = 10**8


def _iso(value: Any) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    return None


def _round_view(row: Round) -> RoundView:
    return RoundView(
        id=row.id,
        security_code=row.security_code,
        status=row.status,
        open_date=row.open_date,
        close_date=row.close_date,
        quantity_units=row.quantity_units,
        cost_units=row.cost_units,
        proceeds_units=row.proceeds_units,
        dividend_units=row.dividend_units,
        pnl_units=row.pnl_units,
        return_rate_units=row.return_rate_units,
        holding_days=row.holding_days,
        data_error=row.data_error,
        oversell_units=row.oversell_units,
        rebuilt_at=row.rebuilt_at,
    )


def _fill_view(row: RoundFill) -> FillView:
    return FillView(
        round_id=row.round_id,
        sequence=row.sequence,
        buy_trade_id=row.buy_trade_id,
        sell_trade_id=row.sell_trade_id,
        quantity_units=row.quantity_units,
        buy_cost_units=row.buy_cost_units,
        sell_proceeds_units=row.sell_proceeds_units,
        dividend_units=row.dividend_units,
        pnl_units=row.pnl_units,
        open_date=row.open_date,
        close_date=row.close_date,
        holding_days=row.holding_days,
    )


def _trade_view(row: Any) -> TradeView:
    return TradeView(
        id=row.id,
        trade_date=row.trade_date,
        line_no=row.line_no or 0,
        side=row.side,
        category=row.category or "",
        security_code=row.security_code,
        quantity_units=row.quantity_units,
        price_units=row.price_units,
        amount_units=row.amount_units,
        commission_units=row.commission_units,
        stamp_tax_units=row.stamp_tax_units,
        other_fee_units=row.other_fee_units,
        cash_balance_units=row.cash_balance_units,
        deleted_at=row.deleted_at,
        version=row.version,
    )


def _asset_daily_view(row: AssetDaily) -> AssetDailyView:
    return AssetDailyView(
        trade_date=row.trade_date,
        cash_units=row.cash_units,
        market_value_units=row.market_value_units,
        total_assets_units=row.total_assets_units,
        holding_cost_units=row.holding_cost_units,
        unrealized_pnl_units=row.unrealized_pnl_units,
        holding_count=row.holding_count,
        cashflow_units=row.cashflow_units,
        prev_total_assets_units=row.prev_total_assets_units,
        daily_pnl_units=row.daily_pnl_units,
        daily_return_units=row.daily_return_units,
        status=row.status,
        definition_version=row.definition_version,
    )


def _asset_build_view(
    state: AssetBuildState | None,
    *,
    market_as_of: date | None,
    today: date,
) -> AssetBuildView:
    if state is None or state.last_built_through is None:
        if state is not None and state.last_status == "blocked":
            return AssetBuildView(
                state="blocked",
                updated_at=state.updated_at,
                version=state.version,
                last_status=state.last_status,
                data_as_of=None,
                definition_version=state.definition_version,
                last_built_through=None,
            )
        return AssetBuildView(
            state="missing",
            updated_at=state.updated_at if state is not None else None,
            version=state.version if state is not None else None,
            last_status=state.last_status if state is not None else None,
            data_as_of=None,
            definition_version=state.definition_version if state is not None else None,
            last_built_through=None,
        )
    stale = (
        state.definition_version != ASSET_DEFINITION_VERSION
        or state.data_as_of is None
        or (market_as_of is not None and market_as_of > state.data_as_of)
        or today > state.last_built_through
    )
    return AssetBuildView(
        state="stale" if stale else "current",
        updated_at=state.updated_at,
        version=state.version,
        last_status=state.last_status,
        data_as_of=state.data_as_of,
        definition_version=state.definition_version,
        last_built_through=state.last_built_through,
    )


async def _bars_for_symbols(
    market_data: MarketDataSource,
    symbols: tuple[str, ...],
    start: date,
    end: date,
) -> tuple[tuple[BarView, ...], bool]:
    if not symbols:
        return (), False
    try:
        bars = await market_data.get_daily_bars(symbols, start, end)
    except ApiError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return (), True
        raise
    views = tuple(
        BarView(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            close_units=bar.close_units,
            return_units=bar.return_units,
        )
        for bar in bars
    )
    return views, False


async def load_viewset(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    period_start: date,
    period_end: date,
    benchmark: str | None,
    market_data: MarketDataSource,
    clock: Clock,
) -> ViewSet:
    """装配 8 视图 + 指纹；`benchmark` 为 None 时不依赖基准（def 自行声明）。"""
    from stock_review.metrics.hash import inputs_hash

    repo = MetricDatasetRepository(session)
    rounds_raw = await repo.rounds_in_period(user_id, period_start, period_end)
    fills_raw = await repo.fills_in_period(user_id, period_start, period_end)
    trades_raw = await repo.trades_in_period(user_id, period_start, period_end)
    asset_raw = await repo.asset_daily_in_period(user_id, period_start, period_end)
    state = await repo.asset_build_state(user_id)

    today = clock.today()
    market_as_of = market_data.as_of if market_data.capabilities.supports_daily_bars else None
    asset_build = _asset_build_view(state, market_as_of=market_as_of, today=today)

    symbols = tuple(
        sorted(
            {row.security_code for row in rounds_raw} | {row.security_code for row in trades_raw}
        )
    )
    bars, bars_missing = await _bars_for_symbols(market_data, symbols, period_start, period_end)

    benchmark_series: tuple[BenchmarkView, ...] = ()
    benchmark_missing = False
    if benchmark:
        raw_benchmark, benchmark_missing = await _bars_for_symbols(
            market_data, (benchmark,), period_start, period_end
        )
        provenance = market_data.provenance if not benchmark_missing else ""
        benchmark_series = tuple(
            BenchmarkView(
                trade_date=bar.trade_date,
                return_units=bar.return_units,
                available=not benchmark_missing,
                provenance=provenance,
            )
            for bar in raw_benchmark
        )

    calendar_days = calendar_of(period_start, period_end)
    observed_days = len({bar.trade_date for bar in bars})

    # ── 指纹（§6.4）────────────────────────────────────────
    fingerprints: dict[str, str] = {}
    rounds_parts = await repo.rounds_fingerprint_parts(user_id)
    fingerprints["rounds"] = inputs_hash(
        {"dataset": "rounds", **{key: _iso(value) or value for key, value in rounds_parts.items()}}
    )
    fills_parts = await repo.fills_fingerprint_parts(user_id)
    fingerprints["fills"] = inputs_hash(
        {"dataset": "fills", **{key: _iso(value) or value for key, value in fills_parts.items()}}
    )
    trades_parts = await repo.trades_fingerprint_parts(user_id)
    fingerprints["trades"] = inputs_hash(
        {"dataset": "trades", **{key: _iso(value) or value for key, value in trades_parts.items()}}
    )
    asset_parts = await repo.asset_daily_fingerprint_parts(user_id)
    fingerprints["asset_daily"] = inputs_hash(
        {
            "dataset": "asset_daily",
            **{key: _iso(value) or value for key, value in asset_parts.items()},
        }
    )
    fingerprints["asset_build"] = inputs_hash(
        {
            "dataset": "asset_build",
            "state": asset_build.state,
            "updated_at": _iso(asset_build.updated_at),
            "version": asset_build.version,
            "last_status": asset_build.last_status,
            "data_as_of": _iso(asset_build.data_as_of),
            "definition_version": asset_build.definition_version,
        }
    )
    market_fingerprint = inputs_hash(
        {
            "dataset": "bars_daily",
            "provenance": market_data.provenance if market_as_of is not None else None,
            "as_of": _iso(market_as_of),
            "symbols": list(symbols),
            "bars": len(bars),
        }
    )
    fingerprints["bars_daily"] = market_fingerprint
    fingerprints["benchmark_series"] = inputs_hash(
        {
            "dataset": "benchmark_series",
            "benchmark": benchmark,
            "provenance": market_data.provenance if benchmark else None,
            "as_of": _iso(market_as_of),
            "available": not benchmark_missing,
            "bars": len(benchmark_series),
        }
    )
    fingerprints["calendar"] = inputs_hash({"dataset": "calendar", "assumption": "weekday_proxy"})

    # ── 门禁 flags（§6.3）──────────────────────────────────
    ratio_out_of_range = any(
        abs(row.return_rate_units) > _SCALE_RATIO_BOUND for row in rounds_raw
    ) or any(
        row.daily_return_units is not None and abs(row.daily_return_units) > _SCALE_RATIO_BOUND
        for row in asset_raw
    )
    density = (
        (Decimal(observed_days) / Decimal(calendar_days.trading_days))
        if calendar_days.trading_days
        else Decimal(0)
    )
    flags: dict[str, bool] = {
        "dataset_missing:bars_daily": bars_missing,
        "dataset_missing:benchmark_series": benchmark_missing,
        "asset_build_missing": asset_build.state == "missing",
        "asset_build_blocked": asset_build.state == "blocked",
        "asset_build_stale": asset_build.state == "stale",
        "market_asof_lag": bool(market_as_of is not None and market_as_of < min(today, period_end)),
        "data_out_of_range": ratio_out_of_range,
        "bar_density_low": bool(density < Decimal("0.8")),
        "degraded_rows": any(row.status in {"degraded", "blocked"} for row in asset_raw),
        "recon_blocked": any(row.status == "blocked" for row in asset_raw),
    }

    return ViewSet(
        rounds=tuple(_round_view(row) for row in rounds_raw),
        fills=tuple(_fill_view(row) for row in fills_raw),
        trades=tuple(_trade_view(row) for row in trades_raw),
        asset_daily=tuple(_asset_daily_view(row) for row in asset_raw),
        asset_build=asset_build,
        bars=bars,
        benchmark=benchmark_series,
        calendar=calendar_days,
        fingerprints=fingerprints,
        flags=flags,
        market_as_of=market_as_of,
        market_provenance=market_data.provenance,
        observed_trading_days=observed_days,
    )


__all__ = ["DEFAULT_BENCHMARK", "load_viewset"]
