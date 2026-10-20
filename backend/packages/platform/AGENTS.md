# AGENTS.md — stock_platform（运行时基础设施）

**前置**：必读根目录 `AGENTS.md`。本文件只补 platform 层的特有约定。

## 定位

stock_platform 是运行时基础设施：DB 连接、配置、租户会话（RLS 第 2 层）、PG 任务队列、幂等键、限速、审计留痕、ID 生成、游标编解码。依赖只有 `stock_contracts` 和 `stock_core`。**不放业务逻辑**（业务逻辑放 ledger 或更高层）。

## 规则速查

### 1. 租户会话：`Database.session()` 自动 SET LOCAL

```python
# API 层业务入口唯一会话打开方式
async with state.db.session(user_id=uid, begin=True) as session:
    # RLS 已自动开启：SET LOCAL app.user_id = '...'
    # stock_app 角色执行任何 SQL 都会被 RLS 谓词过滤
    await TradeRepository(session).list_for_user(user_id=uid)
    ...
# 事务结束 → SET LOCAL 自动失效；连接归还池时已清空 app.user_id（无残留串号）
```

**禁止**在 platform 包的 `db.py` 里手写 `set_config('app.user_id', ...)`——Database 类已封装好。

### 2. 三角色模型：stock_owner 迁移 / stock_app 运行 / stock_backup 只读

```python
# Settings.owner_dsn() / Settings.app_dsn() 由 STOCK_DB_OWNER_DSN / STOCK_DB_APP_DSN 环境变量覆盖
# 不传则从 STOCK_DB_HOST/PORT/NAME 拼默认
```
| 角色 | 用途 | 权限边界 |
|---|---|---|
| `stock_owner` | Alembic 迁移、测试 TRUNCATE | schema/表 owner，**不受 RLS** |
| `stock_app` | API / worker 运行时 | 非 owner、`NOBYPASSRLS`、仅 DML |
| `stock_backup` | 备份/校验演练 | 只读 |

**禁止**代码用 postgres 超级用户——会绕过 RLS，掩盖隔离缺陷。

### 3. JobQueue：PG 队列 + SKIP LOCKED + 租约 + 单飞

```python
# worker 拉取（SKIP LOCKED 避免跨 worker 竞争）
job = await queue.acquire(session, kind="import_commit", lease_seconds=90)

# 处理完成（无论成败，都要 finish）
await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result_dict)

# 发 SSE 事件（emit 内部先 flush 让当前事务的待落库事件落库，避免 seq 冲突）
await queue.emit(session, job_id, event_type="progress", payload={"progress": 50})
```
**关键实现细节**：`emit()` 开头必须 `await session.flush()`——autoflush=False 时，`max(seq)` 读不到本事务里未 flush 的事件，连续 emit 会算出同一个 seq 撞唯一约束。

### 4. 单飞上锁：`kind + scope_key` 部分唯一索引

迁移 `0001_phase1` 里 job 表有唯一索引：

```sql
UNIQUE (kind, scope_key) WHERE status IN ('pending','running')
```
同 scope_key 的任务在 pending/running 状态下只能存在一个——这就是单飞。新任务提交时如果这个唯一索引冲突，`IdempotencyStore` 会返回 `IDEMPOTENCY_IN_PROGRESS`（409 + job_id）。

### 5. 幂等键：三态语义

```
同键同体   → 复用首响应（返回 cached_result，200）
同键异体   → 422 IDEMPOTENCY_KEY_REUSED
处理中     → 409 IDEMPOTENCY_IN_PROGRESS + 正在执行的 job_id
默认有效期  → 24 小时
```

### 6. 游标编解码：不透明、URL 安全、损坏一律 INVALID_CURSOR

```python
from stock_platform.cursor import encode_cursor, decode_cursor

# 编码（JSON 排序键 → base64url，无填充）
cursor = encode_cursor({"d": "2026-06-30", "i": "trd_xxx"})

# 解码（任何 binascii / JSON 解析错误都 raise ApiError(INVALID_CURSOR)）
payload = decode_cursor(cursor)   # {"d": "2026-06-30", "i": "trd_xxx"}
```

### 7. 审计留痕：不含敏感明文

```python
# DO — 只存必要字段
AuditLog(user_id=uid, action="trade.update", target_id=trade_id, before={...}, after={...})

# DON'T — 禁止在 audit 里存 access_token / password / PII
```

### 8. 新仓储方法签名强制 `user_id` 参数

```python
# platform/repositories 风格（ledger/repositories 一致）
class JobRepository:
    async def get_for_user(self, session: AsyncSession, user_id: uuid.UUID, job_id: str) -> Job | None:
        ...
```
**禁止**在 repository 内部调用 `current_context().require_user()`——user_id 必须显式传入。

## 文件清单

| 文件 | 职责 |
|---|---|
| `db.py` | Database 类（连接池 + SET LOCAL app.user_id + 连接归还清空） |
| `models.py` | platform schema ORM（job / job_event / idempotency_key / audit_log / user ...） |
| `config.py` | Settings + build_dsn + get_settings（从根 .env 读） |
| `tenant.py` | TenantContext（从凭证解析 user_id + 角色） |
| `jobs.py` | JobQueue（acquire / finish / emit + SKIP LOCKED + 租约） |
| `idempotency.py` | IdempotencyStore（三态语义） |
| `ratelimit.py` | RateLimitBucket（PG 窗口桶） |
| `audit.py` | 审计留痕（不含敏感明文） |
| `cursor.py` | encode_cursor / decode_cursor（不透明游标） |
| `ids.py` | ULID 风格 ID 生成（trade_id / import_batch_id ...） |
| `security.py` | hasher_for（密码哈希，pbkdf2） |
| `outbox.py` | Outbox 事件表（投递后置） |
