# 三域指标实现（Period2-07）设计与测试用例方案

> 状态：**v1.0 定稿 — 三方交叉评审 + 第二轮独立验证通过（APPROVED_WITH_FIXES，5 项 FAIL 已修）+ 人工决策已批准（2026-10-26，全部按推荐方案）**，可进入实现排期（§10）；决策记录见 §13.4，评审见 [交叉评审](../reviews/2026-10-26-DevDesign-Review-metrics-three-domain.md)、[第二轮验证](../reviews/_p207/review_verification.md)
> 日期：2026-10-26 ｜ 作者：AI（Team Lead + 4 名设计/测试设计）
> 需求来源：用户诉求——对 `docs/DEV-PLAN-v2.md` §3.2 **Period2-07「三域指标实现」**做开发设计与测试用例设计、交叉评审，并给出需人工决策事项的推荐/备选方案。
> 口径来源：`docs/prd/PRD-v2.md` 附录 A1–A27（唯一口径源）、`docs/Architecture-v2.md` §11、`docs/architecture/v2/Extensibility.md` §3/§11.1、`docs/architecture/v2/Testing.md` §9.3/§12、`docs/architecture/v2/API-Catalog.md` API-033/034/035/038/079。
> **本阶段只出文档**：不写生产代码、不改 API-Catalog/PRD/契约；契约修订清单见 §9（批准后由实现阶段落地）。

---

## 0. 决策摘要（2026-10-26 人工已批准，全部按推荐方案；记录见 §13.4）

> 推荐方案 = 正文采用的方案；备选方案保留取舍依据。**A 类**不拍板无法开工；**B 类**影响 golden/口径，可先按推荐实现、后续改判需升 `definition_version`；**C 类**工程实现细节，可委托 Lead。明细见 §13。

### A. 阻塞性决策（不拍板无法开工）

| # | 决策点 | 推荐方案 | 备选方案 | 影响/成本 |
|---|---|---|---|---|
| **D-01** | Period2-05/06（指标引擎+端点）尚未实现，07 如何在空地基上落地 | **两层交付**：07 先交付 `core/stats`（07 份额）+ `core/metrics` + `review/metrics/defs` + golden/oracle（不依赖引擎即可单测）；05/06 作为**硬前置**先补（估 9.5d＝05≈6.5d+06≈3.0d，E-17 已人工裁定接受推荐方案）。07 的 API 验收在 05/06 合并后联调 | ① 05/06 最小子集并入 07（+2.5~3d，边界模糊）；② 严格串行（周期最长，返工最少） | 推荐可并行开工；07 端到端可用日取决于 05/06 |
| **D-02** | replay 行情月频 9 点，择时域需 20 日窗口/h=5,10,20/日频 β/IR | **新增独立确定性日线夹具** `fixtures/market/daily_bars.json`（≥2 年工作日，含 `hs300_tr`）；replay 按区间返回日线；**不动既有 `quotes.json`**（nav/资产表零回归） | ① 直接把 quotes.json 换日线（nav/asset golden 全变，+2~3d 回归）；② live-only（CI 不可复现）；③ 月频近似（违反 A16/A17/A23/A26） | +0.5~1d（夹具+适配器+测试） |
| **D-04** | 选股域 PRD 要求 PIT/财报/行业，但本仓无财报/申万行业/PIT 财务表；API-038 断言"不含晚于 as_of 的财报数据"不可执行 | **本期只做价格类 PIT cohort BHAR 20/60**（cohort=买入日；as_of 门禁作用于行情/事件）；修订 API-038 断言；财报/行业/风格另立任务 | ① 引入财报+行业源（+5~8d，需 PIT 版本化表）；② selection 域整体推迟 | 推荐可交付 2 个 P0 指标；备选①显著超期 |
| **D-06** | 18 个指标、每个 golden + 独立 oracle + mutation ≥80%，DEV-PLAN 估 3.5 人日 | **拆两批 + 修正估时**：07a 核心 10 个（≈9.0~9.5d）、07b exploratory 8 个（≈4.0~4.5d）；合计 13.0~14.0d；不含 05/06 的 9.5d（E-17 裁定）。修正 DEV-PLAN 估时 | ① 严格 3.5d 只交付 6 指标（不满足验收原文）；② 18 全做完整实现（≈14.5d+）；③ 砍独立 oracle（违反 Testing D2） | 唯一能同时满足"18 指标可算 + 每指标 golden + mutation"的排期 |
| **D-19** | 演示数据仅 3 个月（≈132 工作日行）+ 月频 9 点，而 A26 要求日频 ≥60 交易日；二期验收剧本要求"五张卡片有数"；且 demo 账单资金余额与持仓不闭合（超卖→全表 degraded，回撤 >100%） | **对齐"流水窗口 / 行情 as_of / 验收 period"三轴**：① 流水 `months=12` 且**止于 2026-06-30**（`generate_huatai_bill` 增 `end_date` 参数，默认 `_ANCHOR=2026-06-30`）；② 日线行情覆盖到 **2026-09-30**（≈60 个交易日的前向缓冲，供 BHAR 20/60、卖飞 h=20、摊平 fwd20 走完窗口）；③ `DEMO_CLOCK=2026-10-01` 不变，`data_as_of=2026-09-30`；④ **验收 period 改为 `2026-06`**（由 data_as_of 反推，写死到 DEV-PLAN §3.1/§11）；⑤ 生成器跟踪逐标的持仓，**禁止卖出超过持仓**（消除两账户 25 次超卖）；⑥ `seed-demo` 收尾触发 `rebuild_rounds`→`rebuild_nav`→`build_assets`（或新增 `make rebuild`）；⑦ 确定性字节可复现 | ① 保持 3 个月，验收剧本改为"卡片显示数据不足"（改 DEV-PLAN §3.1）；② 只加长测试夹具（演示剧本仍不可达） | 推荐才能满足既定验收；+1d（夹具生成器+seed 重放验证） |
| **D-20** | **DE 方向矛盾**：PRD A15 写 `DE = PLR − PGR`，但 FR-R6/API-Catalog 断言"只卖盈不卖亏 → `DE>0`"（= Odean `PGR − PLR`）。二者相反 | **采用 Odean `DE = PGR − PLR`（DE>0 为处置效应）**，勘误 A15；behavior.de 的 `value` 与 CI 均按此；decomposition 同时给 PGR/PLR/持有天数差 | ① 按 A15 字面 `PLR−PGR`，则 FR-R6/Catalog 断言全部反向（"只卖盈不卖亏"会给负值）；② 双字段并列（`de_odean`/`de_prd`，口径双写、报告难解释） | **阻塞 behavior.de golden 冻结**；推荐与 FR-R6 验收一致 |
| **D-21** | A17 要求"复权序列对齐"，NFR-2 要求 hfq；但 replay `supports_adjusted=False`、live `ADJUST=""`（v1 只不复权） | **v1 用不复权 + `assumptions.adjust="none"`**；除权嫌疑日（跳空超板块涨跌停）→ `unreliable`；金标夹具窗口内不含除权；复权因子/hfq 单列后续任务（ADR-0025 已预留） | ① 本期实现 hfq 因子版本化（+2~3d，依赖 Period2-04 行情因子表）；② 用后复权价近似（与账单现金流口径冲突，易双计） | 推荐零依赖；影响 BHAR/追涨分位/卖飞率的绝对水平（披露 assumptions） |

### B. 口径决策（影响 golden；可先按推荐实现，改判需升 version）

| # | 决策点 | 推荐方案 | 备选方案 | 影响 |
|---|---|---|---|---|
| **D-03** | live 无基准指数源（`market_live.validate_symbols` 拒 `hs300_tr`；`BenchmarkProvider` 只有文档） | replay 基准日线满足验收；live 缺基准 → `timing.*`/`selection.*` **`blocked`** + `what_to_do`；指数适配单列后续任务 | ① 本期补 akshare 指数适配（+1d，CI 不可测）；② 用户自备基准 CSV 导入（新增流程） | live 下择时/选股域不可用，UI 走 blocked 卡片 |
| **D-05** | metric_id 命名：架构要求 dotted（`^(selection\|timing\|behavior\|attribution\|perf)\.`），Catalog 断言用 flat（`win_rate/de/bhar_20`）；version 示例 `2.1` | **dotted id + 全部 `definition_version="1.0"`**；Catalog 断言/示例同步修订（C-01/C-04） | ① flat id + 放宽正则（架构债）；② 双 id 别名（映射漂移风险） | 需改 Catalog 文档（实现阶段随 api-snapshot） |
| **D-07** | 冻结的 `MetricValue` 无 CI 字段，DE/BHAR 的 bootstrap 统计量无法由框架"通用均值型"CI 复现；extensibility 又说 "Random 由 Clock 派生" | **`MetricValue` 增内部字段 `ci_sample`（不出口 API）**；`sample_policy.ci` 按策略派发（wilson/bootstrap/none）；seed=`sha256(metric_id\|version\|inputs_hash)[:8]`，**B=1000（PRD-v1 写 10000；建议按 1000 并修订 PRD，或改 10000，成本×10 需人拍板）**，percentile（pct 索引 0-based：B=1000 → 25/974），xorshift/SplitMix64 纯整数；两次运行逐位相等；**无效 replicate 补抽，上限 10×B，`B_eff<B/2` → CI 缺省** | ① BCa（+1d）；② Clock 派生 seed（golden 抖动，不可接受）；③ 正态近似 CI（DE 偏态）；④ B=10000（PRD v1 原文，B 越大 CI 尾部越稳，但 pf 成本×10） | 需 05 在引擎契约中补字段；否则 DE/BHAR 只能口径双写 |
| **D-08** | 样本门槛与字段出现规则四处不一致：PRD A26"N<30 附 CI"、`constants.MIN_CI_SAMPLE=10`、引擎 `ci_min_n=30`、Architecture §11"非 ok 不出 value"（未区分 stale/unreliable）、API-033 断言③只要求 fx_n5 无 CI（未规定 value 是否存在） | **统一为四态门槛**：`insufficient`（n<10 或结构性无定义）→ **有 value（或 null）+ 无 CI + 无 conclusion**；`pending_window`（日频<60）→ **无 value 键**（A26"不出回撤/IR"）；`blocked/stale/unreliable` → **无 value 键 + 无 conclusion**（Architecture §11 门禁："API 层在非 ok 时不返回 value 与 conclusion"；Extensibility §3.3"stale 返回旧值"需勘误为 C-31）；**CI：n≥min_n(10) 即输出**（PRD-v1 FR-R8 "N<30 的比率必须附 CI" + PRD-v2 A26；`ci_min_n=10`，修正 Extensibility §3.1 的 30）；结构性无定义（n=0、n_win=n_loss=0、n_pos<2、turnover=0）→ `status=ok` + `value:null` + `insufficient` | ① 沿用引擎 `ci_min_n=30`（与 PRD-v2:405/414/422/590、PRD-v1:108/459 五处冲突，需改五处）；② `stale` 返回旧值（Extensibility §3.3，与 Architecture §11"非 ok 不出数字"冲突） | 05/06 与 07 必须同一规则，否则契约测试互斥 |
| **D-09** | 交易日历：`business_days_between` 只按周一~周五，无节假日；`round.holding_days` 已按此落库 | **M1 沿用工作日近似**，`assumptions.calendar="weekday_proxy"`；真实 A 股日历（含节假日/停牌）单列后续任务（需重算 round + version bump） | ① 本期引入内置真实日历（+1~2d，口径分叉/重建）；② 用 bar 交易日代理（覆盖不全） | 节假日附近 1~3 日偏差（已披露） |
| **D-10** | A14 集中度：分母"仅 pnl>0"，Gini/CR1 的边界未定义 | 集中度**只在盈利回合上定义**：Top3=前 3 大盈利/Σ盈利、CR1=最大盈利/Σ盈利、Gini 在盈利 pnl 升序分布上；`n_pos<2` → insufficient；`n_pos<3` → Top3 退化为全部盈利并在 decomp 标注 | ① Gini 含亏损（需平移，经济含义不同）；② Gini 用 \|pnl\|（可比性差） | 盈利回合为 0 时无值（insufficient） |
| **D-11** | A22 "吃掉 TWR 的 Y%" 无公式；两个年化基数不一致（费用 365/D vs TWR 365/首末差）；`|年化TWR|`（ratio）与年化费用（CNY）量纲不可加 | **主 value（core）** `fee_drag_ratio = 年化费用/期间日均持仓市值`（A22 原文"年化平均持仓市值"按"期间日均"落地；费用年化 `365/D`）；`twr_eaten_pct` 仅当 **年化 TWR > 0** 时输出且**标 exploratory**：`= 年化费用/(twr_amount+年化费用)`，`twr_amount = 期间平均总资产 × 年化 TWR`（TWR 年化统一 `365/(last−first)`）；TWR≤0 → null + `unreliable`（避免把"吃掉亏损"讲成"吃掉收益"）；PRD 需补公式（C-15） | ① 只出 `fee_drag_ratio`，不出 Y%（最稳，推荐给"结论"）；② `Y=fee_drag/|TWR|`（量纲错，拒绝） | 可拍板：主 value 无歧义；Y% 作为 exploratory 展示 |
| **D-12** | 回撤族数据源：`nav_daily` 按月稀疏；`asset_daily` 逐自然日且有现金流调整后 `daily_return_units` | **用 `asset_daily.daily_return_units` 复利链构日度 TWR 指数**；`build.state != current → stale`；区间内任一 `blocked` 行 → `blocked`；任一 `degraded` → `unreliable`；`assumptions.nav_source="asset_daily.daily_return"` | ① `asset_daily` 加 `unit_nav_units` 列（+1d）；② 用 nav_daily（月频，日频 MDD 不可得） | 零迁移；复利链与 A4/A5 同口径 |
| **D-13** | HHI/换手/全样本浮盈亏需要**每日逐标的持仓/期末持仓**，`asset_daily` 只有聚合值（无明细表） | **指标层内存重建**：新增纯构建器（复用 `valuation.closing_price_ladder/close_on_or_before/holding_delta`），从账户首笔流水重放到 `period_end`；逐日与 `asset_daily` 对账（0 分公差）；预算 p95<200ms；不落库 | ① 新增 `review.asset_daily_holding` 明细表（+1.5d、存储/增量复杂）；② 期末快照近似（日频 HHI 不可得） | 07 自带构建器在 def 内基于 `trades`+`bars` 重建（E-06：`positions_end`/`holdings_daily` 不入视图层） |
| **D-14** | rf 无数据源；PRD 禁止默认 0 | `settings.rf_annual` 默认 `0.015`，`assumptions.rf_rate` 输出；日化 `rf_d=(1+rf_a)^(1/244)−1`；α 年化 `α_d×244` | ① 内置静态 rf 曲线；② 用户自填（设置页 Period2-11 才做） | 默认值需人工确认（1.5% 或 2.0%） |
| **D-15** | API-035 断言"每项金额 2 位定点"对比率/天数型指标不成立；A13 均值非整数与"天数为整数"冲突 | 下钻 item 增 `unit`；**断言按 `(unit, precision)` 分级**：CNY 2 位、ratio 8 位、days 2 位、count 整数、z 4 位；`SamplePolicy.ci` 支持 `"none"`；`values.py:193` docstring 与实现舍入不一致以代码为准 | ① 强塞金额字段；② 只允许金额型指标下钻 | 弱破坏（用例/文案改） |
| **D-18** | period 与滚动窗口/lookback 语义；回合型指标归属日（close_date vs open_date）；`page()` 用 `COALESCE(close_date,open_date)` | 回合/已实现事件按 **`close_date`**；open 回合按 `open_date ≤ end` 只进全样本；**`window_start = period.start − lookback`（交易日回退）**，`window_end = max(period.end, period.end + forward)` 且 `≤ data_as_of`；lookback/as_of/benchmark 进 `inputs_hash`；新增 `RoundRepository.in_period()`，不复用 page 的 COALESCE | 严格只算 period 内（期末事件被截断，系统性偏差） | 同一 period 不同实现结果不同的根因 |
| **D-25** | "摊平"、"换手率" PRD 只有名词无定义 | **摊平** = 持仓>0 的 buy 且 `price < 加仓前含费均价`（严格低于）；深度 `(均价−买价)/均价`；首次建仓/送转/打新不计。**换手** = 期间双边成交额 / 逐日平均持仓市值（年化 `×244/交易日数`） | ① 摊平含"持平加仓"（`<=`）；② 换手用单边/期末市值（口径不同） | 定义不同 → golden/文案全部重做（需现在定） |
| **D-26** | A24 未给定：事件日基准价（收盘 vs 成交价）、停牌/退市剔除阈值、重叠窗口聚类是否 Newey-West | 事件日基准 = 当日收盘；**停牌代理 = 窗口内该股 bar 覆盖率<50% 剔除**；退市 = 尾部 5 日无 bar 且累计覆盖<90% 剔除；主口径**事件日聚类 t 值**，Newey-West 仅作 decomp 敏感性（M1 不做） | ① 引入 PIT 证券主数据（+2d）；② 不做剔除（含退市/停牌假事件） | 影响 BHAR 样本与 t 值；需 PRD 补定义 |
| **D-27** | 费用口径：A21 未定义独立费用行（红利税/独立手续费行）是否入分子；"双边成交额"毛额 vs 净额 | 分子含**独立费用行**（拆 4 桶并断言 `Σ4桶=fee_amount`）；成交额主口径 **毛额**（`qty×price`），decomp 并列 `turnover_net=Σ|amount|` 作交叉校验；**禁止二次扣费**（`amount_units` 已含费，不得再加费用列求成本） | ① 只认三列（漏红利税）；② 成交额用净额（与 A21"双边成交额"字面略偏） | 费用率分母差 <1bp；影响 golden 第 3 位小数 |
| **D-28** | 行情密度不足的状态表达：`SampleStatus` 无 `low_coverage` 且禁止新增枚举；月频冒充日线必须显式化 | `bar_density<0.8` → `status=unreliable` + `reason`；密度=0 → `blocked`；阈值进 `core/metrics/policy.py`（禁魔数）；窗口基准 = `bar_index`（排第 i 条 bar），`assumptions.window_basis="bar_index"` | ① 新增 `low_coverage` 枚举（改契约）；② 静默用月频点计算（错值，禁止） | 把"数据不足"变成显式状态 |

### C. 工程/契约决策（可委托 Lead，人工抽查）

| # | 决策点 | 推荐方案 | 备选 | 影响 |
|---|---|---|---|---|
| **D-16** | 变异测试：无 mutmut 配置/无 CI workflow；文档路径 `core/src/core` 与实际 `stock_core` 不一致 | **mutmut（pin ≥3.2,<4）+ `make mutation`**：新增 `stock_core/stats|metrics` ≥80%，`ci.py`/`tolerance.py` ≥95%；现有 core 不退化；PR diff-only、nightly 全量；修正文档路径 | ① cosmic-ray（与文档不一致，改文档面大）；② 不跑变异（违反验收） | +1d 配置与首轮；nightly ≤40min |
| **D-17** | API-038 异步诊断 handler 归属（`JobType.DIAGNOSTICS_SELECTION` 已存在但无 handler） | 07 只交付 selection defs/数据视图；**handler 归 Period2-06 收尾（E-18，05/06 评审 v1.1 已明确）**；07 用直接构造的 metric_result 验收 | ① 07 一并实现（+1d，可端到端验 API-038）；② 不做（API-038 不可验收） | 任务边界清晰 |
| **D-22** | admin 是否可读指标端点：admin 不可读交易明细，但指标是聚合值；Catalog API-033 未列 `ADMIN_CANNOT_READ_TRADES` | **403 `ADMIN_CANNOT_READ_TRADES`**（与 assets/rounds 一致：聚合值仍源于交易明细）；Catalog 补错误码 | ① 允许 admin 读聚合指标（与"admin 不可读业务数据"原则冲突）；② 新增专用错误码（无必要） | 安全一致性；需改 Catalog |
| **D-23** | golden 目录双源：Testing §9.1 `fixtures/expected/metrics/` vs Extensibility §12 `backend/tests/golden/<metric_id>/`（Testing §9.3 仅给 oracle 三方比对、未定义 golden 目录）；容差统一 1bp 对微比率（fee≈5e-4）等于 20% 相对误差 | **单源 `backend/tests/golden/<metric_id>/`（Extensibility §12）**；Testing §9.1 勘误为同路径；容差分级：ratio∈[0,1] 用 1bp、微比率用绝对 1e-8+相对 0.5%、CNY 0.01、days 0.01、count 精确；golden 独立 PR + `golden-approved` 标签 | ① 沿用 fixtures/expected（与 Extensibility §12 冲突）；② 统一 1bp（掩盖量级错误） | 校准 Testing 文档（C-15） |
| **D-24** | `decomposition` 是否进 API（引擎 `MetricValue` 有，`MetricResult` 无）；四数字卡需要 median/全样本/CR1/h=5/10 | `MetricResult` 增可选 `decomposition: object\|null`（additive，旧客户端忽略）；值同样用定点串/整数 | ① 通过 explain 文案带出（前端不可结构化消费）；② 每个变体单列 metric id（指标爆炸，违反 A26 预算） | 契约 additive；需 Catalog 修订（C-05） |
| **D-29** | 契约修订总包（C-01~C-43，见 §9；C-35~C-43 来自 05/06 评审 v1.1） | 批准清单，实现阶段统一落（api-snapshot/api-check/sdk 同步） | 逐条另批（低效，易漂移） | 影响 05/06/07 三个任务的契约 |

> **设计内已定、不需人工拍板**：舍入（Decimal ROUND_HALF_UP，出口量化一次）、年化基数 A=244、容差 EPS_AMT=0.01/EPS_R=1bp、金额/比率 scale=2/8、布尔统计用 Wilson、DE/bootstrap 的 percentile 法、费用三列+独立行口径、回撤指数用 asset_daily 日收益、PIT `data_as_of=min(today, market.as_of, build.as_of)`、explain 中英模板——依据见各章与 PRD。

---

## 1. 背景与目标

### 1.1 背景

- **Period2-07 原文**（DEV-PLAN §3.2）："三域指标实现：绩效（胜率含全样本、盈亏比、持仓天数、集中度 CR1/Gini、费用占比/回本门槛）、择时（追涨分位、卖飞率 z、β/α/IR、回撤族）、行为（PGR/PLR DE + bootstrap、换手、摊平、HHI）、选股（PIT cohort BHAR 20/60）｜依赖 Period2-05｜每个指标 1 份 golden（期望值来自手算表/独立实现）+ mutation ≥80%｜3.5 人日"。
- **上游依赖未就绪**：本仓 **Period2-05（指标注册表与执行引擎）与 Period2-06（指标端点）尚未实现**——无 `review/metrics` 包、无 `review.metric_result` 迁移、无 `/metrics` 路由（§2 事实表）。因此本设计必须同时给出"07 要实现的指标层"与"07 依赖的冻结引擎契约"。
- **数据地基已就绪**：`review.round/round_fill`（FIFO 回合）、`review.nav_daily`（稀疏净值：有流水日 + 期末）、`review.asset_daily`（逐自然日资产 + 现金流调整日收益）、`ledger.trade`（含佣金/印花税/其他费）、行情端口 `MarketDataSource`（replay 月频 / live 日线不复权）。

### 1.2 目标（可验收）

1. 18 个指标（§3）的口径公式落为 `stock_core` **纯函数**：禁 float、确定性、零 I/O、零时钟。
2. 每个指标 1 份 **golden**（期望值来自手算表或独立朴素实现，禁止用被测代码生成）；独立 oracle + 生产实现**三方比对**。
3. 指标定义文件可被引擎**自动发现**（放文件即注册，不改 registry/`__init__`），`MetricDef` 13 字段齐全。
4. 每个指标在 `n<10 / n≥10 / 日频<60` 各档下的 `sample_status`、CI、`conclusion`、`value` 行为正确；`blocked` 不出数字，`stale/unreliable` 可出值但无 conclusion（§4.2）。
5. 新增模块 **mutation score ≥80%**（现有 core 不退化）。
6. 二期验收剧本 §3.1 第 1/5 步在 replay 模式可跑通（五卡有数、`curl .../metrics?ids=...` 返回 n/ci/sample_status）。

### 1.3 非目标（范围围栏）

- **不重做引擎与端点**：注册表/框架代算/DAG/失效/物化/`GET /metrics`/`explain`/`drilldown` 归 Period2-05/06（§4 契约）。
- 不做前端看板、月报、归因（Period2-08/09/12）、趋势断点（Period3-02）、行为提醒（Period3-03）。
- 不做 A20 夏普、A25 Brinson/Carino、行业归属、财报类 PIT、真实 A 股交易日历、live 指数行情（§3.3 列后续归属）。
- 不改既有 `/nav`、`/assets`、`/rounds` 响应结构与口径（只读消费）。

---

## 2. 现状与差距（事实表）

| # | 现状 | 证据（文件:行） | 对 07 的影响 |
|---|---|---|---|
| F1 | 无指标引擎：无 `review/metrics/`、无 `MetricDef` 实现、无 DAG/失效/物化、`review.metric_result` 迁移不存在（迁移止于 0006） | `backend/migrations/versions/`（0001~0006）、`backend/packages/review/src/stock_review/models.py:302` | 07 的 defs 无法端到端运行 → D-01 |
| F2 | 无 `/metrics` 路由（API-033/034/035/079 未实现） | `backend/services/api/src/stock_api/routers/`（无 metrics.py） | API 验收不能在本任务单独完成 |
| F3 | 回合引擎已落库：`round.status ∈ closed/open/data_error`、`pnl_units/return_rate_units/holding_days/fills_count`；`round_fill` 逐 chunk 配对 | `models.py:49-131` | 绩效/行为域主数据源就绪 |
| F4 | `round.holding_days` 用周一~周五近似（`business_days_between`，无节假日） | `stock_core/fifo/calendar.py:12-22`、`fifo/engine.py:264` | A13 口径限制 → D-09 |
| F5 | `nav_daily` 是"有流水的日期 + 期末"稀疏行，不是逐日 | `models.py:158-205`、`application/nav.py` | 回撤族不能用 nav_daily → D-12 |
| F6 | `asset_daily` 逐自然日，含 `daily_return_units`（现金流调整）、`status ∈ ok/blocked/degraded`；缺行情行 blocked 且数值 NULL | `models.py:207-257`、`application/assets.py:63` | 回撤族/费用门槛的可靠数据源（需 build current） |
| F7 | `trade` 有 `commission_units/stamp_tax_units/other_fee_units/amount_units/category`；`amount_units` 已含费（禁二次扣费） | `ledger/models.py:163-174`、`application/valuation.py:33-35` | A21/A22 费用口径可算 |
| F8 | replay 行情夹具月频 9 点（4 条序列含 `hs300_tr`），生成器只产月频 | `fixtures/market/quotes.json`、`ledger/fixtures.py:186-248` | 择时/选股域 replay 数据不足 → D-02 |
| F9 | live 只支持 6 位 A 股代码（拒 `hs300_tr`）、只落不复权日线；`BenchmarkProvider` 只在文档 | `platform/market_live.py:1-40,57-74`、`platform/ports.py:131-145` | live 下 β/IR/BHAR 缺基准 → D-03 |
| F10 | 契约枚举已备：`MetricStatus/SampleStatus/ScopeType/JobType.DIAGNOSTICS_SELECTION` | `contracts/enums.py` | 直接复用，不新增枚举 |
| F11 | 常量 `MIN_CONCLUSION_SAMPLE=10`、`MIN_CI_SAMPLE=10`、`A_SHUFFLE_DAYS=244`；引擎文档 `ci_min_n=30` | `contracts/constants.py:21-24`、`Extensibility.md §3.1` | 边界不一致 → D-08 |
| F12 | Catalog API-033 flat id，且未定义 `insufficient` 时 value 是否存在；API-035 金额 2 位；API-038 财报 as_of 断言；MetricResult 无 decomposition | `docs/architecture/v2/API-Catalog.md` §2.3/§4/§6 | 契约修订 → D-05/D-08/D-15/D-24/D-04 |
| F13 | 无 mutmut/CI workflow；`make verify-p2` 不存在（已有 `test-p2/verify-p1`） | 根 `pyproject.toml`、`Makefile:36,45,207` | 变异/验收门禁需新建 → D-16 |
| F14 | demo 数据 3 个月（`months=3,count=120`）、`DEMO_CLOCK=2026-10-01`、行情月频 9 点；账单资金余额与持仓不闭合（000001 无买入→超卖→全表 degraded） | `cli.py:45,615-631`、`ledger/fixtures.py:38-77,186` | 验收剧本"五卡有数"数据不足 → D-19 |
| F15 | `RoundRepository.page()` 日期过滤用 `COALESCE(close_date, open_date)` | `review/repositories/rounds.py:148-166` | 回合归属日必须新增 `in_period` → D-18 |
| F16 | Testing §9.3 存在 `naive_nav/naive_xirr` 但目录里只有 `naive_fifo.py`；`fixtures/expected/` 不存在；`tests/golden/` 不存在 | `backend/tests/oracle/`、`backend/tests/` | 三方比对基础设施需新建 |
---

## 3. 范围：指标目录、命名与边界

### 3.1 指标目录（18 个原子指标，冻结候选）

> **拆分原则**：可独立解释/下钻/golden 的才拆独立 id；并列变体（median、全样本、h=5/10、持续/恢复期）放 `decomposition`（D-24），避免指标爆炸并守住 A26"核心结论 8–12"预算。
> `core` = 进核心结论/行动建议；`expl` = exploratory（`include_exploratory=false` 时默认不返回，D-08）。

| # | metric_id | 域 | 口径来源 | 主数据源 | 级别 | 依赖 | 单位 | 关键边界 |
|---|---|---|---|---|---|---|---|---|
| 1 | `perf.win_rate` | perf | A10 已平仓胜率 + Wilson CI；decomp 全样本胜率/开仓数 | round + trades/bars（期末持仓 def 内重建，E-06） | core | rounds | ratio | pnl=0 不计胜；n=0 → value:null+insufficient |
| 2 | `perf.pnl_ratio` | perf | A11 平均盈利/|平均亏损|；decomp median 版 | round | core | rounds | ratio | 全胜/全亏 → null+insufficient |
| 3 | `perf.holding_days` | perf | A13 交易日均值；decomp median/自然日/资金占用 | round + fill | expl | rounds,fills | days | open 回合（holding_days=0）排除；工作日近似 |
| 4 | `perf.concentration` | perf | A14 Top3/全部盈利；decomp CR1/Gini/n_pos | round | expl | rounds | ratio | 仅 pnl>0；n_pos<2 insufficient |
| 5 | `perf.fee` | perf | A21 费用/双边成交额；decomp 四桶/成交额 | trade | core | trades | ratio | 禁二次扣费；turnover=0 → insufficient |
| 6 | `perf.fee_breakeven` | perf | A22 年化费用/年化平均持仓市值 + 吃掉收益比例 | trade + asset_daily | core | perf.fee, asset_daily | ratio | D<28/blocked 行/TWR 链断 → unreliable/blocked |
| 7 | `perf.expectancy` | perf | A12 `E=p·W−(1−p)·L`；decomp p* | round | expl | win_rate, pnl_ratio | CNY | 全胜 → p*=null；零回合偏差（decomp mean_pnl） |
| 8 | `timing.chase` | timing | A17 追涨分位均值；decomp z 版/coverage | 日线 bars + trade | core | bars_daily | ratio | <20 bar → pending；max=min 事件剔除；密度<0.8 → unreliable |
| 9 | `timing.fly` | timing | A16 卖飞率（z>1，h=20）；decomp h=5/10 | 日线 bars + benchmark + round | expl | bars, benchmark | ratio | 停牌>50%/未走完/退市剔除；缺基准 blocked |
| 10 | `timing.beta` | timing | A23 β；decomp α 年化/t/IR/TE/超额（算术+几何） | asset_daily + benchmark | core | asset_daily, benchmark, rf | ratio | n_days<60 pending_window；Sxx=0 blocked |
| 11 | `timing.drawdown` | timing | A18 MDD；decomp 持续/恢复/水下/Ulcer/Calmar | asset_daily | core | asset_daily | ratio | blocked 行断链→blocked；<60 pending_window；\|r\|>1 unreliable |
| 12 | `behavior.de` | behavior | A15 `DE=PGR−PLR`（Odean，D-20）+ bootstrap CI；decomp PGR/PLR/持有差 | round (+bars) | core | rounds, bars(期末价) | ratio | 任一侧无处置机会 → null+insufficient；CI n≥10 |
| 13 | `behavior.turnover` | behavior | 期间双边成交额/逐日平均持仓市值（年化） | trade + asset_daily | expl | trades, asset_daily | ratio | avg_mv=0 且 num>0 → unreliable |
| 14 | `behavior.averaging_down` | behavior | 摊平买入占比（D-25 定义）；decomp 次数/深度/fwd20 | trades + bars + benchmark | expl | trades, bars | ratio | n_addon=0 → null+insufficient；首次建仓剔除 |
| 15 | `behavior.hhi` | behavior | A19 HHI_total 均值；decomp HHI_eq 均值/最大/期末 | 内存重建持仓 + bars | expl | bars, asset_daily | ratio | 空仓日不进 eq 均值；覆盖率<50% blocked/<90% unreliable |
| 16 | `selection.bhar_20` | selection | A24 BHAR_20 均值；decomp 中位数/t/n/剔除计数 | 日线 bars + benchmark + trade | core | bars, benchmark | ratio | 窗口未走完→pending；无事件→n=0；全剔除→blocked |
| 17 | `selection.bhar_60` | selection | A24 BHAR_60 均值；同上 | 日线 bars + benchmark | core | 同 16 | ratio | 60 日窗口样本更少 |
| 18 | `selection.bhar_hit` | selection | BHAR_20>0 占比 + Wilson（exploratory，可砍） | 日线 bars + benchmark | expl | selection.bhar_20 | ratio | BHAR=0 不计 hit；n=0 → insufficient |

**核心结论预算**：core 共 10 个（#1/2/5/6/8/10/11/12/16/17），落在 A26 的 [8,12]；expl 8 个默认不返回。

### 3.2 命名、版本与单位规范

- **id**：`^(selection|timing|behavior|attribution|perf)\.[a-z0-9_]+$`，一经发布永不复用（Extensibility §3.1）。
- **definition_version**：本期全部 `"1.0"`；MAJOR = 分母/方向/样本定义变化（趋势强制断点）；MINOR = 精度/窗口/文案（Extensibility §3.3）。
- **单位/精度**：`CNY`（2 位定点串）/`ratio`（8 位小数串）/`days`（2 位小数串，如 `"4.36"`）/`count`（整数字符串）/`z`（4 位小数）。内部一律标度整数（`SCALE_AMOUNT=2`、`SCALE_RATIO=8`、`SCALE_PRICE=6`、`SCALE_QTY=4`），禁止 float/Numeric。
- **口径变更纪律**：改公式必须升 version + golden 独立 PR + `golden-approved`（Testing §12.3）；未升版而快照变化 → CI fail。

### 3.3 范围外清单（后续归属）

| 项 | 本期不做理由 | 后续归属 |
|---|---|---|
| A20 夏普 | 依赖 rf 与 60 日门槛，核心预算已满；可由 `timing.beta` 日收益序列顺带得到 | Period2-11 后 / 07b |
| A25 Brinson / 行业归属 | 无申万行业数据、无财报 | Period3-08 |
| 财报类 PIT / 财务质量因子 | 数据缺失（D-04） | 独立数据源任务 |
| 真实 A 股交易日历 | 需内置日历 + 重算 round + 口径 bump（D-09） | 后续任务 |
| live 指数基准适配 | 网络依赖、CI 不可测（D-03） | Period2-11 / 运维任务 |
| 前端卡片/抽屉/报告/趋势/提醒 | 非本任务产出 | Period2-12/09、Period3-02/03 |
| `nav_return` 指标（Catalog API-033 断言提到） | 不在 18 指标目录；净值类由 API-032 覆盖 | 另立/删除断言（C-06） |

### 3.4 与上游/下游任务边界

- **Period2-05 提供**：注册表自动发现、`MetricContext` 数据视图、框架代算（n/CI/sample_status/status/inputs_hash/data_as_of）、DAG/失效、`metric_result` 物化、门禁状态机。
- **Period2-06 提供**：`GET /metrics`、`/metrics/{id}`、`/explain`、`/drilldown`、ETag、`data_as_of`。
- **Period2-07 提供**：`core/stats`、`core/metrics`（18 个纯公式）、`review/metrics/defs/*`（18 个定义）、golden/oracle/契约 kit/mutation。
- **Period2-16 提供**：`review.asset_daily` 与 build 状态（07 只读，要求 `build.state=current`）。

---

## 4. 冻结契约（07 与 05/06 的接口）

> 本节是 07 实现的"编译期契约"：07 只依赖这些形状；若 05/06 设计与此不同，以本节为基准发起契约对齐（记录在评审文档）。

### 4.1 引擎契约（Architecture §11 + Extensibility §3.1/§3.2，不新增 MetricDef 字段）

```python
# review/metrics/defs/<domain>_<name>.py —— 每个指标一个文件，放入即被发现
class WinRateDef:                       # 结构示意（具体基类由 05 提供）
    id = "perf.win_rate"
    definition_version = "1.0"
    domain = Domain.PERF
    scope = ScopeSpec(unit="round", cohort="close_date", period="month")
    params_schema = WinRateParams        # extra="forbid"
    dependencies = (DatasetDep("rounds"), DatasetDep("trades"), DatasetDep("bars_daily"))  # 期末持仓由 def 内基于 trades+bars 重建（E-06/D-13）；dataset 白名单无 positions_end
    sample_policy = SamplePolicy(min_n=10, ci_min_n=10, ci="wilson")
    status_policy = (StatusRule("upstream=blocked"), StatusRule("upstream=stale"))
    unit, precision = Unit.RATIO, 8
    core_conclusion, frequency, benchmark_required, mvp = True, "sync", False, "P0"

    def compute(self, ctx: MetricContext) -> MetricValue:   # 纯函数
        ...                                                  # 只返回 value/n/decomposition/n_pending/ci_sample
    explain = ExplainSpec(conclusion_zh="...", method_zh="...", what_to_do_zh="...")
    drilldown = DrilldownSpec(endpoint="/metrics/perf.win_rate/drilldown", params={"period": "{period}"})
```

- **`MetricValue`（与 05/06 v1.1 §4.2 冻结一致）**：`value_units: int | None`、`n: int`、`decomposition: dict[str, int|str|None]`、`n_pending: int`、**`ci_sample: tuple[tuple[int, ...], ...] | None`（观测=元组，支持成对 bootstrap，E-15；必须升序排序后传引擎、仅内存交接不落库、长度上限 2000（超限只做确定性抽样并记 assumptions））+ `ci_stat_id: str | None`（内部字段，不出口 API，D-07）**。`ci_stat_id` 指向 `core/stats/estimators/` 包内自动发现注册表（pkgutil；07 加文件零改 05；如 `mean`、`ratio_diff_pgr_plr`、`beta_alpha`），引擎按 id 派发——**否则框架无法从原始观测重算 DE 的 bootstrap CI**（Extensibility §3.1 无统计量钩子）。约束：`ci="wilson"` 时占比类指标（如 win_rate）须给 Bernoulli 0/1 观测，引擎从 `ci_sample` 求 `success/n`；`ci="bootstrap"` 要求 `len(ci_sample)==n` 且 `ci_stat_id` 已注册（未注册 → 注册期 fail-fast）；`ci="none"` 时两者必须为 None。
- **框架代算**：`sample_status`（min_n/ci_min_n/pending）、CI（按 `sample_policy.ci` 策略对 `ci_sample` 计算）、`status`（status_policy + upstream）、`assumptions`、`inputs_hash`、`data_as_of`、`computed_at`、持久化/失效。
- **纯度**：`compute` 内禁 socket/open/sqlalchemy/session/`datetime.now`/`random`/float；契约 kit 静态+动态扫描（§8.6）。
- **错误处理**：`compute` 不抛业务 `ApiError`；上游不可用由 `status_policy` 表达；除零/无定义返回 `value_units=None` + decomp 提示，由引擎判 `insufficient/unreliable`。
- **`MetricContext` 数据视图**（07 建议字段，05 可微调但语义不变）：

```python
class MetricContext:
    user_id: UUID
    period: Period            # start/end（业务日 Asia/Shanghai，闭区间）
    as_of: date               # PIT 门禁：min(clock.today, market.as_of, asset_build.data_as_of)
    rounds: RoundsView        # closed/open/data_error + pnl/cost/holding_days/close_date/open_date
    fills: FillsView          # round_fill 明细（下钻/资金占用）
    trades: TradesView        # category/amount/quantity/price/fees/trade_date/line_no
    asset_daily: AssetDailyView    # date/total/market_value/cash/daily_return/status
    asset_build: AssetBuildView    # state/last_status/data_as_of/rows_count
    bars: BarsView            # 日线（as_of 截断）；benchmark 单独视图
    benchmark: BenchmarkView  # hs300_tr 全收益日收益；available=False 时相关指标 blocked
    calendar: CalendarView    # 交易日（M1=weekday proxy，带 assumption）
    rf_annual: Decimal        # settings，默认 0.015
```

- **持仓视图边界（E-06/D-13）**：`holdings_daily`/`positions_end` **不入视图层**（`MetricContext` 不含这两个字段），由 07 def 内基于 `trades`+`bars` 内存重建（§6.5）；registry 的 dataset 白名单不含 `holdings_daily`/`positions_end`；`MetricContext` 字段与各视图字段以 05/06 设计 §4.3 冻结为准。

### 4.2 端点契约（API-033/034/035/079/038，07 只消费）

- **`MetricResult`**：`metric_id/definition_version/scope/period/value?/unit/n/ci_low/ci_high/sample_status/status/assumptions/data_as_of/inputs_hash/computed_at` + **新增 `decomposition?`（D-24）**。
- **value 出现规则（D-08，07 保证、06 执行；依据 Catalog §0.8"blocked 时 value 字段缺省"+API-033 断言②③）**：

| status | sample_status | value | ci_low/high | conclusion |
|---|---|---|---|---|
| `ok` | `ok`（n≥10，`ci≠none`） | 有 | **有** | 有 |
| `ok` | `ok`（n≥10，`ci="none"`） | 有 | 无（该指标不定义 CI） | 有 |
| `ok` | `insufficient`（1≤n<10） | 有（原始值） | 无 | 无（null） |
| `ok` | `insufficient`（结构性无定义：n=0 / n_win=n_loss=0 / n_pos<2 / turnover=0） | **存在但为 null** | 无 | 无 |
| `ok` | `pending_window`（日频<60） | **无 value 键** | 无 | 无 |
| `blocked` / `stale` / `unreliable` | 任意 | **无 value 键**（非 ok 一律不出数字，Architecture §11） | 无 | 无（附 reason/what_to_do） |

- `explain`：`conclusion` 禁裸术语（词表见 §8.6）；非 ok → `conclusion=null` + `reason/what_to_do` 非空。
- **`DEF` 导出约定**：07 每个 def 模块统一导出模块级实例 `DEF`（Extensibility §3.5，唯一名字，registry 只认 `DEF`；05/06 v1.1 §4.3）。
- `drilldown`：`items[]` 至少含 `key/round_id?/trade_id?/event_date?/value/unit/label`；`source_endpoints[]` 可复核；`params` 回填 `period`；**07 def 必须实现 `DrilldownSpec.build_items(ctx, filters)`（pure，E-12）**——引擎只负责排序/分页/格式化，上限 5000 + `truncated`。
- `ETag`（弱校验 `W/"<sha16>"`）/`304`（判定在写库前，304 不写库）、`data_as_of`、`definition_version` 由 06 统一；**`definition_version` bump → 插入新版本行 + 返回 `ok`（旧行保留），对外 `stale` 仅对 `asset_build.state=stale`/`market.as_of` 滞后可达**（E-13/C-41）；07 保证同输入两次计算 `inputs_hash` 相同。

### 4.3 05/06 前置清单（07 开工前必须就绪）

| # | 前置项 | 验收标准 | 归属 |
|---|---|---|---|
| P1 | `review/metrics/` 包 + `MetricDef` Protocol + 自动发现 | 放一个测试 def 即被发现；registry 校验 id 唯一/正则 | 05 |
| P2 | `MetricContext` 数据视图 + `metric_result/metric_dependency` 迁移（含 RLS） | 迁移 up/down；RLS 门禁覆盖；`conftest._REVIEW_TABLES` 更新 | 05 |
| P3 | 框架代算 `sample_status/CI/status/inputs_hash/data_as_of`；`MetricValue.ci_sample` 交接 | 契约 kit `assert_sample_gate` 全绿；DE/BHAR 的 bootstrap CI 与 oracle 一致 | 05 |
| P4 | `GET /metrics`、`/{id}`、`/explain`、`/drilldown` | API-033/034/035/079 契约测试可跑 | 06 |
| P5 | 常量集中：`Z_95/MIN_TRADING_DAYS/RF_ANNUAL_DEFAULT`，`MIN_CI_SAMPLE=10`，`SamplePolicy.ci` 支持 `"none"` | `contracts/constants.py` 与该设计一致（C-17） | 05 |

> **05 验收口径修订（E-13/C-41）**：DEV-PLAN 05 原文"definition_version bump → 旧结果 100% stale"改为"**新版本行插入 + 旧行保留 + `persist.is_stale` 单测**"；对外 `stale` 仅 `asset_build.state=stale`/`market.as_of` 滞后可达。

**并行策略**：05/06 未就绪时，07 先落 `core/stats` + `core/metrics` + defs + golden + oracle + mutation；单测直接调纯函数/用 fake ctx，不依赖引擎，可作为 05/06 的"下游第一个消费者"反向驱动契约。

---

## 5. 统计内核设计（`stock_core/stats`）

> 新模块 `backend/packages/core/src/stock_core/stats/`：纯函数、零 I/O、零时钟、随机由显式 seed 传参；全部 Decimal（`prec=28`）或整数，**禁止 float**；输出回写标度整数。

### 5.1 模块划分

> **归属边界（05/06 v1.1 §4.4，E-05/E-15）**：05 交付 `numeric/wilson/bootstrap` + `estimators/` 框架（含 `mean` estimator）；**07 只交付 `regression/concentration/drawdown/tstat` + 各指标 estimator 文件**（`estimators/` 包内 pkgutil 自动发现，07 加文件零改 05；`ci_stat_id` 未注册 → 注册期 fail-fast）。

| 文件 | 归属 | 职责 | 关键函数 |
|---|---|---|---|
| `numeric.py` | 05 | 定点/整数工具 | `div_round(a,b,scale)`、`sqrt_units(a,scale)`、`median_units`、`pct_index`、`xorshift64_star(seed)`（纯整数） |
| `wilson.py` | 05 | 二项比例 CI | `wilson_interval(success, n, z_units=1959964, scale=8)` |
| `bootstrap.py` | 05 | 确定性 bootstrap | `bootstrap_percentile(samples, stat, *, b=1000, seed, alpha_permille=(25,974))` |
| `estimators/` | 05 框架+`mean`；**07 加文件** | estimator 注册表（包内 pkgutil 自动发现） | `register(id)(fn)`；`fn(samples: Sequence[tuple[int,...]]) -> int`（标度整数统计量） |
| `regression.py` | 07 | OLS β/α、t、IR | `beta_alpha(y_units, x_units, rf_daily_units, scale=8)` → BetaAlpha |
| `concentration.py` | 07 | Gini/CR1/TopK/HHI | `gini_units(sorted_values, scale=8)`、`top_k_share`、`hhi(weights)` |
| `drawdown.py` | 07 | 回撤族 | `drawdown_family(index_units, annualized_twr_units)` → MDD/duration/recovery/underwater/ulcer/calmar |
| `tstat.py` | 07 | 聚类 t 值 | `cluster_t(events: Sequence[tuple[date,int]])` |

### 5.2 精度与舍入

| 量 | 内部 | 输出 | 舍入 |
|---|---|---|---|
| 金额 | `int` 分 | `"1234.56"` | ROUND_HALF_UP |
| 比率 | `int` 1e-8 | `"0.43210000"` | ROUND_HALF_UP，8 位（注意 `Ratio.as_pct` 走 HALF_EVEN 展示，不用于指标计算） |
| 价格 | `int` 1e-6 | 如需 `"12.340000"` | 6 位 |
| 天数 | `int` 1e-2 | `"4.36"`（D-15） | 2 位 |
| 计数 | `int` | `"12"` | 无 |
| z 值 | `int` 1e-4 | `"1.2345"` | 4 位 |
| 中间量 | `Decimal(prec=28)` | — | 出口只量化一次 |

规则：除法/开方/幂在 Decimal 域完成，量化只在函数出口做一次；禁中途截断。回归累加器用 int（`Σxy` scale=16，n≤244 时 <2.4e18 <2^63−1，加 `_MAX_UNITS` 断言防溢出）；HHI 的 `Σmv²`（scale 16，资产大时 ≈1e26）必须 Python 任意精度 int，**不得下推 int64/SQL**。

### 5.3 确定性与随机（D-07）

- `seed = int.from_bytes(sha256(f"{metric_id}|{definition_version}|{inputs_hash}".encode()).digest()[:8], "big")`（与 Clock 无关）。
- PRNG：`xorshift64_star`/`SplitMix64`（纯整数、64 位掩码）；`index_below(n)` 用拒绝采样（`limit=2^64−2^64%n`），消除模偏。
- B=1000；percentile 最近秩 + 线性插值（`pct_index`，B=1000 → 输出下标 25/974）；无效 replicate（分母=0、剔除）丢弃并重抽，`B_eff<500` → CI 缺省。
- 稳定性测试：同 `(samples, seed, b)` 两次调用逐位相等；不同 seed → 结果应不同（防"忽略 seed"）；`ci_sample` 排序后输入，打乱原始观测顺序结果不变。

### 5.4 Wilson CI（胜率/占比类）

`c=(p+z²/2n)/(1+z²/n)`，`h=z·sqrt(p(1−p)/n+z²/4n²)/(1+z²/n)`，`p=k/n`，z=1.959963985。
边界：n=0 → `(None,None)`；k=0 → 下界 0；k=n → 上界 1。
校验值（Decimal）：`wilson(7,10)=(0.39677815,0.89220873)`；`wilson(8,12)=(0.39062209,0.86187991)`；`wilson(18,30)=(0.42320360,0.75409372)`；`wilson(30,30)=(0.88648661,1.00000000)`。

### 5.5 β/α/IR 与聚类 t（A23/A24）

- 日收益：`r_p=asset_daily.daily_return_units`（现金流调整）；`r_b`=基准同日收益（缺失日剔除）；`rf_d=(1+rf_a)^(1/244)−1`。
- OLS（**超额收益回归**；`x_t=r_b,t−rf_d`、`y_t=r_p,t−rf_d`）：`Sxx=n·Σxx−(Σx)²`、`Sxy=n·Σxy−Σx·Σy`、`Syy=n·Σyy−(Σy)²`；`β=Sxy/Sxx`（scale 8）；**`α_d=ȳ−β·x̄`**（x/y 已扣 rf，不再重复减 rf）；`α_ann=α_d×244`；`SSE=(Syy−Sxy²/Sxx)/n`、`s²=SSE/(n−2)`、**`SE(α)=sqrt(s²·(1/n+n·x̄²/Sxx))`**、`t=α_d/SE(α)`；`TE=stdev(excess,ddof=1)×√244`；`IR=mean(excess)/stdev(excess)×√244`；超额算术 `mean(excess)×244`；超额几何 `((1+R_p)/(1+R_b))^(244/n)−1`。**公式校验值**（6 点示例）：β=1.15984016、α_ann=−0.00078896、SE=0.00071462、t=−0.0045、TE=0.02690477、IR=3.0230079、超额算术 0.08133333、几何 0.08302747。
  示例输入（百分比）：`r_b=[0.40,−0.30,0.90,0.20,−0.60,0.70]`、`r_p=[0.55,−0.20,1.10,0.30,−0.85,0.60]`、`rf_a=0.015`、`A=244`（rf_d=(1+rf_a)^(1/A)−1）。
- 样本门槛：`n_days<60` → `pending_window`（A26，**无 value**）；`Sxx=0`（基准恒定）→ `blocked`；方差=0 → `unreliable`。
- BHAR 聚类 t：事件日聚类，`G=事件日去重数`；`u_g=Σ_{i∈g}(BHAR_i−mean)`；`SS=Σu_g²`；`Var(mean)=G/(G−1)·SS/n²`；`t=mean·n·sqrt((G−1)/(G·SS))`（整数平方根 + 半进位）；`G<2` → `t=null`；单例聚类时 t 等于单样本 t（golden 自证）。

### 5.6 回撤族（A18）

- 指数：`I_0=1`，`I_t=I_{t−1}×(1+r_t)`（scale 8，逐日 round）；区间内 blocked 行 → `blocked`（不得跳行拼接）；NULL 洞剔除并计 `n_missing`；**有效交易日 <60 → `pending_window`（无 value，A26"日频<60 不出回撤"）**；剔除导致的洞存在但 ≥60 → `unreliable`。
- `MDD=max_t (peak_t−I_t)/peak_t`；`duration`=峰→谷行数；`recovery`=谷→首次回到前高的行数（期末未恢复 → null + `recovery_censored`）；`underwater_ratio=#{dd_t>0}/n_obs`；`Ulcer=sqrt(Σdd_t²/n_obs)`（`dd_t=(I_t−peak_t)/peak_t`，**n_obs = 日收益观测数，不含初始锚点重复计数**）；`Calmar=年化TWR/\|MDD\|`（MDD=0 → null+unreliable）；年化 TWR `(I_end)^(365/自然日数)−1`（与 `nav.py:189-193` 一致）。
- 日收益绝对值 >1（负市值/超卖）→ `unreliable`；重复 `trade_date` → `blocked`（数据完整性）。

### 5.7 边界与防御（统一）

`n=0/1/5/9/10/29/30/59/60` 逐档；除零（分母=0）；空样本；单元素（方差=0）；极端值（价格 0、数量 0）；超 int64 金额上限（9.2e16 元足够）；`Decimal.InvalidOperation` → 返回 None 由引擎判 `unreliable`（不抛异常）。所有函数返回 dataclass（便于演进），禁止裸 tuple。

---

## 6. 数据、窗口与状态传播语义

### 6.1 数据视图与字段

| 视图 | 来源 | 07 需要的字段 | 规模 | 说明 |
|---|---|---|---|---|
| `rounds` | `review.round` | `id/security_code/status/open_date/close_date/quantity_units/cost_units/proceeds_units/dividend_units/pnl_units/return_rate_units/holding_days/data_error/oversell_units` | ≤1e4 | 绩效/行为/选股 cohort 主源；open 只入全样本 |
| `fills` | `review.round_fill` | `round_id/buy_cost_units/holding_days/open_date/close_date/pnl_units` | ≤5e4 | 资金占用天数/下钻 |
| `trades` | `ledger.trade` | `id/trade_date/line_no/side/category/security_code/quantity_units/price_units/amount_units/commission_units/stamp_tax_units/other_fee_units/cash_balance_units/deleted_at/version` | ≤5e4 | 费用/换手/摊平/持仓重建（`amount_units` 已含费） |
| `asset_daily` | `review.asset_daily` | `trade_date/cash_units/market_value_units/total_assets_units/daily_return_units/status/definition_version` | ≤3e3 日 | 回撤/β/费用率分母/换手分母 |
| `asset_build` | `review.asset_build_state` | `last_status/last_built_through/rows_count/data_as_of/definition_version/start_date` | 1 行 | `stale/missing/blocked` 传播 |
| `bars` | `MarketDataSource.get_daily_bars`（replay 日线夹具/live） | `symbol/trade_date/close_units/return_units` | ≤500 日/标的 | 追涨/卖飞/持仓重建/BHAR |
| `benchmark` | replay `daily_bars.json` 的 `hs300_tr` | `trade_date/return_units` | ≤500 日 | β/α/IR/卖飞/BHAR；`available=False` → blocked |
| `calendar` | `business_days_between`（M1=工作日） | 纯函数 | — | A13；assumption `calendar=weekday_proxy` |
| `rf` | `settings.rf_annual` | Decimal | 标量 | 默认 `0.015`（D-14） |

### 6.2 `period` / lookback / `as_of`（D-18）

- `period=YYYY-MM` → `[月初, min(月末, as_of)]` **闭区间**；**`from/to` 半开 `[from,to)`，M1 仅接受"月初→次月初"（等价单月），跨月/单端 → 422 `INVALID_PERIOD`**（C-34/EC-P1-2）；`period` 与 `from/to` 二选一（API-033）。
- **回合型/已实现事件按 `close_date`**；open 回合按 `open_date ≤ end` 只入全样本；**成交型按 `trade_date`；资产型按 `trade_date`；选股 cohort 按买入 `trade_date`**。
- **滚动窗口 lookback**：`window_start = period.start − lookback_days`（按交易日历回退）；前向窗口 `window_end = max(period.end, period.end + forward_days)` 且 `≤ data_as_of`。lookback 由 def 常量声明，**不是**指标入参；`inputs_hash` 显式包含 `period_start/period_end/lookback/as_of/benchmark_id`。
- `data_as_of = min(clock.today(), market.as_of, asset_build.data_as_of)`；写入 `MetricResult.data_as_of`；PIT 硬约束 `window_end ≤ data_as_of`。
- **禁止**严格只用 period 内事件（期末前的 60 日 BHAR 会被截断，系统性低估）。

### 6.3 状态传播矩阵（上游 → 指标）

| 上游信号 | 受影响指标 | `status` | `sample_status` | `value` | `conclusion` | `what_to_do` 示例 |
|---|---|---|---|---|---|---|
| 对账 `degraded`（有补差行） | 全部 | 回合/费用类 `ok` + `assumptions.recon="degraded"`；#6/#10/#11 用 `unreliable`（非 ok → 无 value） | 按 n | 回合/费用类有；资产类无 | 回合/费用类有；资产类无 | 核对补差行 |
| 对账 `blocked`（断链未闭合） | **分级**：#6/#10/#11 资产类 → `blocked`；其余回合/费用/选股类 → `unreliable`（`assumptions.recon="break_open"`） | 按级 | 按级 | **均无** | 均无 | 先修复账单断链 |
| `asset_build.state=missing` | #6/#10/#11/#13/#15 | `stale`（无旧值→按 `blocked`） | `blocked` | 无 | 无 | 先构建资产表 |
| `asset_build.state=stale` | 同上 | `stale` | 按 n | 无 | 无 | 重建资产表 |
| 区间内 `asset_daily.status=blocked` 行 | #6/#10/#11 | `blocked` | `blocked` | 无 | 无 | 补该日行情 |
| 区间内 `asset_daily.status=degraded` 行 | #10/#11 | `unreliable` | 按 n | 无 | 无 | 查看降级日 |
| 持仓行情缺失（bars 无该日） | #8/#9/#14/#15/#16/#17 | `blocked`/`unreliable`（按覆盖率） | `insufficient`/`pending_window` | 无（仅 insufficient 有值） | 无 | 补行情或换 replay |
| 基准不可用 | #9/#10/#16/#17/#18 | `blocked` | `blocked` | 无 | 无 | 配置基准数据 |
| `n_trading_days<60` | #10/#11 | `ok` | `pending_window` | **无** | 无 | 积累更多数据 |
| `n<10` | 统计类 | `ok` | `insufficient` | 有（结构性无定义时 null） | 无 | — |
| `bar_density<0.8`（0.8 阈值） | #8/#9/#14 | `unreliable` | 按 n | **无** | 无 | 检查行情覆盖 |
| `definition_version` bump（口径升级） | 全部 | `ok`（插入新版本行、旧行保留；E-13/C-41） | 按 n | 有 | 有 | —（自动重算为新版本行） |

> `blocked/stale/unreliable` 无 value 键是硬约束（Architecture §11"非 ok 不返回 value 与 conclusion"；Catalog §0.8 只明写 blocked，c-31 需补 stale/unreliable）；`insufficient` 返回原始值（n=0/结构性无定义为 null）、`pending_window` 无值——四态差异见 §4.2（D-08）。**`stale` 语义收口（E-13/C-41）**：对外 `stale` 仅"上游数据状态过期"可达（`asset_build.state=stale`、`market.as_of` 滞后）；口径 bump 不返回 stale，而是插入新版本行 + 返回 `ok`。

### 6.4 行情能力协商（replay 现状与目标）

- 当前 replay 实际月频 9 点（`supports_daily_bars=True` 但数据密度不足）；**07 目标**：新增 `fixtures/market/daily_bars.json`（≥2 年工作日、4 条序列含 `hs300_tr`），replay 按区间返回日线；`provenance` 带文件 sha256；**不动 quotes.json**（D-02）。**装配路径**：`create_market_source` 现硬编码 quotes.json（`stock_ledger/market.py`），需加配置（如 `MARKET_BARS_FILE`/`mark<et.bars_path`）与双源能力声明——C-28。
- live：只支持 A 股 6 位代码 + 不复权日线；基准指数缺失 → `benchmark.available=False` → 相关指标 `blocked`（D-03）。
- **行情密度门禁**（D-28）：`bar_density=窗口内 bar 数/窗口内工作日数`；`=0 → blocked`；`0<x<0.8 → status=unreliable`（非 ok → 无 value）；阈值进 `core/metrics/policy.py`。
- **asset_daily 派生指标的底层价格密度**（A-P1-4）：`price_refresh_density=区间内价格变动日数/自然日数`；<0.8（如月频 replay）→ `timing.drawdown/beta` `status=unreliable` 并写 `assumptions.price_refresh_density`；D-02 日线夹具落地后应 ≥0.8（验收断言）。
- assumptions 固定输出：`benchmark_id`、`adjust="none"`、`factor_version=""`、`calendar`、`nav_source`、`rf_rate`、`A=244`、`pit_rule`、`window_basis`、`holdings_source`。

### 6.5 每日持仓内存重建（D-13；HHI/换手/摊平/浮盈亏共用）

```
holdings = {symbol: {qty_units, cost_units}}
for trade in stable_order(trades):            # (trade_date, line_no, id)
    if category in {buy, bonus, rights, new_stock}: qty += q; cost += |amount|  # 含费
    elif category == sell: qty -= q; cost -= proportional_cost(q)               # 与 FIFO 成本一致
    snapshot[date][symbol] = (qty, cost)
for date in every natural day in [period_start, period_end]:                 # 逐日，非首末两日
    for symbol: mv = qty × close_on_or_before(ladder, symbol, date)               # 逐标的 floor
    权重 = mv/total（HHI_total）或 mv/Σmv（HHI_eq）
    对账：Σmv == asset_daily.market_value_units 且 cash+Σmv == total_assets_units（0 分公差）
```

- 复杂度 O(T log T + D·S)：10 年 1e4 流水/2500 日/50 标的 ≈ 1.25e5 次运算，<50ms（预算 p95<200ms）。
- 逐日流式聚合（不保留全历史持仓列表）；`tracemalloc` 750 交易日×200 标的 <16MB（§8.9）。
- 对账不符日 → `unreliable` 计数并剔除；缺价日 → 剔除；覆盖率 <50% → `blocked`、<90% → `unreliable`；`assumptions.holdings_source="reconstructed"`。
- 超卖（负持仓）日：与 `asset_daily` 一致允许负市值但该日权重无意义 → 剔除并计 `n_excluded`。

### 6.6 回撤指数与年化

- 用 `asset_daily.daily_return_units`（已现金流调整，等价 A4/A5 的 TWR 日收益）复利成指数；不除以份额。
- `asset_daily` 是逐自然日行；`duration/recovery` 用行数（自然日）输出，decomp 同时给 `n_trading_days`（按周一~周五计，M1 近似）；assumptions 标注。
- 区间首日 `prev_total_assets_units IS NULL` → 跳过首日收益，`n_days−1`；`n_days<2` → insufficient。

### 6.7 assumptions 统一清单

| key | 取值示例 | 适用 |
|---|---|---|
| `benchmark_id` | `hs300_tr` | β/fly/BHAR |
| `rf_rate` | `"0.015"` | β/α/IR |
| `A` | `244` | 年化类 |
| `calendar` | `weekday_proxy` | A13/回撤 duration |
| `nav_source` | `asset_daily.daily_return` | 回撤/β/fee_breakeven |
| `pit_rule` | `price_only` | selection/all |
| `holdings_source` | `reconstructed` | HHI/换手/摊平 |
| `adjust` | `none` | 行情类 |
| `bootstrap` | `{"b":1000,"seed":"sha256:…","method":"percentile"}` | DE/BHAR/expectancy CI |
| `tolerance` | `eps_r=1bp` | 全部 |
---

## 7. 分域设计（18 个指标）

> 本节把四份域设计稿（绩效/择时/行为+选股）合并为可实现的 spec。每指标给出：口径→公式（标度整数）→输入/依赖→窗口→边界→sample/status→输出（decomp）→explain/drilldown→golden→必杀变异→HITL。三段式文案统一禁裸术语（词表 §8.6），中英双模板。

### 7.1 绩效域（perf，7 个）

**域公共口径**
- 回合口径：`status=closed` 入主 value；`data_error` **一律剔除**并计 `n_data_error`；`open` 只入全样本胜率（按 `open_date ≤ end`）。`round.pnl_units = proceeds − cost + dividend`（含费）。
- 费用：佣金/印花税/其他费四桶（含独立费用行，D-27）；`amount_units` 已含费，**不得二次扣费**。
- 归属：`close_date ∈ period`（D-18）；lookback=0（round 是预计算实体）；`benchmark_required=False`。
- `n` = 该指标分母样本数（rounds 数/盈利回合数/计费行数/ok 资产日数），门槛作用于分母。

#### 7.1.1 `perf.win_rate` — 已平仓胜率（含全样本并列）
- 口径：A10。公式：`n_closed=#{closed}`、`wins=#{closed ∧ pnl>0}`、`value=HALF_UP(wins×1e8/n_closed)`；`pnl=0` 既不算胜也不算负但计入分母；`n_closed=0 → value:null+insufficient`。
- decomp：`win_rate_all`（含 open 浮盈浮亏；perf 单测小夹具 5/9 → 0.55555556，主夹具 `fx_metrics_core` → 0.60000000；不可定价时 null+`assumptions.all_sample="unavailable"`）、`n_closed/n_open/n_data_error/wins/n_zero/n_all`。
- CI：Wilson（n≥10）。校验：`wilson(8,12)=(0.39062209,0.86187991)`；`wilson(18,30)=(0.42320360,0.75409372)`。
- 输入：`rounds` + 期末逐标的浮盈亏（**07 在 def 内基于 `trades`+`bars` 重建**，E-06/D-13；`positions_end` 不入视图层、不在 registry dataset 白名单；缺失仅降级 decomp，不阻断 value）。
- golden：`fx_metrics_core` 12 closed、8 盈 → **0.66666667**；`win_rate_all` 9/15=**0.60000000**；n=30 变体 → 0.6 + CI。容差 1bp。
- 必杀变异：`>`→`>=`（`test_win_rate_excludes_zero_pnl`）；分母 `n_closed`→`n_all`；纳入 data_error；整数截断。
- explain：zh「这段时间你平掉的 12 笔交易里，8 笔是赚钱的，胜率 66.7%」/en；method「已平仓回合中盈利回合的占比，附 95% 置信区间」。
- drilldown：`/metrics/perf.win_rate/drilldown?period=` → `{round_id, security_code, close_date, value(pnl CNY), unit, label}`；`source_endpoints=["/rounds/{id}","/rounds/{id}/fills"]`。

#### 7.1.2 `perf.pnl_ratio` — 盈亏比
- A11：**一次舍入** `value=HALF_UP( (Σwin/n_win) × 1e8 / (|Σloss|/n_loss) )`（有理数先合并再量化，避免 avg_win/avg_loss 各自舍入后相除产生 1e-8 级偏差；decomp 的 avg_win/avg_loss 才是各自 2 位量化值）；decomp `pnl_ratio_median`、`avg_win/avg_loss/median_win/median_loss`、`n_win/n_loss/n_zero`。
- 边界：全胜/全亏 → `value:null`+insufficient；median 偶数取两中位均值再 HALF_UP；`|avg_loss|=0` 除零同上。
- 无 CI（`ci="none"`，D-15）。golden：fx_metrics_core → **1.63381463**（median 1.2 由变体）；容差 1bp。
- 变异：去 `abs`；除以 `n_closed`；median 取上中位；`pnl=0` 计入亏损。
- explain：「赚钱的那几笔平均赚 X 元，亏钱的那几笔平均亏 Y 元，赚的是亏的 1.63 倍」。

#### 7.1.3 `perf.holding_days` — 平均持仓天数（exploratory）
- A13：`value=HALF_UP(Σ round.holding_days/n_closed)`（工作日近似，D-09）；decomp `median_holding_days`、`mean_natural_days`（`(close−open).days`，日历天差；A1 09-01→09-08=7）、`capital_weighted_days`（按 `round_fill.buy_cost_units` 加权）、`capital_weighted_natural_days`。
- 输出 `days precision=2`（D-15）。open 回合 `holding_days=0` 必须排除；同日记 1。
- golden：fx_metrics_core → **4.00**、median 4.00、自然日 5.66666667、自然日 median 6.00（排序 1,2,5,5,6,6,6,6,7,7,8,9）；容差 0.01 天。
- 变异：纳入 open；`+1`→`+0`；成本权重换数量权重；median 上中位。assumptions `calendar=weekday_proxy`。

#### 7.1.4 `perf.concentration` — 盈亏集中度（exploratory）
- A14/D-10：`pos=desc([pnl>0])`、`S=Σpos`；`value=HALF_UP(Σtop min(3,n_pos)×1e8/S)`；decomp `cr1`、`gini`（盈利升序，`G=2Σi·x_i/(nΣx)−(n+1)/n`）、`n_pos`、`top3_k`、`top3_amount`、`positive_total`。
- 边界：`n_pos=0` → null+insufficient；`n_pos<2` → Gini null+insufficient；`n_pos=3` → value=1.0 合法（explain 说明）；`pnl=0` 不入分母。
- golden：fx_metrics_core → **0.81170984**、CR1 **0.31427381**、Gini **0.49793802**、n_pos=8；容差 1bp。
- 变异：`>`→`>=`；分母含亏损；Gini 分母 `2n²μ`→`n²μ`；Top3 升序。

#### 7.1.5 `perf.fee` — 费用合计与占比
- A21/D-27：`fee=Σ(commission+stamp+other)_units + Σ|amount_units|（category='fee' 且三列全 0 的行）`；`turnover_gross=Σ money_mul(qty,price)`（category∈{buy,sell}）；`value=HALF_UP(fee×1e8/turnover_gross)`。
- decomp：`fee_amount/commission/stamp_tax/other_fee/standalone_fee/turnover_gross/turnover_net/fee_ratio_net/n_fee_rows`；**组不变量** `Σ4桶=fee_amount`。
- 边界：`turnover=0` → value:null+insufficient；仅买方合法；成交行费用列全 0 且历史缺失 → `status=unreliable` + `assumptions.fee_columns_missing=true`；独立费用行不得与费用列重复（二次扣费）。
- golden：fx_metrics_core → **1250.02/2605200.00 = 0.00047982**（佣金 651.32 + 印花 598.70）；容差：微比率 `1e-8` 绝对 + 0.5% 相对（D-23）。
- 变异：漏印花税；二次扣费；分母纳入 transfer/fee 行；从 `amount_units` 再扣费。assumptions `fee_scope="commission+stamp+other+standalone"`。

#### 7.1.6 `perf.fee_breakeven` — 费用回本门槛
- A22/D-11：`annualized_fee=HALF_UP(fee×365/D)`；`avg_holding_mv=HALF_UP(Σ asset_daily.market_value/n_ok)`（**期间日均**，不年化）（只取 `status≠blocked`）；`value=annualized_fee/avg_holding_mv`；`twr=Π(1+daily_return)−1`；`annualized_twr=(1+twr)^(365/(last−first))−1`；`twr_amount=avg_total_assets×annualized_twr`；`twr_eaten_pct=annualized_fee/(|twr_amount|+annualized_fee)`（分母≤0 → null+unreliable）。
- 边界：`avg_holding_mv=0` → null+insufficient；区间含 blocked 行 → blocked；`build.state≠current` → stale；`D<28` → unreliable；TWR 链断 → unreliable。
- golden：`fx_asset_core` + `fx_metrics_core`（由 oracle 逐日推演）→ 值级示例：`0.05214437`、`twr_eaten_pct=0.49206743`；容差 D-23。
- 变异：`365→244`；`D` 用 `last−first`；blocked 行入均值；Y% 分母漏 `+annualized_fee`。
- explain：「按这个月的成交成本折算成年化，相当于每年吃掉持仓市值 X%；它占掉了这段时间收益的 Y%（时间加权收益口径）」。

#### 7.1.7 `perf.expectancy` — 期望值与盈亏平衡胜率（exploratory）
- A12：`p=wins/n_closed`、`W=Σwin/n_win`、`L=|Σloss|/n_loss`；**一次舍入** `E=HALF_UP( p·W − (1−p)·L )`（有理数合并后量化；主夹具无零回合 → `E≡mean_pnl=1042.16`）；`p*=L/(W+L)`。decomp `mean_pnl=Σpnl/n_closed`（零回合存在时与 E 不同；perf 单测小夹具示例 E=164.29 vs mean_pnl=200.00，见 §13.2）、`p/win_loss_ratio`。
- 边界：全胜 → `p*=null`（value 仍出）；全负 → `p*=1.0`；`pnl=0` 按 A10 计入非胜（不修 A12 原文）。
- CI：bootstrap（均值型；n≥10），seed 见 §5.3。golden：fx_metrics_core → **1042.16 CNY**、`p*=0.37967744`；容差 CNY 0.01/ratio 1bp。
- 变异：`p` 排除零回合；亏损项符号；`p*=R/(1+R)`；W 用 median。

### 7.2 择时域（timing，4 个）

**数据可用性硬结论（D-02 的技术依据，可复算）**

| 指标 | 需要的窗口 | replay 现状（月频 9 点，跨度 174 工作日） | 覆盖率 | 现状结论 |
|---|---|---|---|---|
| `timing.chase` | 买入前 20 条日线 | 任意 20 交易日前向窗口内 bar 数分布 `{0:14 天, 1:160 天}` | **0/174 天满足 20 条** | `insufficient`（n_valid=0，无结论） |
| `timing.fly` | 前 60 bar 日收益 + 后 h=5/10/20 bar + 基准同 h | 每序列仅 **8 个日收益**（60 的 13.3%）；h=20 前向 0 天有 20 条 bar | ≈0 | `insufficient`；live 亦缺基准 |
| `timing.beta` | 60 个成对日收益 | 组合侧 132 工作日行，基准侧仅 8 个月频点 → 成对 ≤8 | **8/60=13.3%** | `pending_window`（A26） |
| `timing.drawdown` | ≥60 个非 NULL `daily_return` | 132 工作日行、无 blocked | **220% 达标** | 数值可算；但 demo 全表 degraded → `unreliable` |

> 结论：**不扩日线夹具，4 个择时指标有 3 个在 replay 下数学上不可算**（不是精度差）。方案 A′（先交付 drawdown + 补日线夹具后上 chase/fly/beta）见 D-02/D-06；日线夹具是 timing 与 selection 的共同硬前置，成本可摊薄。

**域公共口径**：事件归属按 `trade_date`（chase 买入/fly 卖出）；窗口按 **bar_index**（第 i 条日线，D-28）；前向窗口可越过 `period_end` 但止于 `data_as_of`；交易日计数以 bars 的 distinct `trade_date` 为准；密度门禁见 §6.4；`adjust="none"`（D-21）；rf/年化见 §5。

#### 7.2.1 `timing.chase` — 追涨分位
- A17/D-28：`q=(P_buy−min20)/(max20−min20)`，窗口 `T−20..T−1`（bar_index）；`value=mean(q)`；decomp `q_mean_z`（z=(P−mean20)/s20，ddof=1）、`coverage`、`share_gt_1`、`n_flat`、`window_basis`。
- 边界：`den=0` → 事件剔除计 `n_flat`；窗口不足 20 bar → `n_pending` 不进分母；`P_buy>max20` 不截断（`q>1` 计数）；bars 空/密度=0 → blocked；密度<0.8 → unreliable；除权嫌疑 → unreliable（D-21）。
- golden：`fx_bars_min`（93 工作日、4 序列含基准）+ 手算表（20 根等差收盘：`q=0.78947368`；均值三事件 `0.26111111`）；容差 1bp。
- 变异：窗口含买入日；分母换 σ；均值分母用 `n_total`；常量 20→21；`>`↔`>=` 窗口满判。

#### 7.2.2 `timing.fly` — 卖飞率（exploratory）
- A16：对每个卖出腿（按 `close_trade_id` 去重）：`σ`=卖出前 60 bar 日收益样本标准差（ddof=1）；`R_after=Π(1+r_i)−1`（后 h bar）；`z=(R_after−R_bench,h)/(σ·√h) > 1.0`（严格）；`value=#{hit}/n_valid`（h=20），decomp `fly_share_h5/h10`、`weighted_share_h20`、`z_median`、`n_pending`。
- 边界：`σ` 观测<20 剔除；h 窗口覆盖<50%/尾部无 bar（退市）剔除计 `n_pending`；`z==1.0` 不命中；`pending_ratio>0.3` → insufficient；缺基准 → blocked；基准用原始收益（§13.2 已定：不做 β 调整，加权版仅 decomp 预留）。
- golden：`fx_bars_min` + `fx_bars_missing` + 手算（σ=0.02、√5：z={1.78885438,−0.22360680,0.89442719,0}→0.25）；容差 1bp/z 1e-4。
- 变异：`z>1`↔`>=1`；σ ddof；`√h`→`h`；pending 入分母；停牌规则反转。

#### 7.2.3 `timing.beta` — 基准超额（β/α/IR/TE）
- A23：组合 `r_p=asset_daily.daily_return`，基准 `r_b`；估计窗口 = 期末回看 60 交易日（D-18）；公式见 §5.5；`value=β`；decomp `alpha_annual/alpha_t/ir/tracking_error/excess_arith/excess_geo/n_obs/n_missing/rf_rate/a_base/benchmark_id`。
- 边界：`n<60` → `pending_window`（**无 value**）；Sxx=0 → blocked；`|r_p|>1` → unreliable；`daily_return` NULL 洞剔除，剔除后<60 → pending；缺基准 → blocked；`asset_build` 非 current → stale。
- golden：`fx_asset_core`（60 交易日）+ `fx_bars_min` 基准段 + oracle（手算 6 点校验链：β=1.15984016、α_ann=−0.00078896、t=−0.0045、TE=0.02690477、IR=3.0230079）；容差 1bp/t 1e-4。
- 变异：`Sxy/Sxx` 倒数；A=244→252；α 漏 rf/β；TE 用 `r_p`；几何→算术；门槛 60→0。

#### 7.2.4 `timing.drawdown` — 最大回撤族
- A18/D-12：序列 = `asset_daily` 中 `status≠blocked` 且 `daily_return IS NOT NULL` 的行（逐自然日、现金流已剔除）；公式见 §5.6；`value=MDD`；decomp `mdd_duration_td`、`mdd_recovery_td`（未恢复 null+`recovery_censored`）、`underwater_ratio`、`ulcer`、`calmar`、`twr`、`ann_twr`、`n_obs`、`n_missing`、`n_trading_days`。
- 边界：blocked 行 → blocked（断链不拼接）；NULL 洞剔除后 <60 → **pending_window**（无 value），否则 unreliable；`|r|>1` → unreliable；重复日期 → blocked；`build.state≠current` → stale；MDD=0 正常但 Calmar=null+unreliable。
- golden：60+ 交易日手算链（回撤-恢复 + 未恢复 + 等值峰）；手算小例（10 个日收益）：MDD=0.07829824、水下 8 天、Ulcer=0.04165262（n_obs=10，含初始锚点仅一次）、未恢复；容差 1bp/整数精确。
- 变异：峰 `<`↔`<=`；MDD 取末期；水下分母；恢复期右截尾写 0；Calmar 单位；blocked 当 0 收益。

### 7.3 行为域（behavior，4 个）

**域公共口径**：`DE` 采用 **Odean `PGR−PLR`（D-20）**；bootstrap 见 §5.3/§7.4；期末持仓/浮盈由内存重建（D-13）；`ci="none"` 用于 HHI/换手/摊平（D-15）；持仓重建不落库。

#### 7.3.1 `behavior.de` — 处置效应（core）
- A15/D-20（**勘误 A15 的符号**）：
  `G=#{closed ∧ close_date∈period ∧ pnl>0}`、`L=#{closed ∧ pnl<0}`（pnl=0 计 `n_zero`）；
  `g=#{证券: 期末持仓 paper_pnl>0}`、`l=#{证券: paper_pnl<0}`（按证券持仓 1 笔，见 §13.2）；
  `PGR=G/(G+g)`、`PLR=L/(L+l)`、`DE=HALF_UP((G(L+l)−L(G+g))×1e8/((G+g)(L+l)))`（**一次舍入**，避免 PGR/PLR 双舍入 ±1 单位）。
- 期末 paper_pnl = 市值 − 含费成本（**不含已收股息**；与 `asset_daily.unrealized_pnl` 同口径；主夹具 B5 = −603.20，含股息口径 −503.20 仅作对照，不采用）；`build_rounds(trades_to_events(trades≤period_end))` 得到 open_lots；缺价 → blocked（分母不完整）。
- decomp：`pgr/plr`、`holding_days_diff`（亏损回合均持−盈利回合均持）、`n_sold_gain/n_sold_loss/n_paper_gain/n_paper_loss/n_zero/n_data_error/n_partial_realized/n_missing_price`。
- CI：bootstrap 观测码 `{0=已卖盈,1=已卖亏,2=纸面盈,3=纸面亏}`，重抽 n 个，逐次重算 PGR/PLR/DE（分母=0 的 replicate 丢弃），`B=1000`、percentile 95%、`B_eff≥500`；`n=G+L+g+l`。
- 边界：`G+g=0` 或 `L+l=0` → null+insufficient；`data_error` 剔除并计 `n_data_error`（**不单独降级**；超卖/数据错误由对账与回合计数暴露）；`period_end>as_of` → blocked；自证哨兵：重建的 period 内 closed 回合数/pnl 和须与 `ctx.rounds` 一致，否则 unreliable。
- golden：`fx_metrics_core` → G=8、L=4、g=1（A6 浮盈）、l=2（B5/C5 浮亏）→ **PGR=0.88888889、PLR=0.66666667、DE=+0.22222222**；另 `fx_de_pos30`（只卖盈不卖亏 → DE>0 且 CI 不含 0）、`fx_no_de`（DE≈0 且 CI 含 0）；容差 1bp/计数精确。
- 变异：符号翻转（`PLR−PGR`，`test_de_direction_contract`）；`pnl>0`→`>=`；分母漏纸面仓（`G` 替 `G+g`）；seed 不随 inputs_hash 变。

#### 7.3.2 `behavior.turnover` — 换手率（exploratory）
- D-25：`num=Σ|amount_units|`（category∈{buy,sell}，period 内）；`avg_mv=HALF_UP(Σ asset_daily.market_value / |D_used|)`（非 blocked 日）；`raw=num/avg_mv`；`ann=num×244/(avg_mv×N_trading)`。
- decomp：`double_sided_amount/avg_market_value/raw_turnover/buy_amount/sell_amount/n_trading_days/n_natural_days_used/n_blocked_days`。
- 边界：`avg_mv=0` 且 num>0 → unreliable；num=0 → 0+insufficient；`D_used<0.8×period_days` → blocked；`build.state≠current` → stale。
- 无 CI。golden：手算例（num=2,200,000 分、avg_mv=1,100,000 分、N=20 → raw=2.0、ann=24.4）；容差 1bp。
- 变异：双边→单边；244→252；平均天数用 period_days；平均市值改期末值。

#### 7.3.3 `behavior.averaging_down` — 摊平（exploratory，量化定义见 D-25）
- 候选 = `category=buy ∧ qty_before>0`（持仓>0 ⇒ 必在同一 0→0 回合内）；摊平 = `price < 加仓前含费均价`（严格）；`value=n_avg_down/n_addon`；`depth=(均价−买价)/均价`；成本基规则与 `assets.py` 一致。
- decomp：`n_avg_down/n_addon`、`avg_depth`、`fwd20_excess_mean`（摊平后 20 交易日相对基准；未走完计 `n_pending`）、`round_pnl_diff`、`n_first_build/n_rights_addon/n_zero_cost`。
- 边界：`qty_before=0`（首次建仓）不计；`cost_basis=0 ∧ qty>0`（纯送转）不计；`price=0` 剔除；`n_addon=0` → null+insufficient；同日多笔按稳定序 `(trade_date,line_no,id)`。
- 无 CI。golden：持仓 1000/成本 10000 + 加仓 500@9.00 → depth=0.10、占比 0.5、fwd20 超额 0.07、回合 pnl 差 150.00；容差 1bp/CNY 0.01。
- 变异：`<`↔`<=`；去掉 `qty_before>0`；稳定序移除；深度分母用 price。

#### 7.3.4 `behavior.hhi` — 仓位集中度（exploratory）
- A19/D-13：逐日 `mv_i=qty×close//1e8`；`HHI_total=(Σmv_i²+cash²)/total²`；`HHI_eq=Σmv_i²/(Σmv_i)²`（空仓日无定义，不进 eq 均值）；`value=HHI_total 区间均值`；decomp `hhi_total_max/end`、`hhi_eq_mean/max/end`、`avg_holding_count`、`avg_cash_weight`、`n_days_used/blocked/empty/recon_mismatch`。
- 边界：`D_used=0` → blocked；全现金 → HHI_total=1.0（计入）、eq insufficient；单票 100% → eq=1.0；覆盖率<50% blocked/<90% unreliable；对账不符日剔除计 `n_recon_mismatch`；超卖负持仓日剔除。
- 无 CI（日间自相关，M1 不出）。golden：两日手算（d1 mv=[6000,3000]+cash1000 → total=0.46/eq=0.5556；d2 [4000,4000]+cash2000 → 0.36/0.50 → mean=0.41、max=0.46、end=0.36）；容差 1bp。
- 变异：HHI_total 漏现金；eq 分母含现金；逐标的 floor 改四舍五入；均值除 `period_days`。

### 7.4 选股域（selection，3 个）

**域公共口径**：cohort=买入日（`trade_date ∈ period`，`category="buy"`，D-26：rights/new_stock 只入 decomp、bonus 不计）；PIT 只用 `≤as_of` 的行情/事件（`assumptions.pit_rule="price_only"`，D-04）；基准必须 `hs300_tr` 全收益（缺失 → blocked）；`adjust="none"`（D-21）→ 除权嫌疑日 → unreliable；`frequency=async`（API-038；handler 归属 D-17）。

#### 7.4.1 `selection.bhar_20` — 买入事件 BHAR（h=20 交易日，core）
- A24：`BHAR = ch/c0 − bh/b0`（`Π(1+r)` 望远镜恒等，**一次整数除法**）：`j0=最后一个 bench_date≤buy_date 的 j`；`j1=j0+h`；**两腿统一锚在 `bench_date[j0]`**：`c0=close_on_or_before(个股,bench_date[j0])`、`ch=close_on_or_before(个股,bench_date[j1])`；`b0/bh` 取 `bench_close[j0]/[j1]`；decomp 记 `c0_lag_days`（个股停牌时的起点滞后）。
- `value=mean(BHAR)`（一次舍入）；decomp `median/t(事件日聚类)/hit_rate/n/n_pending/n_excluded_suspended/n_excluded_delisted/n_excluded_noprice/mean_benchmark_return`。
- 剔除（D-26）：窗口未走完（`j1` 不存在或 `bench_date[j1]>as_of`）→ `n_pending` 不进分母；停牌=窗口 bar 覆盖率<50%；退市=尾部 5 个基准交易日无 bar 且累计覆盖<90%；事件日无价 → `n_excluded_noprice`；无买入事件 → `n=0` 返回空结果不报错（FR-R4）；全部剔除 → `status=blocked`（reason=ALL_EVENTS_EXCLUDED，不得按 0 处理）。
- CI：均值 bootstrap（B=1000、percentile 95%、n≥10）。t 值：事件日聚类（公式 §5.5）；单例聚类=单样本 t（golden 自证）。
- golden：手算三事件（+0.05/−0.07/+0.05 → mean=0.01、median=0.05、hit=2/3、t=0.2500）；`fx_selection_bhar20`（含 pending/suspended/empty 变体）；容差 1bp。
- 变异：减基准改加；h 用 19/自然日；聚类键用 symbol；去掉 `G/(G−1)`；pending 入分母。

#### 7.4.2 `selection.bhar_60` — 买入事件 BHAR（h=60，core）
- 公式/视图/剔除/CI/输出与 bhar_20 逐项相同，仅 `h=60`、seed 前缀不同；golden：单事件 100→125 / 1000→1100 → **0.15000000**；容差 1bp。

#### 7.4.3 `selection.bhar_hit` — BHAR_20>0 占比（exploratory，可砍）
- 复用 bhar_20 事件集：`hit=#{BHAR>0}/n_valid`；`BHAR=0` 不计 hit；Wilson CI（n≥10）；decomp `n_hit/n_pending/n_excluded_*`、`mean/median`。
- golden：与 bhar_20 同 fixtures → **0.66666667**；变异：`>`↔`>=`；分母含 pending；Wilson 分母 n/n−1。

### 7.5 跨域一致性（实现约束）

| # | 一致性要求 | 落地 |
|---|---|---|
| X-1 | DE/PGR/PLR 笔数口径唯一（D-20），不得金额加权 | `core/metrics/behavior.py` 单一实现；golden 显式断言 DE=+0.22222222 |
| X-2 | 期末持仓/浮盈亏唯一原语（D-13）：perf 全样本胜率、behavior.de、HHI/换手共用 | `core/metrics/holdings.py`（纯）+ `review/application/metrics_positions.py`（装配）；`positions_end`/`holdings_daily` 不入视图层（E-06），由 07 def 内基于 `trades`+`bars` 重建；对账不变量 |
| X-3 | TWR 口径唯一：`perf.fee_breakeven` 与 `timing.drawdown` 共用 `asset_daily.daily_return` 复利链 | `core/metrics/portfolio_index.py`；两处值差 ≤1bp（测试断言） |
| X-4 | 费用/成交额口径唯一：`perf.fee` 与 `behavior.turnover` 共用 `money_mul/turnover` 原语（D-27） | 单一纯函数；`turnover_net` 交叉校验 |
| X-5 | bootstrap 唯一：DE/BHAR/expectancy 共用 `core/stats/bootstrap.py` 与 seed 规则（D-07） | 同 seed 逐位相等测试 |
| X-6 | `/nav` summary 的 `beta/alpha/t/IR`（Catalog API-032 声明）若实现，必须复用 `timing.beta` 的 core 函数 | 一致性测试（同 period 值相等） |
---

## 8. 测试用例设计

> 目标：**每个指标 1 份 golden，期望值来自手算表或独立实现，禁止用被测代码生成**；三方比对（手算表 / 独立 oracle / 生产）+ mutation ≥80%。现状：`backend/tests/` 有 `api/architecture/contract/integration/isolation/oracle/unit/`，**无** `golden/`、`fixtures/`、`perf/`。以下全部为新增。

### 8.1 测试分层

| 层 | 目录（★=新增） | 命名 | 运行命令 | 阻断 |
|---|---|---|---|---|
| L0 纯函数单测 | `unit/core/test_metrics_{perf,timing,behavior,selection}.py`★、`test_stats_ci.py`★ | `test_<func>_<behavior>`，每公式 ≥3 例（正常/边界/退化） | `pytest backend/tests/unit/core --cov=stock_core --cov-branch --cov-fail-under=100` | **硬**（core 100% 不变） |
| L1 golden + 三方比对 | `golden/<metric_id>/golden*.json`★、`golden/test_metrics_golden.py`★、`golden/test_metrics_three_way.py`★ | 目录=metric_id（`perf.win_rate/`） | `pytest backend/tests/golden -q -m golden` | **硬**（golden diff 未审批=一票否决） |
| L2a 契约 kit（纯） | `contract/kit.py`★、`contract/test_metric_plugin.py`★ | `assert_<property>`，参数化 `registry.all()` | `pytest backend/tests/contract/test_metric_plugin.py -q` | **硬**（缺一指标未跑=红） |
| L2b 契约 kit（连库） | `integration/test_metric_contract_db.py`★ | 用 `client/user/admin/owner_engine/app_engine` 既有夹具 | `pytest backend/tests/integration/test_metric_contract_db.py -q` | **硬** |
| L3 API 验收 | `api/test_api_033_metrics.py`★、`_034_metrics_explain.py`★、`_035_metrics_drilldown.py`★、`_038_selection.py`★、`_079_metrics_detail.py`★ | `pytestmark=mark.api`；模块 docstring 写 API-NNN | `pytest backend/tests/api -k "api_033 or api_034 or api_035 or api_038 or api_079"` | **硬**（每端 ≥3 条 + 门禁） |
| L4 隔离/越权 | `isolation/test_isolation_matrix.py`（扩矩阵）、`integration/test_metrics_rls.py`★ | 复用四张矩阵表 | `pytest -m isolation` + `gate_tenant_coverage.py` + `gate_rls_coverage.py` | **硬·零豁免** |
| L5 性能 | `perf/test_metrics_batch_p95.py`★、`perf/test_core_microbench.py`★ | `test_pf_<num>_<scene>` | `pytest backend/tests/perf -q -m perf` | PR-full 告警；release 超 2× 阻断 |
| L6 变异 | `pyproject.toml [tool.mutmut]`★、`scripts/ci/gate_mutation_score.py`★、`Makefile mutation/mutation-full`★ | mutmut 自动 | `make mutation`（diff）/`make mutation-full`（nightly） | nightly 硬（≥80%）；release 阻断 |

- 新增 pytest markers：`golden`、`perf`；`-m "not perf and not live"` 保持 PR-fast 默认。
- 确定性：默认 `FrozenClock("2026-09-30T15:00:00+08:00")`；**前向窗口类（selection.bhar_* / timing.fly / averaging_down.fwd20）用 `FrozenClock("2027-03-31T15:00:00+08:00")` + bars 延到 2027-03-31**，否则 h=60 窗口走不完（M-P1-7）；bootstrap 固定 seed；禁 `sleep`/`rerun`/`waitForTimeout`；golden/越权用例 `skip/xfail` 由 `gate_no_security_skip.py`★ 判红。
- 用例预算：L0 90 / L1 40 / L2a 22 / L2b 10 / L3 50 / L4 24 / L5 4；PR-fast ≤5min、PR-full ≤20min。
- **注意**：`backend/tests/contract/conftest.py` 用同名 autouse 夹具把 `_clean_db` 置空 → 需要 DB 的 kit 断言放 `integration/`。

### 8.2 golden 设计

**目录（D-23）**：`backend/tests/golden/<metric_id>/golden.json`（多份变体：`golden.n5.json`…）；loader `golden/loader.py`★ 用 `glob("golden/**/golden*.json")` 参数化。

```json
{
  "schema_version": 1, "metric_id": "perf.win_rate", "definition_version": "1.0",
  "fixture": {"id": "fx_metrics_core", "sha256": "<csv sha256>", "manifest": "backend/tests/fixtures/metrics/MANIFEST.json"},
  "period": "2026-09", "scope": "round",
  "params": {"basis": "closed", "benchmark": null, "rf": null},
  "input_snapshot": {"trades_token": "<count+Σversion+max(updated_at)>", "bars_sha256": "<fx_bars_min sha256>"},
  "expected": {"value": "0.66666667", "unit": "ratio", "n": 12, "ci_low": "0.39062209", "ci_high": "0.86187991",
    "sample_status": "ok", "status": "ok",
    "decomposition": {"win_rate_all": "0.60000000", "n_pos": 8, "n_neg": 4, "n_open": 3, "n_data_error": 0}},
  "tolerance": {"kind": "ratio_bp", "eps": "0.0001"},
  "expected_source": {"kind": "hand_calc", "ref": "§8.4 表 4-2", "oracle": null},
  "review": {"author": "test-designer", "reviewer": "zhang", "approved": true, "approved_at": "2026-10-27"},
  "boundaries": ["n=12 ≥ min_n：出 Wilson CI", "data_error 不入分母", "open 仅入 win_rate_all"]
}
```

硬规则：`value` 为字符串（防 float 中转）；`unit∈{CNY,ratio,bp,days,count,z,score}`；`ci_*` 仅在 `sample_status=ok 且 n≥ci_min_n` 时非 null；`review.approved=false` 由 `gate_golden_approval.py`★ 判红；`definition_version` 未升而快照变化 → fail。

**18 指标 golden 清单**

| # | metric_id | fixture | 期望值来源 | 容差 | 覆盖边界 |
|---|---|---|---|---|---|
| 1 | `perf.win_rate` | fx_metrics_core | 手算 | 1bp | n=12、全样本 decomp、pnl=0、n=5/9/10/29/30；data_error 用 fx_data_error 变体 |
| 2 | `perf.pnl_ratio` | fx_metrics_core | 手算 | 1bp | 全盈/全亏→null、median |
| 3 | `perf.holding_days` | fx_metrics_core + fx_multilot | 手算 | 0.01 天 | 多 fill 加权、median/自然日、open 排除 |
| 4 | `perf.concentration` | fx_metrics_core | 手算 | 1bp | n_pos=8、Top3/CR1/Gini、n_pos<2/3 |
| 5 | `perf.fee` | fx_metrics_core | 手算 | 1e-8+0.5% 相对 | 四桶、二次扣费、费用列缺失 |
| 6 | `perf.fee_breakeven` | fx_metrics_core + fx_asset_core | oracle+手算 | 1e-8+0.5% | 分母≤0、D<28、blocked 行 |
| 7 | `perf.expectancy` | fx_metrics_core | 手算 | CNY 0.01 | 全胜/全负、零回合偏差 |
| 8 | `timing.chase` | fx_bars_min + fx_metrics_core | oracle+手算 | 1bp | 窗口不含 T、flat 窗口、密度不足 |
| 9 | `timing.fly` | fx_bars_min + fx_bars_missing | oracle+手算 | 1bp/z 1e-4 | h=5/10/20、z=1 边界、停牌/未走完 |
| 10 | `timing.beta` | fx_asset_core + fx_bars_min | oracle+手算 | 1bp/t 1e-4 | 59/60 边界、缺基准、Sxx=0 |
| 11 | `timing.drawdown` | fx_asset_core + fx_dd_chain_60 | 手算+oracle | 1bp | 恢复/未恢复/等值峰/MDD=0/blocked |
| 12 | `behavior.de` | fx_metrics_core + fx_de_pos30/fx_no_de | 手算 PGR/PLR | 1bp/计数精确 | DE=+0.2222、CI 含/不含 0；`fx_de_pos30` **必须含纸面亏损**（否则 D-08 下 L+l=0→insufficient，FR-R6"DE>0 且 CI 不含 0"不可达） |
| 13 | `behavior.turnover` | fx_metrics_core + fx_asset_core | oracle | 1bp | 空仓、blocked 日剔除 |
| 14 | `behavior.averaging_down` | fx_multilot | 手算 | 1bp/CNY 0.01 | 严格低于均价、首次建仓排除、稳定序 |
| 15 | `behavior.hhi` | fx_metrics_core + fx_cash_only | oracle+手算 | 1bp | 双口径、全现金、对账不符剔除 |
| 16 | `selection.bhar_20` | fx_bars_min + cohort | oracle+手算 | 1bp | PIT、pending、停牌/退市剔除、无事件 |
| 17 | `selection.bhar_60` | fx_bars_min + cohort | oracle+手算 | 1bp | 59/60 边界 |
| 18 | `selection.bhar_hit` | fx_bars_min + cohort | oracle | 1bp | BHAR=0 不计、n=0 |

覆盖检查：`test_golden_manifest.py`★ 断言 `registry.all()` id 集合 == golden 目录集合；缺一即 fail。

### 8.3 夹具设计（全部新增、确定性、不依赖 demo 的 9 点月频行情）

**`fx_metrics_core`（主夹具）**：30 行流水 = 12 closed（A1–A5/B1–B4/C1–C3）+ 3 open（A6/B5/C5）；3 标的（600519/000001/300750）；含佣金/印花税/股息；余额链全程为正且闭合（`break_count=0`）；`amount_units` = 净现金流（买=-(量×价+费)，卖=量×价-费，股息=净额）。**主夹具不含超卖**（超卖会产生 FIFO 与 asset_daily 的持仓口径冲突，见 `fx_data_error`）。

| # | 日期 | 证券 | 方向 | 数量 | 价格 | 佣金 | 印花 | 净额 | 余额 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-08-31 | CASH | transfer | 0 | 0 | 0 | 0 | 400000.00 | 400000.00 |
| 2 | 09-01 | 600519 | buy | 100 | 1650.00 | 41.25 | 0 | -165041.25 | 234958.75 |
| 3 | 09-01 | 000001 | buy | 2000 | 12.50 | 6.25 | 0 | -25006.25 | 209952.50 |
| 4 | 09-01 | 300750 | buy | 100 | 213.00 | 5.33 | 0 | -21305.33 | 188647.17 |
| 5 | 09-07 | 600519 | dividend | 0 | 0 | 0 | 0 | 300.00 | 188947.17 |
| 6 | 09-08 | 600519 | sell | 100 | 1700.00 | 42.50 | 85.00 | 169872.50 | 358819.67 |
| 7 | 09-09 | 600519 | buy | 100 | 1680.00 | 42.00 | 0 | -168042.00 | 190777.67 |
| 8 | 09-09 | 300750 | sell | 100 | 220.00 | 5.50 | 11.00 | 21983.50 | 212761.17 |
| 9 | 09-10 | 000001 | sell | 2000 | 12.80 | 6.40 | 12.80 | 25580.80 | 238341.97 |
| 10 | 09-10 | 300750 | buy | 200 | 215.00 | 10.75 | 0 | -43010.75 | 195331.22 |
| 11 | 09-11 | 000001 | buy | 3000 | 12.60 | 9.45 | 0 | -37809.45 | 157521.77 |
| 12 | 09-15 | 600519 | sell | 100 | 1650.00 | 41.25 | 82.50 | 164876.25 | 322398.02 |
| 13 | 09-16 | 000001 | sell | 3000 | 12.40 | 9.30 | 18.60 | 37172.10 | 359570.12 |
| 14 | 09-16 | 600519 | buy | 200 | 1660.00 | 83.00 | 0 | -332083.00 | 27487.12 |
| 15 | 09-17 | 300750 | sell | 200 | 210.00 | 10.50 | 21.00 | 41968.50 | 69455.62 |
| 16 | 09-17 | 000001 | buy | 2000 | 12.30 | 6.15 | 0 | -24606.15 | 44849.47 |
| 17 | 09-18 | 300750 | buy | 100 | 205.00 | 5.13 | 0 | -20505.13 | 24344.34 |
| 18 | 09-22 | 600519 | sell | 200 | 1690.00 | 84.50 | 169.00 | 337746.50 | 362090.84 |
| 19 | 09-23 | 000001 | sell | 2000 | 12.70 | 6.35 | 12.70 | 25380.95 | 387471.79 |
| 20 | 09-23 | 600519 | buy | 100 | 1700.00 | 42.50 | 0 | -170042.50 | 217429.29 |
| 21 | 09-24 | 300750 | sell | 100 | 218.00 | 5.45 | 10.90 | 21783.65 | 239212.94 |
| 22 | 09-24 | 000001 | buy | 1000 | 12.80 | 3.20 | 0 | -12803.20 | 226409.74 |
| 23 | 09-25 | 600519 | sell | 100 | 1695.00 | 42.38 | 84.75 | 169372.87 | 395782.61 |
| 24 | 09-28 | 600519 | buy | 100 | 1640.00 | 41.00 | 0 | -164041.00 | 231741.61 |
| 25 | 09-29 | 600519 | sell | 100 | 1680.00 | 42.00 | 84.00 | 167874.00 | 399615.61 |
| 26 | 09-29 | 000001 | sell | 1000 | 12.90 | 3.23 | 6.45 | 12890.32 | 412505.93 |
| 27 | 09-29 | 300750 | buy | 200 | 215.00 | 10.75 | 0 | -43010.75 | 369495.18 |
| 28 | 09-30 | 600519 | buy | 100 | 1680.00 | 42.00 | 0 | -168042.00 | 201453.18 |
| 29 | 09-30 | 000001 | buy | 1000 | 12.80 | 3.20 | 0 | -12803.20 | 188649.98 |
| 30 | 09-30 | 000001 | dividend | 0 | 0 | 0 | 0 | 100.00 | 188749.98 |

回合映射：A1=#2/#6、A2=#7/#12、A3=#14/#18、A4=#20/#23、A5=#24/#25、A6=#28(open)；B1=#3/#9、B2=#11/#13、B3=#16/#19、B4=#22/#26、B5=#29(open)+#30(股息)；C1=#4/#8、C2=#10/#15、C3=#17/#21、C5=#27(open)。open 估值用 09-30 收盘（A6 1720.00、B5 12.20、C5 205.00）。不放红利税行（股息已税后，避免双计）。


**其余夹具（`backend/tests/fixtures/metrics/<id>/` + `MANIFEST.json` sha256）**

| fixture | 构造要点 | 用途 |
|---|---|---|
| `fx_bars_min` | **≥250 个工作日（2026-01-05..2027-03-31）**，4 条序列（3 标的 + `hs300_tr`），锚点线性插值到 0.01，2026-09-30 末值钉死 open 估值价（A6 1720.00/B5 12.20/C5 205.00）；`return_units`=日环比 8 位；`as_of` 变体用于 pending | 择时/β/BHAR/open 估值/前向窗口（h=20/60、fwd20） |
| `fx_n5` / `fx_n30` | 5 closed / 30 closed（18 盈）独立副本 | n 门槛、CI |
| `fx_de_pos30` / `fx_no_de` | 只卖盈不卖亏（DE>0 且 CI 不含 0）/ 无效应（CI 含 0） | FR-R6 |
| `fx_blocked` | 断链/无现金锚点变体 | blocked 无 value、部分阻断 |
| `fx_bars_missing` | 删某标的 09-14..09-30 bar；另一标的只留 5 根 | pending/blocked、密度门禁 |
| `fx_pit_asof` | 双快照（base as_of=09-15；future 09-16..09-30 收盘 ×3） | PIT：BHAR 不变、`data_as_of≤as_of` |
| `fx_selection_no_event` | 只保留卖出行 | 无买入事件 → 空结果不报错 |
| `fx_multilot` | 单标的 1 回合 3 买 2 卖（`fills_count=5`） | 加权持仓天数/摊平/下钻 |
| `fx_data_error` | 独立超卖夹具（买 100/卖 150），**不放入主夹具**；用于 data_error 剔除边界 | data_error 边界 |
| `fx_oversell` | 超卖 + 断链混合夹具 | 明确 FIFO 与 asset_daily 的持仓语义（实现期须统一，见 R10） |
| `fx_suspension` | 覆盖率 49%/50%/51% 三档 | fly 停牌剔除阈值边界（D-26） |
| `fx_delisted` | 尾部 5 日无 bar + 累计覆盖 89%/90% | 退市剔除阈值边界（D-26） |
| `fx_version_break` | 同一指标 definition_version 1.0→2.0 的两份结果 | stale/趋势断点/重算（Period3-02 前置） |
| `fx_n29` / `fx_n59` | n=29 样本 / 59 交易日 | min_n 与 min_trading_days 两个独立维度边界（M-P0-4） |
| `fx_cash_only` | 仅现金无持仓 | HHI_total=1.0、eq insufficient |
| `fx_corporate_actions` | 送转/配股/股息税（或毛股息+税行）各 1 例；验证 FIFO 成本/数量与费用口径不双计 | 送转/配股/股息边界；R8 |
| `fx_recon_break` | 余额链断链（中途余额错 100.00）+ 补差行 | 对账 blocked/degraded 分级传播 |
| `fx_asset_core` | 手写 22~60 行 `asset_daily`（逐行手算，含 1 行 blocked） | 回撤/费用门槛/换手/β |
| `fx_dd_chain_60` | 60+ 交易日日收益手算序列（回撤-恢复 + 未恢复 + 等值峰 + 单调段） | 回撤族全部成员 |

约束：owner=A、`synthetic:true`、MANIFEST sha256 缺条目即 fail；loader 只直插 `ledger.trade` + 调用生产 rebuild/build **物化输入**（允许），**期望值永不由生产代码生成**；测试期行情用 `ReplayMarketDataSource(<fixture path>)`，不读根 `quotes.json`。

### 8.4 手算表（核心交付；Python Decimal 独立脚本推导，非生产代码生成、非 float）

**回合级 pnl / 持有天数（fx_metrics_core，A8：pnl=卖出净额−买入含费成本+股息净额）**

| 回合 | pnl | 持有交易日 | 自然日 | | 回合 | pnl | 持有交易日 | 自然日 |
|---|---|---|---|---|---|---|---|---|
| A1 | +5131.25 | 5 | 7 | | B2 | −637.35 | 3 | 5 |
| A2 | −3165.75 | 4 | 6 | | B3 | +774.80 | 4 | 6 |
| A3 | +5663.50 | 4 | 6 | | B4 | +87.12 | 3 | 5 |
| A4 | −669.63 | 2 | 2 | | C1 | +678.17 | 6 | 8 |
| A5 | +3833.00 | 1 | 1 | | C2 | −1042.25 | 5 | 7 |
| B1 | +574.55 | 7 | 9 | | C3 | +1278.52 | 4 | 6 |

汇总：8 盈（Σ=18020.91）/4 亏（Σ=5514.98）/12 closed；open：A6 +3958.00（浮盈）、B5 −603.20（不含股息；含股息 −503.20 仅作对照）、C5 −2010.75（浮亏）。主夹具无 data_error（`fx_data_error` 单独覆盖）。

**指标手算表（`fx_metrics_core` 夹具的 `period=2026-09`；与演示验收 `period=2026-06` 无关，两者数据源不同）**

| 指标 | 公式 | 中间值 | 期望值 | 容差 |
|---|---|---|---|---|
| `perf.win_rate` | A10 已平仓 `pnl>0` 占比 | 8/12 | **0.66666667** | 1bp |
| `win_rate_all`（decomp） | A10 含浮盈亏 | (8+1)/(12+3) | **0.60000000** | 1bp |
| `perf.pnl_ratio` | A11 平均盈/abs(平均亏) | 2252.61375 ÷ 1378.745 | **1.63381463** | 1bp |
| `perf.holding_days` | A13 交易日均值 | 48÷12 | **4.00**；median 4.00 | 0.01 |
| 自然日（decomp） | A13 并列 | 68÷12 | **5.66666667**；median 6.00 | 0.01 |
| `perf.concentration` | A14 Top3/全部盈利 | 14627.75÷18020.91 | **0.81170984** | 1bp |
| CR1（decomp） | A14 | 5663.50÷18020.91 | **0.31427381** | 1bp |
| Gini（decomp） | A14（盈利升序） | 两公式互校 | **0.49793802** | 1bp |
| `perf.fee` | A21 费用/双边成交额 | 1250.02÷2605200.00（佣金 651.32+印花 598.70） | **0.00047982** | 1e-8+0.5% |
| `perf.expectancy` | A12 `p·W−(1−p)·L` | (2/3)×2252.61375−(1/3)×1378.745 = 1042.16083 | **1042.16** | CNY 0.01 |
| p*（decomp） | `1/(1+R)` | R=1.63381463 | **0.37967744** | 1bp |
| `behavior.de` PGR | 笔数口径（D-20） | 8/(8+1) | **0.88888889** | 1bp |
| `behavior.de` PLR | 笔数口径（D-20） | 4/(4+2) | **0.66666667** | 1bp |
| `behavior.de` DE | Odean `PGR−PLR` | 见上 | **+0.22222222**；A15 字面口径 = −0.22222222（待 D-20 拍板） | 1bp |
| `behavior.hhi`（小例） | A19 | 持仓 172000/12200、现金 50000（总资产 234200） | HHI_eq **0.87630874**（分母 Σ市值=184200）、HHI_total **0.58765819**（分母 234200） | 1bp |
| Wilson（A10, n=12） | 见 §5.4 | z=1.959963985 | **[0.39062209, 0.86187991]** | 1e-4 |
| `timing.drawdown`（小例） | A18 | 10 个日收益 `[0,+.01,−.02,−.03,+.01,−.04,+.02,+.03,−.01,+.02]` | **MDD=0.07829824**、水下 8 天、**Ulcer=0.04165262**、未恢复 | 1bp |

> 注：Ulcer 的 `n_obs=10`（初始锚点只算一次）；早期草稿用 11 个 nav 值（重复初始锚点）得 0.03971421，**按本表定义修正**。

**bootstrap 算法小例**（验证算法，非生产 CI）：5 个处置观测（2 已卖盈/1 已卖亏/1 纸面盈/1 纸面亏）→ PGR=2/3、PLR=1/2、DE=0.16666667；SplitMix64(seed=…) 重抽 5 个，无效 replicate 丢弃；断言同 seed 两次逐位相等 + 与 oracle 差 ≤1bp。

### 8.5 独立 oracle（`backend/tests/oracle/`）

| 文件★ | 职责 | 铁律 |
|---|---|---|
| `naive_metrics.py` | perf/behavior 朴素实现：win_rate/pnl_ratio/holding_days/concentration(Gini)/fee/expectancy/de_pgr_plr/hhi/bhar | 只 import 标准库（`decimal/dataclasses/statistics`），**禁 import `stock_core/stock_review/stock_ledger`**；自然单位直算（元/股/比例），不复刻生产结构 |
| `naive_stats.py` | wilson/bootstrap/percentile/ols_beta_alpha/mdd_family/gini | `Decimal.sqrt()`；PRNG=SplitMix64 显式常量；回归闭式 cov/var |
| `naive_timing.py` | quantile/z/fly/ols/drawdown | 暴力三重循环求峰-谷-恢复 |
| `naive_bhar.py` | 逐日连乘版 BHAR（不做望远镜化简） | 与生产技术恒等对拍 |

三方比对（`golden/test_metrics_three_way.py`★）：① `production == golden.expected`（容差走 `support/tolerance.py`★ 唯一入口，禁字面量）；② `oracle == golden.expected`（手算项则 oracle == 手算）；③ `production == oracle` 逐项（1bp/0.01/精确）；④ oracle 确定性自检 + `test_oracle_no_production_import`（AST）。**防退化铁律**：oracle 不得沿用生产的公式化简（BHAR 用逐日连乘、MDD 用暴力三重循环、Gini 用成对差公式、DE 用 Fraction 逐计数），至少 10 个核心指标的 golden 必须有独立手算表（不只是 oracle 生成）；oracle 与生产若同错，靠"手算表 + 交叉恒等式"兜底。

### 8.6 契约 kit（纯 kit + 连库 kit，参数化 `registry.all()`）

| 断言 | 内容（失败信息须含 metric_id@version + 字段 + 实际/期望） |
|---|---|
| `assert_metric_def_valid` | 13 字段齐全；id 正则；version 正则；`params_schema` BaseModel+`extra=forbid`；dependencies 存在/深度≤3/无环/level 合法；explain 三字段非空；drilldown endpoint ∈ OpenAPI、params ⊆ endpoint 查询参数；sample_policy 阈值来自 `contracts.constants`（禁魔数 10/30/60）；`core_conclusion` 总数∈[8,12] |
| `assert_purity` | AST 禁 import `time/datetime/random/socket/os/pathlib/sqlalchemy/fastapi/httpx/stock_platform/stock_ledger`、禁 `open/now/utcnow`；运行时 ctx 用"毒属性"桩（访问 `clock/session/random` 即抛） |
| `assert_deterministic` | 同 ctx 两次结果全等；打乱 rounds/trades 顺序结果不变；两次运行 `inputs_hash` 相同 |
| `assert_sample_gate` | 参数化 n=0/5/9/10/29/30/59/60（门槛表见下）；断言 compute 不返回 status/ci 字段 |
| `assert_no_status_leak` | `MetricValue` 字段集 ∈{value_units,n,decomposition,n_pending,ci_sample,ci_stat_id}；无 status/ci/data_as_of/conclusion 键 |
| `assert_drilldown_callable` | endpoint+params 存在；本人调用 200；item key 稳定；值格式按 unit；租户 B 调用不含 A 的 id |
| `assert_tenant_scoped` | A/B 同 fixture 各自独立期望；B 结果不含 A 的 UUID/金额；缓存不串；`metric_result` RLS 直连只见本人 |
| `assert_explain_no_jargon` | 词表 `DE,BHAR,PGR,PLR,TWR,MWR,FIFO,HHI,Wilson,β,α,IR` 词边界忽略大小写扫描 conclusion/reason/what_to_do；`method` 最多 1 个括注术语 |

**样本门槛表（D-08，测试断言唯一依据；样本维度与交易日维度是两个独立参数化轴，不得混用）**

| n / 交易日 | sample_status | value | ci | conclusion | status |
|---|---|---|---|---|---|
| 0 | insufficient | **有（null）**（区间无样本/结构性无定义；**除 insufficient 外均无 value 键**） | 无 | null | ok |
| 1–9 | insufficient | 有（结构性无定义为 null） | 无 | null | ok |
| 10–29 | ok | 有 | **有**（`ci≠none`） | 有 | ok |
| ≥30 | ok | 有 | 有 | 有 | ok |
| 交易日 59 | pending_window | **无** | 无 | null | ok |
| 交易日 60 | ok | 有 | 有 | 有 | ok |
| 上游 blocked | blocked | 无 | 无 | null | blocked |
| 上游 stale | stale | 无 | 无 | null | stale |
| 上游 unreliable | unreliable | 无 | 无 | null | unreliable |

配套结构 kit：`test_metric_discovery_is_decoupled`（测试 defs 路径放文件即被发现、kit 跑到、API 可见）、`test_metric_core_conclusion_budget`、`test_metric_depth_cap`、`test_no_wildcard_plugin_loading`、`test_change_scope`（新增指标 PR 只允许改 `defs/`+`core/metrics|stats/`+fixture）。**`assert_sample_gate` 分两个参数化轴**：`n∈{0,5,9,10,29,30}`（样本数→insufficient/ok+CI）与 `trading_days∈{59,60}`（→pending_window）；不得用 n=59 断言 pending_window。

### 8.7 API 验收矩阵（每端点 ≥3 条，happy/越权/错误码分开计数）

| 端点 | 关键用例 | 关键断言 |
|---|---|---|
| API-033 `GET /metrics` | happy `ids=perf.win_rate,perf.pnl_ratio&period=2026-09`（单测夹具 period；验收剧本用 2026-06）；`domain=behavior`+`include_exploratory=false`；fx_n5→insufficient；fx_blocked→坏指标无 value 而好指标仍 ok；fx_n30→`ci_low<value<ci_high`；越权 B token 只返回 B、body 无 A 的 UUID/金额；admin 403/无 token 401/read PAT 200；`ids`+`domain` 同传 422、未知 id 404、`period=2026-13` 422；`ETag`+304；每 item 含 version/data_as_of/assumptions/inputs_hash | blocked **无 value 键**；insufficient 无 ci 键；flat id 按 D-05 应 404（Catalog 断言须改） |
| API-034 `/explain` | happy；fx_n5→conclusion=null+reason/what_to_do；未知 metric 404、缺 period 422；Accept-Language=en-US 文案变数值不变；裸术语 0 命中；drilldown.endpoint+params 可调 | 非 ok 无 conclusion；drilldown 是可执行声明 |
| API-035 `/drilldown` | happy items 非空、item 含 stable key+value+unit+label、params 回填、source_endpoints 可调；格式按 unit；分页无重无漏、非法 cursor 400；越权 B items 为空/404 无 A id；admin 403/无 token 401；未知 metric 404、缺 period 422；`round_id/security_code` 过滤 | 比率型不得套金额 2 位断言（D-15）；drilldown 断言含 `source_endpoints` 可调、`items` 按 unit 定点（C-37：`event_date/key/truncated`、cursor 绑定查询指纹） |
| API-079 `/metrics/{id}` | 单指标完整血缘；blocked/stale/unreliable 无 value；insufficient 有 value 无 CI；与 API-033 同 item 逐字段相等；ETag/304；两次 `inputs_hash` 相同、改流水后变化；越权/错误码同 033；`definition_version` bump → 新版本行 + 返回 ok（旧行保留，E-13/C-41） | 全字段出现规则与门槛表逐格一致 |
| 软删 / 恢复 / 版本 | 软删一条 trade → metrics `inputs_hash` 变；恢复 → 值回到原值；`include_deleted` 不影响指标 | 指标只消费 `deleted_at IS NULL` |
| ETag / 缓存 | 查询指纹含 `ids/domain/period/benchmark/rf/include_exploratory`；数据变更后 304 → 200；跨用户不命中 | 缓存键含 tenant/params |
| i18n | 18 个 explain 的 en 模板各 1 条裸术语扫描与数值一致断言 | 英文不泄露裸术语 |
| SDK / 契约 | `make sdk` 后 `git diff=0`；新增 `decomposition` 字段类型生成正确 | 契约单源 |
| API-038 `POST /diagnostics/selection` | 202+Location+type；drain 后 selection 指标可读；幂等重放同 job_id；同参异 key 复用/异参 409；PIT as_of 前移 BHAR 不变；跨租户 job 404、admin 403、read PAT 403；缺 Idempotency-Key 422；无买入事件→succeeded 但 n=0 不 500 | 删"不含更晚财报"断言，改"日线 PIT + data_as_of≤as_of"（D-04） |

### 8.8 隔离与越权

- 两租户同 fixture：A/B 各跑同 period，值分别等于各自独立期望；B 响应扫描 A 的哨兵 UUID/金额/证券名零命中（扩 `LIST_ISOLATION_MATRIX`）。
- 跨用户 metric/drilldown：`metric_id` 是全局注册表键（非租户资源），隔离断言为"响应内容不含他人数据 + B 无数据时 items=[]"；**不得**因此从越权矩阵剔除（`gate_tenant_coverage.py` 的 `EXEMPT` 写原因）。
- admin → 403 `ADMIN_CANNOT_READ_TRADES`（D-22）；无 token → 401；read PAT 可读、read PAT 调 API-038 → 403。
- RLS 直连：`metric_result`（若 05 建表）纳入 `information_schema` 策略测试；`conftest._REVIEW_TABLES` 补表名；A 变量只看到 A、不设变量 0 行、伪造成 B 的 INSERT 被拒。
- 门禁自动纳入：`gate_endpoint_coverage` **现有实现只扫描一期 30 端点且仅查 ≥1 引用**（`scripts/ci/gate_endpoint_coverage.py`），需扩展为 phase=2 端点集 + 每端点 ≥3 用例（C-27）；`gate_tenant_coverage`（每 operation 四矩阵）；`gate_rls_coverage`（新表）。

### 8.9 变异测试（D-16）

```toml
# pin mutmut>=3.2,<4（3.x 键名与 2.x 不同；以 mutmut 实际文档为准）
[tool.mutmut]
source_paths = ["backend/packages/core/src/stock_core/metrics", "backend/packages/core/src/stock_core/stats"]
pytest_add_cli_args_test_selection = ["backend/tests/unit/core", "backend/tests/golden"]
pytest_add_cli_args = ["-x", "-q", "-p", "no:cacheprovider"]
also_copy = ["backend/tests/fixtures/metrics"]
```
- 结果导出用 `mutmut export-cicd-stats`（`mutation.json`），由 `gate_mutation_score.py` 解析；若团队选 2.x，则退回 `paths_to_mutate/tests_dir/runner` 键——**实现首日锁定版本并写进 `pyproject.toml` 注释**。

- 门槛：新增 `stock_core/metrics/**`、`stats/**` **≥80%**；`ci.py`/`tolerance.py` ≥95%；现有 core 不退化；`make mutation`=diff-only（PR ≤10min）、`make mutation-full`=nightly（≤40min）；`gate_mutation_score.py`★ 读 `mutation.json`，<80% 或环比降>2pp → exit 1（脚本自带元测试）。
- 必杀算子（详见 §7 各指标）：`<`↔`<=`（pnl>0、z>1、BHAR>0）、符号翻转（DE、expectancy）、常量替换（365/244/1e8/20/60）、分母 n/n−1（Wilson/Gini/TE）、样本集合（data_error/open/pending）、二次扣费、blocked 当 0 收益。

### 8.10 性能与统计可信度

- **Pf-2 拆两级**：① 单 period 单指标 p95≤50ms（纯函数+单视图）；② 批量 20 指标×12 期优先命中 `metric_result` 物化，p95≤300ms 且**允许有 SQL**（读物化）；③ 冷启动/持仓重建/ bootstrap 最坏用例单独预算：10 年账、n=1000、连续 12 期 → ≤2s（超此则走 D-13 降级顺序）。测试输出分位与最慢指标。
- HHI/持仓重建：逐日流式聚合；`tracemalloc` 断言 750 交易日×200 标的峰值 <16MB、250 日 <8MB；超预算记 warning 并评审。
- 微基准：18 个纯函数中位数，基线 `perf/baseline/core.json`（审批 PR 更新），>2× 阻断 PR-full（Pf-10）；用 `time.perf_counter`，不新增 pytest-benchmark。
- 统计可信度：bootstrap 两次逐位相等；B=1000 下 CI 宽度随 n 单调不增（属性测试）；Wilson 与朴素二项近似差 <0.02（n=30）；CI 只在 n≥min_n(10) 出现。

### 8.11 门禁与执行计划

`make verify-p2` **由 Period2-14 建设**；07 只新增 `test-metrics`/`mutation` 并给出"verify-p2 需包含的指标增量步骤"（下表）。07 验收不要求 verify-p2 全绿（DEV-PLAN 把 verify-p2 归 P2-14）。
1. `pytest backend/tests/unit/core --cov=stock_core --cov-fail-under=100`（L0）
2. `pytest backend/tests/golden -m golden`（L1 三方）
3. `pytest backend/tests/contract/test_metric_plugin.py`（L2a）
4. `pytest backend/tests/api -k "api_033 or api_034 or api_035 or api_079"`（L3；API-038 依赖 D-17 的 handler，归 Period2-06 收尾（E-18），07a 不阻塞，P2-14 验收）
5. `pytest backend/tests -m isolation` + `gate_tenant_coverage` + `gate_rls_coverage`（L4）
6. `gate_endpoint_coverage.py`（端点 100% + ≥3 用例）
7. `api-check` + `api-snapshot --check`（契约漂移）
8. `gate_golden_approval.py`（MANIFEST sha256 + approved + version 一致）
9. `gate_mutation_score.py --min 80 --diff`（PR）
10. `pytest backend/tests/perf -m perf -k microbench`（Pf-10）
11. `pytest backend/tests/architecture` + `make lint` + `make typecheck`（ruff/import-linter/mypy strict/no-wall-clock/no-float）
12. 既有 `make test-p2` 全量（回归）+ 前端 `pnpm -C frontend/apps/web exec tsc --noEmit`（契约新增字段后 SDK 同步）

- golden 审批：口径负责人 + 1 reviewer 独立 PR 贴 diff（金额到分/比率到 bp）→ `golden-approved` label → `review.approved=true` 落 JSON；`definition_version` 未升而快照变 → fail；fixture 修改需 ≥2 reviewer + MANIFEST。
- flaky：golden/越权 **0 容差**；禁 skip/xfail/quarantine/sleep/rerun；nightly L1 连跑 3 次比对（三跑一致），不一致即 blocker。
- CI：`.github/workflows/p2-metrics.yml`★（PR-fast ≤5min 无 DB；PR-full ≤20min 全量；nightly mutation-full + Pf-2 + fixture integrity + L1 三跑）。
- 测试工作量：夹具 0.8 + 手算 golden 0.6 + oracle 0.6 + kit 0.3 + API 验收 0.5 + 隔离/门禁脚本 0.3 + 变异配置/killer 0.4 + 性能 0.2 + CI/verify-p2 0.2 = **3.9 人日**（含在 D-06 的 07a/07b 估时内；支持"3.5 人日不成立"的结论）。
---

## 9. 契约修订清单（已获人工批准 2026-10-26；实现阶段开工首日一并落地，本阶段不改文档）

> 原则：本任务只提出修订，不改 API-Catalog/PRD/契约。每项标注破坏性与落地任务。**owner/时点**：05/06/07 各自负责其范围内的条目，**开工首日**一次性提交契约 PR（文档 + `make api-snapshot` + `make sdk`），避免实现中反复改契约。

| # | 文件/位置 | 现状 | 建议改为 | 理由 | 破坏性 |
|---|---|---|---|---|---|
| C-01 | API-Catalog §2.3 API-033 | flat id 示例；未定义 insufficient 时 value 是否存在；`ci_min_n` 未写；`domain` 枚举缺 `perf`；ETag/304 未明写 | dotted id 示例（`perf.win_rate`…）；补 §4.2 状态-字段矩阵（D-08）；写 `ci_min_n=10`；**`domain` 枚举补 `perf`**（EE-P2-1；attribution 本期不收）；补 ETag 弱校验 `W/"<sha16>"` 与 304 说明（E-08/C-36） | D-05/D-08 一致性 | additive |
| C-02 | API-Catalog §2.3 API-034 | 裸术语清单缺 β/α/IR/HHI/Gini/Ulcer/Calmar/PGR/PLR | 扩充词表；非 ok 的 reason/what_to_do 枚举 | explain 可测 | 否 |
| C-03 | API-Catalog §2.3 API-035 | 断言②"每项金额 2 位" | item 增 `unit`；按 unit+precision 分格式（CNY 2/ratio 8/days 2/count 0/z 4） | 比率/天数型下钻 | 弱破坏（用例改） |
| C-04 | API-Catalog §2.3 API-038 | 断言③"不含晚于 as_of 的财报数据" | 改为"行情/事件不含晚于 as_of 的数据；`data_as_of≤as_of`；as_of 前移 BHAR 不变" | 本仓无财报（D-04） | 否 |
| C-05 | API-Catalog §4 `MetricResult` + `review.metric_result` 列 | 无 `decomposition` | 增加可选 `decomposition: object\|null`（值为定点串/整数），API-033/079 返回；05 的物化表加 `decomposition` JSONB 列并纳入 OpenAPI/SDK | 四数字卡需要 median/全样本/CR1/h=5/10（D-24） | additive |
| C-06 | API-Catalog §6 验收矩阵 | `nav_return` 不在 18 指标目录；`fx_round30` 名字与 n==12 矛盾；flat id | blocked 示例改用 `timing.drawdown`/`perf.fee_breakeven`；fixture 改 `fx_metrics_core`；id 改 dotted | 验收可执行（D-05/D-08） | 用例改 |
| C-07 | API-Catalog §1/API-032 | NavSeries summary 声明 `beta/alpha/t/IR`，但 `routers/nav.py` 本期不算 | 二选一：本期从 summary 删除这些字段，或实现时复用 `timing.beta` 的 core 函数（X-3/X-6） | 口径漂移 | 视选择 |
| C-08 | API-Catalog API-033/034/035/079/038 错误码 | 未列 `ADMIN_CANNOT_READ_TRADES` | 五个端点统一补 403（D-22） | D-22 | 否 |
| C-09 | PRD A10 | "并列全样本胜率"未定义浮盈亏数据源 | 补"期末逐标的未实现盈亏（市值−含费成本，不含已收股息）" | 可实现/可测 | 否 |
| C-10 | PRD A13 | "交易日计"未定义日历 | 补 `calendar=weekday_proxy`（M1）或真实日历归属 | D-09 | 否 |
| C-11 | PRD A14 | Gini/CR1 边界未定义 | 补"仅 pnl>0；n_pos<2 insufficient；n_pos<3 Top3 退化" | D-10 | 否 |
| C-12 | **PRD A15** | `DE = PLR − PGR`，与 FR-R6/Catalog"只卖盈不卖亏→DE>0"矛盾 | **勘误为 `DE = PGR − PLR`（Odean）**；保留"DE>0 为处置效应" | D-20 阻塞 behavior.de | 口径勘误 |
| C-13 | PRD A16 | "停牌>50%"无数据源；基准原始 vs β 调整未定 | 补停牌代理（窗口 bar 缺失>50%）；基准原始收益（β 调整仅 decomp） | 可执行 | 否 |
| C-14 | PRD A17/A23/A24 | 复权缺失（D-21）、窗口基准（bar_index）、rf 日化、PIT 定义、剔除阈值不完整 | 补 `adjust=none` + 除权嫌疑检测；`window_basis=bar_index`；rf 日化公式；PIT=price_only；剔除阈值（D-26） | 可实现/可测 | 否 |
| C-15 | PRD A22 | "吃掉 TWR 的 Y%"无公式；两个年化基数不一致 | 补 D-11 公式：`fee_drag=年化费用/期间日均持仓市值`；`twr_eaten=年化费用/(twr_amount+年化费用)` **仅当 TWR>0**；费用 365/D、TWR 365/(last−first) | 可实现/可解释 | 否 |
| C-16 | PRD A26 + FR-R5(405/414) + FR-R8(422) + PRD-v1(108/459) | "N<30 附 CI"与引擎 `ci_min_n=30` 冲突（5 处表述） | 统一为"n≥10 出 CI；n<10 无 CI/无 conclusion；日频<60 pending_window 无 value"；同步改 Extensibility §3.1 `ci_min_n=30→10` | D-08 | 口径统一 |
| C-17 | `contracts/constants.py:21-24` | 已有 `MIN_CI_SAMPLE=10`（与 PRD 一致）；缺 Z_95/MIN_TRADING_DAYS/RF 常量 | 保持 `MIN_CI_SAMPLE=10`；增 `Z_95=Decimal("1.959963985")`、`MIN_TRADING_DAYS=60`、`RF_ANNUAL_DEFAULT=Decimal("0.015")`；改 Extensibility §3.1 的 `ci_min_n=30→10` | D-07/D-08/D-14 | 内部 |
| C-18 | Extensibility §3.1 `SamplePolicy.ci` | 枚举仅 `wilson\|bootstrap` | 支持 `"none"`（A11/A13/A14/A21/A22/HHI/换手/摊平无 CI） | D-15 | additive |
| C-19 | Extensibility §3.1 `MetricValue` | 无 CI 样本字段与统计量 | 增内部 `ci_sample: tuple[tuple[int,...], ...]\|None`（观测=元组，E-15；排序后交接）+ `ci_stat_id`（指向 `core/stats/estimators/` 包内自动发现注册表）；引擎按 `sample_policy.ci + ci_stat_id` 派发（细化见 C-39） | D-07/E-15 | additive（05） |
| C-20 | Testing §9.1/§12.1 | golden 目录双源（`fixtures/expected/metrics/` vs `golden/<id>/`）；变异路径 `core/src/core` 与实际不符 | 单源 `backend/tests/golden/<metric_id>/`；路径改 `backend/packages/core/src/stock_core` | D-23/D-16 | 否 |
| C-21 | `core/values.py:193-195` | `mul_ratio` docstring 写"银行家舍入"，实现是 ROUND_HALF_UP | 以代码为准改 docstring | 口径单源 | 否 |
| C-22 | `review/repositories/rounds.py` | 日期过滤用 `COALESCE(close_date, open_date)` | 新增 `in_period(user_id,start,end,*,statuses)`；`page` 保持原语义（列表展示） | D-18 | additive |
| C-23 | DEV-PLAN §3.2 | Period2-07 估 3.5 人日、范围 18 指标 | 拆 07a/07b 并修正估时（§10）；注明 05/06 先决 | D-06 | 计划修订 |
| C-24 | `platform/config.py` | 无 `rf_annual` | 增 `rf_annual: Decimal = Decimal("0.015")`（env `RF_ANNUAL`） | D-14 | additive |
| C-25 | `Makefile` / CI | 无 `test-metrics`/`mutation`/`verify-p2`/workflow | 增 target 与 `.github/workflows/p2-metrics.yml` | 门禁可跑 | additive |
| C-26 | `docs/DATABASE.md` | 无 `metric_result/metric_dependency` | 由 05 迁移补充（07 不改 schema），含 RLS/`conftest._REVIEW_TABLES` | 台账 | 否 |
| C-27 | `scripts/ci/gate_endpoint_coverage.py` | 只扫描一期 30 端点、仅查 ≥1 引用 | 扩展 phase=2 端点集、每端点 ≥3 用例；`gate_tenant_coverage`/`gate_rls_coverage` 同步纳入新指标端点 | 门禁真实覆盖 07 端点 | 工具改 |
| C-28 | `stock_ledger/market.py::create_market_source` | 硬编码读 `quotes.json` | 增加 bars 文件配置（`MARKET_BARS_FILE`）与双源能力声明（monthly/daily），replay 按 interval 选择 | D-02 的日线夹具需要装配路径 | additive |
| C-29 | API-Catalog API-079 / §4 | API-079 无 §2.3 详细契约；未写 ETag/304、软删过滤、i18n（Accept-Language）、SDK 同步 | 补 API-079 详约（字段/错误码/断言），并把 `decomposition`/`ci` 字段变更纳入 `make sdk` 与 `openapi.json` 快照 | 契约完整性 | additive |
| C-30 | Extensibility §3.2 / Testing §8 U2 / §9.1 / §12.1 / `tests/unit/contracts/test_constants.py` | 文档写"Random 由 Clock 派生"（与 D-07 可复现冲突）、U2 用 `np.random.Generator`、fixture 布局命名、变异阻断级别、`test_constants.py:27` 断言旧常量 | 统一 seed=sha256(inputs_hash)；PRNG 用纯整数实现（禁 numpy 依赖）；golden 目录/命名按 §8.2；变异阻断级别=nightly 建 issue/release 阻断；同步常量测试 | 可复现/可测 | 文档+测试 |
| C-31 | Extensibility §3.3 / Architecture §11 / API-Catalog §0.8 | 三处对 stale/unreliable 的 value 语义不一致（Extensibility 旧值 vs Architecture 非 ok 无 value） | 统一为 Architecture 口径：`status≠ok` 一律无 value/conclusion；Extensibility §3.3 与 Catalog §0.8 补 stale/unreliable 说明 | D-08 唯一矩阵 | 契约澄清 |
| C-32 | PRD A18/A19/A21 + Testing §12.1 | A18 的 Ulcer/水下/持续/恢复、A19 空仓日 HHI、A21 独立费用行/红利税均无 PRD 修订项；Testing §12.1 仍写 `DE = PLR − PGR` | 补 A18/A19/A21 口径段落；Testing §12.1 同步改为 `DE = PGR − PLR`（D-20） | 口径单源/可测 | 文档 |
| C-33 | DEV-PLAN §3.1 验收剧本 | 用 `period=2026-09` 与 `make rebuild`/`make demo-broken` | 改为 `period=2026-06`（D-19 三轴），补 `make rebuild`/`demo-broken` 归属 | D-19 | 计划修订 |
| C-34 | `docs/architecture/v2/API-Catalog.md` §0.4/API-033 | 全局 `from/to` 半开 `[from,to)`，指标文档写闭区间 | 明确：`period=YYYY-MM` 闭区间；`from/to` 遵循 Catalog §0.4 **半开 `[from,to)`**，M1 仅接受"月初→次月初"（等价单月），跨月/单端 422 `INVALID_PERIOD`（EC-P1-2）；`deps/date` 统一说明 | 区间语义单源 | 契约澄清 |
| C-35 | API-Catalog API-033/079 | 顶层 `definition_version`/`assumptions` 聚合语义未定 | 顶层 `definition_version`（所有 item 一致取该值，混合→`"mixed"`）与顶层 `assumptions` 聚合（`benchmark_id/rf_rate/A/engine_version`）（E-14） | 顶层聚合可解释 | additive |
| C-36 | API-Catalog API-033/079 | GET 的 read-through 写副作用与负缓存未明写；ETag 强度未定义 | 明写 GET 的 read-through 写副作用 + 负缓存策略（E-01/E-11）；ETag 弱校验 `W/"<sha16>"`、304 判定在写库前（304 不写库） | 缓存语义单源 | 契约澄清 |
| C-37 | API-Catalog API-035 | item schema 未正式化 | item schema 正式化：`event_date/key`、`truncated`、cursor 绑定查询指纹；`DrilldownSpec.build_items` 契约（E-12）；items 按 unit 定点（C-03） | 下钻可实现/可测 | additive |
| C-38 | API-Catalog API-033 | 校验矩阵不全 | 补校验矩阵：ids 上限/去重/空、from/to 单月、`benchmark` 错误码（`UNKNOWN_BENCHMARK`）、`UNKNOWN_QUERY_PARAM`（E-09） | 校验可测 | 契约澄清 |
| C-39 | Extensibility §3.1 / 本文档 §4.1/§5.1/C-19 | `ci_sample` 单层、estimator 注册与 def 导出名未约定 | `ci_sample` 嵌套观测形状（E-15）+ `core/stats/estimators/` 自动发现注册表 + 07 def 统一导出 `DEF`（Extensibility §3.5） | 契约/实现同步 | additive |
| C-40 | DEV-PLAN | 05/06 估时 3d+2d | 改 6.5d+3.0d（E-17 已人工裁定接受推荐方案，合计 9.5d） | 计划修订 | 计划修订 |
| C-41 | DEV-PLAN 05 验收 / Extensibility §3.3 | "definition_version bump → 旧结果 100% stale" | 改"**新版本行插入 + 旧行保留 + `persist.is_stale` 单测**"；对外 `stale` 仅 `asset_build.state=stale`/`market.as_of` 滞后可达（E-13） | 口径统一 | 契约澄清 |
| C-42 | `Makefile`（并入 C-25） | 无 `test-engine` target | `make test-engine` 并入 C-25 的 target 清单；内含 `alembic downgrade 0006 && upgrade head` 幂等步骤 | 迁移可验 | additive |
| C-43 | `openapi_meta.py` / Catalog §1.1 | 4 个 metrics operationId 未登记 | 登记 4 个 metrics operationId 的示例/错误码/`x-error-codes`；operationId 命名与实现一致（snake_case）；`openapi.json` 生成后 `api-check` 通过 | 契约完整性 | additive |

---

## 10. 实施顺序与估时

> **结论**：18 指标 + 每指标 golden + 独立 oracle + mutation ≥80% 在 **3.5 人日不可达**。按域设计者实测估时汇总：perf 5.4d、timing 5.5d、behavior+selection 3.0d、测试设计 3.9d、共享 stats 1.5d（已含在域内；去重后与 S1~S9 的 13.5d 对齐）→ 去重后 **13~14.5 人日**（不含 05/06 前置 9.5d，E-17 已人工裁定接受推荐方案）。建议拆 07a/07b 并修正 DEV-PLAN（D-06/C-23）。

| 步骤 | 内容 | 产出 | 人日 | 依赖 |
|---|---|---|---|---|
| S0 | **前置**：05 引擎最小内核 + 06 端点（§4.3 P1~P5） | registry/context/CI/status/persist/`/metrics` | 9.5（05≈6.5+06≈3.0，E-17 已人工裁定接受推荐方案；含迁移/RLS/多视图/`ci_sample`+`ci_stat_id` 交接） | — |
| S1 | `core/stats` **07 份额**（`regression/concentration/drawdown/tstat` + estimator 文件；`numeric/wilson/bootstrap/estimators` 框架归 05，见 §5.1）+ 单测 | 纯函数 + 100% 覆盖 | 1.2（原 2.0，−0.8：S-3 交付边界调整） | — |
| S2 | 夹具：`daily_bars.json`（≥2 年）、`fx_metrics_core`（30 行）、约 23 个变体、loader/MANIFEST；demo 扩到 12 个月并修数据闭合 | fixtures | 1.8 | D-02/D-19 |
| S3 | `core/metrics` 纯公式（perf 7 + timing 4 + behavior 4 + selection 3） | 纯函数 + 单测 | 3.0 | S1 |
| S4 | `review/metrics/defs/*`（18 个文件）+ explain/drilldown/params/status_policy | 注册即发现 | 1.5 | S3 |
| S5 | golden（18 份，手算/oracle）+ 审批元数据 + 容差分级 | `tests/golden/<id>/` | 1.2 | S3/S8 |
| S6 | 独立 oracle（naive_metrics/stats/timing/bhar）+ 三方比对 | oracle + 参数化测试 | 1.0 | S3 |
| S7 | 契约 kit + API 验收（033/034/035/079/038，含越权/blocked/ETag/PIT） | `tests/contract`、`tests/api` | 1.2 | S0/S4 |
| S8 | mutation 配置 + 必杀算子 + 首轮跑通 + 门禁接线 | `make mutation` + CI | 0.8 | S1/S3 |
| S9 | 联调（持仓重建装配（def 内，E-06）/`in_period`/MetricDep 传播/`ci_sample`）、回归修复、实现日志 | 全绿 | 1.0 | 全部 |
| — | **07a 合计（核心 10 指标）= ΣS1..S9 − S10 ≈ 9.0~9.5** | | **9.0~9.5** | — |
| S10 | exploratory 8 指标（holding_days/concentration/expectancy/fly/turnover/averaging_down/hhi/bhar_hit）：defs+golden+oracle 增量（**拆分自 S2–S9，不重复计总量**） | 其余 defs+golden | 4.0~4.5 | S1~S9 |
| — | **07 合计（不含 05/06）= ΣS1..S9 = 13.5（取整 13.0~14.0）** | | **13.0~14.0** | — |

**若严格要求 3.5d**：只能交付 6 个指标（`perf.win_rate`/`perf.pnl_ratio`/`perf.fee`/`behavior.de`/`timing.drawdown`/`selection.bhar_20`）的公式+golden，**没有**独立 oracle、explain/drilldown、mutation 门禁——不满足 Period2-07 验收原文（D-06 备选①）。

---

## 11. 验收剧本（07 增量）

```bash
# 前置：`make rebuild` 与 `make demo-broken` 当前不存在，07 需新增（C-25）
make seed-demo                            # 扩展后：12 个月、工作日级行情、数据闭合、收尾自动重建 rounds/nav/assets
make rebuild                              # 新增：重建 rounds→nav→assets（CLI 子命令或 curl 序列）
make test-metrics                         # 新：core/stats+metrics 单测 + golden + oracle 三方比对
make mutation                             # 新：新增模块 mutation ≥80%
make verify-p2                            # 由 Period2-14 建设；07 交付其指标步骤（见 §8.11）
# 冒烟（05/06 就绪后）
curl "…/api/v1/metrics?ids=perf.win_rate,perf.pnl_ratio,perf.fee,behavior.de&period=2026-06" \
  | jq '.items[] | {id:.metric_id,n,sample_status,status,value,ci_low,ci_high}'
# 期望：win_rate n≥10、sample_status=ok、value 小数；blocked 指标无 value 键
curl "…/api/v1/metrics?domain=timing&period=2026-06" \
  | jq '[.items[] | select(.status=="blocked") | .metric_id]'
# 期望：live 无基准时 beta/fly 在 blocked 列表且无 value
```

附验收要点：① 五卡有数（D-19 后；**验收 period=2026-06**，行情延到 2026-09-30 供前向窗口；前端卡片请求必须带 `include_exploratory=true` 才能拿到行为卡所需的 HHI/换手/摊平；`core_conclusion` 只控制"行动建议"，不控制可见性）；② 点胜率下钻 → `/metrics/perf.win_rate/drilldown` items 非空且 trade/round 可查；③ `/explain` 无裸术语、非 ok 无 conclusion；④ `dev/tools` 无前端算数（Period2-15）。黄金审批与门禁见 §8.11。

---

## 12. 风险与缓解

| # | 风险 | 触发 | 后果 | 缓解 | 归属 |
|---|---|---|---|---|---|
| R1 | 与未实现的 05/06 契约漂移 | 05/06 设计与 §4 不一致 | 07 返工、defs 全改 | §4 冻结 + 评审确认；05/06 先合并 P1~P5 | D-01 |
| R2 | 数据不足导致"有数但不达标" | 月频 9 点 / 3 个月 demo | timing 3/4 指标 insufficient/pending，验收剧本失败 | D-02/D-19（日线夹具 + demo 扩长 + 数据闭合） | 07 |
| R3 | 期望值与实现互相自证 | golden 由被测代码生成 | 口径错误漏过 | 独立 oracle + 手算表 + 三方比对；golden 独立 PR | 测试设计 |
| R4 | 统计实现引入 float | Decimal 被转 float、numpy 依赖 | 精度/确定性 | 禁 float 检查（AST/lint）+ 契约 kit；输出标度整数 | 07 |
| R5 | bootstrap 不确定性 | seed 未固定/未进 inputs_hash | golden 抖动 | D-07：seed=sha256(inputs_hash)；双跑逐位相等 | 07 |
| R6 | 回撤/β 在 blocked 日被插值 | 为"凑满"序列 | 输出不可信收益 | 传播矩阵（§6.3）+ `assert_blocked_has_no_value` | 07 |
| R7 | 性能：HHI/持仓重建拖慢 `/metrics` | 10 年 1e4 流水 | p95 超 300ms | 内存重建预算 <200ms；Pf-2 点测；**若点测超预算，降级顺序**：① 单请求内缓存同 period 的持仓重建（一次重建供多个指标）→ ② 物化 `metric_result` 后只读缓存 → ③ 将 HHI/换手标 `unreliable` 并给出重算入口；禁止静默超时 | D-13 |
| R8 | 真实账单税费/送转导致口径偏差 | 分红/送转/配股边界 | 费用/集中度/回合 pnl 错 | 复用 FIFO 成本原语；fixture 覆盖送转/配股/股息；golden 覆盖 | 07 |
| R9 | **DE 方向/口径争议** | A15 与 FR-R6 矛盾 | behavior.de 语义反向、报告误导 | D-20 拍板 + PRD 勘误 + 冻结 golden | D-20 |
| R10 | **demo 数据不闭合** | 资金余额/持仓超卖 | 回撤 >100%、全表 degraded | D-19 修夹具；`abs(r)<=1` 域校验；drawdown golden 不建在 demo 上 | D-19 |
| R11 | 复权缺失导致收益绝对水平偏差 | 除权/分红未复权 | BHAR/追涨/卖飞系统性偏差 | D-21：`adjust=none` + 除权嫌疑检测 + assumptions 披露；后续 hfq 任务 | D-21 |
| R12 | 变异门禁误报/环境缺工具 | mutmut 未装/路径错 | CI 红 | D-16 固定版本 + wrapper；nightly 才跑全量；脚本元测试 | 07 |
| R13 | 估时再次失控 | 18 指标 × 测试 | 延期 | D-06 拆批；先 10 核心再 8 expl | 排期 |
| R14 | `/nav` summary 与 `timing.beta` 双写漂移 | 两处各算 beta | 同一用户两个数 | X-3/X-6 共用 core 函数 + 一致性测试 | 05/07 |

---

## 13. 人工决策事项（HITL 明细与追踪）

> §0 已给结论表；本节给 **A 类阻塞项的完整论证**，并把四份域设计稿提出的补充歧义逐条归并到已决编号（避免编号爆炸）。追踪列：`H-P*`=绩效稿、`H-T*`=择时稿、`H-B*`=行为/选股稿、`H-X*`=测试稿。

### 13.1 A 类（阻塞）详述

**D-01｜依赖：05/06 未实现**
- 事实：无 `review/metrics`、无 `metric_result` 迁移（止于 0006）、无 `/metrics` 路由；DEV-PLAN 明确 07 依赖 Period2-05。
- 推荐：两层交付（§4.3）；05/06 先补 9.5d（05≈6.5d+06≈3.0d，E-17 已人工裁定接受推荐方案）。备选：① 并入 07（+2.5~3d，边界模糊）；② 严格串行（周期最长）。
- 不决后果：07 无法单独完成 API 验收；强行并行开发引擎返工风险高。
- 追踪：H-X?（不适用）

**D-02｜日线夹具（择时/选股共同前置）**
- 事实：quotes.json = 4 序列×9 月频点（2026-01-30~09-30，174 工作日）；chase 20 条窗口覆盖率 **0/174 天**；每序列仅 8 个日收益（60 的 13.3%）；h=20 前向 0 天有 20 条 bar。
- 推荐：独立 `daily_bars.json`（≥2 年工作日，4 序列含 `hs300_tr`），不动 quotes.json。备选：① 换 quotes.json（nav/asset golden 全变，+2~3d）；② live-only（CI 不可复现）；③ 月频近似（违反 A16/A17/A23/A26）。
- 不决后果：3/4 择时指标只能 insufficient/pending_window，二期验收"卡片有数"失败。追踪：H-T1~H-T3、H-B11、H-X?。

**D-04｜选股域数据缺失**
- 事实：无财报/申万行业/PIT 财务表；API-038 断言"不含晚于 as_of 的财报数据"不可执行。
- 推荐：价格类 PIT cohort BHAR 20/60 + 修订断言；行业/财报另立任务。备选：① 引入数据源（+5~8d）；② 推迟 selection。
- 不决后果：坚持 PRD 原文则 07 无法验收 selection。追踪：H-B6、H-B11、H-X?。

**D-06｜范围与估时**
- 事实：域设计实测估时 perf 5.4d、timing 5.5d、behavior+selection 3.0d、测试 3.9d；去重后 13.0~14.0d（不含 05/06 的 9.5d，E-17 裁定）。
- 推荐：07a 核心 10（9.0~9.5d）+ 07b 8 个 exploratory（4.0~4.5d），合计 13.0~14.0d；修正 DEV-PLAN（C-23）。备选：① 3.5d 只做 6 指标（不满足验收）；② 18 全做（≈14.5d+）；③ 砍 oracle（违反 D2）。
- 不决后果：按 3.5d 排期必然延期或降级验收。追踪：H-P*（perf 估时 5.4d）、H-T5（timing 估时 5.5d）、H-B11（behavior 估值）、H-X13（test 估时 3.9d）。

**D-19｜演示数据与验收剧本**
- 事实：demo 3 个月（`months=3,count=120`）+ 月频 9 点；验收脚本用 `period=2026-09`，而流水止于 2026-06-30（`_ANCHOR`）→ n=0；且行情止于 2026-09-30，2026-09 的事件没有前向 20/60 bar → BHAR/卖飞/摊平全 pending。A26 要求日频 ≥60 交易日；生成账单随机卖出可超持仓→超卖→全表 degraded（复刻推演 MDD>100%）。
- 推荐：流水 12 个月止于 2026-06-30、日线行情延到 2026-09-30（前向缓冲）、验收 period=2026-06；修夹具闭合（无超卖、余额链自洽）、seed-demo 触发三重重算；确定性字节可复现。备选：① 保持现状改 DEV-PLAN 验收 period/卡片断言；② 只加长测试夹具（演示不可达）。
- 不决后果：二期验收第一条不过；drawdown 在 demo 上输出 >100% 回撤（误导）。追踪：timing 稿 §3.1 边际发现（demo 数据不闭合）、H-X?。

**D-20｜DE 方向（口径勘误）**
- 事实：PRD A15 写 `DE=PLR−PGR` 且"DE>0 为处置效应"；FR-R6/API-Catalog 断言"只卖盈不卖亏 → DE>0 且 CI 不含 0"。按 Odean 标准，只卖盈不卖亏 ⇒ PGR 高、PLR 低 ⇒ **PGR−PLR>0**。A15 的 `PLR−PGR` 会得到负值，自相矛盾。
- 推荐：采用 **Odean `DE = PGR − PLR`**，勘误 A15（C-12）；golden 用 fx_metrics_core → **+0.22222222**（PGR=8/9、PLR=4/6）。备选：① 按 A15 字面（FR-R6/Catalog 断言全部反向）；② 双字段并列（口径双写，报告难解释）。
- 不决后果：behavior.de golden 无法冻结；若实现期两处口径不一致，报告会自相矛盾。追踪：H-B1（笔数口径）、H-X1（DE 方向）。

**D-21｜复权/全收益口径缺失**
- 事实：A17 要求复权序列；NFR-2 要求 hfq；replay `supports_adjusted=False`、live `ADJUST=""`（v1 只不复权）。benchmark 是 hs300_tr（全收益），个股是不复权 → BHAR 系统性低估（股息率量级 ~2%/年）。
- 推荐：v1 不复权 + `assumptions.adjust="none"` + 除权嫌疑日检测（跳空超板块涨跌停→unreliable）；金标夹具窗口内无除权；hfq/因子版本化单列后续（ADR-0025 预留）。**偏差方向披露**：个股不复权而基准为全收益 → BHAR/超额/α 被股息率（约 −1.5~2%/年）系统性压低；β 的协方差受影响较小、IR 略低估。备选：① 本期实现 hfq（+2~3d，依赖 Period2-04 因子表）；② 后复权近似（与账单现金流口径冲突，易双计）。
- 不决后果：BHAR/追涨/卖飞绝对水平偏差未披露；若验收要求"与 oracle 一致"则夹具可兜底，但真实账户数字偏保守。追踪：H-T4、H-B7、H-X?。

### 13.2 B 类（口径）与 C 类（工程）的补充项归并

| 补充歧义 | 来源 | 归并/裁决 |
|---|---|---|
| 回合归属日（close_date vs open_date）、`page()` COALESCE | H-P1 / H-X12 | **D-18**（推荐 close_date；新增 `in_period`） |
| 全样本浮盈亏数据源（positions_end） | H-P2 | **D-13 + C-09**（内存重建；主 value 不依赖） |
| 独立费用行入账、成交额毛/净额 | H-P3/H-P4 | **D-27**（含独立行；毛额为主，净额并列） |
| A22 两个年化基数 + Y% 量纲 | H-P5 | **D-11**（费用 365/D、TWR 365/(last−first)、TWR 金额化） |
| A12 零回合偏差（p 按 A10） | H-P6 | **采纳为设计内**：照 A12 原文，decomp `mean_pnl` 暴露差异 |
| 结构性无定义（value null vs 无键） | H-P7 | **D-08**（`value:null` + insufficient） |
| days 允许小数、按 precision 分级 | H-P8 / H-X? | **D-15**（days=2 位；下钻按 unit+precision） |
| CI 交接（MetricValue 无 CI 字段） | H-B1 / H-X2 | **D-07 + C-19**（`ci_sample`（排序）+`ci_stat_id` 内部字段；引擎按统计量派发） |
| bootstrap 抽样单元（处置候选集） | H-B2 | **采纳为设计内**（§7.3.1：G/L/g/l 联合重抽；BHAR 按事件） |
| `SamplePolicy.ci` 需 "none" | H-B3 / H-P? | **D-15 + C-18** |
| 摊平定义 | H-B4 | **D-25**（持仓>0 的 buy、严格低于含费均价、深度公式） |
| 换手率定义 | H-B5 | **D-25**（双边成交额/逐日平均持仓市值×244/交易日） |
| BHAR 事件日基准价、停牌/退市阈值 | H-B6 / H-T? | **D-26**（当日收盘、覆盖率<50%/尾部 5 日） |
| 重叠窗聚类（Newey-West） | H-B7 | **D-26**（主口径事件日聚类；NW 仅 decomp，M1 不做） |
| "期末浮盈笔数"粒度（证券 vs lot） | H-B8 | **采纳为设计内**：按证券持仓 1 笔；open 部分卖出只进 decomp |
| HHI 粒度/空仓/覆盖率阈值 | H-B9 | **采纳为设计内**：自然日；空仓不进 eq 均值；<50% blocked/<90% unreliable |
| cohort 事件范围（buy/rights/new_stock/bonus） | H-B10 | **D-26**（buy 为主；rights/new_stock 仅 decomp；bonus 不计） |
| 窗口基准 bar_index + σ ddof | H-T5 | **D-28**（bar_index；ddof=1；密度<0.8 unreliable） |
| A16 基准原始 vs β 调整、加权 | H-T6 | **采纳为设计内**（原始、不加权；β 调整仅 decomp 预留） |
| rf 日化与年化基数 | H-T7 | **D-14**（rf_d=(1+rf_a)^(1/244)−1；α=α_d×244；Calmar 用 A5） |
| 组合日收益口径 / n 计数 | H-T8 | **采纳为设计内**（asset_daily.daily_return；n=非 NULL 且工作日） |
| A18 未定义项（水下分母/持续/恢复/Ulcer/Calmar） | H-T9 / H-B? | **采纳为设计内**（§5.6；右截尾 null+flag；<60 pending 无值） |
| golden 目录双源 / 容差分级 | H-X3 | **D-23 + C-20**（单源 `tests/golden/`；1bp/1e-8+0.5%/0.01/0.01） |
| Catalog 修订 7 处（flat id、035 格式、034 items、038 财报、079 ETag 等） | H-X4 / H-T? | **C-01~C-06** |
| n=0 是 blocked 还是 insufficient | H-X5 | **D-08**（insufficient；`value:null`；无 value 键仅 blocked/pending） |
| 平均持仓市值口径（期初/期末 vs 逐日） | H-X6 / H-B5 | **D-25**（逐日平均） |
| 红利税归属（重复计） | H-X7 | **D-27**（账单只给税后净额；若毛+税行则 engine 冲减；fixture 不放税行） |
| data_error 是否进分母 | H-X8 / H-P? | **采纳为设计内**：一律排除并单列计数（A8 先例） |
| period 语义（close vs trade vs 收益区间） | H-X9 | **D-18** |
| 变异门禁路径/版本 | H-X10 | **D-16 + C-20** |
| admin 指标端点 403 | H-X11 | **D-22 + C-08** |
| `fx_*` fixture 未定义 | H-X? | 本设计 §8.3 即规范 |
| `/nav` summary 与 timing.beta 双写 | H-T1（timing 稿 H-19） | **C-07 + X-3/X-6** |
| live 无基准时 timing 整体 blocked | H-T9（timing 稿 H-27） | **D-03** |

### 13.3 需要人工确认的最小集合（建议逐项勾选）

| 编号 | 决策 | 推荐 |
|---|---|---|
| D-01 | 05/06 先补 9.5d（E-17 已裁定），07 两层交付 | ☐ 同意 ☐ 改：______ |
| D-02 | 新增独立日线夹具，不动 quotes.json | ☐ 同意 ☐ 改：______ |
| D-04 | selection 只做价格类 PIT BHAR + 改断言 | ☐ 同意 ☐ 改：______ |
| D-06 | 07 拆 07a（9.0~9.5d）/07b（4.0~4.5d），合计 13.0~14.0d；修正 DEV-PLAN | ☐ 同意 ☐ 改：______ |
| D-19 | 三轴对齐（流水 12 个月止于 2026-06-30、行情延到 2026-09-30、验收 period=2026-06）+ 修数据闭合 | ☐ 同意 ☐ 改：______ |
| D-20 | DE 采用 Odean `PGR−PLR`，勘误 A15 | ☐ 同意 ☐ 改：______ |
| D-21 | v1 不复权 + assumptions 披露 + 后续 hfq | ☐ 同意 ☐ 改：______ |
| D-08 | **四态门槛（非 ok 无 value，取代 Extensibility §3.3 旧值口径 → 前端 stale 不再渲染旧值；破坏性）** + pending_window 无 value | ☐ 同意 ☐ 改：______ |
| D-07 | `MetricValue.ci_sample`（排序/不落库）+ `ci_stat_id` + seed=sha256(inputs_hash)；**B 取 1000 还是 PRD 的 10000（成本×10）** | ☐ 1000 ☐ 10000 ☐ 改：______ |
| D-24 | `MetricResult` 增 `decomposition` | ☐ 同意 ☐ 改：______ |
| D-22 | admin 读指标 → 403 | ☐ 同意 ☐ 改：______ |
| D-25/26/27 | 摊平/换手/BHAR 剔除/费用细则 | ☐ 同意 ☐ 改：______ |
| D-03 | live 缺基准 → timing/selection 域 blocked；指数适配另立 | ☐ 同意 ☐ 改：______ |
| D-05 | dotted id + 全量 definition_version=1.0 | ☐ 同意 ☐ 改：______ |
| D-09 | 交易日历沿用工作日近似（assumptions 披露） | ☐ 同意 ☐ 改：______ |
| D-10 | 集中度只在盈利回合上定义（Gini/CR1/n_pos 边界） | ☐ 同意 ☐ 改：______ |
| D-11 | 费用回本门槛主 value + Y% exploratory | ☐ 同意 ☐ 改：______ |
| D-12 | 回撤族用 asset_daily.daily_return 复利链 | ☐ 同意 ☐ 改：______ |
| D-13 | 持仓重建分 core 纯函数 + review 装配器；不落库 | ☐ 同意 ☐ 改：______ |
| D-14 | rf_annual 默认 0.015、日化公式 | ☐ 同意 ☐ 改：______ |
| D-15 | 下钻按 unit+precision 分格式；`ci="none"` | ☐ 同意 ☐ 改：______ |
| D-16 | mutmut 3.2+，新增模块 ≥80% | ☐ 同意 ☐ 改：______ |
| D-17 | API-038 handler **归 Period2-06 收尾（E-18）**，07a 不阻塞 | ☐ 同意 ☐ 改：______ |
| D-18 | 归属日 close_date/trade_date + lookback 进 inputs_hash | ☐ 同意 ☐ 改：______ |
| D-23 | golden 单源 `tests/golden/<id>/` + 容差分级 | ☐ 同意 ☐ 改：______ |
| D-28 | bar_index + 密度<0.8→unreliable | ☐ 同意 ☐ 改：______ |
| D-29 | 契约修订总包 C-01~C-43（开工首日一次性提交；C-35~C-43 来自 05/06 评审 v1.1） | ☑ 批准 |

### 13.4 人工决策记录（2026-10-26，已拍板）

> 以下为人工在评审后对关键决策的最终裁定（**全部采用推荐方案**）；实现阶段以本节为准，评审文档 §0/§13 的"待拍板"表述自动失效。

| 决策 | 裁定 | 备注 |
|---|---|---|
| D-01/D-06 范围与排期 | **采纳推荐**：05/06 先补 9.5d（05≈6.5d+06≈3.0d；原批 5~7d 由 05/06 评审 E-17 人工裁定上调，v1.1 同步）；07 拆 07a 核心 10（9.0~9.5d）+ 07b exploratory 8（4.0~4.5d），合计 13.0~14.0d；修正 DEV-PLAN 估时 | 需同步 C-23/C-33/C-40 |
| D-02/D-19 数据 | **采纳推荐**：新增独立日线夹具（≥2 年、含 `hs300_tr`，不动 `quotes.json`）；演示数据三轴对齐（流水 12 个月止于 2026-06-30、行情延到 2026-09-30、验收 `period=2026-06`）+ 修数据闭合 | C-28/C-33 |
| D-20 DE 方向 | **采纳推荐**：`DE = PGR − PLR`（Odean），勘误 PRD A15；`definition_version=1.0` 按此冻结 | C-12/C-32 |
| D-03/D-04 选股域 | **采纳推荐**：本期只做价格类 PIT cohort BHAR 20/60；live 缺基准 → `blocked`；财报/行业/风格另立任务；修订 API-038 财报断言 | C-04/C-07 |
| D-07/D-14 统计参数 | **采纳推荐**：B=1000（percentile）、`seed=sha256(inputs_hash)`、`ci_stat_id`；`rf_annual=0.015` | PRD-v1 的 10000 在 C-32 修订 |
| D-29 契约修订 | **批准 C-01~C-34**，由 05/06/07 开工首日一次性提交契约 PR（api-snapshot/api-check/sdk 同步） | §9 |

> 其余决策（D-05/08/09/10/11/12/13/15/16/17/18/21/22/23/24/25/26/27/28）均按 §0/§13.3 的推荐方案随上述 6 项一并生效。实现前若有改判，须在本文档新增"变更记录"并升 `definition_version`。

---

## 14. 附录

### 14.1 术语

| 缩写 | 含义 | 出处 |
|---|---|---|
| PGR/PLR | 盈利兑现比例 / 亏损兑现比例（笔数口径） | A15（Odean 1998） |
| DE | 处置效应 = PGR − PLR（D-20） | A15（勘误） |
| BHAR | 买入持有异常收益 | A24 |
| CR1/Gini/HHI | 集中度/基尼/赫芬达尔指数 | A14/A19 |
| MDD/Ulcer/Calmar | 最大回撤/溃疡指数/卡玛比率 | A18 |
| IR/TE | 信息比率/跟踪误差 | A23 |
| PIT | 时点一致（as_of 门禁） | NFR-3 |
| golden | 冻结的期望值快照（独立 PR + 审批） | Testing §12.3 |
| TWR/MWR | 时间加权/资金加权收益率 | A5/A6 |

### 14.2 引用

- `docs/DEV-PLAN-v2.md` §3.2（Period2-07）、§3.1（二期验收剧本）、§3.4（风险）
- `docs/prd/PRD-v2.md` 附录 A1–A27、FR-R4/R5/R6/R8；`docs/prd/PRD-v1.md` FR-R4 §4.3、FR-R6 §6.1
- `docs/Architecture-v2.md` §11（指标执行引擎）、§9.2/§9.3、§10.4
- `docs/architecture/v2/Extensibility.md` §3.1/§3.2/§3.3、§11.1/§12
- `docs/architecture/v2/API-Catalog.md` API-033/034/035/036/038/079、§0.8、§4、§6
- `docs/architecture/v2/Testing.md` §9.3、§12.1、§12.3、§10.4
- `docs/architecture/2026-10-25-asset-daily-table.md`（blocked/degraded 与每日资产口径）
- 代码：`stock_core/{values,nav}.py`、`stock_core/fifo/*`、`stock_review/{models,application/*,repositories/rounds.py}`、`stock_platform/{ports,market_live,config}.py`、`stock_ledger/{fixtures,models}.py`、`stock_api/routers/{nav,assets,rounds}.py`
- 内部工作稿（评审后可删）：`docs/architecture/_p207/{brief,part_perf,part_timing,part_behavior_selection,part_test}.md`

### 14.3 评审与变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.9 | 2026-10-26 | Lead 主稿 + 4 份域设计稿合并；18 指标 spec、测试设计、29 项决策、契约修订清单 |
| **v1.0-rc** | 2026-10-26 | 三方交叉评审（13 P0/24 P1）修复：状态-字段四态矩阵、CI 门槛 n≥10、OLS 公式、主夹具去超卖、D-19 三轴对齐、估时/core 计数、契约 C-01~C-34；新增第二轮验证 |
| **v1.0** | 2026-10-26 | 第二轮验证 APPROVED_WITH_FIXES：修完 V-P1-1~9（状态矩阵残留/CI 常量/expectancy 中间值/估时加平/ci_stat_id/kit、D-11 去绝对值、C 清单重排、C-33/C-34 立项、状态头）；**人工决策批准（全部推荐方案）→ 定稿**，见 §13.4 |
| **v1.1** | 2026-10-26 | 05/06 设计评审（v1.1）回写：S-1~S-10 同步（ci_sample 成对观测、positions_end 内部重建、stats 交付边界、DEF 导出/build_items、API-038 归 06、估时 9.5d、stale 语义收口、perf 枚举与 ETag、from/to 半开）；评审记录 docs/reviews/2026-10-26-DevDesign-Review-metrics-engine-endpoints.md |
| v1.0 | 待定 | 三方交叉评审意见收敛 + 人工拍板后定稿（评审记录 `docs/reviews/2026-10-26-DevDesign-Review-metrics-three-domain.md`） |
