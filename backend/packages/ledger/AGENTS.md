# AGENTS.md — stock_ledger（流水域）

**前置**：必读根目录 `AGENTS.md`。本文件只补 ledger 层的特有约定。

## 定位

stock_ledger 是业务核心：交易明细、导入批次、隔离区、摘要规则、对账余额链。职责分三层：

| 层 | 目录 | 职责 | 依赖 |
|---|---|---|---|
| 解析器 | `parsers/` | 把券商原始文件 → 结构化 Bill（**纯函数，不落库**） | stock_core |
| application | `application/` | 业务编排（保存点 / 批次收养 / 幂等短路） | stock_platform.jobs, repo 类 |
| repositories | `repositories/` | Session 操作 + SQL + user_id 过滤 | stock_platform.models |

依赖只有 `stock_contracts`、`stock_core`、`stock_platform`。**不放 FastAPI 路由**。

## 规则速查

### 1. 解析器纯函数：输入 bytes，输出 Bill，不碰 Session

```python
# DO — parsers/huatai.py
def parse_bill(data: bytes, file_name: str, *, clock: Clock, ...) -> ParsedBill:
    # 纯函数：bytes → dataclass（rows / columns / warnings / assumptions）
    # 不 import sqlalchemy / AsyncSession / repository
    ...

# DON'T — 解析器里落库
def parse_and_insert(session, data, ...):
    session.add(Trade(...))   # 错：解析器只负责理解文件，不负责持久化
```

### 2. 半截写入用保存点回滚，不终结外层事务

```python
# application/imports.py 的 commit_batch 模式
async def commit_batch(queue, session, job, clock) -> dict:
    try:
        savepoint = await session.begin_nested()    # ← 半截写入的保护伞
        # ... 写入明细、批次 ...
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
        await savepoint.commit()                    # ← 成功才提交保存点
        return result
    except BaseException as exc:
        if session.in_nested_transaction():
            await savepoint.rollback()              # ← 失败只回滚子事务，不终结调用方
        return await _fail_job(queue, session, job_id, exc)
```
**为什么**：application 层被 worker 调用时，worker 外层有自己的事务（为了 JobQueue.finish 的原子性）。如果 application 直接 `session.rollback()`，会把 worker 的外层事务也滚掉——用保存点隔离。

### 3. 批次收养：API 预建 PENDING，worker 收养回填

```python
# API-011 同步返回 batch_id 的解决方案
# API 侧在请求里预建一个 status='PENDING' 的 ImportBatch，把 id 放进 job.params.batch_id
# worker 侧 commit_batch 读到 params.batch_id → 查到 PENDING 批次 → 回填 broker/file/warnings 等
# 短路条件：existing.status != 'PENDING' 才复用（PENDING 是未被收养的，不能短路）
```
这是一期的设计模式——后续期次如果要简化，可以改成"worker 创建批次后回推 API"，但一期保持收养模式。

### 4. Repository 方法签名：显式收 user_id，返回 ORM 模型或 DTO

```python
class TradeRepository:
    async def list(
        self, session: AsyncSession, user_id: uuid.UUID,
        *, cursor: str | None = None, limit: int = 50
    ) -> tuple[list[Trade], str | None]:
        stmt = select(Trade).where(Trade.user_id == user_id, Trade.deleted_at.is_(None))
        ...
```
**禁止**在 repository 内部调用 `current_context().require_user()`——user_id 必须从 application / api 层显式传入。

### 5. 模型列名：`*_units`（BigInteger 标度整数）+ `user_id`（UUID RLS 谓词）

```python
class Trade(Base):
    __tablename__ = "trade"
    __table_args__ = {"schema": "ledger"}   # 显式 schema
    
    id = mapped_column(String, primary_key=True)
    user_id = mapped_column(PGUUID, index=True)          # RLS 谓词 target
    quantity_units = mapped_column(BigInteger)             # scale=4
    price_units = mapped_column(BigInteger)                # scale=6
    amount_units = mapped_column(BigInteger)               # scale=2
    ...
```
**禁止**在 ledger 模型里出现 `Float` 或 `Numeric`——金额/价格/数量一律 BigInteger。

### 6. 枚举列用 Text + 应用层校验，不建 Enum

```python
class ImportBatch(Base):
    status = mapped_column(String)    # text 存 "pending"/"succeeded"/...
    # 应用层校验: ImportBatchStatus(status) 抛 ValueError 自动转 VALIDATION_ERROR
```
**为什么**：新增枚举值（例如加了 `cancelled`）不需要改表、不需要迁移。

### 7. Trade 模型必须 `eager_defaults=True`（修 updated_at MissingGreenlet）

```python
class Trade(Base):
    __mapper_args__ = {"eager_defaults": True}
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
```
**为什么**：`onupdate` 触发的 `updated_at` 在 Session 里不会自动回填到 ORM 实例——不加 eager_defaults，读 `trade.updated_at` 会抛 MissingGreenlet。

## 文件清单

| 路径 | 职责 |
|---|---|
| `models.py` | Trade / ImportBatch / ImportPreview / QuarantineRow / SummaryRule / Reconciliation |
| `parsers/huatai.py` | 华泰账单解析（纯函数 parse_bill → ParsedBill） |
| `parsers/columns.py` | 列映射推断 / 日期解析工具 |
| `parsers/dedupe.py` | dedupe_key 生成（标的+成交日期+数量+价格） |
| `application/imports.py` | commit_batch / revoke_batch（保存点 + 批次收养） |
| `application/preview.py` | create_preview（不落库，只读估算） |
| `application/trades.py` | list_trades / patch_trade / delete_trade / restore_trade |
| `application/reconcile.py` | 余额链对账 / 断链定位 |
| `repositories/trades.py` | TradeRepository（游标分页 / 乐观锁 / 软删） |
| `repositories/imports.py` | ImportBatchRepository / ImportPreviewRepository |
| `repositories/quarantine.py` | QuarantineRepository |
| `repositories/rules.py` | SummaryRuleRepository + seed_system_rules |
| `repositories/reconciliation.py` | ReconciliationRepository |
| `fixtures.py` | generate_huatai_bill（确定性账单夹具，用于 seed-tenants） |
