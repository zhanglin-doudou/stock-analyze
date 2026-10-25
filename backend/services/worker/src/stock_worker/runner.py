"""Worker 运行循环（Architecture §5.2）：轮询领取 + 心跳 + 租约回收。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import uuid
from typing import Any

from stock_contracts.clock import Clock, SystemClock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.market import create_market_source
from stock_platform import jobs as jobs_module
from stock_platform.config import Settings, get_settings
from stock_platform.db import Database
from stock_platform.jobs import JobQueue
from stock_platform.obs import configure_logging, get_logger
from stock_platform.ports import MarketDataSource

from stock_worker.handlers import HANDLERS

log = get_logger("stock_worker")


class WorkerRunner:
    """单进程消费者：并发度受 PG 池限制，横向扩展即多进程。

    G7（platform 租户表 RLS）后角色双轨：
    - `db`（stock_app）：handler 的租户事务，受 RLS 约束（业务读写唯一入口）；
    - `queue_db`（stock_owner）：**队列控制面**（claim / reap / heartbeat）跨租户，
      必须能读写任意用户的任务行；owner 绕过 RLS，因此只用于这三个内部动作，
      绝不下发给 handler。这不是“应用运行时时用 owner”，而是控制面与数据面的分离。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        concurrency: int = 2,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = Database(self.settings, role="app")
        self.queue_db = Database(self.settings, role="owner")
        self.clock = clock or SystemClock()
        # 行情端口**懒装配**（D11）：replay 只读夹具、无网络；live 分支需要 akshare，
        # 不应在 worker 进程启动时就因缺依赖失败——只有真的执行行情相关任务才装配。
        self._market_data: MarketDataSource | None = None
        self.queue = JobQueue(self.db, self.clock)
        self.concurrency = concurrency
        self._stopping = False
        self.worker_id = f"w{os.getpid()}"

    @property
    def market_data(self) -> MarketDataSource:
        """行情源（懒装配；`session_factory` 必须是无租户的数据面会话工厂）。"""
        if self._market_data is None:
            self._market_data = create_market_source(
                self.settings,
                session_factory=self.db.session,
                clock=self.clock,
            )
        return self._market_data

    async def stop(self) -> None:
        self._stopping = True

    async def close(self) -> None:
        """释放两套连接池（测试/短生命周期入口用）。"""
        await self.queue_db.dispose()
        await self.db.dispose()

    async def run(self) -> None:  # pragma: no cover - 长驻进程
        configure_logging(self.settings.log_level, json_output=self.settings.env == "prod")
        log.info("worker.start", worker=self.worker_id, concurrency=self.concurrency)
        tasks = [asyncio.create_task(self._loop()) for _ in range(self.concurrency)]
        tasks.append(asyncio.create_task(self._reaper()))
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks)
        finally:
            await self.close()
        log.info("worker.stop")

    async def _reaper(self) -> None:
        """租约回收：崩溃/被杀的任务不悬挂（§5.1）。队列控制面走 owner。"""
        while not self._stopping:
            await asyncio.sleep(15)
            async with self.queue_db.session(begin=True) as session:
                reaped = await self.queue.reap_expired_leases(session)
            if reaped:
                log.warning("worker.lease_reaped", count=reaped)

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                claimed = await self._claim_one()
            except Exception as exc:  # pragma: no cover - 池故障
                log.error("worker.claim_failed", error=str(exc))
                await asyncio.sleep(2)
                continue
            if not claimed:
                await asyncio.sleep(0.4)
                continue
            try:
                await self._execute(claimed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - _execute 自身已兜底，防御性
                # 消费循环绝不能因单个任务的异常退出（否则并发度永久-1）。
                log.error("worker.execute_crashed", job=claimed.get("id"), error=str(exc))

    async def _claim_one(self) -> dict[str, Any] | None:
        # 跨租户认领：任何用户的 pending 任务都要能被本 worker 领走 → owner 控制面。
        async with self.queue_db.session(begin=True) as session:
            job = await self.queue.claim(session)
            if job is None:
                return None
            return {
                "id": job.id,
                "kind": job.kind,
                "user_id": str(job.user_id),
                "params": dict(job.params),
                "attempt": job.attempt,
            }

    async def _heartbeat_loop(self, job_id: str) -> None:
        """执行期间独立事务续租（`LEASE_SECONDS` 内必有 `HEARTBEAT_SECONDS` 一次心跳）。

        每次心跳用**独立 owner session**：job 行受 RLS 约束，心跳是跨租户控制面动作；
        handler 的事务可能长期不提交，心跳不能被它绑架；
        任务结束/异常时由 `_execute` 的 finally 取消本协程。
        """
        while True:
            await asyncio.sleep(max(0.01, float(jobs_module.HEARTBEAT_SECONDS)))
            try:
                async with self.queue_db.session(begin=True) as session:
                    await self.queue.heartbeat(session, job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - 池/网络抖动
                log.warning("worker.heartbeat_failed", job=job_id, error=str(exc))

    async def _mark_failed(
        self,
        job: dict[str, Any],
        *,
        error_code: str,
        error: dict[str, Any],
        retryable: bool,
    ) -> None:
        """异常收口：独立事务里登记失败/重试；已是终态则跳过（防重复 finish 覆盖）。"""
        job_id = str(job["id"])
        try:
            async with self.db.tenant_tx(uuid.UUID(str(job["user_id"]))) as session:
                current = await self.queue.get(session, job_id)
                if current is None or JobStatus(current.status).is_terminal:
                    return
                if retryable:
                    await self.queue.fail_or_retry(
                        session, job_id, error_code=error_code, error=error
                    )
                else:
                    await self.queue.finish(
                        session,
                        job_id,
                        status=JobStatus.FAILED,
                        error_code=error_code,
                        error=error,
                    )
        except Exception as exc:  # pragma: no cover - 数据库不可用时仅记录
            log.error("worker.failure_record_failed", job=job_id, error=str(exc))

    async def _execute(self, job: dict[str, Any]) -> None:  # pragma: no cover - 集成测试覆盖
        job_id = str(job["id"])
        handler = HANDLERS.get(job["kind"])
        if handler is None:
            # job 行受 RLS 约束：收口必须在该任务所属租户的会话里做。
            async with self.db.tenant_tx(uuid.UUID(str(job["user_id"]))) as session:
                await self.queue.finish(
                    session,
                    job_id,
                    status=JobStatus.FAILED,
                    error_code="UNSUPPORTED_JOB_KIND",
                    error={"kind": job["kind"]},
                )
            log.error("worker.unsupported_kind", kind=job["kind"], job=job_id)
            return
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id))
        try:
            # 必须带租户上下文：ledger 业务表都启用了 RLS，会话未 SET LOCAL app.user_id
            # 时仓储查询会看不到任何行（表现为 IMPORT_PREVIEW_NOT_FOUND 之类的假 404）。
            async with self.db.tenant_tx(uuid.UUID(str(job["user_id"]))) as session:
                await handler(self.queue, session, job, self.clock, self.market_data)
        except asyncio.CancelledError:
            raise
        except ApiError as exc:
            # handler 未自行收口（如 reclassify）：按业务错误码走 fail/retry。
            await self._mark_failed(
                job,
                error_code=exc.code.value,
                error={"code": exc.code.value, "detail": exc.detail},
                retryable=True,
            )
            log.warning("worker.job_api_error", job=job_id, code=exc.code.value)
        except Exception as exc:
            # 未知异常：终态 failed + INTERNAL_ERROR，绝不让消费循环退出。
            await self._mark_failed(
                job,
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error={"code": ErrorCode.INTERNAL_ERROR.value, "detail": str(exc)},
                retryable=False,
            )
            log.exception("worker.job_crashed", job=job_id, error=str(exc))
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat


def main() -> None:  # pragma: no cover - 进程入口
    runner = WorkerRunner()
    loop = asyncio.new_event_loop()
    _tasks: set[asyncio.Task[None]] = set()

    def _handle(signum: int, _frame: Any) -> None:
        _tasks.add(loop.create_task(runner.stop()))

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle, sig, None)
    try:
        loop.run_until_complete(runner.run())
    finally:
        loop.close()


if __name__ == "__main__":  # pragma: no cover
    main()
