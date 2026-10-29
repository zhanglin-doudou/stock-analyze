# 账单解析与估值口径优化（货币基金 / 债券行情 / 零金额信息行 / 可转债）设计文档

> 状态：**v1.0 — 已人工批准并实现**（门禁全绿；实现记录见 [Implementation-Log](../reviews/2026-10-29-bill-parsing-and-valuation-caliber-Implementation-Log.md)）
> 日期：2026-10-29 ｜ 作者：AI（Team Lead）
> 说明：本文由两篇同日设计合并而成，解决同一主线问题——**真实账单导入后的噪音行与估值断链**：
> - **第一篇**：货币基金余额口径 + 债券行情/买入价兜底（原 `2026-10-29-money-fund-and-bond-market-data.md`）
> - **第二篇**：零金额信息行隔离 + 可转债数量口径与跨代码回合匹配（原 `2026-10-29-subscription-isolation-and-cbond-rounds.md`）
>
> 关联：[2026-10-22-live-market-data-source.md](2026-10-22-live-market-data-source.md)、[2026-10-25-asset-daily-table.md](2026-10-25-asset-daily-table.md)、[2026-10-28-dashboard.md](2026-10-28-dashboard.md)

---

# 第一篇 货币基金余额口径 + 债券行情/买入价兜底

> 需求来源：用户原始诉求——
> 1. 货币基金不能按行情估值。在 `trade` 表新增一个字段存放「货币基金余额」；**货币基金余额 + 现金余额 = 资产余额**，页面显示与所有计算统一用「资产余额」。
> 2. 债券先查 akshare 是否有可用接口；有则按行情估值，取不到则**每日资产按买入价构建**。

## 1. 决策摘要

| # | 决策点 | 推荐方案 | 备选 | 影响 |
|---|---|---|---|---|
| D1 | 货币基金余额存哪 | **`ledger.trade` 新增 `money_fund_balance_units`（BigInteger，可空）**，语义「该行发生后该标的的货币基金余额（元，scale=2）」 | 独立余额表 | 与 `cash_balance_units` 同构，天然支持人工修正、批次撤销、增量脏查询 |
| D2 | 如何判定「货币基金行」 | 解析期按**证券名称含「货币」**识别（归一化后）；行上 `money_fund_balance_units` 非空即视为货基行 | 新增 `security_type` 列 / 代码区段表 | 单列兼作标记，不额外加列；名称含「货币」是券商全称的稳定特征（fixtures 实测 3/3 命中） |
| D3 | 货币基金余额怎么算 | 解析期按**份额累计 × 单位净值（1.0000）**逐行推演；若账单含「基金持仓」区块则用其 `参考市值` 作**期末锚点反推**；无锚点则期初按 0 并在 `assumptions` 留痕 | 只用累计份额不做锚点 | 无锚点时部分月份导入会出现负余额，需假设留痕（见 §5.4） |
| D4 | 资产口径 | `total_assets = cash_units + money_fund_units + market_value_units`；货基**不计入持仓/市值/成本**，不再请求行情 | 货基按 1.0000 价当普通持仓 | 货基彻底脱离行情依赖，资产/净值不再因货基 blocked |
| D5 | 落库资产表 | `review.asset_daily` 新增 `money_fund_units`（BigInteger NOT NULL DEFAULT 0）；`ASSET_DEFINITION_VERSION` 1.0 → **1.1**（自动触发全量重建） | 把货基并入 `cash_units` | 现金/货基/市值三分，可解释；前端可展示「货币基金」列 |
| D6 | 展示口径 | Dashboard KPI「现金余额」→ **「资产余额」（= 现金 + 货币基金）**；资产表新增「货币基金」列，`总资产` 口径不变 | 保持现金口径另加说明 | 与用户「显示余额统一用资产余额」一致 |
| D7 | 债券行情源 | 复用 akshare：`bond_zh_hs_daily`（国债/企业债/公司债）+ `bond_zh_hs_cov_daily`（可转债）；无起止参数 → 全量拉取后本地过滤（同 `fund_etf_hist_sina` 模式） | 不做债券行情，一律买入价 | 已实测 8 个代码可用（§6.1） |
| D8 | 债券价格换算 | akshare 收盘为**净价（元/百元面值）**；市值 = `qty_units × close_units ÷ 100 ÷ 1e8`。券商对账单「成交数量」单位（张/手）先用**成交金额反推系数**（∈{1,10,100}，误差 >2% 则回落 100 并告警） | 硬编码 ÷100 | 防止 100 倍估值错误（手/张口径差异） |
| D9 | 债券买价兜底 | 行情取不到（`not_supported` / `no_data` / `unavailable`）的**债券**标的：按 `cost_basis`（含费买入成本）估值，当日标 `degraded`，原因「债券按买入价估值（无行情：代码）」；非债券缺行情仍 `blocked` | 债券也 blocked | 满足「取不到就按买入价」，同时保留可解释状态 |
| D10 | 货基超卖与利息口径（2026-10-29 用户确认） | **货基允许超卖**：红利发放行保持隔离（不进流水），赎回份额略多于已入账申购的**超出部分 = 累计利息**（零成本，卖款全额计收益）；**全额超卖**（账单期前本金赎回，无成本信息）静默跳过、不计损益。无锚点时货基期初按「-min(累计净额)」反推下界（赎回行自证期初持仓），余额全程 ≥ 0 | 红利发放解析入账 / 货基整体退出 FIFO | 消除「货基赎回必超卖 data_error」与「负余额全表 degraded」；回合 PnL 正确归属利息 |

## 2. 现状与问题

### 2.1 货币基金（实测与代码事实）

- 货币基金**没有可估值净值**：`ak.fund_money_fund_info_em("000009")` 只返回 `净值日期/每万份收益/7日年化收益率/申购状态/赎回状态`；单位净值恒为 1.0000，收益以万份收益累计。
- 账单里货基申赎是**普通明细行**（fixtures `华泰账单_货基_mock.xlsx`）：
  `940018 华泰紫金天天发货币 / 申购确认 / 赎回确认 / 红利发放`，`成交均价=1.000`，`资金余额` 是**现金余额**。
- 导入链路已能把货基行分类为 `buy/sell`（`SYSTEM_RULES.sys_fund_buy/sys_redeem`），并落 `ledger.trade`。
- 但估值链路把它们当**普通持仓**：`holding_delta` 累加份额 → `total_market_value` 要行情 → 940018/004262 等不在支持范围 → **整日 blocked**（资产表、净值全断）。
- 真实对账单（`华泰账单_真实节选_mock.xlsx`）末尾有 `基金持仓` 区块：
  `940018 华泰紫金天天发货币 / 基金份额 51859.86 / 单位净值 1.00000000 / 参考市值 51859.86`。
  解析器目前在「合计」行处终止（`parsers/huatai.py` L394-399），**该区块未被读取**——这是货币基金余额唯一的权威锚点来源。

### 2.2 债券

- ledger 当时**完全没有债券概念**（`grep 债券|bond` 仅命中 no matches）。
- `market_live._classify_symbol` 只认 A 股与场内基金，债券代码一律 `NOT_FOUND + not_supported`。
- **akshare 实测可用**（2026-10-29，绕过系统代理）：

| 接口 | 样例 | 结果 |
|---|---|---|
| `bond_zh_hs_daily(symbol="sh019547")` | 沪国债 | 最新 2026-10-28 收盘 120.905 ✅ |
| `bond_zh_hs_daily(symbol="sz100303")` | 深国债（2023-04 到期） | 历史保留 ✅ |
| `bond_zh_hs_daily(symbol="sh122317")` / `sz112172` | 企业债/公司债 | ✅ |
| `bond_zh_hs_daily(symbol="sh010107")` | 老国债 | ✅ |
| `bond_zh_hs_cov_daily(symbol="sh113050")` / `sh110038` / `sz128136` | 可转债 | ✅ |
| 不存在的代码（如 `sz123456`） | — | 抛 `KeyError: 'date'` → 需映射为空结果 |

- 两个接口**都没有 start/end 参数**（全量返回，本地按区间过滤）；已到期/退市债券仍有历史数据。

## 3. 目标 / 非目标

**目标**
1. 货币基金余额成为**独立、可修正、可解释**的账户资产项，`资产余额 = 现金 + 货币基金`。
2. 资产表、净值、Dashboard、指标计算统一使用「资产余额」；货基不再依赖行情。
3. 债券标的可被行情源识别并按 `bond_zh_hs_daily` / `bond_zh_hs_cov_daily` 取价；取不到时按买入成本兜底且**不阻断**。
4. 迁移、契约、前端、测试全覆盖，门禁全绿。

**非目标**
- 货币基金「万份收益/7日年化」的收益计算（只按份额×1.0000 计余额，收益体现为份额增加）。
- 债券应计利息/全价（v1 只用 akshare 净价）。
- 债券行情的多源交叉验证（akshare 仅此两接口，无第二源）。
- 归因/月报/报告页等二期未完成功能的口径扩展。

## 4. 关键约束

| 约束 | 要求 |
|---|---|
| 禁止 float | 新列一律 `BigInteger` 标度整数（金额 scale=2），边界定点字符串 |
| 改表先评审 | 本文档评审通过后才动迁移（项目硬规则） |
| 分层 | `market_live` 属 platform 层，不得反向依赖 ledger/review；债券分类放 `stock_platform.symbols`，review 与 market 共用 |
| Domain 禁 wall-clock | 解析/推演时间来自注入 `Clock`（既有实现已满足） |
| 迁移版本 | 新增 `0008_...`，bump `platform.schema_version` + `stock_api/version.py::SCHEMA_VERSION` |
| 契约先行 | 新增/变更字段先改 API-Catalog → `make api-snapshot` → `make api-check` → `make sdk` |
| 零伪造 | 无锚点时只写假设与告警，不编造余额 |

## 5. 数据模型

### 5.1 `ledger.trade` 新增列（迁移 0008）

| 列 | 类型 | 空 | 单位/口径 | 说明 |
|---|---|---|---|---|
| `money_fund_balance_units` | `BigInteger` | 是 | 金额 scale=2 | 该行**发生后**该标的（货币基金）份额余额。`NULL` = 非货币基金行；非空即货基行标记 |

- 与 `cash_balance_units` 同构，不加 CHECK（允许负余额，见 §6.4）。
- 索引：无需新增（查询按 `user_id + trade_date` 已有索引）。
- 回滚：`ALTER TABLE ledger.trade DROP COLUMN money_fund_balance_units`。

### 5.2 `review.asset_daily` 新增列（同一迁移）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| `money_fund_units` | `BigInteger` | 否 | `0` | 当日货币基金余额合计（Σ 各货基标的余额） |

- `total_assets_units = cash_units + money_fund_units + market_value_units`。
- `blocked` 行仍全 0（与现口径一致）。
- `ASSET_DEFINITION_VERSION` 1.0 → **1.1**：`AssetBuildState.definition_version` 不同即触发全量重建（既有机制）。

### 5.3 迁移顺序

1. `upgrade`: `ALTER TABLE ledger.trade ADD COLUMN ...`；`ALTER TABLE review.asset_daily ADD COLUMN ... DEFAULT 0 NOT NULL`；`UPDATE platform.schema_version SET version='0008_money_fund_bond'`。
2. `downgrade`: 反向 DDL + 版本号回退 `0007_review_metrics`。
3. `make migrate-check` 验证 ORM 与迁移无漂移。

## 6. 货币基金口径与导入规则

### 6.1 识别（解析期）

`is_money_fund(name)`: `normalize_summary(security_name)`（去换行/空白）包含 **「货币」**。

- fixtures 实测：`华泰紫金天天发货币`、`招商招禧宝货币B`、`南方收益宝货币B` 全部命中。
- 补充口径（2026-10-29 用户确认）：**买/卖方向、成交价格 = 1.000000、佣金/印花税/其他费全为 0** 的行同样识别为货基行（应对全称不含「货币」的券商别名）；`quantity_units != 0` 用于排除转托管确认等零份额噪音行。同一代码任一行命中，整只标的按货基余额口径处理。
- 名称不含「货币」但有真实行情且价格不为 1 的场内基金（如 ETF），仍按普通基金估值。
- 归一化复用 `parsers/columns.py::normalize_summary`，避免折行导致漏判。

### 6.2 余额推演（解析期，纯函数）

对全账单按 `(trade_date, line_no)` 稳定序逐行：

```
delta = +quantity_units  （side=buy/bonus/unknown 且 amount>=0 的份额增加，如「红利发放」）
delta = -quantity_units  （side=sell）
balance_after(code) += delta
row.money_fund_balance_units = balance_after(code) × price_units(1.0000) → scale=2
```

- `红利发放`（`side=unknown`、`amount=0`、`quantity>0`）按**份额增加**处理（红利再投）。
- 同一账单内多个货基标的各自独立推演（`balance_after: dict[code, int]`）。

### 6.3 期末锚点（账单「基金持仓」区块）

- 解析器在「合计」行后**新增可选区块解析**：识别 `基金代码 / 基金名称 / 基金份额 / 单位净值 / 参考市值` 表头，读出货基行的 `参考市值`（缺失时用 `份额 × 单位净值`）。
- 锚点日期取该账单**最大流水日期**（区块无独立日期列）。
- 反推：`balance_before(最后一行) = 锚点 − 最后一行 delta`，向前逐行回推；同时从期初正向推演校验，二者不一致（差额 > 0.01 元）→ 以**锚点反推值**为准并写 `WarningCode`（新增枚举值或复用 assumptions 计数）。
- 无「基金持仓」区块：期初余额按 0，写 `assumptions["money_fund_opening_zero"] = [code...]`。

### 6.4 无锚点/部分导入的处理（2026-10-29 D10 修订）

- 无锚点但账单内出现净赎回（期初 0 推演余额会为负）：期初按 **「-min(累计净额)」反推下界**——赎回行本身就是期初持仓的证据（不可能赎回没有的份额），这是账单自证的推断而非伪造；写 `assumptions["money_fund_opening_inferred"]`。余额全程 ≥ 0，不再触发降级。
- 无锚点且余额天然非负（如只有申购）：期初按 0，写 `assumptions["money_fund_opening_zero"]`。
- 有锚点但反推后余额仍为负（数据矛盾）：**不钳 0、不伪造**；该日行标 `degraded` + 原因「货币基金期初余额未知（无基金持仓锚点）」。
- 用户可通过 `PATCH /trades/{id}` 手工修正该行 `money_fund_balance`（新增可编辑字段），修正后重建资产表即恢复。

### 6.5 估值推演（review 层，assets / nav / bridge 共用）

改造 `application/valuation.py`：

1. 新增 `is_money_fund_row(trade)`：`trade.money_fund_balance_units is not None`。
2. `holding_delta` 对货基行返回 0（不进持仓/成本/市值）；新增 `money_fund_balance(trade) -> int`。
3. `assets.compute_daily_valuations` / `nav` 逐日状态新增 `money_fund_units`：
   - 货基行：`money_fund_units`（按 code 分桶）覆盖为该行余额（同日多行以稳定序最后一行为准，与 `cash_balance` 覆盖语义一致）。
   - `total_assets_units = cash + money_fund + market_value`。
   - `prev_total` / `daily_pnl` / `daily_return` 口径不变（分母仍是资产余额）。
4. 期初锚点：新增 `_initial_money_fund(trades, period_start)`，语义对齐 `_initial_cash`：
   - 区间前最后一条货基余额行直接取；否则用区间内第一条货基余额行反推（余额 − 该行 `amount` 与后续流水的份额增量）。
   - 无任何货基行 → 0，不产生假设。
5. `bridge_for_period`：恒等式仍是**现金口径**（期初现金 + 现金流 + 损益 = 期末现金），货基申赎作为买入/卖出参与 FIFO 与成本，保持现状；文档中明确「桥接不看资产余额」。

### 6.6 对账（reconcile）

- 不变：余额链只校验 `cash_balance`（现金）。
- 货基余额不参与对账（账单无逐行货基余额列可比）。

## 7. 债券行情与买入价兜底

### 7.1 标的分类（`stock_platform/symbols.py`，新增小模块）

单一真相来源，`market_live` 与 `review` 共用：

```python
# 债券代码前缀（6 位）
_CONVERTIBLE_PREFIXES = {"110","111","113","118","123","125","126","127","128","132"}
_BOND_OTHER_PREFIXES  = {"010","019","100","101","102","112","122","124","136","188"}
# 交易所：沪 010/019/110/111/113/118/122/124/132/136/188；深 100/101/102/112/123/125/126/127/128
```

- `classify_symbol(symbol)` → `"stock" | "fund" | "bond" | None`（A 股/场内基金前缀保持不变）。
- `is_bond(symbol)`、`bond_exchange(symbol)`（`sh`/`sz`）。
- 未知/冲突前缀 → `None`（保持 `not_supported`），不猜测。

### 7.2 取数路由（`market_live`）

- `validate_symbols` 放行债券；`_fetch_units` 新增分支：
  - 可转债 → `bond_zh_hs_cov_daily(symbol=f"{exchange}{code}")`
  - 其余债券 → `bond_zh_hs_daily(symbol=f"{exchange}{code}")`
- 两接口**无 start/end**：全量返回后在本地按 `[start, end]` 过滤（复用 `_parse_rows` 的 `date/close` 英文字段；`returns` 在全序列上派生再过滤）。
- `KeyError('date')` / `IndexError` / 全空 → 返回 `([], None)`（负缓存，`no_data`）。
- 瞬时故障（超时/连接/JSON）→ `_TransientMarketError`，不重试第二源（无第二源）。
- 债券收盘必须 > 0（`_parse_rows` 已 fail-closed）。

### 7.3 价格口径与换算（关键风险项）

- akshare 债券收盘是**净价，单位元/百元面值**（如 120.905）。
- 市值公式：`qty_units × close_units ÷ 100 ÷ 1e8`（金额 scale=2）。
- 对账单「成交数量」单位可能是 **张（100 元面值/张）** 或 **手（10 张）**，不可硬编码。用**成交金额反推**：
  ```
  implied = |amount_units| × 1e8 ÷ (quantity_units × price_units)   # 应为 1 / 10 / 100 之一
  取最近档；残差 > 2% → 回落 100，写 assumptions["bond_face_divisor_fallback"]=[code]
  ```
- 推导结果仅用于估值换算（不落库、不改 trade 数据）。
- **后续修正（见第二篇 D2）**：可转债改为固定 `×10` 口径，不再走反推。

### 7.4 买入价兜底（`application/valuation.py` + assets/nav）

- `total_market_value` 返回 `missing`；对每个 missing 标的：
  - `is_bond(symbol)` → 计入 `bond_cost_units[symbol] = cost_basis.get(symbol, 0)`，**不再 blocked**；
  - 非债券 → 仍 blocked。
- 当日若有任一债券走兜底：`status=degraded`，`status_reason` 追加「债券按买入价估值（无行情：019547）」。
- 与既有「行情未覆盖（as_of 之后）」原因合并展示（`_row_status_reason` 统一去重拼接）。
- 净值（nav）同理：缺行情的债券用成本抵扣，当日不计入 blocked 点。

## 8. 影响面清单（第一篇）

| 层 | 文件 | 变更 |
|---|---|---|
| 迁移 | `backend/migrations/versions/0008_money_fund_bond.py` | 两列 + schema_version |
| ledger ORM | `packages/ledger/.../models.py` | `Trade.money_fund_balance_units` |
| ledger 解析 | `parsers/huatai.py` / `parsers/columns.py` | 货基识别、余额推演、`基金持仓`区块解析、`ParsedRow` 新字段 |
| ledger 仓储 | `repositories/trades.py` | `TradeRow`/`TradeUpsert`/`_EDITABLE_FIELDS` 新字段 |
| ledger 应用 | `application/trades.py` / `imports.py` / `preview.py` / `parsers/dedupe.py` | DTO、预览行、去重签名（确认不含新字段） |
| platform | `stock_platform/symbols.py`（新）、`market_live.py` | 分类函数、债券路由、validate_symbols |
| review | `application/valuation.py` / `assets.py` / `nav.py` / `bridge.py` / `models.py` / `repositories/assets.py` | 货基余额、债券兜底、asset_daily 新列 |
| api | `schemas.py` / `routers/assets.py` / `routers/dashboard.py` / `routers/trades.py` / `routers/imports.py` / `openapi_meta.py` | API-081 行加 `money_fund`；Dashboard `cash` 语义/新字段；Trade 出入参 |
| 契约 | `docs/architecture/v2/API-Catalog.md`、`contracts/openapi.json`、前端生成类型 | `make api-snapshot` / `make api-check` / `make sdk` |
| 前端 | `apps/web/src/pages/Assets.tsx`、`Dashboard.tsx`、`Trades.tsx` | 资产表「货币基金」列；Dashboard KPI 改名；流水页展示/编辑货基余额 |
| 文档 | 本文档、`docs/architecture/2026-10-25-asset-daily-table.md` §5、`2026-10-28-dashboard.md` | 口径更新 |

**指标影响**：所有以 `asset_daily.total_assets_units` 为输入的指标（回撤族、波动、Dashboard）自动切换到「资产余额」口径，无需逐指标改动。

## 9. 兼容与回滚（第一篇）

- 旧数据：新列 `NULL`/`0`，旧账导入的货基行**不会自动获得余额**；需重新导入相关账单（或手工补录）后重建资产表。
- 资产表：`definition_version` 升级 → 下次构建自动全量重算。
- 回滚：迁移 downgrade + 回退 `ASSET_DEFINITION_VERSION`；货基行将退回「缺行情 blocked」旧行为（可接受）。

## 10. 测试方案（第一篇）

### 10.1 ledger 解析（unit）
1. 货基 fixtures（`华泰账单_货基_mock.xlsx`）：6 行中 4 行货基行 `money_fund_balance_units` 非空，且逐行余额 = 份额累计 × 1.0000（含「红利发放」份额增加）。
2. 真实节选（`华泰账单_真实节选_mock.xlsx`）：`基金持仓` 区块被解析，940018 期末余额锚定为 **51859.86**，赎回行反推期初 100999.18。
3. 非货基行（股票/转账）新列必须为 `NULL`。
4. 无 `基金持仓` 区块 + 首行赎回 → 余额为负，`assumptions["money_fund_opening_zero"]` 存在，不抛异常。
5. 折行名称（`华泰紫金天\n天发货币`）识别命中。
6. 去重签名（`dedupe.py`）不含新字段（改余额不改变去重键）。

### 10.2 review（unit / integration）
7. 资产推演：货基行**不进** holdings；货基余额使 `total_assets` 等于「现金 + 货基 + 市值」，且**不请求行情**（market 端口断言未被调用该代码）。
8. 余额覆盖：同日两行货基以稳定序最后一行为准。
9. `_initial_money_fund`：区间前锚点 / 区间内反推 / 无货基三种分支。
10. 债券兜底：构造债券持仓 + 行情空 → 当日 `degraded`、`market_value = cost_basis`、总资产不为 0；非债券缺行情仍 `blocked`。
11. 债券行情：`_FakeAkshare` 提供 `bond_zh_hs_daily` / `bond_zh_hs_cov_daily` 返回 `date/open/high/low/close/volume`，断言路由与本地过滤、`KeyError('date')` → 空。
12. 净值同值回归：货基 + 债券场景下 `nav_daily.total_value` 与费用/现金流口径一致。

### 10.3 API / 契约
13. API-081 资产行含 `money_fund`（blocked 为 `null`），`make api-check` 通过。
14. Dashboard `cash` 返回资产余额口径；`money_fund` 字段存在。
15. Trade 出入参支持 `money_fund_balance`（字符串定点），PATCH 修正写入 `trade_history`。
16. `validate_symbols` 对债券代码放行；未知 6 位代码仍 `NOT_FOUND + not_supported`。

### 10.4 门禁
`make lint` → `make typecheck` → `make test-p2` → `make test-metrics` → `make api-snapshot` → `make api-check` → `make sdk` →（DB 可用时）`make verify-p1`（使用 `.env` 的 `STOCK_DB_PORT`，本机为 5432 且已迁移 `0008`；docker 5433 是未迁移的空库，不要覆盖端口）。

---

# 第二篇 零金额信息行隔离 + 可转债数量口径与跨代码回合匹配

> 动机：真实账单验证发现两类噪音/断链——
> 1. 「申购中签 / 新股入账 / 托管转出」等**零金额信息行**（`side=unknown`、`amount=0`）进入 `ledger.trade`，形成无意义持仓（如 001237 中签通知行使持仓形成日早于上市日 4 天、754019 通知行重复计数）；
> 2. 可转债**发债代码（如 754019 曙26发债）与上市代码（如 113xxx 曙26转债）不同**，按 `security_code` 分组的 FIFO 回合永远配不成对，申购代码持仓永不平仓、资产表持续 blocked。

## 11. 需求确认（用户原话）

1. 摘要包含「申购中签、托管转出、新股入账」等**没有发生金额**的账单 → 隔离。
2. 可转债：数量 1 代表 10 张，成交均价是单张金额；发债时证券代码 ≠ 上市时代码；名称特征发债时「xx发债」、上市时「xx转债」；提取证券名称做回合匹配。

## 12. 现状（第二篇）

| 层 | 现状 | 位置 |
|---|---|---|
| 解析 | `side=unknown` 行不隔离，正常进 trades；仅「五类金额/余额全 0」按 `INVALID_AMOUNT` 隔离 | `parsers/huatai.py::_build_candidate` |
| 隔离 | `QuarantineCandidate(raw, line_no, reason_code)`，落 `ledger.quarantine_row` | `parsers/huatai.py` L51-L58 |
| 回合 | FIFO 引擎按 `security_code` 分组配对；回合 ID = `sha256(user_id\|security_code\|key)` | `stock_core/fifo/engine.py`、`stock_review/application/rebuild.py` |
| 事件映射 | `trades_to_events` 按 `category` → `EventKind`（含 `new_stock` → NEW_STOCK） | `rebuild.py::_kind_for` |
| 债券市值 | `qty × close ÷ 1e8 ÷ divisor`，`divisor ∈ {1,10,100}` 由成交金额反推 | `valuation.py::bond_face_divisors` |
| 持仓/资产 | 按 `security_code` 聚合；申购代码无行情 → blocked | `assets.py`、`nav.py` |

实测账单（本地库 2026-10-29）：
- 001237 惠康科技：05-14/18/19 三行 `side=unknown、amount=0`（中签/入账通知），05-18 另有真实缴款 buy -26630 元；上市日 05-22。
- 754019 曙26发债：07-16/20 两行 `unknown、amount=0` + 07-20 真实中签缴款 buy 1（手）@100 = -1000 元；该代码永无行情。

## 13. 设计决策（第二篇）

### D1 零金额信息行隔离（解析层）

- 判定：`normalize_summary(摘要)` 命中关键词 **且** `amount_units == 0` **且** `cash_balance_units is None`（无任何资金意义的纯通知行）。
- 关键词清单（初版，可配置前常量）：`申购中签`、`新股入账`、`托管转出`、`托管转入`。
  - 「托管转入」与「转出」对称，同属无金额过户通知；如评审认为转入应保留，可删。
- 隔离原因码：新增 `ZERO_AMOUNT_INFO`（`reason_code` 为 text 列，**无需迁移**）。
- 被隔离行不进 trades、不参与持仓/回合/估值；隔离区保留原始行可审计、可恢复（现有 quarantine 机制）。
- 边界：同类摘要但**有金额**的行（如真实缴款 buy）不受影响；`side=unknown` 但有金额的行维持现有口径。

### D2 可转债数量口径：固定 1 手 = 10 张

- 可转债（`is_convertible_bond`）的 `quantity_units` 单位是**手**，`price_units` 是**单张价格**（元/张，行情收盘同为元/张）。
- 市值公式改为：可转债固定 `市值 = qty × close × 10 ÷ 1e8`（qty 单位**手**、close 单位**元/张**，1 手 = 10 张 → 1 手 @130 元/张 = 1300 元），**不再走成交金额反推**（`bond_face_divisors` 对可转债跳过）；其它债券（国债/企业债/公司债）保留现有反推逻辑（`÷ divisor`）。
  > 注：初稿曾误写为「÷10」；按 1 手@130 应得 1300 元校验，正确方向是 **×10**。实现以 `valuation.py::bond_value_units` 为准。
- 一致性校验：解析侧断言 `|qty × 10 × price − |amount|| ≤ 容差`，不符时按现有 `WARN` 留痕（不阻断）。

### D3 发债代码 → 上市代码的名称归并（回合 + 持仓 + 估值）

- **stem 提取**：名称去空白后，去掉尾部「发债」/「转债」即 stem（`曙26发债` / `曙26转债` → `曙26`）。
- **归并规则**：同一用户账单内，若存在「名称以发债结尾的代码 S」与「stem 相同、以转债结尾的代码 L」（`is_convertible_bond(L)` 为真），则建立别名 `S → L`；L 为 canonical code。
  - 仅当映射**唯一**时生效：同一 stem 对应多个上市代码（历史重名）或发债代码对应不到转债时，不归并，写 `assumptions["bond_alias_unresolved"]`。
  - 归并表在 review 层由 trades 派生（纯函数，可单测），不落库。
- **回合匹配**：`trades_to_events` 归一 `security_code = alias.get(code, code)`，申购缴款（754019 buy）与上市卖出（113xxx sell）进入同一 FIFO 组，正常配对；回合 ID 用 canonical code（确定性不变）；`RoundFill` 仍引用原始 trade_id。
- **持仓/估值**：holdings 与 `_symbols` 同步归一；行情请求只用 canonical 代码（有真实行情）；754019 这类申购代码不再出现在行情请求里，`not_supported` blocked 消失。
- **上市前窗口估值**：中签缴款日到上市首日之间，canonical 代码无行情 → 走既有债券 `cost_basis` 兜底（`degraded` + 「债券按买入价估值」），资产曲线连续。
- 显示：`security_name` 取**最新一行的名称**（上市后为「xx转债」）。

### D4 版本与重建

- 资产口径版本 `ASSET_DEFINITION_VERSION` 1.1 → **1.2**（货基/债券逻辑外的第三次口径变更）。
- 回合：新增 `REBUILD_DEFINITION_VERSION` 常量（或复用既有 rebuild 触发），本次上线后对受影响证券**全量重建**（`rebuild scope=full`）。
- 无数据库迁移（无新列）；隔离规则只影响**新导入**的账单。

### D5 历史数据处理（运维步骤，非代码）

1. 重新导入含上述行的账单（零金额行进隔离区、754019 行仍在 trades 但归并生效）；
2. `rebuild scope=full` 重建回合；
3. 资产表按 1.2 自动全量重建。
- 不重新导入的历史账单：旧数据保持原样（设计内，不 retroactive）。

## 14. 影响面与改动清单（第二篇）

| 层 | 改动 |
|---|---|
| ledger/parser | `huatai.py`：`_is_zero_amount_info_row` + 关键词常量；`_build_candidate` 接入 `ZERO_AMOUNT_INFO`；可转债「qty×10×price ≈ amount」校验 WARN |
| review | `valuation.py`：可转债固定 ×10（`bond_value_units`，`bond_face_divisors` 跳过可转债）；`rebuild.py`：`trades_to_events` 接入别名归一；`assets.py` / `nav.py`：holdings、`_symbols`、行情请求按 canonical 代码；`ASSET_DEFINITION_VERSION = "1.2"` |
| review（新） | `bond_alias.py`：`extract_bond_stem` / `build_bond_alias` 纯函数 |
| API/前端 | 无契约变更（回合/资产出参字段不变，仅数值口径变化）；资产口径版本展示 1.2 |
| 测试 | 解析（隔离/边界）、别名归并（唯一/冲突/无对应）、FIFO 跨代码配对、资产（上市前 degraded/上市后正常）、回归门禁 |

## 15. 测试清单（第二篇验收）

1. 「申购中签/新股入账/托管转出」零金额行 → quarantine，reason `ZERO_AMOUNT_INFO`，不进 trades；
2. 同摘要但 amount ≠ 0 → 正常进 trades；
3. 754019（曙26发债 buy 1手@-1000）+ 113xxx（曙26转债 sell）→ 同一回合，PnL 正确；
4. 可转债市值：1 手 @ 收盘 130 → 市值 1300 元（×10 固定口径，不再反推）；
5. 中签日至上市前：资产 `degraded` 按成本 1000 元，不再 blocked；
6. stem 冲突（同名两只转债）→ 不归并 + assumptions 留痕；
7. 001237 中签通知行隔离后，持仓形成日 = 05-18 缴款日；
8. `make lint / typecheck / test-p2 / test-metrics / verify-p1` 全绿。

---

# 附：开放问题汇总

| # | 问题 | 结论/建议 | 状态 |
|---|---|---|---|
| Q1 | 「资产余额」是否等于 `total_assets`（现金 + 货基 + 持仓市值）？Dashboard「现金余额」KPI 直接改名/改值？ | 是；KPI 显示资产余额，另设「货币基金」小字 | 已确认并实现 |
| Q2 | 货基识别用「名称含『货币』」+「价格 1 且零费用」是否可接受？ | 可接受；手工修正 + 后续名单扩展 | 已确认并实现 |
| Q3 | 债券「买入价」= 含费成本（`cost_basis`）还是纯成交价 × 持仓量？ | 含费成本（与持仓成本同口径） | 已确认并实现 |
| Q4 | 债券数量单位换算是否接受「成交金额反推系数」自动推导？ | 接受，异常告警；**可转债后续改固定 ×10** | 已实现（第二篇 D2 修订） |
| Q5 | 是否解析账单「基金持仓」区块？ | 要（否则部分月份导入余额可能为负） | 已实现 |
| Q6 | 「托管转入」是否也隔离 | 隔离（与转出对称） | 已实现 |
| Q7 | 零金额关键词是否用户可配置 | 本期常量，后续如需再提设置项 | 遗留 |
| Q8 | 名称归并是否覆盖「配债」（股东优先配售） | 本期只处理「发债/转债」名称对，配债留后续 | 遗留 |
