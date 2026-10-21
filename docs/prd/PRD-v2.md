# 个人交易复盘平台 · 产品需求文档（PRD）

> 版本：**v2.0** ｜ 日期：2026-10-19 ｜ 上一版：[PRD-v1.md](PRD-v1.md)（v1.1）
> 补丁：**v2.1**（2026-10-21）｜ 不新增 FR/NFR 编号，只收紧 FR-L3 的导入隔离口径、补齐 FR-L6 的断链自愈语义（见 §0.6）。
> 本次修订依据：① 系统第一版即考虑**多用户 + 云主机部署**，首批用户为**有经验的程序员投资者**；② 技术形态偏好 **PostgreSQL、前后端分离（React 前端 / Python 后端）、重后轻前、优先 API 可验收**；③ 源代码采用 **Monorepo**。

---

## 0. 修订说明（v1.1 → v2.0）

### 0.1 一句话概括本次修订

**从"单机零安装的个人工具"变为"面向程序员投资者的多用户云服务，以 API 为验收主体、Web 为薄客户端"。** 指标口径（附录 A 的 A1–A27）**全部不变**——金融正确性与部署形态无关；改变的是交付形态、用户模型、验收方式与工程组织。

### 0.2 形态变化对照

| 维度 | v1.1 | **v2.0** | 连带影响 |
|---|---|---|---|
| 部署 | 单进程 + SQLite 单文件 + 双击启动 | **云主机 + Docker Compose**（nginx / api / worker / postgres） | FR-L1 重写；NFR-13 新增；可靠性目标从"本地"升级为"服务级" |
| 用户 | 单用户 seed（`root/100000`） | **多用户注册登录**、角色、数据隔离 | FR-L2 重写；NFR-6 由"无鉴权"改为"认证授权"；新增 FR-A1 |
| 数据存储 | SQLite（WAL） | **PostgreSQL**（双 schema + 行级隔离） | 数据模型加 `user_id`、RLS、分区；附录 B 更新 |
| 交付形态 | SSR + HTMX，无构建 | **React SPA（薄）+ REST API（重）** | FR-D1/D2/D3 重写；NFR-8 加入双栈 Monorepo |
| 验收方式 | 页面 + 手工核对 | **API 可验收优先**：每条 FR 必须给出可脚本验证的 API 判据 | 新增 FR-A2；§5.0 验收总则；架构配套《API-Catalog》 |
| 程序化访问 | 无 | **Personal Access Token**（CLI / 脚本 / CI 直接调用） | 新增 FR-A1；首批用户核心诉求 |
| 工程质量 | 覆盖率 + golden | 覆盖率 + golden + **契约测试 + API 验收套件 + 前端测试** | NFR-7 扩展 |

### 0.3 FR 编号策略

- 既有编号 **FR-L1~L8 / FR-R1~R8 / FR-D1~D8 保持不变**，便于与 v1.1 及《Review.md》逐条对照；内容按形态变化重写。
- 新增 **FR-A1~A4**（API 平台能力）：Token、契约与可验收性、限流配额、审计。
- 每条 FR 均增加 **`API 验收`** 行：以 HTTP 请求 + 可判定断言表述，**不依赖人工点页面**。

### 0.4 变更摘要

| 编号 | v2.0 变更 | 类型 |
|---|---|---|
| FR-L1 | 单进程桌面 → 云主机容器化服务；健康检查、零停机升级、配置外置 | 重写 |
| FR-L2 | seed 单用户 → 注册/登录/刷新/登出/注销 + 角色 + 数据隔离 | 重写 |
| FR-L3 | 导入新增 **preview/commit 两阶段**（API 可脚本化）与原始文件归档 | 增强 |
| FR-L4~L6 | 语义不变，改为 API 优先验收；规则管理提供 CRUD API | 调整 |
| FR-L7 | 用户自助导出（自己的数据）+ 管理员备份/恢复；注销物理删除 | 重写 |
| FR-L8 | 查询 API 升级为完整 REST（游标分页、过滤、排序、字段裁剪、ETag） | 增强 |
| FR-R1 | "同库视图" → 同一 PostgreSQL 内跨 schema 只读查询（仍无水位线） | 调整 |
| FR-R2~R8 | **口径完全不变**；计算改为**异步 Job**（`202 + job_id`），可重算可取消 | 增强 |
| FR-A1~A4 | 新增：PAT、OpenAPI 契约单源与可验收性、限流配额、审计日志 | 新增 |
| FR-D1~D3 | SSR → React SPA；月报改为异步生成 + 下载（HTML/PDF） | 重写 |
| FR-D4~D8 | 语义保留，改为 API + 前端消费 | 调整 |
| NFR-6 | 无鉴权 → 认证授权 + 多租户隔离（RLS 第二道防线） | 替换 |
| NFR-13~15 | 新增：服务级可用性与灾备、安全基线、可观测性与运维接口 | 新增 |

### 0.5 版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-18 | 初稿（单机、无界面、API 化） |
| v1.1 | 2026-10-19 | 依据双视角评审修订 13 项 blocker + 9 项缺失需求 |
| v2.0 | 2026-10-19 | 形态修订：多用户 + 云主机 + PG + 前后端分离 + API 优先验收 + Monorepo |
| v2.1 | 2026-10-21 | 行为补丁：导入全零金额行隔离（`INVALID_AMOUNT`）+ 对账断链自动补差（`side=missing`/`source=system`），见 §0.6 |

### 0.6 v2.1 补丁说明（2026-10-21）

由两条真实账单问题驱动，**不新增 FR/NFR 编号**，只收紧 FR-L3 / FR-L6 的验收口径：

| 补丁 | 规则 | 目的 | 验收 |
|---|---|---|---|
| **P1 导入噪音行隔离** | 一行账单的「佣金、印花税、其他费、发生金额、资金余额」五个金额字段**全为 0**（资金余额列缺失/为空**不算 0**）→ 不写 `trade`，落隔离区 `reason_code=INVALID_AMOUNT`；该判定**先于**重复行判定 | 货基份额再投、转托管确认等零资金流噪音行没有交易语义；写入会污染回合/费用口径，还会被误判成重复行 | preview `estimated.quarantined` 与 commit `counts.quarantined` 同口径计入；同内容的重复全零行仍记 `INVALID_AMOUNT`（不是 `DUPLICATE_IN_FILE`）；`GET /trades` 查不到该行 |
| **P2 对账断链自愈** | `GET /api/v1/reconciliation` 检出断链时自动插入 `side=missing`、`source=system` 的补差流水，落库一次链即闭合；响应新增 `filled_count` 与 `breaks[].filled/filled_trade_id` | 单笔漏记（如漏记转账）不应让资产类指标永久阻断；把缺口显式补成可审计的一行，比让用户手工对数更可复现 | 首次调用 `filled_count=1`、`status=degraded`（不再 `blocked`）；再次调用幂等、`break_count=0` 且补差行仍只有 1 条；`GET /trades?side=missing` 可见补差行 |

> 两处补丁的共同取舍：**宁可显式兜底并留下可审计记录，也不静默吞行或长期阻断。** 补差流水与导入行同表，但以 `source=system` / `side=missing` 与真实成交严格区分（`batch_id=null`），下游可按这两个字段过滤。

---

## 1. 背景与用户

### 1.1 背景

个人投资者持有完整的股票生涯流水明细（券商导出交易账单），希望借助软件能力分析与提升自己的**股票选择**与**交易执行**水平。

v2.0 的起点变化：产品**不再假设本机运行**。第一版即面向多用户与云主机部署，首批用户是**有经验的程序员投资者**——他们能读 OpenAPI、会用 `curl` 与脚本、能跑 Docker、愿意用 Token 调 API，也仍然需要 Web 界面看图表与月报。

### 1.2 首批用户画像与能力假设（决定产品形态）

| 维度 | 假设 | 产品含义 |
|---|---|---|
| 身份 | 有经验的个人投资者，同时是软件工程师 | 可接受 API/CLI 优先；文档与示例即是产品的一部分 |
| 数据规模 | 每人 10 年 ≤ 5 万行流水 | 单租户数据量小，聚合查询可实时 |
| 使用方式 | 月度导入（脚本或网页）+ 高频调 API 取指标 + 偶尔看 Web | **API 是主路径**，Web 是便利层 |
| 环境 | 会写 Docker Compose、能配域名/TLS、能读日志 | 部署文档可要求一定技术能力；不要求"零安装" |
| 期望 | 数据可导出、可脚本化、可接自己的分析工具 | 导出/Token/稳定 API 是硬需求 |
| 不可假设 | 愿意接受"功能只在网页里点" | 每个功能必须有 API |

### 1.3 明确的体验取舍（重后轻前）

- **后端重**：口径、计算、归因、任务编排、审计全部在后端完成并可 API 验收。
- **前端轻**：React SPA 只做"取数 + 渲染 + 交互"，**不允许在前端实现任何业务口径**（口径唯一实现于后端）。
- 不为"傻瓜化"牺牲能力：导入可用脚本完成；Web 提供等价的便利入口，但不是唯一入口。

---

## 2. 产品目标

| 编号 | 目标 | 可验证的成功标准 |
|---|---|---|
| G1 | 交易能力度量系统 | 三域指标全部可 API 输出；30 回合分层抽样：pnl 差额 ≤0.01 元、收益率 ≤1bp、FIFO `trade_id` 集合 100% 一致 |
| G2 | 数据可信可追溯 | 每次导入自动对账（断链 100% 检出，阈值 0.01 元）；任一数字可追到 `definition_version / data_as_of / n / inputs_hash` |
| G3 | **API 优先可验收** | 每条 FR 均有可脚本执行的 API 验收判据；OpenAPI 覆盖率 100%（每个端点有示例与错误码）；验收套件一键运行全绿 |
| G4 | 多用户安全隔离 | 跨用户越权读取/写入 **0 成功**（含 ID 猜测与批量接口）；RLS 与仓储层双层拦截均有测试证据 |
| G5 | 云上可运营 | 月度可用性 ≥99.9%；API p95 读延迟 ≤300ms；滚动升级期间 5xx 率 <0.1%；RPO ≤5min、RTO ≤30min |
| G6 | 程序化可用 | PAT 可完成"导入→触发计算→取指标→下载月报"全流程，且无需人工介入 |
| G7 | Web 可用但薄 | React SPA 覆盖 5 张卡片、回合下钻、月报、日志、设置；前端不含口径代码（架构约束测试可验证） |
| G8 | 复盘以对话完成 | 回答中每个数字可追溯；金额由后端计算填充，LLM 只组织语言；LLM 不可用时金额类问题仍可答 |

---

## 3. 用户与使用场景

### 3.1 用户模型

| 概念 | 定义 |
|---|---|
| 用户（user） | 注册账号，拥有独立的交易数据空间；`user_id` 全局唯一 |
| 租户边界 | **一个用户即一个租户**（首版不做"团队/组织"）。数据隔离以 `user_id` 为唯一维度 |
| 角色 | `user`（默认）、`admin`（运维：备份/恢复/查看系统状态，**不可读用户交易数据**） |
| 凭证 | ① Web 会话：短期 access token + 轮换 refresh token；② PAT：面向脚本/CI，可设 scope 与过期时间，可撤销 |

### 3.2 核心场景

**场景 A｜注册与开通（Web）**
注册 → 邮箱验证（可配置关闭）→ 登录 → 空数据空间首页 → 引导"上传账单或使用 API Token"。

**场景 B｜程序员用户的月度工作流（脚本，主路径）**
```bash
# 1) 上传账单（两阶段，避免脏数据入库）
curl -H "Authorization: Bearer $PAT" -F file=@huatai_2026-09.xlsx \
     https://api.example.com/api/v1/imports/preview
# → 返回列映射建议 + 预计新增/跳过/隔离行；确认后 commit
curl -X POST -H "Authorization: Bearer $PAT" -H "Idempotency-Key: $UUID" \
     -d @commit.json https://api.example.com/api/v1/imports
# → 202 + job_id；轮询或订阅 SSE
# 2) 取指标
curl -H "Authorization: Bearer $PAT" \
     "https://api.example.com/api/v1/metrics?period=2026-09&ids=win_rate,pnl_ratio,de"
# 3) 生成并下载月报
curl -X POST -H "Authorization: Bearer $PAT" -d '{"period":"2026-09"}' \
     https://api.example.com/api/v1/reports
```
**成功标准**：全程无网页操作、无人工等待、每步可脚本判断成败。

**场景 C｜Web 复盘（便利层）**
登录 → 看板 5 卡 → 点数字下钻到"怎么算的 / 涉及哪几笔" → 生成月报 → 保存/分享。

**场景 D｜Web 写交易日志**：买入后记一句理由 → 卖出后自动弹"理由还成立吗" → 季度看"我说的 vs 实际发生的"。

**场景 E｜导出与迁移**：用户导出自己的全量数据（Excel + JSON）→ 离线分析或迁出。

**场景 F｜管理员运维**：查看系统状态与任务积压 → 触发备份 → 演练恢复（**不接触用户交易明细**）。

---

## 4. 产品形态与硬约束

### 4.1 产品组成

| 层 | 模块 | 定位 | 验收主体 |
|---|---|---|---|
| 数据底座 | 账单服务（Ledger） | 交易数据唯一事实源 | API |
| 度量层 | 复盘服务（Review） | 三域度量 + 归因 + 可信度 | API |
| 平台层 | 平台服务（Platform） | 账号、Token、任务、审计、限流 | API |
| 交付层（后端） | 报告/对话/导出 | 把结果变成可带走、可问的产物 | API |
| 交付层（前端） | **React SPA** | 薄客户端：取数、渲染、交互 | 页面（消费同一 API） |

### 4.2 部署形态（云主机）

```
                    ┌──────────── 云主机（Linux, Docker Compose） ────────────┐
Internet ──TLS──▶   │  nginx  :443  ──静态资源──▶ React SPA (dist)            │
                    │     └── /api/* ──▶ api 服务 ×N (uvicorn/gunicorn)        │
                    │                        ├── PostgreSQL 16 (主库)          │
                    │                        └── worker ×M (计算/报告/导出)    │
                    │  volumes: pgdata / data(归档·报告·导出) / logs           │
                    └─────────────────────────────────────────────────────────┘
                           外部依赖（可选、可失败）：行情源 akshare / AlphaFeed、LLM
```

| 组件 | 形态 | 要求 |
|---|---|---|
| 反向代理 | nginx（TLS 终止、静态资源、`/api` 反代） | 前后端**同源**部署，规避 CORS 与第三方 Cookie 问题 |
| API 服务 | 无状态，≥2 副本 | 可水平扩展；不做本地内存态（会话/锁/队列全在 PG） |
| Worker | 独立进程，≥1 | 消费 PG 任务队列；与 API 同镜像不同入口 |
| 数据库 | PostgreSQL 16 | 双 schema（`ledger` / `review`）+ RLS；WAL 归档 |
| 文件 | 本地卷（后续可换对象存储） | 原始账单归档、报告、导出包 |

**一键拉起**：`docker compose up -d` 后自动执行迁移任务；提供 `.env.example`。

### 4.3 API 优先原则（本版最重要的形态约束）

1. **API 是验收主体**：任何 FR 的"完成"以 API 判据为准；页面缺失不阻塞 FR 验收，但 API 缺失则 FR 不通过。
2. **契约单源**：OpenAPI 3.1 由后端生成并作为仓库内快照提交；前端 SDK **由契约生成**，禁止手写请求类型。
3. **前端零口径**：前端不得实现指标计算、不得自行拼接结论文案；一律消费后端结构化结果与人话字段。
4. **可脚本化**：所有写操作可带 `Idempotency-Key`；所有长任务返回 `202 + job_id` 并提供轮询与 SSE。
5. **可判定**：每个端点必须有成功示例、错误码枚举、验收断言（见《API-Catalog》）。

### 4.4 多用户与安全模型

| 项 | 规定 |
|---|---|
| 注册/登录 | 邮箱 + 密码（Argon2id 哈希）；登录返回 access token（短时）+ refresh token（轮换、可撤销） |
| 程序化访问 | PAT（`pat_` 前缀，仅显示一次，存哈希；支持 scope 与过期；可撤销） |
| 隔离 | 所有业务表带 `user_id`；仓储层强制过滤 + PostgreSQL **RLS** 作为第二道防线；`admin` 亦不可读交易明细 |
| 权限 | `user` / `admin`；管理端点独立前缀 `/api/v1/admin/*` |
| 注销与删除权 | 用户可注销：**业务软删保留追溯，但注销时物理删除该用户全部数据**（级联），删除动作记审计 |
| 速率限制 | 按用户与 Token 限流；超限返回 `429 + Retry-After` |

> 与 v1.1 的冲突已处理：v1.1 规定"删除历史永不物理清除"，v2.0 明确区分——**业务操作软删**（可追溯、可恢复）与**账号注销/数据删除权**（物理删除，合规优先）。

### 4.5 技术形态约束

| 编号 | 约束 |
|---|---|
| C1 | 数据库固定 **PostgreSQL**；不引入其他数据库 |
| C2 | 前后端分离：**React + TypeScript / Python**；同源部署由 nginx 反代 |
| C3 | 重后轻前：业务能力全部后端；前端仅渲染与交互 |
| C4 | **Monorepo**：后端（Python workspace）与前端（Node workspace）同仓；契约快照同仓 |
| C5 | 无强制外部中间件：任务队列用 PostgreSQL 实现，**不引入 Redis/Kafka** |
| C6 | 金融口径可复现：金额/收益禁用 float；口径版本化 |
| C7 | 合规：不构成投资建议，不提供标的级买卖建议 |
| C8 | 可观测：结构化日志 + 指标 + trace id 贯穿 + health/ready |

---

## 5. 功能需求

### 5.0 验收总则

1. 每条 FR 给出 **`API 验收`**：以 HTTP 请求 + 可判定断言表达，可被验收套件自动执行。
2. **UI 验收**分级：`必须`（前端为该功能的唯一便利入口）/ `可后置`（API 已达标即可）。
3. 通用约定：金额为**分**的整数字符串或定点数（见附录 B），时间为 ISO 8601 UTC；错误遵循统一错误模型（含 `code / user_message / what_to_do`）。
4. 容差常量：`ε_amt = 0.01 元`、`ε_r = 1bp`、`ε_cum = 5bp`、年化交易日 `A = 244`。
5. 完整端点契约见 [architecture/v2/API-Catalog.md](../architecture/v2/API-Catalog.md)（本 PRD 只定义验收语义）。

### 5.1 平台与账号

#### FR-L1 部署与服务化 —— P0

- 云主机 Docker Compose 一键部署：`nginx + api + worker + postgres + migrate`；配置全部来自环境变量（`DATABASE_URL`、`JWT_SECRET`、`LLM_*`、`MARKET_*`）。
- 提供 `/healthz`（存活）、`/readyz`（就绪：DB 可连、迁移版本匹配）、`/version`。
- 优雅停机：收到 `SIGTERM` 停止接新请求、等待在途请求 ≤30s、释放任务租约。
- **API 验收**：`GET /healthz` → 200；`GET /readyz` → 200 且 body 含 `db: ok, schema_version: <n>`；`docker compose restart api` 期间 5xx 比例 <0.1%（脚本采样）；非法 `DATABASE_URL` 时 `/readyz` 返回 503 且 `code=DB_UNAVAILABLE`。
- **UI 验收**：不适用。

#### FR-L2 多用户认证、授权与数据隔离 —— P0

- 注册、登录、刷新、登出、注销；密码强度校验；登录失败限速。
- 角色 `user` / `admin`；管理端点独立前缀且 `admin` 不可读交易明细。
- 数据隔离：**任何**业务查询/写入只能作用于当前用户；跨用户访问按"不存在"处理（404，不泄露存在性）。
- 注销：物理删除该用户全部业务数据并记审计。
- **API 验收**：
  - `POST /api/v1/auth/register` → 201；重复邮箱 → 409 `EMAIL_TAKEN`。
  - `POST /api/v1/auth/login` → 200 含 `access_token`（≤15min）与 `refresh_token`；错误密码连续 5 次 → 429。
  - `POST /api/v1/auth/refresh` → 200 新 access；旧 refresh 再用 → 401（轮换+复用检测）。
  - **越权**：用户 B 用自己 token 访问用户 A 的 `/api/v1/rounds/{id}` → 404；批量接口中混入 A 的 ID → 403 或该条被剔除且不回显 A 的任何字段（二选一，需一致）。
  - 无 token / 过期 token → 401 且 `WWW-Authenticate: Bearer`。
  - `admin` 调用 `/api/v1/trades` → 403 `ADMIN_CANNOT_READ_TRADES`。
- **UI 验收**：必须（登录/注册/登出）。

#### FR-A1 API Token 与程序化访问 —— P0

- PAT 创建（scope：`read` / `write`；过期时间；名称）、列表（仅显示前缀与元信息）、撤销。
- Token 仅创建时返回明文一次，服务端只存哈希；使用与撤销均入审计。
- **API 验收**：`POST /api/v1/tokens` → 201 含明文一次；`GET /api/v1/tokens` → 200 且不含明文；用 PAT 调用 `GET /api/v1/metrics` → 200；撤销后再调用 → 401 `TOKEN_REVOKED`；只读 PAT 调用 `POST /api/v1/imports` → 403 `INSUFFICIENT_SCOPE`；过期 PAT → 401 `TOKEN_EXPIRED`。
- **UI 验收**：可后置（管理页）。

#### FR-A2 API 契约与可验收性 —— P0

- OpenAPI 3.1 单源生成，快照提交仓库；CI 校验：① 快照 diff（破坏性变更必须升 major）；② 100% 端点含成功示例、错误码枚举、`operationId`；③ 验收套件覆盖 100% 端点。
- 提供机器可读的错误码总表；`operationId` 稳定（前端 SDK 依赖）。
- **API 验收**：CI 脚本 `make api-contract-check` 退出码 0；故意删除某端点示例 → 脚本退出码非 0（元测试）；`GET /api/v1/openapi.json` 返回 200 且 `info.version` 与 `/version` 一致。
- **UI 验收**：不适用。

#### FR-A3 限流与配额 —— P1

- 按用户 + Token 的请求速率限制；长任务（导入/重算/报告）并发上限；超限 `429` 带 `Retry-After` 与 `X-RateLimit-*`。
- **API 验收**：突发 N+1 次请求 → 第 N+1 次为 429；`Retry-After` 为正整数；等待后可成功；同一用户并发提交 3 个全量重算 → 仅 1 个运行，其余 429 或返回同一 `job_id`（需一致）。
- **UI 验收**：可后置。

#### FR-A4 审计日志 —— P1

- 记录：登录、Token 增删、导入/撤销、数据修改/删除、导出、注销、管理员运维操作。
- 用户可查自己的审计；管理员可查系统级事件（不含交易明细）。
- **API 验收**：`GET /api/v1/audit-logs` → 200 且包含刚才的登录与撤销事件，字段含 `actor/action/target/at/request_id`；用户 A 无法查到用户 B 的记录。
- **UI 验收**：可后置。

### 5.2 数据底座

产品支持的账单范围：A 股华泰证券普通账户（结构上预留券商/账户类型扩展）。格式：`.xls` / `.xlsx` / `.csv`。

#### 账单数据示例

| 日期 | 币种 | 股东账号 | 证券代码 | 证券名称 | 摘要 | 成交数量 | 成交均价 | 佣金 | 印花税 | 其他费 | 发生金额 | 资金余额 | 操作理由 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20230103 | 人民币 | 0327201469 | 300308 | 中际旭创 | 证券卖出 | 700.00 | 27.320 | 5.74 | 19.12 | 0.19 | 19098.95 | 21415.47 | | |
| 20230321 | 人民币 | | | | 利息归本 | 0.00 | 0.000 | 0.00 | 0.00 | 0.00 | 2.73 | 90.18 | | |
| 20230612 | 人民币 | 0327201469 | 002938 | 鹏鼎控股 | 股息入帐 | 0.00 | 25.350 | 0.00 | 0.00 | 0.00 | 1890.00 | 3437.15 | | |

#### FR-L3 账单导入（两阶段 + 批次 + 可撤销）—— P0

- **两阶段导入**：
  - `preview`：解析文件、返回**列映射建议 + 日期格式识别 + 数据质量预检**（缺资金余额列、首行非买入、未知摘要、重复行数、全零金额行），**不落业务数据**。
  - `commit`：按确认后的映射与模式落库；返回 `202 + job_id`。
- 字段与格式：13 个固定字段，列名接受常见别名；日期接受 `20230103` / `2023-01-03` / `2023/1/3` / Excel 序列号。
- 噪音行隔离（v2.1）：一行账单的「佣金、印花税、其他费、发生金额、资金余额」五个金额字段**全为 0** → 不写 `trade`，落隔离区 `reason_code=INVALID_AMOUNT`；该判定**先于**重复行判定（同内容的重复全零行统一记 `INVALID_AMOUNT`，不记 `DUPLICATE_IN_FILE`）。「资金余额」列缺失/为空**不算 0**，仍走缺列降级口径。
- 两种更新模式：**全量导入**（行级幂等、bag 去重）、**区间替换**（修复模式，旧数据软删）。
- 批次与撤销：`import_batch` 记录文件名哈希、行数、新增/跳过/隔离数；支持**撤销本次导入**。
- 原始文件归档（保留 N 天，可配置），用于重放与排障。
- **API 验收**：
  - `POST /api/v1/imports/preview` → 200，含 `columns[]`（含 `suggested_field`）、`date_format`、`warnings[]`、`estimated{inserted,skipped,quarantined}`；**预览后 `GET /api/v1/trades` 总数不变**。
  - `POST /api/v1/imports` → 202 + `job_id`；`GET /api/v1/jobs/{id}` 至 `succeeded`；`GET /api/v1/imports/{batch_id}` 的 `inserted` 与预估一致（或差异有 `reason`）。
  - 重复上传同一文件 → 第二次 `inserted = 0`；同日同价同量多笔 → 按出现次数去重，不吞并。
  - `POST /api/v1/imports/{batch_id}/revoke` → 200；撤销后 `GET /api/v1/trades` 行数与导入前一致。
  - 缺资金余额列 → preview 返回 `warnings` 含 `MISSING_CASH_BALANCE`，commit 仍成功但对账标记不可用。
  - 全零金额行 → preview `estimated.quarantined` 与 commit 批次 `counts.quarantined` 同口径计入；`GET /api/v1/imports/{batch_id}/quarantine?reason_code=INVALID_AMOUNT` 返回该行（`line_no` 精确、`raw` 原样），且该行不出现在 `GET /api/v1/trades`。
  - 首行非买入 → commit 成功且返回 `assumption=back_solved` 或 `NAV_UNRELIABLE` 说明。
  - 并发两次 commit 同一 `Idempotency-Key` → 同 `job_id`，数据只落一次。
- **UI 验收**：必须（上传 + 预览确认 + 结果页 + 撤销）。

#### FR-L4 交易数据维护与修复 —— P0

- 单行修正（PATCH，带 `If-Match`/版本）、软删、恢复；修改历史可查。
- **API 验收**：`PATCH /api/v1/trades/{id}` → 200，返回 `version` 递增；用旧 `version` 再 PATCH → 409 `VERSION_CONFLICT`；`DELETE` 后该行不出现在默认查询但 `?include_deleted=true` 可见；`POST /{id}/restore` 后指标复算完成且与原值 diff=0。
- **UI 验收**：可后置（P1）。

#### FR-L5 摘要分类与隔离区 —— P0

- 规则表分类：交易流 / 公司行为流 / 资金流 / **打新流** / 未匹配→隔离区。
- 规则 CRUD API；规则变更后可对指定区间重跑分类。
- **API 验收**：`GET /api/v1/summary-rules` → 200 含默认规则；`POST/PATCH/DELETE` 生效并影响下一次分类；未知摘要 → `GET /api/v1/imports/{id}/quarantine` 返回带行号与原始内容的记录；打新类流水不导致净值断链（对账 `status=ok` 或明确降级）。
- **UI 验收**：可后置（P1）。

#### FR-L6 对账自证（分级阻断）—— P0

- 余额链校验；断链精确定位。分级：资产类指标硬阻断（`blocked`），回合级仍输出并标"含 N 笔可疑"。（v2.1 起断链优先自动补差，补差成功的区间降级为 `degraded`，见下）
- **断链自愈（v2.1）**：`GET /api/v1/reconciliation` 检出断链时，对**带账单行号的导入行**自动写入一条补差流水——`side=missing`、`source=system`、`security_code=CASH`、`security_name=对账补差`、`summary=对账自动补差`、`category=other`、`batch_id=null`；`trade_date`=断链行日期、`amount`=实际余额−(上一行余额+本行发生金额)、`cash_balance`=本行余额−本行发生金额、`line_no`=断链行行号（排序保证补差行排在断链行之前）。落库一次后余额链即闭合，重复调用不再补差（幂等）。手工录入行（`line_no` 为空）无法稳定插到两行之间，其断链不自动补差、保持 `blocked` 交人工修复。
- **状态语义（v2.1）**：本次补差成功或区间内存在 `side=missing` 行 → `status=degraded`（不再 `blocked`）；无断链且无补差行 → `ok`；无资金余额列 → `degraded`（200 降级语义，对应 `IMPORT_MISSING_CASH_BALANCE`）。
- **副作用声明**：该 GET 是自愈写端点——首次调用会写入补差流水，调用方/网关不得假设它只读或缓存其响应；补差流水可经 `GET /api/v1/trades?side=missing` 查询与审计（`source=system`、`batch_id=null`）。
- **API 验收**：`GET /api/v1/reconciliation?period=...` → 200，含 `status`、`break_count`、`filled_count`、`breaks[]{trade_id,line_no,trade_date,expected,actual,diff,filled,filled_trade_id}`；断链样例 fixture 首次调用：`break_count=1`、`filled_count=1`、`breaks[0].filled=true`、`breaks[0].filled_trade_id` 非空，`status=degraded`，且该 `filled_trade_id` 可在 `GET /api/v1/trades` 查到（`side=missing`、`source=system`、`summary=对账自动补差`）；再次调用：`break_count=0`、`filled_count=0`、`status=degraded`（区间内存在 `side=missing` 行），且补差行仍只有 1 条；无断链且无补差行 → `status=ok`；缺资金余额列 → `status=degraded`、`break_count=0`（200 降级语义，对应 `IMPORT_MISSING_CASH_BALANCE`）。
- **UI 验收**：必须（红字 + 断点定位）。

#### FR-L7 数据主权：导出、备份与删除 —— P0

- 用户：导出自己的全量数据（Excel + JSON，异步任务 + 下载链接，有效期）；注销即物理删除。
- 管理员：触发全量备份、查看备份状态与校验结果、执行恢复演练（**不可读交易明细**）。
- **API 验收**：`POST /api/v1/exports` → 202 + `job_id`；完成后 `GET /api/v1/exports/{id}/download` → 200 且 `Content-Disposition` 正确；再次下载在有效期内可用、过期后 410。导出内含 `meta.definition_version / data_as_of / schema_version`。`POST /api/v1/admin/backups` → 202，`GET /api/v1/admin/backups` 含 `checksum/verified`。用户 A 的 export 不能被 B 下载 → 404。
- **UI 验收**：必须（下载入口）。

#### FR-L8 查询 API（完整 REST）—— P0

- 游标分页（`limit` + `cursor`，返回 `next_cursor`）、过滤、排序白名单、字段裁剪（`fields=`）、`ETag`/`If-None-Match`。
- **API 验收**：`GET /api/v1/trades?limit=100` 返回 `next_cursor`；用游标翻完全集无重复无遗漏（与服务端总数比对）；非法 `sort` → 400 `INVALID_SORT_FIELD`；重复请求带 `If-None-Match` → 304；`fields=id,trade_date` 时响应不含其他字段。
- **UI 验收**：可后置。

### 5.3 度量层

> **口径完全沿用 v1.1 附录 A（A1–A27），不在本版重复变更**；以下只列**形态相关**的要求。

#### FR-R1 数据服务化 —— P0

- 度量层读取同一 PostgreSQL 实例内 `ledger` schema 的只读视图；多用户下按 `user_id` 过滤（仓储 + RLS）。
- **API 验收**：新导入提交后，`GET /api/v1/rounds` 在对应 `job` 成功后立即可见（无额外同步步骤）；跨用户不可见。
- **UI 验收**：不适用。

#### FR-R2 期初资产与回合构建 —— P0

- 口径同 v1.1（期初反推 `back_solved`、FIFO 稳定序、股息归属、送转/配股调整、桥接恒等式）。
- 构建改为异步 Job，可重算、可取消、可查看进度。
- **API 验收**：`POST /api/v1/jobs/rebuild-rounds` → 202 + `job_id`；`GET /api/v1/jobs/{id}` 终态 `succeeded`；`GET /api/v1/rounds?security_code=...` 的 `pnl` 与 fixture 期望差额 ≤0.01 元、收益率 ≤1bp；`GET /api/v1/rounds/{id}/fills` 返回 FIFO 配对 `buy_trade_id/sell_trade_id` 集合与期望一致；桥接恒等式校验端点返回 `balanced: true`。
- **UI 验收**：必须（回合列表与下钻）。

#### FR-R3 净值与收益（TWR + MWR）—— P0

- 口径同 v1.1（份额法、外部现金流白名单、XIRR）；`blocked` 区间不输出资产类数值。
- **API 验收**：`GET /api/v1/nav?from=&to=&benchmark=hs300_tr` → 200，`points[]{date, unit_nav, twr_daily, cashflow}`；`summary{mwr, twr, mwr_minus_twr, beta, alpha_annual, t_value, tracking_error, ir}`；被阻断区间返回 `status=blocked` 且无 `value`；日收益与独立 oracle 差额 ≤1bp。
- **UI 验收**：必须（净值卡）。

#### FR-R4 选股能力诊断 —— P0

- 口径同 v1.1（PIT、cohort、BHAR 20/60、行业归属）；异步计算 + 结果缓存。
- **API 验收**：`POST /api/v1/diagnostics/selection`（period）→ 202 + job；完成后 `GET /api/v1/metrics?domain=selection&period=...` 返回 BHAR 均值/中位数/t、样本 `n`、`sample_status`；`as_of` 回测断言：修改 `as_of` 后响应中不含晚于该日的财报数据（测试用 fixture）。
- **UI 验收**：可后置（P1）。

#### FR-R5 择时与绩效体检 —— P0

- 口径同 v1.1（基准锚定沪深300全收益 + 可选、β/α/IR、回撤族、费用口径、追涨分位、卖飞率 z 分数）。
- **API 验收**：`GET /api/v1/metrics?domain=timing&period=...&benchmark=hs300_tr` → 全部指标含 `value/n/ci_low/ci_high/sample_status/assumptions/data_as_of/definition_version`；缺少 `benchmark` 时使用默认并在响应 `assumptions` 中显式标注 `benchmark_id`；`n<30` 时相应指标带 CI 且 `sample_status` 非 `ok` 的项不返回结论性字段。
- **UI 验收**：必须（四数字卡 + 费用卡）。

#### FR-R6 行为偏差诊断 —— P0

- 口径同 v1.1（`DE = PLR − PGR` + bootstrap CI、换手、摊平、HHI 双口径）。
- **API 验收**：`GET /api/v1/metrics?domain=behavior` → `de` 含 `value/ci_low/ci_high/n`；"只卖盈不卖亏" fixture → `de > 0` 且 CI 不含 0；"无处置效应" fixture → CI 含 0。
- **UI 验收**：必须（行为卡）。

#### FR-R7 归因诊断 —— P0

- 口径同 v1.1：P0 五因子拆解（金额 + 占比 + 结论），P1 行业 Brinson（Carino）。
- **API 验收**：`GET /api/v1/attribution?period=...&model=five_factor` → 200，`factors[]{code, amount, ratio, rank}` 与 `residual`；因子金额之和 + residual 与区间总损益差额 ≤0.01 元；单因子主导 fixture 下 `rank[0].code` 正确。
- **UI 验收**：必须（"为什么亏"卡）。

#### FR-R8 统计可信度与最小样本 —— P0

- 口径同 v1.1（N<10 只出原始值、N<30 附 CI、日频 <60 交易日不出夏普/回撤/IR、核心指标固定 8–12 个、其余 `exploratory`）。
- **API 验收**：`n=5` 的区间 → 指标响应 `sample_status=insufficient` 且 `conclusion` 字段为 `null`（**不返回结论文案**）；`n=30` → `ci_low/ci_high` 非空；`exploratory=true` 的指标不出现在"行动建议"端点返回值中。
- **UI 验收**：必须（"可信度"标签）。

### 5.4 交付层

#### FR-D1 Web 应用（React SPA，薄客户端）—— P0

- 技术约束：React + TypeScript（strict）+ Vite + TanStack Query + React Router + ECharts；**API 客户端由 OpenAPI 生成**；不引入组件库重依赖（"轻前"）。
- 页面：登录/注册、看板（5 卡）、回合列表与下钻、指标详情（三段式 + 口径抽屉）、对账、导入向导、月报、日志、设置（Token/基准/隐私开关）、审计。
- **前端零口径**约束：前端不得实现指标计算与结论拼接（架构约束测试可验证）。
- **API 验收**（前端依赖的契约）：SPA 所需的全部数据端点已在《API-Catalog》登记且 100% 通过验收套件；前端构建产物由 `GET /` 提供且 `200`。
- **UI 验收**：必须（全部页面可访问）。

#### FR-D2 月度复盘报告 —— P0

- 异步生成：`POST /api/v1/reports` → `202 + job_id`；完成后提供 HTML（可打印 PDF）与元数据。
- 内容同 v1.1（概览、归因、最赚最亏、行为、重复错误、一条行动、口径脚注）；`blocked` 区间降级为"数据质量说明单"。
- **API 验收**：`POST /api/v1/reports` → 202；`GET /api/v1/reports/{id}` → `status=succeeded`，含 `html_url/pdf_url/checksum/data_as_of`；`GET /api/v1/reports/{id}/download?format=html` → 200 `text/html`；内容中每个数字与 `GET /api/v1/metrics` 同周期一致（脚本比对）；同一 `(period, spec_hash)` 重复请求复用同一报告（`report_id` 相同）；`blocked` 区间生成的是说明单（断言不含收益结论字段）。
- **UI 验收**：必须（生成 + 下载 + 预览）。

#### FR-D3 结论人话层与可追溯下钻 —— P0

- 后端返回三段式结构：`conclusion`（人话）/ `method`（一句算法）/ `drilldown`（可执行查询描述 + 关联明细端点）；前端只渲染。
- 术语映射在后端完成（页面不出现裸术语）。
- **API 验收**：`GET /api/v1/metrics/{id}/explain?period=` → 200 含 `conclusion/method/drilldown{endpoint, params}`；`conclusion` 不含裸术语（词表断言）；`drilldown` 指向的端点可直接调用并返回非空明细；`status!=ok` 时 `conclusion` 为 `null` 且含 `reason` 与 `what_to_do`。
- **UI 验收**：必须。

#### FR-D4 交易日志与闭环 —— P0 建实体 / P1 闭环

- 日志 CRUD + 关联回合；卖出后生成"对照提示"；季度汇总。
- **API 验收**：`POST /api/v1/journal` → 201；`GET /api/v1/journal?round_id=` → 200；回合平仓后 `GET /api/v1/journal/prompts` 返回待对照项；`GET /api/v1/journal/summary?quarter=2026Q3` → 200 含兑现率与理由类型统计；跨用户不可见。
- **UI 验收**：必须（列表 + 编辑 + 对照弹窗）。

#### FR-D5 纵向趋势对比 —— P1

- 按季度/月的胜率、盈亏比、`DE`、费用率、换手率趋势；**口径 major 版本变更处必须断点**。
- **API 验收**：`GET /api/v1/trends?metric=win_rate&granularity=quarter` → 200 `points[]{period, value, definition_version}`；存在口径断点时返回 `breaks[]{at, from_version, to_version}` 且 `points` 不跨断点连线（`segment` 字段区分）。
- **UI 验收**：可后置。

#### FR-D6 行为规则卡与提醒 —— P1

- 规则配置 API（阈值可编辑）+ 触发记录 + 提醒通道（站内；后续邮件/Webhook）。
- **API 验收**：`PUT /api/v1/settings/behavior-rules` → 200；触发后 `GET /api/v1/alerts` → 200 含 `rule_code/triggered_at/payload`；`POST /api/v1/alerts/{id}/ack` → 200；提醒内容不含标的级买卖建议（词表断言）。
- **UI 验收**：可后置。

#### FR-D7 对话复盘问答 —— P1

- 后端工具白名单取数 + **金额后端填充** + LLM 只组织语言 + 出站守卫断言；LLM 不可用时返回模板化结构化答案。
- **API 验收**：`POST /api/v1/chat/sessions` → 201；`POST /api/v1/chat/sessions/{id}/messages` → 200 含 `answer`、`citations[]`（每个数字的来源）、`redaction_applied`；LLM 关闭（配置为 none）时 → 200 且金额类问题仍正确；出站守卫测试：构造含金额的上下文 → 出站 payload 断言不含金额/账号（测试钩子暴露 payload）。
- **UI 验收**：必须（对话页 + 固定问题按钮）。

#### FR-D8 通知与分享 —— P2

- 站内通知 + 报告分享链接（签名、有效期、可撤销）。
- **API 验收**：`POST /api/v1/reports/{id}/shares` → 201 含 `url/expires_at`；匿名 `GET` 该链接 → 200（受限内容）；撤销后 → 404/410；分享仅暴露该报告，不暴露账号与其他报告。
- **UI 验收**：可后置。

---

## 6. 非功能需求

| 编号 | 需求 | 说明 |
|---|---|---|
| NFR-1 | 隐私与数据合规 | 用户数据仅本人可读；`admin` 不可读明细；LLM 出站不含金额与账号；支持导出与注销删除；账单原件加密存储（静态加密） |
| NFR-2 | 数据校验层 | 后复权/as-of 因子快照；涨跌停越界、复权因子跳变校验；证券主数据 PIT 本地化；数据来源留痕（source/fetched_at/as_of/checksum） |
| NFR-3 | 时点一致 | `announce_date ≤ 决策日`；`data_as_of` 不晚于 `as_of`；未走完窗口标 `pending_window` |
| NFR-4 | 数据可信优先 | 导入自动对账（0.01 元）；对账闸门分级；`MetricResult` 携带 `definition_version/inputs_hash/n/data_as_of` |
| NFR-5 | 幂等与可重放 | 导入 bag 去重 + 批次撤销；写接口支持 `Idempotency-Key`；派生数据可全量重建；同输入重复计算 diff=0 |
| NFR-6 | **认证授权与多租户隔离**（替换 v1.1 的"无鉴权"） | JWT + 轮换 refresh + PAT；仓储层强制 `user_id` 过滤 + PostgreSQL RLS 双层；越权按 404 处理；管理端点独立前缀且不可读明细 |
| NFR-7 | **测试与验收** | 分层覆盖率（后端同 v1.1 门禁）+ **API 验收套件覆盖 100% 端点** + **OpenAPI 契约测试** + 前端 Vitest/Playwright + 跨用户越权负向用例 100% 覆盖 |
| NFR-8 | **工程一致性（Monorepo）** | 单仓双栈：Python（uv workspace）+ Node（pnpm workspace）；OpenAPI 快照为契约单源；前端 SDK 由契约生成；CI 路径过滤；Conventional Commits |
| NFR-9 | 依赖 | 主行情源 akshare（须经校验层），可选灾备源；适配器结构；关键数据本地缓存；**不引入 Redis/Kafka** |
| NFR-10 | **性能与容量** | API 读 p95 ≤300ms、写 p95 ≤500ms（不含异步任务）；首页看板数据 ≤1s；全量重算 ≤60s（单租户 5 万行）；容量目标：**100 用户 / 500 万行**，可水平扩 API 与 worker |
| NFR-11 | **可维护性与 API 兼容** | 口径 `definition_version`；API 版本 `/api/v1`；破坏性变更需升版本并保留 N-1 至少一个大版本；零停机迁移（expand-contract） |
| NFR-12 | 可用性文案与错误模型 | 所有错误含 `code / user_message / what_to_do`；API 返回结构化错误；UI 不暴露栈 |
| NFR-13 | **可用性与灾备（服务级）** | 月度可用性 ≥99.9%；API ≥2 副本；滚动升级 5xx<0.1%；RPO ≤5min（WAL 归档）、RTO ≤30min；每季度恢复演练 |
| NFR-14 | **安全基线** | 全站 TLS；Argon2id 密码哈希；Secrets 仅环境变量/密钥管理；OWASP Top 10 对照自查；依赖漏洞扫描；Token 使用审计；CSP/HSTS |
| NFR-15 | **可观测性与运维** | 结构化日志（`request_id/user_id/job_id` 贯穿，敏感字段脱敏）；指标（QPS/延迟/错误率/队列积压/任务失败率/降级次数）；`/healthz`、`/readyz`、`/metrics`；trace 采样 |

---

## 7. 范围外（Non-goals）

- ❌ 团队/组织型租户、席位与计费（首版"一用户即一租户"）
- ❌ SSO / OAuth 第三方登录（预留）
- ❌ Kubernetes / 多集群 / 跨地域多活（首版单云主机 + 单 PG，热备可后续）
- ❌ 移动 App（Web 响应式即可）
- ❌ 多币种换算（字段预留）
- ❌ 日内执行质量分析（账单无时刻字段）
- ❌ 自动交易 / 实盘下单 / 实时行情推送
- ❌ 因子模型（FF3/FF5/Barra）——样本与规模不支持，后续评估
- ❌ 投资建议与标的推荐（合规红线）
- ❌ 重前端工程化：不做微前端、不做 SSR/同构渲染、不做自研组件库

---

## 8. 里程碑与验收

| 里程碑 | 范围 | 验收（API 优先） |
|---|---|---|
| **M0 平台底座** | FR-L1/L2/L3/L4/L6/L8 + FR-A1/A2 + 部署编排 + 迁移 | ① 云主机一键部署，`/readyz` 通过；② 注册/登录/越权负向用例全绿；③ preview→commit→撤销 全链路脚本通过；④ 对账断链检出（≤0.01 元）；⑤ OpenAPI 覆盖率 100%、契约校验通过；⑥ PAT 完成导入+查询 |
| **M1 四问 + 最小前端** | FR-R1~R3、R5、R6、R8 + FR-D1(读页面) + FR-D3 + FR-L5/L7 | ① 五问 API 可验收，全部含 `n/CI/data_as_of`；② 30 回合 golden 复算通过；③ 核心指标 `blocked` 时不返回值；④ 前端完成登录+看板+下钻；⑤ 导出可用 |
| **M2 归因与闭环** | FR-R4、R7 + FR-D2 + FR-D4 + FR-D5 + FR-D6 + FR-A3/A4 | ① 五因子恒等式与排序；② 月报异步生成+下载且数字与 API 一致；③ 日志闭环；④ 趋势断点；⑤ 限流与审计 |
| **M3 对话与打磨** | FR-D7 + FR-D8 + 性能与可用性达标 | ① 对话引用可追溯、断网/无 LLM 可答金额类问题；② 出站守卫断言通过；③ 压测达 NFR-10；④ 滚动升级 5xx<0.1% |

---

## 9. 风险与依赖

| 风险 | 影响 | 应对 |
|---|---|---|
| 多租户越权（最严重） | 泄漏他人交易数据 | 仓储强制过滤 + RLS 双保险 + 系统性越权负向测试（每个端点）+ 批量接口专项用例 |
| 云主机单点（API/PG） | 服务中断 | API ≥2 副本 + PG WAL 归档 + 每日全量 + 一键重建；热备列入 M2 评估 |
| 零停机迁移失败 | 升级中断 | expand-contract 三步 + 迁移前快照 + 回滚脚本 + 升级演练 |
| PostgreSQL 性能随租户增长下降 | 延迟劣化 | 索引与分区（按时间/user_id）、物化指标、连接池；容量与压测门禁 |
| 前端与 API 契约漂移 | 页面报错 | OpenAPI 单源 + 生成式 SDK + 契约快照 diff 阻断 + e2e |
| 行情源限频/不稳定 | 归因不可用 | 适配器 + 缓存 + 能力协商 + 降级矩阵 + 明示 `data_as_of` |
| LLM 成本/配额/幻觉 | 成本与合规 | 服务端统一 key + 用户级开关 + 工具强制取数 + 金额后端填充 + 出站守卫 |
| 数据合规（删除权） | 法律风险 | 注销即物理删除 + 审计留痕 + 导出权 |
| 单人维护双栈成本 | 交付变慢 | 重后轻前 + 前端薄 + 生成式 SDK + 严格分层与契约测试 |

---

## 10. 开放问题

| 编号 | 问题 | 备选 | 建议 |
|---|---|---|---|
| OQ-1 | 邮箱验证是否首版必做？ | (a) 必须 (b) 可配置关闭 | (b)，部署方决定 |
| OQ-2 | 是否引入对象存储存放归档/报告？ | (a) 本地卷 (b) S3 兼容 | (a) 首版；(b) 通过端口预留 |
| OQ-3 | PAT 是否支持细粒度 scope（读哪些域）？ | (a) read/write 二元 (b) 按域 | (a) 首版 |
| OQ-4 | 管理员是否可"以用户身份排障"？ | (a) 完全不可 (b) 需用户授权的一次性凭证 | (a) 首版，合规优先 |
| OQ-5 | 默认基准锁定策略 | 同 v1.1 OQ-1 | 默认沪深300全收益，可切换且历史分段 |
| OQ-6 | 多云/多可用区 | (a) 不做 (b) PG 热备 | (a) 首版，(b) M2 评估 |

---

## 附录 A：关键计算口径（**沿用 v1.1，A1–A27 全部不变**）

> 全局常量：`A = 244`、`ε_amt = 0.01 元`、`ε_r = 1bp`、`ε_cum = 5bp`、`rf` 参数化（默认 1 年期国债或货基 7 日年化，禁止默认 0）、`definition_version` 随口径变更递增（MAJOR.MINOR）。

| # | 术语 | 口径 |
|---|---|---|
| A1 | 期初资产 | `period_initial_cash = 首行资金余额 − 首行发生金额`；标 `assumption=back_solved`；失败则 `NAV_UNRELIABLE` |
| A2 | 外部现金流 | 仅含银证转账、配股缴款、场内基金申赎款；日末发生 |
| A3 | 内部收益 | 股息、红利税、送转、利息归本、佣金/印花税/其他费、融资利息（不折份额） |
| A4 | 单位净值 | `NAV_u,t = (Σ证券市值+资金余额)/份额_t`；`份额_t = 份额_{t−1} + CF_t/NAV_u,t` |
| A5 | TWR | `TWR = Π(1+r_t) − 1`，`r_t = NAV_u,t/NAV_{u,t−1} − 1`；年化 `(1+TWR)^(365/自然日)−1` |
| A6 | MWR | XIRR；同时输出 `MWR − TWR` 及解释 |
| A7 | 回合 | 同证券 FIFO 配对；稳定序 `(交易日, 文件行号, 摘要, 数量, 均价)` |
| A8 | 回合 pnl | `Σ卖出发生金额 − Σ对应买入发生金额绝对值 + 该股数对应股息净额（税后）`；收益率 `= pnl/Σ买入成本（含费）` |
| A9 | 除权处理 | 送转放大数量、总成本不变；配股缴款计入买入成本；跨除权一致性等式 ≤`ε_amt` |
| A10 | 胜率 | 已平仓 `pnl>0` 占比，并列全样本胜率（含浮盈亏）；附 n 与 Wilson 95% CI |
| A11 | 盈亏比 | `平均盈利金额 / abs(平均亏损金额)`，并列 median 版 |
| A12 | 期望值与盈亏平衡胜率 | `E = p·W − (1−p)·L`；`p* = 1/(1+盈亏比)` |
| A13 | 平均持仓天数 | 交易日计（卖出日 − 买入日，同日记 1）；并列 median/自然日/资金占用天数 |
| A14 | 盈亏集中度 | Top3 盈利 / 全部盈利；辅 CR1、Gini；分母仅含 `pnl>0` |
| A15 | 处置效应 | `DE = PLR − PGR`；bootstrap 95% CI；`DE>0` 为处置效应；持有天数差为辅助 |
| A16 | 卖飞率 | `z = (R_after − R_bench,h)/(σ_i日·√h) > 1.0`，`h∈{5,10,20}`；停牌>50%/退市/未走完剔除 |
| A17 | 追涨分位 | `(P_buy − min20)/(max20 − min20)`，复权序列对齐，窗口 `T−20..T−1`；并列 z 版 |
| A18 | 最大回撤族 | `MDD` + 持续期/恢复期/水下占比/Ulcer/Calmar |
| A19 | 仓位 HHI | `HHI_eq` 与 `HHI_total`（含现金）；区间给均值/最大/期末 |
| A20 | 夏普 | `(r̄_d − rf_d)/σ_d × √A`，`A=244`，rf 参数化 |
| A21 | 费用合计与占比 | 佣金+印花税+其他费（展开子项）；禁止二次扣费；主分母=双边成交额 |
| A22 | 费用回本门槛 | `年化费用 / 年化平均持仓市值`；输出"吃掉 TWR 的 Y%" |
| A23 | 基准超额 | 默认沪深300全收益；同起点、同日历、全收益；算术与几何并列；β、α、t、IR |
| A24 | BHAR | `BHAR_h = Π(1+r_i) − Π(1+r_b,h)`，`h∈{20,60}`；事件日聚类 t 值 |
| A25 | Brinson（P1） | 配置/选择/交互；多期 Carino 链接 |
| A26 | 统计门槛 | `N<10` 只出原始值；`N<30` 附 CI；日频 `<60` 交易日不出夏普/回撤/IR |
| A27 | dedupe_key | `hash(user_id + 归一化股东账号 + 日期 + 证券代码 + 摘要 + 数量 + 均价 + 发生金额)`；空账号归一哨兵；同键多笔按出现次序计数去重 |

---

## 附录 B：数据字典与多用户字段（增量）

| 项 | 规定 |
|---|---|
| 金额表示 | API 传输为**定点数字符串**（如 `"1234.56"`，2 位小数），内部以标度整数存储；禁止科学计数法 |
| 价格/数量 | 价格 6 位小数、数量 4 位小数，均为字符串 |
| 比率 | 以十进制小数返回（如 `0.4321`），容差比较按 bp |
| 时间 | 业务日 `YYYY-MM-DD`（Asia/Shanghai 口径）；时间戳 ISO 8601 UTC |
| 多用户列 | 所有业务表必含 `user_id`（UUID 或 BIGINT）；唯一约束与索引均以 `user_id` 前缀 |
| 软删 vs 物理删 | 业务操作软删（可追溯可恢复）；**账号注销时物理删除该用户全部数据**（级联 + 审计） |
| 敏感字段 | 股东账号静态加密存储、接口返回脱敏（末 4 位）；密码仅存 Argon2id 哈希 |
| 股东账号去重 | 空值归一哨兵，禁止空哈希合并（沿用 v1.1 R-B8 修复） |

---

## 附录 C：术语人话对照（沿用 v1.1，服务端生成）

页面与 API 的 `conclusion` 字段统一使用人话措辞（FIFO 回合 → "一次完整买卖"、DE → "赚钱跑得快、亏钱死扛的倾向"、`NAV_UNRELIABLE` → "这段时间的数据不敢给你算收益" 等），裸术语只允许出现在"口径详情"中。

---

## 附录 D：v1.1 → v2.0 影响面映射

| v1.1 条目 | v2.0 处置 | 主要连带改动 |
|---|---|---|
| FR-L1 单机部署 | 重写为云主机服务化 | 架构：部署拓扑、健康检查、滚动升级 |
| FR-L2 seed 单用户 | 重写为多用户认证 | 数据模型 `user_id`、RLS、审计、越权测试 |
| FR-L3~L8 | 语义保留，API 优先 + 两阶段导入 | API-Catalog、幂等键、游标分页 |
| FR-R1 同库视图 | 改为跨 schema 只读 | PG 双 schema |
| FR-R2~R8 口径 | **完全不变** | 计算改异步 Job；结果带血缘 |
| FR-D1 SSR | 重写为 React SPA | 前端工程、生成式 SDK、前端测试 |
| FR-D2 月报 | 异步生成 + 下载 | 文件存储、签名链接 |
| FR-D7 对话 | 改为后端填充 + 出站守卫 | LLM Provider 端口、脱敏测试 |
| NFR-6 无鉴权 | 替换为认证授权与隔离 | 删除"服务仅绑本机"约束 |
| NFR-13~15 | 新增 | SLO、安全基线、可观测性 |
| 单机 ADR-0001/0006/0007 | **被 v2 ADR 取代** | 见 architecture/v2/ADR.md |
