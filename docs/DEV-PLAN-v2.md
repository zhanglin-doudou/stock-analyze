# 开发清单（DEV PLAN）v2.0

> **命名约定**：期次写全称 `Period1/Period2/Period3`，期次任务 ID 为 `Period1-01`；优先级仍可简写 `P0/P1/P2/P3`。


> 依据：[PRD-v2.md](prd/PRD-v2.md)（28 FR + 15 NFR）｜[Architecture-v2.md](Architecture-v2.md)｜[API-Catalog.md](architecture/v2/API-Catalog.md)（82 端点）
> 编排原则：**本地可验证优先，部署与交付最后做**。
> 估时口径：1 名资深全栈 + AI 编码辅助；1 人日 = 6 小时有效开发。总计 **≈ 93.5 人日**（单线程约 19 周；并行轨道可压到 ≈ 12–14 周）。

---

## 0. 原则与约定

### 0.1 三条铁律

1. **每期结束必须"跑得起来 + 点得开 + 断言绿"**：一条 `make verify-pN` 命令跑完全部关键断言，输出可勾选清单；跑不绿就不进下一期。
2. **契约先行**：先定 OpenAPI 骨架 → 生成 TS client → 前后端并行。前端不因为后端未完成而阻塞，后端不因为前端未完成而无法验收。
3. **外部依赖可替换**：行情走**录制回放**、LLM 默认关闭——本地无网络也必须能验完一期到三期的全部核心逻辑。真实外部服务只在"可选验证"里跑。

### 0.2 本地怎么跑（与生产形态的差异）

| 组件 | 本地开发 | 生产（交付阶段才做） |
|---|---|---|
| PostgreSQL | **容器**（`docker compose up -d db`），固定 16 + 固定端口 | 同镜像，数据卷 + WAL 归档 |
| api / worker | **宿主机进程**（uvicorn `--reload` / worker loop），热重载快 | 容器镜像，≥2 副本 |
| 前端 | Vite dev server（5173，`/api` 代理到 8000） | 构建产物 + nginx 同源 |
| 行情 | `MARKET_MODE=replay`（读 `fixtures/market/` 快照） | `live`（akshare + 缓存 + 校验） |
| LLM | `LLM_PROVIDER=none`（走模板降级） | 配置真实 provider |
| TLS/域名 | 不需要（localhost） | nginx + 证书（交付阶段） |

> 好处：本地只依赖 Docker 一个外部条件；重启快、可随意改表、可断网。

### 0.3 本地可验证性矩阵（回答"现在能不能验"）

| 能力 | 本地验证方式 | 是否需外部依赖 |
|---|---|---|
| 注册/登录/刷新/PAT | `curl` + pytest 验收套件 | 无 |
| **多租户越权** | `make seed-tenants` 造体验账号+B/admin，A 的 token 打 B 的资源断言 404 | 无 |
| 账单解析/预览/去重/撤销 | `fixtures/market/*`（合成账单/行情）+ 断言行数 | 无 |
| 口径指标（回合/净值/费用/行为） | `fixtures/market/` 冻结快照 + `FrozenClock` → 期望值写死 | 无 |
| 异步任务/进度/取消 | 本地 worker + 轮询/SSE | 无 |
| 报告生成/下载 | 本地生成 HTML，浏览器直接打开 | 无 |
| 前端页面 | Vite dev + demo 账号 | 无 |
| LLM 对话（降级路径） | `LLM_PROVIDER=none` → 模板答案仍含金额与来源 | 无 |
| LLM 对话（真实模型） | 配 key 后手测（可选） | 需 key |
| 备份/恢复 | 本地 PG 容器 + 本地卷，跑一次 restore 演练 | 无 |
| 多副本滚动升级 / 5xx<0.1% | 本地 compose 起 2 api 副本，反复重启采样 | 部分可验 |
| nginx 同源 + 自签 TLS | 本地 nginx + 自签证书 | 无 |
| **云主机 / 真实 PITR 时延 / 对象存储 / 跨机器恢复** | ❌ 只能部署后验证（见 §5） | 需云资源 |

### 0.4 并行轨道

| 轨道 | 负责内容 | 可并行的前提 |
|---|---|---|
| **B 后端** | 领域计算、API、队列、DB | — |
| **F 前端** | 页面、交互、图表 | OpenAPI 快照已定（一期 Period1-06 后即可并行） |
| **D 数据/夹具** | 账单生成器、行情快照、期望值（oracle） | 口径公式已定（PRD 附录 A） |
| **T 测试/门禁** | 验收套件、越权矩阵、契约检查、CI | API 骨架可跑即开始 |

### 0.5 每期的 Definition of Done

- [ ] `make lint` / `make typecheck` 零错误（后端 ruff+mypy strict；前端 eslint+tsc）
- [ ] 本期末端点的 API 验收用例 100% 通过（含幂等、越权、错误码）
- [ ] `make verify-pN` 一条命令全绿，并打印勾选清单
- [ ] 前端对应页面可点开、可操作、错误态可读（含 `blocked` 状态不显示数字）
- [ ] 本期新增/变更端点：契约快照已更新、示例与错误码齐全、TS client 已重生成
- [ ] `docs/` 中受影响文档已同步（端点/口径/门禁）

---

## 1. 总体路线

```
一期 29.0d ─── 二期 33.5d ─── 三期 23.0d ─── 交付 8.0d
  │             │             │             │
  能登录        能看四问       能归因/问答     能上线
  能导入        能出月报       能闭环        能演练恢复
  能断言        前端五卡       运维能力       可回滚
  ↓             ↓             ↓             ↓
verify-p1     verify-p2     verify-p3     smoke+drill
```

**关键路径（不可并行）**：PG/RLS → 认证+租户上下文 → 导入解析 → 任务队列 → FIFO 回合 → 指标引擎 → 报告 → 部署。

**最先做的三件事**（决定后面能否并行）：① OpenAPI 骨架 + 生成 client；② 多租户上下文与 RLS 闸门；③ 行情回放夹具。

---

## 2. 一期：跑通、看得见、可断言（≈29 人日）

**目标**：本地 `make dev` 之后，浏览器能注册登录、上传真实账单、看到流水、撤销导入；`make verify-p1` 全绿；82 个端点里一期的 30 个有完整验收。

### 2.1 验收剧本（5 分钟）

```bash
make dev            # 起 PG 容器 + api(热重载) + worker + web
make migrate        # 建 3 个 schema + 表 + RLS
make seed-tenants   # 造固定体验账号 1234@dev.com + A/B/admin + 各自账单
make verify-p1      # 一期全部断言

# 手工走查
open http://localhost:5173
# 1) 用 1234@dev.com 登录（密码 1234）
# 2) 上传 fixtures/market/bill_huatai.xlsx → 预览显示识别列/行数/隔离数与实际一致
# 3) 确认导入 → 任务进度 → 流水列表出现数据
# 4) 点「撤销本次导入」→ 列表行数回到导入前
# 5) 换 b@demo.example.com 登录 → 看不到 1234@dev.com 的任何数据（越权拦在 404）
# 6) curl 用 A 的 PAT 调 B 的资源 → 404
```

### 2.2 任务清单

| ID | 任务 | 产出 | 依赖 | 本地验证 | 人日 |
|---|---|---|---|---|---|
| **Period1-01** | Monorepo 骨架与工具链 | `backend/`(uv workspace: contracts/core/ledger/review/delivery/platform + services/api,worker)、`frontend/`(pnpm: apps/web + packages/api-client)、`Makefile`、ruff/mypy/eslint/tsc、import-linter 基线 | — | `make lint && make typecheck` 零错误；`import-linter` 分层契约通过 | 1.5 |
| **Period1-02** | 本地环境编排 | `infra/compose/docker-compose.dev.yml`（仅 PG）、`.env.example`、`make dev/db-up/db-reset` | Period1-01 | `make db-up` 后 `psql` 可连；`make db-reset` 可重建 | 1 |
| **Period1-03** | DB schema + 迁移 + 多租户隔离 | Alembic 多 schema 基线（`platform`/`ledger`/`review`）、`user/refresh_token/api_token/job/import_batch/trade/quarantine_row/summary_rule/reconciliation`、RLS 策略 `tenant_isolation`、应用角色（非 owner、无 BYPASSRLS） | Period1-02 | `make migrate` 幂等；`make test-rls`：未设 `app.user_id` 读租户表返回 0 行 | 2.5 |
| **Period1-04** | contracts + core 值对象 | `contracts`：错误码枚举（47 个 seed）、常量（`EPS_AMT/EPS_R/EPS_CUM/A`）、DTO 基座；`core`：标度整数 `Money/Qty/Price/Ratio`、`Clock` 端口、确定性随机 | Period1-01 | `make test-core`：标度整数属性测试（往返/舍入/溢出）+ core 覆盖 100% | 2 |
| **Period1-05** | API 骨架 | FastAPI 装配根、中间件链（request_id→认证→租户→限流占位）、RFC7807 错误处理（`user_message/what_to_do` 必填）、`/healthz` `/readyz` `/version` `/api/v1/errors`、`/api/v1/openapi.json`、`GET /` 占位 | Period1-03/Period1-04 | `curl /readyz` 含 `db:ok, schema_version`；迁移滞后时 503；`/version` 与 openapi `info.version` 一致 | 1.5 |
| **Period1-06** | 契约链路 | OpenAPI 快照生成与提交、`make api-check`（示例/错误码/operationId 完整性）、`openapi-typescript` 生成 `packages/api-client`、契约 diff 门禁 | Period1-05 | `make api-check` 退出码 0；故意删示例 → 非 0（元测试）；`make sdk` 后 git diff=0 | 1.5 |
| **Period1-07** | 认证与 PAT | 注册/登录/刷新（轮换+复用检测）/登出/me、Argon2id、PAT 创建（明文一次）/列表/撤销、登录失败限速 | Period1-05/Period1-06 | API-001~005、007~009 验收全绿；refresh 复用 → 401；PAT 撤销 → 401 | 2.5 |
| **Period1-08** | 租户上下文 + 仓储基座 | `TenantContext`（仅从 token 取，不可由参数覆盖）、`SET LOCAL app.user_id` + 连接归还 `RESET`、Repository 基类强制注入 `user_id`、越权测试骨架 | Period1-03/Period1-07 | **连接池串号测试**：并发 A/B 请求交错 100 次，无一次读到对方数据 | 1.5 |
| **Period1-09** | 华泰解析器 + 预览 | `BillParser` 注册表、列别名、多日期格式、表头探测、资金余额缺失/首行非买入/未知摘要预检、`POST /imports/preview`（不落库） | Period1-08 | API-010 验收；预览后 `trade` 表行数不变；`fixtures/market` 各格式解析成功 | 2 |
| **Period1-10** | 导入提交 + 去重 + 撤销 | `POST /imports`（202+job）、bag 去重（`dedupe_key+occurrence_idx`）、隔离区落库、批次记录、`revoke` 同步软删 + 失效标记、`GET imports/{id}`、`quarantine` | Period1-09/Period1-11 | API-011~013、015 验收；重复上传 inserted=0；同日同价同量多笔不吞并；撤销后行数回退 | 2.5 |
| **Period1-11** | 任务队列 + worker + jobs 端点 | `platform.job`、`FOR UPDATE SKIP LOCKED` 出队、租约/心跳/退避、单飞部分唯一索引、worker 入口、`GET /jobs`、`GET /jobs/{id}`、`cancel`、SSE `events` | Period1-03 | API-061~064 验收；kill worker 后任务被回收重跑；同参并发只跑一个 | 2 |
| **Period1-12** | 流水查询与维护 | 游标分页、过滤/排序白名单/`fields`、`ETag`/304、`GET /trades`、`GET /trades/{id}`、`PATCH`（版本冲突 409）、软删/恢复、`history` | Period1-08 | API-017~021、080 验收；翻页无重无漏；旧 version PATCH → 409 | 1.5 |
| **Period1-13** | 分类、隔离区、对账 | 默认摘要规则 seed、分类流水线、打新流、未知摘要入隔离区、余额链对账 `GET /reconciliation`（断链定位到行） | Period1-10 | API-014、026 验收；断链 fixture `diff≤0.01` 且 `trade_id` 正确 | 2 |
| **Period1-14** | 数据夹具与回放 | `MARKET_MODE=replay` 适配器骨架 + `fixtures/market/` 快照格式、10 年合成账单生成器、`make seed-tenants`/`seed-demo`、`FrozenClock` | Period1-04 | `make seed-tenants` 幂等；断网跑 `make seed-demo` 成功 | 1.5 |
| **Period1-15** | 前端骨架 | Vite+React+TS+Router+TanStack Query、生成 client 接入、登录/注册页、上传+预览+结果页、流水列表、错误边界（`code/user_message/what_to_do`）、路由守卫、token 内存+静默刷新 | Period1-06/Period1-07 | 浏览器完成 §2.1 手工走查 1–5 步 | 2 |
| **Period1-16** | 一期验收套件 | `backend/tests/api/`（一期端点逐条）、越权负向、`make verify-p1`、`make demo-p1`、`docs` 同步 | 全部 P1 | `make verify-p1` 全绿并打印勾选清单 | 1.5 |

### 2.3 一期不做（范围围栏）

行情实时拉取、指标计算、回合、净值、报告、导出、日志、对话、限流实装（仅留中间件占位）、备份、Docker 生产镜像。

### 2.4 一期风险

| 风险 | 应对 |
|---|---|
| RLS 与连接池串号（最隐蔽） | Period1-08 专项并发测试；连接归还钩子 `RESET`；越权用例进 CI |
| 契约与实现漂移 | Period1-06 门禁先立；生成 client 后手写类型即报错 |
| 解析器被真实账单打脸 | 先拿 3 份真实脱敏账单做 fixture；字段别名表可配置 |
| 估时超支 | 一期若超时，先砍 SSE（改轮询）与 `history` 端点 |

---

## 3. 二期：口径、四问与月报（≈33.5 人日）

**目标**：本地能算出三域指标与五因子归因（数字与 oracle 一致），前端看板 5 卡可点、可下钻，能生成并下载月报。

### 3.1 验收剧本

```bash
make seed-demo && make rebuild      # 导入 + 重建回合/净值/指标
make verify-p2                      # 口径 golden + 端点验收 + 前端 smoke

open http://localhost:5173/dashboard
# 1) 五张卡片有数：净值 vs 沪深300、胜率/盈亏比/持仓天数/集中度、费用、处置效应、最赚最亏
# 2) 点胜率 → 下钻抽屉显示「怎么算的 + 涉及哪几笔」
# 3) 点「生成本月复盘」→ 进度 → 下载 HTML → 离线打开，数字与页面一致
# 4) make demo-broken  → 看板资产类卡片变红字占位、不再显示收益数字
# 5) curl "…/metrics?ids=win_rate&period=2026-09" | jq '.items[0] | {n,ci_low,sample_status}'
```

### 3.2 任务清单

| ID | 任务 | 产出 | 依赖 | 本地验证 | 人日 |
|---|---|---|---|---|---|
| **Period2-01** | FIFO 回合引擎 | `core/fifo`：稳定序配对、分批减仓、股息归属（除息日按持股数）、送转/配股调整、桥接恒等式；独立 oracle `tests/oracle/naive_fifo.py` | Period1-04 | 属性测试：数量/成本守恒；28 条边界 fixture 与 oracle 对拍 ≤0.01 元 | 3 |
| **Period2-02** | 回合构建与数据落库 | `review.round/round_fill`、`rebuild-rounds` job、脏窗口算法与安全回看、`GET /rounds`、`/{id}`、`/{id}/fills`、规则 CRUD（补一期） | Period2-01/Period1-11 | API-027~031、022~025、016 验收；加仓/分批场景 fills 与期望一致 | 2.5 |
| **Period2-03** | 净值与收益 | `review.nav_daily/cashflow`、期初资产反推（`back_solved`）、份额法 TWR、XIRR/MWR、`MWR−TWR`、`GET /nav` | Period2-02 | API-032 验收；大额入金遇上涨方向正确；`NAV_UNRELIABLE` 区间无资产值；日收益 vs oracle ≤1bp | 3 |
| **Period2-04** | 行情适配与校验 | `MarketDataSource` 能力协商、akshare live 实现、本地缓存 L2、`provenance` 留痕、后复权/as-of 因子、涨跌停与复权跳变校验、replay 切换 | Period1-14 | `make test-market`：恶意 fixture（复权不一致/越界/因子跳变）被拦截；`MARKET_MODE=live` 可选手测 | 2.5 |
| **Period2-05** | 指标注册表与执行引擎 | `MetricDef` 13 字段、注册发现、框架代算 `n/CI/inputs_hash/sample_status/status`、DAG 拓扑与环检测、`definition_version` 失效、`metric_result` 物化、门禁状态机 | Period2-03/Period2-04 | `make test-engine`：N<10 → `insufficient` 且 `conclusion=null`；口径 bump → 新版本行 + 旧行保留 + `is_stale` 单测 | 6.5 |
| **Period2-06** | 指标端点 + 三段式 | `GET /metrics`（批量/过滤）、`/{id}`、`/explain`、`/drilldown`、`ETag`、`data_as_of` 透出 | Period2-05 | API-033~035、079 验收；`blocked` 时无 `value`；`conclusion` 无裸术语（词表断言） | 3 |
| **Period2-07** | 三域指标实现 | 绩效（胜率含全样本、盈亏比、持仓天数、集中度 CR1/Gini、费用占比/回本门槛）、择时（追涨分位、卖飞率 z、β/α/IR、回撤族）、行为（PGR/PLR DE + bootstrap、换手、摊平、HHI）、选股（PIT cohort BHAR 20/60） | Period2-05 | 每个指标 1 份 golden（期望值来自手算表/独立实现）+ mutation ≥80% | 3.5 |
| **Period2-08** | 五因子归因 | `core/attribution/five_factor`：选股/板块/买点/卖点/费用 +残差、`GET /attribution`、排序结论生成 | Period2-07 | API-036 验收：Σ因子+残差 = 区间损益（≤0.01）；单因子主导 fixture 排序正确 | 2 |
| **Period2-09** | 月度报告 | `report` 表、`ReportSpec`、区块组装（概览/归因/最赚最亏/行为/重复错误/一条行动/口径脚注）、异步生成、HTML 自包含、`ETag`/下载、`blocked` 降级为说明单 | Period2-06/Period2-08 | API-039~042 验收；离线打开 HTML；报告数字与 `/metrics` 逐一相等（脚本比对）；同 `spec_hash` 复用 | 2.5 |
| **Period2-10** | 数据导出 | `POST /exports`（异步）、Excel+JSON 打包、签名下载与 410 过期 | Period2-09 | API-059/060 验收；导出→清库→导入后行数/回合/净值一致 | 1 |
| **Period2-11** | 设置 | `GET /settings`、`PUT /settings/benchmark`、`PUT /settings/privacy`、默认基准沪深300全收益、切换即触发失效重算 | Period2-03 | API-055~057 验收；切基准后历史趋势分段 | 1 |
| **Period2-12** | 前端看板与下钻 | 五卡（净值/四数字/费用/行为/最赚最亏）、状态徽标（ok/stale/blocked）、下钻抽屉、回合列表与详情、对账横幅、可信度标签 | Period2-06 | 浏览器走查 §3.1 第 1–2、4 步；`blocked` 卡片不渲染数值（快照断言） | 3 |
| **Period2-13** | 前端报告与导出页 | 报告列表/预览/下载、导出任务与下载、空态与失败态 | Period2-09 | 浏览器走查 §3.1 第 3 步 | 1.5 |
| **Period2-14** | 二期验收与越权全量 | 二期端点验收补齐、**受保护端点越权用例 100%**、golden 回归、`make verify-p2` | 全部 P2 | `make verify-p2` 全绿；`make test-isolation` 覆盖全部受保护端点 | 2 |
| **Period2-15** | 前端零口径门禁 | ESLint 自定义规则（禁算术/禁结论拼接/禁裸 fetch/禁裸术语）+ AST 断言 | Period2-12 | 故意在页面算一次 `a-b` → `make lint` 失败 | 1 |
| **Period2-16** | 资产表（每日资产快照） | `review.asset_daily/asset_build_state` + 迁移 0006、共享估值原语（`valuation.py`）、增量构建（变更指纹 + 水位线 + 45 天安全窗）、`GET /assets`、`POST /assets/build`、worker 行情装配、`docs/DATABASE.md` | Period2-03 | API-081/082 验收；增量结果与全量逐行相等；`GET /assets` 分页/过滤/blocked 显式 null、`build` 摘要齐全；门禁 82/82 | 2.5 |

### 3.3 二期不做

Brinson 多期链接、日志/趋势/提醒、分享、对话、限流实装、审计查询、管理端备份、生产镜像。

### 3.4 二期风险

| 风险 | 应对 |
|---|---|
| 口径实现与期望值互相"自证" | 期望值必须来自**手算表或独立朴素实现**，禁止用被测代码生成 |
| 行情语义漂移导致数字不稳 | replay 固定快照为准；live 仅作可选抽查 |
| 前端悄悄算数 | Period2-15 门禁提前到二期收尾（不等三期） |
| 指标太多导致战线拉长 | 核心指标锁 8–12 个进"行动建议"，其余标 `exploratory` |

---

## 4. 三期：闭环、问答与运维能力（≈23 人日）

**目标**：日志闭环、趋势、提醒、分享、对话（含降级）、审计、限流、管理端与性能达标本地位。

### 4.1 验收剧本

```bash
make verify-p3
# 1) 写一条日志 → 平仓后 /journal/prompts 出现对照项 → 季度 summary 有兑现率
# 2) /trends 返回 points 含 definition_version；制造口径 bump 后出现 breaks
# 3) 设行为规则 → 造触发行情 → /alerts 命中 → ack
# 4) LLM_PROVIDER=none 下问"这个月赚了多少"→ 仍返回金额 + citations
# 5) 连打同一端点触发 429 + Retry-After；/audit-logs 含 login 与 token.revoke
# 6) make load-test → p95 达标报告
```

### 4.2 任务清单

| ID | 任务 | 产出 | 依赖 | 本地验证 | 人日 |
|---|---|---|---|---|---|
| **Period3-01** | 交易日志与闭环 | `journal_entry`、CRUD、平仓事件触发对照提示、季度 summary | Period2-02 | API-046~051 验收；事件→提示可回放 | 2 |
| **Period3-02** | 纵向趋势 | `GET /trends`、按季度聚合、`definition_version` 断点（`breaks`+`segment`） | Period2-06 | API-037 验收；口径 bump 后出现断点且不跨段连线 | 1.5 |
| **Period3-03** | 行为规则与提醒 | 规则阈值配置、`AnomalyDetected` 评估器、`alerts`、`notifications`、ack；文案白名单（无买卖建议） | Period2-07/Period3-01 | API-052~054、058 验收；词表断言通过 | 2.5 |
| **Period3-04** | 报告分享 | 签名分享链接、匿名受限读取、撤销、过期 | Period2-09 | API-043~045 验收；匿名只能看该报告；撤销后 410 | 1 |
| **Period3-05** | 审计 | `audit_log` 全写操作留痕、本人/管理员双视图、admin 无明细 | Period1-07/Period1-10 | API-065/066 验收；跨用户不可见 | 1.5 |
| **Period3-06** | 管理端 | `/admin/system/status`、备份触发/列表/校验、恢复演练端点（不读明细） | Period3-05 | API-067~070 验收；admin 读 trades → 403 | 2 |
| **Period3-07** | 对话子系统 | `ToolGateway` 白名单、`ResponseComposer` 本地填值、`EgressGuard` 出站断言、Provider 适配、模板降级、`chat` 端点、`llm_call` 留痕 | Period2-06 | API-077/078 验收；`LLM_PROVIDER=none` 仍答金额；构造含金额上下文 → 出站被拦（测试钩子） | 3 |
| **Period3-08** | 归因增强 | Brinson-Fachler（配置/选择/交互）+ Carino 多期链接 + 行业偏离 | Period2-08 | 三效应之和与区间超额 ≤1bp | 2 |
| **Period3-09** | 限流 | 按用户/PAT 令牌桶（PG 计数）、`429+Retry-After+X-RateLimit-*`、并发任务上限 | Period1-11 | 第 N+1 次 429；同参重算返回同 `job_id` | 1.5 |
| **Period3-10** | 性能 | 索引复核、连接池算式、必要处分区、查询计划审查、`make load-test` 脚本 | Period2-14 | 看板 50 并发 p95 ≤300ms；导入 5000 行任务 ≤30s；全量重算 ≤60s | 2 |
| **Period3-11** | 可观测性 | 结构化日志（脱敏）、Prometheus 指标（队列/延迟/降级/越权拒绝）、trace、告警阈值表 | Period3-06 | `/metrics` 有数；日志含 `request_id/user_id/job_id` 且无金额明文 | 2 |
| **Period3-12** | 端到端与三期满验 | Playwright 8 条关键路径、`make verify-p3`、全量变异测试 | 全部 P3 | `make e2e` 全绿；core 变异分 ≥80% | 2 |

### 4.3 三期不做

云主机、TLS、镜像发布、PITR 实演、多副本压测（留到交付）。

---

## 5. 交付阶段（最后做，≈8 人日）

> 前 3 期一律不写 Dockerfile、不配 nginx、不碰证书。**本地能验完的东西，不要提前上云。**

| ID | 任务 | 产出 | 本地可预演？ | 人日 |
|---|---|---|---|---|
| **D-01** | 生产镜像与编排 | `Dockerfile.api`/`worker`（同镜像多入口）、多阶段构建、`docker-compose.prod.yml`（nginx/api×2/worker/postgres/migrate）、健康检查、资源限制 | ✅ 本地 compose 起全套 | 1.5 |
| **D-02** | nginx 与同源 | 静态资源、`/api` 反代、gzip、缓存头、安全头（HSTS/CSP）、自签证书演练 | ✅ 本地 https://localhost | 1 |
| **D-03** | 迁移与零停机 | `migrate` 一次性任务、`readyz` 校验 schema 版本、expand-contract 演练脚本、回滚说明 | ✅ 本地跑两轮发布 | 1.5 |
| **D-04** | 备份与恢复 | WAL 归档配置、每日全量、`pg_verifybackup` 校验、**PITR 恢复演练脚本** `make restore-drill` | ✅ 本地容器演练 | 1.5 |
| **D-05** | 安全加固 | 密钥注入、应用角色权限复核、依赖漏洞扫描、OWASP 自查清单、日志脱敏复核 | ✅ 扫描本地镜像 | 1 |
| **D-06** | CI/CD 与发布门禁 | 四段流水线（fast/full/nightly/release）、镜像构建与缓存、发布清单、回滚开关 | ✅ 本地 `act` 或手动跑 | 1 |
| **D-07** | 交付文档 | 部署手册、Runbook（SOP-1~10）、容量与扩容、验收报告模板 | — | 0.5 |

**只有上云后才能验证（必须写进交付验收单）**：

| 项 | 验证方式 |
|---|---|
| 真实云主机一键部署 | 新开一台干净 VM，按手册从零部署，≤30 分钟 |
| 真实 TLS/域名 | 证书链、HSTS、HTTP→HTTPS 跳转 |
| 真实 PITR 时延与 RTO ≤30min | 在云上做一次全链路恢复计时 |
| 滚动升级 5xx <0.1% | 云上压测 + 滚动发布采样 |
| 多副本故障转移 | 杀掉一个 api 副本，请求无失败 |
| 外部行情源真实限频表现 | live 模式跑一周，观察熔断与缓存命中 |

---

## 6. 如果时间不够：可裁剪顺序

按"砍了也不影响验证主链路"排列，从后往前砍：

| 可砍项 | 期次 | 影响 | 替代 |
|---|---|---|---|
| `jobs/{id}/events` SSE | 一期 | 进度体验 | 轮询（前端已有兜底） |
| `trades/{id}/history` | 一期 | 修改追溯 | 审计日志兜底 |
| 选股域 BHAR | 二期 | 少一个域 | 只做绩效/择时/行为三域 |
| 回撤族/IR/Sortino | 二期 | 风险刻画变薄 | 只留 MDD + 夏普 |
| Brinson 多期链接 | 三期 | 归因只有单期 | 单期 Brinson 或只留五因子 |
| 分享链接 | 三期 | 无法分享 | 导出 PDF 手动发 |
| 对话自由输入 | 三期 | 只有固定问题 | 固定问题按钮 + 模板 |
| 通知通道（邮件/Webhook） | 三期 | 只有站内 | 站内提醒 |

**不可砍**：多租户隔离（含 RLS）、导入去重与撤销、FIFO/净值口径、指标门禁（`blocked` 不出数）、契约门禁、越权测试、导出（数据主权）。

---

## 7. 质量门禁的落地顺序（跟着期次长出来）

| 期次 | 新增门禁 |
|---|---|
| 一期 | lint+typecheck、import-linter 分层、core 100% 覆盖、契约完整性、越权负向（一期端点）、api 验收（一期端点） |
| 二期 | golden/oracle 对拍、变异测试（core）、受保护端点越权 100%、前端零口径 ESLint、`blocked` 态快照 |
| 三期 | 契约快照 diff（breaking 阻断）、Schemathesis、Playwright e2e、性能门禁（nightly）、依赖漏洞扫描 |
| 交付 | 发布门禁 G-R1~R5、恢复演练通过率、镜像扫描 |

---

## 8. 风险登记

| 风险 | 触发 | 影响 | 应对 |
|---|---|---|---|
| 一期地基拖长 | RLS/连接池、契约链路两处最耗时 | 后续全部顺延 | 这两项排在最先；若超时先砍 `history`/SSE |
| 口径返工 | 期望值来源不独立 | 二期重做 | 先写 oracle 与手算表，再写实现 |
| 前端与后端节奏错位 | 契约未定就开工前端 | 返工 | Period1-06 完成即冻结一期契约 |
| 真实账单兼容 | 只有样例数据 | 上线即崩 | 一期就要 ≥3 份真实脱敏账单做 fixture |
| 估时乐观 | 单人 + 双栈 | 交付延期 | 按 §6 顺序裁剪，不动不可砍项 |
| 上线事故 | 只在本地验过 | 生产故障 | 交付阶段"上云必验清单"逐项签字 |

---

## 附录 A：端点与需求覆盖

### A.1 端点 → 期次/任务（84/84）

| 端点 | 期次 | 任务 | 端点 | 期次 | 任务 |
|---|---|---|---|---|---|
| API-001 | 一 | Period1-07 | API-002 | 一 | Period1-07 |
| API-003 | 一 | Period1-07 | API-004 | 一 | Period1-07 |
| API-005 | 一 | Period1-07 | API-006 | 三 | Period3-05 |
| API-007 | 一 | Period1-07 | API-008 | 一 | Period1-07 |
| API-009 | 一 | Period1-07 | API-010 | 一 | Period1-09 |
| API-011 | 一 | Period1-10 | API-012 | 一 | Period1-10 |
| API-013 | 一 | Period1-10 | API-014 | 一 | Period1-13 |
| API-015 | 一 | Period1-10 | API-016 | 二 | Period2-02 |
| API-017 | 一 | Period1-12 | API-018 | 一 | Period1-12 |
| API-019 | 一 | Period1-12 | API-020 | 一 | Period1-12 |
| API-021 | 一 | Period1-12 | API-022 | 二 | Period2-02 |
| API-023 | 二 | Period2-02 | API-024 | 二 | Period2-02 |
| API-025 | 二 | Period2-02 | API-026 | 一 | Period1-13 |
| API-027 | 二 | Period2-02 | API-028 | 二 | Period2-02 |
| API-029 | 二 | Period2-02 | API-030 | 二 | Period2-02 |
| API-031 | 二 | Period2-02 | API-032 | 二 | Period2-03 |
| API-033 | 二 | Period2-06 | API-034 | 二 | Period2-06 |
| API-035 | 二 | Period2-06 | API-036 | 二 | Period2-08 |
| API-037 | 三 | Period3-02 | API-038 | 二 | Period2-07 |
| API-039 | 二 | Period2-09 | API-040 | 二 | Period2-09 |
| API-041 | 二 | Period2-09 | API-042 | 二 | Period2-09 |
| API-043 | 三 | Period3-04 | API-044 | 三 | Period3-04 |
| API-045 | 三 | Period3-04 | API-046 | 三 | Period3-01 |
| API-047 | 三 | Period3-01 | API-048 | 三 | Period3-01 |
| API-049 | 三 | Period3-01 | API-050 | 三 | Period3-01 |
| API-051 | 三 | Period3-01 | API-052 | 三 | Period3-03 |
| API-053 | 三 | Period3-03 | API-054 | 三 | Period3-03 |
| API-055 | 二 | Period2-11 | API-056 | 二 | Period2-11 |
| API-057 | 二 | Period2-11 | API-058 | 三 | Period3-03 |
| API-059 | 二 | Period2-10 | API-060 | 二 | Period2-10 |
| API-061 | 一 | Period1-11 | API-062 | 一 | Period1-11 |
| API-063 | 一 | Period1-11 | API-064 | 一 | Period1-11 |
| API-065 | 三 | Period3-05 | API-066 | 三 | Period3-05 |
| API-067 | 三 | Period3-06 | API-068 | 三 | Period3-06 |
| API-069 | 三 | Period3-06 | API-070 | 三 | Period3-06 |
| API-071 | 一 | Period1-05 | API-072 | 一 | Period1-05 |
| API-073 | 一 | Period1-05 | API-074 | 三 | Period3-11 |
| API-075 | 一 | Period1-06 | API-076 | 一 | Period1-05 |
| API-077 | 三 | Period3-07 | API-078 | 三 | Period3-07 |
| API-079 | 二 | Period2-06 | API-080 | 一 | Period1-12 |
| API-081 | 二 | Period2-16 | API-082 | 二 | Period2-16 |
| API-083 | 二 | Period2-07 | API-084 | 二 | Period2-12 |
| API-086 | 二 | Period2-06 | | | |

> 校验：`make api-coverage` 读取本表与 [API-Catalog §1](architecture/v2/API-Catalog.md) 比对，断言 `已映射 / 总数 == 1.0`。

### A.2 FR → 期次/主任务（28/28）

| FR | 期次 | 主任务 | 覆盖端点 |
|---|---|---|---|
| FR-L1 | 一 | Period1-05/Period1-06 | API-071~074、API-067 |
| FR-L2 | 一 | Period1-07/Period1-08 | API-001~006、API-017、API-030、API-065、API-066 |
| FR-L3 | 一 | Period1-09/Period1-10 | API-010~016、API-061~063 |
| FR-L4 | 一 | Period1-12 | API-017~021、API-080 |
| FR-L5 | 一 | Period1-13/Period2-02 | API-014、API-016、API-022~025 |
| FR-L6 | 一 | Period1-13 | API-026、API-033 |
| FR-L7 | 一 | Period2-10/Period3-05 | API-006、API-059、API-060、API-068~070 |
| FR-L8 | 一 | Period1-12 | API-017、API-080 |
| FR-A1 | 一 | Period1-07 | API-007~009、API-033 |
| FR-A2 | 一 | Period1-06 | API-073、API-075、API-076 |
| FR-A3 | 一 | Period3-09 | API-028、API-038、API-039、API-059、API-061、API-067 |
| FR-A4 | 一 | Period3-05 | API-065、API-066 |
| FR-R1 | 一 | Period2-02 | API-029、API-030 |
| FR-R2 | 一 | Period2-01/Period2-02 | API-027~031、API-062~064 |
| FR-R3 | 一 | Period2-03 | API-032 |
| FR-R4 | 二 | Period2-07 | API-033、API-038 |
| FR-R5 | 二 | Period2-03/Period2-07 | API-033、API-056、API-079 |
| FR-R6 | 二 | Period2-07 | API-033 |
| FR-R7 | 二 | Period2-08 | API-036 |
| FR-R8 | 二 | Period2-05/Period2-06 | API-033、API-034、API-052、API-079 |
| FR-D1 | 二 | Period1-15/Period2-12/Period2-13 | 全部读端点（API-005、029、032、033、036、040、047、055 等） |
| FR-D2 | 二 | Period2-09 | API-039~042、API-062 |
| FR-D3 | 二 | Period2-06 | API-033~035 |
| FR-D4 | 三 | Period3-01 | API-046~051 |
| FR-D5 | 三 | Period3-02 | API-037 |
| FR-D6 | 三 | Period3-03 | API-052、API-053、API-057、API-058 |
| FR-D7 | 三 | Period3-07 | API-077、API-078 |
| FR-D8 | 三 | Period3-04 | API-043~045、API-054 |

> FR 与端点的权威对应见 [API-Catalog §8](architecture/v2/API-Catalog.md)，本表把其折算到开发期次。

## 附录 B：本地命令速查（随期次递增）

| 命令 | 期次 | 作用 |
|---|---|---|
| `make dev` / `make db-up` / `make migrate` | 一 | 起环境 |
| `make seed-tenants` / `make seed-demo` | 一/二 | 造多租户与演示数据 |
| `make verify-p1` / `p2` / `p3` | 各期 | 本期全部断言 |
| `make demo-p1` / `p2` / `p3` | 各期 | 5 分钟演示脚本（打印预期输出） |
| `make api-check` / `make sdk` | 一 | 契约检查 / 生成客户端 |
| `make test-core` / `test-engine` / `test-market` / `test-isolation` | 一~三 | 分层测试 |
| `make demo-broken` | 二 | 注入断链数据，验证 `blocked` 态 |
| `make e2e` | 三 | Playwright 关键路径 |
| `make load-test` | 三 | 容量压测 |
| `make restore-drill` | 交付 | PITR 恢复演练 |

## 附录 C：估时汇总

| 阶段 | 人日 | 主要交付 |
|---|---|---|
| 一期 | 29.0 | 多租户 + 导入 + 队列 + 契约 + 前端骨架 + 30 个端点 |
| 二期 | 33.5 | 口径全链 + 四问 + 归因 + 月报 + 导出 + ~35 个端点 |
| 三期 | 23.0 | 闭环 + 趋势 + 提醒 + 分享 + 对话 + 审计 + 限流 + 性能 + 审计 |
| 交付 | 8.0 | 镜像 + nginx + 迁移 + 备份恢复 + 安全 + CI/CD + 文档 |
| **合计** | **93.5** | 82/82 端点 + 28/28 FR + 本地可验证 → 上云可交付 |
