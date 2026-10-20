# stock-analyze — 个人交易复盘平台

面向个人投资者的交易复盘系统：导入券商对账单，自动落库、分类与对账，产出多维复盘分析。一期（Phase 1）已交付**多租户底座 + 对账单导入链路 + 任务队列 + 流水维护 + 前端骨架**。

## 功能特性（一期）

- **多租户与安全**：注册 / 登录 / 刷新令牌轮换 / 登出 / 个人访问令牌（PAT）CRUD，登录限速
- **双层隔离**：仓储层强制 `user_id` 过滤 + PostgreSQL RLS（`SET LOCAL app.user_id`），跨账号访问一律 404，admin 角色禁止读写业务明细
- **对账单导入**：华泰账单解析、列映射预检、预览（不落库）→ 提交（异步任务）→ 批次撤销、隔离区查询、bag 去重
- **流水维护**：游标分页 / ETag 缓存 / If-Match 并发控制 / 单行修正 / 软删与恢复 / 修改历史
- **任务系统**：PG 队列（SKIP LOCKED + 租约 + 心跳 + 单飞去重 + 同用户并发上限）、SSE 进度流、取消、积压统计
- **对账**：资金余额链核对，断链精确定位到具体行
- **契约链路**：OpenAPI 快照 → `api-check` 契约完整性门禁 → 前端 TS SDK 生成
- **标度整数**：金额 2 位 / 价格 6 位 / 数量 4 位 / 比率 8 位，全程禁 float，API 边界输出定点字符串

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python ≥ 3.12 · uv workspace · FastAPI · SQLAlchemy 2.0 (async) · asyncpg · Alembic |
| 数据库 | PostgreSQL 16（多 schema + RLS） |
| 任务 | PG 队列 + 独立 worker 进程（SKIP LOCKED） |
| 前端 | Vite · React 18 · TypeScript · React Router · TanStack Query |
| 质量 | ruff · mypy (strict) · import-linter · pytest · vitest · eslint |

## 目录结构

```
stock-analyze/
├── backend/
│   ├── packages/            # 分层包（依赖只允许自上而下）
│   │   ├── contracts/       # DTO / 枚举 / 错误码 / 常量 / Clock（不依赖任何包）
│   │   ├── core/            # 纯计算内核：标度整数、零 I/O、零 wall-clock
│   │   ├── platform/        # DB / 配置 / 任务队列 / 仓储基座 / 幂等 / 限速
│   │   ├── ledger/          # 流水域：模型 / 解析器 / 导入应用层 / 仓储
│   │   ├── review/          # 复盘域（二期）
│   │   └── delivery/        # 交付域（三期）
│   ├── services/
│   │   ├── api/             # FastAPI 应用 + CLI（stock_api.cli）
│   │   └── worker/          # 任务消费者（stock_worker）
│   ├── migrations/          # Alembic（多 schema + RLS 策略）
│   └── tests/               # unit / api / integration / isolation / architecture
├── frontend/
│   ├── apps/web/            # Vite + React 应用
│   └── packages/api-client/ # fetch 传输层（401 刷新重放）+ 生成的 TS 类型
├── contracts/openapi.json   # API 契约快照（api-snapshot 生成，api-check 校验）
├── fixtures/market/         # 行情 / 账单夹具（seed-demo 生成，确定性）
├── infra/
│   ├── compose/             # docker-compose.dev.yml（本地 PostgreSQL）
│   ├── postgres/init/       # 容器首次初始化 SQL（角色 + 库 + 扩展）
│   └── .env.example         # 环境变量模板
├── scripts/db_bootstrap.sql # 本机 PostgreSQL 引导脚本（无 Docker 时）
└── docs/                    # 架构 / 开发计划 / PRD / 数据库指引
```

## 快速开始

### 环境要求

- Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20 与 [pnpm](https://pnpm.io/)
- PostgreSQL 16+（Docker 或本机安装，见 [docs/DATABASE.md](docs/DATABASE.md)）

### 五步启动

```bash
# 1. 环境变量（首次）
make env                 # 复制 infra/.env.example → .env

# 2. 启动数据库并建表（二选一）
make db-up && make migrate      # Docker 方式（端口 5433）
# make db-bootstrap && make migrate   # 本机 PostgreSQL 方式（端口 5432，需覆盖 DSN，见 DATABASE.md）

# 3. 安装依赖
uv sync                  # 后端整套（uv workspace 聚合）
cd frontend && pnpm install && cd ..

# 4. 造演示数据（A/B/admin 三账号）
make seed-tenants

# 5. 一键起服务（api + worker + web）
make dev
```

启动后：

| 入口 | 地址 |
|---|---|
| Web | http://127.0.0.1:5173 |
| API | http://127.0.0.1:8000/api/v1 |
| OpenAPI | http://127.0.0.1:8000/api/v1/openapi.json |
| 健康检查 | /healthz · /readyz · /version · /metrics |

演示账号（`make seed-tenants` 生成，密码 `Demo!Passw0rd2026`）：

| 账号 | 角色 | 说明 |
|---|---|---|
| a@local | user | 演示账号 A（有业务数据） |
| b@local | user | 演示账号 B（有业务数据） |
| admin@local | admin | 运维管理员（不可读写业务明细） |

## 常用命令

```
make help        查看全部目标

── 环境 ──
make env             生成 .env（首次）
make db-up           启动 Docker PostgreSQL（5433）
make db-bootstrap    本机 PostgreSQL 建角色/库（无 Docker 替代）
make db-down         停止数据库
make db-reset        销毁并重建数据库
make migrate         执行 Alembic 迁移（schema + 表 + RLS）

── 运行 ──
make dev             api(热重载) + worker + web
make api / worker / web   单独启动某进程

── 数据 ──
make seed-tenants    造 A/B/admin 账号与账单
make seed-demo       造演示数据（含行情回放）

── 质量 ──
make lint            ruff + eslint
make format          ruff 自动修复 + 格式化
make typecheck       mypy + import-linter + tsc
make test            快速单测（不含 api/integration）
make test-core       core 单测 + 100% 覆盖门禁
make test-api        API 验收套件（需 PostgreSQL）
make test-rls        RLS 隔离集成测试
make test-isolation  越权负向矩阵
make verify-p1       一期质量门禁一条命令全绿

── 契约 ──
make api-snapshot    生成 contracts/openapi.json
make api-check       契约完整性检查（operationId/示例/错误码）
make sdk             由快照生成前端 TS 类型
make api-coverage    端点覆盖报告
make demo-p1         打印 5 分钟演示脚本
```

## 调试指引

### 后端

- **日志级别**：`.env` 中 `STOCK_LOG_LEVEL=DEBUG`；compose 已开启慢查询日志（>200ms）。
- **单独调试 API**：`make api`（uvicorn `--reload`），断点用 VS Code 配置：

```jsonc
// .vscode/launch.json（需安装 Python Debugger / debugpy 扩展）
{
  "type": "debugpy",
  "request": "launch",
  "name": "FastAPI",
  "module": "uvicorn",
  "args": ["stock_api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"],
  "cwd": "${workspaceFolder}",
  "envFile": "${workspaceFolder}/.env"
}
```

- **跑单个用例**：

```bash
uv run pytest backend/tests/api/test_imports_trades_jobs.py -k "api_011" -x   # 按 API-ID/关键词
uv run pytest -m isolation -q                                                 # 按标记
uv run pytest backend/tests/integration -q                                    # 真库用例
```

- **错误排查**：所有错误为 RFC 7807 体，带 `request_id`，可对照 `docs/architecture/v2/API-Catalog.md` §3 错误码表定位；`curl -s http://127.0.0.1:8000/api/v1/errors` 可列出全部错误码。

- **调试任务/队列**：直接查库（角色见 [docs/DATABASE.md](docs/DATABASE.md)）：

```sql
SELECT id, kind, status, attempt, stage, progress, last_error FROM platform.job ORDER BY created_at DESC LIMIT 10;
SELECT job_id, seq, type, payload FROM platform.job_event WHERE job_id = 'job_xxx' ORDER BY seq;
```

- **CLI 调试**：`uv run python -m stock_api.cli <cmd> --help`；`seed-tenants` / `seed-demo` 使用 FrozenClock（2026-10-01），数据确定性可复放。

### 前端

```bash
cd frontend
pnpm --filter web dev        # Vite dev server（/api 代理到 8000）
pnpm -r typecheck            # tsc 全量检查
pnpm -r test                 # vitest
```

- API client 分两层：`packages/api-client/src/http.ts`（手写传输层：Bearer 注入、RFC 7807 解析、401 刷新重放）+ `src/api/generated/schema.d.ts`（由契约生成，勿手改）。改接口后先 `make api-snapshot && make sdk`。

### 数据库调试

见 [docs/DATABASE.md](docs/DATABASE.md)——含双角色连接方式、RLS 验证 SQL、慢查询定位、重置流程与常见故障排查。

## 开发指引

### 分层铁律（import-linter 强制）

```
stock_contracts   不依赖任何其它包
stock_core        只依赖 stock_contracts
stock_platform    只依赖 stock_contracts / stock_core
stock_ledger      只依赖 stock_contracts / stock_core / stock_platform
api / worker   顶层，可依赖所有下层；下层禁止反向依赖
```

改动后运行 `uv run lint-imports`（已并入 `make typecheck`）验证。

### 关键约定

1. **禁 float**：金额/价格/数量/比率一律用 `stock_core.values` 的标度整数（`Money`/`Price`/`Qty`/`Ratio`），API 边界输出定点字符串。
2. **零 wall-clock**：domain 层时间一律注入 `Clock`（contracts 提供 `SystemClock`/`FrozenClock`）。
3. **租户上下文**：`user_id` 只能来自凭证（`get_tenant` 依赖），仓储方法必须显式接收 `user_id` 并过滤；禁止由请求参数覆盖。
4. **写操作三件事**：幂等键（Idempotency-Key）、审计留痕（不含敏感明文）、RFC 7807 错误。
5. **长任务**：走 `stock_platform.jobs.JobQueue`（单飞 + 租约），API 返回 202 + `Location: /api/v1/jobs/{id}`，禁止同步阻塞。

### 新增端点的标准流程（契约先行）

```bash
# 1. 在 stock_api/routers/ 写路由 + stock_api/schemas.py 定义 Pydantic 模型（operation_id 必填）
# 2. 更新 docs/architecture/v2/API-Catalog.md §1 端点表与 §6 验收矩阵
make api-snapshot          # 重新生成契约快照
make api-check             # 校验 operationId 唯一 / 错误码一致 / 文档覆盖
make sdk                   # 同步前端 TS 类型
# 3. 在 backend/tests/api/ 补用例：每个 API-ID 至少 happy / 越权 / 关键错误码 三条
# 4. 迁移有表结构变更时：uv run alembic -c backend/alembic.ini revision --autogenerate -m "..."
```

### 提交前自检

```bash
make verify-p1    # 后端门禁（ruff / 单测 / api-check）
make typecheck    # mypy + import-linter + tsc
make lint         # ruff + eslint
```

## 测试说明

| 套件 | 命令 | 依赖 |
|---|---|---|
| 单测（core/contracts） | `make test` | 无 |
| core 覆盖门禁 | `make test-core`（要求 100%） | 无 |
| API 验收（真实 ASGI + 真库） | `make test-api` | PostgreSQL |
| RLS 双层隔离 | `make test-rls` | PostgreSQL |
| 越权负向矩阵 | `make test-isolation` | PostgreSQL |
| 架构（import-linter 元测试） | `make test-architecture` | 无 |

测试硬约束：不 mock 数据库（RLS 必须真连库）；每个用例自造数据自清理，不依赖执行顺序。测试读根 `.env` 的真实配置。

## 文档索引

- [Architecture-v2](docs/Architecture-v2.md) — 架构总纲（分层 / 隔离 / 任务 / 可靠性）
- [DEV-PLAN-v2](docs/DEV-PLAN-v2.md) — 开发计划（P1~P3 任务拆解与验收）
- [API-Catalog](docs/architecture/v2/API-Catalog.md) — 端点总表 / 错误码表 / 验收矩阵
- [数据库安装与 SQL 指引](docs/DATABASE.md) — 本文档配套的数据库专项文档
- [Testing](docs/architecture/v2/Testing.md) / [Reliability](docs/architecture/v2/Reliability.md) / [Extensibility](docs/architecture/v2/Extensibility.md) / [ADR](docs/architecture/v2/ADR.md)
