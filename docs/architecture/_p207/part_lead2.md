## 6. 数据、窗口与状态传播语义（Lead 主稿）

### 6.1 数据视图与字段映射

| 视图 | 来源 | 07 需要的字段 | 形态/规模 | 说明 |
|---|---|---|---|---|
| `rounds` | `review.round` | `id/security_code/status/open_date/close_date/quantity_units/cost_units/proceeds_units/dividend_units/pnl_units/return_rate_units/holding_days/data_error/oversell_units` | 单用户 ≤1e4 | 绩效/行为/选股 cohort 的主源；`status=open` 只用于全样本胜率/浮盈亏 |
| `round_fills` | `review.round_fill` | `round_id/buy_trade_id/sell_trade_id/quantity_units/buy_cost_units/sell_proceeds_units/pnl_units/open_date/close_date/holding_days` | 单用户 ≤5e4 | 分红归属/下钻到逐笔 |
| `trades` | `ledger.trade` | `id/trade_date/side/category/security_code/quantity_units/price_units/amount_units/commission_units/stamp_tax_units/other_fee_units/cash_balance_units/deleted_at/version` | 单用户 ≤5e4 | 费用/换手/摊平/持仓重建 |
| `asset_daily` | `review.asset_daily` | `trade_date/cash_units/market_value_units/total_assets_units/holding_cost_units/unrealized_pnl_units/holding_count/cashflow_units/prev_total_assets_units/daily_pnl_units/daily_return_units/status/definition_version` | 单用户 ≤3e3 日 | 回撤/β/费用率分母/换手分母 |
| `asset_build` | `review.asset_build_state` | `last_status/last_built_through/rows_count/data_as_of/assumption/definition_version/start_date` | 1 行/用户 | 决定 `stale/missing/blocked` 传播 |
| `bars` | `MarketDataSource.get_daily_bars`（replay 日线夹具 / live 日线） | `symbol/trade_date/close_units/return_units` | 单标的 ≤500 日 | 追涨分位/卖飞/持仓重建/BHAR |
| `benchmark` | replay `daily_bars.json` 的 `hs300_tr` 或 `MarketDataSource` 基准能力 | `trade_date/return_units` | ≤500 日 | β/α/IR/卖飞/BHAR；`available=False` 时相关指标 `blocked` |
| `calendar` | `stock_core/fifo/calendar.py`（M1=周一~周五） | `business_days_between` | 纯函数 | A13；assumption `calendar=weekday_proxy` |
| `rf` | `settings.rf_annual` | `Decimal` | 标量 | 默认 `0.015`；assumption `rf_rate` 输出 |

### 6.2 `period` / `from-to` / lookback（HITL-18）

- `period=YYYY-MM` → `[月初, 月末]` 闭区间（业务日 Asia/Shanghai）；`from/to` 也闭区间；`period` 与 `from/to` 二选一（API-033 契约）。
- **滚动窗口类指标需要 lookback**：`timing.chase` 需区间首日前 20 个交易日；`timing.fly` 需事件后 20 日（可能跨期尾）；`selection.bhar_60` 需买入日后 60 交易日；`timing.beta/drawdown` 至少 60 个交易日。定义：
  - `lookback_days` 由每个 def 的 `params_schema` 常量声明（不是指标入参）；
  - **取值规则**：`window_start = period.start − lookback_days（按交易日历回退）`，`window_end = max(period.end, period.end + max_forward_days)`（仅 fly/BHAR 需要后视，且 `data_as_of` 必须覆盖）；
  - `inputs_hash` 显式包含 `period_start/period_end/lookback/as_of/benchmark_id`（lookback 变化必须使结果失效）；
  - **PIT 硬约束**：`window_end ≤ data_as_of`；`data_as_of = min(clock.today(), market.as_of, asset_build.data_as_of)`，写入 `MetricResult.data_as_of`。
- 拒绝方案：严格只算 period 内事件（期末前的 60 日 BHAR 会被截断 → 系统性低估，且与 A24 不一致）。

### 6.3 状态传播矩阵（上游 → 指标）

| 上游信号 | 受影响指标 | `status` | `sample_status` | `value` | `conclusion` | `what_to_do` 示例 |
|---|---|---|---|---|---|---|
| 对账 `degraded`（有补差行） | 全部（尤其费用/胜率） | `degraded` | 按 n | 有 | 有（附"数据已补差"说明） | 核对补差行 |
| 对账 `blocked`（断链未闭合） | 全部 | `blocked` | `blocked` | 无 | 无 | 先修复账单断链 |
| `asset_build.state=missing` | timing.drawdown/beta、fee_breakeven、turnover | `stale` | `blocked` | 无 | 无 | 先构建资产表 |
| `asset_build.state=stale` | 同上 | `stale` | 按 n | 有（旧值） | 有（标注"数据待更新"） | 重建资产表 |
| 区间内 `asset_daily.status=blocked` 行 | timing.drawdown/beta | `blocked` | `blocked` | 无 | 无 | 补该日行情 |
| 区间内 `asset_daily.status=degraded` 行 | timing.drawdown/beta | `unreliable` | 按 n | 有 | 无 | 查看降级日 |
| 持仓行情缺失（bars 无该日） | chase/fly/BHAR/holdings | 视窗口是否可算 | `pending_window`/`insufficient` | 视 | 无 | 补行情或换 replay |
| benchmark 不可用 | beta/fly/BHAR | `blocked` | `blocked` | 无 | 无 | 配置基准数据 |
| `n_trading_days < 60` | beta/drawdown | `ok` | `pending_window` | 有 | 无 | 积累更多数据 |
| `n < 10` | 统计类 | `ok` | `insufficient` | 有 | 无 | — |
| `definition_version` 不匹配 | 全部 | `stale` | 按 n | 无（或旧值+stale 标记） | 无 | 触发重算 |

> 说明：`blocked` 时"无 value 键"是**硬约束**（Architecture §11、FR-R8、Reliability 硬阻断）；`insufficient/pending_window` 仍有原始 value 但无 CI/无 conclusion（D-08）。

### 6.4 行情能力协商（replay 现状与目标）

- `MarketCapabilities.supports_daily_bars=True` 默认；replay 当前实际只有月频 9 点。**07 目标**：新增 `fixtures/market/daily_bars.json` + replay 适配器按 interval 返回（`daily`/`monthly` 两种视图，`provenance` 带文件 sha256）；`capabilities` 增加真实能力声明，调用方不得猜。
- **不动 `quotes.json`**：现有 `/nav`、`asset_daily` 构建与测试期望值全部依赖月频，改动 blast radius 过大（D-02 备选①被否）。
- live：只支持 A 股 6 位代码 + 不复权日线；基准指数缺失时 `benchmark.available=False` → 相关指标 `blocked`（D-03）。
- assumptions 固定输出：`benchmark_id`、`adjust=""（不复权）`、`factor_version=""`、`calendar`、`nav_source`、`rf_rate`、`A=244`、`pit_rule`。

### 6.5 每日持仓内存重建（HHI / turnover / 浮盈亏 / 摊平）

算法（纯函数 `stock_core/metrics/holdings.py`，输入 trades+bar 收盘序列）：

```
holdings = {symbol: {qty_units: int, cost_units: int}}
for trade in stable_order(trades):                # (trade_date, line_no, id)
    if category in {buy, bonus, rights, new_stock}: qty += quantity; cost += |amount|  # 含费
    elif category == sell: qty -= quantity; cost -= proportional_cost(qty_sold)        # 与 FIFO 成本一致
    elif category == dividend: (成本不变，现金入账)
    snapshot[date][symbol] = (qty, cost)
for date in asset_daily_range:
    for symbol: mv = qty × close(date)（close 缺失 → 该日该标的不可估值 → 该指标 blocked/pending）
    weights = mv / total_assets（HHI_total）或 mv / Σmv（HHI_eq）
```

- **复杂度**：O(T log T + D·S)；单用户 10 年 1e4 流水 / 2500 日 / 50 标的 ≈ 1.25e5 次运算，<50ms（预算 <200ms，Pf-2 覆盖）。
- **必须在 assumptions 标注**：`holdings_source=reconstructed`（非落库明细）。
- 超卖（负持仓）日：与 `asset_daily` 一致允许负市值但该日权重无意义 → 该日从 HHI 样本剔除并记 `n_excluded`。

### 6.6 回撤指数与年化

- 指数构造与状态门禁见 §5.7；`asset_daily.daily_return_units` 已做现金流调整，等价于 TWR 日收益（A4/A5），无需再除以份额。
- **自然日 vs 交易日**：`asset_daily` 是逐自然日行，`duration/recovery` 用**行数（自然日）**输出，decomposition 同时给出 `trading_days` 估计（按行内 `status=ok 且交易日`计数，M1 用周一~周五）；assumptions 标注。
- 区间首日若 `prev_total_assets_units IS NULL`（区间起点无前值）：跳过首日收益，`n_days-1` 计入；若 n_days<2 → insufficient。

### 6.7 assumptions 统一清单（每个指标按需子集）

| key | 取值示例 | 适用 |
|---|---|---|
| `benchmark_id` | `hs300_tr` | β/fly/BHAR |
| `rf_rate` | `"0.015"` | β/α/IR |
| `A` | `244` | 年化类 |
| `calendar` | `weekday_proxy` | A13/回撤 duration |
| `nav_source` | `asset_daily.daily_return` | 回撤/β/fee_breakeven |
| `pit_rule` | `as_of_le_data_as_of` | selection/all |
| `holdings_source` | `reconstructed` | HHI/换手/摊平 |
| `adjust` | `""` | 行情类 |
| `bootstrap` | `{"b":1000,"seed":"sha256:…","method":"percentile"}` | DE/BHAR CI |
| `tolerance` | `eps_r=1bp` | 全部 |

---

## 9. 契约修订清单（实现阶段随任务落地，需人工批准）

> 原则：07 **只提出**修订，不在本阶段改文档/契约。每项标注"是否破坏性"与"谁来改"。

| # | 文件/位置 | 现状 | 建议改为 | 理由 | 破坏性 |
|---|---|---|---|---|---|
| C-01 | `docs/architecture/v2/API-Catalog.md` §2.3 API-033 | `ids=win_rate/de/bhar_20`；fx_n5 仍返回 value；未定义 value 出现规则 | dotted id 示例（`perf.win_rate` 等）；补 §0.8 风格的状态-字段矩阵（=§4.2 表）；`ci_min_n=30` 写入 | D-05/D-08 一致性 | additive 说明，不破 |
| C-02 | API-Catalog §2.3 API-034 | 禁裸术语清单缺 β/α/IR/HHI/Gini/Ulcer/Calmar/PGR/PLR | 扩充词表；非 ok 时 reason/what_to_do 枚举 | explain 模板可测 | 否 |
| C-03 | API-Catalog §2.3 API-035 | 断言②"每项金额 2 位定点" | item 增 `unit`；断言按 unit 分格式（CNY/ratio/days/count/z） | 比率型指标下钻 | 弱破坏（用例改） |
| C-04 | API-Catalog §2.3 API-038 | 断言③"不含晚于 as_of 的财报数据" | 改为"行情/事件不含晚于 as_of 的数据"；若未来接财报再恢复 | 本仓无财报（D-04） | 否 |
| C-05 | API-Catalog §4 `MetricResult` | 无 `decomposition` 字段 | 增加可选 `decomposition: object\|null`（值为定点串/整数），API-033/079 返回 | 四数字卡需要 median/CR1/全样本胜率 | additive |
| C-06 | API-Catalog §6 验收矩阵 | fixture 名 `fx_round30/fx_n5/fx_n30/fx_de_pos` 等未实现 | 按 §8 测试设计落地 fixture 清单；新增 07 指标验收行 | 验收可执行 | 否 |
| C-07 | `docs/prd/PRD-v2.md` 附录 A13 | "交易日计"未定义日历 | 补 `calendar=weekday_proxy`（M1）或真实日历归属 | D-09 | 否 |
| C-08 | PRD 附录 A14 | Gini/CR1 边界未定义 | 补"仅 pnl>0；n_pos<2 insufficient；n_pos<3 Top3 退化" | D-10 | 否 |
| C-09 | PRD 附录 A15 | PGR/PLR 未给实现定义 | 补 Odean 标准定义（卖盈/(卖盈+持亏)）与样本单元 | DE 可实现 | 否 |
| C-10 | PRD 附录 A16 | "停牌>50%" 无数据源 | 补 `suspension_proxy = 区间内 bar 缺失比例>50%` | 可执行 | 否 |
| C-11 | PRD 附录 A17/A23/A24 | z 版窗口、rf 参数化、PIT 定义表述不完整 | 补窗口/rf/PIT 的显式公式与常量 | 可实现/可测 | 否 |
| C-12 | PRD 附录 A22 | "吃掉 TWR 的 Y%" 无公式 | 补 D-11 公式与 TWR≤0 处理 | 可实现 | 否 |
| C-13 | PRD 附录 A26 | "N<30 附 CI" 歧义 | 改为"10≤N<30 有 value 无 CI；N≥30 有 CI" | D-08 | 否 |
| C-14 | `backend/packages/contracts/src/stock_contracts/constants.py:24` | `MIN_CI_SAMPLE=10` | 改 30 或新增 `MIN_CI_SAMPLE=30` 并保留旧名迁移注释 | 与引擎/A26 一致 | 内部 |
| C-15 | `docs/architecture/v2/Testing.md` §12.1 | 变异路径 `core/src/core` | 改为 `backend/packages/core/src/stock_core`；golden 目录 `backend/tests/golden/` | D-16 | 否 |
| C-16 | `docs/DEV-PLAN-v2.md` §3.2 | Period2-07 估时 3.5 人日、范围 18 指标 | 拆 07a/07b 并修正估时（§10）；注明 05/06 先决 | D-01/D-06 | 计划修订 |
| C-17 | `backend/packages/platform/src/stock_platform/config.py` | 无 `rf_annual` | 增 `rf_annual: Decimal = 0.015`（env `RF_ANNUAL`） | D-14 | additive |
| C-18 | `Makefile` | 无 `test-metrics`/`mutation`/`verify-p2` | 增 target（§11） | 门禁可跑 | additive |
| C-19 | `docs/DATABASE.md` | 无 `metric_result/metric_dependency` | 由 05 的迁移补充（07 不改 schema） | 台账 | 否 |

---

## 10. 实施顺序与估时（不含 05/06）

> **结论先说**：18 个指标 + 每个 golden + 独立 oracle + mutation ≥80%，**3.5 人日是显著低估**；
> 按本设计拆批：07a（核心 12 指标）≈ 8.5~9.5d，07b（exploratory 6 指标）≈ 3.5~4.5d，
> 不含前置 05/06 的 ~5d。合计 07 端到端 ≈ 17~19d（含前置）。请人工按 D-01/D-06 拍板。

| 步骤 | 内容 | 产出 | 人日 | 依赖 |
|---|---|---|---|---|
| S0 | **前置**：05 引擎最小内核 + 06 端点（见 §4.3 P1~P5） | registry/context/CI/status/persist/`/metrics` | 5（归属 05/06） | — |
| S1 | `core/stats`（numeric/wilson/bootstrap/regression/concentration/drawdown/tstat）+ 单测 | 纯函数 + 100% 覆盖 | 2.0 | — |
| S2 | 夹具：确定日线 `daily_bars.json`、demo 扩到 12 个月、`fx_metrics_core` | `ledger/fixtures.py` 扩展 + fixture 数据 | 1.5 | — |
| S3 | `core/metrics` 纯公式（perf 7 + behavior 4 + timing 4 + selection 3） | 纯函数 + 单测 | 3.5 | S1 |
| S4 | `review/metrics/defs/*`（18 个文件）+ explain/drilldown 声明 | 注册即发现 | 1.5 | S3 |
| S5 | golden（18 份，手算表/独立实现）+ golden 审批元数据 | `tests/golden/<id>/` | 1.5 | S3/S8 |
| S6 | 独立 oracle `tests/oracle/naive_metrics.py` + 三方比对测试 | oracle + 参数化测试 | 1.2 | S3 |
| S7 | 契约 kit + API 验收（API-033/034/035/079，含越权/blocked/ETag） | `tests/contract`、`tests/api` | 1.2 | S0/S4 |
| S8 | mutation：配置 + 必杀算子 + 首轮跑通 + 门禁接线 | `make mutation` + CI | 1.0 | S1/S3 |
| S9 | 联调、回归修复、文档同步（实现日志） | 全绿 | 1.0 | 全部 |
| — | **07a 合计（核心 12 指标）** | | **8.5~9.5** | — |
| S10 | exploratory 6 指标（含 HHI 持仓重建、摊平、换手、期望值、持仓天数、集中度） | 其余 defs + golden | 3.5~4.5 | S1~S9 |
| — | **07 合计** | | **12~14** | — |

**若人工要求严格 3.5d**：只能交付 6 个指标（`perf.win_rate`/`perf.pnl_ratio`/`perf.fee`/`behavior.de`/`timing.drawdown`/`selection.bhar_20`）的公式+golden，**没有** 独立 oracle、explain/drilldown、mutation 门禁——不满足 Period2-07 的验收原文。

---

## 11. 验收与门禁（07 增量）

### 11.1 本地验证剧本（在 DEV-PLAN §3.1 基础上加 07 断言）

```bash
make seed-demo && make rebuild            # 扩展后：12 个月日线 + 合成流水
make test-metrics                         # 新：core/stats + metrics 单测 + golden + oracle 三方比对
make mutation                             # 新：新增模块 mutation ≥80%
make verify-p2                            # 新：pytest + api-check + 端点验收 + 越权 + 前端 tsc
# curl 冒烟（05/06 就绪后）
curl "…/api/v1/metrics?ids=perf.win_rate,perf.pnl_ratio,perf.fee,behavior.de&period=2026-09" | jq '.items[] | {id:.metric_id,n,sample_status,status,value,ci_low,ci_high}'
# 期望：win_rate n≥10、sample_status=ok、value 小数、blocked 指标无 value 键
curl "…/api/v1/metrics?domain=timing&period=2026-09" | jq '[.items[] | select(.status=="blocked") | .metric_id]'
# 期望：live 无基准时 beta/fly 出现在 blocked 列表且无 value
```

### 11.2 golden 审批与失效

- golden 更新只允许独立 PR：贴 diff（金额到分、比率到 bp）+ `golden-approved` 标签 + `definition_version` 说明（Testing §12.3）。
- `definition_version` 未升而 golden 变化 → CI fail。
- 三方比对（手算表/oracle/生产）任一不一致 → 阻断合并（D2/D8）。

### 11.3 门禁分层

| 层 | 命令 | 阻断级别 | 内容 |
|---|---|---|---|
| PR-fast | `make test-metrics-fast` | 阻断 | core/stats + metrics 单测、golden、契约 kit（purity/determinism/sample gate） |
| PR-full | `make test-metrics` | 阻断 | + oracle 三方比对、API 验收、隔离、`gate_tenant_coverage` |
| nightly | `make mutation` | 建 issue/阻断 release | mutation 全量（≤40min），score ≥80% |
| release | `make verify-p2` 全量 | 阻断 | golden 冻结 + MANIFEST + 越权全绿 |

---

## 12. 风险与缓解

| # | 风险 | 触发 | 后果 | 缓解 | 归属 |
|---|---|---|---|---|---|
| R1 | 与未实现的 05/06 契约漂移 | 05/06 设计与 §4 不一致 | 07 返工、defs 全改 | §4 冻结；评审确认；05/06 先合并 P1~P5 | D-01 |
| R2 | 数据不足导致"指标有数但不达标" | 月频 9 点 / 3 个月 demo | sample_status=pending_window/insufficient，验收剧本失败 | D-02/D-17（日线夹具 + demo 扩长） | 07 |
| R3 | 期望值与实现互相自证 | golden 由被测代码生成 | 口径错误漏过 | 独立 oracle + 手算表 + 三方比对；golden 独立 PR | 测试设计 |
| R4 | 统计实现引入 float | Decimal 被误转 float、numpy 依赖 | 精度/确定性 | 禁 float 检查（AST/lint）；契约 kit 断言输出为 int 标度 | 07 |
| R5 | bootstrap 不确定性 | seed 未固定/未进 inputs_hash | golden 抖动 | seed=sha256(inputs_hash)；双跑逐位相等测试 | D-07 |
| R6 | 回撤/β 在 blocked 日被插值 | 为"凑满"序列 | 输出不可信收益 | 传播矩阵（§6.3）+ 契约 kit `assert_blocked_has_no_value` | 07 |
| R7 | 性能：HHI/持仓重建拖慢 /metrics | 10 年 1e4 流水 | p95 超 300ms | 内存重建预算 <200ms；Pf-2 点测；必要时缓存到 metric_result | D-13 |
| R8 | 真实账单税费/送转导致口径偏差 | 分红/送转/配股边界 | 费用/集中度错 | 复用 FIFO 成本原语；fixture 覆盖送转/配股/股息；golden 覆盖 | 07 |
| R9 | 估时再次失控 | 18 指标 × 测试 | 延期 | D-06 拆批；先 12 核心再 6 expl | 排期 |
| R10 | 变异门禁误报/环境缺工具 | mutmut 未装/路径错 | CI 红 | D-16 固定版本 + wrapper 脚本；nightly 才跑全量 | 07 |

---

## 13. 人工决策事项（HITL 明细，请逐项拍板）

> 每项：**事实依据 → 推荐 → 备选 → 成本/后果**。推荐方案已在正文生效；改判后需同步修改对应章节。

### HITL-1｜任务依赖：05/06 未实现，07 无法端到端交付
- **事实**：仓库无 `review/metrics`、无 `metric_result` 迁移（迁移止于 0006）、无 `/metrics` 路由；DEV-PLAN 明确 07 依赖 Period2-05。
- **推荐**：两层交付（07 的 core/defs/golden 先行；05/06 作为硬前置先补，约 5d）。
- **备选**：① 05/06 最小子集并入 07（+2.5~3d，跨任务边界）；② 严格串行（05/06 全做完再启动 07）。
- **不决后果**：07 无法单独完成 API 验收；若强行并行开发引擎，返工风险高。

### HITL-2｜replay 行情月频 9 点 vs 择时域日线需求
- **事实**：`fixtures/market/quotes.json` 只有 2026-01~09 共 9 个点；A17 需 `T−20..T−1`、A16 需 `h=5/10/20` 日收益与日波动、A23 需日频回归、A26 需 ≥60 交易日。
- **推荐**：新增独立 `daily_bars.json`（≥2 年工作日；含 4 条序列 + `hs300_tr`），replay 适配器按区间返回日线；**不动既有 quotes.json**（nav/资产表零回归）。
- **备选**：① 替换 quotes.json 为日线（+2~3d 回归修复，nav/asset golden 全变）；② live-only（测试不可复现）；③ 月频近似（违反 A16/A17/A23/A26，不可接受）。
- **影响**：不做 → 4 个择时指标只能 `insufficient/pending_window`，二期验收剧本"卡片有数"失败。

### HITL-3｜基准 hs300_tr 数据源（live 无指数适配）
- **事实**：`market_live.py` 只支持 6 位 A 股、只落不复权日线；`BenchmarkProvider` 端口仅文档；replay 有月频 `hs300_tr`。
- **推荐**：本期 replay 日线基准满足验收；live 缺基准 → `timing.beta/fly/selection.*` 返回 `blocked` 并给 `what_to_do`；指数适配单列后续任务。
- **备选**：① 本期补 akshare 指数适配（+1d，CI 不可测）；② 用户自备基准 CSV（新增导入流程）。
- **不决后果**：若要求 live 下看板择时卡有数，需追加指数数据源任务（约 +1~2d）。

### HITL-4｜选股域 PIT：财报/行业数据缺失
- **事实**：本仓无财报数据、无申万行业、无 PIT 财务表；API-038 断言③写"修改 as_of 后不含晚于该日的财报数据"，该断言在本仓不可执行。
- **推荐**：本期只做**价格类 PIT cohort BHAR 20/60**（cohort=买入日；as_of 作用于行情/事件）；同步修订 API-038 断言；行业归属/风格分段/财报因子另立任务。
- **备选**：① 引入财报+行业数据源（+5~8d，含 PIT 版本化表与校验）；② selection 域整体推迟。
- **不决后果**：若坚持 PRD 原文，07 无法验收 selection 域。

### HITL-5｜metric_id 命名与 definition_version
- **事实**：Extensibility §3.1 要求 dotted id；API-Catalog 断言用 flat id（`win_rate/de/bhar_20`）且示例 version=`2.1`；现有 nav/assets 用 `"1.0"`。
- **推荐**：dotted id + 全部 `1.0`；Catalog 断言/示例同步修订（C-01）。
- **备选**：① flat id + 放宽正则（架构债）；② 双 id 别名（多一层映射，易漂移）。
- **不决后果**：id 混用会导致 `/metrics?ids=` 与 registry 校验二选一失败。

### HITL-6｜范围与估时：18 指标 vs 3.5 人日
- **事实**：DEV-PLAN 估 3.5d；本设计拆解 18 指标 + 18 golden + 独立 oracle + mutation + 契约/API 测试 ≈ 12~14d（不含 05/06 的 ~5d）。
- **推荐**：拆 07a（核心 12，8.5~9.5d）+ 07b（exploratory 6，3.5~4.5d）；修正 DEV-PLAN 估时（C-16）。
- **备选**：① 严格 3.5d 只交付 6 指标（不满足验收原文）；② 18 个全做（≈14d+）；③ 放弃独立 oracle（违反 Testing D2）。
- **不决后果**：按 3.5d 排期必然延期或降级验收。

### HITL-7｜bootstrap CI 的确定性与参数
- **事实**：Extensibility 约定 "Random 由 Clock 派生"；golden 要求可复现；DE/BHAR 需要 CI。
- **推荐**：seed=sha256(metric_id|version|inputs_hash)[:8]，B=1000，percentile 法，xorshift64* 纯整数；CI 写 1e-8 标度。
- **备选**：① BCa（+1d）；② Clock 派生 seed（golden 抖动，不可接受）；③ 正态近似（DE 偏态，不推荐）。
- **不决后果**：golden 无法稳定，测试flaky。

### HITL-8｜CI 最小样本边界与 `insufficient` 时是否返回值
- **事实**：PRD A26 表述有歧义；`constants.MIN_CI_SAMPLE=10`；引擎文档 `ci_min_n=30`；API-033 断言 fx_n5 仍返回 value；Architecture §11 说非 ok 不返回 value。
- **推荐**：`min_n=10`（<10 → insufficient，有 value 无 CI/conclusion）；`ci_min_n=30`；`blocked/stale/unreliable` 无 value。
- **备选**：① 沿用 10 出 CI（与引擎/A26 冲突）；② 非 ok 一律无 value（需改 API-033 断言）。
- **不决后果**：05/06 与 07 对同一字段行为不一致 → 契约测试互斥。

### HITL-9｜交易日历：工作日近似 vs 真实 A 股日历
- **事实**：`business_days_between` 只按周一~周五；`round.holding_days` 已按此落库（改则需重建 round）。
- **推荐**：M1 沿用工作日近似，assumptions 显式 `calendar=weekday_proxy`；真实日历另立任务（含 round 重建 + version bump）。
- **备选**：① 立即引入内置真实日历（+1~2d，口径分叉/重建）；② 用 bar 交易日代理（覆盖不全）。
- **不决后果**：A13/回撤 duration 在节假日附近有 1~3 日偏差（已在文档披露）。

### HITL-10｜集中度 Gini/CR1 的负盈亏处理
- **事实**：A14 只写"分母仅含 pnl>0"，未定义 Gini 与 n_pos 小的边界。
- **推荐**：集中度只在盈利回合上定义；n_pos<2 insufficient；n_pos<3 Top3 退化=全部盈利占比。
- **备选**：① Gini 含亏损（需平移）；② Gini 用 |pnl|。
- **不决后果**：不同实现得出不同 Gini，golden 无法对齐。

### HITL-11｜A22 "吃掉 TWR 的 Y%" 公式
- **事实**：PRD 只有文字，无公式；TWR≤0 时分母无意义。
- **推荐**：value=`fee_drag_ratio=年化费用/年化平均持仓市值`；decomp=`twr_eaten_pct=年化费用/(|年化TWR|+年化费用)`，分母≤0 → null + unreliable。
- **备选**：① `Y=fee_drag/|TWR|`；② 只出费用率不出 Y%。
- **不决后果**：费用卡数字无法解释/不可比。

### HITL-12｜回撤族数据源
- **事实**：`nav_daily` 稀疏按月；`asset_daily` 逐自然日且有 `daily_return_units`；`asset_daily` 由 Period2-16 构建。
- **推荐**：用 `asset_daily.daily_return_units` 复利链构指数；`build.state != current → stale`；区间内 blocked → blocked；assumptions 标注。
- **备选**：① `asset_daily` 加 `unit_nav_units` 列（+1d）；② nav_daily（日频不可得，拒绝）。
- **不决后果**：回撤数字依赖月频跳变，日频 MDD 失真。

### HITL-13｜HHI/换手的每日持仓来源
- **事实**：`asset_daily` 只有聚合值；无 `asset_daily_holding` 明细表（Period2-16 D1 备选）。
- **推荐**：指标层内存重建（复用 valuation 原语），不落库；预算 <200ms。
- **备选**：① 新增明细表（+1.5d，存储大）；② 期末快照近似（日频 HHI 不可得）。
- **不决后果**：HHI 双口径/换手无法按日计算。

### HITL-14｜rf 默认值与来源
- **事实**：无国债/货基数据源；PRD 禁止默认 0；Period2-11 才做设置页。
- **推荐**：`settings.rf_annual` 默认 `0.015`，assumptions 输出；后续可改。
- **备选**：① 内置静态 rf 曲线；② 用户自填（设置页未做）。
- **不决后果**：β/α/IR 口径不定，golden 无法冻结。

### HITL-15｜API-035 下钻 item 的值格式
- **事实**：现行断言"每项金额 2 位定点"，对比率型指标不成立。
- **推荐**：item 增 `unit`；按 unit 分格式断言；Catalog 修订（C-03）。
- **备选**：① 强塞金额字段（语义错误）；② 只允许金额型指标下钻（信息缺失）。
- **不决后果**：比率型指标下钻验收无法通过。

### HITL-16｜变异测试工具与门禁范围
- **事实**：无 mutmut/cosmic-ray 配置、无 CI workflow；文档路径与实际包名不一致。
- **推荐**：mutmut（pinned）+ `make mutation`；新模块 ≥80%，现有 core 不退化；PR diff + nightly 全量；修正文档路径（C-15）。
- **备选**：① cosmic-ray（与文档不一致）；② 不做变异（违反验收）。
- **不决后果**：Period2-07 "mutation ≥80%" 无法验收。

### HITL-17｜API-038 异步诊断 handler 归属
- **事实**：`JobType.DIAGNOSTICS_SELECTION` 已存在；无 handler、无 job 注册；07 原文只写"选股指标"。
- **推荐**：07 只交付 defs/数据视图；handler 归 06 端点补齐或 07b；07 用直接构造的 metric_result 做验收。
- **备选**：① 07 一并实现 handler（+1d，端到端可验 API-038）；② 不做（API-038 无法验收）。
- **不决后果**：API-038 的 202→succeeded 流程无人认领。

### HITL-18｜period 与滚动窗口/lookback 语义
- **事实**：API-033 支持 `period` 或 `from/to`；A17/A16/A23/A24 都是滚动窗口，窗口可跨期。
- **推荐**：`window_start=period.start−lookback`（交易日回退）、`window_end=max(period.end, period.end+forward)` 且 `≤data_as_of`；lookback/as_of/benchmark 进 `inputs_hash`。
- **备选**：严格只算 period 内（期末事件被截断，系统性偏差）。
- **不决后果**：同一 period 不同实现结果不同，report/metrics 对不上。

### HITL-19｜演示数据不足以满足 A26 门槛（二期验收剧本不可达）
- **事实**：`seed-demo` 只有 3 个月（`months=3,count=120`）+ 月频 9 点行情；A26 要求日频 ≥60 交易日才出夏普/回撤/IR；DEV-PLAN §3.1 要求"五张卡片有数"。
- **推荐**：`seed-demo` 扩到 ≥12 个月（工作日级行情 + 合成流水），保持确定性字节可复现；验收剧本不变。
- **备选**：① 保持 3 个月，验收剧本改为"卡片显示数据不足"（需改 DEV-PLAN §3.1）；② 仅测试夹具加长，演示环境仍不足（剧本不可达）。
- **不决后果**：按现数据，timing/beta、timing.drawdown、selection.bhar 在演示环境必然 `pending_window/insufficient`，二期验收第一条不过。

### HITL-20｜`decomposition` 是否进 API（契约新增）
- **事实**：`MetricValue.decomposition` 在引擎契约中存在；`MetricResult`（API-033/079）无该字段。但四数字卡/A26 需要 median、全样本胜率、CR1、h=5/10 等并列值。
- **推荐**：`MetricResult` 增加可选 `decomposition: object|null`（additive，旧客户端忽略），值同样用定点串（金额 2 位/比率 8 位/整数）。
- **备选**：① 通过 `/explain` 的 method 文本带出（前端不可结构化消费）；② 每个变体单列 metric id（指标爆炸，违反 A26 预算）。
- **不决后果**：并行变体只能进文案，前端无法展示"含全样本胜率"等卡片数字。

### 13.1 不需要人工拍板的设计决策（已定）

| 决策 | 取值 | 依据 |
|---|---|---|
| 舍入 | Decimal ROUND_HALF_UP，出口量化一次 | `values.py` 既有约定 |
| 年化基数 | A=244 | PRD A20/A26、`constants.A_SHUFFLE_DAYS` |
| 容差 | 金额 0.01、比率 1bp | `EPS_AMT/EPS_R` |
| 金额/比率 scale | 2 / 8 | `constants` |
| 布尔统计（胜率）CI | Wilson | 二项比例标准做法；A10 要求 Wilson |
| DE 样本单元 | 卖出决策机会 | Odean 标准定义（PRD A15 未细写，见 HITL-9） |
| 费用口径 | 佣金+印花税+其他费，分母=双边成交额 | PRD A21；禁止二次扣费 |
| 回撤指数 | asset_daily 日收益复利链 | A4/A5 现金流调整等价 |
| PIT | `data_as_of = min(today, market.as_of, build.as_of)` | NFR-3 |
| explain 语言 | 中英模板，`Accept-Language` 切换 | API-034 断言④ |

---

## 14. 附录

### 14.1 术语与缩写

| 缩写 | 含义 | 出处 |
|---|---|---|
| PGR/PLR | 盈利兑现比例 / 亏损兑现比例 | A15（Odean 1998） |
| DE | 处置效应 = PLR − PGR | A15 |
| BHAR | 买入持有异常收益 | A24 |
| CR1/Gini/HHI | 集中度/基尼/赫芬达尔指数 | A14/A19 |
| MDD/Ulcer/Calmar | 最大回撤/溃疡指数/卡玛比率 | A18 |
| IR/TE | 信息比率/跟踪误差 | A23 |
| PIT | 时点一致（as_of 门禁） | NFR-3 |
| golden | 冻结的期望值快照 | Testing §12.3 |

### 14.2 引用

- `docs/DEV-PLAN-v2.md` §3.2（Period2-07）、§3.1（二期验收剧本）、§3.4（风险）
- `docs/prd/PRD-v2.md` 附录 A1–A27；FR-R4/R5/R6/R8
- `docs/Architecture-v2.md` §11（指标执行引擎）、§9.2/§9.3、§10.4
- `docs/architecture/v2/Extensibility.md` §3.1/§3.2/§3.3、§11.1
- `docs/architecture/v2/API-Catalog.md` API-033/034/035/038/079、§4 DTO、§6 验收矩阵
- `docs/architecture/v2/Testing.md` §9.3、§12.1、§12.3、§10.4
- `docs/reviews/2026-10-25-DevDesign-Review-asset-daily-table.md`（评审标杆）
- 代码：`stock_core/{values,nav}.py`、`stock_core/fifo/*`、`stock_review/{models,application/*}`、`stock_platform/{ports,market_live}.py`、`stock_ledger/{fixtures,models}.py`

### 14.3 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.9 | 2026-10-26 | Lead 主稿（决策摘要/现状/目录/契约/统计内核/数据传播/契约修订/估时/HITL） |
| v1.0 | 待定 | 合并 4 份 part + 交叉评审意见后成稿 |
