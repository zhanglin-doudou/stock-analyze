# 部署与上线准入清单（Docker + Traefik）

> **状态**：2026-11-22 制定，开始实施（P0-1 进行中）
> **关联**：[DEV-PLAN-v2 §5 交付阶段](DEV-PLAN-v2.md#5-交付阶段最后做8-人日) D-01~D-07；本文件是部署专项的**单一事实源**，DEV-PLAN 只保留任务行与指针。
> **口径变更**：D-02 原方案为 nginx 反代，2026-11-22 改为 **Traefik 作为唯一入口网关**；静态站点仍由 web 容器内的 nginx 提供，Traefik 不再承担静态文件与 gzip 之外的 Web 服务职责。

---

## 0. 目标形态

```
                     ┌─────────────────────────────┐
   Internet ──80/443──▶ Traefik（TLS 终止 / 路由 / 中间件）
                     └──────┬──────────────┬───────┘
                            │              │
              Host + /api/* │              │ Host（其余路径）
                            ▼              ▼
                     api × N（uvicorn）   web（nginx + SPA 静态）
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
           worker        migrate（一次性）  cron（sync-market / sync-sectors / 清理）
                            │
                            ▼
              云主机 PostgreSQL:5432（**Docker 编排外**；5432 仅对应用主机开放）
```

**路由表（单域名同源，避免 CORS）**

| 优先级 | 规则 | 上游 | 说明 |
|---|---|---|---|
| 100 | `Host(域) && PathPrefix(/api/)` | api:8000 | 业务 API，路径原样透传（应用无 `root_path`） |
| 100 | `Host(域) && Path(/healthz\|/readyz\|/version)` | api:8000 | 容器探针与版本 |
| 10 | `Host(域)` | web:80 | SPA 静态 + history fallback |
| — | `/metrics`、`/docs` | **不路由** | 仅内网抓取；web 对 `/metrics` 显式 404 |

**服务矩阵**

| 服务 | 镜像 | 副本 | 说明 |
|---|---|---|---|
| traefik | `traefik:v3.3` | 1 | 80/443、ACME、docker provider（自动发现副本） |
| web | `stock-analyze/web` | 1 | `vite build` 产物 + nginx |
| api | `stock-analyze/api` | ≥2 | 同镜像入口 `api`，无状态，可水平扩 |
| worker | `stock-analyze/api` | 1 | 同镜像入口 `worker`（JobQueue 租约天然单飞，扩容前先评估） |
| migrate | `stock-analyze/api` | 一次性 | `alembic upgrade head`，api 依赖其成功退出 |
| db | 云主机 PostgreSQL:16+ | — | **不在编排内**：5432 由云主机提供；角色由 `init-prod/00-bootstrap.sh` 手工初始化 |
| cron | 复用 api 镜像 | 1 | 收盘同步、清理任务（P1，可先用宿主 cron） |

---

## 1. 现状盘点（2026-11-22）

**已有，可直接复用**

| 能力 | 位置 | 备注 |
|---|---|---|
| 健康探针 | `/healthz`、`/readyz`（含 schema 版本校验）、`/version`、`/metrics` | [ops.py](../backend/services/api/src/stock_api/routers/ops.py) |
| 生产禁用 `/docs` | `STOCK_ENV=prod` 时 `docs_url=None` | [app.py](../backend/services/api/src/stock_api/app.py) |
| 结构化日志 | prod 自动 JSON 输出 | `configure_logging(..., json_output=env=="prod")` |
| 三角色 + RLS | owner 迁移 / app 运行 / backup 只读 | [DATABASE.md](DATABASE.md) |
| 迁移校验 | 启动即校验 schema 版本，`readyz` 二次确认 | `_check_schema_version` |
| worker 租约/心跳 | 过期租约回收、心跳续租、单飞 | [runner.py](../backend/services/worker/src/stock_worker/runner.py) |
| SSE 不缓冲提示 | `X-Accel-Buffering: no`、`Cache-Control: no-cache` | jobs / chat 两个流 |
| 同源前端 | API 基址为相对路径 `/api/v1` | [api-client](../frontend/packages/api-client/src/http.ts) |
| 开发用 PG 编排 | `infra/compose/docker-compose.dev.yml`（仅 db） | 端口 5433 |
| 后端测试 CI | `p2-metrics.yml`（fast/full/nightly） | 无镜像构建 |

> 本表为 2026-11-22 制定时的快照；实施进度以 §2 状态列为准。

**缺失（本清单要补齐）**

| 缺口 | 影响 |
|---|---|
| 无任何 Dockerfile、无生产 compose | 无法交付 |
| 无 Traefik 配置 | 无入口、无 TLS |
| `00-bootstrap.sql` 硬编码 dev 密码 | 生产库密码即公开 |
| 客户端 IP 只认 `X-Real-IP`，Traefik 默认不发 | 登录失败计数/验证码限流按代理 IP 归并 |
| 无 `/metrics` 外部访问控制 | 指标裸奔（API 层为公开端点） |
| 无备份/WAL/恢复演练 | 不满足 RPO ≤5min / RTO ≤30min |
| 无定时任务编排 | 行情/板块不同步、验证码与时间线不清理 |
| 无镜像构建/扫描/发布流水线 | 手工发布，无门禁 |
| 无部署手册/Runbook | 故障时无 SOP |

---

## 2. 准入清单

> 状态标记：`[ ]` 未开始 ｜ `[~]` 已落地、待运行验收 ｜ `[x]` 已完成（证据见末列）
> **P0 = 上线阻塞**；P1 = 上线前应完成；P2 = 上云后闭环。

### 2.0 进度总览（2026-11-22 15:45）

| 状态 | 数量 | 含义 |
|---|---|---|
| `[x]` 已完成 | 8 | 配置校验 / 单测 / 文件级证据可复核 |
| `[~]` 已落地待验收 | 9 | 交付物就位，等镜像构建与起栈后做运行验收 |
| `[ ]` 未开始 | 18 | 见对应行 |

**本轮已交付物（可直接复核）**

- 镜像与编排：`infra/docker/Dockerfile.api`、`Dockerfile.web`、`entrypoint.sh`、`infra/nginx/default.conf`、`infra/compose/docker-compose.prod.yml` / `.local.yml`
- 网关：`infra/traefik/dynamic/middlewares.yml`（安全头/CSP、compress 排除 SSE、限流）
- 配置与初始化：`infra/.env.prod.example`、`infra/postgres/init-prod/00-bootstrap.sh`（云主机手工执行）
- 工程入口：`make deploy-*`、`scripts/deploy-smoke.sh`
- 代码守卫：`request_ip.py`（XFF 最右可信值）、`config.py`（prod 弱口令 fail-closed）+ 12 个单测

**当前唯一硬阻塞**：Docker Hub 不可达（`curl registry-1.docker.io` 8s 超时；ghcr 正常），镜像构建无法完成；构建缓存已保留，网络恢复后直接 `make deploy-prod` / `deploy-local`。


### P0-1 镜像与编排（D-01）

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P0-1.1 | `infra/docker/Dockerfile.api` 多阶段构建 | 非 root（uid 10001）、`uv sync --frozen`、`live`/`email` extra 可开关、镜像内可跑 `api/worker/migrate/cli` 四入口 | [~] | Dockerfile 已落地（多阶段、非 root、四入口，`--build-arg EXTRAS=` 开关 live/email）；构建被 Docker Hub 拉层阻塞，四入口待镜像复验 |
| P0-1.2 | `infra/docker/Dockerfile.web` 多阶段构建 | Node 22 + pnpm 构建 → nginx alpine；产物含 `dist/index.html`；镜像 <100MB | [~] | Dockerfile 已落地；构建、`dist/index.html` 与镜像体积待复验 |
| P0-1.3 | `infra/compose/docker-compose.prod.yml` | `docker compose config` 通过；服务 traefik/web/api/worker/migrate 齐全（db 在编排外）；api 可 `--scale api=2` | [x] | prod + local 两套 `docker compose config -q` 均 exit 0；Traefik 通过 labels 发现副本 |
| P0-1.4 | 镜像/编排健康检查 | db `pg_isready`；api `/readyz`；web `GET /`；traefik ping；worker 进程存活 | [~] | db/api/web/traefik 健康检查已声明；worker 以进程存活为准，待起栈验证 |
| P0-1.5 | 迁移先于 api | api `depends_on: migrate: service_completed_successfully`；migrate 失败则 api 不起 | [~] | api/worker 均 `depends_on: migrate: service_completed_successfully`，待起栈验证失败阻断 |
| P0-1.6 | 本地全套预演 | `make deploy-local` 后 `http://localhost` 可登录并完成一次导入 | [ ] | 待镜像构建完成后执行 `make deploy-local` + `make deploy-smoke` |
| P0-1.7 | `.dockerignore` | 排除 `.git/.venv/node_modules/var/.env`；构建上下文 <50MB | [x] | 实测上下文 6.0 MiB / 550 files；排除 .git/.venv/node_modules/var/.env |

### P0-2 Traefik 对接（D-02 改）

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P0-2.1 | 静态配置（entrypoint/provider/ACME） | 80→443 301；证书自动签发/续期；docker provider 自动发现 api 副本 | [~] | 80/443、ACME(LE)、docker provider、`idleTimeout=0` 已配置；行为待起栈/上云 |
| P0-2.2 | 路由与优先级 | `/api/*` 与探针到 api，其余到 web；`/metrics`、`/docs` 公网不可达 | [~] | api 优先级 100、web 10；`/metrics`、`/docs`、`/redoc` 由 nginx 显式 404 |
| P0-2.3 | 中间件栈 | 安全头（HSTS/CSP 等）、compress（**排除 `text/event-stream`**）、网关粗限流 | [~] | 安全头/CSP、compress 排除 `text/event-stream`、限流 120r/s 已配置；待冒烟 |
| P0-2.4 | **真实 IP 对接** | Traefik 后登录失败计数/验证码限流按真实客户端 IP 生效（XFF 最右可信值） | [~] | XFF 最右可信值实现（优先 X-Real-IP）+ 6 单测含伪造最左负向；Traefik 后端到端随 P0-1.6 |
| P0-2.5 | **SSE 透传** | `/api/v1/jobs/{id}/events` 与 chat SSE 经 Traefik 后逐帧到达、不被缓冲/截断 | [ ] | 待起栈后用 `curl -N` 验证逐帧到达 |
| P0-2.6 | `TRUSTED_PROXY_CIDRS` | 覆盖 Docker 网络网段；非受信 peer 伪造 XFF 无效（负向用例） | [x] | `TRUSTED_PROXY_CIDRS=172.16.0.0/12`，sa-net 固定 172.28.0.0/16；负向单测通过 |
| P0-2.7 | CSP 实测 | 浏览器控制台无 CSP 违规；图表/悬浮助手/导入向导均可用 | [ ] | 待浏览器实测无 CSP 违规（图表 / 悬浮助手 / 导入向导） |

### P0-3 密钥与配置（D-05）

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P0-3.1 | 生产 env 模板 `infra/.env.prod.example` | 覆盖全部必填项；无任何真实密钥；`.env.prod` 不入仓 | [x] | `infra/.env.prod.example`；`infra/.env.prod` 已 gitignore |
| P0-3.2 | 密钥注入 | `STOCK_JWT_SECRET`/`EMAIL_CODE_PEPPER`/DB 密码/`GLM_API_KEY`/`MIMO_API_KEY`/`SECTOR_THS_COOKIE` 均由部署侧注入，仓库零明文（LLM 供应商目录见 `config/llm_providers.json`，key 环境变量名在目录里声明） | [~] | 注入点已定义（env_file / 密钥管理）；GLM key、MIMO key、THS cookie、DB 密码待部署侧填写 |
| P0-3.3 | **生产弱口令守卫** | `STOCK_ENV=prod` 时若 JWT secret/pepper 仍为 dev 默认或 <32 字符 → 启动失败 | [x] | prod 守卫：dev 默认值或 <32 字符即启动失败；6 单测 |
| P0-3.4 | 生产库初始化 | `init-prod` 脚本在**云主机**手工执行，用环境变量建角色（不硬编码密码）；重复执行幂等 | [x] | 2026-11-22 在云主机执行成功：三角色（owner/app/backup，均 NOBYPASSRLS）+ `stock_analyze` 库 + citext/pgcrypto；容器内 `stock_app` 认证通过 |
| P0-3.5 | seed 隔离 | 生产镜像/编排不含 `seed-*` 自动执行；文档明示禁止弱口令账号 | [x] | 编排无 `seed-*` 自动执行；§3.5 明示 `1234@dev.com` 等弱口令账号禁止进入生产 |
| P0-3.6 | 邮件通道 | `EMAIL_PROVIDER=aliyun` 使用 RAM 角色/OIDC，不落 AK；`EMAIL_*_ENABLED` 按上线节奏开启 | [ ] | 待阿里云 RAM 角色/OIDC 与 DM 账号 |
| P0-3.7 | 依赖与镜像扫描 | `uv`/`pnpm` 审计 + 镜像 trivy 无 High/Critical（或有豁免记录） | [ ] | 待 uv/pnpm 审计 + 镜像 trivy（依赖镜像构建） |

### P0-4 数据库运维（D-03/D-04）

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P0-4.1 | 生产 PG（云主机 5432） | `max_connections` ≥ 估算；`shared_preload_libraries=pg_stat_statements`；5432 仅对应用主机开放（安全组）；建议 TLS | [x] | `max_connections=200`（估算 ~90）、`pg_stat_statements`（含扩展）、慢查询 200ms；公网 5432 实测超时（安全组拦截）+ pg_hba 仅 localhost/`172.16.0.0/12`；TLS 待评估 |
| P0-4.2 | 备份链路（云主机侧） | WAL 归档 + 每日全量 + `pg_verifybackup`；`stock_backup` 角色只读 | [ ] | 待云主机落实（备份为云主机/云盘职责，不在 Docker 编排内） |
| P0-4.3 | 恢复演练（云主机侧） | 从全量+PITR 恢复到指定时刻，RTO ≤30min 有计时记录 | [ ] | 待云主机落实；`make restore-drill` 未实现 |
| P0-4.4 | 零停机发布 | expand-contract 演练脚本 + 回滚说明；滚动升级期间 `readyz` 对旧实例 fail-closed 行为符合预期 | [ ] | 回滚说明已产出（§6.2/§6.3 含 downgrade 示例）；expand-contract 演练与滚动升级实测待做 |

### P1 定时任务与可观测

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P1-1 | cron 编排 | 收盘后 `sync-market`；`sync-sector-industry/concept/bars`；`purge-email-codes`；`chat-events-cleanup` 均按计划执行且有失败告警 | [ ] | 未开始 |
| P1-2 | 监控告警 | `/metrics` 内网抓取（Prometheus）；队列积压、LLM 降级、板块同步失败、WAF 熔断设阈值 | [ ] | 未开始 |
| P1-3 | 日志治理 | JSON 日志收集/轮转；`request_id/user_id/job_id` 可检索；无金额/令牌明文 | [ ] | 未开始 |

### P1 CI/CD 与发布门禁（D-06）

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P1-4 | 镜像构建流水线 | tag=git sha；构建缓存；推送到镜像仓库 | [ ] | 未开始 |
| P1-5 | 发布门禁 | `make api-snapshot && api-check && sdk`、`test-p2`、镜像扫描全部通过才可发布 | [ ] | 未开始 |
| P1-6 | 回滚开关 | 镜像 tag 回滚 + 迁移回滚说明；回滚演练一次 | [ ] | 未开始 |

### P2 文档与上云验收（D-07）

| ID | 事项 | 验收标准 | 状态 | 证据 / 待办 |
|---|---|---|---|---|
| P2-1 | 部署手册 | 干净 VM 按本文件 ≤30 分钟完成部署 | [ ] | 未开始 |
| P2-2 | Runbook | SOP-1~10（含 DB 不可用、证书失败、SSE 断流、限流误伤） | [ ] | 未开始 |
| P2-3 | 容量与扩容 | api 副本扩容、连接池算式、PG 连接上限表 | [ ] | 未开始 |
| P2-4 | 验收报告模板 | 含 §4 全部上云验证项与证据位置 | [ ] | 未开始 |

---

## 3. 关键对接点（最容易漏的 5 个坑）

### 3.1 真实客户端 IP

- 现状：应用只信任 `TRUSTED_PROXY_CIDRS` 内 peer 的 `X-Real-IP`（[request_ip.py](../backend/services/api/src/stock_api/request_ip.py)）。
- Traefik 默认只追加 `X-Forwarded-For`（最右值为它看到的对端地址），**不发 `X-Real-IP`**。
- 结论：应用侧改为「受信 peer 时优先 `X-Real-IP`，否则取 XFF 最右合法值」；`TRUSTED_PROXY_CIDRS` 配 Docker 网段。
- 负向：非受信 peer 携带伪造 XFF → 仍按 peer 哈希，不得采信。
- **不得**用 uvicorn `--forwarded-allow-ips=*` 绕过（客户端可伪造）。

### 3.2 SSE 透传

- 两个流：`GET /api/v1/jobs/{id}/events`、chat 的 `Accept: text/event-stream`。
- Traefik 本身流式转发，但 **compress 中间件必须排除 `text/event-stream`**，否则可能整体缓冲。
- 入口 `idleTimeout` 不得短于最长流时长（chat 单轮上限 60s）；验收以「逐帧到达」而非「最终有响应」为准。

### 3.3 `/metrics` 与 `/docs`

- `/metrics` 在 API 层是**公开**端点（`openapi_meta` 的 public 集合），`/docs` 仅 prod 自动关闭。
- 网关不路由 `/metrics`；web 对 `/metrics` 显式 404（避免 SPA fallback 返回 200 HTML 造成“假可达”）。
- Prometheus 走内网 `api:8000/metrics` 或独立内网 router。

### 3.4 生产数据库（云主机 5432，不在编排内）

- 生产库由**云主机 PostgreSQL:5432** 提供，`docker-compose.prod.yml` 不含 db 服务；`api/worker/migrate` 通过 `STOCK_DB_HOST` 直连。
- 角色初始化：在云主机上手工执行 `infra/postgres/init-prod/00-bootstrap.sh`（`PGHOST/PGPORT + STOCK_DB_SUPERUSER` + 三个密码环境变量），幂等；仓库 `00-bootstrap.sql` 仅 dev Docker 用。
- 密码轮换：云主机 `ALTER ROLE` + 应用侧改 `.env.prod`；改密码不影响已存在数据。
- 网络边界：5432 **仅对应用主机开放**（安全组收紧）；建议 TLS（`sslmode=require` 可用 `STOCK_DB_*_DSN` 整串覆盖）。
- **拓扑决议（2026-11-22）**：应用与数据库同机部署在云主机；`.env.prod` 的 `STOCK_DB_HOST=host.docker.internal`（compose 已加 `host-gateway` 映射）。**不要填 `localhost`**（容器内的 localhost 是容器自身）。
- 云主机 PG 已配置：`listen_addresses='*'` + pg_hba 仅放行 `127.0.0.1/32`、`::1/128`、`172.16.0.0/12`（Docker 网段）；公网 5432 由云安全组拦截（实测超时）；`max_connections=200`、`pg_stat_statements`、慢查询 200ms；配置备份在 `/etc/postgresql/16/main/*.bak-*`。
- fail-fast：`make deploy-*` 会拒绝 `.env.prod` 里残留 `REPLACE_ME*` 占位符并列出键名（避免 asyncpg DNS 报错）。
- 备份/WAL 归档/恢复演练属云主机侧职责（P0-4.2/4.3），不在 Docker 编排内。

### 3.5 生产弱口令守卫

- 已在 `Settings` 增加 prod 校验：JWT secret / pepper 为 dev 默认值或 <32 字符 → **启动即失败**（fail-closed，单测覆盖）。
- `make seed-*` 创建的 `1234@dev.com` 等**弱口令账号禁止进入生产**；生产编排不执行任何 seed，只能显式 `cli` 调用且需人工确认。

---

## 4. 上云后必测（DEV-PLAN §5 验收单）

| # | 项 | 通过标准 |
|---|---|---|
| 1 | 干净 VM 从零部署 | 按手册 ≤30 分钟，一次成功 |
| 2 | 真实 TLS/域名 | 证书链有效、HSTS 生效、HTTP→HTTPS 301 |
| 3 | 滚动升级 | 5xx <0.1%；`--scale api=2` 逐副本替换 |
| 4 | 副本故障转移 | 杀掉一个 api 副本，请求无失败 |
| 5 | PITR | 全链路恢复计时 RTO ≤30min，RPO ≤5min |
| 6 | SSE | 导入进度与 chat 流式经公网逐帧到达 |
| 7 | 限流按真实 IP | 两个客户端 IP 互不影响；伪造 XFF 不生效 |
| 8 | live 行情 | 连续跑一周，观察限频/熔断/缓存命中 |

---

## 5. 实施顺序

1. P0-1 镜像与编排（先能起）→ 2. P0-2 Traefik 与 IP/SSE（能通）→ 3. P0-3 密钥与守卫（能安全起）→ 4. P0-4 备份演练（能恢复）→ 5. P1 定时任务与 CI（能运维）→ 6. P2 文档与上云验收（能交付）。

**回滚原则**：应用回滚只换镜像 tag；迁移采用 expand-contract，回滚不执行 `downgrade`（破坏性），以“前一版本兼容新 schema”为准。

---

## 6. 常用命令（实施中补全）

```bash
# 生产编排（首次）
cp infra/.env.prod.example infra/.env.prod   # 填密钥（勿提交）
docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml up -d --build

# 扩 api 副本
docker compose -f infra/compose/docker-compose.prod.yml up -d --scale api=2

# 本地全套预演（HTTP，无 TLS）
make deploy-local
./scripts/deploy-smoke.sh            # 路由/探针/安全头冒烟（或 make deploy-smoke）

# 仅校验编排（db 不在编排内）
make deploy-config

# 首次：先在云主机初始化角色（PGHOST/PGPORT 指向云主机）
# PGHOST=<ip> STOCK_DB_SUPERUSER=postgres STOCK_DB_OWNER_PASSWORD=... \
#   STOCK_DB_APP_PASSWORD=... STOCK_DB_BACKUP_PASSWORD=... \
#   sh infra/postgres/init-prod/00-bootstrap.sh
```

---

### 6.1 本地构建 → 云主机部署（arm64 Mac → x86_64 云主机）

> 云主机 1.15.38.201（ubuntu）：PG 16.15 同机、Docker 29.10、镜像加速 mirror.ccs.tencentyun.com。
> ubuntu 不在 docker 组，服务器侧 docker 命令一律 `sudo`。

```bash
# ① 本机交叉构建（arm64 必须指定平台；Intel Mac 可去掉 DOCKER_DEFAULT_PLATFORM）
cd /Users/bean/Documents/AI/stock-analyze
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose \
  --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml build api web
docker image inspect stock-analyze/api:local --format '{{.Os}}/{{.Architecture}}'   # 期望 linux/amd64

# ② 镜像流式传到云主机（不落临时文件）
docker save stock-analyze/api:local stock-analyze/web:local | gzip | \
  ssh -i ~/.ssh/local_macbp.pem ubuntu@1.15.38.201 'gunzip | sudo docker load'

# ③ 代码 + 配置（含 .env.prod 密钥，走 SSH 通道）
rsync -az --info=progress2 -e "ssh -i ~/.ssh/local_macbp.pem" \
  --exclude .git --exclude node_modules --exclude .venv --exclude var \
  --exclude .pnpm-store --exclude .uvcache --exclude .buildx \
  --exclude .mypy_cache --exclude .pytest_cache --exclude .ruff_cache \
  --exclude .hypothesis --exclude tmp-shots --exclude frontend/apps/web/dist \
  ./ ubuntu@1.15.38.201:~/stock-analyze/

# ④ 云主机起栈（--no-build 用预加载镜像；migrate 先行）
ssh -i ~/.ssh/local_macbp.pem ubuntu@1.15.38.201
cd ~/stock-analyze && chmod 600 infra/.env.prod
sudo docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml up -d --no-build

# ⑤ 验证
sudo docker logs stock-analyze-migrate-1 2>&1 | tail -5     # 期望 upgrade head 成功
./scripts/deploy-smoke.sh                                    # http://localhost
BASE_URL=https://xingst.site ./scripts/deploy-smoke.sh       # DNS 生效后走公网
```

### 6.2 云主机本机构建与替换 + 回滚（推荐）

> 云主机是 x86_64 原生构建（无需交叉编译），docker.io 走 mirror.ccs.tencentyun.com、ghcr.io 可达。
> **版本化约定**：镜像 tag 用 git sha（`infra/.env.prod` 的 `TAG`），旧镜像留在磁盘上即天然可回滚。

```bash
# ── ① 代码上服务器（rsync 见 §6.1 ③，或 git pull）后，记录当前版本 ──
ssh -i ~/.ssh/local_macbp.pem ubuntu@1.15.38.201
cd ~/stock-analyze
OLD_TAG=$(sed -n 's/^TAG=//p' infra/.env.prod)          # 记住当前 TAG（回滚目标）
NEW_TAG=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)

# ── ② 构建新镜像（tag=git sha）──
sed -i "s/^TAG=.*/TAG=$NEW_TAG/" infra/.env.prod
sudo docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml build api web

# ── ③ 替换：api/worker/web 用新镜像重建（migrate 先行）──
sudo docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml up -d --no-build

# ── ④ 验证 ──
sudo docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml ps
sudo docker logs stock-analyze-migrate-1 2>&1 | tail -5
./scripts/deploy-smoke.sh
```

**回滚（一条命令切回旧版本）**

```bash
cd ~/stock-analyze
sed -i "s/^TAG=.*/TAG=$OLD_TAG/" infra/.env.prod     # 切回旧 tag
sudo docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml up -d --no-build
./scripts/deploy-smoke.sh
```

- 回滚只换镜像，**不执行 `alembic downgrade`**；前提是上一版本兼容当前 DB schema（expand-contract 发布纪律，见 P0-4.4）。
- `api/worker/migrate` 共用 api 镜像，回滚时一起切换；web 镜像独立同 tag。
- 旧镜像保留策略：磁盘上至少留最近 3 个 tag，再清 `sudo docker image prune`（勿用 `prune -a` 误删待回滚镜像）。
- 查询历史版本：`sudo docker images stock-analyze/api` / `stock-analyze/web`。

### 6.3 完整示例：构建 → 滚动升级 → 迁移回退（downgrade）→ 镜像回滚

> 场景：`$OLD_TAG`（当前线上）→ `$NEW_TAG`（含新迁移），升级后发现问题需要回退。
> ⚠️ `downgrade` 会**删表/删列，数据不可逆**（如 `0020_chat_assistant` 的 downgrade 删全部 chat 表）。
> 只在「新版本刚上线、其产生的数据可丢」的补救窗口使用，且升级前必须备份。常规回滚仍优先**只换镜像、不 downgrade**（expand-contract）。

```bash
ssh -i ~/.ssh/local_macbp.pem ubuntu@1.15.38.201
cd ~/stock-analyze
export COMPOSE="sudo docker compose --env-file infra/.env.prod -f infra/compose/docker-compose.prod.yml"

# ── ⓪ 升级前：备份 + 记录迁移版本（必做）──────────────────
OLD_TAG=$(sed -n 's/^TAG=//p' infra/.env.prod)
sudo -u postgres pg_dump -Fc stock_analyze > /tmp/backup-$OLD_TAG-$(date +%Y%m%d%H%M).dump
$COMPOSE run --rm --no-deps migrate alembic -c backend/alembic.ini current   # 记下 revision

# ── ① 构建新镜像 + 滚动升级（api×2 逐副本替换）────────────
NEW_TAG=$(git rev-parse --short HEAD)
sed -i "s/^TAG=.*/TAG=$NEW_TAG/" infra/.env.prod
$COMPOSE build api web
$COMPOSE up -d --no-build --scale api=2     # Traefik /readyz 自动摘除不健康副本

# ── ② 升级验证 ─────────────────────────────────────────────
./scripts/deploy-smoke.sh
curl -s https://xingst.site/version         # 核对 app_version / schema_version
```

```bash
# ── ③ 出问题：看迁移版本链，确定回退目标 ───────────────────
$COMPOSE run --rm --no-deps migrate alembic -c backend/alembic.ini history
$COMPOSE run --rm --no-deps migrate alembic -c backend/alembic.ini current
```

```bash
# ── ④ 版本回退（含迁移；分钟级不可用的补救流程）──────────
$COMPOSE stop api worker          # 先停新版本，避免新代码打旧 schema
$COMPOSE run --rm --no-deps migrate alembic -c backend/alembic.ini downgrade -1    # 退一步；或 downgrade <revision>
sed -i "s/^TAG=.*/TAG=$OLD_TAG/" infra/.env.prod
$COMPOSE up -d --no-build --scale api=2
./scripts/deploy-smoke.sh
```

**要点**

- **顺序铁律：停新 → downgrade → 起旧**。反过来（旧代码 + 新 schema）或漏停（新代码 + 旧 schema）都会让 `readyz` 的 schema 版本校验 fail-closed——这是设计意图，防止错配上线。
- **纯代码回滚（新版本无迁移）**：跳过 ③④ 的 stop 与 downgrade，直接切 `TAG` 再 `up -d`（零停机，见 §6.2）。
- `downgrade -1` 只退一步；多步用 `downgrade <revision>`（revision 从 `history` 取）。执行后 `current` 应等于 `$OLD_TAG` 版本代码期望的 schema 版本。
- downgrade 补救窗口之外的数据丢不起时，**别 downgrade**，用 ⓪ 的备份恢复（P0-4.3 演练目标）：

```bash
sudo -u postgres pg_restore -d stock_analyze --clean --if-exists /tmp/backup-<旧>.dump
```

**DNS/证书未就绪时**：加 `-f infra/compose/docker-compose.local.yml` 起 HTTP 预演（ACME 不需要）。
**扩容**：`sudo docker compose ... up -d --no-build --scale api=2`；**停止**：`... down`（PG 数据不受影响）。
**回滚**：重新 `docker load` 旧镜像 + `up -d --no-build`；迁移遵循 expand-contract，不做 downgrade。

---

## 7. 实施记录

| 日期 | 变更 | 证据 |
|---|---|---|
| 2026-11-22 | 制定准入清单；D-02 由 nginx 改为 Traefik | 本文件 |
| 2026-11-22 | 落地 Dockerfile.api / Dockerfile.web / prod compose / Traefik 动态中间件 / 生产 env 模板 / init-prod | `infra/docker/`、`infra/compose/`、`infra/traefik/` |
| 2026-11-22 | 真实 IP 对接（XFF 最右可信值）+ prod 弱口令守卫 | `request_ip.py`、`config.py`；`test_request_ip.py`（6 例）、`test_config_prod_guard.py`（6 例）全绿 |
| 2026-11-22 | 编排静态校验通过；Makefile 部署目标；固定 `sa-net` 网段 172.28.0.0/16 | `docker compose config -q`（prod + local 均 exit 0）；`make deploy-config/deploy-prod/deploy-local/deploy-scale/deploy-smoke` |
| 2026-11-22 | 冒烟脚本 | [scripts/deploy-smoke.sh](../scripts/deploy-smoke.sh)：`/`、探针、`/metrics`/`/docs` 404、安全头 |
| 2026-11-22 15:5x | **生产数据库改由云主机 PostgreSQL:5432 提供，Docker 编排移除 db 服务**；init-prod 改为云主机手工执行；`.env.prod.example` 指向云主机；P0-1.3/P0-3.4/P0-4.1 口径更新；P0-4.1 回退 `[~]`（参数在云主机侧待核对） | `docker compose config -q` 通过（5 服务：traefik/web/api/worker/migrate，无 db） |
| 2026-11-22 15:45 | 状态同步：补「证据 / 待办」列与 §2.0 总览；新增完成项 P0-1.7（上下文 6.0 MiB）、P0-3.5（seed 隔离）、P0-4.1（PG 参数与连接数核算）；P0-1.1 补 `EXTRAS` 构建参数 | Docker Hub 仍超时（`registry-1.docker.io` 8s），ghcr 正常；P0-1.1/1.2 等 10 项待起栈 |
| 2026-11-22 | 镜像构建验证（受阻）：首次构建已完成两段 `uv sync`（含 live/email）与源码拷贝，在 `COPY alembic.ini` 暴露路径错误（文件在 `backend/` 而非根目录），已修正；重跑时 Docker Hub 拉层停滞（8.39/12.06MB 持续 8 分钟）未能出最终镜像 | P0-1.1/1.2 仍 `[~]`；网络恢复后执行 `make deploy-prod`（或 `deploy-local`）复验 P0-1.4/1.5/1.6 与 P0-2.1~2.5 |
