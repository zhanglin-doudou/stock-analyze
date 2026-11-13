# 数据库安装与 SQL 指引

本文是 [README](../README.md) 的数据库专项文档：本地 PostgreSQL 的两种安装方式、三角色模型、迁移执行、日常 SQL 操作与故障排查。

一期 schema 布局（3 个业务 schema + 1 个公共缓存 schema，由 Alembic 迁移 `0001_phase1` 创建；`0002_platform_tenant_rls` 补 platform 租户表 RLS；`0003_review_rounds` 建二期复盘表 round / round_fill / round_build_state 并同批启 RLS；`0004_review_nav` 建 `review.nav_daily`；`0005_market_cache` 建公共行情缓存 schema `market`；`0006_review_asset_daily` 建资产快照 `asset_daily` / 构建水位 `asset_build_state`；`0007_review_metrics` 建指标物化 `metric_result` / 血缘 `metric_dependency`；`0008_money_fund_bond` 增货基余额列并重写资产恒等式；`0009_metrics_period_tokens` 扩展指标 `period` 为月/年/自定义区间；`0010_asset_daily_remark` 增资产备注列；`0011_market_sectors` 建板块公共缓存 `market.sector` / `sector_member` / `benchmark_member` / `sector_bar` / `sector_pull_range` / `sector_sync_state` 六表（无 RLS、可再生））：

| Schema | 内容 |
|---|---|
| `platform` | 用户 / 令牌 / 任务 / 幂等键 / 审计 / 配置等平台表；**租户表 job / idempotency_key / user_setting / outbox_event 启 RLS**，认证/系统表不启（见 §2.1） |
| `ledger` | 流水 / 批次 / 预览 / 隔离区 / 摘要规则 / 对账（**全部启 RLS**） |
| `review` | 复盘域（二期已启用）：FIFO 回合 `round`、配对明细 `round_fill`、增量重建水位 `round_build_state`、每日净值 `nav_daily`、每日资产快照 `asset_daily`、资产构建水位 `asset_build_state`（**全部启 RLS**） |
| `market` | 公共行情/板块缓存（live 模式）：日线 `daily_bar`、区间覆盖 `pull_range`；板块 `sector`（行业树/概念目录）、`sector_member`（SCD2 成分）、`benchmark_member`（基准成分权重）、`sector_bar`（板块日线）、`sector_pull_range`（同步覆盖区间）、`sector_sync_state`（同步水位/状态）；**公共数据、不启用 RLS**，可再生（丢了可重拉），不进备份/恢复的正确性断言 |
| `delivery` | 交付域（三期启用） |

---

## 1. 安装本地数据库

### 方式 A：Docker Compose（推荐）

```bash
make db-up        # 等价于 docker compose -f infra/compose/docker-compose.dev.yml up -d db
```

- 镜像 `postgres:16`，宿主机端口 **5433**，数据卷 `sa-dev-pgdata`
- 首次启动自动执行 [infra/postgres/init/00-bootstrap.sql](../infra/postgres/init/00-bootstrap.sql)：装 `citext`/`pgcrypto` 扩展、创建 `stock_owner`/`stock_app` 角色、建库 `stock_analyze`（owner 为 `stock_owner`）
- 已开启 `pg_stat_statements` 与慢查询日志（>200ms）

验证：

```bash
docker exec -it sa-dev-db pg_isready -U postgres   # accepting connections 即就绪
```

### 方式 B：本机 PostgreSQL（无 Docker）

适用于已用 Homebrew 等安装的本机 PG（例如 PG 18，默认端口 **5432**）。

```bash
make db-bootstrap    # 执行 scripts/db_bootstrap.sql（需超级用户，默认取当前登录用户；可 SUPERUSER=postgres make db-bootstrap）
```

该脚本幂等：已存在的角色/库不会重复创建。它会：

1. 创建三个登录角色（见 §2）；
2. 创建数据库 `stock_analyze`（owner 为 `stock_owner`）；
3. 在库内安装 `citext` / `pgcrypto` 扩展并授权连接。

**本机方式必须覆盖端口/DSN**——编辑根目录 `.env`（`make env` 生成），取消注释并按实际端口修改：

```dotenv
STOCK_DB_PORT=5432
STOCK_DB_OWNER_DSN=postgresql+asyncpg://stock_owner:stock_owner_pw@127.0.0.1:5432/stock_analyze
STOCK_DB_APP_DSN=postgresql+asyncpg://stock_app:stock_app_pw@127.0.0.1:5432/stock_analyze
```

> 两种方式都走同一套默认口令（`stock_owner_pw` / `stock_app_pw` / `stock_backup_pw`），仅限本地开发；生产必须经密钥管理注入。

## 2. 角色模型（勿用超级用户跑应用）

| 角色 | 口令（本地默认） | 用途 | 权限边界 |
|---|---|---|---|
| `stock_owner` | `stock_owner_pw` | Alembic 迁移、测试脚手架（TRUNCATE/造数） | schema/表 owner，**不受 RLS 约束** |
| `stock_app` | `stock_app_pw` | API / worker 运行时 | 非 owner、`NOBYPASSRLS`、仅 DML；受 RLS 约束 |
| `stock_backup` | `stock_backup_pw` | 备份/校验（交付阶段 PITR 演练） | 只读 |

关键设计：应用运行时走 `stock_app`，即使代码漏掉 `user_id` 过滤，RLS 也会兜底（`user_id = current_setting('app.user_id')::uuid` 谓词）；迁移/造数才用 `stock_owner`。**任何代码路径都不要用 postgres 超级用户**——超级用户绕过 RLS，会掩盖隔离缺陷。

**worker 的例外（G7 之后）**：`WorkerRunner` 持有两套连接池——`db`（`stock_app`，handler 的租户事务，受 RLS）与 `queue_db`（`stock_owner`，**仅**用于跨租户的 `claim / reap_expired_leases / heartbeat` 队列控制面）。owner 不下发业务读写；handler 仍只拿 app 会话。

### 2.1 RLS 覆盖范围与例外（G7 / `0002_platform_tenant_rls`；P2-02 / `0003_review_rounds`；Period2-03 / `0004_review_nav`；Period2-16 / `0006_review_asset_daily`；Period2-05 / `0007_review_metrics`）

启用 RLS 的租户表（策略名统一 `tenant_isolation`，`USING` 与 `WITH CHECK` 同一谓词，未设变量时 fail-closed）：

| Schema | 表 |
|---|---|
| `ledger` | `trade` / `trade_history` / `import_batch` / `import_preview` / `quarantine_row` / `summary_rule`（系统规则放行 `user_id IS NULL`）/ `reconciliation` |
| `platform` | `job` / `idempotency_key` / `user_setting` / `outbox_event`（`user_id IS NULL` 的系统事件只有 owner 可读写） |
| `review` | `round` / `round_fill` / `round_build_state` / `nav_daily` / `asset_daily` / `asset_build_state` / `metric_result` / `metric_dependency`（全部 `user_id` 非空 + `tenant_isolation` 策略） |

**按设计不启用 RLS 的认证/系统表**（原因见迁移 `0002` docstring；`scripts/ci/gate_rls_coverage.py` 内显式 allowlist）：

| 表 | 原因 |
|---|---|
| `platform.user` | 登录按 email 查用户时尚未知 `user_id`，无法 `SET LOCAL`（认证前置） |
| `platform.refresh_token` / `platform.api_token` | 刷新/PAT 校验按 `token_hash` 查（认证前置，未知 `user_id`） |
| `platform.login_attempt` / `platform.rate_limit_bucket` | 按 email/IP/窗口键计数，认证前即需读写，无 `user_id` 语义 |
| `platform.config_release` / `platform.schema_version` / `platform.backup_run` | 全局系统状态，无 `user_id` |
| `platform.audit_log` | 含登录失败等 actor 未知事件；跨租户不可见由 API 层 actor 过滤保证 |
| `platform.job_event` | 无 `user_id` 列（从属于 `job`，按 `job_id` 访问；写入发生在 job 的租户事务内） |

> `market` schema 的 `daily_bar` / `pull_range` 是跨用户公共行情缓存（无 `user_id`），**按设计不启用 RLS**；`scripts/ci/gate_rls_coverage.py` 只扫 platform/ledger/review 的租户表，不会误报。写路径仅内部装配（不开放 HTTP 端点），`stock_app` 的 DML 由迁移 `0005_market_cache` 的 `GRANT` 授予。

覆盖门禁（新增租户表未补 RLS 即 CI fail；Period2-05 后 review 8 张表自动纳入）：

```bash
.venv/bin/python scripts/ci/gate_rls_coverage.py        # 默认取 .env / STOCK_DB_* 的 owner DSN
# 输出形如：platform: 4/4、ledger: 7/7、review: 8/8，exit 0；缺策略时列出表名并 exit 1
```

## 3. 执行迁移

```bash
make migrate         # alembic upgrade head + 显示 current
make migrate-check   # alembic check（模型与迁移是否漂移）
```

- 配置文件：[backend/alembic.ini](../backend/alembic.ini)，迁移脚本在 [backend/migrations/](../backend/migrations/)（async env，owner DSN）
- 查看状态：

```bash
uv run alembic -c backend/alembic.ini current
uv run alembic -c backend/alembic.ini history
uv run alembic -c backend/alembic.ini heads
```

- 新增迁移：`uv run alembic -c backend/alembic.ini revision --autogenerate -m "xxx"`，生成后**必须人工审阅**（RLS/授权语句不在 autogenerate 范围内）。
- 迁移 `0002_platform_tenant_rls`：为 `platform.job / idempotency_key / user_setting / outbox_event` 补 `tenant_isolation` 策略（`ENABLE RLS` + `CREATE POLICY`，可重复执行）。新增**带 `user_id` 的业务表**必须同批补 RLS，并跑 `scripts/ci/gate_rls_coverage.py`。
- 迁移 `0003_review_rounds`（Period2-02）：建 `review.round` / `review.round_fill` / `review.round_build_state` 三表 + `tenant_isolation` 策略 + `stock_app` DML 授权，并把 `platform.schema_version.version` 更新为 `0003_period2_review`（`stock_api/version.py::SCHEMA_VERSION` 同步）。
- 迁移 `0004_review_nav`（Period2-03）：建 `review.nav_daily`（`UNIQUE(user_id, trade_date)` + 普通索引）+ `tenant_isolation` 策略 + 授权，并把 `platform.schema_version.version` 更新为 `0004_period2_nav`。
- 迁移 `0005_market_cache`（live 行情）：建 `market` schema + `market.daily_bar` / `market.pull_range` 两表 + 索引 + `GRANT`（`stock_app` DML / `stock_backup` 只读）+ 把 `platform.schema_version.version` 更新为 `0005_market_cache`（`stock_api/version.py::SCHEMA_VERSION` 同步）；downgrade 回 `0004_period2_nav`。行情缓存属于**可再生公共数据**，不进备份/恢复演练的正确性断言。
- 迁移 `0006_review_asset_daily`（Period2-16 资产表）：建 `review.asset_daily`（`UNIQUE(user_id, trade_date)` + `CHECK(total_assets_units = cash_units + market_value_units)` + `CHECK(holding_count >= 0)`；**不建** `market_value >= 0`，超卖日允许负市值）与 `review.asset_build_state`（每用户一行水位线 + `version` 乐观锁），同批启 RLS + `stock_app` DML / `stock_backup` 只读授权；另在 `ledger.trade` 建 `ix_trade_user_updated_at (user_id, updated_at, trade_date)` 供增量脏查询；`platform.schema_version.version` 更新为 `0006_review_asset_daily`（`stock_api/version.py::SCHEMA_VERSION` 同步）；downgrade 回 `0005_market_cache`（含删索引/策略/两表）。
- 迁移 `0007_review_metrics`（Period2-05/06）：建 `review.metric_result`（指标物化，`UNIQUE(user_id, metric_id, period, definition_version, params_hash)`）+ `review.metric_dependency`（血缘）+ RLS/授权；`platform.schema_version.version` 更新为 `0007_review_metrics`。
- 迁移 `0008_money_fund_bond`（2026-10-29 货基口径）：`ledger.trade` 增 `money_fund_balance_units`、`review.asset_daily` 增 `money_fund_units`，并重写资产恒等式为 `total_assets_units = cash_units + money_fund_units + market_value_units`；版本推进到 `0008_money_fund_bond`。
- 迁移 `0009_metrics_period_tokens`（2026-10-30）：`metric_result.period` / `metric_dependency.period` 列宽 `varchar(7)` → `varchar(32)`，CHECK 支持 `YYYY-MM` / `YYYY` / `YYYY-MM-DD..YYYY-MM-DD`；版本推进到 `0009_metrics_period_tokens`。
- 迁移 `0010_asset_daily_remark`（2026-10-31）：`review.asset_daily` 增 `remark varchar(256)`（可转债等估值备注），口径版本升至 1.6；版本推进到 `0010_asset_daily_remark`。
- 迁移 `0011_market_sectors`（Period2-17 板块底座，设计 [2026-10-31-sector-data.md](architecture/2026-10-31-sector-data.md)）：建 `market` 板块 6 表 —— `sector`（行业树 / 概念目录）、`sector_member`（SCD2 半开区间 `[effective_from, effective_to)` + 行业当前部分唯一索引兜底"一股最多一个三级行业"）、`benchmark_member`（基准成分权重，`hs300` 等）、`sector_bar`（板块日线）、`sector_pull_range`（同步覆盖区间）、`sector_sync_state`（同步水位/状态）+ 索引 + `GRANT`（`stock_app` DML / `stock_backup` 只读）；`platform.schema_version.version` 更新为 `0011_market_sectors`（`stock_api/version.py::SCHEMA_VERSION` 同步）；downgrade 按 FK 逆序删本迁移 6 表、**不删 `market` schema**（`daily_bar` 仍在用）。板块缓存同属**可再生公共数据**、无 `user_id` / 无 RLS，仅由 CLI `sync-sectors`（`SECTOR_MODE=live`）写入，不进备份/恢复演练的正确性断言。
- 迁移 `0012_review_round_insights`（Period2-18 回合洞察，设计 [2026-11-04-fifo-round-insights.md](architecture/2026-11-04-fifo-round-insights.md)）：建 `review.trade_reason`（`UNIQUE(user_id, trade_dedupe_key, side)`，理由码/文本/标注时点/是否事后标注）与 `review.trade_forward_return`（`UNIQUE(user_id, trade_id, horizon)`，缓存 B0~B3 口径与状态，`ratio_*` 用标度整数、缺失为 NULL）+ `tenant_isolation` 策略 + `stock_app` DML / `stock_backup` 只读授权；`platform.schema_version.version` 更新为 `0012_review_round_insights`（`stock_api/version.py::SCHEMA_VERSION` 同步）；downgrade 回 `0011_market_sectors`（删两表/策略/授权）。
- 迁移 `0013_review_trade_flags`（API-095 账单旗标）：建 `review.trade_flag`（逐笔 20 码判定结果，`UNIQUE(user_id, trade_dedupe_key, flag_code)`）+ `tenant_isolation` 策略 + `stock_app` DML / `stock_backup` 只读授权；`platform.schema_version.version` 更新为 `0013_review_trade_flags`（`stock_api/version.py::SCHEMA_VERSION` 同步）；downgrade 回 `0012_review_round_insights`。
- 迁移 `0014_user_setting_preferences`（用户偏好 P0，设计 [2026-11-11-user-preferences.md](architecture/2026-11-11-user-preferences.md)）：`platform.user_setting` 增 7 列（`bill_near_equal/chase/stop_loss/short_term_*_bps` + `bill_window_trading_days smallint` + `market_session_prefs`/`concept_filter_prefs jsonb`）与 6 条 CHECK（4 个 bps 区间 + 窗口 1~20 + 两个 JSONB 必须是 object）；`review.trade_flag` 增 `flags_hash varchar(16)`（先用默认 `c425bc5b871f59f5` 回填，随后 **DROP DEFAULT** 让构建漏传 hash 立即失败）与 `ix_trade_flag_user_hash (user_id, flags_hash, definition_version)`；`platform.schema_version.version` 更新为 `0014_user_setting_preferences`（`stock_api/version.py::SCHEMA_VERSION` 同步）；downgrade 回 `0013_review_trade_flags`。
  - `concept_filter_prefs` 形状：`{"mode": "off|hide|exclude", "excluded": [{"code": "BK0999", "name": "人工智能"}]}`（`name` 为保存时快照；`resolved` **不入库**，由读路径按当前 `market.sector` 目录判定）。概念过滤是**读时生效**（API-087），不触发任何重算或落库。
- 启动即校验：API 启动时会核对 `platform.schema_version` 与代码版本，不匹配直接拒绝启动——先跑迁移再起服务。

## 4. 连接与执行 SQL

### psql 连接

```bash
# Docker 方式（容器内 psql）
docker exec -it sa-dev-db psql -U stock_owner -d stock_analyze        # 迁移/造数/排查看全量
docker exec -it sa-dev-db psql -U stock_app -d stock_analyze          # 模拟应用视角（受 RLS）

# 本机方式
psql -U stock_owner -h 127.0.0.1 -p 5432 -d stock_analyze
psql -U stock_app   -h 127.0.0.1 -p 5432 -d stock_analyze
```

### 执行脚本文件

```bash
# Docker
docker exec -i sa-dev-db psql -U stock_owner -d stock_analyze -v ON_ERROR_STOP=1 -f - < your.sql
# 本机
psql -U stock_owner -d stock_analyze -v ON_ERROR_STOP=1 -f your.sql
```

### Python 内执行（异步，与后端同栈）

```python
import asyncio
from sqlalchemy import text
from stock_platform.config import get_settings
from sqlalchemy.ext.asyncio import create_async_engine

async def main() -> None:
    engine = create_async_engine(get_settings().owner_dsn())
    async with engine.begin() as conn:
        print(await conn.scalar(text("select count(*) from ledger.trade")))
    await engine.dispose()

asyncio.run(main())
```

## 5. 常用 SQL

### 验证 RLS 生效（stock_app 会话）

```sql
-- 租户会话：设 app.user_id 后才可见本租户行
BEGIN;
SELECT set_config('app.user_id', '<A的uuid>', true);   -- true = 仅本事务生效
SELECT count(*) FROM ledger.trade;                      -- 只返回 A 的行
SELECT count(*) FROM platform.job;                      -- 同上（G7 起 job 也启 RLS）
ROLLBACK;

-- 未设置 app.user_id：默认拒绝，读到 0 行
SELECT count(*) FROM ledger.trade;                      -- 0
SELECT count(*) FROM platform.job;                      -- 0
SELECT count(*) FROM platform.user_setting;             -- 0
```

> worker 的 `claim / reap / heartbeat` 是唯一允许跨租户读写 `platform.job` 的路径，
> 走 owner 连接（`WorkerRunner.queue_db`）；业务代码/排查仍应用 app 角色 + `SET LOCAL`。

### 排查任务队列

```sql
-- 近期任务与失败原因
SELECT id, kind, status, attempt, max_attempts, stage, progress, error_code, created_at
FROM platform.job ORDER BY created_at DESC LIMIT 20;

-- 事件流（seq 即 SSE 的 event id）
SELECT job_id, seq, type, payload FROM platform.job_event
WHERE job_id = 'job_xxx' ORDER BY seq;

-- 卡住的运行中任务（租约是否过期）
SELECT id, status, lease_until, now() FROM platform.job WHERE status = 'running';
```

### 流水与导入排查

```sql
-- 某用户最近流水（定点整数 units，金额 2 位 / 价格 6 位 / 数量 4 位）
SELECT id, trade_date, security_code, side, amount_units, cash_balance_units, dedupe_key
FROM ledger.trade WHERE user_id = '<uuid>' ORDER BY trade_date DESC, occurrence_idx LIMIT 10;

-- 批次状态与计数
SELECT id, status, mode, parsed_count, inserted_count, skipped_count, quarantined_count, revoked_at
FROM ledger.import_batch WHERE user_id = '<uuid>' ORDER BY created_at DESC;

-- 隔离区未处理行
SELECT batch_id, line_no, reason_code, raw FROM ledger.quarantine_row
WHERE user_id = '<uuid>' AND NOT resolved ORDER BY created_at DESC;

-- 幂等键 / 审计
SELECT key, status_code, created_at FROM platform.idempotency_key ORDER BY created_at DESC LIMIT 10;
SELECT action, actor_id, at FROM platform.audit_log ORDER BY at DESC LIMIT 10;
```

### 慢查询定位（Docker compose 已启用 pg_stat_statements）

```sql
SELECT query, calls, mean_exec_time, total_exec_time
FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;
```

## 6. 重置与清理

`make db-reset` 按 `.env` 的 `STOCK_DB_PORT`（环境变量优先）自动选路径：

| 路径 | 触发条件 | 行为 |
|---|---|---|
| Docker | `STOCK_DB_PORT=5433`（默认） | `compose down -v` 销毁数据卷 → 重新 up → 重跑迁移（彻底重建） |
| 本机 PG | `STOCK_DB_PORT=5432`（或其它非 5433） | `stock_api.cli db-truncate`：TRUNCATE 全部数据表（保留表结构与 `platform.schema_version`）→ 重跑迁移 |

```bash
make db-reset        # 双模式：Docker 5433 销毁重建；本机 5432 清空数据+迁移
make migrate         # 仅补跑迁移
```

**自动化测试的清库守卫**（防误清本机开发库）：`backend/tests/conftest.py` 的
`owner_engine` 夹具在创建前校验 owner DSN——只许指向 Docker 开发库
（`127.0.0.1:5433`），指向本机 5432 时整个测试会话直接拒绝（exit 2）。
因此本机 PG 开发时请这样跑测试：

```bash
make db-up                               # 起 Docker 开发库（5433）
STOCK_DB_PORT=5433 make test-p2          # 显式指向 Docker 库跑测试（不动 .env）
```

本机 5432 的清库只有 `make db-reset` 这个人工入口；`db-truncate` 的表清单实时查
`information_schema`（platform/ledger/review/market 四 schema 全表），
不会随迁移漂移漏清新表，且只接受 localhost 目标（远端 DSN 一律拒绝）。

清库后重新造数：`make seed-tenants`（A/B/admin）或 `make seed-demo`（含行情回放）。

## 7. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 连接被拒 / `connection refused` | DB 未起：`make db-up`（Docker）或确认本机 PG 服务在跑、端口与 `.env` 一致 |
| `password authentication failed` | 口令与 `.env` 不符；确认是按 §1 两种方式之一初始化（脚本幂等可重跑） |
| 启动报 schema_version 不匹配 | 迁移未跑到 head：`make migrate` |
| 应用查不到刚插入的数据 | 多半用 `stock_app` 连接但没设 `app.user_id`（RLS 默认拒绝）——运行时请走 API，直连排查用 §5 的租户会话写法 |
| `permission denied for schema ledger` | 用 `stock_owner` 跑迁移（`make migrate`）；`stock_app` 的 DML 授权由迁移 `GRANT` 完成，若手工删过授权需重跑迁移 |
| RLS 好像"失效"了 | 检查是否用 owner/超级用户连接（owner 不受 RLS 约束）；验证请用 `stock_app` |
| `new row violates row-level security policy` | 写入会话没带租户上下文：API 一律 `db.session(user_id=..., begin=True)`，auth 注册的 `user_setting` 在 `set_tenant(user.id)` 之后写；worker handler 用 `tenant_tx(user_id)` |
| `/jobs` 列表为空但 `platform.job` 有行 | 用 app 角色直连时没设 `app.user_id`（G7 起 job 受 RLS）；API 端点已强制租户会话，直连排查用 §5 写法 |
| 新增 review/platform 表后 CI 报 RLS 缺失 | 迁移里补 `ENABLE ROW LEVEL SECURITY` + `tenant_isolation` 策略，再跑 `scripts/ci/gate_rls_coverage.py` |
| 迁移卡住 / 状态异常 | `alembic current` 看版本；必要时 `alembic downgrade` 回退一版，或 `make db-reset` 重建 |

## 8. 备份提示（本地）

```bash
# Docker
docker exec sa-dev-db pg_dump -U stock_owner -d stock_analyze > backup.sql
# 恢复
cat backup.sql | docker exec -i sa-dev-db psql -U stock_owner -d stock_analyze
```

生产 PITR 与 `stock_backup` 只读角色的演练安排在交付阶段，见 [Reliability](architecture/v2/Reliability.md)。
