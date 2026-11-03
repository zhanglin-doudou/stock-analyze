# Period2-08 五因子归因 · 页面功能验收与可读性/可用性评审（2026-11-02）

> 验收对象：运行中的本地实例（Web `http://127.0.0.1:5173`，API `http://127.0.0.1:8000`）
> 验收账号：`1234@dev.com` / `1234`（AGENTS.md 固定本地体验账号）
> 验收请求：`GET http://127.0.0.1:5173/attribution?period=2026-01-01..2026-10-31`
> 对应设计：[五因子归因设计 v1.0](../architecture/2026-11-02-five-factor-attribution.md)（§7.3 共 19 条验收、§8.1 页面结构）；实现状态：Period2-08 08a~08g 已落地（AGENTS.md §0）
> 验收方式：**真实登录 + 真实 API 数据 + 无头 Chrome 渲染**（Chrome 153 headless / CDP，桌面 1440×900、移动 390×844），辅以页面源码、DOM 计算样式/布局度量、数据库手算核对、前端单测。
> **总体结论：不通过（REJECT，需修复 P0 后复验）。** 页面壳、状态机与零口径基本符合设计，但 **桥接对账在全部被测区间均报 `BRIDGE_MISMATCH`**、**请求区间的残差/估值差异常导致 `summary=null`（"本期结论"只剩一个 `—`）**，且存在多处可读性/可用性 P0（原始码值直出、比率符号与金额相反、排序徽标乱序、移动端横向溢出）。在修复数据正确性 P0 前，该页面不能作为"为什么亏"的验收通过件。

---

## 0. 验收摘要

| 维度 | 结论 | 关键证据 |
|---|---|---|
| 页面可访问 / 状态机 | ✅ 基本通过 | 请求 URL 正常加载，`status=degraded`；blocked 区间（2026-06）**不渲染任何金额**，只显示数据质量卡 |
| API-036 19 条验收 | ⚠️ **15 PASS / 3 SKIP / 1 FAIL** | `BRIDGE_MISMATCH` 在 2026-10、2026-01-01..2026-10-31 等**所有被测区间**复现（第 ⑲ 条 FAIL） |
| 数据正确性 / 可解释性 | ❌ **不通过（P0）** | 请求区间 `total_pnl=84,198.47`、`factors=−25,410.83`、`residual=109,609.30`（残差比 130.18%），`valuation_gap=−336,022.00`，`summary=null` |
| 零口径（前端不算数） | ✅ 通过 | 因子条宽度直接使用后端 `presentation.bar_pct`（DOM 实测 405.875px / 34.9%），源码无 `Number/parseFloat/toFixed`；前端单测 124/124 通过 |
| 可读性 / 可用性 | ❌ **不通过（多项 P1/P0）** | 原始 `snake_case` 码值、`0.71875712` 式原始小数、因子顺序 `2,1,5,3,4`、负收益下 ratio 与 amount 符号相反、"基准 amount/total_pnl" 误标等 |
| 移动端 | ❌ 不通过（P1） | 390px 视口下文档宽度 **461px**（横向溢出 71px），右侧内容被裁切；定位到"缺行业归属"长代码串 |

> 验收边界：仅 Chrome headless（Chromium 153）；未验证 Safari/Firefox、真实触屏手势、屏幕阅读器；未执行 `make test-p2` / `make lint` 全量门禁（本轮是页面验收，不是发版门禁）。

---

## 1. 环境与验收对象

| 项 | 值 |
|---|---|
| 时间 | 2026-11-02 23:47–00:00（Asia/Shanghai） |
| Web | `http://127.0.0.1:5173`（Vite dev，`/api` 代理到 API） |
| API | `http://127.0.0.1:8000`（`/healthz`、`/readyz` 均 200） |
| 账号 | `1234@dev.com`（user）；对照用 `admin@demo.example.com`、`b@demo.example.com` |
| 代码状态 | `HEAD=4ac7f75`（`feat(attribution): 落地五因子归因 08a–08g（API-036 + 前端页 + 板块指数数据前置）`）；验收时工作区另有 3 个未提交文件（`Makefile`、`sector_live.py`、`test_sector_live_bars.py`），本轮以**运行实例**为准；正式验收/发版前需确认工作区干净并锁定 commit |
| 渲染 | Google Chrome 153.0.8010.55 `--headless=new --no-sandbox` + CDP；截图见 [assets](2026-11-02-attribution-acceptance/) |
| 数据 | 账户 2021-10-29 起有流水；资产表覆盖至 2026-11-02；板块同步 `board_bar_as_of=2026-10-30` |

**验收请求与结果（摘要）**：

```http
GET /api/v1/attribution?period=2026-01-01..2026-10-31
Authorization: Bearer <1234@dev.com>
200 · 4,185 bytes · 0.92s
status=degraded   total_pnl=84198.47   base_capital=5699936.96
factors: selection=+60,518.25  sector=-91,228.02  buy=-2,750.75  sell=+13,364.88  fee=-5,315.19
residual=109609.30  residual_ratio=1.30179681  summary=null
missing: BRIDGE_MISMATCH / SECTOR_BAR_MISSING(5只) / SECTOR_MAPPING_MISSING(7只) / RESIDUAL_LARGE
```

---

## 2. 页面走查（截图 + DOM）

### 2.1 请求 URL（桌面 1440）——主验收场景

![桌面：请求区间](2026-11-02-attribution-acceptance/desktop-requested.png)

- 页面可正常加载（`document.readyState=complete`，无网络失败、无 JS 异常；控制台仅 Vite/React DevTools/React Router 未来标志警告）。
- 期选择器正确显示"自定义 + 2026/01/01 至 2026/10/31"；页头显示"页面状态 降级 · 数据截至 2026-10-30 · 总损益口径 asset_daily_mtm"。
- 五因子 + 残差 + 数据质量三卡均渲染；`data-testid` 齐全（`attr-summary/attr-residual/attr-quality/attr-missing-*`）。
- **"本期结论"面板只有孤立的 `—`**（`summary=null`），没有任何"为什么没有结论"的解释；对用户而言等价于页面坏了。

### 2.2 可出结论对照（2026-10，still degraded）

![桌面：2026-10 健康态](2026-11-02-attribution-acceptance/healthy-2026-10.png)

- `summary` 正常输出："本期亏损 85,127.13 元，主要来自「板块配置」（-120,877.22 元，占构成 76.8%）……"
- 但同时暴露可读性问题：因子金额与 ratio 符号相反（选股 +30,909.49 / ratio −0.363；板块配置 −120,877.22 / ratio +1.419），且 `BRIDGE_MISMATCH` 仍挂在数据质量卡。

### 2.3 blocked 态（2026-06）——金额不泄露

![桌面：2026-06 blocked](2026-11-02-attribution-acceptance/blocked-2026-06.png)

- ✅ 符合设计 §8.1-④：`status=blocked` 时**不渲染任何金额**，只保留"数据质量"卡（两条缺失项 + 回合/板块/看板/月报入口）。
- ⚠️ 页头"数据截至 2026-11-02"是资产表最新日，不是区间末端（2026-06-30），容易被误读为"该区间数据到 11 月"；且 `missing` 的 `field` 为 `total_pnl/classification_as_of` 原始码值。
- 说明：2026-06 blocked 的 `SECTOR_HISTORY_NOT_COVERED` 是 **live 模式首次同步日晚于区间终点的按设计行为**（AGENTS 板块铁律 5 / 设计 A-05）；demo 验收应使用 replay 夹具或经批准的 look-ahead，本项记为 **SKIP（环境限制）**，不是缺陷。

### 2.4 移动端（390×844）——横向溢出

![移动端：请求区间](2026-11-02-attribution-acceptance/mobile-requested.png)

- 390px 视口下 `documentElement.scrollWidth=461 > clientWidth=390`（溢出 71px），页面右侧（金额、日期输入、部分说明）被裁切。
- CDP 定位唯一越界元素：`.attr-missing li strong` 内"7 个标的（缺行业归属）：300341,600077,600114,600636,603306,603357,688126"（`width=363px`）；`attribution.css:126-129` 的 `flex: 1 1 320px` + 长代码串无换行点导致。修复：该元素加 `min-width:0; overflow-wrap:anywhere`，或后端把代码列表拆数组、前端渲染为可换行 chips。

### 2.5 交互与状态（源码 + DOM）

| 检查 | 结果 | 证据 |
|---|---|---|
| 期间切换（年/月/自定义） | ✅ 可用；URL `period` 与输入同步；`keepPreviousData` 切期间保留旧数据 | `PeriodInput.tsx`、DOM `select[aria-label=期间类型]`/`input[aria-label=开始日期]` |
| 加载态 | ⚠️ 仅一个"加载中…"面板，无骨架屏/无 `aria-busy` | `Attribution.tsx:236-240` |
| 错误态 | ✅ `ProblemView` + 重试 | `Attribution.tsx:213-219` |
| blocked 态 | ✅ 只渲染质量卡 | `Attribution.tsx:240-244` |
| summary=null 态 | ❌ 直接 `displayValue(null)` → `—`，无原因/无行动 | `Attribution.tsx:88-91` |
| 因子排序 | ❌ 固定数组顺序（选股/板块/买点/卖点/费用），rank 徽标为 `2,1,5,3,4`；源码注释"后端 rank 决定展示顺序"未实现 | `Attribution.tsx:78,92-95` |
| 零口径 | ✅ 条宽用 `presentation.bar_pct`；无 `Number()/parseFloat/toFixed` | `Attribution.tsx:26-28,43-47`；前端 `eslint` 规则 + `zero-caliber.test.ts` |

---

## 3. API-036 验收矩阵（设计 §7.3 的 19 条）

> 方法：登录后真实请求 + `var/acceptance-p208/api_checks.py`；数值以请求区间响应为准。判定为 PASS 的条目均附实际值。

| # | 断言 | 结果 | 实际证据 |
|---|---|---|---|
| ① | `Σfactors.amount + residual = total_pnl`（≤0.01） | ✅ PASS | −25,410.83 + 109,609.30 = 84,198.47（差 0.00）；**注意**：这是 plug 恒等式，不能单独证明口径正确 |
| ② | `rank` 从 1 连续无重复 | ✅ PASS | `[2,1,5,3,4]` → 排序后 1..5 唯一 |
| ③ | `Σratio + residual_ratio = 1`（≤1bp） | ✅ PASS | −0.30179681 + 1.30179681 = 1（plug） |
| ④ | 单因子主导 `rank==1` 的 code 正确 | ⏭️ SKIP | 依赖 `fx_attr_single_*` 合成 fixture；本轮未跑 pytest fixture（live 数据非单因子） |
| ⑤ | blocked：`total_pnl` 缺省、金额不渲染 | ✅ PASS | `period=2026-06` → `status=blocked`、`total_pnl=null`、`factors[].amount=null`；页面只渲染质量卡（见 2.3） |
| ⑥ | `factors[]` 恰 5 项、固定顺序、含 `sector`、中文名 | ✅ PASS | 顺序 `selection/sector/buy_timing/sell_timing/fee`；名称 选股/板块配置/买点/卖点/费用 |
| ⑦ | `Σresidual_breakdown = residual` | ✅ PASS（数值） | 0 + 445,625.33 + 0 + 5.97 + 0 − 336,022.00 = 109,609.30；**桶金额可信度存疑，见 §4.2** |
| ⑧ | 结论无裸术语、无买卖建议 | ✅ PASS | `jargon=[]`、`advice=[]`（注意：页面另把原始码值直接展示，见 §5） |
| ⑨ | 跨用户隔离 | ✅ PASS（弱） | B 用户请求 200、`total_pnl=null`（B blocked，无数据）——无泄漏；建议补 A/B 均有数据的越权测试 |
| ⑩ | admin 越界 → 403 `ADMIN_CANNOT_READ_TRADES` | ✅ PASS | 403 + 枚举码正确 |
| ⑪ | 参数校验 | ✅ PASS | 非法 period 422 `INVALID_PERIOD`；brinson 422 `MODEL_NOT_AVAILABLE`；非法 benchmark 422 `UNKNOWN_BENCHMARK`；未知参数 400 `UNKNOWN_QUERY_PARAM` |
| ⑫ | 同输入两跑逐字节一致 | ✅ PASS | 两次响应 SHA-256 相同 |
| ⑬ | 部分缺板块 → degraded + 剔除披露 | ✅ PASS | `status=degraded`、`capital_ratio=0.99243115`、`unattributed` 5 条、`SECTOR_BAR/MAPPING_MISSING` |
| ⑭ | 负 total_pnl 的 ratio/share/summary 语义 | ✅ PASS（语义） | 2026-10：`total_pnl=-85127.13`、`summary` 含"亏损/拖累/占构成"、share≥0；**页面 ratio 符号易误读，见 §5 U-05** |
| ⑮ | 小样本 `n_attributed_segments<10` → `insufficient` + `summary=null` | ⏭️ SKIP | 账户交易频繁，实测 `2026-10-29..10-31` 仍有 14 段；未构造出 <10 的 live 区间，需 fixture 覆盖 |
| ⑯ | `data_as_of=min(资产/行情/板块)`、`assumptions` 18 键 | ✅ PASS | 2026-10-30 = min(2026-11-02, 2026-10-30, 2026-10-30)；18 键齐全 |
| ⑰ | 周末月末不误判 stale | ✅ PASS | `2026-10-30..2026-10-31`：`degraded`，无 `ASSET_SERIES_STALE` |
| ⑱ | 公司行动剔除（不产生幻影因子） | ⏭️ SKIP | 依赖 `fx_attr_corporate_action` fixture；本轮 live 未命中送转/配股事件 |
| ⑲ | bridge 对账（无 `BRIDGE_MISMATCH`） | ❌ **FAIL** | 请求区间与 2026-10、2026-06（blocked 前）均出现 `BRIDGE_MISMATCH`；见 §4.1 |

**小计：15 PASS / 3 SKIP / 1 FAIL。**

> 注：live smoke 脚本 `api_checks.py` 原始输出为 18 PASS / 2 FAIL（其中 ⑮ 因未构造出 n<10 的 live 区间被判 FAIL；本文按设计裁决记为 **SKIP**，由 fixture 覆盖；④⑱ 不在脚本内，同样由 fixture 覆盖）。矩阵以本节 19 条为准。

单测层面：`frontend/apps/web` `pnpm test` 124/124 通过（含 `Attribution.test.tsx` 3 条），但测试目前把 `0.9886` 等原始小数写进断言，未覆盖空结论、排序、移动端溢出。

---

## 4. 数据正确性缺陷（P0，必须修复）

### 4.1 P0-A：桥接对账在全部区间 FAIL（`BRIDGE_MISMATCH`）

**现象**：任何被测区间（2026-10、2026-01-01..2026-10-31）都出现：

```
missing[].code = BRIDGE_MISMATCH
reason = "MTM 损益与桥接恒等式差额超过 1 分（对账断链）"
```

**量化（以 2026-10 为例，数据均来自 `/assets` 与 `/reconciliation/bridge`）**：

| 项 | 值（元） |
|---|---|
| MTM total_pnl（`V_end−V_begin−CF`） | −85,127.13 |
| bridge_profit（realized −40,409.59 + 股息/利息/费用/其他 0） | −40,409.59 |
| Δ浮盈亏（2026-10-30 − 2026-09-30 的 `unrealized_pnl`） | −47,996.94 |
| 桥接预测 = bridge_profit + Δ浮盈亏 | −88,406.53 |
| **差额** | **3,279.40**（阈值 0.01） |

**差额可被完整分解（根因）**：

- 桥接 `initial_open_cost=654,477.93` vs 资产表期初 `holding_cost=659,183.84`（差 4,705.91）；
- 桥接 `open_cost=664,071.80` vs 资产表期末 `holding_cost=670,602.16`（差 6,530.36）；
- 两组成本口径差 → 1,824.45；加货基余额变化 1,454.95 → **3,279.40**。

即：实现用 **bridge（FIFO 成本）** 的“利润”+ **asset_daily（另一套成本/货基口径）** 的“浮盈亏”做对账，两边成本基准不一致，**1 分容差永远不可能通过**。设计 §3.2.1 要求“same primitives”，实现没有对齐。

- 代码位置：`backend/packages/review/src/stock_review/application/attribution.py:687-714`（bridge 对账）、`.../application/bridge.py`（bridge 成本/现金口径）、`.../application/assets.py`（资产表 `holding_cost`/货基口径）。
- 影响：API 永远带 `BRIDGE_MISMATCH`；页面"数据质量"首条即报警；⑲ FAIL。
- 修复方向：① 对账改成资产口径（用 `V_end − V_begin − CF` 对 `Σdaily_pnl`/桥接件需同源成本与货基）；或 ② 让 bridge 与资产表共用同一成本/货基原语后再断言；容差在口径对齐前不应写 1 分。

### 4.2 P0-B：长区间残差/估值差异常，`summary=null`（用户目标区间无法回答"为什么亏"）

**现象**（请求区间）：`total_pnl=84,198.47`、`Σfactors=−25,410.83`、`residual=109,609.30`（残差比 **130.18%**）、`summary=null`；`residual_breakdown` 为：

```
unattributed_price_pnl = +445,625.33
valuation_gap          = -336,022.00
interest               = +5.97
```

`unattributed_price_pnl`（未归因标的的“区间损益”）与账户总损益 84,198.47 数量级矛盾：若未归因标的真赚了 445,625，则归因标的/其他应大幅亏损，但五因子合计仅 −25,410.83，剩余 336,022 被塞进 `valuation_gap`（plug）。

**手工抽核 `000422`（coverage 披露 amount=125,893.00）**：

- 期初持仓 15,800 股 × 2025-12-31 收盘 14.21 = **224,518.00 元**；
- 区间净现金流（`Σamount`）= **251,374.70 元**；三费列合计 **411.30 元**；
- 按设计桶公式 `(0 − 224,518.00) + 251,374.70 + 411.30 = **27,268.00 元**`；
- API 披露 **125,893.00 元**（差 98,625.00）。

→ 桶金额/覆盖率清单的实现与设计公式不一致（或 `_mirror_view` 的期初持仓/估值快照有误）。**在修复前，`residual`、`valuation_gap`、`coverage.unattributed[].amount` 均不可作为验收依据。**
- 代码位置：`backend/packages/review/src/stock_review/application/attribution.py:237-355`（`_mirror_view`）、`.../attribution/residual.py:100-180`（桶公式）、`.../application/attribution.py:891-976`（gap/阈值/`RESIDUAL_LARGE`）。
- 影响：用户请求的页面 `summary` 为空、无法回答"为什么亏"；acceptance ⑦ 虽然通过（plug），但桶的可解释性不成立。

### 4.3 P0-C：用户可见文案单位错误（分/元混用）

`missing[]` 的 `RESIDUAL_LARGE` 原因直接使用标度整数：

```
无法解释的残差 33602200 分超过阈值 58751 分
```

用户界面应展示 **336,022.00 元 / 58.75 元**（或按当前页统一"元"）；且 `missing[].field` 是 `total_pnl/factors/residual` 原始码值。
- 代码位置：`backend/packages/review/src/stock_review/application/attribution.py:971-976`；页面 `Attribution.tsx:69-71` 原文直出。

### 4.4 环境限制（SKIP，不计缺陷）

- `period=2026-06` blocked：live 板块成分首次同步日晚于 2026-06-30（设计 A-05 预期）。demo 验收请使用 `SECTOR_MODE=replay`（`first_sync_on=2026-06-30`）或经批准的 `classification_lookahead=allow`；本轮记为 **SKIP**。
- `small sample`（n<10）与 `corporate action` 无法在 live 账户构造，依赖 fixture；本轮 **SKIP**。

---

## 5. 可读性 / 可用性优化建议

> 优先级：**P0 = 影响"看懂/信任结论"**；**P1 = 影响效率与误解**；**P2 = 打磨/可访问性/后续**。每条给"现状 → 建议 → 证据"。

### 5.1 P0（本次页面验收必须修）

| # | 现状 | 建议 | 证据 |
|---|---|---|---|
| U-01 | `summary=null` 时"本期结论"只有一个 `—`，无原因 | 后端增加 `summary_na_reason`（或前端根据 `missing/RESIDUAL_LARGE/valuation_gap_ratio` 给固定文案）："本期未给出结论：未归因占比/估值差超过阈值；请先看数据质量"。同时给出可点击的"查看原因/去对账"链接 | 截图 2.1；`Attribution.tsx:88-91` |
| U-02 | 原始码值直出：`asset_daily_mtm`、`amount/total_pnl`、`five_factor`、`unattributed_price_pnl`、`valuation_gap`、`corporate_action_pnl`、`SECTOR_BAR_MISSING`、`stock/pseudo`、`total_pnl/factors/residual` | 后端在响应中给中文 `label`（或前端维护**只读**静态映射表；零口径只禁计算/拼接结论，不禁显示映射）：如"未归因标的价格损益/估值差/公司行动/股票/伪代码"；`missing.field` 映射"总损益/因子金额/残差" | 截图 2.1/2.2；`Attribution.tsx:70,137,147-148,231` |
| U-03 | 数字格式不可读：`0.71875712`、`占构成 0.34945875`、`资金覆盖率 0.99243115`、`残差比 1.30179681`；金额无"元"、无千分位/符号色 | 统一用后端已提供的 `presentation.ratio_pct`（"71.9%"）与 `bar_pct`；新增后端 `share_pct`；金额统一 `¥/元` + tabular-nums + 正负色；残差比 >100% 时加"（残差大于总损益）"提示 | 后端已返回 `presentation.ratio_pct` 但页面未用：`Attribution.tsx:40-41,109-110,117` |
| U-04 | 因子行固定顺序，rank 徽标 `2,1,5,3,4`；"主要来自"（rank=1）不在第一行 | 二选一：① 行按后端 `rank` 排序（后端返回排序后的 `factors[]`，或前端仅按 rank 排序，仍属展示）；② 保留固定顺序但增加"排名"列/按排名加视觉分组。当前注释与实现不一致 | 截图 2.2；`Attribution.tsx:78,93-95` |
| U-05 | 负 `total_pnl` 时 ratio 与 amount 符号**相反**（选股 +30,909.49 / ratio −0.363；板块 −120,877 / ratio +1.419），无解释 | 明确标注分母语义：把 `ratio` 展示为"相对总损益的贡献率（总损益为负时符号相反）"，或默认只展示 `share`（占构成）+金额，把 ratio 放 tooltip/明细；不要让两个相反符号并排出现 | 截图 2.2；`api ratio=amount/total_pnl`（设计 D-08） |
| U-06 | 页头"总损益 84198.47 · **基准 amount/total_pnl**"把 `ratio_basis` 标成"基准"；且未展示 `assumptions.benchmark_id` | 改为"总损益 84,198.47 元"+"占比口径：金额/总损益"；基准单独显示 `benchmark_id`（如"基准 hs300_tr（ETF 代理）"），无基准时显示"未启用基准" | `Attribution.tsx:84-86`；真实响应 `assumptions.benchmark_id` 未用 |
| U-07 | 390px 移动端横向溢出 71px，右侧被裁切；根因是缺失清单长代码串不换行 | `.attr-missing li strong { min-width:0; overflow-wrap:anywhere; }`，或后端把代码拆成数组、前端用可换行 chips；顺带检查 320/375px | `attribution.css:126-129`；CDP offender 实测 |

### 5.2 P1（显著影响理解/效率）

| # | 现状 | 建议 |
|---|---|---|
| U-08 | 残差卡：原始桶名 + 金额并列，缺少"为什么有残差/正负含义"；`残差比` 130% 无解释；大段空白后才是标的清单 | 改为"残差去向"说明：未归因标的（+445,625.33）、公司行动、估值差（−336,022.00）按金额排序，加一句人话（"正数=未归因收益，负数=估值差"）；桶默认折叠明细 |
| U-09 | "资金覆盖率 0.99243115"与"已归因 149/149 段"并列，但 `symbols_attributed=43/107`；用户会误以为 99% 标的已归因 | 标清分母："按资金权重 99.24% / 按标的 43/107 / 按段 149/149"；加 `?` tooltip 解释三口径；`base_basis` 中文 |
| U-10 | 数据质量卡：`field` 码值、`make sync-sectors` 开发命令、缺少"影响哪些数字/严重级别" | 每条给：严重级别（阻断/降级）、影响字段中文名（总损益/因子/残差）、用户可执行动作（"去板块页同步"/"联系管理员"）；开发命令放折叠"技术详情"；按严重度排序（bridge/blocked 在前） |
| U-11 | blocked 页头"数据截至 2026-11-02"（资产最新日）与区间终点 2026-06-30 不符 | 区分"资产数据截至/行情截至/板块截至"三源（`assumptions.data_sources`）；区间 blocked 时显示"区间末端 2026-06-30"，避免误读 |
| U-12 | 总损益只是小号灰字放在"本期结论"右上；页面主信息不是"为什么亏" | 顶栏 KPI 化：`本期亏损 85,127.13 元`大字 + 正负色 + 数据截至；因子卡用固定列（排名/因子/金额/占构成/说明）代替当前的"一行内多个空格分隔值" |
| U-13 | 切换期间时沿用旧数据（`keepPreviousData`）但无"正在更新"提示；加载仅"加载中…" | 加骨架屏/`aria-busy`/右上角刷新提示；期间切换时明确"展示的是上一区间数据"直到新数据到达 |
| U-14 | 自定义区间默认结束日=今天，资产表/板块未覆盖时容易直接 blocked/stale；无快捷区间 | 提供"近 3/6/12 月、今年、上月"快捷项；自定义默认 `to = data_as_of`（或最近完整月）；开始日期 > 结束日期时即时提示 |
| U-15 | 无因子定义/口径说明；"板块配置"与用户说的"板块"、"残差"与 summary 里的"未归属"不一致；无"数据来源"入口 | 每个因子加 ⓘ tooltip（一句定义+数据源+符号方向）；统一术语表（选股/板块/买点/卖点/费用/残差=未归属）；页脚放"口径说明"（definition_version/data_as_of/benchmark/假设） |
| U-16 | 因子行不可点击，无法查看"哪些标的贡献了板块/选股" | P1：点击因子展开 Top 标的/回合贡献（后端 additive `drilldown`），复用 `/rounds` 或新增归因 drilldown；至少给"查看相关回合"链接 |

### 5.3 P2（打磨 / 可访问性 / 后续）

| # | 建议 |
|---|---|
| U-17 | 可访问性：因子条 `aria-hidden` 只有颜色表达幅度；为每行加 `aria-label`（如"选股，贡献 60,518.25 元，占构成 34.9%"）；榜单用 `<dl>/<table>` 语义；检查 muted 12.5px 文本对比度；键盘焦点样式；`status`/缺失项用 `role=status`/`aria-live` |
| U-18 | 视觉：因子行金额/ratio 右对齐成列、数字用 tabular-nums（当前 `num` 类部分有）、统一间距；残差卡较长可折叠；空态/错误态统一插图/文案 |
| U-19 | 测试：更新 `Attribution.test.tsx` 断言为后端百分比串（不是 `0.9886`）、补充 summary=null 空态、rank 排序、移动端溢出（可用 CSS 单测阈值）、blocked 无金额快照；增加 E2E 截图回归 |
| U-20 | 后续能力：本页只有单期，建议加"近 12 个月五因子趋势/同环比"入口（设计 §10.2）；基准/行业暴露（M2）上线前不要显示"基准"误导 |
| U-21 | 文案合规复核：summary 已无裸术语/买卖建议；但 `数据质量` 中 `make sync-sectors` 等开发命令不应对最终用户出现（见 U-10） |

---

## 6. 前端零口径与测试核对

- **零口径**：✅ 因子条宽度直接使用后端 `presentation.bar_pct`，DOM 实测 `--w=34.9%`、`i` 宽度 405.875px（相对轨道 1163px ≈34.9%）；源码无 `Number/parseFloat/toFixed` 等；`eslint` 零口径规则已生效（AGENTS 旧描述"未完成"已过时）。
- **但存在"用错字段"的展示缺陷**：页面显示原始 `ratio`/`share` 小数，而后端已提供 `presentation.ratio_pct`/`bar_pct`；这不是前端算数，但属于**零口径呈现未到位**（U-03）。
- **单测**：`cd frontend/apps/web && pnpm test` → 26 文件 / 124 用例全绿；`Attribution.test.tsx` 3 条通过，但未覆盖本报告 P0/P1 场景（空结论、排序、符号、移动端）。

---

## 7. 修复与复验清单（建议顺序）

### 7.1 后端（P0，修完才能复验）

1. **修桥接对账**（P0-A）：`attribution.py:687-714` 与 `bridge.py`/`assets.py` 成本、货基口径对齐；或改为资产口径独立对账（`Σdaily_pnl` + `V_end−V_begin−CF` 同源）；对齐前把容差/口径写进响应 assumptions，禁止 1 分假失败。
2. **修残差桶/镜像持仓**（P0-B）：复核 `_mirror_view` 期初/期末快照与 `residual.py` 桶公式（用 `000422` 手算向量：期初 MV 224,518.00、Σamount 251,374.70、费 411.30 → 27,268.00；当前 API 125,893.00）；补 `000422` 单测/golden。
3. **修用户可见单位**（P0-C）：`RESIDUAL_LARGE` reason 用 `Money` 元格式；`missing[].field` 提供 display label（additive 字段，不破坏契约）。
4. **空结论原因**（P0）：响应增加 `summary_na_reason`（或 `summary=null` 时附 `summary_na_reasons[]`），供前端展示；不要把原因只放在 `missing[]` 里让前端猜。
5. 契约 additive：`factors[].presentation.share_pct`；`residual_breakdown` 每桶 `label`；`missing[].severity`/`field_label`；`assumptions.data_sources` 已在响应里，前端可直接展示。
6. 复验：`period=2026-01-01..2026-10-31` 应**无 `BRIDGE_MISMATCH`/`RESIDUAL_LARGE`**（或 residual 在阈值内且有结论），`summary` 非空；`period=2026-10` 同样；页面"数据质量"为空或仅剩真实可解释项。

### 7.2 前端（P0/P1）

1. 空结论态（U-01）：渲染 `summary_na_reason` + "去数据质量"锚点。
2. 百分比/单位（U-03）：用 `presentation.ratio_pct`、新增 `share_pct`；金额 `¥`/元 + 右对齐；覆盖率 %。
3. 排序（U-04）：按 `rank` 渲染顺序或增加"排名"列（与后端确认排序契约）。
4. 页头（U-06）：分离"总损益/占比口径/基准"；显示 `benchmark_id`。
5. 原始码值映射（U-02）：后端 label 优先；前端静态映射仅作兜底。
6. 移动端（U-07）：缺失清单 `overflow-wrap:anywhere` + chips；在 320/375/390/768 复测无横向滚动。
7. 残差/KPI/缺失数据质量重排（U-08~U-11）。
8. 测试（U-19）同步更新，避免把 raw decimal 写进断言。

### 7.3 复验收标准（建议）

- 同一 URL：页面无 `—` 空结论；`数据质量` 首条不再是 `BRIDGE_MISMATCH`；`RESIDUAL_LARGE` 若存在，必须给用户可理解的原因和"未归因金额/占比"。
- 重跑本报告 §3 矩阵：⑲ 转 PASS；④⑮⑱ 由 fixture 套件覆盖（pytest）；其余保持 PASS。
- 移动端 390px：`scrollWidth == clientWidth`（或 ≤1px 误差）；缺失清单长代码串可换行。
- 页面单测更新后 `pnpm test` 仍全绿，并新增 4 条以上针对本报告 P0/P1 的断言。

---

## 8. 复现与证据

| 证据 | 路径/命令 |
|---|---|
| 原始响应 | `var/acceptance-p208/attr-2026-01-01..2026-10-31.json`（gitignored） |
| API 验收脚本输出 | `python3 var/acceptance-p208/api_checks.py`（20 项 live smoke：18 PASS / 2 FAIL；⑮ 的小样本在本文按 SKIP 裁决，⑲ 为真实 FAIL；§3 为设计 19 条矩阵） |
| 桌面截图（请求区间） | [desktop-requested.png](2026-11-02-attribution-acceptance/desktop-requested.png) |
| 移动截图 | [mobile-requested.png](2026-11-02-attribution-acceptance/mobile-requested.png) |
| 健康态截图 | [healthy-2026-10.png](2026-11-02-attribution-acceptance/healthy-2026-10.png) |
| blocked 截图 | [blocked-2026-06.png](2026-11-02-attribution-acceptance/blocked-2026-06.png) |
| 渲染/DOM 采集脚本 | `var/acceptance-p208/capture.mjs`（Chrome CDP；1440/390 两档，含 overflow 检测） |
| 桥接/资产核对 | `GET /api/v1/reconciliation/bridge?period=2026-10`、`GET /api/v1/assets?from=…`；手算见 §4.1 |
| 手工桶核对 | `psql`：`ledger.trade`（000422 窗口 Σamount/fees）、`market.daily_bar`（2025-12-31/2026-10-30 close） |
| 前端单测 | `cd frontend/apps/web && pnpm test` → 124/124 PASS |

### 8.1 验收范围声明（SKIP）

- 仅 Chrome headless；未覆盖 Safari/Firefox/真实移动设备/屏幕阅读器。
- 未跑 `make test-p2`、`make lint`、`make api-check`、`make verify-p2`（不存在/发版门禁，不属于本次页面验收）；实现已提交 `4ac7f75`，但工作区尚有 3 个未提交文件（见 §1），正式验收前建议 `git status` 干净并锁定 commit。
- 2026-06 blocked 为 live 板块历史限制（设计 A-05），本报告记为环境 SKIP；demo 验收需 replay 或 look-ahead 批准。

---

## 9. 验收员结论

- **页面壳与状态机**：通过（可访问、blocked 不泄露金额、零口径、错误/期间切换可用）。
- **功能与数据正确性**：**不通过**——桥接对账全区间 FAIL；请求区间残差/估值差异常导致无结论；桶金额与手算不一致。
- **可读性/可用性**：**不通过**——7 项 P0 + 多项 P1（见 §5），核心是"看不懂/不信任/移动端被裁切"。
- **建议处置**：按 §7 修 P0 后复验；页面层建议先做 U-01/U-02/U-03/U-04/U-06/U-07（低风险、收益最大），再做残差与数据质量的语义化重排。
