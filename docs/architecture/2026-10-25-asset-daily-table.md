# 资产表（每日资产快照）设计与测试方案

> 状态：**v1.1 — 三方交叉审核 + 第二轮独立验证 + 人工批准已通过；实现与验收完成（2026-10-25），见 [Implementation Log](../reviews/2026-10-25-asset-daily-table-Implementation-Log.md)**
> 日期：2026-10-25 ｜ 作者：AI（Team Lead） ｜ 交叉审核记录：[2026-10-25-DevDesign-Review-asset-daily-table.md](../reviews/2026-10-25-DevDesign-Review-asset-daily-table.md)
> 需求来源：用户原始诉求——"新增一个资产表方便后续显示资产变化，以及后续计算回撤等指标；web 界面以列表形式展示，同时新增『构建/更新资产表』按钮，非首次点击则增量更新资产表。先设计表字段和测试用例并生成文档、交叉审核、人工审核后再执行。"

---

## 0. 决策摘要（人工审核请重点拍板）

| # | 决策点 | 推荐方案（v1.0 已按此设计） | 备选 | 影响 |
|---|---|---|---|---|
| D1 | 表粒度 | **每日总资产快照**：每个自然日一行 | 日期 × 证券明细行 | 列表/回撤只需总资产序列；明细留作后续 `asset_daily_holding` |
| D2 | 存储 | **新建 `review.asset_daily`**；`nav_daily` 不动 | 扩 nav_daily | nav_daily 按月稀疏、由 `GET /nav` 隐式写入，不适合全历史增量 |
| D3 | 与净值关系 | 抽共享估值原语；nav 行为不变；**同值回归测试** | 复制两套逻辑 | 避免口径漂移 |
| D4 | 构建方式 | `POST /assets/build` → **202 异步 job**；`GET /assets` 纯读 | 同步 200 | 异步需给 worker 装配行情端口（见 D11） |
| D5 | 增量策略 | 水位线 + **变更指纹** + 脏窗口 + 45 天安全回看；只重写窗口行 | 持久化逐日持仓做断点续算 | 内存仍全历史重放（个人数据量级；性能目标 §9.7） |
| D6 | blocked 语义 | 资产类硬阻断；blocked 行数值 API 为 **显式 `null`** | 输出 0 / 省略字段 | 遵循 Reliability 硬阻断；与 Catalog §0.8（MetricResult.value 缺省）不是同一语义，需在 API-081 §2 明写 |
| D7 | 数据截止日 | `end = clock.today()`；**行情 `as_of` 之后且有持仓的日期标 degraded**（按最近收盘估值） | end=as_of | 新流水不被吞；避免 as_of 回退导致水位线振荡 |
| D8 | 负市值 | **允许**：超卖/历史缺买入导致负持仓时，`market_value`/`total` 可为负，行标 `degraded` | 改判 blocked 或钳 0 | 与 nav 逐日推演口径一致；不建 `market_value>=0` CHECK |
| D9 | 行情修正 | 三档：① end 前进时的 45 天安全窗；② **as_of 变化（即使 end 未前进）也重算安全窗**，as_of 跨度 >45 天 → full；③ 无 as_of 变化的历史 bar 修正不自动吸收，需 `mode=full` | 建 `market.daily_bar.updated_at` 水位 | 不扩大跨 schema 耦合；风险见 §11 |
| D10 | 流水修正（version>1） | 脏集合含 `version>1`（改日期/软删/恢复）→ **全量重建**；纯新增导入（version=1）仍增量 | 用 trade_history 旧日期精确增量 | 修正/撤销量少，全量更安全；避免「日期改晚」漏算 |
| D11 | worker 行情装配 | WorkerRunner 懒装配 `MarketDataSource`，handler 签名加第 5 参 | 同步构建 / handler 内自建 | live 模式 worker 需 akshare 依赖；replay 无网络 |
| D12 | 强制全量入口 | API 保留 `mode=full`；UI 不暴露 | UI 加「强制重建」 | 行情 >45 天修正只能走 API/运维 |

> D1/D2/D4 为本设计主干；D8/D9/D10/D11/D12 是 v0.1 审核后新增决策，请人工逐项确认（§12）。

---

## 1. 背景与目标

### 1.1 现状
- `review.nav_daily`（迁移 `0004_review_nav`）：`GET /nav`（API-032）按 `YYYY-MM` **同步计算并 upsert** 净值/日收益/总资产/现金流/份额；行只出现在「有流水的日期 + 期末」（稀疏），一次只覆盖一个月。
- `review.round` / `round_build_state`（迁移 `0003_review_rounds`）：`POST /jobs/rebuild-rounds`（API-028）异步重建，脏窗口增量 + 400 天安全回看；worker handler 在 `backend/services/worker/src/stock_worker/handlers.py`。
- 行情端口 `MarketDataSource`（`as_of` / `get_daily_bars`）与 replay 夹具 `fixtures/market/quotes.json`（`as_of=2026-09-30`，**月频** bar）。
- 资产估值口径（现金 + 持仓 × 收盘、外部现金流、含费成本）已在 `stock_review/application/nav.py` 实现，但没有全历史、逐自然日、可增量维护的资产序列表。

### 1.2 目标
1. 新增全历史每日资产快照表，作为「资产变化」与后续回撤族（MDD/持续期/恢复期/水下占比/Ulcer/Calmar，PRD A18）的唯一数据源。
2. Web 新增「资产表」页：列表展示；「构建/更新资产表」按钮；首次全量、后续增量。
3. 增量**可验证**：增量结果与全量重建逐行相等（§9.4 IT-01）。
4. 口径/状态显式落库；前端零计算、零口径。

### 1.3 非目标
- 回撤指标本身、基准对比、归因、报告、导出。
- 每证券持仓明细落库（D1 备选）。
- 改写 `GET /nav` 的返回结构与计算口径（只抽共享原语 + 同值回归）。
- 修复 Catalog 与既有实现的全部历史债（WWW-Authenticate、游标 user 绑定、金额 2/3 位不一致等，见 §11）。

---

## 2. 关键约束

| 约束 | 要求 |
|---|---|
| 禁止 float | 金额/价格/数量/比率全用标度整数（`*_units`，`BigInteger`）；API 边界转定点字符串 |
| Domain 禁 wall-clock | 构建时间、业务日来自注入 `Clock`；worker handler 已持有 clock |
| 仓储显式 `user_id` | 新仓储方法全部带 `user_id` |
| 新表必须 RLS | 两表均 `ENABLE ROW LEVEL SECURITY` + `tenant_isolation`，过 `gate_rls_coverage.py` |
| 契约先行 | API-Catalog §1/§2/§4/§5/§6 → `make api-snapshot` → `make api-check` → router → API 测试 → `make sdk` |
| 错误用 `ApiError(ErrorCode.X)` | 不新增错误码，全部复用既有枚举 |
| 长任务 202 + Location | `POST /assets/build` → 202 + `Location: /api/v1/jobs/{id}` + `Retry-After: 1` |
| 派生重建留痕 | job 行即留痕；**不新增审计事件**（与 rebuild_rounds 一致，已核实现状） |
| 迁移版本 | bump `platform.schema_version` + `stock_api/version.py::SCHEMA_VERSION`；`make migrate-check` 无漂移 |
| 前端零口径 | 金额/比率 string；后端额外给 `daily_return_pct`；页面只展示/排序 |

---

## 3. 备选方案对比

| 方案 | 优点 | 致命问题 |
|---|---|---|
| A. 扩 `nav_daily` + 列表端点 | 表少 | 按月稀疏、`GET /nav` 隐式写、改 API-032 回归面大 |
| **B. 新建 `asset_daily`（采用）** | 全历史独立、纯读、可增量 | 与 nav 总资产列重复 → 共享原语 + IT-07 同值回归 |
| C. 不落库实时算 | 无迁移 | 回撤/趋势/报告需稳定历史；无法增量/留痕 |

**定位**：`asset_daily` = 每日估值事实表；`nav_daily` = 份额法 TWR 结果表。长期可让 nav 消费 asset_daily（非本期）。

---

## 4. 数据模型

### 4.1 `review.asset_daily`（每日资产快照）

一行 = 某用户某自然日收盘后状态。主键确定性：`ast_` + `sha256(user_id|trade_date)[:32]`。

| 列 | 类型 | 空 | 单位/口径 | 说明 |
|---|---|---|---|---|
| `id` | `String(40)` PK | 否 | — | `ast_<32hex>` |
| `user_id` | `UUID` FK→`platform.user.id` CASCADE | 否 | — | 租户键（RLS） |
| `trade_date` | `Date` | 否 | Asia/Shanghai 业务日 | 自然日，连续 |
| `cash_units` | `BigInteger` | 否 | 金额 scale=2 | 当日有余额行取**当日最后一笔**；否则前日现金 + Σ当日 `amount_units` |
| `market_value_units` | `BigInteger` | 否 | 金额 scale=2 | **逐标的 floor**：`Σ(qty_units * close_units // 1e8)`；超卖日可为负（D8） |
| `total_assets_units` | `BigInteger` | 否 | 金额 scale=2 | `cash + market`（DB CHECK） |
| `holding_cost_units` | `BigInteger` | 否 | 金额 scale=2 | 含费成本；buy/rights/new_stock 加 `-amount_units`，sell 按比例结转 |
| `unrealized_pnl_units` | `BigInteger` | 否 | 金额 scale=2 | `market_value - holding_cost`（可为负） |
| `holding_count` | `Integer` | 否 | 个 | 收盘后 `quantity_units != 0` 的标的数；≥0；**blocked 行强制 0**（硬阻断不输出持仓明细，即使语义上有持仓） |
| `cashflow_units` | `BigInteger` | 否 | 金额 scale=2 | `category=transfer` 的 `amount_units` 之和 |
| `prev_total_assets_units` | `BigInteger` | 是 | 金额 scale=2 | **严格上一自然日**的总资产；上一日为 blocked → NULL；首行取期初现金锚点 |
| `daily_pnl_units` | `BigInteger` | 是 | 金额 scale=2 | `total - cashflow - prev_total`；prev 为 NULL 时 NULL |
| `daily_return_units` | `BigInteger` | 是 | 比率 scale=8 | `daily_pnl / prev_total`，**Decimal + ROUND_HALF_UP**（与 core nav 一致）；仅 `prev_total>0`；否则 NULL |
| `status` | `String(16)` | 否 | 枚举 text | `ok` / `blocked` / `degraded` |
| `status_reason` | `String(256)` | 是 | 人话 | blocked 必填；degraded 可填（FIFO 异常 / 行情未覆盖）；ok 为 NULL |
| `definition_version` | `String(16)` | 否 | `^\d+\.\d+$` | 本期 `"1.0"`；bump 后全量重建 |
| `rebuilt_at` | `DateTime(tz)` | 否 | — | 本次计算时刻（`clock.now()`） |
| `created_at` / `updated_at` | `DateTime(tz)` | 否 | — | `server_default now()`（B 类审计时间） |

**不变式（应用层断言 + 测试；前两条 DB CHECK）：**
1. `total_assets_units = cash_units + market_value_units`。
2. `holding_count >= 0`；**不设 `market_value >= 0`**（D8：超卖日允许负市值）。
3. `status='blocked'` ⇒ `cash/market/total/cost/unrealized/cashflow/holding_count = 0`，`prev_total/daily_pnl/daily_return IS NULL`，`status_reason` 非空；API 数值输出 `null`。
4. `daily_pnl IS NOT NULL ⇔ prev_total IS NOT NULL`；`daily_return IS NOT NULL ⇒ prev_total > 0`。
5. `daily_pnl = total - cashflow - prev_total`（prev 非 NULL 时）。
6. `(user_id, trade_date)` 唯一；`[start_date, last_built_through]` 日期连续无洞。
7. 超卖日（负持仓）`holding_count` 仍按非零持仓计数，状态 `degraded`。

### 4.2 `review.asset_build_state`（增量水位线 + 构建摘要）

| 列 | 类型 | 空 | 说明 |
|---|---|---|---|
| `user_id` | `UUID` FK CASCADE PK | 否 | 一用户一行 |
| `start_date` | `Date` | 是 | 历史起点（最早未软删流水日） |
| `last_built_through` | `Date` | 是 | 已构建到的最后自然日；全局 blocked/无数据时 NULL |
| `last_built_at` | `DateTime(tz)` | 是 | 最近一次构建/检查完成时刻（no-op 也更新；配合 `last_mode=noop` 可区分） |
| `last_trade_updated_at` | `DateTime(tz)` | 是 | 构建读取的**全部流水（含软删）** `max(updated_at)` 快照 |
| `last_trade_count` | `BigInteger` | 是 | 同上快照的 `COUNT(*)`（变更指纹 1） |
| `last_trade_version_sum` | `BigInteger` | 是 | 同上快照的 `SUM(version)`（变更指纹 2，捕获改/删/恢复） |
| `data_as_of` | `Date` | 是 | 本次构建读取的 `market_data.as_of`（血缘） |
| `assumption` | `String(16)` | 是 | 期初现金锚点来源；v1 恒为 `back_solved`（§5.2） |
| `last_status` | `String(16)` | 否 | 本次构建整体结果：`ok` / `degraded` / `blocked`（blocked 仅全局无锚点；逐日 blocked 使整体为 degraded） |
| `last_fifo_error` | `Boolean` | 否 | 本次构建时全历史 FIFO 是否存在 `data_error`；下次若变化 → 全量（保证全表 degraded 状态一致）；`reset_state` 置 false |
| `status_reason` | `String(256)` | 是 | 全局 blocked/degraded 原因（逐日原因在 `asset_daily.status_reason`） |
| `last_mode` | `String(16)` | 否 | `full` / `incremental` / `noop` |
| `last_rebuild_from` | `Date` | 是 | 本次实际重写起点 |
| `last_rows_written` | `Integer` | 否 | 本次写入行数（no-op=0） |
| `definition_version` | `String(16)` | 否 | 与行同版本 |
| `rows_count` | `Integer` | 否 | 当前保留行数（写事务内 `count(*)` 复核） |
| `version` | `Integer` | 否 | 乐观锁；更新 `UPDATE ... WHERE version=:expected`，失败 → `VERSION_CONFLICT` |
| `created_at` / `updated_at` | `DateTime(tz)` | 否 | 审计时间 |

> **job result 出口**：构建摘要同时写入 job.result（运维/DB 可查），但 **API 不扩 `JobOut.result`**；前端从 `GET /assets` 响应的 `build` 对象取数（P0 审核结论，见 §7.3）。

### 4.3 索引 / 约束 / RLS / 授权

```sql
-- 0006 迁移要点（完整草案见附录 B）
CREATE TABLE review.asset_daily (...);
ALTER TABLE review.asset_daily
  ADD CONSTRAINT uq_asset_daily_user_date UNIQUE (user_id, trade_date),
  ADD CONSTRAINT ck_asset_daily_total CHECK (total_assets_units = cash_units + market_value_units),
  ADD CONSTRAINT ck_asset_daily_holding_count CHECK (holding_count >= 0);
-- 不重复建 ix_asset_daily_user_date（UNIQUE 索引已支持范围/倒序扫描）
CREATE INDEX ix_trade_user_updated_at ON ledger.trade (user_id, updated_at, trade_date); -- 增量脏查询 index-only
CREATE TABLE review.asset_build_state (...);

ALTER TABLE review.asset_daily ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON review.asset_daily
  USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (同 USING);
-- asset_build_state 同样处理
GRANT SELECT, INSERT, UPDATE, DELETE ON review.asset_daily, review.asset_build_state TO stock_app;
GRANT SELECT ON ... TO stock_backup;
```

- 两表带 `user_id`，RLS 门禁自动纳入：当前实测 `review 4/4`，加 2 表后 `review 6/6`（**现有 4 张**：round/round_fill/round_build_state/nav_daily；v0.1 的「5→6」是笔误）。
- `ledger.trade` 新增索引：`Trade.__table_args__` 与迁移同批声明，`make migrate-check` 保持无漂移（当前基线已实测干净）。

### 4.4 迁移与版本同步

| 项 | 变更 |
|---|---|
| 迁移 | 新 `0006_review_asset_daily.py`：2 表 + 索引 + CHECK + RLS + GRANT + `schema_version='0006_review_asset_daily'`；downgrade 回 `0005_market_cache` |
| 代码版本 | `stock_api/version.py::SCHEMA_VERSION = "0006_review_asset_daily"` |
| ORM | `stock_review.models` + `AssetDaily`/`AssetBuildState`；`REVIEW_TABLES` **4→6** |
| 文档 | `docs/DATABASE.md` §2.1/§6 补 `asset_daily`/`asset_build_state`，并**回填历史欠账**：`nav_daily`、`market` schema（当前缺） |
| 测试脚手架 | `backend/tests/conftest.py::_REVIEW_TABLES` 4→6；`backend/tests/integration/test_rls_isolation.py::_REVIEW_TABLES` 当前只有 3 张（缺 nav_daily）→ 补到 **6** 张并补种子 SQL |

---

## 5. 计算口径

### 5.1 时间与范围
- 业务日：`clock.today()`（Asia/Shanghai）。
- 起点 `start_date`：全部**未软删**流水 `min(trade_date)`；无流水 → 不建行。
- 终点 `end_date = clock.today()`（D7）。行情 `market_data.as_of` 只用于：
  - `data_as_of` 血缘留痕；
  - 日期 > `as_of` 且持仓数 > 0 → 该行 `degraded`（原因"行情未覆盖，按最近收盘估值"）；纯现金行不降级。
  - 不再用 `end = min(today, as_of)`，避免 live 多进程 as_of 回退导致水位线振荡/`delete_after` 反复删补。
- 行粒度：`[start, end]` **每个自然日一行**（含周末/节假日，价格 carry-forward，现金不变）。
- 价格阶梯：`[rebuild_from - 45 天, end]`（全量时 `[start - 45, end]`）的 bars，取"当日或之前最近收盘价"（复用 `_PRICE_LOOKBACK=45` 行为）。

### 5.2 期初锚点与初始持仓
- `start_date` 是全部流水最早日 → **不存在 start 之前的余额行** → `_initial_cash(trades, start)` 必走 `back_solved`（`bridge.py:118-150`）。因此 v1 `assumption` 恒为 `"back_solved"`；`statement` 分支保留在 bridge 供 nav 使用，但资产表不会产生。
- 无任何 `cash_balance` 行 → **全局 blocked**：不写日行；state `last_status=blocked`、`last_built_through=null`、`status_reason` 非空；API 列表空 + 横幅。
- `reset_state` 统一语义：**清空** `start_date/last_built_through/data_as_of/assumption/status_reason/last_rebuild_from`，`rows_count=0`、`last_rows_written=0`、`last_fifo_error=false`、`last_trade_count/last_trade_version_sum/last_trade_updated_at` 写入当前快照（无流水则 0/0/null）；`last_status` 由调用方给（无流水=ok、全局 blocked=blocked）。UT-10 断言 start_date 保留是错误表述，按本节为准。
- 初始持仓为空（start 前无流水）；首行 `prev_total = initial_cash`（无初始持仓市值），缺锚点则 NULL。
- 已知数据前提：期初已有持仓但缺少买入流水时，资产/成本会偏低（与 nav 同限制，见 §11）。

### 5.3 逐日推演
按 `balances_ordered` 同序处理当日流水：`(trade_date, line_no NULLS FIRST, side=missing 优先, id)`——**必须**复用该排序（否则同日余额取值/反推不可复现）：
1. 数量：`buy/bonus/rights/new_stock` 加 `quantity_units`；`sell` 减；无下限（超卖可为负）。
2. 成本：`buy/rights/new_stock` 且 `amount_units != 0` → `cost += -amount_units`；`sell` → `max(0, old_cost + old_cost*delta//old_qty)`。
3. 现金：当日有 `cash_balance_units` 的行 → 取**当日最后一笔**余额；否则 `cash += Σ amount_units`。
   - 已知限制：余额行之后的当日其它 amount 会被"覆盖"吞掉（与 nav 行为一致；若修正需 nav 同步改，属人工决策，v1 不修）。
4. 外部现金流：`category=transfer` 的 amount 求和。
5. 市值：**逐标的** `qty * close // 1e8` 后求和；持有标的在当日及之前无任何收盘 → 当日 `blocked`（文案复用 nav 的 `_blocked_reason`/行情失败分类）。
6. 状态：
   - `blocked`：缺价格（逐日）或全局无锚点（整表）。
   - `degraded`：`fifo_error_now=True`（全历史任一回合 `data_error`，**全局**，与 nav 一致，所有非 blocked 行）；或日期 > `as_of` 且有持仓。
   - `ok`：其余。
   - **FIFO 异常集合变化**（`last_fifo_error` 与当前不同）必须触发全量重建，否则增量只会改窗口、旧行状态会与新行不一致；`status_reason` 写"FIFO 数据异常（超卖等）"。
7. 日盈亏：`daily_pnl = total - cashflow - prev_total`；`prev_total` 取上一自然日非 blocked 总资产（上一日 blocked → NULL；首行取期初锚点）；`daily_return = Decimal(daily_pnl)/Decimal(prev_total) → scale 8 ROUND_HALF_UP`，仅 `prev_total>0`。
8. 整体 `last_status`：全局 blocked → `blocked`；否则有任一非 ok 行 → `degraded`；否则 `ok`。

### 5.4 与 nav / TWR 的关系
- 抽取共享原语到 `stock_review/application/valuation.py`：`initial_cash_anchor` / `closing_price_ladder` / `close_on_or_before` / `total_market_value` / `DailyPortfolioState` / `apply_trade` / `load_prices`；nav 与资产表共用。
- nav 的稀疏循环、TWR/份额/XIRR 逻辑**不变**（API-032 回归兜底）。
- 口径一致性：**资产值/现金流同口径**；**状态口径独立**（nav 全局 degraded；资产表也是全局 degraded，但另有逐日 blocked 与行情未覆盖降级）。
- 护栏 IT-07：同夹具/时钟/行情下，nav 各点 `total_assets`（换算标度整数）== `asset_daily` 同日 total，cashflow 相等；不比对 status。

---

## 6. 增量更新算法（v1.0 修正版）

### 6.1 读取与指纹
- 读取全部流水（含软删，**与 `balances_ordered` 同序**），得到：
  - `live`：未软删行（计算用）；
  - 指纹：`count_all`、`sum(version_all)`、`max(updated_at_all)`（脏检测/过期判定用）。
  - `fifo_error_now = any(round.data_error for round in build_rounds(trades_to_events(live)))`（全历史，决定全局 degraded 与是否需全量）。
- 水位线 `last_trade_updated_at` 取**实际读快照**的 max，不取 `clock.now()`。

### 6.2 伪代码

```python
VERSION = "1.0"
SAFETY_DAYS = 45
WATERMARK_OVERLAP = timedelta(hours=1)   # 仅指纹变化时使用：覆盖「长事务晚提交、updated_at=事务开始时间」竞态

state = await repo.get_state(user_id)
trades_all = await repo.load_trades_including_deleted(user_id)   # 与 balances_ordered 同序
live = [t for t in trades_all if t.deleted_at is None]
if not live:
    await repo.delete_all(user_id)
    await repo.reset_state(user_id, rows=0, through=None, status="ok")
    return BuildResult(mode="full", rows_written=0, rows_total=0)

start = min(t.trade_date for t in live)
end = clock.today()
if end < start:                       # 未来日期流水/时钟回拨
    await repo.delete_all(user_id)
    await repo.reset_state(user_id, rows=0, through=None, start=None, status="ok")
    return BuildResult(mode="full", rows_written=0, rows_total=0)

fifo_error_now = any(r.status == "data_error" for r in build_rounds(trades_to_events(live)))
market_changed = state is not None and state.data_as_of != market_data.as_of

full = (
    mode == "full" or state is None
    or state.definition_version != VERSION
    or state.last_built_through is None
    or state.last_fifo_error != fifo_error_now      # 全局 degraded 集合变化 → 全量保证一致
    or start != state.start_date                    # 双向：起点前移/后移都全量
    or end < state.last_built_through               # 数据源/时钟回退 → 全量收敛
)
if not full and state is not None and market_changed:
    old_as_of = state.data_as_of
    if old_as_of is None or (end - old_as_of).days > SAFETY_DAYS - 1:
        full = True                                 # 行情覆盖跨度超过安全窗 → 窗口不足，全量

dirty_rows: list[Trade] = []
rebuild_from = start
if not full and state is not None:
    if (count_all, version_sum_all, max_updated_at) != (
        state.last_trade_count, state.last_trade_version_sum, state.last_trade_updated_at
    ):
        dirty_rows = await repo.dirty_trades(     # 含软删；与 balances_ordered 同序
            user_id, since_updated_at=state.last_trade_updated_at - WATERMARK_OVERLAP
        )
        if any(t.version > 1 for t in dirty_rows):
            full = True                           # D10：改日期/软删/恢复/撤销 → 全量
        elif not dirty_rows:
            full = True                           # 指纹变了但无时间戳可定位（并发长事务竞态）→ 全量兜底
    if full:
        rebuild_from = start
    else:
        dirty_date = min(t.trade_date for t in dirty_rows) if dirty_rows else None
        last = state.last_built_through
        if end == last and not market_changed and dirty_date is None:
            # no-op：只记录"本次检查"，不动数据/水位线/指纹；便于前端区分 noop
            await repo.update_state_cas(
                expected_version=state.version, last_mode="noop", last_built_at=clock.now(),
                last_rebuild_from=None, last_rows_written=0,
            )
            return BuildResult(mode="noop", rows_written=0, rows_total=state.rows_count)
        rebuild_from = max(start, min(last + 1, end - (SAFETY_DAYS - 1)))
        if dirty_date is not None:
            rebuild_from = min(rebuild_from, dirty_date)
else:
    rebuild_from = start

# 回填/补差落在首个余额行之前时会改变 back_solved 锚点，窗口外行不会重写 → 必须全量
first_balance_date = min((t.trade_date for t in live if t.cash_balance_units is not None), default=None)
if (not full and first_balance_date is not None and start < first_balance_date
        and rebuild_from <= first_balance_date):
    full = True
# 增量时窗口前一日必须已有落库行（用于 prev_total）；缺失 → 全量兜底（必须在拉行情前修正窗口）
prev_row = await repo.get_row(user_id, rebuild_from - 1) if rebuild_from > start else None
if rebuild_from > start and prev_row is None:
    full = True
    rebuild_from = start
# 增量时只拉窗口行情；全量拉 [start-45, end]
prices = await load_prices(live_symbols, from_=rebuild_from - 45, to=end)
# 内存重放现金/持仓/成本（无 I/O）：start..end；仅为窗口生成行
valuations = compute_daily_valuations(
    live, start=start, end=end, rebuild_from=rebuild_from,
    prices=prices, prev_row=prev_row, fifo_error=fifo_error_now,
)
if valuations.global_blocked:
    await repo.delete_all(user_id)
    await repo.reset_state(user_id, through=None, status="blocked", reason=..., assumption=None)
    return BuildResult(mode=..., rows_written=0, rows_total=0, status="blocked")

if full:
    await repo.delete_all(user_id)
else:
    await repo.delete_range(user_id, rebuild_from, end)
await repo.insert_rows(valuations.rows)          # 窗口内行
if not full:
    await repo.delete_after(user_id, end)         # 清理旧构建残留的"未来"行
rows_count = await repo.count(user_id)            # 写事务内复核
overall_status = await repo.overall_status(user_id)  # EXISTS(status != 'ok') → degraded
await repo.update_state_cas(                       # WHERE version=:expected
    start_date=start, last_built_through=end, last_built_at=clock.now(),
    last_trade_updated_at=max_updated_at, last_trade_count=count_all,
    last_trade_version_sum=version_sum_all, data_as_of=market_data.as_of,
    assumption="back_solved", last_status=overall_status,
    last_fifo_error=fifo_error_now, status_reason=valuations.global_reason,
    last_mode=("full" if full else "incremental"),
    last_rebuild_from=rebuild_from, last_rows_written=len(valuations.rows),
    definition_version=VERSION, rows_count=rows_count,
)
```

### 6.3 关键规则
1. **变更指纹** `(count, Σversion, max(updated_at))`：任何插入/修改/软删/恢复都会改变它。指纹不变时直接 no-op / 只补日期；指纹变化时才执行脏查询，且查询下界取 `水位线 − 1h`（`WATERMARK_OVERLAP`）——PG `now()` = 事务开始时间，迟提交的长事务时间戳可能更小，重叠窗把这种竞态纳入脏集合；若重叠窗仍查不到任何脏行 → **全量兜底**。该机制不依赖时钟单调性，也避免了「每次点击都全量扫描脏行」。（`rebuild.py` 同源旧注释「不会漏」属历史债，本期不顺带改。）
2. **含软删读 + 未软删算**：软删/撤销/恢复都走 ORM UPDATE（`updated_at` 刷新），可被指纹捕获；计算只用 `deleted_at IS NULL`。
3. **version>1 → 全量（D10）**：PATCH 可改 `trade_date` 更晚，按当前日期做脏窗口会漏掉旧日期 → 修正/删除/恢复一律全量；纯新增导入（version=1）保持增量。
4. **45 天安全窗**：只在有新数据/脏流水/行情 as_of 变化时重算最近 45 天；无变化重复点击 **no-op**（不写行、不动数据/水位线/指纹，仅把 `last_mode=noop`、`last_built_at`、`last_rows_written=0`、`last_rebuild_from=NULL` 写入 state，便于前端显示"已是最新"）。
5. **稳定序**：所有读取复用 `balances_ordered` 的 ORDER BY（日期→行号 NULLS FIRST→missing 优先→id）；仓储 docstring 声明契约，UT 断言确定性。
6. **失败原子性**：delete/insert/state 同事务；异常回滚 → 旧表与旧水位线完好，job 重试安全。
7. **取消**：写事务前检查；写事务内不中断（与 rebuild_rounds 一致）。
8. **并发**：JobQueue 单飞 `kind=build_assets`、`scope_key=f"{user_id}:assets"`、`params={"mode": <解析默认值后的真实值>}`；同参并发复用（`X-Job-Reused`），异参 409。**禁止**把 params 写死为 auto（否则 full 会被静默复用成 auto）。
9. **状态 CAS**：`UPDATE ... WHERE user_id=:uid AND version=:expected`；0 行 → `VERSION_CONFLICT`（job 失败可重试）；无 state 时 insert。
10. **行情 as_of 变化**：即使 end 未前进也要重算窗口（否则旧 `date > as_of` 的 degraded 行永远修不回来）；as_of 跨度超过 45 天 → 全量。
11. **FIFO 异常集合**：`last_fifo_error` 变化 → 全量；保证"全局 degraded"在增量后与全量等价（IT-01）。
12. **整体状态**：写完后 `EXISTS(status != 'ok')` 决定 `last_status`（有逐日 blocked/degraded → degraded），不要仅看本次窗口。

### 6.4 边界行为表

| 场景 | full? | rebuild_from | 结果 |
|---|---|---|---|
| 首次点击 / 无 state | 是 | start | 全量行 + state |
| 无变化重复点击 | 否 | — | no-op；数据/水位线/指纹不变；仅 `last_mode=noop`/`last_built_at`/`last_rows_written=0`/`last_rebuild_from=NULL`；`rows_written=0` |
| 追加新流水（version=1，日期 > 水位线） | 否 | `min(last+1, end-44)` | 重写安全窗+新日期；与 full 等值 |
| 追加**回填**旧日期新流水（version=1，日期 < 水位线） | 否 | `min(脏日期, end-44)` | 回填在首个余额行之后 → 从回填日起重写；**回填在首个余额行之前 → full**（锚点变化） |
| 修正/软删/恢复（version>1，含日期改晚） | **是** | start | 全量；与 full 等值 |
| 撤销导入批次（软删，version>1） | **是** | start | 全量；删掉批次影响的所有行 |
| 最早流水被删（start 后移） | **是** | start | delete_all + 重写；`rows_count` 复核 |
| `end < last_built_through`（时钟/数据源回退） | **是** | start | 全量收敛（走 `delete_all`，不依赖 `delete_after`） |
| `end < start`（未来流水） | — | — | 清空，state through/start=NULL，rows=0 |
| 无现金锚点 | 全量尝试 | start | 不写行，state blocked |
| `definition_version` 升级 | 是 | start | 全量替换版本 |
| 行情前进（end 前进） | 否 | 安全窗 | 补齐新日期 |
| 行情 as_of 变化但 end 未前进 | 否 | 安全窗 | 重算窗口，修复 `date > as_of` 的 degraded 行 |
| FIFO 异常集合变化 | **是** | start | 全量，保证全表 degraded 一致 |
| 行情修正（45 天内、且 end 前进） | 否 | 安全窗 | 吸收 |
| 行情修正（无 end 前进 / >45 天） | 否/手工 full | — | **不自动吸收**（D9）；需 `mode=full` |
| 构建中途失败 | — | — | 事务回滚，旧数据/旧 state 完好 |

---

## 7. API 契约

### 7.1 `GET /assets`（API-081，`listAssets`）

- 纯读；不落库、不触发构建。认证 `read`；admin → 403 `ADMIN_CANNOT_READ_TRADES`。
- Query：`limit`(1~200，默认 50) / `cursor` / `from`（alias `date_from`）/ `to`（alias `date_to`）/ `status`(`ok|blocked|degraded`) / `sort`（白名单 `trade_date`、`-trade_date`，默认 `-trade_date`）。
  - 日期过滤为**闭区间** `[from, to]`（与 `/rounds` 的 `from/to` + `INVALID_PERIOD` 写法一致；`/trades` 用 `trade_date_from/to` 且无 from>to 校验，属既有差异；Catalog §0.4 半开口径为历史债，本期不引入新分歧）。
- 200：
```json
{
  "items": [{
    "date": "2026-06-30",
    "cash": "-70000.00",
    "market_value": "170620.00",
    "total_assets": "100620.00",
    "holding_cost": "170000.00",
    "unrealized_pnl": "620.00",
    "holding_count": 1,
    "cashflow": "0.00",
    "prev_total_assets": "100000.00",
    "daily_pnl": "620.00",
    "daily_return": "0.00620000",
    "daily_return_pct": "0.62%",
    "status": "ok",
    "status_reason": null
  }],
  "next_cursor": null,
  "has_more": false,
  "build": {
    "state": "current",              // missing | current | stale | blocked
    "last_status": "ok",             // ok | degraded | blocked（blocked 仅全局无锚点）
    "last_built_at": "2026-09-30T08:00:00Z",
    "last_built_through": "2026-09-30",
    "last_mode": "incremental",      // full | incremental | noop
    "last_rebuild_from": "2026-08-17",
    "last_rows_written": 45,
    "rows_count": 125,
    "assumption": "back_solved",     // v1 恒为 back_solved；无数据/blocked 为 null
    "status_reason": null,           // 全局 blocked/degraded 原因（API 字段名与行级 status_reason 一致）
    "has_new_market_data": false,
    "definition_version": "1.0"
  },
  "definition_version": "1.0",
  "data_as_of": "2026-09-30"         // 本次构建血缘；未构建为 null
}
```
- **blocked 行**：`cash/market_value/total_assets/holding_cost/unrealized_pnl/cashflow/prev_total_assets/daily_pnl/daily_return/daily_return_pct` **一律 `null`**（不是 `"0.00"`，也不是省略键）；`holding_count=0`；`status_reason` 非空。
- 金额 2 位定点串（遵循 API-Catalog §0.8）；`daily_return` 8 位比率串；`daily_return_pct` 两位百分比串（后端算）。
- 顶层 `definition_version` = `state.definition_version`（无 state 时取当前常量）；`GET /assets` 只返回与 state 版本一致的行，避免版本 bump 后新旧行混排。
- `build` 块可空性：**无 state（`missing`）** 时除 `state="missing"`、`rows_count=0`、`last_mode=null`、`has_new_market_data=false`、`definition_version` 取当前常量外，`last_* / assumption / status_reason / data_as_of` 全部 `null`；**全局 blocked** 时 `last_status="blocked"`、`status_reason` 非空、`last_built_through=null`、`last_*` 其余为 null、`rows_count=0`。
- `build.state` 判定链（按序，**P1 修正**）：
  1. 无 state → `missing`；
  2. `last_status=blocked`（且 `last_built_through IS NULL`）→ `blocked`；
  3. `last_built_through IS NULL`（无流水/全部软删）→ `missing`；
  4. `state.definition_version != 当前版本` → `stale`；
  5. 当前指纹 `(count, Σversion, max(updated_at))`（**含软删**）与 state 不一致 → `stale`；
  6. 当前 `market_data.as_of > state.data_as_of` → `stale`（`has_new_market_data=true`）；
  7. `clock.today() > state.last_built_through` → `stale`（有新自然日）；
  8. 否则 `current`。
- 错误：401 `TOKEN_EXPIRED`（现状不返回 `WWW-Authenticate`，见 §11）；admin 403；400 `INVALID_CURSOR`/`UNKNOWN_QUERY_PARAM`/`INVALID_SORT_FIELD`；422 `INVALID_PERIOD`（from>to）/`VALIDATION_ERROR`（status 非法、日期格式由 FastAPI 解析失败）。

### 7.2 `POST /assets/build`（API-082，`buildAssets`）

- 认证 `write`；admin 拒绝（`forbid_admin_trades`）。执行顺序与 `/jobs/rebuild-rounds` 一致：`forbid_admin_trades` → `require_write` → 幂等键校验（ADMIN_FORBIDDEN 的 POST 用例因此不需要带 key）。
- Header：`Idempotency-Key` 必填（16~128）；**request_hash 基于解析默认值后的规范 body**（缺省 body 与 `{"mode":"auto"}` 等价，同 key 不误报 422）。
- Body（可选）：`{"mode": "auto" | "full"}`，默认 `auto`。
- 202（新增专用 schema `AssetBuildAccepted`，**不复用** `JobAcceptedOut`，避免多出 `batch_id:null`）：
```json
{"job_id": "job_x", "type": "build_assets", "status": "pending",
 "links": {"self": "/api/v1/jobs/job_x", "events": "/api/v1/jobs/job_x/events"}}
```
  Header：`Location`、`Retry-After: 1`；复用 job 时 `X-Job-Reused: true`；幂等重放 `Idempotency-Replayed: true`（重放路径同样带 Location/Retry-After）。
- 错误：401；403（admin/`INSUFFICIENT_SCOPE`）；422 `VALIDATION_ERROR`（幂等键缺失/mode 非法）；422 `IDEMPOTENCY_KEY_REUSED`（同 key 异体）；409 `IDEMPOTENCY_IN_PROGRESS`（平台语义；HTTP 层较难观测，测试按平台层写，见 §9.4）；409 `JOB_ALREADY_RUNNING`；429 `QUOTA_EXCEEDED`（不承诺 `Retry-After`，现状中间件未注入）。
- Job `kind=build_assets`；handler 上报阶段（**合法枚举**）：`queued(0) → recomputing(40) → writing(80) → finalizing(100)`；progress 单调、粒度 ≥5。平台层 claim 会把 stage 置 `parsing`、finish 置 `done`（既有行为，AT 不按 handler 枚举断言终态 stage）。
- Job `result` 仍写入 `platform.job.result`，键固定为 `{mode, rebuild_from, rows_written, rows_total, built_through, status, assumption, data_as_of}`（**不扩 `GET /jobs` 响应**，测试用 owner 直读 job 行断言）；对外摘要一律读 `GET /assets` 的 `build` 对象（`last_*` 命名，无 `rows_total` 键）。
- Job 单飞：`scope_key=f"{user_id}:assets"`；`params={"mode": <真实值>}`。

### 7.3 契约/门禁同步清单（v1.0 补全）

| 文件/项 | 变更 |
|---|---|
| `docs/architecture/v2/API-Catalog.md` | §1 增 API-081/082（**总数 80→82**，标题/§2「30 个详细契约；其余 50」同步）；§2 详细契约；§4 增 `AssetPoint/AssetPage/AssetBuildState/AssetBuildAccepted` 并给 **Job.type 枚举加 `build_assets`**；§5.2 可取消/阶段；§6 两行验收；§7.3 domain 前缀加 `assets`（并注明仓库实际 operationId 为 camelCase） |
| `scripts/ci/gate_endpoint_coverage.py` | `EXPECTED_ALL_ENDPOINTS = 80 → 82` |
| `backend/services/api/src/stock_api/openapi_meta.py` | `OPERATION_META` 增 `listAssets`/`buildAssets`；`MONEY_FIELDS` **只加资产独有** `cash/holding_cost/unrealized_pnl`（**禁止**加 `market_value/total_assets/prev_total_assets/daily_pnl`，否则 nav 3 位示例被强制 2 位而契约测试红）；`RATIO_FIELDS` 加 `daily_return`；`_STRING_EXAMPLES` 加 `daily_return_pct`；`_INT_EXAMPLES` 加 `holding_count/rows_count/last_rows_written/last_trade_count`；`EXAMPLE_OVERRIDES` 增 `listAssets`/`buildAssets` 手工示例（2 位金额、type=build_assets），`_DICT_EXAMPLES` 增 `build` |
| `backend/tests/contract/test_openapi_contract.py` | operation 数 **43→45**；`json_count 36→38`；`set(OPERATION_META)` 自动覆盖 |
| `backend/tests/isolation/test_isolation_matrix.py` | `ADMIN_FORBIDDEN` + `GET /assets`、`POST /assets/build`；`LIST_ISOLATION_MATRIX` + `GET /assets`（补种子行/自定义断言）；`UNAUTHENTICATED_MATRIX` + 两端点（POST 需在 `_unauth_kwargs` 补 `Idempotency-Key`），保证 `gate_tenant_coverage.py` 全绿 |
| `contracts/openapi.json` | `make api-snapshot` 重生成并提交 |
| `frontend/packages/api-client/src/api/generated/schema.d.ts` | `make sdk` 重生成，勿手改 |
| `docs/DEV-PLAN-v2.md` | 追加「Period2-16 资产表」行（**期次=二**，避免被 G6b 当一期），并改文件内 4 处 80/80/80 计数（DEV-PLAN:6/91/311/416） |
| `docs/DATABASE.md` | §2.1/§6 补两张新表 + 回填 `nav_daily` 欠账（`market` 已有，不重复） |
| `backend/services/worker/src/stock_worker/{runner,handlers}.py` | worker 行情装配 + 新 handler（§10） |
| `backend/packages/contracts/src/stock_contracts/enums.py` | `JobType.BUILD_ASSETS = "build_assets"`（运行时可用裸串，但补枚举保持一致） |
| `backend/tests/unit/platform/test_worker_runner.py` | 假 handler 签名补第 5 参 |
| `backend/tests/unit/contracts/test_enums.py` | 断言 `BUILD_ASSETS` 值 |

---

## 8. Web 界面

### 8.1 路由与入口
- 新页 `frontend/apps/web/src/pages/Assets.tsx`；路由 `/assets`；顶部导航「净值」后加「资产」。
- 标题「资产表」；文案：每日资产快照（现金 + 持仓市值），用于资产变化与后续回撤；本页不做计算。

### 8.2 列表
| 列 | 内容 | blocked 行 |
|---|---|---|
| 日期 / 总资产 / 现金 / 持仓市值 / 持仓成本 / 浮动盈亏 / 日收益额 / 日收益率 / 外部现金流 / 持仓数 | 对应字段；正负着色（复用 `signedClass` 思路） | 数值列 `—` |
| 状态 | badge：正常 / 阻断 / 降级；`status_reason` 作为 title 提示 | 阻断 badge |

- 分页：默认 50；`Cursor` 组件文案「下一页」；默认日期倒序。
- 汇总条（数据来自 `build`）：数据截至 `data_as_of`；记录数 `rows_count`；最近构建/检查 `last_built_at`（no-op 也会更新，需配合 `last_mode` 标注）；最近模式/重写行数；锚点 `back_solved` 提示；`state=stale` → "有新流水/新行情，建议更新"；`last_status=degraded` → 部分阻断/降级提示；`state=blocked` → 阻断横幅 + 空态。
- 无筛选控件（v1 只列表 + 分页；API 已支持 from/to/status 供后续使用）。

### 8.3 按钮与流程
- 主按钮：`state === "missing"` → **「构建资产表」**；否则 **「更新资产表」**；执行中「构建中…/更新中…」disabled。
- 点击：`api.assets.build({mode:"auto"}, newIdempotencyKey())` → 202 → 展示 `JobProgress`（SSE）；**页面级轮询兜底**（现有 `JobProgress` 只重试 SSE，不轮询；复用 `Rounds.tsx` 的 `setTimeout` 轮询到终态）→ 成功后重拉 `GET /assets`，从 `build.last_mode/last_rebuild_from/last_rows_written` 渲染提示：
  - 首次/全量：`已构建 N 天资产记录，数据截至 YYYY-MM-DD。`
  - 增量：`增量更新完成：重算 X 天（自 YYYY-MM-DD 起），数据截至 YYYY-MM-DD。`
  - no-op：`资产表已是最新，无需更新。`
- **进入页面接管在跑 job**：查询 `GET /jobs?status=pending/running`（或复用 Rounds 页的 `watchActiveRebuild` 思路），若存在 `type=build_assets` 的活跃 job → 直接展示进度，避免刷新后重新点击/状态误导。
- 失败：`ProblemView` 展示 `user_message/what_to_do`；job failed 同样展示错误码。
- 零口径：不用 `Number/parseFloat/parseInt/toFixed/算术`；百分比用后端 `daily_return_pct`；`0.00%` 不着色（修正 Nav 页 `signedClass` 对 `"0.00%"` 误判的坑，本页单独处理）。
- 可访问性：按钮 aria/disabled；加载态文本；表格表头用 `role=columnheader`（现有 `Table.tsx` 无 caption，v1 不扩组件）。

---

## 9. 测试方案

### 9.1 分层与命令
| 层 | 位置 | 命令 |
|---|---|---|
| 纯函数单测 | `backend/tests/unit/review/test_valuation.py`、`test_assets_application.py` | `make test-review`（需把新集成文件加入目标或走 `make test-p2`） |
| API 验收 | `backend/tests/api/test_api_081_assets.py`、`test_api_082_assets_build.py` | `make test-api` |
| 增量/等价集成 | `backend/tests/integration/test_assets_incremental.py` | `make test-integration` |
| RLS/隔离 | `backend/tests/integration/test_rls_isolation.py`（补 6 表）、`backend/tests/isolation/test_isolation_matrix.py` | `make test-rls` / `make test-isolation` |
| 契约 | `python -m stock_api.cli api-check`、`python -m stock_api.cli api-snapshot --check`、`pytest backend/tests/contract` | 见命令（`make api-snapshot` 无 `--check` 参数，v0.1 写法有误） |
| 前端 | `Assets.test.tsx`、`router`/`Layout` 测试 | `cd frontend && pnpm -r test` |
| 类型/分层 | mypy / import-linter / tsc | `make typecheck` |
| 迁移 | `make migrate` + `alembic downgrade -1` + `make migrate-check` | 人工/本地执行 |

### 9.2 单元测试（UT）
| ID | 场景 | 输入 | 期望 |
|---|---|---|---|
| UT-01 | 纯现金日 | 第 1 天一条余额行 100000，之后 4 天无流水 | 5 行；cash=total=100000；market=0；pnl=0；首行 prev=100000 |
| UT-02 | 买入 + 月末估值 | 05-29 余额 100000；06-30 买 100@1700（-170000，余额 -70000）；600519 06-30 收盘 1706.20 | cash=-70000.00、market=170620.00、total=100620.00、cost=170000、unrealized=620、pnl=620、return=0.0062（620000 units） |
| UT-03 | 卖出结转成本 | 买 200@10（-2000），卖 100@12（+1200） | 剩余 qty=100、cost=1000 |
| UT-04 | 分红/利息/费用 | dividend/interest/fee | 现金按 amount 变化；不改持仓/成本；非 transfer 不计 cashflow |
| UT-05 | 银证转账 | transfer +50000 | cashflow=50000；pnl 不含该 50000 |
| UT-06 | 送转/配股 | bonus 零金额正数量；rights 负金额 | bonus 加数量不改成本；rights 加数量且成本 += -amount |
| UT-07 | 余额覆盖优先 + 已知限制 | 同日多笔含余额行；余额行之后仍有 amount | 现金取当日最后一笔余额；**记录**"其后 amount 被覆盖"的既有简化（v1 不修，用例固定行为） |
| UT-08 | 周末/节假日 carry-forward | 跨周末持仓 | 非交易日行用最近收盘；日期连续 |
| UT-09 | 缺行情 → blocked | 持有 `999999`（无 bar） | 受影响日 blocked；DB 数值=0、API=null；原因含代码；不 500 |
| UT-10 | 缺现金锚点 → 全局 blocked | 全部 `cash_balance=NULL` | 0 行；state.last_status=blocked、through=NULL、start_date 按 reset_state 清空、status_reason 非空；恢复后旧 reason 清空 |
| UT-11 | back_solved 锚点 | 首行即买入，余额 -70000、amount -170000 | initial_cash=100000；assumption=back_solved；首日 pnl 正确 |
| UT-12 | 超卖 → degraded + 负市值 | 卖出数量 > 持仓 | 负 qty/market_value；status=degraded；不触发 CHECK 失败 |
| UT-13 | 日收益边界 | prev_total=0/负；blocked 次日 | return=NULL；blocked 次日 pnl/return=NULL，再下一日恢复 |
| UT-14 | 确定性 | 同输入/clock 跑两次 | 行/ID/值/status/status_reason/rebuilt_at 完全一致；**排除** `created_at/updated_at`（server_default） |
| UT-15 | 自然日行数/连续性 | 2026-01-01~01-31 | 31 行，无洞；`count(*)==rows_count` |
| UT-16 | 金额守恒（property） | hypothesis 随机流水 | 任意行 `total=cash+market`；非 blocked `pnl=total-cashflow-prev`；成本/浮盈关系成立 |
| UT-17 | 成本守恒 | 随机买卖/送转 | `unrealized=market-cost`；cost ≥ 0；qty=0 时 cost=0 |
| UT-18 | 行情失败分类 | stub 行情源（非 replay 夹具） | 逐标的降级 blocked，不整段失败；文案含三类后缀 |
| UT-19 | `end < start` | FrozenClock 早于最早流水 | 0 行；state through/start=NULL、rows=0；不出现负 rows_count |
| UT-20 | `definition_version` | 常量变更 | 旧 state 触发 full |
| UT-21 | 交易日期改晚 | 旧流水 PATCH 到更晚日期（version>1） | auto 触发 full；结果与 full 相等；旧日期行不再含该流水 |
| UT-22 | 指纹兜底 | 人为构造 `updated_at` 不变但 version/count 变化 | 指纹不一致 → full（脏查询无结果时） |
| UT-23 | 收益率四舍五入 | 正/负 pnl、prev_total 非整除 | Decimal ROUND_HALF_UP 到 1e-8；pct 用 `Ratio.as_pct(2)`（ROUND_HALF_EVEN） |
| UT-24 | status_reason 语义 | blocked/degraded/ok | blocked 必填；degraded 可填；ok 为 NULL |
| UT-25 | 全部未软删 | 全部流水软删 | 0 行；state 重置（各字段显式 NULL/0） |
| UT-26 | FIFO 异常集合变化 | 新增一笔造成超卖（version=1） | auto 触发 full；全表（含旧日期行）同一 degraded 口径；与 full 相等 |

### 9.3 API 验收（AT）
**API-081 `GET /assets`**
| ID | 场景 | 断言 |
|---|---|---|
| AT-01 | happy | 200；`items` 日期倒序；06-30 行金额与 UT-02 oracle 一致；`build.state=current`；`definition_version="1.0"` |
| AT-02 | 未构建 | 200；`items=[]`；`build.state=missing`；owner 直查 `review.asset_daily`/`platform.job` 无新增 |
| AT-03 | 跨用户隔离 | B 列表只有 B 的行；不泄露 A 字段 |
| AT-04 | admin | admin token（含 admin PAT）→ 403 `ADMIN_CANNOT_READ_TRADES` |
| AT-05 | 未认证 | 401 `TOKEN_EXPIRED`（现状**不**返回 `WWW-Authenticate`；Catalog 缺口单列跟进，不在本期修） |
| AT-06 | 游标 | `limit=10` 翻全集无重无漏；篡改 cursor → 400 `INVALID_CURSOR`；A 的 cursor 在 B 名下返回 B 自己的行（200，RLS 兜底，**不**断言 400——现有 cursor 不含 user_id） |
| AT-07 | 参数校验 | `from>to` → 422 `INVALID_PERIOD`；`status=foo` → 422；未知 query → 400；`sort=foo` → 400 |
| AT-08 | blocked 行 | 数值字段全 `null`（`"0.00"`/缺键均失败）；`status_reason` 非空 |
| AT-09 | stale | 构建后插流水/推进行情/时钟跨日 → state=stale；重建后 current |
| AT-10 | 全局 blocked | 200 + 空 items + `build.state=blocked` + `status_reason` 非空 |
| AT-20 | 过滤/排序正向 | `from/to` 闭区间含端点；`status=blocked/degraded`；`sort=trade_date` 升序与默认倒序互逆 |
| AT-21 | limit 边界 | `limit=1/200` 可用；`limit=0/201` → 422 |
| AT-22 | 版本 bump | 改常量后 GET → stale；auto 触发 full 并替换行版本 |
| AT-23 | 构建中读一致性 | job pending/running 时 GET 返回旧的一致快照；drain 后新行原子可见（不声称能观测「无半截行」，构建是单事务） |
| AT-24 | 日期边界 | `from=to` 返回该日；`from` 早于 start/to 晚于 end 正常裁剪 |

**API-082 `POST /assets/build`**
| ID | 场景 | 断言 |
|---|---|---|
| AT-11 | happy 首次 | 202 + Location/Retry-After；type=build_assets；drain 后 `GET /assets` 的 `build` 摘要齐全（last_mode/last_rows_written/last_built_through）；行可见 |
| AT-12 | 幂等重放 | 同 key 同 body → 同一 job_id + `Idempotency-Replayed` + Location；重复构建不重复行；缺省 body 与 `{"mode":"auto"}` 同 key 视为同请求（不 422） |
| AT-13 | 单飞 | 不同 key 发两次 auto（首个待执行）→ 同一 job_id + `X-Job-Reused`；auto 运行中发 full（第三个 key）→ 409 `JOB_ALREADY_RUNNING` |
| AT-14 | 越权 | read PAT → 403 `INSUFFICIENT_SCOPE`；admin → 403 `ADMIN_CANNOT_READ_TRADES`；无 token → 401；跨用户 job 不可见（复用 jobs 404） |
| AT-15 | 校验/配额 | 缺 `Idempotency-Key` → 422；`mode=bad` → 422；预置 4 个**不同 scope**的活跃 job（同 scope 会先撞单飞 409）→ 429 `QUOTA_EXCEEDED`（不承诺 Retry-After） |
| AT-16 | 增量摘要 | 长历史（>45 天）首次 full → 追加流水 → auto：`build.last_mode=incremental`、`last_rows_written == end - last_rebuild_from + 1`、`last_rows_written < build.rows_count`；owner 读 job.result 断言 `rows_written/rows_total`；与 full 结果逐行相等（IT-01） |
| AT-17 | 修正/撤销 | 改旧流水（version>1）→ auto 触发 full 摘要；结果与 full 相等；无残留旧值 |
| AT-18 | 无数据/无锚点 | 无流水 → 202/succeeded、owner 读 job.result.rows_total=0；无锚点 → `build.last_status=blocked`、无行 |
| AT-19 | 幂等语义 | ① 同 key 第二次 → 同 body/job_id + 重放头；② owner 断言 `expires_at≈created_at+24h`；③ 跨用户同 key 互不影响（**不**断言 24h 后失效，现状无过期过滤） |
| AT-25 | 同 key 异体 | 同 key 不同 body → 422 `IDEMPOTENCY_KEY_REUSED` |
| AT-26 | job/SSE 契约 | 写阶段 progress 事件的 `stage ∈ {recomputing,writing,finalizing}` 且单调；`GET /jobs/{id}` 终态 `status=succeeded`、`stage="done"`（平台 claim=parsing/finish=done 属既有行为，不在 handler 断言内）；`GET /jobs` 不新增 `result` 字段 |

### 9.4 增量正确性（IT）

> 造数约定：软删/恢复/撤销一律走 API-015/019/020 或 ORM `update(Trade)`；**禁止**用 raw SQL 只改 `deleted_at`（不会刷 `updated_at`，水位线/指纹检测不到，用例会假绿）。
| ID | 场景 | 断言 |
|---|---|---|
| IT-01 | 增量 == 全量 | 对同一最终数据状态：路径 A 全量；路径 B 旧数据 build→改数据→auto；比较**行字段**（trade_date/cash/market/total/cost/unrealized/holding_count/cashflow/prev/daily_pnl/daily_return/status/status_reason/definition_version；**排除** rebuilt_at/created_at/updated_at）与 **state 字段**（start_date/last_built_through/last_trade_updated_at/count/version_sum/data_as_of/assumption/last_status/last_fifo_error/status_reason/definition_version/rows_count；**排除 `updated_at/created_at` 与** last_built_at/version/last_mode/last_rebuild_from/last_rows_written）；canary：改坏一行 total 必须失败、只改 rebuilt_at 必须不失败 |
| IT-02 | 最小写入 | 历史跨度 >45 天；两次构建间 `FrozenClock.advance(days=1)`；断言存在 `trade_date < rebuild_from` 的行且其 `rebuilt_at` 不变；`rows_written == end - rebuild_from + 1` |
| IT-03 | no-op | clock/as_of/数据不变连点两次：第二次 `last_mode=noop`、rows_written=0；数据/水位线/指纹/through/rows_count 不变；除 `last_built_at/last_mode/last_rows_written/last_rebuild_from` 外，`version/updated_at` 也会变，断言时排除 |
| IT-04 | 旧流水修正 | 改半年前买入（version>1）→ auto 触发 full，结果与 full 相同；旧日期行不再含该流水 |
| IT-05 | 撤销/软删/恢复 | 撤销批次→auto；恢复行→auto；结果与 full 相同；行数/值回退；恢复后重新出现 |
| IT-06 | 行情前进 | as_of 前进 N 天 → 自动补齐（+安全窗）；状态正确 |
| IT-07 | 与 nav 同值 | 同夹具/时钟/行情：nav 各点 total/cashflow 与 asset 同日相等（status 不比较） |
| IT-08 | 并发/失败原子性 | 写事务异常 → 旧行/旧 state 不变；重试成功；无半截行；状态 CAS 冲突 → VERSION_CONFLICT |
| IT-09 | 版本 bump | 旧行被替换为新 definition_version |
| IT-10 | RLS 直连 | app 未设 `app.user_id` → 两表 0 行；A 会话不能写 B 行 |
| IT-11 | end 回退 | 行情 as_of / FrozenClock 回退 → auto 触发 full、`delete_after` 清残留、rows_count 收敛 |
| IT-12 | 起点后移 | 软删最早流水/撤销最早批次 → full + delete_all，旧日期行清空，rows_count == count(*) |
| IT-13 | 日期改晚 | PATCH 旧流水日期到更晚 → auto full，增量旧行不含该流水，结果 == full |
| IT-14 | 指纹竞态兜底 | 模拟「指纹变化但 updated_at 无变化」→ full（用例可在仓储层构造） |
| IT-15 | 构建摘要 | owner 读 job.result 键齐全；`build` 块 `last_*` 与 job.result 的 mode/rebuild_from/rows_written/built_through 语义一致 |
| IT-16 | worker 行情 | replay 模式 WorkerRunner 可完成 build_assets；live 分支需 akshare（标记 live 用例） |
| IT-17 | as_of 前进修复 degraded | 先在有持仓且 date > as_of 时构建（末段 degraded）→ 推进 as_of（end 不变）→ auto：窗口重算，末段变 ok，结果与 full 相等 |

### 9.5 前端测试（FT）
| ID | 场景 | 断言 |
|---|---|---|
| FT-01 | 首次进入 | 不自动构建；「构建资产表」；空态 + 导入链接 |
| FT-02 | 构建流程 | 调用 `api.assets.build({mode:"auto"}, key)`；渲染 JobProgress；SSE 正常结束后重拉 list；按钮变「更新资产表」 |
| FT-03 | 增量提示 | 从 `build.last_mode/last_rebuild_from/last_rows_written` 渲染增量文案；no-op 文案；stale 提示 |
| FT-04 | 列表展示 | 金额/百分比原样显示；blocked 行「—」+阻断 badge；degraded badge；表头正确 |
| FT-04b | 零值着色 | `"0.00%"` 不着色；正负着色只按首字符/正则，不做算术 |
| FT-05 | 分页 | `has_more` → 「下一页」；请求带 cursor；追加不重 |
| FT-06 | 错误 | 7807 展示 `user_message/what_to_do`；job failed 展示错误码；可重试 |
| FT-07 | 零口径 | 源码扫描 `Assets.tsx` 不含 `Number(`/`parseFloat`/`parseInt`/`toFixed` 及算术（当前仓库尚无零口径 ESLint，P2-15 才启用） |
| FT-08 | 汇总/横幅 | 数据截至/记录数/最近构建/模式/锚点/口径版本；blocked 横幅；degraded 提示 |
| FT-09 | 导航 | 顶部「资产」链接；`/assets` 受登录保护（需导出 `Layout` 或 RouterProvider 测试） |
| FT-10 | 可访问性 | 按钮 disabled/aria；加载态；表格表头可读（不要求 caption） |
| FT-11 | 接管在跑 job | 进入页面发现 pending/running 的 build_assets → 展示进度，不重复点 |
| FT-12 | SSE 失败兜底 | JobProgress SSE 连续失败 → 页面轮询收敛到 succeeded 并刷新列表 |
| FT-13 | 增量刷新 | 构建成功 notice 后列表出现新行/摘要更新 |

### 9.6 契约/迁移/门禁（GT）
| ID | 项 | 通过标准 |
|---|---|---|
| GT-01 | API 契约 | api-snapshot 更新后 `api-check` 全绿（82 端点/82 验收） |
| GT-02 | 端点门禁 | `gate_endpoint_coverage.py` 82 端点、一期 30/30 |
| GT-03 | scope/隔离门禁 | `gate_tenant_coverage.py` 全绿（新端点已进矩阵 + 401 用例） |
| GT-04 | RLS 门禁 | `gate_rls_coverage.py` → review 6/6 |
| GT-05 | 迁移 | upgrade + downgrade + `migrate-check`；schema_version 启动校验 |
| GT-06 | 类型/分层 | mypy strict + import-linter + tsc |
| GT-07 | 契约测试 | 45 operation / `json_count=38` / `text_count=2` / problem 示例齐备 |
| GT-08 | SDK | `make sdk` 后前端类型可编译 |
| GT-09 | 文档 | API-Catalog/DATABASE/DEV-PLAN 与代码一致；§6 两行验收存在 |
| GT-10 | worker 单测 | 假 handler 签名更新后 `test_worker_runner` 全绿 |

### 9.7 性能与确定性目标（人工走查/基准记录，不设易抖门禁）
| 指标 | 目标 |
|---|---|
| `GET /assets?limit=50`（10 年 ≈3650 行） | 本地 P95 < 100ms（UNIQUE 索引） |
| 全量构建（10 年、5000 笔、20 标的） | 本地 < 3s（不含行情网络） |
| 增量日常更新 | 写入 ≤ 45 + 新增天数行；行情只拉 `[rebuild_from-45, end]` |
| no-op | < 300ms |
| 确定性 | 同 clock/数据两次构建行值逐字节一致（排除审计时间列） |

---

## 10. 执行计划（获人工批准后；本次不执行）

### 10.1 文件清单
**后端**
- `backend/packages/review/src/stock_review/models.py`（+2 模型；`REVIEW_TABLES` 4→6）
- `backend/packages/review/src/stock_review/application/valuation.py`（新：共享原语）
- `backend/packages/review/src/stock_review/application/assets.py`（构建/增量/状态机/摘要）
- `backend/packages/review/src/stock_review/repositories/assets.py`（分页/状态/删除/插入/指纹查询/CAS）
- `backend/packages/review/src/stock_review/application/nav.py`（改为导入共享原语，行为不变）
- `backend/packages/ledger/src/stock_ledger/models.py`（+`ix_trade_user_updated_at`）
- `backend/migrations/versions/0006_review_asset_daily.py`
- `backend/services/api/src/stock_api/routers/assets.py`、`schemas.py`、`app.py`、`version.py`、`openapi_meta.py`
- `backend/services/worker/src/stock_worker/runner.py`（懒装配 `MarketDataSource`，`_execute` 传第 5 参）
- `backend/services/worker/src/stock_worker/handlers.py`（`JobHandler` 签名 + `build_assets`）
**文档/门禁**
- `docs/architecture/v2/API-Catalog.md`、`docs/DEV-PLAN-v2.md`、`docs/DATABASE.md`
- `scripts/ci/gate_endpoint_coverage.py`、`contracts/openapi.json`、`frontend/.../schema.d.ts`
**测试**
- `backend/tests/unit/review/test_valuation.py`、`test_assets_application.py`
- `backend/tests/api/test_api_081_assets.py`、`test_api_082_assets_build.py`
- `backend/tests/integration/test_assets_incremental.py`、`test_rls_isolation.py`（补 6 表）
- `backend/tests/isolation/test_isolation_matrix.py`（矩阵补行 + `_unauth_kwargs`）
- `backend/tests/contract/test_openapi_contract.py`（45/38）
- `backend/tests/unit/platform/test_worker_runner.py`（假 handler 签名）
- `backend/tests/conftest.py`（TRUNCATE 补 2 表）
**前端**
- `frontend/packages/api-client/src/http.ts`（`assets` 命名空间）
- `frontend/packages/api-client/src/contract.ts`（类型别名导出）
- `frontend/apps/web/src/pages/Assets.tsx`、`Assets.test.tsx`、`router.tsx`（导出 `Layout` 以便测试）

### 10.2 顺序（契约先行）
1. 文档（API-Catalog §1/§2/§4/§5/§6 + DATABASE/DEV-PLAN）→ 2. 迁移 + ORM → 3. `valuation.py` 抽取 + nav 回归 → 4. `assets.py` + repository → 5. worker 行情装配 + handler → 6. router/schema 契约 → 7. `make api-snapshot`/`make api-check` → 8. 测试（UT/IT/AT/GT）→ 9. 前端 + sdk + FT → 10. 全量门禁。

---

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 与 nav 口径漂移 | 总资产不一致 | 共享 `valuation.py` + IT-07 |
| 增量漏算（并发长事务/日期改晚） | 资产表静默过期 | 变更指纹兜底 + version>1→full + IT-13/14 |
| 行情修正 >45 天或无 end 前进 | 旧行估值残留 | D9：`mode=full` 修复入口；文档化；后续可加行情水位线 |
| 内存全历史重放 | 大账单慢 | 个人数据量级；行情按窗口取；后续断点快照（非本期） |
| 超卖负市值 | 用户困惑 | D8：degraded + 原因；与 nav 一致 |
| blocked 行多 | 列表噪音 | 保留以显式缺口；全局 blocked 不写行 |
| 期初缺买入流水 | 资产偏低 | 与 nav 同限制；文档化 |
| worker live 依赖 | 部署缺 akshare | D11：worker 需与 API 同样安装 live extra；replay 无影响 |
| 契约门禁基数/计数 | CI 红 | §7.3 清单 + GT-01/02/07/10 |
| 前端误算 | 违反零口径 | 后端百分比串 + 源码扫描 FT-07 |
| 历史文档债（WWW-Authenticate、游标绑定、nav 3 位、金额 2/3 位） | 评审歧义 | §11 明示为**本期不修**的既有债，不在本设计内扩大范围 |

---

## 12. 待人工审核确认的问题（请逐项回复）

1. **D1**：接受"每日总资产一行"（而非日期×证券明细）作为资产表吗？
2. **D2**：接受新建 `review.asset_daily`、不改 `nav_daily` 吗？
3. **D4+D11**：接受"异步 job + worker 装配行情源"（需改 worker handler 签名、live 部署需 akshare）吗？还是改同步 200？
4. **D8**：接受超卖日出现**负市值**并标 degraded（不建非负 CHECK）吗？
5. **D9**：接受行情修正的三档时机（end 前进窗口 / as_of 变化窗口，跨度>45 天转 full / 无 as_of 变化需 `mode=full`；UI 不暴露 full）吗？
6. **D10**：接受"任何修正/软删/恢复（version>1）都触发全量重建"（而非精确增量）吗？
7. **D6**：接受 blocked 行数值为**显式 null**（非 0、非缺省）吗？
8. **交互**：`/assets` 新导航页、首次「构建资产表」/之后「更新资产表」、构建摘要来自 `GET /assets` 的 `build` 对象（不扩 `/jobs` 响应）是否符合预期？
9. **D7**：接受 `end = clock.today()`，行情 `as_of` 之后仍有持仓的日期标 degraded（按最近收盘估值、纯现金行不降级）吗？
10. **D12**：接受强制全量 `mode=full` 仅 API 暴露、UI 不暴露吗？
11. **范围**：本期只做资产表与数据契约，回撤指标留待后续（PRD A18）——确认吗？

---

## 附录 A：字段口径对照

| 概念 | `nav_daily` | `asset_daily`（本表） |
|---|---|---|
| 行粒度 | 有流水日 + 期末（按月稀疏） | 每个自然日（全历史连续） |
| 写入触发 | `GET /nav` 同步计算 | `POST /assets/build` 异步 job |
| 总资产 | `total_value_units` | `total_assets_units`（同口径） |
| 现金/市值/成本/浮盈 | 不落库 | 落库 |
| 日盈亏/收益率 | 响应期算 | 落库（+ API pct） |
| 状态 | ok/blocked/degraded | 同左，blocked API 数值 null |
| 增量 | 无 | 指纹 + 水位线 + 45 天安全窗 |
| 用途 | TWR/MWR | 资产变化 + 回撤族数据源 |

## 附录 B：迁移 DDL 草案

```python
# backend/migrations/versions/0006_review_asset_daily.py（草案）
revision = "0006"; down_revision = "0005"
SCHEMA_VERSION = "0006_review_asset_daily"; PREVIOUS_SCHEMA_VERSION = "0005_market_cache"
RLS_TABLES = ("asset_daily", "asset_build_state")

# asset_daily: id/user_id/trade_date/cash_units/market_value_units/total_assets_units/
#   holding_cost_units/unrealized_pnl_units/holding_count/cashflow_units/
#   prev_total_assets_units(NULL)/daily_pnl_units(NULL)/daily_return_units(NULL)/
#   status/status_reason/definition_version/rebuilt_at/created_at/updated_at
#   PK(id), UNIQUE(user_id,trade_date), CHECK(total=cash+market), CHECK(holding_count>=0),
#   FK user CASCADE；不建 market_value>=0 CHECK；不重复建 (user_id,trade_date) 普通索引
# asset_build_state: user_id PK, start_date, last_built_through, last_built_at,
#   last_trade_updated_at, last_trade_count, last_trade_version_sum, data_as_of,
#   assumption, last_status, last_fifo_error, status_reason, last_mode, last_rebuild_from,
#   last_rows_written, definition_version, rows_count, version, created_at, updated_at
# 另：CREATE INDEX ix_trade_user_updated_at ON ledger.trade (user_id, updated_at, trade_date)
# RLS tenant_isolation + stock_app DML / stock_backup SELECT + schema_version stamp
```

## 附录 C：验收断言与夹具映射

| 断言 | 数据 | 依赖 |
|---|---|---|
| 06-30 总资产 100620.00、pnl 620.00、收益 0.62% | 05-29 余额 100000；06-30 买 100@1700、余额 -70000；600519 06-30 收盘 1706.20（`quotes.json`，真实存在） | FrozenClock ≥ 2026-07-01；replay |
| back_solved 锚点 | 首行即买入、余额 -70000、amount -170000 | UT-11/IT-07 |
| 缺行情阻断 | 持有 `999999`（夹具无此标的） | UT-09/AT-08（API 层） |
| 全局阻断 | 全部 `cash_balance=NULL` | UT-10/AT-10/AT-18 |
| 增量等价 | 新流水/回填/改日期/撤销 + 两次构建 | IT-01/04/05/13/14 |
| 跨用户隔离 | A/B 各 seed 资产行 | AT-03/IT-10/GT-03 |
| nav 同值 | nav 06 月结果 vs 资产表同日 | IT-07 |
| 长历史增量 | start ≤ end-46 天（如 2026-01-01 起） | IT-02/AT-16 |

---

> **下一步（人工审核通过前不执行）**：交叉审核记录见 `docs/reviews/2026-10-25-DevDesign-Review-asset-daily-table.md`。批准后按 §10 实施，并补实现日志与验收报告。
