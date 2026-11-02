# 五因子归因（Period2-08）设计交叉评审记录（2026-11-02）

> 评审对象：[`docs/architecture/2026-11-02-five-factor-attribution.md`](../architecture/2026-11-02-five-factor-attribution.md)
> 评审方式：**4 名独立评审/验证员只读评审**（口径数学对抗、契约/验收/前端/产品、架构/数据事实核查、独立数值原型验证），Lead 汇总并逐项处置；评审员**未修改仓库任何跟踪文件**（scratch 均在 gitignored `var/`）。
> 结论：**v0.1 REQUEST_CHANGES（2 份 REQUEST_CHANGES + 1 份事实档案 + 1 份数值反例）→ v0.2 文档级 P0 已关闭 → v1.0 人工批准（2026-11-02，A-01~A-11 全部按推荐方向；见 §10）。设计进入实现阶段（先 08a 数据前置）。**

---

## 1. 评审范围与核对基线

评审员已核对真实代码/文档/门禁，而非只读设计稿：

- **口径/数学**：`docs/prd/PRD-v1.md` FR-R7.1/§5.2、`docs/prd/PRD-v2.md` FR-R7/R8、`docs/architecture/v2/API-Catalog.md` §2.3/§6、`docs/architecture/2026-10-31-sector-data.md` §8.2/§8.3/§8.5、`backend/packages/core/src/stock_core/fifo/{engine,types}.py`、`backend/packages/review/src/stock_review/application/{bridge,valuation,assets,portfolio,rebuild}.py`、`metrics/defs/_common.py`、`core/stats/numeric.py`、`ledger/parsers/huatai.py:570-720`；独立数值反例脚本 `var/review-attribution-math/counterexamples.py`。
- **契约/验收/前端/产品**：`API-Catalog.md` §0/§1/§2.3/§6/§7、`DEV-PLAN-v2.md` Period2-08/A.1、`PRD-v2.md` FR-R7/R8/D3、`2026-10-28-monthly-report.md` §8.2、`2026-10-28-dashboard.md` §7；`routers/{metrics,sectors}.py`、`schemas.py`、`openapi_meta.py`、`routing.py`、`deps.py`、`scripts/ci/gate_endpoint_coverage.py`、`tests/contract/test_openapi_contract.py`、`tests/isolation/test_isolation_matrix.py`、`frontend/apps/web/src/{router.tsx,layout/navigation.ts,pages/*}`、`frontend/packages/api-client/{http.ts,contract.ts}`、`apps/web/eslint.config.js`。
- **架构/数据事实**：`pyproject.toml`（import-linter 层规则、mutmut、coverage）、`platform/sector_{live,replay,fixture}.py`、`platform/repositories/sectors.py`、`platform/{ports,config}.py`、`stock_api/cli.py`、`scripts/ci/gate_*.py`、`tests/golden/*`、`fixtures/market/{quotes.json,bill_huatai.xlsx}`；实测门禁基线（venv）：`gate_endpoint` 30/30 + 二期 6/6；`gate_tenant` 受保护 46/46、401 46/46、跨租户 20/20；`api-check`/`api-snapshot --check` 绿；OpenAPI 49 path / 55 operation / 48 JSON 示例。
- **数值验证**：独立整数原型（零 float）`var/review-attribution-numeric/proto.py`，32000 组随机属性测试 / 198315 段、19 条对抗用例、与 core `build_rounds` 交叉 4000/4000、与 `asset_daily` MTM 对账；日志 `run-12000.log`、`run-20000-seed7.log`、报告 `report.md`。

---

## 2. 总体评审结论

- **数学骨架成立**：在"区间内无送转/配股、资产链完整"前提下，`selection+sector+buy_timing+sell_timing−fee = 段净损益` 对 `closed/open`、区间前 lot、部分卖出、T+0、多 lot 逐段成立（买费仅区间内建仓段计入、区间前 lot 的 base 不抵消、卖费方向正确）。数值原型 32000 组 0 误差；与资产口径对账在两级分摊后 32000/32000 残差 0。
- **v0.1 不能直接实现**：共识别 **17 个 P0 根因**（数学 6 + 契约 8 + 架构事实 3），集中在：① 区间边界（周末月末误判 stale / `V_begin` blocked 行被当 0 / 股息现金制）；② 公司行动（送转/配股产生幻影因子或残差吞数）；③ 历史行业成分 look-ahead；④ 契约字段/错误码/门禁计数与真实代码不符（含 401 枚举不存在、golden 路径会打破 G8、越权矩阵漏列）；⑤ 板块指数日线无数据源；⑥ 区间损益无生产者。数值原型另发现 **16 类公式缺陷**（恒等是 plug 恒真、逐段 floor、费用分摊分母、可转债 ×10、股息除息日配对、停牌指数同日、板块二次取整等）。
- **v0.2 已修订**：区间损益/边界/股息口径重写；公司行动整标的剔除（D-12，范围裁决）；D-07 默认 fail-closed + 显式开关；08a 前置（板块指数 + replay 日线）；契约字段/错误码/计数/越权矩阵/前端手写类型逐项冻结；两级 `_allocate` 分摊；残差六桶 + `valuation_gap` 门禁；验收从 5 条扩到 19 条并去 plug 化；估时 8.0~11.5d。
- **人工已批准（2026-11-02）**：A-01~A-11 全部按推荐方向通过（总损益口径、板块语义与 sector §8.3 修订、买点卖点口径与 PRD 勘误、PIT look-ahead 例外与 AGENTS.md 修订、公司行动 M1 剔除范围、前端范围、排期 8.0~11.5d 等）；批准记录见 §10。

---

## 3. 独立数值验证（numeric-verifier）要点

| 项 | 结果 |
|---|---|
| 段级恒等式（Σ因子 = 段参考损益） | 32000 组 / 198315 段，**max 误差 0 分**；但属于代数恒等式（plug/相消），**不能当模型正确性证据** |
| 与 core FIFO 交叉 | 4000/4000 fill 的 buy_cost/sell_proceeds 逐分一致 |
| 与 `asset_daily` MTM 对账 | 干净数据：max 7 分、67.7% 组为 0；A 股整分 tick 4173/4173 组 = 0；改"标的→段两级 `_allocate`"后 **32000/32000 组 = 0** |
| 费用分摊自检 | 数量比例 + 「全部段数量」分母 + 尾差给最后 → 0 失败；逐段独立 floor 会差 1 分并**静默进买点**（反例 3 股买费 100 分 → 33+66=99） |
| 手算 golden | 2 lot、1 次部分卖出、1 次区间前 lot 卖出、1 笔股息 → **Σ因子 = 账户损益 = 1728.40，0 分**（已回填设计 §3.8） |
| 发现的公式缺陷 | D1 恒等式是 plug（真门禁需 coverage/残差桶/资产对账）；D2 逐段 floor（≤段数−1 分/侧）；D3 费用分母（送转/超卖超发或漏发）；D4 送转幻影与残差吞数；D5 配股缴款不在成本；D6 可转债 ×10/债券 divisor；D7 股息除息日与 P0 配对（现金制自洽，权责制需除息日字段）；D8 股息集合必须=区间全部 dividend 行；D9 独立费用/利息/其他分类；D10 板块二次取整（1 亿持仓可差 50 分）；D11 停牌时 I_end 必须与 P1 同日；D12 P0 fallback 与资产侧不可对账；D13 超卖必须阻断；D14 大数中间量禁 SQL；D15 V_begin blocked 行；D16 段必须应用层 `build_rounds` 现算 |
| 处置 | 设计 §3.2/§3.3/§3.6/§3.8/§4.4/§5.3 已按"D 系列"重写；公司行动采纳 D-12 整标的剔除（A-11）；逐段 floor/费用分摊/单步取整/停牌同日写入冻结规则 |

---

## 4. P0 问题与处置（v0.1 → v0.2）

| # | P0 问题（评审证据） | v0.2 处置 | 状态 |
|---|---|---|---|
| P0-01 | **T1 用自然日判 stale**：`V_end.trade_date<T1`/`T1>data_as_of` 把周末月末（2026-02-28/05-31/10-31）误判 blocked，且与自家 `V_end=≤T1 最后一行` 自相矛盾（math P0-1） | §3.2 改用 `latest_workday(min(valuation_end,T1))`/`valuation_end`/`data_as_of` 三对齐；`fx_attr_boundary_weekend` fixture | ✅ 文档级关闭 |
| P0-02 | **区间内送转/配股未建模**：区间前 lot base 取"期末数量×P0"→ 残差虚增（10 送 3 反例 3000 元；配股 1500 元），fixture 未覆盖（math P0-2 / numeric D4/D5） | **D-12 整标的剔除** + `corporate_action_pnl` 桶 + `fx_attr_corporate_action` fixture + P2 数量调整计划（§10.6）；**范围裁决需 A-11 签字** | ✅ 已处置（口径例外待确认） |
| P0-03 | **`V_begin` 边界**：T0 前最后行 blocked（全 0）被当 V_begin；首行 prev NULL 回落 0 掩断链（math P0-3 / numeric D15） | §3.2 以行 `status` 判定（blocked → blocked；区间首行 prev_total NULL → blocked）；**删除 0 回落**；允许真实空账户 0 起点（Lead 终检 F-1 修正）；`fx_attr_p0_blocked` | ✅ 关闭 |
| P0-04 | **§3.4 买费反解写反**（`gross_buy=q×p_b−买费` 与 §3.5 矛盾，照抄费用双计）（math P0-4） | §3.4 统一 `gross_buy=段净成本−买费分摊`、`gross_sell=段净卖出+卖费分摊`，重写逐段代数 | ✅ 关闭 |
| P0-05 | **D-07 单快照 look-ahead**：`period_end<first_sync_on` 用"最早可用快照"= 用未来成分，违反 AGENTS 板块铁律 5/sector §8.5（math P0-5） | D-07 改为**默认 blocked**；显式 `classification_lookahead=deny / allow`（部署开关 + HITL 批准）；修订 AGENTS.md 纳入 C-3；`SECTOR_HISTORY_NOT_COVERED`/`SECTOR_CLASSIFICATION_LOOKAHEAD` | ✅ 文档级关闭（**A-05 待拍板**） |
| P0-06 | **08a 缺失降级 `sector.amount=null`** 与 API-036 断言（rank 1..5、固定含 sector、Σ+residual）冲突，residual 求和未定义（math P0-6） | §4.2/§4.4：**存在可归因候选但零可归因段** → **blocked**（不返回 null/0）；部分缺 → 标的剔除（五因子照给）；无候选标的 → ok/degraded（Lead 终检 F-2） | ✅ 关闭 |
| P0-07 | **错误码不成立**：`UNAUTHENTICATED` 不存在；`FORBIDDEN` 被当 scope 错误；"PAT 无 read→403"无实现（contract P0-1） | §6.3 重写：401 用 `TOKEN_EXPIRED`/`TOKEN_REVOKED`/`MULTIPLE_CREDENTIALS`；admin→403 `ADMIN_CANNOT_READ_TRADES`；删 scope 403 | ✅ 关闭 |
| P0-08 | **benchmark 白名单** 4 个（含 `hs300`）与 `KNOWN_BENCHMARKS` 3 个不一致（contract P0-2 / math P1-11） | §6.1/D-11 复用 `KNOWN_BENCHMARKS={hs300_tr,zz500_tr,csi1000_tr}`；如确需 `hs300` 走 A 类 | ✅ 关闭 |
| P0-09 | **分类时点算法自相矛盾**：`min(period_end,snapshot)` 与"用最早可用快照"冲突；早于 first_sync 时 `memberships_at` 为空 → 全量残差/capital_ratio=0（contract P0-3） | §4.3 冻结算法（先判 first_sync → blocked 或 look-ahead；否则 `min(period_end, membership_as_of)` + 90 天降级）；写入 assumptions/状态机/验收 | ✅ 关闭 |
| P0-10 | **golden 路径会打破 G8**：`backend/tests/golden/attribution/**` 被 loader 全量 glob（schema 需 `metric_id`、MANIFEST/COVERAGE 全覆盖）（contract P0-4） | 改 `backend/tests/attribution/golden/` + **独立 gate**；§12.2 列入 Testing 文档勘误 | ✅ 关闭 |
| P0-11 | **越权门禁工件漏列**：受保护 operation 必须入矩阵；`/attribution` 需 `period` 才能到认证层（contract P0-5） | §6.4 明确 `ADMIN_FORBIDDEN`/`UNAUTHENTICATED_MATRIX` 各加 `("get","/attribution")`、`_REQUIRED_QUERY["/attribution"]="?period=2026-06"`；资源型用例不适用 | ✅ 关闭 |
| P0-12 | **前端 SDK 不止 `make sdk`**：`http.ts` 手写 facade、`contract.ts` 编译期 shape；blocked 整键缺省需防 generated 退化（contract P0-6） | §6.4 增 api-client 手写 `AttributionOut` + shape 钉扎测试（仿 MetricItem）；serializer 用 `model_serializer(mode="wrap")` pop | ✅ 关闭 |
| P0-13 | **blocked 缺省语义未冻结**：`coverage` "或省略"、`base_capital=0.00` 自相矛盾（contract P0-7） | §4.4/§6.3 冻结：blocked 时 `total_pnl/base_capital/factors[].amount/residual/residual_breakdown/coverage 数值键/ratio` 全缺省；`code/name/rank` 保留、`conclusion=null` | ✅ 关闭 |
| P0-14 | **`factors[]` 固定顺序 vs Catalog 断言④ `rank[0].code`**；`fx_single_factor` 未对账（contract P0-8） | §7.3 ④ 改"`rank==1` 的因子 code"；§6.4 同步 Catalog §6 fixture 改名 `fx_attr_single_*`；固定顺序单列断言⑥ | ✅ 关闭 |
| P0-15 | **板块指数日线无数据源**：live `get_board_bars()` 返回 `()`、`upsert_bars` 无调用方、replay `series={}`（arch F1） | **08a 前置任务**（D-14）：live 实现 + `sync-sectors --start` 落盘 + coverage + replay 板块/个股日线扩展；§4.2/§9 | ✅ 关闭（实现前必须完成） |
| P0-16 | **EP-6 在代码中不存在**：`MetricGroup`/`SumToTotal`/`AttributionModel`/`group_version` 0 命中，Extensibility 排 M2 与 DEV-PLAN 二期冲突（arch F2） | D-13/§5.4：M1 不注册 MetricDef/不建 MetricGroup；保留 `AttributionModel` 协议；**ADR 记录** + `Extensibility.md` EP-6 措辞修订（C-5） | ✅ 文档级关闭 |
| P0-17 | **区间损益无生产者**：仓库只有 Dashboard 全历史 `total_profit` 与 bridge 区间分解件（arch F3） | §3.2 定义唯一 MTM 函数 + §3.2.1 桥接式 `total_pnl = bridge_profit + Δ浮盈亏` 独立对账；写进验收⑲ | ✅ 关闭 |

---

## 5. P1 问题与处置

| # | 问题 | 处置 |
|---|---|---|
| 1 | `total_pnl` 与 PRD §5.2.3/API-027 桥接差额（=期初浮盈）未写；Dashboard 差异解释不准（真实差=`other_units`+期初货基） | §3.2.1 增对账公式；R-02 风险描述改写 |
| 2 | ledger 窗口 `[T0,T1]` 与资产行窗口在周末/节假日边界错位（滚入下一交易日） | §3.2 窗口说明：周末现金流滚入下一交易日行；边界落 `valuation_gap` 并披露 |
| 3 | 已清仓后到账股息无归属 | §3.4.1/§3.6：`dividend_without_position` 桶（或可归因标的直接进 selection，不要求有段） |
| 4 | 残差桶不可独立验证（`valuation_gap`=plug 使断言⑦恒真；`unattributed_price_pnl` 符号/货基公式未写死） | §3.6 写死桶公式/互斥/CASH 行规则；§7.2 改"各桶独立复算 + 与资产口径对账"，⑦ 不再靠 plug |
| 5 | 残差门禁双向失效（100 元/21% 拦、99 元/90.9% 放；纯利息月被误判 degraded） | §3.6 `RESIDUAL_LARGE` 只看 `valuation_gap`；已解释桶不触发；比例阈值基于 `V_end` |
| 6 | ratio：`abs(total_pnl)<100分→null` 与断言③冲突；1~100 元区间 ratio 可 ±1e4%；①③⑦ 均 plug 恒真 | §3.7 双分母（`ratio`+`share`）、阈值 `max(100 分, 0.5%×Σabs(amount))`、`ratio_i=div_round(amount_i·1e8,total)` 独立断言；§3.1/§7.2 明示真门禁 |
| 7 | fee 因子不含未归因标的费用（PRD"费用=区间费用合计"偏离）；送转后 `fee_seg` 可负 | §3.4 fee=区间费用合计（含未归因标的）；D-12 剔除公司行动后分母问题消失；`_allocate` + 断言 Σ段费用=trade |
| 8 | D-09 门禁过严/口径不一（status=ok 才出结论；sample_status 只看 n_segments） | D-09 解耦：`total_pnl` 可得 && `n_attributed_segments≥10` && `capital_ratio≥0.90` && `valuation_gap_ratio≤5%`；`blocked` 必封；⑮ 断言 |
| 9 | coverage 分母=Σ段 base 循环依赖（无价标的不可算） | §4.4 `capital_total` 用净成本兜底 + `coverage.base_basis="mixed"` 披露 |
| 10 | M1↔M2 措辞不准（"M2=M1 板块的细化"）：`A=M1 sector−R_b`、`S+I≈M1 selection`，仅无区间内交易/现金流时成立 | §3.9/§10.3 改写精确映射；强调两模型分母不同、不得混算 |
| 11 | benchmark 白名单含 `hs300` 与 `metrics.py:60` 冲突 | 已并入 P0-08 |
| 12 | D-04/D-05 耦合未写明（备选"T0 当日收盘"会与除息日双计） | D-05 改现金制（到账日）+ D-04 取消 same_day fallback，耦合消失；§3.4.1 写明两情形数值 |
| 13 | `total_pnl` 与 `daily_pnl` 逐日口径/边界错位 | §3.2.1 增 `Σdaily_pnl` 校验（≤1 分/边界） |
| 14 | 契约计数只写 55→56，未列 `test_openapi_contract.py:81/82/124`；`OPERATION_META` 未登记 | §6.4 逐行列出（55→56、48→49、text_count=2 不变）；`attribution_get` 元数据 |
| 15 | `MONEY_FIELDS` 清单含已存在项、漏 `residual_ratio`/`share`/示例；`direction` 与 sector over/under 冲突 | §6.4 只增新字段；`RATIO_FIELDS` 增 `residual_ratio/share`；`presentation.tone` 替代 `direction` |
| 16 | `display{bar_pct}` 是第三套命名；metrics 用 `presentation` | §6.2 改 `factors[].presentation{bar_pct,ratio_pct,tone}` |
| 17 | `data_as_of` 定 datetime，metrics 实为 `date`；"三源取最旧"未进验收 | §6.2 改 `date`；⑯ 断言 |
| 18 | §7.3 断言缺口：assumptions 键、coverage/missing 结构、insufficient+summary=null、401、name 中文、blocked 键缺省、固定顺序、data_as_of | §7.3 扩到 19 条（含⑮⑯⑰⑱⑲） |
| 19 | §7.5 curl 脚本不可达/脆弱（demo 可能 degraded；越权断言"金额不同"） | §7.5 改为 pytest 为主；curl 只验 200/恒等/422，不写死 `status=="ok"` |
| 20 | 08f 拆走违反 PRD FR-R7 UI 验收（P0）与 D1 页面可访问 | 保留 08f 在范围；如拆分需 A-08 签字 + SKIP 记录 |
| 21 | 09a 独立交付需改 DEV-PLAN 依赖（A-01） | §8.2/§12.2 增 C-7 与依赖改写项 |
| 22 | 估时算术（分项上界 11.0 非 10.5；08a 未含 0.5d spike；"验收 12 条"实为 19 条） | §9 改 8.0~11.5d（含 spike）；§7.3 19 条 |
| 23 | 漏列门禁：`--cov-fail-under=100`、nightly mutation/golden、`mutation_baseline.json` | §7.4/§6.4 补：core 100% 覆盖已有、attribution 纳入 mutmut ≥85% 并更新 baseline、golden 独立 gate |
| 24 | 前端/契约工件漏列：router/navigation、api-client 手写层 | §6.4/§12.2 补齐 |
| 25 | fixture 路径错且不可控（`backend/tests/fixtures/` 不存在；sector fixture series 空） | §7.1 改 `backend/tests/attribution/fixtures/`+`support/fx_attribution_*.py`，行情/板块通过 app.state 注入受控 replay；08a 补 series |
| 26 | §2/§10 过承诺：跨期相加缺前置；`ratio_basis` 只在 assumptions；漂移轴"前端拼装"违反零口径 | §10.2 加 5 条前置；`ratio_basis` 顶层字段；§10.4 改后端出序列、本期不做 |
| 27 | 细节：`taxonomy="industry_sw"` 与 API 现行 `industry` 不一致；oracle 差异一律进残差会掩盖口径错误；§12.3 缺未回填/未 spike 声明 | §4.1 用 canonical DB taxonomy；§7.2 oracle 逐因子；§12.3 补诚实边界 |
| 28 | 可转债 MV ×10/债券 divisor；残差桶必须复用 `total_market_value/bond_value_units`（numeric D6） | §3.6/§5.3 写死复用资产估值原语 |
| 29 | 停牌时 I_end 与 P1 必须同日，否则板块收益漂进选股（numeric D11） | §3.3/§4.4 写死同日；`SECTOR_BAR_FALLBACK` 码 |
| 30 | `base×r_sec` 大数禁 SQL；中间量 Python int（numeric D14） | §3.7/§5.2 写死 |
| 31 | 段必须应用层 `build_rounds` 现算（open lot 送转后不可从 round 表还原）（numeric D16） | D-15/§3.3/§5.3 写死 |
| 32 | 公司行动/配股→费用分摊分母失真（numeric D3/D5） | D-12 剔除后消失；§3.3 分摊规则写死 |
| 33 | 文档陈旧事实：Dashboard/AGENTS 称"零口径 ESLint 未完成"、"G8 23 项红" | §8.1 事实纠正（ESLint 已启用；G8 实测 25 份通过）；评审记录存档 |

---

## 6. A 类决策与批准结果（2026-11-02 全部批准，按推荐方向）

| # | 决策 | 说明 |
|---|---|---|
| A-01 | 排期/范围（8.0~11.5d vs DEV-PLAN 2d） | 含 08a、前端 08f、验收 08g；裁剪选项见设计 §9 |
| A-02 | D-01 `total_pnl` 口径（MTM vs 已实现 vs 并列） | 推荐 MTM；影响月报/看板一致性 |
| A-03 | D-02 板块因子语义 + sector 设计 §8.3 修订 | 推荐 M1 行业指数贡献 / M2 Brinson；需改 sector 文档与因子名"板块配置"释义 |
| A-04 | D-03 买点/卖点执行口径 + PRD-v1 FR-R7.1 勘误 | 推荐执行口径；追涨/卖飞由 timing 指标承担 |
| A-05 | D-07 PIT 策略：默认 blocked vs 显式 look-ahead 例外；AGENTS.md 铁律 5 修订 | 推荐默认 blocked + 部署开关 + 人工批准例外 |
| A-06 | 08a 数据源 spike 结果（THS 行业指数历史覆盖/列名） | 失败则板块因子不可交付，需 SKIP 记录 |
| A-07 | 残差/覆盖/ratio 阈值（1 元/0.1%/5%/0.90/100 分/0.5%） | 上线前用真实分布校准，建议配置化 |
| A-08 | 前端归因页是否本期交付（PRD FR-R7 UI 验收 vs 排期） | 推荐本期 08f；拆分需签字 |
| A-09 | 性能预算（p95 ≤1.5s）与实测 | 实测后校准；无 PR 级耗时门禁（`perf/` 不存在） |
| A-10 | 历史区间资产表 blocked/stale 可用性 | 资产表构建覆盖优先；老数据可能提示重建 |
| A-11 | 公司行动 M1 整标的剔除（D-12） | 推荐一期剔除 + 残差桶披露；P2 复权调整；否则需 +2~3d 且风险高 |

> 以上 A-01~A-11 已于 **2026-11-02** 人工审核通过，全部按「推荐」方向执行（批准明细见 §10）。

---

## 7. 评审结论

- **v0.1：REQUEST_CHANGES**（口径数学、契约验收各 1 份 REQUEST_CHANGES；事实档案 3 个推翻性结论；数值验证 16 类缺陷）。
- **v0.2：文档级 P0 已关闭**；**v1.0：2026-11-02 人工批准**，设计进入实现阶段（A-01~A-11 全部按推荐方向，见 §10）；实现顺序：**08a 数据前置 → 08b/08c → 08d/08e → 08f/08g**。
- 未闭合项均已在设计/本记录显式标注（A-01~A-11、08a 依赖、公司行动范围、replay 日线、历史 PIT）。
- **SKIP 声明**：`make verify-p2` 不存在（Period2-14 暂停）；`make mutation` 是否在 CI 启用待确认；50 并发性能测试不属于本任务；`fx_attr_*`/golden 尚未落地（本轮只做设计与验证）。

---

## 8. 复现与工件

| 工件 | 路径 | 说明 |
|---|---|---|
| 设计 v0.2 | `docs/architecture/2026-11-02-five-factor-attribution.md` | 本文修订对象 |
| 数值原型/报告/日志 | `var/review-attribution-numeric/{proto.py,report.md,run-*.log}`（gitignored） | 回归命令见 `report.md`；禁 float，纯整数 |
| 数学反例 | `var/review-attribution-math/counterexamples.py`（gitignored） | 送转/配股/股息/费用尾差等最小反例 |
| 架构事实档案 | `var/review-attribution-arch/facts.md`（gitignored） | 逐条 file:line 证据 |
| 契约/验收问题 | 由评审员消息汇总（无独立文件） | 行号已并入设计 §6.4/§7 |
| 门禁基线 | 评审员实测输出 | `gate_endpoint` 30/30 + 二期 6/6、`gate_tenant` 46/46、20/20、`api-check`/`api-snapshot --check` 绿、OpenAPI 49 path/55 op/48 JSON 示例 |

---

## 9. Lead 终检补遗（v0.2 定稿前，非评审员提出）

| # | 终检发现 | 处置 |
|---|---|---|
| F-1 | `V_begin` 行"`total_assets>0`"会误伤真实空账户；blocked 行的 0 与空账户 0 不可区分 | §3.2 改为**以行 `status` 判定**（blocked → blocked；区间首行 `prev_total IS NULL` → blocked），允许真实空账户 0 起点 |
| F-2 | "零个可归因段 → blocked"会误伤仅持债券/货基的账户 | §4.4 改为"**存在可归因候选标的但零可归因段** → blocked"；无候选标的 → `ok/degraded` + 固定句 summary（`capital_total==0` 例外） |
| F-3 | `SECTOR_MODE=live` 下 demo 区间（2026-06）早于首次同步日 → 默认 blocked，手测会误判上线失败 | §4.3/§7.5 明示：手测走 `SECTOR_MODE=replay`（first_sync_on=2026-06-30）或 A-05 批准的 look-ahead；不得用"实盘模式跑 demo 区间"作为 PASS 证据 |
| F-4 | §9 分项估时上界 11.5 与正文/记录 11.0 不一致；验收条数 14/19 不一致；assumptions 键数 17/18 不一致 | 统一为 **8.0~11.5d**、**19 条**、**18 键**（含 `data_sources`） |
| F-5 | 设计 §3.8 golden 表中 A 段 `selection` 含股息（1077.00），与 numeric 原型表格"选股 957 + D 单列"表述不同 | 两表口径已对账：`selection = path − sector + D`，合计 `1274.00` 相同；设计表采用"含股息"口径并注明 D 来源，原型表保留"D 单列"明细，**总数一致** |

---

## 10. 人工审批记录（2026-11-02，A-01~A-11 全部按推荐方向）

> 审批结论：**设计 v1.0 通过**；§11 风险/HITL 全部按推荐方向执行；实现未开始，先做 08a。

| # | 决策 | 批准方向（= 设计推荐） | 生效动作 |
|---|---|---|---|
| A-01 | 排期/范围 | 采用设计 §9 拆分：08a~08g，估时 8.0~11.5 人日；08f 前端页本期交付 | 更新 DEV-PLAN Period2-08（估时/产出/验收）；实现按任务表推进 |
| A-02 | D-01 区间损益 | **MTM 资产口径**（`V_end−V_begin−CF`）+ §3.2.1 桥接式独立对账 | §3.2 生效；月报/看板脚注说明与 Dashboard 全历史差异 |
| A-03 | D-02 板块语义 | **M1=行业指数收益贡献；M2 才 Brinson**（`model=brinson_industry` P3-08） | 修订 sector 设计 §8.3（已加裁决补充）；API-036 `requirements` 按 M1 |
| A-04 | D-03 买/卖点 | **执行口径**（成交价 vs 当日收盘）；追涨/卖飞由 `timing.*` 承担 | PRD FR-R7 口径注（已加）；结论中引用 timing 指标 |
| A-05 | D-07 PIT | **默认 fail-closed**；仅部署开关 `SECTOR_LOOKAHEAD_ENABLED=true` + `classification_lookahead=allow` + 人工批准可用，标 degraded/披露 | AGENTS 板块铁律 5 例外（已加）；sector §8.5 补充（已加） |
| A-06 | 08a 数据源 | 先做 0.5d spike；THS 行业指数落 `market.sector_bar` + replay 日线扩展；失败则 blocked + SKIP 记录 | 08a 列为 P0 前置 |
| A-07 | 阈值 | 采用设计推荐阈值（1 元/0.1%/5%/0.90；ratio 100 分/0.5%），上线前校准、配置化 | §3.6/§3.7/§4.4 生效 |
| A-08 | 前端范围 | **本期交付** `/attribution` 页（替换占位），零口径渲染后端字段 | 08f 保留在范围；PRD FR-R7 UI 验收可执行 |
| A-09 | 性能 | 预算 p95 ≤1.5s（实测校准）；无 PR 级耗时门禁，记录 SKIP | §4.5 生效；08g 记录实测 |
| A-10 | 历史资产表 | 资产表 blocked/stale → blocked + what_to_do；构建覆盖优先 | §4.4 生效 |
| A-11 | 公司行动 | **M1 整标的剔除**（送转/配股）+ `corporate_action_pnl` 残差桶 + `missing[]`；P2 再做数量调整 | D-12 生效；`fx_attr_corporate_action` fixture |

> 批准即代表：C-1~C-9 的文档修订/勘误获准在实现 PR 同批落地；`docs/architecture/2026-11-02-five-factor-attribution.md` v1.0 为 Period2-08 实现与验收的唯一口径依据。
