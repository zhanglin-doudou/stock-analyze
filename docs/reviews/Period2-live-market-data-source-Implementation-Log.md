# 实施记录：MARKET_MODE=live（akshare 真实行情源）

| 项 | 内容 |
|---|---|
| 设计依据 | [2026-10-22-live-market-data-source.md](../architecture/2026-10-22-live-market-data-source.md)（v2，含 D1/D2/D3/D4 人工裁定） |
| 评审依据 | [2026-10-23-DevDesign-Review-live-market-data-source.md](2026-10-23-DevDesign-Review-live-market-data-source.md) |
| 完成日期 | 2026-10-23 |
| 结果 | 代码 + 测试 + 迁移 + 契约快照 + 文档全部落地；本地门禁见 §4（2 项**既有**问题与本变更无关，见 §5） |

---

## 1. 交付内容

### 1.1 平台层（新增）

| 文件 | 内容 |
|---|---|
| `backend/packages/platform/src/stock_platform/market_models.py` | `DailyBar` / `PullRange`（`market` schema，无 RLS/无 `user_id`，列与 0005 逐列一致） |
| `backend/packages/platform/src/stock_platform/market_live.py` | `AkshareMarketDataSource`（白名单/真超时/重试/NaN 校验/定点转换/空结果语义）、`CachedMarketDataSource`（区间并集 + 尾部刷新 + TTL + 负缓存 + `ON CONFLICT`）、`validate_symbols`、`plan_fetch`、`SessionFactory` |
| `backend/migrations/versions/0005_market_cache.py` | `market` schema + 两表 + 索引 + **GRANT** + `schema_version='0005_market_cache'`；downgrade 回 `0004_period2_nav` |

### 1.2 装配与接入

| 文件 | 改动 |
|---|---|
| `backend/packages/ledger/src/stock_ledger/market.py` | `create_market_source(settings, *, session_factory, clock)`：replay / live+cache / live 透传 / akshare 缺失启动期 fail-fast |
| `backend/services/api/src/stock_api/app.py` | `build_state` 改为走工厂（此前直接 new replay，live 分支永远不会生效） |
| `backend/packages/platform/src/stock_platform/config.py` | 10 个 `market_live_*` 字段（`MARKET_LIVE_ADJUST` 为 `Literal[""]`） |
| `backend/services/api/src/stock_api/version.py` | `SCHEMA_VERSION = "0005_market_cache"` |
| `backend/migrations/env.py` | `OWNED_SCHEMAS` 加 `market` + import `market_models` |

### 1.3 NAV 可见性（D2=A：不新增错误码）

| 文件 | 改动 |
|---|---|
| `backend/packages/review/src/stock_review/application/nav.py` | `_load_prices` 返回 `(ladder, causes)`；`blocked_reason` 保留 `缺行情数据：<symbols>` 前缀并追加三类后缀（不可用可重试 / 不在支持范围 / 无行情） |
| `backend/services/api/src/stock_api/schemas.py` | `NavPointOut.blocked_reason: str \| None`（新增可选响应字段，非破坏） |
| `backend/services/api/src/stock_api/routers/nav.py` | 从 `nav_daily` 行填充该字段 |
| `contracts/openapi.json` + `frontend/.../schema.d.ts` | 重新生成（新增可选字段） |

### 1.4 指标/留痕

- `backend/packages/platform/src/stock_platform/obs.py` 新增 `sa_market_fetch_total` / `sa_market_fetch_seconds` / `sa_market_fetch_errors_total` / `sa_market_cache_hits_total`（对应 Reliability FM-17）。
- `market.pull_range` 落 `source/pulled_at/data_end/checksum/bar_count/complete`（NFR-2 最小留痕）。
- 结构化日志：`market.fetch`（symbol/range/bars/latency/attempt/result）、`market.cache`（hit）、`market.store`（covered_to/bars）。

### 1.5 测试

| 文件 | 覆盖 |
|---|---|
| `backend/tests/unit/platform/test_market_live.py`（新增，32 例） | 行解析/定点转换/NaN/负价/重复日期、标的白名单、`timeout=` 透传、瞬时重试与耗尽映射、空结果、`as_of`/`capabilities`/`provenance`/`get_latest_quotes` |
| `backend/tests/unit/platform/test_market_cache.py`（新增，13 例） | `plan_fetch` 纯函数（含尾部恒为缺口）、miss→hit、连续月份只拉缺口、尾部刷新、TTL/负缓存、`ON CONFLICT`、并发双击、app 角色 GRANT |
| `backend/tests/contract/test_market_source.py`（新增，12 例） | `MarketDataSource` 端口契约参数化（replay / akshare mock） |
| `backend/tests/integration/test_market_live_e2e.py`（新增） | `@pytest.mark.live` 真实联网（默认门禁排除） |
| `backend/tests/unit/review/test_nav_application.py` | 新增 blocked_reason 三类后缀 + no_data 用例 |
| `backend/tests/api/test_api_032_nav.py` | 断言 `blocked_reason` 契约（blocked 有值 / 非 blocked 为 null） |
| `backend/tests/conftest.py` | TRUNCATE 加 `market.*`；settings 夹具强制 `market_mode="replay"`（防止 `.env` 改 live 污染套件） |
| `backend/tests/unit/ledger/test_market_replay.py` | live 工厂用例改写：akshare 缺失 fail-fast / 缓存需 session_factory / 关闭缓存纯 akshare |

### 1.6 配置与文档

- 根 `pyproject.toml`：`[project.optional-dependencies] live = ["stock-platform[live]"]`；pytest marker `live`；mypy override（akshare/pandas）。
- `backend/packages/platform/pyproject.toml`：`live = ["akshare>=1.16.72,<1.20", "pandas>=2.0,<3.1"]`；`uv.lock` 已重生成。
- `Makefile` / `stock_api/cli.py`：所有 pytest 门禁排除 `live`（含按路径收集 `backend/tests/integration` 的目标）。
- `infra/.env.example`、`docs/DATABASE.md`、`docs/Architecture-v2.md`、`docs/architecture/v2/README.md`、`ADR.md`（ADR-0025）同步。

### 1.7 顺带修复（为使 `make migrate-check` 通过）

`backend/packages/review/src/stock_review/models.py`：`NavDaily` 原先把唯一性声明为 `unique Index`，与迁移 0004 的 `UniqueConstraint + 普通索引`不一致，导致 `alembic check` 一直报漂移（历史债）。已改为与 DB 完全一致，无迁移变更。

---

## 2. 与设计文档的实现级差异（均已在设计文档回填）

1. `daily_bar` **不建额外索引**：主键 `(symbol, adjust, trade_date)` 已服务查询，删除设计里的冗余 `ix_daily_bar_symbol_date`。
2. 指标集中放 `obs.py`（与现有 `HTTP_*`/`JOBS_*` 风格一致），不改 `market_live.py` 顶层。
3. akshare **运行期** import 失败（启动期已用 `find_spec` 拦截）按瞬时故障处理 → 重试耗尽后 `INTERNAL_ERROR` + `unavailable`，避免裸 `RuntimeError` 穿透成无码 500。
4. `CachedMarketDataSource.inner` 类型放宽为 `MarketDataSource`（装饰器可复用任意实现，便于契约测试）。

---

## 3. 本地迁移

```bash
uv run alembic -c backend/alembic.ini upgrade head   # 0004 -> 0005
uv run alembic -c backend/alembic.ini current        # 0005 (head)
```

`market` schema 已建；`GRANT` 由迁移完成（`stock_app` DML / `stock_backup` SELECT）。

---

## 4. 验证结果（2026-10-23 本地）

| 检查 | 结果 |
|---|---|
| `ruff check backend` / `ruff format --check backend` | ✅ 全通过 |
| `mypy backend/packages backend/services`（strict） | ✅ 91 files, no issues |
| `lint-imports`（L1–L5） | ✅ 5 kept / 0 broken |
| `pytest -m "not integration and not api and not live"` | ✅ **429 passed, 1 skipped**（连续两次） |
| `pytest backend/tests/contract` | ✅ 32 passed |
| `pytest backend/tests/integration -m "not live"` | ✅ 39 passed, 1 skipped |
| `pytest backend/tests/api/test_api_032_nav.py` | ✅ 7 passed |
| `pytest backend/tests/architecture` | ✅ 1 passed |
| `alembic check` | ✅ No new upgrade operations detected |
| `api-snapshot --check` | ✅ 快照无漂移 |
| `api-check` | ✅ 通过（端点/operationId/错误码/示例/x-*） |
| 前端 `pnpm -r typecheck` / `vitest` / `build` | ✅ tsc 无错 / 23 tests / 构建成功 |
| `scripts/ci/gate_endpoint_coverage.py` | ✅ 30/30 |
| `scripts/ci/gate_rls_coverage.py` | ✅ 15/15（market 不在扫描范围，按设计） |
| `scripts/ci/gate_tenant_coverage.py` | ❌ 既有问题（GET /nav 缺隔离/401 用例），见 §5 |
| `uv sync --extra live --dry-run` | ✅ 依赖可解析（akshare 1.19.1 + pandas 等） |
| `make verify-p1`（一条命令） | ⚠️ 前 6 步通过（ruff/format/mypy/import-linter/单测/core 覆盖），第 7 步「API 验收」因 §5.2 既有 flaky 用例中止；后续步骤已逐条单独验证（见下表上方各行） |

**未执行**：真实联网 live e2e（需 `uv sync --extra live` + 网络；东财对服务器 IP 有限流风险）。执行方式：

```bash
uv sync --extra live
MARKET_LIVE_E2E=1 uv run pytest backend/tests/integration/test_market_live_e2e.py -q -m live
```

---

## 5. 已知问题（均为**既有**，与本变更无关；已用 `git stash` 在 HEAD 基线复现）

1. **`scripts/ci/gate_tenant_coverage.py` 在 `GET /nav` 上失败**：基线 HEAD 同样失败（`[✗] GET /nav scope=read via=MISSING`，缺越权/401 用例引用）。因此 `make verify-p1` 会在该步骤中止；补用例属独立任务。
2. **API/队列测试偶发 flaky**（既有，job claim/commit 时序）：`test_classification.py`、`test_imports_trades_jobs.py`、`test_jobs_semantics.py`、`test_api_028_031_rounds.py` 的 import/job 链路，以及 `unit/platform/test_worker_runner.py::test_unknown_exception_marks_failed_and_loop_continues` 偶发 1~2 例失败，重跑即过。**基线对照**（`git stash` 到 HEAD 后跑同一命令）：基线的 `pytest backend/tests/api` 为 5 failed / 149 passed，本变更为 2 failed / 150 passed，失败集合同属该 flaky 家族（基线多出的 2 例是 DB 已升 0005 而基线代码期望 0004 的 schema mismatch readyz 用例）。本变更未引入回归。
3. `GET /nav` 仍是请求内同步外网取数 + `data_as_of=clock.now()`，按设计登记为后续期次（NAV 异步化/`data_as_of` 语义）。

---

## 6. 回滚

1. 代码：还原本批次文件（或用 `git revert`）；
2. 迁移：`uv run alembic -c backend/alembic.ini downgrade 0004`（drop `market` 两表 + schema，版本回 `0004_period2_nav`）；
3. `.env` 保持/改回 `MARKET_MODE=replay` 即回到确定性回放，不依赖 akshare。
