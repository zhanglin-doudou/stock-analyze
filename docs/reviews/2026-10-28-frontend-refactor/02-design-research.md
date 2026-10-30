# 前端重构 · 外部设计调研：深度分析型数据产品与响应式范式

> 作者：design-researcher（Team Lead 委托） · 日期：2026-10-28
> 范围：PC 优先后台级数据产品（个人股票复盘），兼容 iPad；现代化/极简；适合个人深度思考分析。
> 用途：为「A 静观 Quiet Analyst / B 夜盘 Night Desk / C Bento 现代产品风」三套方向与本仓技术选型提供**有出处**的行业依据。

## TL;DR（5 条结论，详见正文）

1. **方向**：A（降噪+强层级+可调密度）最贴合"个人深度复盘"；B 应做成可选暗色主题而非唯一默认；C 只借 KPI/洞察卡语法，不承载长表与主图。
2. **图表**：本阶段**手写 SVG**（净值折线 + 回撤水下面积），不引入图表库；未来大数据量再评估 uPlot（21.3KB gzip、0 依赖）。
3. **表格**：原生 `<table>` + 自研冻结/列优先级；TanStack Table（30.9KB gzip）仅在列配置复杂化后引入；AG Grid（368.1KB gzip）不推荐。
4. **样式**：CSS Modules + CSS 自定义属性 tokens（亮/暗/高对比同源生成，OKLCH），不引入 Tailwind / 全量 UI 库；cmdk（16.7KB gzip）+ Radix Dialog 作为可选交互件。
5. **零口径**：业务口径全部后端；新增 `views/*` 返回**归一化（v∈[0,1]）+ 轴 + 区间 + 格式化标签**；前端仅 `src/charts/geometry.ts` 一个文件做像素投影，配 ESLint override + golden 测试 + CI 审计。

## 0. 调研方法与可信度分级

- **检索方式**：`web_search` + `web_fetch` 真实联网检索；包体数据来自 [Bundlephobia](https://bundlephobia.com/) 的 `/api/size` 接口实测（2026-10-28 抓取，版本随 npm latest 浮动）。
- **证据分级**（正文用标记区分）：
  - `[官方]` 官方规范 / 设计系统 / 库文档，可在浏览器直连；
  - `[产品]` 产品官网、changelog、公开博客中可验证的设计动作与声明；
  - `[行业]` 行业文章 / 二手趋势解读，只作趋势信号，**不单独支撑关键决策**；
  - `[本项目]` 本仓文档或代码现状（相对路径引用）。
- **明确未核实项（避免编造）**：Bloomberg Terminal、TradingView、Koyfin、Observable 首页在本机网络环境无法直连（DNS/反爬），Wikipedia / Investopedia 返回 403，Bento 趋势文章抓取失败。因此正文**不引用这些产品的具体 UI 细节**，只保留可验证的行业共性判断，并逐条标注。
- **本仓现状基线**（用于判断可迁移性）`[本项目]`：`frontend/apps/web/src/styles.css` 共 404 行、`max-width: 1080px`、基准字号 14px、仅 4 列固定 grid、无 `@media` 断点；顶部导航 9 项目前写死在 `router.tsx:34-42`；无 UI / 图表 / CSS 框架（`frontend/apps/web/package.json` 仅 React 18 + react-router-dom）。
- **硬约束**：前端零口径（金额/数量/比率只展示后端字符串，禁 `parseFloat/Number/toFixed`）；后端契约先行（OpenAPI → openapi-typescript）；严格门禁（tsc / vitest / 即将启用的零口径 ESLint）。见 `frontend/AGENTS.md` §1、根 `AGENTS.md` 坑位 #1。

---

## 1. 2025–2026 趋势与真实案例

### 1.1 Editorial minimal / Quiet UI：降噪，而不是减信息

- **Linear 2024 全量重设计** `[产品]`：官方 changelog 明确把目标写成「**reduce visual noise, maintain visual alignment, increase the hierarchy and density**」——即"安静"的落点是**降噪 + 更强层级 + 可调密度**，不是把内容删掉。见 [Welcome to the new Linear](https://linear.app/changelog/2024-03-20-new-linear-ui) 与 [How we redesigned the Linear UI (part Ⅱ)](https://linear.app/now/how-we-redesigned-the-linear-ui)。
- **Linear 的配色工程化** `[产品]`：主题由 `base color / accent color / contrast` 三个变量生成，使用 **LCH 色彩空间**并暴露 contrast 变量以支持高对比无障碍主题；亮/暗两套主题同时重算。对"极简"的直接含义是：**用 token 化色阶 + 单一强调色**，而不是到处手写 hex。见 [part Ⅱ](https://linear.app/now/how-we-redesigned-the-linear-ui)。
- **Stripe Press** `[产品]`：支付公司做书籍出版线，说明"编辑式排版（大字号标题、强留白、克制的色）"已成为金融科技品牌的通用视觉资产，可作为 A 方向的排版参照：[press.stripe.com](https://press.stripe.com/)。
- **Notion** `[产品]`：内容（文档/数据库视图）优先、chrome 极弱的组织方式，说明"少界面、多内容"在知识型工具中的可行性：[notion.com](https://www.notion.com/)。
- **代价**：quiet 风格对排版/间距/字体要求高，密度过低会牺牲扫读效率；没有严格的 type scale 与 spacing scale 就会"空而乱"。本项目 404 行单文件 CSS、14px 基准，一旦走 A 方向必须同时建立 **design tokens（字号/间距/色阶）**，否则不可维护。

### 1.2 Dark trading terminal：暗色不是简单反色

- **Linear 暗色主题** `[产品]`：重设计时"default dark and light themes have increased contrast"，并引入 contrast 变量；说明暗色终端的关键不是 `#000 + 荧光色`，而是**重新计算 elevation 与对比度**。
- **OpenBB** `[产品]`：开源投研平台/终端形态（[openbb.co](https://openbb.co/)，源码 [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB)），是"终端式高密度 + 暗色 + 键盘/命令驱动"的公开可验证案例；本项目 B 方向可参照其"面板化 + 指标密集"的组织方式，但**具体配色/交互细节未逐项核实**。
- **uPlot 的"终端美学"** `[官方]`：官方 README 明确「**No transitions or animations - they're always pure distractions**」，并把高/低 band、cursor sync 作为核心能力（[uPlot README](https://github.com/leeoniya/uPlot)）。这与 B 方向"数据优先、动效克制"一致。
- **代价**：长时间阅读暗色背景的对比度管理更难；白天/打印/截图分享体验差；对色弱用户，暗底上的红绿更易混淆（见 §2.2）。**因此不建议把暗色作为唯一默认主题**，而应做成可选主题或"盯盘模式"。
- 说明：Bloomberg Terminal / TradingView 站点在本环境无法直连校验，故不引用其具体界面结论（仅承认它们是该品类的行业参照）。

### 1.3 Bento grid：一种"分组修辞"，不是页面骨架

- `[行业]` 行业文章把 Bento UI 归纳为"模块化卡片 + 大小不一的网格"趋势（[Bento UI 综述](https://blog.depositphotos.com/bento-ui.html)，本环境抓取失败，仅作趋势信号，**不引用其中具体数据**）。
- `[产品]` 可验证的邻近证据是 SaaS 产品普遍采用"卡片化信息分组 + 顶部全局控件"的结构（Notion、Linear 的视图/面板组织），但**不建议据此断言"所有产品都用 bento"**。
- **判断**：Bento 适合 **KPI / 洞察 / 状态** 这类可被"一眼扫到"的模块；**不适合承载长表格与连续时间序列**（卡片内边距会吞噬密度，切割比较关系）。本项目可借用其"卡片语法"，但主图与持仓表必须落在整块内容区。

### 1.4 Calm technology：把"状态"放进 periphery

- `[官方]` Amber Case 的 Calm Technology 八条原则中与本项目最相关的三条：**"smallest possible amount of attention"**、**"make use of the periphery"**、**"the right amount of technology is the minimum needed"**；官方还给出 trend graph、status light、delay/popup 的使用边界（[calmtech.com](https://calmtech.com/)）。
- **落地含义**：后台构建/导入任务用**状态灯 + JobProgress**（本仓已有 `ui/JobProgress.tsx`）表达，而不是弹窗打断；「数据截至 / 构建状态 / 指标阻断」这类信息应常驻在页面 periphery（顶栏右区），与主任务分离。
- **代价**：calm 化要求状态模型完整（加载/空/错误/降级/陈旧），否则"安静"会变成"看不见问题"。本仓 Dashboard 有 15 字段与状态分类文档（`docs/architecture/2026-10-28-dashboard.md`），可直接复用其状态语义。

---

## 2. 高密度金融数据表与图的可用性证据

### 2.1 数字对齐：tabular-nums 是硬要求

- `[官方]` `font-variant-numeric: tabular-nums` 激活等宽数字（OpenType `tnum`），"allowing them to be easily aligned like in tables"，且自 2020-01 起跨浏览器可用（[MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/font-variant-numeric)）。
- **规则**：金额/数量/比率列右对齐 + 等宽数字 + 小数位与后端字符串一致；`+/-` 符号占位（正数前留空格）避免跳动；单位（¥ / %）用单独列或后缀弱化。
- **与零口径的关系**：前端只负责**对齐与着色**，不做四舍五入/补零（`toFixed` 属禁用 API）。

### 2.2 涨跌色：中国市场默认红涨绿跌，但颜色不能单独承载语义

- `[行业/史料]` 中国内地股市"红涨绿跌"源自早期券商引入的台湾"乾隆"股票软件，此后成为本地惯例；国际惯例为"绿涨红跌"，中国外汇牌价则采用国际惯例（[光明网《中国股市为何"红涨绿跌"》](https://epaper.gmw.cn/wzb/html/2015-08/01/nw.D110000wzb_20150801_1-05.htm)）。→ **A 股复盘系统默认红涨绿跌**；如做国际化/外汇再切换。
- `[官方]` WCAG 2.2 **1.4.1 Use of Color**：颜色不能是传达信息的唯一视觉手段（[W3C](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)）。
- `[官方/行业]` NN/g 指出约 **8% 的男性**存在某种色盲（数据来自其图表文章），并强调颜色不适合表达量级（[Dashboards: Making Charts and Graphs Easier to Understand](https://www.nngroup.com/articles/dashboards-preattentive/)）。
- **规则**：涨跌同时用 `符号(+/-) + 箭头或色块 + 数值`；权重/热力用**长度或位置**编码，颜色只做辅助；ECharts 等库的 decal 图案可作为第二编码（[ECharts Aria 文档](https://echarts.apache.org/handbook/en/best-practices/aria/)）。

### 2.3 线 vs 面积：位置最精确，面积用于"从 0 起的总量"

- `[行业]` NN/g：人对 **2D position / length** 的前注意加工最精确，面积与角度更难比较；因此柱/线优于饼/雷达（[Dashboards](https://www.nngroup.com/articles/dashboards-preattentive/)）。
- **对本项目**：净值曲线用**折线**（可加极淡面积填充只作装饰）；**回撤**用独立"水下"面积图（以 0 为基线、向下延伸），因为回撤本身是"从峰值起算的连续量"，面积语义正确。回撤区间/持续天数/恢复用时已由后端给出（`00-brief-demo-data.md` §2.3、§2.5），前端只画阴影区间。
- `[行业]` 图表三原则 **Context / Clutter-free / Contrast**：每个数字都需要对照物（上月、全历史、基准），否则无意义（[Choosing Chart Types: Consider Context](https://www.nngroup.com/articles/choosing-chart-types/)）。→ KPI 卡必须带**对照/上下文**（同比、区间、中位数），不能只有一个绝对值。

### 2.4 稀疏坐标轴与直接标注

- `[行业]` "clutter-free" 要求去掉非数据墨水：只保留必要刻度、去掉网格底纹或降到极低对比，关键点用**直接标注**（峰值、谷底、最新值）。
- **注意**：uPlot README 明确"**No collision avoidance for axis tick labels**，可能需手工调间距"；若走轻量库路线，13 个月度点的轴标签要在 1024 宽度下单独回归测试（见 §5）。

### 2.5 sparkline

- sparkline 本质是"无轴、无标签、只表达趋势"的微型图。本项目 KPI 卡的趋势线应**限制为装饰性图形**：`aria-hidden="true"` + 旁边始终有文本数字与趋势描述（"较上月 +3.2%"由后端给），不承担精确读数职责。依据仍是 NN/g 的"位置/长度前注意"原则；具体 sparkline 术语的二手解读未引用，避免过度引证。

### 2.6 表格可读性：冻结、斑马纹、hover 都有实证

- `[行业]` NN/g 数据表研究给出可直接执行的规则：**冻结表头与首列**；用**边框、斑马纹、hover 高亮**帮助用户"保持位置感"；列顺序按任务重要性排列；隐藏/重排列要低交互成本且有状态提示；单条记录的查看/编辑应避免在宽表内横向滚动（[Data Tables: Four Major User Tasks](https://www.nngroup.com/articles/data-tables/)）。
- `[行业]` 移动/窄屏表格的降级顺序：**先做桌面可用表 → 再适配**；冻结表头；明确提示可横向滚动（右缘截断/箭头优于圆点）；冻结首列；按需选择列；分组用折叠（[Mobile Tables](https://www.nngroup.com/articles/mobile-tables/)）。
- **行高建议**：鼠标为主桌面 36–40px、iPad 触控 44px；斑马纹在 7 行持仓表上收益低、在 50+ 行流水表上收益高——**按表规模**启用，不要全局斑马纹。

---

## 3. "深度思考"交互模式的行业依据

| 模式 | 可验证依据 | 本项目落地建议 | 代价/风险 |
|---|---|---|---|
| Focus mode | iA Writer 的 focus mode 是"单任务、去 chrome"的公开范式（[iA Writer 文档](https://ia.net/writer/support/editor/focus-mode)）；VS Code 的 Zen mode 同理（[VS Code 文档](https://code.visualstudio.com/docs/getstarted/userinterface)） | 一个"专注复盘"模式：隐藏侧栏/次要 KPI、放大主图与笔记、`Esc` 退出；状态用 URL query 或本地存储 | 需保证键盘可达与退出可见，否则用户"迷失" |
| Side panel / drawer | Linear 重设计明确引入 **side panels to display meta properties**，并保留 list/board/timeline/**split/fullscreen** 视图（[part Ⅱ](https://linear.app/now/how-we-redesigned-the-linear-ui)）；NN/g 也建议单条记录用独立呈现（[Data Tables](https://www.nngroup.com/articles/data-tables/)） | 持仓行/回合行点击 → 右侧 360px inspector；下钻详情不再独占整页，保留主表上下文 | 窄屏需退化为全屏抽屉；焦点管理必须正确 |
| Command palette | `cmdk` 是 MIT 的 React 命令菜单/可访问 combobox 组件（[cmdk](https://github.com/pacocoursey/cmdk)，bundle 16.7KB gzip）；VS Code 命令面板是键盘优先的成熟范式（[文档](https://code.visualstudio.com/docs/getstarted/userinterface)）；Linear changelog 提到可从 **command menu** 进入设置（[changelog](https://linear.app/changelog/2024-03-20-new-linear-ui)） | ⌘K 统一入口：跳页、切时间范围、刷新/构建、打开指标详情；复用现有路由与动作 | 需要全局 actions 注册表；避免与浏览器快捷键冲突 |
| Split view | Linear 提供 split/fullscreen 视图（同上）；VS Code 并排编辑（[文档](https://code.visualstudio.com/docs/getstarted/userinterface)） | 图表 ↔ 表格并排（PC）；iPad 竖屏改为上下堆叠 | 分栏在 1024 下会挤压表格，需要列优先级 |
| Linked brushing | Vega-Lite 的 selection/brush 机制是标准做法（[Vega-Lite 文档](https://vega.github.io/vega-lite/docs/selection.html)）；uPlot 提供 cursor sync（[README](https://github.com/leeoniya/uPlot)） | 悬停净值点 → 同步高亮表格/回撤段；触屏用点按替代 hover | 联动计算的"区间汇总"必须走后端，不能前端算 |
| 笔记 / 标注 | Notion 以"文档 + 数据库"混合承载思考（[notion.com](https://www.notion.com/)） | 右侧 margin notes：复盘笔记/待办/洞察，与页面数据同屏；持久化需后端契约先行 | 不能先做纯前端 localStorage 假实现（与"不伪造"原则冲突） |
| 时间范围对比 / pin 对比 | NN/g 强调"数字必须与另一个数字比较"（[Context](https://www.nngroup.com/articles/choosing-chart-types/)）；uPlot 支持多序列 toggle 与 cursor sync | 时间范围分段控件（全历史/本月/自定义）；"钉住"对比对象后由后端返回对比序列 | 对比/差值/百分比一律后端计算（零口径） |

---

## 4. 三套视觉方向的行业依据与判断

| 维度 | A · 静观 Quiet Analyst | B · 夜盘 Night Desk | C · Bento 现代产品风 |
|---|---|---|---|
| 核心参照 | Linear 的"降噪+层级+密度"、Stripe Press 的编辑排版、NN/g 前注意原则 | Linear 暗色主题工程、OpenBB 终端形态、uPlot"数据优先/无动画" | 卡片化信息分组（bento 趋势文章仅作信号）、现代 SaaS 顶部栏+分段控件 |
| 支撑证据 | 见 §1.1、§2.1–2.4 | 见 §1.2、§2.2、§3 | 见 §1.3、§1.4 |
| 信息密度 | 中高，靠留白与层级控制 | 高，靠面板与紧凑行高 | 中低，靠卡片分组 |
| 深度思考契合度 | **最高**（单一焦点、笔记栏、编辑感） | 高（多面板并行、快速切换） | 中（易上手，但容易"看板化"） |
| 主要代价 | 排版/间距要求高；低密度页需设计兜底 | 对比度与色弱风险；白天可用性差 | 卡片边距吞噬密度；长表被切割 |
| 适用结论 | **推荐作为默认基底** | 作为可选暗色主题 / 盯盘模式 | 仅借用 KPI 卡与洞察卡语法 |

**明确推荐**：以 **A 为默认视觉基底**（最贴合"个人深度复盘"），把 **B 做成 token 化的可选主题**（同一套组件、`data-theme` 切换，避免双份实现），**C 只保留"KPI/洞察卡片"这一局部语法**，不采用全局 bento 骨架。若用户只能选一套：选 A。

---

## 5. 响应式规范（PC 优先，兼容 iPad）

### 5.1 断点策略

| 断点 | 目标设备 | 布局行为 |
|---|---|---|
| ≥1440 | 大屏 PC | 左栏 240px + 内容 ≤1360px + 可选右 inspector 360px |
| 1280–1439 | 常规 PC | 左栏 240px（可折叠到 64px）+ 内容自适应；右栏改为覆盖式抽屉 |
| 1024–1279 | iPad 横屏 | 左栏 64px 图标态；KPI 4→2 列；主图全宽；表格保留 6 个核心列 + 冻结首列 |
| 768–1023 | iPad 竖屏 | 顶部导航 → 抽屉；内容单列；表格列优先级隐藏 + 横向滚动提示；详情用全屏抽屉 |
| <768 | 兜底（非目标） | 主从列表 + 行展开；不承诺复杂表格可用 |

- `[官方]` WCAG 2.2 **1.4.10 Reflow** 要求 320 CSS px 宽下无需双向滚动即可阅读（[W3C](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)）。本项目以 768 为最低承诺，但**布局不应在 320 直接崩溃**。
- 断点按**内容容器宽度**而非设备名命名；用 `@media (min-width: ...)` 渐进增强（PC-first）。

### 5.2 容器查询（Container queries）适用性

- `[官方]` `@container` 自 2023-02 起跨浏览器可用（Baseline widely available），可基于**容器**而非视口做条件样式（[MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@container)）。
- **建议**：KPI 卡网格、洞察卡、图表卡用 `container-type: inline-size` + `@container`，这样卡片放进 3 列或 1 列都能自适应；**页面级导航/侧栏仍用媒体查询**（容器查询不适合决定全局布局，且增加调试复杂度）。
- 实施注意：`container-type: inline-size` 会创建包含块，可能影响 `position: fixed` 后代（抽屉/浮层要提到根节点或用 portal）。

### 5.3 密集表格在 iPad 上的降级阶梯

1. **1024**：保留 6 列（代码/名称/数量/成本/市值/权重），冻结表头 + 首列；权重用 40px 内联条。
2. **768–1023**：列优先级隐藏（先隐藏成本/数量，保留代码/名称/市值/权重）；右缘用渐隐/箭头提示可横向滚动，符合 NN/g "clearly indicate horizontal scrolling"（[Mobile Tables](https://www.nngroup.com/articles/mobile-tables/)）。
3. **<768**：主从列表（每行卡片：名称+市值+权重条），点开行展开详情（NN/g：单条记录独立呈现，[Data Tables](https://www.nngroup.com/articles/data-tables/)）。
4. **不做**：把宽表整体缩放到 0.7 倍（可读性崩坏）、强制横屏（NN/g 明确"rotating is a last resort"，[Mobile Tables](https://www.nngroup.com/articles/mobile-tables/)）。

### 5.4 触控、安全区、输入

- `[官方]` WCAG 2.2 **2.5.8 Target Size (Minimum) (AA)**：指针目标至少 **24×24 CSS px**，或满足间距例外（[W3C](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)）。本项目按 **44px 触控区 / 40px 视觉尺寸** 执行（表格行、分段控件、图标按钮）。
- `[官方]` iOS 安全区用 `env(safe-area-inset-*)` 处理（[MDN env()](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/env)）；若做 iPad 全屏/standalone，顶栏与抽屉需加安全区内边距。
- 触控场景下 `:hover` 信息必须有替代（点按/focus 可见）；表单输入 ≥16px 以避免 iOS 聚焦缩放（工程实践建议）。
- `[官方]` WCAG 2.2 **2.4.11 Focus Not Obscured**：sticky 顶栏/底部操作条不能遮住键盘焦点（[W3C](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum)）；抽屉打开时要做焦点圈定与 `Esc` 关闭。

---

## 6. 技术选型矩阵（针对本仓：本地个人工具 + 严格门禁 + 前端零口径 + 无现成 UI 库）

### 6.1 图表库对比（包体为 Bundlephobia 2026-10-28 实测，min / gzip）

| 方案 | 版本 | min / gzip | 依赖数 | 渲染 | React 现状 | jsdom 测试 | 内建 a11y | 适合本项目的判断 |
|---|---|---|---|---|---|---|---|---|
| **手写 SVG** | — | 0 / 0 | 0 | SVG | 原生 | 最好（可断言 DOM/路径） | 自己控制（可做到最好） | **主图首选**：13 点净值 + 回撤，几何代码量可控 |
| uPlot | 1.6.32 | 49.6KB / 21.3KB | 0 | Canvas 2D | 无官方绑定 | 需 canvas mock | 无 | 备选：未来 K 线/大数据量时用；[README](https://github.com/leeoniya/uPlot) |
| Chart.js | 4.6.1 | 199.9KB / 68.0KB | 1 | Canvas | 社区封装 | 需 canvas mock | 无（官方要求自己加 ARIA，[文档](https://www.chartjs.org/docs/latest/general/accessibility.html)） | 不推荐：canvas 不可读 + 体积/依赖不划算 |
| Recharts | 3.10.1 | 548.4KB / 146.4KB | 11 | SVG | React 组件（peer react） | 好 | 无（需自加） | 备选：若团队希望"声明式少写几何"；体积与 d3 依赖偏重 |
| visx | 4.0.0 | 148.1KB / 49.1KB（@visx/xychart） | 16 | SVG | React 组件 | 好 | 无 | 备选：primitives 灵活但学习成本高（[visx](https://airbnb.io/visx/)） |
| ECharts | 6.1.0 | 1088.7KB / 359.3KB（全量） | 2 | Canvas/SVG | 封装接入 | 需 mock | **有** aria 组件 + decal；可 tree-shake（[导入文档](https://echarts.apache.org/handbook/en/basics/import/)、[Aria](https://echarts.apache.org/handbook/en/best-practices/aria/)） | 备选：复杂图/后续扩展；本阶段不引入 |

补充：
- `[官方]` ECharts 官方提供 Canvas vs SVG 的取舍说明（大数量 Canvas 更稳、SVG 更利于调试/无障碍/SEO，[Canvas vs. SVG](https://echarts.apache.org/handbook/en/best-practices/canvas-vs-svg)）；即便选 ECharts，也应只按需 import。
- `[官方]` Chart.js 文档明确 **canvas 内容对屏幕阅读器不可见**，必须自行加 ARIA/fallback——这是"内建 a11y"一栏判空的直接依据。
- 版本兼容性：各库 peer 依赖仅声明 `react`，**具体 React 18 版本矩阵未在本环境逐一验证**；引入前以官方文档为准。

**图表结论**：主图（净值/回撤/权重条/sparkline）**手写 SVG**；把"几何投影"收敛到独立模块（见 §7）。当出现"K 线、5000+ 点、流式"需求时再评估 uPlot（0 依赖、21.3KB gzip、canvas、无动画），而不是一步到位引入全量图表库。

### 6.2 表格方案对比

| 方案 | 版本 / 体积 | 优点 | 代价 | 判断 |
|---|---|---|---|---|
| 原生 `<table>` + 自研排序/冻结 | 0KB | 语义/可访问性最好；测试最稳；与现有 `ui/Table.tsx` 演进一致（[MDN table](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/table)） | 列管理/虚拟化要自己写 | **推荐**（当前数据规模：7 行持仓、几十行流水） |
| TanStack Table | 9.3.1 / 118.0KB min / 30.9KB gzip | headless、列/排序/筛选能力强、无样式绑定（[TanStack Table](https://tanstack.com/table/latest)） | 学习与抽象成本；"headless"意味着仍要自己写全部 UI | 备选：当列配置/分组成为刚需再引入 |
| AG Grid | 36.2.0 / 1359KB min / 368.1KB gzip | 企业级网格、冻结/分组/虚拟化齐全（[AG Grid](https://www.ag-grid.com/react-data-grid/)、[列冻结](https://www.ag-grid.com/react-data-grid/column-pinning/)） | 体积巨大、样式体系独立、企业功能另需授权（[价格页](https://www.ag-grid.com/license-pricing/)） | 不推荐：与"本地个人工具/极简"冲突 |

### 6.3 样式与组件方案对比

| 方案 | 体积/构建 | 优点 | 代价 | 判断 |
|---|---|---|---|---|
| **CSS Modules + CSS 自定义属性（tokens）** | Vite 内建，0 运行时 | 作用域隔离；token 化主题（亮/暗/密度）；与单文件 CSS 现状平滑迁移；审查友好 | 需自建 tokens/组件层 | **推荐** |
| Tailwind | 构建期生成 CSS | 开发快、约束强（[utility-first](https://tailwindcss.com/docs/utility-first)） | 类名密度高、评审 diff 噪声大；与"零 UI 库/单文件 CSS"现状冲突；需额外配置 | 不推荐（本仓） |
| vanilla-extract | 构建期 CSS-in-TS | 类型安全、零运行时（[vanilla-extract](https://vanilla-extract.style/)） | 额外构建心智；团队/个人工具收益有限 | 可选，不首选 |
| shadcn / Radix | Radix dialog 1.3.1 / 38.6KB min / 12.9KB gzip | 可访问性 primitives（dialog/popover/tooltip）成熟（[Radix](https://www.radix-ui.com/primitives)、[Dialog](https://www.radix-ui.com/primitives/docs/components/dialog)） | 依赖树（dialog 15 个依赖）；shadcn 通常绑定 Tailwind | 可选：**只装需要的 primitive**（如 Dialog），或先用原生 `<dialog>` + 自管焦点 |
| cmdk | 1.1.1 / 49.6KB min / 16.7KB gzip | MIT、可访问 combobox、专为命令面板（[cmdk](https://github.com/pacocoursey/cmdk)） | 4 个依赖；样式自管 | **推荐**（命令面板） |

### 6.4 推荐组合（明确，不允许"都可以"）

> **React 18 + Vite + 现有 react-router-dom + TanStack Query（已有约定）**
> **+ CSS Modules + CSS 自定义属性 tokens（亮/暗/密度三套 token）**
> **+ 手写 SVG 图表（净值/回撤/权重/sparkline）+ 原生 `<table>` 表格**
> **+ cmdk 命令面板 + Radix Dialog（仅抽屉/对话框）**
> **不引入**：Tailwind、ECharts/Recharts/Chart.js、AG Grid、任何全量 UI 库。
> **预留升级位**：uPlot（K 线/大数据量）、TanStack Table（列配置复杂化）、ECharts（复杂图表需求）——均以"按需 import / 路由级 lazy"方式接入。

理由：与"本地个人工具（无 CDN、可离线、包体敏感）+ 严格门禁（tsc/ESLint/vitest）+ 零口径（不能靠图表库内置数据变换）"三项约束同时一致；且手写 SVG 让 golden 测试可直接断言 `path d`，这是后续零口径边界方案（§7）的基础。

---

## 7. 前端零口径 × 图表几何：边界方案（重点）

### 7.1 问题定义

- 零口径的目标是**业务口径运算不在前端发生**（金额/数量/比率/收益/汇总/对比），不是"前端不能做任何数字运算"。
- 图表必然需要 **domain → pixel** 的投影（这是渲染几何，不是业务口径）。行业里这是标准分层：D3 的 scale 就是做 domain→range 映射（[d3-scale](https://d3js.org/d3-scale)）；BFF/视图模型模式也主张由服务端或专用 BFF 产出"面向视图"的数据（[Sam Newman: BFF](https://samnewman.io/patterns/architectural/bff/)）。
- 金额/比率用整数最小单位 + 边界格式化，是金融行业通行做法：Stripe 的金额字段是整数最小货币单位，并单独处理零小数货币（[Stripe Currencies](https://docs.stripe.com/currencies)、[Charges 对象](https://docs.stripe.com/api/charges/object)）；Fowler 的 Money 模式同样主张"整数最小单位 + 边界格式化"（[Money](https://martinfowler.com/eaaCatalog/money.html)）。浮点误差是前端做金额算术的根本风险（[floating-point-gui.de](https://floating-point-gui.de/)）。

### 7.2 三种可落地方案对比

| 方案 | 做法 | 零口径合规 | 成本 | 风险 | 结论 |
|---|---|---|---|---|---|
| **A. 后端 chart-ready view**（推荐主路径） | 新增只读视图端点（如 `GET /views/nav-chart`），返回**已归一化**的 `v ∈ [0,1]`、轴刻度、回撤区间、显示标签；前端只做 `y = (1 - v) * H` 这类像素映射 | 完全合规（业务值由后端算，前端只投影） | 后端契约 + 测试（契约先行流程已有） | 视图 DTO 与展示耦合；交互范围查询需额外端点 | **采用（标准视图全部走 A）** |
| **B. 受限 chart adapter** | 唯一允许解析数值的文件（如 `src/charts/geometry.ts`），只接受归一化 `number[]`，输出像素路径；ESLint 目录级例外 + golden 测试 | 合规（范围受控、可审计） | 低（一个模块 + 测试 + ESLint override） | 例外扩散、被误用做业务计算 | **采用（仅像素投影/悬停命中）** |
| C. 前端 decimal 库 | 引入 decimal.js 在前端做业务算术 | 不合规（口径仍在前端） | 中（包体 + 代码） | 与后端口径漂移、审查成本高 | **否决** |

### 7.3 推荐边界规则（可直接写进重构方案）

1. **口径运算（禁）**：求和、平均、收益率、回撤、权重、同比、差值、四舍五入/补零 → 全部后端。
2. **展示格式（禁）**：`toFixed`、`Intl.NumberFormat` 作用于业务金额/比率 → 后端返回已格式化字符串，前端原样输出。
3. **坐标投影（允许，仅白名单文件）**：归一化值 → 像素坐标、path 命令、轴/区间位置、命中测试。**不产生新的业务数值**。
4. **ESLint**：零口径规则全局启用；仅对 `src/charts/geometry.ts`（及测试）加 override；override 需在注释中写明"仅几何投影"并在 review checklist 中检查。
5. **Golden 测试**：固定 13 点序列 → 断言 SVG `path d`；property test：`v ∈ [0,1] → y ∈ [0,H]`；后端契约测试覆盖视图端点。
6. **审计**：`grep` 例外文件清单纳入 CI；例外文件禁止 import 业务 api 类型（只允许 `NormalizedPoint` 等视图类型）。

### 7.4 视图 DTO 草案（建议，非实现）

```jsonc
// GET /views/nav-chart?range=all
{
  "as_of": "2026-10-27",
  "series": [
    { "date": "2025-10", "v": 0.000, "label": "1.000", "amount_label": "¥1,000,000.00" }
    // ... 13 点
  ],
  "y_ticks": [ { "v": 0.00, "label": "1.000" }, { "v": 0.50, "label": "1.150" } ],
  "bands": [ { "start_index": 5, "end_index": 7, "label": "回撤 -12.8%" } ],
  "annotations": [ { "index": 12, "text": "最新 1.284" } ]
}
```

- `v` 为后端归一化的**无量纲数**（不是金额），前端只投影；`label`/`amount_label` 为后端格式化字符串，前端零算术。
- 范围切换（全历史/本月/自定义）由后端按 range 返回对应序列；前端不切片计算。

---

## 8. 可访问性与性能基线

### 8.1 WCAG 2.2 关键项（与数据产品直接相关）

| 准则 | 要求 | 本项目对策 |
|---|---|---|
| 1.4.1 Use of Color (A) | 颜色非唯一手段 | 涨跌 = 符号/箭头 + 颜色；图表 = 图例 + 直接标注 |
| 1.4.3 Contrast (AA) | 正文 4.5:1 | token 化色阶，CI 用对比度脚本校验 |
| 1.4.10 Reflow (AA) | 320px 宽可读 | 断点降级 + 侧栏抽屉化 |
| 1.4.11 Non-text Contrast (AA) | UI 组件/图形 3:1 | 边框、图表主色、focus ring 校验 |
| 2.4.11 Focus Not Obscured (AA) | 焦点不被遮挡 | 抽屉/粘性栏焦点圈定、滚动补偿 |
| 2.5.7 Dragging Movements (AA) | 拖拽有替代 | 排序/调整列提供按钮式替代 |
| 2.5.8 Target Size (AA) | ≥24×24px | 本项目 44px 触控区 |

（WCAG 2.2 新增 9 条准则的官方汇总见 [W3C: What's New in WCAG 2.2](https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/)；2.2 已移除 4.1.1 Parsing。）

### 8.2 图表专门的可访问性

- 每个图表：`role="img"` + 概括性 `aria-label`（"净值从 1.000 到 1.284，最大回撤 -12.8%"），并**提供可展开的数据表替代**（`<details><summary>查看数据</summary>…`）。
- 交互点（悬停值）需键盘可达：聚焦后 `←/→` 移动数据点并朗读；触屏点按替代 hover。
- Canvas 图表（若未来引入）必须补 ARIA/fallback；ECharts 的 aria 组件可作为参考实现（[ECharts Aria](https://echarts.apache.org/handbook/en/best-practices/aria/)），Chart.js 官方明确 canvas 默认不可读（[Chart.js a11y](https://www.chartjs.org/docs/latest/general/accessibility.html)）。

### 8.3 动效、主题与字体

- `[官方]` `prefers-reduced-motion: reduce` 时关闭图表动画/过渡（[MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion)）。
- `[官方]` `prefers-color-scheme` 与 `prefers-contrast` 作为主题/对比度 token 的输入（[MDN color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme)、[MDN contrast](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-contrast)）；Linear 用 contrast 变量生成高对比主题的做法可借鉴（§1.2）。
- 颜色 token 建议使用 OKLCH（感知均匀，便于生成亮/暗/高对比主题，[MDN oklch](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/oklch)），与 Linear 的 LCH 实践同源。
- **CJK 字体栈**：`system-ui, -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`；数字列 `font-variant-numeric: tabular-nums`；**不引外部字体**（离线/隐私/首屏），如需统一字形可自托管 Noto CJK 子集（[Noto CJK](https://github.com/notofonts/noto-cjk)）。

### 8.4 性能预算（本项目建议值）

| 指标 | 预算 | 依据/说明 |
|---|---|---|
| 初始 JS（gzip） | ≤ 220KB | React 18 + router + query + 应用代码；对照：react-query 单包约 12.9KB gzip（[Bundlephobia](https://bundlephobia.com/)） |
| 图表代码 | 按路由 lazy，单块 ≤ 40KB gzip | 手写 SVG 远低于此；为未来 uPlot（21.3KB）预留 |
| CSS（gzip） | ≤ 30KB | tokens + 组件样式，避免全量 Tailwind 生成 |
| 外部请求 | 0（字体/CDN） | 本地工具可离线 |
| LCP（本地） | < 1.5s | 用 Lighthouse 验证（[Lighthouse](https://github.com/GoogleChrome/lighthouse)） |

> 说明：以上是**项目自定预算**，不是行业硬指标；核心目的是"不因重构把首屏拖慢"，并给图表库选型提供量化门槛。

---

## 9. 参考设计系统可借用的规则

| 设计系统 | 可借用规则 | 链接 |
|---|---|---|
| IBM Carbon | 数据可视化色板分 **categorical / sequential / alert**，且是 IBM Design Language 的"为可访问性与视觉和谐"裁剪子集；2x Grid 间距体系 | [Color palettes](https://carbondesignsystem.com/data-visualization/color-palettes/overview/)、[2x Grid](https://carbondesignsystem.com/elements/2x-grid/overview/)、[Data table](https://carbondesignsystem.com/components/data-table/usage/) |
| GitHub Primer | 有独立的 **Data visualization** UI pattern 与 DataTable 组件；亮/暗 token 成对设计 | [Data visualization](https://primer.style/product/ui-patterns/data-visualization/)、[DataTable](https://primer.style/product/components/data-table/) |
| Ant Design | Table 的密度（size）与固定列是成熟交互 | [Table](https://ant.design/components/table/) |
| Material 3 | 类型尺度与 tonal surface 概念（层级靠色阶而非阴影） | [M3 Typography](https://m3.material.io/styles/typography/overview) |
| Shopify Polaris | 组件级一致性、表单/数据展示规范 | [Polaris](https://shopify.dev/docs/api/app-home/polaris-web-components) |

**提炼为 6 条规则**：① 8px 间距体系 + 12 列/2x 栅格；② 分类色 ≤6 且提供非颜色编码；③ 语义 token（surface/text/border/accent）而非裸 hex；④ 亮/暗/高对比三套 token 同源生成；⑤ 密度模式（舒适/紧凑）影响行高与内边距，不影响字号层级；⑥ 数字右对齐 + tabular-nums + 后端格式化。

---

## 10. 对本项目的 12 条可执行建议

1. **视觉方向**：A 为默认基底；B 做成 `data-theme` 可选主题（同一组件树，不双份实现）；C 只借 KPI/洞察卡语法。验收：三套 token 可切换，截图走查无布局塌陷。
2. **布局骨架**：桌面「左栏 240/64 + 内容 ≤1360 + 右抽屉 360」；<1280 右抽屉覆盖式；顶部栏只放全局（搜索/命令、时间范围、状态、账户）。
3. **断点**：1280 / 1024 / 768 三档；卡片自适应用 `@container`，全局布局用媒体查询；不在组件内查询视口。
4. **表格**：冻结表头+首列、列优先级、横向滚动提示；<768 改主从列表；斑马纹按表规模启用（≥50 行）。
5. **数字**：所有金额/比率列右对齐 + `tabular-nums` + 后端字符串原样输出；禁止任何前端格式化/补零/四舍五入。
6. **涨跌语义**：默认红涨绿跌（A 股惯例）+ 正负号/箭头 + 设置可切换；颜色不作为唯一编码（WCAG 1.4.1）。
7. **主图**：手写 SVG 折线 + 回撤水下面积 + 区间阴影；13 点数据不引入图表库；峰值/谷底/最新值直接标注。
8. **零口径边界**：后端 `views/*` 返回归一化序列 + 轴 + 区间 + 标签；`src/charts/geometry.ts` 唯一例外 + ESLint override + golden 测试 + CI 审计。
9. **命令面板**：⌘K 统一入口（跳页/切范围/刷新构建/打开详情），cmdk 16.7KB gzip + MIT；键盘全可达。
10. **洞察区**：右侧 margin notes 承载「复盘笔记/待办/降级提示」，持久化必须后端契约先行，先用只读洞察文案，禁止 localStorage 假实现。
11. **a11y**：focus-visible 统一、reduced-motion 关闭动画、图表 aria + 数据表替代、44px 触控、对比度脚本进 CI。
12. **性能与迁移**：初始 JS ≤220KB gzip、图表路由级 lazy、按路由渐进迁移并保留现有 200+ 断言；先抽 tokens/布局/表格，再换视觉，最后接图表视图端点。

---

## 11. 参考来源（按主题）

**趋势 / 产品**
- [Linear — Welcome to the new Linear（2024-03）](https://linear.app/changelog/2024-03-20-new-linear-ui)
- [Linear — A design reset (part I)](https://linear.app/now/a-design-reset)
- [Linear — How we redesigned the Linear UI (part Ⅱ)](https://linear.app/now/how-we-redesigned-the-linear-ui)
- [Linear Method](https://linear.app/method)
- [Stripe Press](https://press.stripe.com/) · [Stripe](https://stripe.com/) · [Notion](https://www.notion.com/) · [Robinhood](https://robinhood.com/)
- [OpenBB](https://openbb.co/) · [OpenBB GitHub](https://github.com/OpenBB-finance/OpenBB)
- [Calm Technology（Amber Case）](https://calmtech.com/)
- [Bento UI 趋势文章（抓取失败，仅趋势信号）](https://blog.depositphotos.com/bento-ui.html)

**数据可视化 / 表格可用性**
- [NN/g — Dashboards: Making Charts and Graphs Easier to Understand](https://www.nngroup.com/articles/dashboards-preattentive/)
- [NN/g — Choosing Chart Types: Consider Context](https://www.nngroup.com/articles/choosing-chart-types/)
- [NN/g — Data Tables: Four Major User Tasks](https://www.nngroup.com/articles/data-tables/)
- [NN/g — Mobile Tables](https://www.nngroup.com/articles/mobile-tables/)
- [MDN — font-variant-numeric（tabular-nums）](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/font-variant-numeric)
- [光明网 — 中国股市为何"红涨绿跌"](https://epaper.gmw.cn/wzb/html/2015-08/01/nw.D110000wzb_20150801_1-05.htm)
- [Carbon — Data visualization color palettes](https://carbondesignsystem.com/data-visualization/color-palettes/overview/)
- [Carbon — Data table](https://carbondesignsystem.com/components/data-table/usage/)
- [Primer — Data visualization pattern](https://primer.style/product/ui-patterns/data-visualization/)
- [Ant Design — Table](https://ant.design/components/table/)
- [Material 3 — Typography](https://m3.material.io/styles/typography/overview)
- [Polaris Web Components](https://shopify.dev/docs/api/app-home/polaris-web-components)

**交互模式**
- [iA Writer — Focus Mode](https://ia.net/writer/support/editor/focus-mode)
- [VS Code — User Interface（命令面板 / Zen / 并排编辑）](https://code.visualstudio.com/docs/getstarted/userinterface)
- [cmdk — ⌘K 命令菜单组件](https://github.com/pacocoursey/cmdk)
- [Vega-Lite — Selections / 交互](https://vega.github.io/vega-lite/docs/selection.html)

**响应式 / 无障碍 / 性能**
- [WCAG 2.2 — What's New](https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/) · [WCAG 2.2 标准](https://www.w3.org/TR/WCAG22/)
- [WCAG 1.4.1 Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) · [1.4.10 Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) · [1.4.11 Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) · [2.4.11 Focus Not Obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum) · [2.5.8 Target Size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- [MDN — @container](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@container) · [MDN — env() 安全区](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/env) · [MDN — prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion) · [MDN — prefers-color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme) · [MDN — prefers-contrast](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-contrast) · [MDN — oklch()](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/oklch) · [MDN — font-family](https://developer.mozilla.org/en-US/docs/Web/CSS/font-family)
- [Noto CJK](https://github.com/notofonts/noto-cjk) · [Lighthouse](https://github.com/GoogleChrome/lighthouse)

**技术选型 / 架构**
- [Bundlephobia](https://bundlephobia.com/)（包体数据实测，2026-10-28）
- [ECharts — 按需导入](https://echarts.apache.org/handbook/en/basics/import/) · [ECharts — Aria](https://echarts.apache.org/handbook/en/best-practices/aria/) · [ECharts — Canvas vs SVG](https://echarts.apache.org/handbook/en/best-practices/canvas-vs-svg)
- [uPlot](https://github.com/leeoniya/uPlot) · [Chart.js — Accessibility](https://www.chartjs.org/docs/latest/general/accessibility.html) · [Recharts](https://recharts.org/) · [visx](https://airbnb.io/visx/) · [Observable Plot](https://github.com/observablehq/plot)
- [TanStack Table](https://tanstack.com/table/latest) · [AG Grid](https://www.ag-grid.com/react-data-grid/) · [AG Grid 列冻结](https://www.ag-grid.com/react-data-grid/column-pinning/) · [AG Grid 价格](https://www.ag-grid.com/license-pricing/)
- [MDN — `<table>`](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/table)
- [Tailwind — Utility-first](https://tailwindcss.com/docs/utility-first) · [vanilla-extract](https://vanilla-extract.style/) · [CSS Modules](https://github.com/css-modules/css-modules) · [Radix Primitives](https://www.radix-ui.com/primitives) · [Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog)
- [d3-scale](https://d3js.org/d3-scale) · [Sam Newman — BFF](https://samnewman.io/patterns/architectural/bff/) · [Martin Fowler — Money](https://martinfowler.com/eaaCatalog/money.html) · [Stripe Currencies](https://docs.stripe.com/currencies) · [Stripe Charges](https://docs.stripe.com/api/charges/object) · [floating-point-gui.de](https://floating-point-gui.de/)

> 备注：本环境无法直连 Bloomberg / TradingView / Koyfin / Observable / Wikipedia / Investopedia，故未引用其页面内容；如需补充这几家的一手材料，建议由人工在可访问网络复核后再写入。
