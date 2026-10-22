"""JobQueue 语义单测（G2）：单飞 / 心跳续租 / 取消终态 / 租约回收原子性。

连真实 PostgreSQL：部分唯一索引、`FOR UPDATE SKIP LOCKED`、条件 UPDATE 必须真库验证。
时间用 `FrozenClock` 推进，租约过期不依赖 sleep。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from stock_contracts.clock import FrozenClock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.db import Database
from stock_platform.jobs import LEASE_SECONDS, JobQueue
from stock_platform.models import Job, JobEvent


async def _enqueue(
    db: Database,
    queue: JobQueue,
    user_id: uuid.UUID,
    *,
    kind: str = "import_commit",
    scope: str | None = None,
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[str, bool]:
    async with db.session(begin=True) as session:
        return await queue.enqueue(
            session,
            kind=kind,
            user_id=user_id,
            scope_key=scope or f"{user_id}:{uuid.uuid4().hex[:8]}",
            params=params if params is not None else {},
            **kwargs,
        )


async def _claim_one(db: Database, queue: JobQueue) -> Job | None:
    async with db.session(begin=True) as session:
        return await queue.claim(session)


async def _load(db: Database, queue: JobQueue, job_id: str) -> Job:
    async with db.session(begin=True) as session:
        job = await queue.get(session, job_id)
    assert job is not None
    return job


async def _events(db: Database, job_id: str) -> list[JobEvent]:
    async with db.session(begin=True) as session:
        rows = await session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)
        )
        return list(rows)


# ── 单飞：同参复用 / 异参 409 ───────────────────────────────


async def test_enqueue_same_params_reuses_existing(
    db: Database, queue: JobQueue, user_id: uuid.UUID
) -> None:
    scope = f"{user_id}:preview-1"
    first, reused = await _enqueue(
        db,
        queue,
        user_id,
        scope=scope,
        params={"preview_id": "p1", "mode": "full", "request_id": "r1"},
    )
    assert reused is False
    # request_id 是易变字段，不参与 digest → 仍应复用同一任务（不能重复跑）。
    second, reused_again = await _enqueue(
        db,
        queue,
        user_id,
        scope=scope,
        params={"preview_id": "p1", "mode": "full", "request_id": "r2"},
    )
    assert (second, reused_again) == (first, True)


async def test_enqueue_ignores_internal_batch_id_in_digest(
    db: Database, queue: JobQueue, user_id: uuid.UUID
) -> None:
    """batch_id 是服务端每次请求新生成的内部参数，不能导致同业务请求被误判为异参。"""
    scope = f"{user_id}:preview-internal"
    first, _ = await _enqueue(
        db,
        queue,
        user_id,
        scope=scope,
        params={"preview_id": "p1", "mode": "full", "batch_id": "bat_a"},
    )
    second, reused = await _enqueue(
        db,
        queue,
        user_id,
        scope=scope,
        params={"preview_id": "p1", "mode": "full", "batch_id": "bat_b"},
    )
    assert (second, reused) == (first, True)


async def test_enqueue_different_params_raises_job_already_running(
    db: Database, queue: JobQueue, user_id: uuid.UUID
) -> None:
    scope = f"{user_id}:preview-2"
    first, _ = await _enqueue(db, queue, user_id, scope=scope, params={"mode": "full"})
    with pytest.raises(ApiError) as excinfo:
        await _enqueue(db, queue, user_id, scope=scope, params={"mode": "replace_range"})
    assert excinfo.value.code is ErrorCode.JOB_ALREADY_RUNNING
    assert excinfo.value.http_status == 409
    assert first in (excinfo.value.detail or "")


# ── 心跳续租 / progress 顺带续租 ────────────────────────────


async def test_heartbeat_renews_lease(
    db: Database, queue: JobQueue, clock: FrozenClock, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None and claimed.id == job_id
    original_lease = claimed.lease_until
    assert original_lease is not None

    clock.advance(seconds=LEASE_SECONDS - 5)
    async with db.session(begin=True) as session:
        await queue.heartbeat(session, job_id)

    job = await _load(db, queue, job_id)
    assert job.heartbeat_at == clock.now()
    assert job.lease_until == clock.now() + timedelta(seconds=LEASE_SECONDS)
    assert job.lease_until > original_lease


async def test_progress_also_renews_lease(
    db: Database, queue: JobQueue, clock: FrozenClock, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None
    original_lease = claimed.lease_until
    assert original_lease is not None

    clock.advance(seconds=LEASE_SECONDS - 5)
    async with db.session(begin=True) as session:
        await queue.progress(session, job_id, percent=35, stage="writing", message=None)

    job = await _load(db, queue, job_id)
    assert job.lease_until == clock.now() + timedelta(seconds=LEASE_SECONDS)
    assert job.lease_until > original_lease
    assert job.progress == 35
    assert [event.type for event in await _events(db, job_id)][-1] == "progress"


# ── 取消语义 ───────────────────────────────────────────────


async def test_cancel_pending_is_terminal_immediately(
    db: Database, queue: JobQueue, clock: FrozenClock, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id)

    async with db.session(begin=True) as session:
        status = await queue.request_cancel(session, job_id)
    assert status is JobStatus.CANCELLED

    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.CANCELLED.value
    assert job.cancel_requested is True
    assert job.finished_at == clock.now()
    assert job.lease_until is None
    events = await _events(db, job_id)
    assert events[-1].type == JobStatus.CANCELLED.value

    # 重复取消幂等：不再重复发 cancelled 事件。
    async with db.session(begin=True) as session:
        again = await queue.request_cancel(session, job_id)
    assert again is JobStatus.CANCELLED
    assert len(await _events(db, job_id)) == len(events)


async def test_cancel_running_only_flags_until_worker_checkpoint(
    db: Database, queue: JobQueue, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None and claimed.id == job_id

    async with db.session(begin=True) as session:
        status = await queue.request_cancel(session, job_id)
    assert status is JobStatus.RUNNING

    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.RUNNING.value
    assert job.cancel_requested is True
    assert job.finished_at is None
    assert JobStatus.CANCELLED.value not in [event.type for event in await _events(db, job_id)]

    # worker 在检查点收口：succeeded 会被改写成 cancelled（不会出现"取消后还成功"）。
    async with db.session(begin=True) as session:
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result={"ok": True})
    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.CANCELLED.value
    assert job.finished_at is not None


# ── 租约回收（reaper）──────────────────────────────────────


async def test_reap_expired_retries_and_is_idempotent(
    db: Database, queue: JobQueue, clock: FrozenClock, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None and claimed.attempt == 1

    clock.advance(seconds=LEASE_SECONDS + 1)
    async with db.session(begin=True) as session:
        assert await queue.reap_expired_leases(session) == 1

    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.PENDING.value
    assert job.attempt == 1  # attempt 不因回收而涨
    assert job.lease_until is None
    assert [event.type for event in await _events(db, job_id)][-1] == "retrying"

    # 已重投的任务不会在同一时钟下被重复回收。
    async with db.session(begin=True) as session:
        assert await queue.reap_expired_leases(session) == 0


async def test_reap_expired_after_max_attempts_fails_with_lease_expired(
    db: Database, queue: JobQueue, clock: FrozenClock, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id, max_attempts=1)
    claimed = await _claim_one(db, queue)
    assert claimed is not None and claimed.attempt == 1

    clock.advance(seconds=LEASE_SECONDS + 1)
    async with db.session(begin=True) as session:
        assert await queue.reap_expired_leases(session) == 1

    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.FAILED.value
    assert job.error_code == ErrorCode.JOB_LEASE_EXPIRED.value
    assert job.finished_at is not None
    assert [event.type for event in await _events(db, job_id)][-1] == "failed"


async def test_reap_ignores_running_job_with_valid_lease(
    db: Database, queue: JobQueue, user_id: uuid.UUID
) -> None:
    await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None

    async with db.session(begin=True) as session:
        assert await queue.reap_expired_leases(session) == 0
    job = await _load(db, queue, claimed.id)
    assert job.status == JobStatus.RUNNING.value


async def test_reap_skips_rows_locked_by_another_session(
    db: Database, queue: JobQueue, clock: FrozenClock, user_id: uuid.UUID
) -> None:
    """原子性：另一个事务锁住过期行时，reaper 的 SKIP LOCKED 必须跳过而不是重复回收。"""
    job_id, _ = await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None
    clock.advance(seconds=LEASE_SECONDS + 1)

    async with db.session(begin=True) as holder:
        locked = await holder.scalar(select(Job.id).where(Job.id == job_id).with_for_update())
        assert locked == job_id
        # 并发 reaper（另一连接）：行被锁 → 一个都回收不到。
        async with db.session(begin=True) as other:
            assert await queue.reap_expired_leases(other) == 0
        job = await _load(db, queue, job_id)
        assert job.status == JobStatus.RUNNING.value

    # 锁释放后条件 UPDATE 才生效。
    async with db.session(begin=True) as session:
        assert await queue.reap_expired_leases(session) == 1
    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.PENDING.value


# ── 失败收口不覆盖终态 ─────────────────────────────────────


async def test_fail_or_retry_does_not_overwrite_terminal_job(
    db: Database, queue: JobQueue, user_id: uuid.UUID
) -> None:
    job_id, _ = await _enqueue(db, queue, user_id)
    claimed = await _claim_one(db, queue)
    assert claimed is not None
    async with db.session(begin=True) as session:
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result={"ok": True})

    async with db.session(begin=True) as session:
        status = await queue.fail_or_retry(
            session, job_id, error_code="INTERNAL_ERROR", error={"detail": "late"}
        )
    assert status is JobStatus.SUCCEEDED
    job = await _load(db, queue, job_id)
    assert job.status == JobStatus.SUCCEEDED.value
    assert job.error_code is None
