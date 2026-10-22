"""API-028~031 验收：重建回合任务 + 回合列表/详情/FIFO 配对明细。

覆盖 INTERFACE §4.1：
* API-028：202 + Location/Retry-After；同参 3 次复用同一 job_id（X-Job-Reused）；
  异参单飞 → 409 JOB_ALREADY_RUNNING；scope=period 缺 period → 422 INVALID_PERIOD；
  Idempotency-Key 必填（缺失 → 422）；
* API-029：rebuild 成功后 /rounds 立即可见；跨用户 0 条；游标翻页无重无漏；
* API-030：pnl 与手算值差 ≤0.01；跨用户/不存在 → 404；admin → 403；
* API-031：FIFO buy/sell 配对集合 100% 一致；配对金额为定点字符串。

手算场景（同一证券 600519）：
  buy1 06-02 100 股 @100.00 成本 10000.00
  buy2 06-10  50 股 @ 90.00 成本  4500.00
  sell 06-20  60 股 @120.00 卖出现金 7200.00 → FIFO 消耗 buy1 60 股：
       成本 6000.00，pnl = 7200 - 6000 = 1200.00，收益率 = 0.2
  期末未平仓：buy1 余 40 股成本 4000.00 + buy2 50 股 4500.00 + buy3 40 股 4400.00
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, Trade
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_QTY_RE = re.compile(r"^-?\d+\.\d{4}$")
_RATIO_RE = re.compile(r"^-?\d+\.\d{8}$")


async def _drain_worker(settings) -> None:
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def _execute_one_pending(settings) -> bool:
    """只执行队首一个任务（用于验证 reclassify 成功后自动入队的行为）。"""
    runner = WorkerRunner(settings)
    try:
        claimed = await runner._claim_one()
        if claimed is None:
            return False
        await runner._execute(claimed)
        return True
    finally:
        await runner.close()


async def _seed_batch(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    batch_id = f"bat_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            ImportBatch(
                id=batch_id,
                user_id=user_id,
                preview_id=None,
                status="succeeded",
                mode="full",
                broker="huatai",
                file_name="bill.csv",
                file_sha256="0" * 64,
                file_bytes=128,
                parsed_count=0,
                inserted_count=0,
                skipped_count=0,
                quarantined_count=0,
                columns={"成交日期": "trade_date"},
                warnings=[],
                assumptions={},
                ranges={},
            )
        )
        await session.commit()
    return batch_id


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date,
    side: str,
    quantity_units: int,
    price_units: int,
    amount_units: int,
    security_code: str = "600519",
    batch_id: str | None = None,
) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=batch_id,
                trade_date=trade_date,
                security_code=security_code,
                security_name=security_code,
                side=side,
                quantity_units=quantity_units,
                price_units=price_units,
                amount_units=amount_units,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=None,
                cash_balance_available=False,
                category=side,
                occurrence_idx=0,
                source="import",
                version=1,
                summary="证券买入" if side == "buy" else "证券卖出",
                dedupe_key=f"{security_code}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _seed_round_scenario(engine: AsyncEngine, *, user_id: uuid.UUID) -> dict[str, str]:
    """见模块 docstring 的手算场景；返回各 trade_id。"""
    buy1 = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
    )
    buy2 = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 10),
        side="buy",
        quantity_units=500_000,
        price_units=90_000_000,
        amount_units=-450_000,
    )
    sell = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 20),
        side="sell",
        quantity_units=600_000,
        price_units=120_000_000,
        amount_units=720_000,
    )
    buy3 = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 25),
        side="buy",
        quantity_units=400_000,
        price_units=110_000_000,
        amount_units=-440_000,
    )
    return {"buy1": buy1, "buy2": buy2, "sell": sell, "buy3": buy3}


def _idem_key() -> str:
    return "idem-" + uuid.uuid4().hex


async def _post_rebuild(
    client, api_prefix, headers, *, body: dict | None = None, key: str | None = None
):
    return await client.post(
        f"{api_prefix}/jobs/rebuild-rounds",
        headers={**headers, "Idempotency-Key": key or _idem_key()},
        json=body if body is not None else {"scope": "full"},
    )


async def _rebuild_and_drain(client, api_prefix, user, settings) -> str:
    resp = await _post_rebuild(client, api_prefix, user.headers)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{job_id}", headers=user.headers)
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "succeeded", job.text
    return job_id


async def _rounds(client, api_prefix, headers, query: str = "?limit=200") -> dict:
    resp = await client.get(f"{api_prefix}/rounds{query}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _closed_round(client, api_prefix, headers) -> dict:
    body = await _rounds(client, api_prefix, headers)
    closed = [item for item in body["items"] if item["status"] == "closed"]
    assert len(closed) == 1, body
    return closed[0]


async def test_api_028_accepted_location_and_same_params_reuse(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    await _seed_round_scenario(owner_engine, user_id=user.user_id)
    first = await _post_rebuild(client, api_prefix, user.headers)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["type"] == "rebuild_rounds"
    assert body["status"] == "pending"
    assert first.headers["Location"] == f"/api/v1/jobs/{body['job_id']}"
    assert first.headers["Retry-After"] == "1"

    second = await _post_rebuild(client, api_prefix, user.headers)
    third = await _post_rebuild(client, api_prefix, user.headers)
    assert second.status_code == 202, second.text
    assert third.status_code == 202, third.text
    assert second.json()["job_id"] == body["job_id"]
    assert third.json()["job_id"] == body["job_id"]
    assert second.headers.get("X-Job-Reused") == "true"
    assert third.headers.get("X-Job-Reused") == "true"

    # 同一单飞 scope 内异参（scope=full 但 reason 不同）→ 409 JOB_ALREADY_RUNNING，
    # 不能静默复用一个参数不同的 job。
    different = await _post_rebuild(
        client,
        api_prefix,
        user.headers,
        body={"scope": "full", "reason": "不同参数的并发重建"},
    )
    assert different.status_code == 409, different.text
    assert different.json()["code"] == ErrorCode.JOB_ALREADY_RUNNING.value

    # 不同 scope 的 period 重建是独立单飞 scope（scope_key 含 scope/period）→ 允许并行。
    period = await _post_rebuild(
        client,
        api_prefix,
        user.headers,
        body={"scope": "period", "period": "2026-06"},
    )
    assert period.status_code == 202, period.text
    assert period.json()["job_id"] != body["job_id"]

    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{body['job_id']}", headers=user.headers)
    assert job.json()["status"] == "succeeded", job.text


async def test_api_028_requires_idempotency_key(client, api_prefix, user) -> None:
    resp = await client.post(
        f"{api_prefix}/jobs/rebuild-rounds", headers=user.headers, json={"scope": "full"}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    short = await client.post(
        f"{api_prefix}/jobs/rebuild-rounds",
        headers={**user.headers, "Idempotency-Key": "short"},
        json={"scope": "full"},
    )
    assert short.status_code == 422


async def test_api_028_period_validation(client, api_prefix, user) -> None:
    missing_period = await _post_rebuild(client, api_prefix, user.headers, body={"scope": "period"})
    assert missing_period.status_code == 422
    assert missing_period.json()["code"] == ErrorCode.INVALID_PERIOD.value

    bad_period = await _post_rebuild(
        client, api_prefix, user.headers, body={"scope": "period", "period": "2026-13"}
    )
    assert bad_period.status_code == 422
    assert bad_period.json()["code"] == ErrorCode.INVALID_PERIOD.value

    full_with_period = await _post_rebuild(
        client, api_prefix, user.headers, body={"scope": "full", "period": "2026-06"}
    )
    assert full_with_period.status_code == 422


async def test_api_028_manual_full_shares_scope_with_auto_rebuild(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    """自动 full rebuild（reclassify 成功后入队）与手动 API-028 full 必须同一单飞 scope。

    否则两条 full 会并发 delete/insert 同一个用户的回合表。这里先只执行 reclassify
    job（它会自动 enqueue full rebuild），再 POST 手动 full：同 scope 异参 → 409。
    """
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    reclassify = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": _idem_key()},
        json={},
    )
    assert reclassify.status_code == 202, reclassify.text
    assert await _execute_one_pending(settings) is True

    manual = await _post_rebuild(client, api_prefix, user.headers)
    assert manual.status_code == 409, manual.text
    assert manual.json()["code"] == ErrorCode.JOB_ALREADY_RUNNING.value

    await _drain_worker(settings)
    jobs = (await client.get(f"{api_prefix}/jobs?limit=200", headers=user.headers)).json()["items"]
    rebuild_jobs = [item for item in jobs if item["type"] == "rebuild_rounds"]
    assert len(rebuild_jobs) == 1, "手动 full 不应在自动 full 之外再建一个并行任务"
    assert rebuild_jobs[0]["status"] == "succeeded"


async def test_api_015_revoke_enqueues_full_rebuild_with_same_scope(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    """API-015（同步撤销）入队的 full rebuild 必须与手动 API-028 full 同一单飞 scope。"""
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
        batch_id=batch_id,
    )
    revoked = await client.post(
        f"{api_prefix}/imports/{batch_id}/revoke", headers=user.headers, json={}
    )
    assert revoked.status_code == 200, revoked.text

    jobs = (await client.get(f"{api_prefix}/jobs?limit=200", headers=user.headers)).json()["items"]
    rebuild_jobs = [item for item in jobs if item["type"] == "rebuild_rounds"]
    assert len(rebuild_jobs) == 1, "撤销成功应在同一事务内入队一次 full rebuild"

    # 自动任务仍 pending：手动 full 同 scope 异参（reason）→ 409 而不是并发跑第二条。
    manual = await _post_rebuild(client, api_prefix, user.headers)
    assert manual.status_code == 409, manual.text
    assert manual.json()["code"] == ErrorCode.JOB_ALREADY_RUNNING.value

    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{rebuild_jobs[0]['job_id']}", headers=user.headers)
    assert job.json()["status"] == "succeeded", job.text


async def test_api_029_rounds_visible_after_rebuild(
    client, api_prefix, user, account_factory, admin, owner_engine: AsyncEngine, settings
) -> None:
    _ids = await _seed_round_scenario(owner_engine, user_id=user.user_id)
    await _rebuild_and_drain(client, api_prefix, user, settings)

    body = await _rounds(client, api_prefix, user.headers)
    items = body["items"]
    assert len(items) == 4, items  # 1 closed + 3 open（各剩余 lot 一个 open 回合）
    assert body["has_more"] is False
    closed = [item for item in items if item["status"] == "closed"]
    assert len(closed) == 1
    round_ = closed[0]
    assert round_["security_code"] == "600519"
    assert abs(Decimal(round_["pnl"]) - Decimal("1200.00")) <= Decimal("0.01")
    assert round_["cost"] == "6000.00"
    assert round_["proceeds"] == "7200.00"
    assert round_["quantity"] == "60.0000"
    assert round_["return_rate"] == "0.20000000"
    assert _MONEY_RE.match(round_["pnl"])
    assert _MONEY_RE.match(round_["cost"])
    assert _QTY_RE.match(round_["quantity"])
    assert _RATIO_RE.match(round_["return_rate"])
    assert round_["fills_count"] == 1
    open_rounds = [item for item in items if item["status"] == "open"]
    assert sum(Decimal(item["cost"]) for item in open_rounds) == Decimal("12900.00")

    intruder = await account_factory()
    isolated = await _rounds(client, api_prefix, intruder.headers)
    assert isolated["items"] == [], "B 的回合列表泄露了 A 的数据"

    forbidden = await client.get(f"{api_prefix}/rounds", headers=admin.headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_api_029_cursor_paging_no_dup_no_missing(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    await _seed_round_scenario(owner_engine, user_id=user.user_id)
    await _rebuild_and_drain(client, api_prefix, user, settings)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = "?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = await _rounds(client, api_prefix, user.headers, query)
        seen.extend(item["round_id"] for item in page["items"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
        assert cursor
    assert len(seen) == len(set(seen)) == 4

    closed_only = await _rounds(client, api_prefix, user.headers, "?limit=200&status=closed")
    assert [item["status"] for item in closed_only["items"]] == ["closed"]

    other_security = await _rounds(
        client, api_prefix, user.headers, "?limit=200&security_code=000001"
    )
    assert other_security["items"] == []


async def test_api_030_round_detail_pnl_and_isolation(
    client, api_prefix, user, account_factory, admin, owner_engine: AsyncEngine, settings
) -> None:
    await _seed_round_scenario(owner_engine, user_id=user.user_id)
    await _rebuild_and_drain(client, api_prefix, user, settings)
    closed = await _closed_round(client, api_prefix, user.headers)

    resp = await client.get(f"{api_prefix}/rounds/{closed['round_id']}", headers=user.headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["round_id"] == closed["round_id"]
    assert abs(Decimal(detail["pnl"]) - Decimal("1200.00")) <= Decimal("0.01")
    assert detail["open_trade_id"] != detail["close_trade_id"]

    intruder = await account_factory()
    cross = await client.get(f"{api_prefix}/rounds/{closed['round_id']}", headers=intruder.headers)
    assert cross.status_code == 404
    assert cross.json()["code"] == ErrorCode.NOT_FOUND.value

    missing = await client.get(f"{api_prefix}/rounds/rnd_does_not_exist", headers=user.headers)
    assert missing.status_code == 404

    forbidden = await client.get(f"{api_prefix}/rounds/{closed['round_id']}", headers=admin.headers)
    assert forbidden.status_code == 403


async def test_api_031_fills_fifo_pairing(
    client, api_prefix, user, account_factory, admin, owner_engine: AsyncEngine, settings
) -> None:
    ids = await _seed_round_scenario(owner_engine, user_id=user.user_id)
    await _rebuild_and_drain(client, api_prefix, user, settings)
    closed = await _closed_round(client, api_prefix, user.headers)

    resp = await client.get(f"{api_prefix}/rounds/{closed['round_id']}/fills", headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == len(body["items"]) == 1
    fill = body["items"][0]
    assert fill["sequence"] == 1
    assert fill["buy_trade_id"] == ids["buy1"], "FIFO 应消耗最早买入的 lot"
    assert fill["sell_trade_id"] == ids["sell"]
    assert fill["quantity"] == "60.0000"
    assert fill["buy_cost"] == "6000.00"
    assert fill["sell_proceeds"] == "7200.00"
    assert fill["pnl"] == "1200.00"
    assert _MONEY_RE.match(fill["pnl"])

    # FIFO trade_id 集合与手算一致（buy1 被消耗 60 股；buy2/buy3 仍在 open 回合）
    buy_ids = {fill["buy_trade_id"]}
    sell_ids = {fill["sell_trade_id"]}
    assert buy_ids == {ids["buy1"]}
    assert sell_ids == {ids["sell"]}

    open_rounds = [
        item
        for item in (await _rounds(client, api_prefix, user.headers))["items"]
        if item["status"] == "open"
    ]
    for item in open_rounds:
        empty = await client.get(
            f"{api_prefix}/rounds/{item['round_id']}/fills", headers=user.headers
        )
        assert empty.status_code == 200
        assert empty.json()["total"] == 0

    intruder = await account_factory()
    cross = await client.get(
        f"{api_prefix}/rounds/{closed['round_id']}/fills", headers=intruder.headers
    )
    assert cross.status_code == 404
    assert cross.json()["code"] == ErrorCode.NOT_FOUND.value

    forbidden = await client.get(
        f"{api_prefix}/rounds/{closed['round_id']}/fills", headers=admin.headers
    )
    assert forbidden.status_code == 403
