# Dashboard 设计交叉评审记录（2026-10-28）

> 评审对象：`docs/architecture/2026-10-28-dashboard.md`（v0.1 → 评审后修订为 v0.2）
> 评审方式：**3 名独立评审员只读评审**（前端/验收、后端/契约、数据口径/对抗），Lead 汇总并逐项处置；评审员未修改仓库任何文件。
> 结论：**v0.1 REQUEST_CHANGES → v0.2 文档级 P0 已关闭；仍有 A 类口径 HITL 待人工拍板，未进入实现。**

---

## 1. 评审范围与核对基线

评审员已核对真实代码/文档，而非只读设计稿：

- 前端：`frontend/apps/web/src/{router.tsx,client.ts,pages/*,styles.css,router.test.tsx}`、`frontend/apps/web/package.json`、`frontend/AGENTS.md`；
- 后端：`backend/packages/review/src/stock_review/application/{nav,assets,bridge,rounds,valuation}.py`、`repositories/{assets,rounds}.py`、`stock_api/{schemas.py,routers}`、迁移 0006/0007、`metrics/{gate,engine,defs/*}`；
- 契约/门禁：`docs/architecture/v2/{API-Catalog.md,Testing.md}`、`contracts/openapi.json`、`backend/services/api/src/stock_api/openapi_meta.py`、`scripts/ci/gate_endpoint_coverage.py`、`tests/contract/test_openapi_contract.py`、`tests/isolation/test_isolation_matrix.py`；
- 计划/验收：`docs/DEV-PLAN-v2.md` §3.1/§3.2/A.1、`docs/prd/PRD-v2.md` FR-D1/FR-R8、`docs/reviews/2026-10-27-p207-S10-acceptance.md`。

事实基线核实（V0.1 文档正确）：前端无 Dashboard、`/`→`/trades`、无聚合端点；`/nav`/`/metrics`/`/rounds`/`/assets`/API-083 已有；`make rebuild`/`demo-broken`/`verify-p2` 不存在；前端无 TanStack Query/ECharts/MSW/Playwright。

---

## 2. 总体评审结论

- **架构方向正确**：新增只读聚合端点、两板块 + 子块、显式 `null` + `missing[]`、blocked/missing 不造假、入口占位不产生假下载/假 job、HITL 显式化。
- **v0.1 不能直接实现**：3 名评审员共识别 **10 个 P0 问题条目（归并为 7 类根因）**，集中在：
  1. 时间窗口缺失（旧五卡是月度，Dashboard 是快照/全历史）；
  2. `stale`/`insufficient` 状态语义自相矛盾；
  3. 15 字段没有 1:1 映射、weight 示例自相矛盾；
  4. 最新资产行 blocked 时会把 0 当金额、total_profit 变巨额负数；
  5. `total_profit` 的 residual 单位/`other`/stale 混用；
  6. holdings 无持久化来源、成本引擎不一致；
  7. 契约/门禁/隔离矩阵/验收脚本/缺失清单不完整或不可执行。
- **v0.2 已修订**：新增 `legacy_period`、两轴状态、15 字段追踪表、blocked 行语义、`period_end=A`/residual 单位、`holdings_at` 纯原语、契约/隔离/门禁清单、可执行验收脚本、完整缺失清单。
- **仍需人工拍板**：D-01/D-02/D-03/D-05/D-11/D-14 等口径；未拍板前对应数字只做占位/不出数。

---

## 3. P0 问题与处置

| # | P0 问题（评审证据） | v0.2 处置 | 状态 |
|---|---|---|---|
| P0-1 | **时间窗口缺失**：`/metrics` 默认当月、`/nav` 强制 period；demo 2026-04~06、当前时钟 2026-10 → 旧五卡空；DASH-11 无法成立 | 新增 `legacy_period`（最近有流水/回合月；demo=2026-06）；旧子块显式传 period，全历史 headline 加"全历史"；§6.1 脚本使用 `PERIOD=2026-06`；D-11 | ✅ 文档级关闭（待人工确认窗口） |
| P0-2 | **stale/insufficient 语义冲突**：§3.3 说 stale 可显示、DASH-06 说 stale 不渲染；`insufficient` 不在枚举；demo 真实时钟常 stale | 拆 `build_state`/`data_status` 两轴；`stale` 显示 + 提示，`blocked/missing` 不显示；`insufficient` 入枚举/字段级；DASH-06 改口径 | ✅ 文档级关闭 |
| P0-3 | **15 字段无 1:1 映射/weight 矛盾**：示例 160000/210000 却 weight=32%，DASH-02 要求和=100% | 新增 §1.3 A1~A7/H1~H8 追踪表；`weight_pct = mv/Σmv`（行内和 100%），`position_pct` 单独；示例改 100%；DASH-01/02 改为逐项断言 | ✅ 文档级关闭 |
| P0-4 | **blocked 最新行**：A 为 blocked 时 asset_daily 数值全 0，会输出 `"0.00"`/巨额负 total_profit | §3.1 明确 A.status=blocked → section blocked、金额/比率全 null+missing；区分 build_state/data_status；补 blocked 单测 | ✅ 文档级关闭 |
| P0-5 | **total_profit 单位/other/stale**：`residual_units` 是分、`>0.01` 错；DASH-03 公式漏 `other`；A 旧/B 新；B degraded 未传播 | 阈值改 `abs(residual_units) > B.tolerance_units`；公式含 `−other`；B.period_end=A；B.status/degraded 传播；流水指纹 stale → total_profit null/degraded；DASH-03 用独立手算 fixture | ✅ 文档级关闭（`other` 语义待 H-02） |
| P0-6 | **holdings 没有持久化来源**：asset_daily 只有聚合；nav 逐标快照跳过缺价；asset 平均成本 vs nav FIFO | §4.2 明确新增 `holdings_at(trades, ladder, A)` 纯原语、成本与 asset_daily 同一模型、短窗行情、逐标 null；补 sum(mv)==A.market_value 对账；D-14HITL | ✅ 文档级关闭（成本模型待 H-01） |
| P0-7 | **契约/门禁/隔离必挂**：`OPERATION_META`、契约测试 operation 集合、gate 83/29、隔离矩阵、手写客户端；§11 路径错误 | §11 补 `contracts/openapi.json`、OPERATION_META、84/84、Period2 30、契约测试、LIST/ADMIN/UNAUTH 矩阵、`http.ts/contract.ts`；删除不存在的 `operationId.lock.json` | ✅ 文档级关闭 |
| P0-8 | **旧五卡/Period2-12 漏项**：对账横幅、可信度标签、下钻抽屉、年化 bp 丢失 | §2 补对账横幅、可信度、下钻改详情页、年化 bp 缺失；定级 P0/P1；保留旧卡带 n/CI | ✅ 文档级关闭（下钻形式变更需确认） |
| P0-9 | **验收脚本不可执行**：`make rebuild/verify-p2/demo-broken` 不存在、无 token/period、blocked 无命令、curl 断言无效 | §6.1 重写为 `set -euo pipefail` 脚本：登录 → API-083 → 轮询 → curl+jq；blocked 用 pytest；DOM 用 Vitest/RTL；SKIP 清单；不用 Playwright/curl 测 SPA | ✅ 文档级关闭 |
| P0-10 | **缺失清单不准**：漏 G8 23 项红灯、前端工具链、perf 夹具；API-058 误归 055~057；路径错误 | §7 重写为后端/前端/工程三类，补 G8、D-19、工具链/夹具、API-058、路径/计数；每项标阻塞/修复/SKIP/入口 | ✅ 文档级关闭 |

---

## 4. P1 问题（实现前应处理/设计已标注）

| # | 问题 | 处置 |
|---|---|---|
| 1 | 回撤/胜率绕过指标门禁（`MIN_TRADING_DAYS=60`、blocked/degraded） | §3.2 加 60 日门槛、有效段/`insufficient`/`unreliable`；与 `timing.drawdown` 同源；D-05 |
| 2 | 旧子块无 display/百分比串、比率格式（raw + pct） | 本期子块显示 raw value + 单位 + n/CI；新的 headline 用后端 pct；不声称所有旧卡"全 display" |
| 3 | 裸术语 `TWR/MWR/DE/HHI/CR1` | UI 用中文标签（时间加权/资金加权/处置效应/持仓集中度），技术标识放详情/口径抽屉 |
| 4 | 权重/占比容差、截断、排序 | weight 分母固定；容差 `n×0.5bp`；前端不排序（后端固定顺序）；截断进 missing/notice |
| 5 | 根路由/登录落地/router 测试 | §5.1 明确 Login/Register 跳 `/dashboard`，更新 router.test 导航顺序断言 |
| 6 | 下钻 `/rounds?status` 不生效 | 改 `/metrics/{id}?period=legacy_period`；最赚/最亏用 `round_id` 跳详情 |
| 7 | MDD 索引 off-by-one/blocked 分段 | §4.3 说明 `index[0..n]`、`usable_rows[idx-1]`、duration/recovery 语义；补 NULL 洞/60 日边界测试 |
| 8 | 性能 300ms 乐观 / bridge 二次加载 | §4.2 单次加载 trades + helper；SQL 聚合；无 50k 夹具时 DASH-09 SKIP |
| 9 | 错误码/401/admin guard | §4.1 补 401/403/429/500；§8 补 401/403/list 200；handler 显式 `forbid_admin_trades`/user_id/RLS |
| 10 | 状态/缺失枚举新增 | D-12 两轴；`DashboardMissingCode` 登记；`badge-stale/missing/insufficient` |
| 11 | 占位页可发现/鉴权 | 占位页挂 RequireAuth children；按钮进入；占位页可返回；`data-dependency`；无假下载/job |
| 12 | 缺失清单与 PRD/Testing 漂移 | v0.2 §7/§11 增加 PRD FR-D1 两板块/技术栈偏差、Testing §7 更新；需人工批准 |

---

## 5. 仍需人工拍板（A 类）

1. **D-01**："总投入最大成本"定义 + 成本引擎（资产平均成本 vs FIFO）。
2. **D-02**："总收益额"是否含浮盈/股息/费用；`other`（打新/补差）是否算外部流；stale 是否出数。
3. **D-03**："最大收益/最大成本比率"分母（峰值持仓成本/该笔成本/峰值总资产）——三者含义不同。
4. **D-05**：历史 blocked/degraded 的回撤策略与 60 交易日门槛（是否豁免/分段）。
5. **D-11**：`legacy_period` 固定最近有数据月 vs 用户自选 vs 本月。
6. **D-14**：持仓成本/估值来源（短窗行情+`holdings_at` vs 全历史重放/FIFO）。
7. **D-06**：旧五卡 → 两板块、下钻抽屉改详情页、PRD FR-D1/Testing 技术栈偏差是否正式接受。
8. **D-15**：性能是否允许物化/缓存，或维持 300ms 并缩小夹具。

> 未拍板前：对应字段按 `null` + `missing[]` 占位，不硬编码默认口径；不得用生产代码生成期望值。

---

## 6. 评审结论

- **v0.1 → v0.2 修订有效**：P0 均已在文档层处理，验收脚本/缺失清单可执行性显著改善；无需全量二次评审，建议只对 P0 关闭项和 HITL 决策做一次复核。
- **实现前置**：A 类口径拍板 + 契约先行（API-084/OPERATION_META/gate/隔离矩阵）+ 前端占位路由/组件。
- **明确不做**：Period2-08/09/10/11/13/14/15 功能；只留前端入口与 SKIP 清单。
