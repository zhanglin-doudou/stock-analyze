# 五因子归因（Period2-08）开发设计

> 版本：**v1.0（人工批准，2026-11-02）** ｜ 日期：2026-11-02 ｜ 任务：`docs/DEV-PLAN-v2.md` **Period2-08**
> 状态：**已人工审批通过**（A-01~A-11 全部按本设计「推荐」方向执行）；**实现未开始**（前置 08a）。
> 需求基线：`docs/prd/PRD-v2.md` FR-R7（API-036）、FR-R8（样本门槛）、FR-D3（人话层）；`docs/prd/PRD-v1.md` FR-R7.1/§5.2（五因子定义原文、桥接恒等式）
> 契约基线：`docs/architecture/v2/API-Catalog.md` §1/§2.3（API-036）/§6（验收矩阵）
> 关联设计：[板块数据](2026-10-31-sector-data.md)（§7.4/§8 归因联动）、[Dashboard](2026-10-28-dashboard.md)（§7 未完成项/占位入口）、[月报](2026-10-28-monthly-report.md)（§8.2 归因区块）、[资产表](2026-10-25-asset-daily-table.md)、[指标三域](2026-10-26-metrics-three-domain.md)
> 评审记录：[2026-11-02 交叉评审](../reviews/2026-11-02-DevDesign-Review-five-factor-attribution.md)（v0.1 REQUEST_CHANGES → v0.2 修订 → v1.0 人工批准）
> 数值验证：独立整数原型 `var/review-attribution-numeric/`（gitignored）——32000 组/198315 段、与 core FIFO 交叉 4000/4000、手算 golden 逐分闭合。
>
> **审批记录（2026-11-02，人工审核通过）**：A-01~A-11 全部按本设计「推荐」方向拍板（明细见 §11 与 [评审记录 §10](../reviews/2026-11-02-DevDesign-Review-five-factor-attribution.md)）。据此生效：D-01 MTM 区间损益；D-02 M1 板块=行业指数贡献、M2 才 Brinson；D-03 买/卖点执行口径；D-05 股息现金制；D-07 默认 fail-closed + 显式 look-ahead 例外（部署开关默认关，需人工批准）；D-12 公司行动整标的剔除；D-13 独立端点实现（EP-6 修订）；D-14/08a 为 P0 前置；D-18 前端归因页本期交付；估时 8.0~11.5 人日（A-01）。
>
> **本文定位**：把「五因子归因」从 PRD 的一句话口径，冻结成**可复算、可降级、可扩展**的工程口径。A 类决策已于 2026-11-02 人工批准，实现按 §9 任务表推进（先 08a）。

---

## 0. 决策摘要（A 类已于 2026-11-02 人工批准，全部按推荐方向）

### A. 口径决策（A 类：2026-11-02 已全部批准，按推荐执行）

| # | 问题 | 本设计推荐 | 备选与代价 | 影响/拍板 |
|---|---|---|---|---|
| **D-01** | **「区间损益」`total_pnl` 用什么口径** | **资产表 MTM 口径**：`total_pnl = 期末总资产 − 期初总资产 − 区间外部现金流`（`asset_daily`，含浮盈亏/股息/利息/费用），与 Dashboard headline 同源；并用桥接式 `total_pnl = bridge_profit + Δ浮盈亏` 做独立对账（§3.2.1） | ① 已实现桥接口径：恒等式更简单，但漏浮盈亏，月度"为什么亏"答不全；② 两者并列：前端必须选一个，易误解 | 决定一切因子金额分母；**A-02** |
| **D-02** | **「板块」因子到底算什么** | **M1：行业指数收益贡献**——`sector = base × 行业指数区间收益`（回答"我选的板块本身涨跌贡献了多少"，含大盘项）；**M2（P3-08）才做 Brinson 配置/选择/交互**（`model=brinson_industry`，独立模型、独立分母） | ① 按 `sector-data.md` §8.3 用 Brinson 配置效应当 M1 板块：三效应之和=**超额**≠总损益，必须凭空加"市场"第六因子才闭合，与五因子/`Σ=total_pnl` 冲突（证明见 §3.9）；② 把"行业配置偏离"做成 M1 的 additive 描述字段（P2） | 板块语义与验收 fixture；**需修订 sector 设计 §8.3**；**A-03** |
| **D-03** | **「买点 / 卖点」怎么量化** | **执行质量口径**：买点 = `base − 毛买入额`（买得比"建仓日收盘"低为正）；卖点 = `毛卖出额 − q×卖出日收盘`（卖得比收盘高为正）。可加总、可复算、与"操作稳定性/追涨杀跌"直接对应 | PRD-v1 原文"追涨分位与买入后 5 日回撤的**回归贡献**"：统计口径无法逐分闭合、不可单笔复算、排序不稳；该行为改由 `timing.chase_quantile`/`timing.sold_too_early` 承担，结论中引用 | 可加总性；**需 PRD 勘误**；**A-04** |
| **D-04** | 区间边界参考价 `P0/P1` | `P0 = 严格早于 T0 的最后一个交易日收盘`（不早于 45 天窗口，`PRICE_LOOKBACK`）；`P1 = ≤T1 的最后一个交易日收盘`（停牌用最近可得收盘，与 `valuation.close_on_or_before` 同源；**行业指数必须取与 P1 同一日期**）。**不做 `same_day` 静默兜底**：P0/P1/建仓日/卖出日参考价缺失 → 该标的剔除（§4.4），不做近似 | 用 T0 当日收盘当 P0（把 T0 涨跌算进区间，且与股息现金制冲突）；用期初成本 | 首日损益/区间前 lot 的板块起点；**A-05**（与 D-07 联动） |
| **D-05** | 股息归到哪个因子、按什么时点 | **现金制**：区间内 `category=dividend` 的**到账日**净额（税后）按标的整额**加进 `selection`**（不要求该标的有段）；**不按除息日**、不逐 lot 裁剪历史累计股息。与 `asset_daily` 现金口径一致，天然避免"除息前/后 P0 配对"的双计问题（§3.4.1） | 权责发生制（按除息日）：需新增除息日字段（账单无），且要与 P0 时点严格配对，否则重复计息 | 股息归属与可对账性 |
| **D-06** | 哪些持仓进因子 | **仅"可归因标的"**：A 股股票（非伪代码/非货基/非债/非基金/非新股待上市）**且参与本期**（期末有未平仓 lot、`close_date ∈ [T0,T1]` 的平仓、或区间内有持仓类流水）**且**行业映射可得**且**所需参考价/行业指数可得。债券/ETF/LOF/货基/未映射股票 → **残差**并进入 `coverage`/`residual_breakdown` | 全部标的强制摊入（给债券/货基编造"选股/板块"）；新增第六因子（破坏契约） | 残差规模；P2 资产类别扩展（§10.7） |
| **D-07** | **行业成分时点（PIT）** | **默认严格 fail-closed**：`period_end < first_sync_on` 或 `memberships_at(period_end)` 为空 → **blocked** + `SECTOR_HISTORY_NOT_COVERED`。**可选显式例外**：query `classification_lookahead=allow`（默认 `deny`）时，用最早可用快照并标 `assumptions.classification_lookahead=true`、`status=degraded`、`missing[]=SECTOR_CLASSIFICATION_LOOKAHEAD`；启用需人工批准并修订 `AGENTS.md` 板块铁律 5 | ① 默认就用未来成分（look-ahead，违规）；② 永远 blocked（老账单/历史区间不可用，产品不可用） | 历史可用性；**A-05**；**需修订 AGENTS.md** |
| **D-08** | 因子排序与占比语义 | `rank` 按 **`\|amount\|` 降序**（tie-break 固定因子序）；双分母：`ratio = amount / total_pnl`（贡献率，可超 ±100%，可负）与 `share = \|amount\| / Σ\|amount\|`（构成比，0..1，用于条形宽度）；`residual_ratio = 1e8 − Σratio_i`（plug）；`\|total_pnl\| < max(100 分, 0.5%×Σ\|amount\|)` → `ratio=null` + `ratio_na_reason="PNL_NEAR_ZERO"`（`share` 仍给） | 按 `\|ratio\|` 排序（负 total_pnl 语义翻转、除法放大噪声）；只给单一 ratio（负 total_pnl 时构成比不可读） | 验收②③；**A-07** |
| **D-09** | 结论/文案门禁 | `summary` 允许条件：`total_pnl` 可得 **且** `n_attributed_segments ≥ 10` **且** `valuation_gap_ratio ≤ 5%` **且** `coverage.capital_ratio ≥ 0.90`；与 `status` 解耦（`degraded` 不自动封口，`blocked` 必封）。全量词表断言（禁裸术语）+ 禁买卖建议 | 把 `status=ok` 设为硬前提：任一缺映射即永不出结论（过严）；不看 valuation_gap（结论建立在估值差上） | FR-R8/FR-D3 合规；**A-07** |
| **D-10** | 模型与版本 | `model=five_factor`（默认，`definition_version="1.0"`）；`model=brinson_industry` → **422 `MODEL_NOT_AVAILABLE`**（P3-08 再开放）；`level`/`classification_version` **本期不开放**（THS 行业实际扁平 L1）；响应 additive `ratio_basis` 顶层字段 | 先合成保守 Brinson（数据不具备）；开放 `level` 但只支持 1（假参数） | 契约/错误码；§6 |
| **D-11** | `benchmark` 参数语义 | **M1 五因子不依赖基准**：参数可选，白名单复用 `metrics.application.KNOWN_BENCHMARKS={hs300_tr,zz500_tr,csi1000_tr}`（默认 `hs300_tr`），仅回显 `assumptions.benchmark_id`/`benchmark_total_return_proxy`，不改变金额；非法 → 422 `UNKNOWN_BENCHMARK` | 让 five_factor 也做基准超额（必须引入"市场"第六因子，同 D-02）；另造 4 个白名单（与现网漂移） | 避免"换基准金额不变"被误读；在 `assumptions` 写明 |
| **D-12** | **区间内公司行动（送转/配股）** | **M1 整标的剔除**：某标的在区间内有任一 `bonus`/`rights` 事件（含区间前 lot 跨事件）→ 该标的不进五因子，其区间损益进残差桶 `corporate_action_pnl` + `missing[]=CORPORATE_ACTION_ADJUSTMENT_MISSING`；**P2 再做数量调整**（引擎暴露 lot 数量事件后按调整因子分段，见 §10） | 在 M1 直接做复权调整：需要 lot 级数量事件时间轴（core 未暴露）、配股权责处理、复权 P0/P1 配对，估时 +2~3d 且易错（数值原型已给幻影/残差异常反例） | 覆盖率与残差；**A-11** |

### B. 范围 / 实现决策

| # | 决策 | 结论 | 理由 |
|---|---|---|---|
| **D-13** | 实现位置 | **不走 `MetricDef` 注册**：`stock_core/attribution/` 纯函数 + `stock_review/attribution/` 装配/结论 + `GET /attribution` 独立端点。族不变式（Σ=total_pnl）由端点/组不变量实现，**不新增 5 个 metric def** | 代码事实：`MetricGroup`/`SumToTotal`/`AttributionModel` 全仓 0 命中、无 `core/attribution/`；`DATASET_WHITELIST` 无 sector 数据集；5 因子需共享分段上下文与跨因子不变式。**EP-6 实现形态修订需 ADR 记录**（§5.4） |
| **D-14** | 前置数据任务 | **新增 08a**：板块指数日线（THS 行业）落 `market.sector_bar` + 覆盖元数据 + **replay 夹具扩展（板块指数 + 个股日线）**。这是 08 的 P0 前置，不是可选优化 | 代码事实：`sector_live.get_board_bars()` 返回 `()`、`sync-sectors --start` 被忽略、`sector_fixture.series={}`；replay `quotes.json` 只有 9 个月末点（个股日线不足）。缺板块日线 → 板块因子无数据源；缺个股日线 → 买/卖点参考价失真 |
| **D-15** | 是否新增表/迁移 | **不新增**。用 `ledger.trade` + `review.asset_daily` + `market.sector*` + 行情缓存；回合/lot **应用层 `build_rounds(trades_to_events(...))` 现算**（不读 `review.round/round_fill`，因 open lot 的送转/配股后数量不可还原） | 归因是只读派生；落库会引入口径版本/失效重算面 |
| **D-16** | 同步 / 异步 | **同步端点**（不建 job）。单区间（月/年/自定义 ≤366 天）单用户计算；预算 p95 ≤ 1.5s（§4.5） | 数据量有限；月报异步任务内部调用同一应用函数 |
| **D-17** | 缓存 / ETag | **P0 不做 ETag/304**；响应携带 `inputs_hash` 供 golden/月报快照对齐 | 响应小、计算便宜；ETag 需稳定 superset 指纹，收益低 |
| **D-18** | 前端/月报 | `/attribution` 占位页**本期替换**为只读页；Dashboard 入口沿用 `links.attribution`；月报 09b 接同一 `build_attribution()` 应用函数（**09a 仍可先交付占位**，但需人工确认 DEV-PLAN 依赖改写） | PRD FR-R7 "UI 验收：必须（为什么亏卡）"；前端零口径 |

### C. 与既有文档的冲突（本文提出修订，实现 PR / 人工批准时完成）

| # | 文档 | 现文 | 本设计 | 处置 |
|---|---|---|---|---|
| C-1 | `2026-10-31-sector-data.md` §8.3 | "板块因子 = Brinson 配置效应折算金额；选股 =（S+I）×base_capital" | M1 板块 = 行业指数收益贡献；M2 才做 Brinson 三效应 | 修订 §8.3 增加 M1/M2 分层，保留 M2 原裁决 |
| C-2 | 同上 §8.5 | "首次同步日之前区间一律 `not_covered`/blocked，不得近似" | M1 默认 blocked + 显式 look-ahead 例外开关（D-07）；M2 维持严格 | 修订 §8.5 限定 M2；M1 新增披露字段 |
| C-3 | `AGENTS.md` 板块铁律 5 | "缺失即 fail-closed，禁止 assumed 近似" | M1 的 `classification_lookahead=allow` 是**显式例外**（默认关闭、HITL 批准、文档修订） | 人工批准后修订 AGENTS.md；未批准则 D-07 只保留默认 blocked |
| C-4 | `PRD-v1.md` FR-R7.1 买点/卖点 | 追涨分位/卖飞与后市的回归贡献 | 执行口径（D-03），行为由 timing 指标承担 | PRD 勘误 |
| C-5 | `Extensibility.md` EP-6 | 归因 = `core/attribution` + `review/metrics/defs/attr_*.py`，`MetricGroup`+`SumToTotal` | 协议保留，M1 不注册 metric def、不建 MetricGroup | EP-6 备注修订 + ADR 记录（D-13） |
| C-6 | `API-Catalog.md` §2.3 API-036 | 未列 `residual_ratio/sample/coverage/missing/links/presentation` | additive 增补（§6.2） | Catalog 增补 + `api-snapshot` + 契约测试计数 |
| C-7 | `2026-10-28-monthly-report.md` §8.2 | 09b "调用 `GET /attribution` 同源应用函数" | 明确函数名与快照 DTO | 实现时对齐；09a/09b 依赖需 A-01 确认 |
| C-8 | `DEV-PLAN-v2.md` Period2-08 | 2 人日、验收 3 条、依赖 07/17 | 8.0~11.5 人日（含 08a、前端、门禁）；验收 19 条；新增 08a 前置 | 更新估时/依赖/验收（§9）；**A-01** |
| C-9 | `docs/architecture/v2/Testing.md` | 覆盖门槛写 `attribution.py ≥85%`、golden 路径 `expected/attribution/`（路径已过时） | 维持 ≥85% 变异；golden 落 `backend/tests/attribution/golden/`（不进既有 `tests/golden/` loader，避免 G8 结构门禁误伤） | Testing.md 勘误 + 独立 gate |

> **C-1~C-9 随 A 类批准一并生效**：本轮已在 `AGENTS.md`（板块铁律 5 例外）、`2026-10-31-sector-data.md`（§8.3/§8.5 裁决补充）、`Extensibility.md`（EP-6 实现形态注）、PRD（FR-R7 口径注）写入状态/口径注；其余（Catalog/DEV-PLAN/月报/Dashboard/Testing）在实现 PR 同批落地。

---

## 1. 背景、目标与非目标

### 1.1 现状（2026-11-02 事实基线）

- `GET /attribution`（API-036）**未实现**：`contracts/openapi.json` 无 `/attribution`（49 path / 55 operation），`stock_api/routers/` 无 attribution router，前端 `/attribution` 是 `FeaturePlaceholder`（`frontend/apps/web/src/router.tsx:86-94`，导航 `placeholder:true`）。
- 已具备地基（人工验收通过）：FIFO 回合（core `build_rounds` + `review.round/round_fill`）、净值/TWR（`/nav`）、资产表（`review.asset_daily`，含 `daily_pnl/unrealized_pnl/holding_cost/cashflow`）、18 指标（`/metrics`）、Dashboard、板块数据底座（`market.sector*` + API-087/088）。
- 关键缺口（事实核查）：① **板块指数日线无生产者**（live `get_board_bars` 空实现、`SectorRepository.upsert_bars` 无调用方、replay `series={}`）；② 行业成分只有首次同步日之后（replay=2026-06-30，live=部署日）；③ 基准设置（Period2-11）未实现，`KNOWN_BENCHMARKS` 仅 3 个；④ **区间损益无生产者**（只有 Dashboard 全历史 `total_profit`）；⑤ 行情 replay 夹具只有 9 个月末点（个股日线不足）；⑥ EP-6（`MetricGroup`/`AttributionModel`）在代码中不存在，且 Extensibility 排 M2 与 DEV-PLAN 二期冲突。
- 产品侧已明确（`2026-10-30-metrics-product-optimization.md` §7.4）：归因模型需**另起设计文档（口径/样本/披露/验收）**——本文即该文档。

### 1.2 目标（可验收）

1. `GET /api/v1/attribution?period=YYYY-MM&model=five_factor` 返回 5 因子 + 残差，`Σfactors.amount + residual = total_pnl`（≤0.01 元）；**真门禁不是恒等式本身（plug 恒真），而是**：受控 fixture 下 `residual==0`、`coverage`/`missing[]` 完整、`residual_breakdown` 各桶独立可复算（§7.2）。
2. 每个因子带**人话结论**（无裸术语、无买卖建议）与排序；单因子主导 fixture 排序正确。
3. 明确**残差去哪了**：残差必须拆到固定桶（未归因价格损益/未归属股息/利息/打新收益/其他现金流/公司行动/估值差），桶之和=residual。
4. 明确**降级**：缺板块指数、缺成分映射、缺行情、资产表 blocked/stale、区间含公司行动时返回 200 + `status/missing[]`，**绝不用 0/等权/未来成分冒充**；无法给 `total_pnl` 时 `status="blocked"` 且金额字段缺省。
5. 结果可被 **Dashboard / 月报 / 前端归因页** 零口径消费；同输入同输出（`inputs_hash` 确定性）。
6. 不新增表/迁移；不改现有 18 指标口径；不重写 FIFO。

### 1.3 非目标（本期明确不做）

- Brinson-Fachler / Carino 多期链接（P3-08；`model=brinson_industry` 本期 422）。
- 区间内送转/配股的数量调整归因（M1 整标的剔除，D-12；P2 实现）。
- 债券/ETF/基金/货基的全资产类别归因（→残差，P2 扩展）。
- 风格因子（FF3/FF5/Barra）与同类对比；归因结果的物化/缓存/趋势页（P3-02 独立任务）。
- 归因 CI/显著性（金额是确定性分解；样本门槛只用于"是否下结论"）。
- 改变既有指标口径、回合 pnl 口径、资产表估值口径。

### 1.4 术语

| 术语 | 含义（本设计） |
|---|---|
| 区间损益 `total_pnl` | 区间内账户总资产变化扣除外部现金流（MTM，含浮盈亏/股息/利息/费用），§3.2 |
| 持仓段 segment | 一个 FIFO lot 在统计区间内的可归属片段：区间内卖出部分 / 期末仍持有部分，§3.3 |
| 基准本金 base | 段在区间起点的市场价值锚：区间前 lot 用 `P0`，区间内建仓 lot 用建仓日收盘 `C_b` |
| 可归因标的 | 能同时拿到行业映射 + 行业指数 + 参考价 + 费用的 A 股股票，§4.4 |
| `residual` | `total_pnl − Σfactors`；拆到固定桶，§3.6 |
| M1 / M2 | M1=本期五因子（金额域，Σ=total_pnl）；M2=Brinson 三效应（收益域，Σ=超额），P3-08 |

---

## 2. 为什么归因是这个系统的"最后一公里"（产品定位）

> 用户原话："整个系统的核心是围绕自己的账单数据，对自己进行数据建模，通过各个指标维度进行分阶段的对比，发现自己的问题进行优化改进。比如：我的板块漂移轴是怎样的、我今年比去年是否是有提升、今年的操作是否很不稳定、我后面应该从哪方面进行提升。"

数据链路是 **账单 → 回合（真实交易结构）→ 净值/资产（真实收益）→ 18 指标（能力切片）→ 归因（金额因果排序）→ 行动（改进）**。前四层回答"我做得怎么样"，**归因回答"钱为什么这样变化、下一刀切哪里"**——没有归因，18 个指标只是 18 个数字。

归因的地位可以概括为四句话：

1. **把"比率"翻译成"钱"**。胜率 45%、买点分位 0.9 是能力刻度；"本月亏损 3,200 元，其中买点 −1,900 元（占 59%）、费用 −410 元"才是能立刻行动的信息。金额域分解是唯一能跨指标、跨域汇总的单位。
2. **是"分阶段对比"的公共标尺**。月度/季度/年度、今年 vs 去年，只有因子定义稳定（`definition_version`）才能逐期相减：`Δ板块贡献`、`Δ买点贡献` 直接回答"提升在选股还是执行"。
3. **把"板块漂移"从感觉变成轴**。板块因子逐期序列 + 行业权重/偏离（M2/presentation）就是用户说的"板块漂移轴"：不是"我持有哪些行业"（描述性），而是"我在哪些行业超配/低配、这些偏离贡献了多少钱"。
4. **是"操作是否稳定"的金额证据**。买点/卖点因子逐期波动 = 执行质量稳定性；费用因子 = 摩擦成本；残差占比 = 还有多少看不懂的钱。三个轴与时间轴并置，"今年操作是否很不稳定"从主观感受变成可复核曲线。

**四类用户问题 → 能力映射**：

| 用户问题 | 现有能力 | 归因补上的那一步 | 本期可回答 |
|---|---|---|---|
| 我的板块漂移轴是怎样的？ | `/sectors` 当前快照的行业权重/主动偏离/集中度（API-087） | 行业指数收益贡献逐期序列 + 主动偏离（M2）→ "板块漂移贡献了多少钱" | 单期板块贡献；**漂移轴曲线 P3**（本期不做，§10.4） |
| 我今年比去年是否有提升？ | `/metrics?period=YYYY` 年度 token；`/trends` 未实现 | 同口径因子逐期相减（需同 `definition_version` + 相邻/同月区间 + 资产边界连续，§10.2） | 给两年各自五因子；差值由月报/趋势页组装（P3） |
| 今年的操作是否很不稳定？ | `timing.*`/`behavior.*` | 买点/卖点因子的逐期金额波动 + 费用拖累 | 单期金额；**逐期稳定性 P3** |
| 我后面应该从哪方面提升？ | 指标 verdict/action | 排序结论 + 最大负贡献因子的固定行动模板（无买卖建议） | 本期给 `summary`/逐因子 `conclusion` |

**为什么必须现在做、且先冻口径**：归因一旦上线，月报、趋势、提醒、对话都会引用；口径（`total_pnl` 定义、板块基准、买点参考价）任何一次修订都会让历史数字不可比。因此本文一次性冻结"可复算 + 可降级 + 可扩展"三件事：金额域恒等式保证可复算，`missing[]`/残差桶保证可降级，`AttributionModel` 协议 + `definition_version` 保证可扩展。**本期不夸大**：§10 的漂移轴/同比/稳定性序列明确标注 P3 与前置条件。

---

## 3. 口径设计（核心）

### 3.1 第一性原理：为什么必须"逐持仓段分解"

「Σ因子 + 残差 = 区间损益」要**在数学上恒等**（而不是靠残差兜底），分解必须满足：

1. **同一空间**：所有因子都是金额（分），每个因子都是 `total_pnl` 的真实切片，不引入"超额"等不同分母的量。
2. **望远镜恒等**：对每一段持仓，因子按"价格路径的连续区间"精确剖分，相邻项首尾相消；整户求和后自动等于总损益。

M1 采用"**逐持仓段的价格路径剖分**"：`买点执行差 → 板块贡献 → 个股超额（选股）→ 卖点执行差 → 费用`，每一步用**同一参考价格序列**（个股收盘 + 行业指数收盘）首尾相接。

> **诚实声明（评审 + 数值验证共同结论）**：段级恒等式 `Σ段因子 = 段参考损益` 是代数恒等式（32000 组随机测试 0 误差是必然，不是模型正确性证据）；`Σfactors+residual=total_pnl` 由于 `residual` 是 plug 也恒真。**真正的质量门禁**是：受控 fixture 下 `residual==0`（因为当期损益可由独立资产口径复算）、`residual_breakdown` 各桶独立可复算、`coverage`/`valuation_gap` 阈值、以及"与 asset_daily 逐分对账"（§7.2）。

### 3.2 区间损益 `total_pnl`（D-01：MTM，资产表口径）

**定义**（全部标度整数，单位分）：

```text
V_begin   = asset_daily 中「严格早于 T0」最后一行（status != blocked）的 total_assets；
            若不存在：取区间内首行的 prev_total_assets（必须非 NULL，否则 blocked）
V_end     = asset_daily 中「≤ T1」最后一行的 total_assets
CF_period = Σ asset_daily[trade_date ∈ [T0, T1]].cashflow_units        # 外部现金流（银证转账）
total_pnl = V_end − V_begin − CF_period
```

- **行状态硬约束**（评审补）：`V_begin` 行 `status=="blocked"`（数值全 0，与真实空账户不可区分）→ **blocked**；区间内首行 `prev_total_assets IS NULL` → **blocked**（断链）；`V_end` 行 blocked → blocked；区间内任一行 blocked → blocked。**允许真实空账户的 `total_assets==0` 起点**（账户存在但期初无资产），但不允许用 0 兜底 blocked 行。
- **交易日对齐**（评审补）：T0/T1 是自然日（月末可能是周六/周日）。`V_end` 的期望日期 = `latest_workday(min(valuation_end, T1))`（现有 `assets.py`/`metrics/datasets.py` 同口径），`V_end.trade_date < 该期望日期` 才判 `ASSET_SERIES_STALE`；`T1 > asset_build_state.data_as_of` 才判 stale。**禁止**直接用 `trade_date < T1` 判 stale（2026-02-28/05-31/10-31 都是周末，会全员误判 blocked）。
- **含浮盈亏**：期末持仓按期末收盘估值（停牌用最近收盘、货基按余额、债券按换算系数——与资产表同一原语），"没卖出的亏损"也在 `total_pnl` 里。
- **外部现金流**只来自 `asset_daily.cashflow_units`（`valuation.CASHFLOW_CATEGORIES={transfer}`，银证转账；场内基金申赎/配股缴款按内部现金流处理，见 `bridge.py` 的 rights 注释），**不**从流水二次推断。
- **窗口与周末**：资产行工作日全覆盖，周末流水由资产构建滚入下一交易日行的 `cashflow_units`；ledger 侧股息/利息/费用按 `trade_date ∈ [T0,T1]` 汇总。**T0/T1 恰为周末时，周末发生、滚入区间外交易日的现金流可能落进 `valuation_gap`**（边界披露，§3.6）。
- **与 Dashboard 的差异（写进 assumptions）**：Dashboard 全历史 `total_profit = V_end − (期初现金+期初持仓成本) − cashflows − other`（`dashboard.py`），期初用**成本**；本归因是任意区间，期初必须用**市值**。两处字段名不同，月报引用以本设计为准并在脚注说明。

#### 3.2.1 与桥接恒等式 / 每日 `daily_pnl` 的独立对账

- **桥接式**（PRD-v1 §5.2.4 / API-027）：当 bridge 平衡（`|residual|≤tolerance`）时，`total_pnl = (trading_pnl + dividends + interest − fees + other) + Δ浮盈亏`，其中 `Δ浮盈亏 = unrealized_end − unrealized_begin`（`asset_daily.unrealized_pnl_units`）。实现时用该式做**独立对账**：`|MTM式 − 桥接式| ≤ 1 分` 才判 `reconciliation_ok=true`，否则 `status=degraded` + `BRIDGE_MISMATCH`。这是 M1 唯一的"非 plug"金额门禁（评审 P1-1）。
- **逐日校验**：`total_pnl ≈ Σ daily_pnl（区间行）`（差 ≤ 1 分/边界），用于发现窗口错位。

### 3.3 持仓段（segment）定义

**段来源**（D-15）：应用层 `build_rounds(trades_to_events(trades ≤ T1))` **现算**（与 bridge 同源），不读 `review.round/round_fill`（open lot 经送转/配股后数量不可还原；round 表只有回合聚合与 fill）。

对每个 FIFO lot（买入日 `d_b`、T0 时点剩余数量 `q0`（区间前 lot）/买入数量（新 lot）、净成本 `C_l=abs(买入 amount)` 含买费），按区间切成：

| 段类型 | 条件 | 起点参考 | 终点参考 |
|---|---|---|---|
| `closed` | 该 lot 的卖出 fill `close_date ∈ [T0,T1]` | `d_b ≥ T0`：建仓日收盘 `C_b`；`d_b < T0`：`P0` | 卖出日参考收盘 `C_s`（`close_on_or_before(close_date)`，45 天窗口） |
| `open` | 该 lot 在 `T1` 仍有剩余数量 | 同上 | `P1`（`close_on_or_before(T1)`） |

- **区间前 lot 的 `q0`**：取 `build_rounds(trades < T0)` 的 `open_lots.quantity_units`（不是原始买入量）；新 lot 的段数量取 fill 数量 / T1 剩余量。
- **公司行动（D-12）**：对每个标的，若存在 `trade_date ∈ [T0,T1]` 的 `bonus`/`rights` 事件（含影响区间前 lot 的送转/配股）→ **整标的剔除**（该标的所有段+股息进残差桶 `corporate_action_pnl`），并在 `missing[]` 标 `CORPORATE_ACTION_ADJUSTMENT_MISSING`；**不**用近似数量拆分（数值原型已给"10 送 10 → 买点 +1000/选股 −1000 幻影"反例）。
- **数量/金额分摊（两级 `_allocate`，禁止逐段独立 floor）**：先算**标的级**期初市值 `start_MV = floor(Σq0 × P0)`、期末市值 `end_MV = floor(Σq_at_T1 × P1)`，再按段数量权重用 `_allocate` 分配到各段（尾差给最后），保证 `Σ段 base == start_MV`、`Σ持有段 end == end_MV`（与资产表逐分一致）。新 lot 的 `base = _allocate(floor(q_entry × C_b), 段数量权重)`。**数值原型：逐段独立 floor 会让资产口径残差 max 7 分；改两级分摊后 32000/32000 组 = 0 分。**
- **费用分摊（评审补）**：买费分母 = 该 lot 在区间内**全部段数量之和**（含 T1 持有段），卖费分母 = 该卖 trade 的**全部 matched fills 数量**，均用 `_allocate`（尾差给最后），并断言 `Σ段费用 == trade 费用`；分母为 0/超卖 → `SEGMENT_DATA_ERROR` 整标的阻断（不静默把尾差塞进买点）。
- **同日买卖（T+0）**：`C_b = C_s` → 板块/选股路径为 0，全部落在买点/卖点执行差。
- **除权/除息**：除息按现金制 D-05 处理（价格路径 + 股息现金，无重复）；送转/配股按 D-12 剔除；**不复权**（沿用 PRD D-21 / ADR-0025），因此**不含公司行动的窗口内价格路径与资产表同源**。
- **停牌与参考日**：个股 `close_on_or_before` 取到的**实际日期**即该段的参考日；行业指数 `I_end` 必须取**与之一致的同一日期**（另有 `close_on_or_before` 兜底时，若日期不一致 → `SECTOR_BAR_FALLBACK` + degraded，避免停牌期指数收益漂进选股）；参考价缺失超过 45 天 → 资产表该日必 blocked → 归因 blocked。
- **超卖/data_error**：`FifoResult.data_errors` 涉及标的 → 整标的阻断（`SEGMENT_DATA_ERROR`），不尝试部分配对（数值原型：partial 匹配会导致毛额/费用二选一失真）。

### 3.4 五因子逐项定义（金额域，单位：分）

记号：`b` = 段基准本金，`path` = 段价格路径损益，`gross_buy` = 段毛买入额，`gross_sell` = 段毛卖出额，`q` = 段数量，`F` = 段费用（正数），`D` = 标的区间股息净额。

| code | name | 公式 | 含义 / 数据源 |
|---|---|---|---|
| `selection` | 选股 | `path − sector + D` | 个股相对行业的超额价格收益 + 区间股息；`path = end_value − b` |
| `sector` | 板块配置 | `div_round(b × (I_end − I_start), I_start)`（**单步取整**，不再先算 `r_sec` 再乘） | 行业指数区间收益 × 基准本金；`r_sec` 仅作展示（`decomposition.sector_return`）。指数点用同源整数序列 |
| `buy_timing` | 买点 | `b − gross_buy`（仅 `d_b ≥ T0`，否则 0） | `gross_buy = 段净成本 − 买费分摊`（方向：买=毛+费 ⇒ 毛=净−费）；买入价低于建仓日收盘为正 |
| `sell_timing` | 卖点 | `gross_sell − q×C_s`（仅 `closed` 段，否则 0） | `gross_sell = 段净卖出 + 卖费分摊`（卖=毛−费 ⇒ 毛=净+费）；卖出价高于卖出日收盘为正 |
| `fee` | 费用 | `−F`（组合级汇总：所有买卖段费用 + 独立费用行；**含未归因标的的费用**，PRD FR-R5.4"费用=区间费用合计"） | 佣金+印花税+其他费（`_common.fee_breakdown` 同口径）；发生金额已含/已扣，单列=从净额反解，**不二次扣除** |
| `residual` | 残差 | `total_pnl − Σfactors`（plug，§3.6） | 未归因标的损益、未归属股息、利息、其他现金流、公司行动、估值差、取整 |

**恒等式（逐段）**：

```text
selection + sector + buy_timing + sell_timing − F
= (path − sector + D) + sector + (b − gross_buy) + (gross_sell − q×C_s) − F
= path + b − gross_buy + gross_sell − q×C_s + D − F
```

- `closed` 段（`gross_buy ≈ q×p_b`、`gross_sell ≈ q×p_s`，`p_b/p_s` 为成交价；拆分后含分摊尾差）：代入 `path = q×C_s − b` 得
  `q×C_s − b + b − (q×p_b) + (q×p_s) − q×C_s + D − F = q×(p_s−p_b) + D − 费用 = 段净损益` ✓
- `open` 段：`sell_timing=0`、`gross_sell` 不存在，`path=q_end − b`、`buy_timing=b−gross_buy`，得 `q_end − gross_buy + D − F` = 期初市值/买入净额到期末市值的净变化 ✓
- **`C_b/C_s` 只在恒等式中充当参考锚并完全抵消**；`p_b/p_s` 只通过 `gross` 进入，成交额/价格取整差不破坏恒等（≤尾差进买点/卖点，±1 分/段，两级分摊后趋 0）。

#### 3.4.1 股息（D-05 现金制）

- 集合 = 区间内**全部** `category=dividend` 行的净额（税后），按 `security_code` 汇总；**不要求该标的有段**（除息前已清仓、到账在区间内 → 归 `selection` 若标的可归因，否则 `dividend_without_position`）。
- 时点 = **到账日**（`trade_date`）∈ `[T0,T1]`；与 `asset_daily` 现金口径一致。**不按除息日**、不用引擎 `lot.dividend_units`（累计口径跨区间会重复计息——数值原型反例 +50 元）。
- `P0` 不要求除息前/后：现金制下价格路径取实际收盘（除息后自然低），股息现金补回，恒等与资产口径均自洽（数值原型两情形均 0 残差）。

### 3.5 费用剥离与"禁止二次扣费"

事实（`huatai.py:570-575`、`AGENTS.md` 坑 1）：账单"发生金额"买入为 `−(成交额+佣金+印花税+其他费)`、卖出为 `+(成交额−费用)`；FIFO lot 成本 `=abs(发生金额)` 已含买费、卖出现金已扣卖费。因此：

1. 净损益里费用已扣过一次；因子里的 `fee` 是**从净额反解**单列：`gross_buy = 段净成本 − 买费分摊`、`gross_sell = 段净卖出 + 卖费分摊`。
2. 独立费用行（`category="fee"` 且三费列为 0，如账户管理费）计入 `fee` 因子（`fee_breakdown(standalone_fee)` 同口径）。
3. 费用因子不区分买卖方向；`decomposition` 给佣金/印花税/其他费三列（与"费用回本门槛"交叉）。
4. 某笔 trade 费用字段缺失（三列 0 但净额无法反解）→ 该标的剔除 + `FEE_BASIS_UNKNOWN`（degraded），**禁止**比例估算。

---

### 3.6 残差与残差分解

`residual = total_pnl − Σfactors.amount`（整数减法，恒成立）。残差**必须**拆到固定桶，桶之间互斥，且 `Σ桶 == residual`（`valuation_gap` 为兜底 plug）：

| 桶 | 含义（互斥规则） | 公式（分） |
|---|---|---|
| `corporate_action_pnl` | D-12 被剔除的送转/配股标的的**整标的区间损益**（含其股息；因 `Σamount` 已含股息行，不单列） | `(end_MV − begin_MV) + Σ_{该标的区间 trade.amount_units}`；MV 用资产表同源原语（可转债 ×10、债券 divisor、新股按成本） |
| `unattributed_price_pnl` | **不可归因但可估值**的整标的区间损益（含其股息）：D-06 排除的债券/ETF/基金/未映射股票，或因数据缺口被剔除的 A 股（缺行业指数/缺参考价/缺费用口径/超卖）；**不含 `new_stock` 标的**（见 `ipo_pnl`） | 同上；无行情/无法估值 → 不进本桶（进 `valuation_gap`）；`missing[]` 给出各自 reason |
| `dividend_without_position` | 不属于上述两类的"无持仓但区间到账"股息（应极少） | `Σ dividend 行 amount`（标的未进入任何标的级桶，且未计入 selection） |
| `interest` | 利息（货基收益/结息；`category=interest`，代码多为 CASH） | `Σ interest 行 amount` |
| `ipo_pnl` | **打新收益**（账户级，与 `interest` 同级）：本期被 `new_stock` 资产类别排除的标的的整标的区间盯市损益（可为负＝破发；标签固定「打新收益」/“New-share (IPO) P&L”） | 同标的级公式 `(end_MV − begin_MV) + Σamount + Σ三费列`；按 `symbol_class=new_stock` 路由，不落 `unattributed[]` 标的明细 |
| `other_cashflow` | 未分类现金流（打新冻结/补差/`other`/NULL 分类） | `Σ 其余非交易行 amount`（排除上面已归集的行） |
| `valuation_gap` | 资产表口径差 + 取整 + 无法逐项解释的剩余 | `residual − Σ其余桶`（**plug，可为负**） |

- **CASH 行不进入标的级桶**（避免与 `interest`/`other_cashflow` 双计）；标的级桶只按 `security_code` 归集。
- **分组（2026-11-04 补充）**：`corporate_action_pnl`/`unattributed_price_pnl` 为**标的级桶**（落 `unattributed[]` 明细）；`dividend_without_position`/`interest`/`ipo_pnl`/`other_cashflow`/`valuation_gap` 为**账户级桶**（不落标的明细，`ipo_pnl` 亦然）。恒等式 `residual = Σ标的级桶 + Σ账户级桶`，其中 `residual_reconciliation.account_level_total = dividend + interest + ipo + other + gap`。
- **容差（2026-11-04 修订）** = 期末账户总资产（`review.asset_daily.total_assets_units` 区间末行）× **0.2%**（标度整数分，向下取整；常量 `RESIDUAL_TOLERANCE_PCT = Decimal("0.002")`）。
- 判定用**未归因金额合计** = `|residual|`（即 `|Σ桶|`，含 `valuation_gap`）：
  - `|residual| > 容差` → `status=degraded` + `RESIDUAL_LARGE`；
  - `0 < |residual| ≤ 容差` → `RESIDUAL_WITHIN_TOLERANCE`（`severity=info`），**不降级**；
  - `residual == 0` → 不产生条目。
- 多桶符号相反时按**合计净额**判定（各桶分别小于但合计超过仍命中）；负残差取绝对值；期末总资产为 0 → 容差 0（非零即超容差）。
- 已解释桶（公司行动/未归因标的/利息/打新收益/其他）**同样计入合计**（原「已解释桶不触发」规则取消）。
- 结论门禁用 `valuation_gap_ratio = |valuation_gap| / max(|total_pnl|, 1 元)`：>5% 不出 `summary`（D-09）。
- 残差**不是错误**：它显式回答"这部分钱归不了因（标的类型/数据缺口）"，用 `coverage`/`missing[]` 量化；**禁止**把公司行动/未归因标的的金额硬塞进"选股"制造假归因。

### 3.7 取整、恒等与确定性

- **禁止 float**：全部标度整数；除法统一 `stock_core.stats.numeric.div_round`（half-up、符号远离零）。
- **单步取整**：板块金额 `div_round(base×(I_end−I_start), I_start)`（**不**先算 `r_sec` 再乘 base，避免二次取整；数值原型：1 亿持仓可差 50 分）；`r_sec` 仅作展示。中间量（`base×(I_end−I_start)` 可 >2^63）全程 Python int，**禁下推 SQL 乘法**。
- **plugs**：`selection = path − sector + D`、`residual = total_pnl − Σfactors` 由定义精确闭合；`residual_ratio = 1e8 − Σratio_i`（plug）。
- **等比/比例**：`ratio = div_round(amount, total_pnl, scale=8)`；`share = div_round(|amount|, Σ|amount|, scale=8)`（Σshare=1e8，`Σ|amount|=0` → null）；`bar_pct` 由 `share` 派生（绝对百分比串）。
  - `|total_pnl| < max(100 分, 0.5%×Σ|amount|)` → `ratio=null` + `ratio_na_reason="PNL_NEAR_ZERO"`（`share`/`rank` 仍给）；否则 `ratio` 允许超 ±100%（因子与总损益反号时）。
  - ⚠️ 不得复用 `portfolio.ratio_units`（分母 ≤0 返回 `None`，负 `total_pnl` 会吞掉全部比率）；新增 `core.attribution.ratio.ratio_of(numer, denom)`（`denom==0` → None，**允许负分母**并按数学符号）。
- **排序**：`rank` 按 `|amount|` 降序，tie-break 固定序 `selection < sector < buy_timing < sell_timing < fee`；`rank` 为 1..5 连续；`residual` 不参与排序。
- **确定性 / `inputs_hash`**：`sha256` 规范化 JSON（键排序、紧凑分隔符），含 `model/definition_version/period/benchmark/classification_lookahead` + 数据集指纹（trades 指纹、asset 序列指纹、`sector_sync_state.checksum/membership_as_of/bar_as_of`、行情 as_of）。相同输入逐字节相同。
- **定义版本**：`definition_version="1.0"`；口径变更（D-01~D-12 任一）→ MINOR/MAJOR++；趋势/历史不得跨版本拼接（P3-02 沿用 API-037 `breaks`）。

### 3.8 手算向量（golden，已由独立整数原型验证）

**场景**：区间 `2025-03`（T0=2025-03-01 周六；P0=02-28 收盘 9.50）；600000 / 行业 S1。
trades：`bA` 01-10 买 600@10.00 费 3.00 → 净成本 6003.00；`bB` 03-05 买 400@12.00 费 2.40 → 净成本 4802.40（当日收盘 12.10）；`d1` 03-14 股息 +120.00；`sA` 03-20 卖 600@11.20 费 6.60 → 净 6713.40（收盘 11.00）；`sB` 03-25 卖 200@13.00 费 2.60 → 净 2597.40（收盘 13.00）；P1=14.00；指数 02-28=1000 / 03-05=1020 / 03-20=990 / 03-25=1071 / 03-31=1122。
V_begin=5700.00（仅持仓市值，现金 0）；期末现金 4628.40、期末持仓 2800.00 → `total_pnl = (4628.40+2800.00) − 5700.00 = 1728.40`。

| 段 | q | base b | path | 板块 | 买点 | 卖点 | 费用 F | 股息 D | 选股 | 段净损益 |
|---|---|---|---|---|---|---|---|---|---|---|
| A_sell（区间前 lot，全卖） | 600 | 5700.00 | 900.00 | −57.00 | 0.00 | 120.00 | 6.60 | 120.00 | **1077.00** | 1133.40 |
| B_sell（新建 lot，部分卖 200/400） | 200 | 2420.00 | 180.00 | 121.00 | 20.00 | 0.00 | 3.80 | 0 | 59.00 | 196.20 |
| B_hold（新建 lot，T1 持有 200） | 200 | 2420.00 | 380.00 | 242.00 | 20.00 | 0.00 | 1.20 | 0 | 138.00 | 398.80 |
| **合计** | | | | **306.00** | **40.00** | **120.00** | **−11.60** | **120.00** | **1274.00** | **1728.40** |

- 断言：`1274.00 + 306.00 + 40.00 + 120.00 − 11.60 = 1728.40 = total_pnl`，`residual=0.00`；`ratio` 各 = 金额/1728.40。
- 要点：区间前 lot 的买费 3.00 **不进本期费用因子**（只计卖费 6.60）；B 的买费 2.40 按 200/400 分摊为 1.20（买）+1.20（持有），卖费 2.60（B_sell）；A 段股息 120 归 `selection`（现金制）。
- 独立验证（`var/review-attribution-numeric/`）：32000 组随机属性测试 / 198315 段，**段级恒等 max 误差 0 分**；与 core `build_rounds` 交叉 4000/4000 fill 一致；与 `asset_daily` MTM 对账：**A 股整分 tick 4173/4173 组残差 0**，采用"标的→段两级 `_allocate`"后 32000/32000 组残差 0（未采用前 max 7 分）；19 条对抗用例（T+0/区间前部分卖出/股息/停牌/缺指数/送转/配股/可转债/超卖/费用尾差/极端值）全部通过。
- **断言分层（重要）**：① 段级恒等 = 实现一致性自检（恒真，不单独作为模型正确性证据）；② 受控 fixture 下 `residual==0` 且五因子逐项等于手算值（**真正的 golden**）；③ `residual_breakdown` 各桶独立复算（不靠 plug）+ `coverage`/`missing[]`；④ 与 asset_daily 逐分对账。

### 3.9 与既有口径/文档的差异（为什么不能直接照抄 PRD/sector 设计）

1. **PRD-v1 FR-R7.1 的买点/卖点**（"追涨程度与买入后 5 日回撤的关联贡献 / 卖飞与卖出后走势的贡献"）是**统计贡献**（回归/分摊），无法逐分闭合、不可单笔复算、排序不稳。M1 改为**执行口径**（D-03）：可加总、可解释、与行为指标互补。PRD 需勘误（C-4）。
2. **sector 设计 §8.3 的"板块=Brinson 配置效应"**：Brinson 分解的是**超额收益** `R_p−R_b = A+S+I`，与"总损益金额"差一个 `base×R_b`（市场项）。若把 A 当板块因子、S+I 当选股因子，二者之和 = 超额金额，剩 `base×R_b + 非交易项` 必须进残差——残差会≈大盘收益（通常远大于因子），"排序/主导项"失去意义，也违反 API-036「单因子主导 fixture 排序正确」。数学上必须补"市场"第六因子才闭合，与"五因子"契约冲突。因此 M1 板块 = `base×r_sec`（= 市场项 + 配置项，按我的板块权重口径），M2 再用 Brinson 三效应细化并单独暴露。
   - **M1↔M2 的精确映射（评审 + 数值验证修正）**：`A = base×(Σ_s w_p,s R_b,s − R_b) = M1 板块 − base×R_b`（在无区间内交易/现金流、BoP 权重口径下）；`S+I ≈ M1 选股`（同口径下），但**不是逐项可加/可替代的关系**：两模型分母不同（M1=total_pnl；M2=超额）、`definition_version` 不同、`ratio_basis` 不同，前端/月报**不得混算或并列相加**。M2 实现时须在响应 `assumptions` 同时给 `model`/`ratio_basis`，页面按 model 分流渲染。
3. **sector 设计 §8.5 的历史 PIT**：严格 fail-closed 会让 M1 对首次同步日之前的所有区间 blocked（live 首次同步=部署日）。M1 默认保持 fail-closed（合规基线），另给**显式 look-ahead 开关**（D-07，默认 deny）；M2 维持严格。开关启用前必须人工批准并修订 AGENTS.md 铁律 5（C-3）。
4. **公司行动**：M1 整标的剔除（D-12），不复权、不做数量调整；P2 在引擎暴露 lot 数量事件后实现（§10.6）。

---

## 4. 数据依赖与就绪矩阵

### 4.1 依赖清单（**只读**，不新增表）

| 数据 | 来源（模块/表） | 用途 | 现状 | 失败时的降级 |
|---|---|---|---|---|
| 流水（含费用三列/分类/现金余额） | `ledger.trade`，`TradeRepository.list_all(user_id)` | FIFO 重放、费用、股息/利息/其他分类 | ✅ 已有 | 空 → 零仓位分支（§4.4） |
| FIFO 回合与持仓 lot | `stock_core.fifo.build_rounds`（应用层对 trade 重放） | 段划分、净成本、open lots、data_errors | ✅ 已有（bridge 同源） | 超卖/data_error → 整标的 `SEGMENT_DATA_ERROR` |
| 资产日序列 | `review.asset_daily`，`AssetRepository.series(user_id, definition_version=ASSET_DEFINITION_VERSION="1.7")` | `V_begin/V_end/CF_period/行状态/浮盈亏` | ✅ 已有 | 缺边界/blocked/stale → **blocked** |
| 个股收盘价 | `MarketDataSource.get_daily_bars`（`CachedMarketDataSource`，**读路径只读缓存**，`MARKET_LIVE_CACHE_READ_ONLY=True`），消费侧 `load_prices(..., cached_only=True)` + `close_on_or_before` | 建仓/卖出/边界参考价、期末估值 | ✅ 已有（**replay 夹具仅 9 个月末点，08a 需补日线**） | 缺价 → 该标的剔除（残差）+ `PRICE_REF_MISSING` |
| 行业归属（SCD2） | `market.sector_member`，`SectorRepository.memberships_at(taxonomy="industry_sw", as_of=…)` | 标的→行业映射 | ⚠️ 只有首次同步日之后 | 缺 → 整标的剔除 + `SECTOR_MAPPING_MISSING` |
| **行业指数日线** | `market.sector_bar`，`SectorRepository.board_bars(keys, start, end)` | `sector`（唯一来源） | ❌ **无生产者**（§4.2） | 零个可归因段 → **blocked**；部分缺 → 该标的剔除（残差） |
| 行业目录/分类版本 | `market.sector`，`SectorRepository.list_catalog()` | 名称、`classification_version`、层级 | ✅ 已有 | 缺 → `SECTOR_DATA_MISSING` |
| 板块同步状态 | `market.sector_sync_state`（含 `bar_as_of`） | 新鲜度/覆盖披露 | ✅ 表已有；08a 写 `bar_as_of` | 缺 → 按 `bar_as_of=null` 处理 |
| 基准 | 现有 `benchmark_series`（hs300_tr ETF 代理） | 仅 `assumptions` 回显（D-11） | ⚠️ 代理口径 | 不影响金额；`benchmark_total_return_proxy=true` 披露 |
| 交易日历 | `calendar_of` / `market_as_of` | `P0/P1`/`latest_workday` | ✅ 已有 | 缺 → 用行情 as_of 推导并披露 |

**明确不依赖**：`review.round/round_fill`（应用层现算，D-15）、`metric_result`（不落库）、任何网络实时请求（读路径不联网：`MARKET_LIVE_CACHE_READ_ONLY`/`SECTOR_CACHE_READ_ONLY` 默认 True）。

### 4.2 08a 前置任务：板块指数日线 + replay 日线（P0）

**为什么必须做**：`sector` 的 `I_start/I_end` 唯一来源是行业指数日线；`buy_timing/sell_timing` 需要**交易日当日**收盘。当前事实：live `get_board_bars()` 返回 `()`；`sync-sectors --start` 明说"仅用于板块日线回填（P1），当前忽略"；`_persist()` 不写 `market.sector_bar`；replay `sector_fixture.series={}`；replay `quotes.json` 只有 9 个月末点。

**08a 方案**（独立可验收，估 1.0~1.5d，含 0.5d spike）：

1. **spike（0.5d）**：`akshare.stock_board_industry_index_ths(symbol=板块名称, start_date, end_date)`（已装、签名已核对；列名/历史长度/停牌/日期覆盖需实测并写 `provenance`）。概念板块本期不回填（M1 不用），P2 再说。
2. **live 端口**：`SectorLiveDataSource.get_board_bars(keys, start, end)` 按目录 `name` 拉取 → `SectorBar(taxonomy, sector_code, trade_date, close_units, return_units)`；单板块失败跳过并记 `failures[]`（不阻断整批，与成分同步同策略）；限频/退避沿用 `market_live`。
3. **落盘**：`sync-sectors --start YYYY-MM-DD` 真正生效，写 `market.sector_bar` + `market.sector_pull_range`（区间并集/尾部刷新）+ `sector_sync_state.bar_as_of`；主键幂等 `(taxonomy, sector_code, trade_date)`。（也可只跑行情：`make sync-sector-bars ARGS='--start …'`，不重抓目录/成分）
4. **replay 夹具**：`sector_fixture.py` 为 demo 持仓的行业生成覆盖**账单全区间 + 前向缓冲**的确定性指数序列（解析曲线，bytes 可复现），`first_sync_on` 保持 2026-06-30；**同时**把 `fixtures/market/quotes.json` 的 replay 个股序列扩到日频（覆盖 2026-04-01~2026-09-30，供买/卖点参考价与 BHAR 等；`generate_market_quotes()` 扩展为日频确定性序列，保持 `as_of=2026-09-30`）。
5. **覆盖判定**：读路径只读缓存；`board_bars` 覆盖 `[P0, P1]` 的每段参考日（行业指数日期必须与个股参考日一致；缺 → 该标的剔除 + `SECTOR_BAR_MISSING`）。
6. **验收**：`make sync-sectors ARGS='--start 2025-07-01'`（live 手测可选）幂等；`SectorRepository.board_bars` 非空且 `close_units>0`；replay 下 `fx_attr_*` 板块因子非 0；`make test-p2` 不回归。

> **08a 未完成时**：`GET /attribution` 返回 **`status="blocked"`** + `missing[]=[SECTOR_DATA_MISSING]`（零个可归因段），**不得**返回"板块=0"或 `amount=null`（与 API-036 断言 rank 1..5 / 固定含 sector 冲突，评审 P0-6）。

### 4.3 分类时点与历史覆盖（D-07）

**算法（冻结，消除 v0.1 自相矛盾）**：

```text
first_sync_on     = min(sector_member.effective_from)（或 replay 夹具的 first_sync_on 配置；无数据 → None）
membership_as_of = sector_sync_state.membership_as_of（或 sector_data.as_of）
if period_end < first_sync_on 或 membership_as_of is None:
    if classification_lookahead == "allow":                 # 需人工批准
        classification_as_of = membership_as_of（最早可用快照）
        classification_lookahead = true；status 至少 degraded
    else:
        status = "blocked" + SECTOR_HISTORY_NOT_COVERED
else:
    classification_as_of = min(period_end, membership_as_of) # 不早于 first_sync_on
    |period_end − classification_as_of| > 90 天 → degraded + SECTOR_CLASSIFICATION_NOT_PIT
```

- 行业归属一律 `memberships_at(as_of=classification_as_of)`（单快照应用到区间内所有段）；`classification_as_of`/`classification_lookahead` 进 `assumptions` 与 `inputs_hash`。
- 概念板块本期不参与（多归属、无权威基准权重）。
- **API 参数**：`classification_lookahead=deny|allow`（默认 `deny`）；`allow` 仅在 `SECTOR_LOOKAHEAD_ENABLED=true`（部署配置，默认 false）时生效，否则 422 `MODEL_NOT_AVAILABLE`（避免生产误开）。
- **本地验收注意**：`SECTOR_MODE=live` 时 `first_sync_on`=首次同步日（2026-10-31），查询 `period=2026-06` 会因 `period_end < first_sync_on` **默认 blocked**。手工验收应使用 replay 板块夹具（`SECTOR_MODE=replay` + 夹具 `first_sync_on=2026-06-30`）或按 A-05 批准 look-ahead；此差异必须写进验收记录，不能用「实盘模式跑 demo 区间」作为 PASS 证据。

### 4.4 状态机、覆盖率与 `missing[]` 码表

**`status` 判定（优先级从高到低）**：

| 优先级 | 条件 | status | 金额字段 |
|---|---|---|---|
| 1 | 无 `V_end`/`V_begin`；边界行 blocked；区间内 blocked 行；`T1 > data_as_of`；`V_end` 早于 `latest_workday(min(valuation_end,T1))`；FIFO 致命错误；`period_end < first_sync_on` 且未开 look-ahead；**存在可归因候选标的**（A 股且区间内有持仓/交易）**但零个可归因段**（板块数据全缺/全部缺映射） | `blocked` | `total_pnl`/`base_capital`/`factors[].amount`/`residual`/`residual_breakdown`/`coverage` 数值键 **全部缺省**；`factors[]` 保留 `code/name/rank`，`ratio/conclusion=null`；给 `missing[]`+`what_to_do` |
| 2 | 任一标的剔除（缺映射/缺指数/缺价/公司行动/费用口径未知/超卖）；`degraded` 资产行；`BRIDGE_MISMATCH`；`RESIDUAL_LARGE`；`SECTOR_CLASSIFICATION_NOT_PIT`；`SECTOR_BAR_FALLBACK` | `degraded` | 全给，`coverage`/`missing[]` 披露 |
| 3 | 其余（含"区间无交易且资产无变化"、**无任何可归因候选标的**，如仅债券/货基账户、仅 `severity=info` 的提示如 look-ahead 启用或板块指数期初回退） | `ok`（有未归因桶则 `degraded`） | 全给（五因子可为 0，`summary` 给"本期无交易/无持仓变化"或"本期无可归因 A 股"；`capital_total==0` 时 `capital_ratio=null`，`summary` 用固定句） |

**候选集 vs「参与本期」（2026-11-04 修订）**：

- **候选集来自全历史 FIFO**：`fills_by_code ∪ open_by_code ∪ 区间内持仓类行`，因此长期持有、早已清仓、已退市的标的都会生成 `SymbolPlan`；`coverage.symbols_total` 统计的正是该候选集（**不是**本期参与数，字段名保持不变）。
- **"参与本期"** = 期末仍有未平仓 lot、`close_date ∈ [T0,T1]` 的 closed fill、或区间内有持仓类流水（`buy`/`rights`/`new_stock`/`sell`/`bonus`）。
- **不参与本期的标的**：**不产生 `missing[]` 条目**（其缺映射/缺价/缺指数/公司行动与本期的归因口径无关），`SymbolPlan.in_universe=False`，整标的金额仍进 `unattributed_price_pnl` 残差桶；仅统计在 `symbols_total` 内。**同样不进入 `coverage.unattributed[]` 明细**（明细与 missing 复用同一 `participating` 判据，只披露本期相关标的，2026-11-04 修订）。
- **参与本期**的 A 股缺行业映射 → `warning`（数据质量缺口）；持有但停牌（期末仍有未平仓 lot）同样参与、同样 `warning`。
- `PRICE_LOOKBACK`（45 天）**仅**用于 `PRICE_REF_FALLBACK`（参考价前收回退判定），**不再**参与"是否可归因"判定。

**覆盖率**（全部后端算好）：

```text
capital_total      = Σ_{全部标的} base（无价标的用净成本兜底，标 coverage.base_basis="mixed"）
capital_attributed = Σ_{可归因标的} base
capital_ratio      = div_round(capital_attributed, capital_total, scale=8)      # total=0 → null
segments_total / segments_attributed
symbols_total       = 全历史候选标的数（含本期未参与；字段名不变，语义见下）
symbols_attributed  = 有可归因段的标的数
unattributed[]     = 最多 5 条 {security_code, security_name, symbol_class, reason_code, amount}（按 |amount| 降序；仅本期参与标的）
```

- `security_name` 来源：`ledger.trade.security_name`（按 canonical 代码取最新非空值，空白压缩为单空格；仓库无独立证券主数据表），缺失为 `null`，前端只展示代码。
- `unattributed[]` 按标的只覆盖 `corporate_action_pnl` / `unattributed_price_pnl` 两桶；`interest` / `ipo_pnl`（打新收益） / `other_cashflow` / `dividend_without_position` 为账户级，不进明细（故明细合计不必等于 Σ桶）——`ipo_pnl` 标的因此不出现在 `unattributed[]`。
- **残差对账（2026-11-04）**：后端派生 `residual_reconciliation{unattributed_symbols_total, account_level_total, residual}`，前端零口径只展示。`unattributed_symbols_total = Σper_symbol`（含被 5 条展示上限截断的标的）；`account_level_total = dividend + interest + ipo_pnl + other + valuation_gap`；恒等式 `unattributed_symbols_total + account_level_total == residual` 由 `residual = Σ各桶 + valuation_gap` 构造保证（标度整数分，无取整误差）。blocked 时整键省略。
- **明细截断（2026-11-04）**：展示上限为单一常量 `UNATTRIBUTED_LIMIT = 5`（`stock_review.attribution.coverage`，API 以 `display_limit` 透出，前端不再硬编码/截断）。`residual_reconciliation` 另给 additive 字段 `listed_total`（前 5 条金额合计）、`others_count`（第 6 条及以后的标的数）、`others_total`（其余金额合计）；恒等式 `listed_total + others_total == unattributed_symbols_total` 由同一 `per_symbol` 切分保证。前端仅当 `others_count > 0` 时追加「其余 N 只」汇总行（非标的、不可点击，金额列显示 `others_total`；行内无伪代码）。
- **结论门禁**（D-09）：`summary` 允许 iff `total_pnl` 可得 && `n_attributed_segments ≥ 10` && `capital_ratio ≥ 0.90` && `valuation_gap_ratio ≤ 5%`；**例外**：`capital_total==0`（空账户/无可归因候选）时允许固定句 summary（"本期无交易/无持仓变化"或"本期无可归因 A 股"）；`blocked` 必封；`insufficient` 只给事实句不给行动建议。
- `symbol_class`：`stock`/`fund`/`bond`/`money_fund`/`new_stock`/`pseudo`（复用 `stock_platform.symbols.classify_symbol` 与 `valuation.is_money_fund_row`）。
- **`new_stock` 判定窗口化（2026-11-04）**：归因按**本期窗口 `[T0, T1]`（闭区间）**判定 `new_stock`（`valuation.new_stock_codes_in_window`）——只有窗口内出现非零 `new_stock` 缴款/中签行的标的才排除出五因子；历史中签、本期无新股行的（如 001237 于 2026-06）按普通 `stock` 参与归因。锚日（严格早于 `T0`）的行属上一期，本期按历史持仓处理。估值兜底（缺价按认购成本）仍用全历史 `new_stock_pending_codes`（assets/nav/portfolio/归因镜像共用，口径不变）。

**`missing[]` 码表**（响应字段，不是 `ErrorCode`）：

| code | 触发 | 影响字段 | what_to_do |
|---|---|---|---|
| `ASSET_SERIES_MISSING` | 无 `V_end`/无区间资产行 | 全缺 | 先构建资产表（`POST /assets/build`） |
| `ASSET_SERIES_STALE` | `T1 > data_as_of` 或 `V_end` 早于 `latest_workday(min(valuation_end,T1))` | 全缺 | 重建资产表/补行情到期末 |
| `ASSET_ROW_BLOCKED` | 边界行或区间内行 `blocked` | 全缺 | 修复缺行情/现金锚点后重建 |
| `ASSET_ROW_DEGRADED` | 区间内行 `degraded` | 披露 | 看资产页原因；结论可能失真 |
| `BRIDGE_MISMATCH` | `\|MTM − 桥接式\| > 1 分` | 披露/降级 | 对账页检查断链 |
| `SECTOR_DATA_MISSING` | 无目录/快照/行业指数（**存在候选标的但零可归因段**） | **blocked** | 运行 `make sync-sectors` |
| `SECTOR_HISTORY_NOT_COVERED` | `period_end < first_sync_on` 且未开 look-ahead | **blocked** | 缩短区间 / 人工批准 look-ahead |
| `SECTOR_CLASSIFICATION_LOOKAHEAD` | 启用了 look-ahead | 披露 | 确认口径例外已批准 |
| `SECTOR_CLASSIFICATION_NOT_PIT` | 快照日距期末 >90 天 | 披露 | 同步最新成分 |
| `SECTOR_MAPPING_MISSING` | 某标的无行业归属 | 该标的剔除 | 同步成分/确认 A 股 |
| `SECTOR_BAR_MISSING` | 行业指数日线缺失 | 该标的剔除 | 运行 `make sync-sectors ARGS='--start …'` |
| `SECTOR_BAR_FALLBACK` | 指数参考日与个股参考日不一致 | 披露 | 补板块日线 |
| `PRICE_REF_MISSING` | P0/C_b/C_s/P1 参考价缺失（>45 天窗口） | 该标的剔除 | 补行情 |
| `PRICE_REF_FALLBACK` | 用前收（同日不可得，但 ≤45 天窗口） | 披露/降级 | 补行情 |
| `CORPORATE_ACTION_ADJUSTMENT_MISSING` | 区间内含送转/配股 | 该标的剔除（残差桶） | P2 复权能力（§10.6） |
| `FEE_BASIS_UNKNOWN` | 费用三列全 0 但净额无法反解 | 该标的剔除 | 人工核对流水 |
| `SEGMENT_DATA_ERROR` | FIFO 超卖/负持仓/未匹配配股 | 该标的剔除 | 对账/修正流水 |
| `RESIDUAL_LARGE` | `\|residual\| > 期末总资产 × 0.2%` | 披露/降级 | 看 `residual_breakdown` |
| `RESIDUAL_WITHIN_TOLERANCE` | `0 < \|residual\| ≤ 期末总资产 × 0.2%` | 仅 info（不降级） | 无需处理 |

### 4.5 性能与预算

- 单请求加载：trades 全量（≤5 万行）+ 资产序列（≈1250 行/5 年）+ 个股行情（只取 `[P0, P1]`，持仓 ≤200 只 × ≤250 日；`holdings_at` 的 `HOLDINGS_MAX=200`）+ 行业指数（行业数 × ≤250 日）。两次 `build_rounds`（T0 前、T1）与 bridge 同级。
- 预算：`p95 ≤ 1.5s`（单用户 5 万行、50 持仓、月区间）；无 50 并发要求。优化顺序：① `get_cached_bars` 只读缓存；② 资产序列区间查询（新增 `AssetRepository.range()`）；③ 行业指数按区间裁剪；④ 不落库。
- 读路径**不联网**：行情只读缓存；板块只读 `market.sector_bar`；缺数据降级不 5xx（与 metrics/dashboard 同策略；`datasets.py` 仅对 NOT_FOUND 逐标的重试，其它 ApiError 需转 200 降级）。

---

## 5. 架构与模块

### 5.1 分层与文件清单

```text
backend/packages/core/src/stock_core/attribution/          # 纯函数（只依赖 contracts/core；零 I/O 零时钟零随机）
├── __init__.py
├── types.py                 # SegmentInput / SegmentFactors / Decomposition / AttributionInput / FactorCode
├── allocate.py              # _allocate 同规则（总数按权重分摊，尾差给最后）+ 两级分摊原语
├── five_factor.py           # FiveFactorModel.decompose()（逐段剖分 + 汇总 + 残差）
├── ranking.py               # rank_factors / ratio_of / share_of / ratio_na
├── sector_return.py         # sector_amount(base, i_start, i_end) 单步取整
└── model.py                 # AttributionModel Protocol（EP-6 对齐：model_id/definition_version/decompose）

backend/packages/review/src/stock_review/attribution/       # 装配（取数→core→DTO；不做口径）
├── __init__.py
├── context.py               # AttributionContext：period/trades/asset/sector/bars/fees/dividends
├── segments.py              # lot+fill → SegmentInput（两级分摊、P0/P1、可归因判定、费用分摊）
├── corporate.py             # 送转/配股事件识别与整标的剔除
├── coverage.py              # coverage/missing/status
├── residual.py              # 残差桶（含资产口径逐标的 P&L、桶互斥）
├── presentation.py          # 结论/文案/presentation（词表、无建议、门禁）
└── exceptions.py            # 归因内部错误 → ApiError 映射

backend/packages/review/src/stock_review/application/attribution.py   # 唯一编排入口 build_attribution(...)（API/月报共用）

backend/services/api/src/stock_api/routers/attribution.py   # GET /attribution（operation_id="attribution_get"）
backend/services/api/src/stock_api/schemas.py               # AttributionOut/FactorOut/...（追加）
backend/tests/attribution/                                   # 单测/golden/oracle/属性/fixture（**不进** tests/golden/ loader）
```

- 依赖方向：`core ← review ← api`；`stock_review/attribution` 可依赖 core/platform 仓储/端口；**core 不得 import review/platform**（import-linter L1–L5）。
- 不新增迁移/表；`openapi_meta.py` 增 `attribution_get`；`contracts/openapi.json` 由 `make api-snapshot` 再生成；`make sdk` + 前端手写类型（§6.4）。

### 5.2 `core/attribution` 纯函数 API（冻结形状，实现期再校）

```python
class FactorCode(StrEnum):
    SELECTION="selection"; SECTOR="sector"; BUY_TIMING="buy_timing"; SELL_TIMING="sell_timing"; FEE="fee"

@dataclass(frozen=True, slots=True)
class SegmentInput:
    security_code: str
    kind: Literal["closed", "open"]
    quantity_units: int
    base_units: int                  # b（两级分摊后）
    end_value_units: int             # q×C_s 或 q×P1（两级分摊后）
    gross_buy_units: int | None      # 区间内建仓才有；None=区间前 lot
    gross_sell_units: int | None     # closed 段才有
    fee_units: int                   # 本段区间内费用（正）
    sector_amount_units: int | None  # None → 本段不可归因（整段路径进 residual）
    dividend_units: int = 0          # 标的区间股息按段 base 分摊（Σ=标的净股息；无段时走 residual 桶）

@dataclass(frozen=True, slots=True)
class Decomposition:
    factors: Mapping[FactorCode, int]   # 5 项；Σ = total_pnl − residual
    residual_units: int
    n_segments: int
    n_attributed_segments: int
    base_capital_units: int
    base_capital_attributed_units: int

def decompose(segments: Sequence[SegmentInput], total_pnl_units: int) -> Decomposition: ...
def decompose_segment(seg: SegmentInput) -> SegmentFactors: ...           # 私有/单测
def sector_amount_units(base_units: int, index_start: int, index_end: int) -> int | None: ...
def allocate(total: int, weights: Sequence[int]) -> tuple[int, ...]: ...   # 尾差给最后
def rank_factors(factors: Mapping[FactorCode, int]) -> tuple[FactorCode, ...]: ...
def ratio_of(numer: int, denom: int) -> int | None: ...                    # denom==0 → None；允许负分母
```

`model.py` 的 `AttributionModel` Protocol（EP-6 对齐；M2 可插拔）：

```python
@runtime_checkable
class AttributionModel(Protocol):
    model_id: str                  # "five_factor"
    definition_version: str        # "1.0"
    def decompose(self, data: AttributionInput) -> Decomposition: ...
```

### 5.3 应用层装配 `build_attribution(...)`

```python
async def build_attribution(
    session: AsyncSession, *, user_id: uuid.UUID, period: str,
    model: str = "five_factor", benchmark: str | None = None,
    classification_lookahead: bool = False,
    clock: Clock, market_data: MarketDataSource,
) -> AttributionResult: ...
```

步骤（严格顺序，降级不抛 5xx）：

1. `resolve_period(period=period, today=clock.business_today())`（复用 `metrics.context`；月/年/自定义 ≤366 天；未来 → 422 `INVALID_PERIOD`）；`model != five_factor` → 422 `MODEL_NOT_AVAILABLE`。
2. `trades = TradeRepository.list_all(user_id)`；`assets = AssetRepository.series(...)`；按 §3.2 算 `V_begin/V_end/CF`，blocked 条件短路（不读行情/板块）。
3. `build_rounds(trades_to_events(trades ≤ T1))` 得 fills/open lots/data_errors；同时 `build_rounds(trades < T0)` 得 `q0`。
4. 公司行动识别（`corporate.py`）：区间内 `bonus/rights` 事件 → 标的剔除。
5. 可归因集合：先判「参与本期」（`segments.py`，§4.4），再按 `classify_symbol` + 行业映射 + 参考价/指数可得；参考价 `load_prices(..., cached_only=True)`；指数 `SectorRepository.board_bars(keys, P0_date, P1_date)`。本期不参与的标的不进 `missing[]`。
6. `segments.py` 两级 `_allocate` 构段（§3.3）；股息按标的汇总、按段 base 分摊（无段 → 残差桶）。
7. `FiveFactorModel().decompose(segments, total_pnl)` → `Decomposition`；`residual.py` 拆桶（资产口径逐标的 P&L）；`coverage.py` 状态/覆盖；`presentation.py` 结论/display；`inputs_hash`。
8. 组装 `AttributionResult`（金额 int，API 层格式化）。

### 5.4 与指标引擎 / `Extensibility.md` EP-6 的关系（C-5）

**不注册为 5 个 `MetricDef`**（事实）：无 `MetricGroup`/`SumToTotal` 实现；`MetricDef` 无跨因子恒等钩子；`DATASET_WHITELIST` 无 sector 数据集；5 因子需共享分段上下文且 `GET /metrics?domain=attribution` 不在本任务验收内。

**EP-6 修订（需 ADR 或人工批准）**：保留 `AttributionModel` 协议与"加文件扩展模型"的精神，实现形态改为 `core/attribution/{model.py,five_factor.py}` + `review/attribution/` 装配；`model=brinson_industry` 实现同一 Protocol 即可在 M2 接入（加文件 + 注册一行，不改端点骨架）。ADR 记录需写明：M1 不注册 MetricDef/不建 MetricGroup、族不变式由端点守门（Σ=total_pnl 的诚实验收见 §3.1/§7.2）、`Extensibility.md` EP-6 措辞修订。

### 5.5 结论生成与文案规则（FR-D3 / FR-R8 / 合规）

- **结构**：`summary`（1~2 句）+ `factors[].conclusion`（每因子一句；影响小可"影响较小"）；`method`/`drilldown` 在月报/详情页由静态文案提供（本期不进 API，避免字段膨胀）。
- **模板化、数据驱动**：只由 `{code, amount, ratio, share, rank, status, coverage, valuation_gap}` 组装；禁止自由生成；`Accept-Language` 只换文案不换数字。
- **无裸术语**：`DE/BHAR/PGR/PLR/TWR/MWR/FIFO/HHI/CR1/IR/OLS` 不得出现（词表断言，与 API-034 同一测试）；"板块/选股/买点/卖点/费用/残差"是产品词。
- **无买卖建议**：禁"建议买入/卖出/加仓/清仓/抄底"等；允许"可关注/建议复盘/可核对"。
- **门禁**（D-09）：只看 `total_pnl` 可得 + `n_attributed_segments≥10` + `capital_ratio≥0.90` + `valuation_gap_ratio≤5%`；`capital_total==0` 用固定句（空账户/无可归因 A 股）；`blocked` 必封；`insufficient` 只给事实句。
- **排序句**：`rank=1` 且 `share≥0.20` 时点名"主要来自「X」"；否则"贡献分散"。示例（total_pnl=−3200，买点=−1900，share=0.59）：
  `本期亏损 3,200.00 元，主要来自「买点」（−1,900.00 元，占构成 59.4%）；其次为「费用」（−410.00 元）。未归属 120.00 元，见残差明细。`

### 5.6 确定性 / 缓存 / 版本

- `inputs_hash`：`sha256` 规范化 JSON；字段 = `model/definition_version/period/benchmark/classification_lookahead` + trades 指纹（count + max updated_at）+ asset 序列指纹（首末日期+行数+`MAX(rebuilt_at)`）+ `sector_sync_state.checksum/membership_as_of/bar_as_of` + 行情 `market_as_of`。相同输入 → 相同响应。
- 不做响应级缓存/ETag（D-17）；月报复用应用函数，不落 `metric_result`。
- `definition_version="1.0"`；口径变更需 `docs/migrations/ATTRIBUTION-*` 记录并 bump；历史/趋势跨版本断开（P3-02 沿用 API-037 `breaks`）。

---

## 6. API-036 契约落地

### 6.1 请求

```http
GET /api/v1/attribution?period=2026-06&model=five_factor&benchmark=hs300_tr&classification_lookahead=deny
Authorization: Bearer <jwt|pat>
```

| 参数 | 必填 | 校验 | 说明 |
|---|---|---|---|
| `period` | ✅ | `YYYY-MM` / `YYYY` / `YYYY-MM-DD..YYYY-MM-DD`（≤366 天，非未来）| 复用 `metrics.context.resolve_period`；非法 → 422 `INVALID_PERIOD` |
| `model` | — | `five_factor`（默认）/`brinson_industry` | `brinson_industry` → 422 `MODEL_NOT_AVAILABLE` |
| `benchmark` | — | `KNOWN_BENCHMARKS={hs300_tr,zz500_tr,csi1000_tr}`（默认 `hs300_tr`） | 仅回显/披露（D-11）；非法 → 422 `UNKNOWN_BENCHMARK` |
| `classification_lookahead` | — | `deny`（默认）/`allow` | 仅当部署 `SECTOR_LOOKAHEAD_ENABLED=true` 生效；否则 422 `MODEL_NOT_AVAILABLE`（D-07） |

- `StrictQueryRoute`：未知参数 → 400 `UNKNOWN_QUERY_PARAM`；admin 读业务数据 → 403 `ADMIN_CANNOT_READ_TRADES`；未认证 → 401（`TOKEN_EXPIRED`/`TOKEN_REVOKED`/`MULTIPLE_CREDENTIALS` 等，**不存在 `UNAUTHENTICATED`**）；读端点不校验 read scope（现状）。
- 无写副作用；重复请求同结果（`inputs_hash` 相同）。

### 6.2 响应 200（冻结字段 + additive）

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` / `ratio_basis` | string | `five_factor` / `"amount/total_pnl"`（顶层，M2 另给 `"effect/excess"`） |
| `period` / `period_start` / `period_end` | string/date | 规范化 token 与区间边界（含） |
| `total_pnl` | money | 区间损益（D-01）；blocked 时**键缺省** |
| `ratio_na_reason` | string\|null | `null` 或 `"PNL_NEAR_ZERO"`（D-08）；ratio 为 null 时给机读原因 |
| `total_pnl_basis` | string | `"asset_daily_mtm"` |
| `base_capital` | money | `Σ全部标的 base`（无价标的用净成本，`coverage.base_basis` 披露）；blocked 缺省 |
| `factors[]` | array | **固定 5 项、固定顺序** `[selection, sector, buy_timing, sell_timing, fee]`；每项 `code`（`sector` 必含）、`name`（选股/板块配置/买点/卖点/费用）、`amount`（money，blocked 缺省）、`ratio`（string 或 null）、`share`（string 或 null）、`rank`（1..5，blocked 也保留）、`conclusion`（string 或 null）、`presentation{bar_pct, ratio_pct, tone}`（`tone ∈ positive/negative/flat`；`bar_pct` 由 share 派的绝对百分比串，**前端直接作 CSS 宽度**） |
| `residual` / `residual_ratio` | money / ratio 或 null | 残差与 plug 占比 |
| `residual_breakdown` | object | `{corporate_action_pnl, unattributed_price_pnl, dividend_without_position, interest, ipo_pnl, other_cashflow, valuation_gap}`（money，blocked 缺省）；标签由 `residual_breakdown_labels` 按 `Accept-Language` 给出（`ipo_pnl` 中文「打新收益」/英文 “New-share (IPO) P&L”） |
| `status` | enum | `ok` / `degraded` / `blocked` |
| `sample` | object | `{n_segments, n_attributed_segments, n_rounds, n_symbols, min_n:10, sample_status: ok 或 insufficient}`（`insufficient`=结论受样本限制，金额照给） |
| `coverage` | object | §4.4；blocked 时数值键缺省 |
| `missing[]` | array | `{field, code, reason, what_to_do}`（§4.4 码表） |
| `assumptions` | object | 见下 |
| `definition_version` | string | `"1.0"` |
| `data_as_of` | **date**（与 metrics 一致） | 三源取最旧（资产/行情/板块），逐项另给 `assumptions.data_sources{}` |
| `inputs_hash` | string | §5.6 |
| `links` | object | `{dashboard, sectors, rounds, reports}`（前端不拼 URL） |

`assumptions` 必备键（口径自描述，进 `inputs_hash`）：`total_pnl_basis`、`beginning_value_source{statement_row|prev_total|zero}`、`cashflow_basis="transfer_rows"`、`fee_basis="commission+stamp_tax+other_fee"`、`dividend_basis="cash_ledger_rows_net_after_tax"`、`sector_basis="ths_industry_index"`、`classification_version`、`classification_as_of`、`classification_lookahead`、`board_bar_as_of`、`benchmark_id`、`benchmark_total_return_proxy`、`ratio_basis`、`attributed_universe`、`corporate_action_policy="exclude_symbol"`、`rounding="scaled_int_half_up_1e-8; residual_plug"`、`adjust="none"`、`data_sources{}`。

### 6.3 错误码 / 缺省（v1.0 已对齐真实枚举）

| 场景 | HTTP | code / 行为 |
|---|---|---|
| `period` 非法/未来/超 366 天 | 422 | `INVALID_PERIOD` |
| `model=brinson_industry` | 422 | `MODEL_NOT_AVAILABLE`（`errors.py:55`） |
| `benchmark` 非法 | 422 | `UNKNOWN_BENCHMARK` |
| `classification_lookahead=allow` 但部署未开 | 422 | `MODEL_NOT_AVAILABLE` |
| 未知 query 参数 | 400 | `UNKNOWN_QUERY_PARAM`（StrictQueryRoute） |
| 未认证 | 401 | `TOKEN_EXPIRED`/`TOKEN_REVOKED`/`MULTIPLE_CREDENTIALS`（**无 `UNAUTHENTICATED`**） |
| admin 越界 | 403 | `ADMIN_CANNOT_READ_TRADES` |
| 资产/板块数据不可用 | **200** | `status="blocked"`，金额键缺省，`missing[]`+`what_to_do`（Catalog 断言⑤） |
| 部分标的剔除 | **200** | `status="degraded"`，五因子照给，`coverage`/`missing[]` 披露 |

> 不新增 `ErrorCode`；`missing[]` 码是响应字段（与板块 API 同约定）。**blocked 缺省语义冻结**（评审 P0-7）：`total_pnl/base_capital/factors[].amount/residual/residual_breakdown/coverage 数值键/ratio` 全部缺省；`factors[].code/name/rank` 保留，`conclusion=null`；serializer 用 `model_serializer(mode="wrap")` 显式 pop（同 `MetricItemOut._omit_empty_value`），**不要**让 Pydantic 自动把整对象变 generic object（C-31 前例）。

### 6.4 契约联动清单（逐文件逐行；实现 PR 同批完成）

| 项 | 位置 | 动作 |
|---|---|---|
| API-Catalog §2.3/§6 | `docs/architecture/v2/API-Catalog.md` | 响应 additive 字段、`assumptions` 键、断言细化；§6 fixture 改名 `fx_attr_single_*`（Catalog 现写 `fx_single_factor`） |
| 契约快照 | `contracts/openapi.json` | `make api-snapshot`（+1 path / +1 operation：49→50 path、55→**56** op） |
| 契约测试 | `backend/tests/contract/test_openapi_contract.py` | `:81` assert 55→**56**；`:82` 集合断言随 `OPERATION_META`；`:124` `json_count` 48→**49**；`:125` text_count=2 不变 |
| 端点门禁 | `scripts/ci/gate_endpoint_coverage.py` | `EXPECTED_ALL_ENDPOINTS=88`/`EXPECTED_PERIOD2=34` 不变（API-036 已在 Catalog/A.1） |
| 契约元数据 | `backend/services/api/src/stock_api/openapi_meta.py` | 增 `attribution_get` 的 `OPERATION_META`（api_id=API-036、scope=read、error_codes 含 INVALID_PERIOD/MODEL_NOT_AVAILABLE/UNKNOWN_BENCHMARK/VALIDATION_ERROR + 认证族）；`MONEY_FIELDS` 只增**新**字段（`base_capital`、`valuation_gap`、`corporate_action_pnl`、`unattributed_price_pnl`、`dividend_without_position`、`other_cashflow`；`amount/residual/total_pnl` 已有）；`RATIO_FIELDS` 增 `residual_ratio/share`；`EXAMPLE_OVERRIDES` 给 attribution 200 示例（含 `presentation`） |
| SDK + 手写类型 | `frontend/packages/api-client` | `make sdk` 再生成 + 手写 `AttributionOut` 接口/`http.ts` group + `contract.ts` 编译期 shape 钉扎（仿 MetricItem，含 blocked 省略键断言） |
| 越权矩阵 | `backend/tests/isolation/test_isolation_matrix.py` | `ADMIN_FORBIDDEN += ("get","/attribution")`；`UNAUTHENTICATED_MATRIX += ("get","/attribution")`；`_REQUIRED_QUERY["/attribution"]="?period=2026-06"`（否则 400 而非 401/403）；无资源 ID → 资源型用例不适用 |
| API 验收 | `backend/tests/api/test_api_036_attribution.py` | 每 API-ID ≥3 条：happy / 越权 / 关键错误码（AGENTS 坑 4） |
| 前端 | `frontend/apps/web/src/router.tsx:86-94`、`layout/navigation.ts:42`、`pages/Attribution.tsx`（新） | 替换 `FeaturePlaceholder`；导航去 `placeholder:true`；RTL 断言渲染后端字段 |
| 门禁/CI | `Makefile`、`.github/workflows/*`、`pyproject.toml [tool.mutmut]`、`mutation_baseline.json` | core 覆盖 `--cov-fail-under=100` 已有；attribution 纳入 mutmut 范围（≥85%）并更新 baseline；golden 走**独立** gate（不进 `tests/golden/` loader） |

---

## 7. 测试与验收

### 7.1 fixture 设计（`backend/tests/attribution/fixtures/` + `support/`）

| fixture | 数据 | 断言（除恒等外） |
|---|---|---|
| `fx_attr_single_selection` | 1 只 A 股、行业指数走平、个股大涨、按收盘成交 | `rank==1` 的 code=`selection`；`sector≈0` |
| `fx_attr_single_sector` | 个股涨跌=行业指数涨跌（无超额）、按收盘成交 | `rank==1` 的 code=`sector` |
| `fx_attr_single_buy` | 价格路径 0（同日收盘买/卖）、买价显著低于建仓日收盘 | `rank==1` 的 code=`buy_timing` |
| `fx_attr_single_sell` | 同上、卖价显著高于卖出日收盘 | `rank==1` 的 code=`sell_timing` |
| `fx_attr_single_fee` | 价格几乎不动、佣金/印花税显著 | `rank==1` 的 code=`fee`（负） |
| `fx_attr_mixed` | §3.8 手算向量 + 区间前 lot + 部分卖出 + T+0 + 股息 + 不可归因债券/货基 + 停牌缺价 | `residual==0`；五因子逐项等于手算；`residual_breakdown` 各桶独立复算；`coverage`/`missing[]` |
| `fx_attr_corporate_action` | 区间内 10 送 3 + 10 配 3 | 标的剔除；`corporate_action_pnl` 桶=手算；`missing[]=CORPORATE_ACTION_ADJUSTMENT_MISSING`；不产生幻影因子 |
| `fx_attr_blocked` | 资产区间行 blocked / 缺失 / `V_begin` 行 blocked | 200 `status="blocked"`、金额键缺省、`missing[]` |
| `fx_attr_no_sector` | 行业指数全缺 / 未映射 | 零可归因段 → `blocked`；部分缺 → `degraded` + 标的剔除 |
| `fx_attr_negative_total` | `total_pnl<0`、因子一正一负 | `ratio` 带符号、`share` 0..1、Σratio+residual_ratio=1e8、`summary` 用"亏损/拖累" |
| `fx_attr_boundary_weekend` | 区间末为 2026-05-31（周日）/2026-10-31（周六） | 不因自然日误判 stale；`V_end=latest_workday` |
| `fx_attr_p0_blocked` | T0 前最后资产行 blocked | blocked（不把 0 当 V_begin） |

- 夹具落 `backend/tests/attribution/fixtures/` + `backend/tests/support/fx_attribution_*.py`（仿 `support/fx_metrics_*.py`）；板块/行情通过 `app.state` 注入的 replay 源或测试内构造（`deps.py:get_sector_data` 可替换），**不依赖**全局 `fixtures/market/sectors.json` 的受控数据。
- golden 落 `backend/tests/attribution/golden/<fixture>/<case>.json`（**不进** `backend/tests/golden/`：那里的 loader 要求 `metric_id`/MANIFEST 全量覆盖，会破坏 G8 结构门禁——评审 P0-4）。

### 7.2 golden / oracle / 属性测试

- **golden**：每 fixture 一份逐分期望（金额、ratio、share、rank、residual 桶）；来源 = §3.8 手算表 + 独立整数原型（`var/review-attribution-numeric/`），**双人独立得同值才批准**；golden 独立 PR + `golden-approved` 标签。
- **oracle**：`backend/tests/attribution/oracle/naive_attribution.py` 用 `Decimal` 独立实现同一公式（直接量化到分，不走两级 `_allocate` 优化），**逐因子**比对；差异必须落在已定义桶且 ≤1 分/段，**禁止**"把差异一律塞进残差"掩盖口径错误。
- **属性测试**（Hypothesis）：随机 lot/买卖/股息/费用/公司行动 ≥1 万组，断言：① 段级因子和=段参考损益（自检）② `Σfactors+residual=total_pnl`（整数精确）③ `Σratio+residual_ratio=1e8`（精确）④ `Σshare=1e8` ⑤ rank 连续 ⑥ 两级 `_allocate` 下 `Σ段 base==标的级 floor` ⑦ 费用 `Σ段==trade` ⑧ 与 `build_rounds` fill 集合一致 ⑨ 任意输入不抛非 `ApiError`。
- **与资产口径对账**（非 plug 门禁）：受控 fixture 断言 `residual==0`；随机干净数据（A 股整分 tick、无公司行动）断言 `residual==0`；含非整分 tick 时残差 ≤ 段数−1 分（两级分摊后应为 0）。
- **变异测试**：`core/attribution/**` 突变分 ≥85%（`Testing.md` §10 既有要求）；覆盖费用符号、区间前 lot 基准、股息现金制、公司行动剔除、tie-break、ratio 符号、两级分摊尾差、单步板块取整。

### 7.3 API-036 验收矩阵（Catalog §6 + PRD FR-R7/R8 + 评审补）

| # | 断言 | 用例 | 期望 |
|---|---|---|---|
| ① | `Σfactors.amount + residual = total_pnl`（≤0.01） | 全 fixture | 逐分 0 |
| ② | `rank` 从 1 连续无重复（含 blocked 也保留 code/rank） | 全部 | 1..5 唯一 |
| ③ | `Σfactors.ratio + residual_ratio = 1e8`（≤1bp） | 全部（ratio 非 null） | 精确 1e8（plug） |
| ④ | 单因子主导：**`rank==1` 的因子** code 正确（不是 `factors[0]`，因固定顺序） | `fx_attr_single_*` ×5 | 各自期望 code |
| ⑤ | blocked：`status="blocked"`、`total_pnl` 缺省、`factors[].amount` 缺省 | `fx_attr_blocked` | 200 + 键缺省 |
| ⑥ | `factors[]` 恰 5 项、固定顺序、含 `code="sector"`、`name` 中文 | 全部 | 通过 |
| ⑦ | `residual_breakdown` 各桶之和 = residual，且 `valuation_gap` 独立可复算（不靠 plug 反推） | `fx_attr_mixed`/`fx_attr_corporate_action` | 通过 |
| ⑧ | 结论无裸术语、无买卖建议 | 词表 + 正则 | 0 命中 |
| ⑨ | 跨用户隔离（A/B 各自 200、数据互不泄漏） | 双用户 | 通过 |
| ⑩ | admin 越界 → 403 `ADMIN_CANNOT_READ_TRADES` | admin | 通过 |
| ⑪ | 参数校验：period/model/benchmark/lookahead/未知参数 | 5 组 | 422/422/422/422/400 对应 code |
| ⑫ | 确定性：同输入两跑响应逐字节相同（含 `inputs_hash`） | 全部 | 通过 |
| ⑬ | 覆盖率/降级：部分缺板块 → degraded + 标的剔除 + `coverage.capital_ratio<1` | `fx_attr_no_sector` | 通过 |
| ⑭ | 负 total_pnl：`ratio` 符号、`share` 构成、summary 措辞 | `fx_attr_negative_total` | 通过 |
| ⑮ | 样本门禁：`n_attributed_segments<10` → `sample_status="insufficient"` 且 `summary=null`、金额照给 | 小样本 fixture | 通过 |
| ⑯ | `data_as_of = min(资产/行情/板块)`、`assumptions` 18 键齐全 | 全部 | 通过 |
| ⑰ | 边界：周末月末不误判 stale；`V_begin` blocked → blocked | `fx_attr_boundary_weekend`/`fx_attr_p0_blocked` | 通过 |
| ⑱ | 公司行动剔除：不产生幻影因子、残差桶正确、`missing[]` 存在 | `fx_attr_corporate_action` | 通过 |
| ⑲ | bridge 对账：`\|MTM − 桥接式\| ≤1 分` 或 degraded+`BRIDGE_MISMATCH` | `fx_attr_mixed` | 通过 |

### 7.4 门禁命令（提交前；AGENTS 门禁 + 本任务追加）

```bash
make lint                 # ruff + tsc（前端零口径 ESLint 已在 make lint 中执行）
make typecheck            # mypy strict + import-linter（core 不得 import review/platform）
make test-p2              # 二期主套餐（含 gate_endpoint/gate_tenant/api-check/api-snapshot --check）
make test-metrics         # 指标口径不回归（本任务不改指标）
make test-isolation       # 越权矩阵含 API-036（ADMIN_FORBIDDEN/UNAUTHENTICATED）
make api-snapshot && make api-check   # 契约快照/完整性（50 path / 56 op / 49 JSON 示例）
make sdk                  # 前端 TS 类型再生成
# 覆盖率：core 已有 --cov=stock_core --cov-fail-under=100；attribution 变异：纳入 mutmut 范围 ≥85%，更新 mutation_baseline.json
# golden：backend/tests/attribution/golden 独立 gate（不并入 tests/golden 的 G8 loader）
```

### 7.5 可执行验收（pytest 为主，curl 只做冒烟）

```bash
# 以 pytest fixture 为准（fx_attr_* 不依赖手工造库；replay 注入行情/板块）
pytest backend/tests/unit/core/test_attribution.py \
       backend/tests/unit/review/test_attribution_assembly.py \
       backend/tests/api/test_api_036_attribution.py \
       backend/tests/isolation -q

# 手工冒烟（仅验证端点可达/恒等/错误码，不把 status=="ok" 写死——demo 可能因债券/货基为 degraded）
set -euo pipefail
BASE=http://127.0.0.1:8000/api/v1
TOKEN=$(curl -fsS -X POST "$BASE/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"1234@dev.com","password":"1234"}' | jq -r .access_token)
curl -fsS "$BASE/attribution?period=2026-06" -H "Authorization: Bearer $TOKEN" | jq -e '
  (.factors|length==5) and ([.factors[].code]|index("sector")!=null)
  and ([.factors[].rank]|sort)==[1,2,3,4,5]
  and ((.factors|map(.amount|tonumber)|add) + (.residual|tonumber) - (.total_pnl|tonumber) | abs) <= 0.01'
# model 未开放 → 422
test "$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/attribution?period=2026-06&model=brinson_industry" \
  -H "Authorization: Bearer $TOKEN")" = "422"
```

> 验收时 `make db-up` + `STOCK_DB_PORT=5433`（AGENTS 清库守卫）；`make verify-p2` 不存在（Period2-14 暂停），在评审记录中标注 SKIP。
> **区间选择**：`SECTOR_MODE=live` 下 `period=2026-06` 默认 blocked（`period_end < first_sync_on`）；手工冒烟请用 `SECTOR_MODE=replay`（夹具 `first_sync_on=2026-06-30`）或按 A-05 批准 `classification_lookahead=allow`，否则只能验 blocked 分支。

---

## 8. 前端与月报联动（零口径）

### 8.1 `/attribution` 页面（替换占位，D-18）

```text
PageHeader：期间选择 + 状态徽标（ok/degraded/blocked）+ 数据截至 + 基准回显
① 摘要卡：summary + total_pnl + 五因子（固定顺序）条形/瀑布：
   name、amount、ratio（后端百分比串）、share、rank、conclusion、presentation.bar_pct（直接作 CSS 宽度）
② 残差卡：residual + residual_breakdown 六桶 + coverage.capital_ratio + 未归因清单（≤5）
③ 数据质量卡：missing[]（中文文案 + what_to_do 链接：资产页/板块同步/流水）
④ 空/降级态：blocked → 不渲染任何金额，只渲染 ③；insufficient → 渲染金额、结论区"样本不足"
```

- **零口径硬约束**：页面**不得**做 `amount/total_pnl`、`Math.abs`、百分比拼接、残差校验或重排；条形宽度用 `presentation.bar_pct`（绝对百分比串，去符号直接作 CSS 宽度，与板块偏好条同模式）；排序用后端 `rank`；文案用后端 `summary`/`conclusion`。
- **门禁已存在（事实纠正）**：`frontend/apps/web/eslint.config.js` 已禁 `parseFloat/parseInt/Number(/toFixed`（唯一豁免 `charts/geometry.ts`+测试），`make lint` 会执行；`src/charts/zero-caliber.test.ts` 全 src 扫描兜底——**不要假设零口径门禁未实现**（AGENTS/Dashboard 文档该条已陈旧）。
- **入口**：Dashboard 历史区"归因"按钮沿用 `links.attribution`（API-084 已返回 `/attribution?period=…`）；`/sectors` 页 `links.attribution`（API-087）保持；`router.tsx` 占位替换 + `navigation.ts` 去 `placeholder:true`。
- **验收**：RTL 断言"渲染后端字段"（mock 固定 DTO：页面不出现前端算出的百分比；`coming-soon` 消失）；blocked/insufficient 快照；无 `<a download>`、无假 job。

### 8.2 月报 09b 接入（`2026-10-28-monthly-report.md` §8.2）

- 报告生成任务内**直接调用** `build_attribution(...)`（同源应用函数，不走 HTTP），DTO 写入 `report.snapshot.attribution`；渲染层只读快照。
- 区块内容：五因子金额/占比/排序 + 残差 + 脚注（口径/数据截至/未归因说明）；`blocked` → `section.status="unavailable"` + reason（不展示数值）；`insufficient` → 展示金额、隐藏结论。
- 数字一致性：脚本比对 `snapshot.attribution` vs 同 period `GET /attribution`，逐字段 ≤0.01 元 / 1bp。
- **依赖时序**：09a 可先交付占位不被 08 阻塞，但 DEV-PLAN Period2-09 依赖仍写 Period2-08 → **需 A-01 人工确认后改写 DEV-PLAN**（评审 P1-8）。

---

## 9. 实施计划与估时

> **DEV-PLAN Period2-08 原估 2 人日不可达**（仅 core 五因子 + API + 5 个单因子 fixture golden 已 ≈3.5d；不含板块指数数据前置、replay 日线、前端页、月报联动、门禁）。以下为可执行拆分；**需人工确认排期/裁剪**（HITL A-01）。

| 任务 | 内容 | 依赖 | 估时 | 验收 |
|---|---|---|---|---|
| **08a 数据前置** | spike（0.5d）+ live THS 行业指数 `get_board_bars` + `sync-sectors --start`（或独立 `make sync-sector-bars`）落 `sector_bar`/`sector_pull_range`/`bar_as_of` + replay 板块指数/个股日线夹具扩展 | Period2-17 | 1.0~1.5d | `board_bars` 非空且幂等；replay 覆盖 demo 全区间；`make test-p2` 不回归 |
| **08b core 五因子** | `stock_core/attribution/*`（types/allocate/five_factor/ranking/sector_return/model）+ 单测/属性/变异 | 08a 接口 | 1.5~2.0d | §3.8 golden 逐分；随机 1 万组；段级/两级分摊断言；mutation ≥85% |
| **08c 应用层装配** | `stock_review/attribution/*` + `application/attribution.py`（资产边界/段构建/费用股息分摊/公司行动剔除/coverage/residual/status/inputs_hash） | 08b | 2.0~2.5d | `fx_attr_mixed` 逐分；blocked/degraded/边界矩阵 |
| **08d API-036 + 契约** | router/schema/openapi_meta/api-snapshot/contract 计数/isolation/sdk+手写类型 | 08c | 1.0~1.5d | 契约/隔离/api-check 全绿；断言 ①~⑦⑨~⑫⑯ |
| **08e 结论与文案** | `presentation.py`（模板、词表断言、无建议、样本门禁、bar_pct） | 08c | 0.5~1.0d | ⑧/⑭/⑮；中英文 |
| **08f 前端归因页 + 月报接入点** | `/attribution` 页 + router/navigation + Dashboard 链接确认 + 09b 同源接入点 | 08d/08e | 1.0~1.5d | 页面渲染后端字段；09b 数字一致脚本 |
| **08g 验收与文档** | fixture 12 组 + 全断言 + golden 审批 + 独立 gate + mutmut baseline + 文档联动 | 全部 | 1.0~1.5d | `make test-p2/test-metrics/test-isolation/api-check` 全绿；验收记录 |
| **合计** | | | **8.0~11.5d** | |

**裁剪选项（需 A-01 签字）**：① 08f 前端拆到 Period2-12/13（API 先行，PRD FR-R7 UI 验收暂 SKIP 并记录）；② 08a 概念板块指数不做（M1 不需要，已不含）；③ 残差桶合并（不建议，降低可解释性）。**不建议**砍 08a/08b/08c（会牺牲可验收性）。

---

## 10. 归因的扩展性（后续演进）

归因不是一次性报表，而是**系统的因果分析内核**。四条演进轴保证"加文件/加字段，不改恒等式与既有契约"。

### 10.1 四条演进轴

| 轴 | 演进 | 触发/版本 | 需要的新东西 |
|---|---|---|---|
| **时间** | 单期 → 逐期序列 → 多期链接 → 同比/环比 | `GET /attribution/series`（P3 新端点）；Carino（P3-08） | 逐期结果（可重算，不必须落库）；`definition_version` 断点 |
| **维度** | 五因子 → 因子内子项（费用三列/板块行业明细/买点追涨 vs 执行）→ Brinson 三效应 | additive `factors[].decomposition`；`model=brinson_industry`（P3-08） | `sector_bar` 完整历史、`benchmark_member` 期初权重 |
| **模型** | `five_factor` → `brinson_industry` → 资产类别/多账户模型 | `AttributionModel` 注册表 + `model` 参数 | 新 model 文件 + 注册一行（EP-6） |
| **消费方** | 月报 → 趋势/看板 → 提醒/对话/导出 | 各端点/区块复用 `build_attribution`/DTO | 只读 DTO 复用；LLM 只读已校验字段（禁自由生成数字） |

### 10.2 多期、同比与"今年比去年"（前置条件必须满足才可相加）

- **逐期序列**：P3 新增 `GET /attribution/series?from=…&to=…&granularity=month|quarter`（或扩展 `/trends` 的 `series=attribution`），返回 `points[]{period,total_pnl,factors{},residual,status}` + `breaks[]`；**同 `definition_version` 才可连线**。
- **跨期相加的前置条件（评审补，缺一不可）**：① 相邻区间不重叠且首尾相接；② 每期 `status != blocked` 且 `residual` 已被解释（不把未归因项当收益）；③ 区间边界资产行连续（无断链/无重建缺口）；④ `classification_as_of` 快照一致（否则板块/选股不可比）；⑤ 无跨期未平仓的建仓成本漂移（新 lot 的 `base` 用建仓日收盘，跨期相加仍闭合，但**板块因子跨期不可直接加总**——每期行业收益是区间收益，需重算/链接）。满足后，**金额域因子可逐期相加**；收益率/超额跨期累计需 Carino（M2）。
- **同比/环比**：后端直接给 `delta`（与上月/去年同月），**前端不减法**；"今年比去年是否有提升"= 同口径年度 `total_pnl` + 各因子差值 + 因子结构变化（选股↑/买点↓），报告脚注注明前置条件与断点。
- 断点：`definition_version` 变化 → 断开、旧值保留（落库方案按版本过滤；不落库方案按 `inputs_hash` 版本）——`definition_version` 是唯一断点信号。

### 10.3 Brinson-Fachler + 行业偏离（P3-08，M2）

- 口径沿用 `2026-10-31-sector-data.md` §8.2（本文不重复）：`A = Σ(w_p−w_b)(R_b,s−R_b)`、`S = Σw_b(R_p,s−R_b,s)`、`I = Σ(w_p−w_b)(R_p,s−R_b,s)`；`Σ(A+S+I)=R_p−R_b`（1bp 容差）；`w_p` 用**期初（BoP）权重**（PRD D-23，需人工确认）；现金/未分类 sleeve 展开见该节。
- **与 M1 的精确关系（评审 + 数值验证修正）**：在无区间内交易/现金流的单期口径下，`A = M1 板块贡献 − base×R_b`、`S+I ≈ M1 选股`；但两模型**分母不同**（M1=total_pnl；M2=超额 `R_excess`）、`definition_version` 不同、`ratio_basis` 不同，**不得混算/并列相加**；M2 响应须带 `ratio_basis`/`model`，页面按 model 分流渲染、月报脚注说明差异。
- 前置：`sector_bar`（08a）、`benchmark_member` 期初快照（已有表/同步）、`asset_daily` 期初总资产（已有）；行业层级（THS 扁平 L1；如需多级要回到申万或加 L2 成分源——开放问题）。

### 10.4 "板块漂移轴"（drift axis）

用户要的"漂移"是**时间轴上的行业权重/暴露变化及其代价**，不是当前快照。落地分三步（**本期不做**）：

1. **P2 描述层（低成本）**：月度 `/attribution` 的板块因子 + 当期 `/sectors/overview` 的 `w_p,s/w_b,s/active_weight` 组成"行业贡献 × 权重偏离"散点/序列；若需 PIT 权重序列，新增 `review.sector_exposure_daily` 物化表（P3）。
2. **P3 归因层**：把漂移收益拆到 `配置项`（M2 的 A）+ `漂移项`（期初→期末权重变化交互）——需要逐日/逐期权重。
3. **P3 趋势层**：`drift_index = Σ|w_p,s(t)−w_p,s(t−1)|/2`（行业层面换手）与 `total_pnl`/超额的相关性（n 门槛），回答"漂移是改善了还是伤害了收益"。
- **数据缺口**：行业分类历史（D-07 限制）+ 逐日持仓板块权重（可算但需物化；`holdings_at` 逐日成本高）。
- **零口径**：漂移序列由**后端**出（`/attribution/series` 或新端点），前端只画，不拼装计算（评审 P1-13）。

### 10.5 "操作是否稳定"轴（执行质量）

- 逐期 `buy_timing/sell_timing/fee` 因子金额（本期已产出）→ P3 序列化后可算：均值、标准差、正负月份数、最差/最好月；与 `timing.chase_quantile`、`timing.sold_too_early`、`behavior.turnover` 交叉验证（如"高换手月份买点因子更差"）。
- 结论模板：不评价"你很差"，只给"近 6 个月买点贡献 4 个月为负，合计 −X 元；与追涨分位同期偏高一致，可复盘打法"（无买卖建议）。
- 依赖：08 的逐期可重算能力 + P3-02 趋势端点 + 分析脚本；不需要新表。

### 10.6 扩展点：`AttributionModel` 注册表（EP-6 的落地形态）

```python
# stock_review/attribution/registry.py —— 显式登记（不做 pkgutil 自动发现，避免隐式面）
MODELS: dict[str, Callable[[], AttributionModel]] = {
    "five_factor": FiveFactorModel,              # 08
    # "brinson_industry": BrinsonIndustryModel,  # P3-08（加文件 + 注册一行）
}
```

- 新模型：新增 `core/attribution/<model>.py` + `review/attribution/<model>_input.py`（装配）+ 注册一行 + golden/验收；**不改端点骨架、不改五因子 DTO**（响应 additive：新模型可加 `sectors[]`/`linkage`）。
- 模型未开放 → `MODEL_NOT_AVAILABLE`；模型开放但区间数据不足 → 200 `status=blocked/degraded`。
- 因子集**不扩展第六因子**（恒等式与 DTO 稳定）；新维度放 `factors[].decomposition`/独立 model；实验性模型用 `assumptions.status="experimental"` 区分。
- **P2 公司行动/复权扩展**：core 引擎暴露 lot 级数量事件（或 `build_rounds` 增加 `adjusted_lots` 输出）后，`segments.py` 按"调整因子"分段（送转增量 base=0、配股缴款独立 gross_buy 段），替换 D-12 的整标的剔除；需独立 golden（10 送 3 / 10 配 3）与 `definition_version` bump。

### 10.7 资产类别、账户维度与导出

- **资产类别**（P2）：债券/ETF/LOF/货基/未映射股票目前进残差；扩展为 `model=by_asset_class`（独立模型，输出各类别贡献 + 内部选股/费用），或把 `residual_breakdown.unattributed_price_pnl` 细分到类别（additive 字段，不改五因子）。**不**把这部分硬塞进"选股"。
- **多账户**（PRD FR-R2.4 `merged_view`）：additive `by_account[]{account_id, factors{}}`；合并口径与单账户口径都披露。
- **导出/月报/分享**：只读快照（`report.snapshot.attribution`）+ 分享链接（P3-04）；归因不进 LLM 自由生成（`EgressGuard`/`ToolGateway` 只回后端字段）。

### 10.8 明确不做

风格因子（FF3/FF5/Barra）与同类对比（PRD 已裁）；"如果当时不买"的反事实回测（P2 评估，需严格披露假设，风险高）；LLM 自由撰写归因结论；把归因做成异步大任务（单期同步足够）。

---

## 11. 风险与开放问题（HITL）

| # | 风险/问题 | 影响 | 缓解 | 拍板状态（2026-11-02） |
|---|---|---|---|---|
| R-01 | 估时 8.0~11.5d ≫ DEV-PLAN 2d | 排期冲突/范围蔓延 | §9 裁剪选项；08a 独立任务 | ✅ A-01 已批准（按推荐） |
| R-02 | `total_pnl` 口径（MTM vs 已实现）与 Dashboard 不一致；区间损益无现有生产者 | 月报/看板数字矛盾 | D-01 + §3.2.1 桥接对账 + assumptions + 月报脚注 | ✅ A-02 已批准（按推荐） |
| R-03 | 板块因子语义与 sector §8.3（Brinson 配置）冲突 | 两处文档/两模型口径混用 | C-1 修订；M1/M2 分层 + `ratio_basis` 区分 | ✅ A-03 已批准（按推荐） |
| R-04 | PRD-v1 买点/卖点回归贡献无法闭合 | 验收口径争议 | C-4 勘误；执行口径 + 行为指标互补 | ✅ A-04 已批准（按推荐） |
| R-05 | 历史行业成分只有首次同步后（replay 2026-06-30；live 部署日） | 老区间板块因子不可用或 look-ahead | D-07 默认 blocked + 显式开关；M2 严格 PIT；需改 AGENTS.md | ✅ A-05 已批准（按推荐） |
| R-06 | 08a THS 行业指数可得性/历史长度未 spike；replay 个股日线不足 | 板块/买点卖点不可交付 | 0.5d spike；日线夹具扩展；失败则 blocked + 明确 SKIP | ✅ A-06 已批准（按推荐） |
| R-07 | 公司行动/债券/ETF/货基/未映射占比高 → 残差大、结论弱 | 用户觉得"没解释清楚" | D-12 剔除 + 残差桶 + coverage + 结论门禁；P2 复权/资产类别 | ✅ A-07/A-11 已批准（按推荐） |
| R-08 | 前端归因页是否本期做（PRD UI 验收 vs 排期） | 功能不可演示 | 08f 可拆到 P2-12/13（API 先行）；UI SKIP 记录并需签字 | ✅ A-08 已批准（按推荐） |
| R-09 | 性能：全量 trades + 双次 FIFO + 行情 | 端点超时 | 预算 1.5s；只取 `[P0,P1]` 行情；缓存优先；不落库 | ✅ A-09 已批准（按推荐） |
| R-10 | 历史区间资产表 blocked/stale 比例 | 归因对老数据不可用 | blocked + what_to_do；资产表构建覆盖优先 | ✅ A-10 已批准（按推荐） |
| R-11 | 结论文案合规（禁买卖建议/裸术语） | 合规风险 | 模板 + 词表/正则断言；人工抽检 | — |
| R-12 | 残差容差（期末总资产 0.2%）与 ratio 阈值（100 分/0.5%）需校准 | 结论时有时无/口径扯皮 | 上线前用真实用户/夹具分布校准；配置化常量 | ✅ A-07 已批准（2026-11-04 改为 0.2%×V_end） |
| R-13 | `definition_version` 未来变更导致历史不可比 | 趋势断点 | 版本号 + 趋势 breaks（P3-02）；旧值保留 | — |

**HITL 升级清单（2026-11-02 已全部批准，按推荐方向执行；实现期如遇新歧义仍按 HITL 协议升级）**：A-01（排期/范围）、A-02（D-01 总损益口径）、A-03（D-02 板块语义 + sector 文档修订）、A-04（D-03 买点卖点口径 + PRD 勘误）、A-05（D-07 PIT + AGENTS.md 例外）、A-06（08a 数据源 spike）、A-07（残差/覆盖/ratio 阈值）、A-08（前端范围）、A-09（性能实测）、A-10（历史资产表可用性）、A-11（公司行动剔除范围）。

---

## 12. 文档联动与交付物清单

### 12.1 本任务交付物

1. 本文（`docs/architecture/2026-11-02-five-factor-attribution.md`）——**开发设计 v1.0（已人工批准）**。
2. `docs/reviews/2026-11-02-DevDesign-Review-five-factor-attribution.md`——**独立交叉评审记录**（4 名评审/验证员、问题清单/处置/HITL/数值验证）。
3. 实现 PR 时：代码/测试/golden/契约快照/前端页/月报接入（§9 任务表）。

### 12.2 实现 PR 必须同步的文档/工件

| 文档/工件 | 位置 | 变更 |
|---|---|---|
| API-Catalog | `docs/architecture/v2/API-Catalog.md` §2.3/§6 | additive 字段、`assumptions`、断言、fixture 名 |
| 板块设计 | `docs/architecture/2026-10-31-sector-data.md` §7.4/§8.3/§8.5 | M1/M2 分层、look-ahead 例外、API-036 additive 对齐 |
| **AGENTS.md** | 板块铁律 5 | look-ahead 例外（人工批准后）；补充归因口径链接 |
| PRD 勘误 | `docs/prd/PRD-v1.md` FR-R7.1；`docs/prd/PRD-v2.md` FR-R7 | 买点/卖点口径注、`total_pnl` 口径注 |
| 可扩展性 | `docs/architecture/v2/Extensibility.md` EP-6 | 实现形态修订（C-5）+ ADR |
| 月报设计 | `docs/architecture/2026-10-28-monthly-report.md` §8.2 | 09b 接入函数/DTO/依赖改写 |
| Dashboard 设计 | `docs/architecture/2026-10-28-dashboard.md` §7 | 归因占位 → 已实现；缺失清单更新；零口径"未完成"勘误 |
| 开发计划 | `docs/DEV-PLAN-v2.md` Period2-08 | 估时/依赖/验收/08a 拆分（C-8） |
| Testing 文档 | `docs/architecture/v2/Testing.md` | 路径勘误（`attribution.py` 位置）、golden 独立目录、用例名 |
| 契约/门禁 | `contracts/openapi.json`、`openapi_meta.py`、contract tests、isolation、`frontend/packages/api-client`、`Makefile`/`pyproject.toml [tool.mutmut]`/`mutation_baseline.json`、CI workflow | §6.4/§7.4 |
| 前端 | `router.tsx`、`navigation.ts`、`pages/Attribution.tsx`、RTL | §8.1 |

### 12.3 本轮声明（诚实边界）

- 本文是**设计**，未改任何代码/数据；`fx_attr_*` fixture、golden 数值（仅 §3.8 手算向量）、08a 数据均未落地。
- 未实现：`GET /attribution`、`core/attribution/`、08a 板块指数同步与 replay 日线、前端归因页、月报 09b 接入。
- **A-01~A-11 已于 2026-11-02 人工批准（按推荐方向；§11/评审记录 §10）**；公司行动 M1 整标的剔除（D-12）是**已批准的范围裁决**而非技术终态（P2 复权调整）；历史 PIT 默认 blocked（老账单归因不可用），经批准可用 look-ahead 例外（degraded + 披露）。
- 验收中不可执行项：`make verify-p2` 不存在（Period2-14 暂停）；`make mutation` 是否在 CI 启用需确认；50 并发性能测试不属于本任务。以上在评审记录中标注 SKIP。
- 数值验证产物在 gitignored `var/review-attribution-numeric/`，不随仓库交付；需要复现可按 `report.md` 命令重跑。

---

## 附录 A：符号与公式速查

| 符号 | 含义 | 标度 |
|---|---|---|
| `T0/T1` | 统计区间起止（含） | 日期 |
| `P0/P1` | 区间起点前最后收盘 / 区间末端最后收盘（`close_on_or_before`，45 天窗口） | 价格 1e-6 |
| `C_b/C_s` | 建仓日收盘 / 卖出日收盘 | 价格 1e-6 |
| `q` | 段数量（区间前 lot 取 T0 时点剩余） | 1e-4 |
| `b` | 段基准本金（区间前 `q×P0`；新 lot `q×C_b`；两级分摊） | 分 |
| `path` | `end_value − b` | 分 |
| `I_start/I_end` | 行业指数起点/终点收盘（**与个股参考日同日**） | 指数点（整数同源序列） |
| `F` | 段费用（佣金+印花+其他，正数；分母=全部段数量，`_allocate`） | 分 |
| `D` | 标的区间股息净额（现金制，到账日 ∈ [T0,T1]） | 分 |
| `total_pnl` | `V_end − V_begin − CF_period`（MTM）；另用 `bridge_profit + Δ浮盈亏` 对账 | 分 |

恒等式：`Σ_f factors_f + residual = total_pnl`；`Σ_f ratio_f + residual_ratio = 1e8`；`Σ_f share_f = 1e8`；`selection = path − sector + D`；`sector = div_round(b×(I_end−I_start), I_start)`；`residual = total_pnl − Σfactors`（plug，真门禁见 §3.1/§7.2）。

## 附录 B：参考

- 归因方法与本产品口径取舍：`docs/architecture/2026-10-30-metrics-product-optimization.md` §7.4
- 板块与 Brinson 数据契约：`docs/architecture/2026-10-31-sector-data.md` §7.4/§8
- 月报归因区块：`docs/architecture/2026-10-28-monthly-report.md` §8.2
- 指标引擎/可信度：`docs/architecture/2026-10-26-metrics-three-domain.md` §4；`docs/prd/PRD-v2.md` FR-R7/R8
- 资产表/估值：`docs/architecture/2026-10-25-asset-daily-table.md`
- 交叉评审记录：`docs/reviews/2026-11-02-DevDesign-Review-five-factor-attribution.md`

> 外部方法参考（非本仓权威，仅方法说明）：Brinson-Fachler/BHB 三效应与 Carino 多期链接的通用定义（见 `2026-10-30-metrics-product-optimization.md` §7.4 引用）；本设计不采纳任何外部模板作为验收口径。


