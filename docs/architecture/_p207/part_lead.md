# Period2-07 三域指标实现 —— 开发设计与测试用例设计（Lead 主稿）

> 状态：**v0.9 内部工作稿（供合并）** ｜ 日期：2026-10-26 ｜ 作者：AI（Team Lead）
> 最终交付：`docs/architecture/2026-10-26-metrics-three-domain.md`（合并 4 份 part 后成稿）
> 交叉评审：`docs/reviews/2026-10-26-DevDesign-Review-metrics-three-domain.md`（3 名独立评审）
> 需求来源：`docs/DEV-PLAN-v2.md` §3.2 Period2-07；口径来源 PRD-v2 附录 A1–A27。

---

## 0. 决策摘要（人工审核请重点拍板）

> 规则：**推荐方案是文档正文采用的方案**；备选方案保留完整取舍依据，人工可改判。
> 详细论证见 §12（每项含事实依据、成本、影响面）。编号 D-xx 对应 HITL-xx。

| # | 决策点 | 推荐方案 | 备选方案 | 成本/影响 |
|---|---|---|---|---|
| D-01 | Period2-05/06（指标引擎+端点）尚未实现，07 如何在"空地基"上落地 | **两层交付**：07 先交付 `core/stats` + `core/metrics` 纯函数 + `review/metrics/defs` + golden/oracle（不依赖引擎即可单测）；引擎/端点作为**硬前置**（先补 05/06），07 的 API 验收在 05/06 合并后联调 | ① 把 05/06 最小子集并入 07（估时 +2.5~3d，任务边界模糊）；② 严格串行：05/06 全做完再启动 07（周期最长，但返工最少） | 推荐方案可并行开工且返工最小；07 端到端可用日期取决于 05/06 |
| D-02 | replay 行情月频 9 点，择时域需要 20 日窗口/h=5,10,20/日频 β/IR | **新增独立确定性日线夹具** `fixtures/market/daily_bars.json`（≥2 年工作日序列，4 条序列含 `hs300_tr`），replay 适配器按请求切换日线视图；**不动既有 `quotes.json`**（nav/资产表期望值零回归） | ① 直接把 `quotes.json` 换成日线（blast radius 大：nav/asset_daily 全部 golden 重算）；② live-only 日线（测试不可复现，违反 Testing D6）；③ 月频近似（违反 A17/A16/A23/A26） | 推荐 +0.5~1d（夹具生成器+适配器+测试改写）；备选① 额外 +2~3d 回归修复 |
| D-03 | `hs300_tr` 基准日线在 live 下无指数适配（`market_live.py` 只支持 6 位 A 股） | **replay 有基准日线即可验收**；live 指数适配单列为后续任务；live 缺基准时 `timing.*` 返回 `status=blocked`（`reason="基准数据不可用"`），**绝不静默用 0/等权** | ① 本期补 akshare 指数适配（+1d，依赖网络/限频，CI 不可测）；② 用户自备基准 CSV 导入（新增导入流程，P2.x） | 推荐满足本地验收剧本；live 下择时域不可用需在 UI 降级（已有 blocked 卡片机制） |
| D-04 | 选股域 PRD 要求 PIT/财报/行业归属，但本仓无财报、无申万行业、无 PIT 财务表；API-038 验收断言写"不含晚于 as_of 的财报数据" | **本期只做价格类 PIT cohort BHAR 20/60**：cohort=买入日、as_of 门禁作用于行情与事件；**行业归属/风格分段/财报类 PIT 另立任务**；同步修订 API-038 验收断言（去掉"财报数据"表述，改为"行情/事件 as_of"） | ① 引入财报+行业数据源（+5~8d，需 PIT 版本化表、数据校验，超出 07 范围）；② 整个 selection 域推迟到 07b/后续期次 | 推荐可交付 2 个 P0 指标；备选①显著超期 |
| D-05 | metric_id 命名：架构契约要求 `^(selection\|timing\|behavior\|attribution\|perf)\.[a-z0-9_]+$`，API-Catalog 断言用 flat id（`win_rate/de/bhar_20`）；`definition_version` 起始值 Catalog 示例为 `2.1` | **采用 dotted id**（`perf.win_rate`、`behavior.de`、`selection.bhar_20`、`timing.drawdown`），全部 `definition_version="1.0"`；同步修订 Catalog §2.3/§6/§4 的示例与断言 | ① flat id + 放宽注册表正则（架构债，未来域前缀无处安放）；② 双 id 别名映射（多一层映射与漂移风险） | 推荐零映射成本；需改 Catalog 文档（实现阶段随 `make api-snapshot` 生效） |
| D-06 | 任务列了 18 个指标、估时 3.5 人日；本仓 05/06 未建，且每个指标要求 golden + mutation ≥80% | **分两批**：07a = 核心 12 个（含 CI/explain/drilldown/golden/mutation，约 6.5~7d）；07b = 其余 6 个 exploratory（约 2d）；**把 DEV-PLAN 3.5d 修正为 8.5~9.5d（不含 05/06 的 5d）** | ① 严格 3.5d：只交付 6 个指标（违反"每个指标 1 份 golden"验收）；② 18 个全做完整实现（≈10.5d+）；③ 砍掉独立 oracle 只留手算 golden（违反 Testing D2"D2 独立 oracle"） | 推荐是唯一能同时满足"18 指标可算 + 每个 golden + mutation"的排期；需人工确认估时修正 |
| D-07 | bootstrap CI 的随机性与 golden 可复现冲突（引擎约定"Random 由 Clock 派生"） | **seed = sha256(inputs_hash)[:8] 的 64 位整数**（与 Clock 无关，结果可复现）；B=1000，percentile 法（2.5/97.5 最近秩+线性插值）；PRNG 用 `core/stats/numeric.py` 的 xorshift64* 纯整数实现 | ① BCa（偏差校正，n≥30 更准，+1d 且实现复杂）；② Clock 派生 seed（golden 每次跑可能不同，不可接受）；③ 正态近似 CI（DE 分布偏态，不推荐） | 推荐零额外成本且确定性；备选②破坏 D6 |
| D-08 | CI 最小样本：PRD A26"N<10 只出原始值、N<30 附 CI"；`constants.MIN_CI_SAMPLE=10` 与引擎文档 `ci_min_n=30` 冲突；API-033 断言 fx_n5 仍返回 value | **`min_n=10`（低于则 `sample_status=insufficient`，仍返回 value+n，无 CI、无 conclusion）；`ci_min_n=30`（n∈[10,29) 有 value 无 CI）**；`blocked/stale/unreliable` 时 API **无 `value` 键** | ① 沿用 `MIN_CI_SAMPLE=10`（n=10 就给 CI，与引擎/A26 不一致）；② 非 ok 一律不返回 value（与 API-033 断言 fx_n5 冲突，需改 Catalog） | 推荐与引擎 §3.2/A26/API-033 三处最大公约数；需改 `constants.MIN_CI_SAMPLE` 命名或新增 `MIN_CI_SAMPLE=30`（见 §8 契约修订） |
| D-09 | 交易日历：现有 `business_days_between` 只算周一~周五，无 A 股节假日；round.holding_days 已按此落库 | **M1 沿用工作日近似**，在所有用到它的指标 `assumptions` 显式写 `calendar=weekday_proxy`；真实 A 股日历（含节假日/停牌）单列为后续任务（需重算 round + 指标 version bump） | ① 本期引入内置真实交易日历（A13/回撤窗口更准，但 `round.holding_days` 与指标口径分叉，或需全量重建 round，+1~2d）；② 用行情 bar 交易日作为日历（仅覆盖有行情的标的，节假日仍缺） | 推荐零迁移；备选①口径更准但影响面大 |
| D-10 | A14 集中度：分母"仅 pnl>0"；Gini/CR1 的分母与负 pnl 处理未定义 | **全部集中度统计只在盈利回合（pnl>0）上定义**：Top3=前 3 大盈利/Σ盈利，CR1=最大盈利/Σ盈利，Gini 在盈利 pnl 分布上按升序计算；`n_pos<2` → `insufficient`；`n_pos<3` → Top3 退化 = 全部盈利占比并在 decomposition 标注 | ① Gini 在含亏损的 pnl 上计算（需平移，经济含义不同）；② Gini 在 \|pnl\| 上计算（可比性差） | 推荐与 A14 原文"分母仅含 pnl>0"一致；需在 PRD 补一句 Gini 边界 |
| D-11 | A22 "吃掉 TWR 的 Y%" 未给公式；TWR≤0 时分母无意义 | **输出两个值**：`fee_drag_ratio = 年化费用/年化平均持仓市值`（主 value）；`twr_eaten_pct = 年化费用/(\|年化 TWR\|+年化费用)`，仅当 `\|年化 TWR\|+年化费用>0` 时输出，否则 `null` + `status=unreliable` | ① `Y% = fee_drag_ratio / \|年化 TWR\|`（TWR 为负时符号混乱）；② 只输出费用率，不出 Y%（信息量少，但最稳） | 推荐公式自洽且可解释；需 PRD 附录 A22 补公式 |
| D-12 | 回撤族数据源：`nav_daily` 按月稀疏；`asset_daily` 逐自然日且有现金流调整后 `daily_return_units` | **用 `asset_daily.daily_return_units` 复利链构造日度 TWR 指数**，MDD 族从该指数计算；`build.state != current` → `status=stale`；区间内存在 `status=blocked` 行 → `status=blocked`；`assumptions` 写 `nav_source=asset_daily.daily_return` | ① 给 `asset_daily` 加 `unit_nav_units` 列（避免复利链误差，+1d 迁移与重建）；② 用 `nav_daily`（月频，日频 MDD 不可得，拒绝） | 推荐零迁移；复利链与 TWR 定义一致（A4/A5），误差远小于 1bp |
| D-13 | HHI/换手/A13 资金占用需要**每日逐标的持仓**，`asset_daily` 只有聚合值 | **指标层内存重建**每日持仓：复用 `valuation.closing_price_ladder/close_on_or_before/holding_delta`，按 trade 稳定序重放（复杂度 O(T log S + D·S)，个人账 10 年 ≤1 万行、≤500 天量级，预算 <200ms）；不新增表、不落库 | ① 新增 `review.asset_daily_holding` 明细表（Period2-16 备选 D1，+1.5d、存储大、增量逻辑复杂）；② 用期末快照近似（日频 HHI 不可得，口径降级） | 推荐满足口径且零迁移；性能需在 Pf-2 点测 |
| D-14 | rf（无风险利率）无数据源；PRD 禁止默认 0 | **配置项 `rf_annual` 默认 `0.015`**（1 年期国债近似），在 `assumptions.rf_rate` 显式输出；后续 Period2-11 设置页可改 | ① 内置静态 rf 曲线（2015–2026，维护成本）；② 用户自填（设置页未做，默认值仍需给） | 推荐最少实现；默认值需人工确认（0.015 还是 0.02） |
| D-15 | API-035 下钻断言"每项金额匹配 `^-?\d+\.\d{2}$`"对比率型指标不成立 | **按 `unit` 分格式断言**：CNY→2 位定点；ratio→8 位小数；days/count→整数字符串；z→4 位小数。item 增加 `unit` 字段；修订 Catalog API-035 断言② | ① 所有指标强塞金额字段（语义错误）；② 下钻只返回金额型指标（信息缺失） | 推荐契约小改（additive field + 断言细化） |
| D-16 | 变异测试工具：仓库无 mutmut/cosmic-ray 配置、无 CI workflow；文档写 `core/src/core`，实际是 `packages/core/src/stock_core` | **引入 `mutmut`（pinned）+ `make mutation`**：新模块（`stock_core/stats`、`stock_core/metrics`）≥80%；现有 core 维持 ≥90/100 不变；PR 只跑 diff 文件、nightly 全量；路径按实际 `stock_core` 修正文档 | ① cosmic-ray（并行快，但与 Testing.md 全文不一致，改文档面积大）；② 不跑变异，用 golden+属性替代（违反 Period2-07 验收） | 推荐与既有文档一致；+1d 配置与首轮变异 |
| D-17 | **演示数据只有 3 个月（2026-07~09）月频行情 9 点**，而 A26 门槛要求日频 ≥60 交易日才出夏普/回撤/IR；二期验收剧本 §3.1 要求"五张卡片有数" | **扩展 `seed-demo` 演示数据到 ≥12 个月（工作日级行情 + 合成流水）**，保证 timing/behavior 域在演示环境达到 n≥60；`make seed-demo` 仍确定性、字节可复现 | ① 保持 3 个月，验收剧本改为"卡片显示数据不足"（与 §3.1 冲突，但改动最小）；② 只给测试夹具加长数据，演示环境仍不足（演示剧本不可达） | 推荐才能满足既定验收剧本；+1d（夹具生成器扩展 + seed 重放验证） |
| D-18 | API-038 异步诊断 handler 是否在 07 范围（`JobType.DIAGNOSTICS_SELECTION` 已存在但无 handler/迁移） | **07 只交付 selection 域的 defs + 数据视图**；`POST /diagnostics/selection` handler 归入 06 的端点补齐（或 07b），07 的 API 验收用直接构造 metric_result/同步计算路径覆盖 | ① 07 一并实现 handler（+1d，但能端到端验收 Catalog API-038 的 202 流程）；② 完全不做（Catalog API-038 无法验收） | 推荐任务边界清晰；需在排期里显式安排 handler |

---

## 1. 背景与目标

### 1.1 背景

- DEV-PLAN-v2 §3.2 **Period2-07「三域指标实现」**：绩效/择时/行为/选股四类指标的**口径实现**，本地验证要求"每个指标 1 份 golden（期望值来自手算表/独立实现）+ mutation ≥80%"，估时 3.5 人日。
- 上游依赖 **Period2-05（指标注册表与执行引擎）**、**Period2-06（指标端点 + 三段式）**；本仓当前这两个任务**尚未实现**（无 `review.metric_result` 迁移、无 `review/metrics` 包、无 `/metrics` 路由，见 §2 事实表）。
- 可用的数据地基已就绪：`review.round/round_fill`（FIFO 回合）、`review.nav_daily`（月频净值）、`review.asset_daily`（逐自然日资产 + 现金流调整日收益）、`ledger.trade`（含佣金/印花税/其他费列）、行情端口 `MarketDataSource`（replay 月频 + live 日线不复权）/`market.daily_bar` 缓存。

### 1.2 目标（可验收）

1. 18 个指标（§3）的**口径公式**落为 `stock_core` 纯函数，禁 float、确定性、零 I/O、零时钟。
2. 每个指标 1 份 **golden**（期望值来自手算表或独立朴素实现，禁止用被测代码生成），并有独立 oracle 与生产实现的**三方比对**。
3. 指标定义文件可被引擎**自动发现**（放文件即注册，不改 registry/`__init__`），`MetricDef` 13 字段齐全。
4. 每个指标在 `n<10 / 10≤n<30 / n≥30` 三档下的 `sample_status`、CI、`conclusion` 行为正确；`blocked/stale/unreliable` 不出数字。
5. 新增模块 **mutation score ≥80%**（现有 core 不退化）。
6. 二期验收剧本 §3.1 第 1/5 步在 replay 模式可跑通（五卡有数、`curl .../metrics?ids=...` 返回 n/ci/sample_status）。

### 1.3 非目标（范围围栏）

- **不重做引擎与端点**：注册表/框架代算/DAG/失效/`metric_result` 物化/`GET /metrics`/`explain`/`drilldown` 归 Period2-05/06；本任务只写"引擎要调用的东西"（见 §4 契约）。
- 不做前端看板、月报、归因（Period2-08/09/12）、趋势断点（Period3-02）、行为规则提醒（Period3-03）。
- 不做 A20 夏普、A25 Brinson/Carino、行业归属、财报类 PIT、真实 A 股交易日历、live 指数行情（均在 §3.3 列出理由与后续归属）。
- 不改动既有 `/nav`、`/assets`、`/rounds` 的响应结构与口径（只读消费）。

---

## 2. 现状与差距（事实表）

| # | 现状 | 证据（文件:行） | 对 07 的影响 |
|---|---|---|---|
| F1 | 无指标引擎：无 `review/metrics/` 包、无 `MetricDef` Protocol 实现、无 DAG/失效/物化、无 `review.metric_result` 迁移（迁移到 0006 为止） | `backend/migrations/versions/`（0001~0006）、`backend/packages/review/src/stock_review/models.py:302` | 07 的 defs 无法端到端运行 → D-01 |
| F2 | 无 `/metrics` 路由（API-033/034/035/079 未实现） | `backend/services/api/src/stock_api/routers/`（无 metrics.py） | API 验收不能在本任务单独完成 |
| F3 | 回合引擎已落库：`round.status ∈ closed/open/data_error`、`pnl_units/return_rate_units/holding_days/fills_count`、`round_fill` 逐 chunk 配对 | `models.py:49-131` | 绩效/行为域的**主数据源就绪** |
| F4 | `round.holding_days` 用周一~周五近似（`business_days_between`） | `stock_core/fifo/calendar.py:12-22`、`fifo/engine.py:264` | A13 口径限制 → D-09 |
| F5 | `nav_daily` 是**"有流水的日期 + 期末"稀疏行**，不是逐日 | `models.py:158-205`、`application/nav.py` | 回撤族不能用 nav_daily → D-12 |
| F6 | `asset_daily` 逐自然日，含 `daily_return_units`（现金流调整）、`status ∈ ok/blocked/degraded`、缺行情行 blocked 且数值 NULL | `models.py:207-257`、`application/assets.py:63` | 回撤族的可靠数据源（需 build current） |
| F7 | `trade` 有 `commission_units/stamp_tax_units/other_fee_units/amount_units/category` | `packages/ledger/src/stock_ledger/models.py:163-174` | A21/A22 费用口径可算 |
| F8 | 行情 replay 夹具月频 9 点（4 条序列含 `hs300_tr`），生成器 `_market_rows` 只产月频 | `fixtures/market/quotes.json`、`ledger/fixtures.py:186-248` | 择时域 replay 数据不足 → D-02 |
| F9 | live 适配器只落不复权日线、只支持 6 位 A 股代码；基准指数无适配 | `platform/market_live.py:1-40`、`ports.py:132-160` | live 下 β/IR 缺基准 → D-03 |
| F10 | 契约枚举已备：`MetricStatus/SampleStatus/ScopeType/JobType.DIAGNOSTICS_SELECTION` | `contracts/enums.py` | 直接复用，避免新增枚举 |
| F11 | 常量 `MIN_CONCLUSION_SAMPLE=10`、`MIN_CI_SAMPLE=10`、`A_SHUFFLE_DAYS=244`；引擎文档 `ci_min_n=30` | `contracts/constants.py:30-36`、`Extensibility.md §3.1` | 边界不一致 → D-08 |
| F12 | API-Catalog API-033 验收断言 flat id 与 fx_n5 返回 value；API-035 断言金额 2 位；API-038 断言 as_of 财报 | `API-Catalog.md` §2.3/§6 | 契约修订 → D-05/D-08/D-15/D-04 |
| F13 | 无 mutmut/CI workflow；`make verify-p2` 不存在 | 根 `pyproject.toml`、`Makefile` | 变异/验收门禁需新建 → D-16 |
| F14 | 演示数据 3 个月（`months=3,count=120`）、`DEMO_CLOCK=2026-10-01`、行情月频 9 点 | `cli.py:45`、`cli.py:615-631`、`fixtures.py:186` | 二期验收剧本"五卡有数"数据不足 → D-17 |

---

## 3. 范围：指标目录、命名与版本

### 3.1 指标目录（18 个原子指标；冻结候选）

> **拆分原则**：可独立解释/下钻/golden 的才拆独立 id；并列变体（median、全样本、h=5/10、持续时间）放 `decomposition`，避免指标爆炸并守住 A26"核心结论 8–12"预算。
> `core` = 进行动建议/核心结论；`expl` = exploratory（`include_exploratory=false` 时默认不返回）。

| # | metric_id | 域 | 口径 | 主数据源 | 级别 | 依赖（指标/数据集） | 单位 | 关键边界 |
|---|---|---|---|---|---|---|---|---|
| 1 | `perf.win_rate` | perf | A10 已平仓胜率 + Wilson 95% CI；decomp 全样本胜率（含浮盈亏）、n_open、n_closed | round + bars | core | — | ratio | pnl=0 不计胜；n=0 → insufficient/blocked |
| 2 | `perf.pnl_ratio` | perf | A11 平均盈利/平均亏损绝对值；decomp median 版、avg_win/avg_loss、n_win/n_loss | round | core | — | ratio | 全胜/全亏 → insufficient |
| 3 | `perf.holding_days` | perf | A13 交易日计均值；decomp median、自然日均值、资金占用天数 | round | expl | — | days | 工作日近似（D-09） |
| 4 | `perf.concentration` | perf | A14 Top3 盈利占比；decomp CR1、Gini、n_pos | round | expl | — | ratio | 仅 pnl>0；n_pos<2/3（D-10） |
| 5 | `perf.fee` | perf | A21 费用合计/双边成交额；decomp 佣金/印花/其他费、费用金额、成交额 | trade | core | — | ratio | 禁止二次扣费；成交额=0 → blocked |
| 6 | `perf.fee_breakeven` | perf | A22 年化费用/年化平均持仓市值；decomp 吃掉 TWR 比例、年化费用、年化平均市值 | trade + asset_daily | core | `perf.fee`、asset_daily | ratio | TWR≤0（D-11） |
| 7 | `perf.expectancy` | perf | A12 `E=p·W−(1−p)·L`；decomp 盈亏平衡胜率 p* | round | expl | `perf.win_rate`、`perf.pnl_ratio` | CNY | 全胜/全亏 → p* 无意义 → insufficient |
| 8 | `timing.chase` | timing | A17 买入追涨分位均值；decomp z 版均值、coverage、n_buys | 日线 bars + trade | core | daily bars | ratio | 窗口 max=min → insufficient；<20 日 → pending_window |
| 9 | `timing.fly` | timing | A16 卖飞率（z>1.0 且 h=20 占比）；decomp h=5/10、n_events、剔除数 | 日线 bars + benchmark + trade | expl | daily bars、benchmark | ratio | 停牌>50%/未走完剔除（见 §6） |
| 10 | `timing.beta` | timing | A23 β（对基准超额收益回归）；decomp α 年化、t(α)、IR、TE、超额（算术/几何）、n_days、benchmark_id | asset_daily + benchmark | core | asset_daily、benchmark、`rf_annual` | ratio | n_days<60 → pending_window |
| 11 | `timing.drawdown` | timing | A18 MDD；decomp 持续期、恢复期、水下占比、Ulcer、Calmar | asset_daily | core | asset_daily | ratio | blocked 日 → blocked；<60 → pending_window |
| 12 | `behavior.de` | behavior | A15 `DE=PLR−PGR` + bootstrap 95% CI；decomp PGR、PLR、持有天数差 | round | core | — | ratio | n<10 insufficient；bootstrap seed（D-07） |
| 13 | `behavior.turnover` | behavior | 期间双边成交额/平均持仓市值（年化可选 decomp） | trade + asset_daily | expl | — | ratio | 平均市值=0 → blocked |
| 14 | `behavior.averaging_down` | behavior | 摊平买入笔数占比（定义见 §6/part）；decomp 次数、平均深度、摊平后 20 日收益差 | trade + 日线 | expl | daily bars | ratio | 无摊平 → value=0 + ok（可解释） |
| 15 | `behavior.hhi` | behavior | A19 HHI_total 日均值；decomp HHI_eq 均值/最大、HHI_total 期末、n_days | 内存重建持仓 + 日线 | expl | daily bars、asset_daily | ratio | 无持仓日 → insufficient |
| 16 | `selection.bhar_20` | selection | A24 BHAR_20 均值；decomp 中位数、t（事件日聚类）、n、CI | 日线 bars + benchmark + round | core | daily bars、benchmark | ratio | 事件不足/窗口未走完剔除 |
| 17 | `selection.bhar_60` | selection | A24 BHAR_60 均值；同 16 | 日线 bars + benchmark | core | 同 16 | ratio | 60 日窗口 → 样本更少 |
| 18 | `selection.bhar_hit` | selection | BHAR_20>0 事件占比（选股胜率辅助） | 日线 bars + benchmark | expl | `selection.bhar_20` | ratio | 可砍；n<10 insufficient |

**核心结论预算**：core 共 10 个（1/2/5/6/8/10/11/12/16/17），落在 A26 的 [8,12] 区间内；expl 8 个默认不返回（`include_exploratory=false`）。

### 3.2 命名与版本规范

- **id**：`^{domain}\.[a-z0-9_]+$`，`domain ∈ {perf,timing,behavior,selection}`；**一经发布永不复用**（Extensibility §3.1）。
- **definition_version**：本期全部 `"1.0"`。MAJOR = 分母/方向/样本定义变化（趋势强制断点）；MINOR = 精度/窗口/文案（详见 Extensibility §3.3）。
- **口径变更纪律**：改公式必须升 `definition_version` + golden 独立 PR + `golden-approved` 标签（Testing §12.3）；未升版而快照变化 → CI fail。
- **单位**：`CNY`（金额 2 位定点串）/`ratio`（8 位小数串）/`days`/`count`/`z`（4 位小数）/`bp`。内部一律标度整数（`SCALE_AMOUNT=2`、`SCALE_RATIO=8`），禁止 float/Numeric。

### 3.3 范围外清单（写清后续归属）

| 项 | 本期不做理由 | 后续归属 |
|---|---|---|
| A20 夏普 | 依赖 rf 与 60 日门槛，可由 `timing.beta` 的日收益序列顺带得到；本期核心预算已满 | Period2-11 设置页后 / 07b |
| A25 Brinson / 行业归属 | 无申万行业数据、无财报 | Period3-08（归因增强） |
| 真实 A 股交易日历 | 需内置日历数据 + 重算 round + 口径 bump（D-09） | 后续任务（趋势/回撤精度） |
| 财报类 PIT / 选股质量财务因子 | 数据缺失（D-04） | 独立任务（数据源建设） |
| live 指数基准适配（akshare index） | 网络依赖、CI 不可测（D-03） | Period2-11 / 运维任务 |
| 前端卡片/抽屉/报告 | 非本任务产出（Period2-12/09） | Period2-12 / 09 |
| 指标趋势断点 / 提醒规则 | 依赖 Period3-02/03 | Period3-02/03 |

### 3.4 与上游任务的边界

- **Period2-05 提供**：注册表自动发现、`MetricContext` 数据视图、框架代算（n/CI/sample_status/status/inputs_hash/data_as_of）、DAG/失效、`metric_result` 物化、门禁状态机。
- **Period2-06 提供**：`GET /metrics`（批量/过滤）、`/metrics/{id}`、`/explain`、`/drilldown`、ETag、`data_as_of` 透出。
- **Period2-07 提供**：`core/stats`（统计内核）、`core/metrics`（纯公式）、`review/metrics/defs/*`（18 个定义）、`tests/golden/*`、`tests/oracle/naive_metrics.py`、mutation 配置与测试。
- **Period2-16 提供**：`review.asset_daily` 与 build 状态（07 只读，要求 `build.state=current`）。

---

## 4. 冻结契约（07 与 05/06 的接口）

> **本节是 07 实现的"编译期契约"**：07 的 defs 只依赖这些形状；实现期若 05/06 设计与此不同，以本节为基准发起契约对齐（记录在评审文档）。

### 4.1 引擎契约（来自 Architecture §11 + Extensibility §3.1/§3.2，**不新增字段**）

```python
# review/metrics/defs/<domain>_<name>.py —— 每个指标一个文件，放入即被发现
class WinRateDef:                       # 结构示意（具体基类由 05 提供）
    id = "perf.win_rate"
    definition_version = "1.0"
    domain = Domain.PERF
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = WinRateParams      # extra="forbid"
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=10, ci_min_n=30, ci="wilson")
    status_policy = (StatusRule("upstream=blocked"), StatusRule("upstream=stale"))
    unit, precision = Unit.RATIO, 8
    core_conclusion, frequency, benchmark_required, mvp = True, "sync", False, "P0"

    def compute(self, ctx: MetricContext) -> MetricValue:   # 纯函数
        ...                                                  # 只返回 value/n/decomposition
    explain = ExplainSpec(conclusion_zh="...", method_zh="...", what_to_do_zh="...")
    drilldown = DrilldownSpec(endpoint="/metrics/perf.win_rate/drilldown", params={"period": "{period}"})
```

- `MetricValue`：`value_units: int | None`、`n: int`、`decomposition: dict[str, int|None]`、`n_pending: int`。
- 引擎负责：`sample_status`（min_n/ci_min_n）、CI（`sample_policy.ci`）、`status`（status_policy + upstream）、`assumptions`（数据血缘）、`inputs_hash`、`data_as_of`、`computed_at`、持久化与失效。
- **purity**：`compute` 内禁 socket/open/sqlalchemy/session/`datetime.now`/`random`/float；由契约 kit `assert_purity` 静态+动态扫描。
- **错误处理**：`compute` 不得抛业务 `ApiError`；上游不可用由 `status_policy` 表达；除零等用 `MetricValue(value=None, n=0)` + decomposition 提示，由引擎判 `unreliable/blocked`（具体由 05 的 `status.py` 实现，07 只提供数据视图信号）。
- **ctx 数据视图**（07 建议命名，05 可微调但语义不变）：

```python
class MetricContext:        # 引擎注入，只读
    user_id: UUID
    period: Period          # start/end（闭区间，业务日 Asia/Shanghai）
    as_of: date             # PIT 门禁；默认 clock.today()
    rounds: RoundsView      # closed/open/data_error + pnl/cost/holding_days/close_date/open_date
    fills: FillsView        # round_fill 明细（下钻/费用归因需要）
    trades: TradesView      # category/amount/fees/quantity/price/trade_date
    asset_daily: AssetDailyView   # date/total/daily_return/status/build_state
    bars: BarsView          # 日线序列（含 benchmark），带 as_of 截断
    benchmark: BenchmarkView      # hs300_tr 日收益/净值，缺基准时 available=False
    calendar: CalendarView        # 交易日（M1=weekday proxy，带 assumption 标注）
    rf_annual: Decimal            # 来自 settings（默认 0.015）
    holdings_daily: HoldingsView  # 由 07 提供的内存重建结果（HHI/turnover/浮盈亏用）
```

### 4.2 端点契约（API-033/034/035/079/038，07 只消费）

- `MetricResult` 字段固定（§2.2 brief）：`metric_id/definition_version/scope/period/value?/unit/n/ci_low/ci_high/sample_status/status/assumptions/data_as_of/inputs_hash/computed_at`。
- **value 出现规则（07 必须保证、06 在 API 层执行）**（依据：Catalog §0.8 "`blocked` 时 `value` 字段缺省" + API-033 断言②③）：
  | status | sample_status | value | ci_low/high | conclusion |
  |---|---|---|---|---|
  | `ok` | `ok`（n≥30） | 有 | 有 | 有 |
  | `ok` | `ok`（10≤n<30） | 有 | 无（无 CI） | 有（可出非统计结论）|
  | `ok` | `insufficient`（n<10） | **有（原始值）** | 无 | 无（null） |
  | `ok` | `pending_window`（日频 <60） | 有 | 无 | 无 |
  | `blocked/stale/unreliable` | `blocked` 或任意 | **无 value 键** | 无 | 无 |
- `explain`：`conclusion` 禁裸术语（词表见 §7）；非 ok → `conclusion=null` + `reason/what_to_do` 非空。
- `drilldown`：`items[]` 至少含 `key/round_id?/trade_id?/value/unit/label`；`source_endpoints[]` 可复核；`params` 回填 `period`。
- `ETag`/`304`、`data_as_of`、`definition_version` 由 06 统一；07 提供 `inputs_hash` 稳定性（同输入两次计算必须同 hash）。

### 4.3 05/06 前置清单（07 开工前必须就绪，否则只能做 §4.3 的"冻结层"）

| # | 前置项 | 验收标准 | 归属 |
|---|---|---|---|
| P1 | `review/metrics/` 包 + `MetricDef` Protocol + 自动发现 | 放一个测试 def 即被发现；registry 启动校验 id 唯一/正则 | 05 |
| P2 | `MetricContext` 数据视图 + `metric_result`/`metric_dependency` 迁移（含 RLS） | 迁移 up/down；RLS 门禁覆盖 | 05 |
| P3 | 框架代算 `sample_status/CI/status/inputs_hash/data_as_of` | 契约 kit `assert_sample_gate(n=0/5/9/10/29/30/59/60)` 全绿 | 05 |
| P4 | `GET /metrics`、`/{id}`、`/explain`、`/drilldown` | API-033/034/035/079 契约测试（C-xxx）可跑 | 06 |
| P5 | `ci_min_n=30`、`MIN_CONCLUSION_SAMPLE=10`、words 词表常量集中 | `contracts/constants.py` 与该设计一致 | 05/D-08 |

**并行策略**：若 05/06 未就绪，07 先落 `core/stats` + `core/metrics` + defs + golden + oracle + mutation；这些单测不依赖引擎（直接调纯函数、直接调 def.compute 用 fake ctx），可作为 05/06 的"下游第一个消费者"反向驱动契约。

---

## 5. 统计内核设计（`stock_core/stats`）

> 新模块：`backend/packages/core/src/stock_core/stats/`；纯函数、零 I/O、零时钟、零随机源（随机由显式 seed 传参）。
> 全部走 `Decimal`（上下文 `prec=28`）或整数；**禁止 float**；输出统一回写为标度整数。

### 5.1 模块划分

| 文件 | 职责 | 关键函数（签名草案） |
|---|---|---|
| `numeric.py` | 定点/整数工具 | `to_units(dec, scale)->int`；`from_units`；`div_round(a,b,scale)`；`sqrt_units(a_units,scale)`；`xorshift64(seed)->prng`；`percentile(sorted_units, q_permille)` |
| `wilson.py` | 二项比例 CI | `wilson_interval(success:int, n:int, z_milli:int=1959964, scale=8)->(int|None,int|None)` |
| `bootstrap.py` | 确定性 bootstrap | `bootstrap_percentile(samples: Sequence[int], stat: Callable[[Sequence[int]], int], *, b=1000, seed:int, alpha_milli=(25,975))->(lo,hi)` |
| `regression.py` | OLS β/α、t、IR | `beta_alpha(y_units, x_units, rf_daily_units, scale=8)->BetaAlpha(beta,alpha_daily,alpha_annual,t_alpha,ir,te,n)` |
| `concentration.py` | Gini/CR1/TopK/HHI | `gini_units(sorted_values, scale=8)`；`top_k_share`；`hhi(weights_units, scale=8)` |
| `drawdown.py` | 回撤族 | `drawdown_family(index_units: Sequence[int], annualized_twr_units:int, scale=8)->DrawdownFamily(mdd,duration,recovery,underwater,ulcer,calmar)` |
| `tstat.py` | 聚类 t 值 | `cluster_t(events: Sequence[tuple[date,int]], scale=8)` |

### 5.2 精度与舍入

| 量 | 内部表示 | 输出 | 舍入 |
|---|---|---|---|
| 金额 | `int` 分（scale=2） | `"1234.56"` | ROUND_HALF_UP |
| 比率 | `int` 1e-8 | `"0.43210000"` | ROUND_HALF_UP，8 位 |
| 价格 | `int` 1e-6 | `"12.340000"`（如需） | 6 位 |
| 日数/计数 | `int` | `"12"` | 无 |
| z 值 | `int` 1e-4 | `"1.2345"` | 4 位 |
| 中间计算 | `Decimal(prec=28)` | — | 最后一步才量化 |

规则：**除法/开方/幂一律在 Decimal 域完成，量化只在函数出口做一次**；禁止中途截断（否则偏差累积 >1bp）。`Decimal.sqrt()`/`**` 使用本机 `decimal` 上下文，测试断言 1bp 容差；CI 断言与独立 oracle 比对。

### 5.3 确定性与随机

- `seed = int.from_bytes(sha256(f"{metric_id}|{definition_version}|{inputs_hash}".encode()).digest()[:8], "big")`。
- PRNG：xorshift64*（64 位掩码，纯整数），`next() % n` 取下标（n ≤ 10^4 时模偏差 < 1e-12，可接受，文档注明）。
- `inputs_hash` 由引擎提供（排序后的 params + 依赖数据集版本 token）；07 的纯函数**不得**自行读时钟/随机。
- bootstrap 稳定性测试：同 `(samples, seed, b)` 两次调用逐位相等；不同 seed → 结果应不同（防"忽略了 seed"）。

### 5.4 Wilson CI（默认用于胜率/占比类）

公式（z=1.959963985，95%）：`center=(p+z²/2n)/(1+z²/n)`，`half=z·sqrt(p(1−p)/n+z²/4n²)/(1+z²/n)`；`p=success/n`。
边界：n=0 → `(None,None)`；success=0/n → CI 下界 0、上界>0；success=n 对称。实现只用 Decimal；对 `wilson(7,10)` 手算断言 `(0.3968, 0.8922)`（容差 1e-4）写入单测。

### 5.5 bootstrap CI（DE/BHAR/期望值等）

- 样本单元：DE 用"卖出决策机会"（平仓回合 + 期初/期末 open 持仓的浮盈状态）；BHAR 用事件（买入日）。
- 统计量函数 `stat(samples)` 为纯函数（给定样本序列返回标度整数），bootstrap 只负责重采样。
- 重采样：B=1000，每次有放回抽 n 个；统计量序列排序后取 2.5%/97.5% 分位（线性插值：`lo + (hi-lo)*frac`，全部 Decimal）。
- 输出：`(lo_units, hi_units)`；n<30 → 调用方不请求 CI（引擎按 `ci_min_n` 控制）。

### 5.6 β/α/IR 与聚类 t 值

- 日收益：`r_p,t` 来自 `asset_daily.daily_return_units`（已是现金流调整后）；`r_b,t` 来自基准日线收益；`rf_d = rf_annual/244`（简单年化/244，写入 assumptions）。
- OLS：`β = Σ(x−x̄)(y−ȳ)/Σ(x−x̄)²`（x=超额基准收益，y=超额组合收益）；`α_d = ȳ − β·x̄`；`α_ann = (1+α_d)^244 − 1`（几何）；`t(α) = α_d / SE(α_d)`；`TE = sd(y−x)×√244`；`IR = mean(y−x)/sd(y−x)×√244`。
- 样本门槛：`n_days < 60` → `pending_window`（A26）；分母方差=0 → `unreliable`。
- BHAR t 值：事件日聚类（同一买入日的事件合并），`t = mean / (sd_cluster/√G)`，G=簇数；G<10 → insufficient。

### 5.7 回撤族

- 指数：`I_0=1`，`I_t = I_{t−1}×(1+daily_return_t)`（Decimal 链，scale 8）；区间内 blocked 日 → 该指标 `blocked`（不插值）。
- `MDD = max_t (peak_t − I_t)/peak_t`；`duration = argmax 时间 − 峰值时间`（交易日/自然日按 `asset_daily` 行数）；`recovery` = 从谷底回到前高的行数（期间内未恢复 → `null` + `n_pending`）；`underwater_ratio` = `I_t < peak_t` 的行数占比；`Ulcer = sqrt(mean(((I_t−peak_t)/peak_t)²))`；`Calmar = 年化 TWR / MDD`（MDD=0 → null）。
- 年化 TWR：由同一指数首末值按自然日年化 `(I_end)^(365/天数)−1`（与 nav.py:189 一致口径，见 `stock_core/nav.py:172-196`）。

### 5.8 边界与防御（统一）

`n=0/1/5/9/10/29/30/59/60` 逐档；除零（分母=0）；空样本；单元素（方差=0）；极端值（价格 0、数量 0）；超 int64（金额上限 9.2e18 分 = 9.2e16 元，足够）；Decimal `InvalidOperation` → 返回 None 由引擎判 `unreliable`（不抛异常）。所有函数返回 dataclass，禁止返回裸 tuple（便于演进）。

<!-- MERGE: part_perf -->

<!-- MERGE: part_timing -->

<!-- MERGE: part_behavior_selection -->

<!-- MERGE: part_test -->
