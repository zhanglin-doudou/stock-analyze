"""Worker 运行循环（Architecture §5.2）：轮询领取 + 心跳 + 租约回收。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from typing import Any

from stock_contracts.clock import SystemClock
from stock_contracts.enums import JobStatus
from stock_platform.config import Settings, get_settings
from stock_platform.db import Database
from stock_platform.jobs import JobQueue
from stock_platform.obs import configure_logging, get_logger

from stock_worker.handlers import HANDLERS

log = get_logger("stock_worker")


class WorkerRunner:
    """单进程消费者：并发度受 PG 池限制，横向扩展即多进程。"""

    def __init__(self, settings: Settings | None = None, *, concurrency: int = 2) -> None:
        self.settings = settings or get_settings()
        self.db = Database(self.settings, role="app")
        self.clock = SystemClock()
        self.queue = JobQueue(self.db, self.clock)
        self.concurrency = concurrency
        self._stopping = False
        self.worker_id = f"w{os.getpid()}"

    async def stop(self) -> None:
        self._stopping = True

    async def run(self) -> None:  # pragma: no cover - 长驻进程
        configure_logging(self.settings.log_level, json_output=self.settings.env == "prod")
        log.info("worker.start", worker=self.worker_id, concurrency=self.concurrency)
        tasks = [asyncio.create_task(self._loop()) for _ in range(self.concurrency)]
        tasks.append(asyncio.create_task(self._reaper()))
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)
        await self.db.dispose()
        log.info("worker.stop")

    async def _reaper(self) -> None:
        """租约回收：崩溃/被杀的任务不悬挂（§5.1）。"""
        while not self._stopping:
            await asyncio.sleep(15)
            async with self.db.session(begin=True) as session:
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
            await self._execute(claimed)

    async def _claim_one(self) -> dict[str, Any] | None:
        async with self.db.session(begin=True) as session:
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

    async def _execute(self, job: dict[str, Any]) -> None:  # pragma: no cover - 集成测试覆盖
        handler = HANDLERS.get(job["kind"])
        if handler is None:
            async with self.db.session(begin=True) as session:
                await self.queue.finish(
                    session,
                    job["id"],
                    status=JobStatus.FAILED,
                    error_code="UNSUPPORTED_JOB_KIND",
                    error={"kind": job["kind"]},
                )
            log.error("worker.unsupported_kind", kind=job["kind"], job=job["id"])
            return
        async with self.db.session(begin=True) as session:
            await handler(self.queue, session, job, self.clock)


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
