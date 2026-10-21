# 一期（P1）代码审查与验收报告

> 审查对象：HEAD `b61d441`。相关提交：`f27e645`（文档底座）、`9dd2b1e`（一期功能）、`6b72b9a`（真账单/测试）、`7194854`（注册登录优化）、`b61d441`（非交易账单）
> 验收依据：`docs/DEV-PLAN-v2.md` §0.5 DoD / §2 一期、`docs/architecture/v2/API-Catalog.md`、`docs/architecture/v2/Testing.md`、根 `AGENTS.md` 与各子系统 `AGENTS.md`
> 审查日期：2026-10-20 ｜ 方式：4 名评审并行只读审查 + Lead 全量门禁实跑 + 真实 HTTP 端到端 + 数据库/纯函数复算
> 报告分片（可追溯）：`var/review-p1/foundation.md`（地基）、`ledger.md`（导入/对账）、`api.md`（API/契约）、`frontend-qa.md`（前端/门禁）；原始日志 `var/review-p1/gates/`

> **修复进展（2026-10-21）**：**全部 5 项 P0 已修复并复验** —— P0-2/P0-3 见 [P1-Fix-Log-P0-2-P0-3.md](P1-Fix-Log-P0-2-P0-3.md)，P0-1/P0-4/P0-5 见 [P1-Fix-Log-P0-1-P0-4-P0-5.md](P1-Fix-Log-P0-1-P0-4-P0-5.md)；`make verify-p1` 15/15 全绿、真实 HTTP 复验 14/14 + 23/23。本报告作为审查时点记录保留，P1/P2 仍待修。

---

## 结论（先看这段）

**一期验收结论：不通过（No-Go），需修复后复验。**

- **主链路是通的**：真实 uvicorn + worker + PostgreSQL 下，注册/登录 → 上传预览 → 确认导入（202+job）→ 任务成功 → 流水/ETag → 撤销 → 越权 404 → admin 403 → 幂等重放 → 去重，**23/23 条走查断言通过**（`var/review-p1/live-e2e.log`）。
- **代码基础质量中上**：标度整数、RFC 7807 错误体、StrictQueryRoute/StrictModel、跨租户 404、refresh 轮换/复用检测、PAT 明文一次、ETag/304、SKIP LOCKED 队列、RLS fail-closed 谓词都有真实实现，且静态门禁全绿（ruff / mypy strict 69 文件 / import-linter 5 契约 / core 覆盖 100% / API 90 passed / 越权 18 passed / RLS 4 passed）。
- **但不能签字**：存在 2 个数据正确性/数据丢失 P0、1 个越权 P0、1 个核心验收句 P0、1 个验收证据失效 P0；另有 17 个 P1 级缺口（队列语义、契约漂移、分类未接入、去重/撤销不可恢复、admin PAT 等）。`make verify-p1` 虽然 exit 0，但它**不跑一期 API 验收、越权、RLS、mypy、core 覆盖率、前端门禁**（日志 `86 passed, 108 deselected`），"全绿"不能作为一期验收证据。

| 维度 | 结论 |
|---|---|
| 可运行性（本地起服务、主链路） | ✅ 通过（真实 HTTP 23/23；但 seed 账号无法登录、demo-p1 --run 必失败） |
| 数据正确性 | ❌ 不通过（真账单对账 156/175 假断链；`replace_range` 删而不插） |
| 多租户/权限安全 | ⚠️ 有条件（跨租户 404 实测有效；但 admin PAT 绕过禁区、RLS 仅覆盖 ledger、`summary_rule` WITH CHECK 有洞） |
| 契约一致性 | ❌ 不通过（快照已漂移、0 示例、0 problem+json、SDK 未接入、password 12→8） |
| 质量门禁 | ❌ 不通过（verify-p1 空心；无端点覆盖门禁；前端无 eslint） |
| 文档同步 | ❌ 不通过（一期"24 个端点"实为 30、口令/夹具/技术栈/门禁描述与实现不符） |
| 任务完成度（P1-01~P1-16） | **0 个完全完成 / 16 个部分完成 / 0 个未完成** |

**建议处置**：按 §9 的修复清单先修 5 个 P0（预计 3–4 人日）+ 安全类 P1（2 人日），扩容 `verify-p1` 后重跑 §10 复验清单；在修复前，本版本可作为"内部开发可用版本"，不可作为一期验收签字/进入二期。

---

## 1. 验收范围、判据与方法

### 1.1 判据（来自 DEV-PLAN §0.5，逐条核对）

- [ ] `make lint` / `make typecheck` 零错误（后端 ruff+mypy strict；前端 **eslint**+tsc）→ ⚠️ 后端达标；**前端无 eslint**
- [ ] 本期末端点的 API 验收用例 100% 通过（含幂等、越权、错误码）→ ⚠️ 90+18 绿但不在 verify-p1，API-075 零用例
- [ ] `make verify-pN` 一条命令全绿，并打印勾选清单 → ❌ exit 0 但仅 4 步、无清单（P0-5）
- [ ] 前端对应页面可点开、可操作、错误态可读 → ❌ 撤销入口缺失（P0-4）
- [ ] 本期新增/变更端点：契约快照已更新、示例与错误码齐全、TS client 已重生成 → ❌ 快照漂移、0 示例、SDK 未接入（P0-5/P1-12/P1-14）
- [ ] `docs/` 中受影响文档已同步（端点/口径/门禁）→ ❌ 多处漂移（§6）

### 1.2 范围

- 任务：`P1-01` ~ `P1-16`。
- 端点：`DEV-PLAN 附录 A.1` 标注为"一期"的 **30 个**（不是 §2 文字里的"24 个"，文档自相矛盾，见 §6）。OpenAPI 实现 32 个操作（一期 30 + 提前实现的 API-006/API-074）。
- 代码：`backend/packages/{contracts,core,platform,ledger}`、`backend/services/{api,worker}`、`backend/migrations`、`frontend/**`、`contracts/openapi.json`、`infra/**`、`scripts/**`、`Makefile`、`docs/**`。

### 1.3 方法（证据链）

1. **静态门禁实跑**：按 DEV-PLAN/Testing 拆成 26 个子步骤（ruff/format/mypy/import-linter/pytest 分层/core 覆盖率/契约检查/前端 lint+typecheck+test/seed 幂等），日志在 `var/review-p1/gates/`。
2. **真实 HTTP 端到端**：本地 PostgreSQL 18（Homebrew，5432，`stock_owner`/`stock_app`）+ uvicorn（8010）+ worker，脚本 `var/review-p1/live_e2e.py`，覆盖 §2.1 手工走查与关键负向路径；结果 `live-e2e.log`。
3. **数据/算法复算**：对 `backend/tests/api/华泰账单_mock.xlsx`（唯一真实脱敏账单）导入后，用 SQL 复算余额链两种口径；对 `replace_range`/撤销后重导/取消任务做真实 API 复现。
4. **四路独立代码审查**：A 地基（contracts/core/platform/迁移）、B ledger（解析/导入/对账）、C API/契约、D 前端/验收门禁；每路产出带 `file:line` 的分片，Lead 对关键结论二次实测（凡未实测项在文中标注"静态证据/高置信推断"）。

### 1.4 环境与局限

- 环境：macOS / Python 3.12 / PostgreSQL 18（Homebrew 5432）/ Node v24.19 / pnpm 12.10.1；`uv` 需 `UV_CACHE_DIR=<repo>/.uv-cache UV_OFFLINE=1`（沙箱不允许写 `~/.cache/uv`）。
- 局限：① 未跑 `make db-up/db-reset`（Docker 未验证，compose 在 5433 与当前 `.env` 5432 不同路径）；② 未做浏览器级 UI 点击（无 Playwright/浏览器），前端以 tsc/vitest + 代码审查判定；③ `make sdk` 依赖联网 `pnpm dlx`，未实跑，按实现判定；④ 真实账单只有 1 份（`华泰账单_mock.xlsx`）；⑤ 未做 5 万行/10 年数据量与并发压测（属二/三期）。

---

## 2. 质量门禁实跑结果（26 步）

| # | 检查 | 结果 | 关键输出 / 备注 |
|---|---|---|---|
| 1 | ruff check | ✅ exit 0 | All checks passed |
| 2 | ruff format --check | ✅ exit 0 | 84 files already formatted |
| 3 | mypy strict（packages+services） | ✅ exit 0 | Success: no issues found in 69 source files |
| 4 | import-linter | ✅ exit 0 | L1~L5 全部 KEPT，5 kept / 0 broken |
| 5 | pytest 快速单测（`-m "not integration and not api"`） | ✅ exit 0 | 86 passed, 108 deselected |
| 6 | core 覆盖率门禁 | ✅ exit 0 | 31 passed；`TOTAL 128 stmts 100%`（**仅行覆盖，未卡分支**） |
| 7 | API 验收套件 `backend/tests/api` | ✅（干净重跑） | **90 passed**；首跑出现 1 个 `DeadlockDetectedError`（TRUNCATE 与并发测试互锁），重跑全绿 → 套件对并发环境脆弱 |
| 8 | 越权矩阵 `-m isolation` | ✅ exit 0 | 18 passed, 176 deselected |
| 9 | RLS 直连 `tests/integration/test_rls_isolation.py` | ✅ exit 0 | 4 passed（仅 4 个场景） |
| 10 | 架构约束 tests/architecture | ✅ exit 0 | 1 passed |
| 11 | `make api-check` | ✅ exit 0 | 28 paths / 32 ops；一期未实现 0；§3=47 vs CATALOG=49（2 个未收录） |
| 12 | `make api-coverage` | ⚠️ 信息输出 | 按 FR 分组列出已实现/未实现，未提供"一期覆盖 100%"硬断言 |
| 13 | `make api-snapshot --check` | ❌ **exit 1** | 快照 108290 B vs 现实现 108289 B；唯一差异 `RegisterIn.password.minLength` **12 vs 8** |
| 14 | `make verify-p1` | ✅ exit 0 | **仅 4 步**：ruff check/format、`pytest -m "not integration and not api"`（86 passed 108 deselected）、api-check；**无勾选清单** |
| 15 | `make lint` | ✅ exit 0 | 后端 ruff + 前端 `pnpm -r lint`；**前端 lint 实为 tsc，全仓无 eslint** |
| 16 | `make typecheck` | ✅ exit 0 | mypy + import-linter + tsc |
| 17 | 前端 web vitest | ✅（node 24 下） | 2 tests passed；本仓脚本用 login shell 时曾因旧 node 报 `Unexpected token '??='`，固定 node 后通过 |
| 18 | 前端 api-client vitest | ✅（node 24 下） | 4 tests passed；合计仅 6 条用例，无 coverage |
| 19 | `make seed-tenants`（第 1 次） | ✅ exit 0 | 建 A/B/admin，导入成功 |
| 20 | `make seed-tenants`（第 2 次幂等） | ✅ exit 0 | 账号已存在跳过 |
| 21 | `make seed-demo` | ✅ exit 0 | 成功；**但重写 tracked 的 `fixtures/market/bill_huatai.xlsx`，字节非确定性**（收尾已 `git checkout` 恢复） |
| 22 | `make migrate` / `alembic current/check` | ❌ exit 1 | 当前库 `public.alembic_version` 属主为历史角色 `sa_owner`，`stock_owner` 无权限 → permission denied；**非迁移代码缺陷**（见下） |
| 23 | 迁移幂等（干净临时库复验） | ✅ | 新建 `stock_p1_review`（owner=stock_owner）→ upgrade ×2 + `alembic check` 全通过；3 schema / 21 表 / 7 条 RLS 策略 |
| 24 | `pnpm -r build`（前端生产构建） | ✅ exit 0 | 49 modules，229.74 kB js（额外验证） |
| 25 | 真实 HTTP E2E（23 条断言） | ✅ 23/23 | 见 §3 |
| 26 | SSE / 取消终态 | ✅ / ❌ | SSE 200 + progress 事件正常；**pending 任务取消后永不终态**（复现，见 P1-2） |

**关于 `make migrate` 失败（环境级，非代码缺陷）**：`public.alembic_version` 的表属主是 `sa_owner`（历史库残留），而 `.env` 迁移用 `stock_owner`。在干净库上迁移是幂等且无 drift 的（第 23 项）。处置：`make db-reset`（需把 `.env` 端口与 compose 5433 对齐）或由超级用户 `ALTER TABLE public.alembic_version OWNER TO stock_owner`。同时暴露两个本地编排缺口：`make dev` **并不启动 DB 容器**（与 DEV-PLAN §2.1 描述不符），`make db-reset` 固定走 compose 5433 而当前 `.env` 指 5432（P2）。

**结论：门禁"全绿"是假象。** `make verify-p1` 排除了 108 条 api/isolation 用例，恰好是一期验收的主体；且不含 mypy、import-linter、RLS、core 覆盖率、契约漂移、前端门禁。任何人只看 `verify-p1 exit 0` 都会得出错误结论。

---

## 3. 真实 HTTP 端到端实测（§2.1 手工走查 + 负向）

环境：真实 uvicorn @127.0.0.1:8010 + worker + PG；脚本 `var/review-p1/live_e2e.py`（23 条断言全 PASS，`live-e2e.log`）。

| 组 | 断言 | 结果 |
|---|---|---|
| 运维 | `/readyz` db=ok、`/version` app_version=0.1.0、`/api/v1/errors` 49 码 | ✅ |
| 认证 | 注册 201、登录 200（合法邮箱） | ✅ |
| 预览 | API-010 200，`estimated={parsed:120,inserted:120,skipped:0,quarantined:1}`，预览不落库 | ✅ |
| 导入 | API-011 202 + `Location: /api/v1/jobs/...`；同键同体幂等重放（同 job_id + `Idempotency-Replayed: true`） | ✅ |
| 任务 | worker 真实消费，`status=succeeded/progress=100`；批次 counts inserted=120 | ✅ |
| 流水 | API-017 列表、`ETag`→304、未知 query → 400 `UNKNOWN_QUERY_PARAM`、API-080 详情 | ✅ |
| 去重 | 同文件再传 `inserted=0, skipped=120`（撤销前） | ✅ |
| 撤销 | API-015 200；行数 120→0；重复撤销 409 `IMPORT_ALREADY_REVOKED` | ✅ |
| 越权 | B 读 A 的 trade → 404 `NOT_FOUND`；B 列表不含 A；admin JWT 读业务 → 403 `ADMIN_CANNOT_READ_TRADES`；匿名 → 401 | ✅ |
| 隔离 | B 独立导入 120 行成功（与 A 互不影响） | ✅ |
| SSE | `text/event-stream`，queued→progress 事件正常；`Last-Event-ID` 以 query 参数实现（契约偏差见 P2-8） | ✅ |
| 取消 | 已终态取消 → 409 `JOB_NOT_CANCELLABLE` | ✅ |

**走查失败/不可达项（文档承诺 vs 实际）**：

1. **§2.1 第 1 步 `a@local` 登录失败**：seed 账号邮箱 `a@local` / `b@local` / `admin@local` 不满足 `EmailStr`（必须带点的域名），API 登录返回 422；文档口令 `demo1234` 也与实际 `Demo!Passw0rd2026` 不符。
2. **§2.1 第 4 步浏览器"撤销本次导入"不可完成**：前端没有导入批次列表/详情/撤销入口（`api.imports.revoke` 零调用）。
3. **`make demo-p1 --run` 必失败**：第 2 步注册 `demo@local` 即 422；即使修邮箱，第 5 步写 `mode:"append"` 且缺 `Idempotency-Key` 仍会 422/校验失败。
4. **§2.1 第 2 步夹具路径/行数不符**：仓库无 `fixtures/bills/`；实际是 `fixtures/market/bill_huatai.xlsx`（122 行 / 解析 120 / 隔离 2），不是"328 行"。

---

## 4. 任务级验收结论（P1-01 ~ P1-16）

| ID | 计划产出（摘要） | 实测结论 | 结论 | 主要缺口（详见 §5） |
|---|---|---|---|---|
| P1-01 | Monorepo/工具链/ruff/mypy/**eslint**/tsc/import-linter | 目录与 workspace 齐；ruff/format/mypy/import-linter/tsc 实测全绿 | 部分完成 | **无 eslint**（前端 lint 实为 tsc）；无 branches 覆盖门禁 |
| P1-02 | compose 仅 PG、.env.example、dev/db-up/db-reset | 静态齐备；`db-up/db-reset` 走 Docker 5433，当前 `.env` 5432；`make dev` 不启 DB | 部分完成 | 本地编排与 §2.1 描述不一致；未实跑 Docker |
| P1-03 | 多 schema + 迁移 + RLS + 角色 | 3 schema/21 表/7 条 RLS 策略；干净库迁移幂等；app 角色 NOBYPASSRLS | 部分完成 | **RLS 只覆盖 ledger 7 表**，13 张含 user_id 的 platform 表无 RLS；`summary_rule` WITH CHECK 有洞；当前库 `make migrate` 失败（环境） |
| P1-04 | 错误码 47、常量、DTO 基座、标度整数、Clock、确定性随机、属性测试 | 4 个标度类型 + Clock + 单测；core 行覆盖 100% | 部分完成 | 错误码实为 49；无 DTO 基座；无确定性随机；core 无 hypothesis 属性/溢出用例；未卡分支覆盖 |
| P1-05 | FastAPI 装配/中间件/RFC7807/ops | 端点齐、错误体六字段齐、探针可用 | 部分完成 | `/readyz` 字段与 Catalog 不符、无迁移滞后 503 用例；`/version` 与 openapi 版本口径不清；中间件链只有 request_id（认证/租户在依赖、限流仅登录） |
| P1-06 | 契约快照、api-check、SDK、diff 门禁 | 快照/SDK 已提交；`api-check` 通过 | 部分完成 | **快照漂移（password 12→8）**；`api-check` 不查示例/problem+json/x-*；无"删示例→非 0"元测试；SDK 生成物零引用；`make sdk` 需联网且未锁版本 |
| P1-07 | 注册/登录/refresh/登出/me、Argon2id、PAT、限速 | 功能齐；Argon2id、轮换+复用检测、PAT 明文一次、5 次失败 429 | 部分完成 | **admin PAT 被降级为 user → 可读交易**；JWT 无 aud/iss；401 无 WWW-Authenticate；password 12→8；登出后 refresh 返回 TOKEN_REUSED（文档要 TOKEN_REVOKED） |
| P1-08 | TenantContext、SET LOCAL + 归还 RESET、仓储 user_id、串号测试 | TenantContext 仅由凭证构造；仓储显式 user_id；SET LOCAL 为事务级；RLS 串号测试 1 条 | 部分完成 | **连接归还 RESET 为空实现**（checkin 钩子无效）；`clear_tenant`/`with_tenant` 死代码；无并发 100 次串号用例；RLS 覆盖缺口 |
| P1-09 | 华泰解析器 + 预览 | 别名/多日期/表头探测/预览不落库实测通过；真实账单 177 行可解析 | 部分完成 | 无 BillParser 注册表（broker 硬编码）；**无未知摘要预检**；Excel 原生日期/序列号不支持（整行隔离）；损坏文件抛裸异常→500 |
| P1-10 | 导入提交/去重/隔离区/撤销 | 202+job、同文件重传 inserted=0、撤销回退、隔离区落库均实测 | 部分完成 | **`replace_range` 删而不插（数据丢失）**；**撤销后重导 inserted=0 无法恢复**；同日同量多笔在缺余额列/跨文件时被吞；`replaced_batch_id` 恒 NULL |
| P1-11 | 队列 + worker + jobs 端点 + cancel/SSE | SKIP LOCKED、单飞索引、reaper、jobs 四端点、SSE 可用 | 部分完成 | **心跳方法从未被调用**（>90s 任务会被重投）；**pending 取消永不终态**；同 scope 异参静默复用旧 job；`result_ref` 恒空；handler 抛错可打挂消费循环 |
| P1-12 | 游标分页/过滤/排序/ETag/PATCH/软删/恢复/history | 游标分页、ETag/304、PATCH 409、软删/恢复/history 均有实现与用例 | 部分完成 | **`sort` 只校验不生效**；**`batch_id` 过滤空转**（实测假 batch_id 仍返回全部 120 行）；`include_total` 未实现；游标不签名/不绑过滤 |
| P1-13 | 默认规则/分类/隔离区/对账 | 14 条默认规则、断链定位、degraded 路径有测试 | 部分完成 | **真账单对账 156/175 假断链**；**commit 不写 category**（实测全 NULL）；默认规则与解析器非交易词汇漂移（26/177 未命中）；API-016 属二期（非一期缺口，但分类无一期入口） |
| P1-14 | replay 夹具/10 年生成器/seed/FrozenClock | seed 幂等、FrozenClock、断网可跑成立 | 部分完成 | **replay 适配器不存在**（quotes.json 只写不读）；10 年生成器只有参数无调用/测试；seed/demo 写 `mode="append"` 与契约冲突；夹具字节非确定性；seed 账号不可登录 |
| P1-15 | 前端骨架/登录注册/上传预览/流水/错误边界/守卫/token 内存/静默刷新 | 登录/注册/流水/Problem 错误态/401 单飞刷新可用；生产构建通过 | 部分完成 | **撤销入口缺失（§2.1 第 4 步不可完成）**；无 TanStack Query；生成 SDK 未接入；**token 落 localStorage**（违背"内存"）；无 React ErrorBoundary/角色 403 页；SSE 无轮询兜底 |
| P1-16 | 一期验收套件 + verify-p1 + demo-p1 + docs | API 90/越权 18/RLS 4 可跑；seed 幂等 | 部分完成 | **verify-p1 空心**（4 步、无清单）；API-075 零用例；越权矩阵只覆盖 10 资源端点；无端点覆盖门禁；demo-p1 --run 失败；文档多处漂移 |

> 说明：P1-16 的 `make verify-p1 exit 0` 是"部分完成"而非"完成"，因为 DoD 要求它跑"一期全部断言并打印勾选清单"。

---

## 5. 发现清单

严重度定义：**P0 = 阻断验收**（数据丢失/数据错误/越权/核心验收句不可完成/验收证据失效）；**P1 = 严重**（验收前修复或书面豁免）；**P2 = 中等**（本期内修或登记已知偏差）；**P3 = 轻微**。

**统计：P0 × 5 ｜ P1 × 17 ｜ P2 × 16 ｜ P3 × 6。**

### 5.1 P0（5 项，必须修复后复验）

#### [P0-1] 真实账单金额口径未归一 → 对账 156/175 条假断链（P1-13 失效）（✅ 2026-10-21 已修复，见 Fix Log）
- **位置**：`backend/packages/ledger/src/stock_ledger/parsers/huatai.py:213-228`（成交金额原样落库）、`backend/packages/ledger/src/stock_ledger/application/reconcile.py:61-69`（`_signed_delta_units` 再反号+再扣费）
- **问题**：真实账单的"成交金额/发生金额"是**已扣费、带符号**的现金流（买入为负），解析器原样存入 `amount_units`；对账却按 `buy: -(amount+fees) / sell: amount-fees` 计算 → 符号反转、费用重复扣减。而合成夹具 `fixtures/market/bill_huatai.xlsx` 是"毛额 + 费用单列"，所以测试全绿。
- **实测**（真实 HTTP + SQL 复算，同一份 `backend/tests/api/华泰账单_mock.xlsx`）：
  - 现行算法：`checked=175, breaks=156`，`status=blocked`；首条 line 15 `expected=18298.58 / actual=3473.43 / diff=-14825.15`；
  - 改用 `prev_balance + amount_units`：**`breaks=0/175`**（该账单余额链自洽）；
  - 合成夹具：现行算法 `0/119`（掩盖差异）。
- **影响**：FR-L6/P1-13"余额链对账"在真实账单上直接失效；二期净值/回合会被系统性带偏。
- **建议**：解析层定义并归一 `cash_flow`（带符号净额）与 `amount`（毛额）；对账统一用 `cash_flow`；增加"真实账单 → 对账 ok"回归 fixture；对两套口径做交叉校验（不一致 → warning/隔离）。
- **验证**：真实账单导入后 `GET /reconciliation` 应 `status=ok, breaks=0`；合成的 broken fixture 仍能检出 1 条。

#### [P0-2] `replace_range` 模式删而不插，数据直接丢失（✅ 2026-10-21 已修复，见 Fix Log）
- **位置**：`backend/packages/ledger/src/stock_ledger/application/imports.py:145-168`（先 `soft_delete_range`，再查 `existing_dedupe_keys`）、`backend/packages/ledger/src/stock_ledger/repositories/trades.py:403-410`（不过滤软删）、`routers/imports.py:283-289`（入队 params 不含 range/mapping）
- **问题**：区间替换先软删区间内旧行，随后去重键查询把**刚被软删的行**算作"已存在"，本批全部 `skipped` → 旧行已删、新行 0 插入。且替换区间取账单 min/max 日期，**忽略请求体的 `range`**；`replaced_batch_id` 恒 NULL。
- **实测**：导入 120 行 → `mode=replace_range` 同文件重传 → 批次 `{"inserted":0,"skipped":120}`，`GET /trades` **120→0 行**。
- **影响**：PRD FR-L3"区间替换（修复模式）"变成数据清除；无可回滚血缘。
- **建议**：`existing_dedupe_keys` 过滤 `deleted_at IS NULL`；唯一约束改 partial unique index（见 P1-3）；使用请求 `range` 并校验账单日期；写 `replaced_batch_id`/旧行 ID；补"replace_range 后行数不变"用例。
- **验证**：导入 → replace_range 重传同文件 → `inserted==120` 且 `/trades` 行数不变。

#### [P0-3] admin 的 PAT 被降级为 user，可绕过 `ADMIN_CANNOT_READ_TRADES`（越权）（✅ 2026-10-21 已修复，见 Fix Log）
- **位置**：`backend/services/api/src/stock_api/deps.py:130`（`_resolve_pat` 硬编码 `role=Role.USER`）、`routers/tokens.py:48`（允许 admin 建 PAT）、`platform/models.py:92-113`（`api_token` 无 role 列）
- **问题**：JWT 走真实角色，PAT 一律当 user；`forbid_admin_trades()` 只看 role，于是 admin 用 JWT 建 PAT 后即可读交易明细。
- **实测**：admin JWT `GET /trades` → **403**；同 admin 的 PAT `GET /trades` → **200**（items 可读）。现有测试只覆盖 admin JWT，未覆盖 admin PAT。
- **影响**：身份越权防线可绕，二期 admin/审计/导出端点上线后风险扩大。
- **建议**：`_resolve_pat` 回查 `platform.user.role`（或 token 冗余 role 并随降权失效）；补 `test_admin_pat_cannot_read_trades`；把 PAT 纳入越权矩阵。
- **验证**：admin JWT 建 PAT → 用 PAT 读 `/trades` 应 403。

#### [P0-4] "撤销本次导入"在 UI 完全不可达（§2.1 第 4 步不可完成）（✅ 2026-10-21 已修复，见 Fix Log）
- **位置**：`frontend/apps/web/src/pages/Import.tsx:44-118`（无批次/撤销）、`router.tsx:55-60`（无批次页路由）、`packages/api-client/src/http.ts:542-554`（`listBatches/getBatch/revoke` 已实现但零调用）、`ui/QuarantineList.tsx:20`（孤儿组件）
- **问题**：页面只有"上传→预览→确认导入→进度"，没有导入批次列表/详情/隔离区/撤销按钮；用户无法执行文档承诺的"点撤销本次导入 → 行数回退"。
- **影响**：P1-15 验收句与 §2.1 手工走查 1–5 不成立；后端已实现的 API-012/014/015 对用户不可达。
- **建议**：新增 `/imports` 批次列表 + 详情/隔离区/撤销（接 `listBatches/getBatch/quarantine/revoke`），撤销后刷新流水；补页面测试。
- **验证**：浏览器完成 §2.1 第 4 步；Vitest 覆盖 revoke 调用与行数回退。

#### [P0-5] 验收证据失效：`verify-p1` 空心 + 契约快照已漂移（✅ 2026-10-21 已修复，见 Fix Log）
- **位置**：`backend/services/api/src/stock_api/cli.py:414-442`（VERIFY_STEPS 仅 4 步）、`contracts/openapi.json`（password minLength=12）vs `schemas.py:23`（8）、`cli.py:341-368`（`api-snapshot --check` 存在但未被 verify-p1/Makefile 调用）
- **问题 A**：`make verify-p1` 只跑 ruff×2 + `pytest -m "not integration and not api"` + api-check；日志 **86 passed, 108 deselected**，把一期 API（90）与越权（18）全部排除；不含 mypy/import-linter/RLS/core 覆盖率/契约 diff/前端；不打印勾选清单。api-check 对"一期端点未实现"仅告警、不失败。
- **问题 B**：`api-snapshot --check` **exit 1**：快照 vs 实现差 1 字节（`RegisterIn.password.minLength` 12 vs 8），说明契约已漂移而门禁未拦住；`contracts/openapi.json` 中 `example(s)`=0、`problem+json`=0、无 `Error` schema，P1-06"故意删示例→非 0"无法成立。
- **影响**：'verify-p1 全绿'不能证明一期验收；契约/SDK/文档三方已不一致（password 被悄悄弱化到 8 位）。
- **建议**：`verify-p1` 扩充为 lint + typecheck + test-core + `pytest backend/tests/api` + `-m isolation` + RLS + `api-snapshot --check` + 前端 lint/typecheck/test，并逐 ID 打印清单；`api-check` 增加示例/problem+json/x-* 必填校验与"删示例必失败"元测试；修复密码策略（恢复 12 或走契约变更流程并同步文档/快照）。
- **验证**：`make verify-p1` 输出包含 90+18+4 用例且 `api-snapshot --check` exit 0。

### 5.2 P1（17 项，验收前修复或书面豁免）

#### [P1-1] worker 从未心跳续租，>90s 任务会被 reaper 判过期重投（双执行）
- `platform/jobs.py:26`（`LEASE_SECONDS=90`）、`:218-224`（`heartbeat()` 定义但全仓 0 调用）、`:226-236`（`progress()` 不续租）、`:300-326`（reaper 无行锁）；`worker/runner.py:49-56,84-105`。
- 影响：导入/后续报表/导出的长任务会重复执行；`job_event.seq` 冲突、终态互相覆盖。P1-11 计划产出"租约/心跳"不成立。
- 修法：执行期间起心跳协程（≤20s，独立短事务）；reaper 的 SELECT 加 `FOR UPDATE SKIP LOCKED` + UPDATE 条件；补"长任务只执行一次"用例。

#### [P1-2] pending 任务取消后永不进终态，且永久占并发配额
- `platform/jobs.py:238-239`（只置 `cancel_requested`）、`:186-192`（claim 跳过取消标记）、`:76-85`（配额统计含 pending）；`routers/jobs.py:241-247`。
- 实测：提交后停 worker → cancel 202 → 轮询 6 秒仍 `pending`（API-064 验收要"终态 cancelled"）；用户取消 N 个 pending 后新任务 `QUOTA_EXCEEDED`。
- 修法：`pending` 取消直接迁移 `cancelled` + finished_at + 事件；`running` 沿用检查点收口；补用例断言终态。

#### [P1-3] 软删行永久占用去重键 → 撤销后同文件重导 `inserted=0`，用户无法恢复
- `migrations/versions/0001_phase1_schema.py:482`（`UniqueConstraint(user_id,dedupe_key)` 无 partial）、`ledger/models.py:132`、`repositories/trades.py:403-410`（不过滤 deleted_at）。
- 实测：导入 120 → revoke（120→0）→ 同文件再 preview `inserted=0/skipped=120` → commit 后仍 0 行；用户无法通过重导恢复（只能逐行 restore）。
- 修法：`CREATE UNIQUE INDEX ... WHERE deleted_at IS NULL`；去重查询过滤软删；preview/commit 对"已撤销批次"给可重导语义。

#### [P1-4] 单飞：同 `(kind, scope_key)` 参数不同被静默复用旧 job，`JOB_ALREADY_RUNNING` 全仓未抛
- `platform/jobs.py:70-74`（两分支都 `return existing.id, True`）；`routers/imports.py:294`（scope=`user_id:preview_id`）。
- 影响：同一 preview 先后以 `full` / `replace_range` 提交（不同幂等键）时，第二个请求 202 但执行的是旧参数；`replace_range` 语义可能从未发生。`JOB_ALREADY_RUNNING` 有码无调用。
- 修法：digest 相同才复用，否则 409 `JOB_ALREADY_RUNNING` + 旧 job_id；补用例。

#### [P1-5] RLS 第二道防线只覆盖 ledger：13 张含 `user_id` 的表中 6 张 platform 表无 RLS
- 实测（PG catalog）：`ledger.import_batch/import_preview/quarantine_row/reconciliation/summary_rule/trade/trade_history` 7 表有 RLS；`platform` 侧 6 张含 `user_id` 的表（`api_token/idempotency_key/job/outbox_event/refresh_token/user_setting`）**无 RLS**，`job_event`/`audit_log` 等关联表亦无策略。迁移注释写"platform 表由仓储层 user_id 过滤"。
- 与 `docs/Architecture-v2.md` §14.3"**所有租户表** ENABLE ROW LEVEL SECURITY"直接冲突；AGENTS"RLS 兜底，漏过滤也不会串号"只对 ledger 成立。
- 影响：job/api_token/user_setting/idempotency 等一旦漏写 `WHERE user_id` 即串号；现有跨租户测试靠应用层过滤才绿。
- 修法：为租户级 platform 表补 `tenant_isolation` 策略（认证前置表可单独策略/系统会话）；补 R6"逐表策略覆盖 100%"门禁；RLS 测试从 4 条补到 Testing §4.3 的 8 类场景。

#### [P1-6] `summary_rule` 的 RLS `WITH CHECK` 放行 `user_id IS NULL`，app 角色可写全局系统规则
- `migrations/versions/0001_phase1_schema.py:47-55`：同一 predicate `(user_id IS NULL OR tenant)` 同时用于 USING 与 WITH CHECK；`stock_app` 有全表 DML。
- 影响：任意登录用户可插入/修改全局系统规则（当前无写端点故为潜在，二期规则 CRUD 会放大）。
- 修法：拆策略——租户行 `WITH CHECK (user_id = tenant)`；系统行只 `FOR SELECT`；系统行仅 owner 写。

#### [P1-7] 分类链路未闭环：commit 不写 category，未知摘要预检缺失，分类仅 CLI 可触发
- `application/imports.py:77-234`（commit 不调用 classify）；`parsers/huatai.py:266-388`（无未知摘要判定）；`WarningCode.UNKNOWN_SUMMARY` 全仓无引用；`application/classify.py` 仅被 CLI seed/worker handler 调用。注：API-016 无路由**不**作为一期缺口——DEV-PLAN A.1 把 API-016 归二期（API-Catalog §1 的 P1 是优先级而非期次）。
- 实测：真实账单 commit 后 177 行 `category` **全为 NULL**；preview 显示 `quarantined=0`，但分类阶段有 26 行未知摘要应隔离。
- 影响：P1-13"分类流水线"名存实亡；前端流水无分类；隔离区与预估不一致。
- 修法：preview 阶段用默认规则 dry-run 预检并计入 quarantined+warnings；commit 同事务分类或入队 reclassify；补 category 非空断言。

#### [P1-8] 默认分类规则与解析器"非交易摘要"词汇漂移 → 真实账单 26/177 行误隔离
- `ledger/repositories/rules.py:23-38`（"银行转证券/证券转银行" exact）vs `parsers/columns.py:191-217`（"银行转存/银行转取/转存管转入/转出"）。
- 实测/复算：真实账单 14 行"银行转存"+10 行"银行转取"+2 行"转存管转…"均未命中 → 26/177 误入隔离；seed 日志 A/B 分别隔离 26/27。
- 修法：规则改为 contains 覆盖存/取/转存管词汇，或与 parser 共享同一常量；加"真实账单命中率 100%"回归。

#### [P1-9] 损坏/Excel 原生日期等真实账单必经路径未处理（500 / 整行隔离）
- `parsers/huatai.py:132-151`（`load_workbook`/`xlrd`/csv 未包异常 → `BadZipFile`/`XLRDError` 裸抛 → API 500）；`:50-54` + `columns.py:113-160`（Excel 原生 `datetime` / 序列号转 `str` 后解析失败 → 整文件 0 行可导入）。
- 影响：违反 API-010 只允许 415/413/422 的错误契约；真实券商 Excel 可能整批失败。
- 修法：`_read_rows` 外层捕获 → `IMPORT_UNSUPPORTED_FILE`；xlsx/xls 单元格类型归一（date→YYYYMMDD，Excel serial 转日期）；补 fixture。

#### [P1-10] seed/demo 链路与 API 契约冲突：`mode="append"`、seed 账号邮箱不可登录、demo-p1 --run 必失败
- `cli.py:493`（seed `mode:"append"`）、`:625,720`（demo 同值且缺 `Idempotency-Key`）、`schemas.py:157,186`（只接受 full/replace_range）。
- 实测：DB 中 seed 批次 `mode='append'` 共 2 条；`BatchOut.model_validate(mode="append")` 直接 `ValidationError` → 这两个账号一旦能登录，`GET /imports` 会 500（高置信推断，未直接打端点）。`make demo-p1 --run` 在注册 `demo@local` 处 422（实测）。
- 同时：`a@local/b@local/admin@local` 因 `EmailStr` 不合法无法登录（实测 422）；文档口令 `demo1234` ≠ `Demo!Passw0rd2026`。
- 修法：统一 `mode="full"`；demo 补幂等键；seed 账号改合法邮箱（`a@local.test`）或放宽 EmailStr；同步文档口令/夹具路径。

#### [P1-11] `GET /trades` 的 `sort` 只校验不生效，`batch_id`/`include_total` 参数空转
- `routers/trades.py:152,155,162-163,176-186`；`ledger/application/trades.py:32-40,179-203`（TradeFilters 无 batch_id，list_trades 无 sort）。
- 实测：`?batch_id=imp_不存在` 仍返回全部 120 行（静默错误数据集）；`sort` 不影响顺序。
- 修法：TradeFilters 加 batch_id；sort 白名单透传到仓储（带 id tiebreaker）；实现 include_total 或删除参数；补 3 条用例。

#### [P1-12] 契约快照漂移 + 密码策略从 12 位弱化为 8 位（安全基线）
- `schemas.py:23`、`security.py:23`（`MIN_PASSWORD_LENGTH=8`，docstring 仍写 12）、`API-Catalog.md:139`（12~128）、`contracts/openapi.json`（minLength 12）、commit `7194854` 只改代码未同步快照/文档。
- 影响：契约/客户端/安全口径三方不一致；8 位高复杂度口令可通过（实测 `validate_password_strength("Ab1!abcd")==[]`）。
- 修法：恢复 12 位或走契约变更流程（改文档+快照+变更说明）；补 8~11 位拒绝用例。

#### [P1-13] 前端 token 落 localStorage，违背 P1-15"token 内存+静默刷新"
- `frontend/apps/web/src/store/session.ts:3,9-10,29-30`（access+refresh 都持久化；refresh 30 天）。
- 影响：任意 XSS 可窃取 30 天 refresh token，抵消"轮换+复用检测"。
- 修法：令牌放内存（模块/React context）；refresh 用 httpOnly+SameSite Cookie（需后端配合）或至少 sessionStorage；补 auth/guard 单测。

#### [P1-14] 生成式 SDK 未接入，前端 DTO 全手写，契约漂移不可见
- `packages/api-client/src/api/generated/schema.d.ts` 无任何 import；`http.ts:108-255` 手写 Trade/Preview/Job；`index.ts` 注释"当前仅占位"；已出现 `auth.me()` 调 `/account/me`（实际 `/auth/me`）的潜伏错误。
- 影响：P1-06"生成 client 后手写类型即报错"不成立；后端改字段前端不会编译失败。
- 修法：`http.ts` 类型改用 `components["schemas"]/paths`；删除手写 DTO；CI 加"生成物 diff 为空"检查。

#### [P1-15] 前端无 eslint，"lint 门禁"只是 tsc
- 三个 `package.json` 的 `lint` 都是 `tsc --noEmit`；全仓无 eslint 依赖/配置；`Makefile:31`、README、DEV-PLAN、`frontend/AGENTS.md` 均声称 eslint。
- 影响：P1-01/DoD"前端 eslint+tsc"不成立；二期零口径 ESLint 无挂载基础。
- 修法：加 eslint@9 + typescript-eslint + react-hooks，`lint = eslint . && tsc --noEmit`。

#### [P1-16] 前端 §2.1 关键路径与兜底缺失：无 SSE 轮询兜底、无批次/隔离区 UI、无角色 403 页
- `ui/JobProgress.tsx:46-63`（仅 SSE，5 次重试后放弃，无 `GET /jobs/{id}` 兜底）；`QuarantineList.tsx` 孤儿；`router.tsx:10-14`（仅判断 token 存在）；`Import.tsx` 结果页不显示批次/计数、不刷新流水。
- 修法：JobProgress 加轮询兜底；批次/隔离区 UI（同 P0-4）；角色守卫/403 页；结果页展示 counts 并刷新列表。

#### [P1-17] 上传体积/解压炸弹/内容嗅探缺失（认证用户可内存 DoS）
- `routers/imports.py:184`（`await file.read()` 无上限，读完才判 20MB）、`parsers/huatai.py:111-120,132-151`（zip 解压无大小限制，仅扩展名判断）。
- 修法：分块读取累计上限（超限立即 413）；zip 条目/解压总大小校验；魔数嗅探（xlsx `PK\x03\x04`、xls `D0CF11E0`）。

### 5.3 P2（16 项，本期内修或登记）

| ID | 问题 | 位置 | 建议 |
|---|---|---|---|
| P2-1 | 连接归还 RESET 为空实现（checkin 钩子只清不存在的属性）；`clear_tenant`/`with_tenant` 死代码（且守卫参数取错） | `platform/db.py:53-67,107-118` | 明确 SET LOCAL 事务语义或实现真正的归还清理；修/删死代码；补并发串号用例 |
| P2-2 | 错误码枚举 49 ≠ 文档/seed 47（多 `IMPORT_PREVIEW_NOT_FOUND`、`JOB_LEASE_EXPIRED`）；api-check 只提示不失败 | `contracts/errors.py`, `cli.py:232-246` | 补进 §3 或改回 NOT_FOUND；`SEED_ERROR_COUNT` 改派生 |
| P2-3 | 幂等键 24h TTL 未生效（冲突比对不看 `expires_at`，无 purge） | `platform/idempotency.py:57-74` | 过期即删后按首次处理；加 purge job |
| P2-4 | Clock 与 DB `now()` 混用（enqueue 的 available_at 用 DB 默认值、ids/outbox/repos 用墙钟），FrozenClock 复放不完整 | `platform/jobs.py:88-106`, `ids.py:23` 等 | enqueue 显式注入 clock；业务时间统一收口 |
| P2-5 | 游标不签名/不绑 user/filter/24h；ETag 为强 `"sha"` 而非 `W/"version"` | `ledger/repositories/trades.py:50-66`、`routers/trades.py:86-93` | 游标上收到 platform + HMAC/TTL；ETag 统一弱格式 |
| P2-6 | JWT 无 aud/iss 且不校验；401 无 `WWW-Authenticate`；缺凭证复用 `TOKEN_EXPIRED` 文案 | `platform/security.py:121-160`、`errors.py` | 签发/校验加 aud/iss；中间件注入 WWW-Authenticate；缺凭证用专门码 |
| P2-7 | 错误体缺 `instance`；405 被映射成 422；`X-Request-ID` 响应头重复；审计 request_id 取客户端头而非中间件值 | `errors.py:19-27,39-54`、`middlewares.py:59`、各 router | 逐项修正；补断言 |
| P2-8 | SSE `Last-Event-ID` 用 query 而非 HTTP 头；无 `event: reconnect`；`result_ref` 恒空 | `routers/jobs.py:112,128-136`、`platform/jobs.py:245-273` | 从 Header 读取并兼容 query；补 reconnect；finish 传 result_ref |
| P2-9 | 运维端点契约漂移：`/readyz` 字段（status/checks vs db/schema_version/draining）、`/version` 缺 git_sha、openapi.json 无 ETag/no-cache、`/api/v1/version` 未登记 | `ops.py:31-99`、`version.py`、`app.py:89,103` | 按 Catalog 重塑；登记或删除多余路径；API-075 用例落地 |
| P2-10 | 导入契约缺口：preview 缺 broker/mode；commit 忽略 range/mapping；`can_commit` 恒 true；跨用户 preview 404 用未登记码 | `routers/imports.py:171-178,223-227,283-289`、`ledger/repositories/imports.py:234` | 补齐字段/透传；统一 NOT_FOUND |
| P2-11 | `dedupe_key` 口径含 `cash_balance`、不含 summary，与 PRD A27/Architecture §9.4 不一致；跨文件 occurrence 复位 | `parsers/dedupe.py:35-80`、`parsers/huatai.py:346-369` | 按业务指纹重建键；occurrence 用 DB 计数；部分唯一索引 |
| P2-12 | 撤销只删 version=1 行；restore 不校验 revoked 批次；重复 commit 产生孤儿 pending 批次/job | `application/imports.py:92-95,109-113,231-234,265-271`、`routers/trades.py:286-310` | 短路路径也 finish；失败/取消同步批次状态；补血缘 |
| P2-13 | 对账 `checked=0` 仍 ok；`line_no` 每文件重置导致多批同日交错假断链 | `application/reconcile.py:87-123`、`repositories/trades.py:341-357` | checked=0→degraded；排序加 batch/created_at |
| P2-14 | 夹具/seed 非确定性：`generate_huatai_bill` 每次写出的 xlsx 字节不同（zip 时间戳），`seed-demo/demo-p1` 重写 tracked 文件；`quotes.json` 只写不读（replay 适配器不存在） | `cli.py:535-552,738-744`、`fixtures.py` | 固定生成字节或改 CSV；demo 只读；实现 replay 端口或从一期移除 |
| P2-15 | 本地编排/文档：`make dev` 不启 DB；`make db-reset` 固定 compose 5433 与 `.env` 5432 不一致；`make sdk` 依赖 `pnpm dlx` 联网且未锁版本 | `Makefile:79-83,63-67`、`cli.py:371-410` | dev 加 db-up；db-reset 参数化端口；openapi-typescript 进 devDependencies+lock |
| P2-16 | 股东账号用无盐 MD5 "脱敏"，API 返回 32 位 hex（非 PRD 要求的末 4 位），长度有限的账号可被暴力反推 | `parsers/huatai.py:325-332`、`test_parser_huatai.py:364-392` | 用服务端密钥 HMAC-SHA256（fail-closed），对外 `****1234`；迁移重算 |

### 5.4 P3（6 项，择机）

| ID | 问题 | 位置 |
|---|---|---|
| P3-1 | 退避上限 60s（文档 10min）+无 jitter；priority 升序（文档 DESC）；claim 把任意 kind 的 stage 写成 `parsing` | `platform/jobs.py:286,193,208` |
| P3-2 | core 暴露 `to_float()`；`price_of(qty_of/ratio_of)` 静默接受 float；`mul_ratio` docstring 说银行家舍入实为 HALF_UP | `core/values.py:31-48,72-74,193-195` |
| P3-3 | 游标 base64(JSON) 无签名、两份同构实现（platform 与 ledger） | `platform/cursor.py:16-30`、`ledger/repositories/trades.py:50-68` |
| P3-4 | PAT 前缀 = `pat_`+前 5 位 secret（列表回显，泄露秘密字符） | `platform/security.py:102-106` |
| P3-5 | 审计 `redact` 不递归 list、键名精确匹配；登出只清本地不调 API-004；`auth.me` 路径写错（未调用） | `platform/audit.py:25-35`、`router.tsx:27-36`、`http.ts:512-514` |
| P3-6 | `StrictModel` 缺 `populate_by_name`；`PreviewColumn.confidence: float`；`ErrorCatalogOut.items: list[dict]` 丢结构 | `schemas.py:16-17,111,417-419` |

---

## 6. 契约与文档漂移清单

| # | 权威契约 | 仓库现状 | 处置 |
|---|---|---|---|
| 1 | API-001 password **12~128**、WEAK_PASSWORD 文案"至少 12 位" | 代码 8~128（`schemas.py:23`、`security.py:23`）；快照 12 | 恢复 12 或走契约变更流程 |
| 2 | 一期端点"24 个"（DEV-PLAN §2/§8/附录 C） | 附录 A.1 实为 **30 个**；api-check 显示一期未实现 0（30/30 已实现） | 以 A.1 为单一事实源统一文案 |
| 3 | 每 operation ≥1 个 2xx 示例、错误 problem+json、4 个 x-*（API-Catalog §7.4） | 快照 `examples`=0、`problem+json`=0、无 Error schema、无 x-* | 注入并加 api-check 硬校验 |
| 4 | operationId snake_case + `operationId.lock.json`（§7.3） | 全部 camelCase；无 lock 文件 | 趁 SDK 未大量使用前重命名+lock |
| 5 | §3 错误码 47 | CATALOG 49（多 `IMPORT_PREVIEW_NOT_FOUND`/`JOB_LEASE_EXPIRED`） | 补录或改回 NOT_FOUND |
| 6 | 跨用户 404 + `NOT_FOUND` | preview 跨用户返回 `IMPORT_PREVIEW_NOT_FOUND` | 统一 NOT_FOUND |
| 7 | 401 必须 `WWW-Authenticate: Bearer realm="api"` | 全仓无该头 | 中间件统一注入 |
| 8 | RFC 7807 含 `instance` | 缺 | 补 |
| 9 | SSE `Last-Event-ID` 为头、`event: reconnect`、终态 `result_ref` | query 参数、无 reconnect、result_ref 恒空 | 修正 |
| 10 | `/readyz` 字段 `status:"ok", db, schema_version, draining, worker_heartbeat_age_s`；迁移滞后 503 | 返回 `{status:"ready",checks:{database,schema}}`；503 逻辑存在但无用例 | 重塑并补 503 用例 |
| 11 | API-073 含 `git_sha`；API-075 断言 info.version==/version、paths==§1 | 无 git_sha；`/version.api_version="v1"` vs info.version="0.1.0"；spec 多 `/api/v1/version` | 统一版本口径；登记/删除多余路径；补 API-075 用例 |
| 12 | API-010 broker/mode；API-011 range/mapping；API-018/020 affected_metrics/recompute_job_id；API-062/063 result_ref | 未实现或恒空 | 补齐或明确降级并改文档 |
| 13 | 游标绑定 user/filter/24h；ETag `W/"version"` | base64 JSON 无绑定；强 ETag | 加 HMAC+指纹+TTL |
| 14 | Testing.md §4.3 RLS 8 类场景、§5.1 每端点 ≥3 用例+marker、§6 tools/ci 门禁 | RLS 4 条；2 个巨型测试文件；无 `@pytest.mark.api("API-NNN")`；`tools/ci/` 不存在 | 二期前补齐 |
| 15 | 文档：口令 `demo1234`、`fixtures/bills/huatai_2024.xlsx`"328 行"、eslint、TanStack Query、`make dev` 起 DB | 实际口令 `Demo!Passw0rd2026`；无 `fixtures/bills/`（实际 `fixtures/market/bill_huatai.xlsx` 122 行/120 解析/2 隔离）；无 eslint；无 TanStack；`make dev` 只起 api/worker/web | 同步文档或补实现 |

---

## 7. 安全与多租户专项

### 7.1 RLS 覆盖（PG catalog 实测）

| 表 | 含 user_id | RLS | 策略数 |
|---|---|---|---|
| ledger.trade / trade_history / import_batch / import_preview / quarantine_row / reconciliation / summary_rule | ✅ | ✅ | 1（summary_rule 放行 user_id IS NULL） |
| platform.user / refresh_token / api_token / job / job_event / idempotency_key / outbox_event / user_setting / audit_log / login_attempt / rate_limit_bucket / backup_run / config_release | 部分 ✅ | ❌ | 0 |

- 结果：**第二道防线只覆盖 ledger**；`platform` 表（job/api_token/idempotency/user_setting/refresh_token/outbox_event）依赖应用层 `WHERE user_id`。现有跨租户用例（API 层 404）与 RLS 测试都通过，是因为应用层过滤正确，而非 RLS 兜底。
- 另：`summary_rule` 的 `WITH CHECK` 同时放行 `user_id IS NULL`，`stock_app` 可写全局系统规则（P1-6）。
- 迁移/测试注释写"platform.user RLS 也开着"与事实不符，需纠正文档或补策略。

### 7.2 认证/授权

- **admin PAT 越权（P0-3）**：`_resolve_pat` 硬编码 `Role.USER`；JWT 403 / PAT 200（实测）。
- JWT：HS256 + exp/iat + jti，但无 `aud/iss` 且不校验（P2-6）；JWT 中 role 有 15min 缓存窗口。
- refresh：轮换 + 复用检测 + 整族撤销（实现正确，用例通过）；但登出后 refresh 返回 `TOKEN_REUSED` 与 Catalog `TOKEN_REVOKED` 不一致。
- PAT：Argon2id 无关（仅密码），PAT 哈希存储、明文只返回一次（实现正确）；前缀泄露 5 位 secret（P3-4）。
- 密码：Argon2id 参数合理；但最短 8 位与契约 12 位冲突（P1-12）。
- 上传：认证用户可上传任意大小文件并全量读入内存（P1-17）。
- 传输凭据：额外支持 `X-API-Key` 头，未写入 Catalog；测试大量使用它而 `Authorization: Bearer pat_...` 路径缺少用例（P2，C 的 F16）。
- PII：股东账号用无盐 MD5 当"脱敏"（`parsers/huatai.py:325-332`），可被暴力反推，且 API 返回 32 位 hex 非"末 4 位"（P2-16，建议 HMAC + `****1234`）。

### 7.3 队列/任务安全

- 租约/心跳缺失（P1-1）、pending 取消不终态（P1-2）、单飞异参静默复用（P1-4）是当前队列三大语义缺陷；多副本 worker 上线前必须修。
- `reap_expired_leases` 无行锁，两个 worker 可同时回收同一 job（P1-1 修法）。
- worker 对未捕获异常无收口（reclassify 等 handler 抛错会打挂消费循环，C 的 F12）。

---

## 8. 测试与门禁有效性评估

### 8.1 `make verify-p1` 与 DoD 的差距

| DoD 要求 | 实际 | 差距 |
|---|---|---|
| `make lint` 零错误（后端 ruff+mypy strict；前端 eslint+tsc） | `make lint` 通过；前端 lint 实为 tsc | 无 eslint；mypy 不在 lint 也不在 verify-p1 |
| 本期末端点 API 验收 100%（幂等/越权/错误码） | 90 + 18 用例可跑且绿（干净重跑） | **不在 verify-p1**；API-075 零用例；无端点覆盖统计；无 marker ID |
| `make verify-pN` 一条命令全绿 + 勾选清单 | exit 0 | 仅 4 步；`108 deselected`；无清单 |
| 前端页面可点开可操作 | 登录/流水/导入可用 | 撤销不可达；无批次/隔离区 UI；SSE 无兜底 |
| 契约快照更新/示例/错误码/TS client | 快照已提交 | **已漂移**；0 示例/0 problem+json；SDK 未接入 |
| docs 同步 | — | 多处漂移（§6） |

### 8.2 测试质量

- 90 条 API 用例覆盖 29/30 个一期 ID 的 happy path，但：API-075 无用例；API-072/073/076/062/063 断言弱；越权矩阵只覆盖 10 个资源端点 + 3 个 admin 403 + 1 个未认证 401，且只断言状态码、无副作用断言；写端点无"越权后副作用未发生"验证。
- 测试基础设施：`backend/tests/conftest.py:97-101` 的 autouse `_clean_db` 每用例 TRUNCATE 全表，导致**任何** pytest（含 unit）都连库；并发跑两套测试会 `DeadlockDetectedError`（本次首跑即出现 1 error，复跑 90 passed）。`make test` 帮助文案称"无外部依赖"不成立。
- 无 `tools/ci/`（端点覆盖/越权覆盖/breaking 检查）、无 `@pytest.mark.api("API-NNN")`、无 operationId lock、无 Schemathesis、无变异测试；core 只有行覆盖（`--cov-fail-under=100`，无 `--cov-branch`），platform 覆盖率目标（92/88）未落地（`backend/tests/unit/platform/` 不存在）。
- 前端仅 6 条测试（`Problem.test.tsx` 2 + `http.test.ts` 4），无页面/路由守卫/并发 401 用例，无 coverage；`pnpm -r lint` 只跑 tsc。
- 门禁脚本对环境敏感：本报告首跑前端 test 因 login shell 解析到旧 node 报 `??=` 失败，固定 node 24 后通过 → 建议加 `engines.node`/`.nvmrc` 并在门禁脚本固定 node。

---

## 9. 修复优先级与建议排期

### 第一批（阻断验收，约 3–4 人日）

1. **P0-1 金额口径归一**（0.5–1d）：解析层区分 `cash_flow/amount` + 对账统一 + 真实账单回归 fixture。
2. **P0-2 `replace_range` 数据丢失**（0.5d）：去重过滤软删 + partial unique index + 使用请求 range + 血缘；补回归用例。
3. **P0-3 admin PAT**（0.25d）：PAT 回查 role + 越权用例。
4. **P0-4 撤销/批次 UI**（0.5–1d）：批次列表+详情+隔离区+撤销，接现有 client。
5. **P0-5 门禁与契约**（0.5–1d）：`verify-p1` 扩为完整套件+勾选清单；`api-snapshot --check` 纳入；修密码策略并重新生成快照；api-check 增加示例/problem+json/x-* 校验 + 元测试。

### 第二批（安全/数据 P1，约 2–3 人日）

- P1-5/P1-6 RLS：platform 租户表补策略、`summary_rule` 拆 WITH CHECK、RLS 测试补 8 类场景。
- P1-1/P1-2/P1-3/P1-4 队列与去重：心跳、cancel 终态、软删去重键、单飞异参 409。
- P1-7/P1-8/P1-9/P1-10 导入链路：分类接入 commit、规则词汇、损坏文件/Excel 日期、seed/demo 契约（含合法邮箱与文档口令）。
- P1-11/P1-12：trades 过滤参数、密码 12 位。
- P1-13/P1-14/P1-15：token 内存、SDK 接入、eslint。
- P1-16/P1-17：SSE 轮询兜底/批次 UI 收尾、上传防护。

### 第三批（P2/P3，可与二期并行，进入二期准入清单）

- RLS 归还 RESET、幂等 TTL、Clock 收口、游标签名/TTL、JWT aud/iss、401 头/instance/request_id、SSE 头、readyz/version 契约、导入 preview/commit 字段、dedupe_key 口径、撤销血缘、对账 checked=0、夹具确定性、本地编排与 sdk 锁版本；P3 视时间处理。

---

## 10. 复验清单（修完后按此重跑）

| # | 命令/操作 | 通过标准 |
|---|---|---|
| 1 | `make lint` / `make typecheck` | 0 错误；前端 lint 含 eslint |
| 2 | `make verify-p1` | 输出包含 ruff/mypy/import-linter/core 覆盖率/API 90+/isolation 18+/RLS/契约校验/前端；**逐 ID 勾选清单**；exit 0 |
| 3 | `make api-snapshot && make api-check` | `api-snapshot --check` exit 0；api-check 校验示例/problem+json/x-*；git diff 为空 |
| 4 | `make test-api` / `make test-isolation` / `make test-rls` | 全绿；API-075 有用例；越权矩阵覆盖全部受保护端点且含副作用断言 |
| 5 | 真实 HTTP E2E（`var/review-p1/live_e2e.py`） | 23/23 通过；seed 账号可登录；`make demo-p1 --run` 完整走通 |
| 6 | 真实账单对账 | 导入 `华泰账单_mock.xlsx` → `GET /reconciliation` `status=ok, breaks=0` |
| 7 | replace_range | 导入 → 同文件 replace_range → `inserted==120` 且 `/trades` 行数不变 |
| 8 | 撤销后重导 | 导入 → revoke → 同文件重导 → `inserted==120`、行数恢复 |
| 9 | 取消任务 | pending 取消后 `GET /jobs/{id}` = `cancelled`；不占并发配额 |
| 10 | admin PAT | admin JWT 建 PAT → PAT 读 `/trades` = 403 `ADMIN_CANNOT_READ_TRADES` |
| 11 | RLS 覆盖 | `information_schema` 枚举租户表，策略 100%；未设 `app.user_id` 读 platform 租户表 0 行 |
| 12 | 前端走查 §2.1 | 浏览器完成 1–5 步，含"撤销本次导入"；token 不在 localStorage |
| 13 | 文档 | 一期 30 端点、口令、夹具路径、eslint/TanStack/门禁描述全部一致 |
| 14 | 工作区干净 | `git status` 无 `fixtures/` 变更（seed/demo 不再重写 tracked 文件） |

---

## 附录 A：一期 30 端点验收矩阵（摘要）

图例：✅ 有用例且断言关键值；⚠️ 有但弱/合并/缺关键断言；❌ 缺失。完整矩阵见 `var/review-p1/api.md` §2 与 `frontend-qa.md` §3。

| 端点组 | ID | 实现 | 验收状态 | 主要缺口 |
|---|---|---|---|---|
| 认证 | API-001~005 | ✅ | ✅/⚠️ | API-004 登出后错误码与 Catalog 不一致；401 无 WWW-Authenticate |
| PAT | API-007~009 | ✅ | ✅ | PAT Bearer 路径缺用例；admin PAT 越权（P0-3） |
| 导入 | API-010~015 | ✅ | ✅/⚠️ | API-011 缺 410/409 in-progress/Location 断言；API-015 未断言列表行数回退 |
| 流水 | API-017~021、080 | ✅ | ✅/⚠️ | API-017 缺跨用户列表、sort 生效、batch_id 过滤；API-019 缺冲突码 |
| 对账 | API-026 | ✅ | ⚠️ | 真账单假断链（P0-1）；缺跨用户专项用例 |
| 任务 | API-061~064 | ✅ | ⚠️ | API-062 result_ref 恒空；API-063 缺单调/续传/终态帧；API-064 取消终态缺失（P1-2） |
| 运维 | API-071~073、075、076 | ✅ | ⚠️/❌ | API-075 零用例；072 缺 503；073 缺 git_sha；076 缺每码字段 |

> 实现 30/30，但"验收 100%"不成立：API-075 无任何用例；多条关键断言缺失；门禁不统计端点覆盖。

## 附录 B：证据与复现路径

| 证据 | 路径 |
|---|---|
| 门禁原始日志（26 步） | `var/review-p1/gates/*.log`，汇总 `summary.tsv` |
| 真实 HTTP E2E 脚本与日志 | `var/review-p1/live_e2e.py`、`live-e2e.log` |
| 四路审查分片 | `var/review-p1/{foundation,ledger,api,frontend-qa}.md` |
| 契约漂移复现 | `.venv/bin/python -m stock_api.cli api-snapshot --check`（exit 1） |
| 真实账单对账复现 | 导入 `backend/tests/api/华泰账单_mock.xlsx` → `GET /api/v1/reconciliation?from=2021-10-29&to=2022-06-07` → `breaks=156` |
| replace_range 复现 | 导入 → 同文件 `mode=replace_range` → `counts.inserted=0`、`/trades` 120→0 |
| 撤销后重导复现 | 导入 → revoke → 同文件再导 → `inserted=0` |
| 取消 pending 复现 | 停 worker → commit → cancel → 轮询仍 `pending` |
| admin PAT 复现 | admin JWT 建 PAT → PAT `GET /trades` 200（JWT 为 403） |
| 迁移幂等（干净库） | 临时库 `stock_p1_review` upgrade×2 + `alembic check` 通过（已清理） |

## 附录 C：审查局限

- 未做浏览器级点击走查与 Playwright；前端判定基于代码审查 + tsc/vitest + 手工接口验证。
- 未实跑 Docker `db-up/db-reset`；迁移幂等用宿主机 PostgreSQL 18 临时库验证。
- 未实跑 `make sdk`（依赖联网 `pnpm dlx`）；按其实现与生成物使用情况判定。
- 真实账单仅 1 份；Excel 原生日期/损坏文件等结论来自纯函数复现，未逐一走 HTTP。
- 未做 10 年/5 万行数据量、并发压测、PITR/备份演练（属二/三期与交付）。
- `seed/demo` 写入 `mode="append"` 导致 `GET /imports` 500 的结论为"模型级 + DB 状态"证据（`BatchOut` 拒绝 + DB 内 2 条 append 批次）；因 seed 账号邮箱不合法无法登录，未直接打端点复现。

## 附录 D：值得肯定/可复用资产

- **标度整数**：Money/Price/Qty/Ratio 全 int 域、边界定点字符串、float 防御（core 单测 100%）。
- **错误模型**：统一 `ApiError` + RFC 7807 六字段（含 user_message/what_to_do/retryable/request_id/errors），无 HTTPException 手动错误。
- **多租户**：`TenantContext` 只从凭证构造、仓储显式 `user_id`、ledger RLS fail-closed、跨租户 404（实测）。
- **认证**：Argon2id、refresh 轮换+复用检测+整族撤销、PAT 哈希/明文一次/撤销、登录失败限速。
- **导入/流水**：预览不落库、202+Location、幂等三态（同键同体重放实测）、ETag/304、游标分页、PATCH 版本冲突 409、软删/恢复/history。
- **队列/SSE**：SKIP LOCKED + 单飞唯一索引 + reaper + 事件流（真实 SSE 实测）。
- **工程质量**：分层导入铁律（import-linter 5/5）、mypy strict 69 文件 0 error、ruff 全绿、契约快照/客户端生成链路骨架已搭。

---

> 报告生成：Lead（整合四路评审 + 独立实测）｜所有实测证据可复现于 `var/review-p1/`｜基线 `b61d441`。
