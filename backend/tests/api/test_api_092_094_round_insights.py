"""API-092/093/094 回合洞察列表/详情/刷新（设计 2026-11-04 §6.3~§6.5/§8）。

覆盖 AC-I1~I5、AC-P*、AC-G*：
* 列表零口径（金额 2 位、收益率带 % 由后端产出）、周期口径（close_date）、
  `flag` 设计词表、`has_reason`/`reason_code`/`bucket`/`security_code` 过滤、
  `include_summary=false`、游标与全部错误码；
* 详情：逐笔理由/前瞻/旗标/排序 + 跨账号 404 + admin 403；
* 刷新：幂等键必填、周期校验、scope=all 越权 403、审计仅落计数与周期；
* 数据不足 → `missing[]` 200 降级（不伪造 0）。
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from stock_contracts.errors import ErrorCode
from test_api_028_031_rounds import (
    _closed_round,
    _idem_key,
    _rebuild_and_drain,
    _seed_trade,
)

pytestmark = pytest.mark.api

_RATIO_PCT_RE = re.compile(r"^-?\d+\.\d{2}%$")
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_PRICE6_RE = re.compile(r"^-?\d+\.\d{6}$")
_PRICE2_RE = re.compile(r"^-?\d+\.\d{2}$")


async def _list(client, api_prefix, headers, query: str = ""):
    return await client.get(f"{api_prefix}/rounds/insights{query}", headers=headers)


async def _detail(client, api_prefix, headers, round_id: str):
    return await client.get(f"{api_prefix}/rounds/{round_id}/insights", headers=headers)


@pytest.fixture
async def scenario(client, api_prefix, user, owner_engine, settings):
    """一个完整平仓回合：100 股 @100.00 买入，06-20 全部 @120.00 卖出。"""
    buy = await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
    )
    sell = await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 20),
        side="sell",
        quantity_units=1_000_000,
        price_units=120_000_000,
        amount_units=1_200_000,
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)
    closed = await _closed_round(client, api_prefix, user.headers)
    return {"trades": {"buy1": buy, "sell": sell}, "round": closed}


@pytest.fixture
async def addon_scenario(client, api_prefix, user, owner_engine, settings):
    """加仓场景（replay 夹具 600519 逐日缓涨，确定性）：

    * 06-01 同日两笔买入：100 股 @50.00（id 较小）与 50 股 @1800.00（id 较大）；
    * FIFO 稳定序是 `(交易日, 行号, 摘要, 数量, …)`，同日同摘要下 50 股排在前 → 该回合的
      `open_trade_id` 是 50 股那笔；随后 100 股那笔买入时已有 50 股持仓 → 是真正的加仓笔，
      回合因此打 `addon`，买入侧判定（`below_avg` / `continued_decline` / B2）也落在加仓笔；
    * 06-19 卖 150 股 @100.00（均价 633.33 高于卖价 → 非止盈 → `stop_loss_rebound`）。
    """
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 1),
        side="buy",
        quantity_units=1_000_000,
        price_units=50_000_000,
        amount_units=-500_000,
        trade_id="trd_addon000001",
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 1),
        side="buy",
        quantity_units=500_000,
        price_units=1_800_000_000,
        amount_units=-9_000_000,
        trade_id="trd_addon000002",
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 19),
        side="sell",
        quantity_units=1_500_000,
        price_units=100_000_000,
        amount_units=1_500_000,
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)


@pytest.fixture
async def sell_fly_scenario(client, api_prefix, user, owner_engine, settings):
    """两回合对照（replay 夹具 600519 逐日缓涨，确定性）：

    * 回合 1：07-01 买 100 股 @10.00 → 07-10 卖 100 股 @100.00（止盈且 20 日内最高收盘
      1712.75 > 103.00 → `profit_take_fly`）；
    * 回合 2：07-13 买 100 股 @2000.00 → 07-14 卖 100 股 @1500.00（亏损但同样卖早 →
      `stop_loss_rebound`）。
    """
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 7, 1),
        side="buy",
        quantity_units=1_000_000,
        price_units=10_000_000,
        amount_units=-100_000,
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 7, 10),
        side="sell",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=1_000_000,
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 7, 13),
        side="buy",
        quantity_units=1_000_000,
        price_units=2_000_000_000,
        amount_units=-20_000_000,
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 7, 14),
        side="sell",
        quantity_units=1_000_000,
        price_units=1_500_000_000,
        amount_units=15_000_000,
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)


async def test_api_092_summary_addon_serialized(client, api_prefix, user, addon_scenario):
    """`summary.addon` 从聚合到序列化的完整路径（AC-D*）。

    买入侧判定落在「本回合最后一笔买入」（有加仓时即加仓笔）：本场景最后一笔是
    06-01 的 100 股 @50.00（开仓笔是同日 50 股 @1800.00），因此
    `continued_decline` 按 50.00 这笔评估（价格后续上涨 → 不命中），
    `avg_dip_pct` 也取这笔的 20 日 B2。
    `missed_profit_*` 只统计 `profit_take_fly`，本场景是止损反弹 → 必须为 null。
    """
    resp = await _list(client, api_prefix, user.headers, "?period=2026-06")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    item = body["items"][0]
    assert {"bottom_buy", "stop_loss_sold_early", "drawdown"} <= set(item["flags"])
    # 旧旗标不再输出。
    assert not {"addon", "below_avg", "profit_take_fly", "stop_loss_rebound"} & set(item["flags"])
    # 计数型状态：抄底账单 1 笔（100 股 @50），卖出带短期卖飞旗标且低于均价 → 止损侧 1 笔。
    assert item["status_counts"]["bottom_buy"] == 1
    assert item["status_counts"]["stop_loss_sold_early"] == 1
    assert item["status_counts"]["profit_sold_early"] == 0
    assert item["buy_trade"]["trade_id"] == "trd_addon000002"
    # 逐笔数量取 trade.quantity_units 的展示串（整数份额，不带尾零；此前该字段硬编码为 None）。
    assert item["buy_trade"]["quantity_display"] == "50"

    addon = body["summary"]["addon"]
    assert addon["count"] == 1
    assert addon["rounds"] == 1  # 兼容字段，与 count 同值
    assert addon["declined_after_addon"] == 0
    assert addon["declined_pct"] == "0.00%"
    # 加仓笔 B2 = (1705.96 − 50.00) / 50.00 = +3311.92%
    assert addon["avg_dip_pct"] == "3311.92%"
    assert addon["avg_dip_coverage"] == "1/1"
    assert addon["share_pct"] == "100.00%"  # 1/1 个平仓回合

    sell_fly = body["summary"]["sell_fly"]
    assert sell_fly["sell_count"] == 1
    assert sell_fly["profit_take_fly"] == 0
    assert sell_fly["stop_loss_rebound"] == 1
    assert sell_fly["fly_pct"] == "0.00%"  # 只算止盈卖飞
    assert sell_fly["rounds"] == 0
    assert sell_fly["share_pct"] == "0.00%"
    # 止损反弹不并入 missed_profit（只有止盈卖飞才统计）
    assert sell_fly["missed_profit_worst_display"] is None
    assert sell_fly["missed_profit_end_display"] is None
    assert sell_fly["missed_profit_worst_abs_display"] is None
    assert sell_fly["missed_profit_worst_label"] is None
    assert sell_fly["missed_profit_end_abs_display"] is None
    assert sell_fly["missed_profit_end_label"] is None


async def test_api_092_summary_sell_fly_serialized(client, api_prefix, user, sell_fly_scenario):
    """`summary.sell_fly`：分母是全部卖出回合，分子只算止盈卖飞；金额只并止盈卖飞。"""
    resp = await _list(client, api_prefix, user.headers, "?period=2026-07")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    all_flags = [flag for item in body["items"] for flag in item["flags"]]
    assert body["summary"]["swing"]["closed"] == 2
    assert "profit_sold_early" in all_flags
    assert "stop_loss_sold_early" in all_flags

    sell_fly = body["summary"]["sell_fly"]
    assert sell_fly["sell_count"] == 2
    assert sell_fly["profit_take_fly"] == 1
    assert sell_fly["stop_loss_rebound"] == 1
    assert sell_fly["fly_pct"] == "50.00%"  # 1/2，只算止盈卖飞
    assert sell_fly["rounds"] == 1
    assert sell_fly["share_pct"] == "50.00%"
    # 只统计止盈卖飞那 1 笔：100 股 ×(1712.75 − 100.00) = 161275.00
    assert sell_fly["missed_profit_worst_display"] == "161275.00"
    assert sell_fly["missed_profit_end_display"] == "161275.00"
    # additive：正数 → 少赚；绝对值串与原有带符号串同值。
    assert sell_fly["missed_profit_worst_abs_display"] == "161275.00"
    assert sell_fly["missed_profit_worst_label"] == "少赚"
    assert sell_fly["missed_profit_worst_label_en"] == "missed gain"
    assert sell_fly["missed_profit_end_abs_display"] == "161275.00"
    assert sell_fly["missed_profit_end_label"] == "少赚"
    # 逐项同样带标签，便于前端直接渲染。
    fly_item = next(item for item in body["items"] if "profit_sold_early" in item["flags"])
    assert fly_item["missed_profit_end_label"] == "少赚"
    assert fly_item["missed_profit_worst_abs_display"] == fly_item["missed_profit_worst_display"]

    addon = body["summary"]["addon"]
    assert addon["count"] == 0
    assert addon["avg_dip_pct"] is None
    assert addon["avg_dip_coverage"] == "0/0"


async def test_api_092_summary_addon_empty_when_no_addon(client, api_prefix, user, scenario):
    """无加仓时 `count=0`、百分比为 null、覆盖度 `0/0`（不伪造 0%）。"""
    resp = await _list(client, api_prefix, user.headers, "?period=2026-06")
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]

    assert summary["addon"] == {
        "count": 0,
        "declined_after_addon": 0,
        "declined_pct": None,
        "avg_dip_pct": None,
        "avg_dip_coverage": "0/0",
        "rounds": 0,
        "share_pct": "0.00%",
    }
    assert summary["sell_fly"]["sell_count"] == 1
    assert summary["sell_fly"]["stop_loss_rebound"] == 0


async def test_api_092_list_zero_caliber_and_period_basis(client, api_prefix, user, scenario):
    resp = await _list(client, api_prefix, user.headers, "?period=2026-06")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "2026-06"
    assert body["period_basis"] == "close_date"
    assert body["basis"] == "benchmark_excess_20d"
    assert body["definition_version"] == "1.2"
    assert body["item_total"] >= 1
    assert "不构成投资建议" in body["disclaimer"]
    assert body["missing"] == [] or set(body["missing"]) <= {
        "BENCHMARK_CALENDAR_MISSING",
        "MARKET_DATA_MISSING",
    }

    item = body["items"][0]
    assert item["round_id"] == scenario["round"]["round_id"]
    assert _MONEY_RE.match(item["pnl"])
    assert _RATIO_PCT_RE.match(item["return_rate_pct"])
    assert item["buy_trade"]["forward"]["5"]["b1_pct"] is None or _RATIO_PCT_RE.match(
        item["buy_trade"]["forward"]["5"]["b1_pct"]
    )
    # 汇总数值同样由后端产出，前端零口径
    summary = body["summary"]
    assert summary is not None
    assert _RATIO_PCT_RE.match(summary["swing"]["win_rate_pct"])
    # AC-S3：页面级 `bucket_basis=round`；fill 级配对单列，统计单位是 fill。
    assert body["bucket_basis"] == "round"
    fill_swing = summary["fill_swing"]
    assert fill_swing["bucket_basis"] == "fill"
    assert fill_swing["pairs"] == 1
    assert summary["swing"]["closed"] == body["item_total"]

    # 周期口径：回合 06-20 平仓，落在 2026-06；2026-07 应为空
    july = await _list(client, api_prefix, user.headers, "?period=2026-07")
    assert july.json()["item_total"] == 0
    assert july.json()["items"] == []


async def test_api_092_filters_and_include_summary(client, api_prefix, user, scenario):
    by_bucket = await _list(client, api_prefix, user.headers, "?period=2026-06&bucket=position")
    assert [item["round_id"] for item in by_bucket.json()["items"]] == [
        scenario["round"]["round_id"]
    ]
    none_bucket = await _list(client, api_prefix, user.headers, "?period=2026-06&bucket=long")
    assert none_bucket.json()["items"] == []

    missing_reason = await _list(
        client, api_prefix, user.headers, "?period=2026-06&flag=reason_missing"
    )
    assert [item["round_id"] for item in missing_reason.json()["items"]] == [
        scenario["round"]["round_id"]
    ]
    pending = await _list(client, api_prefix, user.headers, "?period=2026-06&flag=horizon_pending")
    assert pending.json()["items"] == []
    corporate = await _list(
        client, api_prefix, user.headers, "?period=2026-06&flag=corporate_action"
    )
    assert corporate.json()["items"] == []

    by_symbol = await _list(
        client, api_prefix, user.headers, "?period=2026-06&security_code=600519"
    )
    assert by_symbol.json()["item_total"] == 1
    other_symbol = await _list(
        client, api_prefix, user.headers, "?period=2026-06&security_code=000001"
    )
    assert other_symbol.json()["items"] == []

    no_summary = await _list(
        client, api_prefix, user.headers, "?period=2026-06&include_summary=false"
    )
    assert no_summary.json()["summary"] is None


async def test_api_092_filter_by_security_type(client, api_prefix, user, owner_engine, settings):
    """`security_type` 服务端过滤：items 与 summary 同源收敛，四类完备，非法值 422。

    四个标的各一回合：股票 600519、基金 159892、可转债 113708、其它债券 019547。
    """
    for code in ("600519", "159892", "113708", "019547"):
        for trade_date, side, amount_units in (
            (date(2026, 6, 2), "buy", -1_000_000),
            (date(2026, 6, 20), "sell", 1_200_000),
        ):
            await _seed_trade(
                owner_engine,
                user_id=user.user_id,
                trade_date=trade_date,
                side=side,
                quantity_units=1_000_000,
                price_units=100_000_000 if side == "buy" else 120_000_000,
                amount_units=amount_units,
                security_code=code,
            )
    await _rebuild_and_drain(client, api_prefix, user, settings)

    async def fetch(query: str) -> dict:
        resp = await _list(client, api_prefix, user.headers, f"?period=2026-06&{query}")
        assert resp.status_code == 200, resp.text
        return resp.json()

    unfiltered = await fetch("")
    assert unfiltered["item_total"] == 4

    stock = await fetch("security_type=stock")
    assert [item["security_code"] for item in stock["items"]] == ["600519"]
    # summary 必须基于过滤后的集合计算（用户诉求：排除其它品种对统计的干扰）。
    assert stock["summary"]["swing"]["closed"] == 1

    fund = await fetch("security_type=fund")
    assert [item["security_code"] for item in fund["items"]] == ["159892"]
    assert fund["summary"]["swing"]["closed"] == 1

    bond = await fetch("security_type=convertible_bond")
    assert [item["security_code"] for item in bond["items"]] == ["113708"]

    other = await fetch("security_type=other")
    assert [item["security_code"] for item in other["items"]] == ["019547"]

    # 四类互斥完备：逐类相加必须等于全量，不能有标的因归不出而消失。
    total = stock["item_total"] + fund["item_total"] + bond["item_total"] + other["item_total"]
    assert total == unfiltered["item_total"] == 4

    # 逗号分隔与重复参数等价
    comma = await fetch("security_type=stock,fund")
    repeated = await fetch("security_type=stock&security_type=fund")
    assert comma["item_total"] == repeated["item_total"] == 2
    assert comma["summary"]["swing"]["closed"] == 2

    # 非法取值直接 422（券种枚举不再有「不在筛选项」的黑洞）。
    invalid = await _list(client, api_prefix, user.headers, "?period=2026-06&security_type=bond")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"


async def test_api_092_ac_s3_fill_pairs_not_added_to_rounds(
    client, api_prefix, user, owner_engine, settings
):
    """AC-S3：同一回合两笔部分卖出 → fill 配对 2，回合级波段仍 1，两者不相加。"""
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 8),
        side="sell",
        quantity_units=400_000,
        price_units=110_000_000,
        amount_units=440_000,
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 20),
        side="sell",
        quantity_units=600_000,
        price_units=120_000_000,
        amount_units=720_000,
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)

    body = (await _list(client, api_prefix, user.headers, "?period=2026-06")).json()
    summary = body["summary"]
    assert body["item_total"] == 1
    assert summary["swing"]["closed"] == 1
    assert summary["swing"]["position"] == 1
    assert summary["fill_swing"]["pairs"] == 2
    # fill 与回合是两种统计单位：2 ≠ 1，汇总不得相加（AC-S3）。
    assert summary["swing"]["closed"] != summary["fill_swing"]["pairs"]


async def test_api_092_summary_holding_by_pnl_serialized(
    client, api_prefix, user, sell_fly_scenario
):
    """`summary.holding_by_pnl`：亏损/保本/盈利恒定三条，容差与均值由后端给。

    卖飞场景两回合：07-01 买 100 股 @10.00 → 07-10 卖 @100.00（盈利）；
    07-13 买 100 股 @2000.00 → 07-14 卖 @1500.00（亏损）。无保本样本。
    """
    body = (await _list(client, api_prefix, user.headers, "?period=2026-07")).json()
    summary = body["summary"]
    holding = summary["holding_by_pnl"]
    # 保本容差来自 stock_core 常量（默认 100.00 元），不由前端/调用方配置。
    assert holding["flat_tolerance_display"] == "100.00"
    classes = holding["classes"]
    assert [row["code"] for row in classes] == ["loss", "flat", "win"]
    assert [row["label"] for row in classes] == ["亏损", "保本", "盈利"]
    by_code = {row["code"]: row for row in classes}
    assert by_code["loss"]["count"] == 1
    assert by_code["win"]["count"] == 1
    # 无样本的类别仍返回，均值为 null（不显示 0，也不省略分类）。
    assert by_code["flat"]["count"] == 0
    assert by_code["flat"]["avg_holding_trading_days"] is None
    assert by_code["flat"]["avg_holding_trading_days_x10"] is None
    # 三类计数之和恒等于已平仓回合数；x10 定点与展示串同源。
    assert sum(row["count"] for row in classes) == summary["swing"]["closed"]
    for row in classes:
        x10 = row["avg_holding_trading_days_x10"]
        if x10 is None:
            assert row["avg_holding_trading_days"] is None
        else:
            assert row["avg_holding_trading_days"] == f"{x10 // 10}.{x10 % 10}"


async def test_api_092_reason_filters_follow_annotation(client, api_prefix, user, scenario):
    buy_id = scenario["trades"]["buy1"]
    put = await client.put(
        f"{api_prefix}/trades/{buy_id}/reason",
        headers=user.headers,
        json={"reason_code": "bottom_fish"},
    )
    assert put.status_code == 201, put.text

    has_reason = await _list(client, api_prefix, user.headers, "?period=2026-06&has_reason=true")
    assert [item["round_id"] for item in has_reason.json()["items"]] == [
        scenario["round"]["round_id"]
    ]
    by_code = await _list(
        client, api_prefix, user.headers, "?period=2026-06&reason_code=bottom_fish"
    )
    assert by_code.json()["item_total"] == 1
    item = by_code.json()["items"][0]
    assert item["buy_trade"]["reason_label"] == "抄底"


@pytest.mark.parametrize(
    ("query", "status", "code"),
    [
        ("?period=2026-13", 422, ErrorCode.INVALID_PERIOD),
        ("?period=2026-06&sort=foo", 400, ErrorCode.INVALID_SORT_FIELD),
        ("?period=2026-06&sort_dir=sideways", 400, ErrorCode.INVALID_SORT_FIELD),
        ("?period=2026-06&cursor=@@@", 400, ErrorCode.INVALID_CURSOR),
        ("?period=2026-06&unknown_param=1", 400, ErrorCode.UNKNOWN_QUERY_PARAM),
    ],
)
async def test_api_092_error_codes(client, api_prefix, user, query, status, code):
    resp = await _list(client, api_prefix, user.headers, query)
    assert resp.status_code == status, resp.text
    assert resp.json()["code"] == code


async def test_api_093_detail_and_cross_user_404(client, api_prefix, user, scenario):
    resp = await _detail(client, api_prefix, user.headers, scenario["round"]["round_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["round_id"] == scenario["round"]["round_id"]
    assert body["fills"]
    assert body["fills"][0]["pnl_display"] is not None
    # AC-S3：fill 明细用真实交易日（不是 round.holding_days 的 weekday 近似）。
    assert isinstance(body["fills"][0]["holding_trading_days"], int)
    assert body["fills"][0]["bucket"] == "position"
    assert isinstance(body["flags"], list)
    assert isinstance(body["quality"], list)
    assert body["ranking"]["basis"] == "benchmark_excess_20d"
    assert "不构成投资建议" in body["disclaimer"]

    missing = await _detail(client, api_prefix, user.headers, "rnd_missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == ErrorCode.NOT_FOUND


async def test_api_092_093_display_scale_6dp_and_2dp(
    client, api_prefix, user, owner_engine, settings
):
    """`price_display` 守 6 位小数、收盘价守 2 位小数；买卖两向、列表与详情都覆盖。

    回归：此前 `_money`（`Money` scale=2）被用于 1e-6 的价格标度整数，展示值被放大 10⁴
    （真实净价 `50.725500` 输出成 `"507255.00"`）。本场景的成交额含费，可同时验证净价方向：
    买入 100 股 @50.717000、费用 5.30 → 净价 `50.770000`（高于毛价）；
    卖出 100 股 @48.640000、费用 1.00 → 净价 `48.630000`（低于毛价）。
    收盘价取 replay 夹具 600519 的 `(d0, target]` 窗口（与 `compute_forward` 同口径）。
    """
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=1_000_000,  # 100.0000 股
        price_units=50_717_000,  # 毛价 50.717000
        amount_units=-507_700,  # 成交额 −5077.00（含 5.30 费用）
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 20),
        side="sell",
        quantity_units=1_000_000,
        price_units=48_640_000,  # 毛价 48.640000
        amount_units=486_300,  # 成交额 4863.00（扣 1.00 费用）
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)

    body = (await _list(client, api_prefix, user.headers, "?period=2026-06")).json()
    item = body["items"][0]
    buy = item["buy_trade"]
    sell = item["sell_trade"]

    # 净价 = |成交额| × 10⁸ ÷ 数量（scale=6）；买入含费更高、卖出扣费更低。
    assert buy["price_display"] == "50.770000"
    assert sell["price_display"] == "48.630000"
    assert buy["amount_display"] == "-5077.00"
    assert sell["amount_display"] == "4863.00"
    for view, quantity_units, amount_units in (
        (buy, 1_000_000, -507_700),
        (sell, 1_000_000, 486_300),
    ):
        # 标度关系：6 位小数，且严格等于 `price_net` 的标度整数换算值（不允许 10⁴ 级偏差）。
        assert _PRICE6_RE.match(view["price_display"])
        expected_price = (
            Decimal(abs(amount_units)) * Decimal(10**8) / Decimal(quantity_units) / Decimal(10**6)
        )
        assert Decimal(view["price_display"]) == expected_price
        # 数量透出与种子值一致（此前该字段恒为 null）。
        assert view["quantity_display"] == "100"
        # 数量 × 价格 与成交额自洽（买入含费、卖出扣费两个方向）。
        assert Decimal(view["price_display"]) * Decimal(view["quantity_display"]) == abs(
            Decimal(view["amount_display"])
        )

    # 收盘价：2 位小数 + 精确值（replay 夹具 600519 的 (d0, target] 窗口最高/最低收盘）。
    expected_close = {
        ("buy_trade", "5"): ("1702.61", "1701.65"),
        ("buy_trade", "20"): ("1706.20", "1701.65"),
        ("sell_trade", "5"): ("1705.72", "1704.76"),
        ("sell_trade", "20"): ("1709.19", "1704.76"),
    }
    for (key, horizon), (high, low) in expected_close.items():
        forward = item[key]["forward"][horizon]
        assert forward["status"] == "ok"
        assert _PRICE2_RE.match(forward["max_close_display"])
        assert _PRICE2_RE.match(forward["min_close_display"])
        assert forward["max_close_display"] == high
        assert forward["min_close_display"] == low

    # 详情接口同源（`_side_view` / `_to_view` 与列表共用）。
    detail = (await _detail(client, api_prefix, user.headers, item["round_id"])).json()
    assert detail["buy_trade"]["price_display"] == "50.770000"
    assert detail["sell_trade"]["price_display"] == "48.630000"
    assert detail["buy_trade"]["forward"]["20"]["max_close_display"] == "1706.20"
    assert detail["sell_trade"]["forward"]["20"]["min_close_display"] == "1704.76"


async def test_api_092_093_fractional_quantity_two_decimals(
    client, api_prefix, user, owner_engine, settings
):
    """货币基金（940018）按 0.01 份计份额 → `quantity_display` 输出 2 位小数。

    真实库里 940018 的 `quantity_units` 末两位非 0（如 14141900 = 1414.19 份）；
    展示层不得四舍五入成整数，也不得退化成 `"1414.1900"` 的尾零噪音。列表与详情
    共用 `stock_core.values.format_quantity`，这里同时校验「数量 × 价格 == 金额」。
    """
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=14_141_900,  # 1414.19 份
        price_units=1_000_000,  # 1.000000
        amount_units=-141_419,  # −1414.19 元
        security_code="940018",
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 20),
        side="sell",
        quantity_units=14_141_900,
        price_units=1_000_000,
        amount_units=141_419,  # +1414.19 元
        security_code="940018",
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)

    body = (await _list(client, api_prefix, user.headers, "?period=2026-06")).json()
    item = next(row for row in body["items"] if row["security_code"] == "940018")
    for side, sign in (("buy_trade", -1), ("sell_trade", 1)):
        view = item[side]
        assert view["quantity_display"] == "1414.19"
        assert view["price_display"] == "1.000000"
        assert view["amount_display"] == ("-1414.19" if sign < 0 else "1414.19")
        assert Decimal(view["quantity_display"]) * Decimal(view["price_display"]) == abs(
            Decimal(view["amount_display"])
        )

    # 详情与列表同源。
    detail = (await _detail(client, api_prefix, user.headers, item["round_id"])).json()
    assert detail["buy_trade"]["quantity_display"] == "1414.19"
    assert detail["sell_trade"]["quantity_display"] == "1414.19"


async def test_api_092_093_cross_user_sees_nothing(
    client, api_prefix, user, account_factory, scenario
):
    other = await account_factory()
    listing = await _list(client, api_prefix, other.headers, "?period=2026-06")
    assert listing.status_code == 200
    assert listing.json()["item_total"] == 0

    detail = await _detail(client, api_prefix, other.headers, scenario["round"]["round_id"])
    assert detail.status_code == 404


async def test_api_092_093_admin_forbidden(client, api_prefix, admin):
    listing = await _list(client, api_prefix, admin.headers, "?period=2026-06")
    assert listing.status_code == 403
    assert listing.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES


async def test_api_094_refresh_happy_and_audit(client, api_prefix, user, scenario, owner_engine):
    resp = await client.post(
        f"{api_prefix}/rounds/insights/refresh",
        headers={**user.headers, "Idempotency-Key": _idem_key()},
        json={"period": "2026-06"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {
        "computed",
        "skipped",
        "pending",
        "no_calendar",
        "no_bar",
        "corporate_action",
        "divisor_missing",
        "stale_price",
        "data_as_of",
    }
    assert (
        sum(
            body[key]
            for key in (
                "computed",
                "skipped",
                "pending",
                "no_calendar",
                "no_bar",
                "corporate_action",
                "divisor_missing",
                "stale_price",
            )
        )
        >= 1
    )
    # 计数为 int，日期为字符串（零口径，前端不折算）
    assert isinstance(body["computed"], int)
    assert body["data_as_of"] is None or re.match(r"^\d{4}-\d{2}-\d{2}$", body["data_as_of"])

    async with owner_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select meta from platform.audit_log "
                    "where action = 'round_insights.refresh' order by at desc limit 1"
                )
            )
        ).all()
    assert rows
    meta = rows[0][0]
    assert meta["period"] == "2026-06"
    assert "computed" in meta


@pytest.mark.parametrize(
    ("headers_extra", "payload", "status", "code"),
    [
        ({}, {"period": "2026-06"}, 422, ErrorCode.VALIDATION_ERROR),  # 缺幂等键
        ({"Idempotency-Key": "short"}, {"period": "2026-06"}, 422, ErrorCode.VALIDATION_ERROR),
        ({"Idempotency-Key": "k" * 32}, {"period": "2026-13"}, 422, ErrorCode.INVALID_PERIOD),
        (
            {"Idempotency-Key": "k" * 32},
            {"period": "2026-06", "scope": "all"},
            403,
            ErrorCode.FORBIDDEN,
        ),
    ],
)
async def test_api_094_error_codes(client, api_prefix, user, headers_extra, payload, status, code):
    resp = await client.post(
        f"{api_prefix}/rounds/insights/refresh",
        headers={**user.headers, **headers_extra},
        json=payload,
    )
    assert resp.status_code == status, resp.text
    assert resp.json()["code"] == code


async def test_api_094_idempotency_key_is_required_on_repeat(client, api_prefix, user, scenario):
    key = "idem-" + uuid.uuid4().hex
    first = await client.post(
        f"{api_prefix}/rounds/insights/refresh",
        headers={**user.headers, "Idempotency-Key": key},
        json={"period": "2026-06"},
    )
    second = await client.post(
        f"{api_prefix}/rounds/insights/refresh",
        headers={**user.headers, "Idempotency-Key": key},
        json={"period": "2026-06"},
    )
    assert first.status_code == second.status_code == 200
    # ≤5000 笔为同步路径；同键重复执行也合法（第二次命中缓存计入 skipped）
    first_body, second_body = first.json(), second.json()
    assert first_body["computed"] + first_body["skipped"] == (
        second_body["computed"] + second_body["skipped"]
    )


async def test_api_094_scope_missing_skips_unchanged_fingerprint(
    client, api_prefix, user, scenario
):
    """F8 回归：区间内 bar 未变 → 指纹稳定 → `scope=missing` 全量跳过、不重算。"""
    first = await client.post(
        f"{api_prefix}/rounds/insights/refresh",
        headers={**user.headers, "Idempotency-Key": _idem_key()},
        json={"period": "2026-06", "scope": "missing"},
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"{api_prefix}/rounds/insights/refresh",
        headers={**user.headers, "Idempotency-Key": _idem_key()},
        json={"period": "2026-06", "scope": "missing"},
    )
    assert second.status_code == 200, second.text
    first_body, second_body = first.json(), second.json()
    assert first_body["computed"] >= 1
    assert second_body["computed"] == 0
    assert second_body["skipped"] == first_body["computed"]


async def test_api_092_unsupported_symbol_degrades_not_404(
    client, api_prefix, user, owner_engine, settings
):
    """回归：数据源不支持的标的不得让整页 404，降级为 `no_bar` + null。

    2026-11-05 真实故障：`GET /api/v1/rounds/insights?period=2026-09` 因把全部交易
    标的（含 754019，live 不在支持范围 / replay 夹具无该标的）批量传给行情端口，
    `NOT_FOUND` 冒到 API 边界 → 整页 404。修复后请求经 `/api/v1` 前缀返回 200，
    该标的的前瞻收益显式降级，其余标的与回合照常返回。
    """
    for trade_date, side, amount_units in (
        (date(2026, 6, 2), "buy", -1_000_000),
        (date(2026, 6, 20), "sell", 1_200_000),
    ):
        await _seed_trade(
            owner_engine,
            user_id=user.user_id,
            trade_date=trade_date,
            side=side,
            quantity_units=1_000_000,
            price_units=100_000_000 if side == "buy" else 120_000_000,
            amount_units=amount_units,
        )
        await _seed_trade(
            owner_engine,
            user_id=user.user_id,
            trade_date=trade_date,
            side=side,
            quantity_units=1_000_000,
            price_units=100_000_000 if side == "buy" else 120_000_000,
            amount_units=amount_units,
            security_code="754019",
        )
    await _rebuild_and_drain(client, api_prefix, user, settings)

    listing = await _list(client, api_prefix, user.headers, "?period=2026-06")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    items = {item["security_code"]: item for item in body["items"]}
    assert set(items) == {"600519", "754019"}
    unsupported = items["754019"]
    assert unsupported["buy_trade"]["forward"]["5"]["status"] == "no_bar"
    assert unsupported["buy_trade"]["forward"]["5"]["b1_pct"] is None
    assert unsupported["sell_trade"]["forward"]["20"]["b1_pct"] is None
    # 支持标的的既有口径不受影响（仍能取到行情）
    assert items["600519"]["buy_trade"]["forward"]["5"]["status"] != "no_bar"

    detail = await _detail(client, api_prefix, user.headers, unsupported["round_id"])
    assert detail.status_code == 200, detail.text


async def test_api_092_addon_uses_statement_order_not_trade_id(
    client, api_prefix, user, owner_engine, settings
):
    """回归：同日「先卖后买」不得把清仓后的重新建仓误判为加仓。

    真实误判 `rnd_482e85a353666c92458668ea5f3fb829`（688126）：05-12 流水行号
    1709/1711 卖出 1750×2 先把 3500 股清到 0，行号 1712 才买入 1900 股；但该买入的
    ULID 随机段最小，只按 `(trade_date, id)` 排序会把它排到卖出之前 → `addon`。
    这里用「卖出 id 字典序更大、行号更小」的最小场景复现：修复后按行号排序，
    新回合首笔买入的 `qty_before=0`，不再出现 `addon` / `below_avg`。
    """
    # 05-06 建仓 100 股 → 回合 A；05-07 先卖 100 股（行号 20）→ A 清仓。
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 5, 6),
        side="buy",
        quantity_units=1_000_000,
        price_units=10_000_000,
        amount_units=-100_000,
        line_no=10,
        trade_id="trd_zzzz00000001",
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 5, 7),
        side="sell",
        quantity_units=1_000_000,
        price_units=11_000_000,
        amount_units=110_000,
        line_no=20,
        trade_id="trd_zzzz00000002",
    )
    # 05-07 再买 100 股（行号 21）：id 字典序最小，旧排序会排到卖出之前。
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 5, 7),
        side="buy",
        quantity_units=1_000_000,
        price_units=12_000_000,
        amount_units=-120_000,
        line_no=21,
        trade_id="trd_aaaa00000003",
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 5, 8),
        side="sell",
        quantity_units=1_000_000,
        price_units=13_000_000,
        amount_units=130_000,
        line_no=30,
        trade_id="trd_zzzz00000004",
    )
    await _rebuild_and_drain(client, api_prefix, user, settings)

    resp = await _list(client, api_prefix, user.headers, "?period=2026-05")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_open = {item["open_date"]: item for item in body["items"]}
    assert set(by_open) == {"2026-05-06", "2026-05-07"}
    reopening = by_open["2026-05-07"]
    assert reopening["buy_trade"]["trade_id"] == "trd_aaaa00000003"
    # 新回合首笔买入（qty_before=0）为抄底笔；加仓已不再是输出旗标。
    assert reopening["status_counts"]["bottom_buy"] == 1
    # 同源统计：本场景没有任何加仓，summary.addon 必须为 0。
    assert body["summary"]["addon"]["count"] == 0
    assert body["summary"]["addon"]["rounds"] == 0


async def test_api_092_093_flag_glossary_serialized(client, api_prefix, user, sell_fly_scenario):
    """旗标词表：列表与详情同源透出 12 个枚举值，且覆盖响应中出现的全部 item flags。"""
    resp = await _list(client, api_prefix, user.headers, "?period=2026-07")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    glossary = body["flag_glossary"]
    assert len(glossary) == 12
    assert len({entry["code"] for entry in glossary}) == 12
    for entry in glossary:
        assert set(entry) == {
            "code",
            "label_zh",
            "label_en",
            "description_zh",
            "description_en",
            "kind",
        }
        assert entry["label_zh"] and entry["label_en"]
        assert entry["description_zh"] and entry["description_en"]
        assert entry["kind"] in {"item_flag", "filter_alias"}

    glossary_codes = {entry["code"] for entry in glossary}
    observed_flags = {flag for item in body["items"] for flag in item["flags"]}
    assert observed_flags <= glossary_codes
    assert {"bottom_buy", "stop_loss_sold_early", "profit_sold_early"} <= glossary_codes
    assert {"sold_too_early", "reason_missing", "horizon_pending", "corporate_action"} <= (
        glossary_codes
    )

    detail = await _detail(client, api_prefix, user.headers, body["items"][0]["round_id"])
    assert detail.status_code == 200, detail.text
    assert [entry["code"] for entry in detail.json()["flag_glossary"]] == [
        entry["code"] for entry in glossary
    ]
