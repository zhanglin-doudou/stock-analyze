# 05/06 指标引擎与端点设计 · 独立评审 #2（DB/引擎管线/缓存/测试可执行性）

> 对象：`docs/architecture/2026-10-26-metrics-engine-endpoints.md` v1.0（383 行）。只读评审：未改设计/代码/库，未起服务；仅 grep/read + 迁移与门禁脚本核对。
> 依据：三域设计 §4/§5/§6/§9/§10、Extensibility §3/§11.1、Architecture-v2 §11、Testing §12、迁移 0006、`stock_review/models.py`、`conftest.py`、`Makefile`、CI 门禁脚本。

**裁决：REQUEST_CHANGES**
总评：包结构/管线/端点骨架方向正确、与三域 §4 冻结契约基本对齐，但**物化唯一键与数据集指纹两处设计性缺陷会导致"缓存返回错值 + 历史丢失"**，RLS 谓词、CHECK、门禁可执行性亦与既有代码不一致；按下列条目修正后可放行，无需重写设计。
**PASS/FAIL：1 PASS / 8 FAIL**（9 项必核）；发现 **3×P0 / 7×P1 / 5×P2**。

## 1. 逐条核对（9 项必核）

| # | 核对项 | 结论 | 关键指向 |
|---|---|---|---|
| 1 | DB 设计（键/id/CHECK/RLS/FK/配套） | **FAIL** | EE-P0-1/3、P1-1/2/3 |
| 2 | fingerprint（E-04） | **FAIL** | EE-P0-2、P1-4 |
| 3 | read-through 缓存（E-01） | **FAIL** | EE-P0-1/2、P1-2/4 |
| 4 | 状态机 §6.2 vs 07 四态矩阵 | **FAIL** | EE-P1-4 |
| 5 | 事务/分层（会话外计算、L2~L4） | **PASS** | 仅 EE-P2-3 一处待补 |
| 6 | 测试方案 §9 可执行性 | **FAIL** | EE-P1-5、P2-2/5 |
| 7 | 估算/范围（05=3d、06=2d、S0 5~7d） | **FAIL** | EE-P1-6 |
| 8 | 07 范围边界（positions_end/双写/drilldown） | **FAIL** | EE-P1-7、P2-3 |
| 9 | 事实错误（行号/命令/常量/表数/口径） | **FAIL** | EE-P0-2、P1-2、P2-1/4 |

## 2. 发现

### P0

**EE-P0-1｜唯一键/确定性主键缺 `benchmark`、`params`、`rf`、`as_of`：切基准覆盖同版本行，历史与趋势不可复现**（文档 L16、L18、L98、L123）
- 证据：`E-04` 明写 `inputs_hash` 含 `benchmark/rf_annual/domain_params`（L18），但唯一键只有 `(user_id,metric_id,period,definition_version)`（L123）、`id` 同（L98）→ 同 period 不同基准/参数只能 UPSERT 同一行（last-writer-wins）；`docs/DEV-PLAN-v2.md:179` 规定 Period2-11「切换基准即触发失效重算」，基准是用户可变操作；三域 §6.2 `:333` 要求 hash 含 `lookback/as_of/benchmark_id`。
- 建议 + 影响：唯一键与 id 改 `(user_id, metric_id, period, definition_version, benchmark_id, params_hash)`（`rf_annual` 至少进 hash+assumptions；`as_of` 前进需明确"保留最新一行"）。否则 `/trends`（Period3-02）与报告跨期混用不同基准数字，E-02「旧行保留供趋势断点」对基准/参数变更不成立（直接覆盖），并发双基准请求互相覆盖。

**EE-P0-2｜`bars`/`benchmark` 视图不可能用"行数+max(updated_at)+max(id)"做指纹，且未纳入 `as_of`/`provenance`/`sha256`**（文档 L18、L194、L320）
- 证据：replay 行情是文件源、不落 DB（`backend/packages/ledger/src/stock_ledger/market.py:213-236`、`:307` 读 `fixtures/market/quotes.json`），端口已有 `as_of`（`:231`）与 `provenance = replay:<name>#sha256:<16>`（`:192-194`）；`market.daily_bar` 有 `updated_at`（0005:`100`）但 replay 不写它 → L194"各视图自查 updated_at"对 bars/benchmark **不可执行**，换夹具（07 的 `daily_bars.json`、05 的 `bars_mini.json`）时 DB 指纹恒不变。
- 建议 + 影响：bars/benchmark 指纹 = `(as_of, provenance, bar_count, max(trade_date))`，且 `market.as_of` 必须进 `inputs_hash`（三域 §6.2）。否则夹具替换/行情修正后 read-through 仍命中旧行 → **返回错值**；若 blocked/unreliable 行也落库（P1-4），行情恢复后仍永久 blocked。

**EE-P0-3｜`metric_dependency` 主键"随父行确定性"未含 `dep_id`：多依赖指标首写即 PK 冲突；且无 `definition_version` 却按 `(user,metric,period)` 先删后插，与 E-02 冲突**（文档 L132-134、L146、L142）
- 证据：三域 §4.1 冻结示例 `dependencies = (DatasetDep("rounds"), DatasetDep("positions_end"))`（`:183`）——一个指标 ≥2 条依赖，而 id 只由父行（user|metric|period）派生 → 第二条 INSERT 撞 PK；按 `(user_id,metric_id,period)` 删除会把**所有版本**血缘一起删掉。表无 `definition_version`、无 `user_id` FK/CASCADE。
- 建议 + 影响：id 含 `dep_kind|dep_id`（并含 version），删除键加 version，补 `FK(user_id)→platform.user ON DELETE CASCADE`。否则 05 的单依赖测试 def 不暴露、07 接入第一个双依赖指标（win_rate）即炸；版本 bump 后旧版本血缘被抹，"谁消费了 X" 反查与审计失真。

### P1

**EE-P1-1｜CHECK 只拦 `status`，拦不住 07 四态矩阵**（文档 L107 自称"status≠ok 或 pending_window 时必须 NULL（CHECK）"，实际 L124 仅 `status='ok' OR value_units IS NULL`）
- 证据：`ok+pending_window+value_units 非空` 可通过 CHECK；`n>=0 / n_pending>=0 / ci_low<=ci_high / period 与 version 格式` 全部无 CHECK（L108-126）。`ci` 成对（L125）与 `insufficient 可 null` 是唯一被正确覆盖的两格。
- 建议 + 影响：补 `CHECK (NOT (sample_status='pending_window' AND value_units IS NOT NULL))`、`CHECK (n>=0 AND n_pending>=0)`、`CHECK (ci_low_units IS NULL OR ci_low_units<=ci_high_units)`、period/version 正则。否则库层无第二道防线，矩阵错误只靠应用层自觉，07 的 `assert_sample_gate` 无法用 DB 反证。

**EE-P1-2｜RLS 谓词与 0006 不一致（设计自称"同款"）**（文档 L147）
- 证据：0006 为 `user_id = NULLIF(current_setting('app.user_id', true), '')::uuid`（`backend/migrations/versions/0006_review_asset_daily.py:47`），设计漏 `true` 与 `NULLIF`。
- 建议 + 影响：复用 0006:47 的 `TENANT_PREDICATE`（含 `WITH CHECK`）。否则未 `SET LOCAL` 时旧表返回 0 行（fail-closed），照设计写法抛 `unrecognized configuration parameter`(42704)，设计 §9.1 自己的"未设 app.user_id 读 0 行"用例会红。

**EE-P1-3｜`data_as_of NOT NULL` 与"新用户无 asset_build 行"冲突 → blocked 行无法落库**（文档 L118、L204）
- 证据：`asset_build_state.data_as_of` 可空且每用户一行（0006:`107-148`，`:117` nullable）；新用户/未构建资产时该行不存在，而 L204 的 `min(today, market.as_of, asset_build.data_as_of)` 无值，L215 仍要 UPSERT blocked 行。
- 建议 + 影响：缺省 fallback `min(clock.today, market.as_of)` 并写 `assumptions.pit_rule`，或该列 nullable + `CHECK(status='blocked' OR data_as_of IS NOT NULL)`。否则首次访问 `/metrics`（未构建资产）→ IntegrityError → 500，而 07 矩阵该场景应为 200 + stale/blocked。

**EE-P1-4｜"DatasetDep 数据为空/缺失 → blocked"与 07「区间无样本 n=0 → insufficient（value 存在但 null）」冲突；负缓存策略未定义**（文档 L231、L233、L210-215）
- 证据：三域 §6.3 `:350` 与 §4.2 `:228` 明确 n=0 → `insufficient`+value=null，`blocked` 只用于数据集不可用/未构建/基准缺失（`:343-348`）；L231 把"为空"与"缺失"并列。缓存：L210 只写"命中=inputs_hash 一致"，未定义 blocked/unreliable 行是否落库、是否参与命中。
- 建议 + 影响：区分"视图不可用/未构建"（blocked）与"窗口内 0 样本"（insufficient）；明确"仅 status=ok 可命中"或 blocked 行不写 `inputs_hash`/带 TTL。否则新用户 0 回合被判 blocked（违约 D-08 与 API-033 断言③），叠加 P0-2 后永久不可恢复。

**EE-P1-5｜§9 不可执行：新增 4 端点必使 `gate_tenant_coverage` 红；且缺 6 类必测用例**（文档 L314、L308-315）
- 证据：`scripts/ci/gate_tenant_coverage.py:2-10,23` 从运行时 openapi 枚举**全部受保护 operation**，必须出现在 `backend/tests/isolation/test_isolation_matrix.py` 的 `RESOURCE_MATRIX/UNAUTHENTICATED_MATRIX`，带路径参数者必须有跨租户 404——而设计把越权用例放 `backend/tests/api/test_metrics.py`；该 gate 在 `Makefile:162-172`（test-p2）与 `cli.py:442-500`（verify-p1）都会跑。§9 未覆盖：read-through 命中/未命中/失效三态、fingerprint 变更矩阵（R-4 只有断言无测试）、并发 UPSERT、params/benchmark 隔离、as_of 前进、软删恢复。
- 建议 + 影响：`test_isolation_matrix.py` 补 401 参数化 + `ADMIN_FORBIDDEN`；`/metrics/{metric_id}` 无跨租户资源 ID 语义 → 在 `EXEMPT` 写理由；§9 增"read-through/指纹/并发矩阵"行。否则 M-7 后 `make verify-p1`/`test-p2` 必红，缓存失效无回归网。

**EE-P1-6｜无任何估时表，无法证明落在 05=3d / 06=2d 或已批准的 5~7d 内**（文档 L336-352）
- 证据：`docs/DEV-PLAN-v2.md:173-174` 05=3d、06=2d；三域 §10 `:904` S0=5~7d（已含迁移/RLS/多视图/ci_sample 交接）。本设计新增 stats 子集（M-4）、8 视图+C-28 replay 双源（M-5）、迁移 0007+RLS+conftest+DATABASE.md（M-2）、契约 PR（M-1）、read-through+血缘（M-6），M-1~M-8 无一人日。
- 建议 + 影响：补逐步骤人日与合计，标明哪些从 S0 信封吸收、哪些新增，超 7d 提 HITL（§12 现只列 E-*，无排期项）。否则无法判断 05 的 3d 是否被 stats+views+迁移挤爆。

**EE-P1-7｜07 冻结契约要的 `positions_end` 视图在 views Protocol 缺席**（文档 L180-181）
- 证据：三域 §4.1 `:183` 冻结示例依赖 `DatasetDep("positions_end")`，D-13 `:39` 明写"需 05 提供 `positions_end` 视图字段"；设计只留 `holdings_daily` 占位。
- 建议 + 影响：views Protocol 增 `positions_end`（与 §4.1 字段名一致），或明确"07 自带构建器"并删占位。否则 07 写 defs 时 import 不到 → 只能改引擎，破坏 X-1"新增指标零改既有文件"。

### P2

**EE-P2-1｜契约漏项：`domain` 无 `perf`、API-033 无 ETag/304 条目、`params` query 未入 Catalog**（文档 L248、L203、§8 表）
- 证据 `docs/architecture/v2/API-Catalog.md:311` `domain(selection|timing|behavior|all)`（无 perf）、`:310-315` 无 ETag/304；`stock_api/routing.py:41-46` 未知 query → 400。建议 §8 增 3 条 C 条目（domain 加 perf；API-033 ETag/304；`params` 入 Catalog 或移除）。影响：契约漂移 + 严格 query 白名单 400，SDK 无法生成参数。

**EE-P2-2｜测试夹具路径与 07 约定冲突**（文档 L320）
- 证据：根夹具目录是 `fixtures/`（`platform/config.py:73` `STOCK_FIXTURES_DIR=REPO_ROOT/fixtures`），07 约定 `backend/tests/fixtures/metrics/<id>/`+`MANIFEST.json` sha256（三域 §8.3）；`backend/tests/fixtures/` 现不存在，`tests/fixtures/bars_mini.json` 无宿主。建议改 `backend/tests/fixtures/metrics/fx_bars_mini/`（含 MANIFEST），经 `ReplayMarketDataSource(path)` 注入。

**EE-P2-3｜`MetricValue` 放 review，而 07 的 `core/metrics` 在 core 层，L2 禁止 core→review**（文档 L165）
- 证据：`pyproject.toml:115-119`（L2 core forbidden → review）；三域 §10 S3 的 `core/metrics` 公式返回 `MetricValue`。建议 §4/§5 明写"def 层用 MetricValue，core 纯函数返回 `(value_units, n, decomposition)`"，避免 07 二选一返工。

**EE-P2-4｜`stale` 语义漂移未登记：read-through 使 stale 基本不可见，与 Extensibility §3.3 / Architecture §11 / 05 验收原文冲突**（文档 L216-217、L311）
- 证据：Extensibility `:132`（stale → 返回旧值+投递 job）、Architecture-v2 `:630`、`DEV-PLAN-v2.md:173`（bump → 旧结果 100% stale）。建议把该验收改判为"`persist.is_stale` 单测 + 新版本行插入 + 旧行保留"并作为 C 条目（C-31 只统一了 value 语义）。

**EE-P2-5｜迁移 up/down 无执行宿主；新 target 名与 C-25 的 `test-metrics` 不一致**（文档 L308、L311、L327）
- 证据：`backend/tests/` 无 downgrade 测试（grep 无命中），`Makefile:74-77` `migrate` 只 upgrade；三域 C-25 `:885` 命名 `test-metrics`，本设计用 `test-engine`（`make test-engine` 目前不存在，属新增、非误引）。建议 `make test-engine` 内含 `alembic downgrade 0006 && upgrade head` 幂等步骤，target 名并入 C-25 一处定义。

## 3. 已核实（正向）

1. `make migrate`/`make migrate-check` 均存在（`Makefile:74-82`），设计未误引；`make test-engine` 确为新增。
2. `SCHEMA_VERSION="0006_review_asset_daily"`（`stock_api/version.py:5`），L150 的 bump+同步要求正确。
3. `conftest._REVIEW_TABLES` 6 表（`conftest.py:60-67`），`test_rls_isolation.py:126` 另有同名副本 → L148 要求两处同步，方向正确。
4. `RoundFill` 无 `updated_at`（`models.py:101-130`，仅 `BigInteger` 自增 id）→ L194 的"各视图 max(updated_at)"对其不成立；`Round` 有 `rebuilt_at`+`updated_at`（`:92/:96`），`asset_daily`/`nav_daily`/`market.daily_bar` 均有 `updated_at`。
5. `Trade` 有 `updated_at onupdate`+`version`+`deleted_at`（`models.py:133-147`）→ PATCH/软删/恢复确实改指纹，R-4 对 trades 成立。
6. 错误码齐备 `METRIC_NOT_FOUND/INVALID_PERIOD/ADMIN_CANNOT_READ_TRADES`（`errors.py:24,29,53`）→ "不新增错误码"可满足；常量引用准确（`constants.py:21-24`、`enums.py:62-80`）。
7. `RoundRepository` 确无 `in_period`（`repositories/rounds.py` 仅 `by_id/fills/page/count/delete_*/insert_batch`）→ C-22 缺口真实。
8. 端点门禁常量 P1=30、全量=82（`gate_endpoint_coverage.py:26-28`），新增 4 端点不触发其断言；但 C-27（扩展 phase2 / ≥3 用例）在 §8 未登记。
9. 性能口径：三域 §8.10 `:828` 的 ≤300ms 是"批量优先命中物化"预算，冷启动预算 ≤2s（同条③）；L15 把 300ms 当首算预算属口径误引。
