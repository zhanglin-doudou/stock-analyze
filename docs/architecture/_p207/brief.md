# Period2-07 三域指标实现 —— 设计任务书（内部工作稿）

> 本文件是团队内部工作稿（Team Lead 写给 4 位设计/测试设计同学），**不是最终交付物**。
> 最终交付物：`docs/architecture/2026-10-26-metrics-three-domain.md`（开发设计 + 测试用例设计）
> + `docs/reviews/2026-10-26-DevDesign-Review-metrics-three-domain.md`（交叉评审记录）。
> 评审通过且人工拍板后才进入实现。

## 0. 任务与交付

- 用户原始诉求：对 `docs/DEV-PLAN-v2.md` 二期任务 **Period2-07「三域指标实现」** 做
  ①开发设计 ②测试用例设计 ③交叉评审 ④列出需要人工决策的点（推荐方案 + 备选方案）。
- Period2-07 原文（DEV-PLAN §3.2）：
  > 三域指标实现：绩效（胜率含全样本、盈亏比、持仓天数、集中度 CR1/Gini、费用占比/回本门槛）、
  > 择时（追涨分位、卖飞率 z、β/α/IR、回撤族）、行为（PGR/PLR DE + bootstrap、换手、摊平、HHI）、
  > 选股（PIT cohort BHAR 20/60）｜依赖 Period2-05｜
  > 本地验证：每个指标 1 份 golden（期望值来自手算表/独立实现）+ mutation ≥80%｜3.5 人日
- **本阶段只出文档，不写生产代码、不改 API-Catalog/PRD**（契约修订清单在文档里列出，供人工批准后由实现阶段落）。

## 1. 仓库当前事实（设计必须建立在这些事实之上，去代码里核实）

| 事实 | 位置 | 对设计的影响 |
|---|---|---|
| `core` 已有 `fifo/`、`nav.py`、`values.py`（Money/Price/Qty/Ratio 标度整数） | `backend/packages/core/src/stock_core/` | 新指标公式一律落在 `core` 纯函数，复用 Scaled 族 |
| `review.round` / `round_fill` / `nav_daily` / `asset_daily` 已落库 | `backend/packages/review/src/stock_review/models.py` | 绩效/行为/回撤的数据源基本就绪 |
| `round.holding_days` 用**周一~周五工作日**近似交易日（`business_days_between`，无节假日） | `stock_core/fifo/calendar.py` + `engine.py` | A13 口径存在"周末近似"问题，需人工决策 |
| `review.asset_daily` 逐自然日、现金流调整后日收益 `daily_return_units`；无逐标的持仓明细 | 迁移 `0006_review_asset_daily.py`、`application/assets.py` | 回撤族可用；HHI/换手需在指标层内存重建每日持仓 |
| 行情端口 `MarketDataSource.get_daily_bars(symbols,start,end)`；replay 夹具是**月频 9 个点**（4 条序列，含 `hs300_tr`）；live（akshare）只落**不复权日线**、只支持 A 股 6 位代码 | `platform/ports.py`、`market_live.py`、`ledger/fixtures.py`、`fixtures/market/quotes.json` | 择时域（20 日窗口/h=5,10,20/日频 β/IR）在 replay 下**数据不足**，必须给出方案（HITL-2/3） |
| **Period2-05（指标注册表/引擎）与 Period2-06（/metrics 端点）尚未实现**：无 `review.metric_result` 迁移、无 `metrics/` 包、无 `/metrics` 路由 | 全仓检索 | 本任务是"在未建成的地基上设计"：需冻结引擎契约 + 给出前置清单（HITL-1） |
| 契约枚举已备：`MetricStatus(ok/stale/blocked/unreliable)`、`SampleStatus`、`ScopeType`、`JobType.DIAGNOSTICS_SELECTION` | `contracts/enums.py` | 直接复用，不加新枚举值 |
| 现有容差常量：`EPS_AMT=0.01`、`EPS_R=1bp`、`A_SHUFFLE_DAYS=244`、`MIN_CONCLUSION_SAMPLE=10`、`MIN_CI_SAMPLE=10` | `contracts/constants.py` | 与 PRD A26/引擎文档（ci_min_n=30）存在边界不一致，见 HITL-8 |
| 无 mutmut / cosmic-ray 配置、无 `.github/workflows`；`make verify-p2` 未存在 | 根 `pyproject.toml`、`Makefile`、`scripts/` | 变异门禁需随本任务新建（HITL-12） |
| API-033/034/035/079 已写在 API-Catalog，未实现；API-033 验收断言用 flat id（`win_rate/nav_return/de/bhar_20`） | `docs/architecture/v2/API-Catalog.md` §2.3/§6 | id 命名与 `MetricDef.id` 正则冲突（HITL-5） |
| 口径定义：PRD-v2 附录 A1–A27（A10 胜率/All、A11 盈亏比、A12 期望值、A13 持仓天数、A14 集中度、A15 DE、A16 卖飞率、A17 追涨分位、A18 回撤族、A19 HHI、A20 夏普、A21 费用、A22 回本门槛、A23 基准超额 β/α/IR、A24 BHAR、A26 统计门槛、A27 dedupe） | `docs/prd/PRD-v2.md` 附录 A | 指标公式的唯一口径来源；有歧义处必须标 HITL，不许自创 |
| 引擎契约（`MetricDef` 13 字段、框架代算 CI/status、DAG、scope_key/inputs_hash、失效、三重保障） | `docs/architecture/v2/Extensibility.md` §3、§11.1；`Architecture-v2.md` §11 | 本设计**不得改引擎契约**；07 只提供 defs + core 纯函数 + golden |
| 测试契约（golden 目录、oracle 三方比对、突变算子表、覆盖率） | `docs/architecture/v2/Testing.md` §9.3、§12.1、§12.3 | 测试设计必须逐条对齐 |

## 2. 冻结的引擎/端点契约（07 的调用约定，设计时按此假设）

> 这段是 07 与 05/06 的接口。07 的实现不得依赖引擎内部实现细节，只依赖这里的形状。
> 若发现此处与 05/06 设计必要冲突，记为 HITL 并在 part 文件里标注。

### 2.1 `MetricDef`（13 字段，来自 Extensibility §3.1）
`id / definition_version / domain / scope / params_schema / dependencies / compute / explain /
drilldown / sample_policy / status_policy / unit+precision / (core_conclusion,frequency,benchmark_required,mvp)`

- `compute(ctx) -> MetricValue`：**纯函数**，只返回 `value`（标度 int 或 None）、`n`、可选 `decomposition`、可选 `n_pending`；**不返回** ci/status/data_as_of，**不读时钟、不触网、不写库、不用随机**。
- 框架代算：`n`、`sample_status`、`ci_low/ci_high`、`status`、`assumptions`、`inputs_hash`、`data_as_of`、`computed_at`。
- 引擎按 DAG 注入数据集视图（`ctx.rounds / ctx.trades / ctx.asset_daily / ctx.bars / ctx.benchmark` 等，07 在 `MetricContext` 里声明所需视图字段；具体字段名以 05 设计为准，若未定则用本设计建议名并标注）。
- 阈值不写魔数：`min_n=10`、`ci_min_n=30`、`min_trading_days=60` 只来自 `sample_policy` / `contracts.constants`。
- `insufficient` ⇒ API 不返回 `value` 与 `conclusion`？—— 以 API-Catalog/Architecture 为准：**非 ok 状态不返回 value 与 conclusion**（Architecture §11 表 + §2.1 端点断言）。注意 API-033 断言 ③ 说 fx_n5 仍能返回 value；两处口径冲突，**必须在设计里明确取值规则并记 HITL**（建议：`insufficient` 仍返回原始 value + n，但 `conclusion=null`、无 CI；`blocked/stale/unreliable` 不返回 value）。

### 2.2 `MetricResult` 响应（API-033/079）
```
metric_id, definition_version, scope, period, value?, unit, n, ci_low, ci_high,
sample_status, status, assumptions, data_as_of, inputs_hash, computed_at
```
- `value` 为**定点字符串**：金额 2 位（`^-?\d+\.\d{2}$`）、比率 8 位小数（`0.43210000`）、天数/计数为整数字符串、z 值 4 位小数。**内部一律标度整数**。
- `blocked` 时 **无 `value` 键**（不是 null、不是 0）；`insufficient` 时 **无 `ci_low/ci_high` 键**。
- `assumptions` 必须含：`benchmark_id`（若用到）、`rf_rate`（若用到）、`A=244`、`calendar`、`nav_source`、`pit_rule`、`adjust`（等，按指标）。
- 每个响应含 `data_as_of`、`definition_version`；`definition_version` 本期统一 `"1.0"`（口径变更才升）。

### 2.3 explain / drilldown（API-034/035）
- `explain`：`conclusion`（人话，**禁裸术语** DE/BHAR/PGR/PLR/TWR/MWR/FIFO/HHI/Wilson/β/α/IR）+ `method`（一句话口径）+ `drilldown{endpoint,params,description}`；非 ok 时 `conclusion=null` + `reason/what_to_do` 非空。
- `drilldown`：`items[]` 每项含稳定 key（`round_id`/`trade_id`/`event_date`）+ `value`（定点串）+ `unit` + `label`；`source_endpoints[]` 给出可复核端点；`params` 回填。**金额型与比率型指标的 item 值格式必须分开断言**（API-035 现有"金额 2 位"断言与比率指标冲突，记 HITL）。

### 2.4 异步触发（API-038，选股域）
`POST /diagnostics/selection` → 202 + job(`diagnostics_selection`)；完成后 `GET /metrics?domain=selection&period=…` 可读。Job kind 枚举已存在，但 handler/迁移未实现。07 是否包含该 handler 记 HITL。

## 3. 指标目录（本设计的**冻结范围候选**，18 个原子指标）

> 设计规则：**指标按"可独立解释/可独立下钻/可独立 golden"划分**；同一口径的并列变体
> （median 版、全样本版、h=5/10 版）优先放 `decomposition`，避免指标爆炸（A26 核心结论预算 8~12）。
> 若你负责的域里认为必须拆成独立 id，请在 part 文件写明理由并标 HITL。

| # | metric_id | 域 | 口径 | 主要数据源 | core_conclusion | 备注 |
|---|---|---|---|---|---|---|
| 1 | `perf.win_rate` | perf | A10 已平仓胜率 + Wilson CI；decomp 全样本胜率/开仓数 | round | 是 | Catalog 断言 `n=12` 需能对上 |
| 2 | `perf.pnl_ratio` | perf | A11 平均盈/平均亏；decomp median 版 | round | 是 | 全亏/全盈边界 |
| 3 | `perf.holding_days` | perf | A13 交易日均值；decomp median/自然日/资金占用 | round | 否 | 日历口径 HITL-9 |
| 4 | `perf.concentration` | perf | A14 Top3 盈利占比；decomp CR1/Gini/n_pos | round | 否 | Gini 口径 HITL-10 |
| 5 | `perf.fee` | perf | A21 费用/双边成交额；decomp 佣金/印花/其他 | trade | 是 | 禁止二次扣费 |
| 6 | `perf.fee_breakeven` | perf | A22 年化费用/年化平均持仓市值 + "吃掉 TWR%" | trade+asset_daily | 是 | 公式歧义 HITL-11 |
| 7 | `perf.expectancy` | perf | A12 `E=p·W−(1−p)·L`；decomp 盈亏平衡胜率 p* | round | 否 | exploratory（核心预算外） |
| 8 | `timing.chase` | timing | A17 追涨分位均值；decomp z 版/coverage | 日线 bars+trade | 是 | 需日线（HITL-2） |
| 9 | `timing.fly` | timing | A16 卖飞率 z>1 占比（h=20）；decomp h=5/10 | 日线 bars+benchmark | 否 | 停牌/未走完剔除规则 |
| 10 | `timing.beta` | timing | A23 β；decomp α 年化/t/IR/TE/超额（算术+几何） | 日线基准+组合日收益 | 是 | 基准必需；rf HITL-14 |
| 11 | `timing.drawdown` | timing | A18 MDD；decomp 持续/恢复/水下/Ulcer/Calmar | asset_daily.daily_return | 是 | 数据源 HITL-12 |
| 12 | `behavior.de` | behavior | A15 DE=PLR−PGR + bootstrap CI；decomp PGR/PLR/持有天数差 | round | 是 | 固定种子 HITL-7 |
| 13 | `behavior.turnover` | behavior | 期间双边成交额/平均持仓市值 | trade+asset_daily | 否 | 平均市值口径需定义 |
| 14 | `behavior.averaging_down` | behavior | 摊平买入（买入价<当前含费均价）占比/次数；decomp 深度/后续盈亏 | trade | 否 | 定义需量化（补仓 vs 摊平） |
| 15 | `behavior.hhi` | behavior | A19 HHI_total 均值；decomp HHI_eq 均值/最大/期末 | 内存重建每日持仓 | 否 | 无明细表（HITL-13） |
| 16 | `selection.bhar_20` | selection | A24 BHAR_20 均值；decomp 中位数/t/n | 日线 bars+cohort | 是 | PIT 口径 HITL-4 |
| 17 | `selection.bhar_60` | selection | A24 BHAR_60 均值；同上 | 日线 bars+cohort | 是 | 窗口不足剔除 |
| 18 | `selection.bhar_hit` | selection | BHAR>0 占比（选股胜率辅助）；可 exploratory | 日线 bars | 否 | 可砍，标 exploratory |

**范围外（本任务不做，文档里写明理由）**：A20 夏普（需 rf+60 日门禁，可在后续）；A23 的 Brinson/行业（数据缺失）；A25 Brinson/Carino（Period3-08）；A12/A14 之外的组合风险指标；任何前端实现。

## 4. 指标 spec 模板（每个指标必须写全，缺项视为未完成）

```markdown
### <metric_id> — <中文名>
- 域 / definition_version / core_conclusion / mvp / frequency（同步）
- **口径来源**：PRD A<n> 原文关键句（照抄）+ 本文补充定义（只允许补充，不允许改）
- **公式**：标度整数逐步（每一步的 scale、除法舍入 ROUND_HALF_UP、中间量位宽）；给出
  Decimal 等价式与一个 3~5 个元素的小例子（输入→输出，手算数）
- **输入视图**：ctx.<view> 需要字段；上游依赖（metric_id 或 dataset）；blocked 传播规则
- **窗口/period 语义**：period=YYYY-MM 时区间边界（闭区间？首日含？lookback 是否跨期？）
- **边界与异常**：n=0/1/5/9/10/29/30/59/60；pnl=0；全胜/全负；data_error 回合；open 回合；
  上游 blocked/stale/degraded；缺行情；缺基准；除零；极端值（分位窗口 max=min）
- **sample_policy**：min_n / ci_min_n / ci 方法（wilson|bootstrap） / min_trading_days
- **status_policy**：声明式规则（upstream=blocked / recon_suspicious / 缺基准 / 缺行情 / stale）
- **CI/统计细节**：公式、z 值/分位、bootstrap B 与 seed 派生、有偏修正（若选 BCa 说明）
- **输出**：value 单位（CNY/ratio/bp/days/count/z/score）+ precision + decomposition 字段与类型
- **explain**：conclusion 模板（中/英；禁裸术语）+ method 一句话 + 非 ok 的 reason/what_to_do
- **drilldown**：endpoint + params + item 字段（key/value/unit/label）+ 取数逻辑
- **golden**：fixture id、期望值来源（手算表/独立 oracle）、容差、覆盖的边界
- **必杀变异算子**：该指标最危险的 2~4 个突变（`<`↔`<=`、符号翻转、分母 n/n−1、常量替换），
  以及杀它的测试名
- **HITL**：本指标遇到的歧义/不可达点（编号 + 一句话）
```

## 5. part 文件分工与写作要求

| 文件 | 负责 | 内容 |
|---|---|---|
| `docs/architecture/_p207/part_perf.md` | perf 设计 | 指标 1–7 的完整 spec + 本域公共口径（数据视图、舍入、period 语义、dependencies）+ 域级测试要点 |
| `docs/architecture/_p207/part_timing.md` | timing 设计 | 指标 8–11 完整 spec + 日线/基准/日历数据可用性分析（含 3 个可选方案与推荐）+ 择时域 golden 设计 |
| `docs/architecture/_p207/part_behavior_selection.md` | behavior+selection 设计 | 指标 12–18 完整 spec + 持仓内存重建设计 + PIT cohort 设计 |
| `docs/architecture/_p207/part_test.md` | 测试设计 | §7 测试用例设计全套（golden 目录/夹具清单/手算表/oracle/契约 kit/API 验收矩阵/变异/隔离/性能/CI 门禁） |

**硬性要求**：
1. 只写自己的 part 文件；不要改 `docs/architecture/2026-10-25-asset-daily-table.md` 等既有文档；不要写生产代码。
2. 公式必须可执行：给出标度整数步骤与手算例子；**禁止 float**，Decimal 只用于定义与中间推导。
3. 每个数值结论必须可追溯到 PRD 条款或现有代码；无法追溯就标 HITL，不许猜。
4. 引用文件用相对路径 + 行号（如 `backend/packages/core/src/stock_core/nav.py:112`）。
5. 输出语言：中文（术语保留英文）；表格优先。
6. 篇幅：perf/timing/behavior_selection ≤ 450 行；part_test ≤ 600 行。
7. 写完自查：模板缺项 = 0；HITL 编号与本文 §6 不重复（新增用 H-xx 续号）。

## 6. 已识别的 HITL 候选（设计时按此编号引用，新增续号）

| 编号 | 问题 | 推荐取向（待人工拍板） |
|---|---|---|
| HITL-1 | Period2-05/06 未实现，07 如何落地 | 07 拆两层：core 纯函数+defs+golden 先行；引擎/端点作硬前置，冻结契约 |
| HITL-2 | replay 行情月频 9 点，择时域需日线 | 扩展确定性夹具生成器出日线（约 2 年），replay 声明 supports_daily_bars |
| HITL-3 | 基准 hs300_tr 日线（live 指数源） | 端口 + replay 基准日线；live 指数适配可选；缺基准 → blocked |
| HITL-4 | 选股域财报/行业数据缺失 vs Catalog 的 as_of 财报断言 | 本期只做价格类 BHAR，修断言；行业/财报另立任务 |
| HITL-5 | metric_id 命名（dotted vs Catalog flat；version 起始值） | dotted id（架构契约）+ 全量 1.0；Catalog 断言同步改 |
| HITL-6 | 18 个指标 vs 3.5 人日 | 核心 12 全做 + 其余 exploratory；或缩到 12；或加时 |
| HITL-7 | bootstrap 种子/B/CI 方法 | seed=sha256(inputs_hash)，B=1000 percentile，纯整数 PRNG |
| HITL-8 | CI 最小样本 30 vs constants.MIN_CI_SAMPLE=10；insufficient 是否返回值 | ci_min_n=30；insufficient 返回值+n 但无 CI/无 conclusion |
| HITL-9 | 交易日历：工作日近似 vs 真实 A 股日历 | M1 沿用工作日近似并显式 assumptions；真实日历另立任务 |
| HITL-10 | Gini/CR1 口径（分母仅 pnl>0） | Gini 定义在盈利回合 pnl 分布；n_pos<2 → insufficient |
| HITL-11 | A22 "吃掉 TWR 的 Y%" 未定义 | fee_drag_ratio + twr_eaten_pct（分母≤0 → null+unreliable） |
| HITL-12 | 回撤族数据源 | asset_daily.daily_return 复利链；build≠current → stale/blocked |
| HITL-13 | HHI/换手需每日持仓，无明细表 | 指标层内存重建（复用 valuation 原语），不落库 |
| HITL-14 | rf 无数据源/默认值 | settings 默认 1.5%，assumptions 显式；禁止默认 0 |
| HITL-15 | API-035 下钻 item "金额 2 位"断言与比率型指标冲突 | 按 unit 分格式断言；Catalog 修订 |
| HITL-16 | mutmut 配置/门禁范围/路径（docs 写 `core/src/core`，实际 `stock_core`） | mutmut 仅跑新模块 + stats，≥80%，PR diff + nightly 全量 |
| HITL-17 | API-038 异步诊断 handler 是否在 07 范围 | 07 只交付 defs+数据视图；handler 归 06 补丁（或 07b） |
| HITL-18 | period 语义（月）与 rolling 窗口/lookback 的关系 | 指标按"period 区间 + 前置 lookback"计算，lookback 单独入 inputs_hash |

## 7. 相关文档索引（读这些，不要凭空设计）

- `docs/DEV-PLAN-v2.md` §3.2 Period2-07（及 01–06/16 上下文）
- `docs/prd/PRD-v2.md` 附录 A1–A27；FR-R4/R5/R6/R8；§9 风险
- `docs/architecture/v2/Extensibility.md` §3.1（MetricDef 13 字段）、§3.2（框架代算）、§11.1（新增指标 9 步）
- `docs/Architecture-v2.md` §11（引擎）、§9.2/§9.3（数据模型/约束）、§10.4（计算链/脏窗口）
- `docs/architecture/v2/API-Catalog.md` API-033/034/035/036/038/079 与 §6 验收矩阵（fixture 名单）
- `docs/architecture/v2/Testing.md` §9.3（oracle）、§12.1（变异）、§12.3（golden 审批）、§8 数据集
- `docs/reviews/2026-10-25-DevDesign-Review-asset-daily-table.md`（上一任务的评审格式与深度标杆）
- 代码：`stock_core/values.py`、`stock_core/nav.py`、`stock_core/fifo/{engine,calendar,types,bridge}.py`、
  `stock_review/models.py`、`stock_review/application/{rounds,nav,assets,valuation,rebuild}.py`、
  `stock_platform/ports.py`、`stock_platform/market_live.py`、`stock_ledger/fixtures.py`、
  `stock_api/routers/{nav,assets,rounds}.py`、`tests/unit/core/test_nav.py`、`tests/oracle/naive_fifo.py`

## 8. 完成判据

- part 文件覆盖分配到的全部指标 spec，模板零缺项；
- 每个指标至少 1 个 golden 设计（fixture + 期望值来源 + 容差 + 手算例子）；
- 每个指标至少 3 条必杀变异 + 对应测试名；
- 数据不可达/定义歧义全部落 HITL 编号（不许静默假设）；
- 给出域级"实现顺序 + 估时拆分"（人日到 0.1 粒度）；
- 你的 part 文件末尾附 **"给 Lead 的合并提示"**（10 行内：哪些结论需要写进决策摘要、哪些口径必须人工拍板、哪些与其他域耦合）。
