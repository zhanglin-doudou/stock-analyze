# AGENTS.md — stock_contracts（跨上下文契约）

**前置**：必读根目录 `AGENTS.md`。本文件只补 contracts 层的特有约定。

## 定位

stock_contracts 是全仓唯一零依赖包。任何跨层共享的 **DTO / 枚举 / 错误码 / 常量 / 端口（Clock）** 都放这里；**不放任何 I/O / DB / HTTP 代码**。

## 规则速查

### 1. 新增错误码 → 必须同时改三处

```
a. stock_contracts/errors.py → ErrorCode 枚举（大写蛇形，加注释分组）
b. docs/architecture/v2/API-Catalog.md §3 错误码表（user_message / what_to_do / HTTP 状态）
c. make api-check    ← 自动校验（缺任何一处即失败）
```

```python
# DO — 新增错误码的正确姿势
class ErrorCode(StrEnum):
    # ── 资源 ──────────────────────────────────────────────
    TRADE_NOT_FOUND = "TRADE_NOT_FOUND"          # 大写蛇形 + 分组注释
```

### 2. 错误码一旦发布不得改名或复用（稳定契约）

```python
# DON'T — 已在 §3 收录的 code 改名 / 挪含义
TRADE_NOT_FOUND = "BATCH_NOT_FOUND"   # 错：前端已缓存此 code 做分支

# DO — 新增；废弃的 code 保留但加注释标记 deprecated
TRADE_NOT_FOUND = "TRADE_NOT_FOUND"   # 保留不动
# BATCH_NOT_FOUND 已在 §3
```

### 3. 枚举一律 `StrEnum`（Python 3.11+），持久化用 `TEXT`

```python
# DO — StrEnum（值就是字符串，持久化免迁移）
class ImportBatchStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

# DON'T — IntEnum / Enum（迁移成本高，新值需改表）
```

### 4. Clock 端口放 contracts（避免 core→platform 反向依赖）

```python
# 端口定义
class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...

# core 从 contracts 再导出（core/clock.py 三行 re-export）
from stock_contracts.clock import Clock, FrozenClock, SystemClock
```
**为什么**：core 层不能依赖 platform（platform 才是生产实现），端口定义必须在零依赖的 contracts。

### 5. 常量集中在 `constants.py`，带 type hint `Final`

```python
from typing import Final

EPS_AMT: Final[Decimal] = Decimal("0.01")     # 金额容差（元）
EPS_R: Final[Decimal] = Decimal("0.0001")     # 比率容差（1bp）
SCALE_AMT: Final[int] = 2                     # 金额标度
```

### 6. 零依赖、零 I/O、零状态

```python
# DON'T — contracts 层禁止
import sqlalchemy      # 任何 ORM/DB 库
import httpx           # 任何 HTTP 客户端
print(...)             # 禁止副作用
```

## 文件清单

| 文件 | 职责 |
|---|---|
| `errors.py` | ErrorCode 枚举（§3 收录的稳定 code）+ ApiError 异常类 |
| `clock.py` | Clock Protocol + SystemClock / FrozenClock 实现 |
| `enums.py` | 跨层共享 StrEnum（Role / Side / ImportBatchStatus ...） |
| `constants.py` | 标度 / 容差 / 分页默认值 |
| `__init__.py` | 显式 re-export 公共 API |
