# 股票板块数据（三级行业 + 概念）接入开发设计

> 版本：v0.2（2026-10-31，已按独立评审修订）｜ 状态：待评审/待人工拍板
> 需求来源：用户本轮诉求——「引入股票板块数据，主要是三级行业板块和概念板块，二者需要区别开；用于看板统计（板块偏好与板块集中度），后续归因模块也要用」。
> 关联文档：[AGENTS.md](../../AGENTS.md)、[API-Catalog](v2/API-Catalog.md)、[行情源设计](2026-10-22-live-market-data-source.md)、[Dashboard 设计](2026-10-28-dashboard.md)、[三域指标设计](2026-10-26-metrics-three-domain.md)、[资产表设计](2026-10-25-asset-daily-table.md)、[ADR-0025](v2/ADR.md)、[DEV-PLAN-v2](../DEV-PLAN-v2.md) Period2-08/Period3-08。
>
> 本文只做**设计**，不落实现代码。所有数值口径均为标度整数（金额 `_units` scale=2 分、比率 scale=8、价格 scale=6），禁止 float；时间一律由注入的 `Clock` 提供。

> **2026-10-31 数据源更新**：实现阶段已将 live 数据源从「申万三级（legulegu/swsresearch）+ 东财/新浪概念」切换为**同花顺（THS）单一源**——行业与概念目录均走 akshare `stock_board_{industry,concept}_name_ths`，成分股因 akshare 无 THS 成分接口，改为抓取同花顺板块详情页分页表格（`q.10jqka.com.cn/{thshy,gn}/detail/code/{code}/page/{n}/`）。相应变化：① 行业为**扁平一级**（90 个，非申万三级树），`level=1`、`parent_code=None`；② `classification_version` 统一为 `THS`；③ `market.sector.source` 统一为 `ths`。其余口径（互斥/多归属、`E_map` 分母、SCD2、快照校验、`missing[]` 降级）不变。本文其余 SW/EM 描述为原始设计记录，以本条为准。

---

## 0. 决策摘要（人工评审请重点拍板）

### A. 口径/范围决策

| # | 决策点 | 推荐方案 | 备选 | 影响 / 是否需拍板 |
|---|---|---|---|---|
| **D-01** | 「三级行业板块」用什么分类 | **申万三级行业（SW2021，L1/L2/L3 三级树）**：实测 31 个一级 / 131 个二级 / 335 个三级，天然满足「三级」诉求；仅 `sw_index_third_cons` 给成分（按三级），父级只有名称无代码 | ① 东财行业板块（**单层**，无层级，不能做三级）；② 同花顺三级行业（需额外数据源/授权；THS 行业实测 90 个，仅作交叉校验） | 决定成分数据来源与全部口径；**需确认**（若你指的是同花顺三级行业，adapter 可替换但 taxonomy 名称需定） |
| **D-02** | 概念板块口径 | **东方财富概念板块**（`stock_board_concept_*`，数百个、事件驱动增删） | 同花顺概念（`*_ths`，需 cookie/反爬） | 概念多对多标签集，与行业严格分开 |
| **D-03** | 板块数据归属 | 落 **`market` schema 公共参考数据**（无 `user_id`、无 RLS、可再生缓存），不建任何「用户板块表」 | 放 `review`/`ledger` 用户域 | 板块是跨租户公共数据；与 `market.daily_bar` 同模式；G7 门禁不扫 `market` |
| **D-04** | 行业与概念是否共用一张表 | **共用 `market.sector` 目录表，用 `taxonomy` 字段区分**（`industry_sw` / `concept_em`）；成分仍用 SCD2 区间表，但**必须以「完整快照校验通过」为前提才允许 diff/关闭区间**（见 §4.4）；行业「每股当前仅一个三级行业」由 partial unique index 保证，概念「多对多」不建唯一 | 两张完全独立的表；或逐快照全量版本（source 建议） | 共用目录便于统一 API/前端；SCD2 体积小；快照校验是防「漏数据写成剔除」的硬闸门，**不可省略** |
| **D-04b** | 历史 as-of 策略 | **fail-closed**：首次同步日之前的历史一律 `not_covered`/blocked，不做「最早成分近似」 | 用最早已知成分近似（assumed） | 上游只给当前快照、无剔除日；近似会把当前成分冒充历史，归因结论不可解释（§8.5） |
| **D-05** | 成分时点（PIT） | **区间版本表**：`sector_member(effective_from, effective_to)`，同步时在「完整快照校验通过」后 diff 开闭区间；查询按 `as_of` 取有效行；首次同步前的历史 fail-closed（见 D-04b） | 每日全量快照 | 归因/历史看板需要「当时属于哪个板块」；区间表体积小、可回溯 |
| **D-06** | 基准板块权重 | 用户基准默认 `hs300_tr`（收益口径），板块权重从 **沪深 300 成分权重（CSIndex）** 派生；基准权重缺失时主动权重一律 `null` + missing 码 | 等权基准 / 0 权重 | 「板块偏好」= 相对基准的超低配，没基准就不能算；**不得用 0 或等权冒充**；当前仓库基准序列/权重本身也缺失（见 R-06），基准缺失是**大概率态**，必须一等公民处理 |
| **D-07** | 看板落位 | **不改 API-084 的响应结构**：新增 `GET /sectors/overview`；Dashboard「资产概览」内新增 `<section data-testid="asset-sector-block" data-board-member="asset-overview">` 子块**懒加载**（并行取行业 + 概念两次 overview），另设 `/sectors` 详情页承接三级树/概念全量。验收口径从「页面只有 2 个 `<section>`」修正为「**板块级容器恰 2 个**（`[data-board]`）」，子块用 `data-board-member` 归属，不新增第三板块 | ① 给 API-084 加第三板块（与「严格两板块」验收冲突）；② 只做独立页（用户「看板统计」诉求落空） | 涉及 Dashboard 验收/PRD 修订；**需拍板**（推荐子块 + 详情页） |
| **D-08** | 计算代码放哪 | 纯函数进 `stock_core/sector/`（零 I/O、零时钟）；聚合进 `stock_review/application/sectors.py`；API 只做定点格式化 | 在 router 里算 / 前端算 | 分层铁律 + 前端零口径 |
| **D-09** | 是否新增 18 指标 | **本期不新增 MetricDef**（18 指标口径已冻结；板块统计是看板聚合，不是「四问」指标）；归因期次再新增 `attribution.*` defs 并扩 `DATASET_WHITELIST` | 立即把板块集中度注册为 `perf.sector_hhi` | 会影响指标目录/18 指标验收/inputs_hash；**需拍板**（推荐本期不注册） |
| **D-10** | 归因模块边界 | 本期只交付**数据底座 + 口径定义 + API-036 additive 预留**，不实现 `/attribution` | 同步实现五因子/Brinson | 用户明确「后续将要实现」；本期不越界 |
| **D-11** | 数据同步入口 | **只读缓存 + 显式 `make sync-sectors`**（live 下唯一外呼入口）；**不挂请求路径**、**不塞进 API-083 rebuild-period**（否则每个用户重建都外呼一次） | 请求时 read-through；rebuild 顺带同步 | 与 `MARKET_LIVE_CACHE_READ_ONLY=true` 的既有裁决一致；陈旧只降级为 `stale` |
| **D-12** | 板块行情拉取范围（P1/归因期次） | 只为 **「全历史持仓涉及板块 ∪ 基准成分涉及板块」** 增量拉板块指数日线；**v1 看板不依赖板块日线**（权重/集中度只需成分与基准权重）；不做全市场 335+400 个板块的全量日线 | 全量同步；v1 就同步 | 控制 akshare 限频与耗时；板块指数源未验证（R-14），归因期可用个股合成降级 |
| **D-13** | 概念集中度口径 | 行业 HHI 为**主口径**（单归属分区，可排序）；概念只给**可重叠暴露**（`exposure`、覆盖度、`overlap`、多标签占比）与**辅助标签集中度** `HHI_tag`（独立命名/独立文案，禁止与行业 HHI 对比排序） | 概念也强行归一成 HHI 主指标 / 主概念强制单归属 | 概念多归属会导致权重和 >100%，混算会误导；主概念（方案 C）是建模假设不是事实，默认关闭 |
| **D-14** | 口径版本 | `definition_version="1.0"`（看板板块统计）；`classification_version`（SW2021 / EM-2026-10）与 `sector_dataset_version`（数据快照）单独存；分类版本变化时口径断点后续进 `trends.breaks` | 不版本化 | 申万年度调整、概念高频增删必须可解释 |
| **D-15** | 状态判定 | 两轴：`status ∈ {ok,degraded,stale,missing,insufficient,blocked}` + `sector_data_state ∈ {current,stale,missing}`（+ `asset_build_state`）；覆盖率 ≥95% 为 ok、90%~95% 为 degraded、<90% 为 insufficient（见 §6.4）；blocked/missing 不出数 | 只给 ok/error | 与 Dashboard 状态机、`missing[]` 码一致 |
| **D-16** | 错误码 | **不新增错误码**：参数错用 `VALIDATION_ERROR/INVALID_PERIOD`，资源不存在用 `NOT_FOUND`，归因模型不可用用 `MODEL_NOT_AVAILABLE`；数据缺失/降级用 200 响应的 `missing[]/status` 表达（`SectorMissingCode` Literal，与 `DashboardMissingCode` 同构） | 新增 `SECTOR_DATA_MISSING` 等 ErrorCode | 新增码要改 §3 错误码表 + CATALOG + api-check；200 降级码仓里已有先例 |

### B. 实现/范围决策

| # | 决策点 | 结论 |
|---|---|---|
| D-17 | 存储表 | `market.sector` / `market.sector_member` / `market.benchmark_member` / `market.sector_bar` / `market.sector_pull_range` / `market.sector_sync_state`（§3） |
| D-18 | 端口 | 新增 `SectorDataSource` Protocol（放 `stock_platform/ports.py`），adapter 全部放 `stock_platform`（不依赖 ledger），`create_sector_source()` 按 `SECTOR_MODE` 装配 |
| D-19 | API | 新增 **API-087 `GET /sectors/overview`**（看板统计，P0）、**API-088 `GET /sectors`**（目录，P0）；归因期次再扩 API-036；API-084 仅 **additive** 增 `links.sectors` |
| D-20 | 归因接口 | 五因子 `factors[].code` **从 `1.0` 起固定含 `sector`**（不先出 4 因子再补）；Brinson 用 `model=brinson_industry`，`factors[].code ∈ {allocation, selection, interaction}` + additive `sectors[]` 明细；单期 Brinson `1.0`、Carino 多期 `2.0` |
| D-21 | 测试 | 纯函数 oracle + 手算 golden（小宇宙 5~8 只股票）+ API 三件套 + 契约/隔离门禁 + fixture 确定性；见 §10 |
| D-22 | **权重分母** | **已映射权益市值 `E_map`**（`holding_market_value` 中能映射到板块的 A 股市值），不是总资产、不是全部持仓市值：保证 `Σ_s w_s = 1`、`Σ_s active_weight = 0`、HHI 同基；覆盖率/未分类/非股票**单独披露**。`holding_market_value` 包含的债/基金/货基/未映射股票不进分母 | 用 `holding_market_value` 或总资产作分母（会让所有板块系统性低配/主动权重和 ≠ 0） | 与 04 前端草案、02 契约草案的 `holding_market_value` 分母不同，**以本裁决为准**；§6/§7 已按此写 |
| D-23 | **归因权重与精度** | Brinson 用 **期初权重（BoP，R1）**，不用 PRD 字面的「区间平均权重」；金额换算 `amount = ratio × 期初总资产`，**Brinson 金额只承诺 `≤ 本金×1bp`**（100 万本金 ≤100 元），`≤0.01 元` 只属于 M1 五因子金额域 | R2 区间平均权重 + 显式 Timing 项（需改 PRD 验收） | 涉及 PRD §7.2 一句话与 M2 验收修订；**需拍板**（推荐 R1） |
| D-24 | **Dashboard 契约** | `DashboardLinksOut` additive 增加 `sectors`；`Dashboard.test.tsx:181/403` 与 `test_dashboard.py:466-473` 的 `links` 等值断言同步更新；API-084 响应体其余不变 | 不加 link、前端硬编码路径 | 低风险但必须同 PR 改测试，否则红 |

---

## 1. 背景、目标与非目标

### 1.1 现状

- **完全没有板块数据**：全仓 `grep 板块|sector|industry` 只命中注释里的「板块涨跌停」「指标域」等字样，无任何板块表/接口/前端。
- 已有**公共行情缓存**范式：`market.daily_bar` / `market.pull_range`（[market_models.py](../../backend/packages/platform/src/stock_platform/market_models.py)），无 RLS、无 `user_id`、可再生；`MarketDataSource` 端口 + replay/live 两个 adapter + `CachedMarketDataSource`（[ports.py](../../backend/packages/platform/src/stock_platform/ports.py)、[market_live.py](../../backend/packages/platform/src/stock_platform/market_live.py)）可直接照搬。
- 已有**看板聚合**：`GET /dashboard`（API-084）两板块，返回当前持仓行（含 `weight_ratio_units`）与全历史概览；聚合逻辑在 [dashboard.py](../../backend/packages/review/src/stock_review/application/dashboard.py)，权威口径见 [Dashboard 设计](2026-10-28-dashboard.md)。
- 已有**集中度纯函数**：`stock_core.stats.concentration`（`top_k_share/cr1_units/gini_units/hhi_indices`）与 `behavior.hhi`（个股 HHI，含现金口径）——**但都是「按个股」的，不是「按板块」的**。
- 归因**未实现**：API-036 仅在 [API-Catalog](v2/API-Catalog.md) 有契约（`model=five_factor|brinson_industry`），指标引擎 `DOMAINS` 已含 `attribution`（[registry.py](../../backend/packages/review/src/stock_review/metrics/registry.py)），但没有任何 `attribution.*` def；PRD FR-R7/§A25 已定义 Brinson 公式。
- 缺口：**没有证券主数据**（只有 6 位代码 + 名称），板块成分只能按代码映射；成分/分类会随时间和上游数据源漂移。

### 1.2 目标

1. **数据底座**：把三级行业（申万 L1/L2/L3）与概念板块的**目录、成分（含时点）、基准成分权重**落到本地可重建的 `market` 公共缓存；**板块指数日线（P1）** 同表结构预留、归因期次接入；replay 模式可用确定性夹具跑通全链路。
2. **看板统计**：给出「板块偏好」（相对基准的超配/低配）与「板块集中度」（行业 HHI / 有效板块数 / Top1/Top3；概念覆盖度与可重叠暴露），并严格区分行业与概念两种口径。
3. **归因预留**：定义板块收益/权重的数据契约与数学口径，使后续五因子中的「板块」因子与行业 Brinson（Period3-08）可以直接消费，不再回头改数据模型。
4. **工程一致**：遵守禁 float、Clock 注入、契约先行、RLS/公共数据边界、状态机与门禁；每个新增点有验收断言。

### 1.3 非目标

- 不实现 `/attribution`、月报中的板块归因区块、Carino 多期链接（归因期次）。
- 不做全市场板块成分的实时同步、不做板块行情分钟线、不做板块轮动策略/回测。
- 不引入新的字典/中间件（Redis、行情服务），不引入 ECharts（前端仍用 CSS/SVG/表格）。
- 不新增用户级板块配置（自选板块、板块权重自定义）——后续设置增强再议。
- 不把板块数据注册成「四问」18 指标（见 D-09）。

### 1.4 术语

| 术语 | 含义 | 代码/字段 |
|---|---|---|
| 三级行业板块 | 申万行业分类的第三级行业；向上有二级、一级父节点；**一只 A 股在同一分类版本下只属于一个三级行业** | `taxonomy=industry_sw`, `level ∈ {1,2,3}` |
| 概念板块 | 主题/事件标签集合；**一只股票可同时属于多个概念，也可一个都不属于** | `taxonomy=concept_em`, `level=0`（无层级） |
| 板块目录 | 板块本身的元数据（代码、名称、层级、父级、分类版本） | `market.sector` |
| 板块成分 | 股票与板块的归属关系，带生效区间 | `market.sector_member` |
| 板块行情 | 板块指数（申万行业指数 / 东财概念板块指数）的日收盘与日收益 | `market.sector_bar` |
| 基准板块权重 | 基准指数（默认 hs300）成分按板块聚合后的权重 | 由 `market.benchmark_member` × `sector_member` 派生 |
| 板块偏好 | 组合板块权重相对基准板块权重的偏离（主动权重，pp） | `w_p − w_b` |
| 板块集中度 | 组合在板块维度上的集中程度；行业用 HHI/有效板块数/TopK，概念用覆盖度/Top-N 暴露 | §6 |
| 未分类 | 有持仓但按当前分类版本映射不到对应板块的股票市值 | `unclassified_mv` |

---

## 2. 领域模型：为什么行业与概念必须分开

**一句话**：行业是**分区**（每个股票恰好落在一个格子里，权重和 = 100%）；概念是**标签**（同一股票可贴多个标签，暴露之和可 > 100%）。把它们塞进同一个「板块」列表会让集中度、偏好、归因全部失真。

| 维度 | 三级行业板块（申万） | 概念板块（东财） |
|---|---|---|
| 语义 | 国民经济行业分类，稳定、互斥 | 主题/事件标签，叙事驱动 |
| 股票归属 | **0 或 1 个三级行业**（当前分类版本） | **0..N 个概念** |
| 结构 | L1 → L2 → L3 三级树（`parent_code`） | 扁平列表（无父子级，`level=0`） |
| 数量级 | 31 / 二级约 124 / 335（SW2021） | 数百个（随事件增删） |
| 变更频率 | 低（年度/半年度调整，版本化） | 高（新增/删除/改名/成分调整随时发生） |
| 权重和 | 100%（对**已映射权益市值 `E_map`** 归一） | 暴露之和 ≥ 覆盖度，可 >100%（不归一） |
| 集中度算法 | HHI、有效板块数 `1/HHI`、Top1/Top3（标准分区口径） | 覆盖度、`exposure`、`overlap`、多标签占比；`HHI_tag` 仅辅助且独立命名 |
| 板块偏好算法 | `w_p,s − w_b,s`（基准权重可由 HS300 成分派生） | 无权威「概念基准权重」；只做组合自身暴露排序，标 `no_benchmark` |
| 归因用途 | 行业 Brinson（配置/选择/交互）、五因子「板块」因子 | **不进入 Brinson/五因子**（多归属不满足互斥前提）；仅描述性暴露 |
| 数据源 | akshare `sw_index_*` / `index_hist_sw` | akshare `stock_board_concept_*`（东财） |
| 代码形态 | `850111.SI`（申万行业代码） | `BK0999`（东财板块代码） |

**硬约束**：

1. 行业与概念**不共用「板块」聚合函数**：`sector_hhi()` 只接受「互斥分区」权重（分母 `E_map`，Σw=1）；概念必须走 `concept_exposure()`（允许重叠，输出覆盖度/`overlap`/`HHI_tag`）。
2. 概念板块**没有基准权重**：默认只给组合自身暴露，不输出主动权重（除非未来用户自定义概念基准，单列）。
3. 行业三级之间**只做树上聚合**（L3 市值 → L2 → L1），不做跨级相加；同一股票在 L1/L2/L3 三个层级上各出现一次是正常的（不同层级的视图，不是重复计数）。
4. **板块权重分母统一为 `E_map`（已映射权益市值）**，而不是总资产/全部持仓市值：否则 `Σw_s < 1`、`Σactive_weight ≠ 0`，「板块偏好」会系统性偏向低配。

### 2.1 实体关系

```
market.sector (taxonomy, sector_code)  ──parent_code──▶ market.sector (同 taxonomy)
        │ 1
        │ N
market.sector_member (taxonomy, sector_code, symbol, effective_from..effective_to)
        │
        └─ symbol ──映射──▶ review.asset_daily / 持仓重建（按 as_of）

market.benchmark_member (benchmark_id, symbol, weight_units, effective_from..effective_to)
        │
        └─ symbol × market.sector_member ──聚合──▶ 基准板块权重 w_b,s

market.sector_bar (taxonomy, sector_code, trade_date, close/return)   ← 归因期使用
market.sector_sync_state / market.sector_pull_range                   ← 新鲜度与覆盖元数据
```

### 2.2 分类版本与时点（PIT）

- `classification_version`：行业记 `SW2021`（申万 2021 版分类），概念记数据源批次（如 `EM-2026-10`）。两者独立演进。
- `market.sector_member` 用**半开区间** `[effective_from, effective_to)`：
  - 首次同步：写入当前成分，`effective_from = 同步业务日`（上游不提供历史变更日，只能「观测日」生效，文档显式披露）；
  - 后续同步：对同一 `(taxonomy, sector_code, symbol)` 做 diff，消失 → 关闭 `effective_to = 同步业务日`；新增 → 插入 `effective_from = 同步业务日`；不变 → 不动；
  - `as_of` 查询：`effective_from <= as_of AND (effective_to IS NULL OR as_of < effective_to)`。
- **历史局限（fail-closed，D-04b）**：首次同步前不存在成分历史，且上游只给「当前快照」、不提供剔除日。因此查询 `as_of < 首次同步日` 一律 `not_covered`/blocked，**不得**用最早已知成分近似冒充历史（归因期次如需历史，只能另接付费历史成分源，未验证）。
- **行业成分只存「最深层可得的归属」**：绝大多数股票落 L3；申万实测「特钢Ⅱ」无三级子节点，其成分 + 其他无三级归属股票共 **12 只（5217 vs 5229，约 0.23%）** 上游只到 L2，但**没有 L2 成分接口**，无法落库 → 这 12 只在**所有层级**均记未分类（不是只 L3 未分类）。看板/归因必须显式披露未分类桶，不得摊到假三级。
- 行业当前成分的唯一性由**部分唯一索引**兜底：`UNIQUE (symbol) WHERE taxonomy='industry_sw' AND effective_to IS NULL`，从数据库层杜绝「一股两行业」。

---

## 3. 数据模型（`market` schema，迁移 `0011_market_sectors`）

设计原则与 `market.daily_bar` 一致：**公共数据、无 RLS、无 `user_id`、可再生、不进备份正确性断言**；金额/价格/权重一律标度整数（`*_units`），枚举用 `VARCHAR` + 应用层校验（不建 PG Enum，新增值免迁移）。

### 3.1 `market.sector`（板块目录）

```sql
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE market.sector (
    taxonomy                VARCHAR(24)  NOT NULL,  -- 'industry_sw' | 'concept_em'
    sector_code             VARCHAR(32)  NOT NULL,  -- '850111.SI' | 'BK0999'
    level                   SMALLINT     NOT NULL,  -- industry: 1/2/3；concept: 0（无层级）
    name                    VARCHAR(64)  NOT NULL,
    parent_code             VARCHAR(32)  NULL,      -- 仅行业 L2/L3 有父级
    classification_version  VARCHAR(24)  NOT NULL,  -- 'SW2021' | 'EM-2026-10'
    source                  VARCHAR(16)  NOT NULL,  -- 'sw' | 'eastmoney'
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order              INTEGER      NOT NULL DEFAULT 0,
    pulled_at               TIMESTAMPTZ  NOT NULL,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (taxonomy, sector_code),
    CHECK (level BETWEEN 0 AND 3),
    CHECK (
        (taxonomy = 'concept_em' AND level = 0 AND parent_code IS NULL)
        OR (taxonomy = 'industry_sw' AND level BETWEEN 1 AND 3)
    )
);

CREATE INDEX ix_sector_parent ON market.sector (taxonomy, parent_code);
CREATE INDEX ix_sector_level  ON market.sector (taxonomy, level, is_active);
```

- `parent_code` 由同步层解析（申万接口给的是**父级名称**，需按 `(taxonomy, name, level-1)` 反查代码后再写；反查失败 → 该批同步失败，不写半截数据）。
- 已消失的板块不物理删：`is_active=false`，历史成分与行情保留（归因/趋势可回溯）。

### 3.2 `market.sector_member`（板块成分，时点区间）

```sql
CREATE TABLE market.sector_member (
    taxonomy        VARCHAR(24)  NOT NULL,
    sector_code     VARCHAR(32)  NOT NULL,
    symbol          VARCHAR(16)  NOT NULL,  -- 内部 6 位 A 股代码（去 .SH/.SZ）
    vendor_symbol   VARCHAR(16)  NULL,      -- 上游原值（如 600313.SH），留痕/排障
    upstream_effective_on DATE   NULL,      -- 申万「纳入时间」，仅观测，不用于 PIT
    effective_from  DATE         NOT NULL,  -- 含
    effective_to    DATE         NULL,      -- 不含；NULL = 当前有效
    observed_at     TIMESTAMPTZ  NOT NULL,
    source          VARCHAR(16)  NOT NULL,
    PRIMARY KEY (taxonomy, sector_code, symbol, effective_from),
    FOREIGN KEY (taxonomy, sector_code)
        REFERENCES market.sector (taxonomy, sector_code)
);

CREATE INDEX ix_sector_member_symbol ON market.sector_member (symbol, effective_from);
CREATE INDEX ix_sector_member_current ON market.sector_member (taxonomy, sector_code)
    WHERE effective_to IS NULL;

-- 行业：当前时点一只股票最多一个三级行业
CREATE UNIQUE INDEX uq_sector_member_industry_current
    ON market.sector_member (symbol)
    WHERE taxonomy = 'industry_sw' AND effective_to IS NULL;
```

- 概念**允许多行**，不建唯一索引；应用层校验同一 `(taxonomy, sector_code, symbol)` 区间不重叠。
- 行业**只落「当前接口可得的最深层」= L3**（申万只提供三级成分接口），L1/L2 由 `parent_code` 树上 rollup；这样行业当前唯一索引成立，且不会为同一股票重复存三层。实测 12 只股票无 L3 归属且无 L2 成分接口 → **所有层级均记未分类**（§2.2）。
- 只存 A 股股票（`classify_symbol(symbol) == 'stock'`）；基金/债券/货基不入板块成分（同步时过滤并计数到 `filtered_non_stock`）。
- 上游代码带交易所后缀（`600313.SH`/`000713.SZ`），落库前归一为 6 位 `symbol`，原值存 `vendor_symbol` 留痕；`upstream_effective_on` 只做观测，**不能**当 PIT。
- 表体积估算：行业当前 ~5,200 行；概念全量约 4~8 万行（数百个板块 × 平均 100~200 成分）；变更 diff 每次只写变化行，可接受。

### 3.3 `market.benchmark_member`（基准成分与权重）

```sql
CREATE TABLE market.benchmark_member (
    benchmark_id    VARCHAR(24)  NOT NULL,  -- 'hs300'
    symbol          VARCHAR(16)  NOT NULL,
    weight_units    BIGINT       NOT NULL,  -- scale=8：0.00543210 → 543210（存上游原值，不归一）
    weight_as_of    DATE         NOT NULL,  -- 权重发布日（实测可比成分日早 1 个月）
    effective_from  DATE         NOT NULL,
    effective_to    DATE         NULL,
    source          VARCHAR(16)  NOT NULL,  -- 'csindex'
    pulled_at       TIMESTAMPTZ  NOT NULL,
    PRIMARY KEY (benchmark_id, symbol, effective_from),
    CHECK (weight_units >= 0)
);

CREATE INDEX ix_benchmark_member_current ON market.benchmark_member (benchmark_id)
    WHERE effective_to IS NULL;
```

- 权重来自中证指数成分权重（CSIndex），`weight_units` 存**上游原值**（实测 300 行、权重和 100.002%），读取时按「映射到板块的权重之和」归一化后再用（§6.2）；`weight_as_of` 只做展示/审计，**选权重一律按 `effective_from <= as_of`**（观测生效日），避免用晚于快照日的权重造成前视。实测成分日期（2026-10-30）与权重日期（2026-09-30）不同步，归因期取 `effective_from <= period_start` 的最近一版，`assumptions` 披露 `weight_as_of`。
- 基准与 `hs300_tr`（全收益）是**同一个基准的两个视角**：`hs300_tr` 用于收益序列，`hs300` 成分权重用于板块权重。API 的 `benchmark` 参数仍按用户设置（默认 `hs300_tr`）传入，响应 `benchmark_id` 回显该值，`assumptions.benchmark_weight_source="csindex:hs300"` 说明权重来自成分；不要把两个 ID 混为一个字段。

### 3.4 `market.sector_bar`（板块指数日线）

```sql
CREATE TABLE market.sector_bar (
    taxonomy     VARCHAR(24) NOT NULL,
    sector_code  VARCHAR(32) NOT NULL,
    trade_date   DATE        NOT NULL,
    close_units  BIGINT      NOT NULL,  -- scale=6（板块指数点位）
    return_units BIGINT      NOT NULL,  -- scale=8（由 close 序列派生，首根=0）
    source       VARCHAR(16) NOT NULL,
    pulled_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (taxonomy, sector_code, trade_date),
    CHECK (close_units > 0),
    FOREIGN KEY (taxonomy, sector_code)
        REFERENCES market.sector (taxonomy, sector_code)
);
```

- 行业用申万行业指数日线、概念用东财板块指数日线（**均为 P1/归因期次，待联网验证**）；两者 `close_units` 绝对点位不可比，**只用 `return_units` 做归因**。v1 看板（权重/集中度/偏好）**不依赖** `sector_bar`；归因期若板块指数不可得，用「基准成分权重 + 本地个股 `return_units`」合成 `R_b,s`（`assumptions` 标明 `constituent_synthetic`）。
- 只拉「持仓历史涉及板块 ∪ 基准成分涉及板块」（D-12），减少外呼；`sector_bar` 同步失败不回滚目录/成分，只把 `bar_as_of` 置空并标 `not_covered`。

### 3.5 `market.sector_pull_range`（板块行情覆盖元数据）

```sql
CREATE TABLE market.sector_pull_range (
    id             VARCHAR(40) PRIMARY KEY,  -- sha256(taxonomy|code|start|end)[:32]
    taxonomy       VARCHAR(24) NOT NULL,
    sector_code    VARCHAR(32) NOT NULL,
    range_start    DATE        NOT NULL,
    range_end      DATE        NOT NULL,     -- 覆盖上界
    data_end       DATE        NULL,         -- 上游实际返回最后交易日
    requested_end  DATE        NOT NULL,
    complete       BOOLEAN     NOT NULL DEFAULT TRUE,
    bar_count      INTEGER     NOT NULL DEFAULT 0,
    source         VARCHAR(16) NOT NULL,
    checksum       VARCHAR(64) NOT NULL,     -- 逐 bar date:close_units 的 sha256 前 32 位
    pulled_at      TIMESTAMPTZ NOT NULL
);
```

语义与 `market.pull_range` 逐条对齐（区间并集、尾部刷新、负缓存、TTL），避免另造一套覆盖判定。

### 3.6 `market.sector_sync_state`（新鲜度）

```sql
CREATE TABLE market.sector_sync_state (
    dataset_key       VARCHAR(32) PRIMARY KEY,  -- 'industry_sw' | 'concept_em' | 'benchmark_hs300'
    snapshot_id       VARCHAR(40) NULL,         -- sha256(dataset|as_of|checksum)[:32]（最近成功快照）
    status            VARCHAR(16) NOT NULL DEFAULT 'succeeded',  -- succeeded | failed
    as_of             DATE        NULL,         -- 最近一次成功同步的业务日
    catalog_as_of     DATE        NULL,
    membership_as_of  DATE        NULL,
    bar_as_of         DATE        NULL,
    weight_as_of      DATE        NULL,         -- 基准权重发布日（仅 benchmark_*）
    classification_version VARCHAR(24) NULL,
    entity_count      INTEGER     NOT NULL DEFAULT 0,
    member_count      INTEGER     NOT NULL DEFAULT 0,
    source            VARCHAR(16) NOT NULL,
    checksum          VARCHAR(64) NOT NULL,     -- 目录+成分归一化后的内容摘要
    pulled_at         TIMESTAMPTZ NOT NULL,
    error_detail      VARCHAR(512) NULL         -- 失败分类/跳过原因摘要（不含用户数据）
);
```

- 读路径用 `as_of`/`bar_as_of` 判 `stale`；**不读系统时钟**，业务日来自注入 `Clock`。
- 同步失败**不推进** `as_of`，保留旧值并写 `status='failed'`/`error_detail`；下次同步自愈。成功前**绝不写明细**（整快照校验通过后才 diff）。
- 只保留最新快照元数据；若未来需要快照历史（审计/回滚），再扩 `market.sector_snapshot`（P1，不在本期表数内）。

### 3.7 迁移与门禁

- Alembic 迁移编号：`0011_market_sectors`（`down_revision=0010_asset_daily_remark`）。`make migrate-check` 校验 ORM 列与迁移逐列一致（`market_models.py` 新增 6 个模型类；`migrations/env.py` 必须 import 新模型，否则 `alembic check` 看不到表）。
- 迁移内必须含：`CREATE SCHEMA IF NOT EXISTS market`；6 表 DDL；`GRANT USAGE ON SCHEMA market TO stock_app, stock_backup`；`stock_app` DML、`stock_backup` SELECT（漏授权首次请求即 permission denied）；`INSERT ... platform.schema_version = '0011_market_sectors'`；同步把 `stock_api/version.py::SCHEMA_VERSION` 改成 `"0011_market_sectors"`（启动断言，否则拒绝启动）。
- `downgrade` 只按 FK 逆序 DROP 本次 6 表，**不 DROP `market` schema**（`daily_bar/pull_range` 仍在用）；版本戳回 `0010_asset_daily_remark`。
- **G7 不受影响**：[gate_rls_coverage.py](../../scripts/ci/gate_rls_coverage.py) 只扫 `platform/ledger/review` 且只查带 `user_id` 的表；`market.*` 明确不在范围内（与 `market.daily_bar` 同理）。
- 备份/恢复演练不对 `market.*` 做正确性断言（可再生缓存，与 ADR-0025 一致）；但**首次同步前的 as-of 历史不可重建**，属于已知限制（§8.5），不是备份问题。
- 测试清理：`backend/tests/conftest.py` 默认不 TRUNCATE `market`（公共缓存跨用例共享）；板块测试自行清理时按 FK 逆序（先 `sector_member/benchmark_member/sector_bar` 后 `sector`）。

### 3.8 为什么用 SCD2 而不是「逐快照全量版本」

调研阶段曾建议每次同步写一份不可变快照（`sector_sync + sync_id` 全量行），理由是上游只给当前全量、没有剔除事件。本设计仍选 **SCD2 区间表**，理由与风险控制：

- 体积：概念全量 4~8 万行/次；SCD2 只存当前 + 变更，长期小一个量级。
- 风险已用硬闸门覆盖：**先做完整快照校验（行数/父级/唯一/成分覆盖）→ 再 diff**，且目录/成分/基准在**同一事务**提交；上游漏一页时整批失败、旧行不动（§4.4）。这消除了「漏数据被写成剔除」的主要风险。
- 代价：SCD2 的 `effective_from` 只是观测日，不是上游真实变更日；已在 D-04b/§8.5 用 fail-closed 处理历史区间。
- 若未来需要审计/回滚到某一历史快照，再 additive 增 `market.sector_snapshot`（P1），不影响现有读路径。

### 3.9 为什么板块日线独立表（不复用 `market.daily_bar`）

调研建议把板块指数点位塞进 `market.daily_bar`（`symbol='sw:850111.SI'`）以少两张表。本设计选独立 `market.sector_bar`：

- `daily_bar.symbol` 的既有语义是「证券代码」，`MarketDataSource`/`validate_symbols` 只接受 6 位证券代码；混入板块代码会让「哪些行是股票」隐式依赖字符串前缀，后续易误读。
- 板块点位与个股价格不是同一估值口径（不存在复权/停牌/分红），独立表可加 `taxonomy` FK、独立覆盖元数据，读模型更清晰。
- 代价是多两张表（`sector_bar` + `sector_pull_range`），但都是可再生缓存、迁移成本低，且 v1 看板不依赖它们（P1）。

---

## 4. 数据接入（端口、适配器、同步）

### 4.1 端口：`SectorDataSource`（platform 层）

**不扩现有 `MarketDataSource`**——行情与板块是两类数据，扩协议会破坏现有 replay/live 两个实现与全部测试桩。新增独立端口（放 [ports.py](../../backend/packages/platform/src/stock_platform/ports.py)）：

```python
@dataclass(frozen=True, slots=True)
class SectorCapabilities:
    mode: str                              # "replay" | "live"
    taxonomies: tuple[str, ...]            # ("industry_sw", "concept_em")
    benchmark_ids: tuple[str, ...]         # ("hs300",)
    supports_membership: bool = True
    supports_board_bars: bool = True
    supports_benchmark_weights: bool = True

@dataclass(frozen=True, slots=True)
class SectorInfo:
    taxonomy: str; sector_code: str; name: str; level: int
    parent_code: str | None; classification_version: str

@dataclass(frozen=True, slots=True)
class SectorMembership:
    taxonomy: str; sector_code: str; symbol: str

@dataclass(frozen=True, slots=True)
class SectorBar:
    taxonomy: str; sector_code: str; trade_date: date
    close_units: int   # scale=6
    return_units: int  # scale=8

@dataclass(frozen=True, slots=True)
class BenchmarkMember:
    benchmark_id: str; symbol: str; weight_units: int  # scale=8
    weight_as_of: date           # 权重发布日（仅展示/审计，选权重用 effective_from）

@runtime_checkable
class SectorDataSource(Protocol):
    @property
    def capabilities(self) -> SectorCapabilities: ...
    @property
    def as_of(self) -> date: ...          # 数据覆盖到的最后业务日
    @property
    def provenance(self) -> str: ...      # replay: 文件+sha256；live: source/version
    async def get_catalog(self, taxonomy: str | None = None) -> Sequence[SectorInfo]: ...
    async def get_memberships(self, taxonomy: str, *, as_of: date | None = None, level: int | None = None) -> Sequence[SectorMembership]: ...
    async def get_board_bars(
        self, sector_keys: Sequence[tuple[str, str]], start: date, end: date
    ) -> Sequence[SectorBar]: ...
    async def get_benchmark_members(
        self, benchmark_id: str, *, as_of: date | None = None
    ) -> Sequence[BenchmarkMember]: ...
```

同步能力单独成协议（避免 `hasattr` 探测）：

```python
@runtime_checkable
class SyncableSectorSource(Protocol):
    async def sync_to_cache(self, *, today: date, dry_run: bool = False) -> SyncReport: ...
```

- `ReplaySectorSource`：读 `settings.fixtures_dir / "market" / "sectors.json"`，懒加载、fail-closed；不实现同步。
- `CachedSectorSource`（装饰器）：读 `market.*` 缓存；`SECTOR_CACHE_READ_ONLY=true`（默认）时**绝不外呼**，缺口 → 返回空 + 调用方降级；`false` 仅调试用。
- `AkshareSectorSource`：实现外呼 + `sync_to_cache`；akshare 走 optional extra `live`，未安装且 `SECTOR_MODE=live` → 启动期 fail-fast（与行情一致）。
- 装配：`stock_platform/sector_source.py` 的 `create_sector_source(settings, *, session_factory, clock)`；`AppState.sector_data` 注入；worker 同源装配。**不依赖 ledger**（L1-L5：platform 只依赖 contracts/core）。与行情的 `create_market_source` 留在 ledger 的历史原因不同：板块不需要 `ReplayMarketDataSource` 那套 ledger 逻辑，replay/live/工厂全部放 platform 更符合分层；若评审坚持与行情同构，可把 replay/工厂下放 ledger，接口不变（实现细节，不影响契约）。

### 4.2 akshare 映射（primary）

> 本机实测（2026-10-31，akshare 1.19.1）：申万目录/成分可用（31/131/335；成分 0.36s/次）；东财 `push2.eastmoney.com` 在本机系统代理下长时间 `ProxyError`，但有可用窗口（`push2` 200/`push2his` 200），**不能断言源不可用**，需联网环境复验。表内「实测」列为已确认；其余标「未验证」的必须实现前联网核对列名/参数，不得凭记忆编码。

| 用途 | akshare 函数 | 关键返回列（实测/文档） | 落库映射 |
|---|---|---|---|
| 一级行业目录 | `sw_index_first_info()` | 实测 31 行：`行业代码, 行业名称, 成份个数, 静态市盈率, TTM(滚动)市盈率, 市净率, 静态股息率` | `sector(taxonomy=industry_sw, level=1)` |
| 二级行业目录 | `sw_index_second_info()` | 实测 131 行：上述 8 列 + `上级行业`（父级**名称**） | `level=2`，父级按名称反查 L1 |
| 三级行业目录 | `sw_index_third_info()` | 实测 335 行：`行业代码, 行业名称, 上级行业, 成份个数, ...`（只有父级**名称**，无父级代码；名称含全角 `Ⅱ`，禁止 strip/归一） | `level=3`，`parent_code` 反查；`classification_version=SW2021` |
| 三级行业成分 | `sw_index_third_cons(symbol="850111.SI")` | 实测 23 列、8 行、0.36s/次；含 `股票代码`（带 `.SH/.SZ`）、`股票简称`、`纳入时间`（仅观测）；全量 335 次约 2~4 分钟 | `sector_member(industry_sw)`；symbol 归一 6 位，原值存 `vendor_symbol` |
| 行业指数日线 | `index_hist_sw(symbol=...)`（函数存在） | **未验证**：申万行业指数日线来源未确认；v1 看板不依赖它 | `sector_bar(industry_sw)` **P1**；缺失时归因期次用个股合成降级 |
| 概念目录 | `stock_board_concept_name_em()` | **未验证**（本机 ProxyError）；源码列：`排名,板块名称,板块代码,最新价,...,总市值,...` | `sector(taxonomy=concept_em, level=0)` |
| 概念成分 | `stock_board_concept_cons_em(symbol=<名称或BK代码>)` | **未验证**；源码 16 列、分页 `pz=100`、页间 sleep 0.5~1.5s | `sector_member(concept_em)` |
| 概念指数日线 | `stock_board_concept_hist_em(symbol=..., period="日k")` | **未验证**（一次 push2his 直连 200，但 akshare 调用失败） | `sector_bar(concept_em)` **P1** |
| 基准成分/权重 | `index_stock_cons_csindex("000300")` + `index_stock_cons_weight_csindex("000300")` | 实测各 300 行；成分 as-of 2026-10-30、权重 as-of 2026-09-30，权重和 100.002% | `benchmark_member(hs300)`；存 `weight_as_of`，读取时归一化 |

**适配器纪律**：

- 全部 akshare 调用经 `asyncio.to_thread` + 进程内 `Semaphore(2)`（沿用 `market_live` 的出站治理）；超时/重试有上限，失败分类为 `transient` / `invalid_data`。
- 返回行必须逐列校验（代码格式、日期、权重非负、板块名非空），**任何异常整批失败**，不写半截（事务内 upsert）。
- 记 `provenance = f"live:akshare@{akshare.__version__}"`，写入 `pulled_at`；不可复现的时间戳不进入 fixture。

### 4.3 replay 夹具（确定性）

`fixtures/market/sectors.json`（由 `seed-demo`/`write_market_fixtures()` 生成，固定日期与排序，无运行时刻戳）：

```json
{
  "schema_version": 1,
  "as_of": "2026-09-30",
  "first_sync_on": "2026-09-30",
  "classification": {"industry_sw": "SW2021", "concept_em": "EM-2026-10"},
  "benchmark": {"id": "hs300", "as_of": "2026-09-30"},
  "sectors": [
    {"taxonomy": "industry_sw", "sector_code": "801030.SI", "level": 1, "name": "基础化工", "parent_code": null},
    {"taxonomy": "industry_sw", "sector_code": "850111.SI", "level": 3, "name": "种子", "parent_code": "850100.SI"},
    {"taxonomy": "concept_em", "sector_code": "BK0999", "level": 0, "name": "人工智能", "parent_code": null}
  ],
  "memberships": [
    {"taxonomy": "industry_sw", "sector_code": "850111.SI", "symbol": "600519", "effective_from": "2026-09-30"},
    {"taxonomy": "concept_em", "sector_code": "BK0999", "symbol": "300750", "effective_from": "2026-09-30"}
  ],
  "benchmark_members": [
    {"benchmark_id": "hs300", "symbol": "600519", "weight": "0.05320000", "weight_as_of": "2026-09-30", "effective_from": "2026-09-30"}
  ],
  "series": {
    "850111.SI": [{"date": "2026-01-30", "close": "1234.567890", "return": "0.00000000"}],
    "BK0999": [{"date": "2026-01-30", "close": "987.654321", "return": "0.00000000"}]
  }
}
```

- 夹具必须覆盖：demo 持仓（600519/000001/300750/601318/000858 等）的行业与概念；至少 2 个 L1 行业、3 个 L3 行业、3 个概念；1 只「无概念」股票、1 只「多概念」股票、1 只「未分类」股票——用来固定边界测试。示例中的 `600519→850111.SI` 只是**示意**（真实分类以同步数据为准），不要固化成测试知识。
- `first_sync_on` 必须等于最小 `effective_from`；夹具查询 `as_of < first_sync_on` 必须得到 `SECTOR_HISTORY_NOT_COVERED`（用于测 fail-closed）。
- 夹具只读；`ReplaySectorSource.as_of` 取 `as_of` 字段，**不读系统时间**。

### 4.4 同步算法（`make sync-sectors`，幂等）

入口：`stock_api.cli sync-sectors [--start YYYY-MM-DD] [--taxonomies industry,concept] [--dry-run]`；`make sync-sectors` 包装。仅 `SECTOR_MODE=live` 可用，replay 直接提示无需同步。**`--dry-run` 只做配置/DB/表结构检查并打印「将同步的数据集 + 目标 as_of + 预计表数」，不联网、不写库**；不要把它当成「联网预演」（需要真实行数/网络计划时另加 `--plan`，P1，联网只读）。

1. **读配置与业务日**：`today = clock.business_today()`；读 `market.sector_sync_state` 旧值。
2. **目录（完整获取）**：按 taxonomy 拉目录 → 组装父子（名称反查父级代码，失败即整批失败）→ **快照校验**（见下方）→ 事务内 `INSERT ... ON CONFLICT (taxonomy, sector_code) DO UPDATE`；上游消失的标 `is_active=false`。
3. **成分 diff（校验通过后才允许）**：拉每个板块当前成分（仅 A 股，`classify_symbol=='stock'`；上游带后缀的先归一）：
   - **快照完整性闸门**：所有板块成分拉取成功、行数与目录 `成份个数` 一致（允许已知缺口并记录）、行业当前唯一约束预检通过 → 才进入 diff；
   - **同一事务、先 close 后 insert**：先 `UPDATE ... SET effective_to=today WHERE effective_to IS NULL AND (sector_code,symbol) 不在新快照`，再 `INSERT` 新归属；同一股票换行业时不会撞 `uq_sector_member_industry_current`（若顺序反过来，旧行仍 current 时插新行必冲突）；
   - 新成分 → `INSERT (effective_from=today)`；
   - 消失成分 → `UPDATE effective_to=today WHERE effective_to IS NULL`；
   - 不变 → 不写。
   - 冲突保护：行业当前唯一索引报错 → 说明上游数据异常，**回滚并报错**，不静默修正。
4. **基准权重**：拉 hs300 成分/权重；与当前区间内容比较，变化才关闭旧区间/开新区间（`effective_from=today`，`weight_as_of` 仅记录权重发布日；读取按 `effective_from` 选版本）。
5. **板块日线**（P1/归因期次，`--with-bars` 显式开启）：计算「持仓历史代码 ∪ 基准成分」涉及板块集合 → 逐个板块增量拉取（从 `sector_pull_range` 上界+1 到 today）→ upsert `sector_bar` + 更新 `sector_pull_range`；失败只影响 `bar_as_of`，不回滚目录/成分。
6. **状态**：全部成功 → 更新 `sector_sync_state`（`snapshot_id/status/as_of/catalog_as_of/membership_as_of/bar_as_of/weight_as_of/entity_count/member_count/checksum/pulled_at`）；任何一步失败 → **整个快照回滚、不写任何明细、旧状态不推进**，只写/保留 `status='failed'` + `error_detail`，命令退出码非 0。
7. **审计**：只写系统级日志/`platform.audit_log`（`actor=system`、无用户数据）；不写 per-user 记录。

**快照校验清单（防「漏数据写成剔除」）**：

| 校验 | 规则 | 失败动作 |
|---|---|---|
| 目录行数 | 一级 31、二级 131、三级 335（允许上游版本变化，但必须与 `classification_version` 绑定并整体替换） | 整批失败，旧快照保留 |
| 名称唯一性 | 同层 `name` 唯一、`code` 唯一 | 整批失败 |
| 父级完整性 | 二级父名 ∈ 一级名称、三级父名 ∈ 二级名称；孤儿 = 失败 | 整批失败 |
| 成分覆盖 | L1/L2/L3 成分合计均与目录 `成份个数` 合计一致（实测 5229/5229/5217；L3 缺口 12 只记为已知未分类，阈值常量化，不硬编码） | 差异超阈值 → 失败 |
| 当前唯一 | 行业 `(symbol)` 当前唯一；同一 symbol 不得出现在两个 L3 | 失败并告警（上游脏数据） |
| 幂等 | 同 `checksum` 重跑 → no-op | 直接返回 |

> 12 只是**当前实测**的 L3 缺口，不写死在代码里：允许差集（无 L3 归属的 symbol 集合）由本次快照的「L2 成分 − L3 成分」推导并写入同步元数据；下次上游归类变化时阈值随之变化，校验失败即告警而不是静默接受。

**限速（实测）**：申万总览 3 个函数各自 GET 同一页面，反爬会返回 HTML 变体（`AttributeError`）/403/429；单并发、每次调用间隔 0.3~0.5s、有上限重试（3 次，指数退避），**整批重试而不是逐行重试**。申万成分 0.32~0.34s/次，全量 335 次约 2~4 分钟；东财概念分页 `pz=100` + 页间 0.5~1.5s，首次全量可能 10~30 分钟。

**幂等性**：同一天重复执行不产生重复区间（diff 后无变化则不写）；`checksum` 相同 → 整个同步可短路（除 bar 增量外）；并发双跑由 `UNIQUE(dataset_key, ...)` + CLI `pg_advisory_lock` 兜底。**目录 + 成分 + 基准权重必须在同一个短事务内提交**；校验失败时连目录也不落库（避免「目录新、成分为空」的半截状态）。

### 4.5 与 API-083 `rebuild-period` 的边界

- `rebuild-period` 的职责是**用户数据编排**（rounds→assets→nav→metrics），板块是**公共参考数据**：**不触发板块外呼**，只读取缓存。
- 读路径状态：若 `sector_sync_state.as_of < asset_daily.as_of` → 板块统计 `status=stale` + `data_as_of=min(...)`，照常展示数字并提示；若从未同步 → `status=missing`、数字全 `null`、`missing[]` 带操作建议。`status='failed'` 时 `as_of/checksum` 仍是上次成功值，数字照常但提示 `SECTOR_SYNC_FAILED`。
- 未来若要做「归因任务内自动同步」，必须走**全局限流/单飞**（同一时刻只允许一个 sync job），并默认关闭；本期不做。

### 4.6 配置项（`Settings`，`.env` 可覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SECTOR_MODE` | `replay` | `replay`/`live` |
| `SECTOR_FIXTURES_FILE` | 空（用 `fixtures/market/sectors.json`） | replay 夹具路径 |
| `SECTOR_CACHE_READ_ONLY` | `true` | 读路径禁外呼（默认，防盘中/请求期拉数据） |
| `SECTOR_LIVE_MAX_CONCURRENCY` | `2` | 出站并发上限 |
| `SECTOR_LIVE_TIMEOUT_SECONDS` | `30` | 单次调用超时 |
| `SECTOR_LIVE_RETRY_ATTEMPTS` | `2` | 有上限重试 |
| `SECTOR_INDUSTRY_LEVEL_DEFAULT` | `1` | 看板默认行业层级（L1 可比性最好） |
| `SECTOR_COVERAGE_OK_MIN_UNITS` | `950_000_000` | 覆盖率（scale=8）≥ 该值 → ok；**int，不用 float** |
| `SECTOR_COVERAGE_DEGRADED_MIN_UNITS` | `900_000_000` | ≥ 该值 → degraded；否则 insufficient |
| `SECTOR_BENCHMARK_ID` | `hs300` | 板块**权重**基准（与收益基准 `hs300_tr` 区分） |
| `SECTOR_BENCHMARK_MAX_AGE_DAYS` | `45` | 基准权重最大可接受滞后天数 |
| `SECTOR_INDUSTRY_STALE_DAYS` | `35` | 行业快照超过该天数 → `stale`（season 调整低频） |
| `SECTOR_CONCEPT_STALE_DAYS` | `7` | 概念快照超过该天数 → `stale`（事件高频） |
| `SECTOR_SW_CALL_INTERVAL_SECONDS` | `0.4` | 申万总览/成分调用间隔（单并发） |
| `SECTOR_EM_PAGE_INTERVAL_SECONDS` | `0.8` | 东财分页间隔 |
| `SECTOR_SYNC_RETRY_ATTEMPTS` | `3` | 整批重试次数（指数退避） |
| `SECTOR_FLAT_ACTIVE_UNITS` | `100_000` | `|Δ| ≤ 0.001`（scale=8）判平配 |

---

## 5. 读路径与仓储

### 5.1 仓储：`SectorRepository`（`stock_review/repositories/sectors.py`）

- **单一读路径**：API/应用层只依赖 `SectorDataSource` 端口；`CachedSectorSource`（live/缓存模式）内部用 `SectorRepository` 读 `market.*`，`ReplaySectorSource` 读夹具。**应用层不直接 new 仓储**，避免「端口读一套、仓储读另一套」。
- 方法全部显式收 `session`（公共数据，**不收 `user_id`**；这是 `market` 公共数据的既有例外，与 `DailyBar` 查询一致）；但**用户持仓/汇总**必须在应用层持有 `user_id`。
- 关键方法：
  ```python
  async def list_catalog(session, *, taxonomy=None, level=None, active_only=True) -> list[SectorRow]
  async def members_at(session, *, taxonomy, as_of, level: int, symbols: Sequence[str] | None = None) -> dict[str, str | list[str]]
      # 行业：symbol → 该层级的父/自身 sector_code（由 L3 归属 rollup）；概念：symbol → [concept_code,...]
  async def benchmark_weights_at(session, *, benchmark_id, as_of) -> dict[str, int]  # symbol -> weight_units
  async def board_bars(session, *, keys, start, end) -> list[SectorBarRow]
  async def sync_state(session, *, dataset_key) -> SectorSyncStateRow | None
  ```
- 索引命中：`members_at` 走 `(symbol, effective_from)`；`benchmark_weights_at` 走 `(benchmark_id) WHERE effective_to IS NULL`；`board_bars` 走主键范围扫描。

### 5.2 应用层：`stock_review/application/sectors.py`

- `build_sector_overview(session, user_id, sector_data: SectorDataSource, *, as_of, taxonomy, level, parent, benchmark_id, clock) -> SectorOverview`（只读，不建 job、不写表；`sector_data` 由 `AppState`/依赖注入，live 下即 `CachedSectorSource`）。
- 复用现有持仓重建：把 `dashboard._holdings_at` 提取为公共 `application/portfolio.py::holdings_at(...)`（**行为不变**，dashboard 与 sectors 共用，避免两套估值口径）。持仓来源与 API-084 的「最新资产快照」一致：`as_of = 最新 asset_daily.trade_date`（或显式查询参数，但必须 ≤ 最新快照日）。
- 聚合步骤：
  1. `holdings_at(as_of)` → 每个 `symbol` 的 `market_value_units`（缺失价/成本兜底的标的按现有状态标记，不参与板块权重）；
  2. 过滤 `classify_symbol(symbol) == 'stock'` → 权益市值 `E`；非股票（债/基金/货基/现金）单列 `non_stock_market_value`（实测/理论 = `holding_market_value − E`；若重建持仓与 `asset_daily` 不一致导致为负，**夹紧为 0** 并 `status=degraded` + `SECTOR_ASSET_MISMATCH`，不输出负值）；
  3. `SectorRepository.members_at(taxonomy, as_of, level, symbols=股票代码)` → 成分映射；已映射部分 `E_map(L)`，未映射 `E_unmap(L)`；
  4. 行业按层级聚合市值（`level/parent` 过滤）→ 权重（分母 `E_map`，§6）；概念按多归属算 `exposure`（分母 `E`，§6.4）；未映射市值 → `unclassified`；
  5. 基准权重：`benchmark_weights_at` × 行业成分 → 基准板块权重（归一化）；概念无基准；
  6. 组装 DTO（标度整数 + 状态 + missing[]），API 边界格式化。
- 纯函数全部下沉 `stock_core/sector/`（§6.6），应用层只做取数与编排。

### 5.3 性能

- 持仓重建是既有 `/dashboard` 同款成本（≤200 只、45 天价格窗口）；板块部分新增：`members_at`（按 symbol 索引，200 次内）+ 基准成分（300 行）+ 目录（~370 行），单次查询可忽略。
- 目标：`GET /sectors/overview` p95 ≤ 300ms（与 Dashboard 同标准）；小宇宙夹具下 ≤ 100ms。
- 不做跨请求缓存（无 Redis）；如需可加进程内 LRU（后续，按 `checksum+as_of` 键）。

---

## 6. 计算口径（标度整数，core 纯函数）

> 所有函数零 I/O、零时钟、无随机；输入/输出均为标度整数；除法统一 `div_round(numer, denom, scale=k)`（内部 `ROUND_HALF_UP`），中间乘法先整数再除 `10**8`，禁止先量化再乘。金额 scale=2（分），比率 scale=8，价格 scale=6。

### 6.1 记号与分母（D-22）

在快照日 `t`（默认最新 `asset_daily.trade_date`）：

```text
E_t        = Σ_{i: classify_symbol(i)=="stock" 且有市值} mv_i,t        # 权益市值（只含 A 股）
E_map,t(L) = Σ_{i: 有当前入库板块归属（L3，L1/L2 由 rollup）} mv_i,t              # 已映射权益市值
E_unmap,t  = E_t − E_map,t(L)                                          # 未分类权益市值
holding_mv = asset_daily.market_value_units                            # 全部证券持仓市值（含债/基金）
non_stock  = holding_mv − E_t                                         # 债/基金/其他非股票持仓
coverage_t(L) = div_round(E_map,t(L), E_t, scale=8)                  # 板块覆盖率（随请求 level 变化）；E_t=0 → null
equity_pos = div_round(E_t, total_assets, scale=8)                    # 股票仓位（总资产分母）
```

- **板块权重分母 = `E_map(L)`**（不是 `holding_mv`、不是总资产）：保证 `Σ_s w_s = 1`、`Σ_s (w_p−w_b) ≈ 0`、HHI 与主动权重同基。未分类/非股票以 `coverage(L)`、`unclassified`、`non_stock_market_value` 单独披露，**不摊进任何板块**。**`coverage/status` 按请求 `level` 计算**，响应里的 `portfolio.coverage` 必须带 `level` 语义（L1 覆盖率 ≥ L3 覆盖率的常见差异来自 12 只无 L3 股票）。
- `E_t=0`（无股票持仓）→ 所有权重/集中度 `null`，`status=missing`（空仓不是「集中度 1.0」；与 `behavior.hhi` 的现金口径不同，字段说明必须写清）。
- `E_map(L)=0` 且 `E_t>0`（全未分类）→ 权重/HHI `null`，`coverage=0`，`status=insufficient` + `SECTOR_MEMBERSHIP_MISSING`。

### 6.2 行业权重与基准权重（板块偏好）

对行业层级 `L ∈ {1,2,3}`（默认 L1）。成分只存最深层（多为 L3），`L=1/2` 用 `parent_code` 树上 rollup：

```text
MV_s,t(L) = Σ_{i: 归属节点 ∈ descendants(s, L)} mv_i,t              # L3 直接；L1/L2 由子节点求和
w_p,s     = div_round(MV_s,t(L), E_map,t(L), scale=8)                # 组合板块权重，Σ≈1
w_b,s     = div_round(Σ_{i∈s} bench_w_i, Σ_{i∈mapped} bench_w_i, 8)  # 基准板块权重，归一化
Δ_s       = w_p,s − w_b,s                                            # 主动权重（板块偏好）
Δ_pp,s    = div_round(Δ_s, 10_000)                                   # 百分点的百分之一（2 位小数）
D         = div_round(Σ_s |Δ_s|, 2, scale=0)                         # 行业偏离度 Σ|Δ|/2（注意 scale=0）
```

- `bench_w_i` 为基准成分权重（scale=8）；分母只取「映射到该层级板块的基准成分权重之和」，保证 `Σ w_b = 1`。分母为 0 → 基准不可用（`benchmark_weight=null` + `SECTOR_BENCHMARK_WEIGHTS_MISSING`）。
- `Δ_pp` 示例：`Δ=63_376_764` → `div_round(63_376_764, 10_000)=6_338` → 展示「+63.38pp」。**不要**用 `div_round(Δ, 100)` 或 `scale=2`，会把 pp 放大 100 倍。
- `direction`：`Δ > +0.001` → `over`；`Δ < −0.001` → `under`；`|Δ| ≤ 0.001` → `flat`（阈值 `SECTOR_FLAT_ACTIVE_UNITS=100_000`，scale=8）。
- `Σ_s Δ_s` 理论为 0；断言容差 `≤ ceil((n_p+n_b)/2)` units（权重逐项舍入漂移），**不得**据此判 unreliable。
- `benchmark_as_of` 距快照 > `SECTOR_BENCHMARK_MAX_AGE_DAYS=45` → `status=stale`/`degraded` + `SECTOR_BENCHMARK_WEIGHTS_STALE`，数字照常但提示。

### 6.3 行业集中度

```text
HHI_units     = div_round(Σ_s MV_s(L)², E_map(L)², scale=8)            # 精确整数式，避免二次舍入
effective_n   = div_round(10**16, HHI_units, scale=8)                  # 1/HHI，ratio scale=8
top1          = div_round(max_s MV_s(L), E_map(L), scale=8)            # 等价 cr1_units
top3          = div_round(Σ top3 MV_s(L), E_map(L), scale=8)           # 等价 top_k_share(k=3)
benchmark_hhi = div_round(Σ_s (Σ_{i∈s} bench_w_i)², (Σ bench_w)², 8)   # 基准板块权重 HHI
```

- 复用 [concentration.py](../../backend/packages/core/src/stock_core/stats/concentration.py) 的 `top_k_share/cr1_units`；新增 `sector_hhi_from_values`、`effective_n_units`。
- 单板块：HHI=1、top1=1、top3=1、effective_n=1；空仓：全 `null`；`E_map=0`：全 `null`。
- `top3` 的 `k>板块数` 时等于全部权重（安全）；`k<=0` 抛 `ValueError`。
- `HHI` 与 `behavior.hhi`（个股 + 现金）**不是同一口径**，不得共用阈值/文案。
- `benchmark_hhi` 用基准板块权重（归一到 1），与组合 HHI 同基，可并列对比；基准缺失 → `null`。

### 6.4 概念板块（多归属，不归一）

对快照日 `t`，令 `T_i = concepts_of(i,t)`、`n_tags_i = |T_i|`：

```text
MV_c,t       = Σ_{i: c∈T_i} mv_i,t                                   # 标签暴露（重复计入）
exposure_c   = div_round(MV_c,t, E_t, scale=8)                       # 覆盖归一暴露（可 >1）
coverage     = div_round(Σ_{i: n_tags_i>0} mv_i,t, E_t, scale=8)     # 概念覆盖度
untagged     = 1 − coverage                                          # 未标注占比
overlap      = div_round(Σ_c MV_c,t, E_t, scale=8)                   # MV 加权平均标签数（≥coverage）
multi_tag    = div_round(Σ_{i: n_tags_i≥2} mv_i,t, E_t, scale=8)     # 多标签持仓占比
tag_share_c  = div_round(MV_c,t, Σ_c' MV_c',t, scale=8)              # 标签份额，Σ=1（仍重复计数）
HHI_tag      = div_round(Σ_c MV_c,t², (Σ_c MV_c,t)², scale=8)        # 标签集中度（辅助，独立命名）
```

- `exposure_c` 是**暴露**不是资金权重：`Σ_c exposure_c = overlap ≥ coverage`，通常 >1，不可加总。
- `HHI_tag` 是「标签份额」的 HHI，可用于描述概念叙事集中度，但**不是**资金集中度，**禁止**与行业 HHI 并列排序；前端文案必须带「多归属、重复计数」caveat。
- 概念**不进入** Brinson/五因子（多归属不满足互斥划分，三效应恒等式不成立）；`concentration.status="unavailable"`、`hhi=null`。
- 概念覆盖度低**不自动判 blocked**（未标注 ≠ 数据错误）；`coverage<0.5` 时给 `SECTOR_CONCEPT_COVERAGE_LOW` 提示，仍出暴露数字。
- 主概念（强制单归属）方案默认关闭，除非下游书面要求；启用时必须 `assumptions.concept_assignment="catalog_first"` 且版本化。

### 6.5 状态机与阈值（两轴）

**`sector_data_state`（数据轴）**：

| 取值 | 条件 |
|---|---|
| `missing` | 从未同步或目录为空 |
| `current` | `sector_sync_state.as_of >= 快照日`（或允许 1 个交易日滞后）；行业不超过 `SECTOR_INDUSTRY_STALE_DAYS=35`、概念不超过 `SECTOR_CONCEPT_STALE_DAYS=7` |
| `stale` | 已同步但超过上述陈旧阈值（仍可用，降级展示） |

**`status`（统计轴，优先级 `blocked > missing > insufficient > stale > degraded > ok`）**：

| 取值 | 条件 | 数字 |
|---|---|---|
| `ok` | 资产 current；板块 current；`coverage(L) ≥ 0.95`；基准权重可用；无缺价 | 全部输出 |
| `degraded` | `coverage(L) ∈ [0.90,0.95)`；或基准权重缺失；或缺价 ≤ 阈值；或基准过期；或存在未分类但 ≥ 阈值 | 输出，带 caveat/缺失码；基准缺失时 `active_*` 全 null |
| `stale` | 板块数据落后但可用 | 输出 + `data_as_of` 提示 |
| `insufficient` | `E_t>0` 且 `coverage(L) < 0.90`（含 `E_map(L)=0`）；或有效映射样本过少 | 集中度/偏好 `null`，只给覆盖/未分类 |
| `missing` | 资产表未构建 / `E_t=0`（无股票持仓）/ 从未同步板块 | 全 `null` |
| `blocked` | 资产表 blocked、持仓无法估值、板块目录不可读、**`as_of` 早于首次板块同步日（PIT 未覆盖）** | 全 `null`；PIT 场景带 `SECTOR_HISTORY_NOT_COVERED` |

- 覆盖率阈值进 `stock_core/metrics/policy.py`（全部 int，scale=8）：`SECTOR_COVERAGE_OK_MIN_UNITS=950_000_000`、`SECTOR_COVERAGE_DEGRADED_MIN_UNITS=900_000_000`、`SECTOR_FLAT_ACTIVE_UNITS=100_000`、`SECTOR_BENCHMARK_MAX_AGE_DAYS=45`。
- `status` 只是聚合标签；每个数值字段的可用性以自身 `null` + `missing[]` 为准（与 Dashboard 一致）。
- `blocked/insufficient` **不输出 0 或占位百分比**；`stale/degraded` 可显示数字 + 提示。

### 6.6 `stock_core/sector/` 模块规划

```
stock_core/sector/
├── __init__.py
├── grouping.py       # group_by_sector(rows, level) / rollup_to_parent(...)（树聚合；重复归属抛 ValueError）
├── weights.py        # weights_from_values(values, total) / normalize_weights(...) / active_weights(...)
├── concentration.py # sector_hhi_from_values(values, total) / topk_weights(...) / effective_n_units(hhi)
├── preference.py     # deviation_units(active_weights)（Σ|Δ|/2，scale=0）
└── concepts.py       # concept_exposure(mv_by_symbol, memberships) -> ConceptExposure
```

推荐签名（示意；实现时按 mypy strict 补类型）：

```python
def weights_from_values(values: Sequence[int], total_units: int, *, scale: int = 8) -> list[int]
def active_weights(portfolio: Mapping[str, int], benchmark: Mapping[str, int], *, scale: int = 8) -> dict[str, int]
def sector_hhi_from_values(values: Sequence[int], total_units: int, *, scale: int = 8) -> int | None
def topk_weights(values: Sequence[int], total_units: int, *, k: int = 3, scale: int = 8) -> int | None
def effective_n_units(hhi_units: int, *, scale: int = 8) -> int | None
def deviation_units(active: Mapping[str, int]) -> int | None          # Σ|Δ|/2，scale=0
@dataclass(frozen=True, slots=True)
class ConceptExposure:
    exposure: dict[str, int]; coverage_units: int; overlap_units: int
    multi_tag_units: int; hhi_tag_units: int | None
def concept_exposure(mv_by_symbol: Mapping[str, int], memberships: Mapping[str, Sequence[str]], *, total_units: int, scale: int = 8) -> ConceptExposure
```

- 现有 `hhi_indices` 是个股+现金口径，**不复用**；新函数独立可单测。
- `grouping` 的树聚合必须校验「同一 symbol 在每层最多出现一次」；重复 → `ValueError`，不静默去重。
- 归因函数放 `stock_core/attribution/sector_brinson.py`（归因期次实现，本期只冻结签名）。

### 6.7 边界与手算向量

| 场景 | 输入 | 期望 |
|---|---|---|
| 单板块满仓 | 化工=100 | HHI=1.00000000；effective_n=1.00000000；top1=1.0 |
| 两板块等权 | A=50, B=50 | HHI=0.50000000；effective_n=2.00000000 |
| 三板块 60/30/10 | A=60,B=30,C=10 | HHI=0.46000000；effective_n≈2.17391304；top3=1.0 |
| 现金 50% + 股票 50% 单板块 | 股票 A=50，现金=50，总资产=100 | E=50、E_map=50、coverage=1.0、HHI=1.0、equity_pos=0.5（现金不进 HHI） |
| 未分类 | 化工=80，银行=10，电子=6，未分类=4 | E=100、E_map=96、coverage=0.96；w=0.83333333/0.10416667/0.0625；HHI=0.70920139；top3=1.0 |
| 概念重叠 | A∈{C1,C2}、B∈{C1}、C∉任何、D∈{C2}；市值 A=50,B=30,C=10,D=10 | exposure C1=0.8、C2=0.6（E=100）；coverage=0.9；overlap=1.4；multi_tag=0.5（只有 A 属 ≥2 个概念） |
| 基准权重缺失 | — | `active_*` 全 null；`status=degraded`；`SECTOR_BENCHMARK_WEIGHTS_MISSING` |

**手算 golden（小宇宙，§10.2 使用）**：A=50（化工）、B=30（化工）、C=10（银行）、D=6（电子）、E=4（未分类），E=100、E_map=96。**概念指标分母是 `E_t=100`**（含未分类/无标签股票），与行业 `E_map=96` 不同，二者不得横比。

- 权重：化工 `0.83333333`、银行 `0.10416667`、电子 `0.06250000`；`Σ=1.00000000`。
- HHI：`(80²+10²+6²)/96² = 6536/9216 = 0.70920139`；effective_n=`1.41003672`；top1=`0.83333333`；top3=`1.00000000`。
- 基准权重（hs300 映射到化工/银行/电子 = 0.30/0.40/0.30）：active = `+0.53333333` / `−0.29583333` / `−0.23750000`，和 = 0；偏离度 D=`0.53333333`。
- 概念：C1=(A+B)=80、C2=(A+D)=56；exposure=`0.80000000`/`0.56000000`；**coverage=0.86**（有概念标签的 A+B+D=86；C、E 无标签）；overlap=1.36；**multi_tag=0.50**（只有 A 属 C1、C2 两个概念）；tag_share=`0.58823529`/`0.41176471`；HHI_tag=`0.51557093`。
- Brinson 手算（归因期次）：`A=0.0060、S=0.0080、I=0.0180`，本金 1,000,000 元 → `6,000.00 / 8,000.00 / 18,000.00`；`|residual| ≤ 100.00 元`（1bp），不是 0.01 元。

---

## 7. API 契约

> 契约先行：先登记 [API-Catalog](v2/API-Catalog.md) §1/§6，再 `make api-snapshot` → `make api-check` → 写 router → 测试 → `make sdk`。所有端点 `route_class=StrictQueryRoute`、`operation_id` 必填、错误走 `ApiError(ErrorCode.X)`。

### 7.1 API-087 `GET /sectors/overview`（P0，板块看板统计）

| 项 | 内容 |
|---|---|
| 用途 | 当前快照的板块配置/偏好/集中度；Dashboard「板块结构」子块与 `/sectors` 页共用 |
| 认证 | read；本人组合；admin → 403 `ADMIN_CANNOT_READ_TRADES`；无 token → 401 |
| 同步/幂等 | 同步、只读、无副作用（不建 job、不写表、不联网） |
| operation_id | `getSectorsOverview` |
| 缓存 | `ETag: W/"<as_of>-<sector_checksum>-<asset_fingerprint>"`；`If-None-Match` 命中 → 304 空 body |

**Query**（单次只算一个 taxonomy；前端并行两次请求）：

| 参数 | 类型/取值 | 必填 | 默认 | 语义与校验 |
|---|---|---|---|---|
| `as_of` | `YYYY-MM-DD` | 否 | 最新 `asset_daily.trade_date` | 未来日 → 422 `VALIDATION_ERROR`；早于首个资产快照 → 200 `status=missing` + `SECTOR_AS_OF_NOT_AVAILABLE`；早于首次板块同步日 → `status=blocked` + `SECTOR_HISTORY_NOT_COVERED`（fail-closed，不出数） |
| `taxonomy` | `industry` / `concept` | 否 | `industry` | 非法 → 422 `VALIDATION_ERROR` |
| `level` | `1`/`2`/`3`（industry） | 否 | `1` | `taxonomy=concept` 时传非 0 → 422；概念响应 `level=0` |
| `parent` | 板块代码（行业钻取） | 否 | null | 仅 industry；未知 → 200 + `sectors=[]`（不是 404） |
| `benchmark` | 基准 ID（如 `hs300_tr`） | 否 | 用户设置/`hs300_tr` | 未知 → 422 `UNKNOWN_BENCHMARK` |
| `limit` | 1~500 | 否 | 100 | 0/501 → 422；截断时 `others` 聚合、`truncated=true` |

> **taxonomy 值域映射**：API/前端只用 `industry`/`concept`；DB/领域层只用 `industry_sw`/`concept_em`。映射只发生在 API 边界（一处），其他层禁止混用两种字符串。
> 本期**不做** `period` 区间贡献/区间均值（见 §1.3 非目标）；归因期次用 API-036，不把「区间已实现损益」塞进看板。

**响应 200（industry，示意）**：

```json
{
  "as_of": "2026-09-30",
  "taxonomy": "industry",
  "level": 1,
  "status": "ok",
  "asset_build_state": "current",
  "sector_data_state": "current",
  "definition_version": "1.0",
  "sector_dataset_version": "sw2021@2026-09-30",
  "data_as_of": "2026-09-30",
  "sector_data_as_of": "2026-09-30",
  "benchmark_id": "hs300_tr",
  "benchmark_as_of": "2026-09-30",
  "weight_mode": "additive",
  "portfolio": {
    "total_assets": "110000.00",
    "total_assets_display": "110,000.00",
    "holding_market_value": "104000.00",
    "equity_market_value": "100000.00",
    "mapped_market_value": "96000.00",
    "unclassified_market_value": "4000.00",
    "non_stock_market_value": "4000.00",
    "coverage": "0.96000000",
    "coverage_pct": "96.00%",
    "coverage_basis": "mapped_equity_market_value / equity_market_value",
    "equity_position": "0.90909091",
    "equity_position_pct": "90.91%",
    "holding_count": 5,
    "classified_holding_count": 4
  },
  "sectors": [
    {
      "sector_code": "801030.SI", "sector_name": "基础化工", "level": 1, "parent_code": null,
      "market_value": "80000.00", "market_value_display": "80,000.00",
      "weight": "0.83333333", "weight_pct": "83.33%", "holding_count": 2,
      "benchmark_weight": "0.30000000", "benchmark_weight_pct": "30.00%",
      "active_weight": "0.53333333", "active_weight_pct": "53.33%", "direction": "over"
    },
    {
      "sector_code": "801050.SI", "sector_name": "银行", "level": 1, "parent_code": null,
      "market_value": "10000.00", "market_value_display": "10,000.00",
      "weight": "0.10416667", "weight_pct": "10.42%", "holding_count": 1,
      "benchmark_weight": "0.40000000", "benchmark_weight_pct": "40.00%",
      "active_weight": "-0.29583333", "active_weight_pct": "-29.58%", "direction": "under"
    },
    {
      "sector_code": "801080.SI", "sector_name": "电子", "level": 1, "parent_code": null,
      "market_value": "6000.00", "market_value_display": "6,000.00",
      "weight": "0.06250000", "weight_pct": "6.25%", "holding_count": 1,
      "benchmark_weight": "0.30000000", "benchmark_weight_pct": "30.00%",
      "active_weight": "-0.23750000", "active_weight_pct": "-23.75%", "direction": "under"
    }
  ],
  "others": null,
  "unclassified": {
    "market_value": "4000.00", "ratio_of_equity": "0.04000000",
    "holding_count": 1, "symbols": ["000004"]
  },
  "concentration": {
    "method": "industry_single_membership", "status": "ok",
    "hhi": "0.70920139", "effective_n": "1.41003672",
    "top1_weight": "0.83333333", "top3_weight": "1.00000000",
    "benchmark_hhi": "0.34000000", "n_sectors": 3
  },
  "preference": {
    "deviation": "0.53333333",
    "max_overweight": {"sector_code":"801030.SI","sector_name":"基础化工","active_weight":"0.53333333","active_weight_pct":"53.33%"},
    "max_underweight": {"sector_code":"801050.SI","sector_name":"银行","active_weight":"-0.29583333","active_weight_pct":"-29.58%"},
    "top_overweight": [], "top_underweight": []
  },
  "concept": null,
  "missing": [],
  "assumptions": {
    "industry_standard": "SW2021",
    "weight_basis": "mapped_equity_market_value",
    "benchmark_weight_source": "csindex:hs300",
    "non_stock_excluded": "true",
    "pit": "interval_effective_from_to"
  },
  "inputs_hash": "9f2c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a",
  "links": {"dashboard": "/dashboard", "sectors": "/sectors?taxonomy=industry&level=1", "attribution": "/attribution?period=2026-09"}
}
```

> 上例的 `portfolio/sectors` 数值与 §6.7 手算表同源（按千元缩放），可直接逐字符核对。`unclassified.ratio_of_equity` 的分母是 `equity_market_value`（未分类不是板块权重）；`non_stock_market_value = holding_market_value − equity_market_value`，不允许为负。

**概念响应差异**（同一端点，`taxonomy=concept`）：

- `level=0`、`weight_mode="overlapping"`；`sectors[]` 用 `exposure`/`exposure_pct`（**无** `weight`/`active_weight`/`benchmark_weight`/`direction`）；每行字段：`{sector_code,sector_name,level:0,parent_code:null,exposure,exposure_pct,market_value,market_value_display,holding_count,overlap}`。
- `concentration`: `{method:"concept_overlap", status:"unavailable", hhi:null, …}`。
- 新增 `concept` 块：`coverage`/`coverage_pct`（`coverage_basis="tagged_equity_market_value / equity_market_value"`；与 industry 的 `portfolio.coverage` 分子不同，字段说明必须写清）、`tagged_market_value`、`multi_tag_market_value`/`multi_tag_pct`、`overlap`、`hhi_tag`、`top1_concept`、`top5_concepts[]`、`caveat`。`top1_concept`/`top5_concepts` 按 **`exposure` 降序**（不是 `tag_share`）；`hhi_tag` 基于 `tag_share`（Σ=1）计算，两者口径不同，响应/文案必须分别标注。
- `preference: null`（无基准权重）。
- **概念请求下 `portfolio` 的行业专属字段为 null**：`mapped_market_value`/`unclassified_market_value`/`coverage`/`coverage_pct`/`coverage_basis`（这些是行业映射指标）；`total_assets`/`holding_market_value`/`equity_market_value`/`non_stock_market_value`/`equity_position` 仍有效。前端概念 KPI 用 `concept.coverage`，**不要**复用 `portfolio.coverage`。
- `others`：`{market_value, exposure, exposure_pct, holding_count}` 或 `null`；`top5_concepts` 为 `[{sector_code,sector_name,exposure,exposure_pct}]`（最多 5 个）；`inputs_hash` 为 `sha256:<64hex>`（与指标引擎同格式）。

- `preference.top_overweight` / `top_underweight`：`[{sector_code,sector_name,active_weight,active_weight_pct}]`，各 ≤5 个、按 `|active_weight|` 降序；`max_overweight`/`max_underweight` 取首元素或 `null`。
- `others`：`{market_value, weight, weight_pct, holding_count}` 或 `null`；`sectors[].holding_count` 为该板块内持仓只数（同一股票在一层只计一次）。
- `inputs_hash`：`sha256:<64hex>`（内容 = as_of + taxonomy/level/parent + 板块快照 checksum + 持仓/资产指纹），与指标引擎同格式；不参与展示，仅追溯/ETag。

**对象字段闭合（StrictModel 可直接生成 OpenAPI；`null` = 不可用，不是 0）**：

| 对象 | 字段 |
|---|---|
| `portfolio` | `total_assets`, `total_assets_display`, `holding_market_value`, `equity_market_value`, `mapped_market_value`, `unclassified_market_value`, `non_stock_market_value`, `coverage`, `coverage_pct`, `coverage_basis`, `equity_position`, `equity_position_pct`, `holding_count`, `classified_holding_count`（金额/比率 string，计数 int） |
| `sectors[]`（industry） | `sector_code`, `sector_name`, `level`, `parent_code`, `market_value`, `market_value_display`, `weight`, `weight_pct`, `holding_count`, `benchmark_weight`(nullable), `benchmark_weight_pct`(nullable), `active_weight`(nullable), `active_weight_pct`(nullable), `direction`(nullable) |
| `sectors[]`（concept） | `sector_code`, `sector_name`, `level=0`, `parent_code=null`, `exposure`, `exposure_pct`, `market_value`, `market_value_display`, `holding_count`, `overlap`(bool) |
| `preference` | `deviation`(nullable), `max_overweight`(nullable), `max_underweight`(nullable), `top_overweight[]`, `top_underweight[]`；每项 `{sector_code,sector_name,active_weight,active_weight_pct,direction}` |
| `concentration` | `method`(`industry_single_membership`/`concept_overlap`), `status`, `hhi`(nullable), `effective_n`(nullable), `top1_weight`(nullable), `top3_weight`(nullable), `benchmark_hhi`(nullable), `n_sectors`(int) |
| `concept` | `coverage`, `coverage_pct`, `coverage_basis`, `tagged_market_value`, `multi_tag_market_value`, `multi_tag_pct`, `overlap`, `hhi_tag`, `top1_concept`(nullable), `top5_concepts[]`, `caveat`；每项 top `{sector_code,sector_name,exposure,exposure_pct}` |
| `others` | `null` 或 `{market_value,weight,weight_pct,holding_count}`（industry）/`{market_value,exposure,exposure_pct,holding_count}`（concept） |
| `missing[]` | `{field, code, reason, what_to_do}`（字段路径 + 机读码 + 人话） |
| `inputs_hash` | **64 hex（不带 `sha256:` 前缀，与 API-084 一致）**；内容 = as_of + taxonomy/level/parent + 板块 checksum + 资产/持仓指纹 |

**200 降级码（`SectorMissingCode` Literal，不是 ErrorCode）**：

| code | 触发 | field 示例 |
|---|---|---|
| `SECTOR_ASSET_BUILD_MISSING` | 资产表未构建 | `portfolio` |
| `SECTOR_ASSET_BUILD_BLOCKED` | 资产表 blocked | `portfolio` |
| `SECTOR_ASSET_BUILD_STALE` | 资产表 stale | `data_as_of` |
| `SECTOR_DATA_MISSING` | 从未同步板块 | `sectors` |
| `SECTOR_DATA_STALE` | 板块快照落后 | `sector_data_as_of` |
| `SECTOR_SYNC_FAILED` | 最近一次同步失败（`sector_sync_state.status='failed'`），仍展示上次成功数据 | `sector_data_as_of` |
| `SECTOR_MEMBERSHIP_MISSING` | **全部**股票无板块归属（`E_map(L)=0`），不是部分未分类 | `unclassified` |
| `SECTOR_NON_STOCK_HOLDINGS` | 非股票市值**无法计算**（为 null，而非仅仅存在） | `portfolio.non_stock_market_value` |
| `SECTOR_NO_STOCK_POSITIONS` | 无股票持仓 | `sectors` |
| `SECTOR_BENCHMARK_WEIGHTS_MISSING` | 基准板块权重不可得 | `preference` |
| `SECTOR_BENCHMARK_WEIGHTS_STALE` | 基准权重过期 | `benchmark_as_of` |
| `SECTOR_CONCEPT_MULTI_MEMBERSHIP` | 概念多归属 | `concept.overlap` |
| `SECTOR_CONCEPT_COVERAGE_LOW` | 概念覆盖度 < 0.5（仍出暴露） | `concept.coverage` |
| `SECTOR_CONCEPT_HHI_UNAVAILABLE` | 概念请求集中度 | `concentration.hhi` |
| `SECTOR_HISTORY_NOT_COVERED` | 请求 `as_of` 早于首次板块同步日（fail-closed） | `sector_data_as_of` |
| `SECTOR_ASSET_MISMATCH` | 持仓重建市值与 `asset_daily` 不一致（导致 non_stock 偏差） | `portfolio.non_stock_market_value` |
| `SECTOR_SYNC_FAILED` | 最近一次同步失败，仍展示上次成功数据 | `sector_data_as_of` |
| `SECTOR_TRUNCATED` | `limit` 截断 | `others` |
| `SECTOR_HOLDING_TRUNCATED` | 持仓超上限（沿用 `HOLDINGS_MAX=200`） | `portfolio.holding_count` |
| `SECTOR_PRICE_MISSING` | 部分持仓缺价 | `portfolio.mapped_market_value` |
| `SECTOR_AS_OF_NOT_AVAILABLE` | 请求的 as_of 无资产快照 | `as_of` |

> `missing[]` 只列「影响数字可用性/需要用户处理」的项；**部分未分类、存在非股票持仓属于正常披露**，只体现在 `unclassified`/`portfolio.non_stock_market_value` 数字里，不默认出码（所以 §7.1 示例 `missing=[]` 与 `non_stock=4000` 不矛盾）。只有 `E_map(L)=0`、非股票金额无法计算、基准权重缺失、PIT 未覆盖等才出码。

**HTTP 错误码（全部既有 ErrorCode）**：

| 场景 | HTTP | code |
|---|---|---|
| 无凭证/过期/撤销 | 401 | `TOKEN_EXPIRED` / `TOKEN_REVOKED` / `MULTIPLE_CREDENTIALS` |
| admin 调用 | 403 | `ADMIN_CANNOT_READ_TRADES` |
| `as_of` 非法/未来、`level` 非法、`taxonomy` 非法 | 422 | `VALIDATION_ERROR` |
| 未知 `benchmark` | 422 | `UNKNOWN_BENCHMARK` |
| 未知 query 参数 | 400 | `UNKNOWN_QUERY_PARAM` |

**验收断言（API-087）**：

1. **happy**：fx_sector 夹具 + demo 账号 → 200；`Σ sectors.market_value + unclassified.market_value == E_map + E_unmap == equity_market_value`；`weight = market_value/E_map` 与手算逐字符一致；`hhi/effective_n/top1/top3` 与 §10.2 一致；`status=="ok"`。
2. **越权/身份**：admin → 403；无 token → 401；B 账号只看到 B 的持仓（A 的证券代码/金额不出现）；公共目录不含用户数据。
3. **降级/错误**：未同步 → 200 `sector_data_state="missing"`、`status="missing"`、数字全 `null`；`coverage(L)<0.90` → `insufficient` + `hhi=null`；未来 `as_of` → 422；`as_of<first_sync_on` → 200 `status=blocked` + `SECTOR_HISTORY_NOT_COVERED` 且无数字；`benchmark=nope` → 422 `UNKNOWN_BENCHMARK`；`limit=501` → 422。
4. **只读/ETag**：请求前后 `platform.job/review.asset_daily/market.sector_member` 计数不变；带 `If-None-Match` → 304 空 body；同步板块后 ETag 变化。
5. **数值形态**：JSON 中金额/比率/百分比全为 string；没有 float；`Σ weight` 与 `1` 的差 ≤ `n/2` units；概念 `Σ exposure > 1` 时 `caveat` 非空。

### 7.2 API-088 `GET /sectors`（P0，公共目录）

| 项 | 内容 |
|---|---|
| 用途 | 目录树（行业多级/概念列表），供筛选、面包屑、三级钻取、深链参数校验 |
| 认证 | read；**公共数据、无用户维度**；但与 `/metrics/catalog` 一致，admin 也不可读（`ctx.forbid_admin_trades()` → 403 `ADMIN_CANNOT_READ_TRADES`），无 token → 401 |
| 同步/幂等 | 同步、只读、无副作用 |
| operation_id | `listSectors` |

**Query**：`taxonomy`（`industry`/`concept`，默认 industry）、`level`（1/2/3，industry；concept=0）、`parent`（父代码）、`q`（1~32，名称包含）、`active_only`（默认 true）、`limit`（1~200，默认 50）、`cursor`（损坏/跨过滤 → 400 `INVALID_CURSOR`）。

**响应 200（示意）**：

```json
{
  "as_of": "2026-09-30",
  "taxonomy": "industry",
  "catalog_version": "a1b2c3d4e5f60718",
  "sector_data_state": "current",
  "items": [
    {"sector_code":"801030.SI","sector_name":"基础化工","taxonomy":"industry","level":1,
     "parent_code":null,"path":[{"sector_code":"801030.SI","sector_name":"基础化工"}],
     "member_count":413,"source":"sw","classification_version":"SW2021","is_active":true},
    {"sector_code":"850111.SI","sector_name":"种子","taxonomy":"industry","level":3,
     "parent_code":"850100.SI","path":[
       {"sector_code":"801010.SI","sector_name":"农林牧渔"},
       {"sector_code":"850100.SI","sector_name":"种植业"},
       {"sector_code":"850111.SI","sector_name":"种子"}],
     "member_count":8,"source":"sw","classification_version":"SW2021","is_active":true}
  ],
  "next_cursor": null,
  "has_more": false,
  "missing": []
}
```

- `items` 按 `(level, sector_code)` 升序；`member_count` 为当前有效成分数（L1/L2 为所有后代 L3 成分去重求和，L3/概念为直接成分数；同一股票在 L1/L2 只计一次；12 只无 L3 的股票不计入 L1/L2 的 member_count，但在看板 `unclassified` 中披露）；`is_active=false` 默认不返回。
- `catalog_version = sector_sync_state.checksum[:16]`（16 hex；与 `snapshot_id` 的 32 hex 区分，派生规则写死，避免前端/测试混用）。
- 目录未同步 → 200 + `items=[]` + `catalog_version=null` + `SECTOR_DATA_MISSING`，不是 404/500。
- 错误：`VALIDATION_ERROR(422)`/`INVALID_CURSOR(400)`/`UNKNOWN_QUERY_PARAM(400)`/401/admin 403。

### 7.3 API-084 的 additive 变更（D-24）

- `DashboardLinksOut` 增加 `sectors: str`（如 `/sectors?taxonomy=industry&level=1&as_of=2026-09-30`）；`links` 外其余字段不变。
- 同步更新：
  - `backend/tests/api/test_dashboard.py:466-473` 的 `links` 等值断言；
  - `frontend/apps/web/src/pages/Dashboard.test.tsx:181`（mock links）与 `:403-404`（`link-attribution` 附近）；
  - Dashboard 页头加「板块」入口按钮（与「归因（占位）」并列，但**不是占位**，指向真实页）。
- Dashboard 响应仍只有 `asset_overview`/`history_overview` 两个板块级字段；页面板块级容器仍恰好 2 个（`data-board`）。

### 7.4 API-036 归因的 additive 预留（本期不实现）

- 新增可选 query：`level`（1/2/3，默认 1）、`classification_version`（不传取当前）。
- `model=five_factor`：`factors[]` 从 `1.0` 起固定包含 `code="sector"`（名称「板块配置」）；不提供 `sector` 的响应不得发布（数据未就绪 → 422 `MODEL_NOT_AVAILABLE`，不返回 4 因子）。
- `model=brinson_industry`：`factors[]` 恰 3 行（`allocation/selection/interaction`）+ additive `sectors[]`（每行业一行：`w_p/w_b/R_p/R_b/A/S/I` 的字符串形态）+ `residual`/`residual_ratio`/`base_capital`；`definition_version="1.0"`。
- `assumptions` 增：`base_capital`、`weight_basis="beginning_of_period"`、`classification_version`、`benchmark_weight_source`、`cash_sleeve`、`unclassified_weight`。
- 状态：模型未启用 → 422 `MODEL_NOT_AVAILABLE`；模型可用但该区间数据缺失/成分不可得 → 200 `status="blocked"` + `total_pnl` 缺省（沿用 Catalog §6 API-036 断言⑤）；`status="degraded"` 时给 `unclassified_weight`/`cash_sleeve` 说明。
- 兼容性：仅新增可选字段/枚举值，不升 API 版本；单期 `1.0`、Carino 多期 `2.0`（新增 `linkage` 块）。

### 7.5 契约计数联动（必须同 PR 完成）

| 计数 | 现值 | 新增后 |
|---|---|---|
| API-Catalog §1 端点行数 | 86（表头仍写 82，陈旧） | **88**（087/088；089 不登记） |
| OpenAPI operation 数 | 53（`test_openapi_contract.py:81`） | **55** |
| 2xx JSON 示例数 | 46（`:124`） | **48** |
| `EXPECTED_ALL_ENDPOINTS` | 86（`gate_endpoint_coverage.py:28`） | **88** |
| `EXPECTED_PERIOD2` | 31（`:29`） | **34**（087/088 标「二」；补登 API-085 也是二期，31+3=34） |
| DEV-PLAN A.1 总表 | 标题 84/84，实际 85 行（缺 API-085） | **88/88**（补 API-085 + 087/088；85+3=88）；二期 31→34 |

> 陷阱：`api-check` 的「一期未实现」只看 `phases=="一"`，把 087/088 标「二」不会阻塞 `api-check`；但 `gate-endpoint` 的二期计数等式会红，二者必须一起改。

---

## 8. 归因联动（本期只定口径与数据契约）

### 8.1 数据需求

| 需求 | 来源 | 备注 |
|---|---|---|
| 期初持仓与权重 `w_p,s` | `review` 持仓重建 + `asset_daily` | 期初 = `period_start` 前一有效资产日收盘（R1，D-23） |
| 组合板块收益 `R_p,s` | 持仓日收益 → 板块内加权（现金流调整的 sleeve TWR） | 与资产表日收益口径一致；不直接拿板块指数当组合收益 |
| 基准板块权重 `w_b,s` | `market.benchmark_member` × `market.sector_member` | 按 `period_start` 的 as-of 区间 |
| 基准板块收益 `R_b,s` | `market.sector_bar`（申万行业指数/概念板块指数） | 只用 `return_units`；与基准权重同源最佳 |
| 基准总收益 `R_b` | 现有基准序列 `hs300_tr`（ETF 代理时写 `benchmark_total_return_proxy`） | 与 `Σw_b R_b,s` 交叉校验，差 >1bp 进 residual/降级 |
| 分类版本 | `market.sector.classification_version` | 进 `assumptions`/`definition_version` |

### 8.2 行业 Brinson-Fachler（单期，归因期次实现）

对行业层级 `L` 的板块 `s`（权重均 ratio scale=8）：

```text
R_b   = div_round(Σ_s (w_b,s × R_b,s), 10**8)          # 基准区间收益
R_p   = div_round(Σ_s (w_p,s × R_p,s), 10**8)          # 组合区间收益（Brinson 口径）
A_s   = div_round((w_p,s − w_b,s) × (R_b,s − R_b), 10**8)   # 配置
S_s   = div_round(w_b,s × (R_p,s − R_b,s), 10**8)            # 选择
I_s   = div_round((w_p,s − w_b,s) × (R_p,s − R_b,s), 10**8)  # 交互
恒等式：Σ_s (A_s + S_s + I_s) = R_p − R_b（1bp 容差；逐项舍入误差 ≤ 3·n_s·0.5 units）
```

- **权重口径 R1（D-23）**：`w_p` 取**期初权重**（BoP），`w_b` 取 ≤`period_start` 的基准快照；这样 `A+S+I` 才能严格等于实际区间超额。PRD §7.2 字面「区间平均权重」降级为描述性 `decomp.w_p_avg`（不参与三效应），PRD 与 M2 验收需同步修订。
- **现金/未分类 sleeve**（归因为了 `Σw=1`）：`CASH_EQUIV = 现金 + 货基 + 债/基金市值`，`UNMAPPED = 未分类 A 股市值`；`w_p` 含三个 sleeve，`w_b` 只有行业板块。展开：
  - `CASH`：`R_p,CASH=0`、`R_b,CASH=0` → `A_CASH = (w_p,CASH−0)(0−R_b) = −w_p,CASH·R_b`，`S=I=0`；
  - `UNMAPPED`：`R_b,UNMAPPED=R_b`（无板块基准）→ `A_u=0`、`S_u=0`、`I_u = w_u·(R_p,u−R_b)`；
  - 于是 `Σ_s(A+S+I) + I_u = R_p − R_b`（在 sleeve 内无流出入的 R1 口径下），残余差异进 `residual` 并披露。
  描述性板块统计**不用**该划分（§6 只用 `E_map(L)`），两套口径字段名必须区分（`sector_weight_equity` vs `attribution_weight_total`）。
- **金额化**：`amount = div_round(base_capital_units × ratio, 10**8)`，`base_capital` = 期初 `asset_daily.total_assets`（无则 NAV 期初；都无 → blocked）。**精度边界**：收益空间 1 unit=`1e-8`；本金 100 万时 1bp=100 元——因此 Brinson 金额只承诺 `|residual| ≤ 本金×1bp`，**不能**承诺 0.01 元；0.01 元只属于 M1 五因子金额域。
- **质量阈值**：`residual` 以**收益空间 ratio（scale=8）** 表示；`|residual| > 1bp（100_000 units）` → golden/验收失败；`> 25bp（2_500_000 units）` → `unreliable`（无 value），说明权重漂移/现金流/数据不同源失控。若输出金额域 `residual_amount`，阈值是 `|residual_amount| ≤ 本金 × 1bp`，**不要**再除以本金（量纲会错）。

### 8.3 五因子中的「板块」因子（M1）

**裁决：板块因子 = Brinson 配置效应折算金额**（不是行业 beta 贡献）：

```text
P_sector = div_round(base_capital × A_total, 10**8)     # 金额域，2dp
A_total  = Σ_s A_s
选股因子 = (S + I) × base_capital                       # 行业内选股 + 交互合并（M1 口径）
两者之和 = (R_p − R_b) × base_capital                    # 无未分类/费用差异时成立
```

- 理由：可行动（回答「是否超配了跑赢的行业」）；与 M2 同源（M1 的板块因子就是 M2 三效应的配置项）；避免与 `timing.beta` 的市场暴露重复。
- **费用双计警告**：回合 `pnl` 已含费用；五因子若单列费用，个股贡献必须先剥离费用，否则费用被重复扣减（Period2-08 归因设计必须冻结该拆分规则）。
- 买点/卖点因子由五因子期次实现；板块因子只负责行业配置维度，不吸收选股/择时。
- **ratio 基准（与 API-036 断言③对齐）**：`assumptions.ratio_basis` 按 model 写死——`five_factor` 的 `ratio = amount / total_pnl`，`Σfactors.ratio + residual_ratio = 1`；`brinson_industry` 的 `ratio = effect / R_excess`（收益空间），三效应 ratio 之和 = 1（相对超额），**不**要求等于 `total_pnl` 的份额。验收断言按 model 分别写，不得用同一句「Σratio=1」套两个模型。
- `factors[].code="sector"`、`name="板块配置"`；排序结论由后端生成（前端零口径）。

### 8.4 Carino 多期链接（本期不实现，只冻结口径）

```text
k_t = (ln(1+R_p,t) − ln(1+R_b,t)) / (R_p,t − R_b,t)    # 双序列差分；R_p,t=R_b,t 时取极限 1/(1+R_p,t)
K   = (ln(1+R_p) − ln(1+R_b)) / (R_p − R_b)
A_linked = Σ_t (k_t/K) · A_t；S、I 同理
恒等式：A_linked + S_linked + I_linked = R_p − R_b（累计）
```

- **不要**用 `ln(1+R)/R` 单序列近似；必须双序列差分。实现用 `Decimal(prec=40).ln()`（无 float）。
- 单期 Brinson `definition_version="1.0"`；Carino 是新增语义 → `"2.0"` + 响应 additive `linkage` 块；趋势/历史不得跨版本拼接（沿用 API-037 断点策略）。

### 8.5 历史时点与降级

- 板块成分只有首次同步日之后的变更历史；上游不提供剔除日，**首次同步日之前的任何区间一律 `not_covered`/blocked**（D-04b），不得用当前/最早成分近似。
- 区间跨过已知成分变更且无法还原当时归属（同步间隔太粗）→ 422 `MODEL_NOT_AVAILABLE` 或 200 `status="blocked"`（按 API-036 约定）。
- 基准权重同理：只有首次同步后的区间；更早区间 → `blocked` + `what_to_do`（缩短区间/升级数据源历史权重）。
- 未分类占比 `>5%` → `degraded`；`>20%` → blocker（三效应不出结论），但 `total_pnl`/`residual` 仍可给。**本段阈值仅用于归因口径**；描述性看板仍用 §6.5 的 95%/90% 两条线（两者服务对象不同，不要混用）。

---

## 9. 前端设计（零口径）

### 9.1 信息架构（D-07）

```
Dashboard（保持两个板块级容器）
├─ asset-overview [data-board="asset-overview"]
│  ├─ Hero（现有）
│  ├─ SectorBlock  ← 新增子块，data-board-member="asset-overview"
│  │  ├─ KPI：行业 HHI / 有效板块数 / Top3 占比 / 概念覆盖度
│  │  ├─ 偏好条：超配/低配 Top3（仅行业；基准缺失 → 降级为「组合权重 Top3」+ 提示）
│  │  ├─ 概念警示（多归属）
│  │  └─ 链接：「板块分析」→ /sectors?as_of=…
│  └─ 总资产曲线（现有）
└─ history-overview [data-board="history-overview"]（现有，不改）
```

- 子块只放 4 KPI + 3~5 条偏好 + 1 条概念警示 + 脚注，完整树/概念/层级切换去 `/sectors`。
- **不新增板块级 `data-board`**；子块是 asset-overview 的成员。验收：`document.querySelectorAll("[data-board]").length === 2`；`asset-sector-block[data-board-member="asset-overview"]` 存在。
- 数据：并行两次 `GET /sectors/overview?taxonomy=industry|concept&as_of=…`；任一失败不拖垮 Dashboard（子块独立错误态）。
- 不改 API-084 的两板块结构；只加 `links.sectors`（D-24）。

### 9.2 `/sectors` 页面

```
SectorsPage
├─ PageHeader（as_of、状态徽标、数据截至、刷新）
├─ StatusSummary（status + sector_data_state + missing[]）
├─ Tabs：行业板块 | 概念板块
│  ├─ IndustryView
│  │  ├─ KpiRow：HHI / effective_n / Top1 / Top3 / coverage
│  │  ├─ PreferenceBar（超配/低配 TopN；仅基准可用时）
│  │  ├─ LevelSwitch（L1/L2/L3；改 query `level`）
│  │  ├─ SectorTable（代码/名称/市值/权重/基准权重/主动权重/持仓数；L1/L2 行可展开下一级）
│  │  └─ 口径脚注（分母、基准来源、分类版本、PIT）
│  └─ ConceptView
│     ├─ KpiRow：coverage / overlap / multi_tag / HHI_tag / Top1
│     ├─ CaveatBanner（可重叠、不可加总、不可与行业 HHI 横比）
│     └─ ConceptTable（代码/名称/exposure/市值/持仓数/overlap 标记）
└─ 空态/blocked/stale/insufficient 统一组件
```

- URL 深链：`/sectors?taxonomy=industry&level=1&parent=801010.SI&as_of=2026-09-30`；切换 Tab/层级只改 query，TanStack Query `keepPreviousData`。
- 三级下钻：一次只查一层；L1 行展开用 `parent=<code>&level=2` 懒加载；L2 → L3 同理。
- 无 ECharts：偏好条用 CSS width（直接使用后端 `active_weight_pct`/`weight_pct` 串，不做算术）；集中度用数字 + 文本档位，不做指针仪表盘。
- **几何/展示串裁决（G3/G4）**：v1 **不新增** `weight_bar_ratio`/`active_bar_ratio`/`hhi_display` 等派生几何字段；条形宽度直接复用后端已给的绝对百分比串（`weight_pct`/`active_weight_pct`），集中度直接展示 `hhi`/`effective_n` 原串 + 文案档位。理由是「绝对宽度」比「列表内归一化」更真实且零重算；若后续产品要求列表归一化条形，再以 additive 字段 `*_bar_pct` 增加，不破坏 v1。
- **三级树下钻（G1/G2）**：API-088 已含 `parent` 查询与 `items[].path[]`（一级→自身）与 `member_count`，因此面包屑/形树/逐层懒加载无需再扩契约；`/sectors` 只用这两个字段，不自行拼父级。
- 可访问性：条形有 `aria-valuenow`/文本等价；颜色不是唯一信息载体（`+`/`−` 与文字）。

### 9.3 组件与测试锚点

| 组件 | 文件（建议） | data-testid |
|---|---|---|
| `SectorBlock` | `frontend/apps/web/src/pages/dashboard/SectorBlock.tsx` | `asset-sector-block`、`sector-kpi-preference`、`sector-kpi-concentration`、`sector-kpi-concept`、`link-sectors` |
| `SectorsPage` | `frontend/apps/web/src/pages/Sectors.tsx` | `sectors-page`、`sector-tabs`、`sector-status` |
| `PreferenceBar` | `frontend/apps/web/src/charts/PreferenceBar.tsx` | `preference-bar-{code}` |
| `SectorTable` | `frontend/apps/web/src/pages/sectors/SectorTable.tsx` | `sector-row-{code}` |
| `CaveatBanner` | `frontend/apps/web/src/ui/Caveat.tsx` | `concept-caveat` |
| `StatusBadge` | 复用 `ui/status.ts` + 新映射 | `sector-status-badge` |

测试（Vitest + RTL；仓库无 MSW/Playwright）：

1. Dashboard：mock 两个 overview → `asset-sector-block` 存在且 `data-board-member="asset-overview"`；`[data-board]` 恰 2 个；两板块原有断言不变（FT-01 扩展）。
2. 状态：`missing/blocked` 时子块 DOM 无金额/百分比（正则 `\d+\.\d{2}%` 不命中）；`stale` 显示「数据截至」；`degraded` 有徽标 + 提示；基准缺失时偏好条替换为「组合权重 Top3」。
3. 概念：`concept-caveat` 可见；概念表无「基准权重/主动权重」列；`overlap` 行有徽标。
4. 零口径：源码静态断言（现有零口径 ESLint + 测试）——页面不出现 `Number/parseFloat/toFixed`、不求和、不归一、不算 `1/hhi`；所有数字来自 props。
5. 路由/深链：Tab、层级、`parent`、`as_of` 可回填；`/attribution` 仍占位。

### 9.4 API-Client 变更

- 手写方法（[http.ts](../../frontend/packages/api-client/src/http.ts)）：
  ```ts
  getSectorsOverview(params: SectorsOverviewQuery): Promise<SectorsOverview>
  listSectors(params: ListSectorsQuery): Promise<SectorCatalogPage>
  ```
- `contract.ts` 增加类型别名/编译期断言；`make sdk` 生成 `api/generated/schema.d.ts`。
- Query key：
  - `["sectors","overview", as_of ?? "latest", taxonomy, level, parent ?? "", benchmark ?? "default"]`
  - `["sectors","catalog", taxonomy, level, parent ?? "", q, cursor]`
- Dashboard 子块 `staleTime: 60_000`、`enabled: !!dashboard`；失败只渲染子块错误态（不阻塞首屏）。
- 错误展示复用 `ProblemView`；`missing[]` 复用「缺失与降级项」样式；状态徽标新增 `sector` 文案映射。

---

## 10. 测试与门禁

### 10.1 测试矩阵

| 层 | 文件（建议） | 断言 |
|---|---|---|
| core oracle | `backend/tests/oracle/naive_sector.py` + `tests/unit/core/test_sector_*.py` | 与独立朴素实现逐函数对拍；边界表（§6.7）；AST 无 float |
| core golden | `backend/tests/golden/sector_cases.json`（**不进指标 golden 目录等式**） | §10.2 手算表逐字符；期望值不得由被测代码生成 |
| 仓储 | `backend/tests/unit/test_sector_repository.py` | `as_of` 区间命中；行业唯一索引冲突；基准权重归一化；目录树 path |
| 同步校验 | `backend/tests/unit/test_sector_sync_validation.py` | **上游少返回一个板块 → 现有行不得被 close**；目录行数/父级/成分总和不符 → 整批失败、旧快照不动；同 checksum 幂等 |
| 应用层 | `backend/tests/integration/test_sectors_overview.py` | 复用 `holdings_at`；未分类/缺价/非股票；状态优先级；只读 |
| API | `backend/tests/api/test_sectors.py` | API-087/088 各 ≥3 条：happy/越权（admin 403、B 隔离）/错误（422/400/304） |
| 契约 | `make api-snapshot` + `make api-check` | 端点覆盖、operationId、problem+json、§7.4 元数据、计数 88/55/48 |
| 隔离 | `backend/tests/isolation/test_sector_isolation.py` | B 不见 A 持仓；admin 对 087/088 均 403；公共目录无用户数据 |
| 前端 | `frontend/apps/web/src/pages/Sectors.test.tsx`、`Dashboard.test.tsx` 扩展 | §9.3；零口径门禁 |
| 性能 | `backend/tests/perf/test_sectors_latency.py`（可选） | 200 持仓 + 300 基准成分 p95 ≤300ms；无大夹具时 SKIP 并记录 |

### 10.2 golden 手算表（`fx_sector_core`）

夹具：A=50（化工/涤纶/概念 C1,C2）、B=30（化工/农药/概念 C1）、C=10（银行/国有行/无概念）、D=6（电子/半导体/概念 C2）、E=4（未分类/无概念）。`E=100`、`E_map=96`：

| 期望项 | 值（逐字符） |
|---|---|
| 化工/银行/电子权重 | `0.83333333` / `0.10416667` / `0.06250000` |
| coverage | `0.96000000`；`status=ok`（≥0.95） |
| HHI / effective_n | `0.70920139` / `1.41003672` |
| top1 / top3 | `0.83333333` / `1.00000000` |
| 基准权重 0.30/0.40/0.30 → active | `+0.53333333` / `−0.29583333` / `−0.23750000`；和 = 0 |
| 偏离度 D | `0.53333333` |
| 概念 exposure C1/C2 | `0.80000000` / `0.56000000` |
| 概念 coverage/overlap/multi_tag | `0.86000000` / `1.36000000` / `0.50000000` |
| HHI_tag | `0.51557093` |

**边界用例**：空仓（全 null）、单板块（HHI=1）、`E_map=0`（insufficient）、`k>n` 的 top3、基准缺失（active 全 null）、权重和漂移容差、`div_round` scale 误用回归（pp 不被放大 100 倍）。

**oracle 规则**：`naive_sector.py` 用 `Fraction`/`Decimal` 独立实现，不 import `stock_core.sector`；golden 期望值写在 JSON，由人工手算/独立脚本生成。

### 10.3 门禁命令

```bash
make lint && make typecheck
make test-p2
make test-isolation
make api-snapshot && make api-check
make sdk
make gate-golden-structure
make migrate-check
uv run python -m stock_api.cli sync-sectors --dry-run
```

> `sector_cases.json` 不能放进指标 golden 目录等式（`test_golden_manifest.py` 要求目录集合 == 注册指标 id 集合）；放在单独的 `tests/golden/sector/` 或用 fixture 表驱动，避免把 gate 打红。

---

## 11. 实施顺序与估时

| 阶段 | 内容 | 估时 |
|---|---|---|
| 0 | 契约：API-087/088 登记、schemas、openapi_meta、SDK 类型、Dashboard links additive | 0.5d |
| 1 | 迁移 0011 + `market_models` 6 表 + `SectorRepository` + 索引 | 1.0d |
| 2 | `stock_core/sector/*` 纯函数 + oracle/golden + policy 常量 | 1.5d |
| 3 | `SectorDataSource` 端口 + replay 夹具 + `create_sector_source` + `sync-sectors` CLI（含 akshare adapter，联网环境手测） | 2.0d |
| 4 | 应用层 `sectors.py`（提取 `holdings_at`）+ API-087/088 + 状态/缺失码 + 测试 | 1.5d |
| 5 | 前端：Dashboard 子块 + `/sectors` + api-client + 测试 | 2.0d |
| 6 | 归因口径文档化 + 门禁/契约计数/文档/验收记录 | 1.5d |
| **合计** | 不含 `/attribution` 实现与月报改动 | **~10d** |

依赖：0/1 可并行；3 依赖 1；4 依赖 2/3；5 依赖 0/4；6 收尾。归因模块 Period2-08（五因子含 `sector`）依赖本任务 1~4；Period3-08（Brinson）依赖 8 的口径冻结。

---

## 12. 风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-01 | **东财概念接口**在本机系统代理下长时间 `ProxyError`（有可用窗口：push2 200/push2his 200，非源不可用）；概念行数/列内容/成分未验证 | 概念数据无法同步 | 联网环境复验；单并发 + 页间 0.5~1.5s + 有上限重试；replay 夹具兜底测试；必要时换同花顺源（taxonomy 不变、source 变）；概念不可用只影响概念块，行业块不受影响 |
| R-02 | **申万反爬/页面结构漂移**：同一 URL 同分钟 3 次调用仅 1 成功（HTML 变体 → `AttributeError`）、403/429；父级只有**名称** | 行业树同步不稳定/层级错 | 快照级整批重试 + 结构校验 + 失败保留旧快照；name→code 反查；同层唯一校验；缺父/歧义即整批失败；同步限速（0.4s/次） |
| R-03 | 概念多归属被误读为可加总 | 决策误导 | 概念不输出 `weight/active`；`exposure`/`caveat`；前端概念表无基准列 |
| R-04 | 成分历史缺失（首次同步前；上游无剔除日） | 早期归因不可用 | **fail-closed** `not_covered`/blocked；禁止 `assumed` 近似；归因期次评估历史源 |
| R-05 | 板块同步失败/落后 | 数字过期 | `sector_data_state=stale` + `data_as_of`；同步不推进状态；下次自愈；行业/概念分别 35/7 天阈值 |
| R-06 | **基准序列/成分权重缺失或日期错配**（现状 `/nav` beta 为 null、无 hs300 序列；成分 10-30 vs 权重 09-30，权重和 100.002%） | 板块偏好/归因不可交付 | 首选补基准数据；缺失时 `active_*` 全 null + `SECTOR_BENCHMARK_WEIGHTS_MISSING`；**禁止等权冒充**；权重存 `weight_as_of` 并在读取时归一化；归因期次把基准列为前置依赖 |
| R-07 | 概念成分 4~8 万行/首次全量 10~30 分钟；申万 335 次约 2~4 分钟 | 迁移/查询/同步慢 | 只存当前+变更区间；`(symbol,effective_from)` 索引；只存 A 股；单并发限速；首次全量夜间受控执行 |
| R-08 | 与 18 指标/DEV-PLAN 基线偏离 | 验收争议 | D-09 显式登记；本期不注册 MetricDef；文档/PRD 变更清单 |
| R-09 | 「严格两板块」措辞与实际 10+ `<section>` 不符 | 验收误判 | 修正为「板块级容器 2 个（`data-board`）」；子块用 `data-board-member`；同 PR 改文档/测试 |
| R-10 | Brinson 金额误用 0.01 元断言 | 测试永远红 | 明确金额精度 `≤本金×1bp`；0.01 元只用于 M1 五因子金额域 |
| R-11 | 契约硬编码计数（53/46/86/31） | `api-check`/gate 红 | 同 PR 改 `test_openapi_contract.py`/`gate_endpoint_coverage.py`/Catalog 表头；`make api-check` 全绿 |
| R-12 | `div_round` scale 误用（偏离度/pp） | 数字放大 100/1e8 倍 | 单测回归：`deviation` 用 `scale=0`、pp 用 `/10_000`、effective_n 用 `10**16/hhi` |
| R-13 | **12 只股票无三级归属**（特钢Ⅱ等；5217 vs 5229） | 覆盖率略低/未分类桶 | 显式未分类 + 覆盖披露；不摊假三级；归因按未分类 residual |
| R-14 | **板块指数日线未验证/不可得** | 归因 `R_b,s` 不可算 | v1 看板不依赖；归因期次用个股合成降级（`constituent_synthetic`，assumptions 披露）或接付费源；缺失时 `not_covered` |
| R-15 | 迁移漏 GRANT/版本戳/env import 模型 | 启动失败/权限拒绝/`migrate-check` 红 | 迁移模板含 GRANT + `schema_version`；改 `version.py`；`migrations/env.py` import 新模型 |

---

## 13. 契约/文档变更清单

- [ ] [API-Catalog](v2/API-Catalog.md)：§1 增 API-087/088（计数 86→88）；§2 详约；§4 DTO（`SectorsOverview`/`SectorCatalogPage`）；§6 验收矩阵；表头陈旧数同步；§3 **不新增** ErrorCode（若实现中确需，补表 + CATALOG + `openapi_meta`）。
- [ ] `backend/services/api/src/stock_api/openapi_meta.py`：`OPERATION_META` 增 087/088 的 2xx 示例、错误码、security/summary（否则 `api-check` 的 §7.4 元数据校验红）。
- [ ] `backend/tests/contract/test_openapi_contract.py`：`len(operations) 53→55`、`json_count 46→48`；`scripts/ci/gate_endpoint_coverage.py`：`EXPECTED_ALL_ENDPOINTS 86→88`、`EXPECTED_PERIOD2 31→33`。
- [ ] `backend/tests/isolation/test_isolation_matrix.py`：`ADMIN_FORBIDDEN` 与读矩阵加入 `("get","/sectors/overview")`、`("get","/sectors")`。
- [ ] `contracts/openapi.json` + `frontend/packages/api-client/src/api/generated/schema.d.ts` + `http.ts`/`contract.ts` + `make sdk`。
- [ ] `backend/services/api/src/stock_api/schemas.py`：`SectorOverviewOut`/`SectorOut`/`ConcentrationOut`/`PreferenceOut`/`SectorCatalog*`/`SectorMissingCode`；`routers/sectors.py`；`app.py` 注册；`deps.get_sector_data`。
- [ ] `backend/packages/platform/src/stock_platform/`：`ports.py`（`SectorDataSource`/DTO）、`market_models.py`（6 表）、`config.py`（SECTOR_*）、`sector_source.py`/`sector_live.py`/`sector_replay.py`。
- [ ] `backend/migrations/versions/0011_market_sectors.py`（含 6 表 + GRANT + `platform.schema_version` 戳）；`migrations/env.py` import 新模型；`stock_api/version.py::SCHEMA_VERSION="0011_market_sectors"`；`docs/DATABASE.md` 增 `market.sector*`。
- [ ] `backend/packages/core/src/stock_core/sector/*` + `stock_core/metrics/policy.py`（阈值）；`backend/packages/review/src/stock_review/application/sectors.py`、`repositories/sectors.py`、`application/portfolio.py`（从 dashboard 提取 `holdings_at`，行为不变）。
- [ ] `backend/services/api/src/stock_api/cli.py` + `Makefile`：`sync-sectors`；`write_market_fixtures()` 生成 `fixtures/market/sectors.json`；`seed-demo` 接入。
- [ ] `backend/services/worker/src/stock_worker/`：本期**不新增 job kind**（`platform.job.user_id NOT NULL`，全局同步走 CLI；如未来要 job 需先改 job 模型）。
- [ ] 前端：`pages/Sectors.tsx`、`pages/dashboard/SectorBlock.tsx`、`charts/PreferenceBar.tsx`、`ui/Caveat.tsx`、路由/导航、`Dashboard.tsx`（子块 + `data-board`/`data-board-member` + links）、`Dashboard.test.tsx`、`Sectors.test.tsx`、样式 tokens。
- [ ] `docs/DEV-PLAN-v2.md`：§3.2 新增 **Period2-17「板块数据底座与看板」**（依赖 Period2-07/16）；Period2-08/Period3-08 依赖补 17；§A.1 标题 84/84→**88/88**（补回 API-085 + 087/088，85+3），二期计数 31→33。
- [ ] [Dashboard 设计](2026-10-28-dashboard.md)：§2 合并矩阵加板块行；§5.1 路由加 `/sectors`；§5.2 措辞修正（板块级容器 2 个 + 子块）；§6/§7 验收与缺失清单；§11 变更清单；§12 checklist。
- [ ] [Testing.md](v2/Testing.md) §7：板块测试位置；`docs/reviews/`：实现后验收记录。
- [ ] [PRD-v2](../prd/PRD-v2.md) FR-D1 页面清单加 `/sectors`；FR-R7 注明 JSON 五因子含 `sector`；§7.2 权重口径 R1/精度修订（归因期次）。
- [ ] **ADR-0026**「板块分类与数据源」：申万三级 + 东财概念、公共缓存、PIT 区间、E_map 分母、概念不归一、Brinson R1。本文评审通过后补。

---

## 14. 验收 checklist（本任务完成时）

- [ ] 迁移 0011 可升可降；`make migrate-check` 通过；`market.*` 6 表列与 ORM 一致。
- [ ] `sectors.json` 夹具确定性（同输入 diff=0）；`SECTOR_MODE=replay` 下 API-087/088 全绿。
- [ ] `uv run python -m stock_api.cli sync-sectors --dry-run` 只做配置/表检查、不联网不写库；live 手测（联网环境）目录/成分/基准权重各落表 ≥1 行，重复执行幂等；`make sync-sectors` 定义 `ARGS` 透传（`$(ARGS)`），不要把 `--dry-run` 直接跟在 `make` 后（会被 make 自己吞掉）。
- [ ] API-087/088 契约登记、OpenAPI/SDK 同步、`api-check` 全绿；每个 API ≥3 条测试（happy/越权/错误码）。
- [ ] 行业 HHI/TopK/主动权重与 §10.2 手算逐字符一致；概念 `exposure/overlap/HHI_tag` 与 caveat 正确；空仓/未分类/缺基准/缺价/`E_map=0` 边界全部有断言。
- [ ] 权重分母为 `E_map`：`Σ weight ≈ 1`、`Σ active ≈ 0`、`coverage` 独立；与契约草案的 `holding_market_value` 分母差异已在文档说明。
- [ ] Dashboard 子块懒加载、`[data-board]` 恰 2 个、`asset-sector-block[data-board-member=asset-overview]`；`links.sectors` 与测试更新；`/sectors` 三级下钻/概念警示/状态齐全。
- [ ] 前端零口径门禁通过；无 ECharts/新依赖。
- [ ] 无 token 401、admin 403、B 看不到 A 的板块权重；只读端点无写副作用；ETag/304。
- [ ] 归因数据契约（§8）经归因模块 owner 确认可消费；API-036 预留不破坏现有契约；基准缺失/历史 PIT 的 blocked 语义明确。
- [ ] 文档变更清单 §13 全部完成（含 DEV-PLAN/API-Catalog/ADR/Dashboard 设计/PRD）。

---

## 15. HITL 决策明细（需人工确认）

| # | 问题 | 推荐 | 备选 | 影响 |
|---|---|---|---|---|
| H-01 | 「三级行业板块」具体指哪套分类？ | **申万三级（SW2021）**（免费、真三级树） | 同花顺三级/东财行业（单层） | 数据源/分类版本/全部口径；**实现前必须确认** |
| H-02 | 概念板块数据源 | **东方财富概念板块** | 同花顺概念（反爬风险） | 概念代码体系与成分稳定性 |
| H-03 | 板块看板落位 | **资产概览内子块 + `/sectors` 详情页** | 只做独立页 / 塞进 API-084 第三板块 | Dashboard 验收/PRD 修订；**需拍板** |
| H-04 | 是否把板块集中度注册为第 19 个指标 | **本期不注册**（D-09） | 注册 `perf.sector_hhi`（进 18 指标/目录/门禁） | 指标冻结基线、inputs_hash、验收 |
| H-05 | 基准板块权重缺失时的展示 | **主动权重 null + 缺失说明**，不做等权近似 | 等权基准近似 / 隐藏整块 | 「板块偏好」是否有数；当前基准本身缺失（R-06） |
| H-06 | 首次同步前的历史归因 | **不可用即 blocked**，不做假历史 | 用最早成分近似（assumed） | 早期区间归因能力 |
| H-07 | 是否本期做成分子股成分端点（API-089） | **不做**（目录 + 聚合足够；成分下钻 P1） | 本期加 API-089 | 端点数量/契约计数/前端下钻深度 |
| H-08 | Brinson 权重口径 | **期初权重 BoP（R1）**，修订 PRD §7.2「区间平均」 | R2 平均权重 + 显式 Timing | M2 验收「三效应之和 ≤1bp」是否成立；**需拍板** |
| H-09 | 权重分母 | **已映射权益 `E_map`**（D-22） | 全部持仓市值 / 总资产 | 主动权重可加总性、与基准可比性；**需确认** |

> 未拍板前：H-01/H-02/H-03/H-04/H-07/H-09 按推荐实现；H-05/H-06 若变更仅影响状态机文案；H-08 影响归因期次验收，不阻塞本期数据底座。

---

## 附录 A：`market` 表清单速查

| 表 | 主键 | 用途 | 可再生 |
|---|---|---|---|
| `market.sector` | `(taxonomy, sector_code)` | 目录/层级/分类版本 | 是 |
| `market.sector_member` | `(taxonomy, sector_code, symbol, effective_from)` | 成分时点区间 | 是 |
| `market.benchmark_member` | `(benchmark_id, symbol, effective_from)` | 基准权重时点区间 | 是 |
| `market.sector_bar` | `(taxonomy, sector_code, trade_date)` | 板块指数日线 | 是 |
| `market.sector_pull_range` | `id` | 板块行情覆盖元数据 | 是 |
| `market.sector_sync_state` | `dataset_key` | 新鲜度/校验和 | 是 |

## 附录 B：`missing[]` 码表（200 降级，非错误码）

见 §7.1 表；`SectorMissingCode` 为本设计新增的 Literal（与 `DashboardMissingCode` 同构），**不进** `ErrorCode`/CATALOG，避免 `api-check` §3↔CATALOG 双向校验被撑大。

## 附录 C：与既有实现的对齐点

| 既有实现 | 本设计的复用/差异 |
|---|---|
| `market.daily_bar` / `pull_range` | 表结构、覆盖判定、TTL、checksum 全部照搬；新增 6 表同 schema |
| `MarketDataSource` / `CachedMarketDataSource` | **不扩协议**；新增独立 `SectorDataSource`；读路径只读、CLI 才外呼 |
| `create_market_source` | 新增 `create_sector_source`（platform 内，不依赖 ledger） |
| `behavior.hhi` | 个股+现金口径，**不复用**；新增板块维度函数（E_map 分母） |
| `DashboardOut` / `_holdings_at` | 提取 `holdings_at` 公共函数；API-084 仅 `links.sectors` additive |
| 指标引擎 `DATASET_WHITELIST` | 本期不加 sector 数据集（D-09）；归因期次加 `sector`/`benchmark_members` 并 bump inputs_hash |
| API-083 `rebuild-period` | 不触发板块同步；仅读缓存；状态 stale 透出 |
| 错误模型 | 不新增 ErrorCode；数据缺失走 200 `missing[]`（与 Dashboard 一致） |
| 前端零口径 | 所有数值/百分比/条形宽度均为后端字符串；无算术/无新图表库 |
