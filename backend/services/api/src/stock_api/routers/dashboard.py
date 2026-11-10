"""Dashboard 聚合端点（API-084/095，Period2-12）：资产概览 + 历史概览 + 账单旗标月度趋势。

* `GET /dashboard`（`getDashboard`）：**只读**，不落库、不触发构建/job；
* `GET /dashboard/trade-flags`（`getDashboardTradeFlags`）：按「月 × 旗标码」聚合
  `review.trade_flag`，只读当前定义版本的落库结果（不重算）；「完整月」以服务端
  业务日（东八区）为准，最新完整月固定为今天所在月的前一个月；
* 当前快照取最新 `asset_daily` 行（`trade_date <= today`），历史概览为全历史；
* 年度指标区间（`metrics_period`/`_start`/`_end`）= 该快照日所在年份年初 → 快照日；
* 所有金额/数量/比率在本层转**定点字符串**；blocked/missing 字段显式 `null`；
* 年度指标由前端按 `metrics_period` 懒加载 `/metrics`，本端点不内嵌。

跨账号/不存在一律 404，admin 不可读业务数据（身份越权 → 403）。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.values import Money, Ratio, format_quantity
from stock_platform.tenant import TenantContext
from stock_review.trade_flags import (
    MonthlyReturn,
    TradeFlagMonthlySummary,
    monthly_trade_flag_summary,
)

from stock_api.deps import get_market_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    AssetBuildStateOut,
    DashboardAssetOverviewOut,
    DashboardHistoryOverviewOut,
    DashboardHoldingOut,
    DashboardLinksOut,
    DashboardMaxDrawdownOut,
    DashboardMissingOut,
    DashboardMonthlyReturnOut,
    DashboardOut,
    DashboardRoundOut,
    DashboardTradeCountOut,
    DashboardTradeFlagCountOut,
    DashboardTradeFlagMonthOut,
    DashboardTradeFlagSeriesOut,
    DashboardTradeFlagsOut,
)

router = APIRouter(tags=["dashboard"], route_class=StrictQueryRoute)

MONTHS_BASIS = (
    "完整月 = 服务端业务日（东八区）所在月之前、已自然结束的日历月；"
    "最新完整月固定为上一个月。旗标数量为当月命中该旗标的账单数（同一账单可命中多个旗标）。"
)

TOTAL_LABELS: dict[str, str] = {"buy": "买入笔数", "sell": "卖出笔数"}

RETURN_BASIS = (
    "单月收益 = 资产日表逐日收益（TWR，已剔除出入金）在自然月内复利；"
    "无可用日收益的月份显式 0 高度、数值为 null（不伪造 0 收益）。"
)

RETURN_LABELS: dict[str, str] = {"pos": "正收益", "neg": "负收益", "none": "无数据"}


def _bp(ratio_units: int) -> int:
    """比率标度整数（scale=8）→ 基点整数（四舍五入到 1bp，供图表柱高）。"""
    return int((Decimal(ratio_units) / Decimal(10_000)).to_integral_value(rounding=ROUND_HALF_UP))


def _bp_pct_text(bp: int) -> str:
    """基点整数 → 百分比定点串（"2.35%" / "-1.20%" / "0.00%"）。"""
    sign = "-" if bp < 0 else ""
    magnitude = abs(bp)
    return f"{sign}{magnitude // 100}.{magnitude % 100:02d}%"


def _monthly_return_out(item: MonthlyReturn) -> DashboardMonthlyReturnOut:
    """review DTO → API 边界模型：符号/百分比/展示串/说明全部在服务端生成。"""
    if item.value_units is None:
        return DashboardMonthlyReturnOut(
            month=item.month,
            value_bp=0,
            pct=None,
            display=None,
            sign="none",
            available=False,
            days=item.days,
            partial=False,
            note="当月无可用资产日收益",
        )
    bp = _bp(item.value_units)
    sign = "pos" if bp > 0 else "neg" if bp < 0 else "flat"
    pct = _bp_pct_text(bp)
    return DashboardMonthlyReturnOut(
        month=item.month,
        value_bp=bp,
        pct=pct,
        display=pct if bp <= 0 else f"+{pct}",
        sign=sign,
        available=True,
        days=item.days,
        partial=item.partial,
        note="资产表未覆盖整月，收益为已覆盖区间" if item.partial else None,
    )


def _pct_2dp(ratio_units: int | None) -> str | None:
    """比率标度整数 → 两位小数百分比字符串（避免出现 "-0.00%"）。"""
    if ratio_units is None:
        return None
    text = Ratio(ratio_units).as_pct(2)
    return "0.00%" if text == "-0.00%" else text


def _money(value: int | None) -> str | None:
    return None if value is None else str(Money(value))


def _ratio(value: int | None) -> str | None:
    return None if value is None else str(Ratio(value))


def _money_display(value: int | None) -> str | None:
    """金额 → 千分位展示串（展示层格式化；raw 定点串仍保留）。"""
    if value is None:
        return None
    text = str(Money(value))
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    whole, _, fraction = text.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    grouped = ",".join(groups)
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


def _round_out(dto: Any) -> DashboardRoundOut:
    return DashboardRoundOut(
        round_id=dto.round_id,
        security_code=dto.security_code,
        security_name=dto.security_name,
        close_date=dto.close_date,
        pnl=str(Money(dto.pnl_units)),
        return_rate=str(Ratio(dto.return_rate_units)),
        return_rate_pct=_pct_2dp(dto.return_rate_units) or "0.00%",
        holding_days=dto.holding_days,
        pnl_display=_money_display(dto.pnl_units),
    )


def _to_out(result: Any) -> DashboardOut:
    """review 聚合 DTO → API 边界模型（全部格式化在服务端）。"""
    asset = result.asset
    history = result.history
    drawdown = history.max_drawdown
    period = result.legacy_period
    return DashboardOut(
        as_of=result.as_of,
        data_as_of=result.data_as_of,
        legacy_period=period,
        metrics_period=result.metrics_period,
        metrics_period_start=result.metrics_period_start,
        metrics_period_end=result.metrics_period_end,
        status=result.status,
        build_state=result.build_state,
        definition_version=result.definition_version,
        build=AssetBuildStateOut.model_validate(result.build, from_attributes=True),
        asset_overview=DashboardAssetOverviewOut(
            status=asset.status,
            holdings=[
                DashboardHoldingOut(
                    security_code=item.security_code,
                    security_name=item.security_name,
                    quantity=format_quantity(item.quantity_units),
                    cost=_money(item.cost_units) or "0.00",
                    market_value=_money(item.market_value_units),
                    weight_pct=_ratio(item.weight_ratio_units),
                    cost_display=_money_display(item.cost_units),
                    market_value_display=_money_display(item.market_value_units),
                    weight_display=_pct_2dp(item.weight_ratio_units),
                )
                for item in asset.holdings
            ],
            market_value=_money(asset.market_value_units),
            cash=_money(asset.cash_units),
            money_fund=_money(asset.money_fund_units),
            balance=(
                None
                if asset.cash_units is None or asset.money_fund_units is None
                else _money(asset.cash_units + asset.money_fund_units)
            ),
            total_assets=_money(asset.total_assets_units),
            position_pct=_pct_2dp(asset.position_ratio_units),
            total_profit=_money(asset.total_profit_units),
            max_invested_cost=_money(asset.max_invested_cost_units),
            max_cost_return_pct=_pct_2dp(asset.max_cost_return_ratio_units),
            total_fees=_money(asset.total_fees_units),
            bill_start_date=asset.bill_start_date,
            bill_end_date=asset.bill_end_date,
            initial_capital=_money(asset.initial_capital_units),
            initial_capital_display=_money_display(asset.initial_capital_units),
            annualized_return=_ratio(asset.annualized_return_ratio_units),
            annualized_return_pct=_pct_2dp(asset.annualized_return_ratio_units),
            market_value_display=_money_display(asset.market_value_units),
            cash_display=_money_display(asset.cash_units),
            money_fund_display=_money_display(asset.money_fund_units),
            balance_display=(
                None
                if asset.cash_units is None or asset.money_fund_units is None
                else _money_display(asset.cash_units + asset.money_fund_units)
            ),
            total_assets_display=_money_display(asset.total_assets_units),
            total_profit_display=_money_display(asset.total_profit_units),
            max_invested_cost_display=_money_display(asset.max_invested_cost_units),
            total_fees_display=_money_display(asset.total_fees_units),
        ),
        history_overview=DashboardHistoryOverviewOut(
            status=history.status,
            closed_security_count=history.closed_security_count,
            closed_round_count=history.closed_round_count,
            win_count=history.win_count,
            loss_count=history.loss_count,
            flat_count=history.flat_count,
            win_pct=_pct_2dp(history.win_ratio_units),
            loss_pct=_pct_2dp(history.loss_ratio_units),
            flat_pct=_pct_2dp(history.flat_ratio_units),
            max_profit=_money(history.max_profit_units),
            max_profit_display=_money_display(history.max_profit_units),
            max_profit_round=(
                None if history.max_profit_round is None else _round_out(history.max_profit_round)
            ),
            max_profit_cost_pct=_pct_2dp(history.max_profit_cost_ratio_units),
            top_rounds=[_round_out(item) for item in history.top_rounds],
            bottom_rounds=[_round_out(item) for item in history.bottom_rounds],
            top_holding_rounds=[_round_out(item) for item in history.top_holding_rounds],
            top_trade_counts=[
                DashboardTradeCountOut(
                    security_code=item.security_code,
                    security_name=item.security_name,
                    buy_count=item.buy_count,
                    sell_count=item.sell_count,
                    trade_count=item.trade_count,
                    pnl=_money(item.pnl_units),
                    pnl_display=_money_display(item.pnl_units),
                    total_fees=_money(item.total_fees_units) or "0.00",
                    total_fees_display=_money_display(item.total_fees_units),
                )
                for item in history.top_trade_counts
            ],
            max_drawdown=DashboardMaxDrawdownOut(
                from_date=drawdown.from_date,
                to_date=drawdown.to_date,
                duration_days=drawdown.duration_days,
                duration_obs=drawdown.duration_obs,
                recovery_days=drawdown.recovery_days,
                recovery_censored=drawdown.recovery_censored,
                mdd_pct=_pct_2dp(drawdown.mdd_units),
                n_obs=drawdown.n_obs,
                sample_status=drawdown.sample_status,
                reason=drawdown.reason,
            ),
        ),
        missing=[
            DashboardMissingOut(
                field=item.field,
                code=item.code,
                reason=item.reason,
                what_to_do=item.what_to_do,
            )
            for item in result.missing
        ],
        links=DashboardLinksOut(
            assets="/assets",
            rounds="/rounds",
            metrics=f"/metrics?period={result.metrics_period}",
            reports="/reports",
            exports="/exports",
            attribution=f"/attribution?period={period}",
            sectors=f"/sectors?taxonomy=industry&level=1&as_of={result.as_of.isoformat()}",
        ),
        assumptions=result.assumptions,
        inputs_hash=result.inputs_hash,
    )


@router.get("/dashboard", response_model=DashboardOut, operation_id="getDashboard")
async def get_dashboard_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> DashboardOut:
    """API-084：Dashboard 两板块聚合（只读；年度指标按 metrics_period 懒加载）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    market_data = get_market_data(request)

    from stock_review.application.dashboard import build_dashboard

    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await build_dashboard(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=market_data,
        )
    return _to_out(result)


def _to_flags_out(result: TradeFlagMonthlySummary) -> DashboardTradeFlagsOut:
    def _series(items: Any) -> list[DashboardTradeFlagSeriesOut]:
        return [
            DashboardTradeFlagSeriesOut(
                code=item.code,
                label_zh=item.label_zh,
                label_en=item.label_en,
                side=item.side,
                lookahead=item.lookahead,
            )
            for item in items
        ]

    returns_by_month = {item.month: item for item in result.returns}

    return DashboardTradeFlagsOut(
        window_months=result.window_months,
        start_month=result.start_month,
        end_month=result.end_month,
        latest_complete_month=result.latest_complete_month,
        months_basis=MONTHS_BASIS,
        definition_version=result.definition_version,
        has_data=result.has_data,
        has_flag_data=result.has_flag_data,
        has_state_data=result.has_state_data,
        series=_series(result.series),
        state_series=_series(result.state_series),
        total_labels=dict(TOTAL_LABELS),
        items=[
            DashboardTradeFlagMonthOut(
                month=item.month,
                label=item.month,
                buy_count=item.buy_count,
                sell_count=item.sell_count,
                flags=[
                    DashboardTradeFlagCountOut(code=code, count=count)
                    for code, count in item.flag_counts.items()
                ],
                states=[
                    DashboardTradeFlagCountOut(code=code, count=count)
                    for code, count in item.state_counts.items()
                ],
                monthly_return=_monthly_return_out(returns_by_month[item.month]),
            )
            for item in result.items
        ],
        months_without_data=list(result.months_without_data),
        has_return_data=result.has_return_data,
        return_basis=RETURN_BASIS,
        return_labels=dict(RETURN_LABELS),
    )


@router.get(
    "/dashboard/trade-flags",
    response_model=DashboardTradeFlagsOut,
    operation_id="getDashboardTradeFlags",
)
async def get_dashboard_trade_flags_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    months: Annotated[int, Query()] = 6,
) -> DashboardTradeFlagsOut:
    """API-095：账单旗标月度聚合（最近 6/12/24 个完整月，只读）。"""
    if months not in (6, 12, 24):
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="months 只支持 6/12/24")
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await monthly_trade_flag_summary(
            session,
            user_id,
            window_months=months,
            clock=state.clock,
        )
    return _to_flags_out(result)


__all__ = ["router"]
