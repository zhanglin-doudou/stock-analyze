# 一期 P0 修复记录：P0-1 / P0-4 / P0-5

> 日期：2026-10-21 ｜ 基线：`6bfc4f4`（P0-2/P0-3 修复已提交）+ 本次工作区修改 ｜ 关联报告：[Period1-CodeReview-and-Acceptance.md](Period1-CodeReview-and-Acceptance.md) ｜ 前序修复：[Period1-Fix-Log-P0-2-P0-3.md](Period1-Fix-Log-P0-2-P0-3.md)
> 范围：修复审查报告中剩余 3 项 P0（真实账单对账假断链、前端撤销入口缺失、验收门禁/契约证据失效）。**未提交 git**。

## 0. 结论

| P0 | 修复前 | 修复后 | 证据 |
|---|---|---|---|
| **P0-1** 真实账对账假断链 | 真实账单 `checked=175, breaks=156, status=blocked` | 统一「amount_units = 带符号发生金额（净现金流，含费用）」；对账直接累加 → `checked=175, breaks=0, status=ok` | 真实 HTTP 复验 + 单测/API 回归 |
| **P0-4** 撤销入口缺失 | 浏览器无法执行 §2.1 第 4 步「撤销本次导入」 | 新增 `/imports` 导入记录页（批次列表/详情/隔离区/撤销+二次确认），导入页展示批次结果并可撤销；导航入口齐 | vitest 13 passed + `vite build` 通过 |
| **P0-5** 验收证据失效 | `verify-p1` 仅 4 步（`108 deselected`）、契约快照漂移（password 12→8）、0 示例/0 problem+json | `verify-p1` 扩为 **15 步**并打印勾选清单；密码恢复 12；OpenAPI 契约元数据/示例/problem+json 全量补齐；快照与 SDK 重生成；api-check 可阻断违规 | `verify-p1` 15/15 exit 0；真实 HTTP 运行时 openapi == 快照 |

**最终门禁**：`make verify-p1`（等价命令）**15/15 全绿**；真实 HTTP 复验 **P0-1/P0-5 14/14**、P0-2/P0-3 回归 **14/14**、原 §2.1 主链路 **23/23**。

## 1. P0-1：真实账金额口径归一（对账 0 断链）

### 1.1 口径定义（新）

`Trade.amount_units` 一律表示**带符号的发生金额/净现金流**：买入为负、卖出为正、已含费用；非交易转账按资金方向（转入=卖出方向正、转出=买入方向负）；unknown 且无资金流=0。
对账：`expected = prev.cash_balance + current.amount_units`（不再按 side 反号、不再二次扣费）。

### 1.2 改动

- `backend/packages/ledger/src/stock_ledger/parsers/columns.py`
  - 金额列表头语义判定（「发生金额/发生额」= 已带符号净额；「成交金额」= 毛额）。
- `backend/packages/ledger/src/stock_ledger/parsers/huatai.py`
  - 发生金额：直接采用（不扣费、不反号）；
  - 成交金额毛额：`buy = -(gross+fees)`、`sell = gross-fees`；无金额列时先 `qty*price` 推导毛额再换算；
  - 毛额列出现负数时视为已带符号净额（防御）；unknown + 毛额非零 → 0 并记 `assumptions.amount_unsigned_rows`。
- `backend/packages/ledger/src/stock_ledger/application/reconcile.py`
  - 删除 `_signed_delta_units` 的反号/扣费逻辑，直接累加带符号金额；EPS/断链定位/degraded 保留。
- 测试：`test_parser_huatai.py`（按新口径修正 + 新增 5 条）、新增 `test_reconcile_amounts.py`（合成夹具纯函数复算）、`test_imports_trades_jobs.py` 新增真实账单对账回归（API 级）。

### 1.3 实测

| 数据集 | 旧公式 | 新公式 |
|---|---|---|
| 真实账单 `华泰账单_mock.xlsx`（177 行，`from=2021-10-29&to=2022-06-07`） | checked=175, breaks=**156**, blocked | checked=**175**, breaks=**0**, **ok** |
| 合成夹具 `bill_huatai.xlsx`（120 行） | 0 断链 | checked=119, breaks=**0** |

真实 HTTP 复验同时断言：买入 `amount="-60.00"`、卖出 `amount="680.00"`（发生金额口径）；真实账单导入 `inserted=177`。

### 1.4 风险/迁移

- `dedupe_key` 含 amount，口径变化后 hash 变化。**已存在的开发/演示库中旧口径数据需 revoke 重导或清库重 seed**；生产（未发布）需一次性迁移。当前开发库未清库，旧 seed 数据可能仍按旧口径存在（新导入不受影响）。
- 同时含「成交金额 + 发生金额」两列的账单当前取先出现者；两个现有夹具均为单列，风险已记录。
- 下游（二期回合/净值）不得再对 `amount_units` 反号或扣费；二期实现时需以本口径为准。

## 2. P0-4：前端撤销入口（§2.1 第 4 步可完成）

### 2.1 改动

- 新增 `frontend/apps/web/src/pages/Imports.tsx`：批次列表（游标分页/状态徽标/counts 摘要）+ 详情（counts/warnings/假设与范围/隔离区）+ `can_revoke` 撤销（行内二次确认）+ `?batch=<id>` 深链。
- 新增 `frontend/apps/web/src/ui/RevokeBatch.tsx`：撤销按钮（失败走 `ProblemView`）。
- `pages/Import.tsx`：任务成功后显示批次号/插入/隔离/跳过 + 「查看批次」「撤销本次导入」「去交易流水核对」。
- `router.tsx`：新增 `/imports`（RequireAuth 下）+ 导航「导入记录」。
- `ui/QuarantineList.tsx`：`line_no` 支持可空；`styles.css` 增补徽标/结果区样式。
- `packages/api-client/src/http.ts`：修正 `imports.revoke` 返回类型为 `RevokeResult`、补齐 `BatchOut` 字段（未手改 generated schema）。
- 未引入新依赖（项目仍无 TanStack Query，沿用 `useState/useEffect`）；错误全部走 `ProblemView`；无裸 fetch/金额算术。

### 2.2 验证

- `pnpm -r lint` / `typecheck` / `test` / `build` 全 exit 0；**vitest 13 passed**（原 6 + 新增 7：列表渲染、撤销调用+刷新、can_revoke=false 不显示、隔离区空态、导入页结果/撤销等）。
- 真实 HTTP 侧验证 UI 依赖的 API 全链路：`GET /imports` 列表含批次 → `GET /imports/{id}` `can_revoke=true` → `POST revoke` 200 → `/trades` **177→0**。
- 生产构建产物包含「导入记录」。

### 2.3 未覆盖

- 未装 Playwright，**未做浏览器级 E2E**；建议在有前后端的 5173 环境手工走查 §2.1 第 4 步。
- 批次列表无过滤/搜索（后端仅 limit/cursor）；批次状态不自动轮询，需重进刷新。

## 3. P0-5：验收门禁与契约证据

### 3.1 `verify-p1` 扩为 15 步 + 勾选清单

`backend/services/api/src/stock_api/cli.py`：`VERIFY_STEPS` 从 4 步扩为 15 步，任一步失败即中止并打印已通过/失败/未执行清单：

1. ruff check 2. ruff format 3. mypy strict 4. import-linter L1~L5 5. pytest 单测（不含 api/integration）6. core 覆盖率 100% 7. **pytest 一期 API 验收** 8. **pytest 越权矩阵** 9. **pytest RLS 直连** 10. pytest 架构约束 11. **契约快照无漂移** 12. **api-check（覆盖/错误码/示例/x-*）** 13. 前端 tsc 14. 前端 vitest 15. 前端生产构建。

实测：**15/15 通过**，输出勾选清单；API **99 passed**、单测 113 passed、越权 18、RLS 4、架构 1、contract 20、前端 13 tests。

### 3.2 契约漂移：密码恢复 12 + 快照/SDK 重生成

- `backend/packages/platform/src/stock_platform/security.py`：`MIN_PASSWORD_LENGTH=12`；`schemas.py`：`RegisterIn.password min_length=12`；前端 `Register.tsx` 同步 `placeholder/minLength=12`。
- 新增用例锁定契约：8/11 位高复杂度口令 → 422；12 位弱口令 → `WEAK_PASSWORD`。
- `contracts/openapi.json` 重生成（223,892 B）：`example` 134 处、`application/problem+json` 107 处、`x-error-codes` 32 个 operation 全覆盖；`components.schemas.Error` 11 字段。
- SDK 重生成：`openapi-typescript` **7.4.4 固定进 `frontend/packages/api-client` devDependencies**（原 `pnpm dlx` 无锁版本、沙箱/离线不可复现）；`cli sdk` 改用 `pnpm --filter @sa/api-client exec`，连续两次生成 SHA256 一致；`make sdk` 恢复可用。
- `api-snapshot --check` exit 0；真实 HTTP `GET /api/v1/openapi.json` 与快照 JSON **完全相等**。

### 3.3 契约元数据（API-Catalog §7.4）

新增 `backend/services/api/src/stock_api/openapi_meta.py`（静态 `OPERATION_META`，从 Catalog §1/§2 一次性生成，运行时不读 md）：

- `app.openapi_schema = apply_contract_metadata(app.openapi())`（`/docs`、`GET /api/v1/openapi.json`、`cli.openapi_spec()` 同源、确定性缓存）。
- 32/32 operation：`x-required-scope`（public 9/read 12/write 11）、`x-idempotent`（true 11）、`x-async`（仅 `commitImport`）、`x-error-codes`；
- 2xx 示例 32/32（26 JSON + 2 文本 SSE/Prometheus + 4 个 204 No Content 豁免）；
- 错误响应 problem+json 覆盖 12 个 status（400/401/403/404/409/410/413/415/422/429/500/503），引用 `Error` schema 并带示例。
- `validate_contract(spec) -> list[str]`：校验 Error schema、operationId、x-*、2xx 示例、problem+json 引用/示例、x-error-codes 与 status 双向一致；`api-check` 接入后违规即失败。
- 新增 `backend/tests/contract/test_openapi_contract.py`（20 条无 DB 测试）：基准 + 元测试（删一个 example / problem+json / x-* → validate 必非空）。
- `api-check`：[a] 一期缺失端点从“告警”改为**失败**；[d] 改为必须 problem+json；[e] 输出契约元数据违规数量。

### 3.4 验证

```
make verify-p1（15 步）→ ✅ 全绿 + 勾选清单
真实 HTTP：openapi == contracts/openapi.json；x-* 32/32；2xx 示例 32/32；problem+json 32/32
```

## 4. 全量验证汇总

| 项 | 结果 |
|---|---|
| `verify-p1`（15 步） | ✅ 15/15，exit 0，打印勾选清单 |
| ruff / format / mypy strict | ✅ 88 files formatted / 70 source files 0 error |
| 单测 / core 覆盖 | 113 passed / 31 passed，100% |
| API 验收 / 越权 / RLS / 架构 | **99 passed** / 18 passed / 4 passed / 1 passed |
| 契约测试 | 20 passed；`api-snapshot --check` exit 0；api-check 违规 0 |
| 前端 lint/typecheck/test/build | ✅ exit 0；**13 tests**；生产构建含「导入记录」 |
| 真实 HTTP P0 全量 | **14/14**（P0-1 对账 0 断链 + P0-5 契约一致 + P0-4 批次/撤销 API） |
| P0-2/P0-3 回归 | **14/14** |
| 原 §2.1 主链路回归 | **23/23** |

## 5. 剩余事项（P1/P2，本次未修）

- **P1**：worker 心跳缺失、pending 取消不终态、单飞异参复用、RLS 仅覆盖 ledger、`summary_rule` WITH CHECK、分类未接入 commit、真实账单词汇漂移、seed/demo `mode=append` 与账号邮箱、`GET /trades` sort/batch_id/include_total 空转、token localStorage、无 eslint、上传防护等（审查报告 §5.2）。
- **P2**：mapping 透传、`replaced_batch_id` 血缘、幂等 TTL、Clock 收口、游标签名、JWT aud/iss、SSE 头、readyz/version 字段、旧库金额口径迁移等（审查报告 §5.3）。
- **旧库数据**：P0-1 口径变更后，旧 seed/导入数据需 revoke 重导或清库重 seed；本次未清库。

## 6. 环境/操作提示

1. 改 worker 代码后必须重启 `make worker`（无热重载；上轮曾因旧 worker 抢任务导致偶发假失败）。
2. 本地库 schema 已在 P0-2 修复时手工更新（partial unique index）；新环境请 `make db-reset`。
3. `openapi-typescript` 已入 devDependencies（7.4.4）；新环境先 `cd frontend && pnpm install`，再 `make sdk`。
4. 复验使用本地 PG 5432，新增若干 `p0*` 测试账号；dev 库非生产。
