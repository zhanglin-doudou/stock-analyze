# FIFO 回合分析增强：操作理由 / 前瞻收益 / 波段与抄底卖飞识别 / 买卖后表现排序

> 版本：**v1.0（评审修订版，2026-11-04 定稿）** ｜ 状态：**已实现并验收（2026-11-05，API-090~094）**
> 需求来源：产品侧 5 项增强 + 2 项补充建议（2026-11-04）：
> ① 人工补充买入/卖出理由；② 买入/卖出后 5/10/20 行情日涨跌幅（单独入口、每日手动更新、不足 20 行情日留空、后续自动化）；③ 统计波段操作的 FIFO；④ 识别加仓抄底/越跌越买后继续下跌、止盈卖飞/越涨越卖后继续大涨；⑤ 可量化的“买得好/卖得好”评估排序；⑥ 其他建议。
> 关联设计：[FIFO 回合与落库](2026-10-26-metrics-three-domain.md)（round/round_fill 口径）、[月度报告](2026-10-28-monthly-report.md)、[五因子归因](2026-11-02-five-factor-attribution.md)、行为评分卡（另稿未入库）、[PRD FR-D4 交易日志](../prd/PRD-v2.md)（Period3-01 未实现）
> **评审记录**：[独立评审与修订处置](../reviews/2026-11-04-DevDesign-Review-fifo-round-insights.md)（3 名评审：量化/架构/产品；均 REQUEST_CHANGES，P0 已修订并入 v1.0）
> **HITL**：H-01~H-10 已由用户确认（2026-11-04）；评审新增的技术决策见 §10（已按推荐值写入，不阻塞实现；仅 API-ID 期次待 PM 定）。
> **编号修订（2026-11-05）**：API-089 预留、暂不实现；本任务端点改为 **API-090~094**，已登记 [API-Catalog §1/§2.6](v2/API-Catalog.md)；设计内容不变。
> 数据基线：本地 `1234@dev.com`，2026 年（截至 2026-11-04）；现有 FIFO 回合 99 笔已平仓、18 指标、资产表、行情缓存可用。
> **本文是设计，不是实现**；所有阈值/公式为 v1.0 定稿，变更须 bump `definition_version`。
>
> **交付状态（2026-11-05）**：P0/P1 已落地并通过门禁。提交链：`5de8380`（契约/迁移/模型）→ `c2610a4`（core/review）→ `8a958d8`（API + OpenAPI 快照）→ `8317b4b` / `a9729e5` / `fdb01dd`（前端与归因容差/默认 lookahead）。补验收链：`3bfb93d` / `3a17111`（F7）→ `da650e9`（S3）→ `1694095`（F8）。
> **实现口径**：API-092 `items[]` 以本文件 §6.3 **当前正文**为准（`buy_trade` / `sell_trade` 内含 `reason` + `forward`），与 v1.0 初稿示意结构的差异见 **附录 C**；`quality` 在列表为 `list[str]`，详情逐笔见 §6.4。

---

## 0. 决策摘要（v1.0 定稿）

> 状态列：**已批准** = 用户此前 HITL 确认；**评审修订** = 本次独立评审发现 P0/P1 后的收紧，已写入 v1.0；**待 PM** = 仅流程/编号问题。

### A. 核心口径决策

| # | 问题 | v1.0 口径 | 依据/代价 | 状态 |
|---|---|---|---|---|
| **D-01** | 人工理由挂在哪一层 | **交易流水级**，但逻辑键用 **`(user_id, trade_dedupe_key)`**（不是 `trade_id`）：理由在账单重导入/`replace_range` 后仍跟随同一 dedupe 行；`trade_id` 仅作当前展示/审计。买入/卖出各一条，回合详情按首买/末卖聚合 | `trade_id` 在重导入时会变（新 ULID），必须用稳定 `dedupe_key`；见 review P0-2 | 评审修订 |
| **D-02** | 理由时点与形式 | 预置原因码 + 自由文本 + 可选情绪；`annotated_at` **由服务端 Clock 写入**，客户端不得回填；`late_annotated = annotated_at > d0+20 交易日`（服务端判定，仅提示不扣分） | 防止伪造“事前理由”；HITL H-03 原意不变 | 评审修订 |
| **D-03** | “行情日”与持有期 | **真实 A 股交易日历**（基准/指数 bar 日期集合）。`round.holding_days` 是 **weekday proxy（含节假日、同日=1）**，不再用于波段分桶；新增 `holding_trading_days`（同日=1 的约定仍保留，但由真实日历计数） | `fifo/calendar.py:12-22` 证实是工作日代理；见 review P0-3 | 评审修订 |
| **D-04** | 前瞻收益口径 | 存 **B0 执行价差、B1 事件后收盘收益、B2 净成交价后收益、B3 基准相对收益**；B0 与归因 D-03（成交价 vs 当日收盘）同向，B1/B2/B3 为新增事件后口径；**排序只用 20 日 B3**；净价 = 含费 `amount/qty`，毛价 = `trade.price`；同一 ranking 不混 `gross/net` 或 `B3/B1` | 解决“B2 被误称执行质量”、与归因 D-03 冲突、费用口径；见 review P0-1/P0-5 | 评审修订 |
| **D-05** | 波段定义 | 已平仓回合 **`2 ≤ holding_trading_days ≤ 20`**（真实交易日；可配）；`≤1` 超短、`>20` 趋势；另给 fill 级波段配对；**fill 与回合不可相加** | 原 `holding_days` 是工作日；阈值语义改为真实交易日；H-01 的“2–20”意图不变 | 评审修订 |
| **D-06** | 加仓/抄底 | 加仓=买入前同标的持仓>0；**摊平/抄底 = 净买入价 < 加仓前含费均价**；越跌越买=同持仓窗口内≥2次加仓且净买入价逐次更低；成本口径 `broker_net_cost_excl_dividends`，**不做股息调成本**（P1） | 股息调成本会改动既有 `behavior.averaging_down` 口径，本期不改 18 指标；见 review P0-6 | 评审修订 |
| **D-07** | 后续继续下跌 | 买后 **20 交易日**：`B2_20 ≤ −3%` **或** `worst_close_20 ≤ P_net_buy×(1−5%)`；`worst_close` 是收盘口径，**不叫 MAE**；5/10 仅展示；阈值可配 | 现有 bar 只有 close，无 intraday high/low；见 review P1-1 | 评审修订 |
| **D-08** | 止盈卖飞/越涨越卖 | **单一主口径**：`max_close_20 > P_net_sell × (1+3%)`（收盘口径，20 交易日内最高收盘）；**删除“或 B2>5%”冗余条件**；另分 `profit_take_fly`（`is_profit_taking` 子集）与 `stop_loss_rebound`（止损后反弹，不属于卖飞）；错失收益=`qty×(max_close_20−P_net_sell)`，标“理论上限” | B2>5% 已被 max_close 条件逻辑包含；止损后反弹不应被污名化为卖飞；见 review P0-8 | 评审修订 |
| **D-09** | 买/卖后表现排序 | **不再用 0–100 percentile 当“能力分”**。按 **20 日 B3 的原始百分点**排序：`buy_excess_20`、`sell_avoid_20=−B3_20`、`combined_20=(buy_excess_20+sell_avoid_20)/2`（百分点）；`n≥30` 才排名，10–29 显示 raw+CI/样本不足，<10 不下结论；百分位仅作“自身历史相对位次”辅助展示；open 回合单列；象限按正负号（双正/买正卖负/买负卖正/双负） | 解决百分位非能力、n=20 无 CI、5/10/20 等权重复、70/40 构造性象限；对齐 FR-R8；见 review P0-7 | 评审修订（H-04 调整） |
| **D-10** | 存储与更新 | 持久化 `trade_reason` + `trade_forward_return`；**逻辑键 dedupe_key**；forward 行存 `trade_version` + `bars_fingerprint`，trade/行情修复即失效重算；读时聚合，不建 `round_insight` 物化表 | 解决重导入/修正/bar 修复后 `scope=missing` 永不重算；见 review arch P0-3 | 评审修订 |
| **D-11** | 每日手动更新 | `POST /rounds/insights/refresh`：**Idempotency-Key 必填**、per-user 单飞、审计；预扫描 ≤5000 笔同步 200，超出 202+Location；`scope=missing`（默认）只补缺失/失效；`scope=all` 仅内部维护；页面按钮触发 | 写操作三件套；202 阈值明确；见 review arch P0-7/P0-8 | 评审修订 |
| **D-12** | 公司行动/复权 | 当前行情**不复权**（ADR-0025）。**共用 `is_corporate_action` 判定**（`category in {dividend,bonus,rights}` 或 `other`+摘要关键词），覆盖本仓“送转在 other”的真实规则；**窗口内任何已知公司行动 → `status=corporate_action`、收益 null、不参与排序**（现金分红同样 fail-closed，除息日不可得）；**未知 action 兜底：窗口内单日涨跌>25% 且无已知 action → `price_jump_review`，不排名**；P1 接行情侧公司行动/后复权再放开 | 账本行的到账日≠除息日、卖出后/未参与配股会漏检；见 review P0-4 + arch P0-5 | 评审修订 |
| **D-13** | 与既有 journal 的关系 | 本期 `trade_reason` 独立表；journal 上线后以 `dedupe_key/trade_id` 合并，不双写；本期不实现 journal CRUD | 避免等 Period3-01；H-09 原意不变 | 已批准 |
| **D-14** | 页面入口/路由 | 新页 **`/rounds/insights`**；API 路由 **必须把 `/rounds/insights` 注册在 `/rounds/{round_id}` 之前**（FastAPI 注册序），并加路由顺序契约测试；前端静态路由放在 `rounds/:roundId` 前；`pageMeta`/图标补齐 | 现有 `rounds.py:136` 先注册动态路由，会吞静态路由；见 review arch P0-1/product P1-10 | 评审修订 |
| **D-15** | 与既有 18 指标/归因的关系 | **不改 18 指标口径**；把摊平/卖飞判定抽成 core 纯函数（`classify_addon/classify_sell`），metric 与洞察共用；B3 复用 `selection.bhar_20` 的 `compute_bhar_events`/`close_on_or_before`，同 fixture 对账；不重复实现两套公式 | 函数级“复用”原本不成立（内嵌在 def）；见 review P0-2/P0-9 | 评审修订 |
| **D-16** | API 编号/期次 | 新端点 5 个，**API-089 预留（暂不实现）**，本任务从 **API-090~094** 编号（理由 PUT/DELETE、insights list/detail、refresh）；**期次归属待 PM**（建议 Period3-05）；计数联动 `EXPECTED_ALL_ENDPOINTS 88→93`、operations 56→61、JSON 示例 49→+4、Catalog/DEV-PLAN 同步 | 不把编号拍死；见 review arch P1-4 | 待 PM |
| **D-17** | 清理/隐私 | 理由软删、`trade_dedupe_key` 逻辑键；软删/撤销交易隐藏理由、清理孤儿 forward；理由文本不入日志/不进 LLM/不进导出默认；审计只记 code/action | 见 review arch P1-9 | 评审修订 |

### B. 非目标（明确不做）

- 不预测未来价格、不生成买卖建议、不做“AI 猜理由”、不做跨用户排行榜/社交比较。
- 不引入基本面/新闻/情绪外部数据（理由仅人工输入；市场温度类指标不在本期）。
- 不改现有 18 指标口径、不重写 FIFO 引擎、不改回合/资产/归因的既有口径。
- 不做税/融资融券/杠杆识别（数据缺口，见附录）。
- 不做逐笔 tick/分钟级 intraday excursion（现有行情只有收盘价，只有收盘口径极值）。

---

## 1. 目标与验收总览

### 1.1 目标（可验收）

1. **理由闭环**：用户可在“单独入口”或回合详情给每笔买入/卖出补充理由（原因码+文本+情绪），可编辑/删除；理由在重建回合后仍保留；支持按理由筛选。
2. **前瞻收益**：对每笔买入/卖出，计算 5/10/20 真实交易日后的 B0（执行价差）/B1（事件后收盘收益）/B2（净成交价后收益）/B3（相对基准收益）；不足对应 horizon 显示 `—`（null），后续手动/自动补齐；能回答“买后是否涨、卖后是否继续涨”。
3. **波段统计**：按回合/FIFO 配对给出波段数量、占比、胜率、收益贡献、与趋势回合对比；阈值可配置。
4. **抄底/卖飞识别**：识别“加仓且低于成本/越跌越买”的买入，统计其后继续下跌比例/金额；识别“止盈/越涨越卖”的卖出，统计卖飞比例/错失收益。
5. **买卖后表现排序**：按 **20 日 B3 原始百分点**给出每回合 `buy_excess_20`/`sell_avoid_20`/`combined_20` 与正负象限；`n≥30` 才排名，10–29 显示原始值+CI/样本不足；百分位仅作自身历史位次辅助；支持 per-stock 聚合（n≥3）；不称“能力分”。
6. **可追溯/可解释**：每个分数/标签都可下钻到原始交易、价格、公式与数据状态；页面不出现“没有依据的数字”。

### 1.2 验收口径总览（详见 §8）

| 交付 | 验收核心 |
|---|---|
| 理由 | CRUD、跨用户 404、**重导入后按 dedupe_key 保留**、审计、长度/枚举校验、页面展示、ETag 304 正确 |
| 前瞻收益 | 5/10/20 边界、真实交易日历、不足留空、不越 `data_as_of`、任何公司行动 fail-closed、幂等刷新、batch/指纹重算 |
| 波段 | 2–20 **真实交易日**计数/收益/胜率与手算一致；可配阈值；fill/回合不可相加 |
| 抄底/卖飞 | 分类 fixture 手算一致；失败率/错失收益公式可复算；止损后反弹单独归类 |
| 排序 | 原始 20D B3 单调、确定性、n≥30 才排名、CI/样本不足显式、并列稳定、无跨用户 |
| 页面 | 独立入口、理由编辑、刷新按钮、加载/空/错误态、移动端、零口径 |
| 门禁 | API 契约、越权矩阵、SDK、测试/覆盖率、性能 |

### 1.3 需求 → 设计 → 页面/API → 验收追踪

| 需求 | 设计 | 页面/API | 验收 |
|---|---|---|---|
| ① 买/卖理由 | §3.2、§4.1、§6.1/6.2 | `/rounds/insights` 待补理由 tab、回合详情、`PUT/DELETE /trades/{id}/reason`、`/trades?has_reason` | AC-R1~R7 |
| ② 5/10/20 行情日 | §3.3、§4.3、§5.1、§6.5 | 页面逐笔四口径；`GET /rounds/insights`；`POST refresh` | AC-F1~F12 |
| ③ 波段 FIFO | §3.4、§5.2 | 波段 tab/汇总；回合详情 | AC-S1~S4 |
| ④ 抄底/卖飞 | §3.5/3.6、§5.2 | 抄底/卖飞 tab、旗标、金额影响 | AC-D1~D7 |
| ⑤ 买卖后表现排序 | §3.7、§5.3 | 回合表现 tab、`sort=buy_excess_20/...` | AC-K1~K6 |
| ⑥ 其他建议 | §11 | 理由→结果矩阵（P1）、快照（P1） | 后续任务，非本期 AC |
| 独立入口/每日手动更新 | §7、§6.5 | `/rounds/insights` + 刷新按钮 + 自动更新状态 | AC-P1/P3、AC-G3 |

---

## 2. 现状与可复用能力

| 能力 | 现状 | 本设计如何用 |
|---|---|---|
| FIFO 回合 | `review.round` / `round_fill`；`GET /rounds`、`/{id}`、`/{id}/fills`；`holding_days` 是 **weekday proxy**（含节假日，同日=1） | 回合骨架；波段改用真实 `holding_trading_days`，不直接用旧字段 |
| 流水 | `ledger.trade`：`side/category/price/amount/commission/dedupe_key…`；`amount` 含费 | 理由挂 dedupe_key；加仓/卖出识别、净价/毛价 |
| 行情 | `market.daily_bar`（close/return，scale 6/8，`adjust=''` 不复权）；`MarketDataSource` 只读缓存 | 前瞻收益；B0~B3；日历 |
| 基准 | `benchmark_series`（默认 `hs300_tr`，**ETF 代理/非完全 β 中性**） | B3 相对收益；缺失则显示不可排名 |
| 既有指标 | `behavior.averaging_down`（摊平占比/深度，无后验）；`timing.fly`（卖后 20 根最高收盘 > 卖价×1.03，**gross 价**）；`timing.chase`（买点分位）；`perf.holding_days`（weekday proxy） | **抽 core 纯函数后复用**；不把旧指标当新窗口；`timing.fly` 只作 gross 诊断 |
| 资产/现金 | `asset_daily` | 仓位/现金上下文（补充建议） |
| 任务 | `platform.job` + worker handlers；`rebuild_rounds` 等 | 未来每日自动刷新 |
| 交易日志 | **未实现**（Period3-01） | 本期先做 `trade_reason`，后续合并 |
| 前端 | `/rounds`、`/rounds/:id`、通用表格/Problem/零口径 | 新页 `/rounds/insights`（静态路由先于动态）；详情页嵌入 |

**关键复用原则**：

- 卖飞阈值与 `timing.fly` **方向一致**（20 根最高收盘 > 卖价×1.03），但本设计用**净卖价**并拆止盈/止损子集；不声称同一分母；
- 加仓/摊平复用抽出的 core 分类器（净买入价 < 加仓前含费均价）；不改既有 `behavior.averaging_down` 口径；
- B3 单事件与 `selection.bhar_20` 共用 `compute_bhar_events`/`close_on_or_before`/同一基准；同 fixture 对账，不重复实现；
- 所有金额/价格/比率标度整数，`div_round` half-up；禁止 float；时间由 `Clock` 注入；行情只读缓存，读路径不联网。

---

## 3. 领域模型与关键概念的可计算逻辑

### 3.1 对象层级（固定）

```text
trade（一笔买入/卖出流水）
  ├─ reason（人工理由，1:0..1，本设计新增）
  ├─ forward_return（每 horizon 5/10/20；B0/B0n/B1/B2/B3，本设计新增）
  └─ fill（FIFO 配对：买入 lot → 卖出 chunk，1:N）
        └─ round（0→非0→0 的持仓周期，N:1；含多个 fill）
```

- **回合**是“完整持仓周期”，**fill**是“某次卖出消耗了哪一笔买入 lot”；**trade**是用户实际操作。
- 理由挂 **trade**（买/卖各一条）；回合级“买入理由/卖出理由”= 该回合**首笔买入/最后一笔卖出**的理由（若多笔，详情页按笔展示，聚合只取代表笔并标注“多笔”）。
- 前瞻收益挂 **trade**；回合级“买入侧/卖出侧”按金额加权聚合。

### 3.2 人工理由（annotation）

**字段**：

| 字段 | 类型/长度 | 说明 |
|---|---|---|
| `trade_id` | str(40) | 当前流水 ID（展示/审计；re-import 会变） |
| `trade_dedupe_key` | str(64) | **逻辑唯一键**；与 `user_id` 一起唯一 |
| `side` | `buy / sell` | 从 trade 派生并校验一致 |
| `reason_code` | enum(32) | 预置码：买入 `plan_build/add_on/bottom_fish/chase/rebound/dca/news/emotion/other`；卖出 `plan_reduce/take_profit/stop_loss/break_even/rebalance/news/emotion/other` |
| `reason_text` | ≤500 字 | 自由补充；**不做自动生成**；敏感内容不写日志/不出站 |
| `emotion_code` | `calm/anxious/greedy/fear/impulsive/other`（可空） | 可选；用于“情绪与结果”复盘 |
| `tags` | JSONB 数组（≤8，每个 ≤16 字） | 自定义标签（如“业绩超预期”） |
| `annotated_at` | timestamptz | **服务端 Clock 写入**；客户端不可传 |
| `late_annotated` | bool | 服务端按 `annotated_at > d0+20 交易日` 计算；仅提示，不扣分 |
| `version` | int | 乐观锁；PATCH 需 `If-Match`（与全仓写约定一致） |
| `deleted_at` | timestamptz? | 软删；理由删除=软删，审计保留 |

**规则**：

- 一个 **dedupe 行**只能有一个有效理由；重复 PUT 幂等更新；更新时 `If-Match` 必填，不一致 → 409 `VERSION_CONFLICT`。
- trade 不存在/不属于当前用户 → **404**（与 AGENTS 越权规则一致）；admin 写 → 403 `ADMIN_CANNOT_READ_TRADES`。
- 理由随 trade 软删隐藏；trade 恢复（restore）后理由仍可见；trade 物理删除（注销级联）理由级联删。
- 回合重建（rebuild_rounds）**不影响**理由（理由不存 `round_id`，只存 dedupe_key）；round 变化不影响理由。
- **理由覆盖率**：`annotated_buy_trades / buy_trades`（按笔数、按金额各一版；**近 90 日**与全历史两版，避免早期空数据把覆盖率拉到 0）；页面显示，不强制 100%。

### 3.3 前瞻收益（forward return）

#### 3.3.1 行情日、日历与持有期

```text
d0 = trade.trade_date
calendar = 基准指数（默认 hs300_tr，ETF 代理）的交易日集合
           （取 benchmark_series / market.daily_bar 中实际存在的 trade_date；升序）
if 基准日历不可用:
    status=no_calendar；D0 的 5/10/20 全部 null；不参与排序（不得用 weekday 冒充交易日）
dN = calendar 中严格晚于 d0 的第 N 个交易日（N=5/10/20）
data_as_of = max(market.daily_bar.trade_date)（不是进程内 market_data.as_of）
if dN > data_as_of: status=pending；收益 null
```

- **5/10/20 各自独立**：只到 5 日时 `r5` 有值，`r10/r20=null`；页面分别显示 `—`，不是“三个都空”。
- **真实交易日**：必须能跨春节/国庆；不允许自然日或 weekday 近似。没有日历就 fail-closed（`no_calendar`），不拿近似值排名。
- `base_shifted`：若 d0 当日无行情，取最近 ≤d0 的交易日；最多容忍 1 个交易日，超过 → `no_bar`，不参与排序。
- `holding_trading_days`：`calendar` 中 `(open_date, close_date]` 的交易日数（同日=1，沿用现有约定但用真实日历）。**`round.holding_days` 是 weekday proxy，仅展示/兼容，不用于波段分桶**。

#### 3.3.2 价格、费用与四种收益（B0~B3）

交易流水 `amount_units` 是**带符号净现金流、已含费用**（`huatai.py:8` 口径）。因此：

```text
P_gross_buy  = trade.price_units                        # 毛成交价（报价）
P_net_buy    = abs(trade.amount_units) / qty_units      # 含费净买入价（成本口径）
P_gross_sell = trade.price_units
P_net_sell   = trade.amount_units / qty_units           # 扣费净卖出价（收入口径）
C0 = close_on_or_before(d0)
CN = close_on_or_before(dN)
```

| 口径 | 公式 | 语义 | 用途 |
|---|---|---|---|
| **B0 执行价差（毛）** | 买入 `(C0−P_gross_buy)/P_gross_buy`；卖出 `(P_gross_sell−C0)/C0` | 与归因 D-03 同向：成交价相对当日收盘的优劣；正=买得低/卖得高 | 执行质量；与归因买/卖点因子对账 |
| **B0n 执行价差（净）** | 买入 `(C0−P_net_buy)/P_net_buy`；卖出 `(P_net_sell−C0)/C0` | 含费后的真实执行差 | 展示；不参与排序主口径 |
| **B1 事件后收盘收益** | `CN/C0 − 1` | 事件日后行情自身涨跌；买后正=涨，卖后正=继续涨 | **排序主口径（B3）的输入**；5/10/20 |
| **B2 净价后收益** | 买入 `CN/P_net_buy − 1`；卖出 `CN/P_net_sell − 1` | 从实际净价到 N 日收盘；卖后正=价格高于卖出净价（卖飞方向） | 机会成本/经济视角；不用于“择时能力” |
| **B3 基准相对收益** | `B1 − benchmark_return_N` | 相对基准的超额；**不是 β/风格中性，且基准是 ETF 代理** | **20 日 B3 作为排序主指标** |

- **B3 复用**：与 `selection.bhar_20` 共用 `compute_bhar_events`/`close_on_or_before`/同一基准序列；B3 是**单事件**，BHAR 是**事件均值**；验收要求同 fixture 逐位对账，禁止两套公式。
- **B2 与 B0/B1 的关系**：买入 `B2=(1+B0n)(1+B1)−1`（净价近似）；卖出用净卖出价；文档/页面不要求用户做换算。
- **收益窗口**：`(d0, dN]` 的收盘价；不包含 d0 收盘到 d0 开盘的盘中段（B0 捕捉执行）。
- **store**：B0 gross/net、B1、B2、B3、`max_close`、`min_close` 全存；API 只给已格式化的 `_pct/_display` + 原始值（ debugging 字段）。
- **不叫 MAE/MFE**：现有 bar 无 high/low，`min_close/max_close` 是**收盘口径极值**；页面写“区间最低收盘/最高收盘”，不写 intraday excursion。

#### 3.3.3 状态与空值

| status | 触发 | 页面 |
|---|---|---|
| `ok` | 日历、行情、基准齐全，无公司行动 | 正常显示 |
| `pending` | `dN > data_as_of` | `—` + “还差 X 个交易日” |
| `no_calendar` | 基准日历不可用 | `—` + “缺交易日历”，不排名 |
| `no_bar` | d0/dN 无行情或 `base_shifted>1` | `—` + “缺行情” |
| `stale_price` | 末日无 bar，最近收盘 ≤5 交易日；>5 → null | `—` + `stale_days` |
| `corporate_action` | 窗口内任何公司行动（含分红/送转/配股） | `—` + “未复权/公司行动，P1 接行情源” |
| `divisor_missing` | 基准序列缺失 | B3 null；B1/B2 可显示，但不参与排序 |

- **0% 不表示缺失**：缺失一律 null + status；排序排除非 `ok` 行。
- **分红也 fail-closed**：除息日不可从账本可靠取得，价格收益会低估；本期不“照算并降置信度”，而是 null；P1 用行情侧除息/后复权再放开。

#### 3.3.4 公司行动与复权（严格）

- 系统行情当前 **不复权**（`daily_bar.adjust=''`，ADR-0025）。
- **共用 `is_corporate_action`**：不能只查 `category in {bonus,rights,dividend}`；本仓送转/送股默认落 `category=other` + 摘要关键词，必须复用 `ledger/repositories/rules.py` 的 `_is_bonus`/`_kind_for` 或抽出公共函数；否则 10 送 10 会被算成 −50% 假跌。
- 窗口 `(d0, dN]` 内检测到任何 action → `status=corporate_action`，B0~B3 全 null，不参与排名；**不用“到账日”当除息日**。
- 成本基座：`avg_cost_net` 为**券商含费成本，未扣股息**；若 d0 前存在分红/送转，`cost_basis_warning=true`，摊平/止盈标签标注“成本未含息调整”；不修改既有 `behavior.averaging_down` 口径（P1 total-return 成本基座再统一）。
- **未知公司行动兜底**：账本可能缺“未参与配股/已清仓”的行；若窗口内出现单日收盘涨跌绝对值 >25% 且无已知 action，标 `price_jump_review=true`，该事件不参与排序（P1 用行情源/后复权彻底解决）。
- P1：接入行情侧公司行动/后复权因子（`adjust` 维度），升 `definition_version` 并重算；旧行保留。

#### 3.3.5 证券范围、账户与 T+1/部分卖出

- **证券范围**：只纳入有 canonical 代码且 `market.daily_bar` 可用的 A 股/ETF；排除货基（`_is_money_fund`）、`CASH`、债券 alias、空代码、`side in unknown/missing`、`data_error=true` 的行；`new_stock` 保留但打 `is_new_stock` 标记（上市不足 20 交易日不排名）。
- **多账户**：默认合并同一用户全部账户（`account_masked` 仅展示/过滤，P1）；合并假设写入 `assumptions`；用户要求分账户时 P1 增加 `account` 过滤。
- **T+1/同日**：A 股股票不能同日卖出；ETF/可转债可能出现同日；`is_same_day=true` 时持有 TD=1、不参与“波段/卖飞”主排名（单列）；不伪造 T+1 限制。
- **部分卖出/清仓**：`is_full_exit = 卖出后持仓=0`；`is_partial_exit` 反之；卖飞/错失收益只乘该笔数量，不乘整仓；同一回合多笔卖出分别统计。
- **IPO/停牌**：上市/停牌不足 20 TD → `pending/no_bar`；不以 0 填充。

### 3.4 波段（swing）

**定义（默认，可配置）**：

- **超短/隔日**：closed 且 `holding_trading_days ≤ 1`（同日按 1）。
- **波段回合**：closed 且 `2 ≤ holding_trading_days ≤ 20`（`swing_min=2`、`swing_max=20`，可配）。
- **趋势回合**：closed 且 `holding_trading_days > 20`。
- **波段配对（fill）**：`round_fill` 的 `holding_trading_days` 落在 2–20；用于长期回合内分批/做 T。
- **不可相加**：fill 级与回合级是两种统计单位，不得把 fill 数加到回合数上；页面明确标注 `bucket_basis`。

**统计**：

| 指标 | 公式/说明 |
|---|---|
| 波段回合数/占比 | `n_swing / n_closed_rounds`（closed 且 close_date 在期间） |
| 波段收益/贡献 | `Σ swing.pnl / Σ closed.pnl`；分母 ≤0 或无 closed → null |
| 胜率 | `n_swing_pnl>0 / n_swing` |
| 平均收益/平均持有 | 按笔与金额加权两版；持有用 `holding_trading_days` |
| 波段配对 | 单独表；不与回合相加 |
| 分标的 | Top 贡献/亏损；样本 n 显式 |

> `round.holding_days` 仅展示为“工作日近似”，不得用于阈值判定；若缺真实日历，该回合 `bucket=unknown`，不计入波段统计。

### 3.5 加仓抄底 / 越跌越买（买入侧）

```text
qty_before, avg_cost_net_before = 买入前持仓数量与含费均价（成本基座=broker_net_cost，未扣股息）
P_net_buy = abs(amount_units)/qty
is_addon  = qty_before > 0
is_initial= qty_before == 0
below_avg = P_net_buy < avg_cost_net_before
dip_depth = (avg_cost_net_before − P_net_buy) / avg_cost_net_before
is_lower_than_last = P_net_buy < last_buy_net_price
dip_sequence = 同持仓在 60 交易日内 ≥2 次 add-on 且净买价逐次更低
```

- **“后续继续下跌”**（默认）：
  - `B2_20 ≤ −3%` **或** `worst_close_20 ≤ P_net_buy × (1−5%)`；
  - `worst_close_20` = `(d0,d20]` 最低收盘；**不是 intraday low**，字段名不得叫 MAE；
  - `decline_after_addon` 只在 `status=ok` 样本上统计；分母 n 显式；
  - 金额影响：
    - `additional_loss_end_20 = qty × (C20 − P_net_buy)`（期末实际，负=浮亏）；
    - `additional_loss_worst_20 = qty × (worst_close_20 − P_net_buy)`（事后最低收盘，**理论上限/不可实现**）。
- **“越跌越买”**：逐次更低净买价 + 同持仓 + 窗口内 ≥2 次；首次建仓不算；部分卖出后重新买入是否算“同一持仓”由 `position_epoch` 判定（仓位归零则新 epoch）。
- **与既有指标关系**：`is_addon/below_avg/dip_depth` 复用抽出后的 core 分类器；`behavior.averaging_down` 只算聚合率/深度，本设计补后验结果；不改旧指标。
- **成本基座警示**：若 d0 前公司行动导致股息未扣，`cost_basis_warning=true`；标签只展示并标注口径，**不计入“失败率/排序”分母**；P1 total-return 成本。

**聚合**：加仓笔数/金额占比、摊平占比/深度、序列数/长度、后续下跌率、平均/中位 B2 5/10/20、金额影响、理由交叉表（`bottom_fish/add_on` vs 识别）。

### 3.6 止盈卖飞 / 越涨越卖（卖出侧）

```text
qty_before, avg_cost_net_before = 卖出前持仓/含费均价
P_net_sell = amount_units/qty
sell_profit = P_net_sell / avg_cost_net_before − 1
is_profit_taking = sell_profit > 0
is_rising_sell = P_net_sell > last_sell_net_price（同持仓上一笔净卖价）
rise_sequence = 同持仓 60 交易日内 ≥2 次卖出且净卖价逐次更高
max_close_20 = max(close in (d0,d20])
sold_too_early = max_close_20 > P_net_sell × (1+3%)      # 单一主口径
profit_take_fly = is_profit_taking AND sold_too_early
stop_loss_rebound = (NOT is_profit_taking) AND sold_too_early
missed_profit_worst = qty × (max_close_20 − P_net_sell)  # 理论上限
missed_profit_end   = qty × (C20 − P_net_sell)           # 期末可实现口径
```

- **删除“或 B2>5%”**：因为 `C20 > 1.05P ⇒ max_close_20 ≥ C20 > 1.05P > 1.03P`，B2>5% 已被主条件包含，OR 是死条件；只保留 `max_close` 阈值（可配 3%）。
- **与 `timing.fly` 的关系**：`timing.fly` 用 **gross 卖价**、20 根自身 bar，无盈利过滤；本设计用 **net 卖价**，并拆 `profit_take_fly`/`stop_loss_rebound`。两者只做方向性对账，**不声称同一分母**。
- **部分卖出/清仓**：`is_full_exit = 卖出后持仓=0`；同一回合多次卖出分别计算，`missed_profit` 只乘该笔卖出数量，不重复乘整仓。
- **止损后反弹**：单独 `stop_loss_rebound`，**不算卖飞**；页面解释“纪律卖出后反弹是正常成本，不等于错误”。
- **越涨越卖** 是行为模式（多次逐高），与 `sold_too_early`（结果）分列；不等于“止盈卖飞”。
- **分母**：`sold_too_early_rate` 可给“全部卖出”和“止盈卖出”两个分母；页面标清楚。

### 3.7 买入后/卖出后表现：排序（非“能力分”）

> 从 v0.1 的 0–100 百分位综合分改为 **20 日 B3 原始百分点排序**；百分位仅作辅助。原因：percentile 是相对名次，均分会落在 50 分位，不是能力；n=20 无 CI；5/10/20 等权会重复计算重叠窗口。

#### 3.7.1 事件级与回合级

- **事件级**（所有 buy/sell，含未平仓回合的已发生买入；只要 horizon 完整）：
  - `buy_excess_20` = 该笔买入的 `B3_20`（百分点）；
  - `sell_avoid_20` = `−B3_20`（卖出后跑输基准为正=卖得好/避开了下跌；跑赢为负=卖飞方向）；
  - 5/10 日仅展示，不参与主排序。
- **回合级**（closed 且 close_date 在期间）：
  - `buy_excess_20` = 该回合所有 buy 的 B3_20 按成交金额加权；
  - `sell_avoid_20` = 该回合所有 sell 的 `−B3_20` 按成交金额加权；
  - `combined_20 = (buy_excess_20 + sell_avoid_20)/2`（百分点；不是 0–100）。
- **未平仓**：不进入回合排名；其已发生买入进入事件级 buy 统计（单列），不伪装成回合。
- **选择偏差披露**：只对已走完 20 日的事件/已平仓回合评分；这是事后评估，不是交易时的实时信号。

#### 3.7.2 样本、CI 与排名

```text
eligible = 所有相关事件 status=ok（无公司行动/无 no_calendar），且 20 日完整
n_events = 事件数；n_rounds = 合格回合数
if n_rounds >= 30:
    排名 = 按 combined_20（或 buy_excess_20 / sell_avoid_20）降序
    CI = 按回合/事件聚类 bootstrap 95%（P1 实现；P0 至少给 n 和“样本不足”）
elif 10 <= n_rounds < 30:
    显示 raw + “样本不足，仅供参考”，不显示排名/名次
else:
    不显示结论，只显示 n 和原始事件
```

- **阈值**：`min_rank_n=30`（对齐 PRD FR-R8 的 n<30 附 CI/谨慎结论）；`min_display_n=10`；`<10` 不下结论。
- **基准**：只使用 `benchmark_id` 一致的样本；B3 缺失（`divisor_missing`）不参与；**不混 B3/B1**。
- **百分位**：仅在 `n≥30` 时可选显示 `relative_position_pct = 自身历史百分位`，文案“自身历史相对位次（不是能力分/收益预测）”；不得作为主排序键。
- **象限**（按正负号，不按 70/40）：
  - 双正：buy_excess_20>0 且 sell_avoid_20>0；
  - 买正卖负：buy>0, sell<0；
  - 买负卖正：buy<0, sell>0；
  - 双负：both<0。
- **per-stock**：`n≥3` 才聚合；显示均值/中位数/n；仅本人。
- **解释**：B3 是相对 ETF 基准的算术超额，**未做 β/行业/风格中性**；基准变更/ETF 跟踪误差会影响排名；页面写“相对基准”不写“择时能力”。

#### 3.7.3 与行为评分卡的区别

| 名称 | 本设计 | 行为评分卡（另一文档） |
|---|---|---|
| 名称 | 回合/事件“买卖后表现排序” | 行为健康度 0–100 过程分 |
| 输入 | 20 日 B3 超额、买卖事件 | 换手/现金/集中度/DE/卖飞等 |
| 用途 | 复盘哪笔买卖后表现好/差 | 发现行为风险 |
| 禁止 | 不当能力分、不跨用户 | 不当收益预测、不跨用户 |

### 3.8 汇总统计（页面顶部）

| 卡片 | 指标 |
|---|---|
| 理由覆盖 | 买入/卖出理由覆盖率（笔数/金额）、最近 30 日待补笔数 |
| 前瞻收益 | 已计算/待更新/缺行情/无日历/公司行动笔数；5/10/20 平均 B0/B1/B2/B3 |
| 波段 | 波段回合数、占比、胜率、收益贡献、平均持有天 |
| 抄底 | 加仓笔数、摊平占比、后续下跌率、期末/最低收盘金额影响 |
| 卖飞 | 止盈/止损反弹、越涨越卖、卖飞率、理论上限/期末可实现错失 |
| 排序 | 合格回合数、双正/双负数量、Top/Bottom 5（仅自己）；n/CI/样本不足显式 |

---

## 4. 数据模型（新增迁移 `0012_review_round_insights`）

> 设计原则：新表一律 `review` schema、`user_id` 非空 + RLS；金额/价格/数量/比率全标度整数；枚举用 `varchar + 应用层校验`（不建 PG Enum）；所有表可由 `user_id` 独立重建/重算；不触碰现有 `round/round_fill` 口径。

### 4.1 `review.trade_reason`（人工理由）

```sql
CREATE TABLE review.trade_reason (
    id                 varchar(40) PRIMARY KEY,        -- 'trs_' + ULID
    user_id            uuid NOT NULL REFERENCES platform.user (id) ON DELETE CASCADE,
    trade_id           varchar(40) NOT NULL,           -- 当前 trade id（展示/审计；会随 re-import 变）
    trade_dedupe_key   varchar(64) NOT NULL,           -- 稳定逻辑键（ledger.trade.dedupe_key）
    side               varchar(8)  NOT NULL,           -- buy|sell
    reason_code        varchar(32) NOT NULL,
    reason_text        varchar(500),
    emotion_code       varchar(16),
    tags               jsonb NOT NULL DEFAULT '[]'::jsonb,
    annotated_at       timestamptz NOT NULL,           -- 服务端 Clock 写入；客户端不可传
    late_annotated     boolean NOT NULL DEFAULT false, -- 服务端按 d0+20 交易日计算
    version            integer NOT NULL DEFAULT 1,
    deleted_at         timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_trade_reason_side CHECK (side IN ('buy','sell')),
    CONSTRAINT ck_trade_reason_text_len CHECK (char_length(reason_text) <= 500),
    CONSTRAINT ck_trade_reason_tags_len CHECK (jsonb_array_length(tags) <= 8)
);
-- 逻辑唯一键：同一 dedupe 行只有一条有效理由
CREATE UNIQUE INDEX uq_trade_reason_user_dedupe
    ON review.trade_reason (user_id, trade_dedupe_key) WHERE deleted_at IS NULL;
CREATE INDEX ix_trade_reason_user_updated ON review.trade_reason (user_id, updated_at DESC);
CREATE INDEX ix_trade_reason_user_trade ON review.trade_reason (user_id, trade_id);
-- RLS 只参照 review 表先例 0003/0006（0011 是 market.* 公共缓存，无 RLS，不能照抄）
-- ENABLE ROW LEVEL SECURITY + tenant_isolation USING (user_id = current_setting('app.user_id')::uuid)
-- GRANT SELECT/INSERT/UPDATE/DELETE TO stock_app（RLS 生效）
```

- **为什么用 dedupe_key**：`replace_range` 会先软删旧 trade、再用新 ULID 插入；`trade_id` 不是稳定业务键（arch review P0-2）。`ledger.trade.dedupe_key` 对同一账单行稳定，理由在重导入后仍跟随；`trade_id` 仅作当前展示/审计。
- **重建兼容**：`rebuild_rounds` 不删/改 trade，理由不随 round ID 变化；`replace_range` 后按 dedupe_key 关联新 trade；若 dedupe_key 本身变化（账单修正改变核心字段），保留旧理由为“孤儿”并提示用户重标，不静默删除。
- **写入**：`PUT` 使用 `If-Match`（更新时必填，新建可空），version++；`VERSION_CONFLICT(409)`；审计只记 `dedupe_key/reason_code/action`，**不记 reason_text 明文**；理由写操作**不 bump trade.version**，而是把 `reason.version` 纳入 `/trades` ETag（见 §6.6），避免 304 命中后理由消失。
- **软删**：删除= `deleted_at=now()`；重新标注复用旧行（清 `deleted_at`、version++）；trade 注销/撤销时理由隐藏，不物理删；用户注销由 `user_id` FK 级联。

### 4.2 理由码（先静态枚举，后续可配置）

| 方向 | code | 中文 | 备注 |
|---|---|---|---|
| 买入 | `plan_build` | 计划建仓 | 有事先计划 |
| 买入 | `add_on` | 主动加仓 | 看多加仓 |
| 买入 | `bottom_fish` | 抄底 | 主观抄底 |
| 买入 | `chase` | 追涨 | 怕错过 |
| 买入 | `rebound` | 抢反弹 | 短线 |
| 买入 | `dca` | 定投/分批 | 纪律型 |
| 买入 | `news` | 消息/大V | 信息驱动 |
| 买入 | `emotion` | 情绪驱动 | 焦虑/冲动 |
| 买入 | `other` | 其他 | 文本补充 |
| 卖出 | `plan_reduce` | 计划减仓 | 预设计划 |
| 卖出 | `take_profit` | 止盈 | 盈利了结 |
| 卖出 | `stop_loss` | 止损 | 亏损离场 |
| 卖出 | `break_even` | 回本/保本 | 回本执念 |
| 卖出 | `rebalance` | 调仓/换股 | 组合调整 |
| 卖出 | `fear` | 恐慌/杀跌 | 情绪驱动 |
| 卖出 | `news` | 消息/大V | 信息驱动 |
| 卖出 | `other` | 其他 | 文本补充 |

- 枚举放 `stock_contracts.enums`（新增 `TradeReasonCode`，文本存储 + 应用层校验），不建 DB Enum；未来自定义理由码 → 配置化（P1），历史 code 不变。
- `emotion_code` 可选，用于“情绪 vs 结果”分析；**不用于扣分/评判**。

### 4.3 `review.trade_forward_return`（前瞻收益）

```sql
CREATE TABLE review.trade_forward_return (
    id                    bigserial PRIMARY KEY,
    user_id               uuid NOT NULL REFERENCES platform.user (id) ON DELETE CASCADE,
    trade_id              varchar(40) NOT NULL,         -- 当前 trade id
    trade_dedupe_key      varchar(64) NOT NULL,         -- 稳定逻辑键
    trade_version         integer NOT NULL,             -- 计算时 trade.version
    barrier_version       integer NOT NULL DEFAULT 1,   -- 公司行动/基准口径版本（可扩展）
    horizon_days          smallint NOT NULL,            -- 5|10|20
    base_date             date NOT NULL,
    end_date              date,
    base_close_units      bigint,
    trade_gross_price_units bigint,
    trade_net_price_units bigint,                       -- 含费净价（买/卖）
    end_close_units       bigint,
    exec_gross_units      bigint,                       -- B0 毛执行差（scale 8）
    exec_net_units        bigint,                       -- B0n 净执行差（scale 8）
    drift_units           bigint,                       -- B1 事件后收盘收益（scale 8）
    trade_return_units    bigint,                       -- B2 净价后收益（scale 8）
    benchmark_id          varchar(24),
    benchmark_drift_units bigint,                       -- 基准 B1（scale 8）
    excess_units          bigint,                       -- B3 = B1 - benchmark（scale 8）
    max_close_units       bigint,                       -- (d0,dN] 最高收盘
    min_close_units       bigint,                       -- (d0,dN] 最低收盘
    status                varchar(24) NOT NULL,         -- ok|pending|no_calendar|no_bar|stale_price|corporate_action|divisor_missing
    stale_days            smallint NOT NULL DEFAULT 0,
    corporate_action_type varchar(16),                  -- dividend|bonus|rights|other_proxy
    calendar_basis        varchar(16) NOT NULL,         -- benchmark
    bars_fingerprint      varchar(64) NOT NULL,         -- 所用 bar 序列 sha256（检测行情修复）
    definition_version    varchar(16) NOT NULL,         -- "1.0"
    data_as_of            date NOT NULL,                -- max(market.daily_bar.trade_date)
    computed_at           timestamptz NOT NULL,
    CONSTRAINT ck_fwd_horizon CHECK (horizon_days IN (5,10,20))
);
CREATE UNIQUE INDEX uq_fwd_user_dedupe_horizon
    ON review.trade_forward_return (user_id, trade_dedupe_key, horizon_days, definition_version);
CREATE INDEX ix_fwd_user_status ON review.trade_forward_return (user_id, status);
CREATE INDEX ix_fwd_user_dedupe ON review.trade_forward_return (user_id, trade_dedupe_key);
-- RLS 同 review 其它表（0003/0006 模式）
```

- **一行 = 一笔 trade × 一个 horizon**；B0/B0n/B1/B2/B3 同存，避免二次取数；API 不再用 `trade_id` 作为唯一键。
- **失效重算**：refresh 发现 `trade_version` 或 `bars_fingerprint` 变化、`data_as_of` 前进、`status=pending` 到期 → 重算；`scope=missing` 不再“只补空行”，而是“补缺失+按指纹失效”。
- **清理**：trade 软删/撤销后，forward 行标 `status=orphan` 或由 cleanup job 删除；`scope=prune` 只做清理，不对外暴露。
- `max/min_close` 只来自收盘价（bar 无 high/low）；页面用“最高/最低收盘”，**不叫 MAE/MFE**。
- **版本读取**：所有读查询强制 `definition_version = current`；旧版本行保留归档，不参与 summary/排名，避免重复计入。
- 不存 `round_id`：前瞻收益只依赖 trade + 行情；回合侧读时 join。
- **批量**：refresh 按 `(user_id, trade_dedupe_key)` `IN` 预读现有行，再 `INSERT ... ON CONFLICT DO UPDATE` 批量写；不做逐行 get/upsert。

### 4.4 是否需要 `round_insight` 物化表

**P0 不建**。原因：

- 单用户规模 ≤5 万笔（NFR-10）；实际 forward 行 = **buy/sell 笔数 × 3 horizon**（5 万笔 → 15 万行），因此 refresh 必须批量写、读时只取当前页/汇总；
- 回合≤几千，读时聚合回合/事件在单 SQL/内存可接受；不做逐行 N+1；
- 物化表要处理理由/行情/口径/交易日历变化的多重失效，复杂度高、易 stale；
- 若性能不达标（P1），再新增 `review.round_insight` 缓存表，`inputs_hash` 失效；本设计预留，不实现。

---

## 5. 计算流程与伪代码

### 5.1 手动/自动刷新（`refresh_forward_returns`）

```text
refresh(user_id, scope="missing"):
  data_as_of = max(market.daily_bar.trade_date)         # 不用进程内 market_data.as_of
  calendar  = benchmark_calendar(hs300_tr)              # 基准 bar 日期集合；缺则 no_calendar
  trades    = active trades (not deleted) with side in (buy,sell),
              category in eligible set, canonical symbols
  existing  = SELECT ... WHERE user_id=? AND definition_version=current
              AND trade_dedupe_key IN (batch)              # 一次批量读
  bars      = cached bars for symbols, [min(d0)-1, data_as_of]  # 按 symbol/日期批量
  for trade in trades:                                   # 只算 buy/sell；排除货基/CASH/债券/代码异常
    for h in [5,10,20]:
      old = existing.get((trade.dedupe_key,h))
      fingerprint = sha256(trade.version, bars sequence, calendar, benchmark)
      if scope="missing" and old and old.status in ("ok")          and old.trade_version==trade.version and old.bars_fingerprint==fingerprint          and old.data_as_of>=data_as_of and old.definition_version==current:
          skipped += 1; continue
      row = compute_forward(trade,h,bars,calendar,benchmark,data_as_of)
      upsert by (user_id, trade_dedupe_key, h, definition_version)   # ON CONFLICT DO UPDATE
  cleanup: forward rows whose dedupe_key no longer active -> status=orphan/delete
  return counts{computed, skipped, pending, no_calendar, no_bar, corporate_action, divisor_missing}
```

- **可重算条件**：`trade_version` 变化、`bars_fingerprint` 变化、`data_as_of` 前进、旧 `status=pending` 到期、口径版本升级；不是“只补空行”。
- **批量**：先批量 `IN` 读 existing，再 `INSERT ... ON CONFLICT DO UPDATE`；不逐行 get/upsert；按 symbol 分组取行情。
- **幂等**：同一 `(user_id,dedupe_key,horizon,definition_version)` 一行；`scope=missing` 只补缺失/失效；`scope=all` 仅内部维护/口径升级，不开放给普通用户。
- **清理**：软删/撤销交易后，理由隐藏；forward 行由 `scope=prune` 或清理 job 删除，避免 summary/覆盖率被孤儿污染。
- **写路径**：`POST /rounds/insights/refresh` 预扫描 trade 数：≤5000 同步 200；>5000 返回 202 + Location + job；需 `Idempotency-Key`；per-user 单飞（scope_key=`round_insights:{user_id}`）；审计。
- **GET 永不写**；页面只读，刷新是显式写操作。

### 5.2 回合洞察读时聚合（`build_round_insights`）

```text
period 默认按 close_date ∈ 期间（closed 回合）；open 回合单列，不混入期间收益
load closed rounds (close_date in period) + open rounds (事件表) + fills + trades + reasons + forward_rows
# 事件级统计按 trade_date ∈ period；回合排名按 close_date ∈ period；两套 period_basis 分开
for round in rounds:
  # 不要只用 open_trade_id ∪ fills：open 回合的后续买入不在 fills 里
  buys  = all buy trades in position epoch（security + open_date..close_date/ as_of，仓位从 0→非0→0）
  sells = all sell trades in same epoch
  # 过滤空 trade_id、货基/CASH/债券、data_error、缺 side 的行
  holding_td = trading_days_between(open_date, close_date)   # 真实日历
  bucket = "short" if <=1 else "swing" if 2..20 else "trend"
  swing_fill = any(fill.holding_td in 2..20)
  addon_flags = classify_addons(buys, positions_before)      # core 纯函数
  sell_flags  = classify_sells(sells, positions_before)      # core 纯函数
  buy_excess_20  = amount_weighted_mean(B3_20 of buys, only status=ok)
  sell_avoid_20  = amount_weighted_mean(−B3_20 of sells, only status=ok)
  combined_20    = (buy_excess_20 + sell_avoid_20)/2         # 百分点
  reason_open/close = first buy / last sell reason（多笔标 multiple）
```

- **期间归属**：closed 回合按 **close_date** 归期；事件级按 **trade_date** 归期；open 回合单列（买入事件可进事件级统计，不进回合排名）；`data_error=true` 排除并标 `excluded`；summary 分母写清；响应带 `period_basis=close_date`/`event_period_basis=trade_date`。
- **买卖映射**：用**持仓 epoch**（同标的、仓位从 0→非0→0）取该回合全部 buy/sell trade；不能只取 `round.open_trade_id + round_fill`（open 回合后续买入不在 fill 中）。
- **读时聚合不写库**；单页只算当前页 + 汇总；汇总与排序共用全量 raw 查询（轻量选择列），不每页重复。
- **样本不足**：`n_rounds<30` 不排名，返回 raw + `sample_status`；`<10` 不下结论。
- **不能混基**：只纳入 `status=ok`、`benchmark_id` 一致的 B3；B1-only/缺基准行单独分组，不参与主排序。

### 5.3 排序伪代码（20D B3，原始百分点）

```text
eligible = rounds where buy_excess_20 and sell_avoid_20 are ok and basis consistent
if n_rounds >= 30:
    combined_20 = (buy_excess_20 + sell_avoid_20)/2
    rank = order by combined_20 desc, close_date desc, round_id  # deterministic
    ci = clustered bootstrap 95% by round/event (P1; P0 必须给 n 和 sample_status)
    relative_position = optional percentile within eligible, labeled 辅助
elif 10 <= n_rounds < 30:
    rank = null; sample_status = "insufficient"; show raw
else:
    no conclusion; show n only
quadrant = sign(buy_excess_20), sign(sell_avoid_20)  # 双正/买正卖负/买负卖正/双负
```

- **不再使用** 0–100 percentile 作为主分数、不再 `70/40` 象限、不 `mean(5/10/20)`；5/10 只展示。
- `per-stock`：`n_rounds≥3` 才聚合；输出均值/中位数/n；不跨用户。
- 金额加权：按 buy/sell 成交金额；同时给笔数等权版本（可选，不能混）。
- 排名随 `data_as_of`/新增回合变化；响应必须带 `ranking_as_of`、`period`、`n_rounds`、`basis`、`ci`，并说明“相对位置不是能力分”。

### 5.4 每日自动化（P1）

- `JobType.REFRESH_ROUND_INSIGHTS = "refresh_round_insights"`（`stock_contracts.enums` 新增）；`HANDLERS` 注册；handler 调用同一 `refresh_forward_returns`；参数 `{user_id, scope:"missing"}`。
- 触发：**独立 CLI/cron**（如 `make refresh-round-insights` 或部署 cron）在 `sync-market` 之后；**不能假设 `sync-market` 是 API 内成功事件**（它是 owner CLI，无 scheduler）；或用户手动按钮。
- 单飞：`scope_key=f"round_insights:{user_id}"`；沿用 job 租约/配额/取消；失败重试；`job.result` counts。
- 页面“自动更新未开启/上次刷新时间”显式提示，避免用户误以为实时。
- 只补缺失/失效；不改理由；清理孤儿；不越 `data_as_of`。

---

## 6. API 设计（新增 5 个端点，编号实现时按 Catalog 顺延）

> 当前契约基线：88 端点 / 56 operations / 49 JSON 示例（2026-11-04 实测）；新增后约 93 / 61 / +4–5，**实现时按 `make api-snapshot`/gate 实测更新**，不在本文硬编码最终计数。

### 6.1 `PUT /api/v1/trades/{trade_id}/reason`（新增；API-090）

- 用途：新增/更新一笔买入/卖出的理由；认证 **write**；路径 `trade_id` 当前值；服务端按 `dedupe_key` 归并。
- **`If-Match`**：新建可省略；更新必填（值为理由 `version`），不匹配 → `409 VERSION_CONFLICT`。
- 请求（**不接收 `annotated_at`**）：

```json
{
  "reason_code": "bottom_fish",
  "reason_text": "跌到年线附近，按计划分批",
  "emotion_code": "anxious",
  "tags": ["年线", "分批"]
}
```

- 响应：`201`/`200`，body `{trade_id, trade_dedupe_key, side, reason_code, reason_text, emotion_code, tags, annotated_at, late_annotated, version, updated_at, etag}`；`late_annotated` 由服务端计算。
- 错误：404（trade 不存在/跨用户/非 buy|sell/已删除）；422（code 非法/长度/标签/方向）；409（版本/If-Match）；403 ADMIN；401。
- 审计：`dedupe_key/reason_code/action`，不记 reason_text；不写日志明文。
- **不 bump trade.version**；`/trades` 的 ETag 改为 `trade.version + reason.version`（见 6.6）。

### 6.2 `DELETE /api/v1/trades/{trade_id}/reason`（新增；API-091）

- 认证 write；软删；`204`；重复删除 `204`（幂等）；404/403/401 同上；删后 `/trades` ETag 变化。

### 6.3 `GET /api/v1/rounds/insights`（新增；API-092）

> **路由顺序**：本静态路由必须在 `GET /rounds/{round_id}` **之前**注册；否则会被动态路由吞掉（现有 `rounds.py:136` 先注册 `{round_id}`）。前端静态路由同理。

- 用途：独立入口列表 + 汇总；认证 read；游标分页；只读（不刷新）。
- Query：

| 参数 | 说明 |
|---|---|
| `period` | `YYYY`/`YYYY-MM`/`YYYY-MM-DD..YYYY-MM-DD`；**默认 close_date 归属**，`period_basis=close_date` |
| `bucket` | `all / scalp / short / position / long`（默认 all；四段口径见 API-Catalog API-092） |
| `flag` | `bottom_buy / stop_loss_sold_early / profit_sold_early / sold_too_early / drawdown / intraday_t / swing_t / intraday_rebalance_out / intraday_rebalance_in / reason_missing / horizon_pending / corporate_action`（可重复/逗号） |
| `has_reason` | bool；过滤“有/无理由” |
| `reason_code` | buy/sell 理由码（可重复）；非法 400 |
| `security_code` | 过滤 |
| `sort` | `combined_20 / -combined_20 / buy_excess_20 / -buy_excess_20 / sell_avoid_20 / -sell_avoid_20 / close_date / -close_date`（白名单；非法 400 INVALID_SORT_FIELD） |
| `include_summary` | bool 默认 true |
| `limit`/`cursor` | 1–200/游标；非法 400 INVALID_CURSOR |

- 响应 `200`：`period` / `period_basis` / `definition_version="1.2"` / `data_as_of` / `ranking_as_of` / `benchmark_id` / `basis="benchmark_excess_20d"` / `summary{...}` / `items[]` / `next_cursor` / `has_more` / `missing[]` / `assumptions` / `disclaimer`。
- `items[]`（每回合，**全部展示字段均为后端格式化串**）：

```json
{
  "round_id": "rnd_...",
  "security_code": "000422", "security_name": "...",
  "status": "closed", "open_date": "2026-03-02", "close_date": "2026-03-18",
  "holding_days_legacy": 12, "holding_trading_days": 10, "bucket": "position",
  "pnl": "1234.56", "return_rate_pct": "4.56%",
  "buy_trade": {
    "trade_id": "trd_...", "date": "2026-03-02",
    "price_display": "10.00", "quantity_display": "100", "amount_display": "1000.00",
    "reason": {"code":"add_on","label":"主动加仓","text":"...","side":"buy",
               "annotated_at":"2026-03-03T09:30:00+08:00","late_annotated":false},
    "reason_label": "主动加仓",
    "forward": {
      "5":  {"horizon":5,"status":"ok","base_shifted":false,"b0_pct":"0.00%","b0_net_pct":"0.00%",
             "b1_pct":"5.00%","b2_pct":"4.95%","b3_pct":"3.00%",
             "max_close_display":"10.60","min_close_display":"9.80","price_jump_review":false},
      "10": {...}, "20": {...}
    }
  },
  "sell_trade": {
    "trade_id": "trd_...", "date": "2026-03-18",
    "price_display": "12.00", "quantity_display": "100", "amount_display": "1200.00",
    "reason": {"code":"take_profit","label":"止盈","text":"...","side":"sell","annotated_at":"..."},
    "reason_label": "止盈",
    "forward": {"5": {...}, "10": {...}, "20": {...}}
  },
  "buy_excess_pct": "2.10%", "sell_avoid_pct": "1.20%", "combined_pct": "1.65%",
  "rank": 3, "quadrant": "双正",
  "flags": ["bottom_buy", "profit_sold_early", "drawdown"],
  "status_counts": {"bottom_buy": 2, "stop_loss_sold_early": 0, "profit_sold_early": 1},
  "quality": ["forward_complete"],
  "missed_profit_worst_display": "0.00", "missed_profit_end_display": "0.00"
}
```

- `flags` 为**内部旗标码**，只含目标清单内的 8 项（`bottom_buy / stop_loss_sold_early / profit_sold_early / drawdown / intraday_t / swing_t / intraday_rebalance_out / intraday_rebalance_in`）；计数型三项另给 `status_counts`（固定三键整数，可为 0），`flags` 只做「是否命中」的布尔投影。查询参数 `flag=` 使用 §6.3 词表，由服务端做映射，前端只做文案映射、不做判断。旧码（`addon / dip_sequence / below_avg / continued_decline / profit_taking / profit_take_fly / stop_loss_rebound / rising_sell_sequence / full_exit / partial_exit / same_day`）与别名 `addon_decline` 自 v1.2 起不再输出；`summary.addon` 仍内部使用加仓谓词。
- `summary`：`swing{...}`、`addon{...}`、`sell_fly{...}`、`reason_coverage{...}`、`ranking{n_rounds,n_events,min_rank_n,basis,quadrant_counts}`、`flag_round_counts[{flag,count}]`（覆盖词表全部 8 个回合旗标，顺序沿用 `ITEM_FLAG_CODES`；`count` 为全量回合中 `flags` 含该旗标的回合数，同一回合只计一次；数据质量三态 `reason_missing`/`horizon_pending`/`corporate_action` 为查询别名，不入此数组；因列表分页、前端只看当前页，计数必须由后端在全量筛选集上聚合）、`pending{forward_recompute,reason_missing}`。
- **回合状态口径 v1.2（2026-11-12，breaking）**：
  - `bottom_buy`（计数）：回合内买入账单命中账单旗标 `instant_bottom` 或 `short_term_bottom` 的账单个数；同一账单同时命中只计 1。
  - `stop_loss_sold_early` / `profit_sold_early`（计数）：回合内卖出账单先须命中账单旗标 `short_term_sold_early`，再用**含费净卖价**与**卖出前持仓含费均价**分侧；净卖价 ≤ 均价 → 止损侧（**等号归止损侧**），严格高于 → 止盈侧；成本缺失或负成本不分侧。
  - `drawdown`（布尔）：回合内按数量加权的平均卖出价 < 区间最高收盘 × 95%（**严格**）。区间为「首笔买入日（含）至末笔卖出日（不含）」，即**排除卖出当日**。
  - `intraday_t`（布尔）：同一交易日内先**部分卖出**（卖出数量严格小于卖出前持仓）再买入同一证券。
  - `swing_t`（布尔）：回合内部分卖出后在更晚交易日再买入同一证券；同一笔卖出当日已被买入配对的（日内做T）不重复计入波段。
  - `intraday_rebalance_out` / `intraday_rebalance_in`（布尔）：回合内先卖出当前证券、同日再买入其它证券 / 先卖出其它证券、同日再买入当前证券。范围是**当前用户同日全部流水，不按账户切分**（退化口径：所有账户合并成一条时间线）；但对手方只认**真实二级市场证券成交**（2026-11-12 收紧）：`CASH` 资金行（转账/费用/利息/红利税补缴）、公司行动（分红/送转/配股）、新股申购与货基申赎（`money_fund_balance_units` 非空，如 940018）都不算，避免把「卖出后补缴红利税」「卖出后申购货基」误判成调仓。
  - `sold_too_early` 别名 = 两类卖飞计数任一 > 0；`reason_missing` / `horizon_pending` / `corporate_action` 口径不变。
  - 账单级旗标在洞察读路径内联重算（不依赖月度构建任务），行情回看比最早账单日多取 45 天；`DEFINITION_VERSION` 由 `1.1` 升 `1.2`，旧版前瞻缓存自然失效并在读路径现算。
- **零口径展示契约**：所有数值字段带 `_pct`/`_display`/`label`；**前端不得自己算/拼百分号**；缺失为 null，不返回 0 分。`disclaimer` 固定文案（见 §7.4）。
- `summary` 每次请求计算一次，**不随 cursor 变化**；P1 加短 TTL 缓存/ETag（以 `data_as_of`+filter hash 为键）；`items` 分页。<br>- 无数据/blocked：`items=[]` + `missing[]`；`sample_status` 显式；**不伪造 0%**。
- 错误：`INVALID_PERIOD(422)`、`UNKNOWN_QUERY_PARAM(400)`、`INVALID_SORT_FIELD(400)`、`INVALID_CURSOR(400)`、401/403。

### 6.4 `GET /api/v1/rounds/{round_id}/insights`（新增；API-093）

- 用途：单回合理由/前瞻/标签/排序详情；认证 read；跨用户/不存在 404；admin 403。
- 响应：回合基础字段 + `holding_trading_days` + `fills[]`（含逐笔 trade_id/dedupe_key/买卖日/持有 TD/金额）+ 每笔 buy/sell 的 `reason` + `forward{5,10,20}`（B0/B1/B2/B3，`_pct`/`_display`/`status`）+ `flags` + `ranking` + `quality[]`。
- 前端详情页用于编辑理由/查看逐笔；**不混用旧 `holding_days` 与真实 TD**。

### 6.5 `POST /api/v1/rounds/insights/refresh`（新增；API-094）

- 用途：手动补算/失效重算；认证 write；**`Idempotency-Key` 必填**；per-user 单飞。
- 请求：`{"period": "2026", "scope": "missing"}`；`scope=missing`（默认）只补缺失/指纹失效；`scope=all` 仅内部维护/口径升级（普通用户 403 或 422）。
- 预扫描 trade 数：≤5000 → 同步 `200`；>5000 → `202` + `Location: /jobs/{id}`（`refresh_round_insights`），同一 job 幂等。
- 响应 `200`：`{computed, skipped, pending, no_calendar, no_bar, corporate_action, divisor_missing, data_as_of}`；`202`：`{job_id,type,status,links}`。
- 幂等：`Idempotency-Key` + upsert；重复调用不重复写；审计一次；不联网，只读 `market.daily_bar`；行情未同步 → 计 `no_bar/pending` + `what_to_do`，不 5xx。

### 6.6 既有端点 additive 变更与缓存

- `GET /trades`、`GET /trades/{id}`：新增 `reason` 对象 + `has_reason`/`reason_code` 过滤（trade 级待补理由清单）；**additive**。
- **ETag**：列表 ETag 从 `sha256(id:version)` 改为 `sha256(id:version:reason_version)`；详情 ETag 纳入 `reason.version`；理由 PUT/DELETE 后 ETag 必变，禁止 304 后理由“消失”。补 200/304 用例。
- `GET /rounds/{id}`：保持纯回合；理由/前瞻走 `/insights`，避免主 API 体积膨胀。
- 契约联动：Catalog §1/§2/§6；`contracts/openapi.json`；`openapi_meta.py`（OPERATION_META/示例/MONEY+RATIO 字段）；contract tests 计数（当前 56 ops/49 JSON 示例；新增 5 ops，DELETE 204 无 JSON → +4 示例）；`EXPECTED_ALL_ENDPOINTS 88→93`；`EXPECTED_PERIOD2` 如新增三期条目不混算；isolation 矩阵 5 个新操作；SDK/手写类型；`Makefile`/CI。
- 前端路由：`rounds/insights` 放 `rounds/:roundId` 前；`pageTitleFor` 补静态页；图标补 `insights`。

### 6.7 权限与隔离

- 全部端点 `require_user`；理由/前瞻只本人；cross-user 404；admin 403；PAT read 不能写理由/刷新；限流沿用全局。
- 理由文本敏感：**不入日志、不出站、不进 LLM、不进默认导出**；页面仅本人可见。
- 审计：`PUT/DELETE/refresh` 写 `platform.audit_log`（reason_text 脱敏不落）；失败不写；`refresh` 审计含 counts。
- 新表 RLS：`tenant_isolation`；`trade_reason`/`trade_forward_return` 都收 `user_id`；admin 读理由仍 403（身份越权）。

---

## 7. 前端设计（独立入口）

### 7.1 `/rounds/insights` 页面

```text
PageHeader：期间（年/月/自定义，默认年）+ 数据截至 + “手动更新”按钮 + 状态（ok/degraded/blocked）+ 自动更新状态
① 汇总卡：理由覆盖 / 前瞻完成度 / 波段（回合+配对）/ 抄底后验 / 卖飞/止损反弹 / 合格回合 n
② Tabs：回合表现 | 波段 | 抄底 | 卖飞 | 待补理由 / 理由覆盖
③ 表格（每 tab 共用列，全部后端格式化）：
   回合（close_date/标的/持有TD/类型） | 买入后相对表现 20D | 卖出后相对表现 20D |
   买入理由 | 卖出理由 | 买后 5/10/20（执行/事件后/净价/相对） | 卖后 5/10/20 | 旗标 | 详情
④ 行点击 → `/rounds/:id/insights`：
   回合基础 + 逐笔 fill/买卖 trade 时间线；每笔可编辑理由；前瞻表；标签/排序；数据质量
⑤ 筛选：期间、标的、bucket、flag、理由码、has_reason、horizon pending；排序后端白名单
```

- **术语映射（页面必须用中文，不露 B0/B1/B2/B3 裸码）**：
  - B0 → “执行价差（相对当日收盘，毛价）”（笔次固有属性，只依赖成交价 + 成交当日收盘，**不随持有期变化**）
  - B1 → “事件后走势（收盘到收盘）”
  - B2 → “成交后收益（按含费净价）”
  - B3 → “相对基准超额（20 日排序口径）”
- **零口径**：所有数值只渲染 `*_pct`/`*_display`/`label`；不做 `Number/parseFloat/toFixed`；排序由后端；表格不重算；条形宽度用 `bar_pct`。
- **手动更新**：按钮 → `POST /rounds/insights/refresh`（带 `Idempotency-Key`）；显示 `computed/skipped/pending`；202 时显示“后台计算中”并轮询 job；失败 `ProblemView`；防重复。
- **自动更新提示**：本期默认手动；若 worker 未开启，页头显示“自动更新未开启/上次刷新：…”，不能宣称实时。
- **空值/状态**：`pending/no_calendar/no_bar/stale_price/corporate_action/divisor_missing` 显示 `—` + tooltip；**不显示 0%**；公司行动不显示价格收益，避免“假跌”。
- **待补理由**：独立 tab 列出 trade 级买入/卖出（可用 `/trades?has_reason=false`），支持逐笔/批量跳转编辑；显示覆盖率（笔数/金额）与近 90 日待补数。
- **空态**：无回合/无理由/无前瞻/样本不足分别提示下一步；无数据不显示排名。
- **移动端**：表格卡片化或横向滚动；理由编辑全屏 modal；触控 ≥44px。
- **可访问性**：表单 label/`aria-live`；旗标文字+图标；表格 `scope/caption`；颜色对比度。
- **合规文案**：页脚固定 `disclaimer`：**“以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益；相对基准为 hs300_tr ETF 代理；缺失/公司行动显示 —，不显示 0%。”** 禁止“最佳买卖点/你适合/应加仓/跑赢 XX% 用户”等词；分数旁写“相对基准的事后表现，不是能力/收益预测”。

### 7.2 回合详情页（`/rounds/:id`）增强

- 现有回合详情 + FIFO 配对表；新增：
  - “操作理由”：买入/卖出 trade 列表（含 `trade_dedupe_key` 当前映射），编辑/删除；`late_annotated` 提示“事后标注”。
  - “执行价差（相对当日收盘）”：B0 只依赖成交价与成交当日收盘，与 horizon 无关，按买/卖笔次各展示一次（有对应成交行才展示，`—` + 状态），**不放进 5/10/20 列**（2026-11-05 修订：避免同一数值在三个维度重复、被误读成持有期指标）。
  - “5/10/20 行情日”：逐笔事件后走势/净价后收益/相对基准（B1/B2/B3，随 horizon 变化）；`—` + 状态。
  - “标签/排序”：波段/抄底/卖飞/止损反弹；`buy_excess_20`/`sell_avoid_20`/`combined_20` 原始百分点 + n/CI/quadrant；不显示 0–100 能力分。
- 理由编辑复用 `TradeReasonForm`；保存后 `invalidateQueries`，不整页刷新；版本冲突提示重新加载。

### 7.3 导航/入口与路由

- `layout/navigation.ts` 的 `trading` 组新增“回合洞察”`/rounds/insights`；图标新增 `insights`；`pageTitleFor` 对 `/rounds/insights` 返回“回合洞察”，不能落成“回合详情”。
- `router.tsx`：`rounds/insights` **必须放在** `rounds/:roundId` 之前；加路由匹配测试。
- `/rounds` 列表页增加“回合洞察”入口；不新增占位页。
- API 路由同理：`/rounds/insights` 注册在 `/{round_id}` 之前，契约测试断言实际 200 不是 404。

## 8. 验收条件（AC）

> 原则：每个数字可复算、每个空值有原因、每个写操作可隔离/可审计；不满足则 FAIL，不用“看起来对”通过。

### 8.1 理由（AC-R）

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-R1 | 新建/更新/删除理由 | `PUT` 201/200、`DELETE` 204；同 dedupe 幂等；版本冲突 409；测试 `tests/api/test_trade_reason.py` |
| AC-R2 | 归属/越权 | 跨用户/不存在 404；admin 403；未认证 401；PAT read 写 403；isolation 矩阵 |
| AC-R3 | 校验 | 非法 code/长度/标签/方向 → 422；reason_text 不落日志（caplog 断言） |
| AC-R4 | 重建兼容 | `rebuild_rounds` 前后理由仍在；`replace_range` 重导入后用 **dedupe_key** 关联到新 trade，理由不丢；单测 `test_reimport_reason_survives` |
| AC-R5 | 展示/筛选/ETag | `/trades?has_reason=false&side=buy` 返回待补清单；`reason_code` 过滤；理由变更后 `/trades` ETag 变化，200 而非 304；补 304/200 用例 |
| AC-R6 | 审计/隐私 | PUT/DELETE 有 audit 且无 reason_text 明文；reason_text 不出现在日志/导出的默认内容 |
| AC-R7 | 时点 | `annotated_at` 为服务端 Clock；请求带 `annotated_at` → 422/忽略并审计；`late_annotated` 由服务端按真实交易日计算 |

### 8.2 前瞻收益（AC-F）

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-F1 | 5/10/20 边界 | 真实交易日第 5/10/20 天；不足对应 horizon null/pending；不得用第 4/9/19 天；fixture `fx_fwd_calendar` |
| AC-F2 | 交易日历/持有 TD | 跨春节/国庆正确；`holding_trading_days` 与日历手算一致；旧 `holding_days` 仅 legacy；测试 `test_trading_calendar` |
| AC-F3 | B0~B3 与净价 | B0/B0n/B1/B2/B3 与手算一致（ratio 1e-8）；买入/卖出净价用 `amount/qty`；毛/净字段不混；fixture |
| AC-F4 | 不越 `data_as_of` | `data_as_of=max(market.daily_bar.trade_date)`；末日 > data_as_of → pending；不读未来 bar；补 as_of 回退测试 |
| AC-F5 | 公司行动 fail-closed | 窗口内任何 `dividend/bonus/rights/other+关键词` → `status=corporate_action`、收益 null、不排名；fixture 覆盖 `category=other, summary="10送10"`；无已知 action 的单日 >25% → `price_jump_review` 不排名 |
| AC-F6 | 停牌/无日历/退市 | 停牌按最近收盘+stale≤5；>5 → null；缺基准日历 → `no_calendar` 且不排名；新股/退市 null |
| AC-F7 | B3 与 BHAR 对账 | 同一事件集/基准下，B3 与 `selection.bhar_20` 共用函数；均值对账（≤1e-8）；不得两套公式。**结论：通过（2026-11-05，`3bfb93d`/`3a17111`）**——唯一实现为 `portfolio_index.benchmark_adjusted_excess_units`，`test_b3_equals_bhar20_per_event_and_mean` 逐笔差 0、均值差 0 |
| AC-F8 | 刷新幂等/失效 | 同 `Idempotency-Key` 两次调用行数/值不变；trade_version/bars_fingerprint 变化触发重算；`scope=missing` 不重算已 ok；GET 不写。**结论：通过（2026-11-05，`1694095`）**——指纹纳入区间内 `daily_bar` 的 `(max(trade_date), count, 内容摘要)` 等价版本量；`test_insights_fingerprint.py` 证明修订/新增使指纹变化，`test_api_094_scope_missing_skips_unchanged_fingerprint` 证明 bar 不变时 `scope=missing` 全跳过 |
| AC-F9 | 批量/性能 | 5k/20k 两档 fixture：批量 IN 读 + ON CONFLICT 写；5k 单用户 p95 目标 ≤3s（实测校准，不达标则 202）；页面读取 p95 ≤1s |
| AC-F10 | 缺失不伪造 | 无 bar/新股/退市/公司行动 → null+status；输出 0 或“0%”即 FAIL |
| AC-F11 | 证券范围/账户/T+1 | 货基/CASH/债券/空代码/data_error 排除；多账户合并假设披露；`is_same_day`/`is_full_exit`/`is_new_stock` 正确；fixture |
| AC-F12 | 买卖映射/未知 action | open 回合后续买入按 epoch 计入；不依赖 fills；单日涨跌>25% 无 action → `price_jump_review` 不排名 |

### 8.3 波段（AC-S）

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-S1 | 真实交易日定义 | 默认 `2≤holding_trading_days≤20` 为波段；`≤1` 超短、`>20` 趋势；用真实日历，不用 weekday；fixture `fx_swing_td` |
| AC-S2 | 统计 | 数量/占比/胜率/收益/平均 TD 与手算一致；closed 按 close_date 归期；分母≤0/null 显式；`data_error` 排除 |
| AC-S3 | fill/回合不混 | fill 级波段配对单独统计；测试断言两者不相加；`bucket_basis` 透出。**结论：通过（2026-11-05，`da650e9`）**——页面级 `bucket_basis=round`、`summary.fill_swing`（`bucket_basis=fill`）、fill 明细 `holding_trading_days/bucket`；`test_api_092_ac_s3_fill_pairs_not_added_to_rounds` 断言 1 回合 vs 2 fill 不相加 |
| AC-S4 | 对比/空样本 | 波段 vs 趋势并列；空样本 null 不 0；open 回合单列 |

### 8.4 抄底/卖飞（AC-D）

> **v1.2（2026-11-12）修订**：AC-D2 / AC-D4 的「越跌越买 / 越涨越卖」与 AC-D5 的 20D 3% 卖飞、AC-D7 的部分/清仓/T+1 旗标**不再作为回合状态输出**；加仓/加仓后下跌仅保留为 `summary.addon` 内部口径。回合状态以 AC-D8 ~ AC-D13 为准。

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-D1 | 加仓/摊平 | 买入前 qty>0；`summary.addon` 内部口径；测试 `test_addon` |
| AC-D2 | 越跌越买（已废弃） | v1.2 起不再输出旗标 |
| AC-D3 | 后续下跌 | `B2_20≤−3%` 或 `worst_close_20≤P_net_buy×(1−5%)`；仅 `summary.addon.declined_after_addon` 内部使用 |
| AC-D4 | 止盈/越涨越卖（已废弃） | v1.2 起 `profit_taking`/`rising_sell_sequence` 不再输出 |
| AC-D5 | 卖飞（已废弃，见 AC-D9/D10） | 旧 20D 3% 口径仅保留在 `summary.sell_fly` 内部 |
| AC-D6 | 理由一致性 | 手工 code 与识别标签交叉表；不强制一致；`late_annotated` 分层展示；覆盖率近 90 日/全历史 |
| AC-D7 | 部分/清仓/T+1（已废弃） | 由 AC-D11/D12 的做T/调仓判定替代 |
| AC-D8 | 抄底计数 | 回合内买入账单命中 `instant_bottom` 或 `short_term_bottom` 的账单个数；同一账单只计 1；`status_counts.bottom_buy` 与 `flags` 投影一致 |
| AC-D9 | 止损卖飞计数 | 卖出账单命中 `short_term_sold_early` 且含费净卖价 **≤** 卖出前含费均价；等号归止损侧；`status_counts.stop_loss_sold_early` |
| AC-D10 | 止盈卖飞计数 | 卖出账单命中 `short_term_sold_early` 且含费净卖价 **>** 卖出前含费均价；`status_counts.profit_sold_early`；`sold_too_early` = 两类任一 > 0 |
| AC-D11 | 收益回撤 | 数量加权平均卖价 < 首笔买入日（含）~末笔卖出日（不含）最高收盘 × 95%，严格；无 bar/无卖出 → 不触发 |
| AC-D12 | 日内/波段做T | 部分卖出 = `0 < 卖出数量 < 卖出前持仓`；其后第一笔买入同日 → 日内、更晚交易日 → 波段；已被日内配对的卖出不再计波段 |
| AC-D13 | 日内调仓 | 同用户同日全部流水（不按账户切分）；先卖当前证券后买其它 → out；先卖其它后买当前 → in；只判方向与全局行序，不要求金额匹配；对手方只认真实二级市场证券成交（排除 `CASH` 资金行、公司行动、新股申购、货基申赎） |

### 8.5 买卖后表现排序（AC-K）

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-K1 | 原始 20D B3 | `buy_excess_20`/`sell_avoid_20`/`combined_20` 为百分点；正负方向正确；fixture 手算；不得用 0–100 percentile 主排序 |
| AC-K2 | 样本/CI | `n_rounds≥30` 才排名；10–29 显示 raw+样本不足；<10 不下结论；P0 至少 n/sample_status；P1 bootstrap CI |
| AC-K3 | 确定性/并列 | 排序键 `combined_20 desc, close_date desc, round_id`；同输入稳定；测试 |
| AC-K4 | 无未来/不混基 | 只用 20D 完整且 status=ok；open 单列；B3/B1 不混；缺基准不排名 |
| AC-K5 | 象限/聚合 | 按正负号双正/买正卖负/买负卖正/双负；per-stock n≥3；仅本人 |
| AC-K6 | 解释/合规 | 显示 raw、n、basis、CI、ranking_as_of、disclaimer；禁止“能力分/击败他人/收益预测” |

### 8.6 页面/集成（AC-P）

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-P1 | 独立入口/路由 | `/rounds/insights` 受保护可访问；API 静态路由先于 `/{round_id}`；前端静态路由先于 `/:roundId`；契约/路由测试 |
| AC-P2 | 理由编辑/ETag | 列表/详情可编辑；保存后 ETag 变化、无 304 丢理由；版本冲突提示；无明文日志 |
| AC-P3 | 手动更新 | 按钮带 Idempotency-Key；200/202 两条路径；防重复；失败重试；自动更新未开启提示 |
| AC-P4 | 零口径/展示契约 | 只渲染 `_pct/_display/label`；无裸小数/裸码/0% 缺失；排序由后端；条形用 bar_pct；源码零算术 |
| AC-P5 | 术语/合规 | 页面禁用 B0/B1/B2/B3 裸码；中文标签；页脚 disclaimer；词表断言（最佳买卖点/应加仓/能力分等） |
| AC-P6 | 状态/空态/移动端 | pending/no_calendar/no_bar/stale/corporate_action 有原因；390px 无横向溢出；卡片/modal 可用 |
| AC-P7 | 可访问性 | 表单 label/aria-live；旗标文字+图标；表格 scope/caption；对比度 |
| AC-P8 | 回归 | `/rounds`、`/rounds/:id`、`/trades`、Dashboard 不回归；`test-p2`/`test-isolation`/`api-check` 全绿 |

### 8.7 数据/迁移/门禁（AC-G）

| # | 验收 | 判定/方法 |
|---|---|---|
| AC-G1 | 迁移/版本 | `0012` 建表 + RLS + 索引 + downgrade；`schema_version` stamp 与 `version.py` 同步；`REVIEW_TABLES`/conftest/RLS 测试含新表；`migrate-check` 通过 |
| AC-G2 | 契约 | Catalog/DEV-PLAN/`EXPECTED_ALL_ENDPOINTS 93`/operations 61/JSON 示例 +4/隔离矩阵/SDK/手写类型；`make api-snapshot && make api-check` |
| AC-G3 | 作业 | `JobType.REFRESH_ROUND_INSIGHTS` 入枚举 + HANDLERS；单飞/租约/取消/重试测试；CLI/ cron 触发说明 |
| AC-G4 | 清理/隐私 | 软删/撤销后 reason 隐藏、forward 清理；RLS 扫描；reason_text 不入日志/导出/LLM |
| AC-G5 | 性能/规模 | 5k/20k 两档；批量 SQL；分页/汇总一致性；summary 不随 cursor 变化 |
| AC-G6 | 文档同步 | DATABASE/API-Catalog/DEV-PLAN/AGENTS/设计/评审记录/版本号一致 |

### 8.8 Fixture 与测试落点（最低）

- `backend/tests/fixtures/round_insights/`：
  - `fx_calendar`（跨春节/国庆；5/10/20；同日=1）
  - `fx_b0_b3`（B0/B1/B2/B3 手算；毛/净价）
  - `fx_swing_td`（1/2/20/21 TD）
  - `fx_addon`（低于均价、逐次更低、继续下跌/反弹、epoch 重开）
  - `fx_sellfly`（止盈卖飞、越涨越卖、止损后反弹、部分/清仓）
  - `fx_corporate`（`other`+“10送10”、分红、配股）
  - `fx_ranking`（35 个回合已知 raw；30 阈值；并列）
  - `fx_quality`（停牌、缺基准、no_calendar、pending）
- 测试：`tests/unit/core/test_round_insights.py`、`tests/api/test_round_insights.py`、`tests/api/test_trade_reason.py`、`tests/integration/test_round_insights_flow.py`、`tests/contract`、`tests/isolation`。
- 性能：`tests/perf/test_round_insights.py`（5k/20k；CI 可跑 mark，不作为单元默认）。
- 前端：`Attribution` 之前的教训——新增 `zero-caliber`/空态/移动端/路由顺序/词表测试。

## 9. 实施计划与估时（v1.0）

| 阶段 | 内容 | 估时 | 依赖 |
|---|---|---|---|
| **P0-1 数据模型/稳定键** | 迁移 0012：reason/forward + dedupe_key + RLS + 索引 + downgrade + schema_version/version.py + REVIEW_TABLES/conftest | 1.5d | 无 |
| **P0-2 理由 API/UI/缓存** | 枚举/CRUD/If-Match/审计 + server Clock + ETag + `has_reason/reason_code` 过滤 + 表单 | 1.5d | P0-1 |
| **P1-1 前瞻收益 core** | 真实日历/净价/B0~B3/公司行动 shared detector/BHAR 对账 + batch refresh + data_as_of | 2.5~3.0d | P0-1、行情缓存 |
| **P1-2 洞察/排序** | swing/addon/sellfly 分类器 + 20D B3 + n/CI + per-stock + open/closed 分离 + API | 2.5~3.0d | P1-1 |
| **P1-3 页面** | `/rounds/insights` + 详情 + 待补理由 + 手动刷新 + 路由/术语/合规 | 2.5~3.0d | P1-2 |
| **P1-4 门禁/验收** | fixtures/API/isolation/契约/SDK/性能/a11y/词表/路由顺序 | 2.0d | 全部 |
| **P2 自动化** | JobType/handler/CLI/cron/单飞/重试 | 1.0d | P1 |
| **合计** | | **13.0~16.0d**（不含 PM/评审等待） | |

> 最小切片（理由 + 前瞻 + 单页，不含波段/抄底/卖飞/排序）约 **5.5~7.5d**；排序与 CI 是本次评审后加重的部分，建议分两批交付。

---

## 10. HITL 决议与新增待确认

> 用户在 2026-11-04 已确认 H-01~H-10“没有问题”。评审发现其中 H-01/H-04/H-05/H-07 的实现口径需按专业正确性收紧，已写入 v1.0；**不是重新征询偏好**，而是把“用户意图”落成可实现口径。若用户不同意，按“回退项”在实现前调整。

### 10.1 原 H-01~H-10：确认与 v1.0 落地

| # | 用户意图 | v1.0 落地 | 回退项 |
|---|---|---|---|
| H-01 | 波段 2–20 天 | **真实交易日** `holding_trading_days` 2–20；≤1 超短、>20 趋势；fill 级另算 | 若坚持工作日 proxy，需明确标注（不推荐） |
| H-02 | 同时看收益/超额 | B0 执行、B1 事件后、B2 净价后、B3 相对基准；排序只用 **20D B3** | 只看 B1（会高估择时，不推荐） |
| H-03 | trade 级理由 | trade 级 + dedupe_key 逻辑键；回合聚合 | 仅回合级（丢分批理由） |
| H-04 | 自比较排序 | **原始 20D B3 百分点**排序；n≥30 才排名；百分位只作辅助 | 0–100 percentile（评审否决，统计上不是能力） |
| H-05 | 阈值 | 买后 20D B2≤−3% 或 worst_close≤−5%；卖后 max_close>净卖价×1.03；**不回退 B2>5% OR** | 统一 ±5%；只期末 |
| H-06 | 每日更新 | 手动 refresh（Idempotency-Key，≤5000 同步/超过 202）；后续 cron/job | 全部异步 |
| H-07 | 公司行动 | **任何公司行动 fail-closed**、不排名；P1 接行情源/后复权；不硬算 | 硬算（送转会产生假跌，不推荐） |
| H-08 | 理由码 | 静态枚举 + 自由文本；P1 配置化 | 纯自由文本 |
| H-09 | 与 journal 关系 | 本期独立 `trade_reason`；journal 上线后合并；不双写 | 等 3-01 |
| H-10 | 绝对收益展示 | 页面并列 B0/B1/B2/B3；排序只 B3；不隐藏绝对 | 只显示超额/只显示绝对 |

### 10.2 评审新增技术决策（v1.0 已采用）

| # | 决策 | 采用值 | 影响/说明 |
|---|---|---|---|
| T-01 | 稳定键 | `(user_id, trade_dedupe_key)` 作为 reason/forward 逻辑键；`trade_id` 仅当前展示 | 解决 re-import 理由丢失；见 arch review P0-2 |
| T-02 | 理由时间 | 服务端 Clock 写 `annotated_at`；客户端不得传；`late_annotated` 服务端算 | 防伪；见 arch P0-6 |
| T-03 | 日历/持有期 | 真实交易日；`holding_trading_days`；旧 `holding_days` 只展示 | 见 quant P0-3 |
| T-04 | 公司行动 | 共用 `is_corporate_action`（含 `other`+摘要关键词）；窗口内任何 action → null | 修送转漏判；见 quant P0-4/arch P0-5 |
| T-05 | 净价/费用 | B2/止盈/卖飞/摊平用 `amount/qty` 含费净价；B0 毛价与归因 D-03 对齐 | 见 quant P0-1/P0-5 |
| T-06 | 18 指标 | **不改**既有 18 指标口径；新增分类器抽 core 共用；B3 与 BHAR 共用函数 | 避免范围爆炸；见 quant P0-2/P0-6 |
| T-07 | 排序统计 | 20D B3 原始百分点；n≥30；CI/sample_status；不把 percentile 当能力；象限按正负号 | 见 quant P0-7/P1-5 |
| T-08 | 路由顺序 | `/rounds/insights` 先于 `/{round_id}`；前后端都测 | 见 arch P0-1/product P1-10 |
| T-09 | 刷新 | Idempotency-Key + 单飞 + 202/200；data_as_of 用 DB max；批量 SQL | 见 arch P0-3/P0-4/P0-7/P0-8 |
| T-10 | API 编号/期次 | **API-089 预留（不实现）**；本任务 API-090~094，期次待 PM（建议 Period3-05）；计数联动 | 唯一待 PM 项 |
| T-11 | 清理/隐私 | 软删/撤销隐藏理由；forward 清理；reason_text 不入日志/导出/LLM | 见 arch P1-9 |

> **仍需 PM 确认**：T-10 的期次归属与 Catalog/DEV-PLAN 编号；其余技术口径已定稿，不再阻塞实现。

---

## 11. 其他建议（按优先级）

| 优先级 | 建议 | 价值/边界 |
|---|---|---|
| **P1（本设计内可做）** | **理由 → 结果矩阵**：`reason_code × B0/B1/B2/B3/失败率`，回答“哪种理由最常让我亏/卖飞” | 本需求最高价值的衍生报表；`late_annotated` 分层，避免事后解释污染 |
| **P1** | **决策时快照**：标注理由时记录价格/仓位/成本/近 20 日涨跌，区分“事前逻辑/事后解释” | 只存快照，不做预测；不评判情绪 |
| **P1** | **机会成本金额化**：卖飞“理论上限/期末可实现”两列；抄底“期末浮亏/最低收盘理论上限”两列 | 不把不可实现的最大值当实际亏损；金额+百分比并列 |
| **P1** | **执行 vs 事件拆分**：B0（执行价差，与归因 D-03 同口径）、B1（事件后）、B2（净价后）分开 | 解决“买得好但成交差”/“成交好但买错”的归因 |
| **P2** | **capture ratio**：`已实现 PnL / 区间最大浮盈`（收盘口径） | 需逐日 bar；P1 指标 |
| **P2** | **市场状态分层**：基准 MA60 上/下或近 60 日涨跌分桶 | 避免牛市错觉；不声称因果 |
| **P2** | **理由与行为评分卡打通**：理由覆盖率/抄底失败率/卖飞率作为评分卡 E 维输入 | 评分卡尚未实现，接口后置 |
| **不做** | 买卖点预测、跨用户排行榜、AI 自动写理由、0–100“能力分”、收益率反推能力 | 合规/统计/隐私边界 |
| **不做** | 引入 hfq/公司行动外部源（本期） | P1 后再放开公司行动窗口 |
| **必须** | 刷新只读行情缓存；先 `sync-market`；缺失/停牌/除权/无日历显式 null，不伪造 0 | 数据质量底线 |

---

## 12. 风险与开放问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| 市场整体涨跌/β/风格 | 事件后收益同向，B3 仍非完全中性 | 只用 B3 20D 排序；页面写“相对基准”；P1 做 β/行业分层 |
| 样本少/新账户 | raw 不稳、排名噪声 | n≥30 才排名；10–29 显示 raw+CI/样本不足；<10 不下结论 |
| 公司行动/停牌/退市 | 前瞻收益假跌/假涨 | 任何公司行动 fail-closed；停牌 stale≤5；P1 接行情源/后复权 |
| `trade_id` 重导入 | 理由/前瞻丢失 | dedupe_key 逻辑键；重导入迁移；孤儿提示 |
| `data_as_of` 不可靠 | 越界/未来数据 | 用 `max(daily_bar.trade_date)`；不读未来 bar；AC-F4 |
| 刷新慢/并发 | 页面卡/重复写 | 批量 SQL；Idempotency-Key+单飞；5000 阈值 200/202 |
| 理由事后标注 | 因果污染 | server `annotated_at` + `late_annotated`；理由×结果分层 |
| 理由文本敏感 | 隐私/出站 | 不入日志/导出/LLM；RLS；仅本人 |
| 与 journal 重复 | 双写/不一致 | 本期独立表；journal 上线后迁移；H-09 |
| 排序被误读为建议/能力 | 合规/误导 | 文案“事后相对表现，不是能力/收益预测”；无跨用户 |
| 与 18 指标口径漂移 | 数字不一致 | 抽 core 分类器/共用 B3 函数；不改旧指标；文档映射 |
| API 编号期次 | Catalog/DEV-PLAN 计数错 | 实现前 PM 定 T-10；gate 同步 |

---

## 附录 A：手算示例（验收向量）

**A.1 前瞻收益（买入，含费净价）**

- 买入 1,000 股，毛价 10.00，费用 5.00 → `amount = −10,005.00`，`P_net_buy = 10.005`；d0 收盘 10.00。
- 第 5/10/20 日收盘 10.50/11.00/12.00；基准同期 +2%/+4%/+8%。
- `B0 毛`：(10−10)/10 = 0.00%。
- `B0n 净`：(10−10.005)/10.005 = **−0.04998%**。
- `B1`：+5.00% / +10.00% / +20.00%。
- `B2`：10.50/10.005−1=**+4.9475%**；11/10.005−1=**+9.9450%**；12/10.005−1=**+19.9400%**。
- `B3`：+3.00% / +6.00% / +12.00%。
- 不足 20 日：`h20=null`，`h5/h10` 有值（独立填充）；页面显示 `—`。

**A.2 加仓抄底失败（净价含费）**

- 原持仓均价 10.00；加仓 800 股，毛价 9.00，费用 16.00 → `P_net=9.02`。
- 20 日内最低收盘 8.30，期末 8.60。
- `B2_20 = 8.60/9.02−1 = −4.656%`（≤−3%）→ `decline_after_addon=true`。
- `worst_close_20 = 8.30/9.02−1 = −7.98%`（≤−5%）。
- `additional_loss_end = 800×(8.60−9.02) = −336 元`；`additional_loss_worst = 800×(8.30−9.02) = −576 元`（**理论上限**，不是可实现亏损）。

**A.3 止盈卖飞（净价含费，单一主口径）**

- 卖出 500 股，毛价 20.00，费用 10.00 → `P_net_sell=19.98`。
- 20 日内最高收盘 22.00，期末 21.00；`19.98×1.03=20.5794` → `max_close 22.00 > 20.5794` → `sold_too_early=true`。
- `missed_profit_worst = 500×(22.00−19.98) = 1,010 元`（理论最高，不可实现）。
- `missed_profit_end = 500×(21.00−19.98) = 510 元`（期末可实现口径）。
- 若卖出前均价 21.00（亏损卖出）→ `is_profit_taking=false`，标记 `stop_loss_rebound`，**不算止盈卖飞**。
- 不再使用 `B2>5%` 作为并列条件（已逻辑包含）。

**A.4 波段（真实交易日）**

- 回合 A 持有 8 TD、pnl +800；B 持有 20 TD、pnl −200；C 持有 21 TD、pnl +500。
- A/B 为波段，C 为趋势；`swing_rounds=2/3`；`swing_pnl=600`；总 pnl 1,100；贡献 54.55%；波段胜率 1/2；平均 300/回合（按笔）。
- 若 A 为 1 TD → 超短，不计波段；fill 级另算，不与回合数相加。

**A.5 排序（20D B3 原始百分点）**

- 31 个合格回合（n≥30）：某回合 `buy_excess_20=+8.00%`、`sell_avoid_20=+5.00%` → `combined_20=+6.50pp`；象限“双正”；按 combined 降序排名，显示 n=31、CI（P1）。
- 另一回合 buy−2%、sell+3% → +0.5pp，象限“买负卖正”。
- 12 个合格回合：只显示 raw + “样本不足”，**不排名**；不计算 0–100 percentile。
- 5 个：只显示 n 和原始事件，不下结论。

---

## 附录 B：术语与参考

- **B0 执行价差（毛）**：买入 `(C0−P_gross)/P_gross`；卖出 `(P_gross−C0)/C0`；与归因 D-03 的买/卖点执行口径同向。
- **B0n 执行价差（净）**：用含费净价；用于经济视角。
- **B1 事件后收盘收益**：`C(dN)/C(d0)−1`；不是“决策漂移”，也不等于“择时能力”。
- **B2 净价后收益**：买入 `C(dN)/P_net_buy−1`；卖出 `C(dN)/P_net_sell−1`（正=卖后价格高于净卖价）。
- **B3 相对基准收益**：`B1−benchmark_B1`；单事件口径，非 BHAR 均值；基准是 `hs300_tr` ETF 代理，非 β/风格中性。
- **行情日**：A 股交易日（基准 bar 日期集合），不是自然日；无日历则 no_calendar。
- **holding_trading_days**：真实交易日持有期；旧 `round.holding_days` 是 weekday proxy，仅展示。
- **波段**：默认 `2≤holding_trading_days≤20` 的已平仓回合/配对；fill 与回合不可相加。
- **抄底/摊平**：加仓且净买价低于加仓前含费均价；不声称正确/错误，只标后验下跌。
- **卖飞**：卖出后 20 日最高收盘 > 净卖价×1.03；拆 `profit_take_fly`/`stop_loss_rebound`；不是投资建议。
- **买/卖后表现排序**：20D B3 原始百分点；不是能力分、不是收益预测、不跨用户。
- 参考：`docs/architecture/2026-10-26-metrics-three-domain.md`（18 指标）、`docs/architecture/2026-11-02-five-factor-attribution.md`（买/卖点 D-03）、`docs/architecture/2026-11-04-investor-behavior-scorecard.md`（行为评分卡）、`docs/prd/PRD-v2.md` FR-D4（journal）、`docs/architecture/v2/Testing.md`。

> **v1.0 定稿（2026-11-04）**：H-01~H-10 已确认，T-01~T-11 已按评审建议写入；仅 T-10（API-ID 期次）待 PM。所有阈值/公式改变须 bump `definition_version` 并更新评审记录。

## 附录 C：实现与 §6.3 示意结构的差异（2026-11-05）

§6.3 正文已按**实现**回写；下表保留 v1.0 初稿示意与实现差异，供评审追溯：

| 项 | v1.0 初稿示意 | 实现 | 原因/影响 |
|---|---|---|---|
| 买卖侧结构 | 并列 `buy_reason`/`sell_reason` + `forward.buy/sell.h5..h20` | `buy_trade`/`sell_trade` 各含 `trade_id/date/价量额/reason/reason_label/forward["5"\|"10"\|"20"]` | 与详情逐笔、理由编辑（trade_id）统一；前端不拼路径 |
| 前瞻字段 | `exec_gross_pct/drift_pct/trade_return_pct/excess_pct` | `horizon/status/base_shifted/b0_pct/b0_net_pct/b1_pct/b2_pct/b3_pct/max_close_display/min_close_display/price_jump_review` | 与 core 向量/状态机一致，避免同义字段两套 |
| flags | 对象布尔 | 内部码数组 `list[str]` | 与 `flag=` 词表服务端映射；新增旗标免破坏结构 |
| quality | 对象（`forward_complete`/`calendar_basis`/...） | `list[str]`；`price_jump_review` 及前瞻非 ok 状态进入该列表 | 详情按“质量提示码”展示；日历基准在 page 级 assumptions |
| ranking | 嵌套对象含 CI/相对位置 | 列表页扁平 `buy_excess_pct`/`sell_avoid_pct`/`combined_pct`/`rank`/`quadrant`；CI/相对位置在 `summary.ranking` | 契合零口径字符串，避免每行重复大对象 |
| 变更规则 | — | 以上为当前实现口径；若改回示意结构须 bump `definition_version` 并同步 SDK/测试 | — |
