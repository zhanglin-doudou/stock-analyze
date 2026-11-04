"""归因端点（API-036，2026-11-02 五因子设计 §6）。

* `GET /attribution`（`getAttribution`）：五因子归因（只读、无写副作用、同步）；
* 降级/数据不可用一律 200：`status=degraded/blocked` + `missing[]`
  （码是响应字段，不是 `ErrorCode`）；
* blocked 时金额键缺省（`AttributionOut._omit_blocked`），禁止输出 0 冒充；
* 参数非法 422（`INVALID_PERIOD` / `MODEL_NOT_AVAILABLE` / `UNKNOWN_BENCHMARK`，
  校验在 application 层 `build_attribution`，本层只做协议适配）；
* admin 不可读业务数据 → 403 `ADMIN_CANNOT_READ_TRADES`。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from stock_core.values import Money, Ratio
from stock_platform.tenant import TenantContext
from stock_review.application.attribution import (
    AttributionFactor,
    AttributionResult,
    build_attribution,
)
from stock_review.attribution.presentation import pct_str

from stock_api.deps import get_market_data, get_sector_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    AttributionCoverageOut,
    AttributionFactorOut,
    AttributionLinksOut,
    AttributionMissingOut,
    AttributionOut,
    AttributionPresentationOut,
    AttributionReconciliationOut,
    AttributionSampleOut,
    AttributionUnattributedOut,
    attribution_payload,
)

router = APIRouter(tags=["attribution"], route_class=StrictQueryRoute)

_FIELD_LABELS: dict[str, str] = {
    "total_pnl": "总损益",
    "factors": "因子归因",
    "residual": "未解释残差",
    "classification_as_of": "板块分类时点",
    "coverage": "覆盖率",
    "data_as_of": "数据截至",
}
_BUCKET_LABELS: dict[str, str] = {
    "corporate_action_pnl": "公司行动",
    "unattributed_price_pnl": "未归因标的损益",
    "dividend_without_position": "无持仓分红",
    "interest": "利息",
    "ipo_pnl": "打新收益",
    "other_cashflow": "其他现金流",
    "valuation_gap": "估值差（未能解释）",
}
_BUCKET_LABELS_EN: dict[str, str] = {
    "corporate_action_pnl": "Corporate actions",
    "unattributed_price_pnl": "Unattributed symbol P&L",
    "dividend_without_position": "Dividends without position",
    "interest": "Interest",
    "ipo_pnl": "New-share (IPO) P&L",
    "other_cashflow": "Other cash flows",
    "valuation_gap": "Valuation gap (unexplained)",
}
_BLOCKER_CODES: frozenset[str] = frozenset(
    {
        "ASSET_SERIES_MISSING",
        "ASSET_ROW_BLOCKED",
        "SECTOR_DATA_MISSING",
        "SECTOR_HISTORY_NOT_COVERED",
    }
)


def _money(value: int | None) -> str | None:
    return None if value is None else str(Money(value))


def _ratio(value: int | None) -> str | None:
    return None if value is None else str(Ratio(value))


def _factor_out(factor: AttributionFactor) -> AttributionFactorOut:
    return AttributionFactorOut(
        code=factor.code,
        name=factor.name,
        amount=_money(factor.amount_units),
        ratio=_ratio(factor.ratio_units),
        share=_ratio(factor.share_units),
        rank=factor.rank,
        conclusion=factor.conclusion,
        presentation=AttributionPresentationOut(
            bar_pct=factor.presentation.bar_pct,
            ratio_pct=factor.presentation.ratio_pct,
            share_pct=factor.presentation.share_pct,
            tone=factor.presentation.tone,
            sector_bar_fallback=factor.presentation.sector_bar_fallback,
        ),
    )


def _to_out(result: AttributionResult, *, lang: str = "zh") -> AttributionOut:
    breakdown: dict[str, str] | None = None
    breakdown_labels: dict[str, str] | None = None
    if result.residual_breakdown is not None:
        breakdown = {key: str(Money(v)) for key, v in result.residual_breakdown.items()}
        labels = _BUCKET_LABELS_EN if lang == "en" else _BUCKET_LABELS
        breakdown_labels = {key: labels.get(key, key) for key in breakdown}
    coverage: AttributionCoverageOut | None = None
    if result.coverage is not None:
        coverage = AttributionCoverageOut(
            capital_total=str(Money(result.coverage.capital_total_units)),
            capital_attributed=str(Money(result.coverage.capital_attributed_units)),
            capital_excluded=str(
                Money(
                    result.coverage.capital_total_units - result.coverage.capital_attributed_units
                )
            ),
            capital_ratio=_ratio(result.coverage.capital_ratio_units),
            capital_ratio_pct=pct_str(result.coverage.capital_ratio_units),
            base_basis=result.coverage.base_basis,
            segments_total=result.coverage.segments_total,
            segments_attributed=result.coverage.segments_attributed,
            symbols_total=result.coverage.symbols_total,
            symbols_attributed=result.coverage.symbols_attributed,
            symbols_participating=result.coverage.symbols_participating,
            unattributed=[
                AttributionUnattributedOut(
                    security_code=entry.security_code,
                    security_name=entry.security_name,
                    symbol_class=entry.symbol_class,
                    reason_code=entry.reason_code,
                    amount=str(Money(entry.amount_units)),
                )
                for entry in result.coverage.unattributed
            ],
        )
    return AttributionOut(
        model=result.model,
        ratio_basis=result.ratio_basis,
        period=result.period,
        period_start=result.period_start,
        period_end=result.period_end,
        status=result.status,
        definition_version=result.definition_version,
        total_pnl_basis=result.total_pnl_basis,
        total_pnl=_money(result.total_pnl_units),
        ratio_na_reason=result.ratio_na_reason,
        base_capital=_money(result.base_capital_units),
        factors=[_factor_out(factor) for factor in result.factors],
        summary=result.summary,
        summary_na_reason=result.summary_na_reason,
        residual=_money(result.residual_units),
        residual_ratio=_ratio(result.residual_ratio_units),
        residual_ratio_pct=pct_str(result.residual_ratio_units),
        residual_breakdown=breakdown,
        residual_breakdown_labels=breakdown_labels,
        residual_reconciliation=(
            None
            if result.residual_reconciliation is None
            else AttributionReconciliationOut(
                unattributed_symbols_total=str(
                    Money(result.residual_reconciliation.unattributed_symbols_total_units)
                ),
                listed_total=str(Money(result.residual_reconciliation.listed_units)),
                others_count=result.residual_reconciliation.others_count,
                others_total=str(Money(result.residual_reconciliation.others_units)),
                display_limit=result.residual_reconciliation.display_limit,
                account_level_total=str(
                    Money(result.residual_reconciliation.account_level_total_units)
                ),
                residual=str(Money(result.residual_reconciliation.residual_units)),
            )
        ),
        sample=AttributionSampleOut(
            n_segments=result.sample.n_segments,
            n_attributed_segments=result.sample.n_attributed_segments,
            n_rounds=result.sample.n_rounds,
            n_symbols=result.sample.n_symbols,
            min_n=result.sample.min_n,
            sample_status=result.sample.sample_status,
        ),
        coverage=coverage,
        missing=[
            AttributionMissingOut(
                field=item.field,
                field_label=_FIELD_LABELS.get(item.field, item.field),
                code=item.code,
                severity="blocker" if item.code in _BLOCKER_CODES else item.severity,
                reason=item.reason,
                what_to_do=item.what_to_do,
            )
            for item in result.missing
        ],
        assumptions=dict(result.assumptions),
        data_as_of=result.data_as_of,
        inputs_hash=result.inputs_hash,
        links=AttributionLinksOut(**result.links),
    )


def _language(value: str | None) -> str:
    """`Accept-Language` → `zh`/`en`（只换文案不换数字，§5.5）。"""
    return "en" if value and value.lower().startswith("en") else "zh"


@router.get("/attribution", response_model=AttributionOut, operation_id="getAttribution")
async def get_attribution_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str, Query()],
    model: Annotated[Literal["five_factor", "brinson_industry"], Query()] = "five_factor",
    benchmark: Annotated[str | None, Query()] = None,
    classification_lookahead: Annotated[Literal["deny", "allow"], Query()] = "deny",
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
) -> JSONResponse:
    """API-036：五因子归因（FR-R7；数据不可用 → 200 blocked/degraded + missing[]）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    lang = _language(accept_language)
    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await build_attribution(
            session,
            user_id=user_id,
            period=period,
            model=model,
            benchmark=benchmark,
            classification_lookahead=classification_lookahead == "allow",
            clock=state.clock,
            market_data=get_market_data(request),
            sector_data=get_sector_data(request),
            lang=lang,
            lookahead_enabled=state.settings.sector_lookahead_enabled,
        )
    # blocked 整键省略在载荷层完成（见 `attribution_payload`）：直接返回 JSONResponse
    # 可绕过 response_model 的 None 补全，同时保留 OpenAPI 完整字段表。
    return JSONResponse(content=attribution_payload(_to_out(result, lang=lang)))


__all__ = ["router"]
