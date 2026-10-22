"""G2 队列语义的 API 验收：pending 取消终态 + 单飞异参 409。

旧 API-064 用例只断言 202；本文件补「取消后 GET 一致 + 事件落地」与
「同 (kind,scope_key) 参数不同 → 409 JOB_ALREADY_RUNNING」，不改旧断言。
数据库走环境变量指定的独立库（验收用 `stock_p2_g2`）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_platform.models import Job, JobEvent

pytestmark = pytest.mark.api

_HEADER = (
    "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,佣金,印花税,其他费,资金余额"
)
_ROWS = ["2026-06-30,600519,贵州茅台,买入,100,1500.00,150000.00,5.00,0.00,0.10,100000.00"]


def _bill_bytes() -> bytes:
    return ("\n".join([_HEADER, *_ROWS]) + "\n").encode("utf-8")


async def _seed_job(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    status: str = "pending",
    stage: str = "queued",
    attempt: int = 1,
) -> str:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Job(
                id=job_id,
                user_id=user_id,
                kind="import_commit",
                scope_key=f"{user_id}:{uuid.uuid4().hex[:6]}",
                dedupe_key=f"import_commit:{uuid.uuid4().hex[:8]}",
                canonical_params="{}",
                params={},
                status=status,
                priority=100,
                attempt=attempt,
                max_attempts=3,
                progress=0,
                stage=stage,
            )
        )
        await session.commit()
    return job_id


async def _job_events(engine: AsyncEngine, job_id: str) -> list[JobEvent]:
    async with AsyncSession(engine) as session:
        rows = await session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)
        )
        return list(rows)


async def _preview(client, api_prefix, user) -> str:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("bill.csv", _bill_bytes(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["preview_id"])


async def _commit(client, api_prefix, user, preview_id: str):
    return await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview_id, "mode": "full"},
    )


# ── API-064：pending 取消 → 终态 cancelled（立即可见）────────


async def test_api_064_cancel_pending_is_terminal_and_consistent(
    client, api_prefix, user, owner_engine
) -> None:
    job_id = await _seed_job(owner_engine, user_id=user.user_id, status="pending")

    resp = await client.post(f"{api_prefix}/jobs/{job_id}/cancel", headers=user.headers)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "cancelled", body
    assert body["finished_at"] is not None

    detail = await client.get(f"{api_prefix}/jobs/{job_id}", headers=user.headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "cancelled"
    assert detail.json()["finished_at"] == body["finished_at"]

    events = await _job_events(owner_engine, job_id)
    assert [event.type for event in events] == ["cancelled"]


async def test_api_064_cancel_running_only_requests_cancel(
    client, api_prefix, user, owner_engine
) -> None:
    job_id = await _seed_job(owner_engine, user_id=user.user_id, status="running", stage="writing")

    resp = await client.post(f"{api_prefix}/jobs/{job_id}/cancel", headers=user.headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"
    assert resp.json()["finished_at"] is None

    detail = await client.get(f"{api_prefix}/jobs/{job_id}", headers=user.headers)
    assert detail.json()["status"] == "running"

    async with AsyncSession(owner_engine) as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.cancel_requested is True
    assert [event.type for event in await _job_events(owner_engine, job_id)] == []


# ── 单飞：同参复用 / 异参 409 ──────────────────────────────


async def test_api_011_single_flight_same_params_reuses_job(client, api_prefix, user) -> None:
    preview_id = await _preview(client, api_prefix, user)
    first = await _commit(client, api_prefix, user, preview_id)
    assert first.status_code == 202, first.text
    first_job_id = first.json()["job_id"]

    # 幂等键不同但业务参数相同（batch_id 是每次请求新生成的内部参数，不参与单飞比较）
    # → 复用同一 job，而不是 409。
    second = await _commit(client, api_prefix, user, preview_id)
    assert second.status_code == 202, second.text
    assert second.json()["job_id"] == first_job_id
    assert second.headers.get("X-Job-Reused") == "true"


async def test_api_011_single_flight_different_params_conflicts_409(
    client, api_prefix, user
) -> None:
    preview_id = await _preview(client, api_prefix, user)
    first = await _commit(client, api_prefix, user, preview_id)
    assert first.status_code == 202, first.text
    first_job_id = first.json()["job_id"]

    # 同 (kind, scope_key) 但 mode 不同 → 必须 409 并带出旧 job_id，不能跑错参数。
    second = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={
            "preview_id": preview_id,
            "mode": "replace_range",
            "range": {"from": "2026-06-01", "to": "2026-06-30"},
        },
    )
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == ErrorCode.JOB_ALREADY_RUNNING.value
    assert first_job_id in body["detail"]
