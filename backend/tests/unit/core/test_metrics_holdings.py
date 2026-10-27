"""持仓重建 + 组合收益单测（Period2-07）。"""

from __future__ import annotations

from stock_core.metrics.holdings import (
    DailyPortfolio,
    HoldingLot,
    build_daily_portfolio,
)
from stock_core.metrics.portfolio_index import (
    compound_index,
    daily_portfolio_returns,
)


class TestBuildDailyPortfolio:
    def test_simple_buy_and_hold(self) -> None:
        """买入 1 股某标的，持仓过夜。"""
        prior_cash = 10_000_00  # 10 万（scale=2 分）
        prior_lots: dict[str, tuple[int, int]] = {}
        # 买入：symbol="AAPL", qty=10000 (1股 scale=4), price=15000000 (150元 scale=6), fee=3000 (30元 scale=2)
        day_fills = [("AAPL", 10_000, 15_000_000, 3_000)]
        day_closes = {"AAPL": 15_000_000}  # 收盘价 150.000000 scale=6
        dp = build_daily_portfolio(
            prior_cash_units=prior_cash,
            prior_lots=prior_lots,
            day_fills=day_fills,
            day_closes=day_closes,
        )
        assert len(dp.lots) == 1
        assert dp.lots[0].symbol == "AAPL"
        assert dp.total_assets_units == dp.cash_units + dp.lots[0].market_value_units

    def test_sell_reduces_position(self) -> None:
        """持有后卖出部分。"""
        prior_cash = 0
        prior_lots = {
            "AAPL": (20_000, 10_000_000)
        }  # 2 股（scale=4 = 0.0002 股？不对，10_000/10_000=1股）
        # 修正：20_000 scale=4 表示 2.0000 股
        day_fills = [("AAPL", -10_000, 12_000_000, 2_000)]  # 卖 1 股
        day_closes = {"AAPL": 13_000_000}
        dp = build_daily_portfolio(
            prior_cash_units=prior_cash,
            prior_lots=prior_lots,
            day_fills=day_fills,
            day_closes=day_closes,
        )
        assert len(dp.lots) == 1
        assert dp.lots[0].quantity_units == 10_000

    def test_full_sell_removes_lot(self) -> None:
        prior_cash = 0
        prior_lots = {"AAPL": (10_000, 10_000_000)}
        day_fills = [("AAPL", -10_000, 12_000_000, 2_000)]
        day_closes = {"AAPL": 13_000_000}
        dp = build_daily_portfolio(
            prior_cash_units=prior_cash,
            prior_lots=prior_lots,
            day_fills=day_fills,
            day_closes=day_closes,
        )
        assert len(dp.lots) == 0

    def test_add_buy_updates_fifo_avg_cost(self) -> None:
        """已有持仓的标的再买入 → 触发 L104-105 的 FIFO 均价更新分支。

        prior_lots 里的 ac 是真实元（Decimal 直接存），与 L96 新 symbol 路径一致。
        """
        # prior: 1 股 AAPL，均价 100 元 → prior_lots ac=100
        prior_cash = 10_000_00
        prior_lots = {"AAPL": (10_000, 100)}
        # 再买入 1 股，价 150 元 (price_u scale6 = 150_000_000)
        day_fills = [("AAPL", 10_000, 150_000_000, 3_000)]
        day_closes = {"AAPL": 160_000_000}
        dp = build_daily_portfolio(
            prior_cash_units=prior_cash,
            prior_lots=prior_lots,
            day_fills=day_fills,
            day_closes=day_closes,
        )
        assert len(dp.lots) == 1
        assert dp.lots[0].quantity_units == 20_000  # 2 股
        # new_avg = (100 * 1 + 150 * 1) / 2 = 125 元 → 125_000_000 scale6
        assert dp.lots[0].avg_cost_units == 125_000_000

    def test_prior_zero_qty_lot_is_skipped(self) -> None:
        """prior_lots 中 qty <= 0 的 symbol → 触发 L120 continue 跳过。"""
        prior_cash = 5_000_00
        prior_lots = {
            "EMPTY": (0, 0),  # qty=0 → 应被跳过
            "NEG": (-5000, 100_000_00),  # 负数 qty → 也应跳过
        }
        day_fills: list[tuple[str, int, int, int]] = []
        day_closes: dict[str, int] = {}
        dp = build_daily_portfolio(
            prior_cash_units=prior_cash,
            prior_lots=prior_lots,
            day_fills=day_fills,
            day_closes=day_closes,
        )
        assert len(dp.lots) == 0

    def test_missing_close_falls_back_to_avg_cost(self) -> None:
        """持仓标的未出现在 day_closes → 触发 L125 的 close_price = ac 兜底。"""
        prior_cash = 0
        prior_lots = {"AAPL": (10_000, 100)}  # ac=100 元（真实元）
        day_fills: list[tuple[str, int, int, int]] = []
        day_closes: dict[str, int] = {}  # 缺失 AAPL
        dp = build_daily_portfolio(
            prior_cash_units=prior_cash,
            prior_lots=prior_lots,
            day_fills=day_fills,
            day_closes=day_closes,
        )
        assert len(dp.lots) == 1
        # closing_price 兜底为 avg_cost=100 → 100 * 1_000_000 = 100_000_000 scale6
        assert dp.lots[0].closing_price_units == 100_000_000


class TestEquityMvUnitsProperty:
    def test_sums_all_lots_market_value(self) -> None:
        """L51: DailyPortfolio.equity_mv_units property 覆盖。"""
        lot1 = HoldingLot(
            symbol="AAPL",
            quantity_units=10_000,
            avg_cost_units=100_000_00,
            market_value_units=50_000_00,  # 50 万元
            closing_price_units=110_000_00,
        )
        lot2 = HoldingLot(
            symbol="GOOGL",
            quantity_units=5_000,
            avg_cost_units=200_000_00,
            market_value_units=30_000_00,  # 30 万元
            closing_price_units=210_000_00,
        )
        dp = DailyPortfolio(date_index=0, cash_units=20_000_00, lots=[lot1, lot2])
        assert dp.equity_mv_units == 80_000_00  # 50M + 30M


class TestDailyPortfolioReturns:
    def test_two_portfolios_known_return(self) -> None:
        dp1 = DailyPortfolio(date_index=0, cash_units=100_000_00)
        dp2 = DailyPortfolio(date_index=1, cash_units=105_000_00)
        # r = (105_000_00 - 100_000_00) / 100_000_00 = 0.05 → 5_000_000 scale8
        rets = daily_portfolio_returns([dp1, dp2])
        assert rets == [5_000_000]

    def test_single_portfolio_returns_empty(self) -> None:
        dp = DailyPortfolio(date_index=0, cash_units=100_000_00)
        assert daily_portfolio_returns([dp]) == []

    def test_zero_prev_asset_is_skipped(self) -> None:
        """L40: 前一日总资产为 0 → 跳过该日收益率。"""
        dp0 = DailyPortfolio(date_index=0, cash_units=0)
        dp1 = DailyPortfolio(date_index=1, cash_units=50_000_00)
        dp2 = DailyPortfolio(date_index=2, cash_units=105_000_00)
        rets = daily_portfolio_returns([dp0, dp1, dp2])
        # dp0→dp1 被跳过，只剩 dp1→dp2: r = (105-50)/50 = 1.1 → 但 scale=8 → 110_000_000
        assert rets == [1_100_000_00]


class TestCompoundIndex:
    def test_no_return_is_zero(self) -> None:
        assert compound_index([]) == 0

    def test_flat_return_is_zero(self) -> None:
        assert compound_index([0, 0, 0]) == 0

    def test_simple_positive(self) -> None:
        # [0.01, 0.01] → (1.01)^2 - 1 = 0.0201 → 2_010_000 scale8
        rets = [1_000_000, 1_000_000]
        assert compound_index(rets) == 2_010_000

    def test_mixed_returns(self) -> None:
        # [0.01, -0.01] → 1.01 × 0.99 - 1 = 0.9999 - 1 = -0.0001 → -100_00 scale8
        rets = [1_000_000, -1_000_000]
        assert compound_index(rets) == -100_00
