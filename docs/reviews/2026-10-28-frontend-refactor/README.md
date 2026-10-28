# 前端重构调研与三方案提案

> 日期：2026-10-28 ｜ 作者：AI（Team Lead + 3 名队友）｜ 状态：**调研/提案，未改任何生产代码**
> 范围：`frontend/apps/web`（React 18 + Vite + 手写 CSS）｜ 目标：数据展示与交互更好、PC 优先并兼容 iPad、现代化/极简、适合个人深度思考分析
> 产出物：
> - 三套高保真预览（HTML 可交互静态稿 + PC/iPad 截图）：[方案 A](#4-方案-a-静观-quiet-analyst)、[方案 B](#5-方案-b-夜盘-night-desk)、[方案 C](#6-方案-c-bento-现代产品风)
> - 现状审计：[01-current-state-audit.md](01-current-state-audit.md)（512 行，逐条 `文件:行` 证据）
> - 外部设计调研：[02-design-research.md](02-design-research.md)（趋势、案例、技术选型，带来源链接）
> - 预览图共用剧本：[00-brief-demo-data.md](00-brief-demo-data.md)（三方案同数据、同信息架构，保证可比）
>
> ⚠️ 预览图是**静态高保真稿**（HTML 自包含、无外网资源），用同一份演示数据渲染；不是已接入真实 API 的可运行页面。
>
> **快速对比**：[首屏三方案对比图](previews/comparison-hero.png) · [整页三方案对比图](previews/comparison-full.png)

---

## 0. 结论先行

### 0.1 一句话推荐

**推荐以方案 A「静观」为骨架做主界面，吸收方案 B 的「右侧 Inspector + 键盘优先 + 密度切换」，用方案 C 的组件形态（圆角、分段控件、柔和层次）做局部现代化——即“A 的呼吸感 + B 的工作台效率 + C 的产品精致度”。B 的暗色作为可选 `data-theme`（同一组件树、同一套 token 生成），而不是双份实现。**

> 这一组合与外部调研的结论一致：A 作默认基底、B 作为可选主题、C 只借 KPI/洞察卡语法（见 [02 §10](02-design-research.md)）。

如果必须三选一：
- **长期个人使用、每天深度复盘、最怕视觉疲劳 → 选 A。**
- **数据密度优先、常在夜间看盘、习惯键盘 → 选 B。**
- **要现代产品感、iPad 展示多、未来可能给别人用 → 选 C。**

### 0.2 为什么不是“直接换套 UI 库”

现状不是“皮肤旧”，而是四个结构性问题（证据见 [01 审计](01-current-state-audit.md)）：

| # | 问题 | 现状证据 | 重构要解决什么 |
|---|---|---|---|
| 1 | 信息架构扁平 | 顶部 9 项平铺、无分组/激活态（`router.tsx:33-43`）；15 个 KPI 无上下文 | 分组导航 + 全局时间范围 + 下钻链 |
| 2 | 数字不可扫读 | 无 `¥`/千分位/`tabular-nums`；同一收益率三形态（`10.00%` / `0.40625000`）；正负色与国际/A 股语义未定 | 统一数字排版 + 语义色 + 符号编码 |
| 3 | 无趋势可视化 | 0 个图表（`src` 内 0 `<svg>`），净值/回撤只有表格与文字 | 净值曲线、回撤带、sparkline、权重条 |
| 4 | 零响应式 | 404 行 CSS **0 个 `@media`**，固定 1080px；13 列回合表在 390/768 撑破页面 | PC 优先、1024/768 降级规范、Safari 兼容 |

因此本提案交付的是**设计系统 + 信息架构 + 组件改造 + 分阶段路线图**，预览图只是设计语言的视觉锚点。

---

## 1. 调研方法与输入

1. **代码基线**：通读 `frontend/apps/web` 全部源码与测试（约 4,056 行非测试源码 / 2,293 行测试），只读审计，不修改。
2. **产品基线**：`docs/architecture/2026-10-28-dashboard.md`（Dashboard 15 字段、状态两轴、D-01~D-13 决策）、`docs/prd/PRD-v2.md`（FR-D1/D3/D5、NFR）、`docs/architecture/2026-10-26-metrics-three-domain.md`（18 指标、状态语义）。
3. **外部调研**：2025–2026 数据产品设计趋势、金融数据可视化最佳实践、响应式/表格降级、图表与表格技术选型、WCAG 2.2 与性能基线；来源与结论见 [02-design-research.md](02-design-research.md)。
4. **方案表达**：三套静态高保真稿，共用同一份内容剧本与信息架构（[00-brief-demo-data.md](00-brief-demo-data.md)），在 1600 与 1024 两个宽度下截图，保证横向可比。

> 方法边界：未做用户访谈/可用性测试（个人项目、单用户）；未接入真实数据；未做浏览器矩阵实测（仅 Chromium 截图 + 规范推演）。这些列入 §12 验收阶段。

---

## 2. 现状诊断（摘要）

完整证据见 [01-current-state-audit.md](01-current-state-audit.md)，这里只列影响设计决策的部分。

### 2.1 值得保留的资产

- `packages/api-client/src/http.ts`：RFC 7807 解析、401 刷新重放、SSE 流，**重构不要动传输层**。
- `ui/Problem.tsx`、`ui/JobProgress.tsx`、`ui/Cursor.tsx`、`ui/Uploader.tsx`、`ui/RevokeBatch.tsx`：错误态/进度/游标/上传语义正确，重写样式即可。
- `store/session.ts`、路由守卫 `RequireAuth`、`FeaturePlaceholder`。
- 所有金额/比率已是后端定点字符串——**这是资产，不是包袱**。

### 2.2 必须重写的部分

| 层 | 处理 |
|---|---|
| `Layout`/导航 | 重写：分组侧栏 + 命令面板 + 面包屑 + `NavLink` 激活态 |
| `styles.css` | 拆分为 `tokens.css` + `base.css` + 按组件 CSS Modules；保留 `.money/.badge-*` 兼容类名（测试锁定，`Assets.test.tsx:373-392`） |
| `Table.tsx` | 升级为 `DataTable`：粘性表头、列优先级、密度、键盘行导航、数字右对齐、空/加载骨架；**排序/分页仍由后端** |
| 数据层 | 页面各自 `useState/useEffect` → 统一 `useQuery` 封装（TanStack Query 或轻量自研，见 §11） |
| 状态词表 | 7 套重复映射 → 一个 `status.ts`（两轴：build_state / data_status） |
| 页面 | Dashboard 重做；其余页做“组件级迁移”，不一次性重写 |

### 2.3 顺手要修的真实 bug（审计发现）

1. `Register.tsx:21-27` 的错误对象缺 `status` → `ProblemView` 渲染成通用错误。
2. `Metrics.tsx:85`、`MetricDetail.tsx:33` 用 `toISOString().slice(0,7)` 取 UTC 月份，与 `Nav.tsx:27-31` 的本地月份不一致（月初 0:00–8:00 会落到上月）。
3. 登出只清 localStorage，未调 `POST /auth/logout`（refresh token 不失效）。
4. iPadOS Safari 不支持 `input[type=month]`（3 处使用）→ 需降级为“年 + 月”双 select。

---

## 3. 外部设计调研结论（摘要）

> 完整来源、案例与技术选型见 [02-design-research.md](02-design-research.md)。以下为对本次决策最关键的部分。

### 3.1 趋势判断

1. **“安静界面”（quiet/minimal）成为数据分析工具的默认审美**：Linear 2024 重设计的目标是“降噪 + 更强层级 + 可调密度”，而不是删内容；配色由 `base/accent/contrast` 变量在 LCH 空间生成（[Linear changelog](https://linear.app/changelog/2024-03-20-new-linear-ui)、[重设计复盘](https://linear.app/now/how-we-redesigned-the-linear-ui)）。适合长时间阅读，和“个人深度思考”目标一致。
2. **高密度金融终端仍然成立，但只对“熟练用户 + 高频操作”成立**：暗色、等宽数字、多窗格、键盘优先；OpenBB 是公开可验证的终端式案例（[openbb.co](https://openbb.co/)），uPlot 官方也主张“无动画、数据优先”（[README](https://github.com/leeoniya/uPlot)）。代价是学习曲线与日间可读性。
3. **Bento 网格 = 现代 SaaS 的分组修辞，不是页面骨架**：适合 KPI/洞察/状态这类“一眼扫到”的模块，不适合承载长表格与连续时间序列（卡片内边距会吞噬密度、切割比较关系）。行业证据有限，见 [02 §1.3](02-design-research.md) 的未核实声明。
4. **图表不是装饰**：人对位置/长度的前注意加工最精确，图表应优先用线/条而不是饼图（[NN/g Dashboards](https://www.nngroup.com/articles/dashboards-preattentive/)）；净值的“形状”（趋势 + 回撤区间）比 15 个孤立 KPI 更能触发复盘直觉。
5. **响应式的关键不是断点数量，而是“信息降级顺序”**：平板先隐藏次要列、折叠侧栏，而不是整体缩放（[NN/g Mobile Tables](https://www.nngroup.com/articles/mobile-tables/)）；WCAG 2.2 1.4.10 要求 320px 下可重排阅读（[W3C Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)）。

### 3.2 对本项目的三条硬结论

1. **PC 优先 ≠ 忽略平板**：1024（iPad 横）必须保持“左导航 + 主区 + 右栏”三栏语义，只是右栏下沉；768（iPad 竖）退化为单列 + 折叠导航 + 表格列优先；<768 主从列表兜底（[02 §5.3](02-design-research.md)）。
2. **数字排版先行**：`font-variant-numeric: tabular-nums`（[MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/font-variant-numeric)）、右对齐、千分位、固定小数位、单位与符号分级；这一步的收益大于换任何配色。
3. **涨跌色必须显式决策**：现网是“绿涨红跌”（`styles.css:336-341`），与 A 股“红涨绿跌”相反；A 股惯例来自本地券商软件传统（[光明网](https://epaper.gmw.cn/wzb/html/2015-08/01/nw.D110000wzb_20150801_1-05.htm)）。建议做成 **语义 token（`--up/--down`）+ 默认红涨绿跌 + 设置可切换**，且颜色永不单独承载信息（WCAG 2.2 1.4.1，[W3C](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)），必须同时有 `+/-` 与 `▲/▼`。
4. **状态放 periphery**：构建/导入/降级用状态灯与常驻信息表达，不弹窗打断（Calm Technology 的 “smallest possible amount of attention / use the periphery”，[calmtech.com](https://calmtech.com/)）。

### 3.3 技术选型建议（详见 §11）

| 需求 | 推荐 | 不推荐 | 理由 |
|---|---|---|---|
| 样式 | CSS 变量 tokens + CSS Modules | Tailwind/shadcn 立刻全量引入 | 14 页规模、门禁严格、需保留测试锁定的类名；先 tokens 再谈框架 |
| 图表 | 自研轻量 SVG（折线/面积/回撤带/sparkline/条形）+ 1 个 scale 工具 | ECharts 全量 | 图表类型少、包体敏感、jsdom 可测；未来要 K 线/热力再引 ECharts 子模块 |
| 表格 | 原生 `<table>` + `DataTable` 封装 | AG Grid | 数据量小（个人）、无需虚拟滚动；AG Grid 重且授权复杂 |
| 数据请求 | TanStack Query（+12KB gzip）或轻量 `useResource` | 继续各页复制 useEffect | 统一缓存/失效/重试，减少 3 处 job 轮询复制 |
| 图标 | 自研 SVG sprite（现有 19px 线性风格） | 图标库全量 | 包体与一致性；约 20 个图标足够 |
| 命令面板 | cmdk（16.7KB gzip，MIT，带可访问 combobox） | 自研 keydown 轮子 | 调研实测；自研要重做焦点/aria，得不偿失（[cmdk](https://github.com/pacocoursey/cmdk)） |
| 抽屉/对话框 | 原生 `<dialog>` + 自管焦点，或仅装 Radix Dialog（12.9KB gzip） | 全量 UI 库 | 只装需要的 primitive（[Radix](https://www.radix-ui.com/primitives)） |
| 视觉回归 | Playwright 截图（可选，CI 另开） | 继续只靠 jsdom | 布局/响应式无法用 jsdom 验证 |

> 选型依据、Bundlephobia 实测体积与来源链接见 [02 §6](02-design-research.md)；其中明确列出“未核实项”，避免把无法验证的产品界面细节当依据。

### 3.4 证据分级

[02-design-research.md](02-design-research.md) 用 `[官方]/[产品]/[行业]/[本项目]` 四级标注证据，并声明 Bloomberg Terminal、TradingView、Koyfin、Observable 等站点在当前网络环境无法直连，因此不引用其具体 UI 细节。本报告只采纳能落到本仓约束的结论。

---

## 4. 方案 A ·「静观 Quiet Analyst」

> 气质关键词：**纸感、留白、编辑排版、低饱和、专注**。像一本可以交互的复盘笔记。
> 预览：[PC 1600×2351](previews/a-quiet-analyst-pc.png) · [iPad 1024×1600](previews/a-quiet-analyst-pad.png) · [HTML 稿](mockups/a-quiet-analyst.html)

### 4.1 设计理念

把“看数据”变成“读自己的交易”。主视觉不是卡片，而是**排版本身**：大号 tabular 数字、细线分隔、留白形成节奏；全页只保留一个深墨绿强调色，其余靠墨色深浅建立层级。适合每天固定时间坐下来、逐屏阅读与思考，而不是频繁点击的操盘台。

### 4.2 视觉语言

- **底色**：暖纸白 `#f6f5f0`，面板 `#fbfaf6`；**不用阴影**，用 1px 细线与留白分隔。
- **文字**：墨色 `#1b1d19` / 次级 `#3c4038` / 弱化 `#858a7e`；标题用宋体（Songti SC）营造编辑感，正文与数字用系统无衬线 + `tabular-nums`。
- **强调色**：深墨绿 `#3b5f4b`，只用于链接、当前态、净值线；正向 `#2f6b4f`，负向 `#a8483a`。
- **图形**：净值线 1.8px、面积透明度 10%、回撤区间用 10% 红填充 + 虚线；无渐变、无发光。
- **圆角**：仅 8–10px（按钮/搜索框），卡片本身直角或极浅圆角。

### 4.3 布局结构（PC）

```
┌──76px 图标栏──┬──────────────────────────────────────────────┐
│ 静            │ 顶栏：面包屑 · 搜索(⌘K) · 数据状态 · 专注/密度 │
│ 看板(激活)    ├──────────────────────────────────────────────┤
│ 交易流水      │ 页头：日期眉题 + 大标题 + 时间范围（下划线 tab）│
│ 回合          │ 英雄区：总资产 58px + 4 个细分统计（细线网格） │
│ 净值          │ 净值曲线（跨全宽，回撤区间高亮 + 标注）        │
│ 资产          │ ┌ 当前持仓（无边框表格+权重条）┬ 历史概览 ┐   │
│ 指标          │ └ 指标健康度（18 点阵）───────┴ 最赚/最亏 ┘   │
│ 导入/记录     │ 复盘线索（笔记/待办） + 快捷下钻               │
│ 设置/头像     │ footer：口径 · 快捷键提示                      │
└───────────────┴──────────────────────────────────────────────┘
```

### 4.4 交互设计

- **时间范围**用下划线 tab（全历史/本月/近一年/自定义），不做胶囊按钮，保持纸感。
- **专注模式**：隐藏图标栏与右侧栏，只留正文 + 图表；**密度切换**：舒适/紧凑两档行高。
- **复盘线索**：可勾选笔记 + 关联指标 + 一键写笔记；这是“深度思考”的核心落点。
- **快捷键**：`⌘K` 命令面板、`G D` 回看板、`F` 专注。
- **下钻**：表格行 hover 变强调色；指标/回合/资产均可点击进入详情。

### 4.5 数据展示亮点

1. 58px 总资产是唯一“超大数字”，其余次级统计不超过 25px——**层级极其明确**。
2. 净值曲线 + 回撤区间 + “39 天 / 57 天收复”叙事标注，把数字变成故事。
3. 持仓权重用**细线长度**编码（权重条），无表格边框，眼睛沿列即可比较。
4. 18 个指标用**点阵 + 四域分组**表达健康度，一眼看出“9 正常 / 4 阻断”。
5. 历史概览用**胜负平堆叠条**替代三个百分比数字。

### 4.6 响应式（iPad）

- 1024：图标栏 64px，英雄区上下堆叠，4 个统计横排；图表高度 232；两栏变单栏，历史概览下沉；表格隐藏“数量/成本”列；指标健康 2×2。
- 768（竖屏）：进一步把表格转为“标的 + 市值 + 权重”3 列，其余信息进行详情；导航改为顶部抽屉。

### 4.7 优点 / 风险 / 成本

| 维度 | 评价 |
|---|---|
| 数据展示 | ★★★★★ 层级最强，适合“读懂”而非“扫读” |
| 交互效率 | ★★★☆☆ 需要键盘/命令面板补足 |
| 视觉耐久 | ★★★★★ 低饱和、无阴影，长期看不腻 |
| 平板适配 | ★★★★☆ 单栏自然，表格需列降级 |
| 实现成本 | 中：主要是排版与图表；无新依赖 |
| 主要风险 | 留白多 → 单屏信息量低；宋体在 Windows 回退需确认；深色需求要另做一套 |

---

## 5. 方案 B ·「夜盘 Night Desk」

> 气质关键词：**暗色、密集、等宽数字、多窗格、专业终端**。像 Bloomberg/Linear Dark 的混合体，为夜间复盘与键盘操作而生。
> 预览：[PC 1600×1528](previews/b-night-desk-pc.png) · [iPad 1024×1600](previews/b-night-desk-pad.png) · [HTML 稿](mockups/b-night-desk.html)

### 5.1 设计理念

把复盘当成“值班”：一屏之内同时看到净值、持仓、风险、指标健康、待办队列；选中任一标的后右侧 Inspector 立即联动。信息密度优先，操作靠键盘与状态灯，不做多余装饰。

### 5.2 视觉语言

- **底色**：`#070a0e` / 面板 `#0c1118` / 顶栏 `#0a0e14`；1px 冷灰边框 `#1a2430`。
- **文字**：`#d7e0ea` / `#9fadbf` / `#6b7b8e`；全局等宽字体（SF Mono/JetBrains Mono）+ 中文 PingFang 回退。
- **强调色**：薄荷绿 `#3ce0a4`（正常/正向），琥珀 `#e3aa52`（警告），红 `#f06868`（亏损/阻断）；状态灯带发光。
- **图形**：净值线 1.6px + 18% 透明渐变面积；**水下回撤带**独立分区；十字光标 + 浮动数据框；密集表格 + 权重条 + 选中行 2px 左侧高亮。
- **圆角**：2–4px，几乎直角；面板用标题栏+脚注构成“仪器面板”。

### 5.3 布局结构（PC）

```
┌─64px 图标栏─┬──────────────────────────────────────────────────────────┐
│ TR DESK     │ 状态条：DATA CURRENT · BUILD OK · ⌘K · CST 时钟            │
│ 看板(激活)  ├───────────────────────────────┬──────────────────────────┤
│ 交易        │ 页头 + 时间分段 + 刷新/构建    │ INSPECTOR（选中标的）    │
│ 回合        │ 报价条：总资产/市值/现金/仓位  │ 指标健康 18 点阵          │
│ 净值        │ 净值图 + 水下回撤带            │ 复盘队列（HIGH/MED/LOW）  │
│ 资产        │ 持仓表（选中行联动）           │ SYSTEM 状态               │
│ 指标        │ 最赚/最亏 · RISK · BEHAVIOR    │                          │
└─────────────┴───────────────────────────────┴──────────────────────────┘
```

### 5.4 交互设计

- **选中联动**：点击持仓行 → 右侧 Inspector 切换；`↑/↓` 切换标的；`⌘K` 命令；`F` 专注；`?` 帮助。
- **图表十字光标**：拖动锁定区间、滚轮缩放（设计稿只表达 affordance，实装可先做 hover + 区间选择）。
- **队列即待办**：HIGH/MED/LOW 三档，关联指标 id，可勾选完成（本地存储，后续入后端）。
- **密度**：表格行高 31px，比 A 紧约 35%，适合 13 列宽表。

### 5.5 数据展示亮点

1. **一屏成立**：净值、持仓、风险、行为、指标健康、待办同时在场，不需要滚动即可判断“今天该看什么”。
2. **水下回撤带**：把 MDD 从一个百分比变成“何时水下、多深、多久”的形状。
3. **报价条**：总资产 + sparkline + 本金对比，一眼看到“赚在哪”。
4. **INSPECTOR**：单票权重 37.05% 超限 → 就地告警，不用跳页。
5. **选中行 + 右侧详情**，是高频分析动作最短路径。

### 5.6 响应式（iPad）

- 1024：Inspector 从右栏下沉为“Inspector + 指标健康”双列；报价条 2×3；图表 250；持仓表隐藏“数量/成本”；页头按钮压缩。
- 768：单列；Inspector 以底部抽屉呈现（设计稿未展示，需实装时补）；命令面板用全屏 sheet。

### 5.7 优点 / 风险 / 成本

| 维度 | 评价 |
|---|---|
| 数据展示 | ★★★★★ 密度与联动最强 |
| 交互效率 | ★★★★★ 键盘 + 选中联动 + 一屏决策 |
| 视觉耐久 | ★★★☆☆ 暗色长时间阅读疲劳；白天强光下对比度问题 |
| 平板适配 | ★★★☆☆ 多窗格在 1024 需重排，暗色在 iPad 上观感好 |
| 实现成本 | 中高：图表交互、Inspector 联动、键盘系统 |
| 主要风险 | 对“个人复盘”可能过重；深色需要完整设计 token 与对比度校验；首次使用者学习成本高 |

---

## 6. 方案 C ·「Bento 现代产品风」

> 气质关键词：**明亮、圆角、柔和层次、Bento 网格、现代 SaaS**。最容易“第一眼好看”，也最容易上手。
> 预览：[PC 1600×2000](previews/c-bento-pc.png) · [iPad 1024×1600](previews/c-bento-pad.png) · [HTML 稿](mockups/c-bento.html)

### 6.1 设计理念

用卡片把复杂信息切成可消化的块：总资产、收益、仓位、风险、洞察、待办各占一格；颜色克制但允许一条靛蓝→青的渐变作为“品牌记忆点”。适合“我希望它看起来像一个现代产品”，也方便未来扩展多用户/分享。

### 6.2 视觉语言

- **底色**：冷白 `#f6f7fb` / 卡片纯白；16px 圆角、1px 浅描边 `#e8eaf2` + 柔和阴影。
- **主色**：靛蓝 `#4f46e5` → 青 `#06b6d4` 渐变；绿 `#16a34a` / 红 `#dc2626` / 琥珀 `#d97706`。
- **字体**：系统无衬线，数字 tabular；字号 12/13/14/18/30。
- **图形**：面积渐变 + 峰值/谷底标记 + 回撤区间色带；KPI 卡内嵌 sparkline；进度环/条。
- **组件**：顶部栏 + 左侧分组侧栏 + 分段控件 + 胶囊 tab + 状态 chip + 卡片。

### 6.3 布局结构（PC）

```
┌────────────── 顶部栏：Logo · 搜索/命令 · 构建/页面状态 · 时间范围 · 头像 ──────────────┐
│ 侧栏(220px)  │ 页头（标题+说明+数据截至+刷新）                                       │
│ 概览         │ 4 张 KPI 卡（总资产/总收益/峰值成本收益率/仓位，带 sparkline）         │
│  看板(激活)  │ ┌ 净值曲线（大卡，含回撤带/峰值谷底标注）┬ 洞察与待办（3 条+输入）┐   │
│ 交易         │ └────────────────────────────────────────┴──────────────────────┘   │
│  流水/回合   │ ┌ 当前持仓（卡内表格+权重条）┬ 历史概览（胜负条/回撤/最赚最亏）┐      │
│ 分析         │ └─────────────────────────────┴──────────────────────────────┘      │
│  净值/指标   │ 最赚 3 笔 · 最亏 3 笔 · 指标健康度（18 指标摘要）                    │
│ 数据         │ 数据源与构建 · 本月复盘摘要 · 快捷下钻（占位项弱化）                  │
│  资产/导入   │ footer：口径说明                                                    │
└──────────────┴──────────────────────────────────────────────────────────────────────┘
```

### 6.4 交互设计

- **分段控件**切换时间范围；**胶囊 tab** 切换“净值/总资产/净值与收益”。
- **洞察与待办**卡：风险/阻断/提示三类 chip，点击“查看归因/去设置基准/查看指标详情”。
- 卡片 hover 轻微抬升（shadow 加深），行 hover 高亮，全部可点击区域有明确 affordance。
- 命令面板入口放在顶部搜索框；侧栏“月报/导出/归因”以弱化“占位”状态出现。

### 6.5 数据展示亮点

1. **KPI 卡带上下文**：总资产卡给“近 12 个月”sparkline + 数据截至；收益率卡给“相对峰值成本 0 / 峰值 1”刻度；仓位卡给“持仓/现金”构成。
2. **净值大卡**是视觉中心：回撤区间色带、峰值 1.143/谷底 1.040 标注、hover 数据框。
3. **历史概览**用胜负平堆叠条 + 回撤时间轴（39 天回撤/57 天修复）。
4. **指标健康度**用状态 chip 汇总（正常/样本不足/阻断/不可信/待窗口）+ 阻断指标 id 列表。
5. **洞察与待办**把指标异常翻译成行动，符合“深度思考 → 结论”。

### 6.6 响应式（iPad）

- 1024：侧栏折叠为图标栏（或顶部抽屉），Bento 从 4 列变 2 列；KPI 2×2；净值卡与洞察卡上下堆叠；表格隐藏次要列。
- 768：单列；KPI 横向滚动或 2 列；洞察卡置顶（先看结论）；导航抽屉。

### 6.7 优点 / 风险 / 成本

| 维度 | 评价 |
|---|---|
| 数据展示 | ★★★★☆ 分组清晰、上下文好，但密度低于 B |
| 交互效率 | ★★★★☆ 熟悉模式、几乎零学习成本 |
| 视觉耐久 | ★★★☆☆ 阴影/圆角/渐变多，长期可能显“模板感” |
| 平板适配 | ★★★★★ 卡片天然响应式，iPad 观感最好 |
| 实现成本 | 中：卡片/网格/图表都需要精致调校 |
| 主要风险 | 同质化；卡片留白牺牲密度；“现代感”容易过时；渐变需克制避免廉价 |

---

## 7. 横向对比

### 7.1 评分矩阵（1–5，★ 为推荐项）

| 维度 | A 静观 | B 夜盘 | C Bento |
|---|---|---|---|
| 数据密度 | 3 | **5** | 3.5 |
| 信息层级 | **5** | 4 | 4 |
| 扫读效率 | 4 | **5** | 4 |
| 深度思考氛围 | **5** | 4 | 3 |
| 现代感 | 4 | 4 | **5** |
| 视觉耐久 | **5** | 3 | 3 |
| 上手成本（越低越好） | 4 | 2 | **5** |
| iPad 适配 | 4 | 3.5 | **5** |
| 实现成本（越低越好） | **4** | 3 | 3.5 |
| 与现有代码兼容 | 4 | 4 | 4 |
| “个人专属感” | **5** | 5 | 2.5 |

### 7.2 场景选择题

| 你的真实场景 | 选 |
|---|---|
| 每天晚上 30–60 分钟逐项复盘，喜欢读文字/结论 | **A** |
| 交易时段频繁切页、看大量表格、习惯快捷键 | **B** |
| 想让系统“看起来像 2026 年的产品”，会拿 iPad 给朋友看 | **C** |
| 既想专注阅读，又要一屏看清持仓与风险 | **A + B 的 Inspector** |
| 想保留极简，又希望卡片层次更现代 | **A + C 的组件形态** |

### 7.3 成本估算（单人手改 React + CSS，不含后端）

| 方案 | 设计系统 | 页面迁移 | 图表/交互 | 响应式 | 合计（人天） |
|---|---|---|---|---|---|
| A | 2 | 5 | 3 | 2 | **≈12** |
| B | 2.5 | 5 | 5（Inspector/键盘/十字光标） | 2.5 | **≈15** |
| C | 2.5 | 5 | 4 | 2 | **≈13.5** |

> 估算是“从现有代码演进到预览稿完成度”的粗量级，含测试更新；不含后端图表字段（若走契约路线另加 1–2 天）。

---

## 8. 推荐方案：A 为骨架的混合体

### 8.1 组合规则

| 来源 | 采纳 | 不采纳 |
|---|---|---|
| A | 纸感浅色、编辑排版、大数字层级、细线分隔、复盘线索 | 纯图标导航（改为 76px 图标 + hover 展开标签） |
| B | 右侧 Inspector、选中联动、键盘/命令面板、密度切换、状态灯 | 全局暗色（改为可选主题）、十字光标复杂缩放 |
| C | 圆角与卡片层次（仅用于交互块与图表容器）、分段控件、状态 chip、KPI 卡的 sparkline/上下文 | Bento 满屏卡片、重阴影、大面积渐变 |

### 8.2 推荐方案的关键界面（文字示意）

```
左：76px 浅色图标栏（激活态竖线） + 可选展开为 220px 分组导航
顶：面包屑 · ⌘K 搜索 · 数据状态 · 专注/密度/主题
主：
  页头（数据截至/时间范围下划线 tab）
  英雄区（大数字 + 4 细分统计）
  净值曲线卡（细线 + 回撤带 + 关键标注）
  ┌ 持仓表（无边框、权重线） ┬ 右侧 Inspector（选中行联动）┐
  └ 历史概览 + 最赚最亏 ─────┴ 指标健康 + 复盘线索 ──────┘
底：口径说明 + 快捷键
```

### 8.3 为什么这样组合

- 保留 A 的“深度思考”气质（这是用户的原话目标），避免 C 的模板感与 B 的压迫感；
- 用 B 的 Inspector 解决 A “点击路径长”的问题，用键盘补足效率；
- 用 C 的圆角/分段控件提升“现代感”，但限制在组件层，不改变 A 的留白结构。

---

## 9. 目标设计系统（实现规范）

### 9.1 设计 Token（CSS 变量，建议直接落地）

```css
:root {
  /* 色板（浅色主题；深色主题覆盖同名变量） */
  --bg: #f6f5f0;          --surface: #fbfaf6;      --surface-2: #f1efe8;
  --text: #1b1d19;        --text-2: #3c4038;       --muted: #7d8278;
  --line: #e3e1d6;        --line-strong: #d3d1c4;
  --accent: #3b5f4b;      --accent-soft: #ecefe8;
  --up: #a8483a;          /* A 股红涨（可切换） */
  --down: #2f6b4f;        /* A 股绿跌（可切换） */
  --warn: #94703a;        --danger: #a8483a;
  /* 字体 */
  --font-ui: -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  --font-display: "Songti SC", "Noto Serif CJK SC", serif;
  --font-mono: "SF Mono", "JetBrains Mono", Menlo, monospace;
  /* 字号（PC） */
  --fs-display: 58px; --fs-h1: 42px; --fs-h2: 21px; --fs-h3: 15px;
  --fs-body: 14px;    --fs-sm: 12.5px; --fs-xs: 11px; --fs-micro: 10px;
  /* 间距 4px 网格 */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px; --s6: 32px; --s7: 48px;
  /* 布局 */
  --rail: 76px; --content-max: 1440px; --side-col: 400px; --gutter: 48px;
  /* 圆角/阴影 */
  --r-sm: 6px; --r-md: 10px; --r-lg: 14px;
  --shadow-1: 0 1px 2px rgba(27,29,25,.04);
  --shadow-2: 0 8px 24px rgba(27,29,25,.06);
  /* 动效 */
  --ease: cubic-bezier(.2,.6,.2,1); --dur: 160ms;
}
[data-density="compact"] { --row-h: 32px; }
[data-density="cozy"]   { --row-h: 42px; }
@media (prefers-reduced-motion: reduce) { :root { --dur: 0ms; } }
```

### 9.2 数字排版规范（最高优先级）

| 规则 | 做法 |
|---|---|
| 对齐 | 所有金额/数量/比率右对齐；表头右对齐；单位与数值同一基线 |
| 等宽数字 | 全局 `font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1;` |
| 固定小数位 | 由后端返回；前端不得 `toFixed`（零口径） |
| 千分位/货币 | 后端已格式化则直接展示；若后端未给货币符，前端仅做 `¥` 前缀（展示层，非计算） |
| 层级 | 主数字 58px → 次级 25px → 行内 13.5px → 脚注 10.5px |
| 正负 | 同时给颜色 + `+/-` 符号 + `▲/▼`；颜色可配置 |
| 空值 | 统一 `—`（非 ok 不显示数字，沿用现有约定） |
| 单位 | 不在数字中间换行；用 `<span class="unit">` 弱化 |

### 9.3 状态语义（两轴，统一一个模块）

```ts
type BuildState = "missing" | "current" | "stale" | "blocked";
type DataStatus = "ok" | "degraded" | "blocked" | "missing" | "insufficient" | "unreliable" | "pending_window";
// 页面状态优先级：blocked > missing > insufficient > stale > degraded > ok
```

- 统一 `status.ts`：中文标签、颜色 token、图标、是否可显示数值、`what_to_do` 兜底。
- 徽标必须“颜色 + 文案 + 形状”三重编码，满足色盲可用性。
- 现有 7 套词表（`Dashboard/Metrics/Assets/Nav/Rounds/...`）合并为一份，保留旧导出以兼容测试。

### 9.4 组件清单（第一阶段）

`AppShell` `SideRail` `TopBar` `PageHeader` `TimeRangeTabs` `Stat` `StatGrid` `TrendChart` `Sparkline` `DrawdownBand` `DataTable` `WeightBar` `StatusBadge` `StatusTwoAxis` `Inspector` `Drawer` `NoteCard` `CommandPalette` `EmptyState` `Skeleton` `ProblemView`（复用）`JobProgress`（复用）`Toast` `Kbd`。

### 9.5 页面模板

1. **概览页模板**（Dashboard）：页头 + 英雄 KPI + 主图 + 2 栏（主数据 + 侧栏洞察）。
2. **列表页模板**（Trades/Rounds/Imports/Assets）：页头 + 筛选条（可折叠）+ DataTable + 分页/游标 + 详情抽屉。
3. **详情页模板**（RoundDetail/MetricDetail）：面包屑 + 标题/状态 + 三段式 + 下钻表 + 相关链接。
4. **表单页模板**（Import/Settings/Login）：单列 ≤560px，步骤条、错误内联、提交态。

---

## 10. 信息架构与导航

### 10.1 导航分组（解决 9 项平铺）

| 分组 | 入口 | 说明 |
|---|---|---|
| 概览 | 看板 | 默认首页 |
| 交易 | 交易流水、回合 | 流水 → 回合下钻 |
| 分析 | 净值与收益、指标、归因（占位） | 核心复盘 |
| 数据 | 资产、导入、导入记录、月报（占位）、导出（占位） | 数据与产出 |
| 设置 | PAT / 基准（未实现）/ 主题 / 密度 | 底部 |

- 桌面：76px 图标栏 + hover 展开 220px 标签；或固定 220px 可折叠侧栏（推荐后者用于 A+）。
- iPad 横：图标栏 64px；竖：顶部汉堡 + 全屏抽屉。
- 占位入口可见但弱化（`待开发` chip），不隐藏（满足 D-07）。
- 当前页用 `aria-current="page"` + 竖线/底色，**不再有无激活态的导航**。

### 10.2 全局时间范围

- 顶部统一 `全历史 / 本月 / 近一年 / 自定义`；各页保留页内局部筛选，但默认继承全局范围。
- URL 同步：`?from=&to=&period=`，刷新/回退不丢（解决审计 §2.5）。
- 日期输入统一组件：两个 select（年/月）+ 可选 date range；**避开 iPadOS 不支持 `input[type=month]` 的坑**。

### 10.3 下钻链

`看板 → 指标详情 → 回合详情 → 交易流水` 全链路可回退（带来源 URL）；行点击打开右侧 Inspector（桌面）/底部抽屉（平板），保留“在新页面打开”。

---

## 11. 数据可视化与零口径

### 11.1 图表清单（按优先级）

| # | 图表 | 页面 | 数据来源 | 实现 |
|---|---|---|---|---|
| 1 | 净值曲线 + 回撤区间 | 看板/净值 | `/nav` points（date、unit_nav） | 自研 SVG 折线/面积 + 区间标注 |
| 2 | 水下回撤带 | 看板 | 由净值序列派生（几何，非业务口径） | 自研 SVG 面积 |
| 3 | KPI sparkline | 看板 | 需后端补 `sparkline[]` 或趋势 API（FR-D5） | 自研 SVG |
| 4 | 持仓权重条 | 看板/资产 | `weight_pct` | CSS 宽度（几何）/ 后端 `weight_bp` |
| 5 | 胜负平堆叠条 | 看板/回合 | win/loss/flat 计数 | CSS flex |
| 6 | 指标健康点阵 | 看板/指标 | 指标状态 | CSS grid |
| 7 | 回撤时间轴 | 看板/指标 | 起止/持续/恢复 | CSS/SVG |
| 8 | 分布/直方图（后续） | 指标详情 | drilldown 数值 | 自研 SVG 或 ECharts 子模块 |

### 11.2 零口径下的图表实现（必须拍板）

**冲突**：零口径禁止在业务页面 `parseFloat/Number()`；但画 SVG 必须把 `"1.28400000"` 变成像素坐标。调研的结论是：**口径运算不在前端发生，但“domain → pixel”的几何投影是渲染行为，可以用受控方式落地**（D3 scale 是标准分层，[d3-scale](https://d3js.org/d3-scale)；BFF/视图模型同样主张服务端产出面向视图的数据，[Sam Newman](https://samnewman.io/patterns/architectural/bff/)）。

**推荐方案（双轨，见 [02 §7](02-design-research.md)）**：

1. **后端 chart-ready 视图（主路径）**：新增只读视图端点（如 `GET /views/nav-chart?range=all`），返回已归一化的无量纲序列 `v ∈ [0,1]`、轴刻度、回撤区间、标注与**已格式化的展示标签**；前端只做 `y = (1 - v) * H` 投影与命中测试。契约先行（API-Catalog → snapshot → sdk）。

   ```jsonc
   {
     "as_of": "2026-10-27",
     "series": [{ "date": "2025-10", "v": 0.000, "label": "1.000", "amount_label": "¥1,000,000.00" }],
     "y_ticks": [{ "v": 0.0, "label": "1.000" }, { "v": 0.5, "label": "1.150" }],
     "bands": [{ "start_index": 5, "end_index": 7, "label": "回撤 -12.80%" }]
   }
   ```

2. **受限几何白名单（过渡/交互）**：`src/charts/geometry.ts` 是全仓唯一允许 `Number()` 的模块，只接受已知格式的展示字符串或归一化 number，输出像素坐标/path；ESLint 目录级 override + golden 测试（13 点固定序列断言 `path d`）+ property test（`v∈[0,1] → y∈[0,H]`）。
3. **门禁扩展**：零口径规则全局启用；把现有 `?raw` 零口径断言从 2 个单文件扩到 `src/**/*.{ts,tsx}`（排除 `charts/geometry.ts` 及其测试），拆文件后不再静默失效；几何模块禁止 import 业务 api 类型。

> 无论选哪条，**求和/平均/收益率/回撤/权重/排序/比较/四舍五入仍必须由后端完成**；前端只做几何映射与展示。前端 decimal 库做业务算术的方案已否决（口径仍在前端，见 [02 §7.2](02-design-research.md)）。

### 11.3 图表技术选型

| 选项 | 体积 | 适用 | 结论 |
|---|---|---|---|
| 自研 SVG + scale | ≈0（约 300 行） | 折线/面积/条形/sparkline/回撤带 | **首选**，5 类图够用，jsdom 可测 |
| ECharts（按需） | ~150–300KB | K 线、热力、复杂交互 | 二期按需引入，`echarts/core` 子模块 |
| Recharts | ~90KB | React 声明式 | 不如自研可控，不建议 |
| uPlot | ~45KB | 超大数据量 | 个人数据量不需要 |

---

## 12. 交互增强清单

| # | 能力 | 价值 | 成本 |
|---|---|---|---|
| 1 | 命令面板 `⌘K`（搜标的/页/指标/命令） | 任意页面 3 键到达 | 小（自研 150 行） |
| 2 | 右侧 Inspector（选中行联动） | 高频分析最短路径 | 中 |
| 3 | 键盘：`g d`/`g r`/`/`/`f`/`?` | 效率与专业感 | 小 |
| 4 | 密度切换（舒适/紧凑） | 适配 13 列表格 | 小 |
| 5 | 专注模式（隐藏导航/侧栏） | 深度阅读 | 小 |
| 6 | 复盘笔记/待办（本地 → 后端） | 思考沉淀 | 中 |
| 7 | URL 状态（筛选/范围/选中） | 可分享/可回退 | 小 |
| 8 | 统一 Toast + 可关闭 notice | 反馈一致性 | 小 |
| 9 | 骨架屏 + 空态 + 错误态 | 感知性能 | 小 |
| 10 | 图表区间选择/联动高亮 | 深度分析 | 中（二期） |

---

## 13. 实施路线图（建议 5 个阶段）

> 原则：**先地基后页面、先 Dashboard 后其他页、每阶段可回滚、测试随页面迁移**。每阶段结束跑 `pnpm -r typecheck && pnpm -r test`。

### Phase 0 · 决策与基线（1–2 天）

- 拍板：方案选择、涨跌色语义、图表零口径路线、a11y 级别（建议 WCAG 2.2 AA）、是否引入 TanStack Query。
- 修正文档漂移（[01 §8.8](01-current-state-audit.md)）：AGENTS 的 TanStack Query/ESLint/页面清单；PRD 的 ECharts/Playwright 标记为 SKIP。
- 建立 `tokens.css` + 主题切换骨架（`data-theme`/`data-density`），保留旧 CSS 类名兼容。

### Phase 1 · AppShell + 导航 + Dashboard（3–4 天）

- `AppShell`（侧栏/顶栏/面包屑/命令入口）、路由懒加载、`NavLink` 激活态、`document.title`。
- 引入 TanStack Query：`QueryClientProvider` + `useQuery` 封装；先只接 Dashboard，旧页保持 `useState/useEffect`（共存迁移）。
- Dashboard 重做：KPI 英雄区、净值图（图表适配层）、持仓表、历史概览、指标健康、复盘线索。
- 复用：`ProblemView`、`JobProgress`、状态两轴模块；补 Dashboard 降级/空态。
- 验收：现有 `Dashboard.test.tsx` 全绿（允许补 testid），新增图表/交互快照。

### Phase 2 · 通用组件 + 列表页迁移（3–4 天）

- `DataTable`（粘性表头、列优先级、密度、键盘、空/加载）、`FilterBar`、`Drawer`、`StatusBadge`。
- 迁移：Rounds → Trades → Assets → Imports → Nav。保留 testid/文案，逐页更新测试。
- 平板降级：<1280 隐藏次级列；<768 卡片行；表格横向滚动仅作兜底。

### Phase 3 · 详情/表单/设置（2–3 天）

- MetricDetail（三段式 + 口径抽屉/详情页）、RoundDetail、Import 向导、Settings（PAT + 主题/密度/涨跌色）。
- 统一表单错误、loading、disabled；修 Register 错误对象与 UTC 月份 bug。

### Phase 4 · 增强与门禁（2–3 天）

- 命令面板、键盘地图、专注模式、笔记持久化。
- 响应对照（1440/1280/1024/768/390）、iPad Safari 实测、对比度与键盘走查。
- 视觉回归（Playwright 可选）、前端 CI job、零口径 ESLint（含 chart 白名单）、性能预算（首屏 JS ≤220KB gzip、CSS ≤30KB gzip）。

### Phase 5 · 可选主题（1–2 天）

- 深色主题（若选 A/C），用同名 token 覆盖；尊重 `prefers-color-scheme` + 手动切换。

---

## 14. 测试与门禁改造

| 风险 | 对策 |
|---|---|
| 61 用例/287 断言锁定 DOM | 逐页迁移：先加新结构，再改断言；保留 37 个 `data-testid` 与 `.money/.badge-*` 类名 |
| className 精确断言（`Assets.test.tsx:373-392`） | 兼容层保留旧类名，或同 PR 更新断言（需评审） |
| 零口径源码断言只扫 2 文件 | 扩到全 `src/**`，`ui/charts` 白名单单独断言 |
| 无 ESLint | 引入 ESLint + 自定义 `no-float-business-calc` 规则（或先脚本 grep 门禁） |
| CI 不跑前端 | 新增 GitHub Actions job：`pnpm -r typecheck && pnpm -r test && pnpm -r build` |
| 布局/响应式无测试 | 可选 Playwright 截图对比（1600/1024/768/390 四档） |
| 无前端性能基线 | 首屏 JS ≤220KB gzip、CSS ≤30KB gzip、图表路由级 lazy（单块 ≤40KB）、无外部字体/CDN 请求；Lighthouse LCP <1.5s（自定预算，见 [02 §8.4](02-design-research.md)） |
| 无 a11y 验证 | 目标 WCAG 2.2 AA：对比度脚本进 CI、`focus-visible` 统一、图表 `role="img"` + 数据表替代、44px 触控、`prefers-reduced-motion` 关闭动画 |

**迁移策略**：用“新壳 + 旧页”共存——先换 Layout 与 Dashboard，其他页暂用旧样式（命名空间隔离），确认不冲突后逐页迁移；避免一次性重写 14 页导致测试雪崩。

---

## 15. 已拍板决策（2026-10-28，用户确认按推荐项执行）

> 用户原话：「需要拍板的事项我已确定使用推荐，请按照实施路线图进行按阶段开发，每个阶段进行整理代码、验收和提交 commit，如果实施阶段遇到需要排版的请停下工作请求人工介入。」

| # | 决策 | 最终结论 | 落地阶段 |
|---|---|---|---|
| D1 | 主线方案 | **A+ 混合**：A 骨架 + B Inspector/键盘/密度 + C 组件形态；B 暗色作为可选 `data-theme` | Phase 1 |
| D2 | 涨跌色语义 | **默认红涨绿跌（A 股）**，设置可切“绿涨红跌”；正负色只作辅助，必须带 `+/-` 与 `▲/▼`（WCAG 2.2 1.4.1）。三方案预览稿按绿涨红跌绘制，**仅视觉示意** | Phase 1（token）/ Phase 3（设置） |
| D3 | 图表零口径路线 | **后端 chart-ready 视图为主路径**（`GET /views/*`，归一化 `v∈[0,1]` + 轴 + 区间 + 已格式化 label）；过渡期用 `src/charts/geometry.ts` 白名单做像素投影，配 ESLint override + golden `path d` 测试；否决前端 decimal 业务算术 | Phase 1（geometry）/ Phase 2（后端视图） |
| D4 | TanStack Query | **引入**（+12.9KB gzip），Phase 1 先接 Dashboard，旧页共存迁移 | Phase 1 |
| D5 | 深色主题 | **先浅色**，Phase 5 用同名 token 覆盖实现深色 | Phase 5 |
| D6 | ESLint + 前端 CI | **都引入**：ESLint + 零口径规则、GitHub Actions 前端 job | Phase 4 |
| D7 | iPad 最低版本 | **iPadOS 16+**；`@container` 仅用于卡片渐进增强，全局布局仍用媒体查询 | Phase 1–2 |
| D8 | 导航结构 | **合并为 5 组**（概览/交易/分析/数据/设置）+ 3 个占位入口弱化显示，不隐藏 | Phase 1 |
| D9 | 无障碍目标 | **WCAG 2.2 AA**（含 1.4.1 / 1.4.10 / 1.4.11 / 2.4.11 / 2.5.8） | 全阶段 |
| D10 | 迁移策略 | 新壳 + 旧页共存；每阶段独立 commit；不一次性重写 14 页 | 全阶段 |

---

## 16. 实施与提交约定（已确认）

### 17.1 阶段门禁

每个阶段必须依次完成，才允许提交该阶段 commit：

1. **整理代码**：删除本阶段临时文件/兼容代码；`git diff` 自查无无关改动。
2. **静态检查**：`cd frontend && pnpm -r typecheck`（Phase 4 起加 `pnpm -r lint`）。
3. **测试**：`cd frontend && pnpm -r test`；涉及后端契约时另跑对应 pytest 与 `make api-check`。
4. **构建**：`cd frontend && pnpm -r build`。
5. **人工验收点**：响应式/视觉走查（1600 / 1024 / 768）——若本阶段产生**新的排版/视觉歧义**（例如间距、字号、栅格取舍没有既定 token 可依），**暂停并请求人工介入**，不得自行决定。
6. **提交**：一个阶段一个 commit，消息格式 `feat(frontend): <phase 名称>` 或 `refactor(frontend): ...`；footer 记录验收命令与结果。

### 17.2 阶段顺序

| 阶段 | 交付 | Commit 前缀 |
|---|---|---|
| Phase 0 | 决策文档、文档漂移修正、tokens/base/主题骨架 | `docs(frontend):` / `chore(frontend):` |
| Phase 1 | AppShell + Dashboard + TanStack Query + geometry | `feat(frontend):` |
| Phase 2 | DataTable + 列表页迁移 + 后端 views 视图 | `refactor(frontend):` |
| Phase 3 | 详情/表单/设置 + bug 修复 | `feat(frontend):` |
| Phase 4 | 命令面板/键盘/CI/ESLint/视觉回归 | `chore(frontend):` |
| Phase 5 | 深色主题（可选） | `feat(frontend):` |

### 17.3 人工介入触发条件

- 预览稿与设计 token 无法覆盖的新版式（如“主图放左还是放上”“KPI 卡放几列”）。
- 字号/间距/圆角/阴影的新取值（无既定 token）。
- 需要改变既有信息架构或交互路径（非 bug 修复）。
- 视觉验收发现两种合理解释且会影响用户习惯。

> 触发时：暂停当前阶段，列出「已掌握信息 / 需要决策什么 / 推荐选项及理由」，等人工确认后继续。

---

## 17. 文件清单与复现方式

```
docs/reviews/2026-10-28-frontend-refactor/
├── README.md                     ← 本文（入口）
├── 00-brief-demo-data.md         ← 三方案共用演示数据/信息架构/截图规范
├── 01-current-state-audit.md     ← 现状审计（512 行，file:line 证据）
├── 02-design-research.md         ← 外部调研（趋势/案例/选型/来源）
├── mockups/
│   ├── a-quiet-analyst.html      ← 方案 A 自包含稿
│   ├── b-night-desk.html         ← 方案 B 自包含稿
│   └── c-bento.html              ← 方案 C 自包含稿
└── previews/
    ├── a-quiet-analyst-pc.png    ← 1600×2351
    ├── a-quiet-analyst-pad.png   ← 1024×1600
    ├── b-night-desk-pc.png       ← 1600×1528
    ├── b-night-desk-pad.png      ← 1024×1600
    ├── c-bento-pc.png            ← 1600×2000
    ├── c-bento-pad.png           ← 1024×1600
    ├── comparison-hero.png       ← 三方案首屏横向对比（2288×680）
    └── comparison-full.png       ← 三方案整页横向对比（1972×1063）
```

**复现截图**（本机 Chrome，必须带 `--user-data-dir`）：

```bash
cd docs/reviews/2026-10-28-frontend-refactor
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-sandbox --disable-dev-shm-usage \
  --user-data-dir=/tmp/shot-ud --hide-scrollbars --force-device-scale-factor=1 \
  --window-size=1600,2000 \
  --screenshot="$PWD/previews/c-bento-pc.png" \
  "file://$PWD/mockups/c-bento.html"
```

> A/B 使用 CDP 全页截图（`Page.getLayoutMetrics` + `captureBeyondViewport`）以获得自然高度；C 使用固定 1600×2000 视口。对比图由 Pillow 按统一宽度缩放后拼接（标签用 Hiragino Sans GB）。

**实施状态**：D1–D10 已确认（见 §15）。Phase 0 已完成决策文档并提交；后续按 §13 路线图逐阶段开发，每阶段独立 commit。

| 阶段 | 状态 | Commit | 备注 |
|---|---|---|---|
| 选型文档 | ✅ 已完成 | `5e5a8c1` | 三方案 + 预览 + 审计 + 调研 + D1–D10 |
| Phase 0 | ✅ 已完成 | `d1108b0` | tokens/主题骨架 + 文档漂移修正 + 基线红灯修复 |
| Phase 1a | ✅ 已完成 | `80608b6` | TanStack Query Provider、status 两轴、geometry 白名单、零口径全量门禁 |
| Phase 1b | ✅ 已完成 | 本提交 | AppShell + 导航 5 组 + 路由懒加载 + 页面标题 |
| Phase 1c | ⏳ 待开始 | — | Dashboard 重做 + 图表 + 响应式 |
