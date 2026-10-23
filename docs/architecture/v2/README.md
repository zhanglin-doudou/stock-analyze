# 架构文档包 v2 · 索引（现行）

> 需求基线：[PRD-v2.md](../../prd/PRD-v2.md)（v2.0，28 FR + 15 NFR）
> 文档包版本：**2.1** ｜ 日期：2026-10-23 ｜ 共 6 份、约 3350 行
> 状态：**现行**。v1 包（[`../v1/README.md`](../v1/README.md)）为历史基线，已被本包取代。

---

## 1. 本次修订的依据 → 落在哪里

| 修订依据（需求方） | 落点 |
|---|---|
| 第一版即考虑多用户 + 云主机部署 | Architecture-v2 §4/§5/§9/§16；[Reliability §1–§6](Reliability.md)；ADR-0013/0014 |
| 首批用户是有经验的程序员投资者 | Architecture-v2 §2（API 优先）、[API-Catalog](API-Catalog.md) 全文；ADR-0017 |
| PostgreSQL | Architecture-v2 §9；Reliability §4（参数/PITR）；ADR-0014/0018 |
| 前后端分离（React / Python） | Architecture-v2 §6/§12；Testing §7（前端测试与零口径）；ADR-0016/0021 |
| 重后轻前 | Architecture-v2 §12、§11（口径只在后端）；ADR-0021 |
| **优先 API 可验收** | **[API-Catalog](API-Catalog.md)**（78 端点 + 验收矩阵）；Architecture-v2 §2/§2.3；Testing §5 |
| Monorepo | Architecture-v2 §6；Extensibility §7/§8；ADR 见 §6 |

---

## 2. 文档清单

| 文档 | 行数 | 职责 | 最先读哪节 |
|---|---|---|---|
| [../Architecture-v2.md](../../Architecture-v2.md) | 951 | **主文档**：驱动力、**可验收 API 清单（先行）**、服务级质量属性、C4、Monorepo 双栈、PG 多租户、队列、指标引擎、前端架构、安全、部署迁移、28+15 追溯矩阵 | §0.2、§2、§19 |
| [API-Catalog.md](API-Catalog.md) | 600 | **可验收 API 目录**：总则、80 个端点总表、30 个详细契约、47 个错误码、12 个 Schema、异步/SSE、验收矩阵 80/80、契约治理、FR→API 追溯 28/28 | §1、§6、§8 |
| [Reliability.md](Reliability.md) | 506 | 服务级高可用：8 条 SLO 与错误预算、故障域与 15 个单点、优雅停机与滚动发布、expand-contract、PG/PITR、队列可靠性、20 行 FMEA、8×10 降级矩阵、容量、告警、SOP-1~10、12 项演练 | §1、§3、§7 |
| [Testing.md](Testing.md) | 548 | 测试与门禁：两类新增高危缺陷、分层覆盖率（含端点/越权硬门禁）、12 类套件、**20 行越权矩阵 + RLS 直连 8 项**、API 验收套件、契约三件套、前端测试与零口径断言、确定性、CI 四段、性能 | §2、§4、§5 |
| [Extensibility.md](Extensibility.md) | 506 | 可扩展性：X-1~X-8 判据、EP-1~EP-12、指标注册表 v2、**租户演进路径**、**API 兼容治理**、前端扩展、版本化矩阵、9 项反扩展、四个作业单 | §2、§4、§5 |
| [ADR.md](ADR.md) | 238 | ADR-0013~0025：13 条决策（含取代关系、理由、后果、被否方案） | 摘要表 + 0013/0014/0016/0020/0025 |

---

## 3. 单一事实源（数字冲突时以此为准）

| 内容 | 权威位置 |
|---|---|
| **端点清单与字段级契约** | [API-Catalog §1/§2](API-Catalog.md) |
| 错误码 | [API-Catalog §3](API-Catalog.md) |
| 端点验收矩阵 | [API-Catalog §6](API-Catalog.md) |
| SLO / 错误预算 / RTO / RPO | [Reliability §1](Reliability.md) |
| 覆盖率与门禁强度 | [Testing §2](Testing.md) |
| 越权测试矩阵 | [Testing §4](Testing.md) |
| 扩展点与 `MetricDef` 字段 | [Extensibility §2/§3](Extensibility.md) |
| 数据表与索引 | Architecture-v2 §9 |
| 端口契约 | Architecture-v2 §8 |
| 架构决策 | [ADR.md](ADR.md) |
| 指标口径 A1–A27 | [PRD-v2 附录 A](../../prd/PRD-v2.md) |
| 容差常量 | 代码 `backend/packages/contracts/`（`EPS_AMT/EPS_R/EPS_CUM/A`）；测试 `backend/tests/support/tolerance.py` |

---

## 4. 关键数字

| 项 | 值 |
|---|---|
| 部署 | 单云主机 Docker Compose：nginx + api×N + worker×M + PostgreSQL 16 + migrate |
| 代码组织 | Monorepo：`backend/`（uv workspace）+ `frontend/`（pnpm workspace）+ `openapi/` 契约快照 |
| 后端分层 | `services/api → application → domain ← infrastructure`（core 纯计算） |
| 数据 | PG 三业务 schema：`ledger` / `review` / `platform` + 1 个公共缓存 schema `market`；38 张表（36 张业务表：ledger 8 / review 11 / platform 17 + 2 张公共缓存表 `market.daily_bar` / `market.pull_range`）；RLS 双层隔离 |
| 队列 | PG `platform.job` + `FOR UPDATE SKIP LOCKED`（无 Redis/Kafka） |
| **端点** | **80 个**（API-001~080），28/28 FR 覆盖，验收矩阵 80/80；详细契约 30 个；错误码 47 个 |
| 错误码 | ≥30 个（机器可读 `/api/v1/errors`） |
| ADR | v2 新增 13 条（0013~0025），取代 v1 的 0001/0006/0007/0008 |
| 扩展点 | 12 个（EP-1~EP-12） |
| SLO | 可用性 ≥99.9%（43.2min/月预算）；读 p95 ≤300ms；5xx ≤0.1%；RPO ≤5min；RTO ≤30min |
| 覆盖率 | core 100/100（变异 ≥80%）、platform 92/88、契约/端点覆盖 100%、越权用例 100% |
| 降级矩阵 | 8 故障 × 10 功能域 |
| FMEA / 越权矩阵 / 演练 | 20 行 / 20 行 + RLS 8 项 / 12 项 |
| 容量目标 | 100 用户 / 500 万行 / 库约 8GB |

---

## 5. 建议阅读顺序

| 角色 | 顺序 |
|---|---|
| 要开始写代码 | **API-Catalog §1**（端点清单）→ Architecture-v2 §6（目录）→ §9（数据模型）→ §10（队列）→ Extensibility §11（作业单） |
| 要做前后端联调 | API-Catalog §0（总则/错误/分页/幂等）→ §5（异步/SSE）→ §7（契约治理） |
| 架构评审 | Architecture-v2 §0.2（差异）→ §2（API 优先）→ §3（质量属性）→ ADR 摘要 → Reliability §1/§7 → Testing §2/§4 |
| 运维/上线 | Reliability §1（SLO）→ §3（发布）→ §4（PG/PITR）→ §12（Runbook）→ §13（演练） |
| 长期演进 | Extensibility §4（租户演进）/§5（API 兼容）/§10（反扩展）→ ADR 演进链 |

---

## 6. v1 → v2 决策取代链

| 主题 | v1 | v2 |
|---|---|---|
| 部署 | 单进程 + SQLite（ADR-0001） | 单机多容器 + PG（**ADR-0013**） |
| 渲染 | SSR + HTMX（ADR-0006） | React SPA + REST（**ADR-0016**） |
| 契约 | 无显式治理 | OpenAPI 单源 + 生成式 SDK（**ADR-0017**） |
| 队列 | 进程内 + outbox（ADR-0007） | PG `SKIP LOCKED` + outbox（**ADR-0015**） |
| 持久化 | SQLite WAL（ADR-0008） | PG + WAL 归档 PITR（**ADR-0018**） |
| 隔离 | 无（单用户） | RLS + 仓储双层（**ADR-0014/0020**） |
| 长任务 | 进程内作业 | 202 + job + SSE（**ADR-0019**） |
| 前端职责 | 渲染 | 纯展示、零口径（**ADR-0021**） |
| **保留** | 分层与端口(0002)、标度整数(0003)、指标注册表(0004)、产物分离(0005)、后复权(0009)、门禁状态机(0011)、LLM 旁路(0022)、覆盖分层(0024) | 同左 |

---

## 7. 维护规则

1. **端点以 API-Catalog 为准**：新增/改端点必须同时更新 ① 契约快照、② §1 总表、③ §6 验收矩阵、④ Architecture-v2 §2.2 摘要；缺一即视为未完成（CI 可查端点覆盖）。
2. **破坏性变更**必须新增 ADR 并升 API 版本；`/v1` 内只允许 additive（Extensibility §5）。
3. **数字只改主位**：改 SLO/覆盖率/端点后按 §3 表检查其余文档引用。
4. **决策不可悄悄改**：ADR 编号连续、不改已采纳正文；改变主意新建 ADR 并标注取代。
5. **与 PRD 的关系**：PRD 是"要什么"，本包是"怎么建"。架构发现需求不可实现时回到 PRD 修订（PRD-v2 已吸收 v1 架构反馈的去重规则）。
6. **v1 文档只读**：`../ADR.md`、`../Reliability.md` 等不再更新，仅作为历史依据引用。

---

## 8. 已知未决项（继承 PRD-v2 §10 与 ADR 遗留）

| 编号 | 未决项 | 当前承载 |
|---|---|---|
| OQ-1 | 邮箱验证是否必做 | API-Catalog：注册端点已留 `email_verification` 配置位；FR-L2 |
| OQ-2 | 是否引入对象存储 | `ObjectStore` 端口已抽象（Extensibility EP-10）；首版本地卷 |
| OQ-3 | PAT 细粒度 scope | 首版 `read`/`write` 二元（ADR/API-Catalog §0） |
| OQ-4 | 管理员排障是否可代用户身份 | 首版完全不可（ADR-0014 授权矩阵） |
| OQ-5 | PG 热备 / 多机 | Reliability §2 给消除路径与触发条件（M2 热备 → M2.5 第二台主机） |
| OQ-6 | 租户模型升级为团队 | Extensibility §4 给五步零停机迁移，首版只预留不实现 |

---

## 9. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-19 | 1.0 | v1 文档包：单机版主文档 + 3 专篇 + ADR-0001~0012 |
| 2026-10-19 | 2.0 | **形态修订**：多用户 + 云主机 + PG + 前后端分离 + API 优先验收 + Monorepo；新增 API-Catalog；ADR-0013~0024 |
| 2026-10-23 | 2.1 | 行情 live 源：新增 `market` 公共缓存 schema（`daily_bar` / `pull_range` 2 表）+ ADR-0025（不复权估值 + 区间并集缓存）；表数 36→38、ADR 12→13 |
