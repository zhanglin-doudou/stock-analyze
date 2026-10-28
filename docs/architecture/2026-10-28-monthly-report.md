# 月度复盘报告（Period2-09）开发设计与测试方案

> 状态：**v0.1 设计稿 — 待交叉评审 + 人工拍板；本阶段只出文档，不写生产代码、不改 API/契约/数据库**
> 日期：2026-10-28 ｜ 作者：AI（Team Lead）
> 需求来源：`docs/DEV-PLAN-v2.md` §3.2 **Period2-09「月度报告」**；`docs/prd/PRD-v2.md` **FR-D2**；`docs/architecture/v2/API-Catalog.md` **API-039~042、API-062**；`docs/Architecture-v2.md` §6/§9/§10/§14；`docs/architecture/v2/Extensibility.md` EP-7/§11.3；`docs/architecture/v2/Testing.md` §9/§12。
> 事实基线（2026-10-28）：
> - **Period2-07 已完成**：18 指标引擎、`/metrics` 端点、`metric_result`、`GET /nav`、`review.asset_daily`、**API-083 `POST /jobs/rebuild-period`（rounds→assets→nav→metrics）** 已落地并验收。
> - **Period2-08（五因子归因）暂不做**：仓库无 `attribution` 包/表/`GET /attribution`，API-036 未实现。
> - **Period2-09 尚未实现**：无 `review.report` 表/迁移、无 `/reports` 路由、`stock_delivery` 仅空包、无报告模板/区块/仓储；API-039~042 仅在 API-Catalog 登记，OpenAPI 中无操作。
> - 结论：本设计必须处理 **08 缺失**与 **API-083 已提供上游构建能力** 两个现实约束，不能假设归因已就绪。

---

## 0. 决策摘要（人工评审请重点拍板）

> **A 类 = 不拍板无法完整验收 FR-D2**；**B 类 = 影响实现方式/复用/运维，可按推荐先做**；**C 类 = 可委托 Lead 的工程细节**。
> 推荐方案 = 正文采用；备选保留取舍依据。明细见 §14。

### A. 阻塞/范围决策

| # | 决策点 | 推荐方案 | 备选方案 | 影响 |
|---|---|---|---|---|
| **A-01** | **Period2-08 未实现**，FR-D2 要求"归因"区块 | **09a/09b 两段交付**：09a 交付报告主体（概览/最赚最亏/行为/口径脚注/blocked 说明单/下载/异步/ETag），归因区块以 `status=unavailable` 的**说明区块**占位（不伪造五因子数值）；09b 在 08 完成后接入 `GET /attribution` 的只读结果 | ① 严格等 08 完成再启动 09（周期最长，但报告内容完整）；② 在 delivery 里自实现五因子（口径/分层双写，**不推荐**）；③ 删除归因区块（PRD 内容缺项） | 09a 可独立验收 API-039~042 的骨架/下载/幂等/blocked；09b 补 FR-D2 第 2 项。需人工确认"09a 是否可先验收" |
| **A-02** | API-039 允许 `format=pdf`，但 PRD OQ-3 已倾向"HTML + 浏览器打印"，仓库无 PDF 引擎 | **v1 仅产出 HTML（含打印 CSS）**；`format=["pdf"]` 请求 → 422 `VALIDATION_ERROR`，`what_to_do` 指向"用浏览器打印为 PDF"；`pdf_url=null`；同步修订 API-Catalog/PRD OQ-3 | ① 引入 WeasyPrint/Chromium（+1.5d、镜像体积/字体/离线渲染风险）；② 接受 `pdf` 但实际返回 HTML（**语义欺骗，拒绝**） | 不实现原生 PDF；若必须原生 PDF，估时另加 1.5d，且需离线字体测试 |
| **A-03** | "重复错误"和"下月只做一件事"在 PRD 中只有名词，没有判定阈值/行动模板；报告不能写死主观结论 | **规则配置化 + 人工批准规则表**：`config/review/report_rules.yaml` 定义 `mistake_code → metric/comparator/threshold/streak` 与 `action_code → 文案`；未批准前这两个区块返回 `unavailable(RULES_PENDING)`，**不生成结论**；批准后按规则渲染 | ① 本期不做这两区块（FR-D2 缺 2 项）；② 用 LLM 生成（违反"零口径/可复现/offline"，且需 LLM 依赖）；③ 硬编码经验阈值（不可审计，不推荐） | 09a 可先交付其余区块；09b 依赖人工给阈值与文案（预计 0.5d） |
| **A-04** | 报告生成时的数据新鲜度：只读当前派生数据 vs 先确保上游构建 | **报告 job 复用 API-083 的构建链**：把 `rebuild_period_job` 的阶段抽成可复用 `build_period_data(...)`，`report_generate` 在同一事务内先 rounds→assets→nav→metrics，再渲染；确保"任意自然月可生成"且数字与 `/metrics` 同源 | ① 要求用户先调 API-083，报告只做快照（实现轻 0.5d，但"报告可独立生成"验收弱、易出现 stale 数字）；② 报告只调 `batch_metrics`（资产/回合可能未更新，风险最高） | 推荐 +0.5d 重构，报告 job 时长预计 10~20s（Pf-6 ≤20s）；需确认可接受报告触发的重算副作用 |
| **A-05** | `blocked` 区间降级为"说明单"的判定阈值 | **硬阻断只看资产/净值门禁**：`nav.status=blocked` 或 `asset_build.state=blocked` → 报告 `blocked=true`，只输出数据质量+脚注；**部分指标 blocked/stale/unreliable** → 报告仍 `succeeded`，对应区块降级、`degraded=true`；不因单个指标失败拒绝整份报告 | ① 任一核心指标 blocked 就整份说明单（用户失去可读内容）；② 所有指标都 blocked 才降级（可能输出误导性收益结论） | 需人工确认"blocked 说明单"边界；影响 API-039 验收④与前端提示 |

### B. 实现/复用决策（推荐先按此）

| # | 决策点 | 推荐方案 | 备选 | 影响 |
|---|---|---|---|---|
| **B-01** | `spec_hash` 是否含 `definition_version` | **含当前注册表中参与区块的 definition_versions + report_schema_version**；版本 bump 后同请求生成新报告，不返回旧口径 | 只含 period/format/sections（同 spec 永远复用，口径升级后仍返回旧报告，**不推荐**） | 报告可追溯；旧报告保留，趋势/分享不受影响 |
| **B-02** | 报告并发生成 | **单飞到 `(user_id, period)`**：同 period 同 spec 复用 report/job，不同 spec 在运行中 → 409 `JOB_ALREADY_RUNNING`；完成后可生成不同 spec | 单飞 `(user,period,spec)`（不同 sections 可并发，但资源/配额复杂） | 与现有 JobQueue 单飞语义一致；避免同一月多份重算 |
| **B-03** | HTML 渲染技术 | **Jinja2 + 文件模板**（`delivery/templates/reports/`），autoescape，区块模板化；新增 `jinja2` 依赖 | 纯 Python 字符串拼接（少依赖，但模板难维护/难扩展） | 需在 delivery pyproject 加 `jinja2>=3.1`；无网络/无 JS |
| **B-04** | 报告文件存储 | **沿用 `LocalObjectStore`（`var/objects`）**；报告 key=`reports/{user_id}/{period}/{report_id}.html`；**原子写 tmp+rename + sha256**；DB 只存 key/checksum/size；不新增对象存储 | 直接存 DB BYTEA（简单，但备份/卷语义不一致）；外部 S3（OQ-2 未定） | 需给 `ObjectStore` 增加原子写/或报告存储封装；保留可迁移到 S3 |
| **B-05** | `report` 表归属 | **`review.report`**（Architecture §9.2 已规划），模型可放 `stock_delivery/reports/models.py`，迁移 `0008` 统一放 `backend/migrations`；RLS/授权与 review 表一致 | 新建 `delivery` schema（与架构表清单冲突） | 需更新 RLS gate/数据库文档/schema_version |
| **B-06** | 取消/失败的报告状态 | `status ∈ {pending,running,succeeded,failed}`；`blocked` 是 succeeded 上的布尔；取消 → `failed` + `error.code=JOB_CANCELLED`；重试复用同一 report_id | 新增 `cancelled` 状态（需改 API 枚举/前端） | 与 JobStatus 解耦；API-041 可返回 failed 详情 |

### C. 工程细节（可委托 Lead）

| # | 决策点 | 推荐 |
|---|---|---|
| C-01 | 报告中图表 | v1 不嵌 JS；可选**内联 SVG**（纯函数生成，无外链）；ECharts 不引入（离线/自包含要求） |
| C-02 | 列表分页 | `limit∈[1,200]`，cursor 绑定 user+period+id，非法 cursor → `INVALID_CURSOR` |
| C-03 | HTML 数字属性 | 每个指标元素带 `data-metric-id`/`data-value`/`data-status`，供 parity 脚本与前端测试读取 |
| C-04 | 报告保留/清理 | 不主动删除；报告可再生；新增孤儿文件清理（job 失败/DB 回滚时按 key 覆盖/清扫），Retention 另立任务 |
| C-05 | 审计 | 不新增 audit 事件（与 rebuild 一致），job 行 + report 行即留痕；分享/删除在 P3 另设计 |

---

## 1. 背景与现状

### 1.1 上游已就绪

| 能力 | 现状（可依赖） |
|---|---|
| 回合/净值/资产 | `review.round/round_fill`、`review.nav_daily`、`review.asset_daily`；`GET /nav`、`/rounds` |
| 指标引擎 | `GET /metrics`（API-033）、`/metrics/{id}`、explain/drilldown；`metric_result` 物化 + 状态矩阵 |
| 月度编排 | **API-083 `POST /jobs/rebuild-period`**：单 job 顺序 rounds→assets→nav→metrics；`rebuild_period_job` 已实现 |
| 任务系统 | `JobQueue`（SKIP LOCKED、单飞、heartbeat、cancel、progress、result_ref）、`GET /jobs/{id}`（API-062） |
| 存储 | `LocalObjectStore`（`var/objects`）；API state 已有 `object_store` |
| 多租户 | `review` RLS、`gate_rls_coverage.py`、隔离测试框架 |
| 契约 | API-Catalog 已登记 API-039~042/062；OpenAPI 快照/元数据/门禁链路可复用 |

### 1.2 缺口（本设计补齐）

1. `review.report` 表、`Report` 模型/仓储、迁移 `0008`、RLS/授权/数据库文档。
2. `ReportSpec`、`spec_hash`、确定性 `report_id`、报告状态机。
3. `stock_delivery.reports`：区块协议/注册、ViewModel、Jinja2 渲染器、自包含 HTML、文件存储。
4. `POST /reports` 异步入口（202 + job + report_id）、`GET /reports`/`{id}`/`download`、ETag/304、`Content-Disposition`。
5. `report_generate` worker handler：构建/快照/渲染/落库/进度/重试/取消。
6. 与 `/metrics` 的数字一致性校验、blocked 说明单、离线 HTML 测试、越权测试。
7. **不补** Period2-08 归因实现；只留可插拔的 `attribution` 区块与 `unavailable` 降级。

### 1.3 目标（可验收）

1. API-039：异步生成，202 + `report_id` + `job_id`；同 `(period,spec_hash)` 复用同一 `report_id`。
2. API-040：本人报告列表，按 period 倒序，游标分页。
3. API-041：详情含 `status/blocked/checksum/data_as_of/html_url` 等；跨租户 404。
4. API-042：HTML 下载 200、`Content-Disposition` 正确、未完成 409、ETag/304。
5. 报告内每个指标数字与 `GET /metrics` 同周期/同 benchmark 一致（脚本比对，按 unit 容差）。
6. HTML 自包含：无外部 CSS/JS/字体/图片链接，断网可打开；可打印。
7. blocked 区间生成说明单，`blocked=true`，不渲染收益结论/数值。
8. 区块可扩展：新增区块不改渲染框架/前端；每区块有 `ok/unavailable/blocked/insufficient` 降级。
9. 安全：跨租户 404、admin 403、HTML 转义/无注入、只读 PAT 不可写。

### 1.4 非目标（本期不做）

- Period2-08 五因子归因的算法与 `/attribution` 端点。
- 原生 PDF（见 A-02）、长图导出、报告分享/匿名访问（Period3-04）、报告 vs 上月并列（P1）。
- 前端报告页（Period2-13）、数据导出（Period2-10）、日志/趋势/提醒。
- 报告删除/保留策略、对象存储迁移（OQ-2）、跨设备同步。
- LLM 生成结论（报告全部模板化/规则化，离线可复现）。

---

## 2. 需求与验收追踪

| 编号 | 需求 | 设计章节 | 验收/测试 |
|---|---|---|---|
| API-039 | `POST /reports` 202 + report_id/job；同 spec 复用；blocked 说明单 | §6.1、§9、§11 | T-039-* |
| API-040 | `GET /reports` 列表 | §6.2 | T-040-* |
| API-041 | `GET /reports/{id}` 详情/状态 | §6.3 | T-041-* |
| API-042 | `GET /reports/{id}/download` HTML | §6.4 | T-042-* |
| API-062 | job 详情/进度/result_ref=report_id | §6.5、§9 | T-062-* |
| FR-D2 | 概览/归因/最赚最亏/行为/重复错误/一条行动/脚注 | §8 | T-BLOCK-*、T-PARITY-* |
| NFR-4/5 | 数字可追溯、幂等可重放 | §7.4、§10 | T-PARITY、T-039-04 |
| Testing §4.2 | 跨租户 404 | §6/§12 | T-ISO-* |
| Reliability | 报告 ≤20s、原子写、可重生成 | §9.6、§13 | T-PERF-*、T-STORAGE-* |

---

## 3. 关键约束

| 约束 | 要求 |
|---|---|
| 契约先行 | 先改 API-Catalog §1/§2/§6、OpenAPI 快照、错误码表、SDK，再写 router/测试 |
| 禁止 float | 报告中的金额/比率/收益一律使用后端已格式化的**定点字符串**；区块层不做算术 |
| 时间注入 | 报告生成时间、`data_as_of` 来自 `Clock`/派生数据；禁止 wall-clock |
| 显式 user_id | `ReportRepository` 所有方法显式 `user_id`；RLS 兜底 |
| 新表必须 RLS | `review.report` 启用 `tenant_isolation`，过 `gate_rls_coverage.py` |
| 长任务 | 202 + `Location: /api/v1/jobs/{id}` + `Retry-After: 1`；job 单飞/重试/取消/heartbeat |
| 错误 | 只用 `ApiError(ErrorCode.X)`；跨租户 404；admin 403 `ADMIN_CANNOT_READ_TRADES` |
| 迁移 | bump `platform.schema_version` + `stock_api/version.py::SCHEMA_VERSION`；迁移可完整 downgrade |
| 前端零口径 | 本任务只出 HTML/JSON，前端（P2-13）只加载/下载，不计算 |
| 离线自包含 | HTML 不含任何 `http(s)://`、外链 CSS/JS/字体/图片、`<script>`；断网可打开 |
| 报告不可变 | 同一 report_id 的文件/快照生成后不覆盖（重试覆盖同 key 也必须内容确定）；改数据重生成新 report_id/spec_hash |

---

## 4. 数据模型与迁移

### 4.1 `review.report`

```sql
CREATE TABLE review.report (
    id                    varchar(40) PRIMARY KEY,          -- rpt_ + sha256(user|period|spec_hash)[:32]
    user_id               uuid NOT NULL REFERENCES platform.user(id) ON DELETE CASCADE,
    period                varchar(7) NOT NULL,              -- YYYY-MM
    spec_hash             varchar(64) NOT NULL,             -- sha256(canonical spec + versions + schema)
    spec                  jsonb NOT NULL,                   -- ReportSpec 原样（无敏感字段）
    report_schema_version varchar(16) NOT NULL,             -- 如 "1.0"
    status                varchar(16) NOT NULL,             -- pending|running|succeeded|failed
    blocked               boolean NOT NULL DEFAULT false,
    degraded              boolean NOT NULL DEFAULT false,
    blocked_reasons       jsonb NOT NULL DEFAULT '[]'::jsonb,
    data_as_of            date,
    checksum              varchar(64),                      -- sha256(html bytes)
    storage_key           text,                             -- reports/{user}/{period}/{report_id}.html
    size_bytes            bigint,
    snapshot              jsonb,                            -- 报告快照：指标+NAV+回合+归因（见 §4.3）
    definition_versions   jsonb NOT NULL DEFAULT '{}'::jsonb,
    assumptions           jsonb NOT NULL DEFAULT '{}'::jsonb,
    job_id                varchar(40),
    error                 jsonb,
    created_at            timestamptz NOT NULL,
    started_at            timestamptz,
    finished_at           timestamptz,
    CONSTRAINT uq_report_user_period_spec UNIQUE (user_id, period, spec_hash),
    CONSTRAINT ck_report_status CHECK (status IN ('pending','running','succeeded','failed')),
    CONSTRAINT ck_report_period CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT ck_report_artifact CHECK (
        status <> 'succeeded'
        OR (checksum IS NOT NULL AND storage_key IS NOT NULL AND size_bytes IS NOT NULL)
    ),
    CONSTRAINT ck_report_blocked CHECK (blocked = false OR status = 'succeeded')
);

CREATE INDEX ix_report_user_period_created
    ON review.report (user_id, period DESC, created_at DESC);
CREATE INDEX ix_report_user_status ON review.report (user_id, status);
```

设计说明：
- `blocked` 是 succeeded 上的**内容属性**，不是独立状态；`failed` 不写 checksum/key。
- `degraded` 用于"部分指标 stale/unreliable/blocked"但报告仍可读。
- `snapshot` 是生成时的**不可变证据**（指标 value/status/n/ci/inputs_hash + NAV/回合/归因），用于：
  - 报告 HTML 与 `/metrics` 的 parity 校验；
  - 后续复盘/分享/导出不重新计算；
  - 审计与失败重试幂等。
- `spec` 不含用户隐私/账号；`definition_versions` 单独列便于趋势/重生成判断。
- `report_id` 确定性：同 user/period/spec_hash 永远同 ID；唯一约束兜底。
- `job_id` 仅记录最近一次生成任务；重试复用同 report_id。

### 4.2 `ReportSpec`（canonical JSON）

```json
{
  "period": "2026-09",
  "format": ["html"],
  "sections": ["overview", "attribution", "top_rounds", "behavior", "repeated_mistakes", "one_action", "footnotes"],
  "benchmark": "hs300_tr",
  "report_schema_version": "1.0"
}
```

- `sections` 按白名单校验，顺序按配置（请求只决定集合，不决定顺序）；默认全部区块；报告只消费核心指标（`mvp=P0`），不含 exploratory。
- `format` 只接受 `html`（v1）；`pdf` 的请求行为见 A-02，需人工确认后定 422 或 400。
- `spec_hash = sha256(canonical_json({spec, definition_versions, engine_version}))`；`definition_versions` 取请求时活跃注册表（含依赖闭包）。
- `benchmark` 本期来自服务默认（`hs300_tr`，与 `/metrics` 默认一致）；Period2-11 设置接入后从用户设置解析；变更 benchmark 即新 spec。

### 4.3 `snapshot` 结构（报告全部数字的唯一来源）

```json
{
  "period": "2026-09",
  "data_as_of": "2026-09-30",
  "metrics": [
    {
      "metric_id": "perf.win_rate",
      "definition_version": "1.0",
      "domain": "perf",
      "status": "ok",
      "sample_status": "ok",
      "value": "0.66666667",
      "unit": "ratio",
      "precision": 8,
      "n": 12,
      "n_pending": 0,
      "ci_low": "0.39062209",
      "ci_high": "0.86187991",
      "reason": null,
      "assumptions": {"rf_rate": "0.015", "calendar": "weekday_proxy"},
      "data_as_of": "2026-09-30",
      "inputs_hash": "…"
    }
  ],
  "nav": {"status": "ok", "total_pnl": "…", "return": "…", "benchmark_return": "…", "twr": "…", "mwr": "…"},
  "rounds": {"closed": 30, "open": 3, "data_error": 0},
  "top_rounds": [
    {"round_id": "rnd_...", "security_name": "…", "pnl": "…", "return_rate": "…", "holding_days": 42, "fee": "…"}
  ],
  "bottom_rounds": [],
  "attribution": {"status": "unavailable", "reason": "PERIOD2_08_NOT_IMPLEMENTED"}
}
```

- 所有数值为**展示定点字符串**（由 review 层按 `precision` 量化），delivery 不再格式化/计算。
- 不含个股价格、账号、股东号等敏感字段；`security_name` 仅用于报告正文的最赚最亏；回合数组仅含报告展示所需字段。
- `attribution` 在 08 未实现时固定 `unavailable`；09b 接入后为结构化因子数组 + residual。
- `top_rounds/bottom_rounds` 只含 closed 回合，最多各 3 条；`data_error` 不进入榜单。

### 4.4 文件存储

| 项 | 方案 |
|---|---|
| 端口 | 复用 `LocalObjectStore`；新增 `put_bytes_atomic(key, data)`（tmp 写 + `os.replace`），或报告存储封装保证原子 |
| key | `reports/{user_id}/{period}/{report_id}.html` |
| checksum | sha256(html bytes)，同时作为 ETag |
| 大小 | `size_bytes` 落库；预期 30~200KB（内联 CSS/少量 SVG） |
| 一致性 | 先写 tmp → 原子 rename → DB 更新 `succeeded`；DB 回滚时孤儿文件由重试覆盖/清扫脚本处理 |
| 清理 | 本期不删；用户注销级联 DB，文件由后续 retention 任务清理（P3） |
| 部署 | API 与 worker 必须挂载**同一个 `data` 卷**（单机 Compose）；多机/换 S3 前不可把 worker 与 API 分到无共享存储的节点，否则下载读不到文件（OQ-2 预留给对象存储） |

### 4.5 迁移与 RLS

- `backend/migrations/versions/0008_review_reports.py`：create table/index/check → RLS → grants → stamp `0008_review_reports`。
- `stock_api/version.py::SCHEMA_VERSION = "0008_review_reports"`；`docs/DATABASE.md`、`docs/architecture/v2/Testing.md` 表清单同步。
- `report` 加入 RLS gate 覆盖清单（`gate_rls_coverage.py` 期望表数 +1），授权沿用 `stock_app` DML / `stock_backup` SELECT。
- downgrade 完整回滚：drop index/table，恢复版本、RLS/授权。

---

## 5. 架构与包结构

### 5.1 分层

```
stock_api (router) ──► stock_delivery.reports (application/domain/render) ──► ports
       │                        │
       │                        ├─ ReportRepository (SQLAlchemy adapter)
       │                        ├─ ReportDataPort  (adapter: stock_worker/report_data.py)
       │                        └─ ObjectStore
       └─ job enqueue ──► stock_worker handler ──► stock_delivery.reports.service
```

- **delivery 只消费结构化产物，零口径**：不实现指标/归因算法；`ReportDataPort` 返回快照 DTO。
- **不跨上下文直接 import review**：`ReportDataPort` 的实现（`build_period_data`/`batch_metrics`/回合榜适配）放在**顶层 worker** 的 `stock_worker/report_data.py`；`stock_delivery` 只定义 port 并用 Fake 做单元测试，不新增 `stock-review` 依赖（保持 Architecture §6.1 L5）。
- `stock_api` 只负责创建/查询报告与任务，不装配数据端口；`stock_worker` 为顶层，负责任务编排与数据适配，不写模板。
- 新表模型可放 `stock_delivery/reports/models.py`（`schema="review"`），仓储实现放 `stock_delivery/reports/repository.py`；模型不 import review。

### 5.2 包结构

```
backend/packages/delivery/src/stock_delivery/
├── reports/
│   ├── spec.py              # ReportSpec、DEFAULT_SECTIONS、spec_hash、report_id
│   ├── snapshot.py          # ReportSnapshot/ReportMetric/ReportRound 等纯 DTO
│   ├── view_model.py        # ReportViewModel（区块唯一输入）
│   ├── blocks/
│   │   ├── base.py          # ReportBlock Protocol + BlockResult
│   │   ├── overview.py
│   │   ├── attribution.py   # 08 未就绪：unavailable
│   │   ├── top_rounds.py
│   │   ├── behavior.py
│   │   ├── repeated_mistakes.py
│   │   ├── one_action.py
│   │   └── footnotes.py
│   ├── renderer.py          # Jinja2 环境、模板装配、自包含校验
│   ├── service.py           # generate_report（构建/快照/渲染/落库编排）
│   ├── repository.py        # ReportRepository
│   ├── models.py            # review.report ORM
│   ├── storage.py           # ReportStorage（ObjectStore 原子写）
│   └── ports.py             # ReportDataPort/ReportRepositoryPort
├── templates/reports/
│   ├── report.html.j2
│   ├── _style.css
│   └── blocks/*.html.j2
└── tests? （测试统一放 backend/tests）
```

`backend/services/api/src/stock_api/routers/reports.py`、`backend/services/worker/src/stock_worker/handlers.py::_report_generate`、`backend/services/worker/src/stock_worker/report_data.py`（`ReportDataPort` 的 review 适配器，仅顶层可 import review）。

### 5.3 区块协议

```python
class ReportBlock(Protocol):
    block_id: str
    order: int
    requires: tuple[str, ...]          # metric_id / "nav" / "rounds" / "attribution"
    def render(self, vm: ReportViewModel, *, mode: Literal["html", "text"]) -> BlockResult: ...
```

- `BlockResult`：`html`/`text`/`status`（`ok|unavailable|blocked|insufficient`）/`reason`。
- 区块**纯函数**：禁 Repository/网络/Clock；只读 ViewModel；不得算术（数值已格式化）。
- 单区块异常不拖垮整份报告：渲染器捕获 → 该区块 `unavailable` + 日志，报告仍可 succeeded（除非构建/IO 失败）。
- 注册：`delivery/reports/blocks/` 自动发现；顺序/开关 `config/review/report_blocks.yaml`（默认内嵌常量，配置缺失时用默认）；`report_schema_version` MINOR++。
- 区块新增步骤按 `Extensibility §11.3`：合约 + 四态快照 + 模板 + 配置 + parity/术语断言。

### 5.4 报告快照采集（ReportDataPort）

```python
class ReportDataPort(Protocol):
    async def build_and_snapshot(
        self, session: AsyncSession, *, user_id, period: str, spec: ReportSpec,
        clock, market_data, rf_annual: Decimal,
        progress: Callable[[int, str], Awaitable[None]] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> ReportSnapshot: ...
```

- `build_and_snapshot` 负责调用 `build_period_data` + 采集 `ReportSnapshot`；只返回纯 DTO，不把 review 对象泄漏给 delivery。
- `ReportRepositoryPort` 至少：`create_or_get(user, period, spec_hash, spec, now)`、`get_for_user(user, report_id)`、`list_for_user(user, filters, cursor, limit)`、`mark_running/succeeded/failed(...)`；全部显式 `user_id`。
- `ReportStorage`：`write_atomic(key, data) -> checksum,size`、`read(key)`、`delete(key)`；失败清理。

`stock_review` 增加只读应用函数（或 delivery adapter 直接调用既有应用函数）：

| 数据 | 来源 | 说明 |
|---|---|---|
| 指标 | `stock_review.application.metrics.batch_metrics(ids=core)` | 与 `/metrics` 同一应用函数；`persist_safely` 落库（可选） |
| 说明 | 以下数据由 `stock_worker/report_data.py` 适配器采集，转换成 delivery 的纯 DTO；delivery 不 import review | — |
| NAV 汇总 | `fetch_nav_for_period` / 构建结果 | TWR/MWR/期末资产/benchmark 对比；blocked 判定 |
| 回合榜 | `list_rounds`/RoundRepository | 按 pnl 取前 3/后 3；只取 closed/data_error 可解释字段 |
| 历史信号 | `metric_result` 最近 12 个月 | 重复错误连续月判定（A-03 规则批准后） |
| 归因 | `attribution` 应用（08 后） | 09a 固定 unavailable |

> **构建前置（A-04）**：`report_generate` 先调用抽取后的 `build_period_data`（API-083 同源），确保 rounds/assets/nav/metrics 同事务更新；然后 `batch_metrics` 再取一次快照（同一事务内已算，可直接复用 `build_period_data` 返回的 `BatchResult`，避免二次计算）。

---

## 6. API 设计

### 6.1 API-039 `POST /api/v1/reports`

请求体：

```json
{
  "period": "2026-09",
  "format": ["html"],
  "sections": ["overview", "attribution", "top_rounds", "behavior", "repeated_mistakes", "one_action", "footnotes"]
}
```

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `period` | string | 是 | `YYYY-MM`，不晚于当前月；未来 → 422 `INVALID_PERIOD` |
| `format` | string[] | 否 | 默认 `["html"]`；v1 仅 `html`；`pdf` 按 A-02 处理 |
| `sections` | string[] | 否 | 默认全部；重复/未知 → 422 `VALIDATION_ERROR`；只影响渲染集合，不改变固定顺序 |

请求头：`Idempotency-Key` **必需**（16~128）。

响应 `202`：

```json
{
  "report_id": "rpt_0123456789abcdef0123456789abcdef",
  "job_id": "job_01J9Z6...",
  "type": "report_generate",
  "status": "pending",
  "links": {
    "self": "/api/v1/jobs/job_01J9Z6...",
    "events": "/api/v1/jobs/job_01J9Z6.../events",
    "report": "/api/v1/reports/rpt_0123456789abcdef0123456789abcdef"
  }
}
```

- 头：`Location: /api/v1/jobs/{job_id}`、`Retry-After: 1`；复用已有报告时 `X-Report-Reused: true`；幂等重放 `Idempotency-Replayed: true`。
- 同 `(user, period, spec_hash)` 已存在：
  - `pending/running` → 202 + 同 report_id/job_id；
  - `succeeded` → 202 + 同 report_id + `status="succeeded"` + 最近 job_id；
  - `failed` → 复用 report_id 并重置 pending/新 job（同幂等键的**重放**仍返回原 202，不重新生成）。
- 同 period 不同 spec 且已有 active report job → 409 `JOB_ALREADY_RUNNING`（B-02）。
- 错误：`INVALID_PERIOD(422)`、`VALIDATION_ERROR(422)`、`IDEMPOTENCY_KEY_REUSED(422)`、`JOB_ALREADY_RUNNING(409)`、`QUOTA_EXCEEDED(429)`、`ADMIN_CANNOT_READ_TRADES(403)`。

### 6.2 API-040 `GET /api/v1/reports`

Query：`period?`、`status?`、`limit`（1~200，默认 20）、`cursor?`。

```json
{
  "items": [
    {
      "report_id": "rpt_...",
      "period": "2026-09",
      "status": "succeeded",
      "blocked": false,
      "degraded": false,
      "data_as_of": "2026-09-30",
      "checksum": "…",
      "created_at": "2026-10-01T00:00:00Z",
      "finished_at": "2026-10-01T00:00:12Z"
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

- 按 `period DESC, created_at DESC, id DESC`；只返回本人；admin 403；越权不可见。
- 非法 cursor → `INVALID_CURSOR(400)`；非法 status → `VALIDATION_ERROR(422)`。

### 6.3 API-041 `GET /api/v1/reports/{report_id}`

```json
{
  "report_id": "rpt_...",
  "period": "2026-09",
  "status": "succeeded",
  "blocked": false,
  "degraded": false,
  "blocked_reasons": [],
  "report_schema_version": "1.0",
  "spec_hash": "…",
  "format": ["html"],
  "sections": [
    {"id": "overview", "status": "ok", "reason": null},
    {"id": "attribution", "status": "unavailable", "reason": "PERIOD2_08_NOT_IMPLEMENTED"}
  ],
  "checksum": "…",
  "size_bytes": 123456,
  "data_as_of": "2026-09-30",
  "definition_versions": {"perf.win_rate": "1.0", "...": "1.0"},
  "assumptions": {"benchmark_id": "hs300_tr", "rf_rate": "0.015", "calendar": "weekday_proxy"},
  "html_url": "/api/v1/reports/rpt_.../download?format=html",
  "pdf_url": null,
  "error": null,
  "created_at": "…",
  "started_at": "…",
  "finished_at": "…"
}
```

- 成功响应带 `ETag: "<checksum>"`；`If-None-Match` 命中 → 304（无副作用）。
- pending/running 返回 200（无 checksum/html_url），供前端轮询；failed 返回 200 + error（下载 409）。
- 跨租户 → 404 `NOT_FOUND`；admin → 403 `ADMIN_CANNOT_READ_TRADES`。

### 6.4 API-042 `GET /api/v1/reports/{report_id}/download`

Query：`format=html`（默认）；`pdf` 按 A-02。

- 成功：`200`，`Content-Type: text/html; charset=utf-8`，`Content-Disposition: attachment; filename="review-2026-09.html"`，`Content-Length`，`ETag`，`Cache-Control: private, max-age=31536000, immutable`。
- `If-None-Match` 命中 → 304。
- 未完成（pending/running/failed）→ 409 `REPORT_NOT_READY`；跨租户 → 404；admin → 403；非法 format → 422。
- 流式返回文件；不重新渲染；校验 `checksum`/`size_bytes` 与 DB 一致，不一致 → 500 `INTERNAL_ERROR` + 告警（文件损坏）。

### 6.5 API-062 `GET /api/v1/jobs/{job_id}`

- `type="report_generate"`；`result_ref=report_id`；`result` 含 `{report_id, period, status, blocked, checksum, stages}`；进度 0~100。
- 报告 job 与报告状态双通道：客户端优先轮询 `/jobs/{id}`，完成后 `GET /reports/{id}` 取元数据。

### 6.6 契约同步清单

- API-Catalog §1 保持 4 行；§2 补 `report_id` 响应、`format/sections` 精确 schema、`X-Report-Reused`；§5/§6 补验收矩阵；§3 错误码表确认复用。
- OpenAPI：新增 `ReportCreateIn/ReportAcceptedOut/ReportListItem/ReportPage/ReportOut/ReportSectionOut`；`operation_id` 固定 `reports_create/reports_list/reports_detail/reports_download`。
- `make api-snapshot`/`api-check`/`sdk`；前端 TS 类型自动生成（P2-13 使用）。
- `docs/architecture/v2/Testing.md` 表清单/验收矩阵；`docs/DATABASE.md` 表；`docs/architecture/v2/API-Catalog.md` 报告字段。

---

## 7. 生成流程与状态机

### 7.1 时序

```
Client                API                         Worker                     Store/DB
  │ POST /reports      │                             │                          │
  │───────────────────►│ validate spec/idempotency   │                          │
  │                    │ ensure report row (pending) │                          │
  │                    │ enqueue report_generate     │                          │
  │◄── 202 report/job ─│                             │                          │
  │                    │                             │ lease job; mark running  │
  │                    │                             │ build_period_data        │
  │                    │                             │  rounds→assets→nav→metrics
  │                    │                             │ batch_metrics snapshot   │
  │                    │                             │ render blocks/HTML       │
  │                    │                             │ put_bytes_atomic ───────►│
  │                    │                             │ update report succeeded  │
  │                    │                             │ job.finish(result_ref)   │
  │ GET /jobs/{id} ───►│                             │                          │
  │ GET /reports/{id} ►│ read report row             │                          │
  │ GET .../download ─►│ stream file/ETag            │                          │
```

### 7.2 报告状态

```
pending ──job lease──► running ──success──► succeeded(blocked=false|true)
   │                     │
   │                     └──error/retry exhausted──► failed
   └──enqueue fails/rollback──► (row rollback, no report)
```

- `pending/running`：详情可读、下载 409。
- `succeeded`：HTML 已原子落盘、checksum 已写；`blocked=true` 时仅说明单。
- `failed`：`error{code,message,retryable}`，可重试（同 report_id，重置 pending）。
- 取消：job cancel → 报告 `failed` + `error.code=JOB_CANCELLED`；已写文件按 key 覆盖/清理（C-04）。
- 重试：同 report_id、同 spec_hash、同 storage_key；内容确定性（FrozenClock/固定输入），覆盖后 checksum 不变；达到 max_attempts=3 → failed。

### 7.3 幂等与复用

1. `IdempotencyStore.begin`：同 key 同 request_hash → 返回存储响应（`Idempotency-Replayed: true`）；同 key 异 hash → 422。
2. `ReportRepository.find_by_spec(user, period, spec_hash)`：存在则复用；不存在则 `INSERT ... ON CONFLICT DO NOTHING`，冲突后重查（并发同 spec）。
3. Job `scope_key = f"{user_id}:report:{period}"`，params=`{spec_hash,...}`；同 spec active job 复用（`X-Job-Reused: true`）；异 spec active → 409。
4. `report_id` 确定性 + DB 唯一约束，保证幂等/重试/并发安全。

### 7.4 快照与 parity

- `report_generate` 只使用**同一事务/同一批次**的 `batch_metrics` 结果；`build_period_data` 返回的 `BatchResult` 直接作为快照来源，不二次计算。
- `snapshot` 存储所有报告用到的指标/NAV/回合（含 status/reason/n/ci/inputs_hash）；HTML 的每个数字来自 snapshot，不重新查询。
- 报告 detail `assumptions` 必须包含 `benchmark_id/rf_rate/calendar`；`definition_versions` 与 snapshot 一致。
- 生成后数据变化：报告保持不可变；detail `data_as_of` 告知截止日；重新生成需新 spec_hash（若 definition_versions 变了）或同 spec 重试（已 succeeded 则复用旧报告，**不自动刷新**；前端可“重新生成”新 spec 若允许，见 B-01）。

---

## 8. 区块设计（FR-D2 七区块）

| # | block_id | order | 输入 | 状态降级 |
|---|---|---|---|---|
| 1 | `overview` | 10 | nav 汇总 + `perf/timing` 指标 | blocked → 说明；缺 nav → `insufficient` |
| 2 | `attribution` | 20 | attribution 结果 | 08 未实现 → `unavailable(PERIOD2_08_NOT_IMPLEMENTED)` |
| 3 | `top_rounds` | 30 | 回合榜 | 无 closed 回合 → `insufficient` |
| 4 | `behavior` | 40 | behavior/perf 指标（de/turnover/averaging_down/hhi/fee） | 非 ok 区块内逐项降级 |
| 5 | `repeated_mistakes` | 50 | 历史 `metric_result` + 规则 | 规则未批准 → `unavailable(RULES_PENDING)`；历史不足 → `insufficient` |
| 6 | `one_action` | 60 | repeated mistake/优先级 | 无可用信号 → `unavailable`；措辞过合规词表 |
| 7 | `footnotes` | 90 | snapshot 元数据 | 始终可渲染 |

### 8.1 概览
- 内容：报告期、交易笔数/已平仓/未平仓、收益金额与比例、vs 基准、β、数据截止日。
- 数值来源：nav 汇总（TWR/MWR/总资产）+ `timing.beta`/`perf.*`；所有数字来自 snapshot，**区块不做算术**。
- 状态：nav blocked → 仅数据质量说明（不显示收益）；β `blocked/stale` → 单项占位；其余正常。
- 图表：v1 可选内联 SVG 净值曲线（纯函数生成）；没有则省略，不引 JS。

### 8.2 归因（09a 占位 / 09b 接入）
- 09a：区块渲染"本期归因暂不可用（五因子模块尚未上线）" + 说明；不显示任何因子金额/占比；`section.status=unavailable`。
- 09b（08 完成后）：调用 `GET /attribution` 同源应用函数；展示五因子金额/占比/排序 + residual；不重算。
- 若归因 blocked/stale → 区块降级，报告仍可读；blocked 报告不展示归因数值。

### 8.3 最赚/最亏回合
- 取 `closed` 回合按 `pnl_units` 排序；`data_error` 单独排除并在脚注说明；最多 3+3，不足则显示现有。
- 每项：证券名称（脱敏? 用户自己的报告可显示）、收益金额/比例、持有天数、费用；**共性描述只基于可用字段**（持有天数区间、是否亏损、费用占比、追涨标签），无行业数据不生成"板块共性"。
- 安全：证券名称 HTML 转义；不输出账号/股东号。

### 8.4 行为观察
- 指标：`behavior.de`、`behavior.turnover`、`behavior.averaging_down`、`behavior.hhi`、`perf.fee`。
- 每个指标展示 value/CI/n/status；非 ok 按 `insufficient/blocked/stale/unreliable` 显示原因 + `what_to_do`，不出结论。
- 不在报告层拼接新结论；可复用 engine 的 explain 文案（后端生成）。

### 8.5 重复错误（A-03；规则未批准前 `unavailable`）
- 规则配置示例（**阈值仅为结构示例，数值必须人工拍板**）：

```yaml
# config/review/report_rules.yaml（09b 使用）
mistakes:
  chase:
    metric_id: timing.chase
    comparator: gte
    threshold: "TBD"          # 人工拍板，禁止实现前写死
    lookback_months: 12
  averaging_down:
    metric_id: behavior.averaging_down
    comparator: gte
    threshold: "TBD"
  disposition:
    metric_id: behavior.de
    comparator: gt
    threshold: "0"
    require_ci_excludes_zero: true
  turnover:
    metric_id: behavior.turnover
    comparator: gte
    threshold: "TBD"
  concentration:
    metric_id: behavior.hhi
    comparator: gte
    threshold: "TBD"
actions:
  reduce_chasing: "下月只做一件事：买入前先记录理由，并复核当日涨幅是否处于近期高位。"
  reduce_averaging: "下月只做一件事：加仓前先写下原买入理由是否仍成立。"
  reduce_turnover: "下月只做一件事：非计划内交易先等 24 小时再执行。"
  reduce_fee: "下月只做一件事：优先选择计划内、少笔数的交易。"
```

- 判定：同一 `mistake_code` 在最近 N 个月连续触发 → `streak=N`；报告输出"第 N 次"；当前月未触发 → 不生成。
- 选择：streak 最大者优先；并列按配置 order；仅使用 `status=ok` 且 CI/样本满足的月份；缺失月份不连续。
- 历史不足 `< 2` 个月 → `insufficient`，不编造。
- 必须人工确认：**阈值来源/是否允许报告出现"错误"定性/文案是否构成投资建议**。

### 8.6 一条行动
- 从已确认的 `mistake_code` 映射到 `action_code`，固定模板文本；无信号 → "本期无可执行的单条行动（数据不足）"。
- 合规：不含"买入/卖出/加仓/减仓/推荐/目标价"等具体操作建议（用行为约束表述）；词表扫描 + 快照测试。
- 每份报告**最多一条**；不与指标结论冲突。

### 8.7 口径与可信度脚注
- `data_as_of`、`report_schema_version`、`definition_versions`、各指标 `n/n_pending/sample_status`、`blocked/degraded` 原因、行情/日历假设（`benchmark_id/rf_rate/calendar`）。
- 报告生成时间/版本；不显示内部 ID 外的敏感信息。

### 8.8 blocked 说明单
- 触发：`nav.status=="blocked"` 或 `asset_build.state=="blocked"`（A-05）。
- 内容：数据质量原因、受影响区间/日期、`what_to_do`（补现金锚点/行情/重建）、口径脚注；**不渲染概览收益、归因、回合盈亏、行为结论**。
- `report.blocked=true`；`sections` 中除 `overview`（说明版）/`footnotes` 外为 `blocked`；HTML 无 `data-metric-value` 性能数字。
- API-039 验收④：`detail.blocked==true` 且 sections 不含收益结论字段。

---

## 9. 异步任务与运维

### 9.1 抽取 `build_period_data`

将 `stock_review.application.rebuild_period.rebuild_period_job` 的阶段抽出为：

```python
async def build_period_data(
    session, *, user_id, clock, market_data, period: str,
    include_exploratory: bool = False, rf_annual: Decimal = Decimal("0.015"),
    progress: Callable[[int, str], Awaitable[None]] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> PeriodBuildResult:
    """rounds→assets→nav→metrics 的可复用编排。

    PeriodBuildResult 至少含：rounds 计数、assets 摘要、nav 摘要、BatchResult（metrics
    快照/状态/definition_versions）、data_as_of；不返回 HTML/文件。
    """
```

- `rebuild_period_job` 保留为 API-083 的 job 包装（进度 10/40/60/85/100 不变）。
- `report_generate` 调用 `build_period_data`（进度 5/20/40/60/80/90/100），再渲染；失败整体回滚，report 置 failed。
- 不改变 API-083 行为/验收；需回归 API-083 六条测试。

### 9.2 worker handler

```python
async def _report_generate(queue, session, job, clock, market_data):
    from stock_delivery.reports.service import generate_report
    return await generate_report(queue, session, job, clock, market_data)
```

- `report_generate` 在 `HANDLERS` 注册；`JobType.REPORT_GENERATE` 已存在。
- **worker 存储装配**：`WorkerRunner` 新增惰性 `object_store`（`LocalObjectStore(settings.data_dir / "objects")`）；handler 签名从 5 参扩展为 6 参（`queue, session, job, clock, market_data, object_store`，其余 handler 忽略最后一项），与 D11 的 `market_data` 装配方式一致；单元测试注入 FakeObjectStore。
- 心跳：`build_period_data` 每阶段 `queue.progress` + `queue.heartbeat`（若 runner 已有）；长渲染前再检查取消。
- 事务：沿用 runner `tenant_tx`；对象写用原子 rename；DB 提交前校验 checksum。

### 9.3 观测

- 日志字段：`report_id/user_id/period/spec_hash/job_id/status/blocked/degraded/size_bytes/duration_ms`；不记录 HTML 内容/敏感字段。
- 指标（Prometheus）：`report_generated_total{status}`、`report_generation_seconds`、`report_orphan_files_total`（可选）。
- SLO：Pf-6 报告 12 个月 ≤20s（单份 ≤5s 目标，见 Architecture §14）；失败可重试。

---

## 10. 数字一致性（关键验收）

### 10.1 同源

- 报告 job 与 `/metrics` 使用同一 `batch_metrics`、同一 `benchmark/rf/registry`、同一事务输入。
- 报告 `snapshot` 持久化后，HTML 只读 snapshot；不在渲染时重算/重查。
- `/metrics` 可能因用户后续导入而变化；报告是不可变快照，detail `data_as_of` 是解释边界。

### 10.2 校验脚本

1. 生成报告 `R`（period=P）→ 读 `GET /reports/{id}` 的 `snapshot`（API 可只暴露摘要；测试可直接读 DB/内部接口）。
2. 同一时刻 `GET /metrics?period=P&include_exploratory=false`（同 benchmark）。
3. 对每个 `metric_id` 比较 `status/sample_status/n/value/ci_low/ci_high`；
   - ratio 容差 1bp / CNY 0.01 / days 0.01 / count 0；
   - 报告 HTML 中 `data-metric-id` 与 snapshot 一致。
4. 断言报告 HTML 中的数字集合 ⊆ snapshot；不出现 snapshot 外数字（页眉页码除外）。
5. `data_as_of`、`definition_versions` 一致。

### 10.3 测试夹具

- 用现有 `fx_round30`/`fx_metrics_core` 生成 2026-09 数据；`FrozenClock=2026-10-01`；replay 行情。
- blocked 夹具：缺现金锚点/缺行情；断言说明单。
- 历史夹具：`metric_result` 插入最近 6 个月行为信号，验证 streak（规则批准后）。

---

## 11. 测试方案

### 11.1 测试分层

| 层 | 路径 | 内容 |
|---|---|---|
| 单元 | `backend/tests/unit/delivery/` | ReportSpec/spec_hash/report_id；区块四态/转义/顺序；renderer 自包含；存储原子/checksum |
| 合约 | `backend/tests/contract/test_report_block.py` | `ReportBlock` 协议、无 I/O/无算术、配置开关、schema version |
| API | `backend/tests/api/test_reports.py` | 039~042/062、幂等、复用、列表、详情、下载、ETag、错误码、越权 |
| 集成 | `backend/tests/integration/test_report_generation.py` | worker drain、文件落盘、失败重试、取消、blocked、孤儿清理 |
| 验收 | `backend/tests/api/test_reports_parity.py` | 报告 vs `/metrics` 脚本比对；离线 HTML；数字属性 |
| 隔离 | 既有 `backend/tests/isolation` + T-ALL | 报告列表/详情/下载/任务跨租户 404；admin 403 |
| 性能 | `backend/tests/perf/test_report_perf.py` | 12 个月报告 p95 ≤20s；文件大小上限 |
| 架构 | `backend/tests/architecture/test_report_layering.py` | delivery 不 import api/worker；区块不 import repository |

### 11.2 必测矩阵（摘）

| ID | 场景 | 断言 |
|---|---|---|
| T-039-01 | 新建 HTML 报告 | 202、report_id/job_id 非空、Location/Retry-After |
| T-039-02 | 同 spec 重复请求（不同幂等键） | 同 report_id；`X-Report-Reused: true`；只 1 个 job |
| T-039-03 | 同 key 重放 | 202 同响应；`Idempotency-Replayed: true` |
| T-039-04 | 同 key 异 body | 422 IDEMPOTENCY_KEY_REUSED |
| T-039-05 | 缺幂等键/坏 period/未知 section | 422；无 report 行 |
| T-039-06 | read PAT/admin | 403；无副作用 |
| T-039-07 | 配额/并发异 spec | 429/409 |
| T-039-08 | blocked 区间 | 202→succeeded；blocked=true；无收益数字 |
| T-040-01 | 列表排序/分页/过滤 | period 倒序、cursor、仅本人 |
| T-040-02 | 跨租户/非法 cursor | 跨租户列表 0 条（不泄露存在性）；非法 cursor 400 INVALID_CURSOR |
| T-041-01 | 成功详情 | status/checksum/data_as_of/html_url/sections/versions |
| T-041-02 | pending/running | 200 无 html_url；下载 409 |
| T-041-03 | 跨租户/admin | 404/403 |
| T-041-04 | ETag 304 | If-None-Match 命中 304，无文件重读副作用 |
| T-042-01 | HTML 下载 | 200 `text/html`、Content-Disposition、checksum |
| T-042-02 | 未完成/失败 | 409 REPORT_NOT_READY |
| T-042-03 | `format=pdf` | 422（按 A-02） |
| T-042-04 | 损坏文件/缺文件 | 500 + 告警（或 410，按契约） |
| T-062-01 | job 进度/result_ref | result_ref=report_id；终态 succeeded |
| T-BLOCK-01..07 | 七区块 ok | 快照 HTML、数字/文案 |
| T-BLOCK-08..14 | 四态降级 | unavailable/blocked/insufficient 不抛异常、不出结论 |
| T-PARITY-01 | 报告 vs metrics | 所有指标逐项一致 |
| T-OFFLINE-01 | HTML 断网打开 | 无外链/script；file:// 渲染 |
| T-SEC-01 | 证券名/原因含 HTML | 转义；无注入 |
| T-STORAGE-01 | 原子写/checksum | 无半文件；DB 校验 |
| T-RETRY-01 | 失败重试 | 同 report_id；最终 succeeded；无重复文件 |
| T-CANCEL-01 | 取消 | report failed/JOB_CANCELLED；无成功文件 |
| T-PERF-01 | 12 个月 | p95 ≤20s |

### 11.3 命令

```bash
make test-reports       # unit + contract + api + integration
make test-reports-parity
make test-isolation      # T-ALL 包含 report
make test-perf           # 报告 perf（nightly）
make api-check && make api-snapshot && make sdk
make verify-p2           # 最终全量
```

---

## 12. 实施顺序与估时

| 阶段 | 内容 | 估时 |
|---|---|---|
| 0 | 契约/表/迁移/API-Catalog/OpenAPI/schema version/RLS | 0.5d |
| 1 | `Report` 模型/仓储/spec/spec_hash/id/snapshot DTO | 0.5d |
| 2 | API-039~042 + 幂等/复用/列表/详情/下载/ETag | 1.5d |
| 3 | `build_period_data` 抽取 + `report_generate` handler/重试/取消 | 1.0d |
| 4 | 七区块 + Jinja2 模板 + 自包含 CSS + 存储原子写 | 1.5d |
| 5 | 测试（API/区块/parity/离线/隔离/存储/性能）+ 文档 | 1.5~2.0d |
| **09a 合计** | 不含归因接入、重复错误规则（区块占位） | **6.0~7.0d** |
| 09b | 归因区块接入（08 后）+ 重复错误/行动规则批准与测试 | +1.0~1.5d |
| 可选 | 原生 PDF | +1.5d |

> **DEV-PLAN 的 2.5 人日不可达**：仅 API+异步+HTML+测试已约 6d；若砍掉重复错误/行动/归因/PDF 可压到 ~4.5d，但仍超 2.5d。请人工确认排期（A-01/A-03）或缩减范围。

### 文件清单（实现阶段）

```
新增：backend/migrations/versions/0008_review_reports.py
新增：backend/packages/delivery/src/stock_delivery/reports/**
新增：backend/packages/delivery/templates/reports/**
新增：backend/services/api/src/stock_api/routers/reports.py
新增：backend/tests/api/test_reports.py、test_reports_parity.py
新增：backend/tests/unit/delivery/test_report_*.py
新增：backend/tests/contract/test_report_block.py
修改：backend/services/worker/src/stock_worker/handlers.py（handler 6 参）
修改：backend/services/worker/src/stock_worker/runner.py（object_store 装配）
修改：backend/services/api/src/stock_api/app.py（router 注册）
修改：backend/packages/review/src/stock_review/application/rebuild_period.py（抽取）
修改：backend/packages/delivery/pyproject.toml（仅 jinja2；不新增 stock-review）
新增：backend/services/worker/src/stock_worker/report_data.py（review 适配器）
修改：Makefile、CI、version.py、DATABASE.md、API-Catalog/OpenAPI/SDK
```

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 08 缺失/归因占位 | FR-D2 不完整 | 09a/09b 分段；人工确认；09b 只接 08 输出 |
| 报告数字与 metrics 漂移 | 验收失败/信任崩塌 | 同源 `build_period_data` + snapshot + parity 脚本；禁止渲染层重算 |
| 报告构建过重 | 生成慢/阻塞 | 单飞/配额；Pf-6 20s；可后续改为后台预热 |
| HTML 外部依赖/注入 | 离线失败/安全 | Jinja2 autoescape、无 script/外链、词表/快照/安全测试 |
| 文件与 DB 不原子 | 半份/孤儿 | tmp+rename；checksum/大小校验；重试覆盖；清扫 |
| 报告口径/action 合规 | 误导/合规风险 | A-03 规则人工批准；词表扫描；非 ok 不出结论 |
| 迁移/RLS 遗漏 | 数据越权 | 0008 RLS + gate + 跨租户测试 |
| 并发/重试重复报告 | 多份/重复计费 | 确定性 ID + unique + 幂等 + 单飞 |
| report 表/模型放 delivery 但与 review schema 混用 | 架构争议 | 评审 B-05；必要时模型放 review、仓储在 delivery |
| API 与 worker 未共享 data 卷 | 下载 404/500 | 部署约束/Compose 测试；后续对象存储 |
| 后续 metrics/asset 变更 | 旧报告不可比 | report_schema/definition_versions + data_as_of；不覆盖旧报告 |

---

## 14. HITL 决策清单（需人逐项确认）

| # | 问题 | 推荐 | 备选 | 不确认的后果 |
|---|---|---|---|---|
| A-01 | 08 未做，报告是否可先交 09a（归因占位） | 是，09a/09b 分段 | 等 08 / 自实现归因 | 范围/验收无法定稿 |
| A-02 | 原生 PDF 是否本期做 | 否，HTML+打印；pdf 请求 422 | WeasyPrint +1.5d | API/PRD 字段冲突 |
| A-03 | "重复错误/一条行动"阈值与文案谁定 | 人工批准规则表；未批准前 unavailable | 不做/LLM/硬编码 | 报告不能下结论或不可审计 |
| A-04 | 报告是否触发上游重算 | 是，复用 `build_period_data` | 先 API-083/只算 metrics | 数字新鲜度/验收风险 |
| A-05 | blocked 说明单阈值 | 仅 nav/asset 硬阻断 | 任一核心指标/全部 | blocked 误判/误导 |
| B-01 | spec_hash 是否含 definition_versions | 是 | 否 | 口径升级后复用旧报告 |
| B-02 | 单飞粒度 | `(user, period)` | `(user, period, spec)` | 并发/配额行为 |
| B-03 | 模板技术 | Jinja2 | 手写字符串 | 可维护性/依赖 |
| B-04 | 存储/清理 | LocalObjectStore + 原子写；不删 | DB BYTEA/S3/TTL | 运维/迁移 |
| B-05 | report 表归属 | `review.report` | delivery schema | 架构/RLS gate |
| B-06 | 取消状态 | failed + JOB_CANCELLED | 新增 cancelled | API 枚举/前端 |
| C-01 | 图表 | 可选内联 SVG；无 JS | ECharts/无图 | 离线/依赖 |

---

## 15. 验收清单（实现完成后）

- [ ] 迁移 0008 + schema_version + RLS/授权/DB 文档同步；`make migrate-check` 无漂移。
- [ ] API-039~042、API-062 全部通过；OpenAPI/sdk/api-check 同步。
- [ ] 25 个报告测试（T-039~T-042）通过；跨租户/ admin 403 全部通过。
- [ ] 报告 HTML 与 `/metrics` 逐指标 parity ≤ 容差；HTML 数字属性一致。
- [ ] 离线打开 HTML（断网、file://）成功；无外链/script；打印布局正常。
- [ ] blocked 区间生成说明单：`blocked=true`，无收益结论/数值。
- [ ] 同 spec 复用 report_id；同 key 幂等；并发/重试/取消行为符合 §7。
- [ ] 文件原子写/checksum/大小校验；失败重试无半文件/重复报告。
- [ ] 报告 job ≤20s（12 个月夹具）；日志无敏感信息。
- [ ] 09b（若批准）：归因区块接入、重复错误/行动规则与合规测试。
- [ ] 更新 DEV-PLAN Period2-09 验收描述（含 09a/09b 范围与 PDF 约定）。
