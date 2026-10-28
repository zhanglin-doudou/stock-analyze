"""Dashboard 聚合端点（API-084，Period2-12）：资产概览 + 历史概览。

* `GET /dashboard`（`getDashboard`）：**只读**，不落库、不触发构建/job；
* 当前快照取最新 `asset_daily` 行（`trade_date <= today`），历史概览为全历史；
* 所有金额/数量/比率在本层转**定点字符串**；blocked/missing 字段显式 `null`；
* 旧五卡（月度口径）由前端按 `legacy_period` 懒加载 `/metrics`，本端点不内嵌。

跨账号/不存在一律 404，admin 不可读业务数据（身份越权 → 403）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from stock_core.values import Money, Qty, Ratio
from stock_platform.tenant import TenantContext

from stock_api.deps import get_market_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    DashboardAssetOverviewOut,
    DashboardHistoryOverviewOut,
    DashboardHoldingOut,
    DashboardLinksOut,
    DashboardMaxDrawdownOut,
    DashboardMissingOut,
    DashboardOut,
    DashboardRoundOut,
)

router = APIRouter(tags=["dashboard"], route_class=StrictQueryRoute)


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
        status=result.status,
        build_state=result.build_state,
        definition_version=result.definition_version,
        asset_overview=DashboardAssetOverviewOut(
            status=asset.status,
            holdings=[
                DashboardHoldingOut(
                    security_code=item.security_code,
                    security_name=item.security_name,
                    quantity=str(Qty(item.quantity_units)),
                    cost=_money(item.cost_units) or "0.00",
                    market_value=_money(item.market_value_units),
                    weight_pct=_ratio(item.weight_ratio_units),
                )
                for item in asset.holdings
            ],
            market_value=_money(asset.market_value_units),
            cash=_money(asset.cash_units),
            total_assets=_money(asset.total_assets_units),
            position_pct=_pct_2dp(asset.position_ratio_units),
            total_profit=_money(asset.total_profit_units),
            max_invested_cost=_money(asset.max_invested_cost_units),
            max_cost_return_pct=_pct_2dp(asset.max_cost_return_ratio_units),
            total_fees=_money(asset.total_fees_units),
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
            max_profit_round=(
                None if history.max_profit_round is None else _round_out(history.max_profit_round)
            ),
            max_profit_cost_pct=_pct_2dp(history.max_profit_cost_ratio_units),
            top_rounds=[_round_out(item) for item in history.top_rounds],
            bottom_rounds=[_round_out(item) for item in history.bottom_rounds],
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
            metrics=f"/metrics?period={period}",
            reports="/reports",
            exports="/exports",
            attribution=f"/attribution?period={period}",
        ),
        assumptions=result.assumptions,
        inputs_hash=result.inputs_hash,
    )


@router.get("/dashboard", response_model=DashboardOut, operation_id="getDashboard")
async def get_dashboard_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> DashboardOut:
    """API-084：Dashboard 两板块聚合（只读；旧五卡按 legacy_period 懒加载）。"""
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


__all__ = ["router"]
