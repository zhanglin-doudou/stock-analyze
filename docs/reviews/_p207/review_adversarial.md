# Period2-07 三域指标设计 · 独立评审 #3（对抗性 / 可测试性 / 验收与进度）

> 对象：`docs/architecture/2026-10-26-metrics-three-domain.md` v0.9（1071 行）｜日期 2026-10-26｜只读评审，未改设计/代码
> 依据：DEV-PLAN §3.1/§3.2、PRD FR-R4/R5/R6/R8 与附录 A、Testing §9.3/§12、API-Catalog §0.4/§0.8/§6、上任务设计与实施日志、四份 part 工作稿；数值用 `.venv/bin/python`（Decimal）独立复算。

**裁决：REQUEST_CHANGES**
**总评**：公式与口径推导是本期最扎实的部分；但"可验收性"不过关——按 v0.9 排期，第 1 天卡在 `make rebuild`，第 1 周卡在验收 period 无数据，golden 冻结前还会撞上互斥的字段规则与"设计内已定"其实未定的口径。
**最坏后果**：07 以"golden 全绿"结案，但演示环境五卡全空/日期错月；真实账单因送转、断链、无基准再次全红——此时 `definition_version` 与 golden 已冻结，口径版本无法回滚。
**80/20**：只做对 80% 时最坏的是状态传播与字段出现规则（前端会照常渲染不可信数字）> 数据源选择（asset_daily vs nav、data_error 是否入成交型指标）> 下钻/物化的租户与缓存边界；公式本身反而不是最先爆的。

## 发现

### A-P0-1｜验收剧本在数据时间轴上不可达，第一条命令不存在（§11、D-02/D-19、§7.4、DEV-PLAN §3.1）
- 问题：① `make seed-demo && make rebuild` 的第二条命令在 Makefile/CLI 均不存在（`make -n rebuild` → No rule；`cli.py` 无 rebuild 子命令）；`seed-demo` 只重建回合（`cli.py:604`），不建 nav/asset_daily/metric_result，而 asset_daily 依赖 API-082 job → 回撤/β/换手/HHI/fee_breakeven 全是 `build.state=missing`；已有批次时 `_import_bill` 直接跳过（`cli.py:571`），夹具升级后旧库不会刷新，必须先 db-reset。② 验收锚点 `period=2026-09`，但 demo 流水锚死在 `_ANCHOR=2026-06-30`（`ledger/fixtures.py:49,68`，months=3 → 流水仅 2026-04-01~06-30；两个 demo 账户各 25 次超卖）；D-19 只"扩到 ≥12 个月"（→2025-07~2026-06）仍不含 09 月。③ 更硬的是前向窗口：行情止于 2026-09-30，`period=2026-09` 的买入/卖出事件没有 20/60 根前向 bar → `selection.bhar_20/60`（core）、`timing.fly`、`averaging_down.fwd20` 全 pending，n=0。
- 攻击场景：按 D-19 实施后跑验收，`curl metrics?ids=perf.win_rate&period=2026-09` 返回 `n=0/insufficient`；golden 却可另选 period 全绿 → "测试绿、验收红"，且无人发现直到人工走查五卡。
- 建议：D-19 改为"账单锚点/DEMO_CLOCK/行情 as_of 三者对齐：流水覆盖 as_of 前 12 个月、行情覆盖 as_of 后 ≥60 交易日"；验收 period 由 `data_as_of` 推导（或固定 2026-06/07 并同步改 DEV-PLAN §3.1/§11）；C-25 补 `rebuild`/`test-metrics`/`demo-broken` 目标与"夹具变更需 db-reset"；五卡落成 API 脚本断言（period 内回合数 ≥10、value 非空）。
- 影响：二期验收第 1/5 步与 FR-R4/FR-R5 API 验收同时失败；这是"最先崩的地方"。

### A-P0-2｜字段出现规则三处互斥，05/06/07 必然各写一版（D-08、§4.2、§6.3、§7.1.1 vs §8.6、§8.3、§8.7）
- 问题：① n=0：§4.2/§6.3/§7.1.1 写"`insufficient`，value **存在但 null**"，§8.6 门槛表却写"0 → **无 value 键**"；② blocked 粒度：§6.3 写"对账 blocked → **全部**指标 blocked"，§8.3/§8.7 又写 `fx_blocked` "**部分阻断**、坏指标无 value 而好指标仍 ok"；③ stale：§4.2 无 value，§6.3 写"无（或旧值+stale）"（另与 Extensibility §3.3 冲突）。
- 攻击场景：05 按 §6.3 做全局 blocked 传播，06 按 §8.7 写 API-033"好指标仍 ok"→ 契约测试必红；前端按 value 键存在性分流，却无法区分"n=0"与"blocked"。
- 建议：新增一张"状态（全表 blocked / 区间单日 blocked / degraded / stale / insufficient n=0 / pending_window）× 指标类别 → value/ci/conclusion 键"决策表，删除所有其它表述；D-08 补 n=0 行；四种 blocked 粒度各给 fixture 与断言；加 `assert_field_presence_matrix` 契约测试。
- 影响：三方实现互斥、golden/契约每天红；字段级破坏性变更还会联动 SDK/前端返工。

### A-P0-3｜估时/范围/门禁归属表自身加不平，排期承诺不可信（§10、D-06、§8.11、D-17）
- 问题：S1–S9 逐项和 = 2.0+1.8+3.5+1.5+1.5+1.2+1.2+1.0+1.0 = **14.7d**，表末却写"07a 合计 8.5~9.5"；加 S10（3.5~4.5）得 18.2~19.2d，与"07 合计 12~14"差 4~5d。"07a 核心 12"与 §3.1 的 core=10/expl=8 不符；S10 标"6 指标"却列 8 个（holding_days/concentration/expectancy/fly/turnover/averaging_down/hhi/bhar_hit）。S0=5d 把 05 要新增的 metric_result 迁移+RLS、bars/benchmark/asset_daily/positions 视图、CI 派发、失效、ci_sample 都算进去，且 P3 要求"bootstrap 与 oracle 一致"——把 07 的 oracle 前置给 05。D-17 声明 API-038 handler 归 06/07b，但 §8.7/§8.11 又把 API-038 纳入 07 验收与 verify-p2；verify-p2 在 DEV-PLAN 归 Period2-14。
- 攻击场景：05 按 DEV-PLAN 的 3d 交付 → 07 D+1 无引擎；或按 12~14d 承诺后实现阶段发现 §10 自己多出 5d，资源已按 12~14 排。
- 建议：重排 §10（逐项求和必须等于小计；05/06 独立列 6~8d 并注明新增视图/迁移）；07a/07b 严格按 §3.1 的 10/8 拆；API-038 与 verify-p2 的归属先改 DEV-PLAN 再开工（07 只交 `make test-metrics`）。
- 影响：D-06 的"唯一可行排期"结论不成立，工期再次失控。

### A-P0-4｜冻结 golden 前必须拍板的口径被漏出最小集合，契约修订清单无 owner（§13.3、§9、FR-R6）
- 问题：§13.3"最小确认集合"未列 D-03（live 无基准 → timing/selection 5 个指标永久 blocked）、D-05（dotted id 改公开契约）、D-11（A22 公式需改 PRD）、D-13（内存重建）、D-14（rf 1.5%/2.0%，正文自认"需人工确认"）、D-18、D-28；C-01~C-26 未指定谁在何时改 Catalog/PRD/openapi/SDK。FR-R6 要求"只卖盈不卖亏 → DE>0 且 CI 不含 0"，但 D-08 规定"L+l=0 → null+insufficient"：fixture 若真是"只卖盈、无纸面亏"，PLR=0/0，验收不可达，必须显式要求含纸面亏损。
- 攻击场景：按 FR-R6 造 `fx_de_pos30`（只卖盈利、无持仓）→ behavior.de 返回 null/insufficient，API-033 断言⑤失败；或 C-01 未落地先实现 dotted id，`ids=win_rate` 404。
- 建议：D-03/D-05/D-14 升 A 类进 §13.3 并给日期；补 C-27（PRD FR-R4 财报断言）、C-28（FR-R5/FR-R8 与 A26 的 CI 口径）、C-29（`from/to` 闭区间 vs Catalog §0.4 半开）、C-30（API-035 断言③ trade_id）、C-31（FR-R6/Testing §12.1 的 DE 方向）；C 清单加 owner+时点（建议 06 在 S0 内跑 api-snapshot/sdk）。
- 影响：07 在旧契约上无法写 API 用例；行为域 golden 冻结后可能整域返工。

### A-P1-1｜三方比对退化为"两方自证"，且 oracle 与生产同源（§8.5、§8.2、§1.2 目标 2）
- 问题：18 份 golden 中 8 份（chase/fly/beta/fee_breakeven/turnover/hhi/bhar_20/60/hit）期望来源写"oracle(+手算)"；§8.5 的比对②"oracle == golden.expected"在 golden 由 oracle 生成时恒真，只剩生产↔oracle 两方。四份 part 与合并稿由同一批模型按同一文档编写，oracle 与生产会"同错同消"——正撞 DEV-PLAN §3.4 的风险。
- 建议：上述 8 个指标各补一份可复算手算表（固定 3~5 条事件/60 条序列），golden 记录双来源；`expected_source.kind=oracle` 的 golden 一律不得 approved。
- 影响："每指标 1 份 golden + 独立实现"的验收文字满足，但口径错误仍会漏过；mutation ≥80% 变成数字游戏。

### A-P1-2｜夹具边界缺口与"假覆盖"，合并稿还改了 part 的口径未记录（§8.3、§12 R8、D-21/D-26/D-27）
- 问题：R8 称"fixture 覆盖送转/配股/股息"，但 §8.3 无送转、无配股、无红利税档、无停牌恰好 49/50/51%、无退市、无 n=9/29/59 的 DB 级夹具；`fx_asset_core`"手写 22~60 行"与 §7.2.3"60 交易日"矛盾，又与 D-13"逐日与 asset_daily 对账 0 公差"要求的 trades/asset_daily 同源冲突（§8.3 约束又称 loader 只直插 trade+生产 build）。合并稿把 part_test 的 fee golden 从 1250.02/2,605,200 改成 1279.12/2,658,000——差值恰是 C4 `data_error` 交易的费/成交额，即"data_error 是否计入成交型指标（fee/turnover）"已发生口径变化，但没有任何 D/HITL 记录；part_perf 的 `fx_round30=12 closed+18 open`（Catalog n==12 的唯一解）也在合并稿丢失，仅剩 C-06 改 fixture 名。
- 建议：补 `fx_corp_actions`（送转+配股+红利税）、`fx_suspension`（49/50/51%）、`fx_delisted`、`fx_n29/fx_n59`、`fx_version_break`；`fx_asset_core` 由 `fx_metrics_core` 流水经生产 build 生成（blocked 行用缺价构造）；显式定义 data_error 交易在成交型指标的取舍并重算 golden。
- 影响：PRD 边界无验证，真实账单必翻车；合并稿与 part 的数值漂移无法解释，评审追溯失效。

### A-P1-3｜五卡验收与 exploratory 划分冲突（DEV-PLAN §3.1、§3.1 表、D-08）
- 问题："四数字卡"含持仓天数、集中度，而设计把二者标 `expl` 且"默认不返回"；`include_exploratory=false` 时这两张卡无数据，传 true 又破坏 A26"核心 8–12/expl 默认隐藏"的意图。另外"净值 vs 沪深300"的基准对比在 `routers/nav.py:109` 明写"本期暂不计算"，C-07 二选一未拍板，07 也不实现 → 该卡只有净值没有对比。
- 建议：把 holding_days/concentration 提为 core（core=12 顶格，需同步 A26 预算）或明确五卡显式请求 explor（加 API-033 用例）；C-07 选"实现时复用 timing.beta core 函数"并计入 07 或 05 的 scope。
- 影响：五卡"有数"与 A26 预算二者必错一个；验收时才发现卡片数不对。

### A-P1-4｜回撤/β 用"逐自然日资产行"伪装日频，A26 门槛被绕过（D-12、D-28、§7.2）
- 问题：demo 行情只有 9 个月频点，asset_daily 却是逐自然日行（`assets.py:245`），132+ 个"日观测"里只有 9 个非零收益；drawdown 的 n_obs≥60、β 的 n≥60 因此"达标"，但 MDD/Ulcer/水下占比/β 全由月频跳变决定，数值不可信。D-28 的 bar_density 门禁只约束 bars 型指标，未覆盖 asset_daily 派生指标。
- 建议：给 asset_daily 派生指标加 `underlying_price_coverage`（价格刷新点/自然日），月频时置 `unreliable`；日线夹具落地后重算回撤/β golden；ACCEPTANCE 只认可 replay 日线模式。
- 影响："测试能过但产品会错"；演示回撤数字一旦被用户/券商 App 对比就会暴露。

### A-P1-5｜自然日/CI 门槛的 golden 本身有错（§7.1.3、§8.4、§8.6）
- 问题：§7.1.3 定义 `mean_natural_days=(close−open).days+1`，但 §8.4 自然日表用 diff（A1=7 而非 8，Σ=68），得 5.66666667/median 6.00；按定义应为 80/12=6.66666667、median 7.00。§7.1.3 又写 median 5.00，与 §8.4 的 6.00 冲突。门槛表把"分子样本 n"与"日频交易日"混成一张表，`assert_sample_gate` 对 `registry.all()` 盲套会自相矛盾（round 型指标没有 60 日门槛）。
- 建议：锁定"是否 +1"并重算 §8.4；golden 加"同日记 1"例；门槛表拆成 `min_n/ci_min_n/min_trading_days` 三列，kit 按 `sample_policy` 分流。
- 影响：实现按公式就红、按表就错 1 天（=100×容差）；门槛 kit 无法实现。

### A-P1-6｜性能预算与最坏用例缺失（§8.10、D-13、D-07）
- 问题：Pf-2 要求"20 指标×12 期 ≤300ms、批内 SQL=0"，但 D-13 持仓重建 p95<200ms、DE/expectancy/BHAR 三处 bootstrap B=1000、HHI 需逐日重算；12 期意味着 ≥12 次重建或全历史一次。无任何用例覆盖 bootstrap/重建最坏情况（10 年账、n=1000、连续 12 期、CI 冷启动）。
- 建议：Pf-2 改为"单 period 单指标 p95≤50ms + 批量以 metric_result 物化/缓存命中率为目标"，另立 bootstrap 与重建预算；补 10 年账基准用例。
- 影响：release 前才发现 300ms 不可达，只能砍 CI 或加缓存（引入缓存污染风险）。

### A-P1-7｜验收缺失项：权限/RLS、ETag/缓存、i18n、软删、SDK、版本失效（§8.7/§8.8）
- 问题：① 软删/恢复（`deleted_at`/`version`）在 trades 视图中如何过滤、恢复后 `metric_result` 是否失效，无用例；② ETag/304 的查询指纹是否含 benchmark/rf/include_exploratory、缓存键与失效规则未定义（API-033 只测两次 inputs_hash 相同）；③ i18n 只测 API-034 一条，18 个 explain 英文模板无 golden，英文裸术语会漏；④ C-01~C-26 落地后谁跑 api-snapshot/sdk 未定（verify-p2 只 `--check`）；⑤ 无 `fx_version_break`（definition_version stale）夹具；⑥ 报告复用（P2-09 spec_hash）与指标版本不一致时旧报告复用风险未声明；⑦ 错误码逐条断言（ids+domain 422、未知 domain、INVALID_CURSOR、admin 403）未全列。
- 建议：在 §8.7/§8.8 增加对应行；ETag 指纹显式列入 assumptions/inputs_hash 并加 304→数据变更→200 的用例；i18n 加 18 指标 × en 的裸术语扫描；C 清单标注 snapshot/sdk 的 owner 与时点。
- 影响：上线后"前端/报告/API 三个数"或"改了流水还命中旧 ETag"类问题最难排查。

### A-P2-1｜§6.5 伪代码与文字/复杂度不一致（§6.5、§5.3）
- 问题：伪代码 `for date in [period_start, period_end]` 只算两天，正文要求逐日 HHI/换手/每日对账；§5.3 percentile 写"输出下标 25/974"与 `alpha_permille=(25,975)` 不一致；`B_eff<500` 后"重抽"上限只在 part_test 出现（10B），合并稿丢失。
- 建议：补逐日伪代码与 D·S 复杂度推导；锁定 0/1-based 与最大重抽次数并写进 oracle 共测。
- 影响：实现按伪代码会少算天数；bootstrap 在极端样本上可能不终止或与 oracle 不一致。

### A-P2-2｜β/α 校验链不可复算，SE(α) 公式量纲可疑（§5.5、§7.2.3）
- 问题：§5.5 把 `SE(α)` 写成 `sqrt(s²(1/n+x̄²/(Sxx/n²)))`（Sxx=nΣxx−(Σx)² 是中心和的 n 倍），与标准 OLS 截距标准误不一致；§7.2.3 给出 `β=1.15984016、α_ann=−0.00078896、t=−0.0045、TE=0.02690477、IR=3.0230079`，但未给可复算的 60 日序列，无法判断这些值是否自洽。
- 建议：手算表附 6~10 点回归输入；oracle 用 centered 闭式并做 β/α/t 对拍；把该公式从"设计内已定"移入需 reviewer 复核项。
- 影响：golden 可能是"按错公式算对的错值"，且无法被 mutation 发现。

### 验收剧本逐条走查（前提：D-02/D-19/05/06 全部按推荐落地）
| # | 步骤 | 依赖 | 结论 | 原因 |
|---|---|---|---|---|
| 0 | `make seed-demo && make rebuild` | D-19/D-02、Makefile、05/06/07 | **FAIL** | `rebuild` 目标不存在（`make -n rebuild` → No rule）；`seed-demo` 只建回合，不建 nav/asset/metrics；已有批次即跳过；流水止 2026-06-30，D-19 扩 12 个月仍不含 09 月 |
| 1 | 五卡有数（DEV-PLAN §3.1-1） | 前端 P2-12、nav P2-03、asset P2-16、07 指标 | **FAIL@07**（P2-14 前 CONDITIONAL） | 07 无前端；"净值 vs 沪深300"基准对比 `routers/nav.py:109` 明示本期不算（C-07 未决）；持仓天数/集中度是 expl，D-08 默认不返回；demo 无 09 月流水；live 下 5 指标 blocked |
| 2 | 点胜率下钻（§3.1-2，API-035 + 抽屉） | 06 端点、C-03、P2-12 | **CONDITIONAL** | 后端需 C-03 的 unit 分级；Catalog 断言③"item.trade_id 均可在 /trades 查到"对回合型下钻不成立（C-03 未覆盖）；07 不出抽屉 |
| 3 | `curl …/metrics?ids=win_rate&period=2026-09`（§3.1-5） | 05/06、C-01、demo | **CONDITIONAL** | DEV-PLAN 原文 flat id 在 D-05 后 404（C-01/DEV-PLAN 未同步改）；period=2026-09 n=0；n=12 时 ci_low=null 符合 D-08，但脚本 jq 会输出 null，容易被误判 |
| 4 | `make verify-p2` | §8.11、05/06、P2-14、mutmut | **CONDITIONAL** | 11 步中 3 步依赖非 07 交付（API-038 无 handler、nav/asset/report）；第 7 步 snapshot 需先落 C-01~C-26；第 9 步 mutmut 未安装；与 DEV-PLAN 将 verify-p2 归 P2-14 冲突 → 07 结束不可能全绿 |

> 若坚持"验收剧本不变"：DEV-PLAN §3.1 第 3 步月报（P2-09）与第 4 步 `make demo-broken`（Makefile 无 target、无任务归属）必须另给归属，否则验收根本无法完整执行。

## 上线后最可能被真实账单打脸的 3 个场景
1. **公司行为 + 不复权**：真实账单出现送转/配股/红利税（fixture 未覆盖，D-21 又明确不复权），次月胜率/盈亏比/集中度/BHAR 与券商 App 全部对不上；用户第一反应是"胜率算错"，而 golden 全绿无法自证。
2. **一行坏数据 = 全表红**：真实账单常有一行断链/缺资金余额/历史超卖；按 §6.3 会 `blocked` 全部指标（或超卖→全表 degraded），五卡全红或回撤/β"unreliable"。D-19 只修 demo，不修真实导入的降级策略——产品在"最需要可信"时什么都不给。
3. **live 缺基准 + 交易日历近似**：D-03 让 timing/selection 5 个核心指标在生产上永久 blocked（宣传"择时/选股诊断"打开是空的）；D-09 的 weekday 日历与不复权让持仓天数/BHAR 与同花顺差 1–3 天、年化约 2%，用户逐条对账后不再信任全部指标。

---
> 说明：本文件为独立红队评审材料，只读核对设计/代码/夹具；未修改设计文档、生产代码或其它评审文件。
