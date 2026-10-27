# Period2-07 择时域（timing）设计 —— part_timing

> 范围：指标 #8–#11（`timing.chase` / `timing.fly` / `timing.beta` / `timing.drawdown`）。
> 本文件是设计稿，不写生产代码、不改 API-Catalog/PRD。口径唯一来源：PRD 附录 A16/A17/A18/A23/A26
> （`docs/prd/PRD-v2.md:580-590`）+ `docs/architecture/v2/Extensibility.md:94-110`（引擎契约）+ `docs/Architecture-v2.md:620-633`（§11）。
> 全文标度：价格 6 / 金额 2 / 数量 4 / 比率 8（`backend/packages/contracts/src/stock_contracts/constants.py:15-18`），除法一律 ROUND_HALF_UP。

## 0. 决策摘要（人工优先拍板）

| # | 结论 | 依据 | HITL |
|---|---|---|---|
| D1 | replay 夹具**只能算出回撤族**；`chase`/`fly`/`beta` 在现夹具下必然 `insufficient`（bar 覆盖率 0/174 天） | §3.1 硬数据 | HITL-2/3 |
| D2 | **推荐扩确定性夹具到日线**（2 年 × hs300_tr + 演示实际持仓标的），`drawdown` 先落 asset_daily 不等日线 | §3.3 方案 A | HITL-2 |
| D3 | 新增域级状态规则 **`bar_density<0.8 → status=unreliable`**，把"数据不足"显式化，禁止用月频点静默算 20 日窗口 | §1.5 | H-26 |
| D4 | `beta` 的估计窗口 = **期末回看 60 交易日**（不是 period 本身；单月约 21 交易日必然不足） | §2.3 / HITL-18 | H-24 |
| D5 | A17 的"复权序列"**无法满足**：replay `supports_adjusted=False`（`market.py:196`）、live `ADJUST=""`（`market_live.py:57`）→ 不复权 + assumptions 标注 | §2.1 | H-22 |
| D6 | rf 参数化：`rf_a` 默认 1.5%（HITL-14），日化 `rf_d=(1+rf_a)^(1/244)-1`；α 年化 = `α_d×244`；Calmar 用 A5 的 365/自然日年化 | §1.4 | H-23 |

## 1. 域公共口径

### 1.1 输入视图（建议名，待 Period2-05 `MetricContext` 最终确认；brief §2.1 允许标注）

| 视图 | 字段 | 落入依赖 | 用户 |
|---|---|---|---|
| `ctx.trades` | `trade_id/trade_date/security_code/quantity_units/price_units/amount_units/category` | `DatasetDep("trades")` | chase |
| `ctx.rounds` | `round_id/security_code/close_date/close_trade_id/proceeds_units/quantity_units/status/data_error`（`models.py:49-99`） | `DatasetDep("rounds")` | fly |
| `ctx.bars` | `Bar{symbol,trade_date,close_units,return_units}`（`ports.py:105-113`） | `DatasetDep("bars_daily")` | chase/fly/beta |
| `ctx.benchmark` | `benchmark_id` + `Bar` 序列（缺省 `hs300_tr`） | `DatasetDep("benchmark_series")` | fly/beta |
| `ctx.asset_daily` | `trade_date/daily_return_units/prev_total_assets_units/total_assets_units/status`（`models.py:207-257`） | `DatasetDep("asset_daily")` | beta/drawdown |
| `ctx.asset_build_state` | `state/last_status/rows_count` | `DatasetDep("asset_build_state")` | drawdown |
| `ctx.params` | `benchmark_id/rf_rate/timezone` + 各指标 params_schema | — | 全部 |

### 1.2 标度整数与舍入（全指标共用）

- 比率链乘：`nav_t = round(nav_{t-1}×(10^8+r_t)/10^8)`（int，HALF_UP），**禁止** float 中间量；高位乘积用 `Decimal`，累加器用 int。
- 回归累加器位宽：`Σxy`（scale 16）≈ `n×1e16`；`n≤244` 时 <2.4e18 < 2^63−1，安全；上线前加 `_MAX_UNITS` 断言（同 `market.py:50` 风格）。
- 除法：`q = (a×10^s)//b` 前一律先判 `b==0`；`b==0` 的语义由各指标定义（剔除/None/blocked），**不许**返回 0。

### 1.3 period 语义与 lookback

- `period=YYYY-MM` → 闭区间 `[首日, 末日]`；事件归属按 `trade_date`（chase 买入日 / fly 卖出日）。
- **前置 lookback 允许跨期**（HITL-18）：chase 20 bar、fly 60 bar（σ）+ h bar（前向）、beta 60 交易日；lookback 数据版本计入 `inputs_hash`，不进 period 边界。
- 前向窗口允许越过 `period_end`（fly 的 h bar），但越过夹具 `as_of` → `n_pending`（`pending_window`）。
- 交易日计数：**以 `ctx.bars` 的 distinct `trade_date` 为准**（真实 bar 日期）；`stock_core/fifo/calendar.py:12` 的周一~周五近似仅用于 `min_trading_days` 复核与文案，必须在 `assumptions.calendar="weekday_approx"` 显式声明（HITL-9 延续）。

### 1.4 rf / 年化 / 统计门槛（HITL-14 落地）

| 量 | 定义 | 来源 |
|---|---|---|
| `A` | 244（交易日年化基数） | `constants.py:21-22`；PRD A26 |
| `rf_a` | 年化无风险利率，默认 `0.015`（禁止 0） | HITL-14 |
| `rf_d` | `(1+rf_a)^(1/244)−1`，scale 8 | 本文补充（H-23） |
| α 年化 | `α_d × 244`（算术；PRD A23 "算术与几何并列"只约束超额，不约束 α） | 本文补充 |
| 超额算术 | `mean(r_p−r_b) × 244` | A23 |
| 超额几何 | `((1+R_p)/(1+R_b))^(244/n) − 1`（`R=Π(1+r)−1`） | A23 + 本文补充 |
| `min_n` / `ci_min_n` / `min_trading_days` | 10 / 30 / 60，只来自 `sample_policy`，**禁魔数** | `constants.py:23-24`、HITL-8 |
| `assumptions` 必备键 | `benchmark_id`（用到时）、`rf_rate`（用到时）、`A=244`、`calendar`、`nav_source="asset_daily"`、`adjust`、`window_basis` | API-033 响应 |

### 1.5 行情密度门禁（本域新增的核心规则，H-26）

`bar_density(窗口) = #bars(窗口内) / #期望交易日(窗口内)`，期望交易日 = 窗口自然区间内周一~周五日数。

| bar_density | 动作 |
|---|---|
| `=0` | `status=blocked`，`reason="窗口内无行情"` |
| `0 < x < 0.8` | `status=unreliable`，`reason="行情密度不足 {bars}/{expected}"`，**仍返回 value**（供排障），但无 `conclusion` |
| `≥0.8` | 正常计算 |

这条规则把"用 9 个月频点冒充 20 日日线"从**静默错值**变成**显式状态**（FR-R8 精神、Architecture §11 门禁）。`drawdown` 不吃这条规则（它不直接读 bar，见 §3.4），改吃 `asset_build_state` + 收益域校验。

## 2. 指标 spec

### 2.1 `timing.chase` — 追涨分位

- 域 timing / `definition_version="1.0"` / `core_conclusion=true` / `mvp="M1"` / `frequency=sync` / `benchmark_required=false`
- **口径来源**（PRD A17 原文）：`(P_buy − min20)/(max20 − min20)`，复权序列对齐，窗口 `T−20..T−1`；并列 z 版。
  **补充定义**：`T` = 买入日；窗口取 **T 之前最近 20 条 bar**（bar_index 语义，含 `T−1` 那条、不含 `T`）；`P_buy` = `trade.price_units`（不复权，见 D5）。
- **公式（标度整数逐步）**：对每个有效买入事件 e：
  1. `num = P_buy_units[6] − min20_units[6]`；`den = max20_units[6] − min20_units[6]`
  2. `den == 0`（窗口平价）→ 事件剔除，`n_flat+1`
  3. `q_units[8] = round_half_up(num × 10^8 / den)`（Decimal 中间量，输出 scale 8）
  4. `value[8] = round_half_up(Σq_units / n_valid)`
  Decimal 等价式：`q=(P−min)/(max−min)`；`value=(1/n)Σq`。
  **手算例 1**（20 条等差收盘 `10.00+0.10i, i=0..19` → min=10.00、max=11.90，`P_buy=11.50`）：`q=(1.50)/(1.90)=0.78947368` → `78947368`。
  **手算例 2**（舍入）：`num=1.00, den=3.00` → `33333333`（1/3 HALF_UP）。
  **手算例 3**（均值）：三事件 `q∈{0.35, 0.10, 0.33333333}` → `Σ=0.78333333`、`Σ/3=0.26111111` → `26111111`。
- **z 版（decomposition）**：`z = (P_buy − mean20)/s20`，`s20` = 20 条收盘的**样本标准差（ddof=1）** scale 8。
  例：同上 20 条收盘 → `mean=10.95`、`s20=0.59160798`、`P=11.50` → `z=0.92966968`（输出 4 位 `0.9297`）。
  **变异对照**：若 `s20` 用 ddof=0（`0.57662813`）→ `z=0.9538`，差值 0.024 ≫ 1e-4，可杀。
- **输入视图**：`ctx.trades`（`category=="buy"`）+ `ctx.bars`；无上游指标依赖。
- **窗口/period 语义**：统计 period 内买入事件；每事件 lookback = 该日之前 20 条 bar（可跨期）；`assumptions.window_basis="bar_index"`。
- **边界与异常**：`n=0/1/9` → `insufficient`（无 conclusion）；`n=10` → 出值无 CI；`n≥30` → 带 bootstrap CI；窗口不足 20 bar → `n_pending+1` 且不进分母；`P_buy>max20` → `q>1` **不截断**（decomp `share_gt_1` 计数）；买入侧不受 FIFO `data_error` 影响（照计）；`ctx.bars` 为空 → `blocked`；bar 密度不足 → `unreliable`（§1.5）。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="bootstrap"`、`min_trading_days=20`。
- **CI/统计细节**：bootstrap B=1000、percentile 法（无 BCa，M1 不做有偏修正）；seed=`sha256(inputs_hash)` 派生整数 PRNG（HITL-7）；重采样单位 = 买入事件（含其 q 值），`n<30` 不算 CI。
- **status_policy**：`upstream=blocked → blocked`；`bar_density<0.8 → unreliable`；`adjust=none` 且期间有疑似除权（跳空>±15% 且无对应分红流水）→ `unreliable`（P1 检测，M1 只写 assumptions）。
- **输出**：`value` unit=`ratio` precision=4（序列化 8 位小数）；decomposition：`q_mean_z[z,4]`、`coverage[ratio,8]`、`share_gt_1[ratio,8]`、`n_flat[count]`、`window_basis[str]`。
- **explain**：`conclusion`＝"本月买入价平均落在前 20 个交易日价格区间的 {value:.0%} 位置"（**禁裸术语**：不出现"分位/复权/β"）；`method`＝"用买入价在买入前 20 个交易日最高最低价区间中的相对位置衡量买点高低"；非 ok → `reason`+`what_to_do`。
- **drilldown**：`endpoint="/api/v1/metrics/timing.chase/drilldown"`、`params={period}`；items key=`trade_id`、`value`=q（ratio 8 位）、`unit="ratio"`、`label="该笔买入的追涨分位"`；`source_endpoints=["/api/v1/trades"]`。
- **golden**：`fx_timing_bars_daily_2y` + 手算表 `fixtures/expected/timing/chase_n12.yaml`（12 个买入事件，含平窗、>max、窗口不足各 1）；期望值来源＝手算表 + `naive_chase`；容差 `EPS_R=1bp`。
- **必杀变异**：① 窗口 `T−20..T−1` → `T−19..T`（含买入日）→ `test_chase_window_excludes_buy_day`；② 分母 `max−min` → `s20` → `test_chase_quantile_hand_table`；③ 均值分母 `n_valid` → `n_total` → `test_chase_mean_excludes_pending`；④ 常量 20→21 → `test_chase_window_constant_from_params`；⑤ `>` ↔ `>=` 在"窗口满 20 bar"判定 → `test_chase_requires_exactly_20_bars`。
- **HITL**：H-20（窗口基准/ddof）、H-22（复权缺失）、H-26（密度状态）。

### 2.2 `timing.fly` — 卖飞率

- 域 timing / `definition_version="1.0"` / `core_conclusion=false` / `mvp="M1"` / `frequency=sync` / `benchmark_required=true`
- **口径来源**（PRD A16 原文）：`z = (R_after − R_bench,h)/(σ_i日·√h) > 1.0`，`h∈{5,10,20}`；停牌>50%/退市/未走完剔除。
  **补充定义**：统计对象＝**卖出成交**（不是回合：一次卖出可能拆成多个 `round` 共享 `close_trade_id`，必须按 `close_trade_id` 去重）；`value` 用 `h=20`，decomposition 给 `h=5/10`；`R_bench,h` = **原始基准** h bar 收益（非 β 调整，见 H-21）。
- **公式（标度整数逐步）**：对每个卖出腿 s（`close_date`、`security_code`、`proceeds_units`）：
  1. `σ[8]` = 卖出日前 60 条 bar 的 `return_units` 样本标准差（ddof=1，Decimal sqrt 舍入 scale 8）；观测 <20 条收益 → 剔除
  2. `R_after[8] = Π(10^8+r_i)/10^8 − 1`（卖出后第 1..h 条 bar，逐条 round）
  3. `R_bench,h[8]` 同上，基准同序位置
  4. `denom = σ_d × sqrt(h)`（Decimal）；`z = (R_after − R_bench)/denom`
  5. 判定 `z > 1.0`（**严格大于**，比较在 scale 12 精度做，避免等于 1 的边界误判）
  6. `value[8] = round_half_up(#hit / n_valid)`；`weighted_share[8] = Σ(proceeds|hit)/Σproceeds`
  **手算例**（`h=5`，`σ=0.02`，`sqrt(5)=2.23606798`，`denom=0.04472136`，`R_bench,5=0.02`）：
  | 腿 | `R_after` | `R_after−R_bench` | `z`（scale 8） | 命中 |
  |---|---|---|---|---|
  | 1 | 0.10 | 0.08 | 1.78885438 | ✅ |
  | 2 | 0.01 | −0.01 | −0.22360680 | ❌ |
  | 3 | 0.06 | 0.04 | 0.89442719 | ❌ |
  | 4 | 0.00 | 0.00 | 0 | ❌ |
  → `value = 1/4 = 0.25000000`（`25000000`），`n=4`；第 5 腿未走完 → `n_pending=1`。
- **输入视图**：`ctx.rounds`（`status=="closed"`、`data_error==False`）+ `ctx.bars` + `ctx.benchmark`。
- **窗口/period 语义**：卖出腿 `close_date ∈ period`；前向窗口允许跨 `period_end`；`assumptions.window_basis="bar_index"`、`forward_horizon="bars"`。
- **边界与异常**：`h=5/10/20` 只来自 params（默认 20）；h 窗口内缺失 bar >50% 或后向无 bar（退市）→ 剔除计 `n_pending`；`σ` 观测不足 → 剔除计 `n_pending`；`z==1.0` → 不命中（严格 >）；缺基准/基准序列长度不齐 → `blocked`；`pending_ratio>0.3` → `insufficient`；`n<10` → 无 conclusion；同一 `close_trade_id` 多回合 → 只计 1 腿。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="wilson"`（比例型）、`min_trading_days=20`。
- **CI/统计细节**：Wilson 95%（z=1.959963985 常量进 `core/metrics/policy.py`，禁散落）；单侧不做；`n<30` 不算 CI；`pending_ratio` 只影响 status，不进 CI 分母。
- **status_policy**：`benchmark 缺失 → blocked`；`bar_density<0.8 → unreliable`；`pending_ratio>0.3 → insufficient`；`upstream rounds blocked → blocked`。
- **输出**：`value` unit=`ratio` precision=4；decomposition：`fly_share_h5[ratio,8]`、`fly_share_h10[ratio,8]`、`weighted_share_h20[ratio,8]`、`z_median[z,4]`、`n_pending[count]`。
- **explain**：`conclusion`＝"卖出后有 {value:.1%} 的情况，这只票明显跑赢了同期大盘"；`method`＝"比较卖出后一段时间个股涨幅与同期大盘涨幅，再用这只票平时的日波动折算成倍数，超过 1 倍算卖飞"；非 ok → `reason/what_to_do`（如"这段时间的行情数据不够，先按月看"）。
- **drilldown**：`endpoint="/api/v1/metrics/timing.fly/drilldown"`、`params={period,h}`；items key=`round_id`（+`event_date`=`close_date`）、`value`=z（z 4 位）、`unit="z"`、`label="该笔卖出的卖飞倍数"`；`source_endpoints=["/api/v1/rounds"]`。
- **golden**：`fx_timing_bars_daily_2y` + `fixtures/expected/timing/fly_h20.yaml`（12 腿：含 1 腿 `z==1.00000000` 边界、1 腿停牌、1 腿未走完、1 腿退市）；容差：占比 1bp、z 1e-4。
- **必杀变异**：① `z>1.0` → `z>=1.0`（z=1 边界腿）→ `test_fly_strict_threshold_z_equals_one`；② σ 分母 ddof `n`→`n−1` → `test_fly_sigma_uses_sample_std`；③ `√h` → `h` → `test_fly_scale_sqrt_h`；④ 未走完腿计入分母 → `test_fly_pending_excluded_from_denominator`；⑤ 停牌剔除规则反转 → `test_fly_suspended_leg_dropped`。
- **HITL**：H-21（原始 vs β 调整基准、加权口径、σ 除数）、H-20（bar_index）、H-26（密度状态）。

### 2.3 `timing.beta` — 基准超额（β/α/t/IR/TE + 算术与几何超额）

- 域 timing / `definition_version="1.0"` / `core_conclusion=true` / `mvp="M1"` / `frequency=sync` / `benchmark_required=true`
- **口径来源**（PRD A23 原文）：默认沪深300全收益；同起点、同日历、全收益；算术与几何并列；β、α、t、IR。
  **补充定义**：`r_p` = `review.asset_daily.daily_return_units`（逐自然日、现金流调整 `daily_pnl=total−cashflow−prev_total`，`assets.py:312-329`）；`r_b` = 基准同日收益（bar 序列，缺失日剔除）；**估计窗口 = `[period_end − 60 交易日, period_end]`**（跨期回看，H-24）；`rf` 见 §1.4。
- **公式（标度整数逐步；累加器 int，输出 Decimal 舍入）**：x=`r_b`、y=`r_p`（均 scale 8）：
  1. `n, Σx, Σy, Σxx, Σyy, Σxy` 全 int（`xx/xy` scale 16）
  2. `Sxx = n·Σxx − (Σx)²`、`Sxy = n·Σxy − Σx·Σy`、`Syy = n·Σyy − (Σy)²`（均 scale 16）
  3. `β[8] = round_half_up(Sxy × 10^8 / Sxx)`；`Sxx==0`（基准恒定）→ `blocked`
  4. `x̄=Σx/n`、`ȳ=Σy/n`（Decimal）；`rf_d=(1+rf_a)^(1/244)−1`
  5. `α_d = ȳ − rf_d − β_d·(x̄ − rf_d)`；`α_ann[8] = round(α_d × 244)`
  6. `SSR = (Syy − Sxy²/Sxx)/n²`；`s² = SSR/(n−2)`；`SE(α)=sqrt(s²·(1/n + x̄²/(Sxx/n²)))`；`t[4]=round(α_d/SE,4)`
  7. `excess_t=r_p−r_b`；`TE[8]=stdev(excess,ddof=1)×√244`；`IR[8]=mean(excess)/stdev(excess)×√244`
  8. `excess_arith[8]=mean(excess)×244`；`excess_geo[8]=((1+R_p)/(1+R_b))^(244/n)−1`
  **手算例**（n=6，`A=244`，`rf_a=1.5%`→`rf_d=0.00006102077`；序列 `r_b(%)=[0.40,−0.30,0.90,0.20,−0.60,0.70]`、`r_p(%)=[0.55,−0.20,1.10,0.30,−0.85,0.60]`）：
  | 量 | 值（Decimal 校验后） |
  |---|---|
  | `Σx,Σy`（scale 8） | `1_300_000` / `1_500_000`；`x̄=216667`、`ȳ=250000` |
  | `β` | `1.15984016` |
  | `α_d` | `−0.0000032334` → `α_ann = −0.00078896`（−0.079%） |
  | `SE(α)` / `t` | `0.00071753` / `−0.0045` |
  | `TE` / `IR` | `0.02690477` / `3.0230079` |
  | 超额算术 / 几何（年化） | `0.08133333` / `0.08302747` |
  （该 6 点序列仅作公式链校验；真实计算受 `min_trading_days=60` 门禁，`n=6` 时必然 `insufficient`。）
- **输入视图**：`ctx.asset_daily`（`daily_return_units` 非 NULL 且 `status!=blocked`）+ `ctx.benchmark`；无上游指标依赖（`/nav` summary 的 `beta/alpha_annual/...` 必须复用同一 core 函数，见 H-19）。
- **窗口/period 语义**：β/α/TE/IR 均用**同一 60 交易日回看窗口**；`period` 只决定窗口末端；`lookback_days` 计入 `inputs_hash`；`assumptions{benchmark_id, rf_rate, A:244, nav_source:"asset_daily"}`。
- **边界与异常**：`n<60` → `insufficient`（A26：日频 <60 交易日不出 IR；本文把 β 一并纳入，理由：8~59 点的 β 不稳）；`30≤n<60` 不出值（A26 门槛）、`n≥30` 才有 CI；缺基准 → `blocked`；`asset_build_state.state∈{stale,blocked}` → `stale`/`blocked`；`daily_return` 有 NULL 洞（`prev_total≤0`）→ 剔除并计 `n_missing`，若剔除后 <60 → `insufficient`；`|r_p|>1`（负市值/超卖）→ `unreliable`；`rf_a<=0` → `VALIDATION_ERROR`（禁止默认 0）。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`min_trading_days=60`、`ci="bootstrap"`。
- **CI/统计细节**：成对重采样 `(r_p,r_b)` B=1000，percentile；β 与 α 共用同一次重采样（保证 CI 一致）；t 值不做 CI（本身就是检验统计量，`t` 4 位小数）；seed=`sha256(inputs_hash)`；`n<30` 不算 CI，`n<60` 不出值。
- **status_policy**：`benchmark 缺失 → blocked`；`asset build blocked → blocked`、`stale → stale`、`degraded → unreliable`；`bar_density(基准窗口)<0.8 → unreliable`；`n<60 → insufficient`。
- **输出**：`value` unit=`ratio` precision=4（β）；decomposition：`alpha_annual[ratio,8]`、`alpha_t[z,4]`、`ir[ratio,8]`、`tracking_error[ratio,8]`、`excess_arith[ratio,8]`、`excess_geo[ratio,8]`、`n_obs[count]`、`n_missing[count]`、`rf_rate[ratio,8]`、`a_base[count]`。
- **explain**：`conclusion`＝"你的组合跟随大盘波动的放大倍数约 {β:.2f}；扣除大盘与资金利率后年化多赚/少赚 {α:.1%}"（**禁裸术语**：不出现"β/α/IR/夏普/回归"）；`method`＝"把每天组合涨跌和沪深300涨跌放在一起做线性拟合，斜率是跟随倍数、截距是超额收益"；非 ok → `reason/what_to_do`（如"这段时间基准行情不足，先只看净值"）。
- **drilldown**：`endpoint="/api/v1/metrics/timing.beta/drilldown"`、`params={period,benchmark}`；items key=`event_date`、`value`=`r_p−r_b`（ratio 8 位）、`unit="ratio"`、`label="当日比大盘多/少的涨跌"`；`source_endpoints=["/api/v1/nav","/api/v1/assets"]`。
- **golden**：`fx_dd_chain_60`（60 交易日，见 2.4）+ `fx_timing_bars_daily_2y` 基准段 + `fixtures/expected/timing/beta_n60.yaml`；期望值＝手算表（前 6 点如上行）+ `naive_ols` 全序列；容差：β/α/TE/IR/超额 1bp、t 1e-4。
- **必杀变异**：① `Sxy/Sxx` → `Sxx/Sxy` → `test_beta_not_reciprocal`；② `A=244` → `252` → `test_annualization_base_244`；③ α 漏减 `rf_d` 或漏乘 `β` → `test_alpha_uses_rf_and_beta`；④ `TE` 用 `stdev(r_p)` 而非 `stdev(r_p−r_b)` → `test_te_uses_excess_returns`；⑤ 几何超额退化为算术 → `test_excess_geometric_differs_from_arithmetic`；⑥ `min_trading_days` 60→0（月频点也能算）→ `test_beta_blocked_when_window_short`。
- **HITL**：H-19（id 与 `/nav` 双写）、H-23（rf 日化/年化基数）、H-24（`r_p` 口径与 n 计数）、H-25（A26 计数口径）。

### 2.4 `timing.drawdown` — 最大回撤族

- 域 timing / `definition_version="1.0"` / `core_conclusion=true` / `mvp="M1"` / `frequency=sync` / `benchmark_required=false`
- **口径来源**（PRD A18 原文）：`MDD` + 持续期/恢复期/水下占比/Ulcer/Calmar。
  **补充定义**（A18 未定义，本设计定，见 H-25）：序列取 `asset_daily` 中 `status!="blocked"` 且 `daily_return_units IS NOT NULL` 的行（**逐自然日**，`assets.py:312-329`，现金流已剔除）；`n_obs` = 该行数；`min_trading_days=60` 按其中 `weekday<5` 的行数计。
- **公式（标度整数逐步）**：`nav_0=10^8`（scale 8）
  1. `nav_t = round_half_up(nav_{t-1}·(10^8+r_t)/10^8)`（int）
  2. `peak_t = max(peak_{t-1}, nav_t)`；`dd_t[8] = round((peak_t−nav_t)·10^8/peak_t)`
  3. `MDD[8] = max dd_t`
  4. 持续期（交易日）＝峰 index → 最大回撤谷 index 的差；恢复期＝谷 → 首次 `dd==0` 的差；区间末仍 `dd>0` → 恢复期 null 且 `recovery_censored=true`
  5. 水下占比 `[8] = #{t: dd_t>0}/n_obs`
  6. `Ulcer[8] = sqrt(Σ_{t=1..n} dd_t² / n_obs)`（`dd` 先转 scale 8 小数再平方）
  7. `TWR[8]=nav_n−1`；年化（A5：365/自然日）`ann=(1+TWR)^(365/自然日数)−1`；`Calmar[8]=ann/|MDD|`，`MDD==0` → `None`+`unreliable`
  **手算例**（`r=[+0.40%,−0.80%,+1.00%,−0.20%,+0.40%]`，6 个连续交易日、自然日跨 8 天）：
  `nav=[1.00000000, 1.00400000, 0.99596800, 1.00592768, 1.00391582, 1.00793149]`；
  `dd=[0, 0, 0.00800000, 0, 0.00200000, 0]` → **MDD=`0.00800000`**（`800000`）；
  最长回撤：峰 idx1 → 谷 idx2 → 恢复 idx3，**持续期 1 交易日、恢复期 1 交易日**；
  水下占比 `2/5=0.40000000`；**Ulcer=`0.00368782`**；`TWR=0.00793149`；`ann(365/8)=0.43396965`；**Calmar=`54.24620681`**（5 点示例年化被放大，仅校验公式链；golden 用 n≥60）。
- **输入视图**：`ctx.asset_daily` + `ctx.asset_build_state`；无基准、无 bars 直接依赖。
- **窗口/period 语义**：period 内行；链起点 = period 首日 `nav=1`；不回看期外（MDD 只看期内）；`assumptions.nav_source="asset_daily"`、`a_base=244`、`calendar="weekday_approx"`。
- **边界与异常**：`n<60` → `insufficient`（A26）；期内含 `status=="blocked"` 行（缺行情硬阻断，`assets.py:281-297`）→ **链断裂 → `blocked`**；含 `daily_return` NULL 洞（`prev_total≤0`）→ 剔除 + `n_missing`，剔除后 <60 → `insufficient`，否则 `unreliable`；`MDD==0`（单调上涨）→ 值 `0` 正常、`Calmar=None`+`unreliable`；`|r|>1` → `unreliable`（负市值/超卖，见 §3.4）；重复 `trade_date` → `blocked`（数据完整性）；`build.state!="current"` → `stale`。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`min_trading_days=60`、`ci="bootstrap"`。
- **CI/统计细节**：对 `r_t` 有放回重采样 B=1000、percentile；**不做块 bootstrap**（M1 接受偏差，写进 `method` 与 `assumptions`）；MDD 的 CI 只作参考、不参与 status 判定；`n<30` 不算 CI；seed=`sha256(inputs_hash)`。
- **status_policy**：见 §3.4 传播表。
- **输出**：`value` unit=`ratio` precision=4（MDD）；decomposition：`mdd_duration_td[count]`、`mdd_recovery_td[count|null]`、`recovery_censored[bool]`、`underwater_ratio[ratio,8]`、`ulcer[ratio,8]`、`calmar[ratio,8|null]`、`twr[ratio,8]`、`ann_twr[ratio,8]`、`n_obs[count]`、`n_missing[count]`、`n_trading_days[count]`。
- **explain**：`conclusion`＝"这段时间最大一次回撤是 {MDD:.1%}，从最高点跌下去用了 {dur} 天、又用了 {rec} 天回到原高点"；`method`＝"用每日资产变化连乘出净值曲线，记录每次从最高点回落的最大幅度"（**禁裸术语**：不出现"MDD/Ulcer/Calmar/TWR"）；非 ok → `reason/what_to_do`（blocked："这段时间有几天缺少行情，回撤曲线会断，先补行情再算"）。
- **drilldown**：`endpoint="/api/v1/metrics/timing.drawdown/drilldown"`、`params={period}`；items key=`event_date`、`value`=dd（ratio 8 位）、`unit="ratio"`、`label="当日距区间最高点的回撤"`；`source_endpoints=["/api/v1/assets"]`。
- **golden**：`fx_dd_chain_60`（60 交易日：一段回撤-恢复 + 一段未恢复 + 1 个等值峰）+ `fx_dd_blocked_hole` + `fixtures/expected/timing/drawdown_n60.yaml`；容差：MDD/水下/Ulcer/Calmar 1bp、持续/恢复期整数精确。
- **必杀变异**：① `peak` 更新 `<` → `<=`（等值峰处多记一天回撤）→ `test_drawdown_equal_peak_not_drawdown`；② `MDD=max` → 取末期回撤 → `test_mdd_is_maximum_not_last`；③ 水下分母 `n_obs` → `n_obs−1` → `test_underwater_denominator`；④ 未恢复时恢复期写 0 而非 null → `test_recovery_right_censored`；⑤ `Calmar` 分母漏 `|.|`/少乘 100 → `test_calmar_units`；⑥ blocked 行当 0 收益拼接 → `test_drawdown_blocked_breaks_chain`。
- **HITL**：H-25（A18 未定义项）、H-26（`|r|>1` 状态）、HITL-12（数据源）。

## 3. 数据可用性分析（HITL-2/3 的技术依据）

### 3.1 硬数据：现状盘点（全部可复算/可核对）

| # | 事实 | 数值 | 证据 |
|---|---|---|---|
| F1 | replay 行情序列数 / 每条 bar 数 | 4 条（`hs300_tr`/600519/000001/300750）× **9 bar** | `fixtures/market/quotes.json`；`fixtures.py:186-207` |
| F2 | 日期跨度 | 2026-01-30 → 2026-09-30 = 243 自然日 = **174 个周一~周五日** | `_MARKET_DATES` |
| F3 | 相邻 bar 间隔 | **20~23 个工作日**（月频） | 计算 |
| F4 | 任意 20 交易日前向窗口内 bar 数 | 分布 `{0 bar: 14 天, 1 bar: 160 天}`；**含 20 条 bar 的天数 = 0/174** | 计算 |
| F5 | h 前向窗口"含≥1 bar"的天数占比 | h=5: 40/174=23%；h=10: 80/174=46%；h=20: 160/174=92%；**含 ≥h bar 的天数均为 0** | 计算 |
| F6 | 每序列可得日收益观测数 | **8**（60 日门槛的 13.3%；250 日 β 调整基准的 3.2%） | F1 |
| F7 | `MarketDataSource` 能力 | replay `supports_daily_bars=True` 但数据月频；`supports_adjusted=False` | `market.py:196-203` |
| F8 | live 标的校验 | 只收 `^\d{6}$` 且 A 股前缀集合 → **`hs300_tr` 被拒**（`NOT_FOUND`+`market_failure=not_supported`） | `market_live.py:67-74,171-194` |
| F9 | live 复权 | `ADJUST=""`（v1 只不复权，`Literal[""]` 显式拒绝 qfq/hfq） | `market_live.py:57-59`；live 设计 §3 |
| F10 | `BenchmarkProvider`（EP-5） | 文档有（`Extensibility.md:48,68`），**代码零实现**（全仓无该类） | grep |
| F11 | 组合日收益来源 | `asset_daily` 逐**自然日**行，`daily_return_units` 仅 `prev_total>0` 时非 NULL | `models.py:207-257`；`assets.py:312-329` |
| F12 | demo 账单时间跨度 | 交易日 2026-04-01 → 2026-06-30；asset 终点 = `clock.today()=2026-10-01` → **184 自然日 / 132 个工作日行** | `fixtures.py:49,66-77`；`cli.py:45` |
| F13 | demo 账单的持仓结构 | `_SECURITIES` 5 只 + 摘要按 `i%5` 轮转 → 只有 600519（买）/000001（卖）改变持仓；601318/000858 永不持仓，故**不产生缺行情 blocked**；000001 无买入行 → **超卖 → `fifo_error` → 全表 `degraded`** | `fixtures.py:38-46`；`assets.py:304-310` |
| F14 | 回撤族数据源现状 | 132 个工作日行 ≥ 60 → 数值**可算**；但价格阶梯是月频 carry-forward（`PRICE_LOOKBACK=45` 只负责"找到最近收盘"），日内波动不可见 | `valuation.py:39,86-119`；`assets.py:633` |
| F15 | 测试资产现状 | `backend/tests/oracle/` 只有 `naive_fifo.py`（Testing §9.3 提到的 `naive_nav/naive_xirr` 未落地）；`fixtures/expected/` **不存在** | `ls` |

**边际发现（需实现阶段用真实 `compute_daily_valuations` 复验，非本文件结论）**：按 F13 用 Python 复刻 `_build_rows(seed=7)` + 资产推演口径，得到 181 个观测中 90 个日收益非零、`|r|` 最大 3.21、累计 `TWR=−8.55`、`MDD=3.25` —— 即现有 demo 夹具的 **`资金余额` 列与摘要口径持仓不闭合**，回撤族会算出 >100% 的"回撤"。因此 **drawdown 的 golden 不得建在 demo seed 上**，必须用独立构造的 `fx_dd_chain_60`，且实现需加 `abs(r)<=1` 域校验（§2.4 边界），否则该指标会输出不可解释的数字。

### 3.2 逐指标：需要的窗口/样本 vs replay 可提供

| 指标 | 需要 | replay 现状 | 覆盖率 | 结论 |
|---|---|---|---|---|
| `timing.chase` | 每个买入事件前 **20 条 bar** + `P_buy` | demo 账单 5 只标的里 3 只有日线序列；窗口内最多 **1** 条 bar（F4） | **0/174 天可满足**（1/20=5% 密度） | `n_valid=0` → `insufficient`（无 conclusion）；窗口不足还会计 `n_pending` |
| `timing.fly` | 卖出腿 × (`σ`=前 60 bar 日收益 + 后 h=20 bar + 基准同 h bar) | 每序列仅 8 个收益（F6）；基准仅 8 个月频点；h=5 时仅 23% 的天有前向 bar | 组合覆盖率 ≈ 0；停牌/未走完剔除后更少 | `n=0` → `insufficient`；**缺基准在 live 也成立**（F8/F10） |
| `timing.beta` | **60 交易日**成对日收益 + 基准日收益 | 组合侧有（`asset_daily` 132 个工作日行）；基准侧**8** 个月频点；对齐后成对样本 ≤8 | **8/60 = 13.3%** → 违反 A26 60 交易日门槛 | `insufficient`（A26 不出 IR）；β 亦不出 |
| `timing.drawdown` | ≥60 个非 NULL `daily_return`（其中 `weekday<5`） | 132 个工作日行、无 blocked（F12/F13）；但全表 `degraded` 且序列被月频估值污染 | **132/60 = 220%** → 门槛达标 | 数值**能算**（`sample_status=ok`），但 `status` 必须 `unreliable`（F13/F14 + §3.4） |

**A26 门槛结论**：`beta`/`fly` 在 replay 与 live **都**不满足 60 交易日门槛；`drawdown` 满足；`chase` 的 20 日窗口在 9 个月频点下覆盖率 = 0（F4 的 160 天里窗口内只有 1 条 bar，`max−min` 恒为 0 → 全部事件被 `den` 剔除）。**验收剧本**：二期 `make verify-p2` 第 1 步（DEV-PLAN §3.1）的"四数字卡"里 timing 四张卡会有 **3 张空/占位**、1 张（回撤）有数；三期 §4.1 的 `/trends` 只对 `timing.drawdown` 有 points，其余无点（不会崩，但趋势图空）。

### 3.3 三个可选方案与推荐

| 方案 | 做法 | 代价/人日 | 收益 | 风险 |
|---|---|---|---|---|
| **A（推荐）扩确定性日线夹具** | `generate_market_quotes()` 改出日频（**2 年 = 488 bar/序列**），覆盖 `hs300_tr` + 600519/000001/300750（demo 实际持仓）＋ 保留月频字段兼容；`schema_version`/`provenance` bump 到 `deterministic-fixtures/v2`；seed-demo 同步重写；replay 声明 `supports_daily_bars=true`（已为 true，只补数据密度） | **5.5**（§5 明细） | 4 个指标全部可算；golden/CI 可离线确定；A26 达标；验收剧本二期 4 张卡有数 | `quotes.json` 从 4.3KB 增到约 1MB；Period2-04 需回归（市场夹具格式） |
| B live-only | `MARKET_MODE=live` 时才出 timing 指标，replay 一律 `blocked` | **0.3** | 不需要夹具 | `hs300_tr` 被 `validate_symbols` 拒（F8）→ β/α/IR **恒 blocked**；CI 无法 golden（依赖网络）；与 DEV-PLAN §3.4"replay 固定快照为准"冲突 |
| C 全标记 blocked（设计先行） | `chase/fly/beta` 恒 `blocked`，只实现 `drawdown`（吃 `asset_daily`） | **1.6**（T1 的 drawdown 部分 + T2） | 最快落地；FR-R8 状态语义完整 | FR-R5 验收"timing 全部指标含 `value/n/ci/...`"无法通过（3 项无 value）；二期看板 timing 卡空 3/4 |
| **A′（= C 起步 + A 补齐）** | 先交付 `drawdown`（不依赖日线）+ 域骨架/门禁，再补日线夹具上 `chase/fly/beta` | **1.6 + 3.9**（分两批，合计 5.5） | 先让二期有数、后补齐；HITL-1 的"core 先行"也满足 | 合计 5.5 仍超 3.5 预算 → 触发 HITL-6（建议拆 07b） |

**推荐：A′**。理由：① `drawdown` 的数据前提已在 Period2-16 落库（F12），不应等日线；② `chase/fly/beta` 没有日线就是**数学上不可算**（不是"精度差"），方案 B 在 live 下也缺基准，只有补夹具能解决；③ 补夹具是**一次性**投入，同时服务 `perf`/`selection` 域（BHAR 20/60 同样要日线，见 brief #16/#17），摊薄后成本低于各部分另建。

### 3.4 `drawdown` 数据源选择（HITL-12）与 blocked/stale 传播

| 候选数据源 | 结论 | 理由 |
|---|---|---|
| `review.asset_daily.daily_return_units`（**采纳**） | 逐自然日、现金流调整、已落库、可增量 | `assets.py:312-329` 的 `daily_pnl=total−cashflow−prev_total` 即"剔除出入金后的日收益"，与 A5 的 `r_t` 同口径 |
| `review.nav_daily`（不采纳） | 按月稀疏、由 `GET /nav` 隐式写入 | 见 `2026-10-25-asset-daily-table.md` §1.1 |
| 临时重算（不采纳） | 违反"指标层不打行情"、每次请求 O(全历史) | Architecture §11 物化要求 |

状态传播（`compute()` 不写 status，这里定义的是 `status_policy` 声明，由框架执行）：

| 上游事实 | status | sample_status | 说明 |
|---|---|---|---|
| `asset_build_state.state=="blocked"`（无现金锚点，整表无行） | `blocked` | `blocked` | 无 value |
| `asset_build_state.state=="stale"`（指纹/水位线落后） | `stale` | 保持 | 可返回旧值 + reason |
| 期内存在 `status=="blocked"` 行（缺行情） | `blocked` | `blocked` | 链断裂，**不得**跳行拼接 |
| 期内 `daily_return` 有 NULL 洞 | `unreliable` | 若剔除后 <60 → `insufficient` | `n_missing` 落 decomp |
| 任一 `abs(daily_return)>1`（即日收益绝对值 >100%，负市值/超卖，F13） | `unreliable` | 保持 | M1 的 demo 夹具会命中，故 golden 不用 demo |
| `n_trading_days<60` | `ok`（值仍在，按 HITL-8 取向；与 A26 原文『不出回撤』冲突，见 H-25） | `insufficient` | A26 / HITL-8 |
| 同 `trade_date` 重复行 | `blocked` | `blocked` | 数据完整性违约 |

## 4. 择时域测试与 golden 设计

### 4.1 夹具清单（新增，全部确定性、无 wall-clock）

| fixture id | 内容 | 覆盖边界 |
|---|---|---|
| `fx_timing_bars_daily_2y` | 4 序列 × 488 日线（2×A=244 个交易日；日期网格＝周一~周五、无节假日，含 `hs300_tr`）；固定 drifts，无随机 | 正常计算、跨期 lookback |
| `fx_timing_suspend` | 某标的中段停牌 6/12 个交易日（bar 缺失）+ 一段"缺 >50%" | fly 停牌剔除、chase 窗口不足 |
| `fx_timing_tail_open` | 夹具尾部只到 `period_end + 8 bar`（h=20 未走完） | `n_pending`、`pending_window` |
| `fx_timing_window_short` | 只有 12 条 bar 的标的 | chase `n_pending`、`insufficient` |
| `fx_timing_no_bench` | 不含 `hs300_tr` 的夹具 | fly/beta `blocked`（缺基准） |
| `fx_timing_missing_code` | 持有 `999999`（无序列） | `bar_density=0 → blocked` |
| `fx_dd_chain_60` | 60+ 交易日 `daily_return_units` 手算序列（回撤-恢复 + 未恢复 + 等值峰 + 单调段） | drawdown 全部成员、右截尾、MDD=0 |
| `fx_dd_blocked_hole` | 含 1 行 `status=blocked` + 1 行 `daily_return=NULL` | `blocked` vs `unreliable` 传播 |
| `fx_dd_flat` | 60 行全 `r=0` | `MDD=0`、`Calmar=None`+`unreliable` |

### 4.2 独立 oracle（`backend/tests/oracle/naive_timing.py`，新文件）

- 原则（Testing §9.3）：可读性优先、与被测代码零共享、`Decimal` 全精度、**不 import 生产模块**（import-linter 合约 C7）。
- 函数：`naive_quantile(closes, p_buy)`、`naive_z(...)`、`naive_fly(legs, bars, bench, h)`、`naive_ols(r_p, r_b, rf_a, A)`（返回 β/α/t/IR/TE/arithmetic/geometric）、`naive_drawdown(rets)`（三重循环暴力：峰-谷-恢复）。
- 三方比对：手算表 `fixtures/expected/timing/*.yaml`（人工，贴 diff 流程见 Testing §12.3）↔ oracle ↔ 生产实现；**任一方不一致即失败**，禁止用生产实现生成期望值。

### 4.3 容差

| 输出 | 容差 | 依据 |
|---|---|---|
| 比率型（占比/β/MDD/水下/Ulcer/TE/超额） | `EPS_R = 1bp`（`1e-4`） | `constants.py:11` |
| z / t | `1e-4`（输出 4 位） | brief §2.2 |
| 交易日/计数（持续期、恢复期、n） | 精确整数 | — |
| Calmar | 1bp 或相对 1e-4（取严） | 年化放大 |

### 4.4 必杀变异算子汇总（mutmut 只跑 `stock_core/metrics/timing/**`，门槛 ≥80%，HITL-16）

| 指标 | 变异 | 杀手测试 |
|---|---|---|
| chase | 窗口含买入日 / 分母换 σ / 分母 n / 常量 20 / 窗口满 20 的 `>`↔`>=` | `test_chase_window_excludes_buy_day`、`test_chase_quantile_hand_table`、`test_chase_mean_excludes_pending`、`test_chase_window_constant_from_params`、`test_chase_requires_exactly_20_bars` |
| fly | `z>1`↔`>=1` / σ ddof / `√h`→`h` / pending 进分母 / 停牌规则反转 | `test_fly_strict_threshold_z_equals_one`、`test_fly_sigma_uses_sample_std`、`test_fly_scale_sqrt_h`、`test_fly_pending_excluded_from_denominator`、`test_fly_suspended_leg_dropped` |
| beta | 协方差倒数 / A 244→252 / α 漏 rf 或漏 β / TE 用 r_p / 几何→算术 / 门槛 60→0 | `test_beta_not_reciprocal`、`test_annualization_base_244`、`test_alpha_uses_rf_and_beta`、`test_te_uses_excess_returns`、`test_excess_geometric_differs_from_arithmetic`、`test_beta_blocked_when_window_short` |
| drawdown | 峰 `<`↔`<=` / max→末期 / 水下分母 / 恢复期右截尾 / Calmar 单位 / blocked 当 0 收益 | `test_drawdown_equal_peak_not_drawdown`、`test_mdd_is_maximum_not_last`、`test_underwater_denominator`、`test_recovery_right_censored`、`test_calmar_units`、`test_drawdown_blocked_breaks_chain` |

## 5. 实现顺序与估时（人日，0.1 粒度）

| 步 | 内容 | 人日 | 依赖 |
|---|---|---|---|
| T1 | `core/metrics/timing/{window,ols,drawdown}.py` 纯函数（标度整数；`window` 含 bar_density） | 1.6 | — |
| T2 | `drawdown` def + golden + 传播测试（不依赖日线，先交付） | 0.8 | T1、Period2-16 |
| T3 | 日线夹具生成器改造 + provenance/schema bump + `seed-demo` 同步 | 1.2 | HITL-2 拍板 |
| T4 | `chase`/`fly`/`beta` def（params_schema/explain/drilldown/status_policy） | 0.9 | T1、T3、HITL-1 |
| T5 | `naive_timing.py` oracle + 手算表 YAML + 三方比对测试 | 0.7 | T2/T4 |
| T6 | 变异配置与门禁接线（mutmut 路径 `stock_core`，HITL-16） | 0.3 | Testing §12.1 |
| **合计** | | **5.5** | 超 Period2-07 的 3.5 预算 → HITL-6（建议 T3/T4 拆 07b 或复用 selection 域夹具投入） |

## 6. 新增 HITL（续号，编号与其它 part 需 Lead 去重）

| 编号 | 问题 | 推荐取向 |
|---|---|---|
| H-19 | `metric_id` 不一致：brief 用 `timing.chase`/`timing.fly`，`Extensibility.md:198-233` 示例用 `timing.sold_too_early`/`timing.chase_quantile`；且 PRD FR-R3/API-Catalog NavSeries 声明的 summary 含 `beta/alpha_annual/t_value/tracking_error/ir`，但 `routers/nav.py:109` 写明 benchmark『本期暂不计算对比』、summary（`routers/nav.py:230-247`）只有 mwr/twr/n_days → 与 `timing.beta` 双写口径 | brief 名字为准；`/nav` summary 补 beta/α 时必须复用同一 core 函数，两处值相等（加一致性测试） |
| H-20 | 窗口基准：`bar_index`（第 i+h 条 bar）vs 真实交易日历；σ 用 60 bar 且 ddof=1 | `bar_index` + `assumptions.window_basis`；`calendar.py:12` 只做门禁复核 |
| H-21 | A16 细节：`R_bench,h` 用**原始**基准（A16 字面）还是 `Extensibility.md:207` 的 **β 调整**（该行注『前 250 日 OLS』）；占比加权 vs 不加权 | 原始 + 不加权为 value；β 调整/加权放 decomp（`params.bench_mode` 预留） |
| H-22 | A17 要求"复权序列对齐"，但 replay `supports_adjusted=False`、live `ADJUST=""`（F7/F9） | v1 不复权 + `assumptions.adjust="none"`；除权跳变检测→`unreliable`；复权因子另立任务 |
| H-23 | rf 日化公式（`(1+rf_a)^(1/244)−1` vs `rf_a/244`）与 α/Calmar 年化基数（`×244` vs A5 的 365/自然日） | 按 §1.4；HITL-14 只定了 rf 来源/默认值，未定换算 |
| H-24 | 组合日收益口径：`asset_daily.daily_return`（含现金/含费/逐自然日）vs 持仓加权收益；n 计数（自然日行 vs `weekday<5`） | `asset_daily`；n 用"非 NULL 且 `weekday<5`" |
| H-25 | A18 未定义：水下占比分母、持续期/恢复期定义、Ulcer 公式、Calmar 年化基数、未恢复的右截尾；且 A26『日频<60 交易日不出回撤』与 HITL-8『insufficient 仍返回 value』取向冲突 | 按 §2.4；右截尾写 null + `recovery_censored`；<60 返回 value 但无 CI/conclusion |
| H-26 | 行情密度不足的状态表达：`SampleStatus` 无 `low_coverage`（`enums.py:69-75`，禁加新枚举） | 用 `status=unreliable` + `reason`；`bar_density<0.8` 为阈值（本设计定的常量，需进 `core/metrics/policy.py`，禁硬编码） |
| H-27 | live 无基准源（F8/F10）时 FR-R5"缺 benchmark 时用默认并标注"→ 默认 `hs300_tr` 仍取不到数据，timing 域在 live 下整体 `blocked` | 人工决定：接受 live-timing 整体 blocked，或补 live 指数适配（EP-5）后才开 live |

## 给 Lead 的合并提示（≤15 行）

1. **硬结论**：replay 现状下只有 `timing.drawdown` 能算出数（132 工作日行 ≥ A26 的 60）；`chase`/`fly`/`beta` 必然 `insufficient`——chase 的 20 日窗口在 9 个月频点下覆盖率 **0/174 天**，fly/beta 的 60 日门槛只有 **8/60=13.3%**。
2. `beta` 在 live 下也 `blocked`（`hs300_tr` 被 `market_live.validate_symbols` 拒；`BenchmarkProvider` 只有文档没有代码）。
3. 最高风险 ①：不改夹具就必须接受 FR-R5 验收"timing 全指标含 value"打不过（HITL-2/3）；②：demo 账单的 `资金余额` 与持仓不闭合，会算出 >100% 的回撤，drawdown golden 必须独立构造（§3.1 边际发现，需实现阶段复验，勿直接引用为定论）；③：`β/α` 与 `/nav` summary 双写（H-19），口径漂移风险高。
4. 需要写的决策摘要条目：D1/D2/D3/D4（§0 表）+ 方案 A′ 与 5.5 人日拆分。
5. 必须人工拍板：HITL-2/3（夹具）、H-22（复权）、H-23（rf 日化）、H-24（r_p 口径）、H-19（id/双写）。
6. 新增 HITL 编号 **H-19~H-27（9 条）**，与 perf/behavior/selection part 可能撞号，请合并时统一去重（建议域前缀 `H-T1…` 或全局重排）。
7. 与其他域耦合：① `part_behavior_selection` 的 BHAR 20/60 同样要日线 → 夹具投入可摊薄，请合并预算口径；② `perf.fee_breakeven`/`perf.*` 不用 bars，与 timing 无耦合；③ 引擎侧需要新增 `ctx.bars`/`ctx.benchmark` 视图与 `bar_density` 门禁（Period2-05 契约清单，属 HITL-1 前置）。
