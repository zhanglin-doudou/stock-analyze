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


async def test_api_092_list_zero_caliber_and_period_basis(client, api_prefix, user, scenario):
    resp = await _list(client, api_prefix, user.headers, "?period=2026-06")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "2026-06"
    assert body["period_basis"] == "close_date"
    assert body["basis"] == "benchmark_excess_20d"
    assert body["definition_version"] == "1.0"
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
    by_bucket = await _list(client, api_prefix, user.headers, "?period=2026-06&bucket=swing")
    assert [item["round_id"] for item in by_bucket.json()["items"]] == [
        scenario["round"]["round_id"]
    ]
    none_bucket = await _list(client, api_prefix, user.headers, "?period=2026-06&bucket=trend")
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
    assert summary["swing"]["swing"] == 1
    assert summary["fill_swing"]["pairs"] == 2
    # fill 与回合是两种统计单位：2 ≠ 1，汇总不得相加（AC-S3）。
    assert summary["swing"]["closed"] != summary["fill_swing"]["pairs"]


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
    assert body["fills"][0]["bucket"] == "swing"
    assert isinstance(body["flags"], list)
    assert isinstance(body["quality"], list)
    assert body["ranking"]["basis"] == "benchmark_excess_20d"
    assert "不构成投资建议" in body["disclaimer"]

    missing = await _detail(client, api_prefix, user.headers, "rnd_missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == ErrorCode.NOT_FOUND


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
