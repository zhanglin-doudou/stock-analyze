# 个人交易复盘平台 · 软件架构设计文档

> 版本：**v2.0** ｜ 日期：2026-10-19 ｜ 需求基线：[PRD-v2.md](prd/PRD-v2.md)（v2.0，28 条 FR + 15 条 NFR）
> 上一版：[Architecture.md](architecture/v1/Architecture.md)（v1，单机版，保留为历史基线）
> 配套：[v2/API-Catalog.md](architecture/v2/API-Catalog.md)（**可验收 API 目录**）｜[v2/Reliability.md](architecture/v2/Reliability.md)｜[v2/Testing.md](architecture/v2/Testing.md)｜[v2/Extensibility.md](architecture/v2/Extensibility.md)｜[v2/ADR.md](architecture/v2/ADR.md)

---

## 0. 导读与本次修订

### 0.1 修订依据与影响面

| 依据（来自需求方） | 架构影响 |
|---|---|
| 第一版即考虑**多用户 + 云主机部署** | 无状态服务、认证授权、多租户隔离（RLS）、服务级 SLO、PITR 备份 |
| 首批用户是**有经验的程序员投资者** | API/PAT/契约/SDK 成为一等公民；文档与示例即产品 |
| **PostgreSQL** | 双 schema、RLS、`SKIP LOCKED` 队列、分区与索引策略、连接池算式 |
| **前后端分离（React / Python）** | 双栈 Monorepo、OpenAPI 单源、生成式 SDK、前端零口径 |
| **重后轻前** | 口径与任务编排全部在后端；前端只渲染与交互 |
| **优先 API 可验收** | 本文把「可验收 API 清单」提到 §2（主文档最早的可施工章节）；每条 FR 以 API 判据验收 |
| **Monorepo** | `backend/`（uv workspace）+ `frontend/`（pnpm workspace）+ `openapi/` 契约快照 + `infra/` |

### 0.2 与 v1 的结构性差异（不是小改）

| 维度 | v1（单机） | **v2（云服务）** | 后果 |
|---|---|---|---|
| 进程 | 单进程 + SQLite | nginx + api×N + worker×M + PostgreSQL | 需要无状态化、租户上下文、分布式队列 |
| 存储 | 单文件 | PG 多 schema + RLS + PITR | 数据模型加 `user_id`，隔离有第二道防线 |
| 用户 | seed 单行 | 注册/登录/刷新/PAT/角色/注销 | 认证授权成为横切能力 |
| 前端 | SSR + HTMX | React SPA（薄）+ REST API | 契约成为跨团队边界，必须机器校验 |
| 验收 | 页面 + 手工 | **API 优先 + 验收套件 100% 端点** | 端点清单先于实现定稿 |
| 可用性 | 崩溃安全 | 99.9% 服务级 + 滚动发布 + 零停机迁移 | expand-contract、就绪探针、错误预算 |

**不变的部分（刻意保持）**：指标口径（PRD 附录 A 的 A1–A27）、FIFO/NAV/归因的**纯计算内核**、指标注册表与门禁状态机、结构化产物与人话层分离、"事实强持久化/派生可重建"原则。**口径与部署形态无关**——这是本次修订最重要的连续性。

### 0.3 被取代的架构决策

| v1 ADR | 决策 | 状态 |
|---|---|---|
| ADR-0001 | 单进程 + SQLite | **被 ADR-0013 取代** |
| ADR-0006 | SSR + HTMX + 本地资产 | **被 ADR-0016 取代** |
| ADR-0007 | 进程内事件总线 | **被 ADR-0015 取代**（outbox 保留，载体改 PG） |
| ADR-0008 | 事实 FULL / 派生 NORMAL | **被 ADR-0018 取代**（PG 事务与 WAL 归档） |
| ADR-0012 | LLM 旁路 + 金额本地填充 | **保留**（多用户下更关键，见 ADR-0022） |
| ADR-0002/0003/0004/0005/0009/0010/0011 | 分层/标度整数/指标注册表/产物分离/后复权/分层覆盖/降级门禁 | **继续有效** |

---

## 1. 架构驱动力

### 1.1 从 43 条需求到 10 个驱动力

| 驱动力 | 来源 | 架构含义 |
|---|---|---|
| **D1 API 可验收** | G3、FR-A2、§5.0 | 端点先定稿；OpenAPI 单源；验收套件 1:1 |
| **D2 多租户隔离** | G4、NFR-6、FR-L2 | `user_id` 贯穿 + RLS + 越权负向测试 |
| **D3 口径正确** | FR-R2~R8、附录 A | 纯计算内核、`definition_version`、golden/oracle |
| **D4 可追溯** | G2、NFR-4、FR-D3 | `inputs_hash/data_as_of/n` 进每个结果 |
| **D5 外部数据不可靠** | NFR-2/3/9 | 适配器 + 能力协商 + 缓存 + 降级 |
| **D6 异步长任务** | FR-L3/R2/D2、NFR-10 | PG 队列 + 租约 + 幂等 + SSE 进度 |
| **D7 云上可运维** | NFR-13/15、FR-L1 | 无状态 + 就绪探针 + 零停机迁移 + PITR |
| **D8 前后端契约稳定** | NFR-8/11、FR-A2 | 版本化 + 快照 diff + 生成式 SDK |
| **D9 隐私与合规** | NFR-1/14、FR-D7 | 认证、加密、出站守卫、注销物理删除 |
| **D10 单人可维护双栈** | 约束 C-单人 | 重后轻前、薄前端、生成式代码、严格分层 |

### 1.2 质量属性场景（可测）

| 编号 | 场景 | 度量 | 架构回应 |
|---|---|---|---|
| ASR-1 | 用户 B 猜测用户 A 的资源 ID | 跨租户请求 **0 成功**，返回 404 | 仓储强制过滤 + RLS + 越权矩阵测试（§14） |
| ASR-2 | 滚动发布期间持续有流量 | 5xx <0.1%，p95 不劣化 >20% | 无状态 + readiness 摘除 + N-1 兼容（§16） |
| ASR-3 | 迁移加字段并改读路径 | 零停机，可回滚 | expand-contract 三步（§16） |
| ASR-4 | 导入 5000 行 + 触发全量重算 | 导入 API 响应 ≤1s（202），任务 ≤60s | 队列 + 后台 worker + 幂等重算（§10） |
| ASR-5 | 行情源全天不可用 | 回合/费用/行为仍可用；资产类标 `stale` | 降级矩阵 + `data_as_of`（Reliability §7） |
| ASR-6 | 口径变更（卖飞率改 z 分数） | 旧结果 100% `stale`，趋势断点 | `definition_version` + DAG 失效（§11） |
| ASR-7 | 新增一个指标 | 0 处既有文件改动 | 注册表 + 契约测试（Extensibility EP-1） |
| ASR-8 | PG 主库宕机 | RTO ≤30min，RPO ≤5min | WAL 归档 PITR + 恢复 SOP（Reliability §4） |
| ASR-9 | 前端与后端版本错位 | 旧 SPA + 新 API 不报错 | 兼容承诺 N-1 + DTO 演进规则（§12） |
| ASR-10 | LLM 不可用 | 金额类问题仍可答 | 后端填充 + 模板降级（§13） |

### 1.3 约束

| 编号 | 约束 |
|---|---|
| C1 | 数据库固定 PostgreSQL 16；不引入其他数据库 |
| C2 | 前后端分离：React+TS / Python；nginx 同源反代（规避 CORS 与三方 Cookie） |
| C3 | 重后轻前：业务能力全在后端，前端零口径 |
| C4 | Monorepo：双 workspace + 契约快照同仓 |
| C5 | 不引入 Redis / Kafka / K8s（队列用 PG，编排用 Compose） |
| C6 | 金额/收益禁 float；口径版本化可复现 |
| C7 | 合规：不构成投资建议；注销即物理删除 |
| C8 | 可观测：日志/指标/trace + health/ready |
| C9 | 单人开发：复杂度预算有限，优先"少而深" |

---

## 2. 【先行】可验收 API 清单

> **本章是本次修订的核心交付**：先把"什么叫完成"定义成可脚本执行的 HTTP 判据，再谈实现。完整契约（请求/响应字段、错误码、示例）见 [v2/API-Catalog.md](architecture/v2/API-Catalog.md)；本文给出**全量端点清单 + 验收规则 + 治理机制**。

### 2.1 API 设计总则

| 编号 | 规则 | 说明 |
|---|---|---|
| A1 | 前缀与版本 | `/api/v1`；破坏性变更升 `/api/v2`，`/v1` 保留至少一个大版本 |
| A2 | 契约单源 | OpenAPI 3.1 由后端生成 → 快照 `openapi/openapi.json` → 前端 SDK 生成。**禁止手写请求类型** |
| A3 | 认证 | `Authorization: Bearer <token>`，token 为 access JWT（15min）或 PAT（scope `read`/`write`）；refresh 轮换 |
| A4 | 幂等 | 所有写操作接受 `Idempotency-Key`；同键同 body 重放返回同一响应/`job_id`；同键不同 body → 422 |
| A5 | 异步 | 长任务一律 `202 Accepted` + `Location: /api/v1/jobs/{id}`；进度用轮询或 `GET /jobs/{id}/events`（SSE） |
| A6 | 分页 | 游标式：`limit`（默认 50，上限 200）+ `cursor` → `next_cursor`（不透明）。禁止 offset 深翻 |
| A7 | 过滤/排序/裁剪 | 过滤显式白名单；`sort` 白名单，非法值 400；`fields=` 字段裁剪，减少胖响应 |
| A8 | 错误 | RFC 7807 + `code / user_message / what_to_do / retryable / request_id`；**`user_message` 必填** |
| A9 | 越权语义 | 跨用户资源一律 **404**（不泄露存在性）；`admin` 访问业务明细 403 |
| A10 | 缓存 | 报告/指标快照支持 `ETag` + `If-None-Match` → 304 |
| A11 | 限流 | 按 user/PAT 限流，返回 `429` + `Retry-After` + `X-RateLimit-*` |
| A12 | 可追溯 | 每个响应含 `X-Request-Id`；指标类响应含 `data_as_of`、`definition_version` |

### 2.2 端点全量清单

> API-ID（`API-001` 起）由《API-Catalog》分配；本表按域分组，**P0 = M0/M1 必须**。

**① 认证与账号（FR-L2）**

| 方法 | 路径 | 用途 | 优先级 | 同步/异步 |
|---|---|---|---|---|
| POST | `/api/v1/auth/register` | 注册 | P0 | 同步 |
| POST | `/api/v1/auth/login` | 登录 → access + refresh | P0 | 同步 |
| POST | `/api/v1/auth/refresh` | 轮换刷新 | P0 | 同步 |
| POST | `/api/v1/auth/logout` | 撤销当前 refresh 家族 | P0 | 同步 |
| GET | `/api/v1/auth/me` | 当前身份与角色 | P0 | 同步 |
| DELETE | `/account` | 注销（物理删除 + 审计） | P0 | 同步 |

**② 程序化访问（FR-A1）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| POST | `/api/v1/tokens` | 创建 PAT（明文仅返回一次） | P0 |
| GET | `/api/v1/tokens` | 列表（仅前缀与元信息） | P0 |
| DELETE | `/api/v1/tokens/{id}` | 撤销 | P0 |

**③ 导入（FR-L3）**

| 方法 | 路径 | 用途 | 优先级 | 同步/异步 |
|---|---|---|---|---|
| POST | `/api/v1/imports/preview` | 解析 + 列映射建议 + 预检（**不落业务数据**） | P0 | 同步 |
| POST | `/api/v1/imports` | 提交导入（幂等） | P0 | 异步 202 |
| GET | `/api/v1/imports` | 批次列表 | P0 | 同步 |
| GET | `/api/v1/imports/{batch_id}` | 批次详情（新增/跳过/隔离数） | P0 | 同步 |
| POST | `/api/v1/imports/{batch_id}/revoke` | 撤销批次（同步软删 + 触发指标失效） | P0 | 同步 |
| GET | `/api/v1/imports/{batch_id}/quarantine` | 隔离区明细 | P0 | 同步 |
| POST | `/api/v1/imports/{batch_id}/reclassify` | 按新规则重跑分类 | P1 | 异步 |

**④ 流水与规则（FR-L4/L5/L8）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| GET | `/api/v1/trades` | 游标分页查询（过滤/排序/裁剪） | P0 |
| GET | `/api/v1/trades/{id}` | 单行详情 | P0 |
| PATCH | `/api/v1/trades/{id}` | 修正（`If-Match` 版本冲突 409） | P0 |
| DELETE | `/api/v1/trades/{id}` | 软删 | P0 |
| POST | `/api/v1/trades/{id}/restore` | 恢复 | P0 |
| GET | `/api/v1/trades/{id}/history` | 修改历史 | P1 |
| GET | `/api/v1/summary-rules` | 分类规则列表 | P0 |
| POST | `/api/v1/summary-rules` | 新建规则 | P1 |
| PATCH | `/api/v1/summary-rules/{id}` | 改规则 | P1 |
| DELETE | `/api/v1/summary-rules/{id}` | 删规则 | P1 |

**⑤ 对账（FR-L6）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| GET | `/api/v1/reconciliation` | 区间状态 + 断点明细 | P0 |
| GET | `/api/v1/reconciliation/bridge` | 桥接恒等式校验结果 | P0 |

**⑥ 度量（FR-R1~R8）**

| 方法 | 路径 | 用途 | 优先级 | 同步/异步 |
|---|---|---|---|---|
| GET | `/api/v1/rounds` | 回合列表 | P0 | 同步 |
| GET | `/api/v1/rounds/{id}` | 回合详情 | P0 | 同步 |
| GET | `/api/v1/rounds/{id}/fills` | FIFO 配对明细（可追溯） | P0 | 同步 |
| GET | `/api/v1/nav` | 净值序列 + 汇总（TWR/MWR/β/α/IR） | P0 | 同步 |
| GET | `/api/v1/metrics` | 批量取指标（含 n/CI/状态） | P0 | 同步 |
| GET | `/api/v1/metrics/{metric_id}` | 单指标详情（完整 `MetricResult`，含血缘与状态） | P0 | 同步 |
| GET | `/api/v1/metrics/{metric_id}/explain` | 三段式（结论/算法/下钻） | P0 | 同步 |
| GET | `/api/v1/metrics/{metric_id}/drilldown` | 涉及哪几笔 | P0 | 同步 |
| GET | `/api/v1/attribution` | 五因子 / Brinson | P0 | 同步 |
| GET | `/api/v1/trends` | 纵向趋势（含口径断点） | P1 | 同步 |
| POST | `/api/v1/diagnostics/selection` | 触发选股诊断（PIT/BHAR） | P0 | 异步 202 |
| POST | `/api/v1/jobs/rebuild-rounds` | 重建回合 | P0 | 异步 202 |

**⑦ 报告（FR-D2/D8）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| POST | `/api/v1/reports` | 生成月报（期间幂等） | P0 |
| GET | `/api/v1/reports` | 报告列表 | P0 |
| GET | `/api/v1/reports/{id}` | 报告元数据 | P0 |
| GET | `/api/v1/reports/{id}/download` | 下载 HTML/PDF | P0 |
| POST | `/api/v1/reports/{id}/shares` | 创建分享链接 | P2 |
| GET | `/api/v1/shares/{share_token}` | 匿名访问分享（受限） | P2 |
| DELETE | `/api/v1/shares/{share_id}` | 撤销分享 | P2 |

**⑧ 日志与提醒（FR-D4/D6）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| GET/POST | `/api/v1/journal` | 日志列表 / 新建 | P0 |
| PATCH/DELETE | `/api/v1/journal/{id}` | 修改 / 删除 | P0 |
| GET | `/api/v1/journal/prompts` | 待对照提示 | P1 |
| GET | `/api/v1/journal/summary` | 季度"我说的 vs 实际" | P1 |
| GET | `/api/v1/alerts` | 提醒列表 | P1 |
| POST | `/api/v1/alerts/{id}/ack` | 确认提醒 | P1 |
| GET | `/api/v1/notifications` | 站内通知列表（FR-D8） | P2 |

**⑨ 设置与导出（FR-L7、FR-D3/D6、隐私开关）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| GET | `/api/v1/settings` | 展示偏好与默认基准 | P0 |
| PUT | `/api/v1/settings/benchmark` | 基准切换（带 `benchmark_id`） | P0 |
| PUT | `/api/v1/settings/privacy` | 隐私开关（LLM 可见金额等） | P0 |
| GET/PUT | `/api/v1/settings/behavior-rules` | 行为规则阈值 | P1 |
| POST | `/api/v1/exports` | 导出全量（异步） | P0 |
| GET | `/api/v1/exports` | 导出任务列表 | P0 |
| GET | `/api/v1/exports/{id}/download` | 下载（有效期/410） | P0 |

**⑩ 任务与对话（FR-D7）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| GET | `/api/v1/jobs` | 我的任务列表 | P0 |
| GET | `/api/v1/jobs/{id}` | 任务状态/进度/结果引用 | P0 |
| POST | `/api/v1/jobs/{id}/cancel` | 取消（可取消态才允许） | P0 |
| GET | `/api/v1/jobs/{id}/events` | SSE 进度（`Last-Event-ID` 续传） | P1 |
| POST | `/api/v1/chat/sessions` | 新建会话 | P1 |
| GET | `/api/v1/chat/sessions` | 会话列表 | P1 |
| POST | `/api/v1/chat/sessions/{id}/messages` | 提问（含 citations） | P1 |

**⑪ 审计、管理与运维（FR-A4、FR-L1、NFR-15）**

| 方法 | 路径 | 用途 | 优先级 |
|---|---|---|---|
| GET | `/api/v1/audit-logs` | 本人审计 | P1 |
| GET | `/api/v1/admin/audit-logs` | 系统级事件审计（无明细） | P1 |
| GET | `/api/v1/admin/system/status` | 系统状态（不含交易明细） | P1 |
| POST | `/api/v1/admin/backups` | 触发备份 | P1 |
| GET | `/api/v1/admin/backups` | 备份列表与校验结果 | P1 |
| POST | `/api/v1/admin/restore-drills` | 恢复演练（演练用备份） | P1 |
| GET | `/healthz` | 存活探针 | P0 |
| GET | `/readyz` | 就绪（DB + 迁移版本） | P0 |
| GET | `/version` | 版本与 schema/口径版本 | P0 |
| GET | `/metrics` | Prometheus 文本（内网） | P1 |
| GET | `/api/v1/openapi.json` | 契约自描述 | P0 |
| GET | `/api/v1/errors` | 机器可读错误码总表 | P1 |

**规模**：**80 个端点**（API-001~080），覆盖 28 条 FR（权威清单与字段级契约见 [API-Catalog §1](architecture/v2/API-Catalog.md)；覆盖关系见 §19 与 API-Catalog §8）。

> 路径约定：本节用**完整路径**；《API-Catalog》§1 省略公共前缀 `/api/v1`（运维端点 `/healthz`、`/readyz`、`/version`、`/metrics` 不在前缀下）。两处端点集合一致，以 API-Catalog 为准。

### 2.3 验收判定规则（"完成"的定义）

| 层级 | 断言内容 | 示例 |
|---|---|---|
| L1 契约 | 状态码、`Content-Type`、必填字段、错误结构 | `POST /imports` → `202` 且 `Location` 可解析 |
| L2 数据 | 业务值在容差内、状态机终态正确 | 回合 `pnl` 与 fixture 期望差额 ≤0.01 元 |
| L3 副作用 | 数据库真实变化、幂等重放不重复写入、审计留痕 | 同 `Idempotency-Key` 重放 → 行数不变 |

**门禁**：
1. **端点覆盖率 100%**：每个端点至少一条验收用例，CI 统计 `covered_endpoints / total_endpoints == 1.0`，否则阻断。
2. **OpenAPI 检查 100%**：每个端点有 `operationId`、成功示例、错误码枚举、请求/响应 schema；缺失即阻断（FR-A2）。
3. **越权负向 100%**：每个受保护端点必须有跨租户用例（§14、Testing §4）。
4. **契约快照 diff**：破坏性变更未升版本 → 阻断。

### 2.4 契约治理

```
FastAPI 路由 + Pydantic DTO
        │ 生成
        ▼
openapi/openapi.json（仓库快照，CI 校验 diff）
        ├──▶ frontend/packages/api-client（生成式 SDK，禁止手写类型）
        ├──▶ Schemathesis 契约测试（用真实 PG 跑属性测试）
        └──▶ docs / 对外契约
```

| 变更类型 | 是否 breaking | 处置 |
|---|---|---|
| 新增可选请求字段 / 新增响应字段 | 否 | 直接发布 |
| 新增枚举值（消费方需容忍） | 否（约定） | 发布 + 前端 `default` 分支 |
| 删除/重命名字段、改类型、改语义、改必填 | **是** | 升 `/api/v2` 或新增字段并保留旧字段 N-1 |
| 改 `operationId` | 是（SDK 破坏） | 视为 breaking |
| 新增错误码 | 否 | 前端需按 `code` 兜底 |

---

## 3. 质量属性的服务级重定义

### 3.1 高可用（这次是真的服务级）

| 目标 | 可测定义 | 机制 | 详述 |
|---|---|---|---|
| 服务在线 | **月度可用性 ≥99.9%**（错误预算 ≈43min/月） | api ≥2 副本 + readiness + 滚动发布 + 连接池 | Reliability §1 |
| 数据不丢 | **RPO ≤5min**（WAL 归档）；每日全量 + PITR | WAL 归档、备份校验、恢复演练 | Reliability §4 |
| 故障可恢复 | **RTO ≤30min**；季度演练通过率 100% | PITR SOP + 快照 + 回滚脚本 | Reliability §11 |
| 降级可用 | 依赖故障时核心读写仍可用，且**降级必须可见** | 降级矩阵 + 状态标注 | Reliability §7 |
| 零停机发布 | 滚动升级期间 5xx <0.1% | 无状态 + expand-contract + N-1 兼容 | §16 |

**仍不做的**：K8s、多活、跨地域、自动故障转移（首版）。理由与重新评估触发条件见 ADR-0013 与 Extensibility 反扩展清单。**首版是单机多容器：主机级故障会中断服务**——这一点必须诚实标注（Reliability §2）。

### 3.2 高测试覆盖（多了一层"契约"）

| 层 | 行/分支 | 门禁 | 理由 |
|---|---|---|---|
| `backend/packages/core` | **100% / 100%**（变异 ≥80%） | 硬 | 口径错误代价最高 |
| `backend/packages/contracts` | 98% / 96% | 硬 | 跨上下文 DTO 校验器 |
| `*.domain` | 98% / 96% | 硬 | 不变量 |
| `*.application` | 95% / 90% | 硬 | 失败路径与鉴权分支 |
| `backend/services/api` | 行覆盖**软**；**端点覆盖 100% + 每条路由必须声明 `CurrentUser` 依赖** | 硬（结构性） | 路由以接线为主，用契约与依赖声明保证安全，而非堆行覆盖 |
| `*.infra` | 85% / 80%（新增行 ≥90%） | 软 + diff 硬 | 以仓储集成 + 回放为主 |
| `backend/packages/platform` | 92% / 88% | 硬 | 队列/租约/PAT scope/RLS 会话注入，错则静默毁数据 |
| `frontend/apps/web` | 80% / 70% | 软 | 以组件 + e2e 为主 |
| `frontend/packages/api-client` | 排除 | — | 生成代码 |
| **OpenAPI 端点验收覆盖** | **100%** | 硬 | 本次修订的验收主体 |
| **越权负向覆盖** | **100% 受保护端点** | 硬 | 多租户最严重风险 |
| 整体 / diff | ≥90% / ≥85%；diff ≥95% | 告警 / 硬 | 防"整体好看、新代码裸奔" |

### 3.3 高扩展性（多了"多租户"和"API 版本"两个维度）

| 变更类型 | 允许触碰 | 禁止触碰 | 预算 |
|---|---|---|---|
| 新增指标 | `review/metrics/defs/*.py` 1 文件 | core 引擎、API、前端 | 0 处既有文件改动 |
| 新增券商账单 | `ledger/parsers/*.py` + 注册 1 行 | 导入主流程 | ≤1 文件 / ≤1 人日 |
| 新增数据源 | 1 个 Protocol 实现 | 计算层 | ≤1 文件 / ≤2 人日 |
| 新增报告区块 | 1 个 `ReportBlock` + 1 个模板 | 渲染框架 | ≤2 文件 |
| 新增前端页面 | `apps/web/src/pages/` + 路由注册 | 后端 | 不改后端 |
| API 破坏性变更 | 新版本路由 + 迁移说明 | 改 `/v1` 语义 | 走 ADR + 版本 |

**明确不做**：多租户计费/席位、团队租户（仅数据模型预留）、微前端、GraphQL 网关、插件热加载第三方包。

---

## 4. 系统上下文（C4 L1）

```
   浏览器(React SPA) ─┐
                      ├─ HTTPS ─▶ [nginx] ─▶ [api ×N] ─┐
   API 客户端/脚本/CI ─┘              │                  ├─▶ PostgreSQL 16
   (Bearer PAT)                       └─▶ [worker ×M] ──┘   (ledger/review/platform)
                                                             │
   admin(运维) ──────────────────────────────────────────────┤
                                                             ▼
                                        外部可选依赖：akshare 行情 / AlphaFeed(灾备) / LLM
                                        外部：备份存储（本地卷或对象存储，端口抽象）
```

| 外部系统 | 方向 | 失败影响 | 应对 |
|---|---|---|---|
| 浏览器 / API 客户端 | 入 | — | 认证、限流、契约 |
| PostgreSQL | 内 | 全站不可用 | 副本、PITR、就绪探针 |
| 行情源（akshare/AlphaFeed） | 出（可选） | 行情类指标不可用 | 适配器 + 缓存 + 降级 + `data_as_of` |
| LLM | 出（可选） | 对话质量下降 | 后端模板降级；金额不出站 |
| 备份存储 | 出 | RPO 劣化 | 告警 + 本地兜底 |

---

## 5. 容器视图（C4 L2）

```
Docker Compose（云主机）
┌──────────────────────────────────────────────────────────────────────┐
│  nginx :443                                                          │
│   ├── /            → 前端静态资源（SPA dist）                          │
│   └── /api,/healthz,/readyz,/metrics → upstream api:8000              │
│                                                                       │
│  api ×N  (uvicorn+gunicorn workers, 无状态)                            │
│   ├─ 中间件链: request_id → 认证 → 租户上下文 → 限流 → 路由            │
│   ├─ 同步读路径: 直接查 PG（游标分页 + ETag）                          │
│   └─ 长任务: 写 platform.job → 202                                     │
│                                                                       │
│  worker ×M  (同镜像不同 entrypoint)                                    │
│   └─ SELECT ... FOR UPDATE SKIP LOCKED → 租约 → 执行 → 提交 → 发 outbox │
│                                                                       │
│  postgres 16  (schemas: ledger / review / platform, RLS)              │
│  migrate      (一次性: alembic upgrade head)                          │
│  volumes: pgdata / data(归档·报告·导出) / logs                         │
└──────────────────────────────────────────────────────────────────────┘
```

| 为什么不是 K8s / 微服务 | 收益 | 代价 | 结论 |
|---|---|---|---|
| 上 K8s | 自动扩缩、自愈 | 运维复杂度 ≫ 单人可维护预算 | ❌ 首版；触发条件写入 ADR-0013 |
| 拆微服务 | 独立部署 | 分布式事务、跨服务鉴权、调试成本 | ❌ 模块化单体 + 两个可部署单元（api/worker）已够 |
| 引入 Redis/Kafka | 队列与缓存 | 多一个必须运维的有状态组件 | ❌ PG `SKIP LOCKED` + 物化表足够 |

**关键判断**：`api` 与 `worker` 是**同一代码库的两个部署单元**（不是微服务）——共享 domain/application，只在入口与并发模型上不同。

---

## 6. 代码结构（Monorepo 双栈）

```
stock-analyze/
├── pyproject.toml                 # uv workspace root（backend）
├── pnpm-workspace.yaml            # pnpm workspace root（frontend）
├── Makefile                       # 统一入口：make dev / test / lint / api-check / e2e
├── openapi/
│   └── openapi.json               # ★ 契约快照（单一事实源，CI 校验 diff）
├── backend/                       # ── Python（uv workspace members）
│   ├── packages/
│   │   ├── contracts/             # Pydantic DTO、枚举、错误码、常量（容差/年化基数/最小样本）
│   │   ├── core/                  # ★ 纯计算内核：values/fifo/nav/metrics/attribution/stats
│   │   ├── ledger/                # 数据底座：domain/application/infrastructure
│   │   ├── review/                # 度量层
│   │   ├── delivery/              # 报告区块、对话、导出
│   │   └── platform/              # 认证、租户、队列、outbox、配置、观测、备份
│   ├── services/
│   │   ├── api/                   # FastAPI：routers/middlewares/deps/errors
│   │   └── worker/                # 任务消费者入口
│   └── tests/                     # unit/integration/api/contract/architecture
├── frontend/                      # ── TypeScript（pnpm workspace）
│   ├── apps/web/                  # React SPA：pages/components/features/lib
│   └── packages/api-client/       # ★ 由 openapi.json 生成的 SDK
├── infra/
│   ├── compose/docker-compose.yml
│   ├── nginx/nginx.conf
│   └── .env.example
├── fixtures/                      # golden fixtures（bills/market/expected）
├── tests/e2e/                     # Playwright
└── docs/
```

### 6.1 分层铁律（后端）

```
services/api ─▶ application ─▶ domain ◀── infrastructure
     │              │            ▲              │
     └──── contracts ┴────────────┴──────────────┘
              platform（横切：auth/tenant/jobs/events/obs）
```

| 规则 | 内容 | 强制手段 |
|---|---|---|
| L1 | `core`/`domain` 零 I/O、零全局态、零 wall-clock | import-linter + AST 扫描测试 |
| L2 | application 只依赖 domain + ports + contracts | import-linter layers |
| L3 | infrastructure 实现 ports，不被 application 直接 import | import-linter independence |
| L4 | API 路由不直接访问 ORM | 架构测试 |
| L5 | 跨上下文只经 contracts DTO 或事件 | independence 契约 |
| L6 | **前端零口径**：`frontend/` 不得实现指标计算或结论拼接 | ESLint 规则 + AST 断言（Testing §7） |

---

## 7. 上下文职责

| 上下文 | 职责 | 聚合根 | 关键不变量 |
|---|---|---|---|
| **Ledger** | 导入两阶段（含全零金额噪音行隔离）、去重、分类、软删、对账（断链自动补差）、批次撤销、导出 | `ImportBatch`、`Trade` | 同租户 `(dedupe_key, occurrence_idx)` 唯一；软删不参与下游 |
| **Review** | 期初、FIFO 回合、净值、三域指标、归因、可信度 | `Round`、`MetricResult` | 桥接恒等式；`blocked` 不出结论 |
| **Platform** | 用户/Token/会话、租户上下文、任务队列、限流、审计、配置、备份 | `User`、`Job` | 任何业务读写必须带 `user_id`；admin 不可读明细 |
| **Delivery** | 报告区块、对话编排、导出包 | `Report` | 只消费结构化产物，零口径 |
| **Core** | 纯计算内核（被 ledger/review 复用） | — | 确定性、无 I/O、标度整数 |

---

## 8. 端口与适配器

| 端口 | 定义位置 | 生产实现 | 测试替身 |
|---|---|---|---|
| `TradeRepository` / `ImportBatchRepository` | `ledger/domain/ports.py` | SQLAlchemy(async) | 内存 + 真 PG 集成 |
| `SummaryRuleRepository` | 同上 | SQLAlchemy | 同上 |
| `RoundRepository` / `NavRepository` / `MetricRepository` / `AttributionRepository` | `review/domain/ports.py` | SQLAlchemy | 同上 |
| `ReportRepository` / `JournalRepository` / `AlertRepository` | `delivery/domain/ports.py` | SQLAlchemy | 同上 |
| `UserRepository` / `TokenRepository` / `AuditRepository` | `platform/domain/ports.py` | SQLAlchemy | 同上 |
| `MarketDataSource` | `review/domain/ports.py` | akshare / AlphaFeed | 录制回放 |
| `BillParser` | `ledger/domain/ports.py` | 华泰解析器（注册表） | FixtureParser |
| `LlmProvider` / `ToolGateway` | `delivery/domain/ports.py` | GLM / 本地 | Echo / 录制 |
| `JobQueue` | `platform/ports.py` | PG `SKIP LOCKED` | 同步执行器 |
| `EventBus` | `platform/ports.py` | PG outbox + 消费者 | 内存收集器 |
| `RateLimiter` | `platform/ports.py` | PG 计数桶 | 固定时钟 |
| `TenantContext` | `platform/ports.py` | 请求作用域（含 RLS 会话变量） | 显式上下文 |
| `PasswordHasher` | `platform/ports.py` | Argon2id | 快速替身（仅测试） |
| `Clock` | `platform/ports.py` | SystemClock | FrozenClock |
| `ObjectStore` / `BackupTarget` | `platform/ports.py` | 本地卷（端口预留 S3） | 临时目录 |

---

## 9. 数据架构

### 9.1 多租户模型

| 决策 | 内容 | 理由 |
|---|---|---|
| 隔离模型 | **共享库 + 共享 schema + `user_id` 列 + RLS** | 100 用户量级，运维最简；RLS 提供第二道防线 |
| 租户 ID | `user_id`（UUID）；一用户即一租户 | 与 PRD §4.4 一致；团队租户演进路径见 Extensibility §4 |
| RLS | 所有租户表 `ENABLE ROW LEVEL SECURITY`；策略 `USING (user_id = current_setting('app.user_id')::uuid)` | 即使仓储漏过滤也读不到他人数据 |
| 会话变量 | 每请求事务内 `SET LOCAL app.user_id = :uid`，连接归还前重置 | 防连接池串号（关键安全点） |
| 管理面 | `platform.user` 等系统表不进租户策略；admin 仅访问系统视图 | 满足"admin 不可读交易明细" |

### 9.2 schema 与表清单（v1 的 31 张 → v2 的 36 张）

**`platform`（系统与多租户）**

| 表 | 关键列 | 说明 |
|---|---|---|
| `user` | id, email(citext unique), password_hash, role, status, email_verified_at, created_at, deleted_at | 账号 |
| `refresh_token` | id, user_id, token_hash, family_id, expires_at, revoked_at, replaced_by | 轮换与复用检测 |
| `api_token` | id, user_id, name, token_prefix, token_hash, scopes[], expires_at, last_used_at, revoked_at | PAT |
| `job` | id, user_id, kind, scope_key, status, params, attempt, max_attempts, available_at, lease_until, heartbeat_at, progress, result_ref, error_code | 队列（`SKIP LOCKED`） |
| `idempotency_key` | user_id, key, endpoint, request_hash, response_ref, created_at, expires_at | 幂等 |
| `outbox_event` | id, type, aggregate, payload, occurred_at, published_at, attempts | 事件 |
| `audit_log` | id, actor_id, action, target_type, target_id, meta, request_id, at | 审计 |
| `user_setting` | user_id, benchmark_id, privacy_flags, display_prefs, version | 用户设置 |
| `config_release` | id, key, value, version, applied_at | 配置版本化 |
| `rate_limit_bucket` | key, window_start, count | 限流 |
| `llm_session` / `llm_message` / `llm_call` | session_id, user_id, role, content_ref, redaction_applied, tokens, latency_ms | 对话与留痕 |
| `share_link` | id, report_id, token_hash, expires_at, revoked_at | 报告分享 |
| `backup_run` | id, started_at, finished_at, kind, location, checksum, verified, detail | 备份 |
| `alembic_version` / `schema_version` | — | 迁移与应用版本 |

**`ledger`**：`trade`、`trade_history`、`import_batch`、`quarantine_row`、`summary_rule`（系统默认 + 用户覆盖）、`reconciliation`、`provenance`、`export_log`（均含 `user_id`）

**`review`**：`round`、`round_fill`、`nav_daily`、`cashflow`、`metric_result`、`metric_dependency`、`attribution`、`report`、`journal_entry`、`behavior_rule`、`alert`（均含 `user_id`）

### 9.3 关键索引与分区

| 索引/分区 | 目的 |
|---|---|
| `trade (user_id, trade_date, id)` | 区间扫描、净值构建 |
| `trade (user_id, security_code, trade_date)` | 单标的重放 |
| unique `(user_id, dedupe_key, occurrence_idx)` where `deleted_at is null` | 幂等导入 |
| `metric_result` unique `(user_id, metric_id, definition_version, scope_type, scope_key, period_start, period_end)` | 物化去重 |
| `metric_result (user_id, status)` | 失效扫描 |
| `job` 部分唯一 `(kind, scope_key) where status in ('pending','running')` | 单飞 |
| `job` 索引 `(status, available_at)` | 出队 |
| `nav_daily` PK `(user_id, date)` | 时序 |
| `trade` 按 `trade_date` 年分区（≥500 万行后启用） | 大表治理 |

### 9.4 关键设计（沿用 v1 的教训）

- **标度整数**：金额分、价格 1e-6、数量 1e-4、比率 1e-8；API 边界转定点字符串。
- **去重**：`dedupe_key` 不含资金余额；空股东账号归一哨兵；同键多笔按出现序号 bag 去重。
- **软删 vs 物理删除**：业务软删（可恢复）；**注销即物理删除该用户全部数据**（级联 + 审计），PRD §4.4。
- **血缘**：`metric_result` 携带 `definition_version / inputs_hash / data_as_of / n / assumptions`。
- **敏感字段**：股东账号静态加密、接口脱敏（末 4 位）；密码 Argon2id；Token 只存哈希。

---

## 10. 异步任务与工作流

### 10.1 队列模型

```sql
-- 出队（原子认领）
UPDATE platform.job SET status='running', attempts=attempts+1,
       lease_until = now() + interval '60 seconds', started_at=now()
WHERE id = (
  SELECT id FROM platform.job
  WHERE status='pending' AND available_at <= now()
  ORDER BY priority DESC, available_at
  FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```

| 机制 | 设计 |
|---|---|
| 租约 | `lease_until` 60s；worker 每 15s 续租；过期由回收任务置回 `pending`（attempts+1） |
| 重试 | 指数退避 `delay = min(2^attempt × 5s, 10min)` + 抖动；`max_attempts` 后进 `failed`（毒任务隔离） |
| 幂等 | 任务以 `(kind, scope_key, 输入指纹)` 为幂等键；重跑覆盖同键结果 |
| 单飞 | 部分唯一索引保证同 `(kind, scope_key)` 只有一个 `pending/running` |
| 取消 | `pending` 可立即取消；`running` 置 `cancel_requested`，worker 在阶段边界检查并中止 |
| 进度 | `progress` 0–100 + `progress_detail`；SSE 推送 |
| 投递事件 | 任务完成写 `outbox_event`，由消费者派发（页面失效/提醒/报告就绪） |

### 10.2 任务类型与触发

| 任务 kind | 触发 | 幂等键 |
|---|---|---|
| `import_commit` | `POST /imports` | `(batch_id)` |
| `import_revoke` | `POST /imports/{id}/revoke` | `(batch_id)` |
| `rebuild_rounds` / `rebuild_nav` | 导入后事件 / 手动 | `(user_id, period)` |
| `diagnose_selection` / `diagnose_timing` / `diagnose_behavior` | API 触发 | `(user_id, kind, period, 口径版本)` |
| `compute_metrics` / `compute_attribution` | 上游完成事件 | `(user_id, period, 指标组)` |
| `generate_report` | `POST /reports` | `(user_id, period, spec_hash)` |
| `export_data` | `POST /exports` | `(user_id, scope)` |
| `backup` | 定时（每日）/ 管理员 | `(date)` |

### 10.3 导入流水线（两阶段）

```
[preview]  上传 → 解析 → 列映射建议 → 质量预检（缺列/首行非买入/未知摘要/重复行）
              └─ 返回预览（不落业务数据）
[commit]   202 job → 规范化 → dedupe_key → bag 去重 → 分类 → 隔离区 → 单事务落库
              → 余额链对账 → 写 outbox(BatchImported) → 触发重建链
```

**设计要点**：preview 与 commit 共享同一解析实现（避免两套逻辑漂移）；commit 的事务边界是"一个批次"，保证 ASR-1；stages 各自纯函数，便于 fixture 测试。

**噪音行与补差行（v2.1）**：① 解析阶段（preview 与 commit 共用同一实现、早于去重）即判「佣金/印花税/其他费/发生金额/资金余额」五字段全为 0 的行 → `INVALID_AMOUNT` 隔离、不写 `trade`（零资金流噪音行既污染回合/费用口径，又会被误判成 `DUPLICATE_IN_FILE`）；② 余额链对账检出断链时写入 `side=missing`、`source=system` 的补差流水，一次补差后链闭合、重复调用幂等；此类断链的对账状态为 `degraded`（不再是 `blocked`），补差行以 `batch_id=null` / `summary=对账自动补差` 与真实成交区分。

### 10.4 计算链与脏窗口

```
BatchImported / TradesChanged / 口径变更 / 定时
   → rebuild_rounds（脏窗口） → rebuild_nav → compute_metrics（DAG） → compute_attribution
   → 写 metric_result（含血缘） → 页面/报告缓存失效
```

脏窗口规则（沿用 v1 的正确性保险丝）：从最早变动日回退 `max(20 交易日, 最长持仓期)`；口径 MAJOR 变更则全量；`data_as_of` 前移从生效日算起。**多租户下按 `user_id` 分片执行**，单用户作业互不阻塞。

---

## 11. 指标执行引擎

与 v1 同构（这是刻意保留的资产），只增加租户与异步维度：

| 环节 | v2 设计 |
|---|---|
| 注册 | `MetricDef` 13 字段；`backend/packages/review/src/review/metrics/defs/` 自动发现 |
| 契约 | `compute()` 只返回 `MetricValue`，**拿不到 status**；`n/ci/sample_status/status/inputs_hash` 框架代算 |
| scope | `scope_key` 含 `user_id`，保证不同用户结果不串 |
| 执行 | DAG 拓扑 + 按 `user_id` 并行（worker 池），结果批量写 PG |
| 失效 | `definition_version`（MAJOR.MINOR）或 `inputs_hash` 变化 → `stale`；MAJOR → 趋势断点 |
| 门禁 | `ok / stale / blocked / unreliable`；**API 层在非 ok 时不返回 `value` 与 `conclusion`**（FR-R8、FR-L6） |

> 为什么门禁做在 API 层而不只在前端：v2 的验收主体是 API。**API 必须保证"不可信的数字不出现"**，否则任何客户端（脚本/第三方）都会拿到脏数据。前端守卫是第二道，不是第一道。

---

## 12. 前端架构（React，薄客户端）

### 12.1 技术选型

| 关注点 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript(strict) | 生态成熟、类型安全 |
| 构建 | Vite | 快、零配置、静态产物 |
| 数据层 | TanStack Query | 缓存/重试/失效与"异步任务轮询"天然契合 |
| 路由 | React Router | 标准 |
| 图表 | ECharts（按需引入） | 金融图表能力 |
| 样式 | Tailwind（仅工具类，不自研组件库） | 轻前 |
| API 客户端 | **由 `openapi.json` 生成** | 禁止手写类型，杜绝漂移 |
| 测试 | Vitest + Testing Library + MSW；Playwright | 组件 + 契约 mock + 关键路径 |

### 12.2 结构

```
frontend/apps/web/src/
├── app/           # 路由、Provider、全局错误边界
├── pages/         # dashboard / rounds / round-detail / diagnostics / reconciliation
│                  # imports / reports / journal / settings / tokens / audit / login
├── features/      # 按领域切片：hooks + 视图模型（不含口径）
├── components/    # 表格、图表容器、状态徽标（ok/stale/blocked）、下钻抽屉
├── lib/           # api-client 封装、query keys、错误映射、格式化（金额/比率展示）
└── guards/        # 认证守卫、权限守卫、降级态守卫
```

### 12.3 关键约束与模式

| 约束/模式 | 做法 |
|---|---|
| **零口径** | 指标计算与结论文案一律来自 API（`value` + `conclusion` + `method`）；ESLint + AST 断言禁止在前端出现计算逻辑 |
| 认证 | access token **仅存内存**；refresh 走 httpOnly cookie（同源）；401 → 静默刷新一次 → 失败跳登录 |
| 异步任务 | `202` → 打开任务抽屉 → 轮询（2s 退避到 10s）或 SSE；离开页面不中断 |
| 降级态 | `status != ok` 的卡片渲染占位 + 原因 + `what_to_do`；**组件层面禁止渲染数值** |
| 错误 | 统一错误边界读 `code/user_message/what_to_do`；按 `code` 做差异提示 |
| 性能 | 路由级代码分割；ECharts 按需；首屏关键数据并行拉取；列表虚拟滚动 |

### 12.4 前后端版本兼容窗口

| 场景 | 期望 | 机制 |
|---|---|---|
| 新 API + 旧 SPA | 不崩 | DTO 只新增可选字段；旧 SPA 忽略未知字段 |
| 旧 API + 新 SPA（回滚） | 不崩 | 新 SPA 不依赖未上线的字段；部署顺序：先 API 后 SPA |
| 破坏性变更 | 走 `/api/v2` | 双版本并行至少一个大版本 |

---

## 13. 对话子系统（P1）

```
用户提问 → ToolGateway（白名单只读工具）→ 后端取数（含金额，本地）
        → ResponseComposer（模板填充：数值 + citations）→ EgressGuard（断言无金额/账号）
        → LlmProvider（只收指标名/比例/分位/样本量 + 措辞指令）→ 回答 = 本地数值 + LLM 措辞
```

| 组件 | 职责 | 保障 |
|---|---|---|
| `ToolGateway` | 工具白名单 + 参数 schema + 调用留痕 + **强制带 tenant** | 防幻觉、防越权 |
| `ResponseComposer` | 本地填数值与 `citations` | 金额可见、断网可答 |
| `EgressGuard` | 出站前 schema/正则断言（金额、账号、邮箱、股东代码） | NFR-1、NFR-14 |
| `LlmProvider` | 多 provider、超时、熔断、降级 | 可用性 |
| `llm_call` | 留痕（含 `redaction_applied`） | 审计 |

**降级**：LLM 不可用 → 返回模板化结构化答案（仍含数值与来源），只是措辞不灵活。

---

## 14. 认证、授权与多租户隔离

### 14.1 认证流

| 凭证 | 形态 | 有效期 | 存储 |
|---|---|---|---|
| access token | JWT HS256（`sub/role/tenant/jti`） | 15min | 前端内存 |
| refresh token | 32B 随机、DB 存哈希、家族轮换 + 复用检测 | 30d | httpOnly cookie |
| PAT | `pat_<id>_<secret>`、DB 存哈希、scope + 过期 | 可配 | 用户自行保存 |

### 14.2 授权矩阵

| 角色 | 业务数据（trades/rounds/metrics/reports） | 自己的设置/Token | 系统状态/备份 | 审计 |
|---|---|---|---|---|
| `user` | 仅自己 | 仅自己 | ✗ | 仅自己 |
| `admin` | **✗（403 `ADMIN_CANNOT_READ_TRADES`）** | 自己 | ✓ | 系统级（无明细） |

### 14.3 双层隔离（defense in depth）

```
请求 → 认证中间件解析 subject → TenantContext.set(user_id, role)
     → 事务开始：SET LOCAL app.user_id = :uid   ← 第 2 层（RLS）
     → 仓储方法自动追加 user_id 过滤            ← 第 1 层（应用）
     → SQL 执行：RLS 策略再次校验
     → 事务结束：RESET app.user_id（或连接归还钩子强制重置）
```

**必须测试的失败模式**：连接池串号（前一请求的 `app.user_id` 残留）→ 用连接归还钩子 + 每事务 `SET LOCAL` 双保险，并有专门集成测试。

---

## 15. 性能与容量

| 场景 | 预算（PRD NFR-10） | 手段 |
|---|---|---|
| API 读 | p95 ≤300ms | 索引、游标分页、`fields=` 裁剪、ETag、物化指标 |
| API 写（不含任务） | p95 ≤500ms | 只写事实表 + 入队，重计算异步 |
| 看板首屏数据 | ≤1s（并行 4~6 个请求） | 聚合端点 + 指标物化 + 前端并行 |
| 导入 5000 行 commit | 任务 ≤30s | 单事务 + 批量插入（`COPY`/`execute_values`） |
| 全量重算（单租户 5 万行） | ≤60s | 脏窗口 + 向量化边界（仅 infra 只读模型，core 对拍） |
| 报告生成 | ≤5s | 区块复用 + 缓存 |

| 容量 | 目标 | 算式/说明 |
|---|---|---|
| 用户数 | 100 | 首版目标 |
| 流水总行数 | 500 万 | 100 × 5 万 |
| PG 连接 | api 副本×池(10) + worker×池(5) ≤ `max_connections`−保留 | 例：2×10+2×5+5=35 ≤ 100 |
| 存储 | 5~20 GB | 事实表 + 物化 + 归档 |
| 队列吞吐 | ≥100 任务/分钟 | 单 worker 足够；按需加 worker |

**扩容决策**：p95 超预算 → 先查索引/慢查询 → 加 api 副本 → 加 worker → 升 PG 规格 → 读写分离（后续）。

---

## 16. 部署、迁移与发布

### 16.1 编排

| 服务 | 副本 | 说明 |
|---|---|---|
| nginx | 1（宿主） | TLS、静态、反代；配置版本化 |
| api | ≥2 | 无状态；`CMD` = uvicorn/gunicorn workers |
| worker | ≥1 | 同一镜像，`CMD` = worker entrypoint |
| postgres | 1（主） | 数据卷；WAL 归档到备份位置 |
| migrate | 一次性 | `alembic upgrade head`，失败则阻止 api 启动（readyz 校验版本） |

### 16.2 零停机迁移（expand-contract）

| 阶段 | 动作 | 上线顺序 | 回滚 |
|---|---|---|---|
| 1 expand | 只加（新列可空 / 新表 / 新索引 `CONCURRENTLY`） | 迁移 → 部署新 API | 删新列/表 |
| 2 migrate | 回填数据（后台任务，分批，可中断续跑） | 新 API 双读/双写 | 停止回填，旧路径仍可用 |
| 3 contract | 切读新列 → 观察一个版本 → 删旧列 | 再发布一次 | 需数据回滚脚本（谨慎） |

**规则**：禁止在单次迁移中"加列+改读+删列"；大表索引必须 `CONCURRENTLY`；每步迁移前自动快照；`readyz` 校验 schema 版本匹配。

### 16.3 发布

```
构建镜像（api/worker 同镜像不同入口 + 前端静态包）
 → migrate 一次性任务（失败即中止）
 → 滚动更新 api（逐个摘流→重启→就绪）
 → 滚动更新 worker（先停止领取新任务→处理完在途→重启）
 → 冒烟：/readyz + 关键 API 验收子集
 → 失败回滚：切回上一镜像 tag（API 保持 N-1 兼容）
```

---

## 17. 可观测性

| 线 | 内容 | 落点 |
|---|---|---|
| 结构化日志（JSON） | `request_id / user_id / job_id / route / status / duration_ms / error_code`；金额/账号/邮箱脱敏 | stdout → 收集 |
| 指标（Prometheus） | 请求量/延迟/错误率、队列深度与任务时长、DB 连接池与慢查询、降级次数、越权拒绝次数、备份年龄 | `/metrics`（内网） |
| Trace | 请求 → 服务 → DB/外部调用的 span；异步任务带 `trace_id` 贯穿 | OTel → 后端 |
| 探针 | `/healthz`（存活）、`/readyz`（DB+迁移版本） | nginx/编排 |
| 告警 | 5xx 率、p95 超预算、队列积压、备份超期、越权事件、TLS 到期 | 值班渠道 |

---

## 18. 关键架构决策摘要（v2）

| ADR | 决策 | 取代 | 一句话理由 |
|---|---|---|---|
| [0013](architecture/v2/ADR.md) | 云上单机多容器（Compose）+ 模块化单体（api/worker 两部署单元） | 0001 | 满足多用户与云部署，同时守住单人运维预算 |
| [0014](architecture/v2/ADR.md) | PostgreSQL 单库多租户（`user_id` + RLS） | — | 100 用户量级运维最简，隔离有两道防线 |
| [0015](architecture/v2/ADR.md) | 任务队列用 PG `SKIP LOCKED`，不引 Redis/Kafka | 0007 | 少一个必须运维的有状态组件 |
| [0016](architecture/v2/ADR.md) | 前后端分离：React SPA + REST，nginx 同源 | 0006 | 需求方的技术形态偏好；同源规避 CORS/Cookie 复杂度 |
| [0017](architecture/v2/ADR.md) | OpenAPI 单源 + 生成式 SDK + 验收 100% 端点 | — | "API 优先可验收"的机制化落地 |
| [0018](architecture/v2/ADR.md) | PG 事务分级 + WAL 归档 PITR | 0008 | 服务级 RPO/RTO 的实现载体 |
| [0019](architecture/v2/ADR.md) | 异步任务 + 202 + SSE | — | 长任务不阻塞 API，p95 可保 |
| [0020](architecture/v2/ADR.md) | 多租户双层隔离（仓储 + RLS）+ 越权负向测试全覆盖 | — | 最严重风险，必须结构性防呆 |
| [0021](architecture/v2/ADR.md) | 前端零口径（生成式 SDK + AST 断言） | — | 重后轻前；口径单一实现 |
| [0022](architecture/v2/ADR.md) | LLM 旁路 + 金额后端填充 + 出站守卫（沿用并强化） | — | 隐私与可用性同时成立 |
| [0023](architecture/v2/ADR.md) | 软删用于业务追溯，注销物理删除 | — | 合规优先，冲突显式化 |
| [0024](architecture/v2/ADR.md) | 分层差异化覆盖 + 契约/越权覆盖硬门禁 | 0010（延续） | 覆盖率≠正确性；多租户需新增硬门禁 |

---

## 19. 需求追溯矩阵

### 19.1 功能需求（28 条）

| FR | 主要模块 | 关键 API | 关键设计 | 测试类型 |
|---|---|---|---|---|
| FR-L1 部署与服务化 | `services/api`、`infra/compose` | `/healthz` `/readyz` `/version` | 无状态 + 就绪探针 + 优雅停机 | 发布门禁、故障注入 |
| FR-L2 认证授权隔离 | `platform`（auth/tenant） | `/auth/*` | JWT+refresh 轮换+PAT；RLS 双层 | 越权矩阵、集成 |
| FR-L3 导入（两阶段） | `ledger` | `/imports/preview` `/imports` | 预览不落库；commit 异步；bag 去重；全零金额行隔离 | golden、属性、验收 |
| FR-L4 流水维护 | `ledger` | `/trades/*` | 版本冲突 409；软删可恢复 | 验收、集成 |
| FR-L5 分类与隔离区 | `ledger` | `/summary-rules` `/imports/{id}/quarantine` | 规则 CRUD + 配置化 | 单元、fixture |
| FR-L6 对账分级阻断 | `ledger`+`review` | `/reconciliation` | 余额链核对 + 断链自动补差 + 引擎门禁状态机 | fixture、快照 |
| FR-L7 导出/备份/删除 | `platform`+`delivery` | `/exports/*` `/admin/backups` | 异步导出 + 签名下载 + 物理删除 | 集成、安全 |
| FR-L8 查询 API | `ledger` | `/trades` | 游标分页 + ETag + 裁剪 | 契约、验收 |
| FR-A1 PAT | `platform` | `/tokens/*` | 哈希存储、scope、可撤销 | 验收、安全 |
| FR-A2 契约可验收 | `contracts`+`openapi` | `/openapi.json` | 快照 diff + 示例强制 | 契约门禁 |
| FR-A3 限流配额 | `platform` | 全部写端点 | 令牌桶 + 429/`Retry-After` | 验收、压测 |
| FR-A4 审计 | `platform` | `/audit-logs` | 全写操作留痕 | 验收 |
| FR-R1 数据服务化 | `review` | `/rounds` `/nav` | 同库跨 schema + 租户过滤 | 集成 |
| FR-R2 期初与回合 | `core/fifo` | `/rounds/*` `/jobs/rebuild-rounds` | FIFO 纯函数 + 桥接恒等式 | 属性、golden、变异 |
| FR-R3 净值与收益 | `core/nav` | `/nav` | 份额法 + XIRR | 属性、oracle |
| FR-R4 选股诊断 | `core/metrics` | `/diagnostics/selection` | PIT + cohort + BHAR | golden |
| FR-R5 择时与绩效 | `core/metrics` | `/metrics?domain=timing` | 基准锚定 + 回撤族 + 费用 | golden、契约 |
| FR-R6 行为诊断 | `core/metrics` | `/metrics?domain=behavior` | PGR/PLR + bootstrap | 单元、fixture |
| FR-R7 归因 | `core/attribution` | `/attribution` | 五因子恒等式 + Brinson | golden |
| FR-R8 统计可信度 | `core/stats`+引擎 | `/metrics` | 框架代算 + API 层门禁 | 边界、快照 |
| FR-D1 React SPA | `frontend/apps/web` | 依赖全部读端点 | 生成式 SDK + 零口径 | 组件、e2e、AST 断言 |
| FR-D2 月报 | `delivery/reports` | `/reports/*` | 异步生成 + 区块插件 + 原子写 | 快照、e2e |
| FR-D3 人话与下钻 | `delivery`+引擎 | `/metrics/{id}/explain` | 元数据驱动三段式 | 快照、词表断言 |
| FR-D4 日志闭环 | `delivery` | `/journal/*` | 事件触发对照 | 集成 |
| FR-D5 纵向趋势 | `delivery` | `/trends` | 口径断点 | 单元 |
| FR-D6 行为规则卡 | `platform`+`delivery` | `/settings/behavior-rules` `/alerts` | 规则配置 + outbox | 单元、集成 |
| FR-D7 对话 | `delivery/chat` | `/chat/*` | 工具白名单 + 出站守卫 | 断网 e2e、出站断言 |
| FR-D8 通知分享 | `delivery` | `/reports/{id}/shares` | 签名链接 + 可撤销 | 验收、安全 |

### 19.2 非功能需求（15 条）

| NFR | 机制 | 验证 |
|---|---|---|
| NFR-1 隐私合规 | 租户隔离、静态加密、脱敏、出站守卫、注销删除 | 越权矩阵、出站断言、删除验证 |
| NFR-2 数据校验层 | 适配器校验器 + provenance + 涨跌停/复权校验 | 恶意 fixture、契约测试 |
| NFR-3 时点一致 | PIT 门 + `data_as_of` + `pending_window` | golden、单元 |
| NFR-4 数据可信 | 对账闸门 + 血缘字段 | 集成、快照 |
| NFR-5 幂等可重放 | 幂等键 + bag 去重 + 批次撤销 + 任务幂等 | 属性、验收冲突用例 |
| NFR-6 认证授权隔离 | JWT/refresh/PAT + RLS + 越权 404 | 越权矩阵 100% |
| NFR-7 测试与验收 | 分层覆盖 + 验收 100% 端点 + 契约 + 前端 + 越权 | CI 门禁 |
| NFR-8 工程一致性 | Monorepo 双栈 + 契约单源 + 生成式 SDK | 架构约束、契约 diff |
| NFR-9 依赖与灾备源 | `MarketDataSource` 能力协商 + 缓存 | 契约测试、熔断 |
| NFR-10 性能容量 | 索引、物化、异步、连接池算式 | 压测门禁 |
| NFR-11 兼容与迁移 | 版本化 + expand-contract + N-1 | 迁移演练、发布门禁 |
| NFR-12 错误模型 | `code/user_message/what_to_do` 必填 | 契约快照 |
| NFR-13 可用性与灾备 | ≥2 副本 + PITR + 演练 | 故障注入、恢复演练 |
| NFR-14 安全基线 | TLS、Argon2id、secrets、漏洞扫描、CSP/HSTS | 安全清单、扫描门禁 |
| NFR-15 可观测性 | 日志/指标/trace/探针/告警 | 巡检、诊断包 |

---

## 20. 演进路线（与 PRD §8 对齐）

| 阶段 | 架构增量 | 退出条件 |
|---|---|---|
| **M0 平台底座** | Monorepo 骨架、PG 多 schema + RLS、认证、任务队列、导入两阶段、Compose 编排、契约生成链 | `/readyz` 通过；越权用例全绿；**API 验收覆盖 100%**；preview→commit→撤销脚本通过 |
| **M1 四问 + 最小前端** | 计算引擎、物化指标、API 门禁、看板/回合/下钻页面、导出 | core 100% + 变异 ≥80%；golden 通过；前端可登录看数 |
| **M2 归因与闭环** | 归因 DAG、月报、日志、趋势、限流、审计、备份 | 五因子恒等式；月报与 API 数字一致；PITR 演练通过 |
| **M3 对话与打磨** | ToolGateway、出站守卫、分享、压测与容量达标 | 出站断言；断网可答；p95 达标；滚动升级 5xx<0.1% |

**演进原则**：只新增端口与路由，不改已定分层；API 破坏性变更必须新版本；口径变更走 `definition_version`。

---

## 21. 架构风险登记

| 风险 | 触发条件 | 影响 | 缓解 / 决策点 |
|---|---|---|---|
| **越权泄漏**（最严重） | 仓储漏过滤 / 连接池串号 | 泄漏他人交易数据 | RLS 双保险 + 每事务 `SET LOCAL` + 越权矩阵 100%；上线前专项渗透 |
| 单机为主故障域 | 主机/卷故障 | 全站中断 | 承认接受（首版）；PITR + 重建 SOP；M2 评估热备与多机 |
| PG 成为瓶颈 | 租户增长、胖查询 | 延迟劣化 | 索引/分区/物化；压测门禁；读写分离预案 |
| 迁移事故 | 加列+改读同批 | 发布失败/数据错 | expand-contract 强制 + 迁移前快照 + 演练 |
| 契约漂移 | 手改 DTO/手写前端类型 | 页面报错 | 契约单源 + 生成式 SDK + diff 阻断 |
| 队列积压 | 重算风暴 | 任务延迟 | 单飞 + 优先级 + 背压 + 积压告警 |
| 双栈维护成本 | 单人 | 交付变慢 | 重后轻前、生成式代码、薄前端、严格分层 |
| 前端实现口径 | 赶工期 | 数字双标准 | AST 断言 + 评审清单 |
| LLM 出站泄露 | 上下文含金额 | 合规事故 | 出站守卫硬断言 + 模板填充 |
| RLS 被绕过（超级用户连接） | 用 owner 连接执行 | 隔离失效 | 应用连接使用非 owner 角色；RLS 强制策略；集成测试直连验证 |

---

## 附录 A：技术选型（含被否方案）

| 领域 | 选型 | 理由 | 被否 |
|---|---|---|---|
| 后端 | Python 3.12 + FastAPI + Pydantic v2 | 契约自动生成、异步、生态 | Django（重）、Flask（契约弱） |
| ORM/迁移 | SQLAlchemy 2.0 async + asyncpg + Alembic（多 schema） | 方言正确、迁移可控 | 裸 SQL（迁移/测试差） |
| DB | PostgreSQL 16 | 需求方偏好 + RLS + `SKIP LOCKED` + PITR | MySQL（RLS 弱）、SQLite（多用户不可） |
| 队列 | PG `SKIP LOCKED` | 零新增运维组件 | Redis/Kafka/Celery |
| 前端 | React 18 + TS strict + Vite + TanStack Query | 生态 + 类型 + 构建快 | Next.js SSR（不要求 SEO、增加复杂度） |
| UI | Tailwind（工具类） | 轻前、无组件库负担 | MUI/AntD（重、定制成本高） |
| 图表 | ECharts（按需） | 金融图表成熟 | Chart.js、D3（成本高） |
| 契约 | OpenAPI 3.1 + openapi-typescript/orval | 单源 + 生成式 SDK | GraphQL、手写 SDK |
| 测试 | pytest + hypothesis + testcontainers + Schemathesis + Vitest + Playwright + mutmut + import-linter | 覆盖口径/契约/越权/前端 | 只有单测 |
| 部署 | Docker Compose + nginx | 单人可运维、可迁移 | K8s、Serverless（冷启动/DB 连接问题） |
| 观测 | structlog + prometheus-fastapi-instrumentator + OTel | 标准、可离线 | 商业 APM（成本/隐私） |

## 附录 B：统一命令入口

| 命令 | 作用 |
|---|---|
| `make dev` | 起 PG + api + worker + web（本地） |
| `make test` | 快速单测（无外部依赖） |
| `make test-integration` | 真 PG（testcontainers）集成 |
| `make test-api` | **API 验收套件（100% 端点）** |
| `make test-contract` | OpenAPI 快照 diff + Schemathesis |
| `make test-e2e` | Playwright |
| `make lint` | ruff/mypy（后端）+ eslint/tsc（前端）+ import-linter |
| `make api-check` | 生成契约 + 校验示例/错误码/operationId 完整性 |
| `make sdk` | 由契约重新生成前端 SDK |
| `make up` / `make migrate` / `make smoke` | 部署 / 迁移 / 冒烟 |

## 附录 C：API 风格约定（摘要）

- 资源复数名词、kebab-case 路径；操作型动词用子资源（`/imports/{id}/revoke`）。
- 列表响应统一 `{ items: [], next_cursor, total? }`；单资源直接返回对象。
- 时间为 ISO 8601 UTC；业务日为 `YYYY-MM-DD`；金额为定点字符串。
- 错误统一 problem+json；`code` 为大写蛇形、稳定、可枚举。
- `operationId` = 域名 + 动作（`imports.commit`、`metrics.list`），一经发布不可改名。
- 所有写端点必须声明幂等策略；所有长任务必须返回 `202 + Location`。
