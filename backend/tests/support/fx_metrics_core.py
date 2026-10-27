"""fx_metrics_core 主夹具（设计文档 §8.3/§8.4，手工转录，确定性）。

30 行流水（2026-08-31 现金锚点 + 2026-09 全月）→ 12 closed + 3 open 回合：
- A1–A5/B1–B4/C1–C3 closed，A6/B5/C5 open；pnl/持有天数逐行取自 §8.4 手算表；
- 余额链全程为正且闭合（break_count=0）；主夹具无超卖、无 data_error；
- 金额 scale=2（分）、价格 scale=6、数量 scale=4；`amount_units` 为净现金流（已含费）。

派生锚点（§8.4）：8 盈 Σ=18020.91 / 4 亏 Σ=5514.98；费用 1250.02（佣金 651.32 + 印花 598.70）；
双边成交额毛额 2605200.00、净额 2604653.90。

另含 ``make_hhi_small_context``（§8.4 behavior.hhi 小例：两持仓 172000/12200 + 现金 50000）。
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from stock_review.metrics.context import MetricContextData
from stock_review.metrics.views import (
    AssetDailyView,
    BarView,
    FillView,
    RoundView,
    TradeView,
    empty_viewset,
)

PERIOD = "2026-09"
PERIOD_START = date(2026, 9, 1)
PERIOD_END = date(2026, 9, 30)
AS_OF = date(2026, 9, 30)
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
RF = Decimal("0.015")

_REBUILT_AT = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)


def _d(day: int) -> date:
    return date(2026, 9, day)


# ── 流水（§8.3 表逐行；price/amount/佣金/印花单位为 scale 整数）──
# (行号, 日期, 标的, category, 股数, 价格, 佣金分, 印花分, 净额分, 余额分)
_TRADE_ROWS: tuple[tuple[int, date, str, str, int, int, int, int, int, int], ...] = (
    (1, date(2026, 8, 31), "CASH", "transfer", 0, 0, 0, 0, 40_000_000, 40_000_000),
    (2, _d(1), "600519", "buy", 1_000_000, 1_650_000_000, 4_125, 0, -16_504_125, 23_495_875),
    (3, _d(1), "000001", "buy", 20_000_000, 12_500_000, 625, 0, -2_500_625, 20_995_250),
    (4, _d(1), "300750", "buy", 1_000_000, 213_000_000, 533, 0, -2_130_533, 18_864_717),
    (5, _d(7), "600519", "dividend", 0, 0, 0, 0, 30_000, 18_894_717),
    (6, _d(8), "600519", "sell", 1_000_000, 1_700_000_000, 4_250, 8_500, 16_987_250, 35_881_967),
    (7, _d(9), "600519", "buy", 1_000_000, 1_680_000_000, 4_200, 0, -16_804_200, 19_077_767),
    (8, _d(9), "300750", "sell", 1_000_000, 220_000_000, 550, 1_100, 2_198_350, 21_276_117),
    (9, _d(10), "000001", "sell", 20_000_000, 12_800_000, 640, 1_280, 2_558_080, 23_834_197),
    (10, _d(10), "300750", "buy", 2_000_000, 215_000_000, 1_075, 0, -4_301_075, 19_533_122),
    (11, _d(11), "000001", "buy", 30_000_000, 12_600_000, 945, 0, -3_780_945, 15_752_177),
    (12, _d(15), "600519", "sell", 1_000_000, 1_650_000_000, 4_125, 8_250, 16_487_625, 32_239_802),
    (13, _d(16), "000001", "sell", 30_000_000, 12_400_000, 930, 1_860, 3_717_210, 35_957_012),
    (14, _d(16), "600519", "buy", 2_000_000, 1_660_000_000, 8_300, 0, -33_208_300, 2_748_712),
    (15, _d(17), "300750", "sell", 2_000_000, 210_000_000, 1_050, 2_100, 4_196_850, 6_945_562),
    (16, _d(17), "000001", "buy", 20_000_000, 12_300_000, 615, 0, -2_460_615, 4_484_947),
    (17, _d(18), "300750", "buy", 1_000_000, 205_000_000, 513, 0, -2_050_513, 2_434_434),
    (18, _d(22), "600519", "sell", 2_000_000, 1_690_000_000, 8_450, 16_900, 33_774_650, 36_209_084),
    (19, _d(23), "000001", "sell", 20_000_000, 12_700_000, 635, 1_270, 2_538_095, 38_747_179),
    (20, _d(23), "600519", "buy", 1_000_000, 1_700_000_000, 4_250, 0, -17_004_250, 21_742_929),
    (21, _d(24), "300750", "sell", 1_000_000, 218_000_000, 545, 1_090, 2_178_365, 23_921_294),
    (22, _d(24), "000001", "buy", 10_000_000, 12_800_000, 320, 0, -1_280_320, 22_640_974),
    (23, _d(25), "600519", "sell", 1_000_000, 1_695_000_000, 4_238, 8_475, 16_937_287, 39_578_261),
    (24, _d(28), "600519", "buy", 1_000_000, 1_640_000_000, 4_100, 0, -16_404_100, 23_174_161),
    (25, _d(29), "600519", "sell", 1_000_000, 1_680_000_000, 4_200, 8_400, 16_787_400, 39_961_561),
    (26, _d(29), "000001", "sell", 10_000_000, 12_900_000, 323, 645, 1_289_032, 41_250_593),
    (27, _d(29), "300750", "buy", 2_000_000, 215_000_000, 1_075, 0, -4_301_075, 36_949_518),
    (28, _d(30), "600519", "buy", 1_000_000, 1_680_000_000, 4_200, 0, -16_804_200, 20_145_318),
    (29, _d(30), "000001", "buy", 10_000_000, 12_800_000, 320, 0, -1_280_320, 18_864_998),
    (30, _d(30), "000001", "dividend", 0, 0, 0, 0, 10_000, 18_874_998),
)

_SIDE_BY_CATEGORY = {"buy": "buy", "sell": "sell", "dividend": "in", "transfer": "in"}

TRADES: tuple[TradeView, ...] = tuple(
    TradeView(
        id=f"trd_{row:04d}",
        trade_date=day,
        line_no=row,
        side=_SIDE_BY_CATEGORY[category],
        category=category,
        security_code=security,
        quantity_units=qty,
        price_units=price,
        amount_units=amount,
        commission_units=commission,
        stamp_tax_units=stamp_tax,
        other_fee_units=0,
        cash_balance_units=balance,
        deleted_at=None,
        version=1,
    )
    for row, day, security, category, qty, price, commission, stamp_tax, amount, balance in _TRADE_ROWS
)

# ── 回合（§8.4 手算表；holding_days=交易日口径，pnl 含费含股息净额）──
# (id, 标的, 开仓日, 平仓日, 持仓交易日, pnl 分)
_ROUND_ROWS: tuple[tuple[str, str, date, date | None, int, int], ...] = (
    ("rnd_A1", "600519", _d(1), _d(8), 5, 513_125),
    ("rnd_A2", "600519", _d(9), _d(15), 4, -316_575),
    ("rnd_A3", "600519", _d(16), _d(22), 4, 566_350),
    ("rnd_A4", "600519", _d(23), _d(25), 2, -66_963),
    ("rnd_A5", "600519", _d(28), _d(29), 1, 383_300),
    ("rnd_A6", "600519", _d(30), None, 0, 395_800),
    ("rnd_B1", "000001", _d(1), _d(10), 7, 57_455),
    ("rnd_B2", "000001", _d(11), _d(16), 3, -63_735),
    ("rnd_B3", "000001", _d(17), _d(23), 4, 77_480),
    ("rnd_B4", "000001", _d(24), _d(29), 3, 8_712),
    ("rnd_B5", "000001", _d(30), None, 0, -60_320),
    ("rnd_C1", "300750", _d(1), _d(9), 6, 67_817),
    ("rnd_C2", "300750", _d(10), _d(17), 5, -104_225),
    ("rnd_C3", "300750", _d(18), _d(24), 4, 127_852),
    ("rnd_C5", "300750", _d(29), None, 0, -201_075),
)

ROUNDS: tuple[RoundView, ...] = tuple(
    RoundView(
        id=round_id,
        security_code=security,
        status="closed" if close is not None else "open",
        open_date=open_day,
        close_date=close,
        quantity_units=0,
        cost_units=0,
        proceeds_units=0,
        dividend_units=0,
        pnl_units=pnl,
        return_rate_units=0,
        holding_days=holding,
        data_error=False,
        oversell_units=0,
        rebuilt_at=_REBUILT_AT,
    )
    for round_id, security, open_day, close, holding, pnl in _ROUND_ROWS
)

# ── fills（每 closed 回合 1 条；buy_cost=买入净支出 |amount|，§8.3 净额列取绝对值）──
_FILL_ROWS: tuple[tuple[str, int, int, date, date], ...] = (
    ("rnd_A1", 16_504_125, 5, _d(1), _d(8)),
    ("rnd_A2", 16_804_200, 4, _d(9), _d(15)),
    ("rnd_A3", 33_208_300, 4, _d(16), _d(22)),
    ("rnd_A4", 17_004_250, 2, _d(23), _d(25)),
    ("rnd_A5", 16_404_100, 1, _d(28), _d(29)),
    ("rnd_B1", 2_500_625, 7, _d(1), _d(10)),
    ("rnd_B2", 3_780_945, 3, _d(11), _d(16)),
    ("rnd_B3", 2_460_615, 4, _d(17), _d(23)),
    ("rnd_B4", 1_280_320, 3, _d(24), _d(29)),
    ("rnd_C1", 2_130_533, 6, _d(1), _d(9)),
    ("rnd_C2", 4_301_075, 5, _d(10), _d(17)),
    ("rnd_C3", 2_050_513, 4, _d(18), _d(24)),
)

FILLS: tuple[FillView, ...] = tuple(
    FillView(
        round_id=round_id,
        sequence=1,
        buy_trade_id=f"{round_id}_b1",
        sell_trade_id=f"{round_id}_s1",
        quantity_units=0,
        buy_cost_units=buy_cost,
        sell_proceeds_units=0,
        dividend_units=0,
        pnl_units=0,
        open_date=open_day,
        close_date=close_day,
        holding_days=holding,
    )
    for round_id, buy_cost, holding, open_day, close_day in _FILL_ROWS
)

# 固定指纹：CI seed 派生链可复现（D-07；变更即黄金值重冻）
FINGERPRINTS: dict[str, str] = {
    name: "sha256:" + f"fx_metrics_core.{name}.v1".ljust(64, "0")
    for name in (
        "rounds",
        "fills",
        "trades",
        "asset_daily",
        "asset_build",
        "bars_daily",
        "benchmark_series",
        "calendar",
    )
}


def make_fx_context(
    *,
    rounds: tuple[RoundView, ...] = ROUNDS,
    fills: tuple[FillView, ...] = FILLS,
    trades: tuple[TradeView, ...] = TRADES,
    asset_daily: tuple[AssetDailyView, ...] = (),
    bars: tuple[BarView, ...] = (),
    as_of: date = AS_OF,
    observed_trading_days: int = 60,
    flags: dict[str, bool] | None = None,
) -> MetricContextData:
    """fx_metrics_core 的纯内存上下文（period=2026-09 闭区间）。"""
    views = empty_viewset(period_start=PERIOD_START, period_end=PERIOD_END)
    return MetricContextData(
        user_id=USER_ID,
        period=PERIOD,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        as_of=as_of,
        params={},
        benchmark=None,
        rf_annual=RF,
        rounds=rounds,
        fills=fills,
        trades=trades,
        asset_daily=asset_daily,
        asset_build=views.asset_build,
        bars=bars,
        benchmark_series=(),
        calendar=views.calendar,
        flags=dict(flags or {}),
        upstream={},
        fingerprints=dict(FINGERPRINTS),
        market_as_of=AS_OF,
        engine_version="1.0",
        observed_trading_days=observed_trading_days,
    )


# ── behavior.hhi 小例（§8.4：持仓 172000/12200 + 现金 50000，总资产 234200）──

_HHI_SMALL_DAY = date(2026, 9, 30)
_HHI_SMALL_TRADES: tuple[TradeView, ...] = (
    TradeView(
        id="trd_hhi_1",
        trade_date=date(2026, 9, 29),
        line_no=1,
        side="buy",
        category="buy",
        security_code="TST001",
        quantity_units=1_000_000,  # 100 股
        price_units=1_720_000_000,  # 1720.00
        amount_units=-17_200_000,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=None,
        deleted_at=None,
        version=1,
    ),
    TradeView(
        id="trd_hhi_2",
        trade_date=date(2026, 9, 29),
        line_no=2,
        side="buy",
        category="buy",
        security_code="TST002",
        quantity_units=10_000_000,  # 1000 股
        price_units=12_200_000,  # 12.20
        amount_units=-1_220_000,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=None,
        deleted_at=None,
        version=1,
    ),
)
_HHI_SMALL_BARS: tuple[BarView, ...] = tuple(
    BarView(symbol=symbol, trade_date=day, close_units=close, return_units=0)
    for day in (date(2026, 9, 29), _HHI_SMALL_DAY)
    for symbol, close in (("TST001", 1_720_000_000), ("TST002", 12_200_000))
)
_HHI_SMALL_ASSET: tuple[AssetDailyView, ...] = (
    AssetDailyView(
        trade_date=_HHI_SMALL_DAY,
        cash_units=5_000_000,
        market_value_units=18_420_000,
        total_assets_units=23_420_000,
        holding_cost_units=0,
        unrealized_pnl_units=0,
        holding_count=2,
        cashflow_units=0,
        prev_total_assets_units=None,
        daily_pnl_units=None,
        daily_return_units=None,
        status="ok",
        definition_version="1.0",
    ),
)


def make_hhi_small_context() -> MetricContextData:
    """单日 HHI 小例：HHI_total=0.58765819、HHI_eq=0.87630874（§8.4）。"""
    return make_fx_context(
        rounds=(),
        fills=(),
        trades=_HHI_SMALL_TRADES,
        asset_daily=_HHI_SMALL_ASSET,
        bars=_HHI_SMALL_BARS,
    )


# ── 状态/门槛变体（§8.2 row 1：n=5/10、data_error 剔除）──

_N5_IDS = ("rnd_A1", "rnd_A2", "rnd_A3", "rnd_A4", "rnd_A5")
_N10_IDS = (*_N5_IDS, "rnd_B1", "rnd_B2", "rnd_B3", "rnd_B4", "rnd_C1")


def make_n5_context() -> MetricContextData:
    """5 closed（3 盈 2 亏）、无 open：n<10 → insufficient、无 CI。"""
    chosen = tuple(item for item in ROUNDS if item.id in _N5_IDS)
    return make_fx_context(rounds=chosen, fills=(), trades=())


def make_n10_context() -> MetricContextData:
    """10 closed（7 盈 3 亏）+ 3 open：n=10 边界 → ok、出 Wilson CI。"""
    chosen = tuple(item for item in ROUNDS if item.id in _N10_IDS or item.status == "open")
    return make_fx_context(rounds=chosen, fills=(), trades=())


def make_data_error_context() -> MetricContextData:
    """rnd_B2 标记 data_error：剔除出 closed 分母，计入 n_data_error。"""
    rounds = tuple(
        dataclasses.replace(item, data_error=True) if item.id == "rnd_B2" else item
        for item in ROUNDS
    )
    return make_fx_context(rounds=rounds, fills=(), trades=())


__all__ = [
    "AS_OF",
    "FILLS",
    "FINGERPRINTS",
    "PERIOD",
    "PERIOD_END",
    "PERIOD_START",
    "RF",
    "ROUNDS",
    "TRADES",
    "USER_ID",
    "make_data_error_context",
    "make_fx_context",
    "make_hhi_small_context",
    "make_n5_context",
    "make_n10_context",
]
