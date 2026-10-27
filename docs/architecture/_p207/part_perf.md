# part_perf —— 绩效域（指标 #1–#7）完整 spec

> 状态：**v0.9 内部工作稿（供 Lead 合并）** ｜ 日期：2026-10-26 ｜ 作者：perf-designer
> 口径来源：`docs/prd/PRD-v2.md` 附录 A10/A11/A12/A13/A14/A21/A22/A26；冻结契约：`docs/architecture/_p207/brief.md:37-67`（§2 冻结契约）、
> `docs/architecture/v2/Extensibility.md:98-126`、`docs/Architecture-v2.md:620-634`。
> 合并目标：`docs/architecture/2026-10-26-metrics-three-domain.md`。**本文件不含生产代码改动。**

## 0. 决策摘要（本域需要 Lead/人工拍板的部分）

| # | 结论 | 依据 | 对合并稿的影响 |
|---|---|---|---|
| P-1 | 本域 7 个指标**全部只用已落库数据**（round/round_fill/trade/asset_daily），replay 月频行情足够 | §1.4 | 绩效域不依赖 HITL-2 的日线夹具（唯一例外：全样本胜率的期末浮盈亏要期末收盘，月频有 2026-09-30 点） |
| P-2 | 主 `value` 一律取 A10/A11/A13/A14/A21/A22/A12 的**原文口径**，并列变体（median/全样本/自然日/CR1/Gini/p*）全部落 `decomposition` | brief §3 拆分原则 | 本域 7 个 id，不拆变体 |
| P-3 | 进 `core_conclusion` 的 **4 个**：`win_rate`/`pnl_ratio`/`fee`/`fee_breakeven`；其余 3 个 exploratory | A26 预算 8–12；Lead 表 §3.1 已按此冻结 | perf 域对核心预算的占用 = 4 |
| P-4 | `compute` 返回的 `n` = **该指标分母样本数**（不是区间回合总数）：win_rate→n_closed，concentration→n_pos，fee→计费成交行数，fee_breakeven→asset_daily ok 行数 | A26 门槛应作用在分母上 | 05 的 `sample_status` 直接吃 def 返回的 `n`，无需二次映射 |
| P-5 | `data_error` 回合**全部剔除**且只在 decomposition 计数 | `fifo/types.py:143-145`「closed_rounds … data_error 不计入」已有先例 | 不需要新 HITL；写成域级口径 |
| P-6 | 微比率指标（fee/fee_breakeven，量级 1e-4~1e-2）的 golden 容差**不能用 1bp** | 1bp 相对误差 ≈ 20%，会掩盖量级错误 | 转 part_test：容差分级表（§3.3） |

## 1. 本域公共口径

### 1.1 period 语义与归属日

| 项 | 定义 | 说明 |
|---|---|---|
| `period=YYYY-MM` | `start`=当月 1 日、`end=min(当月最后自然日, as_of)`，**业务日 Asia/Shanghai**，闭区间 `[start,end]` | 与 brief §2.3 一致；月末未到时 `end` 截到 `as_of`，避免未来日被当成 `degraded` 行（`assets.py:304` `uncovered`） |
| 回合归属日 | **`close_date ∈ [start,end]`**（平仓口径）；`open` 回合按 `open_date ≤ end` 计入"全样本"，不参与主 value | 依据 A10「已平仓」；平仓日归属与 A8 收益确认一致。**新歧义 → H-01** |
| 成交归属日 | `trade_date ∈ [start,end]`，且 `deleted_at IS NULL`（软删不入统计） | `ledger/models.py:182` |
| 资产归属日 | `asset_daily.trade_date ∈ [start,end]`；均值只取 `status ∈ {ok, degraded}`，且**区间内出现 `blocked` 行即 #6 判 BLOCKED**（过滤是防线，不是降级替代——blocked 行数值全 0，当 0 参与会虚低） | `application/assets.py:375-397` |
| lookback | 本域**全部为 0**（round/round_fill 是预计算实体，无需前置窗口）；`lookback_days=0` 显式进 `inputs_hash` params | HITL-18 的"M1 无 rolling 窗口"结论 |
| 期间自然日数 D | `D = (end - start).days + 1`（2026-09 → **30**） | 与"区间首末差"不同，见 H-05 |

### 1.2 回合数据口径（A10/A11/A12/A13/A14 共用）

| 状态 | 是否入主 value 分母 | 进入哪个 decomposition 字段 | 依据 |
|---|---|---|---|
| `closed` | 是 | `n_closed` | `models.py:42-43` |
| `data_error` | **否** | `n_data_error` | `fifo/types.py:143-145`（`pnl` 在超卖回合不可比） |
| `open` | 否 | `n_open` + 仅 `win_rate` 的全样本字段 | A10「并列全样本胜率（含浮盈亏）」；`open` 行 `holding_days=0`（`fifo/engine.py:362`）必须排除，否则均值被 0 拖低 |

- `round.pnl_units = proceeds − cost + dividend`（`fifo/engine.py:311`），`cost = abs(buy amount_units)`（`engine.py:140`），**已含费**。
- `round.holding_days` 是数量加权后四舍五入的**工作日近似**（`engine.py:305-310` + `fifo/calendar.py:12-22`）→ 见 HITL-9。
- 期末浮盈亏（全样本胜率用）：`asset_daily` 只有聚合 `unrealized_pnl_units`（`models.py:241`），**无逐标的值** → 需 `positions_end` 视图（复用 `valuation.total_market_value` 逐标的 floor，`application/valuation.py:99-119`）→ **H-02**。

### 1.3 费用来源字段（A21/A22）

| 量 | 字段 | 说明 |
|---|---|---|
| 佣金 | `ledger.trade.commission_units` | `ledger/models.py:169` |
| 印花税 | `trade.stamp_tax_units` | `models.py:170`；A 股卖出单边，生成器口径 `0.0005`（`ledger/fixtures.py:56-64`） |
| 其他费 | `trade.other_fee_units` | `models.py:171` |
| 发生金额 | `trade.amount_units`（带符号、**已含费用**） | `parsers/huatai.py:72-73`「净现金流，含费用」 |
| 独立费用行 | `trade.category='fee'`（股息红利税/独立手续费行） | `repositories/rules.py:48,72`（`sys_div_tax`/`sys_fee`） |

- **"禁止二次扣费"的准确含义**：`amount_units` 已折算费用，**不得再叠加三个费用列**去求成本/净额（`application/valuation.py:33-35` 明文）。费用列只用于**费用指标自身的分子**，绝不回写 pnl/成本。
- 成交额 `turnover` 只统计 `category ∈ {buy, sell}`（存量代码的持仓影响类见 `valuation.py:30`）；`new_stock/rights/bonus/transfer/fee` 不算成交额 → **H-04**。

### 1.4 与 `asset_daily` / `nav_daily` 的耦合

| 指标 | 是否需要资产序列 | 原因 |
|---|---|---|
| #1 win_rate（主 value） | 否 | round 已落库 |
| #1 win_rate（全样本 decomp） | **要期末价格** | 浮盈亏 = 期末市值 − 含费成本（`positions_end`） |
| #2/#3/#4/#7 | 否 | 纯 round/fill |
| #5 fee | 否 | 纯 trade |
| #6 fee_breakeven | **是**（`asset_daily`） | 平均持仓市值 + TWR 复利链（HITL-12 同一数据源） |

- `asset_daily.daily_return_units = daily_pnl / prev_total`（现金流调整后、`prev_total>0` 才非 NULL，`application/assets.py:170-173,325-331`）→ 用它复利得 TWR，与 A4/A5 同口径。
- **不用 `nav_daily`**：它是"有流水的日期 + 期末"稀疏行（Lead §2 F5），做不了逐日均值与完整复利链。
- 耦合门禁：`asset_build_state.last_status=blocked` → 本域唯一依赖方 #6 `BLOCKED`；`build` 非 current → `STALE`。

### 1.5 dependencies 图（level=perf=3，深度 ≤3，无环）

```
            ┌ DatasetDep(rounds) ──► perf.win_rate ◄── DatasetDep(positions_end)*
            ├ DatasetDep(rounds) ──► perf.pnl_ratio
            ├ DatasetDep(rounds) ──► perf.holding_days ◄── DatasetDep(fills)
            ├ DatasetDep(rounds) ──► perf.concentration
            ├ DatasetDep(trades) ──► perf.fee
            └ DatasetDep(trades)+DatasetDep(asset_daily) ──► perf.fee_breakeven ◄── MetricDep(perf.fee)
perf.win_rate + perf.pnl_ratio ──► perf.expectancy        # 深度 2，合法
* `positions_end` 为新增数据集视图（05 提供），不可用时只降级 decomposition，不阻断 value（H-02）
```
- **MetricDep 的语义（本域约定）**：用于 DAG 排序 + 状态传播；**数值一律由共享的 `core.metrics.perf` 纯函数重算**，不跨指标读 `decomposition`（decomposition 不是契约）。上游 `status ∈ {blocked,stale,unreliable}` → 下游同因 blocked/stale；上游 `sample_status=insufficient` **不阻断**下游（原始值仍在）。
- **其余 MetricDef 字段（全域）**：`benchmark_required=False`（**本域无任何指标使用基准** → "缺基准"对本域不构成 blocked）、`frequency="sync"`、`definition_version="1.0"`；`scope` 见 §1.6。

### 1.6 标度、舍入与输出格式

| 量 | 内部 | 输出 | 规则 |
|---|---|---|---|
| 金额 | `int` 分（scale2） | `"1234.56"` | `ROUND_HALF_UP`，出口只量化一次（`values.py:48`） |
| 比率 | `int` 1e-8（`values.py:153-157`） | `"0.43210000"` | `ROUND_HALF_UP` |
| 天数 | `int` 1e-2 | `"4.36"` | **本域新声明**：`days` + precision=2（均值非整数）→ H-08 |
| 计数 | `int` | `"12"` | 无小数 |
| 中间量 | `Decimal(prec=28)` | — | 除法/开方在 Decimal 域；禁 float；`Money.from_decimal` 运行时拒绝 float（`values.py:42-43`） |
| `scope` | 回合型 `unit="round", cohort="close_date", period="month"`（#1/#2/#3/#4/#7）；成交型 `unit="trade", cohort="trade_date"`（#5）；资产型 `unit="day", cohort="trade_date"`（#6） | — | `scope_key` 由引擎生成（含 user_id），def 不碰 |

### 1.7 sample_policy / status_policy / 取值规则（全域统一）

| status | sample_status | `value` | `ci_low/high` | `conclusion` |
|---|---|---|---|---|
| `ok` | `ok`（n≥30） | 有 | 有（若 `ci≠none`） | 有 |
| `ok` | `ok`（10≤n<30） | 有 | 无 | 有 |
| `ok` | `insufficient`（n<10） | **有原始值**（D-08） | 无 | `null` |
| `ok` | `insufficient`（结构性无定义：n=0 / n_win=0 且 n_loss=0 / n_pos<2） | **键存在但为 null** | 无 | `null` → H-07 |
| `blocked`/`stale`/`unreliable` | 任意 | **无 `value` 键**（`Architecture-v2.md:631`） | 无 | `null` + `reason`/`what_to_do` |

- 全域 `min_n=10`（`MIN_CONCLUSION_SAMPLE`，`constants.py:23`）、`ci_min_n=30`（HITL-8；与 `constants.py:24` 的 `MIN_CI_SAMPLE=10` 冲突，D-08 已裁定）。
- 全域统一 status 规则（声明式）：`upstream(rounds|trades|asset_daily)!=current → STALE`；`recon=blocked → BLOCKED`（账单断链可能漏行，费用/回合同样少算，**不等差处理**）；`asset_build=blocked` → 仅 #6 `BLOCKED`；`recon=degraded` → 仅 #6 `UNRELIABLE`（回合/成交类不依赖现金链）。

### 1.8 与 brief §6 已有 HITL 的关系 + 本域新增

| brief HITL | 是否影响 perf | 在本文落地 |
|---|---|---|
| HITL-1（05/06 未实现） | 是 | 全部 spec 只依赖 §1.5 视图与 §2 模板；golden 用 fake ctx 可独立跑（Lead D-01） |
| HITL-5（dotted id） | 是 | 全部 `perf.*` dotted，`definition_version="1.0"` |
| HITL-8（ci_min_n/取值） | 是 | §1.7；`n<10` 返回原始 value |
| HITL-9（工作日近似） | 是 | #3 显式 `assumptions.calendar=weekday_proxy` |
| HITL-10（Gini/CR1 分母） | 是 | #4 全部只在 `pnl>0` 上定义，`n_pos<2 → insufficient` |
| HITL-11（A22 的 Y%） | 是 | #6 双输出 + 量纲精化（H-05） |
| HITL-12（回撤族数据源） | 间接 | #6 的 TWR 复用 `asset_daily.daily_return` 链 |
| HITL-15（下钻格式） | 是 | 全部下钻 `item.unit` 分格式（§2 各条） |
| HITL-18（period/lookback） | 是 | §1.1（lookback=0） |
| HITL-2/3/4/6/7/13/14/16/17 | 否/仅参数 | #7 复用 bootstrap seed 规则（HITL-7/D-07）；#1 的全样本复用 HITL-13 的内存重建原语 |

**本域新增（编号 H-01…H-08，与 brief 的 HITL-1..18 不冲突；合并时建议统一重编为 HITL-19 起）**

| # | 歧义 | 推荐取向 |
|---|---|---|
| H-01 | 回合型指标的 period 归属日（`close_date` vs `open_date`）未在 A10–A14 规定；`round` 仓储现有过滤是 `COALESCE(close_date, open_date)`（`repositories/rounds.py:159`） | 归属 `close_date`；open 回合仅进全样本；05 新增 `rounds.in_period()` 查询（勿复用 `page` 的 COALESCE 口径） |
| H-02 | 全样本胜率的"浮盈亏"数据源：`asset_daily` 只有聚合值，逐标的需内存重建（同 HITL-13） | `positions_end` 视图；缺行情/重建失败 → 主 value 保持 `ok`，`decomposition.win_rate_all=null` + `assumptions.all_sample="unavailable"` |
| H-03 | A21 分子是否含**独立费用行**（`category='fee'`：股息红利税、独立手续费行）；A21 只写"佣金+印花税+其他费" | 含；拆 4 桶（佣金/印花税/其他费/独立费用行），并断言 `Σ4 桶 == fee_amount`（组不变量）。理由：A3 把红利税归"内部收益"、A8 股息净额已扣税，费用合计口径需覆盖真实支出 |
| H-04 | A21 分母"双边成交额"：毛额（`qty×price`）还是净额 `Σ|amount|`（含费、可对账）；A21 未定义 | 主口径**毛额**（逐笔 `money_mul` 后 floor 到分，`valuation.py:36-37` 同款 scale）；`decomposition.turnover_net` 并列给出（两者差 ≤ 费用合计，可作交叉断言） |
| H-05 | A22 两个年化基数不一致：费用年化用"期间自然日 D"，TWR 年化按 `nav.py:189-193` 用"首末差"；且 D-11 的 `\|年化 TWR\|` 与"年化费用"量纲不一致（ratio vs CNY） | 费用年化 `365/D`；TWR 年化 `365/(last−first)`；**"年化 TWR"金额化** = `平均总资产 × 年化 TWR` 后再与年化费用相加；`D<28` → `UNRELIABLE`（年化不可靠）并写 `assumptions.annualize_min_days=28` |
| H-06 | A12 的 `p` 取 A10 胜率（`pnl=0` 计入非胜）→ 零盈亏回合被当作"按平均亏损额亏损"；与"E=平均每笔盈亏"在经济上不等价 | 主 value **照 A12 原文**；`decomposition.mean_pnl = Σpnl/n_closed` 并列暴露差异（golden 显式断言两者不同：164.29 vs 200.00）；PRD 后续补一句 |
| H-07 | "结构性无定义"（n=0、n_pos<2、n_win=n_loss=0、turnover=0、avg_mv=0）该判 `insufficient` 还是 `unreliable`；API 是 `value:null` 还是**无 value 键** | `status=ok` + `sample_status=insufficient` + **`value:null`**（区间无交易≠数据不可信）；`blocked/stale/unreliable` 才无键。需 API-033 断言补一条 |
| H-08 | A13 均值非整数（4.3617…）与"天数/计数为整数字符串"（brief §2.2、D-15）冲突 | `unit=days` + `precision=2`（`"4.36"`）；D-15 的格式规则细化为"按 def 的 `(unit, precision)` 分级"，天数为 decimal(2) 而非 int |

**契约修订清单（本域提出，实现阶段随 `make api-snapshot` 落，不在本任务改文档）**

| # | 文件/位置 | 修订 |
|---|---|---|
| C-1 | `contracts/constants.py` | 新增 `Z_95=Decimal("1.959963985")`、`MIN_TRADING_DAYS=60`、`RF_ANNUAL_DEFAULT=Decimal("0.015")`；`MIN_CI_SAMPLE` 改 `30`（D-08）→ 本域一律读常量，禁魔数 |
| C-2 | `Extensibility.md:107` `SamplePolicy.ci` | 枚举需支持 `"none"`（A11/A13/A14/A21/A22 无 CI） |
| C-3 | API-035 断言② | 按 `item.unit`+def `precision` 分级断言（D-15/H-08） |
| C-4 | API-033 断言① | flat id → dotted（D-05）；断言 `n==12` 应对齐 `perf.win_rate`（fx_round30 定义见 §2 #1 golden） |
| C-5 | `core/values.py:193-195` | `mul_ratio` docstring 写"银行家舍入"，实现是 `ROUND_HALF_UP`（`:48`）→ **以代码为准**，改 docstring（避免两处口径） |
| C-6 | `stock_review/repositories/rounds.py` | 新增 `in_period(user_id, start, end, *, statuses)`（H-01）；`asset_daily` 已有 `page` 可复用（`repositories/assets.py:173`） |

## 2. 指标 spec（#1–#7，严格按 brief §4 模板）

### #1 `perf.win_rate` — 已平仓胜率（含全样本并列）
- **域/版本/core/mvp/frequency**：perf / `1.0` / `core_conclusion=True` / P0 / sync
- **口径来源**：A10「已平仓 `pnl>0` 占比，并列全样本胜率（含浮盈亏）；附 n 与 Wilson 95% CI」；A26；FR-R8。
- **公式**（标度整数，出口一次量化）：
  `n_closed=#{status=closed}`；`wins=#{closed ∧ pnl_units>0}`；`value = HALF_UP(wins × 1e8 / n_closed)`；`n_closed=0 → None`。
  手算（G-P1）：7 笔已平仓、4 盈 2 亏 1 平 → `4/7 = 0.5714285714` → `57142857` → `"0.57142857"`。
  全样本 decomp：`n_all=n_closed+n_open`，`win_all=wins+#{open ∧ unrealized>0}`，`5/9 → 55555556`（`"0.55555556"`）。
- **输入视图/依赖**：`ctx.rounds{status,pnl_units,close_date,open_date,round_id,security_code}`；`ctx.positions_end{symbol,unrealized_pnl_units}`。`DatasetDep("rounds")`+`DatasetDep("positions_end")`。传播：rounds 不可用→BLOCKED；positions_end 不可用→仅降级 decomp（H-02）。
- **窗口/period**：§1.1；close_date 闭区间；open 按 `open_date ≤ end`；lookback=0。
- **边界与异常**：n=0→`value:null`+insufficient（H-07）；n=1/5/9→insufficient（有值无 CI 无结论）；10/29→ok 无 CI；30→ok 有 CI；`pnl=0` **既不算胜也不算负但计入分母**（`>0` 严格，Testing §12.1 判据 `test_win_rate_excludes_zero_pnl`）；全胜→CI 上界 `1.00000000`；全负→下界 `0.00000000`；data_error 剔除并计 `n_data_error`；open 只进全样本；上游 blocked/stale；除零 n=0。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="wilson"`、`min_trading_days=0`。
- **status_policy**：`upstream(rounds)!=current→STALE`；`recon=blocked→BLOCKED`；`positions_end 缺失→不变更 status`。
- **CI**：Wilson，`z=1.959963985`（C-1）：`c=(p+z²/2n)/(1+z²/n)`、`h=z·sqrt(p(1−p)/n+z²/4n²)/(1+z²/n)`、`CI=c±h`，`Decimal.sqrt()`，8 位 HALF_UP。校验值：`wilson(7,10)=(0.39677815,0.89220873)`、`wilson(18,30)=(0.42320360,0.75409372)`、`wilson(30,30)=(0.88648661,1.00000000)`、`wilson(0,30)=(0,0.11351339)`。
- **输出**：`unit=ratio`、`precision=8`；decomp：`n_closed/n_open/n_data_error/wins/n_zero`(count)、`win_rate_all`(ratio)、`n_all`(count)。
- **explain**：zh `conclusion`「这段时间你平掉的 {n_closed} 笔交易里，{wins} 笔是赚钱的，胜率 {value:.1%}」；en「You closed {n_closed} trades; {wins} were profitable — a {value:.1%} win rate.」；`method`「已平仓回合中盈利回合的占比，附 95% 置信区间」；禁裸术语（Wilson/FIFO/TWR/HHI 不得出现，**"置信区间"可用**）。非 ok：`reason`「这段时间没有已平仓的交易」/`what_to_do`「先导入账单，再重建回合」。
- **drilldown**：`GET /api/v1/metrics/perf.win_rate/drilldown?period=`；items[] `{round_id, security_code, close_date, value(pnl 金额 2 位), unit:"CNY", label:"盈利|亏损|持平"}`；`source_endpoints=["/api/v1/rounds/{round_id}","/api/v1/rounds/{round_id}/fills"]`；params 回填 `period`、`basis=closed`。
- **golden**：G-P1（§3.1）期望 `0.57142857`；G-P4（n=30、18 胜）期望 `0.6` + CI `(0.42320360,0.75409372)`；API 级 `fx_round30` 断言 `n==12`（API-033 ①）→ **fx_round30 = 30 个回合，其中 12 个 `close_date∈2026-09` 已平仓 + 18 个 open**；容差 `EPS_R=1e-4`（比率 ∈[0,1]，1bp 足够）。
- **必杀变异**：① `pnl_units>0`→`>=0`（`test_win_rate_excludes_zero_pnl`）；② 分母 `n_closed`→`n_all`（`test_win_rate_closed_denominator`）；③ 纳入 data_error（`test_win_rate_excludes_data_error`）；④ 除法用整数截断（`test_win_rate_rounding_half_up`）。
- **HITL**：HITL-5、HITL-8、H-01、H-02、H-07。

### #2 `perf.pnl_ratio` — 盈亏比
- **域/版本/core/mvp/frequency**：perf / `1.0` / `True` / P0 / sync
- **口径来源**：A11「`平均盈利金额 / abs(平均亏损金额)`，并列 median 版」。
- **公式**：`avg_win=HALF_UP(Σwin_pnl/n_win)`、`avg_loss=HALF_UP(Σloss_pnl/n_loss)`（金额域，分）、`value=HALF_UP(avg_win×1e8/|avg_loss|)`。
  手算（G-P1）：`Σwin=190000 分/4=47500`、`Σloss=-50000/2=-25000` → `47500/25000=1.9` → `190000000` → `"1.90000000"`。
  median 版：`med_win=30000`、`med_loss=25000`（偶数取两中位均值，Decimal 后 HALF_UP 到分）→ `1.2` → `120000000`。
- **输入视图/依赖**：`ctx.rounds{status,pnl_units}`；`DatasetDep("rounds")`。传播：rounds 不可用→BLOCKED。
- **窗口/period**：§1.1（close_date 闭区间）。
- **边界**：`n_win=0` 或 `n_loss=0`（全亏/全胜）→ 比值无定义 → `value:null`+insufficient（H-07）；`pnl=0` 不计入任一侧但计入 `n_closed`（decomp `n_zero`）；n=0/1/5/9 insufficient；median 偶数样本；`|avg_loss|=0` 除零（同上）。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="none"`（A11 未要求 CI；C-2）、`min_trading_days=0`。
- **status_policy**：同 #1（`upstream`/`recon`）。
- **CI/统计细节**：无 CI（`ci="none"`）；`median` 仅 decomp，不参与推断。
- **输出**：`unit=ratio`、`precision=8`；decomp：`avg_win`(CNY)、`avg_loss`(CNY，负数)、`median_win`(CNY)、`median_loss`(CNY)、`pnl_ratio_median`(ratio)、`n_win/n_loss/n_zero`(count)。
- **explain**：zh「赚钱的那几笔平均赚 {avg_win} 元，亏钱的那几笔平均亏 {avg_loss} 元，赚的是亏的 {value:.2f} 倍」；en「Your average winner made ¥{avg_win} while your average loser lost ¥{avg_loss} — a {value:.2f}x ratio.」；`method`「盈利回合平均盈利 ÷ 亏损回合平均亏损的绝对值」；禁裸术语。非 ok：`reason`「这段时间没有亏损（或盈利）的已平仓交易，比值没有意义」/`what_to_do`「换一个交易更完整的区间再看」。
- **drilldown**：`.../perf.pnl_ratio/drilldown?period=&side=win|loss`；items[] `{round_id, close_date, value(pnl 金额 2 位), unit:"CNY", label:"盈利|亏损"}`；`source_endpoints=["/api/v1/rounds/{round_id}"]`。
- **golden**：G-P1 → `value=1.90000000`、`pnl_ratio_median=1.20000000`、`avg_win=475.00`、`avg_loss=-250.00`、`n_win=4`、`n_loss=2`、`n_zero=1`；全胜/全负两个边界用例取自 G-P1 的子集（`n_loss=0`/`n_win=0`）；容差 1bp。
- **必杀变异**：① 分母去掉 `abs`（负比值，`test_pnl_ratio_uses_abs_loss`）；② `avg_win` 除以 `n_closed` 而非 `n_win`（`test_pnl_ratio_average_over_winner_count`）；③ median 偶数取上中位（`test_pnl_ratio_median_even_sample`）；④ 把 `pnl=0` 计入亏损侧（`test_pnl_ratio_ignores_zero_pnl`）。
- **HITL**：H-01、H-07。

### #3 `perf.holding_days` — 平均持仓天数（exploratory）
- **域/版本/core/mvp/frequency**：perf / `1.0` / `core_conclusion=False` / P0 / sync
- **口径来源**：A13「交易日计（卖出日 − 买入日，同日记 1）；并列 median/自然日/资金占用天数」。
- **公式**：`value=HALF_UP(Σ round.holding_days / n_closed)`（单位日，2 位小数）。手算（G-P1）：`(5+3+10+1+1+2+6)=28 /7 = 4.00`。
  decomp：`median_holding_days=3.00`；`mean_natural_days=HALF_UP(Σ day_i/n_closed)`，`day_i=(close_date−open_date).days+1` → `42/7=6.00`；`median_natural_days=4.00`；
  `capital_weighted_days=HALF_UP(Σ cost_i×days_i / Σ cost_i)`，用 `round_fill.buy_cost_units` → `10,250,000/2,350,000 = 4.3617 → 4.36`；`capital_weighted_natural_days = 14,550,000/2,350,000 = 6.19`。
- **输入视图/依赖**：`ctx.rounds{status,holding_days,open_date,close_date}`、`ctx.fills{buy_cost_units,holding_days,round_id}`；`DatasetDep("rounds")`+`DatasetDep("fills")`。
- **窗口/period**：§1.1；`round_fill.open_date/close_date` 与 round 一致（`models.py:128-130`），无 lookback。
- **边界**：`open` 回合 `holding_days=0`（`engine.py:362`）**必须排除**；单回合；同日记 1；跨月/跨年（自然日可能 >365）；data_error 剔除；`Σcost=0`（理论上不出现，防御 → decomp 该字段 null）；n<10 insufficient。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="none"`、`min_trading_days=0`。
- **status_policy**：同 #1；`assumptions` 必含 `calendar="weekday_proxy"`（HITL-9）、`days_basis="business_days_between"`。
- **CI/统计细节**：无 CI（exploratory + `ci="none"`）。
- **输出**：`unit=days`、`precision=2`（H-08）；decomp：`median_holding_days`(days)、`mean_natural_days`(days)、`median_natural_days`(days)、`capital_weighted_days`(days)、`capital_weighted_natural_days`(days)、`n_closed`(count)。
- **explain**：zh「平均每笔持有了 {value} 个交易日（按周一到周五近似，未扣法定节假日）」；en「You held winners and losers for {value} trading days on average (weekday approximation).」；`method`「已平仓回合持仓天数的均值，同一买卖日按 1 天计」；禁裸术语（不出现"交易日历实现/交易日基准"）。
- **drilldown**：`.../perf.holding_days/drilldown?period=`；items[] `{round_id, open_date, close_date, value(2 位定点天数字符串，H-08), unit:"days", label:"持有 N 个交易日"}`；`source_endpoints=["/api/v1/rounds/{round_id}/fills"]`。
- **golden**：G-P1 → `4.00`、median `3.00`、自然日均值 `6.00`、资金占用 `4.36`、资金占用自然日 `6.19`；另含"同日记 1"用例（round.holding_days=1）；容差 0.01 天。
- **必杀变异**：① 纳入 open 回合（0 值污染，`test_holding_days_excludes_open_rounds`）；② 自然日 `+1`→`+0`（`test_holding_days_same_day_counts_one`）；③ 成本权重换成数量权重（`test_holding_days_capital_weighted`）；④ median 取上中位（`test_holding_days_median_even_sample`）。
- **HITL**：HITL-9、H-01、H-08。

### #4 `perf.concentration` — 盈亏集中度（exploratory）
- **域/版本/core/mvp/frequency**：perf / `1.0` / `False` / P0 / sync
- **口径来源**：A14「Top3 盈利 / 全部盈利；辅 CR1、Gini；分母仅含 `pnl>0`」。
- **公式**：`pos=desc([pnl_units | pnl>0])`、`S=Σpos`、`n_pos=len(pos)`；
  `value=HALF_UP(Σ top min(3,n_pos) × 1e8 / S)`。手算（G-P1）：`pos=[120000,30000,30000,10000]`、`S=190000`、`top3=180000` → `0.94736842`。
  `CR1=120000/190000=0.63157895`；
  `Gini`（D-10：盈利分布升序）`=ΣΣ|x_i−x_j| / (2n²μ)`，`μ=S/n` → `660000/(2×16×47500)=0.43421053`；
  独立校对式 `G=2Σ i·x_i/(n·Σx) − (n+1)/n = 0.43421053`（三方 oracle 之一）。
- **输入视图/依赖**：`ctx.rounds{status,pnl_units}`；`DatasetDep("rounds")`。
- **窗口/period**：§1.1。**n = n_pos**（§0 P-4），因此 A26 门槛作用于盈利回合数，`n_closed` 落 decomp。
- **边界**：`n_pos=0`（全亏）→ null+insufficient；`n_pos<2` → Gini 无定义 → null+insufficient（HITL-10）；`n_pos=2` → Top3 退化为 `value=1.0` 但 CR1 有区分度（`top3_k` 记 `min(3,n_pos)`）；`n_pos=3` → `value=1.00000000`（合法，explain 需说明"只有 3 笔盈利"）；`pnl=0` 不入分母；data_error 剔除。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="none"`。
- **status_policy**：同 #1。
- **CI/统计细节**：无 CI（Gini 的 bootstrap CI 不在 A14 要求内，列为后续）。
- **输出**：`unit=ratio`、`precision=8`；decomp：`cr1`(ratio)、`gini`(ratio)、`n_pos/n_closed/top3_k`(count)、`top3_amount`(CNY)、`positive_total`(CNY)。
- **explain**：zh「你赚的钱里，最大的 3 笔占了 {value:.1%}」；en「Your top 3 winners account for {value:.1%} of all realized gains.」；`method`「前 3 大盈利回合金额 ÷ 全部盈利回合金额（只统计已平仓盈利回合）」；`n_pos<3` 时文案改为「你一共只有 {n_pos} 笔盈利回合，集中度按全部盈利统计」。禁裸术语（不出现 HHI/Gini 英文裸词，中文"基尼系数"仅在 method 口径说明可接受）。
- **drilldown**：`.../perf.concentration/drilldown?period=&rank=1..N`；items[] `{round_id, security_code, close_date, value(pnl 金额), unit:"CNY", label:"第 k 大盈利"}`；`source_endpoints=["/api/v1/rounds/{round_id}"]`。
- **golden**：G-P1 → `0.94736842`、`cr1=0.63157895`、`gini=0.43421053`、`n_pos=4`、`top3_amount=1800.00`、`positive_total=1900.00`；另测 `n_pos=3`（=1.0）与 `n_pos=1`（insufficient）；容差 1bp。
- **必杀变异**：① `pnl>0`→`>=0`（0 进分母，`test_concentration_excludes_zero_pnl`）；② 分母换成全部回合 pnl 之和（含亏损，`test_concentration_denominator_positive_only`）；③ Gini 分母 `2n²μ`→`n²μ`（`test_concentration_gini_normalization`）；④ Top3 按升序取（`test_concentration_top3_descending`）。
- **HITL**：HITL-10、H-01、H-07。

### #5 `perf.fee` — 费用合计与占比
- **域/版本/core/mvp/frequency**：perf / `1.0` / `True` / P0 / sync
- **口径来源**：A21「佣金+印花税+其他费（展开子项）；禁止二次扣费；主分母=双边成交额」。
- **公式**：`fee = Σ(commission+stamp_tax+other_fee)`（period 内全部有效 trade 行）`+ Σ|amount_units|`（`category='fee'` 且三列全 0 的行，H-03）；
  `turnover_gross = Σ money_mul(qty,price)` over `category ∈ {buy,sell}`（H-04；`money_mul` 是唯一金额乘法入口、HALF_UP 到分（`values.py:198-200`），与 `valuation.py:118` 的逐标的 floor 不同，差值 ≤1 分/笔 → 用 `turnover_net` 交叉校验）；
  `value = HALF_UP(fee × 1e8 / turnover_gross)`。
  手算（G-P2）：`comm=1699+stamp=1705+other=0 → fee=3404 分`；`gross=6,790,000 分` → `3404/6790000=0.00050132548…` → `50133` → `"0.00050133"`。
  G-P3 的平铺版（成交价 = 当日携带收盘价，费用是唯一损益）：`fee=3451`、`gross=6,901,800` → `"0.00050001"`。
- **输入视图/依赖**：`ctx.trades{category,side,trade_date,quantity_units,price_units,amount_units,commission_units,stamp_tax_units,other_fee_units,deleted_at}`；`DatasetDep("trades")`。不依赖 round（费用列与 FIFO 无关）。
- **窗口/period**：§1.1；`deleted_at IS NULL`；lookback=0。
- **边界**：`turnover=0`（区间只有转账/费用行）→ null+insufficient（H-07，不 block）；只有买方（期间未卖出）→ 合法（双边=买+卖，卖为 0）；区间内有成交但三列全 0（历史上未落费用列）→ `fee=0` + `assumptions.fee_columns_missing=true` + `status=unreliable`（否则会被误读成"零费用"）；`category='fee'` 行与成交行费用列**重复计数**（二次扣费）必须由条件「三列全 0」排除；dedupe 由导入层保证（A27），指标层不再去重。
- **sample_policy**：`min_n=10`（n=计费成交行数）、`ci_min_n=30`、`ci="none"`。
- **status_policy**：`upstream(trades)!=current→STALE`；`recon=blocked→BLOCKED`（断链可能漏行→费用少算）；`recon=degraded→不变更`（费用列不依赖现金链，degraded 常见于缺余额列，不应阻断费率）。
- **CI/统计细节**：无 CI。
- **输出**：`unit=ratio`、`precision=8`；decomp：`fee_amount`(CNY)、`commission`(CNY)、`stamp_tax`(CNY)、`other_fee`(CNY)、`standalone_fee`(CNY)、`turnover_gross`(CNY)、`turnover_net`(CNY)、`fee_ratio_net`(ratio)、`n_fee_rows`(count)。**组不变量**：`commission+stamp_tax+other_fee+standalone_fee == fee_amount`。
- **explain**：zh「这段时间你的交易成本合计 {fee_amount} 元，占成交额 {value:.2%}」；en「Trading costs totaled ¥{fee_amount}, or {value:.2%} of your two-way turnover.」；`method`「佣金 + 印花税 + 其他费 合计 ÷ 双边成交额（买入额 + 卖出额）」；禁裸术语（不出现 TWR/DE 等；"双边成交额"属口径词，可保留）。非 ok：`reason`「这段时间没有证券成交，算不出费用占比」/`what_to_do`「换一个有成交的区间」。
- **drilldown**：`.../perf.fee/drilldown?period=&fee_type=commission|stamp_tax|other|standalone`；items[] `{trade_id, event_date, value(金额 2 位), unit:"CNY", label:"佣金|印花税|其他费|独立费用"}`；`source_endpoints=["/api/v1/trades/{trade_id}","/api/v1/trades?from=&to="]`。
- **golden**：G-P2 → `value=0.00050133`、`fee_amount=34.04`、`commission=16.99`、`stamp_tax=17.05`、`turnover_gross=67900.00`、`turnover_net=67882.88`、`fee_ratio_net=0.00050145`；G-P3 → `0.00050001`；容差见 §3.3（微比率用绝对 `1e-8` + 相对 0.5%，**不用 1bp**）。
- **必杀变异**：① 分子只取佣金（漏税，`test_fee_includes_stamp_tax`）；② 独立费用行与费用列重复计入（二次扣费，`test_fee_no_double_count`）；③ 分母 `turnover_gross`→加 `transfer/fee` 行金额（`test_fee_turnover_scope`）；④ `amount_units` 已含费却又叠加列求净额（`test_fee_does_not_resubtract_from_amount`）。
- **HITL**：HITL-15、H-03、H-04、H-07。

### #6 `perf.fee_breakeven` — 费用回本门槛
- **域/版本/core/mvp/frequency**：perf / `1.0` / `True` / P0 / sync
- **口径来源**：A22「年化费用 / 年化平均持仓市值；输出"吃掉 TWR 的 Y%"」；HITL-11/D-11。
- **公式**（H-05 精化；`D=期间自然日数`）：
  `fee`（同 #5）→ `annualized_fee=HALF_UP(fee×365/D)`；
  `avg_holding_mv=HALF_UP(Σ asset_daily.market_value_units / n_ok)`（只取 `status≠blocked`）；
  `value=HALF_UP(annualized_fee×1e8/avg_holding_mv)`；
  `twr=Π(1+daily_return_units)−1`（同一批 ok 行）；`annualized_twr=(1+twr)^(365/(last−first))−1`（`nav.py:189-193` 同口径）；
  `twr_amount=HALF_UP(avg_total_assets×annualized_twr)`；`twr_eaten_pct=annualized_fee/(|twr_amount|+annualized_fee)`（分母 ≤0 → null + `UNRELIABLE`）。
  手算（G-P3）：`fee=3451 分`、`D=30` → `annualized_fee=41,987.1667 → 41987 分`；`avg_holding_mv=805,210 分` → `value=41987/805210=0.05214437`；`twr=−0.00034510`；`annualized_twr=−0.00433483`；`avg_total_assets=9,998,301.57 分` → `twr_amount=−43,340.91`；`twr_eaten_pct=41987.17/(43340.91+41987.17)=0.49206743`。
- **输入视图/依赖**：`ctx.trades`（费用）、`ctx.asset_daily{trade_date,market_value_units,total_assets_units,daily_return_units,status}`；`MetricDep("perf.fee")`+`DatasetDep("trades")`+`DatasetDep("asset_daily")`（依赖仅用于排序/状态传播，数值重算，§1.5）。
- **窗口/period**：§1.1；`asset_daily` 行按 `trade_date` 闭区间切片；`D=(end−start).days+1`；TWR 年化用首末行差（≠D，H-05）。
- **边界**：`avg_holding_mv=0`（整月空仓）→ null+insufficient（H-07）；区间内存在 `blocked` 行 → `BLOCKED`（D-12 同源）；`build.state!=current` → `STALE`；`D<28` → `UNRELIABLE`（年化不可靠，H-05）；TWR 分母链断裂（`daily_return_units IS NULL` 且非首行）→ `UNRELIABLE`；`n_ok<10` → insufficient；`annualized_twr` 为负且 `|twr_amount|` 极小 → Y% 趋近 1（explain 需措辞"几乎全部"）。
- **sample_policy**：`min_n=10`（n=ok 行数）、`ci_min_n=30`、`ci="none"`、`min_trading_days=0`。
- **status_policy**：`asset_build=blocked→BLOCKED`；`asset_build.state!=current→STALE`；`recon=blocked→BLOCKED`；`recon=degraded→UNRELIABLE`；`blocked_row>0→BLOCKED`。
- **CI/统计细节**：无 CI；相关量（TWR/年化）全部 Decimal，除幂运算外无近似；`assumptions` 必含 `nav_source="asset_daily.daily_return"`、`annualization="365/D for fee, 365/(last-first) for TWR"`、`blocked_rows`、`annualize_min_days=28`。
- **输出**：`unit=ratio`、`precision=8`；decomp：`fee_amount`(CNY)、`annualized_fee`(CNY)、`avg_holding_mv`(CNY)、`avg_total_assets`(CNY)、`twr`(ratio)、`annualized_twr`(ratio)、`twr_amount`(CNY)、`twr_eaten_pct`(ratio)、`period_days/n_days/n_blocked_days`(count)。
- **explain**：zh「按这个月的成交成本折算成年化，相当于每年吃掉持仓市值 {value:.2%}；它占掉了这段时间收益的 {twr_eaten_pct:.0%}」；en「Annualized trading costs equal {value:.2%} of your average position value, absorbing {twr_eaten_pct:.0%} of this period's return.」；`method`「年化费用 ÷ 平均持仓市值；"占掉收益"= 年化费用 ÷（年化收益金额 + 年化费用）」；禁裸术语（**TWR 不得出现**，用"时间加权收益"或"这段时间的收益"）。非 ok：`reason`「这段区间有行情缺失的日期，或资产表还没构建到最新」/`what_to_do`「先重建资产表（POST /assets/build），再刷新指标」。
- **drilldown**：`.../perf.fee_breakeven/drilldown?period=&basis=fee|mv`；items[] `{event_date 或 trade_id, value(金额 2 位), unit:"CNY", label:"费用|当日持仓市值"}`；`source_endpoints=["/api/v1/assets?from=&to=","/api/v1/trades?from=&to="]`。
- **golden**：G-P3（30 行 asset_daily + 4 成交行 + 1 转账行）→ `value=0.05214437`、`annualized_fee=419.87`、`avg_holding_mv=8052.10`、`twr=-0.00034510`、`annualized_twr=-0.00433483`、`twr_eaten_pct=0.49206743`；边界用例：空仓月（avg_mv=0）、blocked 行 1 天、D=1；容差见 §3.3。
- **必杀变异**：① `365→244`（`test_fee_breakeven_annualization_base`）；② `D` 用 `last−first`（29）代替 30（`test_fee_breakeven_period_days`）；③ blocked 行纳入均值（`test_fee_breakeven_excludes_blocked_rows`）；④ `twr_eaten_pct` 分母漏 `+annualized_fee` 或去掉 `abs`（`test_fee_breakeven_eaten_denominator`）。
- **HITL**：HITL-11、HITL-12、H-05、H-07。

### #7 `perf.expectancy` — 期望值与盈亏平衡胜率（exploratory）
- **域/版本/core/mvp/frequency**：perf / `1.0` / `False` / P0 / sync
- **口径来源**：A12「`E = p·W − (1−p)·L`；`p* = 1/(1+盈亏比)`」。
- **公式**：`p`=A10 胜率（`n_win/n_closed`）、`W`=A11 平均盈利、`L=`|A11 平均亏损|；
  `E_units = HALF_UP(p_units×W_units/1e8 − (1e8−p_units)×L_units/1e8)`（分）；
  `p* = HALF_UP(L×1e8/(W+L))`（等价 `1/(1+R)`，R=A11）。
  手算（G-P1）：`p=4/7`、`W=47500`、`L=25000` → `27142.857−10714.286=16428.571 → 16429 分 → "164.29"`；`p*=25000/72500=0.34482759`。
  对照：`mean_pnl=140000/7=20000 分 → "200.00"`（含 1 个零回合，故 ≠ E，H-06）。
  代数性质（无零回合时）：`p·W−(1−p)·L ≡ Σpnl/n_closed` → 作属性测试（`test_expectancy_equals_mean_pnl_when_no_zero`）。
- **输入视图/依赖**：`MetricDep("perf.win_rate")`+`MetricDep("perf.pnl_ratio")`（§1.5 语义：状态传播 + 共享纯函数重算）。上游 `status` 非 ok → 同因 blocked/stale；上游 `sample_status=insufficient` 不阻断（原始值仍可算）。
- **窗口/period**：§1.1（与 #1/#2 同区间，保证三个数出自同一批回合）。
- **边界**：`n=0` → null+insufficient；`n_win=0`（全负）→ `E=−L` 有定义、`R` 约定为 0 → `p*=1.00000000`；`n_loss=0`（全胜）→ `E=p·W` 有定义、`R=∞` → `p*=null`（decomp 该字段 null，value 仍输出）；`pnl=0` 回合按 A10 计入非胜（H-06）；data_error 剔除。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="bootstrap"`（均值型统计；seed/B 按 HITL-7/D-07：`seed=sha256(inputs_hash)`、B=1000 percentile）；`min_trading_days=0`。
- **status_policy**：同 #1 + 上游指标状态传播。
- **CI/统计细节**：bootstrap 重采样单元 = 已平仓回合 pnl；统计量 = 均值 E；分位 2.5%/97.5%（最近秩 + 线性插值，Decimal）；`n<30` 不出 CI。
- **输出**：`unit=CNY`、`precision=2`；decomp：`p`(ratio)、`avg_win`(CNY)、`avg_loss`(CNY)、`win_loss_ratio`(ratio)、`breakeven_win_rate`(ratio)、`mean_pnl`(CNY)、`n_win/n_loss/n_zero/n_closed`(count)。
- **explain**：zh「按你现在的胜率和盈亏比，每做一笔的期望是 {value} 元；胜率要超过 {p*:.1%} 才长期不亏」；en「At your current hit rate and win/loss size, each trade is worth ¥{value} on average; you need a win rate above {p*:.1%} to break even.」；`method`「胜率 × 平均盈利 − 亏损概率 × 平均亏损」；禁裸术语（"期望""盈亏比"可用；不得出现 Kelly/TWR/DE）。非 ok：`reason`「这段时间的已平仓交易太少，期望值不足以参考」/`what_to_do`「累积到 10 笔以上已平仓交易再看」。
- **drilldown**：`.../perf.expectancy/drilldown?period=`；items[] `{round_id, close_date, value(pnl 金额), unit:"CNY", label:"盈利|亏损|持平"}`；`source_endpoints=["/api/v1/rounds/{round_id}"]`。
- **golden**：G-P1 → `E=164.29`、`p*=0.34482759`、`mean_pnl=200.00`、`p=0.57142857`、`win_loss_ratio=1.90000000`；边界：全胜（p*=null）、全负（p*=1.00000000）、n=9（insufficient）、n=30（bootstrap CI 非空且 `ci_low<value<ci_high`）；容差：CNY 0.01 元、ratio 1bp。
- **必杀变异**：① `p` 用 `n_win/(n_win+n_loss)`（排除零回合，`test_expectancy_uses_a10_win_rate`）；② 亏损项符号（`−`→`+`，`test_expectancy_loss_sign`）；③ `p*` 用 `R/(1+R)`（方向反，`test_expectancy_breakeven_inverted`）；④ `W` 用 median（`test_expectancy_uses_mean_win`）。
- **HITL**：HITL-7、HITL-8、H-06、H-07。

## 3. 域级测试要点（golden / 边界 / 变异 / 容差）

### 3.1 golden 清单（纯函数层，`backend/tests/golden/<metric_id>/a/`）
| id | ctx 夹具 | 覆盖指标 | 期望值来源 | 关键期望 |
|---|---|---|---|---|
| G-P1 | `perf_rounds_ctx_v1.json`：7 closed + 2 open + 1 data_error | #1 #2 #3 #4 #7 | **人工手算表**（本文件 §2 各条） | 0.57142857 / 1.90000000 / 4.00 / 0.94736842 / 164.29 / p*=0.34482759 / 全样本 0.55555556 |
| G-P2 | `perf_fee_ctx_v1.json`：4 成交行（买 1000@12.50 / 卖 1000@12.60 / 买 100@213.00 / 卖 100@215.00）+ 1 转账行 | #5 | 手算表 + 生成器口径（`ledger/fixtures.py:56-64`，佣金 0.025%、印花税卖出 0.05%） | 0.00050133；fee=34.04；comm=16.99；stamp=17.05 |
| G-P3 | `perf_fee_breakeven_ctx_v1.json`：平铺 4 成交行（买 1000@12.36 / 卖 1000@12.36 / 买 100@221.49 / 卖 100@221.49，价=携带收盘价）+ 30 行 asset_daily（2026-09-01..30，首日锚点来自 09-01 转账行） | #6（兼 #5 交叉） | 独立 oracle（`tests/oracle/naive_metrics.py` 朴素逐日推演）+ 手算表 | 0.05214437；annualized_fee=419.87；avg_mv=8052.10；Y=0.49206743；fee=34.51；twr=-0.00034510 |
| G-P4 | `perf_win_rate_n30_ctx_v1.json`：30 closed、18 盈 | #1 CI + 门槛 | 手算表 + Wilson 独立实现 | 0.6 + CI (0.42320360,0.75409372) |

- **oracle 三方比对**（Testing §9.3）：手算表（本文件）+ `naive_metrics.py`（可读性优先、不同作者、不 import 生产代码）+ 交叉恒等式（Gini 两公式、`Σ费用桶=fee`、`p*↔R` 互算、`E` 与 `Σpnl/n` 在无零回合时相等）。生产代码禁止 import `tests/`（import-linter C7）。

### 3.2 契约 kit 边界矩阵（`n=0/1/5/9/10/29/30`，按指标参数化）
| 档 | #1 | #2/#4/#7 | #3 | #5 | #6 |
|---|---|---|---|---|---|
| 0 | value:null + insufficient | value:null + insufficient | 同左 | 同左（turnover=0） | 同左（avg_mv=0） |
| 1 | 有值无 CI 无结论 | 有值无 CI 无结论（n_loss=0 → null） | 有值 | 有值 | 有值 |
| 5/9 | 同上 | 同上 | 同上 | 同上 | 同上 |
| 10/29 | 有值、有结论、**无 CI** | 同左 | 有值 | 同左 | 同左 |
| 30 | 有值 + CI + 结论 | 有值 +（#7 bootstrap CI） | 有值 | 有值 | 有值 |
- 另加：`blocked/stale/unreliable` 三态必须**无 `value` 键**（API-033 ②、API-079）；`assets.build.state!=current` → #6 `stale`。
- 不适用于本域的模板边界项：「分位窗口 max=min」（本域无窗口类指标）、「缺基准」（§1.5 已声明 `benchmark_required=False`）、「复权/停牌剔除」（本域不用行情序列，除 #1 期末估值外）。

### 3.3 容差分级（转 part_test，本域提案）
| 指标量级 | 容差 | 理由 |
|---|---|---|
| ratio ∈[0,1]（#1/#2/#3 派生/#4/#7 decomp） | 绝对 `EPS_R=1e-4` | 1bp 已足够严格 |
| **微比率**（#5 `~5e-4`、#6 `~5e-2` 与 Y% ） | 绝对 `1e-8` + 相对 **0.5%** | 1bp 对 5e-4 是 20% 相对误差，会掩盖量级错误（§0 P-6） |
| CNY | `EPS_AMT=0.01` | 与对账口径一致 |
| days | 0.01 天 | 2 位小数 |

### 3.4 变异算子汇总（必杀，与 Testing §12.1 对齐）
| 算子 | 本域落点 | 杀手测试 |
|---|---|---|
| `<`↔`<=` | `pnl_units>0`（#1/#4） | `test_win_rate_excludes_zero_pnl`、`test_concentration_denominator_positive_only` |
| 符号翻转 | #7 亏损项、#2 `abs` | `test_expectancy_loss_sign`、`test_pnl_ratio_uses_abs_loss` |
| 常量替换 | `365`（#6）、`1e8`（全部分母归一） | `test_fee_breakeven_annualization_base`、`test_constants_match_prd_appendix` |
| 分母 n/n−1 | Wilson `n`、Gini `2n²`、#2 `n_win/n_loss` | golden + `test_concentration_gini_normalization` |
| 样本集合 | data_error/open 纳入 | `test_win_rate_excludes_data_error`、`test_holding_days_excludes_open_rounds` |
| 二次扣费 | #5 独立费用行 + 费用列 | `test_fee_no_double_count` |

## 4. 实现顺序与估时（人日，0.1 粒度；不含 05/06）

| # | 工作 | 估时 | 前置 |
|---|---|---|---|
| 1 | `core/stats`：`numeric.div_round/sqrt_units`、`wilson.py`（全域共用，与 Lead §5 重叠） | 0.4 | — |
| 2 | `core/metrics/perf.py` 纯函数：win_rate 0.3 / pnl_ratio+median 0.3 / holding_days 0.3 / concentration+Gini 0.4 / fee 0.4 / fee_breakeven 0.6 / expectancy 0.2 | 2.5 | 1 |
| 3 | 7 个 def（explain 中英、drilldown、sample/status_policy） | 0.7 | 2 |
| 4 | golden 4 份 + `naive_metrics.py` oracle + 契约 kit 边界矩阵 | 0.9 | 2,3 |
| 5 | mutmut 配置与首轮本域变异（≥80%） | 0.4 | 2 |
| 6 | 与 05/06 联调（`positions_end`/`in_period` 视图、MetricDep 传播） | 0.5 | 05 P1–P3 |
| **合计** | perf 域 | **5.4**（其中共享 stats 0.4） | |
- 本域 5.4d 高于"18 指标 8.5~9.5d"平摊（perf 7 个 ≈ 3.7d）→ 原因：golden/oracle/mutation 是固定成本、#6 需资产链。**给 D-06 的输入**：按域分配时 perf 需 ~5d（若压到 4d，先砍 #3/#4/#7 的 decomposition 深度）。

## 5. 给 Lead 的合并提示（≤15 行）

1. **核心预算**：perf 进 core 的恰好 4 个（win_rate/pnl_ratio/fee/fee_breakeven），与 Lead §3.1 表一致；exploratory 3 个（holding_days/concentration/expectancy）。
2. **必须人工拍板的新增 8 条**：H-01（归属日）、H-02（全样本浮盈亏数据源）、H-03（独立费用行入账）、H-04（成交额毛/净）、H-05（两个年化基数 + D-11 量纲）、H-06（A12 零回合偏差）、H-07（结构性无定义的 value:null）、H-08（days 允许小数）。请合并时统一重编为 HITL-19…HITL-26，避免与 timing/behavior 的 H-xx 撞号。
3. **与决策摘要的衔接**：H-07 影响 D-08 的取值表（需补一行"结构性无定义"）；H-05 是对 D-11 公式的量纲修正（不推翻，只补"年化 TWR 金额化"）；H-08 细化 D-15 的格式规则（按 `(unit, precision)` 分级，days=decimal(2)）。
4. **契约修订 6 条**（§1.8 C-1..C-6）请进"契约修订清单"：常量补 `Z_95/MIN_TRADING_DAYS/RF`、`SamplePolicy.ci` 需支持 `"none"`、API-033 断言 ① 改 dotted + n==12 对齐、API-035 分格式、`values.py:193` docstring 与代码舍入不一致、`RoundRepository` 需新增 `in_period`。
5. **跨域耦合**：① #1 全样本与 behavior HHI/换手共用"内存重建每日/期末持仓"原语 → 需与 behavior 设计统一（Lead D-13），`positions_end` 视图归 05 数据集层；② #6 的 TWR 链与 timing.drawdown 同源（HITL-12/D-12），须同一实现，否则两处 TWR 会差 >1bp；③ #5/#6 与 behavior.turnover 共用"双边成交额"口径（H-04 的结论要同步给 behavior）。
6. **估时**：perf 域 5.4d（含共享 stats 0.4d），高于平摊值，建议 D-06 按域重分配（§4 末）。
7. **fx_round30 定义**（API-033 断言 n==12 的唯一解）：30 个回合 = 12 个 `close_date∈2026-09` 已平仓 + 18 个 open；行情序列只需 2026-09-30 一点（月频 replay 满足），不必等 HITL-2 的日线夹具。
8. **未解决风险**：`positions_end` 若 05 不提供，则 A10 的"全样本胜率"本期只能给 `null` + `assumptions.all_sample="unavailable"`（口径不完整但主 value 可用）——请 Lead 在决策摘要里显式接受或排期。
