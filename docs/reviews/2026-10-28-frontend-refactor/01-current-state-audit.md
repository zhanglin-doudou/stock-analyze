# 前端现状审计（重构基线）

> 日期：2026-10-28 ｜ 作者：repo-auditor（Team Lead 委派）｜ 范围：`frontend/`（只读审计）
> 方法：通读源码 + 只读 grep/`git status`；**未修改任何源码、未运行任何写仓库的构建**。`frontend/apps/web/dist/` 是 10-28 10:39 的既有产物（gitignored，`.gitignore:16`），仅用于度量体积。
> 口径：全部结论均给 `文件:行`；文档与实现不一致处单列，并以**代码为事实**。
> 关联：[共用剧本](00-brief-demo-data.md)、[Dashboard 设计](../../architecture/2026-10-28-dashboard.md)、[PRD-v2](../../prd/PRD-v2.md)。

---

## 0. 结论速览

1. **技术栈比文档更"裸"**：运行时依赖只有 4 个（`frontend/apps/web/package.json:13-18`）。无 UI 库、图表库、CSS 框架、Tailwind、TanStack Query、ESLint、Playwright——与 `frontend/AGENTS.md:13,32`、`PRD-v2.md:430` 不符（§1.2）。
2. **规模**：`apps/web/src` 非测试源码约 **4,056 行**（CSS 404 行、14 个页面 3,066 行、`ui/` 7 组件 365 行）；测试 **2,293 行 / 12 文件 / 61 用例 / 287 个 `expect`**。既有 dist：JS 275,040 B（gzip 86,374）、CSS 4,444 B（gzip 1,473），单 chunk 无路由拆分。
3. **信息架构 = "9 项平铺导航 + 卡片长页"**：顶部 9 链接无分组/无当前态（`router.tsx:33-43`）；3 个占位路由没有导航入口，只能从 Dashboard 进入（`router.tsx:84-113`）。
4. **视觉层级扁平**：body 14px，标题仅 20/16/14px 三档（`styles.css:26-35`）；KPI 数值 `<strong>` 继承 14px，无大数字、货币符、千分位、`tabular-nums`（`Dashboard.tsx:59-62`、`styles.css:389-397`）。
5. **零响应式**：全 CSS **0 个 `@media`/`@container`**；固定 `max-width:1080px`（`styles.css:54-58`）、filters 固定 4 列（`:342-346`）；表格无横向滚动容器，13 列回合表在 390/768 上会撑破页面（§4）。
6. **零图表**：`src` 内 0 个 `<svg>`/canvas/图表依赖；净值、回撤、趋势全部只有表格与文字（`Nav.tsx:259-264`、`Dashboard.tsx:286-307`）。
7. **零口径执行良好**：业务页面 0 处 `parseFloat/parseInt/Number()/toFixed()`（grep 仅命中 2 处测试名）；但"符号判定"用字符串前缀实现且复制 4 份，className 被测试钉死（`Assets.test.tsx:373-392`）。
8. **数字格式三套并存**：`10.00%`（`Dashboard.tsx:98`）、裸小数 `0.40625000`（`Rounds.tsx:174`）、表头"TWR（累计，小数）"（`Nav.tsx:219`）——同一概念三种视觉形态。
9. **状态语义 7 套映射重复**（§3.7），`blocked` 在不同轴含义不同；设计文档 D-12 的两轴拆分（`2026-10-28-dashboard.md:35`）尚未落地。
10. **最大地雷是测试锁定 DOM**：77 次 `getByText`、43 次 `getByTestId`、35 次 `getByRole`，并断言精确 className；两个"零口径源码断言"用 `?raw` **只扫单文件**（`Dashboard.test.tsx:10,320-325`、`Assets.test.tsx:16,554-560`）→ 拆文件后门禁静默失效。
11. **CI 完全不测前端**：唯一 workflow 的 `paths` 无 `frontend/**`，job 无 pnpm 步骤（`.github/workflows/p2-metrics.yml:9-16,25-36`）；`make test` 只跑 pytest（`Makefile:133-135`）。
12. **文档漂移 8 条**（§8.8）需在重构前修正，否则会"按错误文档写代码"。

---

## 1. 技术栈与依赖现状

### 1.1 实际依赖（以 lock 解析为准）

| 类型 | 包 | 声明 | 解析 | 证据 |
|---|---|---|---|---|
| 运行时 | react / react-dom | ^18.3.1 | 18.3.1 | `apps/web/package.json:15-16`、`frontend/pnpm-lock.yaml:16-24` |
| 运行时 | react-router-dom | ^6.28.0 | 6.30.6 | `apps/web/package.json:17`、`pnpm-lock.yaml:22-24` |
| 运行时 | @sa/api-client | workspace:* | 本地包 | `apps/web/package.json:14` |
| 开发 | vite | ^5.4.11 | 5.4.21 | `apps/web/package.json:26`、`pnpm-lock.yaml:1245` |
| 开发 | vitest / jsdom | ^2.1.8 / ^25.0.1 | 2.1.9 / 25.0.1 | `apps/web/package.json:24-27` |
| 开发 | @testing-library/react | ^14.3.1 | 14.3.1 | `apps/web/package.json:20` |
| 开发 | typescript | ^5.6.3 | 5.9.3 | `apps/web/package.json:25`、`pnpm-lock.yaml:1226` |
| 生成 | openapi-typescript | 7.4.4 | 7.4.4 | `packages/api-client/package.json:23` |

- 本机：Node **v24.19.0**、pnpm **12.15.0**（实测）。
- 工作区：`frontend/pnpm-workspace.yaml:1-3` 只有 `apps/*`、`packages/*`；根 `frontend/package.json:6-12` 仅聚合 `-r` 脚本。
- **没有**：UI 组件库、图表库、CSS 框架/Tailwind/CSS Modules/CSS-in-JS、TanStack Query、状态库、ESLint、Prettier、Playwright、Storybook、MSW。

### 1.2 文档/PRD 声称但未安装（以代码为准）

| 声称 | 出处 | 实际 | 证据 |
|---|---|---|---|
| TanStack Query | `frontend/AGENTS.md:13,84-95` | 未安装；全部页面手写 `useState/useEffect` | `apps/web/package.json:13-27` 无该依赖；`Dashboard.tsx:106-127` |
| ECharts | `PRD-v2.md:430` | 未安装；设计已改为 v1 不引入 | `2026-10-28-dashboard.md:33`（D-10） |
| 零口径 ESLint 规则 | `frontend/AGENTS.md:32` | 无 ESLint、无配置、无自定义规则 | 全仓 `find *eslint*` 无结果；`apps/web/package.json:6-12` |
| Playwright | `PRD-v2.md:492` | 只有 vitest+jsdom | `apps/web/vite.config.ts:18-22` |
| 组件库 | `frontend/AGENTS.md:13` | 仅 7 个自研小组件 | `apps/web/src/ui/` |

> 注：`2026-10-28-dashboard.md:59`（C-04）已写明"仓库未引入 TanStack Query"，与 frontend AGENTS 自相矛盾；以设计文档 + 代码为准。

### 1.3 脚本与门禁

| 命令 | 实际行为 | 证据 |
|---|---|---|
| `pnpm --filter web dev` | Vite dev `127.0.0.1:5173`，`/api` → `:8000` | `vite.config.ts:5-17` |
| `pnpm --filter web build` | `tsc --noEmit ×2 && vite build` | `apps/web/package.json:8` |
| `pnpm -r lint` | **仅 tsc**，不是 eslint | `apps/web/package.json:9` |
| `pnpm -r typecheck` | tsc --noEmit（web + node + api-client） | `apps/web/package.json:10`、`packages/api-client/package.json:19` |
| `pnpm -r test` | vitest run（web 12 文件 + api-client 2 文件） | `apps/web/package.json:11`、`vite.config.ts:18-22` |
| `make lint` | ruff + `pnpm -r lint`（即 tsc）；注释自称 "ruff + eslint" | `Makefile:116-120,31` |
| `make test` | 只有 pytest，**不含前端** | `Makefile:133-135` |
| CI | `paths` 无 `frontend/**`、无 pnpm | `.github/workflows/p2-metrics.yml:9-16,25-36` |
| `make sdk` | 后端 cli 从 openapi 生成类型 | `Makefile:209-211` |

### 1.4 构建产物（既有 dist，非本次构建）

JS 275,040 B → gzip 86,374 B；CSS 4,444 B → gzip 1,473 B（`frontend/apps/web/dist/assets/`）。`router.tsx:62-117` 全量静态 import 14 个页面，**无路由级 code-splitting**；React+Router 基线约占 150KB raw，业务代码约 120KB。引入图表/组件库前须先定首屏预算（建议重构后 ≤150KB gzip 首屏）。

### 1.5 工程配置

- `tsconfig.json`：`strict` + `noUnusedLocals` + `noUnusedParameters` + `noFallthroughCasesInSwitch`（`apps/web/tsconfig.json:10-13`）→ 重构残留变量会直接构建失败。
- `vite.config.ts`：`test.globals:false`（`:21`）→ 测试必须显式 import vitest。
- `index.html`：`lang="zh-CN"`、viewport 正常（`:2,5`），但 title 固定"交易复盘平台"（`:6`）、不随路由更新；无 favicon/theme-color。

---

## 2. 页面清单与信息架构

### 2.1 路由（`router.tsx:62-117`，共 18 条 path）

| 分组 | 路径 → 组件 | 行 |
|---|---|---|
| 认证 | `/login`→Login、`/register`→Register | `:63-64` |
| 外壳 | `/` → redirect `/dashboard`（RequireAuth+Layout） | `:65-73` |
| 主页面 | `/dashboard`、`/trades`、`/rounds`、`/nav`、`/assets`、`/metrics`、`/import`、`/imports`、`/settings` | `:74-83,114` |
| 详情页 | `/rounds/:roundId`、`/metrics/:metricId` | `:77,81` |
| 占位 | `/reports`、`/exports`、`/attribution` → `FeaturePlaceholder` | `:84-113` |
| 兜底 | `*` → redirect `/`（静默） | `:117` |

- 用 `createBrowserRouter`（`:62`）但**无 loader、无 errorElement、无 Suspense/lazy**（全仓 grep 0 命中）→ 渲染异常白屏，未知路径静默回首页。

### 2.2 页面职责 · 数据 API · 下钻

| 页面 | 职责 | 主要 API | 下钻/跳转 |
|---|---|---|---|
| Dashboard | 资产概览 8 KPI+持仓表；历史概览 7 KPI+回撤+最赚最亏 3；月度旧指标懒加载 | `GET /dashboard`（`:117`）；按需 `GET /metrics`（`:134`） | `/rounds/:id`（`:93`）、资产、指标详情（`:364`）、3 占位页（`:255-261,330-338`） |
| Trades | 流水列表、内联编辑（If-Match）、软删/恢复 | list/patch/delete/restore（`:29,53,72,83`） | 无（不回链回合） |
| Rounds | 回合列表+4 条件筛选+手动重建 job | `GET /rounds`（`:64`）、`POST /jobs/rebuild-rounds`（`:144`） | `/rounds/:id`（`:181`） |
| RoundDetail | 回合口径+FIFO 配对明细 | `GET /rounds/{id}`+`/fills`（`:28`） | 返回列表、`/trades`（`:134`） |
| Nav | 月度净值/TWR/MWR+逐日表+每日持仓 | `GET /nav`（`:65`；**后端 UPSERT**，`http.ts:611`） | `/rounds`（`:196`）、`/import`（`:314`） |
| Assets | 资产日快照+构建/更新 job+接管在跑任务 | `GET /assets`、`POST /assets/build`、`GET /jobs`、SSE（`:174,278,203,384`） | `/import`（`:405`） |
| Metrics | 月度 18 指标+一键构建复盘数据 | `GET /metrics`（read-through，`http.ts:660`）、`POST /jobs/rebuild-period`（`:199`） | `/metrics/:id`（`:220`） |
| MetricDetail | 指标三段式+下钻样本表 | explain+drilldown（`:46,56`） | `/rounds/:id`（`:88`）、回列表（`:98`） |
| Import | 上传→预览（列映射/预检）→提交→进度→批次结果→撤销 | preview/commit/getBatch/revoke（`:32,49,62`、`ui/RevokeBatch.tsx:27`） | `/imports?batch=`（`:179`）、`/trades`、`/rounds` |
| Imports | 批次列表+内联详情+隔离区+撤销 | batches/detail/quarantine（`:61,77`、`ui/QuarantineList.tsx:30`） | 无（详情靠 `?batch=`，`:44`） |
| Settings | PAT 创建（一次性明文）/列表/撤销 | tokens list/create/revoke（`:18,39,53`） | 无 |
| Login/Register | 认证+表单校验 | auth login/register（`Login.tsx:20`、`Register.tsx:31`） | 互跳 |

### 2.3 导航结构（`router.tsx:27-60`）

- 结构：`header.app-header`（品牌"交易复盘"+9 链接+登出）→ `main.app-main` → `<Outlet/>`（`:30-58`）。
- 9 项顺序被测试锁定：前 5 项 `["看板","交易流水","回合","净值","资产"]`（`router.test.tsx:29-30`）。
- **无激活态**：全用 `<Link>`，无 `NavLink`/`aria-current`（全仓 0 命中）；3 个占位路由不在导航中（`router.tsx:84-113`），只由 Dashboard 链接暴露——与共用剧本 §2.7 第 1 条"9 入口 + 3 占位入口，不可隐藏"有偏差。
- 登出只清 localStorage（`router.tsx:44-53`），**未调 `POST /auth/logout`**（客户端有此方法 `http.ts:392`，全仓 0 调用）→ 服务端 refresh token 不失效。

### 2.4 PRD 页面清单 vs 实现（`PRD-v2.md:431`）

| FR-D1 要求 | 现状 | 证据 |
|---|---|---|
| 登录/注册、回合+下钻、导入 | ✅ | `router.tsx:63-64,76-77,82` |
| 看板（5 卡） | ⚠️ 已变更两板块 15 字段 | `Dashboard.tsx:203-385`、`2026-10-28-dashboard.md:45` |
| 指标详情（三段式+**口径抽屉**） | ⚠️ 三段式 ✅、抽屉→独立详情页 | `MetricDetail.tsx:130-187`、设计文档 `:126` |
| 设置（Token/**基准**/**隐私**） | ⚠️ 仅 PAT | `Settings.tsx:62-124` |
| 月报 | 占位 | `router.tsx:84-93` |
| **对账 / 日志 / 审计** | ❌ 无页面 | 全仓无对应组件 |

### 2.5 URL 状态与下钻链

`Dashboard`→`RoundDetail`/`MetricDetail`/`Assets`/占位页；`Metrics`→`MetricDetail`→`RoundDetail`。`RoundDetail.tsx:57` 固定回 `/rounds`（丢筛选）；`MetricDetail.tsx:98` 只带 period。全仓 `useSearchParams` 仅 4 页（`Nav.tsx:45`、`Metrics.tsx:84`、`MetricDetail.tsx:32`、`Imports.tsx:43`）；Rounds 的 4 个 filter 与 Trades 的 `include_deleted` 只在组件 state（`Rounds.tsx:47-48`、`Trades.tsx:19`）→ 刷新即丢、无法回退。

---

## 3. 视觉与交互逐项体检

### 3.1 布局

外壳 `header` 白底 1px 下边框 + `main` 居中（`styles.css:42-58`）；**无侧栏、面包屑、页脚、全局动作区**。`max-width:1080px`+`padding:18px`（`:54-58`）：1440 视口内容仅占 75%、1920 仅占 56%，两侧空白 180/420px——高密度数据场景同时"浪费宽度 + 页面过长"。页面骨架高度统一为 `<h1>`+`<p class="muted">`口径说明+`.card` 流；Dashboard 把标题与"刷新"塞进 `.row-actions`（`Dashboard.tsx:166-171`），属布局 hack。

### 3.2 导航

见 §2.3。补充：`header`/`nav` 均 `display:flex` 且**无 `flex-wrap`**（`styles.css:42-53`），9 个中文链接+品牌+登出最小宽约 600–620px → 390px 必然横向溢出（§4.2）。

### 3.3 排版

| 项 | 现状 | 证据 |
|---|---|---|
| 基准字号 | 14px；档位仅 20/16/14/12px | `styles.css:17,26-35,399` |
| 字体栈 | 中文系统栈 | `styles.css:16` |
| 数字 | 无 `tabular-nums`/`font-variant-numeric`（全仓 0 处） | `styles.css` 全文 |
| 行高 | 未声明，走 UA 默认 | `styles.css:14-20` |
| 字重 | 仅 `strong` + 默认 | `Dashboard.tsx:61` |

### 3.4 表格

- 通用组件 `Table` 38 行：空数据渲染 `<p class="muted">`，否则原生 `<table class="table">`（`ui/Table.tsx:16-38`）。
- **不支持**：排序、列宽、冻结列/表头、斑马纹、密度、hover、行选中、caption、`scope`（`ui/Table.tsx:22-25`）——加载态只能靠 `emptyText` 字符串（`Assets.tsx:340-346`）。
- 样式：全边框、`text-align:left`、`vertical-align:top`、`th` 灰底（`styles.css:119-134`）→ 数字列左对齐+比例字体=不可扫读。
- 列数：Rounds **13**（`Rounds.tsx:156-186`）、Assets 11（`:299-337`）、Trades 10（`:90-133`）、Nav 7–8（`:92-149`）、Imports 7（`:101-120`）。
- **7 张手写 `<table class="table">` 绕过组件**（Dashboard 占 2）：`Dashboard.tsx:228-251,351-384`、`Nav.tsx:216-244`、`RoundDetail.tsx:86-115`、`Imports.tsx:174-195`、`Import.tsx:103-124`、`Settings.tsx:98-124`。

### 3.5 表单

`label` 块包裹输入（`styles.css:79-82`）、input 统一 `width:100%`（`:88-98`）、`select/date/month` 各自声明（`:353-369`）。**缺**：`fieldset/legend`、`autocomplete`（`Login.tsx:36,42`、`Register.tsx:52-73`）、`aria-describedby`（`Register.tsx:75-77` 字段错误未关联）、禁用态样式（仅 `.btn:disabled`，`:69-72`）。Trades 编辑卡无 busy 态（`Trades.tsx:165-172`），与 Assets/Metrics 的 `aria-busy` 不一致。

### 3.6 KPI

`minmax(180px,1fr)` 自适应网格（`styles.css:383-388`），label 12px 灰、值 `<strong>` 14px（`:389-404`）。**无对比基准、趋势、目标、组成拆解**；8+7 个 KPI 等权平铺（`Dashboard.tsx:205-222,267-284`）；历史/快照/月度三套窗口只有一行 muted 说明（`:172-175`），违反设计文档"三种窗口分别标注"（`2026-10-28-dashboard.md:117`）；无"本期结论/洞察"区（设计 §2 第 7 条要求可空文案，`:129`）。

### 3.7 状态徽标（7 套映射重复）

| 文件 | 映射 | 关键值 |
|---|---|---|
| `Dashboard.tsx:12-33` | 3 套 | ok/degraded/stale/blocked/missing/insufficient/unreliable |
| `Nav.tsx:8-12` | nav status | ok/blocked/degraded |
| `Rounds.tsx:16-20` | round status | closed/open/data_error |
| `Imports.tsx:11-17` | batch status | pending/running/succeeded/failed/revoked |
| `Metrics.tsx:25-37` | 2 套 | ok/blocked/stale/unreliable |
| `Assets.tsx:20-53` | 3 套 | ok/blocked/degraded；full/incremental/noop；missing/current/stale/blocked |
| `MetricDetail.tsx:9-14` | sample status | ok/insufficient/pending_window/blocked |

- 徽标样式 20 变体（`styles.css:274-300,310-335,371-382`），其中 `badge-ok≈badge-current≈badge-succeeded≈badge-closed`（同绿色）、`badge-pending≈badge-running≈badge-open`（同蓝色）→ 语义只能靠文案区分。
- `blocked` 在 build_state/page status/row status/metric status/sample status 里含义不同（`Dashboard.tsx:21-33`），用户无法建立心智模型；未知状态原样输出英文（`Nav.tsx:16`、`Rounds.tsx:24`、`Assets.tsx:28`）。

### 3.8 错误态

- `ProblemView` 统一渲染 7807 六字段 + `role="alert"` + 重试（`ui/Problem.tsx:39-70`）——**现有质量最高的部分**。
- 但契约过严产生真实 bug：`toProblem` 把"非 7807 且非 Error 的对象"归为通用错误（`ui/Problem.tsx:4-30`），而 `isProblem` 要求 `code:string && status:number`（`http.ts:60-65`）；`Register.tsx:21-27` 抛出 `{code,user_message}` 无 `status` → 渲染"发生了未预期的错误"而非"两次输入的密码不一致"（真实提示只靠字段级 `:75-77` 兜底）。
- 页面级错误不一致：Dashboard 整页 return 错误（`Dashboard.tsx:142-149`），其余内联 `ProblemView`（`Trades.tsx:145`、`Nav.tsx:204`）；job 失败有 2 份手写 `Problem` 兜底（`Assets.tsx:92-105`、`Metrics.tsx:68-80`）。

### 3.9 加载态（7 种写法并存）

整页 early return（`Dashboard.tsx:151-158`）｜`Table` 的 `emptyText="加载中…"`（`Assets.tsx:340-346`、`Trades.tsx:185`）｜独立 muted 段落（`Assets.tsx:409`、`Metrics.tsx:351`）｜按钮文案（"构建中…/更新中…/提交中…/撤销中…"，`Assets.tsx:291-297`、`Import.tsx:146`）｜`Cursor` disabled（`ui/Cursor.tsx:13,17`）｜进度条（唯一可视化，`ui/JobProgress.tsx:72-74`）｜无反馈（`RoundDetail.tsx:60`）。无骨架屏、无统一 `aria-busy`（仅 `Assets.tsx:361`、`Metrics.tsx:285`）、无请求去重。

### 3.10 空态

全部是灰色一句话、文案散落、无统一 CTA 组件：`"暂无数据"`（`Table.tsx:16`）、`"没有持仓（或资产表未构建）。"`（`Dashboard.tsx:226`）、`"还没有回合：导入账单后…"`（`Rounds.tsx:193`）、`"还没有资产记录：先导入账单…"`（`Assets.tsx:346`）、`"隔离区为空"`（`ui/QuarantineList.tsx:52`）等 10+ 处；部分页面表格后再补一次导入引导（`Assets.tsx:404-406`、`Rounds.tsx:268-270`）。

### 3.11 颜色

- 设计变量只有 5 个（`styles.css:1-8`），其余 **45 个硬编码色值**（`#0a7d32`×7、`#fff`×6、`#e6f4ea`×4…），无 success/warning 语义变量。
- **正负语义与 A 股惯例相反**：`.money-pos` 绿 `#0a7d32`、`.money-neg` 红 `#b42318`（`styles.css:336-341`）。A 股用户心智是**红涨绿跌**，重构需先拍板（会影响三套预览方案）。
- 对比度（WCAG 估算，待工具复核）：`--muted` ≈4.8:1 ✅；`--accent` on 白 ≈4.6:1 ✅（临界）；`.badge-pending/.badge-running` `#1f6feb` on `#e8f0fe` ≈**4.0:1 ❌**（12px 小字需 4.5:1，`styles.css:296-300`）。
- `.money` 基类被大量使用（`Dashboard.tsx:61`、`Nav.tsx:21`、`Assets.tsx:59`、`Rounds.tsx:28`、`RoundDetail.tsx:9`），但 **CSS 中不存在 `.money` 规则**（只有 `.money-pos/.money-neg`）——一个被测试断言的"空类"（`Assets.test.tsx:373-378`）。

### 3.12 图标 / 动效 / 密度

- **零图标体系**：0 个 `<svg>`、0 图标库；唯一"图标"是登录页密码可见性 emoji `🙈/👁`（`Login.tsx:53`）。动作可发现性全靠中文文案。
- 动效仅 `job-progress-fill` 的 `transition: width 0.2s`（`styles.css:257-261`）；无 hover 态（仅 `.password-toggle:hover`，`:211-213`）、无 `prefers-reduced-motion`、无暗色（`:2` 只 `color-scheme: light`）。
- 密度偏松（卡片 padding 16/margin 12，`:103-109`；cell padding 6/8，`:125-131`），无"紧凑/舒适"切换，也没有侧栏让主内容更宽。

---

## 4. 响应式现状

### 4.1 事实

- `styles.css` 404 行中 **`@media`=0、`@container`=0、`clamp()/min()/max()` 视口函数=0**（全量核对）。
- 唯二自适应：`kpi-grid` 的 `auto-fit minmax(180px,1fr)`（`:383-388`）与 `filters` 固定 4 列（`:342-346`）。
- 内容区固定 `max-width:1080px`（`:54-58`）、认证页 380px（`:110-113`）。
- 表格**无** `overflow-x:auto` 包裹（全 CSS 仅 `.job-progress-bar` 用 `overflow:hidden`，`:251-256`）；`.btn-ghost`/`.badge` 有 `nowrap`（`:73-77,275-283`），其余任意换行。

### 4.2 各断点具体问题（估算）

| 视口 | 内容可用宽 | 问题 |
|---|---|---|
| **390** | 354px | ① header 最小宽约 600px → 横向溢出 ~250px（`:42-53`）；② Rounds 13 列 min-content ≈900px+ → 整页横向滚动（无 overflow 容器）；③ filters 4 列每列约 77px，`date/select` 不可用（`:342-346`）；④ 15 KPI 逐行堆叠成超长页；⑤ 触控目标约 32px（`.btn` padding 6/12，`:61-68`），低于 44px 建议 |
| **768** | 732px | ① 表格仍无横向滚动，13/11 列被压缩 → 中文表头逐字换行、数字与日期错行；② filters 每列约 175px 勉强可用，Rounds 4 个动作按钮挤一行（`:230-250`）；③ header 已无余量 |
| **1024** | 988px | ① max-width 1080 未触发，13 列每列约 76px，`"收益率（小数）"`≈84px → 表头换行；② 大额数字（如 `-123456.78`≈78px）临界；③ 无 iPad 触控优化 |
| **1440** | 1080px（被截断） | 左右各 180px 空白；15 KPI 仍按 5/3、5/2 折行（1080/194≈5 列），大屏不增密 |
| **1920** | 1080px | 左右各 420px 空白（内容仅 56%）；表格不获额外列宽，无多栏/主从可能 |

> 结论：当前**不是"响应式不完善"，而是"没有响应式"**。需先定 PC 优先 + iPad 的断点策略（建议 1280/1024/768），并把表格降级模式（列优先/卡片化/横向滚动+冻结首列）列硬验收。

### 4.3 表格溢出机制

1. `.table{width:100%}`（`styles.css:119-124`）只约束"期望宽度"；`table-layout:auto`（未声明）下按 **min-content** 排版：中文表头最小 1 字宽，英文/数字 token 不换行（`trd_buy_1`、`-123456.78`、`0.40625000`）。
2. 13 列×最小内容宽 > 容器，且无 `overflow-x` 包裹层 → **溢出传导到 `body`**，整页横向滚动（iOS 还会弹性回弹）。
3. `.app-main` 只限 `max-width` 不限 `min-width`、不裁剪；无 `table-layout:fixed`、无 `word-break`。

### 4.4 filters 网格

`repeat(4, minmax(0,1fr))`（`styles.css:342-346`）与控件数不匹配：Rounds 4 个刚好（`Rounds.tsx:204-229`），Nav/Metrics 只有 1–2 个却各占 1/4（`Nav.tsx:183-191`、`Metrics.tsx:272-305`）；无断点 → 小屏压扁。

---

## 5. 无障碍与键盘现状

### 5.1 语义结构

| 项 | 现状 | 证据 |
|---|---|---|
| 文档语言 / 地标 | ✅ `zh-CN`、`<header>/<nav>/<main>` | `index.html:2`、`router.tsx:31-57` |
| 导航可访问名 | ❌ `<nav>` 无 `aria-label` | `router.tsx:33` |
| 标题层级 | ⚠️ Trades h1→h3（无 h2）；Metrics/Rounds/Assets 只有 h1 无 h2 | `Trades.tsx:149`、`Metrics.tsx:265` |
| 表格 | ❌ 无 `<caption>`、`<th>` 无 `scope`；RoundDetail 用 th+td 拼 2×4 信息表 | `ui/Table.tsx:22-25`、`RoundDetail.tsx:86-115` |
| skip-link / document.title | ❌ 均无 | 全仓；`index.html:6` |
| 图片 | 0 `<img>`，无 alt 问题 | — |

### 5.2 ARIA（全部 10 行 / 11 个属性）

`ProblemView role="alert"`（`ui/Problem.tsx:43`）｜`RevokeBatch role="group"+aria-label`（`ui/RevokeBatch.tsx:41`）｜`Nav` 的月份/日期 `aria-label`（`Nav.tsx:189,282`）｜`Login` 密码可见性 `aria-label`（`Login.tsx:51`）｜`Assets/Metrics` 按钮 `aria-busy`（`Assets.tsx:361`、`Metrics.tsx:285`）｜3 处数据缺失提示 `role="alert"`（`Import.tsx:165`、`Imports.tsx:172`、`RoundDetail.tsx:78`）。

**缺失**：`aria-current`（导航/分页）、`aria-live`（notice 成功提示、JobProgress 进度）、`role="progressbar"`+`aria-valuenow`（`ui/JobProgress.tsx:72-77` 只有 div）、`aria-sort`（无排序）、`aria-expanded`（无折叠/抽屉）、`aria-describedby`（字段错误）、`aria-pressed`。

### 5.3 焦点 / 键盘 / 触控

- **无任何 `:focus`/`:focus-visible` 自定义样式**（`styles.css` 全量核对），依赖 UA 默认轮廓；无快捷键、无 roving tabindex、无 Esc、无焦点陷阱（无 dialog）。
- 2 处原生阻塞 `window.confirm`（`Trades.tsx:68` 软删、`Settings.tsx:50` 撤销 PAT）：不可样式化、测试需 mock，且与 `RevokeBatch` 内联确认（`ui/RevokeBatch.tsx:40-49`）不一致。
- 触控目标：`.btn` 高约 32px（`styles.css:61-68`）、导航链接纯文本约 20px 高（`:50-53`）——iPad 偏小。
- `type=month` 在 Safari/iPadOS 不支持（§8.5）；`prefers-reduced-motion`/`prefers-color-scheme` 均未处理（`:2`）。

### 5.4 数字可读性（无障碍视角）

无 `tabular-nums`、数字列左对齐（`styles.css:129`）、小数不对齐；无千分位/货币符/正号（`Dashboard.tsx:59-62` 原样输出 `"170620.00"`），而共用剧本要求 `¥1,284,360.00`/`+¥236,180.00`（`00-brief-demo-data.md:22-30`）——这是"金额不可扫读"的功能缺口，不是美术问题。

---

## 6. 数据展示痛点（逐条证据）

1. **9 项顶部导航拥挤且无层级**：9 平铺链接无分组/激活态/折叠（`router.tsx:33-43`），390 下溢出；深层页不显示所属层级（`RoundDetail.tsx:56-58` 仅"← 返回回合列表"）；首页默认 Dashboard（`:73`）但"看板"与其他页等权（`router.test.tsx:26`）。
2. **信息层级扁平**：每页都是"标题+灰说明+卡片流"；`Dashboard.tsx:164-385` 有 1 h1+4 h2+5 h3+4 `.card`，其中"最大回撤"**卡片套卡片**（`:286` 的 `.card` 位于 `:265` 的 section 内）；字号只有 4 档、所有元素同视觉重量。
3. **无趋势可视化**：净值只有逐日表格（`Nav.tsx:259-264`）+5 列 summary（`:216-244`），回撤只有文字区间（`Dashboard.tsx:286-307`）；0 个 SVG/canvas/图表库；FR-D5 趋势 API 也不存在（`PRD-v2.md:456-460`）→ 前端即使想画也无数据源。
4. **KPI 无上下文**：15 个 KPI 只有标签+值+少量 hint（`Dashboard.tsx:64-82`，唯一 hint 是"平仓 N 笔"`:272`），无环比/峰值/占比/中位数；无洞察/结论区（设计文档 `:129` 要求可空占位）。
5. **表格是唯一表达**：15+ 张表格，无条形/进度/热力/分布等替代编码（唯一图形化是 JobProgress 进度条与徽标）；持仓"权重"只给字符串（`Dashboard.tsx:247`）无权重条；最赚/最亏是纯 `<ul>` 文本（`:316-328`）。
6. **数字不可扫读/格式不统一**：无千分位与货币符（`Dashboard.tsx:59-62`）；小数位 2/6/8 位混排（`Rounds.tsx:174`、`Nav.tsx:219-240`）；同概念三形态（`10.00%` vs `0.40625000` vs `0.10000000`）；表头用技术口径"TWR（累计，小数）"（`Nav.tsx:219`）；状态列混在数字列尾（`Assets.tsx:299-337`）。
7. **裸术语直接展示（FR-D3 违约但被测试锁定）**：`Metrics.tsx:221` 把 `metric_id` 当链接文本、`Dashboard.tsx:365` 旧指标表同理；PRD 要求"页面不出现裸术语"（`PRD-v2.md:446`），但测试断言 `"perf.total_return"` 可见（`Dashboard.test.tsx:314`）→ 改中文名必须同步改测试与后端 explain。
8. **状态语义不清**：Dashboard 同屏展示"构建状态"+"页面状态"两个徽标（`Dashboard.tsx:176-180`），`build_state` 还决定警告横幅（`:182-186`）；7 套词表（§3.7）。
9. **时间口径混杂**：Dashboard 单页三套窗口（最新快照/全历史/`legacy_period`，`Dashboard.tsx:172-175`）；其他页各自一段（Nav/Metrics/MetricDetail 用 `period`，Rounds 用 from/to，Trades/Assets 无日期过滤）；**没有全局时间范围**。另有真实时区 bug：`Metrics.tsx:85`、`MetricDetail.tsx:33` 用 `new Date().toISOString().slice(0,7)`（UTC 月份），而 `Nav.tsx:27-31` 用本地月份——月初 0:00–8:00 会得到上一个月。
10. **单位/口径脚注散落**：`UNIT_LABELS` 两份重复（`Dashboard.tsx:35-41`、`Metrics.tsx:39-45`，后者被 `MetricDetail.tsx:7` 跨页 import）；口径说明是页面顶部 muted 长文（`Nav.tsx:177-180`、`Rounds.tsx:198-201`、`Assets.tsx:351-353`），无 tooltip/脚注系统。

---

## 7. 交互痛点

1. **无全局搜索/命令面板**：唯一搜索是 Rounds 代码筛选且需点"查询"（`Rounds.tsx:204-212,231-233`）；Trades 无代码/日期/方向筛选（`Trades.tsx:23-46`）；全仓无 `keydown` 监听。
2. **无统一日期范围**：4 种形态并存——`type=month`（`Nav.tsx:185`、`Metrics.tsx:274`、`MetricDetail.tsx:109`）、双 `type=date`（`Rounds.tsx:224,228`）、无（Trades/Assets）、只读展示（`Dashboard.tsx:172`）；仅 Nav 校验格式（`Nav.tsx:33,55-58`）。
3. **无批量操作**：Trades 逐行编辑/软删（`Trades.tsx:100-132`）、恢复前须勾"包含已删除"（`:139-142`）；Imports 只能整批撤销（`Imports.tsx:116`）；无多选/批量导出。
4. **无抽屉/侧栏，详情三种模式**：独立路由（`RoundDetail`/`MetricDetail`，`router.tsx:77,81`）、同页内联卡片（`Imports.tsx:146-247`）、同页 select 切换（`Nav.tsx:277-309`）；无统一交互语言，详情 URL 也不同步（`Imports.tsx:50` 只读初始化）。
5. **刷新/构建动作分散**：见下表——同类动作 5 种命名，且"刷新"在 Nav/Metrics 实际写库（`http.ts:611,660`）。
6. **反馈不一致**：`.notice` 永不自动消失/不可关闭（`Assets.tsx:375-379`、`Rounds.tsx:253`、`Metrics.tsx:313-317`、`Import.tsx:198`）；失败可能双提示（`Assets.tsx:230-232`）；确认交互两套（§5.3）。
7. **Job 轮询/SSE 三处复制**：`Assets.tsx:199-263`、`Rounds.tsx:87-137`、`Metrics.tsx:120-183` 各自实现"接管在跑 job→800ms 轮询→SSE done→`handledJobRef` 去重→重拉/兜底 Problem"；三份 `TERMINAL`（`:107`/`:43`/`:15`）与三份 `setTimeout(...,800)`（`:255`/`:129`/`:175`）逐字重复。`JobProgress` 只管 SSE、失败重试 5 次后静默（`ui/JobProgress.tsx:47-60`），轮询兜底被迫由页面承担。
8. **编辑/删除无撤销**：Trades 编辑无 busy（`Trades.tsx:165-172`），软删不可撤销（`:72`）；PAT 明文只显示一次但无复制按钮/确认（`Settings.tsx:86-92`）。
9. **URL 状态不足**：可分享状态仅 4 页（§2.5）；筛选/分页丢刷新；`RoundDetail.tsx:57` 固定回列表丢筛选。
10. **无并发/冲突提示**：409 走通用 ProblemView（`Trades.tsx:61-64`），无"重新加载最新版本"专用路径。

| 页面 | 动作 | 位置 |
|---|---|---|
| Dashboard | 刷新 / 加载月度指标 | `Dashboard.tsx:168-170,346-348` |
| Nav | 构建净值与收益（GET 带 UPSERT） | `Nav.tsx:193-195` |
| Assets | 构建/更新资产表 + 刷新 | `Assets.tsx:357-368` |
| Rounds | 查询/清空/刷新/重建回合 | `Rounds.tsx:231-249` |
| Metrics | 月份/构建复盘数据/刷新/含探索指标 | `Metrics.tsx:272-305` |
| MetricDetail | 月份/刷新 | `MetricDetail.tsx:107-122` |
| Trades/Imports/RoundDetail/Settings | **无刷新**（Trades 靠过滤变化触发） | — |

---

## 8. 重构约束清单

### 8.1 零口径（第一约束）

规则：金额/数量/价格/比率一律 `string`，业务页面禁止 `parseFloat/parseInt/Number()/toFixed`（`frontend/AGENTS.md:17-31`）；前端不得实现指标计算与结论拼接（`PRD-v2.md:432`）。**当前合规**：业务源码 0 命中。灰色地带：

1. `signedClass()` 用字符串前缀/正则判正负（`Nav.tsx:20-24`、`Rounds.tsx:28-32`、`Assets.tsx:58-62`、`RoundDetail.tsx:9-13`）——不涉算术，但是"解析后端格式化字符串"，4 份且对 `"0.00%"/"-0.00"` 处理不一致（Assets 修过 Nav 的 bug，`Assets.tsx:55-57`）；
2. `JobProgress` 的 `progress` 是 number（`:13`）→ `${progress}%`（`:73`）——进度非业务数值，需在规则里写明边界；
3. 前端按金额排序会用字典序（`"999.00" > "1000.00"`）→ 排序必须由后端 `sort` 承担（当前仅 trades/assets 支持，`http.ts:130,175`）。

### 8.2 图表/表格的数值几何冲突（核心难点）

| 需求 | 需要的数值 | 与零口径冲突 | 建议 |
|---|---|---|---|
| 净值曲线/面积图 | y=净值/总资产，x=日期 | `unit_nav:"10000.000000"`、`total_assets:"55000.000"` 是字符串，手写 SVG 必须转 number | **契约先行**：后端在 `/nav` 增补图表就绪字段（如 `series[{x_date,y_nav_bp:int}]`），前端只画；或拍板"`ui/charts/` 为唯一允许 `Number()` 的白名单 + ESLint/测试" |
| KPI sparkline | 月度序列 y | Dashboard 无时间序列字段（只有聚合） | 需新趋势 API（FR-D5）或后端 `sparkline[]`；不要前端用 `/metrics` 逐月拼 |
| 持仓权重条 | width=`weight_pct` | `"37.05%"` 字符串 | 后端加 `weight_bp:int`（基点整数），或 CSS `width:var(--w)` 由后端字符串注入（仍需解析） |
| 回撤区间高亮 | 日期区间 | 无冲突：起止日期映射 x 轴下标（数组索引非业务数值） | 推荐此路径 |
| 表格数值排序/对齐 | 比较大小 | 字符串比较不可靠 | 排序交后端；对齐用 `tabular-nums`（纯 CSS） |
| 正负着色 | 符号位 | 字符串前缀在 0 值/百分比/科学计数上易错 | 后端返回 `sign` 或统一格式；前端只读符号 |

> **"画图"与零口径的冲突不能靠前端内联计算绕过**：要么后端补图表就绪数值，要么设受限白名单模块，必须在重构设计阶段拍板，否则每个图表都会成为门禁例外。

### 8.3 契约先行 / 自动生成

`packages/api-client/src/api/generated/schema.d.ts`（5,842 行）由 `make sdk` 生成、**禁止手改**（`contract.ts:1-17`、`frontend/AGENTS.md:34-40`）；前端唯一入口是 `contract.ts`（re-export + 别名 + 40+ 编译期断言，`:288-423`）。接口变更顺序：API-Catalog → `make api-snapshot` → `make api-check` → `make sdk` → `tsc`（根 `AGENTS.md:56-66`、`Makefile:209-211`）。新增字段（图表数值/趋势/状态两轴）必须先走后端契约，否则 `contract.ts` 断言让 tsc 失败。

### 8.4 测试锁定（最大成本项）

| 指标 | 数值 | 证据 |
|---|---|---|
| 测试文件 / 用例 / `expect` | 12 / 61 / 287（api-client 另 2 文件 15 用例 68 断言） | `apps/web/src/**/*.test.tsx`、`packages/api-client/src/*.test.ts` |
| `getByText`+`findByText` / `getByTestId`+`findByTestId` / `getByRole` | 77+35 / 43+18 / 35 | 各 test 文件 |
| 覆盖的 testid | 37 个不同值（Dashboard 38 次） | `Dashboard.test.tsx:224-318` |
| 精确 className 断言 | ✅ `"money"`/`"money money-neg"`/`"badge-blocked"` | `Assets.test.tsx:373-392` |
| 零口径源码断言 | 仅 2 文件、`?raw` 单文件扫描 | `Dashboard.test.tsx:10,320-325`、`Assets.test.tsx:16,554-560` |
| **无测试页面** | Trades / Settings / Login / Register | 无对应 test 文件 |

锁定内容：导航前 5 项顺序与 href（`router.test.tsx:24-30`）；Dashboard 15 KPI testid/文案/`"—"` 兜底/missing `data-code`；Assets 按钮状态机与着色（`Assets.test.tsx:229-318,373-392,495-535`）；Rounds/Imports/Import/RoundDetail/MetricDetail 整句文案；ProblemView 六字段（`Problem.test.tsx:27-32`）。

### 8.5 无图表库 / 无 UI 库 / iPad 兼容

现状 0 图表库、0 UI 库，`ui/` 全手写 365 行。引入须同时满足体积预算（§1.4）、React 18、jsdom 可测、a11y 可控、不破坏零口径。**iPad 风险**：`input type=month` 在 Safari/iPadOS 不支持（`Nav.tsx:185`、`Metrics.tsx:274`、`MetricDetail.tsx:109`），仅 Nav 有格式校验；重构须提供可用降级（两个 select 或文本+正则）。

### 8.6 单文件 CSS 拆分边界

全局类名无命名空间/无 `@layer`/无语义变量（§3.11），由 `main.tsx:4` 全局引入。测试锁定了 `.money/.money-pos/.money-neg/.badge-*` 类名（§8.4）→ 拆分必须**保留这些 class**（或同步改测试）；`.money` 虽无样式但被断言，不能直接删。建议：先抽 tokens（颜色/间距/字号/圆角）到 `:root`+语义变量，再按 `layout/table/form/badge/feedback` 分文件，`.table/.btn/.card/.badge-*` 保留兼容层，新类走前缀，避免一次性重写触发大量测试/视觉回归。

### 8.7 无障碍基线

建议目标 WCAG 2.2 AA（PRD 未给级别，需 Lead 拍板）。最低交付：表格 caption/scope、`aria-current` 导航、`aria-live` 成功/进度、progressbar、focus-visible、触控 ≥40px、对比度 ≥4.5（修 `badge-pending`）、`prefers-reduced-motion`、`document.title`、skip-link。

### 8.8 文档漂移清单（先修文档，避免按错文档写代码）

| # | 漂移 | 文档位置 | 实际 | 建议 |
|---|---|---|---|---|
| 1 | TanStack Query 已使用 | `frontend/AGENTS.md:13,84-95` | 未安装、无 QueryClient | 改文档或引入（建议先改文档，记录现状 useState/useEffect） |
| 2 | lint = eslint | `frontend/AGENTS.md:121` | `pnpm -r lint`=tsc；根 AGENTS 已注明（`AGENTS.md:154`） | 改 frontend AGENTS 与 `Makefile:31` |
| 3 | 页面清单含 `TradeList.tsx` | `frontend/AGENTS.md:115` | 实际 `pages/Trades.tsx` | 更新 |
| 4 | 生成 SDK 路径 | `packages/api-client/src/index.ts:4`（"apps/web/src/api/generated（占位）"） | 实际 `packages/api-client/src/api/generated/schema.d.ts`；web 下只有空 `.gitkeep` | 改注释 |
| 5 | ECharts 技术约束 | `PRD-v2.md:430` | 未安装；设计 D-10 明确 v1 不引入 | 补丁豁免 |
| 6 | Playwright | `PRD-v2.md:492` | 无 e2e | 明确 SKIP 并记录 |
| 7 | 零口径 ESLint 已启用 | `frontend/AGENTS.md:32` | 未启用（根 `AGENTS.md:7` 记为暂停） | 注明"仅 2 个 `?raw` 测试兜底" |
| 8 | 状态两轴未落地 | 设计 D-12（`2026-10-28-dashboard.md:35`） | 页面各自映射（§3.7） | 重构时统一 |

---

## 9. 可复用资产与保留/重写边界

### 9.1 建议保留（已被测试/契约覆盖）

| 资产 | 规模 | 保留理由 | 证据 |
|---|---|---|---|
| `packages/api-client/src/http.ts` | 729 行 | Bearer、7807、401 刷新重放、SSE 断线续传、Idempotency-Key、If-Match；8 单测 | `http.ts:305-720`、`http.test.ts` |
| `packages/api-client/src/contract.ts` | 423 行 | 生成类型唯一入口 + 40+ 编译期断言 | `contract.ts:288-423` |
| `store/session.ts` | 70 行 | `useSyncExternalStore` 订阅式会话，无依赖 | `session.ts:1-70` |
| `router.tsx` RequireAuth+骨架 | 25 行 | 守卫简单可靠，3 测试 | `router.tsx:20-24`、`router.test.tsx:33-74` |
| `ui/Problem.tsx` | 70 行 | RFC 7807 人话渲染，质量最高 | `Problem.tsx:39-70` |
| `ui/JobProgress.tsx` | 82 行 | SSE+重试，保留但需补 a11y 与轮询职责 | `JobProgress.tsx:12-82` |
| `ui/Cursor.tsx` / `Uploader.tsx` | 23/31 行 | 简单控件 | `Cursor.tsx:10-23`、`Uploader.tsx:9-30` |
| `ui/RevokeBatch.tsx` / `QuarantineList.tsx` | 57/64 行 | 内联确认（优于 confirm）/隔离区分页 | `RevokeBatch.tsx:18-56`、`QuarantineList.tsx:20-63` |
| `pages/ComingSoon.tsx` | 39 行 | 占位契约（`data-dependency`/`data-feature`，无假数据） | `ComingSoon.tsx:15-39`、`ComingSoon.test.tsx` |
| 测试基建 | — | `vi.mock("../client")` + fixture builder 模式 | `Dashboard.test.tsx:12-108`、`Assets.test.tsx:19-120` |

### 9.2 建议重写（视觉/骨架层）

| 对象 | 现状问题 | 处置 |
|---|---|---|
| Layout+导航 | 9 项平铺、无激活态/分组/侧栏/移动端 | 重写为侧栏+分组导航+占位入口；保留路由与 `RequireAuth`；注意导航前 5 项顺序被测试锁定 |
| `styles.css` | 无 token/响应式/层级 | 拆 tokens+布局+组件；保留兼容类名（§8.6） |
| `ui/Table` | 无排序/密度/冻结/空态 CTA/caption | 升级 `DataTable`（align/density/sortKey/width/sticky），保留 API 兼容或一次性迁移并同步测试 |
| KPI 卡 | 14px 值、无上下文/趋势 | 抽 `ui/Kpi`（大数字/tabular/趋势/对比槽），保留 testid 与文案 |
| 徽标 | 20 变体/7 套映射/语义不可辨 | 统一两轴状态模型 + `StatusBadge` + token 色板；`badge-*` 保留兼容 |
| 页面数据层 | 14 页手写 loading/error/notice（state 100+ 处） | 抽 `useApiQuery`/`useJob` hook，统一 loading/error/empty/refetch；轮询收敛为 1 处 |
| 7 处手写 table / 3 种详情模式 | 与组件不一致、交互语言分裂 | 统一表格变体；详情定"路由+侧栏预览"或 Drawer 一种主模式 |

### 9.3 页面逐一处置

| 页面 | 骨架 | 视觉 | 交互 | 测试影响 |
|---|---|---|---|---|
| Dashboard | 保留两板块 | Bento/主从（KPI+主图+持仓+历史） | 时间范围、趋势、洞察卡 | 15 KPI testid 全锁 → 保留 |
| Trades | 保留 | 表格重做、编辑改 Drawer | 筛选（日期/方向/代码）、批量、撤销 toast | **无测试**（需补） |
| Rounds | 保留 | 宽表重做（列优先/冻结） | 筛选进 URL、排序 | 文案/链接锁 |
| RoundDetail | 保留 | 描述列表/卡片重排 | 返回保留筛选 | 数值/文案锁 |
| Nav | 保留 | 加曲线+持仓分布 | 月份控件兼容 iPad | `nav-*` testid 锁 |
| Assets | 保留 | 汇总条分层、表格重做 | 刷新/构建语义分离（构建=写、刷新=读） | 16 测试最重（按钮状态机/className） |
| Metrics / MetricDetail | 保留 | 按 domain 分组、三段式排版 | 探索指标位置、口径抽屉 | `metric-*` testid 锁 |
| Import / Imports | 保留 | 预览表、进度卡、详情侧栏化 | 分步、错误恢复、URL 同步 | 文案锁（7/5 用例） |
| Settings / Login / Register | 保留 | PAT 卡片化+复制、auth 卡统一 | 其他设置占位、autocomplete | **无测试**（可自由重构，需补） |

---

## 10. 重构风险与验收建议

### 10.1 风险矩阵

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | 破坏 287 个断言（文案/testid/className） | 高/高 | 分阶段替换；保留 `data-testid` 与关键文案；保留 `money/badge-*` 兼容类 |
| R2 | 零口径被图表需求打破 | 高/高 | 先拍板路线（后端图表字段 vs 白名单）；把零口径测试升级为全目录扫描（否则拆文件后失效） |
| R3 | 绕过契约先行（前端手写类型/字段） | 中/高 | 新字段走 API-Catalog→openapi→`make sdk`；同步改 `contract.ts` 断言 |
| R4 | 体积超预算（图表/UI 库） | 中/中 | 首屏 gzip ≤150KB；路由级 `lazy()`；先测后引 |
| R5 | iPad 兼容（month 控件/32px 触控/表格溢出） | 高/中 | 真机验收；390/768 截图；控件降级 |
| R6 | 状态语义继续分裂 | 高/中 | 建 `status.ts` 单一词表+两轴模型，页面不再自建 map |
| R7 | Job/轮询回归（3 处复制） | 中/高 | 抽 `useJob`，用现有 Assets/Rounds/Metrics 测试做回归网，先抽不改行为 |
| R8 | 无前端 CI → 回归无人发现 | 高/中 | 新增 workflow：`pnpm -r typecheck && pnpm -r test`（`frontend/**` 触发）；`make test` 补前端 |
| R9 | 无测试页面被静默改坏 | 中/中 | 重构前补 Trades/Settings/Login/Register 行为测试（happy path+错误态） |
| R10 | 文档漂移误导实现 | 高/低 | 先修 §8.8 清单（1 个 doc PR），再动代码 |

### 10.2 演进策略（每阶段可独立验收）

1. **阶段 0 门禁前置**：修文档漂移；零口径静态断言升级为全 `src` 扫描（排除测试与白名单）；CI 加前端 job；记录基线（61+15 用例、tsc 0 error、dist 体积、5 档宽度截图）。
2. **阶段 1 tokens+布局壳**：`:root` 语义变量 → 侧栏/顶栏 Layout 重写（保留路由/守卫/导航 href 与顺序）→ `document.title`、focus-visible、skip-link。
3. **阶段 2 基础组件**：`DataTable`（保留 `.table` 兼容）、`Kpi`、`StatusBadge`、`EmptyState`、`Skeleton`、`Notice`（aria-live）、`Drawer`；每组件配单测。
4. **阶段 3 数据层收敛**：`useApiQuery`+`useJob`；先在 Metrics/Assets/Rounds 替换（有测试保护），再替换其余。
5. **阶段 4 页面重排**：Dashboard（Bento/主从+趋势，依赖后端图表字段）→ Rounds/Trades 宽表 → 详情页 → Import/Imports → Settings/认证页（补测试）。
6. **阶段 5 响应式+a11y 收口**：5 档宽度截图；对比度/focus/触控/aria 清单逐项过；iPad 真机。

### 10.3 验收建议

- **基线快照**：重构前 `pnpm -r test` 全绿、`tsc` 0 error、dist 体积、390/768/1024/1440/1920 截图。
- **测试策略**：不改断言语义；仅允许"组件迁移导致的查询方式更新"（如 `getByText`→`getByRole`），PR 中逐条列出；补 4 个无测试页面。
- **零口径门禁**：全 `apps/web/src/**/*.tsx` 扫描（排除 `*.test.tsx` 与白名单）；`signedClass` 收敛为 1 份并加单测。
- **a11y**：至少加 `vitest-axe` 冒烟；人工键盘走通"登录→看板→下钻→返回"。
- **视觉回归**：用现有 headless Chrome 截 5 档宽度，重点 13 列表格/KPI 网格/header 溢出。
- **性能**：首屏 gzip ≤150KB、Dashboard 首屏 ≤1 请求（API-084）+按需 1；lazy 后逐页度量 chunk。
- **兼容**：Safari 桌面+iPadOS 实测 month/date 控件、触控尺寸、表格横向滚动。

### 10.4 建议新增门禁（供 Lead 决策）

| 门禁 | 现状 | 建议 |
|---|---|---|
| 前端 CI job | 无 | `frontend/**` 触发 `pnpm -r typecheck && pnpm -r test` |
| `make test` | 仅 pytest | 追加前端（或拆 `make test-web`） |
| 零口径静态检查 | 2 个 `?raw` 测试 | 全目录扫描脚本 + `make lint` 调用 |
| ESLint | 无 | flat config + `no-restricted-syntax` 实现零口径（P2-15 既定目标） |
| 组件测试 | 仅页面级 | 新增 `ui/` 组件单测 |

---

## 附录 A · 关键行号速查

| 文件 | 关键行 |
|---|---|
| `router.tsx` | 20-24 守卫；27-60 Layout；33-43 导航；44-53 登出；62-117 路由；84-113 占位；117 catch-all |
| `styles.css` | 1-8 变量；14-20 body；42-58 布局；61-77 按钮；88-101 输入；119-134 表格；251-261 进度；274-335 徽标；336-341 正负色；342-352 filters；383-404 KPI |
| `Dashboard.tsx` | 12-41 映射；59-82 Value/Kpi；113-158 加载/错误；166-201 头部；203-263 资产；265-339 历史；341-385 月度 |
| `Nav.tsx` | 8-41 状态/月份；54-90 构建；92-149 列；174-204 筛选；216-264 表；267-309 持仓 |
| `Assets.tsx` | 20-105 标签/工具；114-157 汇总条；170-263 加载/接管/轮询；273-346 构建/列/空态 |
| `Rounds.tsx` | 16-41 标签/过滤；58-154 加载/轮询/重建；156-186 列；203-266 筛选/表/分页 |
| `Metrics.tsx` / `MetricDetail.tsx` | Metrics 17-45 标签、96-183 加载、214-255 列、270-311 筛选；MetricDetail 40-64 加载、128-187 三段式 |
| `Trades.tsx` / `Settings.tsx` | Trades 23-88 操作、90-133 列、147-179 编辑；Settings 15-58 加载/创建/撤销、65-124 表单/列表 |
| `Import.tsx` / `Imports.tsx` | Import 21-87 流程、95-202 UI；Imports 57-120 加载/列、146-247 详情 |
| `ui/*.tsx` | Table 16-38；Problem 4-70；JobProgress 12-82；Cursor 10-23；Uploader 9-30；QuarantineList 20-63；RevokeBatch 18-56 |
| `router.test.tsx` | 24-30 导航锁定；33-74 守卫 |
| `Dashboard.test.tsx` | 224-318 KPI 断言；314 裸指标 ID；320-325 零口径 |
| `Assets.test.tsx` | 229-318 按钮状态机；373-392 className；495-535 a11y/接管；554-560 零口径 |
| `packages/api-client/src/http.ts` | 60-65 Problem 判定；305-720 传输层；611/660 写副作用注释；717 命名空间 |
| `packages/api-client/src/contract.ts` | 1-17 约定；288-423 编译期断言 |
| `frontend/AGENTS.md` / `PRD-v2.md` | AGENTS 13/17-32/84-95/115/121；PRD 208/430-432/446/456-460/492/495 |
| `docs/architecture/2026-10-28-dashboard.md` | 33 D-10；35 D-12；45/47 B-04/B-06；59 C-04；85-101 15 字段；117-131 窗口/合并；126 抽屉→详情页 |

## 附录 B · 未覆盖/待确认

1. **未实测浏览器渲染**：各断点问题基于 CSS/DOM 推算（已标"估算"），阶段 5 需截图实测。
2. **后端图表字段**（`series`/`weight_bp`/`sign`）是否易加：本次未展开后端路由，需契约设计阶段确认。
3. **红涨绿跌**是否切换：产品决策（见调研文档），本审计只记现状。
4. 未审计队友产物（`02-design-research.md`、`mockups/`、`previews/`），也未据此评价视觉方案。
