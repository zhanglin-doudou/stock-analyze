# 资产表（每日资产快照）实现与验收日志

> 日期：2026-10-25 ｜ 分支：`feature/period-2` ｜ 设计：[2026-10-25-asset-daily-table.md](../architecture/2026-10-25-asset-daily-table.md) ｜ 审核：[2026-10-25-DevDesign-Review-asset-daily-table.md](2026-10-25-DevDesign-Review-asset-daily-table.md)
> 结论：**实现完成，门禁/测试全绿**（详见 §4/§5）；本文记录交付范围、关键实现决策、验证证据与已知限制。

---

## 1. 交付范围

用户需求：新增每日资产表（展示资产变化、供后续回撤指标），Web 列表 + 「构建/更新资产表」按钮，非首次点击增量更新。

已交付：

| 层 | 内容 |
|---|---|
| DB | 迁移 `0006_review_asset_daily`：`review.asset_daily`（每日快照）+ `review.asset_build_state`（水位线/构建摘要）+ RLS/GRANT + `ledger.trade` 增量索引；schema_version 0006 |
| 计算 | `stock_review/application/valuation.py`（nav/资产表共享估值原语）+ `application/assets.py`（全量/增量/列表/worker 编排）+ `repositories/assets.py`（稳定序、指纹、CAS、游标分页） |
| API | `GET /assets`（API-081 `listAssets`，纯读列表）+ `POST /assets/build`（API-082 `buildAssets`，202 异步 job、幂等、单飞） |
| Worker | 行情端口懒装配；`JobHandler` 统一 5 参；`build_assets` handler；`JobType.BUILD_ASSETS` |
| Web | `/assets` 页面：列表 11 列、构建/更新按钮、JobProgress + 轮询兜底、接管在跑 job、增量/no-op 提示、状态横幅、分页、错误态、零口径 |
| 契约/文档 | API-Catalog（82 端点/32 详细契约/16 Schema/82 验收）、DEV-PLAN Period2-16、DATABASE、README/Architecture 计数、openapi 快照、前端 SDK |

## 2. 关键实现决策（与设计一致）

1. **每日自然日一行**，`asset_daily` 为全历史估值事实表；`nav_daily` 保持不动。
2. **增量**：变更指纹 `(COUNT(*), SUM(version), MAX(updated_at))`（含软删）+ 1h 重叠窗 + 45 天安全窗；纯新增导入（version=1）增量，修正/软删/恢复（version>1）全量；`start` 双向变化、`end` 回退、`definition_version`/`last_fifo_error` 变化、回填早于首个余额行、窗口前一日缺行 → 全量。
3. **no-op**：数据/水位线/指纹不动，仅写 `last_mode=noop`、`last_built_at`、`last_rows_written=0`、`last_rebuild_from=NULL`，前端可显示「已是最新」。
4. **口径**：现金余额覆盖优先、含费成本、逐标的 floor 市值、逐日 `prev_total`、Decimal ROUND_HALF_UP 日收益率；blocked 行 DB 数值 0、API 显式 `null`；全局 FIFO `data_error` → 全表 degraded（`last_fifo_error` 变化触发全量，保证增量=全量）。
5. **API**：`GET /assets` 纯读、不触发构建；`POST /assets/build` 202 + Location/Retry-After，幂等键 + 规范化 request_hash，单飞 `scope_key={user}:assets`、`params={"mode":真实值}`；构建摘要经 `build` 块暴露（不扩 `JobOut`）。
6. **worker**：`WorkerRunner` 懒装配 `create_market_source(settings, session_factory=self.db.session, clock=self.clock)`；`JobHandler(queue, session, job, clock, market_data)`；`WorkerRunner(settings, clock=...)` 可选参数用于测试同钟（默认 SystemClock，向后兼容）。
7. **测试**：软删/恢复/撤销造数走 API/ORM（避免 raw SQL 不刷 `updated_at`）；DB 测试使用隔离库 `stock_analyze_asset_test` 串行执行，避免与本地 `make dev` 旧 worker 争用。

## 3. 主要文件

**新增**：`backend/migrations/versions/0006_review_asset_daily.py`、`stock_review/application/{valuation.py,assets.py}`、`stock_review/repositories/assets.py`、`stock_api/routers/assets.py`、`tests/api/test_api_081_assets.py`、`test_api_082_assets_build.py`、`tests/integration/test_assets_incremental.py`、`tests/unit/review/{test_valuation.py,test_assets_application.py}`、`frontend/apps/web/src/pages/{Assets.tsx,Assets.test.tsx}`、`router.test.tsx`、本文档。
**修改**：review models、nav/bridge（原语复用，行为不变）、ledger Trade 索引、version.py、API schemas/app/openapi_meta、worker runner/handlers、contracts enums、conftest/RLS/isolation/contract/worker 测试、API-Catalog/DEV-PLAN/DATABASE/README/Architecture、gate_endpoint_coverage、openapi.json、前端 api-client/router。

## 4. 验证结果（全部通过）

### 4.1 后端测试（隔离库 `STOCK_DB_NAME=stock_analyze_asset_test`，串行）

| 套件 | 结果 |
|---|---|
| `pytest backend/tests/unit` | **447 passed** |
| `pytest backend/tests/integration -m "not live"` | **58 passed**（含 19 条资产增量集成；2 live 跳过） |
| `pytest backend/tests/api` | **177 passed**（含 API-081 10、API-082 9） |
| `pytest backend/tests/isolation` | **71 passed** |
| `pytest backend/tests/contract` | **32 passed** |
| 合计 | **785 passed** |

### 4.2 契约/门禁/迁移

| 项 | 结果 |
|---|---|
| `python -m stock_api.cli api-check` | ✅ 39 path / 45 operation；文档 82 API-ID；违规 0 |
| `python -m stock_api.cli api-snapshot --check` | ✅ 无漂移 |
| `scripts/ci/gate_endpoint_coverage.py` | ✅ 一期 30/30；API-Catalog 82 |
| `scripts/ci/gate_tenant_coverage.py` | ✅ 受保护 36/36；跨租户资源 17/17；401 36/36；列表隔离 4/4 |
| `scripts/ci/gate_rls_coverage.py` | ✅ 17/17（review 6/6） |
| `alembic upgrade head` / `current` | ✅ 0006；`alembic check` 无漂移；downgrade/upgrade 往返已验证 |
| 迁移 schema_version | `0006_review_asset_daily` |

### 4.3 静态检查

| 项 | 结果 |
|---|---|
| `ruff check backend scripts` | ✅ All checks passed |
| `ruff format --check` | ✅ 159 files formatted |
| `mypy backend/packages backend/services` | ✅ 95 files, no issues |
| `lint-imports` | ✅ 5 kept / 0 broken |
| 前端 `pnpm -r test` | ✅ web 44 + api-client 15 |
| 前端 `pnpm -r typecheck` / `lint` | ✅ exit 0 |
| 前端 `pnpm --filter web build` | ✅ built in 593ms |

## 5. 设计偏差 / 实现补充

1. **missing 可空性修正**：`state 存在但 last_built_through IS NULL`（无流水/全软删）时，`build` 块按设计返回 `state="missing"` 且 `last_* / assumption / status_reason / data_as_of` 全 null；新增集成测试覆盖。
2. **WorkerRunner 可选 `clock` 参数**：仅测试用（默认 SystemClock），不改变生产行为。
3. **测试时钟桥接**：API 测试的 `_drain_worker` 会把 pending job 的 `available_at` 拨到过去（job 的 available_at 由 DB 真实时钟生成，测试用 FrozenClock 否则永不 claim）；仅测试数据，不改生产代码。
4. 设计 §6.2 中「回填早于首个余额行」使 `full=True` 后必须重置 `rebuild_from=start` 并全量拉行情；实现按 §6.4 语义执行，已在集成测试覆盖。
5. `docs/DATABASE.md` 回填了 0004 `nav_daily` 与 0006 资产表，并把 0005 排在 0006 之前。

## 6. 已知限制 / 后续

| 项 | 说明 |
|---|---|
| 本地 dev worker | 主库 `stock_analyze` 上可能仍挂着**旧代码** `make dev` worker；拉取本次代码后需重启 worker 才会注册 `build_assets` handler（否则 job 会 `UNSUPPORTED_JOB_KIND`）。API `--reload` 会自动加载新代码。 |
| 主库迁移 | 本地主库需执行 `make migrate` 到 0006（本次验证在隔离库执行；backend-core 也已在主库执行过 0006）。 |
| 行情历史修正 | 无 as_of 变化的历史 bar 修正不自动吸收，需 `mode=full`（D9 已人工确认）。 |
| 性能 | 未做 10 年/5000 笔基准；个人数据量级按设计目标（§9.7）后续记录。 |
| live 行情 | 测试使用 replay 夹具；live 模式未联网验证（需要 akshare extra）。 |
| 回撤指标 | 本表作为数据源，MDD/持续期/恢复期等留待后续 Period2-07。 |

## 7. 提交

- 分支：`feature/period-2`
- 本次提交内容：设计/审核/实现日志 + 全部代码/测试/契约/文档；未包含 `var/` 内部实现契约与本地测试库。
- 未执行 `git push`（等待人工确认）。
