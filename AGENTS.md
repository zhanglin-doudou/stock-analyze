# AGENTS.md — 跨子系统强制约定

本文档是**本仓 AI 编程助手的第一入口**。任何未阅读本文档就开始编码的 AI 会在 30 秒内踩坑。

## 🚨 AI 最容易踩的 7 个坑（必须前置阅读）

### 1. 禁止 float，金额/价格/数量/比率一律用标度整数
```python
# DON'T — 此仓从架构上杜绝 float
amount = sum(t.price * t.qty for t in trades)

# DO — 内部 int，边界定点字符串
from stock_core.values import Money, Price, Qty
total = Money(sum(t.amount_units for t in trades))   # 全在 int 域计算
resp.amount = str(total)                               # API 边界才转字符串
```
**为什么**：浮点误差在对账/收益复算里累积成"假断链"。数据库列以 `_units` 结尾（`BigInteger`），不存浮点。

### 2. Domain 层禁止 wall-clock，时间一律注入
```python
# DON'T — 任何模块直接取系统时间
import time; start = time.time()
from datetime import datetime; now = datetime.utcnow()

# DO — 通过 Clock 端口注入
from stock_contracts.clock import Clock
def classify(self, clock: Clock) -> None:
    cutoff = clock.now()   # 测试可传 FrozenClock 确定性复放
```
**为什么**：domain 代码必须可在测试中用 FrozenClock 复放；直接取 wall-clock 的代码无法测试时间相关逻辑。

### 3. 仓储方法必须显式收 `user_id`，禁止隐式 tenant 上下文
```python
# DON'T — 隐式从 contextvars 或 request 对象取 tenant
async def TradeRepository.list(self) -> list[Trade]:
    uid = current_context().require_user()   # 错：仓储层不该依赖请求上下文

# DO — user_id 作为参数显式传入
async def TradeRepository.list(self, user_id: uuid.UUID, *, limit: int = 50) -> list[Trade]:
    stmt = select(Trade).where(Trade.user_id == user_id).limit(limit)
```
**为什么**：RLS 已兜底（即使漏过滤也不会串号），但显式传参让代码审查和单元测试都能看见"隔离边界"在哪。

### 4. 新增端点先动 OpenAPI/Catalog，再动代码（契约先行）
```
1. 在 docs/architecture/v2/API-Catalog.md §1 加端点行、§6 加验收矩阵
2. make api-snapshot              重新生成 contracts/openapi.json
3. make api-check                 校验契约完整性
4. 再写 stock_api/routers/xxx.py     # operation_id 必填
5. 再写 backend/tests/api/xxx.py  # 每个 API-ID 至少 3 条：happy / 越权 / 关键错误码
6. make sdk                       同步前端 TS 类型
```

### 5. 错误一律抛 `ApiError(ErrorCode.X, detail=...)`，禁止 `HTTPException`
```python
# DON'T
raise HTTPException(status_code=404, detail="not found")

# DO
from stock_contracts.errors import ApiError, ErrorCode
raise ApiError(ErrorCode.NOT_FOUND, detail=f"trade {trade_id} 不存在")
```
**为什么**：自动走 RFC 7807 中间件，响应体带 `code/user_message/what_to_do/retryable/request_id/errors[]` 六字段，文档错误码表 §3 已收录。

### 6. 错误码必须落在 `ErrorCode` 枚举里，禁止随手抛字符串
```python
# DON'T
raise ApiError("trade not found")    # 字符串 code，不在枚举里 → api-check 会报警

# DO
raise ApiError(ErrorCode.TRADE_NOT_FOUND, detail="...")
```
**为什么**：`make api-check` 会扫 API-Catalog §3 错误码表 vs 代码使用的 ErrorCode 枚举，不一致即失败。

### 7. 跨账号访问一律返回 404 + `NOT_FOUND`，禁止 403 + `FORBIDDEN`
```python
# DON'T — 泄露了"资源存在但属于别人"
raise ApiError(ErrorCode.FORBIDDEN, detail="该资源不属于你")

# DO — 只泄露"不存在"
raise ApiError(ErrorCode.NOT_FOUND, detail=f"trade {trade_id} 不存在")
```
**例外**：admin 读业务数据（运维角色越界）→ 403 `ADMIN_CANNOT_READ_TRADES`（这是**身份**越权，不是资源归属越权）。

---

## HITL 升级协议
升级触发器（任一触发即升级）
- 歧义（Ambiguity）：指令存在多个同样合理的解释，且选错会造成实际损害。例如“退回这个订单”，但存在两个未结订单，不得猜测。
- 置信度低：Agent 自我评估置信度 < 0.75（可根据项目校准），且操作影响非平凡。
- 影响不可逆：即将执行的操作无法轻易撤销（删除数据、合并代码、发送对外通知等）。
- 依赖缺失/阻塞：所需的外部输入、决策或上游产物尚不存在或处于阻塞状态，继续执行只能靠猜。
- 策略例外：请求要求绕过已定义的规则边界，即使 Agent 确信例外是合理的，也必须由人授权。

升级协议
- 立即 停止当前任务，将状态标记为 Blocked。
- 输出结构化的升级请求，至少包含：触发了哪个升级条件、Agent 已经掌握了哪些信息、需要人做出什么具体决策、以及Agent 当前倾向的选项（如果有）及理由。
- 不得自行解除阻塞。解除阻塞的动作必须由人完成，Agent 不得在人工响应前自行恢复执行。

## 分层铁律 L1–L5（import-linter 强制，`make typecheck` 即跑）

```
stock_contracts   不依赖任何其它包                    KEPT 验证
stock_core        只依赖 stock_contracts                KEPT 验证
stock_platform    只依赖 stock_contracts / stock_core      KEPT 验证
stock_ledger      只依赖 stock_contracts / stock_core / stock_platform
api / worker   顶层，可依赖所有下层；下层禁止反向依赖
```
违反即阻断提交。不要临时在下层 `import stock_api.xxx`——即使是"只用到一个 DTO"，也应该把 DTO 上提。

## 全局编码约定（从全仓现有代码归纳）

| 约定 | 示例 |
|---|---|
| 所有 `.py` 文件首行 `from __future__ import annotations` | 70/70 文件覆盖 |
| Router 层一律 `APIRouter(route_class=StrictQueryRoute)` | 6/6 router |
| Pydantic schema 加 `operation_id="xxx"`（与 API-Catalog 对齐） | 必填 |
| Repository 方法签名 `async def method(self, session, user_id, ...)` | 强制 user_id 参数 |
| 写操作三件套：幂等键 Idempotency-Key、审计留痕（不含敏感明文）、RFC 7807 错误体 | 长任务必须 202 + `Location: /jobs/{id}` |
| Session 用法：`async with state.db.session(user_id=uid, begin=True) as session:` | api 层 |
| 半截写入需回滚：`savepoint = await session.begin_nested()` + `await savepoint.rollback()` | ledger application 层 |
| 枚举列用 `text` 存储 + 应用层校验，不建 `Enum` 列（新值免迁移） | ledger models |
| 数据库列名：`user_id`（UUID）、`*_units`（BigInteger 标度整数） | 全仓一致 |
| SQL 注入防护：参数化查询；cursor 解码时损坏一律 `INVALID_CURSOR` 拒绝 | 仓储层 |

## 导入排序（ruff I001 强制）

```python
from __future__ import annotations           # 必须在最上

import os                                    # 标准库
import uuid
from typing import Annotated

from fastapi import APIRouter                # 第三方

from stock_contracts.errors import ApiError     # 本仓（按层：contracts → core → platform → ledger）
from stock_core.values import Money
from stock_platform.tenant import TenantContext
```

## 门禁命令（提交前必跑）

```bash
make verify-p1     # ruff / pytest / api-check 全绿
make typecheck     # mypy strict + import-linter + tsc
make lint          # ruff fix + eslint
make api-snapshot  # 新增/改动端点后更新契约
make api-check     # 契约完整性校验
make sdk           # 同步前端 TS 类型
```

## 各子系统专属约定

| 子系统 | AGENTS.md 位置 |
|---|---|
| contracts（DTO / 枚举 / 错误码 / 常量 / Clock） | `backend/packages/contracts/AGENTS.md` |
| core（纯计算 / Scaled 族 / 零 I/O 零时间） | `backend/packages/core/AGENTS.md` |
| platform（DB / 配置 / JobQueue / 幂等 / TenantContext） | `backend/packages/platform/AGENTS.md` |
| ledger（模型 / 解析器 / application / 仓储） | `backend/packages/ledger/AGENTS.md` |
| api（router / schema / deps / middlewares） | `backend/services/api/AGENTS.md` |
| worker（job handler / 心跳 / 租约 / 单飞） | `backend/services/worker/AGENTS.md` |
| frontend（api-client / web / 零口径） | `frontend/AGENTS.md` |

本文档覆盖跨层与反直觉约定；进入子系统编码前，请先看对应子系统的 AGENTS.md。
