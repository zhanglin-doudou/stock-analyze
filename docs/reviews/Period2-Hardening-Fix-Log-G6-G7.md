# 二期准入 G6 + G7 交付记录：SDK/覆盖门禁与平台 RLS

> 日期：2026-10-21 ｜ 前置：[G5/G1 交付记录](Period2-Hardening-Fix-Log-G5-G1.md) ｜ 准入检查单：[Period2-Entry-Checklist.md](Period2-Entry-Checklist.md)
> 范围：G6（生成式 SDK 接入 + 端点/越权门禁）、G7（platform 租户表 RLS + review schema 覆盖门禁）。本次完成后 **G1~G7 全部关闭**。

## 0. 结论

| Gate | 状态 | 关键证据 |
|---|---|---|
| **G6a** 前端生成 SDK 接入 | ✅ | generated `schema.d.ts` 成为唯一类型源（`contract.ts` 统一 re-export + 编译期断言）；`http.ts` 手写 DTO 移除（744→518 行）；新增 7 条契约测试；前端 20 tests / lint / typecheck / build 全绿 |
| **G6b** 端点/越权门禁 | ✅ | `gate_endpoint_coverage.py`：实现 30/30、测试引用 30/30；`gate_tenant_coverage.py`：受保护 23/23、跨租户资源 12/12、401 23/23、列表隔离 4/4；负向注入可失败 |
| **G7** platform RLS + review 覆盖 | ✅ | `0002` 迁移对 job/idempotency_key/user_setting/outbox_event 启用 RLS；worker 双池（app 数据面 + owner 队列控制面）；`gate_rls_coverage.py` 11/11（platform 4/4、ledger 7/7、review 0/0）；RLS 测试 20 条 |
| 集成 | ✅ | `verify-p1` **18/18**（新增 3 个 gate）；API 110 passed、isolation 42、RLS 6、unit 157、contract 20、前端 20 tests；主库迁移 0002 + seed 幂等 + 运行时 seed/demo/login/job 全通 |

## 1. G6a：前端接入生成 SDK

- 新增 `frontend/packages/api-client/src/contract.ts`：
  - `import { components, paths, operations } from "./api/generated/schema"`；
  - 导出 `ApiSchemas/ApiPaths/ApiOperations` 与 generated→前端 DTO 别名（Error→Problem、TradeOut→Trade、BatchOut→ImportBatch、PreviewOut→Preview、JobOut→Job、RevokeOut→RevokeResult 等）；
  - 14 组编译期断言（字段增删/改名在 `tsc` 阶段报错）。
- `http.ts`：手写 DTO 全部移除；请求体也取契约（ImportCommitIn/PatIn 等）；修 `auth.me` 路径 `/account/me → /auth/me`；`imports.commit` 返回 202 `JobAccepted`；`trades.patch` 按 `TradeMutationOut`。
- 新增 `contract.test.ts` 7 条：一期 30 端点 method+path（API-075 自引用例外）、50 个 schema 均已生成、Error 六字段、http.ts 核心字段、`/auth/me` 回归守卫、“不再手写 DTO”守卫。
- 抗漂移实证：删 generated `TradeOut.amount` → tsc 报错；改名 `BatchOut` → 契约测试失败；均已恢复。
- 残余：SSE 事件 data 仍手写（OpenAPI 未建模）；`node-shims.d.ts` 为测试读快照的最小 node:fs 声明，后续引入 @types/node 可删除。

## 2. G6b：覆盖门禁

- `scripts/ci/gate_endpoint_coverage.py`：DEV-PLAN A.1「一」30 个 API-ID × API-Catalog §1 × 运行时 OpenAPI（API-075 自引用特判）+ `tests/api`/`tests/isolation` 引用扫描；缺失 exit 1。
- `scripts/ci/gate_tenant_coverage.py`：运行时 `x-required-scope` → 23 个受保护 operation；矩阵/EXEMPT 校验（跨租户资源、401、列表隔离、admin 403）；缺失 exit 1。
- 扩展 `backend/tests/isolation/test_isolation_matrix.py`：20→42 用例（新增 PATCH trades、DELETE token、4 个列表隔离、23 个 401 参数化）。
- 补齐 API-075（openapi 200/version/paths/富化字段）与 API-072（迁移滞后 → readyz 503 SCHEMA_VERSION_MISMATCH，finally 恢复）；API-071/073/076 改名可扫描。
- 负向自检：注入 API-999 / 伪造受保护端点 → 两门禁均 exit 1。

## 3. G7：platform RLS + review 门禁

- 新迁移 `backend/migrations/versions/0002_platform_tenant_rls.py`：
  - `platform.job / idempotency_key / user_setting / outbox_event` 启用 RLS + `tenant_isolation`（USING/WITH CHECK = `app.user_id`，未设置 fail-closed）；
  - 认证/系统表（user/refresh_token/api_token/login_attempt/rate_limit_bucket/config_release/schema_version/backup_run/audit_log/job_event）保持不启用，原因写迁移 docstring + `docs/DATABASE.md §2.1`。
- 运行时双轨：
  - `WorkerRunner`：`db`（app，handler/业务数据）+ `queue_db`（owner，仅 claim/reap/heartbeat）；`close()` 释放两池；
  - API jobs 端点全部 `session(user_id=..., begin=True)`；`auth.register` 建默认 user_setting 前 `set_tenant`；`/auth/me` 租户会话；
  - idempotency/outbox 路径核查均在租户事务内。
- `scripts/ci/gate_rls_coverage.py`：owner + pg_catalog 枚举带 `user_id` 表，要求 RLS + tenant 策略；认证表 ALLOWLIST（表名+原因）；review 0 表输出 0/0，P2-02 建表自动纳入。
- `backend/tests/integration/test_platform_rls.py` 20 条：R1 隔离 / R2 未设变量 0 行 / R3 他人谓词 0 / R4 跨租户 UPDATE/DELETE 0 / R5 INSERT WITH CHECK 拒绝 / R6 逐表策略 / R7 连接池复用无残留 / R8 app 禁 DDL + admin 直连 0 行。
- 迁移幂等：现有库与新库 upgrade ×2 + check 无 drift；downgrade/upgrade 循环通过。
- 测试适配：worker `_drain_worker`、unit/platform conftest 随双池调整；其余 API 套件全绿。

## 4. 接线与验证

- `Makefile`：新增 `gate-endpoint` / `gate-tenant` / `gate-rls`。
- `verify-p1`：4 步 → **18 步**，新增三个 gate 步骤（本报告实测 18/18，打印勾选清单）。
- 主库：迁移 `0002` 成功（幂等）、`seed-tenants` 幂等、基线仍 **3 用户 / 238 流水 / 2 批次**；`gate_rls_coverage.py` 11/11。
- 运行时（独立 0002 库）：seed/demo 幂等 + `demo-p1 --run`（注册→登录→预览→提交→worker job succeeded→流水）+ 三账号登录 200，验证 worker owner 控制面 + handler app 数据面组合正确。
- 文档：`docs/DATABASE.md` 更新 RLS 范围/门禁/排障；worker AGENTS 规则 5 改为双池；DEV-PLAN 一期端点数 24→30；Period2 检查单 G6/G7 置 ✅。

## 5. 风险/遗留

1. `gate_endpoint_coverage` 依赖 DEV-PLAN A.1 / API-Catalog §1 表格格式；修表需同步解析（fail-loud）。
2. 5 个端点的 body/header 校验先于认证依赖：裸请求可能 422 而非 401；401 矩阵用最小合法载荷命中认证层。若要“未认证一律 401”需后端前置认证依赖（未做）。
3. `audit_log/job_event` 跨租户不可见仍依赖 API 层过滤（设计未启 RLS）；P2-02 的 review 表建表必须同批 RLS，否则 `gate_rls_coverage` 失败。
4. SSE 事件 data、ListTradesQuery 等非响应 DTO 仍手写；后续可补 OpenAPI schema 再纳入。
5. `node-shims.d.ts` 是测试读取快照的最小声明，引入 @types/node 后删除。
