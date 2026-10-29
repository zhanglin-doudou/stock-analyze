# 账单解析与估值口径优化（货基 / 债券 / 零金额信息行 / 可转债）— 实现记录

> 设计：[2026-10-29-bill-parsing-and-valuation-caliber.md](../architecture/2026-10-29-bill-parsing-and-valuation-caliber.md)（已人工批准）
> 日期：2026-10-29
> 迁移：`0008_money_fund_bond`
> 资产口径版本：`ASSET_DEFINITION_VERSION` **1.0 → 1.1（货基/债券）→ 1.2（可转债别名归并）**（均触发资产表全量重建）
> 说明：本文由两篇同日实现记录合并而成，对应设计文档的两篇——
> - **第一篇**：货币基金余额 + 债券行情/买价兜底
> - **第二篇**：零金额信息行隔离 + 可转债口径与跨代码回合匹配

---

# 第一篇 货币基金余额 + 债券行情/买价兜底

## 1. 交付摘要

| 需求 | 落地结果 |
|---|---|
| 货币基金余额存 `trade` 表 | 新增 `ledger.trade.money_fund_balance_units`（BigInteger 可空，金额 scale=2）；非空即「货基行」标记 |
| 资产余额 = 现金 + 货基 | 新增 `review.asset_daily.money_fund_units`；`total_assets_units = cash + money_fund + market_value`；新增只读派生 `balance = cash + money_fund` 供页面显示 |
| 显示与计算统一用资产余额 | Dashboard KPI「现金余额」→「资产余额」；资产表新增「货币基金」「资产余额」列；流水表新增「货币基金余额」列 |
| 债券行情 | `stock_platform.symbols` 分类 + akshare `bond_zh_hs_daily` / `bond_zh_hs_cov_daily` 路由（全量拉取、本地区间过滤） |
| 取不到按买入价 | 债券缺行情按 `cost_basis` 估值，当日 `degraded` + 原因「债券按买入价估值（无行情：代码）」；非债券仍 `blocked` |

## 2. 分层改动清单

### 2.1 contracts / core

- 无新增 DTO；沿用 `Money/Price/Qty` 标度整数与 `QTY_PRICE_SCALE = 10**8`。

### 2.2 platform

| 文件 | 改动 |
|---|---|
| `stock_platform/symbols.py`（新增） | `classify_symbol` / `is_bond` / `is_convertible_bond` / `bond_exchange` / `bond_symbol`；stock/fund/bond/None 四态，A 股与场内基金前缀表从 `market_live` 抽为单一真相来源 |
| `stock_platform/market_live.py` | 债券路由 `_fetch_bond`：可转债 → `bond_zh_hs_cov_daily`，其余 → `bond_zh_hs_daily`；`_SOURCE_COLUMNS["bond"] = ("date","close")`；`validate_symbols` 放行债券；`KeyError('date')`（不存在代码）映射为空结果 |

债券接口无 start/end 参数，全量返回后本地过滤；returns 由收盘序列全量派生后再截取区间。

### 2.3 ledger

| 文件 | 改动 |
|---|---|
| `migrations/versions/0008_money_fund_bond.py`（新增） | `ledger.trade.money_fund_balance_units`；`review.asset_daily.money_fund_units NOT NULL DEFAULT 0`；重建 `ck_asset_daily_total`；stamp `0008_money_fund_bond` |
| `stock_ledger/models.py` | `Trade.money_fund_balance_units` |
| `parsers/huatai.py` | `ParsedRow.money_fund_balance_units`；货基识别 = 名称含「货币」**或**买/卖价格 = 1.000000 且三项费用全 0（`quantity_units != 0`）；逐行余额推演；解析账单末尾「基金持仓」区块作为期末锚点 |
| `parsers/dedupe.py` | 去重签名**不包含** `money_fund_balance_units`（人工修正余额不改变去重键） |
| `application/imports.py` / `preview.py` / `trades.py` / `repositories/trades.py` | 新字段落库、预览回显、PATCH 审计留痕 |

余额推演口径（金额分，与 review 层一致）：

```python
def _money_fund_row_delta(row: ParsedRow) -> int:
    """货基行的余额增量（金额分）：卖出取负，其余（含红利再投）取正。"""
    shares = -row.quantity_units if row.side == Side.SELL.value else row.quantity_units
    return _share_value_units(shares, row.price_units or _ONE_PRICE_UNITS)
```

- 有「基金持仓」锚点：`期初 = 锚点金额 − 账单内净增量`（真实节选实测 51859.86 → 期初 100999.18）。
- 无锚点：`期初 = 0`，写入 `assumptions["money_fund_opening_zero"]`，不伪造余额。
- 同日多行：按 `(trade_date, line_no)` 稳定序累计，最后一行即当日余额。

### 2.4 review

| 文件 | 改动 |
|---|---|
| `application/valuation.py` | `is_money_fund_row` / `money_fund_share_delta` / `initial_money_fund_balances` / `bond_face_divisors` / `BOND_COST_FALLBACK_PREFIX`；`total_market_value` 返回 `(total, missing, bond_fallback)`，债券缺行情按 `cost_basis` 兜底 |
| `application/assets.py` | `ASSET_DEFINITION_VERSION = "1.1"`；`_symbols` 排除货基（不再为其请求行情）；`AssetPoint.money_fund_units`；`total = cash + money_fund + market` |
| `application/nav.py` | 货基期初余额与每日增量进入总资产；债券换算系数随快照传递 |
| `application/dashboard.py` | `DashboardAssetOverview.money_fund_units`，missing/blocked 分支为 `None` |
| `models.py` | `AssetDaily.money_fund_units` + CHECK（`total = cash + money_fund + market`） |

债券换算（设计 D8）：`market_value = qty_units × close_units ÷ 1e8 ÷ divisor`，`divisor ∈ {1,10,100}` 由成交金额反推（残差 ≤2%），推不出回落 100。
**注意**：可转债换算在第二篇改为固定 ×10，不再反推。

### 2.5 api（契约先行）

| 文件 | 改动 |
|---|---|
| `docs/architecture/v2/API-Catalog.md` | API-081 响应新增 `money_fund`、`balance`（`balance = cash + money_fund`）；验收断言 `definition_version == "1.1"` |
| `stock_api/schemas.py` | `TradeOut.money_fund_balance`、`TradePatchIn.money_fund_balance`、`AssetPointOut.money_fund/balance`、`DashboardAssetOverviewOut.money_fund/balance(+display)` |
| `routers/trades.py` | 出参 `money_fund_balance`；PATCH 支持设置/清除（`None` 清除） |
| `routers/assets.py` | `money_fund`；`balance = Money(cash + money_fund)`，blocked → `null` |
| `routers/dashboard.py` | `money_fund`、`balance`、`money_fund_display`、`balance_display` |
| `stock_api/version.py` | `SCHEMA_VERSION = "0008_money_fund_bond"` |
| `contracts/openapi.json` + 前端 SDK | `make api-snapshot` / `make sdk` 重新生成 |

兼容性：新增字段全部可空或带默认值；`definition_version` 变更由既有「版本不同 → 全量重建」机制自动消化。

### 2.6 frontend

| 文件 | 改动 |
|---|---|
| `api-client/src/contract.ts` | `AssertAssetPointFields` 加 `money_fund`/`balance`；`AssertTradeFields` 加 `money_fund_balance` |
| `pages/Assets.tsx` | 新增「货币基金」「资产余额」列；口径说明「资产余额 = 现金 + 货币基金，另加持仓市值」 |
| `pages/Dashboard.tsx` | KPI `kpi-cash` → `kpi-balance`（「资产余额」）；eyebrow 文案同步 |
| `pages/Trades.tsx` | 新增「货币基金余额」列 |
| `Assets.test.tsx` / `Dashboard.test.tsx` | fixture 与断言同步（口径版本 1.1、blocked 字段为 null、KPI id） |

## 3. 实现中发现并修正的问题（第一篇）

1. **货基余额标度混算（严重）**：初版用 `Qty`（scale=4）累加份额、又当作 `Money`（scale=2）落库，余额放大 100 倍。改为解析与 review 两端统一在「金额分」域计算（`Money.from_decimal(Qty × Price)`）。
2. **无锚点时期初被错误反推**：`opening = anchors.get(code, 0) - deltas` 会让期初抵消全部增量、期末恒为 0。修正为「有锚点才反推，否则期初 0」；货基_mock 首行余额恢复为 -116295.38。
3. **债券换算系数公式方向相反**：初版按 `qty × price × divisor ≈ amount × 1e8` 反推，与 `qty × close ÷ 1e8 ÷ divisor` 的口径相反。修正为 `amount × 1e8 × divisor ≈ qty × price`。
4. **货基仍被请求行情**：`assets._symbols` 只按 `holding_delta != 0` 过滤，货基份额仍会进入行情请求。增加 `not is_money_fund_row(trade)` 排除。
5. **端点覆盖门禁常量漂移（既有）**：`gate_endpoint_coverage.py` 期望 84，而 API-Catalog 已含 API-085（85 条）。常量更正为 85。
6. **越权矩阵缺 `/assets/chart`**：补入跨租户列表隔离、401、admin 403 三类覆盖，并加「B 看不到 A、A 自己可见」双向断言。
7. **隔离用例资产行不可见**：`_seed_asset_row` 硬编码 `definition_version='1.0'`，与新版本过滤冲突，改为 `1.1`。
8. **旧用例拿 113001 当「不支持代码」**：113001 是沪市可转债，新分类下已支持，`test_market_cache.py::test_unsupported_symbol_fails_before_network` 不再抛错。改用北交所 `830799`（仍不支持）。
9. **科创板可转债（118 开头）漏分类**：用 akshare `bond_zh_cov()` 全表复核（1066 只），发现 118 段 78 只（如 `118003`）未纳入，会被 `validate_symbols` 判为不支持。已补入 `_CONVERTIBLE_BOND_PREFIXES` 与沪市前缀（实测 `bond_zh_hs_cov_daily("sh118003")` 返回 1062 条），并新增放行与路由用例。

## 4. 测试覆盖（第一篇，对应设计 §10）

| # | 场景 | 用例位置 | 结果 |
|---|---|---|---|
| 1 | 货基_mock 逐行余额（-116295.38 / -9586.85 / -116307.65 / -110639.33）+ assumptions | `backend/tests/unit/ledger/test_parser_huatai.py` | PASS |
| 2 | 真实节选：940018 锚点 51859.86、反推期初 100999.18；202308 余额 10000.00 | 同上 | PASS |
| 3 | 非货基行新列为 NULL | 同上 | PASS |
| 4 | 无锚点 + 首行赎回 → 负余额 + `money_fund_opening_zero`，不抛异常 | 同上 | PASS |
| 5 | 折行名称识别 | 同上（fixtures 回归） | PASS |
| 6 | 去重签名不含新字段 | 同上（`dedupe_key` 断言） | PASS |
| 7 | 货基不进 holdings，计入资产且不请求行情 | `backend/tests/unit/review/test_assets_application.py` | PASS |
| 8 | 同日两行货基以最后一行覆盖 | 同上 | PASS |
| 9 | 期初余额三分支（区间前锚点 / 区间内反推 / 无货基） | `backend/tests/unit/review/test_valuation.py` | PASS |
| 10 | 债券缺行情 → degraded + cost_basis；非债券仍 blocked | `test_valuation.py` + `test_assets_application.py` | PASS |
| 11 | 债券/可转债路由、区间过滤、`KeyError('date')` → 空 | `backend/tests/unit/platform/test_market_live.py` | PASS |
| 12 | 货基 + 债券场景净值口径 | `backend/tests/unit/review/test_nav_application.py` | PASS |
| 13 | API-081 含 `money_fund`（blocked 为 null） | `backend/tests/api/test_api_081_assets.py` | PASS |
| 14 | Dashboard `money_fund` / `balance` 及 display；missing/blocked 为 null | `backend/tests/api/test_dashboard.py` | PASS |
| 15 | Trade 出入参 `money_fund_balance`，PATCH 设置/清除 | `backend/tests/api/test_imports_trades_jobs.py` | PASS |
| 16 | 债券代码放行、未知代码仍 `NOT_FOUND + not_supported` | `test_market_live.py` + API 用例 | PASS |
| 17 | 科创板可转债 `118003` 放行且走 `bond_zh_hs_cov_daily`（`sh118003`） | `test_market_live.py`（`test_star_market_convertible_bond_routes_sh_cov`） | PASS |
| 18 | 名称不含「货币」但价格 1、零费用 → 按货基余额口径（10000.00） | `test_parser_huatai.py::test_money_fund_recognized_by_price_one_and_zero_fees` | PASS |
| 19 | 价格 1 但有佣金 → 不识别为货基 | `test_parser_huatai.py::test_price_one_with_fees_is_not_money_fund` | PASS |
| 20 | 价格 1 零费用卖出 → 余额按份额减少 | `test_parser_huatai.py::test_money_fund_price_one_sell_reduces_balance` | PASS |

另有 `backend/tests/integration/test_assets_incremental.py` 与 `backend/tests/isolation/test_isolation_matrix.py` 同步到 `definition_version == "1.1"`。

## 5. 门禁结果（第一篇）

| 命令 | 结果 |
|---|---|
| `make lint` | 通过 |
| `make typecheck` | 通过（mypy strict + import-linter L1–L5 全 KEPT） |
| `make test-p2` | 通过（含 api-check、契约快照无漂移、端点覆盖 52/85、越权覆盖） |
| `make test-metrics` | 通过（25 份 golden 结构门禁） |
| `make api-snapshot` | 通过（快照已更新） |
| `make api-check` | 通过 |
| `make sdk` | 通过（前端 TS 类型同步） |
| `cd frontend && pnpm -r test` | 100/100 通过 |
| `make verify-p1` | 18/18 通过（使用 `.env` 中的 `STOCK_DB_PORT=5432`；数据库已迁移到 `0008_money_fund_bond`）；单元子集 943 passed |

### 环境备注

设计文档曾写 `STOCK_DB_PORT=5433 make verify-p1`。本机实际有两条库：`.env` 指向的 **5432**（本机 PostgreSQL，已迁移 `0008`）与 docker `sa-dev-db` 对外映射的 **5433**（未迁移空库）。首次按 5433 运行时全部步骤失败；改回 `.env` 默认值后全绿。后续本地验收请直接 `make verify-p1`，不要覆盖端口。

## 6. 回滚方案（第一篇）

1. `alembic downgrade 0007_review_metrics`：删除 `ledger.trade.money_fund_balance_units`、`review.asset_daily.money_fund_units`，恢复旧 CHECK，版本号回退。
2. 将 `ASSET_DEFINITION_VERSION` 改回 `1.0` 并重建资产表（或直接清空 `review.asset_daily` 由其重算）。
3. 前端字段可保留展示（旧 API 会因 `extra="forbid"` 在出参缺字段时报错，因此降级需同时回退前端或保持 API 字段返回 `null`）。

## 7. 遗留与未覆盖（第一篇）

- 债券仅用 akshare 净价，不含应计利息/全价，也无第二数据源交叉验证（设计非目标）。
- 货基识别为「名称含『货币』」+「买/卖价格 1 且零费用」两条口径；仍可能有误判（例如恰以 1.000 成交且零费用的非货基产品），发现后需名单/规则细化（设计 Q2）。
- 规则只在**导入解析**时生效；历史已入库的流水不会自动补写 `money_fund_balance_units`，需重新导入该账单（或按同口径回填）后重建资产表。
- `verify-p2` 整体仍未提供（Period2-14 暂停），二期验收按 `test-p2` + `test-metrics` + 各 gate 执行。

---

# 第二篇 零金额信息行隔离 + 可转债口径与跨代码回合匹配

## 8. 落地内容（对照设计 §13）

| 设计项 | 实现 | 位置 |
|---|---|---|
| D1 零金额信息行隔离 | `amount=0 且 cash_balance IS NULL 且摘要命中关键词` → `_RowError(ZERO_AMOUNT_INFO)`，检查置于 `INVALID_AMOUNT` 之前；关键词：申购中签 / 新股入账 / 托管转出 / 托管转入（`normalize_summary` 折行拼接后匹配） | `parsers/huatai.py` |
| 隔离原因码 | `QuarantineReason.ZERO_AMOUNT_INFO`（text 列，无迁移） | `stock_contracts/enums.py` |
| D2 可转债一致性 WARN | buy/sell 且 qty>0、price>0、amount≠0 的可转债（含「xx发债」名）行：`abs(qty×10×price − abs(amount))` 偏差 >2% → `WarningCode.CBOND_AMOUNT_INCONSISTENT`，计数落 `assumptions["cbond_amount_inconsistent_rows"]`（不阻断） | huatai.py、enums.py |
| D2 可转债市值 ×10 | 新增 `bond_value_units`：可转债 `qty×close×10÷1e8`（qty=手、close=元/张）；`bond_face_divisors` 对 `is_convertible_bond` 跳过反推；`total_market_value` 债券分支改用 `bond_value_units` | `application/valuation.py` |
| D3 名称归并纯函数 | 新建 `bond_alias.py`：`extract_bond_stem`（去空白、去尾部「发债/转债」）、`build_bond_alias`（发债代码 stem 唯一对应上市转债才归并，冲突/无对应进 unresolved）、`canonical_code` | `application/bond_alias.py` |
| D3 回合归一 | `trades_to_events(..., bond_alias)`：事件 `security_code` 归一为 canonical；`rebuild_rounds_for_user` 先 `_load_bond_alias`（全量 distinct 代码+名称派生，unresolved 打 warning 日志）；period 作用域按 canonical 归一后反向扩出别名源代码加载/删除，避免漏载发债买入导致超卖误判 | `application/rebuild.py` |
| D3 资产/净值/桥接归一 | `assets.py`：`compute_daily_valuations` 逐日 holdings 归一（货基分支除外）、`_symbols` 只发上市代码行情请求；`ASSET_DEFINITION_VERSION = "1.2"`；`nav.py`：alias 贯穿 `trades_to_events` / `code_names` / `holding_symbols` / 逐日循环，`_build_holding_snapshots` 用 `bond_value_units`；`bridge.py`：两处子集调用显式传全量派生 alias（否则 period_start 前 fifo_before 键错配） | assets.py / nav.py / bridge.py |

回合显示名无需改动：FIFO 引擎取 `last_sell_security_name`，上市后卖出行名即「xx转债」。

## 9. 对设计文档的修正（第二篇）

- **D2 公式方向**：初稿写「市值 = qty × close ÷ 1e8 **÷10**」，用真实数字校验错误（1 手@130 应 1300 元，÷10 得 13 元）。实现为 **×10**（手→张），设计文档已修正并加注。验收标准（1300 元）不变。

## 10. 测试（第二篇，对照设计 §15）

- 解析：`test_parser_huatai.py` +12 例——4 关键词参数化隔离、折行摘要、有金额/有资金余额不隔离、非关键词零金额行不隔离、一致无 WARN、发债/转债代码不一致 WARN（-5000 vs 应 -1000）、费用容差不误报（-1000.50）、股票行不校验。
- 别名：`test_bond_alias.py`（新）——stem 提取、唯一映射、冲突/无对应不归并、转债名但非转债代码段（019547）不归并、非债忽略、`canonical_code`。
- FIFO 跨代码：`test_mapping.py`——754019 买 1 手@-1000 + 113050 卖 1 手@+1300 → 归一 113050、closed round 1 个、PnL +300.00、显示名「曙26转债」；显式 alias 子集归一；冲突不归并。
- 估值：`test_valuation.py`——`bond_value_units` 1 手@130→1300 元、`bond_face_divisors` 跳过可转债、总市值含可转债、上市前缺行情按成本 1000 元兜底（degraded 非 blocked）。
- 资产：`test_assets_application.py::test_cbond_alias_prelisting_degraded_postlisting_ok`——07-01 现金锚点 100000 → 07-20 买发债 → 上市前 degraded（缺 113050 行情、含 754019 持仓）→ 上市后 ok、市值 1300、总资产 100300。
- 既有断言同步：`test_bond_face_divisor_consistent_with_valuation` 面值口径用例 113050 → 122009（公司债）；`definition_version` 断言 1.1 → 1.2（`test_assets_incremental.py`、`test_api_081_assets.py`）；隔离矩阵 `_seed_asset_row` 改用 `ASSET_DEFINITION_VERSION` 常量（消除硬编码）。

## 11. 门禁结果（第二篇）

| 门禁 | 结果 |
|---|---|
| `make lint` | ✅ ruff + 前端 tsc/eslint |
| `make typecheck` | ✅ mypy strict + import-linter（5 kept, 0 broken）+ tsc |
| `make test-p2` | ✅ 含 api 224 条、isolation 93 条、api-check / 快照无漂移 |
| `make test-metrics` | ✅ 含 G8 金标结构门禁 25 份 |
| `make verify-p1` | ✅ 30/30 端点 + 前端 vitest/构建 |

排障记录：排查期间 API 套件出现「worker 任务卡 running / TRUNCATE 死锁」且基线（stash 后）同样复现，定位为环境性 flake——docker 容器 `sa-dev-db` 已连续运行 6 天、autovacuum backlog；`docker restart sa-dev-db` 后全绿。教训：**多个 make 门禁不得并行跑**（共享同一测试库，seed 账号唯一约束必撞）。

## 12. 遗留 / 边界（第二篇）

- 「托管转入」一并隔离（与「托管转出」同类纯通知行）；有金额或有资金余额的同名行不受影响。
- stem 冲突（同名两只转债）只留日志/assumptions，不归并、不阻断——符合设计「宁可不归并，不可错归并」。
- 未做：历史已入库的零金额信息行不在本次清理（重导账单即按新口径隔离）；如需回填清理，另开任务。

---

# 第三篇 货基超卖容忍 + 无锚点期初反推（2026-10-29 用户决策）

> 起因：用户上传真实账单（`changed_new.xlsx`，无「基金持仓」锚点区块、940018 首行即赎回
> 116,295.38、含五零「红利发放」行），构建资产表后**所有日期 degraded**。诊断出三个根因：
> ① 货基期初按 0 → 首行赎回后余额恒负 → `money_fund_unknown` 全表降级；
> ② 红利发放行被 INVALID_AMOUNT 隔离 → 份额丢失 → 09-30 赎回 106,720.80 > 申购 106,708.53
> → FIFO 超卖 12.27；③ 货基行参与 FIFO，首行赎回无买入可配必报 data_error。
> 用户拍板：**货基允许超卖，超卖数量 = 当次交易的收益（即红利发放的利息）；红利发放依旧隔离。**

## 13. 落地内容（对照设计 D10 + §6.4 修订）

| 改动 | 实现 | 位置 |
|---|---|---|
| 货基超卖容忍 | `FifoEvent` 新增 `allow_oversell`（默认 False）；`_apply_sell`：货基**部分超卖**的超出份额生成零成本 Fill（`buy_trade_id=""`、holding_days=0），卖款差额全额计 `pnl`（利息），不记 data_error；**全额超卖**（无 lot 可配，期初本金赎回）返回 `(None, 0)` 静默跳过，不记错、不计损益 | `stock_core/fifo/types.py` / `engine.py` |
| 货基行打标 | `trades_to_events`：`allow_oversell = trade.money_fund_balance_units is not None`（rebuild/nav/bridge 全部调用方自动生效） | `application/rebuild.py` |
| 无锚点期初反推 | `_apply_money_fund_balances` 改两遍推演：第一遍期初 0 记录各代码最小累计净额，无锚点且 min<0 → `期初 = -min`（赎回行自证持仓下界），写 `assumptions["money_fund_opening_inferred"]`；min≥0 → 保持 `money_fund_opening_zero`。返回签名 `set[str]` → `tuple[dict[str,int], set[str]]` | `parsers/huatai.py` |
| 口径版本 | `ASSET_DEFINITION_VERSION` 1.2 → **1.3**（货基余额值变化，自动全量重建） | `application/assets.py` |
| 红利发放 | **保持隔离**（INVALID_AMOUNT 五零规则不动），利息经 FIFO 超卖口径回到回合 PnL | 无改动 |

设计权衡：零成本利息 Fill 保持了「Σ fills = 回合聚合」守恒不变量，且无需 rounds 表结构变更；
全额超卖静默跳过是保守选择——无法区分本金与利息时一律按本金（pnl 0）处理，杜绝phantom收益。

## 14. 真实账单端到端验证（changed_new.xlsx）

| 环节 | 结果 |
|---|---|
| 解析 | `assumptions["money_fund_opening_inferred"] = {"940018": 11630765}`；余额序列 12.27 → 106720.80 → 0 → 5668.32 → 7782.33 → **0**，全程 ≥ 0（期末恰好清空，与账单一致） |
| FIFO | `data_errors = []`；940018 两个 closed 回合：PnL **+12.27**（利息）与 0.00，`oversell_units = 0` |
| 资产表 | 31 行 **0 行 degraded**（仅剩缺行情 blocked，离线验证空 ladder 所致，生产有行情即为 ok）；修复前同账单全部 degraded |

## 15. 测试与门禁（第三篇）

- 新增：`test_fifo_engine.py` 货基部分超卖（零成本利息 Fill + 守恒）/ 全额超卖（静默跳过）2 例；
  `test_mapping.py` 货基行打标 + 940018 申赎利息 12.27 闭环 2 例。
- 口径修订：`test_parser_huatai.py`（opening_zero → opening_inferred，余额 12.27/106720.80/0/5668.32）；
  `test_classification.py`（API 出参 12.27 / 106720.80）；`definition_version` 断言 1.2 → 1.3（5 处）。
- 门禁：`make lint` / `make typecheck` / core **100% 覆盖**（318 条）/ `make test-p2`（含 api-check、
  快照无漂移）/ `make test-metrics` / `make verify-p1` 全绿；单元 749 条全过。
  期间 `test_rebuild_rounds_job_runs_through_worker` 单发 worker 领取 flake，单跑即过（已知环境性问题）。

---

# 第四篇 新股待上市口径（2026-10-29 用户决策）

> 起因：用户上传真实账单（`changed_new.xlsx`），001237 惠康科技「认购缴款 → 上市首日」之间
> 无行情，资产表整段 `blocked`，且回合构建不出来。用户要求：**上市前应降级（按买入价计资产）
> 而非阻断，回合数据也要能构建**。

## 16. 根因（三处，均与「新股」语义相关）

| # | 现象 | 根因 |
|---|---|---|
| R1 | 001237 持仓 500 股被算成 1500 股 | 同一批中签有三行（05-14「市值申购中签」、05-19「新股入帐」、05-18「新股申购确认缴款」），全部命中 `new_stock` 规则且 `qty>0` → `holding_delta` 每行都 +500 |
| R2 | 回合构建不出（data_error 超卖） | FIFO 只把 `EventKind.BUY` 建 lot，`NEW_STOCK` 落入「不参与配对」分支 → 上市首日卖出无 lot 可配 |
| R3 | 资产表 05-18 ~ 05-21 整段 `blocked` | `total_market_value` 只对**债券**缺行情做成本兜底，股票缺行情一律进 `missing` → blocked |

## 17. 落地内容（对照设计 D11）

| 改动 | 实现 | 位置 |
|---|---|---|
| 通知行不计持仓 | `holding_delta(category, qty, *, amount_units=0)`：`new_stock` 且 `amount_units == 0` → 0（只有缴款行建仓）；送股（`bonus`）不受影响 | `valuation.py` |
| 缴款行建 lot | `build_rounds`：`EventKind.BUY` **或**（`NEW_STOCK` 且 `amount != 0`）建 lot；零金额通知行仍不建 | `stock_core/fifo/engine.py` |
| 待上市成本兜底 | `total_market_value(..., new_stock_codes=...)` 返回 **4 元组** `(total, missing, bond_fallback, new_stock_fallback)`；`new_stock_pending_codes(trades)` 取「`new_stock` 且金额非 0」的代码集合 | `valuation.py` |
| 行级状态 | `_row_status_reason` 新增 `new_stock_fallback` → 「新股待上市，按认购价估值（无行情：001237）」；命中即 `degraded` | `assets.py` |
| 净值口径同步 | nav 三处 `_total_market_value` + 三处 `holding_delta` 同步（Dashboard 与资产表不打架） | `nav.py` |
| Dashboard | `holding_delta` 调用补 `amount_units` | `dashboard.py` |
| 口径版本 | `ASSET_DEFINITION_VERSION` 1.3 → **1.4**（持仓语义变化，自动全量重建） | `assets.py` |

设计权衡：`pending_codes` 只由「缴款金额非 0」的行构成，避免把真缺行情的普通标的也兜底（那会掩盖
数据源缺口）；上市后一旦有收盘价就自动回到市价口径，无需额外状态位。

## 18. 真实账单端到端验证（changed_new.xlsx）

| 环节 | 修复前 | 修复后 |
|---|---|---|
| 持仓 | 001237 = 1500 股（通知行重复计数） | **500 股**（仅缴款行建仓） |
| 回合 | 无（超卖 data_error） | 1 个 `closed` 回合：qty 500 / 成本 26630.00 / 卖出 67457.81 / **PnL 40827.81**，显示名「N惠康」 |
| 资产表 05-18 ~ 05-21 | `blocked`（缺行情数据：001237…） | **`degraded`**「新股待上市，按认购价估值（无行情：001237）」，总资产 = 现金 + 认购成本 |
| 资产表 05-22（上市首日） | 仍 blocked | **`ok`**，按市价 135 元/股估值 |

## 19. 测试与门禁（第四篇）

- 新增：`test_fifo_engine.py`（缴款建 lot + 上市卖出成回合、仅缴款保持 open 回合 2 例）；
  `test_valuation.py`（通知行不计持仓、`new_stock_pending_codes`、待上市兜底 3 例）；
  `test_assets_application.py`（001237 上市前 degraded / 上市首日 ok / 持仓只计一次 1 例）。
- 口径修订：`definition_version` 断言 1.3 → 1.4（5 处）。
- 门禁：`make lint` / `make typecheck` / core **100% 覆盖**（320 条）/ `make test-p2` / `make test-metrics` /
  `make verify-p1` 全绿。
- 环境说明：本机 dev API/worker 与测试共用 `stock_analyze` 库，TRUNCATE 会与之争锁导致 `deadlock detected`。
  门禁改在**独立测试库** `stock_analyze_test` 上执行（`createdb` + `create extension citext/pgcrypto` +
  `STOCK_DB_NAME=stock_analyze_test make migrate`），不改动用户运行中的 dev 进程。

## 20. 遗留（第四篇范围外，已确认非本次回归）

- 账单期前已持有的股票（本例 000938 / 688126 / 688181）卖出时无期初 lot → 超卖 data_error，
  资产表对应日期仍 `degraded`「FIFO 数据异常」。属既有「无期初持仓锚点」限制，与本篇新股口径无关。

# 第五篇 看板持仓口径对齐 + 货基负余额兜底（2026-10-29）

## 21. 问题与根因

**现象（用户报告，账号 `1234@dev.com`）**

- 资产表显示 `degraded`；
- 看板 `DASHBOARD_HOLDING_PRICE_MISSING / asset_overview.holdings.market_value`，
  缺行情标的 18 个：000730、001526、004137、004262、004369、004749、005194、009712、010051、
  011063、011258、012470、015649、016778、017539、202308、754019、940018。

**根因（两处，均为「货基上线后口径漂移」）**

1. **看板未跟上资产表口径**：`dashboard._holdings_at` 仍把每一行当「普通持仓」累加数量，然后对所有
   标的统一请求行情。18 个代码中 **17 个是货币基金**（解析上传账单得到货基代码 20 个，两者交集
   即这 17 个）；剩下 1 个 `754019` 是**可转债发债代码**（`「xx发债」，1 手=10 张`），持仓数量为 0
   （新建仓行 qty=0、托管转出行 delta=0）。
   → 货基本来「余额在 `money_fund_balance_units`，不该请求行情」，看板却把它们当持仓去要价 → 必然缺失。
2. **资产表负余额降级**：货基余额按「账单末尾『基金持仓』区块」**期末锚点反推**，而这份账单缺
   `[省心投]` 赎回/转出流水（如 004369 两笔申购累计 19990.96，期末锚点只有 19.00）→ 期初被反推成
   −19971.96、首行余额 −9971.96。旧实现把「反推中间余额为负」判为「期初未知」，把
   2021-10-29 ~ 2025-12-16 共 **1510 行**资产标 `degraded`。

> 说明：本机各库（`stock_analyze` / `stock_analyze_test` / Docker `sa-dev-db`）的 `ledger.trade` 均为 0 行，
> 无法直接查该账号落库数据；因此复现统一采用**「解析上传电子对账单 → 内存跑 compute_daily_valuations / 看板持仓」**。

## 22. 落地内容（对照设计 D12）

| 文件 | 改动 |
|---|---|
| `packages/review/src/stock_review/application/dashboard.py` | `_holdings_at` 与资产表同口径：货基行 `continue`（不进持仓/不请求行情）；`canonical_code` 归并发债代码；无行情时债券/待上市新股按 `cost_basis` 兜底（`cost_fallback`），只有真缺价的普通标的进 `missing_prices`。返回值改为 4 元组 `(holdings, missing_prices, cost_fallback, truncated)` |
| 同上 | 新增 WARNING `dashboard.holdings_degraded`（`missing_price_symbols` / `cost_fallback_symbols` / `asset_status` / `asset_status_reason`），满足「日志输出降级原因」 |
| `packages/review/src/stock_review/application/assets.py` | 删除 `MONEY_FUND_UNKNOWN_REASON` 与 `money_fund_unknown` 降级分支；`status` 只看 `fifo_error_today \|\| uncovered \|\| bond_fallback \|\| new_stock_fallback`；`ASSET_DEFINITION_VERSION` 1.4 → **1.5** |
| 同上 | `build_degraded` 日志新增 `degraded_reasons`（分类计数）与 `fallback_symbols`（成本兜底标的）；新增 `assets.money_fund_balance_clamped` 监控日志 |
| `packages/review/src/stock_review/application/valuation.py` | 新增 `money_fund_balance(trade) = max(0, balance_units)`；`initial_money_fund_balances` 两处改用之 |
| `packages/review/src/stock_review/application/nav.py` | 逐日货基余额同样走 `money_fund_balance`（净值/资产/看板同一口径） |
| `packages/ledger/src/stock_ledger/parsers/huatai.py` | `_apply_money_fund_balances` 返回 `(inferred, zero, clamped)`；反推负值写 0（**不改累计值**，期末仍落锚点）；`assumptions["money_fund_balance_clamped"]` 留痕 |

## 23. 真实账单端到端验证（普通账户电子对账单_20261020135757.xlsx）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 解析 assumptions | `{}`（全按反推值落库，含负值） | `{"money_fund_opening_zero": ["001526"], "money_fund_balance_clamped": ["002960","004137","004369","016778"]}` |
| 资产表（1817 行） | 1510 行 `degraded`「货币基金期初余额未知（无基金持仓锚点）」 | **1817 行全 `ok`，0 blocked / 0 degraded** |
| 看板请求行情标的 | 18 个（含 17 个货基 + 754019） | **8 个真实股票**：000938 / 002020 / 300009 / 300373 / 300496 / 300863 / 688019 / 688165 |
| 隔离行 | 714 条（706 `[省心投]红利发放` INVALID_AMOUNT + 8 条 qty=0 转存管转出） | 不变（按设计仍隔离） |

## 24. 测试与门禁（第五篇）

- 新增 `backend/tests/unit/review/test_dashboard_holdings.py`（4 例）：货基不进持仓且不请求行情 /
  可转债发债代码归并（1 手×110×10 = 1100.00）/ 债券+待上市新股成本兜底 / 普通股票真缺价仍 missing。
- 新增 `test_parser_huatai.py::test_money_fund_negative_reverse_balance_clamped_to_zero`（004369 两笔申购
  19990.96、锚点 19.00 → 行余额 0 / 1900，`clamped={"004369"}`）。
- 新增 `test_assets_application.py::test_money_fund_negative_balance_is_clamped_not_degraded`（当日 `ok`、
  `money_fund_units=0`、`total_assets_units=10000.00`）。
- 口径修订：`definition_version` 断言 1.4 → 1.5（5 处，`test_assets_incremental.py` / `test_api_081_assets.py`）。
- 门禁：`backend/tests/unit` 759 passed / unit/review 205 passed / parser_huatai 60 passed；
  `make lint`、`make typecheck`（mypy strict + import-linter + tsc）通过；`STOCK_DB_NAME=stock_analyze_test make test-p2`
  全绿（269 passed）。
- 备注：`test_metrics_persist.py::test_concurrent_same_key_upsert` 曾在整套并发负载下偶发
  `duplicate key ... metric_result_pkey`，**单独运行与整套重跑均通过**，与本次改动无文件交集（指标落库链路未动）。

## 25. 遗留（第五篇范围外）

- 账单期前已持有股票卖出无期初 lot → 超卖 data_error（同第四篇 §20，属既有「期初持仓锚点」缺失问题）。
- 货基余额「0 兜底」只解决**账单单侧流水缺陷**；若后续补导完整流水，重建即由锚点自然修正。
