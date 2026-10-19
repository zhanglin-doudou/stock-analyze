# 测试策略与质量门禁（v2 · 多租户 · 前后端分离）

> 版本 **v2.0**｜日期 2026-10-19｜范围：`backend/`（uv workspace：`packages/{contracts,core,ledger,review,delivery,platform}` + `services/{api,worker}`）、`frontend/`（pnpm workspace：`apps/web` + `packages/api-client`）、`openapi/`、`infra/`。
> 上游输入：[PRD-v2](../../prd/PRD-v2.md)（§5.0 验收总则、NFR-6/7/10、附录 A/B）、[v1 Testing](../v1/Testing.md)（**只继承口径测试思想，不继承基础设施**）、[API-Catalog](API-Catalog.md)（`API-001` 编号体系，本文只引用编号）。
> 本文**不定义任何新口径**：口径唯一事实源是 PRD 附录 A（A1–A27，v2 未改）与 `backend/packages/contracts/constants.py`。本文定义的是**如何证明口径没被实现错、租户没被串、契约没漂**，以及什么条件下不允许合并/发布。

## 0. 一页结论

| # | 决策 | 内容 | 为什么 |
|---|---|---|---|
| D1 | 覆盖率分层沿用 v1"分层不设全局 100%"，**三个包上调、一个层下调** | `core` 100/100 硬；`contracts` 97→**98/96**；`platform` 90→**92/88**；`services/api` 70→**75/65 且降为软**（改用端点验收覆盖作硬指标） | v2 的 `platform` 承担 RLS session 注入、PAT 哈希、job 租约、限流——**横切安全正确性**；`api` 是薄路由，追端点覆盖比追行覆盖更值 |
| D2 | 正确性由**独立 oracle** 证明，不由被测代码自证 | `backend/tests/oracle/` 朴素实现 + `fixtures/expected/` 手算表 + 生产实现三方比对 | v1 病根"接口调通即验收"在 v2 更危险：多了 100 个端点可以"调通" |
| D3 | **越权用例与口径用例同级**，且**零豁免** | 不允许 `pragma: no cover`、不允许 quarantine、不允许 waive；缺一条即红 | 越权是本产品**唯一会泄漏真实资金数据**的缺陷（PRD §9 风险表第 1 行） |
| D4 | 契约漂移用**编译期 + CI 双闸** | OpenAPI 快照 diff gate + 生成式 `api-client` + `tsc --noEmit` | 跨语言边界（Pydantic→JSON→TS）的漂移编译期不可见，运行时只表现为"页面白了" |
| D5 | 真 PG，不用 SQLite、不用 `:memory:` | 每 worker 一库，`CREATE DATABASE … TEMPLATE` 克隆，迁移只跑一次 | RLS、`SKIP LOCKED`、`NUMERIC`、`timestamptz`、游标语义 SQLite 上不存在，测了也不代表生产 |
| D6 | 确定性先于覆盖率（v1 U1–U11 → v2 U1–U12） | 新增 PG 事务隔离、并行 worker、异步事件时序 | 不确定的测试不能当门禁，也无法做 golden diff 与变异 |
| D7 | 门禁分级 | 阻断：越权、金额/阻断态、golden diff、`core` 覆盖率、契约 breaking、端点验收覆盖；告警：`api`/`delivery`/web 覆盖率、性能 | 门禁太多等于没有门禁 |
| D8 | 一票否决只有 6 项 | 越权用例缺失/失败、golden diff 未审批、OpenAPI breaking 未升版、`blocked` 态渲染出收益、生成代码手改、`core` 覆盖率跌破 100 | 这 6 项任一发生都是"发出去就出事" |

## 1. 测试策略总纲

### 1.1 v2 新增两类高危缺陷（v1 结构上不可能出现）

| 编号 | 缺陷类 | 为什么 v1 没有、v2 必然有 | 为什么行覆盖率完全无感 | 唯一能抓它的手段 |
|---|---|---|---|---|
| **H1** | **越权 / 租户泄漏** | v1 单用户 + 无鉴权，物理上只有一个人的数据；v2 每条查询、每个 job、每个下载链接都是新泄漏面 | 漏写 `WHERE user_id = :uid` 后分支照常执行、行照常命中，覆盖率仍 100% | 跨租户负向用例（§4）+ RLS 直连 SQL + 架构约束（强制 `user_id`） |
| **H2** | **前后端契约漂移** | v1 SSR 同进程、模板与 DTO 共享 Python 类型；v2 是 Pydantic→JSON→TS 生成链，两侧可各自演进 | 前后端各自 100% 覆盖，"拼接处" 0 覆盖 | OpenAPI 快照 diff + 生成式 SDK + Schemathesis + MSW 契约生成 + E2E（§6/§7） |

### 1.2 四类缺陷 × 哪类测试能抓

图例：`●` 主抓（该缺陷的第一道门）｜`◐` 可部分捕获｜`—` 基本无感

| 测试类型（见 §3） | 口径实现错 | 越权/租户泄漏 | 契约漂移 | 接线/门禁错 | 证据形态 |
|---|---|---|---|---|---|
| 单元 pytest / Vitest | ◐ | — | — | ◐ | 单函数断言 |
| 属性 hypothesis | ● | ◐（不变量：输出 ID 集合 ⊆ 本租户） | — | — | 反例最小化 |
| 仓储集成（真 PG + RLS） | ◐ | ● | — | ◐ | SQL 行集 / `EXPLAIN` |
| 契约（Schemathesis + 快照） | — | ◐（响应含他人字段即红） | ● | — | 契约违规报告 |
| API 验收（`API-xxx`） | ● | ● | ◐（结构断言顺带） | ● | 状态码/业务值/副作用 |
| 前端组件（RTL + MSW） | — | — | ◐（MSW 由契约生成） | ●（`blocked` 不渲染 value） | DOM 断言 |
| E2E（Playwright） | — | ◐ | ●（端到端接线） | ● | 浏览器轨迹 |
| 架构约束（AST / import-linter） | — | ●（强制 `user_id`、禁裸 SQL） | ◐（生成代码禁手改） | ● | lint 报告 |
| 变异（mutmut） | ● | ◐（RLS 谓词变异体） | — | — | mutation score |
| 性能/容量 | — | ◐（数据量放大后越权查询退化） | — | ◐ | p95 直方图 |
| 数据质量（PII / fixture） | ◐ | ◐（fixture 串租户 = 测试假绿） | — | ◐ | 正则扫描报告 |

**读法**：没有单一测试能同时抓住四类缺陷。口径靠 `属性 + 验收 + 变异`，越权靠 `验收负向 + RLS 直连 + 架构约束`，契约靠 `快照 + 生成 + E2E`，三者互不替代。

### 1.3 v1 → v2 测试基础设施断裂表（不可继承的部分）

| 维度 | v1（作废） | v2（本文定义） | 断裂原因 |
|---|---|---|---|
| 数据库 | SQLite 文件库 + WAL | **PG 16 + 三 schema + RLS**，template DB 克隆 | RLS/`SKIP LOCKED`/`NUMERIC`/游标语义 SQLite 没有 |
| 认证/租户 | 无（seed 单用户） | JWT 15min + 轮换 refresh + PAT(scope) + role；A/B/admin 三夹具 | 每个验收用例都要带"谁" |
| HTTP 入口 | `httpx.ASGITransport` | 验收用 ASGITransport；**Schemathesis/E2E 用真 uvicorn + nginx 同源** | 中间件、限流、RLS 连接钩子只在真事件循环生效 |
| 展示层 | Jinja/HTMX 文本断言 | React SPA（RTL + MSW）+ Playwright | 断言对象从 HTML 变 DOM/网络 |
| 契约 | 无（同进程共享类型） | `openapi/openapi.json` 快照 + 生成 `api-client` | H2 |
| 长任务 | 同步 job + `ManualClock.tick()` | `202+job_id` + SSE + 真 worker 轮询 | §8 U10 |

## 2. 分层覆盖率与门禁表

### 2.1 后端（`backend/`）

| 层 / 路径 | 行 | 分支 | 附加硬指标（v2 新增） | 工具 | 门禁强度 | 不达标后果 |
|---|---|---|---|---|---|---|
| `backend/packages/core/` | **100%** | **100%** | 零豁免；`values/*`、`metrics/tolerance.py` 变异 ≥95% | pytest-cov + branch | **硬（无豁免）** | CI fail，禁止合并（D8） |
| `backend/packages/contracts/` | **98%** | **96%** | 每个 DTO 的 `model_validator` 100%；`Money`/`Ratio` 序列化分支 100%；错误码 `code` 枚举全覆盖 | pytest-cov | 硬 | CI fail |
| `{ledger,review}/domain/` | **98%** | **96%** | `reconcile.py` 分级判定、`sample_gate.py` 门槛、`dedupe_key` 归一 100% | pytest-cov | 硬 | CI fail |
| `{ledger,review}/application/` | **95%** | **90%** | 每个 use case 的 `user_id` 注入路径 **100%**；跨租户拒绝分支 **100%**；失败路径（撤销/降级/阻断）100% | pytest-cov | 硬 | CI fail |
| `{ledger,review}/infrastructure/` | **85%** | **80%** | 新增/变更行 ≥90%；每个 Repository 方法 ≥1 条 RLS 断言 | pytest-cov | **软**（85 以下告警；新增行 <90 阻断） | 告警 / 新代码阻断 |
| `backend/packages/platform/` | **92%** | **88%** | `SET LOCAL app.user_id` 注入与清除、PAT 哈希与 scope 判定、`SKIP LOCKED` 租约、限流窗口 100% | pytest-cov | 硬 | CI fail |
| `backend/packages/delivery/` | 80% | 70% | 报告/导出渲染的 `blocked` 降级分支 100% | pytest-cov | 软 | 告警 |
| `backend/services/api/` | 75% | 65% | **router 依赖声明 100% 含 `CurrentUser`**；端点验收覆盖 100% | pytest-cov + 端点覆盖 | 软（行）+ **硬（依赖/端点）** | 告警 / 缺依赖阻断 |
| `backend/services/worker/` | 80% | 70% | 每个 handler 终态写入 **100%**；`idempotency_key` 命中路径 100% | pytest-cov | 硬（终态） | CI fail |
| **新增/变更行** | **≥95%** | — | 存量覆盖率不作为新代码挡箭牌 | `tools/ci/gate_diff_coverage.py` | 硬 | 阻断 |

### 2.2 前端、契约与越权用例覆盖

| 对象 | 目标 | 工具 / 脚本 | 门禁强度 | 不达标后果 |
|---|---|---|---|---|
| `apps/web/src/**`（排除生成与 `test/`） | 行 ≥80% / 分支 ≥70% | `vitest --coverage` | 软 | 告警 + PR 说明（页面多为编排） |
| `apps/web/src/features/**/components/**` | 行 90% / 分支 85% | 同上；渲染门禁组件 `MetricCard`/`GateBanner`/`SampleBadge` | **硬** | 阻断 |
| `apps/web/src/lib/auth/**`、`**/guards/**` | 行 95% / 分支 90% | 同上 | **硬** | 阻断（刷新/跳转错会让全员登不进去） |
| `packages/api-client/src/{http,auth,errors}/**`（手写） | 行 95% / 分支 90% | `vitest` + `msw` | **硬** | 阻断 |
| `**/generated/**`（生成代码） | 排除（`coverage.exclude`） | `tsc --noEmit` 保证类型一致 | — | 不参与覆盖率 |
| **OpenAPI 契约完整性** | 100% 端点含成功示例 + 错误码枚举；`operationId` 与 lock 逐条一致 | `tools/ci/gate_openapi_completeness.py` + `check_openapi_breaking.py` | **硬** | 阻断 |
| **验收套件端点覆盖** | 100%（`API-001..API-nnn`） | `tools/ci/gate_endpoint_coverage.py` | **硬** | 阻断（§5.5） |
| **越权：受保护端点 cross-tenant 404** | 100% | `tools/ci/gate_tenant_coverage.py` | **硬（零豁免）** | 阻断（D3/D8） |
| **越权：写端点跨租户被拒 + 副作用未发生** | 100% | 同上 `--require-side-effect` | **硬** | 阻断（只断言 404 不够） |
| **越权：admin 端点 role 403** | 100% | `test_admin_endpoints.py::test_role_matrix` | **硬** | 阻断 |
| **越权：高危类别攻击变体** | 批量/游标/导出/SSE/分享/审计/revoke 各 ≥3 | `--min-per-category 3` | **硬** | 阻断（§4.2） |

### 2.3 相对 v1 的三处调整与论证

1. **`contracts` 97/95 → 98/96**：v2 的 DTO 多了 `user_id`、`Idempotency-Key` 校验、`Money`（定点字符串）/`Ratio` 自定义类型、`MetricResult` 增加 `status/reason/what_to_do`；Pydantic v2 `mode="after"` 校验器分支变多，**一个校验器漏测就让 `blocked` 或他人 `user_id` 流到响应**。
2. **`platform` 90/85 → 92/88**：v2 的 `platform` 不只管 job/backup，还管 **RLS 连接级 session 变量、PAT 哈希与 scope、限流窗口**——H1 的第二道防线，事故面覆盖全部功能域。
3. **`services/api` 改为"端点覆盖硬 + 行覆盖软"**：薄路由的行覆盖可被无意义用例刷到 90%，而"每个 router 是否声明 `CurrentUser`"是**结构性**的，一条 AST 测试覆盖 100% 泄漏面。

## 3. 测试金字塔 v2

| 套件 | 目录 | 目标层 | 工具 | 规模预算 | 时长预算 | 触发通道 | 门禁 |
|---|---|---|---|---|---|---|---|
| 单元 | `backend/packages/*/tests/unit/`、`frontend/apps/web/src/**/*.test.tsx` | core / domain / web 纯函数 | pytest；Vitest | ~1600 条 | ≤40s | pre-commit + PR-fast | 硬 |
| 属性 | `backend/packages/{core,ledger,review}/tests/properties/` | core / domain | hypothesis | ~50 条 × 200 examples | ≤120s | PR-full + nightly | 硬 |
| 仓储集成（真 PG） | `backend/packages/*/tests/integration/`、`backend/tests/integration/` | infra + RLS | pytest + asyncpg + template DB | ~300 条 | ≤6min | PR-full + nightly | 硬 |
| 契约（OpenAPI） | `backend/tests/contract/` | services/api | Schemathesis + syrupy + breaking 检查 | 全端点 × 50 examples | ≤8min | PR-full + nightly | 硬（breaking 阻断） |
| **API 验收套件** | `backend/tests/api/test_api_*.py` | 全栈（api→PG→worker） | pytest + httpx（真 uvicorn） | ~260 条（≈端点数 ×3） | ≤9min | PR-full + release | 硬（100% 端点） |
| 前端组件 | `frontend/apps/web/src/**/*.test.tsx` | web | Vitest + RTL + MSW | ~220 条 | ≤90s | PR-fast + PR-full | 硬（关键目录） |
| 前端架构 | `frontend/tests/architecture/` | web | node AST + ESLint 自定义规则 | ~12 条 | ≤20s | PR-fast | 硬（零口径） |
| E2E | `frontend/e2e/*.spec.ts` | 全链路 | Playwright（chromium） | 8 条关键路径 | ≤6min | PR-full（冒烟 2 条）+ nightly | 硬 |
| 架构约束（后端） | `backend/tests/architecture/` | 全后端 | import-linter + AST | ~26 条 | ≤25s | PR-fast | 硬 |
| 变异 | mutmut on `backend/packages/core/src/core` | core | mutmut | ~700 变异体 | nightly ≤40min | nightly | 硬（score 门槛） |
| 性能/容量 | `backend/tests/perf/`、`perf/k6/` | API + 计算 | k6 + pytest-benchmark | 12 场景 | nightly ≤25min | nightly + release | warning（release 中 2× 超预算阻断） |
| 安全 | `backend/tests/security/`、`frontend/e2e/authz.spec.ts` | API + Web | pytest 负向 + 依赖扫描 + ZAP baseline | 越权全矩阵（§4）+ 依赖 | ≤3min | PR-fast（依赖）+ nightly（DAST） | 硬 |

**三层断言约定**：① **传输层** 状态码 + `Content-Type` + Pydantic DTO 反序列化（`extra="forbid"`）+ 错误三字段 `code/user_message/what_to_do`；② **业务值层** 唯一容差入口 `backend/tests/support/tolerance.py::assert_amt/assert_ret/assert_cum`，金额按定点字符串比较，禁 `float()` 中转；③ **副作用层** 用独立 psycopg 连接查库（不走 ORM，避免"ORM 自己过滤了就以为写进去了"），断言 `version`/`deleted_at`/审计行/job 状态迁移序列。

## 4. 多租户越权测试矩阵（核心）

### 4.1 方法与夹具

| 项 | 约定 | 落地 |
|---|---|---|
| 租户夹具 | 每会话固定三租户：A=`…000a`、B=`…000b`、admin=`…00ad`；邮箱 `user-a@example.test`（`.test` 保留域） | `backend/tests/factories/tenants.py::TenantFactory` |
| **`user_id` 无默认值** | 所有工厂的 `user_id` 为**必填位置参数**，不提供默认 → 忘传即 `TypeError`（"编译期"防串租户） | `backend/tests/factories/{trades,rounds,journal,exports}.py` |
| 统一断言 | `assert_tenant_invisible(resp)`：`404` **且** body 不含 A 的任何 UUID/金额/证券名（正则扫哨兵集合）**且** 不含 `forbidden/permission/owner` 等存在性暗示词 | `backend/tests/support/tenant_assert.py` |
| 404 vs 403 口径 | **跨租户一律 404**（不泄露存在性）；**角色不足一律 403**（admin 调 `/trades` → `ADMIN_CANNOT_READ_TRADES`）；批量接口二选一但**全仓一致**，由 `tests/architecture/test_error_semantics_consistency.py` 机器校验 | PRD FR-L2 验收行 |
| 存在性侧信道 | "存在但属他人"与"根本不存在"两种 UUID：状态码/响应体/`Content-Length` 全等，p95 时延差 <15ms | `backend/tests/api/test_api_tenant_side_channel.py::test_nonexistent_and_foreign_are_indistinguishable` |
| ID 形态 | 断言业务 ID 为 UUIDv4（非自增）；1000 个随机 UUID 遍历返回 **0 条**数据 | `test_uuid_enumeration_yields_no_data` |

### 4.2 端点类别 × 攻击方式 × 期望结果（20 条）

> `API-xxx` 为《API-Catalog》编号占位。`T-ALL` 宏 = 对该资源 ID 依次发起 `GET 详情 → PATCH → DELETE → 列表过滤 → 导出/下载 → job/SSE 订阅`，期望全部 404 且响应不含 A 任何字段。

| # | 端点类别（API 编号） | 攻击方式 | 期望结果 | 用例锚点 |
|---|---|---|---|---|
| 1 | 回合详情 `GET /rounds/{id}`（API-04x） | B 用自己 token 请求 A 的回合 UUID | **404** `ROUND_NOT_FOUND`（非 403），body 无 A 的 `security_name/pnl` | `test_api_042_rounds_isolation.py::test_cross_tenant_404` |
| 2 | 交易列表 `GET /trades`（API-03x） | 注入测试开关**断开仓储 `user_id` 过滤** | **RLS 兜底**：仅返回 B 的行；UUID 集合 ∩ A = ∅ | `test_trades_isolation.py::test_rls_blocks_when_repo_filter_disabled` |
| 3 | 单行修改 `PATCH /trades/{id}` | B 带 A 的 `If-Match` 改 A 的行 | 404；**副作用**：A 行 `version` 与 `updated_at` 未变 | `test_trades_write_isolation.py::test_patch_cross_tenant_no_side_effect` |
| 4 | 单行删除 `DELETE /trades/{id}` | B 删 A 的行 | 404；A 行 `deleted_at IS NULL` 不变 | `test_trades_write_isolation.py::test_delete_cross_tenant_no_side_effect` |
| 5 | 批量 `POST /trades/batch-delete` | 数组混入 3 个 A 的 ID + 1 个 B 的 ID | 403 `CROSS_TENANT_ID` 或剔除 A 项；**响应不回显 A 任何字段**；B 的 1 条被删 | `test_batch_isolation.py::test_mixed_ids_rejected_atomically` |
| 6 | 游标分页 `?cursor=`（API-03x） | B 复用 A 的 `next_cursor`（内嵌 `user_id`+HMAC） | 400 `INVALID_CURSOR` 或空页；**不得**返回 A 行 | `test_pagination_isolation.py::test_foreign_cursor_rejected` |
| 7 | 导入预览 `POST /imports/preview` | B 上传 A 的账单（同内容哈希） | 200（属新数据）但**不得**关联 A 的 `import_batch`；`estimated` 只算 B 空间 | `test_imports_isolation.py::test_preview_scoped_to_tenant` |
| 8 | 导入撤销 `POST /imports/{batch_id}/revoke` | B revoke A 的 batch | 404；A 批次 `status` 与行数不变 | `test_imports_isolation.py::test_revoke_cross_tenant_404` |
| 9 | 隔离区 `GET /imports/{id}/quarantine` | B 传 A 的 batch id | 404；不泄露行号/原始摘要 | `::test_quarantine_cross_tenant` |
| 10 | 导出下载 `GET /exports/{id}/download` | B 下载 A 的导出包 | 404，**且无** `Content-Disposition`/`Content-Length` 泄露（防长度侧信道） | `test_exports_isolation.py::test_download_cross_tenant_404` |
| 11 | 报告下载 `GET /reports/{id}/download` | B 下载 A 的月报 | 404；与"不存在"响应体全等 | `test_reports_isolation.py::test_report_download_cross_tenant` |
| 12 | 分享链接（FR-D8） | 匿名用 A 的 share token，附加 `?report_id=<B 的报告>` | 404/410；链接**只**绑定创建时那份报告 | `test_shares.py::test_share_token_single_target` |
| 13 | 任务轮询 `GET /jobs/{id}` | B 轮询 A 的 `job_id` | 404；`GET /jobs` 列表只含 B 的 | `test_jobs_isolation.py::test_poll_cross_tenant_404` |
| 14 | SSE `GET /jobs/{id}/events` | B 订阅 A 的 job 事件流 | 404 且**连接不建立**；若建立则 0 帧（不得出现 `progress`/结果摘要） | `::test_sse_cross_tenant_no_frames` |
| 15 | PAT 管理（FR-A1） | B 撤销 A 的 token id；B 用 A 的明文 PAT | 404；PAT 调用返回 401（token 绑定 user） | `test_tokens_isolation.py::test_revoke_foreign_token_404` |
| 16 | 审计 `GET /audit-logs` | B 查 `?actor=<A>` / `?request_id=<A 的>` | 403 `INVALID_FILTER` 或空集；返回行 `actor` 恒为 B | `test_audit_isolation.py::test_actor_filter_rejected` |
| 17 | admin 端点 `GET /admin/backups` | `user` 角色访问；admin 调 `GET /trades` | 403 `ADMIN_REQUIRED`；403 `ADMIN_CANNOT_READ_TRADES` | `test_admin_endpoints.py::test_role_matrix`（6 端点 × 2 角色参数化） |
| 18 | 注销 `POST /auth/delete-account` | B 注销后 A 的全量数据仍在 | B 全表 0 行（物理删 + 审计）；A 行数与指标 diff=0 | `test_account_deletion.py::test_deletion_is_tenant_scoped` |
| 19 | 设置 `PUT /settings/behavior-rules` | B 改配置后检查 A 的 settings | A 的配置不变（`scoped_by_user` 断言） | `test_settings_isolation.py::test_settings_scoped` |
| 20 | 日志/提醒 `GET /journal?round_id=`、`POST /alerts/{id}/ack` | B 传 A 的回合 id / 提醒 id | 404；A 的提醒 `acked_at` 仍为空 | `test_journal_isolation.py`、`test_alerts_isolation.py` |

### 4.3 RLS 直连验证（第二道防线，必须独立于应用层）

用**原始 SQL**（`asyncpg`/`psql`，绕过 SQLAlchemy）验，否则只是把应用层测试再跑一遍：

| # | 场景 | 执行 | 断言 | 文件 |
|---|---|---|---|---|
| R1 | 设 `app.user_id=A` 后查 `ledger.trade` | `SET LOCAL app.user_id='<A>'` + `SELECT count(*)` | 只看到 A 的行 | `backend/tests/integration/test_rls_direct.py::test_select_scoped` |
| R2 | **不设** `app.user_id`（连接复用未初始化态） | 裸连接 `SELECT count(*)` | **0 行**（fail-closed），不得回退全表 | `::test_missing_session_var_yields_zero` |
| R3 | 设 A 但 `WHERE user_id=<B>` | 显式越权条件 | 0 行（RLS 与谓词双拦截） | `::test_explicit_foreign_predicate_still_zero` |
| R4 | 跨租户 `UPDATE`/`DELETE` | `WHERE user_id=<B>` | 影响行数 0 | `::test_update_delete_scoped` |
| R5 | `INSERT` 伪造成 B | `VALUES (… <B>)` | 违反 `WITH CHECK` → `InsufficientPrivilege` | `::test_insert_with_check` |
| R6 | 每张业务表逐一验证 | 表清单取自 `information_schema`（`ledger`/`review`） | 策略覆盖 = 100%；缺策略的表 **fail** | `::test_every_business_table_has_rls` |
| R7 | 连接池泄漏 | 归还连接后再取，检查 `app.user_id` 已 `RESET` | 新租户不继承上一租户（50 并发） | `::test_pool_reset_between_tenants` |
| R8 | admin 直连 | `SET app.user_id=<admin>` + `SET app.role=admin` | 交易表 **0 行**（admin 亦不可读明细） | `::test_admin_cannot_read_trades_at_db_level` |

> R2 是全表最重要的一条：RLS 常见事故是 `using (user_id = current_setting('app.user_id')::uuid)`，而变量缺失时 `current_setting` 报错 500 或返回空串导致**全放行**。必须 fail-closed。

### 4.4 覆盖度量与 CI 门禁

```python
# backend/tests/api/_endpoint_registry.py（由 tools/ci/gen_endpoint_registry.py 从 API-Catalog.md 生成）
PROTECTED = {("GET", "/api/v1/rounds/{round_id}"): "API-042", ...}
TENANT_CASES = {"API-042": ["test_cross_tenant_404", "test_cross_tenant_no_side_effect"], ...}
```

| 门禁 | 判据 | 脚本 |
|---|---|---|
| G-ISO-1 | `PROTECTED - TENANT_CASES.keys() == ∅` | `tools/ci/gate_tenant_coverage.py --min-per-endpoint 1` |
| G-ISO-2 | 写端点 100% 有"跨租户写被拒 + 副作用未发生"双断言 | 同上 `--require-side-effect` |
| G-ISO-3 | 高危类别（批量/游标/导出/SSE/分享/审计/revoke）各 ≥3 变体 | `--min-per-category 3` |
| G-ISO-4 | RLS 表覆盖 = `information_schema` 业务表集合 | `test_rls_direct.py::test_every_business_table_has_rls` |
| G-ISO-5 | 越权用例 **0 条**被 `skip/xfail/quarantine` | `tools/ci/gate_no_security_skip.py` |

## 5. API 验收套件设计

### 5.1 与 `API-001` 1:1 对应

| 机制 | 约定 | 落地 |
|---|---|---|
| 编号 | Catalog 每个端点一个 `API-NNN`；测试文件名 `test_api_<NNN>_<slug>.py`，模块级 `pytestmark = [pytest.mark.api("API-042")]` | `backend/tests/api/` |
| 生成 | `tools/ci/gen_api_acceptance_index.py` 从 Catalog 表格生成 `_acceptance_index.py`（编号 ↔ method+path ↔ 测试文件） | CI 校验双向 diff 为空 |
| 一端点三用例 | ① happy path（含示例值）② 鉴权/越权（受保护端点）③ 错误码（≥1 个 4xx 枚举值） | 缺任一即 gate 失败 |

### 5.2 租户化数据工厂（`backend/tests/factories/`）

| 工厂 | 产出 | 关键约束 |
|---|---|---|
| `TenantFactory` | A/B/admin + 各自 JWT/PAT | `user_id` 固定可复现（`…000a`），不随机 |
| `BillFactory(tenant, scene)` | 合成账单 → `trade` 行（复用 `fixtures/bills/a/`） | 强制 `tenant`；金额走 `Decimal`；`cash_balance` 由行重算（§12.4） |
| `RoundFactory(tenant, pattern)` | 已平仓回合 + FIFO fills | B 侧永远只有"诱饵"数据（1 行交易 + 1 回合） |
| `JobFactory(tenant)` | 已入队 job（`platform.job`） | 供 SSE/轮询/租约用例 |
| `MarketSnapshotFactory` | 冻结行情（`fixtures/market/**`） | 不联网；`data_as_of` 固定 |

### 5.3 断言三层（逐层可独立失败）

| 层 | 断言内容 | 失败意味着 | 工具 |
|---|---|---|---|
| L1 传输 | 状态码、`Content-Type`、`extra="forbid"` 反序列化、错误三字段、`WWW-Authenticate` 存在性 | 契约/错误模型坏了 | `assert_ok(resp, DTO)`、`assert_error(resp, code=…)` |
| L2 业务值 | `assert_amt/assert_ret/assert_cum`；定点字符串逐位比较；`n/ci_low/ci_high/sample_status/data_as_of/definition_version` 非空规则 | 口径错或血缘缺失 | `backend/tests/support/tolerance.py` |
| L3 副作用 | 独立 psycopg 查行数/`version`/`deleted_at`/审计行；job 迁移序列 `queued→running→succeeded`；重算 `inputs_hash` 相等 | 写没落/落了两次/状态跳变 | `backend/tests/support/db_probe.py` |

### 5.4 幂等与异步断言

| 断言 | 方法 | 期望 | 用例 |
|---|---|---|---|
| 同 key 同 body 重放 | 连发两次 | 同 `202` + **同 `job_id`** + 数据落一次 | `test_idempotency.py::test_replay_same_key` |
| 同 key 不同 body | 连发两次 | 409 `IDEMPOTENCY_KEY_REUSED` | `::test_same_key_different_body_409` |
| 不同 key 同 body（重复上传） | 连发两次 | 第二次 `inserted=0, skipped=n` | `::test_duplicate_upload_zero_inserted` |
| 并发同 key（5 并发） | `asyncio.gather` | 全部同 `job_id`；DB 只多一批 | `::test_concurrent_same_key_single_job` |
| 异步终态 | `wait_for_job(client, job_id, terminal={"succeeded","failed","canceled"}, timeout=10s, interval=50ms)`；**禁 `sleep`** | 预算内到终态；`failed` 必带 `code/what_to_do` | `backend/tests/support/jobs.py` |
| SSE 时序 | 订阅 `events` 至终态 | ≥1 帧 `progress` + 终态帧；终态帧不晚于轮询可见 | `test_jobs_sse.py::test_event_order` |

### 5.5 "端点 100% 覆盖"的度量与门禁

```bash
uv run python tools/ci/openapi_ops.py --out /tmp/ops.json                      # 契约侧端点全集
uv run pytest backend/tests/api --collect-only -q --json-report --json-report-file=/tmp/collect.json
uv run python tools/ci/gate_endpoint_coverage.py --ops /tmp/ops.json --collect /tmp/collect.json --require-tenant --require-error-code --min-cases 3
```

| 判定 | 通过条件 | 失败后果 |
|---|---|---|
| 端点覆盖 | `ops - covered == ∅` | 阻断（D8） |
| 用例完整性 | 每端点 ≥3 条，受保护端点含越权 | 阻断 |
| 实测覆盖 | `--cov=services/api` 行覆盖仅作告警；端点覆盖才是硬指标 | 告警 |

## 6. 契约测试（三件套 + 两个配套）

### 6.1 三件套

| # | 机制 | 位置 | 判据 | 触发器 |
|---|---|---|---|---|
| K1 | **OpenAPI 快照 diff gate** | `openapi/openapi.json` + `tools/ci/check_openapi_breaking.py` | breaking 非空 → 必须同时：升 `info.version` **major** + `openapi/BREAKING.md` 登记 + `api-breaking-approved` label | PR-fast |
| K2 | **Schemathesis 属性测试** | `backend/tests/contract/test_openapi_schemathesis.py` | `checks=all` + 自定义 `no_500`/`no_tenant_leak`；对**真 uvicorn + 真 PG** 跑 | PR-full + nightly |
| K3 | **生成式 SDK 漂移检测** | `tools/ci/gen_api_client.sh` → `frontend/packages/api-client/src/generated/` | 重生成后 `git diff --exit-code`；手写 wrapper 引用不存在的类型 → `tsc --noEmit` 失败 | PR-fast |

**K1 breaking 判定规则**（`tools/ci/breaking_rules.py`，**每条都有元测试**，否则工具退化为"永远返回 0"）：

| 规则 | 举例 | breaking | 元测试 |
|---|---|---|---|
| 删除/重命名 `operationId` | `listTrades` → `getTrades` | **是** | `test_deleted_operation_id_is_breaking` |
| 删除端点 / 改 method | 移除 `DELETE /trades/{id}` | **是** | `test_removed_path_breaking` |
| 新增必填字段 / optional→required | 新增 `required: mapping_version`；`benchmark` 变必填 | **是** | `test_new_required_field_breaking`、`test_made_required_breaking` |
| 删除响应字段 | `MetricResult.status` 消失 | **是** | `test_removed_response_field_breaking` |
| 收紧枚举 | 错误码删 `TOKEN_EXPIRED` | **是** | `test_enum_narrowing_breaking` |
| 改 `format`/类型 | `amount: string` → `number`（float！） | **是** | `test_type_change_breaking` |
| 改状态码集合 | 201 → 200 | **是** | `test_status_code_change_breaking` |
| 新增端点 / 新增可选字段 / 放宽枚举 | 加 `GET /trends` | 否 | `test_non_breaking_passes` |

**K2 的租户维度**：Schemathesis 用 3 组 token（A/B/admin/PAT-read）轮换生成请求，对每个响应执行自定义 check `no_tenant_leak`：响应中的任意 UUID/股东账号/金额不得属于**非当前 token 所属租户**（依据会话注入的哨兵集合）。这条 check 能在不知道攻击路径的前提下**自动**发现大量 H1。

### 6.2 两个配套

| 配套 | 内容 | 落地 |
|---|---|---|
| MSW handler 契约生成 | `tools/ci/gen_msw_handlers.py` 读 `openapi.json` → `frontend/apps/web/src/test/msw/handlers.generated.ts`；`onUnhandledRequest:"error"`；**禁止手写 mock 响应结构** | 否则前端测试证明的是"前端与它自己想象的后端一致" |
| `operationId` 稳定性 | `openapi/operationId.lock.json`：`operationId → (method, path, since_version)`；删除/改名即 fail（即使 path 未变） | `backend/tests/contract/test_operation_id_lock.py` |

## 7. 前端测试

### 7.1 组件/页面测试

| 对象 | 用例 | 断言 | 文件 |
|---|---|---|---|
| 看板 5 卡 | `DashboardPage` 渲染 | 5 张卡从 MSW 取数；卡内数字**等于** mock 的 `display` 字符串（零换算） | `frontend/apps/web/src/features/dashboard/DashboardPage.test.tsx` |
| `MetricCard` | `status=blocked` | **不渲染** `value`；渲染 `reason`+`what_to_do`+红字；DOM 不含"年化/超额/夏普/收益"结论词 | `.../components/MetricCard.test.tsx::test_blocked_hides_value` |
| `SampleBadge` | `sample_status=insufficient` | 渲染"样本不足"；**不出现** `conclusion` 文本（不拼结论） | `.../SampleBadge.test.tsx::test_insufficient_no_conclusion` |
| 回合下钻 | 点数字 → 抽屉 | 请求参数与后端 `drilldown{endpoint,params}` 逐字段一致（前端不拼装） | `.../RoundDrilldown.test.tsx::test_drilldown_uses_backend_params` |
| 图表 smoke | 净值折线 | `echarts.setOption` 收到的 `series.data` 与 API `points[]` **逐点相等**（验证不二次计算） | `.../NavChart.test.tsx` |
| 错误态 | 500 / 超时 / 429 | 错误边界 + 重试按钮；429 展示 `Retry-After` | `.../ErrorState.test.tsx` |

### 7.2 "前端零口径"架构断言（硬门禁）

| ID | 规则 | 实现 | 为什么 |
|---|---|---|---|
| F1 | `apps/web/src/**` 禁止 import `decimal.js`/`big.js`/`mathjs`/`bignumber.js` | ESLint `no-restricted-imports` | 有算术库就会有算术 |
| F2 | 禁止对 `metric.*.value`、`*.twr`、`*.pnl` 做二元运算或 `Number()/parseFloat()/toFixed()` | 自定义规则 `frontend/tools/eslint-rules/no-metric-math.ts` | 前端只展示后端给的 `display` 字符串 |
| F3 | 禁止 JSX 拼接结论文案：模板串含 `赚钱/亏钱/跑得快/死扛/建议/应该` 且插值来自数据 | `no-conclusion-concat.ts` | 结论文案唯一来源是后端 `conclusion`（FR-D3） |
| F4 | 非"口径详情"组件禁止裸术语：字面量命中 `TWR/MWR/XIRR/BHAR/PLR/PGR/夏普` | `no-naked-terms.ts`（allowlist `features/method/**`） | 术语映射在后端（PRD 附录 C） |
| F5 | `apps/web` 不得直接 `fetch(`/`axios(`，必须经 `packages/api-client` | `no-restricted-globals` + import 规则 | 绕过生成 SDK = 绕过契约 |

**双保险**：`test_backend_conclusion_verbatim.test.tsx` mock 后端返回 `conclusion:"这段时间的数据不敢给你算收益"` → DOM **原样包含**且不含额外"赚钱/亏"词；另有 `test_no_eslint_disable_in_web.py`（`apps/web` 内出现 `eslint-disable` 即 fail），防止静态规则被绕过。

### 7.3 认证流（`apps/web/src/lib/auth/`）

| 场景 | 期望 | 用例 |
|---|---|---|
| access 过期 → 401 | 单次 `POST /auth/refresh` 后重放原请求，用户无感 | `refresh.test.ts::test_single_refresh_on_401` |
| 3 个并发 401 | **只发 1 次** refresh（single-flight） | `::test_concurrent_401_single_flight` |
| refresh 失败 | 清 token → 跳 `/login?next=<当前路由>`，不循环刷新 | `::test_refresh_failure_redirects_login` |

### 7.4 Playwright 关键路径（8 条，`frontend/e2e/`）

| # | 文件 | 路径 | 断言要点 |
|---|---|---|---|
| E1 | `auth.spec.ts` | 注册 → 登录 → 看板 | 空数据引导文案；token 存 `sessionStorage` 而非 `localStorage` |
| E2 | `import.spec.ts` | 上传 → preview → commit → 轮询 → 回合列表 | 202 后显示 job 进度；终态后回合出现；用 `expect().toPass()` 不用 `waitForTimeout` |
| E3 | `blocked.spec.ts` | 断链区间看板 | 净值卡显示"不敢给你算收益"，**DOM 无**数字收益；对账页红字 + 断点行 |
| E4 | `report.spec.ts` | 生成月报 → 轮询 → 下载 HTML | `Content-Disposition` 触发下载；报告内 3 个数字与 API 脚本比对一致 |
| E5 | `export.spec.ts` | 导出 → 下载 Excel | 文件可被 `xlsx` 解析；含 `meta.definition_version` |
| E6 | `token.spec.ts` | 创建 PAT → 复制明文 → 撤销 | 明文只显示一次；刷新后列表无明文 |
| E7 | `authz.spec.ts` | 手工改 URL 访问他人 `round_id` | 渲染 404 页（非 500/白屏），无数据闪现 |
| E8 | `share.spec.ts` | 创建分享 → 无痕上下文打开 → 撤销 | 无痕可见受限内容；撤销后 404/410 |

## 8. 确定性工程 v2

| # | 不确定性 | 污染点 | 消除手段 | 验证 |
|---|---|---|---|---|
| U1 | 墙钟 | `computed_at/data_as_of/job 时间/报告日期` | `Clock` 端口 + `FixedClock("2026-10-19T15:00+08:00")`；AST 禁 `datetime.now/time.time` | `backend/tests/architecture/test_no_wall_clock.py` |
| U2 | 随机 | bootstrap CI、Wilson、抽样 | 显式 `seed=20261019`；`np.random.Generator(PCG64(seed))` | `test_bootstrap_seed_deterministic` |
| U3 | 浮点 | 金额/收益率 | 全链路 `Decimal`；DB `NUMERIC(18,2)/(18,6)`；API 序列化为**字符串**；`ROUND_HALF_UP` | `test_no_float_in_money_path.py`（AST + 响应正则禁科学计数法） |
| U4 | 时区 | 业务日 vs 时间戳 | DB `timestamptz`；业务日 `date` + `ZoneInfo("Asia/Shanghai")`；日历年化 365 | `test_timezone_contract.py`（时间戳必带 offset） |
| U5 | 行情 | 复权改写历史 | `fixtures/market/**` parquet + `meta.json(sha256/factor_version/data_as_of)`；测试只经 `ReplayMarketData` | `test_market_snapshot_hash` |
| U6 | 网络 | akshare / LLM / 下载 | pytest 会话级 socket 禁用；MSW `onUnhandledRequest:"error"` | `test_suite_has_no_network` |
| U7 | **PG 隔离** | 用例间互相污染 | **每测试事务回滚**（SAVEPOINT 包裹，结束 `ROLLBACK`）；迁移/RLS 专项用每 worker 独立库；不用 `:memory:` | `test_isolation_mode_documented` |
| U8 | **并行 worker** | xdist 抢同一库 | `-n 4`；每 worker 独立 `test_<workerid>`，由 `test_template` 克隆（`CREATE DATABASE … TEMPLATE`）；vitest `pool:"forks"` | `conftest.py::pg_database`（session 级） |
| U9 | **迁移一次性** | 每用例跑 Alembic 太慢 | `test_template` 于 session 首建时 `alembic upgrade head`（含三 schema + RLS + 分区）**只跑一次**；另设 `test_migrations.py` 跑 up/down/up | `test_migrations_roundtrip.py` |
| U10 | **异步等待** | job/SSE/worker | **禁 `sleep`**：`wait_for_job(...timeout=10s, interval=50ms)`；单测用 `InlineWorker`（同步执行），集成用子进程 worker + 轮询上限 | `test_no_sleep.py`（AST）+ `assert_elapsed < 2s` |
| U11 | 排序/迭代 | FIFO 稳定序、JSON 键序 | 显式 `sorted()` 按 A7 五元组；JSON `sort_keys=True` | P-06 顺序不敏感属性 |
| U12 | **限流时钟** | 429 窗口依赖真实时间 | `RateLimiter` 注入 `Clock`；测试 `FixedClock.advance(60)` 而非真等 | `test_rate_limit_window.py` |

**唯一容差入口**：`backend/tests/support/tolerance.py`；用例内**禁止**字面量 `0.01`/`0.001`（AST 扫描 `test_no_magic_tolerance.py`）。

## 9. Golden fixture 与边界矩阵（多租户化）

### 9.1 目录结构与命名

```
backend/tests/fixtures/
├── MANIFEST.json                    # sha256 / owner_tenant / synthetic / reason / schema_version
├── tenants/{a,b,admin}.yaml         # A=主租户 …000a；B=诱饵租户；admin=无业务数据
├── bills/a/<NN>_<scene>.{xlsx,csv}  # 28+2 条，owner=A
├── market/mkt-replay-cn_daily-20260101-v1/{meta.json,calendar.parquet,daily_hfq.parquet,factors.parquet}
├── expected/{rounds,nav,metrics,attribution}/a/<NN>_*.{yaml,json}
└── private/                         # .gitignore，真实脱敏账单仅本地
```

**命名**：`bills/<owner_tenant>/<NN>_<snake_case_scene>.<ext>`；`NN` 段位沿用 v1（`10s` 交易结构 / `20s` 公司行为 / `30s` 资金与打新 / `40s` 导入与对账 / `50s` 统计门槛与归因）；`expected/<domain>/<tenant>/<NN>_*` 与账单同名，测试用 `glob`+`parametrize` **自动配对**（新增 fixture 不改测试代码）；`MANIFEST.json` 缺条目即 fail（`backend/tests/data/test_fixture_manifest.py`）。
**`T-ALL` 宏**：每个 fixture 加载后自动收集其产生的全部资源 ID（round/trade/batch/export/report/job/share），对 B 与 admin 逐一发起 §4.2 的访问，期望全部 404/403 且 body 无 A 字段——**一条 fixture 自动获得 20 类越权断言**。

### 9.2 28 条边界矩阵（v1 口径断言 + v2 多租户断言）

> 全部 fixture 归属**租户 A**；"多租户断言"列除特别说明外均指 `T-ALL` + 该条特有攻击。

| # | fixture（`bills/a/`） | 口径断言（v1 继承） | v2 新增多租户断言 | FR |
|---|---|---|---|---|
| 1 | `10_add_position_two_lots` | FIFO 跨批配对；pnl/成本/`trade_id` 集合精确相等 | `T-ALL`；B 不得复用 A 的 `dedupe_key` | FR-R2, G1 |
| 2 | `11_partial_reduce_three_sells` | 3 回合；剩余 0；`Σpnl` 与现金流闭合 | B 的列表 `Σpnl` 不含 A 任何回合 | FR-R2 |
| 3 | `12_same_day_multi_fill` | bag 去重计数；FIFO 用文件行号破平局 | 同文件 B 再上传 → B 空间 `inserted=n`（**去重键含 `user_id`**，A27） | FR-L3, A27 |
| 4 | `15_oversell_negative_qty` | 不生成负持仓；标 `data_error` | 隔离记录只对 A 可见 | FR-R2, FR-L6 |
| 5 | `20_cash_dividend_single_lot` | 回合 pnl 含税后股息净额 | `T-ALL` | FR-R2, A3/A8 |
| 6 | `22_bonus_share_10_for_3` | 数量 ×1.3、总成本不变 | `T-ALL` | FR-R2, A9 |
| 7 | `23_rights_issue_payment` | 缴款入买入成本；不新增回合 | `T-ALL` | FR-R2, A9 |
| 8 | `24_ex_div_consistency` | 恒等式 ≤ `ε_amt` | `T-ALL` | FR-R2 |
| 9 | `25_dividend_tax_tiers` | 20/10/0% 档位边界精确命中 | `T-ALL` | FR-R2 |
| 10 | `26_suspended_valuation` | 停牌取前收 + `stale`；>50% 剔除卖飞率 | `T-ALL`；导出只含 A 标的 | FR-R2, A16 |
| 11 | `27_delisted_holding` | 退市股不消失；计入全样本分母 | `T-ALL` | A10 |
| 12 | `30_ipo_three_flows` | 不参与回合配对；净值不断链 | 打新流水导出归属 A | FR-L5 |
| 13 | `31_interest_vs_transfer` | 利息=内部收益；转账=外部现金流 | `T-ALL` | FR-R3, A2/A3 |
| 14 | `32_large_deposit_uptrend` | `MWR > TWR` 方向 + CI | `T-ALL` | FR-R3 |
| 15 | `40_unknown_summary_quarantine` | 100% 隔离 + 行号 | B 查 A 的 quarantine → 404；行号不泄露 | FR-L5 |
| 16 | `41_first_row_not_buy` | 期初反推 `back_solved`；首行入库 | `T-ALL` | FR-R2, A1 |
| 17 | `42_first_row_no_balance` | `NAV_UNRELIABLE`；只出回合级 | B 的空间不被 A 的降级污染 | FR-R2, FR-L6 |
| 18 | `43_balance_chain_break` | 检出率 100%；断点精确定位 | 断链事件只对 A 可见 | FR-L6, G2 |
| 19 | `44_no_cash_balance_column` | 降级不静默失败 | `T-ALL` | FR-L3 |
| 20 | `45_duplicate_upload` | 二次 0 新增；撤销回原状 | **B 上传同一文件必须全量入库**（反证 `user_id` 在去重键内） | FR-L3, NFR-5 |
| 21 | `46_interval_replace` | 旧行软删；复算等价直导 | B 传 A 的区间参数不触达 A 的行 | FR-L3 |
| 22 | `47_gbk_excel_serial` | 编码/序列号/别名表头；与 UTF-8 版 diff=0 | `T-ALL` | FR-L3 |
| 23 | `48_dirty_row_isolation` | 脏行进隔离区；不阻断整批 | 隔离行导出只含 A | NFR-2 |
| 24 | `50_n5_insufficient` | `sample_status=insufficient`；无结论文案 | 前端 `SampleBadge` 不显示结论（E2E E3 同断言） | FR-R8, A26 |
| 25 | `51_intraday_lt60_days` | 不出夏普/MDD/IR；`pending_window` | `T-ALL` | FR-R8 |
| 26 | `52_disposition_only_winners` | `DE>0` 且 CI 不含 0 | `T-ALL`；B 的 DE 不受 A 影响 | FR-R6, A15 |
| 27 | `53_no_disposition_effect` | `DE≈0` 且 CI 含 0 | `T-ALL` | FR-R6 |
| 28 | `54_attribution_buy_point` | 因子和 + residual ≤ `ε_amt`；`rank[0]=buy_point` | `T-ALL` | FR-R7 |
| 补1 | `55_attribution_residual` | 残差显式列出 | `T-ALL` | FR-R7 |
| 补2 | `56_high_beta_bull` | 未标 `benchmark_id+β` 不得出"选股能力强" | `T-ALL` | FR-R5 |

### 9.3 三方 oracle（防"用被测代码生成期望值"）

| 类型 | 位置 | 覆盖 | 可信原因 |
|---|---|---|---|
| 人工手算表 | `fixtures/expected/rounds/a/*.yaml` | 28 条的回合 pnl/收益率 | 与实现零共享代码 |
| 朴素参考实现 | `backend/tests/oracle/{naive_fifo,naive_nav,naive_xirr}.py` | FIFO/TWR/XIRR | 可读性优先、独立作者；**生产代码禁止 import `tests/`**（import-linter 合约 C7） |
| 交叉恒等式 | 桥接恒等式、数量守恒、`Σfactors+residual=区间损益` | 全 fixture | 不依赖外部期望值，能抓"期望值本身抄错" |

## 10. CI 流水线 v2

### 10.1 路径过滤与并发

| 变更集 | 触发任务 | 说明 |
|---|---|---|
| `backend/**` | backend-fast + backend-full + 端点契约门禁 | |
| `frontend/**` | web-fast + web-full + Playwright 冒烟 | |
| `openapi/**` | **强制全链路**：backend-full + 重生成 SDK + `tsc --noEmit` + Schemathesis + 快照 diff | 契约单源，改契约必须两侧同验 |
| `infra/**` | compose 起栈 + `/readyz` + 迁移 up/down | |
| 仅 `docs/**` | markdownlint + 断链 | 不跑测试，省 20min |

- `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}`（nightly 不取消）。

### 10.2 四段流水线

| 通道 | 触发 | 内容 | 超时 | 失败策略 |
|---|---|---|---|---|
| **pre-commit** | 本地提交 | ruff format/check、`lint-imports`、mypy（增量）、prettier+eslint（staged）、core 单测、web 单测（changed） | **60s** | 阻断提交 |
| **PR-fast** | 每次 push | ① ruff / mypy --strict（changed）② 后端架构约束 26 条 ③ 前端 F1–F5 ④ core+domain 单测 ⑤ web 关键目录 RTL ⑥ 数据质量/PII ⑦ diff coverage ⑧ `check_openapi_breaking` ⑨ 生成代码未手改 ⑩ 依赖漏洞 | **≤5min** | blocking |
| **PR-full** | `full-ci` label / ready-for-review | fast + ① 仓储集成（真 PG）② API 验收全量 ③ 越权 20 类 + RLS 8 项 ④ Schemathesis ⑤ golden ⑥ 属性 200 examples ⑦ Playwright 冒烟 2 条 ⑧ 按包覆盖率 | **≤20min** | blocking |
| **nightly** | 每日 03:00 | full + 属性 1000 examples + Playwright 全量 + mutmut 全量 + k6 性能 + DAST + 迁移 up/down/up + 备份恢复演练 + flake 统计 | **≤90min** | 建 issue（`quality-gate`）；连续 2 天失败升 blocker |
| **release** | tag | nightly 全量 + golden 冻结 + MANIFEST sha256 校验 + 越权全绿留档 + 性能 2× 预算阻断 | **≤120min** | blocking（任一失败禁止发布） |

### 10.3 矩阵、缓存与隔离

| 项 | 策略 | 理由 |
|---|---|---|
| Python / Node | 仅 3.12 / 仅 22 | 技术栈固定（C1/C2），多版本只烧时间 |
| PG | `postgres:16` service container（tmpfs） | 与生产同版本；RLS/分区行为一致 |
| 缓存 | `uv`（key=`uv.lock`）/ `pnpm`（key=`pnpm-lock.yaml`）/ `.mutmut-cache`（key=core 源码 hash）/ Playwright browsers | 首跑 4min → 热跑 50s |
| 并行 | `pytest -n 4`（每 worker 一库）；`vitest --pool=forks`；Playwright `workers:2` | §8 U8 |
| 隔离 | PR 用独立 PG service；nightly 用独立 volume；不共享 DB | 防上游失败污染下游 |

### 10.4 覆盖率按包判定（示意，不合并大报告）

```yaml
- run: |
    uv run pytest backend/packages/core/tests -q --cov=core --cov-branch --cov-fail-under=100
    uv run pytest backend/packages/contracts/tests -q --cov=contracts --cov-branch --cov-fail-under=98
    uv run pytest backend/packages/{ledger,review}/tests/unit -q --cov=ledger.domain --cov=review.domain --cov-branch --cov-fail-under=98
    uv run pytest backend/packages/platform/tests -q --cov=platform --cov-branch --cov-fail-under=92
- run: uv run pytest backend/packages/delivery/tests backend/services/api/tests -q --cov=delivery --cov=services.api --cov-fail-under=80 || echo "::warning::soft cov"
- run: uv run python tools/ci/gate_diff_coverage.py --xml cov.xml --min=95                  # 硬
- run: uv run python tools/ci/gate_endpoint_coverage.py --require-tenant --min-cases 3      # 硬
- run: uv run python tools/ci/gate_tenant_coverage.py --min-per-endpoint 1 --min-per-category 3   # 硬
```

### 10.5 前端门禁（PR-fast 顺序执行，fail-fast）

| 步骤 | 命令 | 门禁 |
|---|---|---|
| Lint+Type | `pnpm -r lint`（含自定义 F1–F5）+ `pnpm -r exec tsc --noEmit`（strict） | web 0 error/0 warn；类型 0 error（含生成代码） |
| Unit | `pnpm -C apps/web test -- --coverage` | 关键目录阈值 + 全局 80/70 |
| Arch | `node frontend/tests/architecture/run.mjs` | F1–F5 + 禁 `eslint-disable` |
| Build | `pnpm -C apps/web build` + `size-limit` | JS ≤400KB gzip；ECharts 按需引入 |
| 生成一致 | `bash tools/ci/gen_api_client.sh && git diff --exit-code …/generated` | 0 diff |

## 11. 性能与容量测试

| # | 场景 | 数据规模 | 判据（p95） | 工具/文件 | 通道 |
|---|---|---|---|---|---|
| Pf-1 | 看板并发（5 端点混合） | 50 VU / 单租户 5 万行 | 读 **≤300ms**；首屏聚合 ≤1s | `perf/k6/dashboard_50vu.js` | nightly |
| Pf-2 | 批量指标（20 指标 × 12 期） | 单租户 5 万行 | ≤300ms | `perf/k6/metrics_batch.js` | nightly |
| Pf-3 | 导入 preview（5000 行 xlsx） | 5000 行 | ≤2s（同步上限） | `backend/tests/perf/test_import_preview.py` | nightly |
| Pf-4 | 导入 commit（异步） | 5000 行 | 端到端 ≤30s | `perf/k6/import_commit.js`（轮询终态） | nightly |
| Pf-5 | 全量重算 | 5 万行 / 单租户 | **≤60s**（NFR-10） | `perf/k6/rebuild_rounds.js` | nightly + release |
| Pf-6 | 月报生成 | 12 个月 | ≤20s | `perf/k6/report.js` | nightly |
| Pf-7 | 导出（Excel+JSON） | 5 万行 | ≤60s | `perf/k6/export.js` | nightly |
| Pf-8 | 游标分页遍历全集 | 20 万行 | 单页 ≤300ms；无 N+1（`EXPLAIN` 断言索引命中） | `test_pagination_query_count.py`（计 SQL 条数） | nightly |
| Pf-9 | 容量：100 用户 / 500 万行 | `tools/seed_perf.py --users 100 --trades 50000` | Pf-1/2 的 p95 劣化 **≤20%** | k6（同上场景） | nightly + 月度 |
| Pf-10 | 计算层微基准 | 单 fixture | 回归 >2× 才阻断 | `backend/tests/perf/test_core_microbench.py`（≤30s） | **PR-full** |
| Pf-11 | 慢查询守护 | 全部集成用例 | 单条 SQL >100ms 记 warning 并输出 `EXPLAIN` | `backend/tests/conftest.py` SQL 计时钩子 | PR-full |

**环境**：独立 `infra/compose.pref.yml`（PG tmpfs + 固定 `shared_buffers`）；`tools/seed_perf.py` 固定种子生成 100 用户 × 5 万行；性能用例**只用** `perf` profile，不复用测试库。
**为什么不阻塞 PR**：只有 Pf-10/Pf-11 在 PR-full（合计 ≤40s），其余全在 nightly；release 时 Pf-1/Pf-5 超预算 2× 才阻断。

## 12. 变异、flake、豁免与 PII

### 12.1 变异测试（mutmut）

| 项 | 约定 |
|---|---|
| 范围 | `backend/packages/core/src/core/`（`fifo.py/nav.py/metrics/*/attribution.py/values/*`）+ **v2 新增** `platform/rls.py`（RLS 谓词构造）、`platform/security/policy.py`（scope 判定） |
| 门槛 | 全局 **≥80%**；`values/decimal_math.py`、`metrics/tolerance.py` ≥95%；`fifo.py`/`nav.py` ≥90%；`attribution.py` ≥85%；**`rls.py`/`policy.py` 100%（安全代码不允许等价变异体豁免）** |
| 运行 | nightly 全量（≤40min）；PR 只跑 `git diff` 涉及文件（≤10min） |
| 失败 | score 下降 >2pp 或跌破门槛 → issue `quality-gate`，不阻断当次合并但**阻断 release** |

**必杀算子（粗体为 v2 增补）**：

| 算子 | 高发位置 | 为什么必须杀死 | 杀手测试 |
|---|---|---|---|
| `<` ↔ `<=` | `win_rate.py`（`pnl > 0`）、`tolerance.py` | 胜率与容差边界 | `test_win_rate_excludes_zero_pnl`、P-09 |
| 符号翻转 | `fifo.py`、`behavior.py`（`DE = PLR − PGR`） | DE 写反会把"死扛"说成"跑得快" | `test_disposition_effect_detected` |
| 常量替换 | `tolerance.py`、`A=244` | 用 252 系统性偏差 | `test_constants_match_prd_appendix` |
| 分母 `n`/`n−1` | `wilson.py`、`sharpe.py`、费用占比 | "数字对但量级错" | golden + 手算表 |
| `and` ↔ `or` | `reconcile.py` 分级阻断、`sample_gate.py` | 阻断写松 → 渲染不可信收益 | `test_blocked_state_hides_return_cards` |
| **删除/短路 `user_id` 谓词** | `rls.py`、`scoped()` 助手、`repositories/*.py` 的 `WHERE` 构造 | 一处漏写 = 全表泄漏 | `test_every_query_has_tenant_predicate`（AST）+ §4.3 R2 |
| **`scope` 判定翻转** | `security/policy.py`（read vs write） | 只读 PAT 能写 | `test_read_scope_pat_cannot_write` |
| **限流窗口边界** | `rate_limit.py` | 第 N 次就 429 或多放一次 | `test_rate_limit_exact_boundary` |

### 12.2 flaky 治理

| 项 | 规则 |
|---|---|
| 判定 | 同 commit 连续 3 次不稳，或 nightly 同类失败 ≥2 次但本地不复现 |
| 处置 | 移入 `backend/tests/quarantine/`（默认 `--ignore`）+ 开 issue，SLA **5 个工作日**；超期自动 fail nightly |
| 禁令 | **禁止** `pytest-rerunfailures`、禁止 shell 短路、禁止 CI 重跑整条流水线"让它绿"；**E2E/SSE/时间相关禁止 `waitForTimeout`**，一律 `expect().toPass({timeout})` |
| 度量 | flaky 率 <1%；**越权与阻断态用例的 flaky 率必须为 0**（波动即视为产品缺陷） |

### 12.3 覆盖率豁免与 golden 审批

| 机制 | 规则 |
|---|---|
| `# pragma: no cover` | 仅限：平台专属分支（同行注释理由）、`TYPE_CHECKING`、`except BaseException: raise`（需 `issue: #NNN`）；总数 >10 或环比增加即 fail |
| `coverage-allowlist.toml`（v2 新增） | 每条含 `path/reason/owner/issue/expires(≤90 天)`；**过期自动 fail**；`packages/core/**`、`platform/rls.py`、`platform/security/**` **禁止进入 allowlist** |
| 越权用例 | **零豁免**：`skip/xfail/quarantine` 由 `gate_no_security_skip.py` 直接判红（D3） |
| golden 更新 | 只允许独立 PR；贴 diff（金额到分、比率到 bp）+ 口径负责人加 `golden-approved`；`definition_version` 未升而快照变 → fail |
| fixture 变更 | 新增：1 reviewer + MANIFEST；**修改数据：≥2 reviewer（含口径负责人）**并说明"为什么旧数据错"；删除：留 tombstone |

### 12.4 测试数据 PII（多租户版）

| 字段 | 规则 | 为什么 |
|---|---|---|
| 邮箱 | 一律 `*@example.test`（保留域），禁真实域名 | 测试数据会进日志/artifact |
| 股东账号 | 合成 10 位 `0000000001…`（保留长度与空值语义）；映射表 `_pii_map.local.json` 不入库 | `dedupe_key` 依赖账号，但真实账号是金融标识 |
| **金额** | **保序缩放后重算**：定 factor（如 0.37）→ 每笔取整到分 → **由新流水重算 `资金余额` 列**（不得逐行缩放） | 逐行缩放会让舍入误差破坏余额链，fixture 自己触发断链 |
| 证券代码/名称 | **保持真实**（`300308` 中际旭创） | 行情快照按代码对齐，改代码会让复权/基准失真 |
| 日期 | 保持真实交易日（对齐行情快照与 A=244） | 年化/交易日计数依赖日历 |
| **JWT/PAT** | `.env.test` 固定 dummy secret；PAT 明文只在创建响应出现，不得进 fixture/日志/快照 | 防测试凭证被误当真实凭证复用 |
| 日志/artifact | `test_logs_have_no_secrets.py` 扫描测试日志与 `--json-report`：不得含 `Authorization:`、`pat_`、`refresh_token`、邮箱明文 | NFR-1/NFR-15 |
| 出站 | `test_llm_payload_has_no_amount_no_account`（FR-D7 出站守卫） | 合规红线 |

**校验测试**：`test_fixtures_no_pii.py`（身份证/手机号/真实账号正则）、`test_fixture_manifest.py`（每条有 `owner_tenant`+sha256+`synthetic:true`）、`test_pii_amount_chain_closed.py`（缩放后余额链仍闭合）、`test_fixtures_tenant_disjoint.py`（A 与 B 的 UUID/金额集合**零交集**，否则"不可见"断言可能是假绿）——全部位于 `backend/tests/data/`。

## 附录：测试文件索引（v2 落地位置）

| 文件 | 职责 |
|---|---|
| `backend/tests/conftest.py` | 全局夹具：`FixedClock`、`pg_database`（template 克隆）、`tenants`(A/B/admin)、`replay_market`、`no_network`、`sql_timer` |
| `backend/tests/{factories,support,oracle}/` | 租户化工厂（`user_id` 无默认值）/ `tolerance`·`jobs`·`db_probe`·`tenant_assert` / 朴素参考实现 |
| `backend/tests/architecture/` | `test_{import_contracts,no_wall_clock,no_sleep,no_float_in_money_path,tenant_predicate,routers_require_current_user,error_semantics_consistency}.py` |
| `backend/tests/api/` | `test_api_<NNN>_*.py` + `_acceptance_index.py`（与 `API-NNN` 1:1）+ `test_api_tenant_*`/`test_*_isolation.py`（§4.2 的 20 类） |
| `frontend/tests/architecture/run.mjs` + `frontend/tools/eslint-rules/` | 前端零口径 F1–F5 |
| `frontend/e2e/*.spec.ts` | Playwright 8 条关键路径（E1–E8） |
| `tools/ci/` | `gate_{endpoint,tenant,diff}_coverage.py`、`gate_no_security_skip.py`、`check_openapi_breaking.py`、`gen_{msw_handlers,api_client,api_acceptance_index}.sh`（**每个脚本自带元测试**） |
