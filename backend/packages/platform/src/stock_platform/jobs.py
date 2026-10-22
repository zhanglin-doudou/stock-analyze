"""任务队列（Architecture §5 / §14.5）：PG SKIP LOCKED + 租约 + 单飞 + 配额。

不引入外部 broker；崩溃恢复靠租约回收（心跳超时 → 重投或终态 failed）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode

from stock_platform.cursor import decode_cursor, encode_cursor
from stock_platform.db import Database
from stock_platform.ids import job_id as new_job_id
from stock_platform.models import Job, JobEvent

LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20
DEFAULT_MAX_ATTEMPTS = 3
USER_CONCURRENT_LIMIT = 4


def canonical_params(params: dict[str, Any]) -> str:
    """确定性序列化：同语义参数 → 同哈希（单飞/幂等前提）。"""
    volatile = {"request_id", "idempotency_key", "trace_id", "ts"}
    cleaned = {k: v for k, v in params.items() if k not in volatile}
    return json.dumps(
        cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


# 服务端内部参数：同一业务请求重试时会重新生成，不能参与单飞语义比较。
# 例：/imports 每次请求预建 PENDING 批次并写入 params.batch_id；若把它算进 digest，
# 同 preview 同参数的第二次提交会被误判为“异参”而 409，而不是复用同一 job。
_INTERNAL_PARAMS = frozenset({"batch_id"})


def semantic_params(params: dict[str, Any]) -> dict[str, Any]:
    """去掉服务端内部参数后的业务语义参数（单飞/幂等比较用）。"""
    return {k: v for k, v in params.items() if k not in _INTERNAL_PARAMS}


def params_digest(params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_params(semantic_params(params)).encode("utf-8")).hexdigest()


class JobQueue:
    """PG 实现；`scope_key` 决定单飞范围（如 user:period）。"""

    __slots__ = ("_clock", "_db")

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    # ── 入队 ────────────────────────────────────────────────
    async def enqueue(
        self,
        session: AsyncSession,
        *,
        kind: str,
        user_id: uuid.UUID,
        scope_key: str,
        params: dict[str, Any],
        priority: int = 100,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        concurrent_limit: int = USER_CONCURRENT_LIMIT,
    ) -> tuple[str, bool]:
        """返回 (job_id, reused)。reused=True 表示复用同参数的既有任务。"""
        digest = params_digest(params)

        existing = await self._find_active(session, kind=kind, scope_key=scope_key)
        if existing is not None:
            if params_digest(existing.params) == digest:
                return existing.id, True
            # 单飞但参数不同：静默复用会跑错参数（如 rebuild 的区间/模式），必须 409。
            raise ApiError(
                ErrorCode.JOB_ALREADY_RUNNING,
                detail=f"已有任务 {existing.id} 正在执行",
            )

        running = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.user_id == user_id,
                Job.status.in_((JobStatus.PENDING.value, JobStatus.RUNNING.value)),
            )
        )
        if int(running or 0) >= concurrent_limit:
            raise ApiError(ErrorCode.QUOTA_EXCEEDED)

        candidate = new_job_id()
        stmt = (
            pg_insert(Job)
            .values(
                id=candidate,
                user_id=user_id,
                kind=kind,
                scope_key=scope_key,
                dedupe_key=f"{kind}:{scope_key}:{digest}",
                canonical_params=canonical_params(params),
                params=params,
                status=JobStatus.PENDING.value,
                priority=priority,
                max_attempts=max_attempts,
                progress=0,
                stage="queued",
            )
            .on_conflict_do_nothing()
            .returning(Job.id)
        )
        inserted = await session.scalar(stmt)
        if inserted is not None:
            await self.emit(session, inserted, "queued", {"kind": kind})
            return inserted, False

        # 竞态：并发入队被单飞索引挡住 → 同参复用；异参同样 409（不能返回旧 job 骗调用方）。
        raced = await self._find_active(session, kind=kind, scope_key=scope_key)
        if raced is None:  # pragma: no cover - 理论上不可达
            raise ApiError(ErrorCode.INTERNAL_ERROR)
        if params_digest(raced.params) != digest:
            raise ApiError(
                ErrorCode.JOB_ALREADY_RUNNING,
                detail=f"已有任务 {raced.id} 正在执行",
            )
        return raced.id, True

    async def _find_active(self, session: AsyncSession, *, kind: str, scope_key: str) -> Job | None:
        return await session.scalar(
            select(Job).where(
                Job.kind == kind,
                Job.scope_key == scope_key,
                Job.status.in_((JobStatus.PENDING.value, JobStatus.RUNNING.value)),
            )
        )

    async def get(self, session: AsyncSession, job_id: str) -> Job | None:
        return await session.scalar(select(Job).where(Job.id == job_id))

    async def get_for_user(
        self, session: AsyncSession, user_id: uuid.UUID, job_id: str
    ) -> Job | None:
        """按 `user_id` 过滤取任务（跨账号一律按不存在处理，§14.2）。"""
        return await session.scalar(select(Job).where(Job.user_id == user_id, Job.id == job_id))

    async def list_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Job], str | None]:
        """按创建时间倒序的游标分页（新任务写入不导致翻页错位）。"""
        capped = max(1, min(limit, 200))
        stmt = select(Job).where(Job.user_id == user_id)
        if statuses:
            stmt = stmt.where(Job.status.in_(statuses))
        if cursor is not None:
            payload = decode_cursor(cursor)
            try:
                cursor_at = datetime.fromisoformat(payload["c"])
                cursor_id = payload["i"]
            except (KeyError, ValueError) as exc:
                raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from exc
            stmt = stmt.where(
                (Job.created_at < cursor_at)
                | ((Job.created_at == cursor_at) & (Job.id < cursor_id))
            )
        stmt = stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(capped + 1)
        rows = list(await session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"c": last.created_at.isoformat(), "i": last.id})
        return rows, next_cursor

    async def count_by_status(self, session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
        """积压统计（`/jobs` 的 backlog 字段）。"""
        rows = await session.execute(
            select(Job.status, func.count()).where(Job.user_id == user_id).group_by(Job.status)
        )
        stats = {status.value: 0 for status in JobStatus}
        for status_value, count in rows:
            stats[str(status_value)] = int(count)
        return stats

    # ── 领取 / 心跳 ─────────────────────────────────────────
    async def claim(
        self, session: AsyncSession, *, kinds: tuple[str, ...] | None = None
    ) -> Job | None:
        now = self._clock.now()
        selection = (
            select(Job.id)
            .where(
                Job.status == JobStatus.PENDING.value,
                Job.available_at <= now,
                Job.cancel_requested.is_(False),
            )
            .order_by(Job.priority, Job.available_at, Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if kinds:
            selection = selection.where(Job.kind.in_(kinds))
        claimed = await session.scalar(
            update(Job)
            .where(Job.id == selection.scalar_subquery())
            .values(
                status=JobStatus.RUNNING.value,
                attempt=Job.attempt + 1,
                started_at=now,
                lease_until=now + timedelta(seconds=LEASE_SECONDS),
                heartbeat_at=now,
                stage="parsing",
                message=None,
            )
            .returning(Job.id)
        )
        if claimed is None:
            return None
        job = await self.get(session, claimed)
        return job

    async def heartbeat(self, session: AsyncSession, job_id: str) -> None:
        now = self._clock.now()
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.RUNNING.value)
            .values(heartbeat_at=now, lease_until=now + timedelta(seconds=LEASE_SECONDS))
        )

    async def progress(
        self, session: AsyncSession, job_id: str, *, percent: int, stage: str, message: str | None
    ) -> None:
        # 顺带续租：handler 在长循环里上报进度时刷新租约，避免被 reaper 误回收。
        now = self._clock.now()
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                progress=percent,
                stage=stage,
                message=message,
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=LEASE_SECONDS),
            )
        )
        await self.emit(
            session, job_id, "progress", {"progress": percent, "stage": stage, "message": message}
        )

    async def request_cancel(self, session: AsyncSession, job_id: str) -> JobStatus | None:
        """请求取消：PENDING 直接终态 cancelled（立即可见）；RUNNING 仅置 flag。

        返回取消请求后的实际状态；任务不存在返回 None。条件 UPDATE 保证与 worker
        的 claim 不打架（claim 用 status='pending' 做条件，谁先赢谁生效）。
        """
        now = self._clock.now()
        cancelled = await session.scalar(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PENDING.value)
            .values(
                status=JobStatus.CANCELLED.value,
                cancel_requested=True,
                finished_at=now,
                lease_until=None,
                heartbeat_at=None,
            )
            .returning(Job.id)
        )
        if cancelled is not None:
            await self.emit(session, job_id, JobStatus.CANCELLED.value, {"cancelled": True})
            return JobStatus.CANCELLED
        current = await session.scalar(select(Job.status).where(Job.id == job_id))
        if current is None:
            return None
        status = JobStatus(current)
        if status is JobStatus.RUNNING:
            # RUNNING 不能由 API 直接改终态（handler 正在写库）；由 worker 在检查点收口。
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING.value)
                .values(cancel_requested=True)
            )
        return status

    async def is_cancel_requested(self, session: AsyncSession, job_id: str) -> bool:
        value = await session.scalar(select(Job.cancel_requested).where(Job.id == job_id))
        return bool(value)

    async def finish(
        self,
        session: AsyncSession,
        job_id: str,
        *,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        result_ref: str | None = None,
        error_code: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        job = await self.get(session, job_id)
        if job is None:  # pragma: no cover
            return
        if job.cancel_requested and status is JobStatus.SUCCEEDED:
            status = JobStatus.CANCELLED
        now = self._clock.now()
        job.status = status.value
        job.finished_at = now
        job.lease_until = None
        job.result = result
        job.result_ref = result_ref
        job.error_code = error_code
        job.error = error
        if status is JobStatus.SUCCEEDED:
            job.progress = 100
            job.stage = "done"
        await self.emit(
            session, job_id, status.value, {"progress": job.progress, "stage": job.stage}
        )

    async def fail_or_retry(
        self, session: AsyncSession, job_id: str, *, error_code: str, error: dict[str, Any]
    ) -> JobStatus:
        """租约内失败：可重试则回 pending，否则终态 failed。"""
        job = await self.get(session, job_id)
        if job is None:  # pragma: no cover
            return JobStatus.FAILED
        current = JobStatus(job.status)
        if current.is_terminal:
            # 已被取消/已完成：外层兜底收口不能覆盖终态（handler 自己已收口后抛异常的场景）。
            return current
        now = self._clock.now()
        if job.attempt < job.max_attempts and not job.cancel_requested:
            job.status = JobStatus.PENDING.value
            job.available_at = now + timedelta(seconds=min(2**job.attempt * 5, 60))
            job.lease_until = None
            job.error_code = error_code
            job.error = error
            await self.emit(session, job_id, "retrying", {"attempt": job.attempt})
            return JobStatus.PENDING
        job.status = JobStatus.FAILED.value
        job.finished_at = now
        job.lease_until = None
        job.error_code = error_code
        job.error = error
        await self.emit(session, job_id, "failed", {"code": error_code})
        return JobStatus.FAILED

    async def reap_expired_leases(self, session: AsyncSession) -> int:
        """崩溃恢复：心跳超时 → 重投或 JOB_LEASE_EXPIRED 终态。

        并发安全（§5.1）：先用 `FOR UPDATE SKIP LOCKED` 锁住过期行（另一个 worker 正在
        回收的行直接跳过），再用条件 UPDATE（仍要求 status='running' 且 lease_until < now）
        二次校验后落终态/重投——即使两个 reaper 同时扫到，也只有一个能改到行。
        """
        now = self._clock.now()
        expired = (
            Job.status == JobStatus.RUNNING.value,
            Job.lease_until.is_not(None),
            Job.lease_until < now,
        )
        locked_ids = list(
            await session.scalars(select(Job.id).where(*expired).with_for_update(skip_locked=True))
        )
        if not locked_ids:
            return 0

        retried = list(
            await session.execute(
                update(Job)
                .where(*expired, Job.id.in_(locked_ids), Job.attempt < Job.max_attempts)
                .values(
                    status=JobStatus.PENDING.value,
                    available_at=now,
                    lease_until=None,
                    heartbeat_at=None,
                )
                .returning(Job.id, Job.attempt)
            )
        )
        failed = list(
            await session.execute(
                update(Job)
                .where(*expired, Job.id.in_(locked_ids), Job.attempt >= Job.max_attempts)
                .values(
                    status=JobStatus.FAILED.value,
                    finished_at=now,
                    lease_until=None,
                    heartbeat_at=None,
                    error_code=ErrorCode.JOB_LEASE_EXPIRED.value,
                    error={"lease_expired_at": now.isoformat()},
                )
                .returning(Job.id, Job.attempt)
            )
        )
        for job_id, attempt in retried:
            await self.emit(session, job_id, "retrying", {"attempt": attempt})
        for job_id, _attempt in failed:
            await self.emit(session, job_id, "failed", {"code": ErrorCode.JOB_LEASE_EXPIRED.value})
        return len(retried) + len(failed)

    # ── 事件（SSE 重放）─────────────────────────────────────
    async def emit(
        self, session: AsyncSession, job_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        # 会话 autoflush=False：先 flush 让本事务里先前 add 的事件落到 DB，
        # 否则 `max(seq)` 读不到它们，连续两次 emit 会算出同一个 seq 而撞唯一约束。
        await session.flush()
        seq = await session.scalar(
            select(func.coalesce(func.max(JobEvent.seq), 0)).where(JobEvent.job_id == job_id)
        )
        session.add(
            JobEvent(job_id=job_id, seq=int(seq or 0) + 1, type=event_type, payload=payload)
        )

    async def events_since(
        self, session: AsyncSession, job_id: str, *, after_seq: int = 0
    ) -> list[JobEvent]:
        rows = await session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
            .order_by(JobEvent.seq)
        )
        return list(rows)


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


async def purge_old_job_events(session: AsyncSession, *, older_than_days: int = 7) -> int:
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await session.execute(
        text("delete from platform.job_event where at < :cutoff"), {"cutoff": cutoff}
    )
    # AsyncSession.execute 静态返回 Result（不含 rowcount）；运行时实为 CursorResult，
    # 与 repositories/imports.py 一致地用 getattr 兜底取影响行数。
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount or 0)
