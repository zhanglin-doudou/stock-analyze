# 指标引擎与指标端点（Period2-05 / Period2-06）设计与测试方案

> 状态：**v1.1 — 已独立评审并修订（评审记录见 [2026-10-26-DevDesign-Review-metrics-engine-endpoints.md](../reviews/2026-10-26-DevDesign-Review-metrics-engine-endpoints.md)）；待人工确认 E-17 估时差异后进入实现**
> 日期：2026-10-26 ｜ 作者：AI（Team Lead + 2 名独立评审）
> 需求来源：[DEV-PLAN §3.2](../DEV-PLAN-v2.md) Period2-05「指标注册表与执行引擎」+ Period2-06「指标端点 + 三段式」
> 上游契约（**已批准，本设计不得违背**）：[三域指标设计 §4 冻结契约](2026-10-26-metrics-three-domain.md)、§6.3 状态传播矩阵、§9 契约修订清单 C-01~C-34、§13.4 人工决策记录（B=1000 / rf=1.5% / DE=Odean / 日线夹具 / 验收 period=2026-06）
> 关系说明：三域设计解决"算什么"（18 个指标口径）；本设计解决"怎么算、怎么存、怎么查"（引擎框架、物化、端点）。重叠处一律以三域设计为准；本设计对 07 契约的**增量修订**集中列在 §14（需同步更新 07 文档）。

---

## 0. 决策摘要（人工评审请重点拍板）

| # | 决策点 | 推荐方案 | 备选 | 影响 |
|---|---|---|---|---|
| **E-01** | 计算触发方式：API-033 是同步端点，物化表何时写入 | **read-through 缓存**：GET 先按 `(user, metric, period, version, params_hash)` 查物化行；`inputs_hash` 命中且 `status=ok` 直接返回；未命中同步计算 + UPSERT。不做预计算 job。**GET 的写副作用与 `/reconciliation` 同类，需在 Catalog 明写（C-36）**；写失败不阻断读（记日志，返回本次计算结果） | ① worker 预计算 job（未算过的指标只能 blocked，首次体验差、多任务链）；② 纯实时不落库（trends/报告无历史） | 首次请求承担计算耗时；缓存正确性靠 §6.4 指纹 |
| **E-02** | 物化唯一键与口径/参数版本保留 | 唯一键 **`(user_id, metric_id, period, definition_version, params_hash)`**；`params_hash=sha256(canonical(benchmark, rf_annual, def params))`。口径 bump 或 benchmark/rf 变化 → 新行插入，旧行保留（供 Period3-02 趋势断点/多基准对比） | 唯一键不含 `params_hash`：切换 benchmark 时 UPSERT 覆盖旧行，切换回来只能重算且历史丢失、无法多基准对比 | 行数 = 指标×月数×版本×参数组合，个人量级可接受 |
| **E-03** | `metric_dependency` 表语义 | **血缘记录表**：物化成功时写本次实际消费的依赖（指标/数据集 + 指纹 + `definition_version`），仅诊断/审计；失效判定不查此表（运行时 DAG 足够）。删除/覆盖按 `(user, metric, period, definition_version)` 粒度，**不得跨版本删** | 持久化 DAG 边做失效传播：defs 是静态代码，运行时 DAG 已足够，持久化边会漂移 | 与 E-02 的"旧行保留"一致 |
| **E-04** | `inputs_hash` 构成与数据集指纹 | `sha256:<64hex>`（71 字符）。构成：`metric_id, definition_version, engine_version, params_hash, period_start/end, as_of, benchmark_id, rf_annual, 各 DatasetDep 指纹, 各 MetricDep.inputs_hash`。**每个视图有确定指纹**（§6.4 表）：DB 表用 `count + max(updated_at) + max(id)`；`round_fill` 无 `updated_at` → 用 `count + max(id) + round_build_state.last_rebuilt_at`；**bars/benchmark 必须含 `provenance + file_sha256 + market.as_of`（文件型 replay）或 `market.daily_bar` 指纹（DB 型 live）** | 内容全量哈希：精度最高但每次 GET 扫全表，p95 不可行 | 数据修正（PATCH/撤销/软删/对账补差）均改变指纹 → 自动 miss |
| **E-05** | `core/stats` 归属（05 与 07 的边界） | **05 交付**：`numeric.py / wilson.py / bootstrap.py / estimators/`（estimator 注册表 + `mean`）；**07 交付**：`regression.py / concentration.py / drawdown.py / tstat.py` + 新增 estimator 模块（`ratio_diff_pgr_plr`/`beta_alpha` 等）。**estimator 走包内自动发现（`core/stats/estimators/*.py`，放文件即注册）**，07 加统计量不改 05 既有文件 | stats 全部归 05：05 膨胀 2 人日且大部分函数在 06/07 联调前无消费者；stats 全部归 07：05 引擎无法算 CI、契约 kit 无法驱动 | 需同步修订 07 §5.1/§10 S1（§14） |
| **E-06** | 数据视图装配范围 | **05 交付视图接口 + 装配 + replay 月频最小实现 + C-28（`MARKET_BARS_FILE` 配置 + 双源能力声明）**；07 的 ≥2 年日线夹具只换文件。**`holdings_daily`/`positions_end` 由 07 在 def 内基于 `trades + bars` 重建**（D-13），05 只提供 `trades/bars/asset_daily` 视图与重建所需的纯数据，不把指标口径放进引擎 | 05 实现持仓重建：把 07 口径搬进引擎，违反"引擎不做指标口径"；留空壳到 07：06 端点无法覆盖 bars 型参数路由 | 07 落夹具文件后 BarsView 零改动（X-1 判据） |
| **E-07** | scope 粒度 | **M1 只支持 `period` 粒度=月（`YYYY-MM`）**；`ScopeSpec.unit` 仍接受 07 的 `round/position/day/trade` 作为**统计单元元数据**（仅存储/透出，不影响引擎路由）。其余 period 粒度（周/季/年/自定义区间）在解析层抛 `VALIDATION_ERROR`（占位） | 把 unit 当 scope 拒绝：07 的 `perf.win_rate` scope.unit=round 会被误拒（初稿缺陷）；一次实现多 period 粒度：过度设计 | 07 全部指标 month period；unit 元数据透传 |
| **E-08** | ETag 策略（API-033/079） | **弱 ETag**：`W/"<sha16>"`（内容哈希；`routers/trades.py:_if_none_match_hit` 已忽略 `W/` 前缀，可直接复用），指纹覆盖 **查询参数（ids/domain/period/from/to/benchmark/include_exploratory）+ registry_version + 每个 item 的 `(metric_id, definition_version, params_hash, inputs_hash, status)` 排序拼接**；`If-None-Match` 命中 → 304 空 body；explain/drilldown 不出 ETag | 强 ETag / version 整数：与 Catalog §0.8 的 `W/"<version>"` 句式不同 | 复用 `routers/trades.py` 的 If-None-Match 模式（已支持弱前缀） |
| **E-09** | query 校验/错误语义 | **`ids`**：逗号分隔、trim、去重、最多 50；空/全空白 → `422 VALIDATION_ERROR`；未知或格式非法 → `404 METRIC_NOT_FOUND`；`ids` 与 `domain` 同传 → `422 VALIDATION_ERROR`。**`domain`** 非法 → `422 VALIDATION_ERROR`。**`from/to`** 仅支持"同一自然月"（半开 `[from,to)`），跨月或只给一端 → `422 INVALID_PERIOD` 并给出 what_to_do。**`benchmark`** 未知 → `422 UNKNOWN_BENCHMARK`。默认 `period` 与 `from/to` 二选一，同传 → 422 | 部分返回 + warnings：改 Catalog 契约，破坏 additive | 与 Catalog 断言/错误码对齐，契约增量见 C-35/C-38 |
| **E-10** | 测试 def 与 07 真实 def 的隔离 | **测试 def 放 `backend/tests/support/metric_defs/`，只在测试构造 registry 时通过 `build_registry(extra_paths=[...])` 注入；生产 `registry` 只扫 `stock_review/metrics/defs/`**。测试 def 用 `perf.win_rate_stub` 等独立 id，与 07 的 `perf.win_rate` 不冲突，07 合并时无需删除生产代码 | 放在生产 defs/ 包内靠"07 删除"：05/06 合并期间 `/metrics` 会向用户暴露 stub 指标，API 验收与真实指标混淆 | 测试可独立跑，生产 registry 干净 |
| **E-11** | **negative caching**：`blocked/unreliable` 是否落库 | **只物化 `status=ok` 的结果**（含 `insufficient/pending_window`）；`blocked/unreliable` **不落库**（同请求内可复用，用于一次批量请求的多指标共享），下次请求重算。这样行情恢复/断链修复后不会出现"永久 blocked 缓存"；代价是故障期间每次请求重复计算（失败快，可接受） | 一律落库 + TTL：需要缓存失效任务/Redis；TTL 内仍可能返回过期 blocked | 正确性优先；R-3 性能预算只按"可算"路径测 |
| **E-12** | **drilldown 明细来源** | 在 def 中增加纯函数 `DrilldownSpec.build_items(ctx, filters) -> Sequence[DrilldownItem]`（engine 只负责排序/分页/校验/格式化，不拼口径）。`DrilldownItem(key, value_units, unit, label, round_id?, trade_id?, event_date?)`；items 上限 5000，超出返回 `truncated=true` 并在 assumptions 披露。**不能用 `metric_result.decomposition` 反推明细**（decomposition 只有聚合值） | 引擎按指标硬编码明细查询：18 指标 × 引擎分支，违反"加指标不改引擎"；持久化 detail 表：写放大且 07 无 golden | 07 每个 def 必须实现 build_items（§14 同步） |
| **E-13** | **GET 的 `stale` 可达性** | **`stale` 只在"上游数据状态过期"时对返回出**（`asset_build.state=stale`、`market/data_as_of` 滞后）→ `status=stale`、**无 value**、reason=`ASSET_BUILD_STALE`/`MARKET_ASOF_LAG`，**不读缓存、不重算、不落库**；**口径 `definition_version` bump 不走 stale**：read-through 直接算新版本行并返回 `ok`（旧行保留供 trends）。`/metrics` 的 gate 优先级：`blocked > stale > unreliable > ok` | 把 bump 当 stale（07 §6.3 现文）：要么返回旧值（违反非 ok 无 value），要么与 E-02 新版本行矛盾；把 build stale 当 blocked：07 §6.3 明确 stale 语义 | 需同步 07 §6.3 的两行（§14 S-7） |
| **E-14** | **顶层响应字段语义**（MetricResult 之外） | 顶层 `period`、`data_as_of`、`definition_version`、`assumptions`：`definition_version` = 所有 item 版本一致时取该值，否则 `"mixed"`；`assumptions` = 本次请求的**公共假设**（`benchmark_id` 若请求/指标使用、`rf_rate`、`A:244`、`engine_version`），逐 item 的完整 assumptions 仍在 item 内 | 顶层省略定义：不同客户端实现不一致；把所有 item assumptions 合并到顶层：字段语义混乱 | C-35 修订 Catalog API-033/079 示例 |
| **E-15** | **`ci_sample` 形状**（07 C-19 的细化） | `ci_sample: tuple[tuple[int, ...], ...] | None`——**每个观测是一个 int 元组**（DE 的观测=(code,)，beta 的观测=(r_p,r_b)，BHAR=(bhar,)），升序排序（按元组字典序）、仅内存、不落库、观测数上限 2000。`ci_stat_id` 指向 estimator（`mean`/`ratio_diff_pgr_plr`/`beta_alpha_mean`…），estimator 接收 `Sequence[tuple[int,...]]` | 单层 `tuple[int,...]`：无法表达成对 bootstrap（beta/drawdown），07 无法交付 CI | 需同步修订 07 §4.1/§5.3/C-19（§14） |
| **E-16** | **`metric_result.inputs_hash` 列宽/格式** | 统一 `sha256:<64hex>`（71 字符）→ `String(80)`；`params_hash` 用 64 位 hex；`dep_hash` 同 80；API 原样输出 `inputs_hash`（Catalog 示例即 `sha256:…`） | 只存 64 hex：API 需二次加工，且与 Catalog 示例不一致 | 小改，避免 64/71 混用 |
| **E-17** | **估时与排期**（05+06 是否需要超过已批准的 5~7d） | 按本设计细化：05 ≈ 6.5d、06 ≈ 3.0d，合计 **9.5d**（不含 07）；比三域设计 D-01 批准的 5~7d 多 2.5~4.5d。原因：迁移+RLS+四视图+read-through+stats 子集+drilldown builder+契约 PR。**需人工拍板**：① 接受 9.5d 并修正 DEV-PLAN/07 S0；② 把 C-28（0.5d）+ drilldown builder（1d）挪到 07/07b，05+06 ≈ 8d；③ 砍 stub/部分端点验收，05+06 压到 7d | 强制 5~7d：只能砍测试/契约联调，验收不完整 | 人工决策 E-17；影响 07 S0 与总排期 |
| **E-18** | **API-038 handler 归属**（跨文档修正） | 按 07 D-17：**API-038 的 `diagnostics_selection` handler 归 Period2-06 端点补齐**（不是 07）。05/06 本期只做 033/034/035/079；API-038 在 06 收尾前实现 handler 与 202 流程 | 归 07：07 只做 defs/golden，不应背 job handler | 修正 05 初稿"API-038 → 07"的错位（07 D-17 原文为 06/07b） |

> E-01/E-02/E-05/E-11/E-12/E-15/E-17 影响数据、契约与排期，请人工逐项确认；其余为工程决策（可委托 Lead 抽查）。**本设计不含 18 指标口径、golden、oracle、mutation**（07 范围）。

---

## 1. 背景与现状

### 1.1 上游已就绪

| 事实 | 证据 |
|---|---|
| 回合/净值/资产表三条数据链已落库并可重建 | `review.round/round_fill/round_build_state`（迁移 0003）、`nav_daily`（0004）、`asset_daily/asset_build_state`（0006） |
| 契约枚举已备：`MetricStatus/SampleStatus/ScopeType/JobType.DIAGNOSTICS_SELECTION` | `backend/packages/contracts/src/stock_contracts/enums.py` |
| 常量已有 `MIN_CONCLUSION_SAMPLE=10`、`MIN_CI_SAMPLE=10`、`A_SHUFFLE_DAYS=244`；错误码已有 `METRIC_NOT_FOUND/INVALID_PERIOD/UNKNOWN_BENCHMARK/ADMIN_CANNOT_READ_TRADES` | `contracts/constants.py:21-24`、`contracts/errors.py` |
| 端点骨架模式（StrictQueryRoute、游标、ETag/304、ApiError）成熟 | `routers/trades.py`、`routers/assets.py`、`routers/nav.py`、`routing.py` |
| 行情端口 `MarketDataSource`（replay 月频 / live 日线不复权）与 `as_of` 门禁已实现 | Period2-04 |
| 三域设计已批准：引擎契约 §4.1、端点契约 §4.2、四态 value 矩阵、前置清单 §4.3 P1~P5 | `2026-10-26-metrics-three-domain.md` |

### 1.2 缺口（本设计要补齐）

1. 无 `review/metrics` 包：无 `MetricDef` Protocol、注册表、`MetricContext` 视图、引擎、estimator 注册表。
2. 无 `review.metric_result/metric_dependency` 表（迁移止于 0006）。
3. 无 `/metrics` 系列路由（API-033/034/035/079 未实现）。
4. 常量缺 `Z_95/MIN_TRADING_DAYS/RF_ANNUAL_DEFAULT`（C-17）；config 缺 `rf_annual`（C-24）。
5. Extensibility 三处待同步：`SamplePolicy.ci` 加 `none`（C-18）、`MetricValue.ci_sample/ci_stat_id`（C-19 细化，E-15）、`ci_min_n=30→10`（C-16）。
6. replay 行情无日线配置路径（C-28）。
7. `RoundRepository` 缺 `in_period()`（C-22）。
8. **drilldown 明细接口缺失**（E-12）——初稿误以为 `decomposition + 仓储查询`足够，评审已推翻。

### 1.3 目标（可验收）

1. def **放文件即被发现**（pkgutil 只扫自有包）；注册表校验 id 正则/唯一/deps 深度≤3/无环；**新增指标零改既有文件**（X-1 判据）。
2. 引擎框架代算：`sample_status`、CI（wilson/bootstrap/none + estimator 派发）、`status`（状态机 + 上游传播）、`assumptions`、`inputs_hash`、`data_as_of`、`computed_at`；`compute` 纯函数（禁 I/O/时钟/float）。
3. 物化 read-through：命中即读、未命中即算即存；**命中条件 = 同唯一键 + inputs_hash 相等 + status=ok**；口径/参数 bump 后旧行保留、新行插入；同输入两次 `inputs_hash` 相同。
4. 4 个端点上线并满足 Catalog §2.3 断言与 07 §4.2 四态矩阵；`blocked/stale/unreliable` 无 value 键；`/explain` 无裸术语；`/drilldown` 每项可回查。
5. 迁移 0007 up/down 幂等；新表 RLS 全绿；`conftest._REVIEW_TABLES` 更新。
6. `make test-engine` 门禁可跑；api-snapshot/sdk 同步；门禁命令全绿。

### 1.4 非目标（范围围栏）

- **18 个真实指标 def、golden、oracle、mutation、手算表** → Period2-07。
- API-038 `diagnostics_selection` handler → **Period2-06 收尾补齐**（E-18；07 D-17）。
- 前端看板/下钻抽屉 → Period2-12；`/trends` → Period3-02（本设计只做版本保留策略）；`/attribution`/报告/告警 → 其它期次。
- 既有 `/nav`、`/assets`、`/rounds` 响应与口径零改动（只读消费）。

---

## 2. 关键约束

| 约束 | 要求 |
|---|---|
| 禁止 float | `value_units/ci_*/n/pending` 全为标度整数；`decomposition/assumptions` 的 JSONB 值只能是 int 或定点字符串；序列化前 `assert_no_float` |
| Domain 零 I/O 零时钟 | `compute` 纯函数；时间注入 `Clock`；随机只接受显式 seed（D-07：`sha256(metric_id|version|inputs_hash)[:8]`） |
| 仓储显式 `user_id` | 全部新仓储/视图方法首参 `user_id`（RLS 第二道防线） |
| 新表必须 RLS | 两表 `ENABLE ROW LEVEL SECURITY` + `tenant_isolation`（`USING/WITH CHECK` 同谓词），过 `gate_rls_coverage.py` |
| 契约先行 | API-Catalog/PRD/Testing/Extensibility 修订 → `make api-snapshot` → `make api-check` → router → API 测试 → `make sdk` |
| 错误 `ApiError(ErrorCode.X)` | 复用既有码：`METRIC_NOT_FOUND/INVALID_PERIOD/VALIDATION_ERROR/UNKNOWN_BENCHMARK/UNKNOWN_QUERY_PARAM/INVALID_CURSOR/ADMIN_CANNOT_READ_TRADES`，**不新增错误码** |
| 分层铁律 | `stock_core/stats` 只依赖 contracts；`stock_review/metrics` 依赖 core/platform/contracts；api/worker 顶层；`MetricValue` 放 review，不进 contracts |
| 导入排序/`from __future__` | ruff I001；Router 一律 `StrictQueryRoute` |
| 前端零口径 | 数值定点字符串出口；`decomposition` 值同为定点串/整数 |

---

## 3. DB 设计（迁移 `0007_review_metrics.py`）

### 3.1 `review.metric_result`（物化结果表）

一行 = 某用户某指标在某 period、某口径版本、某参数组合下的**最近一次成功计算**。

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `String(40)` | PK | `mtr_` + `sha256(user_id|metric_id|period|definition_version|params_hash)[:32]`（UPSERT 幂等） |
| `user_id` | `UUID` | FK `platform.user` CASCADE, NOT NULL | 租户列（RLS） |
| `metric_id` | `String(64)` | NOT NULL | dotted id |
| `definition_version` | `String(16)` | NOT NULL, CHECK regex `^\d+\.\d+$` | 计算时 def 版本 |
| `params_hash` | `String(64)` | NOT NULL | sha256(canonical params：benchmark/rf/def params) |
| `params` | `JSONB` | NOT NULL DEFAULT `'{}'` | 规范化参数原文（审计/复算；不含 user/period） |
| `domain` | `String(16)` | NOT NULL | perf/timing/behavior/selection/attribution（冗余，免 join） |
| `period` | `String(7)` | NOT NULL, CHECK regex `^\d{4}-(0[1-9]|1[0-2])$` | `YYYY-MM` |
| `scope` | `JSONB` | NOT NULL | ScopeSpec 序列化（unit/cohort/period） |
| `status` | `String(16)` | NOT NULL, CHECK ∈ MetricStatus | ok/stale/blocked/unreliable（**只写 ok**；stale/blocked/unreliable 不落库，见 E-11/E-13） |
| `sample_status` | `String(16)` | NOT NULL, CHECK ∈ SampleStatus | ok/insufficient/pending_window/blocked |
| `value_units` | `BigInteger` | NULL | 标度整数；**CHECK：仅 status=ok 且 sample_status∈{ok,insufficient} 可为非 NULL** |
| `unit` | `String(8)` | NOT NULL, CHECK ∈ {CNY,ratio,days,count,z} | 出口单位 |
| `precision` | `Integer` | NOT NULL, CHECK 0~8 | 出口小数位 |
| `n` | `Integer` | NOT NULL DEFAULT 0, CHECK ≥0 | 样本量 |
| `n_pending` | `Integer` | NOT NULL DEFAULT 0, CHECK ≥0 | 窗口未走完剔除数 |
| `ci_low_units` / `ci_high_units` | `BigInteger` | NULL | CI 同标度；CHECK 成对 + 仅 status=ok 且 sample_status=ok 时非 NULL |
| `decomposition` | `JSONB` | NULL | additive 分解 `{key: 定点串|int|null}`（C-05） |
| `assumptions` | `JSONB` | NOT NULL DEFAULT `'{}'` | 口径披露（calendar/adjust/rf_rate/window_basis…） |
| `reason` | `String(128)` | NULL | 非 ok 的机读原因（白名单枚举） |
| `inputs_hash` | `String(80)` | NOT NULL | `sha256:<64hex>`（E-16） |
| `data_as_of` | `Date` | NOT NULL | PIT：`min(clock.today, market.as_of, asset_build.data_as_of)`；**无 asset_build 行时退化为 `min(clock.today, market.as_of)` 并写 `assumptions.pit_rule="no_asset_build"`**（EE-P1-3；依赖 asset_build 的指标仍 blocked、不落库） |
| `computed_at` | `DateTime(tz)` | NOT NULL | 注入 Clock |
| `created_at/updated_at` | `DateTime(tz)` | server_default now() / onupdate | 审计时间 |

**约束与索引**：
- `uq_metric_result(user_id, metric_id, period, definition_version, params_hash)`（E-02）
- `ck_metric_result_value_gate`：`value_units IS NULL OR (status='ok' AND sample_status IN ('ok','insufficient'))`
- `ck_metric_result_ci_gate`：`(ci_low_units IS NULL) = (ci_high_units IS NULL)` 且 `ci_low_units IS NULL OR (status='ok' AND sample_status='ok')`
- `ck_metric_result_status_gate`：`NOT (status='ok' AND sample_status='blocked')`；`status='ok' OR reason IS NOT NULL`（非 ok 必带机读原因）
- `ck_metric_result_period` / `ck_definition_version` / `ck_unit` / `ck_n_nonneg` / `ck_precision`；`ck_metric_result_as_of`：`data_as_of IS NOT NULL`
- `ix_metric_result_user_period(user_id, period)`、`ix_metric_result_user_domain(user_id, domain)`、`ix_metric_result_user_metric_period(user_id, metric_id, period)`

### 3.2 `review.metric_dependency`（血缘记录表，E-03）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `String(40)` | PK：`mtd_` + sha256(user|metric|period|version|dep_kind|dep_id)[:32] |
| `user_id` | `UUID` | 租户列（RLS） |
| `metric_id` | `String(64)` | 消费方指标 |
| `period` | `String(7)` | 同父行 |
| `definition_version` | `String(16)` | **同父行（E-03 修正：缺此列会跨版本误删）** |
| `dep_kind` | `String(16)` | `metric` / `dataset`，CHECK |
| `dep_id` | `String(64)` | 上游 metric_id 或数据集名（白名单见 §4.4） |
| `dep_hash` | `String(80)` | 上游 inputs_hash 或数据集 fingerprint |
| `metric_result_id` | `String(40)` | FK `review.metric_result.id` ON DELETE CASCADE |
| `created_at` | `DateTime(tz)` | 写入时间 |

- 唯一键 `(user_id, metric_id, period, definition_version, dep_kind, dep_id)`；索引 `(user_id, metric_id, period, definition_version)`、`(user_id, dep_kind, dep_id)`（"谁消费了 X"）。
- 写入：随物化同一事务；重算时按 `(user_id, metric_id, period, definition_version)` 删除旧行再插（**只清当前版本**）。
- 不参与失效判定，仅诊断/审计/未来 lineage 工具。

### 3.3 RLS 与迁移

- 两表 `ENABLE ROW LEVEL SECURITY`；策略：
  `USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)`，`WITH CHECK` 同谓词（**与 0006 完全一致，未设变量 fail-closed**）。
- `conftest._REVIEW_TABLES` 追加 `metric_result/metric_dependency`（当前 6 张）；`test_rls_isolation.py` 表清单同步。
- `docs/DATABASE.md` 增补两表章节（C-26）。
- 迁移头：`revision="0007"`、`down_revision="0006"`、`SCHEMA_VERSION="0007_review_metrics"`、`PREVIOUS_SCHEMA_VERSION="0006_review_asset_daily"`；`backend/services/api/src/stock_api/version.py:5` 的 `SCHEMA_VERSION` 同步为 `"0007_review_metrics"`（当前 `"0006_review_asset_daily"`）；`make migrate-check` 无漂移；downgrade 完整删策略/索引/两表并还原版本字符串。
- 授权沿用 0006 的 `_grant_roles()`：`GRANT SELECT, INSERT, UPDATE, DELETE ON <两表> TO stock_app`；`GRANT SELECT ... TO stock_backup`。
- **UPSERT 显式更新**（`ON CONFLICT ... DO UPDATE SET value_units=..., updated_at=now()`）——SQLAlchemy `onupdate` 在 `ON CONFLICT` 路径不触发，必须显式写 `updated_at`。

---

## 4. 契约层与接口冻结（05 的实现契约，07 依赖）

### 4.1 常量 / 配置 / 枚举

```python
# contracts/constants.py 增补（C-17）
Z_95: Final[Decimal] = Decimal("1.959963985")
MIN_TRADING_DAYS: Final[int] = 60
RF_ANNUAL_DEFAULT: Final[Decimal] = Decimal("0.015")   # D-14/§13.4 已批准
ENGINE_VERSION: Final[str] = "1"                        # 进 inputs_hash；引擎语义变更时 bump
```
- `SamplePolicy.ci`：`wilson | bootstrap | none`（C-18）；`ci_min_n=10` 与 `MIN_CI_SAMPLE=10` 一致（D-08）。
- `config.py`：`rf_annual: Decimal = RF_ANNUAL_DEFAULT`（env `RF_ANNUAL`，C-24）；`market_bars_file: str | None = None`（C-28，默认 None=沿用 quotes.json）。
- `MetricStatus/SampleStatus/ScopeType` 复用 contracts 枚举，不新增。

### 4.2 `MetricValue`（review/metrics/base.py）

```python
@dataclass(frozen=True, slots=True)
class MetricValue:
    value_units: int | None            # 标度整数；None=结构性无定义
    n: int                              # 分母样本数
    decomposition: Mapping[str, int | str | None]   # 定点串/整数，禁 float
    n_pending: int = 0
    ci_sample: tuple[tuple[int, ...], ...] | None = None  # E-15：观测=元组；升序；仅内存；≤2000
    ci_stat_id: str | None = None      # estimator id；ci=none 时 None
```
- `compute` **不得**返回 status/CI/data_as_of/conclusion。
- `ci_sample`/`ci_stat_id` 不出口 API、不落库；`assert_no_status_leak` 允许字段集为上述五字段 + 本两项。

### 4.3 `MetricDef` 与组件字段冻结（07 defs 直接依赖）

> `MetricDef` 共 **13 个字段 + 4 个附加**：13 字段 = `id/definition_version/domain/scope/params_schema/dependencies/compute/explain/drilldown/sample_policy/status_policy/unit/precision`；附加 = `core_conclusion/frequency/benchmark_required/mvp`（Extensibility §3.1）。

```python
class MetricDef(Protocol):
    id: str                          # ^(selection|timing|behavior|attribution|perf)\.[a-z0-9_]+$
    definition_version: str          # ^\d+\.\d+$
    domain: Domain                   # PERF/TIMING/BEHAVIOR/SELECTION/ATTRIBUTION
    scope: ScopeSpec                 # period=month（M1）；unit∈{round,position,day,trade} 为统计单元元数据
    params_schema: type[BaseModel]   # extra="forbid"；默认值即默认请求
    dependencies: tuple[MetricDep | DatasetDep, ...]  # 深度≤3、无环、dataset id 白名单
    sample_policy: SamplePolicy      # min_n=10, ci_min_n=10, ci=..., min_trading_days=0/60
    status_policy: tuple[StatusRule, ...]
    unit: Unit                       # CNY/RATIO/DAYS/COUNT/Z
    precision: int                   # 0~8
    core_conclusion: bool            # 总数∈[8,12]
    frequency: Literal["sync", "async"]
    benchmark_required: bool
    mvp: Literal["P0", "M1", "M2"]
    def compute(self, ctx: MetricContext) -> MetricValue: ...   # 纯函数
    explain: ExplainSpec
    drilldown: DrilldownSpec

@dataclass(frozen=True, slots=True)
class ScopeSpec: unit: str; cohort: str; period: str        # M1: "month"
@dataclass(frozen=True, slots=True)
class SamplePolicy: min_n: int = 10; ci_min_n: int = 10; ci: str = "none"; min_trading_days: int = 0
@dataclass(frozen=True, slots=True)
class StatusRule: kind: str; code: str                      # 例 "upstream=blocked" / "dataset_missing=bars"
@dataclass(frozen=True, slots=True)
class ExplainSpec:
    conclusion_zh: str; conclusion_en: str
    method_zh: str; method_en: str
    what_to_do_zh: str | None = None; what_to_do_en: str | None = None
@dataclass(frozen=True, slots=True)
class DrilldownFilters:        # 由 API query 规范化后传入
    period: str; benchmark: str | None; round_id: str | None; security_code: str | None

@dataclass(frozen=True, slots=True)
class DrilldownSpec:
    endpoint: str
    params: Mapping[str, str]
    description_zh: str; description_en: str
    build_items: Callable[[MetricContext, DrilldownFilters], Sequence[DrilldownItem]]  # E-12
@dataclass(frozen=True, slots=True)
class DrilldownItem:
    key: str; value_units: int | None; unit: Unit; label: str
    round_id: str | None = None; trade_id: str | None = None; event_date: date | None = None
    security_code: str | None = None   # 逐证券/事件型明细（win_rate/chase/BHAR）
```
- **导出约定**：每个 def 模块必须导出模块级实例 `DEF`（Extensibility §3.5 约定，唯一）；registry 只认这个名字。07 的 def 文件按此写（§14 同步）。
- **Dataset id 白名单（冻结）**：`rounds`、`fills`、`trades`、`asset_daily`、`asset_build`、`bars_daily`、`benchmark_series`、`calendar`。`holdings_daily/positions_end` **不在视图层**（E-06：07 在 def 内重建）。
- **MetricContext 字段（冻结）**：`user_id/period(start,end)/as_of/params/rf_annual/rounds/fills/trades/asset_daily/asset_build/bars/benchmark/calendar`；视图均为不可变序列/映射，禁止暴露 ORM 对象。
- **视图字段冻结**：rounds（id/security_code/status/open_date/close_date/quantity_units/cost_units/proceeds_units/dividend_units/pnl_units/return_rate_units/holding_days/data_error/oversell_units/rebuilt_at）；fills（round_id/sequence/buy_trade_id/sell_trade_id/quantity_units/buy_cost_units/sell_proceeds_units/dividend_units/pnl_units/open_date/close_date/holding_days）；trades（id/trade_date/line_no/side/category/security_code/quantity_units/price_units/amount_units/commission_units/stamp_tax_units/other_fee_units/cash_balance_units/deleted_at/version）；asset_daily（trade_date/cash_units/market_value_units/total_assets_units/holding_cost_units/unrealized_pnl_units/holding_count/cashflow_units/prev_total_assets_units/daily_pnl_units/daily_return_units/status/definition_version）；bars（symbol/trade_date/close_units/return_units）；benchmark（trade_date/return_units/available/provenance）。

### 4.4 `core/stats` 归属与 estimator 注册（E-05/E-15）

| 模块 | 归属 | 内容 |
|---|---|---|
| `core/stats/numeric.py` | 05 | `div_round/sqrt_units/median_units/pct_index/xorshift64_star` |
| `core/stats/wilson.py` | 05 | `wilson_interval(success, n, z: Decimal = Z_95, scale=8)`（与 C-17 的 `Z_95` 单点；不再用 1e-6 整数 z） |
| `core/stats/bootstrap.py` | 05 | `bootstrap_percentile(samples: Sequence[Sequence[int]], stat, *, b=1000, seed, alpha_permille=(25,974))` |
| `core/stats/estimators/` | 05 提供框架 + `mean.py`；**07 加文件** | `register(id)(fn)` 装饰器 + 包内 `pkgutil` 自动发现；`fn(samples: Sequence[tuple[int,...]]) -> int`（标度整数统计量） |
| `core/stats/{regression,concentration,drawdown,tstat}.py` | 07 | OLS/Gini/HHI/MDD/聚类 t |
| `core/stats` 测试 | 05 测 05 份额；07 测 07 份额 | 校验值取 07 §5.4/§5.5 |

- estimator 白名单通过注册表暴露；`ci_stat_id` 未注册 → 注册期 `RegistryError`（fail-fast），不等到请求。
- 05 不实现 DE 的 `ratio_diff_pgr_plr`（07 实现）；05 用 `mean` estimator 驱动全链路测试。

---

## 5. `stock_review/metrics` 包结构

```
backend/packages/review/src/stock_review/metrics/
├── __init__.py          # registry/engine 入口
├── base.py              # MetricDef/MetricValue/SamplePolicy/StatusRule/ScopeSpec/ExplainSpec/
│                        # DrilldownSpec/DrilldownItem/Unit/Domain
├── registry.py          # pkgutil 扫 defs/；校验 13 字段、id/version 正则、deps 深度/环、dataset 白名单、
│                        # core_conclusion 计数、ci_stat_id 已注册；build_registry(extra_paths=()) 供测试
├── errors.py            # RegistryError/CycleError（API 层翻译为 ApiError）
├── context.py           # MetricContext 组装 + Period 解析（YYYY-MM 闭区间；from/to 半开→单月）
├── views.py             # 8 个视图 Protocol（§4.3 白名单）+ freeze()/fingerprint()
├── datasets.py          # 视图装配（查仓储→冻结；首参 user_id；RLS 会话）
├── hash.py              # canonical_json / inputs_hash / params_hash / dataset fingerprint / seed 派生
├── gate.py              # 门禁状态机（§6.3）
├── engine.py            # 管线编排（§6.1/§6.2）
├── persist.py           # UPSERT + 血缘写 + 负缓存策略（E-11）
├── estimators.py        # 05 侧：core.stats.estimators 的注册入口/错误翻译（薄封装）
└── defs/
    ├── __init__.py
    └── （生产为空/仅注释；测试 def 不在此目录，见 E-10）
```
- 生产 `registry` 只扫 `stock_review.metrics.defs`；测试用 `build_registry(extra_paths=["backend/tests/support/metric_defs"])` 注入 `perf.win_rate_stub` 等。
- `core_conclusion=True` 总数超 [8,12] → 注册期 `RegistryError`（07 冻结时收敛；测试 def 不计入生产校验）。

---

## 6. 引擎管线

### 6.1 管线（`engine.py`，每次 GET /metrics 执行）

```
① 解析参数     period 或 from/to；benchmark；include_exploratory；按 defs params_schema 校验 extra=forbid
② 规范化参数   params = 默认值 ∪ 请求覆盖；params_hash = sha256(canonical(params))
③ 解析 as_of   min(clock.today, market.as_of, asset_build.data_as_of)
④ 选 defs      ids 精确匹配 / domain 过滤 / include_exploratory 过滤（E-09）
⑤ 建 DAG       Kahn 拓扑 + 环检测（注册时已校验，冗余防御）
⑥ 装配视图     收集所需 DatasetDep，一次装配 + fingerprint（§6.4）；同请求内共享
⑦ 逐指标（拓扑序）：
   a. inputs_hash = hash(metric_id, version, engine_version, params_hash, period, as_of, deps…)
   b. 按 (user, metric, period, version, params_hash) 查行：
      - 命中 且 inputs_hash 相等 且 status=ok → 直接用（缓存命中，含 insufficient/pending_window）
      - 其它情况 → 重算；blocked/unreliable 不读旧行、不落库（E-11）
   c. 门禁预检（§6.3）：上游 blocked / dataset missing / build 非 current / 基准不可用 → blocked；
      数据越界/密度不足 → unreliable（仍 compute，value 不出口）
   d. ctx 组装 → def.compute(ctx) → MetricValue
   e. 框架代算：sample_status（四态矩阵）→ CI（ci 策略 + ci_stat_id 派发 + seed 派生）→
      conclusion 可用性（status=ok 且 sample_status=ok 且 n≥min_conclusion_sample）→ assumptions 合并
   f. 若 status=ok：UPSERT metric_result + 同事务写 metric_dependency（只写当前版本）；
      若 blocked/unreliable：不写库（E-11），响应仍带 reason
⑧ 组装响应 DTO（定点串出口）+ 顶层 assumptions + ETag
```
- **幂等**：同输入双跑 `inputs_hash` 一致；bootstrap seed 由 hash 派生，逐位相等。
- **事务**：视图冻结/计算在会话外；写库短事务；写失败仅日志，不阻断读（E-01）。
- **并发**：UPSERT `ON CONFLICT (user_id, metric_id, period, definition_version, params_hash) DO UPDATE`；并发同键无副作用。

### 6.2 缓存与负缓存（E-01/E-11）

| 场景 | 行为 |
|---|---|
| 命中 + inputs_hash 相同 + status=ok | 直接返回（不重算） |
| 命中但 inputs_hash 不同（数据/口径/as_of 变化） | 重算并 UPSERT 覆盖当前唯一键行 |
| 无行（首次/新版本/新 benchmark） | 计算；status=ok 才落库 |
| 上次是 blocked/unreliable | 不读旧行、不落库；本次重新计算（行情恢复即恢复） |
| 写库失败 | 记录 `obs` 指标/日志；返回本次计算结果（200）；不缓存 |
| 同一请求多指标 | 共享视图装配与 fingerprint；不共享 MetricValue |

### 6.3 门禁状态机（`gate.py`）

优先级：`blocked > stale > unreliable > ok`（stale 只在"上游数据状态过期"时产出，E-13）。`sample_status` 独立判定。

| 触发 | status | 动作 |
|---|---|---|
| 上游 MetricDep `blocked` | blocked | 不 compute；reason=`upstream_blocked:<dep_id>` |
| 数据集**不可用/未构建**（bars 源不可用、benchmark 缺失、`asset_build.state=missing`） | blocked | reason=`dataset_unavailable:<dep_id>` |
| 对账 blocked / `asset_build.state=missing` 或 blocked（07 §6.3） | blocked | reason 按规则表（missing 无旧值按 blocked） |
| `asset_build.state=stale` / `market.as_of` 滞后 | **stale** | 无 value、不读缓存/不重算；reason=`ASSET_BUILD_STALE`/`MARKET_ASOF_LAG`（E-13） |
| 区间内 `asset_daily.status=blocked`（回撤/β） | blocked | 断链不拼接 |
| **区间无样本（n=0）** | **不得判 blocked** | 交给 `sample_status=insufficient`、`value:null`（07 D-08） |
| 数据越界（`|r|>1`）、密度<0.8、degraded 行 | unreliable | 仍 compute；value 不出口 |
| 无上游问题 | ok | 正常出口 |


- `sample_status`（与 07 §4.2 逐格一致）：`n<min_n` → `insufficient`（有 value/null、无 CI、无 conclusion）；`sample_policy.min_trading_days>0` 且 `observed_days<MIN_TRADING_DAYS` → `pending_window`（**无 value**）；`n≥min_n` → `ok`；`ci≠none` 且 `n≥ci_min_n(=10)` → 输出 CI。
- **value 出口**：`status≠ok` 或 `sample_status=pending_window` → 响应无 `value` 键；`insufficient` → 有 value（结构性无定义为 null）、无 CI、无 conclusion。
- `conclusion` 可用性 = `status=ok 且 sample_status=ok 且 n≥min_conclusion_sample`；否则 explain 返回 `conclusion=null` + reason/what_to_do。
- **reason 机读码白名单**：`UPSTREAM_BLOCKED:<metric_id>`、`DATASET_UNAVAILABLE:<dep_id>`、`RECON_BLOCKED`、`ASSET_BUILD_BLOCKED`、`BENCHMARK_MISSING`、`DATA_OUT_OF_RANGE`、`BAR_DENSITY_LOW`、`ASSET_BUILD_STALE`、`PENDING_WINDOW`；新增码需登记。

### 6.4 数据集指纹（E-04；逐视图可执行）

| 数据集 | 指纹组成 | 覆盖的变更 |
|---|---|---|
| `rounds` | `count(*)` + `max(rebuilt_at)` + `max(id)` + `round_build_state.last_rebuilt_at/version` | 导入/撤销/重建/口径 bump |
| `fills` | `count(*)` + `max(id)` + `round_build_state.last_rebuilt_at` | 重建（fill 行重写） |
| `trades` | `count(*)` + `count(deleted_at)` + `max(updated_at)` + `max(id)` | 新增/PATCH/软删/恢复/去重撤销/补差 |
| `asset_daily` | `count(*)` + `max(updated_at)` + `max(id)` + `asset_build_state.updated_at/version/definition_version` | 资产重建/增量 |
| `asset_build` | `state.updated_at` + `state.version` + `last_status` + `data_as_of` | 构建状态变化 |
| `nav_daily` | `count(*)` + `max(updated_at)` + `max(rebuilt_at)` | 净值重算 |
| `bars_daily` / `benchmark_series` | replay：`provenance + file_sha256 + market.as_of`；live：`market.daily_bar` 的 `count(*) + max(updated_at) + max(trade_date)` + `market.pull_range.checksum/pulled_at` | 夹具文件替换/行情拉取/缓存更新 |
| `calendar` | 固定 `calendar=weekday_proxy`（常量，进 assumptions） | 日历口径变更需 bump |
| 配置 | `rf_annual` 字符串、`benchmark_id` | rf/基准切换（同时进 params_hash） |
| 依赖指标 | 上游 `inputs_hash` | 上游重算 |

- `inputs_hash = sha256("sha256:" + hex(canonical_json({...})))`？**统一格式**：`inputs_hash = "sha256:" + sha256(canonical_json(...)).hexdigest()`；`params_hash = sha256(canonical_json(params)).hexdigest()`（64 hex）。canonical JSON：键排序、无空格、Decimal→定点字符串、禁 float/NaN。
- 指纹查询必须走索引（`(user_id, updated_at)` 等），单次 ≤2 条聚合 SQL；见 §10 性能预算。

---

## 7. 端点设计（Period2-06）

### 7.1 API-033 `GET /metrics`

**query**：
- `ids`（逗号分隔，≤50，去重）/ `domain`（`perf|timing|behavior|selection|all`）/ `period=YYYY-MM` 或 `from/to`（半开 `[from,to)`，M1 仅同一自然月）/ `benchmark`（默认 `hs300_tr`）/ `include_exploratory`（默认 false）。
- `ids` 与 `domain` 二选一（同传 422）；**两者都不传 → 等价 `domain=all`**；`period` 与 `from/to` 二选一（同传 422）；`from/to` 仅接受"月初→次月初"的半开区间（等价单月），否则 422（C-34）。
- 未知 query → `400 UNKNOWN_QUERY_PARAM`（StrictQueryRoute）。

**响应 200**：
```
items[] MetricResult(
  metric_id, definition_version, scope, period, value?, unit, n, ci_low, ci_high,
  sample_status, status, assumptions, data_as_of, inputs_hash, computed_at, decomposition?)
period, data_as_of, definition_version, assumptions{benchmark_id?, rf_rate, A:244, engine_version}
```
- `value`：定点字符串；`ratio` 8 位、`CNY` 2 位、`days` 2 位、`count` 整数、`z` 4 位（C-03）。
- `scope`：`{unit:"month", cohort:"<cohort>", period:"month"}`（M1）。
- `item` 顺序：给了 `ids` 按 `ids` 顺序（去重后），否则按 registry 的 metric_id 升序。
- 顶层 `definition_version`：所有 item 一致取该值，否则 `"mixed"`（E-14/C-35）；顶层 `assumptions`：公共假设（`benchmark_id` 仅当请求/指标使用、`rf_rate`、`A`、`engine_version`）。
- `ETag`：见 E-08；命中 `If-None-Match` → 304 空 body（响应头 `ETag: W/"<sha16>"`）。**304 判定在写库前**：先生成 items/ETag，命中则不执行 UPSERT（304 无副作用）。

**错误码**：`METRIC_NOT_FOUND(404)`（未知/非法 id）、`INVALID_PERIOD(422)`（格式/未来月/from-to 非单月）、`UNKNOWN_BENCHMARK(422)`、`VALIDATION_ERROR(422)`（ids+domain、period+from/to、空 ids、domain 非法）、`UNKNOWN_QUERY_PARAM(400)`、`ADMIN_CANNOT_READ_TRADES(403)`、401。

### 7.2 API-034 `GET /metrics/{metric_id}/explain`

- `period` 必填；`benchmark?`；`Accept-Language`（zh-CN/en-US）。
- 响应：`metric_id/definition_version/period/conclusion?/method/drilldown{endpoint,params,description}/status/reason?/what_to_do?/assumptions/data_as_of`。
- `conclusion` 仅 `status=ok 且 sample_status=ok 且 n≥10`；否则 null + `reason/what_to_do` 非空（中英按语言）。
- `method` 必有（口径一句话，允许 1 个括注术语）；`drilldown.endpoint` 必须可由本 API 调用（测试断言）。
- 词表断言：`conclusion/reason/what_to_do` 不得出现 `DE/BHAR/PGR/PLR/TWR/MWR/FIFO/HHI/Gini/Ulcer/Calmar/IR/β/α`（C-02）；`method` 最多 1 个括注术语。
- 错误码：`METRIC_NOT_FOUND(404)`、`INVALID_PERIOD(422)`、`VALIDATION_ERROR(422)`、admin 403、401；无 ETag。
- **数据来源**：explain/drilldown 与 API-033 共用同一 read-through 引擎（blocked/unreliable 不落库时也能解释/下钻），不得只读 `metric_result`。

### 7.3 API-035 `GET /metrics/{metric_id}/drilldown`

- 请求：`period` 必填；`benchmark?`；`round_id?/security_code?`；`limit`(1~200，默认 50)；`cursor?`。
- **生成方式（E-12）**：`DrilldownSpec.build_items(ctx, filters)` → items；engine 按稳定键 `(event_date, key)` 升序排序后分页；`cursor` 编码 `(last_key, query_fingerprint)`，篡改/跨查询 → `400 INVALID_CURSOR`；items >5000 → 截断并 `truncated=true`（assumptions 披露）。
- item：`key/value/unit/label` + 可选 `round_id/trade_id/event_date`；`value` 按 `(unit, precision)` 分级格式化（C-03）。
- 响应增可选 `status/reason`（additive，C-37）：指标 blocked/unreliable 时 `items=[]` + `status/reason`，不抛 500；`insufficient` 仍返回已有明细。
- `source_endpoints[]`：def 声明，测试逐个调用 200；`params` 回填 `metric_id/period/benchmark?`。
- 错误码：`METRIC_NOT_FOUND(404)`、`VALIDATION_ERROR(422)`、`INVALID_CURSOR(400)`、admin 403、401。
- 数据一致性：`trade_id/round_id` 必须能在本人 `GET /trades`/`GET /rounds` 查到；事件型指标（BHAR）无 trade_id 时用 `event_date+security_code`（C-03/C-37）。

### 7.4 API-079 `GET /metrics/{metric_id}`

- query：`period` 必填、`benchmark?`（用于选择 `params_hash` 行；缺省 `hs300_tr`）。
- 返回单个完整 `MetricResult`（同 7.1 item 形状 + 顶层 assumptions/rf/A/engine_version）。
- ETag/304 同 7.1；错误码同 7.1（无 UNKNOWN_BENCHMARK 时省略）；与 API-033 同 period/params 的 item **逐字段相等**（测试断言）。

### 7.5 Router/应用服务与契约流程

- `stock_api/routers/metrics.py`：`APIRouter(route_class=StrictQueryRoute)`，operationId `metrics_batch/metrics_explain/metrics_drilldown/metrics_detail`；路径参数 `metric_id` 做 dotted 校验（非法 → 404）。
- `stock_review/application/metrics.py`：`list_metrics/explain/drilldown`（首参 user_id；注入 session/clock/market_source/settings；只读视图 + read-through 写）。
- 契约先行：改 API-Catalog §1/§2.3/§4/§5/§6（C-01/C-02/C-03/C-05/C-08/C-29/C-31/C-34 + 本设计 C-35~C-37）→ `make api-snapshot` → `make api-check` → router → `backend/tests/api/test_metrics.py` → `make sdk`。

---

## 8. 契约修订落地清单（05/06 份额 + 本设计新增）

| # | 内容 | 归属 |
|---|---|---|
| C-01 | API-033：dotted id 示例 + 四态矩阵 + `ci_min_n=10`；**`domain` 枚举补 `perf`**（Catalog 现状缺 perf，EE-P2-1）；补 ETag/304 说明 | 06 |
| C-02 | API-034：裸术语词表扩充 + 非 ok 的 reason/what_to_do 枚举 | 06 |
| C-03 | API-035：item 增 `unit`/`event_date`、按 unit+precision 分级格式 | 06 |
| C-05 | Catalog §4 `MetricResult` 增 `decomposition?`/`unit`/item `assumptions`；`metric_result` 表列同步 | 05+06 |
| C-06 | Catalog §6 验收矩阵：`nav_return`/flat id/`fx_round30` 与 n==12 矛盾；06 端点的 fixture/断言同步 | 06 |
| C-08 | API-033/034/035/079 错误码补 `ADMIN_CANNOT_READ_TRADES` | 06 |
| C-16/17 | Extensibility `ci_min_n=30→10`；constants 增 `Z_95/MIN_TRADING_DAYS/RF_ANNUAL_DEFAULT/ENGINE_VERSION` | 05 |
| C-18/19 | Extensibility：`SamplePolicy.ci` 支持 none；`MetricValue.ci_sample/ci_stat_id`（E-15 细化形状） | 05 |
| C-22 | `RoundRepository.in_period(...)`（`close_date` 优先，`page` 语义不动） | 05 |
| C-24 | `config.py` 增 `rf_annual` | 05 |
| C-26 | `docs/DATABASE.md` 增两表章节 | 05 |
| C-28 | replay `MARKET_BARS_FILE` 配置 + 双源能力 | 05 |
| C-29 | API-079 详约补齐（ETag/304/错误码/断言） | 06 |
| C-31 | Catalog §0.8 / Extensibility §3.3：stale/unreliable 无 value 语义统一 | 06 |
| C-34 | Catalog §0.4：period 闭区间 vs from/to 半开 | 06 |
| **C-35** | API-033/079 顶层 `definition_version`（一致→版本，混合→`"mixed"`）与 `assumptions` 聚合语义 | 06 |
| **C-36** | API-033/079 明写 **GET 的 read-through 写副作用**与负缓存策略（E-01/E-11） | 06 |
| **C-37** | API-035 item schema 正式化（`event_date/key`、`truncated`、cursor 绑定查询指纹） | 06 |
| **C-38** | API-033 校验矩阵：ids 上限/去重/空、from/to 单月、benchmark 错误码、UNKNOWN_QUERY_PARAM | 06 |
| **C-39** | 契约/实现同步：`MetricValue.ci_sample` 嵌套观测形状 + estimator 注册表 + `DEF` 导出约定 | 05（同步 07 §4.1/§5.3/C-19） |
| **C-40** | DEV-PLAN：05/06 估时 3d+2d → 6.5d+3.0d（或按 E-17 备选调整） | 人工 |
| **C-41** | **`stale` 语义/05 验收修订**：DEV-PLAN 05 原文"bump→旧结果 100% stale"改为"新版本行插入 + 旧行保留 + `persist.is_stale` 单测"；与 Extensibility §3.3/Architecture §11 统一（EE-P2-4/E-13） | 05（文档） |
| **C-42** | `make test-engine` 并入 C-25 的 target 清单；内含 `alembic downgrade 0006 && upgrade head` 幂等步骤（EE-P2-5） | 05 |
| **C-43** | `openapi_meta.py` 登记 4 个 metrics operationId 的示例/错误码/`x-error-codes`；Catalog §1.1 的 operationId 命名与实现一致（snake_case）；`openapi.json` 生成后 `api-check` 通过 | 06 |

> 落地方式：**开工首日一次性契约 PR**（文档 + api-snapshot + api-check + sdk），与三域设计 §9 一致；C-35~C-43 需同时补进 07 的 C 清单（§14）。

---

## 9. 测试方案

### 9.1 测试分层

| 层 | 内容 | 位置 |
|---|---|---|
| 迁移 | 0007 up/down 幂等、RLS（未设 `app.user_id` 0 行、跨租户写拒）、CHECK 约束（value/ci/period/version/unit）、索引存在 | `tests/integration/test_rls_isolation.py` + `tests/integration/test_metrics_migration.py`★ |
| core/stats（05 份额） | numeric/wilson/bootstrap/estimators：校验值（07 §5.4/§5.5）、确定性双跑、不同 seed 不同、`ci_sample` 乱序不变、成对观测 estimator | `tests/unit/core/test_stats_*.py` |
| registry | 自动发现、`DEF` 导出、id/version 正则/唯一、环/深度、dataset 白名单、`ci_stat_id` 未注册 fail-fast、`core_conclusion` 计数、测试 def 注入（生产 registry 不含） | `tests/unit/review/test_registry.py` |
| 引擎 | D-08 四态矩阵逐格、状态传播（上游 blocked→下游 blocked）、空区间 n=0→insufficient、unreliable 不出口 value、`pending_window` 无 value、`make test-engine` 两条原文验收（N<10→insufficient 且无 conclusion；**口径 bump→新版本行插入 + 旧行保留 + `persist.is_stale` 单测**，`/metrics` 返回新行 ok 而非 stale，C-41）、`inputs_hash` 稳定、指纹变更→重算 | `tests/unit/review/test_engine.py` + `make test-engine` target |
| 物化/缓存 | UPSERT 幂等（行数不变）、版本+参数保留、负缓存（blocked 不落库、恢复后重算）、写失败不阻断读、并发同键、命中/未命中/失效矩阵（§6.4 每个视图至少 1 条变更） | `tests/unit/review/test_persist.py` |
| 契约 kit | **07 拥有 `tests/contract/kit.py`**；05 只交付引擎侧最小断言（`assert_sample_gate/assert_blocked_has_no_value/assert_no_float/纯度 AST`）供 05 先行测试，07 在其上扩展并参数化 `registry.all()`；避免 05/07 双写同一文件 | `tests/unit/review/test_engine_kit.py`（05）→ 07 `tests/contract/kit.py` |
| API 验收 | 033/034/035/079 每端点 ≥3 条（happy/越权 404·admin 403/错误码 422·400）+ 本设计 §9.2 场景矩阵 | `tests/api/test_metrics.py` |
| 契约门禁 | api-snapshot/api-check/sdk git diff=0；`gate_endpoint_coverage`（phase=2，C-27）、`gate_tenant_coverage`、`gate_rls_coverage` | Makefile |

### 9.2 必测场景矩阵（新增/易漏）

- **缓存/指纹**：导入新增、PATCH 改价（version>1）、软删、恢复、撤销批次、对账补差、资产重建、行情文件替换（replay）、live bar 更新、口径 bump、benchmark 切换、rf 变化 — 每种断言"inputs_hash 变化 + 重算"；反例（无关用户/无关指标变化）断言"不重算"。
- **负缓存**：制造 blocked（缺基准/资产未构建）→ 修复 → 下一次请求变 ok（不是永久 blocked）。
- **空区间**：period 内无回合/无成交 → `insufficient` + value null（不是 blocked）。
- **版本**：definition_version bump → 新行插入、旧行仍在、GET 返回新版本 ok；`/trends` 未来读取旧行（本设计只预留）。
- **校验**：ids 空/重复/51 个/未知/非法格式；ids+domain；period 非法/未来月；from/to 跨月/半开边界/单端；benchmark 未知；include_exploratory true/false；未知 query → 400；游标篡改 → 400。
- **ETag**：同参数两次 → 304；改数据/改 benchmark/改 include_exploratory → 200 新 ETag；304 无 body。
- **drilldown**：items 非空、稳定排序、分页无重无漏、cursor 篡改、`truncated=true`（大结果）、trade_id 可回查、event_date 型 item、source_endpoints 可调。
- **explain**：zh/en 模板、非 ok 无 conclusion、裸术语扫描（en 也要）、`method` 括注术语 ≤1、drilldown 可调。
- **越权/门禁**（EE-P1-5）：4 个 operation 全部加进 `tests/isolation/test_isolation_matrix.py` 的 `RESOURCE_MATRIX/UNAUTHENTICATED_MATRIX/ADMIN_FORBIDDEN`；`/metrics/{metric_id}` 无租户资源 ID 语义 → 在 `gate_tenant_coverage` 的 `EXEMPT` 写理由（"指标为按 user_id 计算的派生值，响应内容由 RLS 保证不含他人数据"），但 401/403/内容扫描用例必须保留；A token 查 B 数据 0 泄露；admin 403；无 token 401；read PAT 可读；跨用户 cursor 只返回本人。
- **read-through/指纹矩阵**（EE-P1-5）：命中→不重算、未命中→算、指纹变化→重算、负缓存恢复、并发同键、写失败不阻断读；至少覆盖 §6.4 每行一种变更（导入/PATCH/软删/恢复/撤销/补差/资产重建/夹具替换/rf/benchmark/版本 bump）。
- **回归**：`make verify-p1`、`make test-p2` 全绿（nav/assets/rounds 零改动）。

### 9.3 夹具与命令

- 测试 def：`backend/tests/support/metric_defs/perf_win_rate_stub.py`（Wilson）+ `perf_expectancy_stub.py`（bootstrap/成对观测），id `perf.win_rate_stub`/`perf.expectancy_stub`；经 `build_registry(extra_paths=...)` 注入。
- 微型数据：`make seed-tenants` 既有回合数据；日线 **`backend/tests/fixtures/metrics/fx_bars_mini/`**（含 `bars.json` + `MANIFEST.json` sha256 + `owner/synthetic` 元数据，与 07 §8.3 同构图；EE-P2-2），经 `ReplayMarketDataSource(path)` 注入，不读根 `fixtures/`。
- 验收命令：
```bash
make migrate && make migrate-check          # 0007 up / 无漂移；再跑一次幂等
make test-engine                            # 新 target：registry/engine/persist/kit 单测
make verify-p1 && make test-p2              # 既有回归零破坏
make api-check && make sdk                  # 契约快照/TS client 同步
curl ".../api/v1/metrics?ids=perf.win_rate_stub&period=2026-06" | jq '.items[0]'
```

---

## 10. 实施顺序与估时

| 步骤 | 内容 | 人日 |
|---|---|---|
| M-1 | 契约 PR（05/06 份额 + C-35~C-43）+ api-snapshot + sdk | 0.5 |
| M-2 | 迁移 0007（两表 + RLS + CHECK + 索引）+ models + conftest/RLS/migration 测试 | 1.0 |
| M-3 | contracts 常量 + config.rf_annual/bars_file + base/registry/hash（含 `DEF`/dataset 白名单/estimator 注册） | 1.0 |
| M-4 | core/stats 05 份额（numeric/wilson/bootstrap/estimators + 单测） | 1.5 |
| M-5 | views/datasets（8 视图 + 指纹）+ C-22 `in_period` + C-28 replay bars | 1.5 |
| M-6 | gate/engine/persist（read-through + 负缓存 + 血缘 + 并发/失败路径）+ 引擎单测 + `make test-engine` | 2.0 |
| M-7 | application/metrics.py + routers/metrics.py（4 端点 + ETag + drilldown builder 接入）+ API 验收 | 1.5 |
| M-8 | docs 同步（DATABASE/实现日志/评审记录）+ 全门禁复跑 | 0.5 |
| — | **合计**：05 ≈ 6.5d、06 ≈ 3.0d，合计 **9.5d**（不含 07） | **9.5** |

> **与已批准 5~7d 的差异**：见 E-17。若人工选备选②（C-28 0.5d + drilldown builder 1.0d 挪 07b）→ 8.0d；备选③（砍 stub/部分验收）→ 7.0d。07 的 S0（5~7d）与总排期需同步修正（§14）。

---

## 11. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 与 07 冻结契约漂移 | 本设计 §4 为 07 的编译期契约；§14 列出对 07 的增量修订；评审以 07 为准仲裁 |
| R-2 | 07 def 需要视图没有的字段 | 视图字段冻结（§4.3）；新增字段属 additive，07 补视图不改引擎；`holdings_daily` 由 07 重建 |
| R-3 | read-through 首算延迟（18 指标） | 同请求共享视图；**07 §8.10 的 300ms 是"批量优先命中物化"的预算，冷启动预算 ≤2s**；首算超 2s 走 07 R7 降级（先物化再读）；负缓存不写库避免故障期放大 |
| R-4 | 指纹粒度过粗/过细 | §6.4 逐视图表；每类变更至少 1 条测试；未知变更（如直接 SQL 改库）由 `ENGINE_VERSION`/指纹兜底；replay 文件替换靠 file sha |
| R-5 | 物化表膨胀 | 指标×月×版本×参数组合；个人量级；无清理任务（YAGNI），但预留 `period` 索引 |
| R-6 | 测试 def 泄漏/与 07 冲突 | E-10：测试 def 不进生产 defs；生产 registry 不扫测试路径 |
| R-7 | drilldown 明细昂贵 | E-12：纯函数 + 上限 5000 + 稳定分页；drilldown 单独预算（≤200ms，month scope） |
| R-8 | GET 写副作用争议 | E-01/C-36 明写；与 `/reconciliation` 先例一致；写失败不阻断读 |
| R-9 | 并发/缓存竞态 | 唯一键 UPSERT；同键并发以最后写入者为准（同输入结果相同）；无跨请求锁 |
| R-10 | 估时超已批准 5~7d | E-17 人工拍板；备选②/③ |

---

## 12. HITL 决策清单

| # | 决策 | 建议 | 状态 |
|---|---|---|---|
| E-01 | read-through vs 预计算 job | read-through + GET 写副作用明写 | 待拍板 |
| E-02 | 唯一键含 `params_hash`、旧行保留 | 采用 | 待拍板 |
| E-05 | stats 05/07 归属 + estimator 自动发现 | 采用 | 待拍板 |
| E-07 | M1 仅 month period；`ScopeSpec.unit` 作为元数据透传 | 采用 | 待拍板 |
| E-10 | 测试 def 隔离 | 采用 | 待拍板 |
| E-11 | blocked/unreliable 不落库（负缓存） | 采用 | 待拍板 |
| E-12 | drilldown 用 `build_items` 纯函数 | 采用 | 待拍板 |
| E-13 | `/metrics` 的 stale 仅限"上游数据状态过期"（无 value、不重算）；口径 bump 返回新版本 ok | 采用 | 待拍板 |
| E-15 | `ci_sample` 嵌套观测 + estimator | 采用 | 待拍板 |
| E-17 | 05+06 估时 9.5d vs 已批准 5~7d | ① 接受 9.5d 并改排期（推荐）② 挪 C-28+drilldown 到 07b→8d ③ 砍验收→7d | **待人工拍板** |
| E-18 | API-038 handler 归 06 | 采用（与 07 D-17 一致） | 待拍板 |
| 其余 E-03/04/06/08/09/14/16 | 工程决策 | 已按推荐设计 | 可抽查 |

---

## 13. 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-10-26 | 初稿 |
| **v1.1** | 2026-10-26 | 独立评审后修订：① 唯一键加 `params_hash`（E-02）；② blocked/unreliable 不落库（E-11）；③ 新增 drilldown `build_items`（E-12，修掉"decomposition 反推明细"的 P0）；④ 指纹逐视图可执行（E-04/§6.4，含 replay 文件 sha）；⑤ `ci_sample` 改嵌套观测（E-15）；⑥ stale 仅"上游数据过期"可达、口径 bump 返回新版本 ok（E-13）；⑦ 顶层响应语义/校验矩阵/ETag 指纹（E-08/E-09/E-14）；⑧ DB CHECK/依赖表版本/RLS 谓词修正；⑨ 测试 def 隔离（E-10）；⑩ 估时 9.5d 与 E-17；⑪ 契约 C-35~C-43；⑫ 新增 §14 对 07 的同步修订清单（S-1~S-10） |

---

## 14. 对 07 设计的同步修订清单（实现前需同步更新 `2026-10-26-metrics-three-domain.md`）

> 本设计对 07 冻结契约做了 4 处**增量**修改（均向后兼容、不推翻 07 的 18 指标口径），07 文档需同步：

| # | 07 位置 | 现状 | 改为 |
|---|---|---|---|
| S-1 | §4.1 `MetricValue` | `ci_sample: tuple[int,...]`（单层） | `ci_sample: tuple[tuple[int,...], ...]`（观测=元组，支持成对 bootstrap；E-15）；`ci_stat_id` 指向 `core/stats/estimators/` 自动发现注册表；`ci="wilson"` 时 win_rate 需给 Bernoulli 0/1 观测（engine 求 success/n） |
| S-2 | §4.1 `MetricContext` / §7.1.1 | 列 `holdings_daily`、`positions_end` 视图 | `holdings_daily`/`positions_end` **不入视图层**，由 07 def 基于 `trades+bars` 重建（E-06/D-13）；视图字段按本设计 §4.3 冻结 |
| S-3 | §5.1 / §10 S1 | 07 交付整个 `core/stats`（含 numeric/wilson/bootstrap/estimators） | 05 交付 `numeric/wilson/bootstrap/estimators`；07 交付 `regression/concentration/drawdown/tstat` + estimator 模块；07 S1 估时相应下调（约 −0.8d） |
| S-4 | §4.2/§8.7 + §9 C 清单 | 无 `DEF` 导出约定、无 drilldown `build_items`、无 C-35~C-40 | 07 def 统一导出 `DEF`（Extensibility §3.5）；`DrilldownSpec.build_items` 必须实现（E-12）；C 清单追加 C-35~C-43；§8.7 API-035 断言按 C-37 细化 |
| S-5 | D-17 / §1.4 | "API-038 handler 归 06/07b"表述模糊 | 明确 **归 Period2-06 收尾**（E-18） |
| S-6 | §10 S0 / D-01 | "05/06 前置 5~7d" | 按 E-17 裁定改为 8~9.5d（或备选），并同步 DEV-PLAN 估时 |
| S-7 | §6.3/§4.2 + DEV-PLAN 05 验收 | §6.3 的 `bar_density<0.8` 行仍写"有 value"、`definition_version 不匹配→stale`；DEV-PLAN 05 写"bump→旧结果 100% stale" | ① bar_density 行改"无 value"；② version bump 改为"新版本行 + 返回 ok"，`stale` 仅用于 `asset_build.state=stale`/`market.as_of` 滞后；③ 05 验收改为"新版本行 + 旧行保留 + is_stale 单测"（E-13/C-41） |
| S-8 | §9 C 清单 / API-Catalog API-033 / §8.6 kit | `domain` 无 `perf`、无 ETag/304 明写；kit 字段写 `value` 而 §4.1 为 `value_units` | 补 `perf` 枚举、ETag/304、`value_units`；C 清单追加 C-41~C-43（EE-P2-1/4/5、EC-P2-1） |
| S-9 | §6.2 | "`from/to` 也闭区间" | 改为"`period` 闭区间；`from/to` 半开 `[from,to)`"（C-34/EC-P1-2） |
| S-10 | §4.1/§7.1.1 | `DatasetDep("positions_end")` + `MetricContext` 需 positions_end | 07 在 def 内基于 `trades+bars` 重建；`MetricContext` 不含 positions_end（E-06/EC-P0-4） |

> 建议实现前由 Lead 一次性提交"07 文档同步 PR"（纯文档，不改口径），与本设计的契约 PR 同批；否则 05 引擎与 07 defs 会在 `ci_sample`/视图/drilldown 接口上各写一版。
