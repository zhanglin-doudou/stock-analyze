# Period2-01 / Period2-02 实施与验收日志

> 日期：2026-10-22 ｜ 范围：[DEV-PLAN-v2.md](../DEV-PLAN-v2.md) §3.2 的 Period2-01（FIFO 回合引擎）与 Period2-02（回合构建与数据落库）
> 依据：[Period2-Entry-Checklist.md](Period2-Entry-Checklist.md)、[API-Catalog.md](../architecture/v2/API-Catalog.md)、[PRD-v1.md](../prd/PRD-v1.md) FR-R2 / 附录 A7–A9、[Testing.md](../architecture/v2/Testing.md) §9

## 0. 结论

| 任务 | 状态 | 验收判据 |
|---|---|---|
| Period2-01 `core/fifo` 纯引擎 + 独立 oracle | ✅ 完成 | 28 条边界场景与 oracle 对拍 `|pnl 差| ≤ 1 分`；hypothesis 数量/成本/股息守恒；`stock_core` 覆盖率 100%（161 passed） |
| Period2-02 回合落库 / rebuild-rounds / 规则 CRUD / API-016、022~025、027~031 | ✅ 完成 | 后端全量 `pytest` 590 passed；API-016/022~025/027~031 验收用例 32 条；受保护端点 33/33、跨租户资源 17/17、RLS 14/14（review 3/3） |

**未进入下一步的前置已满足**：Period2-03（NAV）依赖的 Period2-02 回合表、现金流分类（dividend/interest/fee/transfer）与 FIFO open lots 已就绪；Period2-04 行情可直接叠加到 open lots 市值。

## 1. 交付物

### 1.1 Period2-01：`stock_core.fifo`（纯计算内核，零 I/O / 零 wall-clock）

| 文件 | 内容 |
|---|---|
| `backend/packages/core/src/stock_core/fifo/types.py` | `EventKind` / `FifoEvent`（稳定序 `stable_key`）/ `Fill` / `Round` / `OpenLot` / `DataError` / `FifoResult`（含 `closed_rounds` / `open_rounds` / `realized_pnl_units` / `trading_pnl_units` / `dividend_units` 聚合） |
| `.../fifo/engine.py` | `build_rounds(events)`：稳定序排序、按证券分组、FIFO 配对、分批减仓、股息除息日按持股数归属、送转数量放大成本不变、配股缴款入成本且不新增回合、超卖 `data_error`（绝不产生负持仓）、open 回合、整数分摊尾差归最后 chunk |
| `.../fifo/dividend.py` | `DIVIDEND_TAX_TIERS` + `dividend_tax_rate_bp/units/net`：≤30 日 20%、31~365 日 10%、>365 日 0%，边界 30/31/365/366 精确 |
| `.../fifo/calendar.py` | `business_days_between(start, end)`：`(start, end]` 交易日（周一至周五），同日记 1 |
| `.../fifo/bridge.py` | `BridgeLedger` / `BridgeIdentity` / `compute_bridge`：桥接恒等式 + 确定性 `inputs_hash`（sha256 规范化 JSON） |

### 1.2 Period2-02：review schema 与落库链路

| 文件 | 内容 |
|---|---|
| `backend/packages/review/src/stock_review/models.py` | `review.round`（确定性 `rnd_` 主键、pnl/return_rate/holding_days、`data_error`/`oversell_units`、`fills_count`）、`review.round_fill`（逐 chunk 配对明细）、`review.round_build_state`（rebuild 水位线）；三表全部带 `user_id` |
| `backend/migrations/versions/0003_review_rounds.py` | 建表 + `tenant_isolation` RLS（USING / WITH CHECK 同谓词，未设 `app.user_id` fail-closed）+ `stock_app` DML / `stock_backup` SELECT 授权 + `schema_version='0003_period2_review'`；downgrade 已验证往返 |
| `.../stock_review/repositories/rounds.py` | 游标分页（`close_date DESC NULLS LAST, id DESC`）、按证券删除、批量插入、计数；损坏游标 → `INVALID_CURSOR` |
| `.../stock_review/application/rebuild.py` | `trades_to_events` 纯映射；`rebuild_rounds_for_user`（full / period 脏窗口 + `LOOKBACK_DAYS=400` 安全回看 + 区间起点持仓证券 + 软删重算）；确定性 ID；`rebuild_rounds_job`（进度/取消/收口） |
| `.../stock_review/application/rounds.py` | `RoundDTO` / `FillDTO`、列表/详情/配对明细（不存在 → `NOT_FOUND`） |
| `.../stock_review/application/bridge.py` | `bridge_for_period`：现金锚点（期初前最后余额 / 首行 `back_solved`）、区间分类现金流、FIFO 交易损益与 open cost、`compute_bridge` |
| `backend/services/worker/src/stock_worker/handlers.py` | 注册 `rebuild_rounds`；`import_commit` / `reclassify` 成功后自动入队 full rebuild（`scope_key=f"{user_id}:full"`，`ApiError` 只记日志，不影响主任务） |
| `backend/services/api/src/stock_api/routers/rules.py` | API-022~025 规则 CRUD（系统规则 + 本人规则；PATCH `If-Match` 乐观锁；DELETE 后系统规则自然回退） |
| `.../routers/rounds.py` | API-028~031（rebuild 202 + Location/Retry-After/`X-Job-Reused`；游标列表/详情/fills；admin 403、跨租户 404） |
| `.../routers/imports.py` | API-016 reclassify（202；Idempotency-Key 必填；批次级过滤）；API-015 撤销后同事务入队 full rebuild |
| `.../routers/reconciliation.py` | API-027 `GET /reconciliation/bridge`（period 必填；`as_of` 收窄期末；断链 → balanced=false） |
| `.../routers/app.py` | 注册 `rules` / `rounds` router |

### 1.3 测试与夹具

- `backend/tests/oracle/naive_fifo.py`：**独立朴素实现**（Decimal、零 import 本仓包；测试内 AST 守卫），作第三方对拍基准。
- `backend/tests/unit/core/test_fifo_*.py`：28 条边界场景 oracle 对拍 + 6 条手算 golden（期望值写死）+ hypothesis 属性测试（数量/成本/股息守恒、无负持仓、pnl 恒等式、输入顺序无关）+ 单元覆盖。
- `backend/tests/unit/review/`：`trades_to_events` 映射（送转/配股/补差行/未知/空）与确定性 round id。
- `backend/tests/integration/test_rounds_rebuild.py`（12 条）：full 手算 pnl、幂等、空/撤销清空、period 脏窗口隔离、窗口外 `updated_at`、软删重算、分页/详情/fills、bridge 闭合/degraded/blocked/other、worker handler 端到端。
- `backend/tests/integration/test_rls_isolation.py`：review 三表租户隔离 / 未设租户 0 行 / 跨租户写被 WITH CHECK 拒绝。
- `backend/tests/api/test_api_016_reclassify.py`、`test_api_022_025_summary_rules.py`、`test_api_027_bridge.py`、`test_api_028_031_rounds.py`（32 条，每 API-ID ≥3 条：happy / 越权 / 关键错误码）。
- `backend/tests/isolation/test_isolation_matrix.py`：新增 22 个参数化越权条目（资源/列表/401/admin）。

## 2. 关键口径与设计决定

1. **回合定义（与 Testing §9.2 #2 对齐）**：每个 `SELL` 事件生成一个 closed 回合，消耗 1..N 个 FIFO 买入 lot，形成多个 `round_fill`；「一次买入 + 三次分批减仓」= 3 个回合。期末仍未卖出的 lot 生成 `status="open"` 回合。
2. **回合 pnl（PRD A8）**：`pnl = Σ卖出净现金 − Σ买入净成本 + 税后股息净额`；收益率 `= pnl / Σ买入成本（含费）`。ledger 的 `amount_units` 已是**带符号净现金流**（买入为负、卖出/股息/利息为正），回合内不再二次扣费。
3. **稳定序**：`(trade_date, line_no, summary, quantity_units, price_units, trade_id)`，同日多笔按文件行号破平局。
4. **股息归属**：除息日按当时各 open lot 持股数量比例分摊；分红不改 lot 成本；被卖出时按数量比例带入对应回合。红利税档位以独立 `category=fee` 行进入 bridge 的 `fees`，`dividend_tax_units` 不重复扣税。
5. **送转**：`category=other` + 摘要含「送股/转增/送转」+ 正数量 + 零金额 → `BONUS`；按持股比例放大数量、总成本不变。为此补充 3 条默认系统规则（`sys_bonus_stock` / `sys_bonus_cap` / `sys_bonus_split`，priority 45）。
6. **配股**：`category=rights` → `RIGHTS`；按持股比例增加数量并把缴款计入 lot 成本，**不新增回合**。
7. **超卖**：只配对可用数量，剩量记 `DataError(kind="oversell")` 且回合 `status="data_error"` / `oversell_units`；不产生负持仓，卖出现金只按可配对数量分摊（不虚增 pnl）。
8. **桥接恒等式（API-027）**：
   ```
   residual = initial_cash + initial_open_cost + cashflows + realized_pnl
              + dividends + interest - fees + other - ending_value
   ending_value = 期末现金 + open_cost（P2-02 按成本计量；P2-03 换市值后同式成立）
   ```
   `realized_pnl` 为区间内平仓回合的**不含股息**交易损益（股息单列，避免二次计入）；`other` 为打新/补差/未知现金流，非零时 `status=degraded`；无现金锚点 → `status=blocked`。
9. **rebuild 幂等与脏窗口**：回合 ID = `rnd_` + `sha256(user_id|security_code|key)[:32]`，重复 rebuild ID/数值不变；period 模式先确定脏证券（区间 trade + 上次 rebuild 后 `updated_at` 变化 + 区间起点持仓 + 400 天安全回看），再按这些证券**全历史**重算（FIFO 路径依赖），只删这些证券的旧回合。
10. **自动重建**：导入提交 / 分类复算 / 撤销成功后自动入队 full rebuild；自动路径与 API-028 full 使用同一单飞 key `f"{user_id}:full"`，避免并发 delete/insert 互踩。
11. **契约与元数据**：新增 10 个 operation 全部登记 `OPERATION_META`（scope/idempotent/async/error_codes），`make api-check` 违规 0；快照与前端 SDK 同批更新。
12. **schema 版本**：迁移 0003 同步 `platform.schema_version='0003_period2_review'` 与 `stock_api/version.py::SCHEMA_VERSION`，启动校验成对。

## 3. 端点清单（本期新增/补齐）

| API | Method / Path | operationId | 说明 |
|---|---|---|---|
| API-016 | POST `/imports/{batch_id}/reclassify` | `reclassifyImport` | 202 + job；批次级重跑分类；Idempotency-Key 必填 |
| API-022 | GET `/summary-rules` | `listSummaryRules` | 系统默认 + 本人规则（含 disabled） |
| API-023 | POST `/summary-rules` | `createSummaryRule` | 201；服务端生成 `usr_`+8hex |
| API-024 | PATCH `/summary-rules/{rule_id}` | `updateSummaryRule` | If-Match 乐观锁；旧版本 409 |
| API-025 | DELETE `/summary-rules/{rule_id}` | `deleteSummaryRule` | 204；系统规则自然回退 |
| API-027 | GET `/reconciliation/bridge` | `getReconciliationBridge` | 桥接恒等式 balanced/residual/inputs_hash |
| API-028 | POST `/jobs/rebuild-rounds` | `rebuildRounds` | 202 + Location；同参复用、异参 409 |
| API-029 | GET `/rounds` | `listRounds` | 游标分页 / security_code / status / from-to |
| API-030 | GET `/rounds/{round_id}` | `getRound` | 回合详情（pnl/return_rate/holding_days） |
| API-031 | GET `/rounds/{round_id}/fills` | `listRoundFills` | FIFO 配对明细（buy/sell trade_id 集合可追溯） |

## 4. 验证证据

在独占测试库 `stock_analyze_p2lead`（迁移 head = 0003）上执行：

```bash
# 后端全量
STOCK_DB_NAME=stock_analyze_p2lead .venv/bin/python -m pytest -q
# → 590 passed

# Period2-01 覆盖率门禁（100%）
.venv/bin/python -m pytest backend/tests/unit/core --cov=stock_core --cov-fail-under=100 -q
# → 161 passed；stock_core TOTAL 468 stmts / 0 miss / 100%

# Period2-02 数据/集成
STOCK_DB_NAME=stock_analyze_p2lead .venv/bin/python -m pytest backend/tests/unit/review backend/tests/integration -q
# → unit/review 13 passed；integration 39 passed（含 test_rounds_rebuild.py 12 passed）

# API / 越权
STOCK_DB_NAME=stock_analyze_p2lead .venv/bin/python -m pytest backend/tests/api backend/tests/isolation -q
# → api 146 passed；isolation 64 passed

# 静态与门禁
.venv/bin/ruff check backend                      # All checks passed
.venv/bin/ruff format --check backend             # 131 files already formatted
.venv/bin/mypy backend/packages backend/services  # Success: 86 source files
.venv/bin/lint-imports                            # Contracts: 5 kept, 0 broken
.venv/bin/python scripts/ci/gate_endpoint_coverage.py  # 30/30
.venv/bin/python scripts/ci/gate_tenant_coverage.py    # 受保护 33/33；资源 17/17；401 33/33；列表 4/4
STOCK_DB_NAME=stock_analyze_p2lead \
  .venv/bin/python scripts/ci/gate_rls_coverage.py     # platform 4/4、ledger 7/7、review 3/3（14/14）
.venv/bin/python -m stock_api.cli api-check             # ✅ 42 operations，契约违规 0
.venv/bin/python -m stock_api.cli api-snapshot --check  # ✅ 快照无漂移
STOCK_DB_NAME=stock_analyze_p2lead .venv/bin/alembic -c backend/alembic.ini check  # No new upgrade operations

# 一期 18 步总门禁（在迁移到 0003 的独占库上复跑；含前端 tsc/vitest/build）
UV_CACHE_DIR=$PWD/.uvcache STOCK_DB_NAME=stock_analyze_p2lead make verify-p1
# → ✅ verify-p1 全绿（18/18），其中 RLS 门禁为 platform 4/4、ledger 7/7、review 3/3
```

关键断言摘录：

- API-031：`Σ fills.pnl == round.pnl`、`Σ fills.quantity == round.quantity`，FIFO `buy_trade_id/sell_trade_id` 集合与 hand-computed 期望 100% 一致。
- API-030：手算场景 `buy1 100@100.0 → sell 60@120.0` → `pnl=1200.00`，接口值与手算差 ≤0.01。
- API-028：同参 3 次返回同一 `job_id`（第 2/3 次 `X-Job-Reused: true`）；异参 409 `JOB_ALREADY_RUNNING`；与自动 full rebuild 同 scope 时 409，不并发踩写。
- API-027：现金链闭合 fixture `balanced=true`、`|residual|≤0.01`；cash_balance 断链 fixture `balanced=false`、`residual≠0`；重复调用 `inputs_hash` 相同。
- 迁移 0003 `downgrade 0002 → upgrade head` 往返成功；`platform.schema_version=0003_period2_review`。

## 5. 文档/契约同步

- `docs/architecture/v2/API-Catalog.md`：补 API-016 / 022~025 / 029~031 明细章节；扩充 API-027 identity 字段与恒等式；API-028 异参单飞口径改为 409 `JOB_ALREADY_RUNNING`（G2 语义）；§6 验收矩阵同步。
- `docs/DATABASE.md`：review schema 三表、RLS 覆盖（review 3/3）、0003 迁移、TRUNCATE 清单。
- `docs/reviews/Period2-Entry-Checklist.md`：状态、排期与变更记录。
- `contracts/openapi.json` 重新生成（42 operations）；`frontend/packages/api-client/src/api/generated/schema.d.ts` 由 `make sdk` 同步。
- `backend/packages/review/pyproject.toml` 补 `stock-ledger` 依赖并同步 `uv.lock`（review 应用层读取 ledger 明细）。

## 6. 已知限制 / 后续任务

| 限制 | 说明 | 归属 |
|---|---|---|
| 交易日历仅按周一至周五 | 未接入 A 股节假日/停牌日历；`holding_days` 为近似交易日 | Period2-04/05 行情日历接入后可替换 |
| 打新（new_stock）不参与回合 | 缴款/退款/中签入账作为非回合现金流，bridge 计入 `other` 并 `degraded` | Period2-03 净值口径处理 |
| bridge 期末资产按成本计量 | `ending_value = 期末现金 + open_cost`；P2-03 换成市值后恒等式形式不变 | Period2-03 |
| 红利税档位 helper 未接入流水 | 当前红利税取账单独立 fee 行；`dividend_tax_units` 供后续合成/校验 | Period2-03/07 |
| API-024 重复 DELETE 自有规则 → 404 | 与跨账号/系统规则 404 不可区分（不泄露存在性）；未做幂等 204 | 可接受 |
| API-027 `as_of` 语义 | 冻结签名无 `as_of`，实现为必须落在 period 内并收窄 `period_end` | P2-03 扩展 |
| 送转依赖摘要关键词 | 需 `category=other` + 摘要含 送股/转增/送转 + 零金额；真实账单若用其它措辞需补规则 | 规则可配置 |

## 7. 复跑指引

```bash
# 1) 迁移（schema_version 必须与代码成对）
.venv/bin/alembic -c backend/alembic.ini upgrade head

# 2) 静态
.venv/bin/ruff check backend && .venv/bin/ruff format --check backend
.venv/bin/mypy backend/packages backend/services && .venv/bin/lint-imports

# 3) 后端测试（独占库，避免并发 TRUNCATE 互相踩）
STOCK_DB_NAME=stock_analyze_p2lead .venv/bin/python -m pytest -q
# 或只跑 Period2-01/02 的套餐（Makefile 新增）：
#   STOCK_DB_NAME=stock_analyze_p2lead make test-p2
#   STOCK_DB_NAME=stock_analyze_p2lead make test-review

# 4) 门禁
.venv/bin/python scripts/ci/gate_endpoint_coverage.py
.venv/bin/python scripts/ci/gate_tenant_coverage.py
STOCK_DB_NAME=stock_analyze_p2lead .venv/bin/python scripts/ci/gate_rls_coverage.py
.venv/bin/python -m stock_api.cli api-check
.venv/bin/python -m stock_api.cli api-snapshot --check

# 5) 契约 SDK（改端点后）
.venv/bin/python -m stock_api.cli api-snapshot && .venv/bin/python -m stock_api.cli sdk
```
