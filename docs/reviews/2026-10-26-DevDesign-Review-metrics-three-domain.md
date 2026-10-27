# 三域指标实现（Period2-07）设计 · 交叉评审记录

> 审核对象：`docs/architecture/2026-10-26-metrics-three-domain.md`
> 审核版本：**v0.9（1071 行）** → 修订为 **v1.0-rc → v1.0**
> 日期：2026-10-26 ｜ 组织：AI（Team Lead）｜ 类型：三名独立评审员并行 + 第二轮独立验证
> **人工批准：2026-10-26（全部采用推荐方案，记录见设计文档 §13.4）；设计文档定稿为 v1.0。**
> 说明：评审员均为 fresh context、只读；未修改设计文档/生产代码/数据库。数值复算一律用 `Decimal`/`Fraction`/整数标度，**禁止 float**，不采信文档自证。

---

## 1. 审核元信息

| 项 | 内容 |
|---|---|
| 评审员 | `reviewer-methodology`（方法学/统计）、`reviewer-integration`（仓库集成/契约/可执行性）、`reviewer-adversarial`（对抗性/可测性/验收与进度）；第二轮 `reviewer-verifier`（闭环验证） |
| 评审材料 | `docs/reviews/_p207/{review_methodology,review_integration,review_adversarial,review_verification}.md` |
| 核对规模 | 方法学 46 项（18 指标口径 + 20 项数值复算 + 8 项状态语义）；集成 66 条断言；对抗性通读全文 + 验收 5 步逐条走查 + 夹具/估时/命令核对 |
| 已执行基线 | `grep/read` 逐条核对代码（含 `pytest --collect-only` 类只读检查）、`python Decimal` 独立复算、`make -n` 检查 target、迁移目录核对；**未**起服务/写库/改代码 |
| 结论 | 三方均 **REQUEST_CHANGES**（无 APPROVE）；合并 P0 去重 13 条、P1 约 24 条、P2 若干；v1.0 已逐条收敛，第二轮验证见 §6 |

### 1.1 裁决汇总

| 评审员 | 聚焦 | 裁决 | P0 | P1 | P2 | 关键阻塞项 |
|---|---|---|---|---|---|---|
| reviewer-methodology | 口径/统计正确性 | REQUEST_CHANGES | 7 | 8 | 5 | 状态-字段三处互斥；CI 门槛与 PRD 5 处冲突；回撤 n<60 语义；OLS 公式量纲；CI 交接；DE 与夹具冲突 |
| reviewer-integration | 仓库集成/契约 | REQUEST_CHANGES | 2 | 8 | 6 | demo 数据与验收 period 错位；主夹具超卖自相矛盾；mutmut 配置/门禁/装配路径/契约漏项 |
| reviewer-adversarial | 对抗性/验收 | REQUEST_CHANGES | 4 | 7 | 2 | 验收时间轴不可达；字段规则互斥；估时表加不平；HITL 最小集合漏项 |
| reviewer-verifier | 第二轮闭环 | APPROVED_WITH_FIXES（P0=0 / FAIL=5，均已在 v1.0 修复） | 0 | 9 | 8 | 状态矩阵残留、CI 常量、expectancy 中间值、估时加平、ci_stat_id/kit 互斥 |

---

## 2. P0 处置（去重后 13 条，全部已修/已裁决）

| 编号 | 问题（评审证据） | 处置 | v1.0 位置 |
|---|---|---|---|
| **M-P0-1** | `unreliable/stale` 的 value 规则三处互斥（§4.2 无 value vs §6.3/§6.4 有值），违反 Architecture §11"非 ok 不返回 value" | **接受**：统一为 Architecture 口径——`status≠ok`（blocked/stale/unreliable）一律**无 value/无 conclusion**；`insufficient` 有 value（结构性无定义为 null）、`pending_window` 无 value；新增 C-31 勘误 Extensibility §3.3/Catalog §0.8 | §4.2、§6.3、§8.6、C-31 |
| **M-P0-2** | "CI 仅 n≥30"与 PRD 五处冲突（PRD-v2:405/414/422/590、PRD-v1:108/459）；C-16 只改 A26；"API-033 fx_n5 有 value"不成立 | **接受（改判 PRD 口径）**：`ci_min_n=10`，n≥10 即输出 CI；C-16 扩为 5 处整包 + Extensibility §3.1 `30→10`；修正 D-08 的事实描述 | §0 D-08、§4.2、§8.6、C-16/C-17 |
| **M-P0-3** | 回撤 n<60 判 `insufficient`（有 value），违反 FR-R8"日频<60 不出回撤" | **接受**：`pending_window` + **无 value**；"缺失洞导致不足"与"窗口不足"分开表述 | §5.6、§7.2.4 |
| **M-P0-4** | 门槛表 n=0"无 value 键"与全文 value:null 相反；样本 n 与交易日混为一个参数化维度 | **接受**：n=0 → `value:null` + insufficient；`assert_sample_gate` 拆两个轴（n∈{0,5,9,10,29,30} / trading_days∈{59,60}） | §8.6、§8.2 |
| **M-P0-5** | OLS 的 α/SSR/SE 公式量纲错；golden β/α/t/TE/IR 五数不能同时成立（评审反算 t≈−0.0145） | **部分接受**：公式确有笔误（α 双重扣 rf、SSE 少乘 n、SE 分母口径），**已按标准 OLS 重写**；但**拒绝**"t≈−0.0145"的结论——用修正公式对文档 6 点示例独立复算：β=1.15984016、α_ann=−0.00078896、SE=0.00071462、**t=−0.0045**、TE=0.02690477、IR=3.0230079，与原 golden 一致（评审反算基于旧错公式）。新增 6 点输入/中间量供复算 | §5.5、§7.2.3 |
| **M-P0-6** | `ci_sample` 缺统计量交接，DE 的 bootstrap 非均值型；顺序敏感破坏确定性 | **接受（替代方案）**：`MetricValue` 增 `ci_sample`（排序后、仅内存、不落库、上限 2000）+ `ci_stat_id`（指向 `core/stats/estimators.py` 注册表）；引擎按 `sample_policy.ci + ci_stat_id` 派发；不新增 MetricDef 13 字段 | §4.1、§5.3、§8.6、C-19 |
| **M-P0-7** | `data_error>5%→unreliable` 与 DE golden（ok）互斥；阈值分母未定义 | **接受**：删除该降级规则（data_error 一律剔除并计数，由对账/回合计数暴露）；主夹具改为 **0 data_error**，超卖移入 `fx_data_error` | §7.3.1、§8.3 |
| **I-P0-1 / A-P0-1（同因）** | demo 流水锚死 2026-06-30、验收 period=2026-09 → n=0；行情止 2026-09-30 → 前向窗口全 pending；`make rebuild` 不存在；seed-demo 不建 nav/asset | **接受**：D-19 改为三轴对齐（流水 12 个月止于 2026-06-30、日线延到 2026-09-30 前向缓冲、验收 period=2026-06）；seed-demo 收尾触发 rounds→nav→assets；C-25 新增 `make rebuild`/`demo-broken`；§8.1 前向窗口用例用 `FrozenClock=2027-03-31` + bars 延到 2027-03-31 | D-19、§10、§11、§8.1、§8.3、C-25 |
| **I-P0-2** | 主夹具 C4 超卖与 FIFO"不产生负持仓"/asset_daily 流水累计冲突；C5/B5 浮亏口径自相矛盾 | **接受**：主夹具**移除 C4 超卖**（改为独立 `fx_data_error`）；主夹具 30 行 = 12 closed + 3 open；B5 按"不含股息"改 −603.20；C5 200 股一致；fee/turnover 恢复 1250.02/2,605,200（0.00047982）；新增 `fx_oversell` 暴露实现期需统一的语义 | §8.3、§8.4、§7.1.5、§7.3.1 |
| **A-P0-2** | n=0 / 对账 blocked 粒度 / stale 三处互斥 | **接受**：新增唯一"四态 × 指标类别"矩阵（§4.2+§6.3）；对账 blocked 分级（资产类 blocked、回合/费用类 unreliable）；stale 统一无 value（D-08） | §4.2、§6.3 |
| **A-P0-3** | §10 估时加不平（14.7 vs 8.5~9.5）；core 12 vs 10；S10 标 6 列 8；05/06 5d 低估；API-038/verify-p2 归属冲突 | **接受**：S1–S9 重算为 13.5d；**核心 10 / exploratory 8**；S0 改 5~7d；API-038 从 07a 验收移除（归 06/07b）；`verify-p2` 归 P2-14，07 只给增量步骤 | §10、§3.1、§8.11、D-17 |
| **A-P0-4** | §13.3 最小集合漏 D-03/D-05/D-11/D-13/D-14/D-18/D-28；C 清单无 owner；FR-R6 与 D-08 潜在冲突 | **接受**：§13.3 补齐 16 行；C 清单加 owner/时点（开工首日一次性提交）；明确 `fx_de_pos30` **必须含纸面亏损**，否则 FR-R6"DE>0 且 CI 不含 0"不可达 | §13.3、§9、§8.2 |

---

## 3. P1 处置（关键项）

| 编号 | 问题 | 处置 | 位置 |
|---|---|---|---|
| M-P1-1 | pnl_ratio 先量化再相除 → 1.63380598 | **接受**：改为有理数合并后一次量化 → 1.63381463（decomp 的 avg 仍量化展示） | §7.1.2 |
| M-P1-2 / I-P2-2 / A-P1-5 | 自然日 `+1` vs 手算 68/12；median 5.00 vs 6.00 | **接受**：自然日=`(close−open).days`（A1=7）；median=6.00；§7.1.3 同步 | §7.1.3、§8.4 |
| M-P1-3 | expectancy 用 0.6667/0.3333 得 1042.28；"mean_pnl=1200"无据 | **接受**：改为有理数一次量化（主夹具 E=1042.16 ≡ mean_pnl，n_zero=0）；"E vs mean_pnl"示例改到含零回合的 perf 单测小夹具（164.29 vs 200.00）并标注 | §7.1.7、§8.4 |
| M-P1-4 | B=1000 vs PRD-v1"重抽样 10000 次" | **部分接受**：保留 B=1000 并写入 D-07 备选④（B=10000，成本×10）；抽样单元写入 C-32 修订 FR-R6；**需人工在 D-07 拍板 B** | D-07、C-32 |
| M-P1-5 | D-11 年化基数仍不一致；"年化平均市值"文本≠实现；TWR<0 语义反转 | **接受**：文本改"期间日均持仓市值"；TWR 年化统一 `365/(last−first)`；`twr_eaten_pct` 仅 TWR>0 输出，否则 null+unreliable | D-11、§7.1.6 |
| M-P1-6 | Testing §12.1 的 `DE=PLR−PGR` 未纳入勘误；D-23 引用不存在；Extensibility Clock-Random 未修订 | **接受**：新增 C-32（PRD A18/A19/A21 + Testing §12.1 DE 方向）；C-20 改述"测试文档单源修订"；C-30 覆盖 Extensibility §3.2/U2 | C-20/C-30/C-32 |
| M-P1-7 | 前向窗口 golden 与 FrozenClock=2026-09-30 互斥（h=60 需到 2026-11） | **接受**：selection/fly/fwd20 用 `FrozenClock=2027-03-31` + bars 延到 2027-03-31；每份 golden 标注 as_of；h=60 的纯函数单测可用手工 60 点序列 | §8.1、§8.3 |
| M-P1-8 | BHAR 两腿起点未对齐；FR-R4 β 调整/行业基准无 C 项；不复权对 α 的系统偏差未披露 | **接受**：两腿统一锚 `bench_date[j0]` + `c0_lag_days`；D-21 增列"α/IR 被股息率压低方向"；FR-R4 财报/β 调整另立（C-04/D-04 已含） | §7.4.1、D-21 |
| I-P1-1 | verify-p2/rebuild/demo-broken 不存在；路径缺 backend/；缺静态检查 | **接受**：C-25 扩为"补 target + verify-p2 复用 verify-p1 静态检查 + test-p2 + backend/ 前缀 + 前端 tsc"；§8.11 补 12 步 | C-25、§8.11 |
| I-P1-2 | gate_endpoint_coverage 只覆盖一期 30 端点且只查引用 | **接受**：C-27 扩展 phase=2 + ≥3 用例；§8.8 改述 | C-27、§8.8 |
| I-P1-3 | mutmut 配置按 2.x 写 | **接受**：改 3.x 键（`source_paths/pytest_add_cli_args_test_selection`）、产物 `mutmut export-cicd-stats`；保留 2.x 回退说明 | §8.9 |
| I-P1-4 | ci_sample 不足复算 DE CI | **接受**：见 M-P0-6（`ci_stat_id` + 排序 + 不落库） | §4.1、C-19 |
| I-P1-5 | stale 语义三处冲突 | **接受**：统一 Architecture 口径（非 ok 无 value），C-31 | §4.2、C-31 |
| I-P1-6 | D-13 构建器放 core 却依赖 review 原语（违反 L2） | **接受**：拆 `core/metrics/holdings.py`（纯）+ `review/application/metrics_positions.py`（装配）；X-2 同步 | D-13、X-2 |
| I-P1-7 | §9 漏 8 项契约 | **接受**：C-27~C-32 覆盖（gate/装配/API-079/SDK/Testing/PRD A18/A19/A21）；C-05 补 `metric_result.decomposition` 列 | §9 |
| I-P1-8 | daily_bars 无装配路径 | **接受**：C-28 增 `MARKET_BARS_FILE` + 双源能力声明；seed-demo 双写 | C-28、§6.4 |
| A-P1-1 | 三方比对退化（8 份 golden 由 oracle 生成；oracle 与生产同源） | **接受**：新增"防退化铁律"——oracle 不得沿用生产化简（逐日连乘/暴力 MDD/成对差 Gini/Fraction DE）；≥10 个核心指标必须有独立手算表；`expected_source.kind=oracle` 不得 approved | §8.5 |
| A-P1-2 | 夹具缺送转/配股/停牌/退市/版本断点；合并稿 fee 口径漂移无记录 | **接受**：新增 `fx_corporate_actions/fx_recon_break/fx_suspension/fx_delisted/fx_version_break/fx_n29/fx_n59/fx_oversell`；fee 口径变化在 §2 本记录 P0 I-P0-2 说明（1279.12→1250.02 是移除 C4 超卖行所致） | §8.3、本记录 §2 |
| A-P1-3 | 五卡与 exploratory 划分冲突；nav 基准对比未决 | **接受（部分）**：卡片请求 `include_exploratory=true`；`core_conclusion` 只管"行动建议"不管可见性；nav 基准对比列入 C-07（实现时复用 timing.beta core 或在 summary 删除字段，需人工选） | §11、C-07 |
| A-P1-4 | asset_daily 用逐自然日行"伪装日频"，A26 门槛被绕过 | **接受**：新增 `price_refresh_density` 门禁（<0.8 → drawdown/beta unreliable 无 value）；D-02 日线落地后应 ≥0.8 并断言 | §6.4 |
| A-P1-6 | 性能预算与最坏用例缺失 | **接受**：Pf-2 拆两级（单 period 单指标 50ms；批量允物化/SQL）+ 最坏用例 2s + D-13 降级顺序 | §8.10、R7 |
| A-P1-7 | 缺软删/ETag 指纹/i18n/SDK/版本失效用例 | **接受**：§8.7 增 4 行；`fx_version_break` 夹具；C-29 补 API-079 详约/ETag/SDK | §8.7、C-29 |
| A-P2-1 | §6.5 伪代码只算两天；percentile 索引不一致；重抽上限丢失 | **接受**：伪代码改逐日；`pct_index` 0-based 25/974 统一；补抽上限 10×B / `B_eff<B/2` | §6.5、§5.3/§5.5 |
| A-P2-2 | β/α 校验链不可复算；SE 公式可疑 | **接受**：公式已修正；§5.5 补 6 点输入与中间量（β/α/SE/t/TE/IR/超额）供独立复算 | §5.5 |
| I-P2-1/2/3/4/5/6 | nav_daily 表述、自然日、行号/名目不实、依赖缺 positions_end、规模数字、变异产物 | **接受**：均已修（第 5 章/§2/§8.9/§8.10）；`WinRateDef.dependencies` 补 `positions_end`；规模统一 10 年 ≤4e3 日 | 全文 |
| M-P2-1~5 | 手算例输入不全/percentile 矛盾/PRD 缺项/夹具 B5/工程细节 | **接受**：手算标注"仅函数单测校验值"；percentile/补抽已统一；C-32 补 A18/A19/A21；B5 改 −603.20；`ci_sample` 不落库+上限 2000；`assert_purity` 允许 `date`；`as_pct` HALF_EVEN 仅展示 | §8.4/§8.6/§5.3 |

---

## 4. 评审员之间的分歧与 Lead 裁决

| 分歧 | 各方观点 | Lead 裁决 |
|---|---|---|
| stale 是否返回旧值 | 方法学/集成：按 Architecture §11 非 ok 无 value；Extensibility §3.3：stale 返回旧值 | **按 Architecture**（非 ok 无 value）：API 门禁优先，避免"旧数字被当新数字"；以 C-31 勘误 Extensibility（保留旧值供运维可查 `decomposition`/DB） |
| CI 从 n≥10 还是 n≥30 | Extensibility 文档 `ci_min_n=30`；PRD 5 处要求"N<30 附 CI" | **按 PRD：n≥10 出 CI**；C-16/C-17 改 Extensibility |
| OLS 的 t 值 | 方法学反算 t≈−0.0145、五数不自洽 | **保留 golden 五数**：公式笔误已修，修正公式下 t=−0.0045、五数自洽（附 SE=0.00071462 推导）；评审反算基于旧错公式 |
| B=1000 vs 10000 | PRD-v1 写 10000；性能/成本考虑 1000 | **保留 1000 为推荐 + D-07 备选④（10000）**，人工拍板；抽样单元在 C-32 修订 FR-R6 |
| golden 目录 | 方法学：Testing 无 `golden/<id>/`，双源理由不成立 | **保留单源 `backend/tests/golden/<metric_id>/`**（Extensibility §12），C-20 改为"Testing §9.1 同路径修订"，删除"双源冲突"表述 |
| 验收 period | 集成：改 period；对抗性：数据三轴对齐 | **采纳三轴对齐**：流水止 2026-06-30、行情延到 2026-09-30、验收 period=2026-06；同时保留"由 data_as_of 反推"原则 |

---

## 5. 手算表修正记录（v0.9 → v1.0）

| 项 | v0.9 | v1.0（修正后） | 依据 |
|---|---|---|---|
| `perf.fee` | 0.00047982（初版）→ 0.00048123（含 C4 行） | **0.00047982**（1250.02/2,605,200；佣金 651.32+印花 598.70） | 移除主夹具 C4 超卖行后重算 |
| `behavior.hhi` 小例 | HHI_eq 0.54207904（误用含现金分母） | **0.87630874**（Σ市值分母）/ HHI_total **0.58765819** | Decimal 复算（方法学/集成一致确认） |
| `behavior.de` | 早期草稿曾用金额加权 0.13302974 | **+0.22222222**（笔数口径 G=8/L=4/g=1/l=2） | D-20（Odean） |
| 自然日 | 公式 `+1`、median 5.00 | **`.days`、5.66666667、median 6.00** | 68/12 复算 |
| `perf.pnl_ratio` | 中间量化 1.63380598 风险 | **1.63381463**（一次量化） | 有理数合并 |
| `perf.expectancy` | 0.6667/0.3333 → 1042.28；mean_pnl=1200 无据 | **1042.16**；mean_pnl=1042.16（n_zero=0） | 有理数 + 夹具复算 |
| B5 浮亏 | −503.20（含股息） | **−603.20**（不含股息，按定义） | D-13/§7.3.1 |
| `timing.drawdown` Ulcer | 0.03971421（11 个 nav 值重复锚点） | **0.04165262**（n_obs=10） | 定义统一 |
| 主夹具费/成交额 | 1279.12 / 2,658,000 | **1250.02 / 2,605,200** | 移除 C4 行 |

---

## 6. 第二轮独立验证（v1.0）

> 验证员：`reviewer-verifier`（fresh context，只读）；报告：`docs/reviews/_p207/review_verification.md`。
> 对象：v1.0-rc（1111 行，md5 `2556a286…`）｜方法：`Decimal(prec=40)` 独立复算 + 只读 grep/read。
> **结论：APPROVED_WITH_FIXES — P0=0，PASS 4 / FAIL 5；5 项 FAIL 已在本版逐条修复（见下），复核不重开设计。**

| # | 核查项 | 结果 | 关键证据/修复 |
|---|---|---|---|
| 1 | 状态-字段四态矩阵唯一且全文一致 | PASS（修复后） | V-P1-1：§6.3 unreliable/degraded 行 value 改"无"、§8.6"除 insufficient 外均无 value 键"；§13.3 D-08 标注破坏性 |
| 2 | CI 门槛统一 `n≥min_n(10)` | PASS（修复后） | V-P1-2：§4.3 P5 `MIN_CI_SAMPLE=30→10`、引用 C-14→C-17 |
| 3 | OLS 公式与 6 点示例自洽 | PASS | β=1.15984016、SE=0.00071462、t=−0.0045、TE/IR/超额逐位一致；已补 6 点输入脚注 |
| 4 | 手算表 15 项 Decimal 复算 | PASS（修复后） | V-P1-3：expectancy 中间值改 `(2/3)/(1/3)` → 1042.16083→1042.16；其余 14 项零偏差 |
| 5 | 主夹具 30 行自洽、无超卖 | PASS | 余额链 30/30 闭合（期末 188749.98）；12 closed+3 open；B5 −603.20；C4 已移除 |
| 6 | D-19 三轴 / seed 链 / make rebuild | PASS | 流水 12 个月止于 2026-06-30、行情到 2026-09-30、验收 period=2026-06；C-28 装配/C-25 target |
| 7 | §10 估时加平、core10/expl8 | PASS（修复后） | V-P1-4：07a=9.0~9.5、07b=4.0~4.5、ΣS1..S9=13.5，总量统一 13.0~14.0 |
| 8 | 契约修订/门禁闭环 | PASS（修复后） | V-P1-7/8：C 范围统一 C-01~C-34、C-33（DEV-PLAN §3.1）/C-34（from/to 半开）已立项 |
| 9 | 新引入矛盾 | PASS（修复后） | V-P1-5：kit 字段集补 `ci_stat_id`；V-P1-6：D-11 去 `|twr_amount|`、仅 TWR>0 输出；V-P1-9：状态头改"待修项已修+待拍板 §13.3 27 项" |
| 10 | 未闭环/需拍板 | 见 §7 | 无 V-P0；D-08 口径反转、B=1000/10000、C-33/34 须人工拍板 |

### 6.1 验证员新发现（V-P1）处置

| 编号 | 问题 | 处置 |
|---|---|---|
| V-P1-1 | 非 ok 无 value 的口径反转未闭环（§6.3/§8.6/§13.3 残留） | **接受并修复**：三处一致；D-08 勾选项注明"取代 Extensibility §3.3，前端 stale 不再渲染旧值（破坏性）" |
| V-P1-2 | §4.3 P5 `MIN_CI_SAMPLE=30` | **接受**：改 10，引用 C-17 |
| V-P1-3 | §8.4 expectancy 中间值双舍入 | **接受**：改 `(2/3)/(1/3)` |
| V-P1-4 | §10 估时加不平、三处总量互异 | **接受**：07a=9.0~9.5、07b=4.0~4.5、合计 13.0~14.0（ΣS1..S9=13.5） |
| V-P1-5 | `ci_stat_id` 未进契约 kit 字段集 | **接受**：字段集补 `ci_stat_id` |
| V-P1-6 | D-11 只改 §0、§7.1.6/C-15 仍用绝对值（分支不可达） | **接受**：去绝对值，仅 TWR>0 输出 |
| V-P1-7 | C 清单账实不符（写到 C-32/C-30，C-32 乱序） | **接受**：统一 C-01~C-34 并按序重排 |
| V-P1-8 | DEV-PLAN §3.1 period、from/to 半开无 C 项 | **接受**：补 C-33/C-34 |
| V-P1-9 | 状态头结论先行 | **接受**：改为"5 项已修 + 待拍板 §13.3 27 项" |

---

## 7. 仍未闭环（需人工拍板/实现期解决）

| # | 项 | 状态 |
|---|---|---|
| 1 | D-20 DE 方向（Odean PGR−PLR，勘误 A15） | **待人工拍板**（阻塞 behavior.de golden 冻结） |
| 2 | D-02 日线夹具、D-19 演示数据扩展 | ✅ 已批准（2026-10-26） |
| 3 | D-06 估时/拆批（13.0~14.0d + 05/06 5~7d） | ✅ 已批准（2026-10-26） |
| 4 | D-07 B=1000 vs 10000 | ✅ 已批准：B=1000 |
| 5 | D-03 live 基准（5 个指标生产永久 blocked） | 待人工拍板 |
| 6 | D-04 selection 只做价格类 PIT | ✅ 已批准（2026-10-26） |
| 7 | D-05 dotted id（公开契约变更） | ✅ 已批准（2026-10-26） |
| 8 | D-14 rf 默认值（1.5% vs 2.0%） | ✅ 已批准：1.5% |
| 9 | D-08 非 ok 无 value 取代 Extensibility §3.3（前端 stale 不再显示旧值，破坏性） | ✅ 已批准（随 D-08 推荐） |
| 10 | C-07 nav summary beta/alpha/IR：补实现或删字段 | ⏳ 实现期二选一（不阻塞 07a） |
| 11 | C-33 DEV-PLAN §3.1 验收 period；C-34 from/to 半开语义 | 随契约总包批准 |
| 12 | C-01~C-34 契约修订总包 | ✅ 已批准（2026-10-26） |

---

## 8. 评审结论

- **设计质量**：口径推导与手算表是本期最扎实部分；18 指标口径忠实 PRD A10–A24/A26（未发现偷改分母/方向/窗口），D-20 方向正确。
- **主要缺陷已收敛**：13 条 P0 全部修复或裁决；P1 关键项（CI 门槛、OLS 公式、状态语义、夹具、估时、契约/门禁）已闭环；P2 记录在案或在实现期处理。
- **放行条件**：v1.0 通过第二轮验证（已满足：无 V-P0，5 项 FAIL 已修）；**人工已于 2026-10-26 批准全部推荐方案（设计文档 §13.4）——准予按设计文档 §10 进入实现**。实现前唯一待定：C-07（nav summary beta/alpha/IR 二选一，不阻塞 07a）。
- 变更记录：见设计文档 §14.3。
