"""API-016 验收：`POST /imports/{batch_id}/reclassify`（按新规则重跑该批次分类）。

覆盖 INTERFACE §4.1：
* happy：202 + `reclassify` job + Location/Retry-After；worker 跑完后新规则生效；
* 跨用户批次 404（不泄露存在性）/ 不存在批次 404；
* Idempotency-Key 必填（缺失/长度非法 → 422）、同键同体重放 → 同 job_id；
* 批次范围隔离：只复算目标批次，不碰其它批次的行（classify 的 batch_id 过滤）。
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, Trade
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api


async def _drain_worker(settings) -> None:
    """把队列里的任务全部领取并执行（真实 WorkerRunner，覆盖 worker 侧租户会话）。"""
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
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
                parsed_count=1,
                inserted_count=1,
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
    batch_id: str,
    summary: str,
    trade_date: date = date(2026, 6, 15),
) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=batch_id,
                trade_date=trade_date,
                security_code="600519",
                security_name="贵州茅台",
                side="buy",
                quantity_units=1_000_000,
                price_units=100_000_000,
                amount_units=-1_000_000,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=None,
                cash_balance_available=False,
                occurrence_idx=0,
                source="import",
                version=1,
                summary=summary,
                dedupe_key=f"600519:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


def _key() -> str:
    return "idem-" + uuid.uuid4().hex


async def _create_rule(client, api_prefix, user, pattern: str, category: str = "transfer") -> dict:
    resp = await client.post(
        f"{api_prefix}/summary-rules",
        headers=user.headers,
        json={
            "name": f"规则-{pattern[:8]}",
            "category": category,
            "side": "both",
            "match_type": "contains",
            "pattern": pattern,
            "priority": 20,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_api_016_reclassify_accepted_and_new_rule_applies(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    trade_id = await _seed_trade(
        owner_engine, user_id=user.user_id, batch_id=batch_id, summary="自定义业务XYZ"
    )

    resp = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": _key()},
        json={},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["type"] == "reclassify"
    assert body["batch_id"] == batch_id
    assert body["status"] == "pending"
    assert resp.headers["Location"] == f"/api/v1/jobs/{body['job_id']}"
    assert resp.headers["Retry-After"] == "1"

    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{body['job_id']}", headers=user.headers)
    assert job.json()["status"] == "succeeded", job.text

    # 新规则命中摘要 → 再 reclassify 后该批次明细分类更新。
    await _create_rule(client, api_prefix, user, "自定义业务XYZ")
    second = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": _key()},
        json={"reason": "规则更新"},
    )
    assert second.status_code == 202, second.text
    await _drain_worker(settings)

    trade = await client.get(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert trade.status_code == 200, trade.text
    assert trade.json()["category"] == "transfer"


async def test_api_016_cross_user_batch_404(
    client, api_prefix, user, account_factory, owner_engine: AsyncEngine
) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    intruder = await account_factory()
    resp = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**intruder.headers, "Idempotency-Key": _key()},
        json={},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


async def test_api_016_unknown_batch_404(client, api_prefix, user) -> None:
    resp = await client.post(
        f"{api_prefix}/imports/bat_does_not_exist/reclassify",
        headers={**user.headers, "Idempotency-Key": _key()},
        json={},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


async def test_api_016_requires_idempotency_key(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    missing = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify", headers=user.headers, json={}
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    too_short = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": "short"},
        json={},
    )
    assert too_short.status_code == 422


async def test_api_016_same_key_replays_same_job(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    key = _key()
    first = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": key},
        json={},
    )
    assert first.status_code == 202, first.text
    second = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": key},
        json={},
    )
    assert second.status_code == 202, second.text
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json()["job_id"] == first.json()["job_id"]

    jobs = await client.get(f"{api_prefix}/jobs?limit=200", headers=user.headers)
    reclassify_jobs = [item for item in jobs.json()["items"] if item["type"] == "reclassify"]
    assert len(reclassify_jobs) == 1, "同键同体不应产生第二个 reclassify job"


async def test_api_016_only_target_batch_is_reclassified(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    batch_a = await _seed_batch(owner_engine, user_id=user.user_id)
    batch_b = await _seed_batch(owner_engine, user_id=user.user_id)
    trade_a = await _seed_trade(
        owner_engine, user_id=user.user_id, batch_id=batch_a, summary="只在A批次命中"
    )
    trade_b = await _seed_trade(
        owner_engine, user_id=user.user_id, batch_id=batch_b, summary="只在A批次命中"
    )
    await _create_rule(client, api_prefix, user, "只在A批次命中")

    resp = await client.post(
        f"{api_prefix}/imports/{batch_a}/reclassify",
        headers={**user.headers, "Idempotency-Key": _key()},
        json={},
    )
    assert resp.status_code == 202, resp.text
    await _drain_worker(settings)

    item_a = await client.get(f"{api_prefix}/trades/{trade_a}", headers=user.headers)
    item_b = await client.get(f"{api_prefix}/trades/{trade_b}", headers=user.headers)
    assert item_a.json()["category"] == "transfer"
    assert item_b.json()["category"] is None, "批次范围过滤失效：B 批次被一起改了"
