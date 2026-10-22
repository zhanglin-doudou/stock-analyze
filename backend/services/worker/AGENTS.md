# AGENTS.md — stock_worker（任务消费者）

**前置**：必读根目录 `AGENTS.md`。本文件只补 worker 层的特有约定。

## 定位

stock_worker 是 PG 队列的消费进程：轮询领取 → 心跳 → 执行业务 handler → 终态收口。**不做 HTTP**、**不做鉴权**、**不做路由**——只把 JobQueue 里的任务领出来交给 ledger 业务层。

## 规则速查

### 1. Handler 签名统一：`(queue, session, job, clock) -> dict | None`

```python
# handlers.py 注册表（按 kind 映射）
HANDLERS: dict[str, JobHandler] = {
    "import_commit": _import_commit,
    "import_revoke": _import_revoke,
    "reclassify": _reclassify,
}

# handler 内部延迟 import 避免循环依赖
async def _import_commit(queue, session, job, clock) -> dict[str, Any] | None:
    from stock_ledger.application.imports import commit_batch
    return await commit_batch(queue, session, job, clock)
```
**为什么延迟 import**：worker 直接 import stock_ledger 顶层时，ledger 又会 import stock_platform，容易与 runner 的 import 顺序打架；延迟到 handler 调用链才 import 避免循环。

### 2. job dict 里 user_id 是 str（PGUUID 序列化）

```python
# runner.claim 返回的 job dict
job = {"id": "...", "kind": "...", "user_id": "550e8400-e29b-41d4-a716-446655440000", ...}

# application 层入口转成 UUID
async def commit_batch(queue, session, job, clock) -> dict:
    user_id = uuid.UUID(str(job["user_id"]))   # str → UUID
```

### 3. 执行前检查取消（幂等中断）

```python
if await queue.is_cancel_requested(session, job_id):
    await queue.finish(session, job_id, status=JobStatus.CANCELLED, result={"cancelled": True})
    return {"job_id": job_id, "cancelled": True}
```
长任务在关键节点（解析完、写入前、对账中）都应该有这个检查——用户取消后尽快退出。

### 4. Runner 三个循环：_loop（消费） + _reaper（租约回收） + 心跳

```python
# WorkerRunner.run() 启动
tasks = [
    asyncio.create_task(self._loop()),       # concurrency 个（默认 2）
    asyncio.create_task(self._reaper()),     # 定时扫 lease_until 过期的 running 任务
]
```
**为什么要 reaper**：worker 进程崩溃后，running 状态的任务会"悬挂"——租约回收把它们标记 expired / 重试，不让它们永远卡住。

### 5. 双连接池：数据面必须 `app`，队列控制面用 `owner`

G7 起 platform 的 `job/idempotency_key/user_setting/outbox_event` 启用 RLS，而队列的
claim/reap/heartbeat 本质是**跨租户控制面**（不知道自己将要跑哪个用户的任务），
因此拆成两个池：

```python
class WorkerRunner:
    def __init__(self, settings, ...):
        self.db = Database(self.settings, role="app")        # 数据面：handler 用，受 RLS
        self.queue_db = Database(self.settings, role="owner")  # 控制面：claim/reap/heartbeat
        self.queue = JobQueue(self.queue_db, self.clock)
```

- **handler 绝不能用 owner**：`_execute` 内必须 `self.db.tenant_tx(user_id)`，否则业务表漏隔离。
- owner 只用于 JobQueue 的 claim/reap/heartbeat；dispose 时两个池都要释放。
- 新增任何跨租户查询前先问：它属于控制面（owner）还是数据面（app）？

### 6. 新任务类型 = 新 handler + 新 JobQueue 注册

```
a. 在 handlers.py 加 _xxx_handler 函数 + HANDLERS["kind"] = _xxx_handler
b. 在 stock_platform/jobs.py 的 HANDLERS 或 runner 侧补 kind 常量（如果有集中定义）
c. 确认 API 层投递的 kind 与 worker handler 名一致（字符串，无枚举）
```

## 文件清单

| 文件 | 职责 |
|---|---|
| `runner.py` | WorkerRunner 主循环（_loop + _reaper + 心跳 + 并发度控制） |
| `handlers.py` | HANDLERS 注册表 + 各 kind handler（延迟 import ledger 应用层） |
| `__main__.py` | `python -m stock_worker` 入口 |
