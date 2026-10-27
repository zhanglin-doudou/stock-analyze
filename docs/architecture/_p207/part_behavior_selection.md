# Period2-07 行为域 + 选股域 指标设计（part_behavior_selection）

> 范围：指标 #12–#18（behavior.de / turnover / averaging_down / hhi；selection.bhar_20 / bhar_60 / bhar_hit）。
> 状态：设计稿（待交叉评审 + 人工拍板）。`definition_version` 全量 `"1.0"`（HITL-5）。
> 约束：不改引擎契约（brief §2）、不写生产代码、不改既有文档；口径唯一来源 = PRD-v2 附录 A（沿用 v1.1）+ PRD-v1 FR-R4/FR-R6/§6。
> 所有公式落在 `core` 纯函数 + `review` 应用层纯构建器；金额/价格/数量/比率一律标度整数，禁 float。

## 0. 本域公共口径

### 0.1 数据视图（07 对 05 的字段建议名）
| 视图 | 内容 | 现有代码/来源 | 消费者 |
|---|---|---|---|
| `ctx.rounds` | `(id,status,open_date,close_date,pnl_units,cost_units,proceeds_units,dividend_units,holding_days,data_error)`；`status∈{closed,open,data_error}`（`models.py:42-43`，枚举列 text+应用层校验） | `models.py:49-98`、`application/rounds.py:22-51` | de、avgdown |
| `ctx.trades` | ledger `Trade` 全量（`trade_date≤as_of`），含 `category/quantity_units/price_units/amount_units/{commission,stamp_tax,other_fee}_units/cash_balance_units/line_no/id` | `ledger/models.py:155-186` | de 持仓重建、turnover、avgdown |
| `ctx.bars` | `{symbol: [(date, close_units)]}` 升序收盘阶梯 | `application/valuation.py:70-96`、`load_prices:138-173` | de/hhi/avgdown/bhar |
| `ctx.benchmark` | `benchmark_id` + `[(date, close_units)]`（默认 hs300_tr 全收益） | `platform/ports.py:106-113`、`ledger/fixtures.py:198` | bhar、avgdown fwd20 |
| `ctx.asset_daily` | `period` 内逐自然日 `AssetDaily`（cash/market_value/total/status） | `models.py:207-256` | turnover、hhi |
| `ctx.as_of/period/params` | `as_of=min(请求, market.as_of, asset build data_as_of)` | `platform/ports.py:131-134` | 全部 |

- **期末浮盈数据源（与 perf H-02 协调）**：`review.round` 只落回合与配对明细，**未落 open_lots**（`models.py:101-130,301-308`），无法从库表还原期末浮盈。优先消费 05 的 `ctx.positions_end{symbol, unrealized_pnl_units}`（`part_perf.md` H-02，口径 `市值−含费成本`）；视图不可用时，在 compute 内用 `build_rounds(trades_to_events(trades≤period_end)).open_lots` 现算（`fifo/engine.py:76`、`application/rebuild.py:98,338`，纯函数可单测）。**两条路径必须共用同一 `paper_pnl` 纯函数**，禁止口径分叉（否则 DE 与 A10 全样本胜率会给出不同浮盈）。
- 建议 05 把 `asset_build_state.last_status / data_as_of` 放进 `ctx.meta`，否则 HHI/换手的 `stale/blocked` 传播无法判定（本设计假定可用，缺失则按 blocked）。

### 0.2 标度与整数原语（新增 `stock_core/metrics/intmath.py` + `stock_core/stats/numeric.py`，落点对齐团队 D-07/D-16）
| 原语 | 定义 | 备注 |
|---|---|---|
| `hu(n,d)` | `q,r=divmod(n,d); q+(1 if 2r>=d else 0)`，`d>0` | 与 `values.py:48` 的 ROUND_HALF_UP 一致；负数时表现“半数向 +∞”（如 −1.5→−1），须单测锁定 |
| `ratio_units(n,d)` | `hu(n*10^8, d)` | 比率输出 scale=8 |
| `median_units(xs)` | 奇数取中位；偶数 `hu(a+b,2)` | 例：`[5e6,−7e6,5e6]→5e6`；`[−2,−1]→−1` |
| `pct_linear(sorted,q_num,q_den)` | 0-based 位置 `pos=(B−1)·q_num/q_den`；`i=pos//q_den`、`f=pos%q_den`；`f==0 → s[i]`，否则 `s[i]+hu(f·(s[i+1]−s[i]), q_den)` | 最近秩+线性插值（整数精确，避免越界）；团队 D-07。B=1000 时 2.5% 在 s[24]/s[25] 间、97.5% 在 s[974]/s[975] 间插值 |
| `xorshift64_star(seed)` | 纯整数 PRNG：`x^=x>>12; x^=x<<25; x^=x>>27; out=(x·0x2545F4914F6CDD1D)&(2^64−1)` | 用于 behavior.de 与 selection.bhar_* 的 bootstrap（团队 D-07 指定） |
| `cluster_mean_t(xs, groups)` | 事件日聚类稳健 t：`Var=G/(G−1)·SS/n²`；整数平方根 + 半进位到 4dp | 用于 selection.bhar_* 的 t 值 |

本文所有手算例均已用 `python3` + Decimal/整数 `hu` 复算校验（禁 float）。复用：`Money/Ratio`（`values.py:23-77,135-163`）、逐标的 floor `qty*close//10^8`（`valuation.py:118`，与 `total_market_value` 同口径，保证 Σ逐标的 = 聚合市值）。

### 0.3 持仓内存重建（HITL-13 核心设计；DE 期末浮盈 / turnover / HHI 共用）
现状：`review.asset_daily` 只有聚合值（`models.py:207-256`），无逐标的持仓明细表；`compute_daily_valuations`（`application/assets.py:211-355`）已逐日重放持仓/成本/现金，但只输出聚合市值。

**方案（不落库）**：新增 `stock_review/application/behavior_positions.py` 纯构建器（无 Session、无时钟），复用 `valuation` 原语：
1. `symbols = {code | 任一 holding-impact trade(≤period_end)}`（`HOLDING_CATEGORIES`，`valuation.py:30`）。
2. 取价：`load_prices(market, symbols, period_start−45d, period_end)`（`valuation.py:39,138`），得阶梯 + `market_causes`。
3. 从**账户首笔流水日**起按自然日重放到 `period_end`：逐日按稳定序 `(trade_date, line_no, id)` 应用 `holding_delta`，维护 `holdings{code:qty}`；现金/成本只在需要时重建（HHI 直接用 `asset_daily.cash_units`，避免复制期初锚点逻辑）。
4. 仅对 `[period_start, period_end]` 逐日估值：`mv_i = qty_i*close_on_or_before(ladder,code,d)//10^8`；任一持仓标的缺价 → 该日 `blocked`（`blocked_price_reason`，`valuation.py:128`）。
5. 交叉校验（每日）：`Σ mv_i + cash == asset_daily.total_assets_units` 且 `Σ mv_i == asset_daily.market_value_units`（0 分公差）。不符 → 该日 `unreliable` 计数，不进均值；**不得**静默取值。
6. 缓存/性能：不做跨请求缓存（引擎已物化 `MetricResult`，`scope_key+inputs_hash` 失效）；单次预算 p95 ≤300ms（NFR-10，`Architecture-v2.md:739-745`）：1 次 `get_daily_bars` + 1 次 trades 查询 + O(交易数 + 天数×标的数) 纯 int 运算；5 年账/100 标的 ≈ 20 万次乘除，Python 内 <50ms。
7. 缺行情传播：任一日缺价 → 该日不进统计；`used/period_days<50% → blocked`、`<90% → unreliable`（H-28）。上游 `asset_daily` 未构建 → `blocked`（沿用 HITL-12 语义）。

**HITL-13 取舍**：M1 采用内存重建（零迁移、零写放大、与 `asset_daily` 天然对账）；拒绝新增 `review.holding_daily` 表（迁移 + 写入量 + 与 rebuild 耦合），仅当压测超预算再立 Period3 任务。代价：无法直接分页历史持仓明细，下钻在请求内重算（≤300ms 预算内可行）。

### 0.4 period / as_of / lookback / 通用边界
| 项 | 规则 |
|---|---|
| period | `YYYY-MM` → 闭区间 `[YYYY-MM-01, 月末]`；行为域 realized 按 `close_date`、paper 按 `period_end` 快照；选股按 **买入日 cohort**（`buy_date∈period`，`PRD-v1.md:373`） |
| lookback | de/avgdown/hhi 需从账户首笔流水重放（`lookback="account_start"`）；最早 `trade_date` 必须进 `inputs_hash`（HITL-18 延伸）。BHAR 窗口可延伸到 `period_end` 之后，但止于 `as_of` |
| as_of | 只用 `trade_date≤as_of` 的 bars/基准/流水；`period_end>as_of` → 行为域 `blocked`（无期末快照）；BHAR 窗口未走完 → `n_pending` 不进分母 |
| n 门槛 | `n=0` → value=None/insufficient；`n∈{1,5,9}` → insufficient（有原始值无 conclusion）；`n∈{10..29}` → ok 无 CI；`n≥ci_min_n(=30)` → 附 CI。注：A26 字面“N<30 附 CI”与 `ci_min_n=30`/API-033 断言④冲突，按 HITL-8 取“n≥30 出 CI” |
| pnl=0 | 不计入胜/负两侧（只计 `n_zero`）；不得用 `>=`（变异重点） |
| min_trading_days | 仅 timing 的夏普/回撤/IR 使用（A26）；本域全部显式不启用 |
| explain 语言 | 所有 conclusion/method 提供中/英两套模板（API-034 ④ Accept-Language），数值与 code 不变 |
| data_error 回合 | 一律剔除并计 `n_data_error`；比率 `>5%` → `unreliable` |
| open 回合 | 不作为已实现事件；仅用其 lot 快照（H-27） |
| 除零 | 分母 0 → value=None + 明确 reason，禁返回 0（0 有语义，见各指标） |
| 极端值 | 分位/最大最小相等、全胜/全负、空仓、单标的 100% 集中均须有 golden |

### 0.5 本文件新增 HITL（续号，不占 HITL-1..18）

> 注：正文出现的「perf H-02」是 `part_perf.md` 的域内编号（`positions_end` 视图），与本文件 H-19…H-29 不冲突。
| 编号 | 问题 | 推荐取向（待拍板） |
|---|---|---|
| H-19 | compute 是纯函数且 `MetricValue` 无 CI 字段，DE 的 bootstrap 统计量无法由“通用均值型”框架 CI 复现 | `MetricValue.decomposition["_bootstrap"]` 传充分统计（观测码），引擎按 `def.id` 派发 `core.stats.numeric` 中的统计策略；公开响应剥离 `_` 前缀键。需 05 确认 |
| H-20 | PRD-v1 写“按回合重抽样”，但 PGR/PLR 分母含**期末持仓**（非回合） | 对“处置候选集（已实现回合+期末持仓）”联合重抽样；按证券聚类的 block bootstrap 作敏感性分析 |
| H-21 | `SamplePolicy.ci∈{wilson,bootstrap}` 无 “none”，HHI/换手/摊平不需要 CI | 允许 `ci="none"`（SamplePolicy 内枚举，非 13 字段变更），CI 键缺省 |
| H-22 | “摊平”PRD 只有名词，无量化定义 | 采用 #14 定义（加仓、含费均价、严格低于、深度） |
| H-23 | 换手率 PRD 只有“年化单边换手率”一句 | 采用 #13 定义（双边成交额/平均持仓市值×244/交易日） |
| H-24 | BHAR 事件日基准价（收盘 vs 成交价）与停牌/退市剔除阈值无 PRD 原文 | 事件日收盘为基准；覆盖率<50% 剔除停牌、尾部 5 日无 bar 判退市 |
| H-25 | “按事件日聚类”不覆盖相邻日重叠窗口 | 主口径事件日聚类；decomp 附 Newey-West(lag=h−1) 待拍板 |
| H-26 | 仓内 live 只落**不复权**日线（brief §1），PRD NFR-2 要求 hfq | M1 做“价格类 BHAR”+ 除权嫌疑日检测；API-038 断言改口径（HITL-4 延伸） |
| H-27 | “期末浮盈笔数”粒度（lot/证券/回合）与 open 回合部分卖出的已实现盈亏归属 | 按**证券持仓**计 1 笔；open 回合的已实现部分不计入已实现笔数，只进 decomp |
| H-28 | HHI 日均粒度（自然日 vs 交易日）、空仓日 HHI_eq 无定义、缺行情阈值 | 自然日；空仓日不进 HHI_eq 均值；覆盖率 <50% blocked / <90% unreliable |
| H-29 | 选股 cohort 的“买入事件”范围 | `category=="buy"` 为主口径；`rights/new_stock` 只进 decomp；`bonus` 不计 |

## 1. 行为域

### behavior.de — 处置效应（DE = PLR − PGR + bootstrap CI）
- 域 behavior / version 1.0 / core_conclusion=是 / mvp=M1 / frequency=sync（周期=月，参数 `period`）。
- **口径来源**：PRD-v2 A15“`DE = PLR − PGR`；bootstrap 95% CI；`DE>0` 为处置效应；持有天数差为辅助”（`PRD-v2.md:579`）；PRD-v1 `FR-R6 §6.1`“`PGR = 已实现盈利笔数/(已实现盈利笔数 + 期末浮盈笔数)`；`PLR = 已实现亏损笔数/(已实现亏损笔数 + 期末浮亏笔数)`；按回合重抽样 10000 次”（`PRD-v1.md:418-424`）；PRD-v1 A15 同式（`:650`）。**“笔”=回合**（FIFO 0→0 周期，`models.py:49-98`；不是卖出流水笔数）；PGR 的分母只与“纸面**盈**”配对、PLR 只与“纸面**亏**”配对，**禁止交叉配对**（交叉会使比例失去“卖出倾向”含义）。本文只补充“笔/持仓/舍入/抽样”的实现定义。
- **公式**（标度整数；`hu`=ROUND_HALF_UP）：
  ```
  G = #{r: r.status=="closed" ∧ r.close_date∈period ∧ pnl_units>0}
  L = #{r: 同上 ∧ pnl_units<0}                       # pnl==0 不计，计 n_zero
  paper_pnl(code) = Σ_{lot∈open_lots[code]} (lot.qty*close_units//10^8 − lot.cost_units)   # 浮盈不含已收股息
  g = #{code: paper_pnl>0}；l = #{code: paper_pnl<0}  # 按证券持仓 1 笔（H-27）
  PGR_units = hu(G*10^8, G+g)；PLR_units = hu(L*10^8, L+l)
  DE_units  = hu((L*(G+g) − G*(L+l))*10^8, (L+l)*(G+g))    # 精确有理数一次舍入，避免双舍入
  ```
  Decimal 等价：`DE = 1/(1+l/L) − 1/(1+g/G)`；手算例 `G=2,L=1,g=1,l=1` → `PGR=0.66666667`、`PLR=0.50000000`、`DE=−0.16666667`（0.5−2/3=−1/6）；decomp 两项分别舍入后会得 −16666667 恰同，但通用情况必须按精确式（见 golden 断言）。
- **期末持仓构造**：`fifo=build_rounds(trades_to_events(trades≤period_end))`；`open_lots` 逐 lot 估值（`types.py:109-119`），`close=close_on_or_before(ladder,code,period_end)`；任一只无价 → 该持仓不可定价 → `blocked`（分母不完整，不允许部分计算）。成本含费（`amount_units` 已折费，禁二次扣费）。**浮盈口径不含已收股息**（“浮盈”=未实现价格损益，且与 `asset_daily.unrealized_pnl_units`/perf H-02 同口径）；已实现口径按 A8 含股息——该不对称写入 `assumptions{realized_includes_dividend:true, paper_excludes_dividend:true}`，并在 decomp 给 `paper_dividend_excluded=true`。
- **输入视图**：`ctx.rounds`、`ctx.trades`、`ctx.bars`、`ctx.as_of`；依赖 `rounds`(dataset)+`trades`+`bars`；上游 blocked（bars 全局不可用）→ 本指标 blocked。**自证哨兵**：重建的 period 内 closed 回合笔数/pnl 之和须与 `ctx.rounds` 一致，否则 `unreliable`（防 rounds 物化与 trades 漂移导致分子分母不同源）。
- **窗口/period**：已实现按 `close_date∈[period_start,period_end]`；paper 固定 `period_end` 快照；`period_end>as_of` → blocked（0.4）。
- **边界**：`G+g==0` 或 `L+l==0` → PGR/PLR 无定义 → value=None + insufficient（“某一侧无处置机会”）；`n=0`→None；`n=9/10/29/30` 按 0.4；全胜/全负同上；`data_error>5%`→unreliable；open 回合部分卖出只进 `n_partial_realized`（H-27）。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="bootstrap"`、`B=1000`、percentile 95%（HITL-7：PRD-v1 写 10000、brief/团队 D-07 取 1000；B 变化按 `definition_version` 精度层处理）。`n = G+L+g+l`（处置候选总数）。
- **status_policy**：`upstream=blocked→blocked`；`capability=bars_missing→blocked`；`data_error_ratio>0.05→unreliable`；`period_end>as_of→blocked`；`ci` 不参与 status。
- **CI/统计细节**：观测码 `0=已卖盈,1=已卖亏,2=纸面盈,3=纸面亏`；B 次有放回重抽 `n` 个码，逐次重算 PGR/PLR/DE（任一 PGR/PLR 分母=0 的 replicate 丢弃）；`B_eff<500` → CI 缺省（insufficient 语义）。PRNG：`xorshift64_star`（`x^=x>>12; x^=x<<25; x^=x>>27; out=(x·0x2545F4914F6CDD1D)&(2^64−1)`），`index_below(n)` 用拒绝采样（`limit=2^64−2^64%n`，>limit 重抽）消除模偏。seed=`int.from_bytes(sha256(inputs_hash.encode()).digest()[:8],"big")`（团队 D-07；H-19：inputs_hash 须在 CI 阶段可得；inputs_hash 已含 metric_id，天然按指标区分）。分位用 `pct_linear`（0.2，最近秩+线性插值）：B=1000 时 2.5% 插值 s[24]/s[25]、97.5% 插值 s[974]/s[975]。**禁 numpy/random/float**（与 `Testing.md:327` 的 U2 `np.random.Generator` 约定冲突，一并记 HITL-7）。采用 percentile 法、不做 BCa；若人工改选 BCa 需 B≥2000 并重开 HITL-7。
- **输出**：value=DE（ratio，precision 8；符号：>0 有处置效应）；decomposition：`pgr`/`plr`(ratio)、`holding_days_diff`(days：亏损回合均持−盈利回合均持，整数半进位；FIFO 扭曲见 A15 备注)、`n_sold_gain/n_sold_loss/n_paper_gain/n_paper_loss/n_zero/n_data_error/n_partial_realized/n_missing_price`(count)。CI 内部标度 1e-8。
- **explain**：conclusion=“你有「赚一点就跑、亏了死扛」的倾向：卖出赚钱股票的比例比卖出亏钱股票低 {x} 个百分点”（`|DE|`；符号决定文案方向；`sample_status!=ok → conclusion=null + reason/what_to_do`）；method=“比较已卖出盈利/亏损与期末浮盈/浮亏持仓的比例差（95% 区间）；区间不含 0 才下结论”。**禁裸术语 DE/PGR/PLR**。
- **drilldown**：`GET /metrics/behavior.de/drilldown?period=…` → items `{round_id|security_code, value(ratio/CNY), unit, label}`（已实现盈利/亏损回合、期末浮盈/浮亏持仓）；`source_endpoints=["/rounds?period=…","/assets?from=…&to=…"]`；`params{period}`。
- **golden**：`fx_behavior_de_pos30`（G=6,L=12,g=9,l=3 → PGR=0.4、PLR=0.8、DE=+0.4，bootstrap CI 不含 0）；`fx_behavior_de_null30`（G=8,L=8,g=7,l=7 → PGR=PLR=8/15 → DE=0，n=30，CI 含 0）；手算表用上面的 6 观测例（DE=−0.16666667）+ holding_days_diff：盈 [5,7]、亏 [18,22] → 20−6=14 天；另加双舍入对照 `fx_behavior_de_rounding`（G=1,L=2,g=2,l=1 → 精确 DE=+0.33333333，而 PGR/PLR 各自舍入后相减=+0.33333334，必须命中精确式）。期望值来源：`tests/oracle/naive_pgr.py` 独立实现 + `fixtures/expected/metrics/behavior_de*.yaml`；容差 0（标度整数精确相等；bootstrap 同 seed 同 B 必须逐 replicate 相等）。
- **必杀变异**：① `DE=PLR−PGR` 符号翻转 → `test_de_sign_disposition_effect`；② `pnl>0` 改 `>=` → `test_de_excludes_zero_pnl`；③ 分母 `G+g` 改 `G`（漏纸面仓）→ `test_de_denominator_includes_paper_positions`；④ seed 常量/不派生 → `test_bootstrap_seed_changes_with_inputs`、`test_bootstrap_deterministic_same_seed`。
- **HITL**：HITL-7（B/CI 方法/seed）、H-19（CI 交接）、H-20（抽样单元）、H-27（笔数粒度）；HITL-9（交易日近似影响 holding_days_diff）。

### behavior.turnover — 换手率
- 域 behavior / 1.0 / core_conclusion=否 / mvp=M1 / sync。
- **口径来源**：PRD-v1 §5.5“年化单边换手率”（`:412`）、§6.2“年化换手率与季度净收益的秩相关”（`:428`）；PRD-v2 FR-R6 只列“换手”（`:410`）。**PRD 无分子/分母精确定义 → H-23**；brief §3 目录口径“期间双边成交额/平均持仓市值”。
- **公式**：
  ```
  num = Σ |amount_units|            (category∈{buy,sell}, trade_date∈period)   # 双边成交额，含费净额
  avg_mv = hu(Σ_{d∈D_used} asset_daily.market_value_units, |D_used|)          # D_used=非 blocked 自然日
  raw_units = hu(num*10^8, avg_mv)
  ann_units = hu(num*244*10^8, avg_mv*N_trading)        # N_trading=period 内交易日数
  ```
  例：买 10000.00 + 卖 12000.00（num=2,200,000 分）、avg_mv=11,000.00（1,100,000 分）、N=20 → raw=2.00000000、年化=24.40000000。年化基数 `A=244`（禁 252，`constants.py:21`）；含费净额使分子 <1bp 偏差，写入 assumptions。
- **输入视图**：`ctx.trades`、`ctx.asset_daily`；依赖 trades+asset_daily；asset_daily 缺失/未构建 → blocked（不得用 trades 自造市值）。
- **period**：自然月闭区间；`D_used` 为自然日（与 asset_daily 粒度一致），年化用交易日数（优先 `ctx.benchmark` 会话数，回退 `business_days_between`，HITL-9）。
- **边界**：`D_used < 0.8*period_days` → blocked；`avg_mv==0`（空仓）且 num==0 → value=0 + insufficient（无可衡量持仓）；`avg_mv==0` 且 num>0 → unreliable（分母为 0，禁 Infinity）；`num==0`（未交易）→ 0/insufficient；单边只买未卖照常计入双边的买单；n 门槛（`n=D_used`）见 0.4。Decimal 等价：`ann = (Σ|amount| / avg_mv) × 244 / N_trading`。
- **sample_policy**：`min_n=10`（D_used）、`ci="none"`（H-21）、无 ci_min_n。
- **status_policy**：`asset_build!=current→stale`；`asset_daily_missing→blocked`；`avg_mv=0 且 num>0→unreliable`。
- **CI/统计细节**：`ci="none"`（H-21）——期间聚合比值没有可定义抽样分布，不出 CI、不做 bootstrap；n=D_used 只用于样本门槛。
- **输出**：value=年化换手率（ratio，precision 8）；decomposition：`double_sided_amount`(CNY)、`avg_market_value`(CNY)、`raw_turnover`(ratio)、`buy_amount`/`sell_amount`(CNY)、`n_trading_days`/`n_natural_days_used`/`n_blocked_days`(count)。
- **explain**：conclusion=“这段期间你的交易额相当于持仓市值的 {raw} 倍，年化约 {ann} 倍；换手越高，费用与择时错误的拖累越大”；method=“双边成交额 ÷ 平均持仓市值，按 244 交易日年化”。禁裸术语。
- **drilldown**：`/trades?from=&to=` → items `{trade_id, value=amount, unit=CNY, label=买入/卖出}`；`source_endpoints=["/trades?…","/assets?…"]`。
- **golden**：`fx_behavior_turnover`（上例 → 2.00000000 / 24.40000000；30 自然日、20 交易日）；边界 fixture：空仓（unreliable/0）、asset_daily blocked 日（剔除后 D_used 变小数）。
- **必杀变异**：① 双边改单边（除 2 或只取 sell）→ `test_turnover_double_sided`；② 年化 244↔252 → `test_constants_match_prd_appendix`；③ 平均天数 `D_used`→`period_days` → `test_turnover_uses_nonblocked_days`；④ 分母平均市值改期末市值 → `test_turnover_uses_time_average_mv`。
- **HITL**：H-23、HITL-9、H-21；HITL-12 的 asset_daily 失效语义延伸。

### behavior.averaging_down — 摊平（量化定义）
- 域 behavior / 1.0 / core_conclusion=否 / mvp=M1 / sync。
- **口径来源**：PRD-v1 §6.3 只有一句“识别『同一标的价格下跌后加仓』笔数与摊平后继续下跌的比例”（`:431-432`）；PRD-v2 FR-R6 只列“摊平”。**无公式 → H-22**，以下为本设计补齐（只补充、不改口径名义）。
- **公式（可执行定义）**：
  ```
  # 成本基（与 assets.py:252-276 完全同规则，避免口径漂移）
  cost += (−amount_units)                    # category∈{buy,rights,new_stock}
  cost  = max(0, cost + cost*delta//old_qty) # sell, delta<0
  avg_cost_before_units = hu(cost_basis*10^8, qty_before)      # price scale 6；qty_before>0
  候选加仓 = {t: t.category=="buy" ∧ qty_before>0}             # 首次建仓/送转/打新不算
                                                               # 持仓>0 ⇒ 必在同一 0→0 回合内（engine.py:185-208），无需额外“同回合”判定
  摊平事件 = {t∈候选加仓: t.price_units < avg_cost_before_units}  # 严格低于，等于不算
  depth_units = hu((avg_cost_before − price)*10^8, avg_cost_before)      # 0..1e8
  new_avg_units = hu((cost_basis+|amount|)*10^8, qty_before+qty_add)     # 摊平后含费均价
  value_units = hu(n_avg_down*10^8, n_addon)                            # 占比
  ```
  例：持仓 1000 股/成本 10000.00 → 含费均价 10.000000；加仓 500@9.00 → 摊平，depth=0.10000000、new_avg=9.666667；另一次加仓 100@11.00 不摊平 → `n_addon=2, n_avg_down=1, value=0.50000000`。fwd20 例：后 20 交易日 9.00→9.90（+10%），基准 1000→1030（+3%）→ `fwd20_excess=0.07000000`；回合 pnl 差例：摊平回合 +100.00 对其它 −50.00 → 150.00。
- **输入视图**：`ctx.trades`（含 line_no/id 稳定序，A7）、`ctx.bars`、`ctx.benchmark`、`ctx.rounds`；依赖 trades+bars+benchmark(+rounds)。无基准 → fwd20 段 null 但占比仍可算（decomp 缺项，status 不降级）。
- **period**：事件按 `trade_date∈period`；成本基从账户首笔重放；同日多笔按稳定序 `(trade_date, line_no or 0, id)`，保证确定性。
- **边界**：`qty_before==0` → 首次建仓，不计候选；`cost_basis==0 ∧ qty>0`（如纯送转股）→ 不计（n_zero_cost）；`price_units==0`（无价调整类）→ 剔除；`n_addon=0` → value=None/insufficient；rights/new_stock 只在 `n_rights_addon`；fwd20 窗口未走完 → `n_pending` 且该事件不进 fwd20 均值；深度分母 0 已由 qty>0 排除；n 门槛（`n=n_addon`）见 0.4。Decimal 等价：`depth = (avg_cost_before − price)/avg_cost_before`、`value = n_avg_down/n_addon`。
- **sample_policy**：`min_n=10`（n_addon）、`ci="none"`；`n_pending` 走 `pending_rule="forward_bars"`。
- **status_policy**：`bars_missing→blocked`（fwd20 段）；`data_error>5%→unreliable`；占比段不依赖行情。
- **CI/统计细节**：`ci="none"`（H-21）——占比是确定性计数比，不出 CI；fwd20 段为描述性均值（`n_pending` 单列），不做显著性断言。
- **输出**：value=摊平占比（ratio 8）；decomposition：`n_avg_down`/`n_addon`/`avg_depth`(ratio)/`fwd20_excess_mean`(ratio)/`round_pnl_diff`(CNY)/`n_pending`/`n_first_build`/`n_rights_addon`/`n_zero_cost`。
- **explain**：conclusion=“你有 {n} 次在持仓亏损时加仓，占全部加仓的 {p}；摊平后 20 个交易日的表现平均比基准 {方向} {x} 个百分点”；method=“买入价低于加仓前含费均价即视为摊平”。禁裸术语。
- **drilldown**：`/trades?category=buy&from=&to=` → items `{trade_id, value=depth, unit=ratio, label=摊平深度}`；`source_endpoints=["/trades?…","/rounds?…"]`。
- **golden**：`fx_behavior_avgdown`（上例 0.5 / depth 0.1 / fwd20 0.07 / round_pnl_diff 150.00）；边界：首次建仓剔除、收盘买在均价上方不算、同日两笔稳定序。
- **必杀变异**：① `<`→`<=`（均价等于也算摊平）→ `test_avgdown_strictly_below_avg_cost`；② 去掉 `qty_before>0`（把建仓算摊平）→ `test_avgdown_excludes_first_build`；③ 稳定序排序移除 → `test_avgdown_same_day_stable_order`；④ 深度分母用 price 而非 avg_cost → `test_avgdown_depth_denominator`。
- **HITL**：H-22（定义本身）、H-24（fwd20 窗口/停牌规则复用）。

### behavior.hhi — 仓位集中度 HHI（双口径）
- 域 behavior / 1.0 / core_conclusion=否 / mvp=M1 / sync。
- **口径来源**：PRD-v2 A19（`:583`）：“`HHI_eq = Σ(市值_i/Σ市值)²`；`HHI_total = Σw_i²`（含现金为一项）；区间聚合给逐日均值 + 最大值 + 期末值”；PRD-v1 A19/§6.2（`:429,654`）同式。**无明细表 → 0.3 内存重建（HITL-13）**。
- **公式**（逐日；`total = cash + Σ mv_i`）：
  ```
  mv_i = qty_i*close_on_or_before(ladder,code,d)//10^8
  HHI_total_units = hu((Σ mv_i² + cash²)*10^8, total²)
  HHI_eq_units    = hu(Σ mv_i²*10^8, (Σ mv_i)²)          # Σmv=0 → None（空仓日）
  mean_units = hu(Σ_{d∈D_used} HHI_units, |D_used|)；max；end=最后一个 D_used 日
  ```
  例（两日）：d1 mv=[6000.00,3000.00]+现金 1000.00 → HHI_total=0.46000000、HHI_eq=0.55555556；d2 mv=[4000.00,4000.00]+现金 2000.00 → 0.36000000/0.50000000；→ value(HHI_total 均值)=0.41000000、max=0.46000000、end=0.36000000；HHI_eq 均值=0.52777778、max=0.55555556、end=0.50000000。中间量量级：`mv²` ≈ 1e20~1e22（随资产规模）、×1e8 ≈ 1e28~1e30，**必须用 Python 任意精度 int**（不得下推 int64/SQL）。
  - 注意：`Σ mv_i²` 与总市值一致性由逐标的 floor 保证（`Σ逐标的 == total_market_value`，0.2）→ 与 asset_daily 对账可证。
- **输入视图**：`ctx.trades`、`ctx.bars`、`ctx.asset_daily`（cash/total/状态）；依赖 trades+bars+asset_daily。asset_daily 缺失 → blocked；bars 缺价 → 逐日剔除（0.3 第 4/7 步）。
- **period**：统计期 `[period_start,period_end]` 的自然日（H-28）；持仓从账户首笔重放，仅区间内估值；`end`=区间最后一个可用日。
- **边界**：空仓日 → HHI_eq 不计入均值（`n_days_empty` 计数），HHI_total=1（全现金）计入；单标的 100% → HHI_eq=1.0；`D_used=0` → blocked；覆盖率阈值见 0.3；`holding_count=0` 的 HHI_total 恰为 1.0（须 golden 锁定）；n 门槛（`n=D_used`）见 0.4。Decimal 等价：`HHI = Σ w_i²`（`w_i=市值_i/总资产`，现金权重为现金/总资产；eq 口径分母为 Σ市值）。
- **sample_policy**：`min_n=10`（D_used）、`ci="none"`（H-21）。
- **status_policy**：`asset_build!=current→stale`；`asset_daily_missing→blocked`；`coverage<0.5→blocked`、`<0.9→unreliable`；对账不符日 → `unreliable` 计数并剔除。
- **CI/统计细节**：`ci="none"`（H-21）——逐日均值的 CI 需日间独立假设（持仓自相关使其不成立），M1 不出 CI；如需不确定性，decomp 附 `n_days_used` 与 min/max 即可。
- **输出**：value=HHI_total 区间均值（ratio 8）；decomposition：`hhi_total_max`/`hhi_total_end`、`hhi_eq_mean`/`hhi_eq_max`/`hhi_eq_end`(ratio)、`avg_holding_count`(count)、`avg_cash_weight`(ratio)、`n_days_used`/`n_days_blocked`/`n_days_empty`/`n_recon_mismatch`(count)。
- **explain**：conclusion=“你的持仓集中度约 {value}（0 越分散、1 越集中）；最高一天 {max}，期末 {end}”；method=“逐日按个股市值占总资产（含现金）平方和求均值”。禁裸术语 HHI。
- **drilldown**：`/metrics/behavior.hhi/drilldown?period=…` → items `{date, value, unit=ratio, label="单日集中度"}`（最多 31 项）；`source_endpoints=["/assets?from=&to=…"]`。
- **golden**：`fx_behavior_hhi`（上例两日）；边界：全现金（HHI_total=1）、单票 100%、blocked 日剔除、对账不符日剔除、空仓日不进 eq 均值。
- **必杀变异**：① HHI_total 漏现金项 → `test_hhi_total_includes_cash`；② HHI_eq 分母用 total（含现金）→ `test_hhi_eq_denominator_excludes_cash`；③ 逐标的 floor 改四舍五入 → `test_hhi_symbol_value_floor_matches_total_mv`；④ 均值除 `D_used` 改 `period_days` → `test_hhi_mean_uses_valuable_days`。
- **HITL**：HITL-13（重建方案）、H-28（粒度/阈值）、HITL-12（asset_daily 失效）、HITL-9（日历）。

## 2. 选股域

### selection.bhar_20 — 买入事件 BHAR（h=20 交易日）
- 域 selection / 1.0 / core_conclusion=是 / mvp=M1 / frequency=async（API-038 job，HITL-17：handler 归属待定，本设计只交付 defs+数据视图）。
- **口径来源**：PRD-v2 A24（`:588`）“`BHAR_h = Π(1+r_i) − Π(1+r_b,h)`，`h∈{20,60}`；事件日聚类 t 值”；PRD-v1 FR-R4（`:373-380`）“以**买入日 cohort** 归属区间”“输出均值、中位数、t 值（重叠窗口按事件日聚类）”+ 验收“给定固定行情快照与合成流水，BHAR 与独立实现 diff ≤ ε_r；区间内无买入事件返回空结果而非报错；修改 as_of 后不出现晚于该日的财报数据”。PIT 原文见 FR-R4 §4.1（`:368`）。
- **公式**（精确有理数，一次舍入；`h∈{20,60}`）：
  ```
  cohort = {t: t.category=="buy" ∧ t.trade_date∈period}          # 主口径（H-29）
  j0 = 最后一个满足 bench_date[j] ≤ buy_date 的 j（基准交易日历）
  j1 = j0 + h；要求 j1 存在 ∧ bench_date[j1] ≤ as_of，否则 → n_pending（不进分母）
  c0 = close_on_or_before(ladder, sym, buy_date)；ch = close_on_or_before(ladder, sym, bench_date[j1])
  b0 = bench_close[j0]；bh = bench_close[j1]
  BHAR_units = hu((ch*b0 − bh*c0)*10^8, c0*b0)
  mean = hu(Σ BHAR, n)；median = median_units(BHAR)；hit = #{BHAR>0}
  ```
  等价性：`Π(1+r_i) = ch/c0`（逐日收益望远镜求和，停牌日 r=0），故整数实现只做**一次**除法，不累积 1e-8 误差。Decimal 等价：`BHAR = ch/c0 − bh/b0`。手算例（3 事件）：`(100→110, 1000→1050)`=+0.05、`(100→95, 1000→1020)`=−0.07、`(50→52, 1000→990)`=+0.05 → mean=0.01000000、median=0.05000000、hit=2/3=0.66666667、t=0.2500（见下）。
  - 60 日例：`100→125`、`1000→1100` → BHAR=0.15000000。

  | 事件 | buy_date | 个股 c0→ch | 基准 b0→bh | BHAR |
  |---|---|---|---|---|
  | E1 | 2026-02-05 | 100.000000→110.000000 | 1000.000000→1050.000000 | 0.05000000 |
  | E2 | 2026-02-06 | 100.000000→95.000000 | 1000.000000→1020.000000 | −0.07000000 |
  | E3 | 2026-03-02 | 50.000000→52.000000 | 1000.000000→990.000000 | 0.05000000 |

  （手工夹具让基准起点价固定 1000.000000 便于手算；真实夹具由生成器产出，基准起价随日变化。）
- **t 值（事件日聚类，cluster-robust）**：
  ```
  G = 事件日去重数；G<2 → t=null
  u_g = Σ_{i∈g}(BHAR_i − mean)；SS = Σ_g u_g²
  Var(mean) = G/(G−1) · SS/n²；t = mean·n·sqrt((G−1)/(G·SS))
  t_units(4dp) = 整数平方根 + 半进位（`isqrt(X²·num·10^8//den)` 再按平方比较进位；禁 float）
  ```
  例：3 个不同事件日、BHAR=[5e6,−7e6,5e6]、mean=1e6、SS=96e12 → `Var=3/2·96e12/9=16e12`，SE=4e6，t=0.2500；**单例聚类时 cluster-robust t 恒等于单样本 t**（golden 用此恒等式自证）。
- **PIT 语义**：只用 `trade_date≤as_of` 的行情/事件；`data_as_of=min(as_of, bars.as_of, benchmark.as_of)`；`assumptions.pit_rule="price_only"`、`adjust="unadjusted"`（H-26）。**本仓无财报/行业数据**（全仓检索确认）：API-038 断言的“不含晚于 as_of 的财报数据”**不可执行** → HITL-4 推荐修订为：“① 所有 bars/基准点 `trade_date≤as_of`；② `as_of` 前移时 `n_pending` 单调不减、BHAR 值不变；③ 响应 `data_as_of≤as_of`”。财报/行业 PIT 另立任务（Brinson/A25 在 Period3-08）。
- **边界与异常 / 剔除规则（H-24）**：① 窗口未走完（`bench_date[j1]>as_of` 或 j1 不存在）→ `n_pending`，不进分母；② 停牌：窗口内该股 bar 覆盖率 `<50%`（`h` 个基准交易日中该股有 bar 的比例）→ `n_excluded_suspended`；③ 退市：窗口最后 5 个基准交易日无该股 bar 且累计覆盖 `<90%` → `n_excluded_delisted`（仓内无 PIT 证券主数据，用行情缺失代理，写入 assumptions）；④ 事件日无任何可得价（`c0 is None`）→ `n_excluded_noprice`；⑤ 区间内无买入事件 → `value=None, n=0, sample_status=insufficient`，**返回空结果而非报错**（FR-R4 验收）；⑥ 有效事件全部被剔除（n=0 但 cohort 非空）→ `value=None, status=blocked`（reason=`ALL_EVENTS_EXCLUDED`，不得静默按 0 处理）。`n`=有效事件数；`n∈{1,5,9,10,29,30}` 按 0.4，`n=1` 时 `median=该值`、`t=null`（G<2）。本 spec 的公式/视图/剔除规则可被 bhar_60 原样复用（见下）。
- **CI/统计细节**：均值 bootstrap（同 0.2 的 `xorshift64_star`/`pct_linear`，B=1000，95% percentile，CI 标度 1e-8）；seed=`int.from_bytes(sha256(inputs_hash.encode()).digest()[:8],"big")`（团队 D-07；inputs_hash 含 metric_id/h，故 20/60 自动不同）；分位 `pct_linear`（最近秩+线性插值）；t 值与 CI 相互独立（CI=均值不确定性，t=聚类稳健方差），不做 BCa。
- **输入视图**：`ctx.trades`、`ctx.bars`、`ctx.benchmark`；依赖 trades+bars+benchmark_series。缺基准 → blocked（`benchmark_required=true`）；基准非全收益（如价格指数）→ `unreliable` + `assumptions.benchmark_id/benchmark_kind`；**replay 夹具当前只有月频 9 个点（`ledger/fixtures.py:187-196`）→ 无法定义 h=20/60 交易日窗口 → blocked（`CAP_DAILY_BARS_MISSING`），禁止用月频插值伪造日线**（硬前置 HITL-2/3）。
- **sample_policy**：`min_n=10`、`ci_min_n=30`、`ci="bootstrap"`（对事件 BHAR 均值重抽样，B=1000，95% percentile（最近秩+线性插值，`pct_linear`），CI 内部标度 1e-8；PRNG/seed/分位同 behavior.de（seed 由 `inputs_hash` 派生，不再加前缀））、无 min_trading_days（A26 的 60 日只约束夏普/回撤/IR）。
- **status_policy**：`benchmark_missing→blocked`；`capability=bars_missing→blocked`；`adjust!=hfq ∧ 除权嫌疑日(|日收益|>板块涨跌停)→unreliable`（H-26）；`recon_suspicious_ratio>0.05→unreliable`；`pending_ratio` 不降级（只影响样本状态；pipeline 仍 ok）。
- **输出**：value=BHAR 均值（ratio 8）；decomposition：`median`(ratio)、`t`(unit=score，4dp)、`hit_rate`(ratio)、`n`/`n_pending`/`n_excluded_suspended`/`n_excluded_delisted`/`n_excluded_noprice`(count)、`mean_benchmark_return`(ratio)；`h` 固定入 `params_schema`（`Literal[20,60]`，不跨指标共享 params）。
- **explain**：conclusion=“你买入后 20 个交易日的表现比沪深300全收益平均{方向} {x} 个百分点（n={n}）”；method=“每个买入事件持有 {h} 个交易日，与同期沪深300全收益对比后取平均；未走完窗口的事件不计入”；非 ok → `conclusion=null` + `reason/what_to_do`。禁裸术语 BHAR/PIT/cohort。
- **drilldown**：`/metrics/selection.bhar_20/drilldown?period=…` → items `{event_date+security_code, value=BHAR, unit=ratio, label="买入后20日超额"}`；`source_endpoints=["/metrics?domain=selection…","/trades?…"]`；`params{period,h,benchmark}`。
- **golden**：`fx_selection_bhar20`（上表 E1–E3：mean=0.01000000、median=0.05000000、t=0.2500、hit=0.66666667）；`fx_selection_bhar60`（单事件 0.15000000）；`fx_selection_bhar_pending`（窗口含 as_of 之后日期 → n_pending=1、分母排除）；`fx_selection_bhar_suspended`（覆盖率<50% 剔除）；`fx_selection_bhar_empty`（无买入事件 → 空结果）。行情来源：**测试内合成日线**（`tests/factories/bars_daily.py` 纯整数确定性生成），API 级 golden 待 HITL-2 扩日线夹具后补。期望值来源：`tests/oracle/naive_bhar.py` + 手算表；容差 0（整数精确；与独立实现 diff ≤ε_r=1bp 为上限，实为 0）。
- **必杀变异**：① 减基准改加 → `test_bhar_vs_benchmark_sign`；② `h=20` 用 19/自然日 → `test_bhar_horizon_trading_days`；③ 聚类键用 symbol 而非事件日 → `test_bhar_cluster_by_event_date`；④ `G/(G−1)` 修正去掉 → `test_cluster_t_singleton_equals_one_sample_t`；⑤ 窗口未走完计入分母 → `test_bhar_pending_window_excluded`。
- **HITL**：HITL-4（财报数据缺失/Catalog 断言修订）、H-24（剔除阈值/基准价）、H-25（重叠窗聚类）、H-26（复权）、H-29（cohort 事件范围）、HITL-2/3（日线+基准夹具）、HITL-17（API-038 handler 归属）。

### selection.bhar_60 — 买入事件 BHAR（h=60 交易日）
- 域 selection / 1.0 / core_conclusion=是 / mvp=M1 / frequency=async（同 bhar_20）。
- **口径来源**：PRD-v2 A24（`:588`）+ PRD-v1 FR-R4 §4.3（`:375-378`）的 `h∈{20,60}` 并列口径；本指标只取 `h=60`。
- **公式/输入视图/period/边界与剔除/sample_policy/status_policy/explain/drilldown**：与 `selection.bhar_20` **逐项相同**，仅以下差异：`h=60`（`params_schema` 默认值不同，两指标独立 `definition_version`）、seed 由各自 `inputs_hash` 派生（含 h，故与 bhar_20 自动不同）、窗口更长 → `n_pending` 更多；**不合并进 decomposition**（Catalog 已登记两个独立 id，且 h 影响样本口径）。
- **CI/统计细节**：同 bhar_20 的均值 bootstrap（B=1000、95% percentile、CI 标度 1e-8）与事件日聚类 t 值公式；仅 seed（`inputs_hash` 内含 h）与窗口终点 `j0+60` 不同。
- **输出**：value=BHAR_60 均值（ratio 8）；decomposition：`median`/`t`(4dp)/`hit_rate`/`n`/`n_pending`/`n_excluded_*`/`mean_benchmark_return`，字段同 bhar_20。
- **golden**：`fx_selection_bhar60`（单事件 `100→125`、`1000→1100` → 0.15000000；n=1 → median=该值、t=null、sample_status=insufficient；期望值来源/容差同 bhar_20）；需 h=60 的日线覆盖 → 依赖 HITL-2 夹具。
- **必杀变异**：同 bhar_20（`h` 常量 20↔60 各自锁定 → `test_bhar_horizon_trading_days`；另加 `test_bhar60_uses_60_sessions`）。
- **HITL**：同 bhar_20（HITL-4、H-24/H-25/H-26、HITL-2/3、HITL-17）。

### selection.bhar_hit — BHAR>0 占比（exploratory，可砍）
- 域 selection / 1.0 / core_conclusion=否 / mvp=M2（exploratory=true，可砍）/ async（同 BHAR）。
- **口径来源**：brief §3 目录“BHAR>0 占比（选股胜率辅助）；可 exploratory”；PRD-v1 §4.3“区分『选得对』（BHAR>0）与『拿得住』”（`:378`）。无独立 PRD 公式，属派生统计量。
- **公式**：事件集与剔除规则同 `selection.bhar_20`（h=20）；`hit_units = hu(#{BHAR_i>0}*10^8, n)`；`n=0` → None/insufficient；CI 用 Wilson（`sample_policy.ci="wilson"`，框架代算），n≥30。
- **CI/统计细节**：Wilson 95%（框架代算，`core/stats/ci.py`；z 用定点常数、禁 float），输入 `n_hit/n`；`n<ci_min_n` 时不出 CI 与结论（0.4）。
- **输入视图**：同 bhar_20（复用其事件/窗口构建，避免两套剔除逻辑漂移；实现上共享 `build_bhar_events(h=20)` 纯函数）。
- **period/边界**：同 bhar_20；`BHAR==0` 不计 hit（严格 `>0`，变异重点）；全 hit/全不 hit（Wilson 边界 0/1）；`n=0`/全剔除的两种空结果语义同上；n 门槛见 0.4。
- **status_policy**：同 bhar_20（缺基准/缺行情/复权嫌疑）。
- **输出**：value=hit rate（ratio 8）；decomposition：`n_hit`/`n`/`n_pending`/`n_excluded_*`、`mean`/`median`(ratio)；`include_exploratory=false` 时不出现在响应中（API-033 断言⑤）。
- **explain**：conclusion=“你买入的股票里 {p} 在 20 个交易日后跑赢沪深300全收益”；method=“买入后 20 交易日相对基准为正的事件占比”。
- **drilldown**：同 bhar_20，items 增加 `hit` 布尔标签。
- **golden**：复用 `fx_selection_bhar20` → 0.66666667（Wilson CI 由框架 golden 断言）。
- **必杀变异**：① `>0`→`>=0` → `test_bhar_hit_excludes_zero`；② 分母用含 pending → `test_bhar_hit_denominator_excludes_pending`；③ Wilson 分母 n vs n−1 → `test_wilson_denominator`。
- **HITL**：HITL-6（是否砍）、H-24、H-26。

## 3. 域级测试与 golden 清单
| 项 | 内容 |
|---|---|
| golden 目录 | `fixtures/expected/metrics/{behavior_de,behavior_turnover,behavior_avgdown,behavior_hhi,selection_bhar20,selection_bhar60,selection_bhar_hit}.yaml`（含 `inputs_hash`/`definition_version`/手算表来源；审批按 Testing §12.3 独立 PR + golden-approved） |
| 夹具 | `fx_behavior_de_pos30/null30`、`fx_behavior_turnover`、`fx_behavior_avgdown`、`fx_behavior_hhi`、`fx_selection_bhar20/60/pending/suspended/empty`；日线用测试内合成生成器（HITL-2 后迁 replay 夹具）；全部金额/价格/比率为定点字符串，MANIFEST 记 sha256 |
| 独立 oracle | `tests/oracle/naive_pgr.py`（逐笔计数 + 有理数 DE）、`naive_bootstrap.py`（独立作者、同 PRNG 规格但独立实现）、`naive_bhar.py`（逐日连乘，不做望远镜化简，用于对拍等价性）；生产代码禁 import tests（Testing §9.3） |
| 交叉恒等式 | ① `Σ逐标的 mv == asset_daily.market_value`；② `cash+Σmv == total_assets`；③ 单例聚类 t == 单样本 t；④ `Π(1+r)` 望远镜 == 端点价比；⑤ `|Σ(重建市值变化) − asset_daily.daily_pnl|` 在无现金流日 ≤ε_amt（仅诊断不阻断） |
| 契约/fast 测试 | `assert_purity`（禁 I/O/时钟/随机）、`assert_deterministic`（两次 inputs_hash 相同、bootstrap 逐 replicate 相同）、`assert_no_float`（MetricValue/decomposition 递归无 float）、`assert_no_bare_terms`（DE/PGR/PLR/HHI/BHAR/PIT/cohort 不进 conclusion）、`assert_sample_gate`（n=0/5/9/10/29/30 全枚举）、`assert_drilldown_resolves` |
| 性能 | HHI/DE 重建：5 年账+100 标的 ≤300ms（p95）；BHAR 1000 事件 ≤200ms；bootstrap B=1000 ≤50ms |
| 变异 | 本域每指标 ≥3 个必杀算子（见各 spec），core 新模块纳入 `mutmut` 范围，score ≥80%（HITL-16） |

## 4. 实现顺序与估时（人日）
| 步骤 | 内容 | 人日 | 前置 |
|---|---|---|---|
| 1 | `core/metrics/intmath.py` + `core/stats/numeric.py`（hu/median/pct_linear/xorshift64_star/cluster_t）+ 单测 | 0.3 | — |
| 2 | `review/application/behavior_positions.py`（持仓逐日重建 + 对账校验） | 0.4 | 0.3 节 |
| 3 | behavior.de（defs + golden + oracle） | 0.4 | 1,2 + H-19 |
| 4 | behavior.turnover | 0.15 | — |
| 5 | behavior.averaging_down | 0.25 | 1,2 |
| 6 | behavior.hhi | 0.35 | 1,2 |
| 7 | selection.bhar_20/60/hit（共享 `build_bhar_events`） | 0.5 | 1 + 日线夹具(HITL-2) |
| 8 | golden/oracle/变异接线 + 契约测试 | 0.5 | 3–7 |
| **合计** | 7 个指标 ≈ **3.0 人日**（18 指标按此密度 ≈7–8 人日） | 3.0 | → HITL-6：3.5 人日预算不成立 |

## 给 Lead 的合并提示（≤15 行）
1. **四个关键定义**：DE 的 PGR/PLR 严格按 PRD-v1 A15（分母含“期末浮盈/浮亏笔数”，按证券持仓 1 笔、浮盈不含已收股息；DE 用精确有理数一次舍入）；摊平=持仓>0 的 buy 且 `price < 加仓前含费均价`（严格），深度 `(均价−买价)/均价`；HHI 双口径逐日算，`HHI_total` 含现金、`HHI_eq` 不含；BHAR 用端点价比（望远镜恒等）逐事件算，基准必须 hs300_tr 全收益。
2. **必须写进决策摘要**：HITL-13 选“内存重建”而非新表（含 300ms 预算与对账不变量）；DE/HHI 依赖账户首笔全量重放 → lookback 必须进 `inputs_hash`（HITL-18 延伸）。
3. **必须人工拍板**：H-19（bootstrap CI 与冻结引擎契约的交接方式，影响 05 引擎）、H-20（重抽样单元）、H-22/H-23（摊平/换手定义，PRD 只有名词）、H-24/H-25（BHAR 剔除与重叠窗聚类）、H-26 + HITL-4（不复权 vs hfq、API-038 断言修订）、H-27（笔数粒度）。
4. **跨域耦合**：① 日线/基准夹具是 timing（HITL-2/3）与 selection 的共同硬前置；② `asset_daily` 的 build 状态/hit 语义（HITL-12）影响 turnover/HHI 的 stale/blocked；③ **必须与 perf H-02 统一 `positions_end`**：DE 期末浮盈与 A10 全样本胜率共用同一视图/同一 `paper_pnl` 纯函数（本 part 已按此改口径，请 Lead 把 H-02 与 D-13 合并为一条）；④ `SamplePolicy.ci="none"`（H-21）与 CI 交接（H-19）需 05 一并确认；⑤ bootstrap 已对齐 D-07（`xorshift64_star` + `pct_linear` 最近秩+线性插值 + B=1000 + seed=sha256(inputs_hash)[:8]），与 timing part 口径一致。
5. **风险 top3**：① 冻结契约下 DE 的 bootstrap 无合法落点（H-19，可能导致 behavior.de 阻塞）；② PRD 未定义摊平/换手，若人工选择不同口径将连带 golden/文案重做；③ 仓内行情只有月频 9 点且不复权 → selection 域在 replay 下无法产出可解释 BHAR（依赖 HITL-2 + H-26）。
