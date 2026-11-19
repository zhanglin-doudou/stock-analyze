"""API-107~109：超卖卖出人工修订（GET/PUT/DELETE `/trades/{id}/oversell-revision`）。

覆盖：
* 纯超卖卖出的状态查询（未配对数量 = 卖出数量）；
* PUT 注入两条流水（资金划转 + 无费用买入），字段/来源/备注/去重键符合口径；
* 同一卖出重复 PUT 只更新注入行，不叠加；成本非法 → 422；
* 非超卖卖出（真实账单已覆盖）→ 422；买入流水 → 422；跨账号 → 404；
* DELETE 软删注入行且幂等；区间覆盖（soft_delete_range）不误删注入行；
* 注入行在 FIFO 里排在卖出之前，重算后回合转为 closed、pnl = 卖款 − 修订成本；
* 部分超卖（已有 lot 只够配对一部分）同样可修订，注入数量只补未配对部分，
  修订后回合转 closed、pnl = 卖款 −（原有成本 + 修订成本）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import ErrorCode
from stock_core.fifo import build_rounds
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository
from stock_review.application.rebuild import trades_to_events
from stock_review.application.revisions import MANUAL_REVISION_NOTE
from test_api_028_031_rounds import _seed_trade

pytestmark = pytest.mark.api

_SELL_DATE = date(2026, 6, 7)
_SELL_QTY = 100_000  # 10 股（scale=1e4）


async def _seed_oversell_sell(engine, user_id, *, code: str = "127061") -> str:
    return await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=_SELL_DATE,
        side="sell",
        quantity_units=_SELL_QTY,
        price_units=112_392_000,
        amount_units=112_392,  # 1123.92 元
        security_code=code,
        line_no=259,
    )


async def _revision_rows(engine, user_id, sell_id: str) -> list[Trade]:
    async with AsyncSession(engine) as session:
        rows = await session.scalars(
            select(Trade)
            .where(Trade.user_id == user_id, Trade.revision_of == sell_id)
            .order_by(Trade.dedupe_key)
        )
        return list(rows)


async def _build_trades_rounds(engine, user_id, code: str):
    async with AsyncSession(engine) as session:
        trades = await session.scalars(
            select(Trade)
            .where(
                Trade.user_id == user_id,
                Trade.deleted_at.is_(None),
                Trade.security_code == code,
            )
            .order_by(Trade.trade_date, Trade.line_no, Trade.id)
        )
        return build_rounds(trades_to_events(list(trades)))


async def test_get_oversell_revision_reports_unmatched_quantity(
    client, api_prefix, user, owner_engine
):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)

    resp = await client.get(
        f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "none"
    assert body["sell"]["trade_id"] == sell_id
    assert body["sell"]["quantity"] == "10"
    assert body["sell"]["oversell_quantity"] == "10"
    assert body["cost"] is None
    assert body["cash_trade"] is None and body["buy_trade"] is None


async def test_put_oversell_revision_injects_two_legs_and_closes_round(
    client, api_prefix, user, owner_engine
):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)

    resp = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    revision = body["revision"]
    assert revision["status"] == "revised"
    assert revision["cost"] == "1000.00"
    assert revision["sell"]["oversell_quantity"] == "0"
    assert revision["cash_trade"]["amount"] == "1000.00"
    assert revision["buy_trade"]["quantity"] == "10"
    assert revision["buy_trade"]["amount"] == "-1000.00"
    assert revision["cash_trade"]["revision_of"] == sell_id
    assert revision["buy_trade"]["revision_of"] == sell_id
    # 端点在同一事务内只派发回合重建；资产表等用户全部补录后手动触发。
    assert body["rebuild_job_id"]
    assert body["assets_job_id"] is None

    rows = await _revision_rows(owner_engine, user.user_id, sell_id)
    assert len(rows) == 2
    by_key = {row.dedupe_key: row for row in rows}
    assert set(by_key) == {f"mr:{sell_id}:cash", f"mr:{sell_id}:buy"}
    cash = by_key[f"mr:{sell_id}:cash"]
    buy = by_key[f"mr:{sell_id}:buy"]
    assert cash.security_code == "CASH"
    assert cash.category == "transfer"
    assert cash.side == "sell"
    assert buy.category == "buy"
    assert buy.side == "buy"
    for row in rows:
        assert row.source == "manual"
        assert row.batch_id is None
        assert row.cash_balance_units is None
        assert row.note == MANUAL_REVISION_NOTE
        assert row.trade_date == _SELL_DATE
        assert row.line_no == 259
        assert row.version == 1

    # 引擎口径：注入行排在卖出之前 → 超卖消失，回合 closed 且 pnl = 卖款 − 修订成本。
    result = await _build_trades_rounds(owner_engine, user.user_id, "127061")
    assert result.data_errors == ()
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.status == "closed"
    assert round_.close_trade_id == sell_id
    assert round_.quantity_units == _SELL_QTY
    assert round_.proceeds_units == 112_392
    assert round_.cost_units == 100_000
    assert round_.pnl_units == 12_392
    assert round_.oversell_units == 0
    assert result.realized_pnl_units == 12_392

    # `/trades` 透出 `revision_of`，页面可据此标记「非真实成交账单」。
    listed = await client.get(
        f"{api_prefix}/trades",
        headers=user.headers,
        params={"security_code": "127061"},
    )
    assert listed.status_code == 200, listed.text
    injected = [item for item in listed.json()["items"] if item["revision_of"] == sell_id]
    assert {item["source"] for item in injected} == {"manual"}
    assert len(injected) == 1  # 资金腿 `CASH` 不在该证券过滤下


async def test_put_oversell_revision_supports_new_stock_switch(
    client, api_prefix, user, owner_engine
):
    """打新开关：开启时注入买入腿 category=new_stock，关闭/更新后回到 buy。"""
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    url = f"{api_prefix}/trades/{sell_id}/oversell-revision"

    resp = await client.put(
        url, headers=user.headers, json={"cost": "1000.00", "is_new_stock": True}
    )
    assert resp.status_code == 200, resp.text
    revision = resp.json()["revision"]
    assert revision["is_new_stock"] is True
    assert revision["buy_trade"]["category"] == "new_stock"
    rows = await _revision_rows(owner_engine, user.user_id, sell_id)
    buy = next(row for row in rows if row.dedupe_key.endswith(":buy"))
    assert buy.category == "new_stock"

    # 关闭开关重复提交：同一注入行改写 category 并更新成本，不叠加新行。
    resp = await client.put(url, headers=user.headers, json={"cost": "1200.00"})
    assert resp.status_code == 200, resp.text
    revision = resp.json()["revision"]
    assert revision["is_new_stock"] is False
    assert revision["buy_trade"]["category"] == "buy"
    assert revision["buy_trade"]["amount"] == "-1200.00"
    rows = await _revision_rows(owner_engine, user.user_id, sell_id)
    assert len(rows) == 2

    got = await client.get(url, headers=user.headers)
    assert got.status_code == 200, got.text
    assert got.json()["is_new_stock"] is False


async def test_partial_oversell_revision_closes_round(client, api_prefix, user, owner_engine):
    """部分超卖回合也能修订：只补未配对部分，修订后转 closed。"""
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=40_000,  # 4 股，成本 400.00
        price_units=100_000_000,
        amount_units=-40_000,
        security_code="127061",
    )
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)

    got = await client.get(f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "none"
    assert got.json()["sell"]["quantity"] == "10"
    assert got.json()["sell"]["oversell_quantity"] == "6"

    resp = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "600.00"},
    )
    assert resp.status_code == 200, resp.text
    revision = resp.json()["revision"]
    assert revision["status"] == "revised"
    assert revision["cost"] == "600.00"
    # 只补未配对的 6 股，而不是整笔卖出的 10 股（否则会凭空多出 4 股持仓）。
    assert revision["buy_trade"]["quantity"] == "6"
    assert revision["buy_trade"]["amount"] == "-600.00"

    result = await _build_trades_rounds(owner_engine, user.user_id, "127061")
    assert result.data_errors == ()
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.status == "closed"
    assert round_.close_trade_id == sell_id
    assert round_.quantity_units == _SELL_QTY
    assert round_.proceeds_units == 112_392
    assert round_.cost_units == 40_000 + 60_000
    assert round_.pnl_units == 112_392 - 100_000
    assert round_.oversell_units == 0


async def test_superseded_by_real_bills_blocks_put_but_allows_delete(
    client, api_prefix, user, owner_engine
):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    assert (
        await client.put(
            f"{api_prefix}/trades/{sell_id}/oversell-revision",
            headers=user.headers,
            json={"cost": "1000.00"},
        )
    ).status_code == 200

    # 用户之后补导入了真实买入账单，已经能覆盖这笔卖出 → 注入行变成「待确认」。
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=_SELL_QTY,
        price_units=100_000_000,
        amount_units=-100_000,
        security_code="127061",
    )
    got = await client.get(f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "superseded"

    blocked = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1200.00"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == ErrorCode.VALIDATION_ERROR

    deleted = await client.delete(
        f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["revision"]["status"] == "not_oversell"


async def test_put_is_idempotent_and_updates_cost(client, api_prefix, user, owner_engine):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    first = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert first.status_code == 200, first.text
    cash_id = first.json()["revision"]["cash_trade"]["trade_id"]
    buy_id = first.json()["revision"]["buy_trade"]["trade_id"]

    second = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "900.00"},
    )
    assert second.status_code == 200, second.text
    revision = second.json()["revision"]
    assert revision["cost"] == "900.00"
    assert revision["cash_trade"]["trade_id"] == cash_id
    assert revision["buy_trade"]["trade_id"] == buy_id
    assert revision["cash_trade"]["amount"] == "900.00"
    assert revision["buy_trade"]["amount"] == "-900.00"

    rows = await _revision_rows(owner_engine, user.user_id, sell_id)
    assert len(rows) == 2
    assert all(row.version == 2 for row in rows)


@pytest.mark.parametrize("cost", ["0", "-1.00", "abc"])
async def test_put_rejects_invalid_cost(client, api_prefix, user, owner_engine, cost):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    resp = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": cost},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR


async def test_revision_accepts_sell_without_side_but_category_sell(
    client, api_prefix, user, owner_engine
):
    """导入行可能只有类别没有方向（`side=unknown`）：按引擎口径仍应可修订。

    回归：曾经按 `side` 把关，导致资产页对这类超卖流水取修订信息直接 422。
    """
    sell_id = await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=_SELL_DATE,
        side="unknown",
        category="sell",
        quantity_units=_SELL_QTY,
        price_units=112_392_000,
        amount_units=112_392,
        security_code="127061",
    )

    resp = await client.get(
        f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "none"

    put = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["revision"]["status"] == "revised"


async def test_put_rejects_covered_sell_and_buy_trade(client, api_prefix, user, owner_engine):
    # 有买入覆盖的卖出：不超卖，不需要人工修订。
    buy_id = await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=_SELL_QTY,
        price_units=100_000_000,
        amount_units=-100_000,
        security_code="600519",
    )
    sell_id = await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=_SELL_DATE,
        side="sell",
        quantity_units=_SELL_QTY,
        price_units=112_392_000,
        amount_units=112_392,
        security_code="600519",
    )
    covered = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert covered.status_code == 422
    assert covered.json()["code"] == ErrorCode.VALIDATION_ERROR

    # 买入流水不适用「卖出超卖修订」。
    on_buy = await client.put(
        f"{api_prefix}/trades/{buy_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert on_buy.status_code == 422
    assert on_buy.json()["code"] == ErrorCode.VALIDATION_ERROR


async def test_delete_revision_is_idempotent_and_restores_oversell(
    client, api_prefix, user, owner_engine
):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    created = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert created.status_code == 200

    deleted = await client.delete(
        f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers
    )
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()["revision"]
    assert body["status"] == "none"
    assert body["sell"]["oversell_quantity"] == "10"
    assert body["cost"] is None

    rows = await _revision_rows(owner_engine, user.user_id, sell_id)
    assert len(rows) == 2
    assert all(row.deleted_at is not None for row in rows)
    assert all(row.version == 2 for row in rows)

    again = await client.delete(
        f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=user.headers
    )
    assert again.status_code == 200
    assert again.json()["revision"]["status"] == "none"


async def test_soft_delete_range_skips_revision_rows(client, api_prefix, user, owner_engine):
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    created = await client.put(
        f"{api_prefix}/trades/{sell_id}/oversell-revision",
        headers=user.headers,
        json={"cost": "1000.00"},
    )
    assert created.status_code == 200

    async with AsyncSession(owner_engine) as session:
        repo = TradeRepository(session)
        await repo.soft_delete_range(
            user.user_id,
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 30),
            at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        await session.commit()

    rows = await _revision_rows(owner_engine, user.user_id, sell_id)
    assert len(rows) == 2
    assert all(row.deleted_at is None for row in rows)


async def test_revision_rejected_across_users(
    client, api_prefix, user, account_factory, owner_engine
):
    other = await account_factory()
    sell_id = await _seed_oversell_sell(owner_engine, user.user_id)
    resp = await client.get(
        f"{api_prefix}/trades/{sell_id}/oversell-revision", headers=other.headers
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND


async def test_get_oversell_revision_unknown_trade_is_404(client, api_prefix, user):
    resp = await client.get(
        f"{api_prefix}/trades/trd_{uuid.uuid4().hex[:12]}/oversell-revision",
        headers=user.headers,
    )
    assert resp.status_code == 404
