"""任务端点（API-061~064）：列表含积压、详情进度、SSE 流、取消。

SSE 只是优化：客户端仍以 `GET /jobs/{id}` 轮询兜底（§5.3）。跨账号订阅
→ 404 且**不建立事件流**（先校验归属再返回 StreamingResponse）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from stock_contracts.enums import JobStage, JobStatus
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.jobs import JobQueue
from stock_platform.models import Job
from stock_platform.obs import get_logger
from stock_platform.tenant import TenantContext

from stock_api.deps import AppState, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import JobOut, JobPage

log = get_logger("stock_api.jobs")

router = APIRouter(tags=["jobs"], route_class=StrictQueryRoute)

SSE_POLL_SECONDS = 1.0
SSE_KEEPALIVE_SECONDS = 15.0


def _to_out(job: Job) -> JobOut:
    return JobOut(
        job_id=job.id,
        type=job.kind,
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        message=job.message,
        result_ref=job.result_ref,
        error=job.error,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        available_at=job.available_at,
        heartbeat_at=job.heartbeat_at,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _load_job(state: AppState, user_id: uuid.UUID, job_id: str) -> Job:
    """取任务并强制归属校验（跨账号 → 404）。"""
    async with state.db.session(begin=True) as session:
        job = await state.queue.get_for_user(session, user_id, job_id)
    if job is None:
        raise NotFoundError(detail=f"任务 {job_id} 不存在")
    return job


@router.get("/jobs", response_model=JobPage, operation_id="listJobs")
async def list_jobs(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    status: Annotated[str | None, Query(description="逗号分隔的状态过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> JobPage:
    """API-061：任务列表（含积压统计）。仅本人任务。"""
    user_id = ctx.require_user()
    state = get_state(request)
    statuses: tuple[str, ...] | None = None
    if status:
        parsed = [item.strip() for item in status.split(",") if item.strip()]
        valid = {member.value for member in JobStatus}
        invalid = [item for item in parsed if item not in valid]
        if invalid:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR, detail=f"非法的 status 过滤值: {', '.join(invalid)}"
            )
        statuses = tuple(parsed)
    async with state.db.session(begin=True) as session:
        rows, next_cursor = await state.queue.list_for_user(
            session, user_id, statuses=statuses, cursor=cursor, limit=limit
        )
        backlog = await state.queue.count_by_status(session, user_id)
    return JobPage(items=[_to_out(row) for row in rows], next_cursor=next_cursor, backlog=backlog)


@router.get("/jobs/{job_id}", response_model=JobOut, operation_id="getJob")
async def get_job(
    job_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> JobOut:
    """API-062：任务详情/进度；终态含 result_ref。"""
    user_id = ctx.require_user()
    job = await _load_job(get_state(request), user_id, job_id)
    return _to_out(job)


@router.get("/jobs/{job_id}/events", operation_id="streamJobEvents")
async def stream_job_events(
    job_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    last_event_id: Annotated[str | None, Query(alias="Last-Event-ID")] = None,
) -> Response:
    """API-063：SSE 进度流（`text/event-stream`）。

    先校验归属，跨账号直接 404，**不建立事件流**。
    """
    user_id = ctx.require_user()
    state = get_state(request)
    job = await _load_job(state, user_id, job_id)  # 归属校验（404 短路）
    after_seq = 0
    if last_event_id:
        try:
            after_seq = int(last_event_id)
        except ValueError as exc:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="Last-Event-ID 必须是整数") from exc

    return StreamingResponse(
        _event_stream(state, user_id, job_id, job.status, after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 反代不缓冲
        },
    )


def _sse_name(db_event_type: str) -> str:
    """DB 事件类型 → 契约事件类型（固定 progress/done/error，§5.3）。"""
    if db_event_type in (JobStatus.SUCCEEDED.value, JobStatus.CANCELLED.value):
        return "done"
    if db_event_type == JobStatus.FAILED.value:
        return "error"
    return "progress"


def _format_sse(seq: int, name: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\nid: {seq}\ndata: {payload}\n\n"


async def _event_stream(
    state: AppState,
    user_id: uuid.UUID,
    job_id: str,
    initial_status: str,
    after_seq: int,
) -> AsyncIterator[str]:
    queue: JobQueue = state.queue
    last_seq = after_seq
    last_keepalive = time.monotonic()
    status = initial_status
    try:
        while True:
            emitted_terminal = False
            result_ref: str | None = None
            progress_snapshot = {"progress": 0, "stage": JobStage.QUEUED.value, "message": None}
            async with state.db.session(begin=True) as session:
                job = await queue.get_for_user(session, user_id, job_id)
                if job is None:
                    status = JobStatus.FAILED.value
                    yield _format_sse(
                        last_seq, "error", {"job_id": job_id, "code": ErrorCode.NOT_FOUND.value}
                    )
                    return
                status = job.status
                result_ref = job.result_ref
                progress_snapshot = {
                    "progress": job.progress,
                    "stage": job.stage,
                    "message": job.message,
                }
                events = await queue.events_since(session, job_id, after_seq=last_seq)
                for ev in events:
                    last_seq = ev.seq
                    name = _sse_name(ev.type)
                    data: dict[str, object] = {
                        "job_id": job_id,
                        **ev.payload,
                        "at": ev.at.isoformat(),
                    }
                    if name != "progress":
                        data["status"] = job.status
                        data["result_ref"] = job.result_ref
                        emitted_terminal = True
                    yield _format_sse(ev.seq, name, data)

            if status in (
                JobStatus.SUCCEEDED.value,
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
            ):
                if not emitted_terminal:
                    # 重连落在终态之后：补发一次终态，保证客户端能收尾。
                    name = "error" if status == JobStatus.FAILED.value else "done"
                    yield _format_sse(
                        last_seq,
                        name,
                        {
                            "job_id": job_id,
                            "status": status,
                            "result_ref": result_ref,
                            **progress_snapshot,
                        },
                    )
                return
            if time.monotonic() - last_keepalive >= SSE_KEEPALIVE_SECONDS:
                yield ":keep-alive\n\n"
                last_keepalive = time.monotonic()
            await asyncio.sleep(SSE_POLL_SECONDS)
    except asyncio.CancelledError:  # 客户端断开
        raise


@router.post(
    "/jobs/{job_id}/cancel", status_code=202, response_model=JobOut, operation_id="cancelJob"
)
async def cancel_job(
    job_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> JobOut:
    """API-064：请求取消（检查点语义）；已终态 → 409 JOB_NOT_CANCELLABLE。"""
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        job = await state.queue.get_for_user(session, user_id, job_id)
        if job is None:
            raise NotFoundError(detail=f"任务 {job_id} 不存在")
        if JobStatus(job.status).is_terminal:
            raise ApiError(
                ErrorCode.JOB_NOT_CANCELLABLE, detail=f"任务已处于终态 {job.status}，无法取消"
            )
        if not job.cancel_requested:
            await state.queue.request_cancel(session, job_id)
            job.cancel_requested = True
        snapshot = _to_out(job)
    return snapshot
