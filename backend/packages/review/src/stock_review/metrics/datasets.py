"""视图装配（Period2-05，§4.3/§6.4）：查仓储 + 行情 → 冻结 ViewSet + 指纹。

一次请求只装配一次，所有指标共享；视图在会话外（已冻结成 tuple），计算阶段零 I/O。
行情源不可用/缺符号不抛 500，而是转成 `flags` 让门禁产出 blocked（fail-closed 但不 5xx）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.ports import MarketDataSource
from stock_platform.symbols import classify_symbol

from stock_review.application.assets import ASSET_DEFINITION_VERSION, latest_workday
from stock_review.application.bond_alias import build_bond_alias, canonical_code
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
# 组合日收益越界阈值（标度 1e8）：|日收益| > 100% 基本是标度/桥接错误。
_SCALE_RATIO_BOUND = 10**8
# 回合收益越界阈值（标度 1e8）：新股首日/借壳/多年长持可合法超过 100%（如 IPO 4 日 +153%），
# 只有 |回合收益| > 100 倍才判定为标度污染。阈值过紧会让一个合法极端回合污染整批指标
# （data_out_of_range 是全局 flag，2026-10-30 线上事故：单只新股致年度 10/10 指标 unreliable）。
_ROUND_RETURN_BOUND = 10**10
# 风险/择时指标需要「截至期末的滚动窗口」而不是自然月内样本：资产日视图向前多取
# 120 自然日（≈80 个交易日），供 beta 的 60TD 配对与 drawdown 的 60TD 窗口使用。
_ASSET_LOOKBACK_DAYS = 120
# 选股类指标（BHAR 20/60）需要「买入后前向 N 个交易日」的行情；若只加载到 period_end，
# 期初买入的样本永远凑不齐前向窗口（n=0）。向前多取 120 自然日（≈80 个交易日），
# 上限今天；回放模式按夹具自然截断。
BAR_FORWARD_DAYS = 120
# 伪代码/非证券行：不参与行情请求（CASH 是现金余额占位，货基由 money_fund 列标记）。
_PSEUDO_CODES = frozenset({"CASH"})

logger = logging.getLogger(__name__)


def out_of_range_rows(
    rounds_raw: Sequence[Round], asset_period_raw: Sequence[AssetDaily]
) -> tuple[str, ...]:
    """返回触发 data_out_of_range 的行级明细（供日志/排障）；空 tuple = 未越界。

    判定分两级：回合收益用 `_ROUND_RETURN_BOUND`（合法极端收益不误伤），
    组合日收益用 `_SCALE_RATIO_BOUND`（单日 ±100% 基本是数据错误）。
    """
    rows: list[str] = []
    for round_row in rounds_raw:
        if abs(round_row.return_rate_units) > _ROUND_RETURN_BOUND:
            rows.append(
                f"round {round_row.security_code} {round_row.open_date}~{round_row.close_date} "
                f"return_rate_units={round_row.return_rate_units}"
            )
    for asset_row in asset_period_raw:
        if (
            asset_row.daily_return_units is not None
            and abs(asset_row.daily_return_units) > _SCALE_RATIO_BOUND
        ):
            rows.append(
                f"asset_daily {asset_row.trade_date} daily_return_units={asset_row.daily_return_units}"
            )
    return tuple(rows)


# live 模式下总收益指数暂以对应 ETF 作为代理（akshare 无 hs300_tr 全收益序列）；
# replay 模式保留原 symbol，避免破坏回放夹具。接入正式全收益指数后删除本表。
_BENCHMARK_LIVE_PROXY: dict[str, str] = {
    "hs300_tr": "510300",
    "zz500_tr": "510500",
    "csi1000_tr": "512100",
}


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
        security_name=row.security_name or "",
        money_fund_balance_units=row.money_fund_balance_units,
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
    # 与 GET /assets 的状态判定同口径（assets.py rule 6/7）：周六/周日不判 stale——
    # 资产表周末不写行，last_built_through 停在周五；行情覆盖日同理对齐工作日。
    stale = (
        state.definition_version != ASSET_DEFINITION_VERSION
        or state.data_as_of is None
        or (market_as_of is not None and latest_workday(market_as_of) > state.data_as_of)
        or latest_workday(today) > state.last_built_through
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


def _bar_views(bars: Any) -> tuple[BarView, ...]:
    return tuple(
        BarView(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            close_units=bar.close_units,
            return_units=bar.return_units,
        )
        for bar in bars
    )


async def _bars_for_symbols(
    market_data: MarketDataSource,
    symbols: tuple[str, ...],
    start: date,
    end: date,
) -> tuple[tuple[BarView, ...], bool, tuple[str, ...]]:
    """批量取 bars；任一不支持时逐 symbol 重试，避免一个坏代码杀死整批指标。

    返回 ``(bars, all_missing, missing_symbols)``：``all_missing`` 仅当所有 symbol 都
    取不到时为 True（门禁 blocked）；部分缺失不阻断，由指标按实际 bars 计算并由 n 体现。
    """
    if not symbols:
        return (), False, ()
    try:
        bars = await market_data.get_daily_bars(symbols, start, end)
    except ApiError as exc:
        if exc.code is not ErrorCode.NOT_FOUND:
            raise
        collected: list[Any] = []
        missing: list[str] = []
        for symbol in symbols:
            try:
                part = await market_data.get_daily_bars([symbol], start, end)
            except ApiError as inner:
                if inner.code is not ErrorCode.NOT_FOUND:
                    raise
                missing.append(symbol)
                continue
            collected.extend(part)
        missing_tuple = tuple(missing)
        return _bar_views(collected), len(missing_tuple) == len(symbols), missing_tuple
    return _bar_views(bars), False, ()


def bars_window(period_end: date, today: date) -> tuple[date, date]:
    """行情读取窗口的结束日：期末 + 前向窗口，且不超过今天。"""
    return period_end, min(today, period_end + timedelta(days=BAR_FORWARD_DAYS))


def benchmark_fetch_symbol(benchmark: str, market_data: MarketDataSource) -> str:
    """live 模式下用 ETF 代理总收益指数；replay 模式保留夹具 symbol。"""
    if market_data.capabilities.mode == "replay":
        return benchmark
    return _BENCHMARK_LIVE_PROXY.get(benchmark, benchmark)


def metric_symbols(rounds_raw: list[Round], trades_raw: list[Any]) -> tuple[str, ...]:
    """需要请求行情的标的：过滤伪代码/货基，按 bond alias 归并，并剔除不支持品种。"""
    bond_alias, _unresolved = build_bond_alias(
        (row.security_code, row.security_name) for row in trades_raw
    )
    wanted: set[str] = set()
    for row in rounds_raw:
        if row.security_code and row.security_code not in _PSEUDO_CODES:
            wanted.add(row.security_code)
    for row in trades_raw:
        code = row.security_code
        if not code or code in _PSEUDO_CODES or row.money_fund_balance_units is not None:
            continue
        wanted.add(canonical_code(bond_alias, code))
    return tuple(sorted(code for code in wanted if classify_symbol(code) is not None))


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
    # 资产日多取一段回看：beta/drawdown 用「期末回看 60 个交易日」而不是自然月内。
    asset_raw = await repo.asset_daily_in_period(
        user_id, period_start - timedelta(days=_ASSET_LOOKBACK_DAYS), period_end
    )
    asset_period_raw = [row for row in asset_raw if period_start <= row.trade_date <= period_end]
    state = await repo.asset_build_state(user_id)

    today = clock.today()
    symbols = metric_symbols(rounds_raw, trades_raw)
    # 行情加载区间：期初 → min(今天, 期末+前向窗口)，供 BHAR 前向收益使用。
    bar_from = period_start
    bar_to = bars_window(period_end, today)[1]
    bars, bars_missing, bars_missing_symbols = await _bars_for_symbols(
        market_data, symbols, bar_from, bar_to
    )
    # 只读缓存模式下 market_data.as_of 可能是进程启动时的近似值；用实际 bars 的最大日期，
    # 让 asset_build stale 判定与 data_as_of 一致。
    market_as_of = (
        max((bar.trade_date for bar in bars), default=None)
        if market_data.capabilities.supports_daily_bars
        else None
    ) or (market_data.as_of if market_data.capabilities.supports_daily_bars else None)
    asset_build = _asset_build_view(state, market_as_of=market_as_of, today=today)

    benchmark_series: tuple[BenchmarkView, ...] = ()
    benchmark_missing = False
    benchmark_fetch = None
    if benchmark:
        benchmark_fetch = benchmark_fetch_symbol(benchmark, market_data)
        raw_benchmark, benchmark_missing, _ = await _bars_for_symbols(
            market_data, (benchmark_fetch,), bar_from, bar_to
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
    # bar_density 只统计「本期」行情覆盖，不被前向窗口放大。
    period_bar_days = {
        bar.trade_date for bar in bars if period_start <= bar.trade_date <= period_end
    }
    bar_observed_days = len(period_bar_days)
    # 日频 sample_status 的观察天数：取行情/资产回看中较大的交易日集合（beta/drawdown
    # 依赖回看窗口；bar_density 仍只用 bar_observed_days，避免被回看放大）。
    asset_trading_days = len({row.trade_date for row in asset_raw if row.trade_date.weekday() < 5})
    observed_days = max(bar_observed_days, asset_trading_days)

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
            "missing_symbols": list(bars_missing_symbols),
        }
    )
    fingerprints["bars_daily"] = market_fingerprint
    fingerprints["benchmark_series"] = inputs_hash(
        {
            "dataset": "benchmark_series",
            "benchmark": benchmark,
            "fetch_symbol": benchmark_fetch,
            "provenance": market_data.provenance if benchmark else None,
            "as_of": _iso(market_as_of),
            "available": not benchmark_missing,
            "bars": len(benchmark_series),
        }
    )
    fingerprints["calendar"] = inputs_hash({"dataset": "calendar", "assumption": "weekday_proxy"})

    # ── 门禁 flags（§6.3）──────────────────────────────────
    out_of_range = out_of_range_rows(rounds_raw, asset_period_raw)
    if out_of_range:
        logger.warning(
            "metrics viewset data_out_of_range: user=%s period=%s~%s rows=%s",
            user_id,
            period_start,
            period_end,
            list(out_of_range),
        )
    density = (
        (Decimal(bar_observed_days) / Decimal(calendar_days.trading_days))
        if calendar_days.trading_days
        else Decimal(0)
    )
    flags: dict[str, bool] = {
        "dataset_missing:bars_daily": bars_missing,
        "dataset_missing:benchmark_series": benchmark_missing,
        "asset_build_missing": asset_build.state == "missing",
        "asset_build_blocked": asset_build.state == "blocked",
        "asset_build_stale": asset_build.state == "stale",
        # 阈值对齐工作日：周末/节假日行情不会前进，周五缓存不应在周六判 lag。
        "market_asof_lag": bool(
            market_as_of is not None
            and latest_workday(market_as_of) < latest_workday(min(today, period_end))
        ),
        "data_out_of_range": bool(out_of_range),
        "bar_density_low": bool(density < Decimal("0.8")),
        # 部分标的缺行情：不阻断（指标按可得样本算，n 体现覆盖），留 flag 供排障/文案。
        "bars_partial": bool(bars_missing_symbols) and not bars_missing,
        "degraded_rows": any(row.status in {"degraded", "blocked"} for row in asset_period_raw),
        "recon_blocked": any(row.status == "blocked" for row in asset_period_raw),
    }
    active_flags = sorted(name for name, on in flags.items() if on)
    if active_flags:
        logger.info(
            "metrics viewset flags: user=%s period=%s~%s flags=%s",
            user_id,
            period_start,
            period_end,
            active_flags,
        )

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


__all__ = ["DEFAULT_BENCHMARK", "load_viewset", "out_of_range_rows"]
