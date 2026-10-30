# Dashboard 页面（资产概览 + 历史概览）开发设计、验收剧本与入口预留

> 状态：**v0.2 — 已完成交叉评审并关闭 P0（3 名独立评审，记录见 [交叉评审](../reviews/2026-10-28-DevDesign-Review-dashboard.md)）；待人工拍板 A 类口径；本阶段只出文档，不写生产代码、不改 API/数据库**
> 日期：2026-10-28 ｜ 作者：AI（Team Lead）
> 需求来源：用户本轮诉求（Dashboard 两板块 + 未完成验收入口预留/缺失清单）；`docs/DEV-PLAN-v2.md` §3.1/Period2-12；`docs/prd/PRD-v2.md` FR-D1；`docs/architecture/v2/API-Catalog.md` API-027/029/032/033/081/083；`docs/architecture/v2/Testing.md` §7。
> 事实基线（2026-10-28）：
> - 前端**没有** `Dashboard` 页面；`/` 目前重定向 `/trades`（`frontend/apps/web/src/router.tsx`）。
> - 后端已有 `GET /nav`、`GET /metrics`、`GET /rounds`、`GET /reconciliation/bridge`、`GET /assets`、`POST /jobs/rebuild-period`（API-083）；**没有** Dashboard 聚合端点。
> - Period2-08（归因）、Period2-09（月报）、Period2-10（导出）、Period2-11（设置）、Period2-13（报告/导出页）、Period2-14/15（验收/零口径门禁）未完成；`make rebuild`、`make demo-broken`、`make verify-p2` 不存在。
> - 因此本设计同时交付：**Dashboard 页面 + 验收剧本修订 + 未完成功能的前端入口预留 + 缺失内容清单**；剩余二期任务不实现。

---

## 0. 决策摘要（人工评审请重点拍板）

> **A 类 = 不拍板无法定稿 Dashboard 数字/验收**；**B 类 = 实现方式/范围，可按推荐先做**；**C 类 = 可委托 Lead**。明细见 §13。

人工审核：zhang 按推荐实现。

### A. 口径/范围决策

| # | 决策点 | 推荐方案 | 备选 | 影响 |
|---|---|---|---|---|
| **D-01** | **"总投入最大成本"** 的定义（用户原词有歧义） | **历史峰值持仓成本**：`max(asset_daily.holding_cost_units)`（逐日持仓成本的最大值，不含现金）；字段名 `max_invested_cost`，文案"历史峰值持仓成本" | ① 当前持仓成本合计；② 累计买入成本（Σ 所有买入成本，含已卖出）；③ 历史峰值总资产 | 直接决定"最大成本收益率"分母；选错会给出无意义百分比 |
| **D-02** | **"总收益额"** 的口径 | **全历史累计总收益**（含已实现+浮动+股息+利息−费用，含当前持仓市值）：`total_profit = latest_total_assets − initial_cash − initial_open_cost − cashflows − other`；`bridge` 的 `period_end = A`（最新资产日）、`period_start = 首笔流水日`；`|residual_units| > B.tolerance_units` 或 `B.status != ok` → `degraded/missing`（不硬算） | ① 仅已实现回合 pnl；② 仅当月/当年收益；③ TWR 折算金额 | 与"总投入最大成本"共同决定收益率；`other`（无份额打新/补差）语义需人工确认；有份额打新缴款已进 lot 成本，不进 `other` |
| **D-03** | **"最大收益额与最大成本的比率"** | **最大单笔已平仓收益额 ÷ 历史峰值持仓成本**（与 D-01 一致），字段 `max_profit_cost_pct`；另在 tooltip 给 `max_profit_round_return_pct`（该笔收益率）作辅助，不混用 | ① 最大单笔收益 ÷ 该笔自身成本（=最大单笔收益率）；② 最大单笔收益 ÷ 当前持仓成本；③ 最大单笔收益 ÷ 累计买入成本 | 用户原句"最大成本"未说明是"该笔成本"还是"总投入峰值"；需人工确认 |
| **D-04** | **"已平仓股票数量"** | **distinct security_code（已平仓且非 data_error）** 为 headline；同时返回 `closed_round_count`（平仓笔数）供副标题，避免"股票数/笔数"混淆 | 只按平仓笔数计（更接近 win_rate 的 n） | 影响文案与验收断言；两个口径同时展示最稳 |
| **D-05** | **最大回撤的数据范围与异常行** | 用全历史 `asset_daily` **有效日收益**（`status=ok/degraded`）复利链；仅 blocked/NULL 行断链，degraded（新股按成本、债券缺行情）日收益有效不断链、不进 missing；有效观测 `<60` 交易日 → `insufficient`（不冒充"历史回撤"）；完全无有效段 → `blocked`。全历史无回撤 → `mdd=0` + 起止 null | ① 任一 blocked 整块不出值（会让有单日断链的用户永久看不到历史）；② 跳过 blocked 继续算（失真）；③ 仅用当前月 `timing.drawdown`（不是"历史"） | 影响历史概览可用性；演示数据若历史有断链会降级而非整块消失；需人工确认 60 日门槛 |
| **D-06** | **旧"五卡"验收如何合并到两板块** | **保留旧内容为两板块内的子块**：净值 summary + 费用 + **对账横幅**并入"资产概览"；四数字（胜率/盈亏比/持仓天数/集中度，带 n/CI/可信度）+ 行为 + 最赚最亏 + **下钻入口**并入"历史概览"；页面仍只有 2 个大板块 | ① 直接删旧五卡（PRD/验收缺项）；② 保留旧五卡再加两板块（页面过载，违背2板块） | 下钻用 `/metrics/{id}/explain` 详情页替代"抽屉"，需同步修订验收；对账/可信度标签保留 |
| **D-07** | 未完成功能的**入口预留**方式 | 新增 `/reports`、`/exports`、`/attribution` **路由页占位**（`ComingSoon`）；Dashboard 顶部/历史区放按钮进入占位页；**不渲染任何假数字/假按钮成功**；占位页标注依赖任务 | ① 仅 disabled 按钮（不可发现）；② 隐藏入口（验收无法证明预留）；③ 先做假页面（误导） | 用户明确要求"先进行前端入口预留"；必须可测试 |
| **D-08** | Dashboard 数据获取方式 | **新增 `GET /api/v1/dashboard`（API-084）聚合请求字段 + 当前持仓 + 最赚最亏**；旧子块补充指标仍由现有 `/metrics` 获取（前端只展示） | ① 前端拼 `/nav`+`/assets`+`/rounds`+`/metrics`（计算/一致性在前端，违反零口径）；② 扩展 `/assets`（语义混杂） | 新端点让 §1.3 的 15 项字段有唯一来源；旧指标保持现有 API |
| **D-09** | 当前快照取哪天/是否自动构建 | **只读最新 `asset_daily` 行**（`trade_date ≤ today`），不自动触发构建；**API-084 自身不写任何表**；旧子块按需懒加载，`/nav`/`/metrics` 的既有 GET 物化写不隐瞒；`asset_build.state` 为 `stale/missing/blocked` 时显示状态与"去构建"链接（跳 `/assets`，不自动 job） | ① Dashboard 自动触发 rebuild（有副作用/慢）；② 用实时期末估值（与资产表可能分叉）；③ 首屏同时调 `/nav`+`/metrics`（触发写） | 首屏只调 API-084；数据可能滞后但显式披露；既有 GET 写副作用另行标注 |
| **D-10** | 图表/可视化依赖 | v1 **不新增图表库**；用 CSS 进度条/文字/简单内联 SVG；ECharts 留到后续（PRD 有要求但当前未依赖） | ① 引入 ECharts（体积/测试成本）；② 不做任何可视化 | 影响"净值曲线"旧卡；可先放占位/后续补 |
| **D-11** | 旧五卡用哪个 period | **2026-10-30 修订：指标子块改为年度口径**——API-084 返回 `metrics_period`（`as_of` 所在自然年，如 `2026`）、`metrics_period_start`（1/1）、`metrics_period_end`（=as_of），前端按此自动请求 `/metrics?period={metrics_period}`（低频操作者月度样本不足）；`legacy_period`（最近有流水/回合的月份）保留给归因占位链接；全历史 headline 标注"全历史" | ① 默认当月（demo 全空）；② 用户自选；③ 不显示旧五卡 | 解决同页三套时间口径；history headline 与指标子块（年度）明确分开 |
| **D-12** | 状态分类法 | **拆两轴**：`build_state`（missing/current/stale/blocked，来自 `asset_build_state`）与 `data_status`（ok/degraded/blocked/insufficient/missing，来自行/指标）；页面状态优先级 `blocked > missing > insufficient > stale > degraded > ok` | 单轴混用（现状评审发现矛盾） | 前后端/测试按两轴映射；避免 stale 与 blocked 冲突 |
| **D-13** | 缺失清单口径 | 每项给"是否阻塞本任务验收 / 本次是否修 / SKIP 依据 / 占位入口"；G8 红灯、D-19、前端工具链/夹具缺口全部列入 | 只列 API/页面 | 验收可逐项核对，不把"暂停"误判为完成 |

### B. 实现/范围决策（推荐先按此）

| # | 决策点 | 推荐 |
|---|---|---|
| B-01 | API ID | 新增 **API-084 `GET /dashboard`**；更新 API-Catalog §1/§6、OpenAPI、SDK、门禁计数（83→84） |
| B-02 | Dashboard 端点返回 | 请求字段 + 当前持仓 + 最赚/最亏 + 状态/缺失项；**不做**全量 metrics 内嵌；旧子块由 `/metrics` 补 |
| B-03 | 页面路由 | `/dashboard`；根路径 `/` 重定向 `/dashboard`；导航新增"看板"（与"净值/资产/指标"并列） |
| B-04 | 页面结构 | 两个 `<section>`：`asset-overview`、`history-overview`；旧五卡以子块合并（见 §2） |
| B-05 | 状态表达 | 字段 `null` + `missing[]` + 状态徽标；不输出 0 冒充缺失；**blocked/missing 不渲染数字，stale 可显示并提示** |
| B-06 | 前端零口径 | 所有金额/比例/百分比由后端格式化；前端只排序/展示/样式；不 `Number()/toFixed()` 指标值 |
| B-07 | 占位页 | `ComingSoon` 组件 + `/reports`、`/exports`、`/attribution`；`FeaturePlaceholder` 带 `data-testid`/依赖任务/返回链接 |
| B-08 | 验收剧本 | 修订 §3.1：先跑 Dashboard 两板块；未完成项改为"入口存在 + 占位可见 + 无假数字"，并单独列缺失清单（§7） |
| B-09 | 测试 | 后端 API-084 契约/隔离/状态；前端 Vitest 页面/占位/零口径静态断言；独立验收脚本 `scripts/acceptance/dashboard.sh` 或 pytest |

### C. 工程细节

| # | 决策 | 推荐 |
|---|---|---|
| C-01 | 当前持仓 | 用 `asset_daily` 最新行 + `review` 现有估值原语重建；前后端都不重复实现估值 |
| C-02 | 历史最大回撤 | 新增 `core/stats/drawdown.py::max_drawdown_window` 纯函数（返回 mdd/峰/谷/持续），复用已实现回撤族；不在 API 层重算 |
| C-03 | 分页/性能 | Dashboard 无分页；持仓表最多返回全部持仓（个人规模；>200 行截断并提示）；查询 ≤300ms 目标 |
| C-04 | 缓存 | 不做服务端缓存；前端沿用现有 `useState/useEffect` 页面模式（仓库未引入 TanStack Query）；提供手动刷新 |
| C-05 | 审计 | 只读端点，不写审计；不触发 job |

---

## 1. 背景与目标

### 1.1 现状

- 前端已有交易流水、回合、净值、资产、指标、导入、设置（PAT）页面；**没有 Dashboard**，`/` → `/trades`。
- 后端已有完整回合计/净值/指标/资产/桥接能力，但新的概览字段（§1.3 的 A1~A7/H1~H8）没有聚合端点；前端不能自行计算（零口径）。
- 二期验收剧本 §3.1 仍按"五卡"描述；`make rebuild`/`make demo-broken`/`make verify-p2` 与报告/导出/设置/零口径门禁未完成。

### 1.2 目标

1. 交付 `/dashboard` 两板块：
   - **资产概览**：当前持仓及市值、资产余额（现金 + 货币基金）、仓位百分比、总收益额、总投入最大成本、最大成本收益率、总费用消耗。
   - **历史概览**：已平仓股票数量、平仓盈利/亏损数量及占比、历史最大收益额、最大收益额与最大成本比率、最大回撤时间段、回撤收益率。
2. 与既有验收（五卡/PRD FR-D1）**合并而非丢失**：旧内容按 §2 映射为两板块子块；验收脚本改为两板块断言。
3. 未完成 P2 功能：**前端入口预留**（可发现、可测试、无假数据），并在文档列出缺失内容清单（§8）。
4. 保持前端零口径、**blocked/missing 不显示数字、stale 显示并提示**、API 契约先行、多租户隔离。

### 1.3 用户 15 字段 → API 路径追踪表（评审 P0-3 修订）

> 用户口径按语义归并如下（"当前持仓及市值"拆为 list + 市值 headline；"盈利/亏损数量分别占比"拆为计数与占比；"最大回撤时间段/回撤收益率"归入一个对象）。**这是实现与验收的 1:1 对照表**，DASH-01 逐行断言。

| # | 用户字段 | API 路径 | 中文文案 | `data-testid` | 窗口/来源 |
|---|---|---|---|---|---|
| A1 | 当前持仓 | `asset_overview.holdings[]` | 当前持仓 | `holding-row-*` | 最新 `asset_daily` + 持仓重建 |
| A2 | 持仓市值 | `asset_overview.market_value` | 持仓市值 | `kpi-market-value` | 最新资产日 |
| A3 | 资产余额 | `asset_overview.balance` | 资产余额（现金+货币基金） | `kpi-balance` | 最新资产日 |
| A4 | 仓位百分比 | `asset_overview.position_pct` | 仓位 | `kpi-position` | `market_value/total_assets` |
| A5 | 总收益额 | `asset_overview.total_profit` | 总收益 | `kpi-total-profit` | 全历史（D-02） |
| A6 | 总投入最大成本 | `asset_overview.max_invested_cost` | 历史峰值持仓成本 | `kpi-max-invested-cost` | 全历史（D-01） |
| A7 | 最大成本收益率 | `asset_overview.max_cost_return_pct` | 峰值成本收益率 | `kpi-max-cost-return` | A5/A6 |
| H1 | 已平仓股票数量 | `history_overview.closed_security_count` | 已平仓股票数 | `kpi-closed-securities` | 全历史 closed rounds distinct code |
| H2 | 盈利数量 | `history_overview.win_count` | 盈利笔数 | `kpi-win-count` | 全历史 closed rounds |
| H3 | 亏损数量 | `history_overview.loss_count` | 亏损笔数 | `kpi-loss-count` | 全历史 closed rounds |
| H4 | 盈利占比 | `history_overview.win_pct` | 盈利占比 | `kpi-win-pct` | H2/closed_round_count |
| H5 | 亏损占比 | `history_overview.loss_pct` | 亏损占比 | `kpi-loss-pct` | H3/closed_round_count |
| H6 | 历史最大收益额 | `history_overview.max_profit` | 历史最大单笔收益 | `kpi-max-profit` | 全历史 closed rounds max pnl |
| H7 | 最大收益额/最大成本比率 | `history_overview.max_profit_cost_pct` | 最大收益/峰值成本 | `kpi-max-profit-cost` | H6/A6（D-03） |
| H8 | 最大回撤时间段 + 回撤收益率 | `history_overview.max_drawdown.{from,to,duration_days,mdd_pct}` | 最大回撤区间/幅度 | `kpi-max-drawdown` | 全历史 asset_daily 日收益（D-05） |

**额外展示（不计入 15 项，但页面/旧验收需要）**：`total_assets`、`closed_round_count`、`flat_count/win_bar_pct...`、`max_profit_round`、`top_rounds/bottom_rounds`、`recovery_days/censored`、`legacy_period`、`missing[]`、`status/data_as_of`。这些字段的产生和验收单独列在 §7/§8；DASH-01 只断言上表 15 行 + 额外字段不缺失/类型正确。

### 1.4 非目标

- 不实现 Period2-08/09/10/11/13/14/15 的后端/页面功能（只留入口）。
- 不实现归因、月报、导出、设置基准/隐私、趋势/日志/对话/审计。
- 不做实时行情刷新、自动构建、多币种、按账户聚合（当前单用户单库）。
- 不做 Dashboard 服务端缓存/物化表（本期读聚合）。
- 不改变 `/nav`、`/assets`、`/metrics`、`/rounds` 现有口径与行为。

---

## 2. 与既有验收的合并矩阵

> **窗口约定（评审 P0-1，2026-10-30 修订）**：指标子块是**年度**口径（`metrics_period`：`as_of` 所在年 1/1 → `as_of`），由 API-084 显式返回并自动加载；资产概览 headline 是**最新快照**；历史概览是**全历史**。三种窗口在页面标题/子块上分别标注，禁止混用。`legacy_period` 仅保留给归因占位链接。

| 旧验收项（DEV-PLAN §3.1 / PRD FR-D1 / Period2-12） | 新位置 | 处理 |
|---|---|---|
| ① 净值 vs 沪深300、绝对收益、β | 资产概览 · "资产与收益"子块 | **部分保留**：`/nav?period=legacy_period` 展示 TWR/MWR/净值；**基准对比/β 当前 `/nav` 不提供（summary.beta=null），列入缺失清单 §7，不伪造**；旧卡①不能声称已满足 |
| ② 四数字：胜率/盈亏比/持仓天数/集中度 | 历史概览 · "核心指标（legacy_period）"子块 | **合并**：胜率仍取 `/metrics?period=legacy_period&ids=perf.win_rate`（保留 n/CI/sample_status/可信度），不要用全历史 `win_pct` 替代；全历史 `win_pct` 只作历史概览 headline，两者加"本月/全历史"标签 |
| ③ 费用：合计/占比/年化 bp/回本门槛 | 资产概览 · "费用"子块 | **合并**：`total_fees`（全历史）作 headline；`/metrics?period=legacy_period&ids=perf.fee,perf.fee_breakeven` 展示本月占比/回本门槛；"年化 bp"当前无独立字段，列入缺失清单 |
| ④ 行为：DE/换手/摊平/HHI | 历史概览 · "行为（legacy_period）"子块 | **保留**；由 `/metrics?period=legacy_period&ids=...` 指标卡展示（含 n/CI/可信度），非 ok 不出结论 |
| ⑤ 最赚/最亏 3 笔 | 历史概览 · "最赚/最亏"子块 | **保留**；最大收益 headline + 3+3 列表（含 `round_id`，可跳回合详情） |
| **下钻抽屉**（Period2-12） | 历史概览 · 指标卡/数字链接 | **合并为详情页**：点胜率等 → `/metrics/{id}?period=legacy_period`（现有 `MetricDetail` 已渲染 explain/drilldown）；此为验收变更 |
| **对账横幅**（Period2-12） | 资产概览顶部 | **保留**：读 `GET /reconciliation?period=legacy_period`；有断链/补差时显示横幅 + 链接 `/assets`；无数据则隐藏 |
| **可信度标签**（FR-R8/Period2-12） | 历史概览指标子块 | **保留**：复用 metric `sample_status/n/ci_low/ci_high`，显示"样本不足/窗口未满/可信/阻断"；全历史 headline 也标 `n/样本状态` |
| 顶部一句本期结论 | 两板块页头 | **保留为可空文案**：无报告/归因时不生成结论；占位"本期结论待月报/归因上线" |

> 结论：**页面视觉只有 2 个板块**；旧五卡全部有归宿，不另起第三板块。验收从"五卡有数"改为"两板块 + 子块断言 + legacy_period"；下钻抽屉改为详情页、基准卡部分缺失，均为明确的验收变更，不是静默丢失。

---

## 3. 术语与公式（本设计的核心）

### 3.1 资产概览

设最新资产日 `A = latest asset_daily(trade_date ≤ clock.today())`；全历史桥接结果 `B`（`bridge_for_period(period_start=首笔流水日, period_end=A)`）。**若 `A.status == blocked`（单日缺价/断链）：asset 概览整体 `blocked`，以下金额/比率全部 `null`，绝不把 blocked 行的 0 当 0 元输出。**

| 字段 | 公式/来源 | 格式 | 缺失/异常 |
|---|---|---|---|
| `holdings[]` | 当前持仓：代码/名称/数量/成本/市值/权重；成本口径与 `asset_daily` 一致（见 §4.2） | 数量/金额定点串；权重 `"12.34%"` | 无持仓 → `[]`；单标缺价 → 该标 `market_value=null` + `degraded` |
| `cash` | `A.cash_units` | CNY 2 位 | A 缺失/blocked → null |
| `money_fund` | `A.money_fund_units` | CNY 2 位 | A 缺失/blocked → null |
| `balance` | `A.cash_units + A.money_fund_units`（资产余额口径，页面「余额」统一显示） | CNY 2 位 | A 缺失/blocked → null |
| `market_value` | `A.market_value_units` | CNY 2 位 | null |
| `total_assets` | `A.total_assets_units` | CNY 2 位 | null |
| `position_pct` | `market_value / total_assets`（8 位比率、2 位百分比展示） | `"76.19%"` | `total_assets<=0` → null + reason |
| `total_profit` | `A.total_assets − B.initial_cash − B.initial_open_cost − B.cashflows − B.other`；`B.period_end=A`；`abs(B.residual_units) > B.tolerance_units` 或 `B.status != "ok"` → `degraded/missing`，不硬算 | CNY 2 位 | B blocked、stale、A blocked → null |
| `max_invested_cost` | `max(asset_daily.holding_cost_units)` 全历史（同一 definition_version；跳过 blocked；degraded 行计入，section 状态取**最新快照行**而非任一历史行） | CNY 2 位 | 无有效行 → null |
| `max_cost_return_pct` | `total_profit / max_invested_cost` | `"6.86%"` | 分母 0/null → null |
| `total_fees` | 全历史 `fee_breakdown(trades_until_A)` 的 `fee_amount`（佣金+印花税+其他费+独立费用行） | CNY 2 位 | 无流水 → `"0.00"`；**不存在"费用列缺失"状态**（三列 NOT NULL default 0），若解析警告缺列则 `degraded` |
| `weight_pct`（持仓行） | `holding.market_value / Σholdings.market_value` | `"100.00%"` | 无持仓/总市值 0 → null；行内权重和≈100%（按后端 2 位舍入容差 `n×0.5bp`） |
| `settlement_status` / `reconciliation` | 对账横幅数据（API-026） | 对象 | 无对账数据 → 隐藏 |

补充解释：
- `total_profit` 的 `other` 是桥接未分类现金流（无份额打新/补差/未知）；有份额的打新缴款已按买入计入 FIFO lot 成本，**不得**再进 `other`（否则残差恰好等于 `other`，全历史桥接假性不平）。按外部资金流从收益扣除，并在 `assumptions` 标注；对账补差是否算外部流需人工确认（D-02/H-02）。
- `initial_open_cost` 仅在数据从持仓中途开始时非零；全历史起点=首笔流水时通常为 0，但保留字段不删，避免假设错误。
- **stale 与 total_profit**：若 `asset_build_state` 显示流水指纹已变化（stale），`total_profit` 可能"旧资产 − 新流水"，必须置 `null` + `degraded`，不能显示可能误导的数。
- 持仓市值之和必须与 `A.market_value` 在同一价格阶梯/同一估值日对账；差异 > 1 分 → `degraded` 并说明，不静默取一个。为保证同价，看板持仓**只读行情缓存**（不触发尾部补拉），直接复用资产构建时落库的价格；`build_state=stale` 时快照落后于当前流水/行情，二者本就不可比，只提示 `DASHBOARD_STALE`，不再叠加伪 `HOLDINGS_MISMATCH`。
- `max_invested_cost` 的备选定义见 D-01；若人工选择"累计买入成本/峰值总资产"，仅换分母并同步文案/字段名。

### 3.2 历史概览

设 `R = closed rounds（status=closed 且 data_error=false）`；`asset_series = asset_daily 全历史行（同一 definition_version）`。

| 字段 | 公式/来源 | 格式 | 缺失/异常 |
|---|---|---|---|
| `closed_security_count` | `len(distinct security_code of R)` | int | R 空 → 0（这是**真实 0**，不是 missing） |
| `closed_round_count` | `len(R)` | int | 0 |
| `win_count` / `loss_count` / `flat_count` | `pnl>0 / pnl<0 / pnl==0` | int | — |
| `win_pct` / `loss_pct` / `flat_pct` | 各计数 / `closed_round_count` | `"60.00%"` | 分母 0 → null |
| `win_bar_pct`... | 与 `*_pct` 同值（CSS 宽度） | 同上 | 与 pct 同空；可只保留 bar 一套 |
| `max_profit` | `max(pnl of R)`（含股息，FIFO 回合 pnl） | CNY 2 位 | R 空 → null |
| `max_profit_round` | `{round_id, security_code, security_name, close_date, pnl, return_rate}`；并列取 `close_date,id` 最早 | 对象 | null |
| `max_profit_cost_pct` | `max_profit / max_invested_cost`（D-03；分母是平均成本峰值，**与分子 FIFO 不同引擎**，需人工确认） | `"13.89%"` | 分母 0/null → null |
| `top_rounds` / `bottom_rounds` | R 按 `pnl DESC/ASC, close_date, id` 取 3+3；含 `round_id/security_code` 供跳转；`flat` 可入 top/bottom 但标 0 | 数组 | 不足 3 条则返回现有条目，不补空对象 |
| `max_drawdown.from/to` | 有效日收益序列的峰/谷日期 | date | 无回撤/样本不足 → null |
| `max_drawdown.duration_days` | `to − from` 自然日（交易观测数另给 `duration_obs`） | int | null |
| `max_drawdown.mdd_pct` | `-MDD`（展示负百分比，如 `"-18.42%"`）；后端内部 `mdd_units` 为正 | `"-18.42%"` | insufficient/blocked → null |
| `max_drawdown.recovery_days` | 谷后回到峰值的**自然日**；未恢复 null + `censored=true` | int/null | — |
| `max_drawdown.n_obs` / `sample_status` | 有效日收益条数/门禁 | int/枚举 | `<60` → `insufficient`，不出 MDD |

- **回撤门禁（评审 P0-1 / 2026-10-30 修订）**：复用 `timing.drawdown` 的 `MIN_TRADING_DAYS=60`；有效观测 `<60` → `max_drawdown` 全 null + `insufficient`，**不得**用 2 条观测出"历史回撤"。`|daily_return|>1` 视为 `unreliable`；只有 `blocked` 行或日收益 NULL **才断链**。`degraded` 行（新股待上市按成本、债券缺行情按成本）日收益仍有效，**不得断链、不得进 missing/notice**；按最长连续有效段计算，完全无有效段 → `blocked`。
- **MDD 纯函数**：扩展现有 `core/stats/drawdown.py::drawdown_family` 的返回（additive：`peak_index/trough_index/recovery_index`），或新增 `max_drawdown_window` 并共享扫描；索引语义为 `index[0..n]`，`peak_index=0` 无对应日期；映射到 `usable_rows[idx-1]`。禁止 API/前端重算。
- `max_profit` 与"历史最大收益额"同义：**单笔已平仓最大收益（含股息）**；若产品指"单日最大收益"需人工拍板。
- `win_pct` 是全历史口径；旧五卡的月度胜率由 `/metrics?period=legacy_period&ids=perf.win_rate` 单独展示，两者不互相替代。

### 3.3 状态与缺失（评审 P0-2/P1-1 修订）

**两轴**，不要混成一个 `status`：

| 轴 | 取值 | 来源 |
|---|---|---|
| `build_state` | `missing/current/stale/blocked` | `asset_build_state` + 指纹/水位检查（`list_assets` 的派生逻辑） |
| `data_status` | `ok/degraded/blocked/insufficient/unreliable/missing` | 最新资产行、metrics 状态、回撤样本门禁 |

页面展示优先级：`blocked > missing > insufficient > unreliable > stale > degraded > ok`；但每 section 保留自己的 `data_status`，顶层 `status` 只做聚合提示，不覆盖 section 信息。

| 状态 | 含义 | 前端 |
|---|---|---|
| `ok` | 数据齐全 | 正常数字 |
| `stale` | build_state=stale（有新流水/行情） | **数字可显示** + 黄色"数据截至 X，建议更新" |
| `degraded` | 部分降级/勾稽差异/单标缺价 | **数字可显示** + 黄色提示；相关字段进 missing |
| `insufficient` | 样本/窗口不足（如 MDD<60 日） | 该字段不显示，显示"样本不足"，不挡其他字段 |
| `unreliable` | 异常收益/市值越界 | 该字段不显示 |
| `blocked` | 无现金锚点/资产阻断/回撤无有效段 | **不显示该 section 的收益/回撤数字**，只显示原因+操作 |
| `missing` | 从未构建/无流水 | 引导构建/导入，不显示数字 |

`missing[]` 统一形状：
```json
{"field":"max_drawdown","code":"DASHBOARD_MDD_INSUFFICIENT","reason":"有效交易日 < 60","what_to_do":"先补齐/重建资产表后重试"}
```
- `code` 必须是登记枚举（新增 `DashboardMissingCode`，或复用 `gate.py` 的 `ASSET_BUILD_BLOCKED/DATA_DEGRADED/RECON_BLOCKED`）；不允许自由字符串。
- `blocked`/`missing` 的字段值为 `null` 且进 `missing[]`；`stale/degraded` 可保留旧数字但必须带 `data_as_of`/原因。
- **评审 P0-2 已关闭**：stale 显示数字；DASH-06 只对 blocked/missing 断言不渲染数字。
- `styles.css` 需补 `badge-stale/badge-missing/badge-insufficient`（现有只有 ok/blocked/degraded）。

---

## 4. 后端设计

### 4.1 新端点 API-084 `GET /api/v1/dashboard`

- 用途：Dashboard 首屏聚合（FR-D1/D1 新需求）；认证 read；同步；**API-084 自身只读**；无 Idempotency-Key。
- 请求：无 query（当前快照 + 服务端推导 `legacy_period`）。后续可加 `as_of`，本期不做。
- 响应 200（示意）：

```json
{
  "as_of": "2026-09-30",
  "data_as_of": "2026-09-30",
  "legacy_period": "2026-06",
  "status": "ok",
  "definition_version": "1.0",
  "dashboard_version": "1.0",
  "inputs_hash": "sha256:…",
  "assumptions": {"calendar": "weekday_proxy", "benchmark_id": "hs300_tr"},
  "missing": [],
  "asset_overview": {
    "status": "ok",
    "build_state": "current",
    "data_status": "ok",
    "holdings": [
      {"security_code": "600519", "security_name": "…", "quantity": "100.0000",
       "cost": "150000.00", "market_value": "160000.00", "weight_pct": "100.00%"}
    ],
    "cash": "50000.00",
    "market_value": "160000.00",
    "total_assets": "210000.00",
    "position_pct": "76.19%",
    "total_profit": "12345.67",
    "max_invested_cost": "180000.00",
    "max_cost_return_pct": "6.86%",
    "total_fees": "2345.67"
  },
  "history_overview": {
    "status": "ok",
    "data_status": "ok",
    "closed_security_count": 12,
    "closed_round_count": 30,
    "win_count": 18, "loss_count": 10, "flat_count": 2,
    "win_pct": "60.00%", "loss_pct": "33.33%", "flat_pct": "6.67%",
    "win_bar_pct": "60.00%", "loss_bar_pct": "33.33%", "flat_bar_pct": "6.67%",
    "max_profit": "25000.00",
    "max_profit_round": {"round_id": "rnd_…", "security_code": "600519", "security_name": "…", "close_date": "2026-08-12", "pnl": "25000.00", "return_rate": "0.25000000"},
    "max_profit_cost_pct": "13.89%",
    "max_drawdown": {
      "from": "2026-04-10", "to": "2026-05-20", "duration_days": 40,
      "mdd_pct": "-18.42%", "recovery_days": 65, "censored": false
    },
    "top_rounds": [
      {"round_id": "rnd_…", "security_code": "600519", "security_name": "…", "pnl": "25000.00", "return_rate": "0.25000000", "holding_days": 30, "close_date": "2026-08-12"}
    ],
    "bottom_rounds": []
  },
  "links": {"assets": "/assets", "nav": "/nav", "metrics": "/metrics", "rounds": "/rounds"}
}
```

- 错误码：401（TOKEN_EXPIRED/TOKEN_REVOKED/MULTIPLE_CREDENTIALS）、403 `ADMIN_CANNOT_READ_TRADES`、429/500/503 由中间件返回；数据缺失/空账户走 200 + `missing[]`，不是 404。`build_state`/`data_status` 分离见 §3.3；`legacy_period` 由服务端按"最近有流水/回合的月份"推导（demo=2026-06）。
- ETag：本期不强制；若实现，按 `sha256(inputs_hash + latest_asset_id)` 生成弱 `W/"..."`，与既有读端点一致；`If-None-Match` → 304，不得写库。
- 契约：API-Catalog §1 新增 API-084；§6 新增验收行；`openapi_meta.OPERATION_META`、`gate_endpoint_coverage.py`（83→84、Period2 29→30）、契约快照 `contracts/openapi.json`、生成的 `schema.d.ts`、手写 `http.ts/contract.ts`、隔离矩阵全部同步。

### 4.2 应用层

新增 `backend/packages/review/src/stock_review/application/dashboard.py`：

```python
@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    as_of: date
    data_as_of: date | None
    status: str
    asset: AssetOverview
    history: HistoryOverview
    missing: tuple[MissingItem, ...]
    assumptions: dict[str, str]
    inputs_hash: str

async def build_dashboard(
    session, *, user_id, clock, market_data
) -> DashboardSnapshot:
    ...
```

- **API-084 自身只读**：不调用 `compute_and_store_nav`/`build_assets`/`rebuild_period`，不写任何表。旧子块懒加载的 `/nav`/`/metrics` GET 既有可能写 `nav_daily`/`metric_result`，页面必须折叠/点击加载，并接受既有读端点副作用（不在首屏触发）。
- 读取（单次上下文，避免 `bridge_for_period` 重复加载全量流水）：
  - `AssetRepository` 新增 `latest(user_id, definition_version)` / `all(user_id, definition_version)` / `max_holding_cost(...)`；`build_state` 由 `_build_state_view` 派生（DB 只有 `last_status`，**不直接读 state 列**）；
  - `asset_daily` 全历史只取 `trade_date/status/daily_return_units/holding_cost_units` 所需列，按 `definition_version` 过滤；
  - `trades` 一次加载（`TradeRepository.balances_ordered(period_end=A)`），同一对象传给 bridge helper 与 fee helper；
  - `round` 用 SQL 聚合 + top/bottom 查询，显式 `user_id`；
  - **当前持仓唯一来源**：新增 `core`/`review` 纯原语 `holdings_at(trades, ladder, A)`（或复用 `core/metrics/holdings.py::build_daily_portfolio`），成本口径**与 `asset_daily` 移动加权一致**；只取当前持仓标的的短窗行情（`as_of=A` 前 45 天），缺价逐标 `market_value=null`；**不用 `compute_nav_points` 全历史回放**；
  - 全历史桥接：从 `bridge` 抽 `initial_cash/open_cost/cashflows/other/residual` 的 helper，接收已加载 trades，避免二次查询；`period_end=A`。
- 计算全部在 `stock_review`（整数/Decimal），API 层只转定点字符串；**不在前端 calculator**。仓储方法全部显式 `user_id`；路由用 `state.db.session(user_id=...)` 并调 `ctx.forbid_admin_trades()`。
- `inputs_hash = "sha256:" + sha256(canonical_json({as_of, asset_build_fingerprint, rounds_fingerprint, trades_fingerprint, definition_version, dashboard_version}))`；fingerprint 用现有 `*_fingerprint_parts`，不要自造。`dashboard_version` 单独字段，D-01/D-02/D-03 改判时升版本。

### 4.3 历史回撤纯函数（评审 P1-7 修订）

现有 `backend/packages/core/src/stock_core/stats/drawdown.py::drawdown_family`（:50）已做复利/MDD 扫描，但**不返回峰/谷索引**（:16-32）。**不要另写第二套算法**：扩展其返回（additive 字段，保持旧调用兼容）或抽出共享扫描供两个入口使用：

```python
@dataclass(frozen=True, slots=True)
class DrawdownWindow:
    mdd_units: int | None          # 正数
    duration_obs: int | None       # 峰→谷的收益观测数
    peak_index: int | None         # 0..n；index[0] 在首个收益之前，无对应日期
    trough_index: int | None
    recovery_obs: int | None
    censored: bool

def max_drawdown_window(...) -> DrawdownWindow:
    ...
```

- 索引语义：`drawdown_family` 的 `index` 长度 `n+1`；`peak_index/trough_index` 用 `usable_rows[idx-1]` 映射日期（`idx=0` 为起点无日期）；剔除 NULL 日收益后必须用 `usable_rows` 列表，不能用原始 asset 行。
- 与 `timing.drawdown` **同源**：`MIN_TRADING_DAYS=60`、`|r|>1 → unreliable`、只有 blocked/NULL 断链（degraded 行日收益有效，不断链）；Dashboard 只在有效连续段上取 MDD，并在 `missing/notice` 披露 gaps；测试对照 `timing.drawdown` 的 value（同段时一致）。
- API 映射：`duration_days = trough_date - peak_date`（自然日）；`recovery_days` 同理；`mdd_pct` 在格式化层取负。
- 单测：无回撤、单调下跌、V 形、未恢复、blocked、NULL 洞、`|r|>1`、恰好 59/60 日、`peak_index=0`。
- 新增纯函数后 `make test-core` 必须 100% 覆盖（现有门禁）。

### 4.4 查询性能与边界

- 目标：单用户 5 万行流水/10 年资产，`GET /dashboard` p95 ≤300ms（NFR-10 / Pf-1）；实测超限则对 `asset_daily` 聚合/最新行加索引或缓存。
- 持仓明细最多返回 200 只，超出截断 + `missing/notice`（个人账户实际远小于）。
- 资产行数上限保护（如 20,000 行）；超限返回 `degraded` + 提示，不 OOM。
- 不返回股东号/账号等敏感字段。

---

## 5. 前端设计

### 5.1 路由与导航

- 新增 `frontend/apps/web/src/pages/Dashboard.tsx`（按仓库现有 `pages/` 目录，不用 `features/`）。
- `router.tsx`：
  - 根 `index` 重定向 `/dashboard`（当前 `/trades` 改掉）；
  - `/dashboard` 受 `RequireAuth` 保护；占位页 `/reports`、`/exports`、`/attribution` 也必须挂在同一 `Layout + RequireAuth` children 下；
  - 导航第一位加"看板"；同时更新 `Login.tsx:22`、`Register.tsx:38` 的 `navigate("/trades")` → `/dashboard`；
  - 更新 `router.test.tsx` 对导航前四项顺序的断言（新增"看板"会改变顺序）。
- 保留 `/nav`、`/assets`、`/metrics`、`/rounds` 链接，作为下钻入口。

### 5.2 页面结构（严格 2 个板块）

```
<DashboardPage>
  <PageHeader> 数据截至 / 状态徽标 / 刷新 / 入口按钮（月报、导出、归因占位）
  <section data-testid="asset-overview"> 资产概览
    <BuildBanner> build_state/data_status / 数据截止 / 去构建 CTA（跳 /assets，不自动 job）
    <ReconciliationBanner> 对账断链/补差（/reconciliation?period=legacy_period）
    <KpiGrid> 现金 / 持仓市值 / 仓位 / 总收益 / 峰值成本 / 最大成本收益率 / 总费用
    <HoldingsTable> 代码 名称 数量 成本 市值 权重（weight=mv/Σmv，sum≈100%）
    <AssetSubBlocks lazy> 净值与收益（/nav?period=legacy_period，标签：本月）· 费用（/metrics?period=legacy_period&ids=perf.fee,perf.fee_breakeven，标签：本月）
  </section>
  <section data-testid="history-overview"> 历史概览
    <KpiGrid> 已平仓股票数 / 盈利 / 亏损 / 最大收益 / 最大收益/最大成本 / 最大回撤
    <WinLossSummary> 计数 + 占比 + 简洁 bar（宽度用后端 bar_pct 字符串）
    <DrawdownCard> 时间段 + 回撤收益率 + 恢复 + n/sample_status
    <CredibilityBadges> 每个月度指标显示 n/CI/sample_status（/metrics）
    <HistorySubBlocks lazy> 核心指标（/metrics?period=legacy_period&ids=perf.win_rate,perf.pnl_ratio,perf.holding_days,perf.concentration，标签：本月；带 n/CI）· 行为（behavior.de/turnover/averaging_down/hhi）· 最赚/最亏（dashboard，含 round_id 跳转）
  </section>
</DashboardPage>
```

- 页面只有两个 `<section>`；旧五卡以 `AssetSubBlocks`/`HistorySubBlocks` 子块存在，不新增第三板块。
- 胜/负占比条：后端在字段中同时返回 CSS-ready 宽度串 `win_bar_pct/loss_bar_pct`（如 `"60.00%"`），前端直接作为 `style.width` 字符串使用，**不得** `parseFloat` 后计算；若不实现条，只显示文本百分比。
- 状态（按 §3.3 两轴）：
  - `blocked`/`missing` section：不渲染数字（DOM 无金额/百分比），显示原因 + `what_to_do` + "去构建/对账"链接；测试快照断言无数字。
  - `stale/degraded`：数字正常 + 黄色提示；`insufficient/unreliable` 只影响对应字段。
  - 所有 section 显示 `build_state` 与 `data_status`，不混用。
- 每个数字带 `data-testid`/`data-field`/`data-value` 供验收脚本比对；不从前端换算。
- 旧子块数据获取（**legacy_period 必须显式传**）：
  - `/nav?period=legacy_period`：时间加权/资金加权/净值 summary（图表 v1 不做则只展示 summary；此 GET 会物化 nav_daily，懒加载并接受既有副作用）；
  - `/metrics?period=legacy_period&ids=perf.win_rate,perf.pnl_ratio,perf.holding_days,perf.concentration,perf.fee,perf.fee_breakeven,behavior.de,behavior.turnover,behavior.averaging_down,behavior.hhi`：旧卡/可信度；
  - `/reconciliation?period=legacy_period`：对账横幅；
  - `/dashboard`：新字段 + 最赚/最亏；
  - 前端只渲染后端字符串，不计算；不引入 TanStack Query/ECharts（当前 package.json 无这些依赖）。

### 5.3 前端入口预留（未完成 P2）

新增 `FeaturePlaceholder` 组件/页面（`data-testid="coming-soon"`，文案含依赖任务，不显示假数据）：

| 入口 | 路由/位置 | 状态 | 依赖 | 验收断言 |
|---|---|---|---|---|
| 生成本月复盘 | Dashboard 页头按钮 → `/reports` | 占位页 | Period2-09/13 | 点击进入占位页，显示"报告功能暂停"，无假 HTML 下载 |
| 导出数据 | Dashboard 页头按钮 → `/exports` | 占位页 | Period2-10/13 | 同上 |
| 归因 | 历史概览"归因"子块 → `/attribution` | 占位页 | Period2-08 | 显示"归因暂未上线"，不显示因子数值 |
| 基准/隐私设置 | Settings 页新增"偏好设置（建设中）"卡片 | 占位 | Period2-11 | 卡片存在，无假保存 |
| 月报列表/下载 | `/reports` 占位页可含未来入口说明 | 占位 | Period2-09 | 无 404/白屏 |
| 导出任务/下载 | `/exports` 占位页 | 占位 | Period2-10 | 无 404/白屏 |
| 趋势/日志/对话/审计 | 不放入 Dashboard；保留缺失清单 | 不做入口 | P3 | 本期不验收 |

- 占位页不是 false success：不返回文件、不写 job、不显示伪造数值。
- `/reports` 占位页与同日 [月度报告设计](2026-10-28-monthly-report.md) 的 `/reports` 路由/入口保持一致；本期只做占位，不实现报告。
- `ComingSoon` 可复用；路由必须在 `router.test.tsx`/页面测试断言。

### 5.4 CSS/可访问性

- 沿用现有 `styles.css`；新增 `.dashboard-grid`/`.kpi`/`.kpi-value`/`.dashboard-section`/`.winloss-bar`；移动端单列。
- 数字使用 `<strong>` + `aria-label`；状态徽标 `badge-ok/stale/degraded/blocked/missing`；颜色不作为唯一信息。
- 百分比文案由后端 `*_pct` 字符串直接渲染；不 `Number()`/`toFixed()`。

---

## 6. 验收剧本（修订版，可直接执行）

### 6.1 主剧本

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000/api/v1}"
PERIOD="${PERIOD:-2026-06}"        # demo 流水止于 2026-06（D-19 未落时的已知 period）
EMAIL="${EMAIL:-1234@dev.com}"
PASS="${PASS:-1234}"
SKIPS=()

# 0) 前置：当前可用命令
make seed-demo
make test-metrics
make test-p2
make gate-golden-structure || SKIPS+=("G8 approval red: 22 COVERAGE 骨架 + fee_breakeven 口径")

# 1) 登录拿 access token（demo 账号；失败则记录 SKIP）
TOKEN=$(curl -fsS -X POST "$BASE/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | jq -r .access_token)
AUTH="Authorization: Bearer $TOKEN"

# 2) 构建该 period 的 rounds→assets→nav→metrics（API-083；当前已有）
JOB=$(curl -fsS -X POST "$BASE/jobs/rebuild-period" -H "$AUTH" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d "{\"period\":\"$PERIOD\"}" | jq -r .job_id)
for _ in $(seq 1 120); do
  STATUS=$(curl -fsS "$BASE/jobs/$JOB" -H "$AUTH" | jq -r .status)
  [ "$STATUS" = "succeeded" ] && break
  [ "$STATUS" = "failed" ] && { echo "rebuild failed"; exit 1; }
  sleep 1
done
[ "$STATUS" = "succeeded" ] || { echo "rebuild timeout"; exit 1; }

# 3) API-084 断言（jq；更完整断言用 pytest）
curl -fsS "$BASE/dashboard" -H "$AUTH" | tee /tmp/dashboard.json | jq -e '
  (.legacy_period | type=="string") and
  (.asset_overview.holdings | type=="array") and
  (.history_overview.closed_round_count | type=="number") and
  (.missing | type=="array")
'

# 4) 前端页面/占位/blocked：Vitest + RTL（仓库无 Playwright/MSW）
(cd frontend && pnpm -r test)

# 5) 明确 SKIP（缺失项不伪装通过）
SKIPS+=("make verify-p2 不存在（Period2-14 暂停）")
SKIPS+=("make rebuild / make demo-broken / D-19 demo 数据未完成")
SKIPS+=("Period2-08/09/10/11/13/15 功能未实现，仅占位")
printf 'SKIP: %s\n' "${SKIPS[@]}"
```

**手工/浏览器部分**（在 `pnpm dev` 下）：

1. 打开 `http://localhost:5173/dashboard`：
   - 存在 `data-testid="asset-overview"` / `history-overview`，无第三 section；
   - A1~A7、H1~H8（§1.3）逐项与 `/api/v1/dashboard` 字段相等；`data-value` 逐字符；
   - 指标子块显示 `metrics_period=2026`（年度：2026-01-01 → as_of）标签并自动加载；`legacy_period` 只用于归因占位链接。
2. 状态：
   - 正常/`stale`：数字可读 + 黄提示；
   - `blocked`/`missing`：该 section DOM 无金额/百分比，显示原因 + `what_to_do`；
   - 用 pytest 的 blocked fixture（见 §8）验证，不依赖不存在的 `make demo-broken`。
3. 指标子块：年度指标卡按 `metrics_period` 自动加载（年度区间 1/1→as_of 标签），资产子块有净值 summary/费用/对账横幅（`legacy_period`）；历史子块有四数字（带 n/CI/可信度）/行为/最赚最亏。
4. 下钻：点胜率 → `/metrics/perf.win_rate?period=2026-06`（不再用不支持 query 的 `/rounds?status`）；点最赚/最亏行 → `/rounds/{round_id}`。
5. 占位入口：Dashboard 按钮 → `/reports`、`/exports`、`/attribution` 占位页（`coming-soon`，`data-dependency` 标明 Period2-09/10/08）；无 `<a download>`、无 job、无假数字；从占位页可返回看板。
6. 缺失项：按 §7 清单核对；`SKIP` 输出必须列出。

### 6.2 验收断言（脚本级）

| ID | 断言 |
|---|---|
| DASH-01 | §1.3 的 A1~A7、H1~H8 **逐项**存在；缺失字段为 `null` 且进 `missing[]`（不是用 0 代替） |
| DASH-02 | `Σholdings.weight_pct≈100%`（按后端 2 位舍入，容差 `n×0.5bp`）；`Σholdings.market_value == asset_overview.market_value`（同价格/同日；差>1 分 → degraded） |
| DASH-03 | `total_profit` 与**独立手算 fixture**（initial_cash/open_cost/flows/other 固定值）一致；不得用生产 bridge 自证；stale/blocked 为 null |
| DASH-04 | 赢/亏/平计数之和 = `closed_round_count`；占比和=100%（量化容差） |
| DASH-05 | MDD 用独立手算序列；`n_obs<60` → `insufficient` 且 mdd=null；blocked/degraded 只降级不整块抹掉；`from/to` 与手算峰谷一致 |
| DASH-06 | **blocked/missing** 不渲染数字；stale 可显示数字（评审 P0-2 已关闭） |
| DASH-07 | 无 `Number(`/`parseFloat`/`parseInt`/`toFixed` 用于指标值/比率；无裸术语（`TWR/MWR/DE/HHI/CR1` 用中文标签） |
| DASH-08 | 无 token 401；admin 403 `ADMIN_CANNOT_READ_TRADES`；B 的 200 响应不含 A 的 `security_code/金额/UUID`；B 无数据 200+missing（不是 404） |
| DASH-09 | API p95 ≤300ms 目标（5 万行夹具为后续性能任务；未建夹具时标 SKIP，不伪通过）；首屏只调 `/dashboard`，旧子块懒加载 |
| DASH-10 | `/reports`、`/exports`、`/attribution` 占位页用 **RTL 路由渲染**断言 `coming-soon`、`data-dependency`、无下载/job；`curl` 200 不作为证据 |
| DASH-11 | 指标子块数值与 `/metrics?period={metrics_period}&ids=...` 逐项一致（`metrics_period` 年 1/1→as_of，仅 `status=ok` 字段）；`/nav`、对账横幅仍用 `legacy_period` |
| DASH-12 | 两轴状态映射/未知枚举兜底；`badge-stale/missing/insufficient` 有样式 |
| DASH-13 | API-084 在 Catalog/OpenAPI/OPERATION_META/gate 计数/隔离矩阵/手写客户端全部登记；无契约漂移 |

## 7. 缺失内容清单（二期剩余；评审 P0-6 修订）

> 每项：是否阻塞本任务验收 / 本次是否修 / 入口预留 / 依据。**"暂停"不等于"验收通过"**；未完成功能一律不可显示假数字/假下载。

### 7.1 后端端点/口径

| # | 缺失项 | 状态/证据 | 阻塞本任务？ | 本次修？ | 入口/占位 | 依据 |
|---|---|---|---|---|---|---|
| 1 | API-036 `/attribution` + 五因子 | 未实现 | 否（归因区块可 unavailable） | 否 | `/attribution` 占位 + 历史子块说明 | Period2-08 暂停 |
| 2 | API-039~042 报告 + report 表/异步/HTML | 未实现 | 否（入口占位） | 否 | `/reports` 占位；Dashboard 按钮 | Period2-09 暂停 |
| 3 | API-055~057 设置（基准/隐私） | 仅 PAT 设置 | 否（基准缺失已入第 11 项） | 否 | Settings "偏好设置（建设中）" | Period2-11 暂停 |
| 4 | API-058 行为规则阈值 | 未实现；**属三期**（不是 055~057） | 否 | 否 | 不做入口 | DEV-PLAN A.1：Period3-03 |
| 5 | API-059/060 导出 | 未实现 | 否 | 否 | `/exports` 占位；Dashboard 按钮 | Period2-10 暂停 |
| 6 | API-038 选股诊断 | 未实现（E-18 延后） | 否 | 否 | 无（不在 Dashboard） | 历史 design |
| 7 | **G8 golden approval** | S10：22 个 COVERAGE 骨架 + fee_breakeven 口径 → approval 23 项红；`make gate-golden` 失败 | **是**（费用/五卡"有数"依赖；旧卡可信度） | 否 | 旧费用子块可展示，但验收 SKIP 并注明 | S10 §3.1 |
| 8 | 基准数据源/`BenchmarkProvider` | `/nav` beta/alpha 为 null；market_live 拒 hs300_tr；无基准序列 | 是（旧卡①不可满足） | 否 | 资产子块"基准暂不可用"占位 | 缺失 §7.1 |
| 9 | 对账数据/横幅 | API-026/027 已有，前端无；Dashboard 需读取 | 否（可本任务做） | **是**（读取+横幅） | 无 | Period2-12 |
| 10 | 可信度标签/下钻 | 指标有 n/CI/sample_status；详情页已有 explain | 否 | **是**（子块+详情链接） | `/metrics/:id` | Period2-12 |
| 11 | 指标子块 period | 2026-10-30：指标子块改年度口径 | 否 | **是**（`metrics_period` 后端返回 + 前端自动加载） | 子块标签"年度指标（YYYY）" | 低频操作者月度样本不足 |
| 12 | 年化 bp | 无独立字段；fee/fee_breakeven 不等价 | 否 | 否 | 费用子块"暂缺" | 评审 P0-7 |

### 7.2 前端页面/入口

| # | 缺失项 | 本次修？ | 入口 |
|---|---|---|---|
| 13 | Period2-13 报告列表/预览/下载 | 否 | `/reports` 占位（`data-dependency="Period2-09/13"`） |
| 14 | Period2-13 导出任务/下载 | 否 | `/exports` 占位（`Period2-10/13`） |
| 15 | 归因页 | 否 | `/attribution` 占位（`Period2-08`） |
| 16 | 设置基准/隐私 | 否 | Settings 卡片占位（`Period2-11`） |
| 17 | 对账页/API-038 选股 | 否 | 不做入口（本期明确） |
| 18 | 趋势/日志/对话/审计/分享/通知 | 否 | 不做入口（P3） |
| 19 | 导航/根路由 | **是** | `/dashboard` + 导航"看板"；Login/Register 落地 `/dashboard`；router.test 更新 |
| 20 | 占位组件 | **是** | `src/ui/ComingSoon.tsx`（新建；仓库当前无此组件） |

### 7.3 工程/数据/测试基建

| # | 缺失项 | 影响 | 本次修？ | 备注 |
|---|---|---|---|---|
| 21 | `make rebuild` / `make demo-broken` / `make verify-p2` | 验收不可执行 | 否 | 用 §6.1 curl + `test-metrics/test-p2` 替代并 SKIP |
| 22 | D-19 demo：12 个月流水/日线/禁止超卖/行情 as_of | 旧卡/回撤/演示正常态不可达 | 否 | 当前 3 个月账单、9 个月频点、demo degraded；验收固定 period=2026-06 + FrozenClock |
| 23 | 前端工具链：TanStack Query/ECharts/MSW/Playwright/ESLint 自定义规则 | Testing §7 相关断言不可直接执行；文档不能假设已存在 | 否 | 本期用 Vitest/RTL + 源码正则；PRD FR-D1 技术栈偏差需拍板 |
| 24 | 50k/10 年性能夹具、k6/Pf-1 | DASH-09 不可测 | 否 | 本期只做小规模 benchmark；性能门禁 SKIP |
| 25 | 契约路径/计数 | `contracts/openapi.json`（非 `openapi/openapi.json`）；无 `operationId.lock.json`；Catalog 头 82/83 陈旧 | **是** | 84/84、Period2 30、OPERATION_META、手写客户端 |
| 26 | `scripts/acceptance/` 目录 | 当前不存在 | **是**（新增脚本） | 本任务新增 |
| 27 | PRD FR-D1/Testing §7 五卡/ECharts 基线 | 与两板块设计冲突 | **是**（文档修订清单） | 需人工批准范围变更 |
| 28 | Period2-14/15（verify-p2/零口径门禁） | 全量验收不可执行 | 否 | 本期先写 Dashboard 源码正则断言 |

> 结论：本任务仅实现 **API-084 + Dashboard 页面 + 占位入口 + 缺失清单 + 对账/可信度/下钻子块**；第 1~8、13~18、21~24、28 项保持未完成/占位，验收时明确 SKIP。

## 8. 测试设计

### 8.1 后端

| 层 | 文件 | 用例 |
|---|---|---|
| 纯函数 | `backend/tests/unit/core/test_drawdown_window.py` | 无回撤/V 形/单调下跌/未恢复/blocked/异常收益 |
| 应用 | `backend/tests/unit/review/test_dashboard_application.py` | 15 字段、总收益公式、峰值成本、盈亏占比、缺失状态、禁写库 |
| API | `backend/tests/api/test_dashboard.py` | 200/字段格式、空数据 200 missing、blocked 200、**无 token 401、admin 403、B 列表型 200 且无 A 数据**（不是跨租户 404）、无敏感字段、op coverage ≥3 |
| 隔离 | `backend/tests/isolation/test_isolation_matrix.py` | `LIST_ISOLATION_MATRIX`（B 200 无 A）、`ADMIN_FORBIDDEN`（403）、`UNAUTHENTICATED_MATRIX`（401）；gate-tenant |
| 契约 | `make api-snapshot/api-check/sdk` + `backend/tests/contract/test_openapi_contract.py` | API-084 在 `OPERATION_META`、422/401/403 错误码、example；契约 operation 数 50→51（或同步集合）；`gate_endpoint_coverage` 84/30 |
| 性能 | `backend/tests/perf/test_dashboard.py`（或 `make test-perf-api`） | 10 年/5 万行 p95 ≤300ms；无夹具时本任务 SKIP 并列入 §7 |

### 8.2 前端

| 对象 | 文件 | 断言 |
|---|---|---|
| Dashboard 页面 | `frontend/apps/web/src/pages/Dashboard.test.tsx` | 两 section、字段文本=API display、状态、空态、持仓表 |
| blocked | 同上 | DOM 无数字/无收益词；显示原因+操作 |
| 入口占位 | `ComingSoon.test.tsx`/router test | `/reports`、`/exports`、`/attribution` 可访问；`coming-soon`；无假下载 |
| 零口径 | `Dashboard.test.tsx` 源码 `?raw` 正则（仓库无 ESLint 自定义规则工具） | 无 `Number(`/`parseFloat`/`parseInt`/`toFixed` 用于指标；无前端比率拼接；无裸术语 |
| 路由 | `router.test.tsx` | `/` → `/dashboard`；未登录 → `/login`；导航含"看板" |

### 8.3 验收脚本

- `scripts/acceptance/dashboard.sh`（新增）：§6.1 的 seed → 登录 → API-083 构建 → curl `/dashboard` → jq 断言 DASH-01~05/08；DOM 断言用 `pnpm -r test`（Vitest/RTL），**不用 Playwright**（仓库没有）。
- 在 `make verify-p2` 缺失期间，脚本单独可跑并输出 `SKIP` 清单（引用 §7）。
- 禁止用生产代码生成期望值；后端单测用独立手算 fixture（DASH-03/05），不复用 bridge/drawdown 生产实现做 oracle。

---

## 9. 实施顺序与估时

| 阶段 | 内容 | 估时 |
|---|---|---|
| 0 | 契约（API-084/Catalog/OpenAPI/SDK/metrics 计数） | 0.3d |
| 1 | 纯函数 `max_drawdown_window` + 应用 `dashboard.py` + 聚合查询 | 1.2d |
| 2 | API-084 + schema/状态/错误/隔离测试 | 0.8d |
| 3 | 前端 Dashboard 两板块 + 持仓表 + 状态/空态 | 1.5d |
| 4 | 入口占位页/路由/Settings 卡片 + ComingSoon | 0.5d |
| 5 | 测试（前端/后端/验收脚本）+ 文档/验收更新 | 1.2d |
| **合计** | 不含 Period2-08/09/10/11；含契约元数据/隔离矩阵/前端手写客户端/性能优化的最小增量 | **6.5~8d**（本任务优先，其他暂停） |

> 说明：DEV-PLAN Period2-12 原 3 人日只覆盖旧五卡；新增两板块/聚合 API/回撤纯函数/入口占位/验收脚本/契约元数据/隔离矩阵后约 6.5~8d。若必须压到 3d，需砍旧五卡子块或历史回撤（不推荐，用户明确要求）。

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| "总投入最大成本/最大收益比率"口径歧义 | 数字错、返工 | D-01/D-03 人工拍板；文档写公式；前后端同源 |
| 全历史 bridge/资产扫描慢 | 首屏 >1s | 索引/行数上限/必要时缓存；5 万行性能测试 |
| 历史 blocked 行导致回撤整块不可用 | 验收"历史概览无数据" | D-05 改为有效连续段 + 60 日门槛；提供 gaps 原因/操作；演示数据避免断链 |
| 旧五卡与新两板块信息重复 | 页面过载 | D-06 合并矩阵；子块折叠；验收以两板块为主 |
| 占位页被误认为功能完成 | 用户误解 | 明确"暂停/依赖任务"；无假数据；缺失清单 |
| 前端隐藏计算 | 违反零口径 | 后端给所有 display 串；ESLint/AST/测试 |
| API-084 契约增加 | OpenAPI/SDK/门禁漂移 | 契约先行 + `contracts/openapi.json` + OPERATION_META + gate 84/30 + 手写客户端 |
| 当前资产/回合成 stale | 数字过期 | `data_as_of`/build state/CTA；不自动构建 |

---

## 11. 契约/文档变更清单

- `docs/architecture/v2/API-Catalog.md`：§1 增 API-084（标题 84/84、Period2 30）；§4/§6 验收矩阵；§2 详约或 OpenAPI 详约；表头 82 陈旧数同步。
- `contracts/openapi.json` + `frontend/packages/api-client/src/api/generated/schema.d.ts` + 手写 `http.ts/contract.ts` + `make sdk`；**仓库没有 `operationId.lock.json`**。
- `backend/services/api/src/stock_api/schemas.py`：`DashboardOut` 等。
- `backend/services/api/src/stock_api/routers/dashboard.py`；`app.py` 注册。
- `frontend/apps/web/src/router.tsx`、`Layout` 导航、`Dashboard.tsx`、`ui/ComingSoon.tsx`、`Login.tsx`/`Register.tsx`、`router.test.tsx`、`styles.css`（badge 样式）。
- `docs/DEV-PLAN-v2.md` §3.1：五卡 → 两板块验收；§3.2 Period2-12 描述更新；§A.1 新增 API-084 行，计数 83→84、Period2 29→30、总表 82/82 陈旧数修正（实现时）。
- `docs/architecture/v2/Testing.md` §7：Dashboard 测试位置（旧 `features/dashboard/` 改为 `pages/Dashboard.test.tsx` 或保留）。
- `docs/reviews/2026-10-27-p207-S10-acceptance.md`：新增 Dashboard 验收记录与缺失清单引用。

---

## 12. 验收 checklist（本任务完成时）

- [ ] API-084 契约登记、OpenAPI/SDK 同步、`api-check` 全绿。
- [ ] `/dashboard` 两板块，§1.3 的 A1~A7/H1~H8 齐全；数字与 API 逐字符一致。
- [ ] 旧五卡子块按 §2 合并，验收剧本更新；legacy_period=最近数据月。
- [ ] **blocked/missing 不显示数字；stale 可显示并提示**；无 token 401、admin 403、B 列表 200 无 A 数据。
- [ ] `/reports`、`/exports`、`/attribution` 占位入口存在；无假下载/假数字。
- [ ] 缺失清单 §7 与实际状态一致；验收脚本输出 SKIP 项。
- [ ] 前端零口径源码正则断言；后端 p95 目标准入（无 5 万行夹具时 SKIP，不伪通过）。
- [ ] `make test`/`test-p2` 可用部分全绿；`verify-p2` 缺失时明确记录。

---

## 13. HITL 决策明细（需人工确认）

| # | 问题 | 推荐 | 备选 | 影响/必须确认 |
|---|---|---|---|---|
| D-01 | "总投入最大成本" | 历史峰值持仓成本（不含现金） | 当前成本/累计买入/峰值总资产 | 收益率分母；必须确认成本引擎（移动加权 vs FIFO） |
| D-02 | "总收益额" | 全历史（含浮盈+股息+利息−费用）；`other` 作为外部流；stale 时 null | 已实现/当月/TWR 金额 | `other`（打新/补差）语义；是否含已收股息 |
| D-03 | 最大收益额/最大成本比率 | 最大单笔 FIFO pnl ÷ 峰值平均成本（混引擎，需知悉） | ÷ 该笔成本（=最大单笔收益率）/峰值总资产 | 可能量纲混用；建议另给该笔收益率 |
| D-04 | "已平仓股票数量" | distinct code；另给 round count | 仅 round count | 文案/验收 |
| D-05 | 最大回撤异常行/样本 | 有效连续段 + 60 交易日门槛；blocked/degraded 分段披露，不整块抹掉 | 任一 blocked 整块/跳过不管 | 历史可用性；需与 FR-R8 一致 |
| D-06 | 旧五卡合并/PRD 5 卡 | 两板块内子块保留 + 对账/可信度/下钻；正式修订 PRD/Testing | 删旧卡/另加板块/不修订 PRD | 页面结构/验收/文档偏离 |
| D-07 | 入口预留 | 路由占位页 + 入口按钮 + 返回 | disabled/隐藏 | 可发现性/验收 |
| D-08 | 新端点 vs 前端拼 | 新 API-084 | 前端聚合/扩展 assets | 架构/性能 |
| D-09 | 当前快照/副作用 | API-084 只读最新 asset_daily；旧子块懒加载（接受 `/nav`/`/metrics` 既有物化写） | 自动构建/首屏全打/实时估值 | 首屏/一致性/写副作用 |
| D-10 | 图表 | 无新依赖，CSS/SVG | ECharts | 与 PRD FR-D1 技术栈偏差 |
| D-11 | legacy_period | 最近有流水/回合月（demo 2026-06） | 当月/用户自选 | 旧五卡是否有数 |
| D-12 | 状态分类 | `build_state` + `data_status` 两轴 | 单轴 | stale/blocked 语义 |
| D-13 | 15 字段/额外字段 | §1.3 表；额外字段单列 | 只保留 15 | 验收可脚本化 |
| D-14 | 持仓成本/估值来源 | 与 asset_daily 同成本模型；短窗行情 + `holdings_at` | FIFO 全历史回放 | 性能/一致性 |
| D-15 | 性能 | 单次加载 + DB 聚合；300ms 目标；50k 夹具后续 | 物化/cache/降低夹具 | 是否允许缓存 |

> 若人工未在实现前排板：按推荐实现；涉及口径的 D-01/D-02/D-03/D-05/D-11/D-14 必须在 golden/验收前确认，否则只做占位不出数。**评审 P0 已在本 v0.2 文档修订；P0 关闭条件见交叉评审记录。**
