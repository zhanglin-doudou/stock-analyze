"""真实 akshare 联网集成用例（默认门禁排除，手动执行）。

前置：

- `uv sync --extra live`（安装 akshare/pandas）；
- 网络可达东财；
- 显式设置 `MARKET_LIVE_E2E=1`（避免 CI/本地误跑外网）。

执行：`MARKET_LIVE_E2E=1 uv run pytest backend/tests/integration/test_market_live_e2e.py -q -m live`
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from stock_contracts.errors import ApiError
from stock_ledger.market import create_market_source
from stock_platform.config import Settings
from stock_platform.ports import MarketDataSource

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("MARKET_LIVE_E2E") != "1",
        reason="联网集成用例需显式设置 MARKET_LIVE_E2E=1",
    ),
]

pytest.importorskip("akshare", reason="需 `uv sync --extra live`")

SYMBOL = "000651"
START = date(2021, 10, 1)
END = date(2021, 10, 31)


@pytest.fixture
def live_source() -> MarketDataSource:
    settings = Settings(market_mode="live", market_live_cache_enabled=False)
    return create_market_source(settings)


async def test_fetch_real_daily_bars(live_source: MarketDataSource) -> None:
    bars = await live_source.get_daily_bars([SYMBOL], START, END)
    assert len(bars) >= 15, f"2021-10 交易日应 >=15，实际 {len(bars)}"
    assert all(bar.symbol == SYMBOL for bar in bars)
    assert all(bar.close_units > 0 for bar in bars)
    assert all(START <= bar.trade_date <= END for bar in bars)
    assert [bar.trade_date for bar in bars] == sorted(bar.trade_date for bar in bars)
    # 收益由 close 序列派生：相邻日收益不应出现离谱跳变（±50%）
    assert all(abs(bar.return_units) <= 50_000_000 for bar in bars)  # |日收益| <= 50%


async def test_fetch_unsupported_symbol_is_fail_closed(live_source: MarketDataSource) -> None:
    with pytest.raises(ApiError):
        await live_source.get_daily_bars(["510300"], START, END)
