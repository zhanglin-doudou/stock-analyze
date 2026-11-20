# 前端 UI 全局化：Tailwind 3.4 + Radix 包装层

> 状态：**已实施（2026-11-19）**
> 取代：`docs/architecture/2026-11-13-import-mapping-refactor.md` §8.1 **H-1「仅 `/import` 限定域」**
> 关联：`frontend/AGENTS.md` §10、`frontend/apps/web/src/components/ui/README.md`

## 1. 背景

2026-11-13 的导入映射重构按 H-1 批准了**限定范围** Tailwind + shadcn：`prefix:"tw-"`、`preflight:false`、content 只扫 `/import`、`import.css` 随路由懒加载、Portal 挂 `#import-portal`。该方案成功隔离了首屏预算，但也带来四个问题：

1. **能力不可复用**：其他页面写 `tw-` 类不会生成 CSS，Radix 组件只能被 `/import` 使用。
2. **包装层漂移**：`Button` 用 10px 圆角 / 36px 高，`SelectTrigger` 用 `--r-sm` / `--control-h`；焦点环、禁用态各写一套；`@radix-ui/react-dialog` 已安装却没有包装。
3. **静默失效**：Tailwind v3 无法对 `var(--color)` 应用 `/opacity` 修饰符，`tw-bg-muted/50` 实际输出为纯色；这类写法在代码里无法从类型/构建发现。
4. **没有边界门禁**：页面可以直接 `import "@radix-ui/..."`，没有测试阻止“绕过包装层”。

用户明确要求把 Tailwind 与 Radix **全局化**，并建立“新组件优先 Radix、先按项目风格二次封装再使用”的规范。本决议据此取代 H-1 的范围限制。

## 2. 决策

### D1 · Tailwind 全局可用，但保持 `tw-` 前缀与 `preflight:false`

- `content` 覆盖 `./index.html` 与 `./src/**/*.{ts,tsx}`，任何页面都能使用工具类。
- 保留 `prefix:"tw-"`：现有手写 CSS 存在 `.table` / `.card` / `.btn` / `.muted` 等通用类，去前缀会直接冲突。
- 保留 `preflight:false`：既有 14 个页面依赖 UA 排版（例如 `textarea` 的默认边框、标题 margin），全量 preflight 需要逐页回归，收益不足。
- 组件必需的 border/表单 reset 收敛到 `src/styles/tailwind.css`，且只作用于 `[class^="tw-"], [class*=" tw-"]`，特异性为 0。

### D2 · 全局 CSS 入口只保留一个

- 唯一入口：`src/styles/tailwind.css`，由 `src/main.tsx` 引入一次。
- 删除 `src/features/import/import.css` 与 `#import-portal[data-import-ui]` 限定域；`components.json` 的 `tailwind.css` 指向新入口。
- 代价：Tailwind 工具类从 Import 懒加载 chunk 移到入口 CSS（实测 +2.9KB gzip，入口 CSS 9.5KB / 预算 30KB）。收益：任何页面立即可用，且不会再出现“类名不生成”的隐性问题。

### D3 · Radix 只在 `src/components/ui` 内出现

- 页面/功能代码**禁止**直接 `import "@radix-ui/*"`。
- 页面按需导入 **子模块**：`@/components/ui/button`、`@/components/ui/dialog`……不要从 `@/components/ui` barrel 导入。
  - 原因：我们的包装组件在模块顶层调用 `React.forwardRef`，Rollup 无法把它当作纯副作用删除；barrel 会把所有 Radix 包打进当前页面 chunk。实测 `/import` 从 52.3KB 涨到 63.9KB gzip，改回子模块后约 53.1KB。
- `src/components/ui/index.ts` 保留为“统一出口/文档/测试”的公共面，不作为业务页面入口。

### D4 · 本次不迁移既有页面

- 现有手写 CSS + 语义 token 继续工作；新功能、新组件优先 Tailwind + Radix 包装层。
- 逐页迁移另开任务，必须有视觉回归与预算复测。

### D5 · 规范与门禁一起落地

- 视觉只允许消费 `tokens.css` 语义变量；包装层禁止裸 hex。
- 新增 Radix 组件必须完成 tokens 化包装、a11y、测试、`index.ts` 导出后才允许业务使用。
- 用 ESLint + `architecture.test.ts` 双重门禁固化上述边界。

## 3. 架构与文件

```text
frontend/apps/web/
├─ tailwind.config.ts              # 全局：tw- 前缀 / preflight:false / tokens 映射
├─ components.json                 # shadcn 生成约定；css 指向 styles/tailwind.css
├─ src/
│  ├─ main.tsx                     # 引入 styles/tailwind.css（唯一入口）
│  ├─ styles/
│  │  ├─ tokens.css                # 唯一视觉变量来源（未改）
│  │  └─ tailwind.css              # @tailwind + scoped base reset
│  ├─ lib/utils.ts                 # cn()：prefix + 自定义 token 类分组
│  └─ components/ui/
│     ├─ index.ts                  # 公共出口（测试/文档；页面不用 barrel）
│     ├─ styles.ts                 # focusRing / overlay / panel / motion 共享片段
│     ├─ portal.ts                 # Portal 容器 context（默认 body）
│     ├─ button.tsx  input.tsx  label.tsx  badge.tsx  card.tsx  table.tsx
│     ├─ select.tsx  switch.tsx  checkbox.tsx
│     ├─ dialog.tsx  alert-dialog.tsx  dropdown-menu.tsx
│     ├─ tooltip.tsx  popover.tsx  tabs.tsx
│     ├─ architecture.test.ts      # 边界/规范门禁
│     └─ components.test.tsx       # 包装组件 smoke + a11y
```

## 4. Tailwind 规范

| 主题 | 规则 |
|---|---|
| 类名前缀 | 一律 `tw-`（`cn()` 已用 `extendTailwindMerge({ prefix: "tw-" })`） |
| 颜色 | 只能用 config 中映射到 `tokens.css` 的语义 key；禁止 `tw-bg-[#fff]` / 私有 hex |
| 透明度 | **禁止** `tw-bg-primary/50`、`tw-text-muted/70`：v3 对 `var()` 颜色会**静默丢弃**透明度。需要浅色底用 `*-soft` token（`accent`/`warn-soft`/`danger-soft`…）或实体 `surface-2` |
| 控制件 | 高度 `tw-h-control`（`--control-h`）、圆角 `tw-rounded-sm`（`--r-sm`） |
| 卡片 | 圆角 `tw-rounded-card`（`--r-card`，项目纸感直角） |
| 字号 | 语义档位：`tw-text-micro/small/body/h3/h2/h1/display`；默认 `text-sm` 等仍可用 |
| 阴影 | `tw-shadow-1` / `tw-shadow-2`（映射 `--shadow-1/2`） |
| 层级 | `tw-z-rail/topbar/drawer/dialog/command/toast`，禁止裸 `tw-z-50` 新增 |
| 动效 | `tw-duration-ui`（`--dur`）；浮层用 `tailwindcss-animate` 的 `data-[state=open]:tw-animate-in` 组合 |
| 暗色 | 由 `<html data-theme="dark">` 覆盖同名 token；组件不写 `dark:` 私有色值 |
| 全局 base | 只在 `styles/tailwind.css`，只作用于 `tw-` 类元素；禁止新增 `:root` 或全局元素选择器 |

## 5. Radix 包装规范

### 5.1 必须遵守

1. **位置**：只能放 `src/components/ui/*.tsx`；一个 primitive 一个文件，文件名 kebab-case。
2. **导出**：具名导出 + `index.ts` 追加 `export * from "./xxx";`；不要 default export。
3. **样式**：用 `cn()` + Tailwind 工具类；颜色/圆角/阴影/层级只取第 4 节语义 key；共享片段从 `./styles` 取（`focusRing` / `disabledState` / `overlayClass` / `panelClass` / `popoverMotion` / `panelSideMotion`）。
4. **变体**：多形态组件用 CVA（参考 `button.tsx` / `badge.tsx`），`defaultVariants` 必须显式。
5. **转发**：`React.forwardRef` + `React.ElementRef` / `ComponentPropsWithoutRef`；`displayName` 使用 Radix 原值或组件名。
6. **Portal**：通过 `usePortalContainer()` 取容器，默认 `undefined` → `document.body`；不要硬编码 `#import-portal`。
7. **a11y**：保留 Radix 的 `aria-*`/键盘行为；关闭按钮必须有可读文案（`tw-sr-only`）；图标 `aria-hidden`。
8. **测试**：至少覆盖渲染/交互；新增 primitive 在 `components.test.tsx` 补一条 smoke。
9. **零口径**：金额/比率仍只渲染后端字符串；包装层不做 `Number()` / `parseFloat` / `toFixed`。

### 5.2 新增组件清单（顺序执行）

```text
1. pnpm add @radix-ui/react-xxx
2. 读 Radix 文档确认 Root/Trigger/Portal/Content 的受控与非受控 API
3. 在 src/components/ui/xxx.tsx 按 §5.1 完成封装（先写 default 变体，再补 sizes）
4. 在 index.ts 增加 export *
5. 在 components.test.tsx 补 smoke（打开/关闭/键盘可达）
6. 在页面按子模块导入：import { Xxx } from "@/components/ui/xxx"
7. 跑 pnpm -r lint / test / build / budget
8. 如新增 token，先改 tokens.css，再改 tailwind.config.ts，最后更新本文件
```

### 5.3 现有组件变体

| 组件 | 变体/尺寸 |
|---|---|
| `Button` | `default / secondary / outline / ghost / destructive / link` × `default / sm / lg / icon` |
| `Badge` | `default / secondary / outline / destructive / success / warn / info` |
| `Input` / `Textarea` | 无变体，统一 control 高度/焦点环 |
| `Card` | `Card / Header / Title / Description / Content / Footer` |
| `Table` | `Table / Header / Body / Footer / Row / Head / Cell / Caption` |
| `Tabs` | `Tabs / List / Trigger / Content`（segmented 风格） |
| `Dialog` / `AlertDialog` | 模态 + 遮罩 + Header/Footer/Title/Description/Close |
| `DropdownMenu` | Item / CheckboxItem / RadioItem / Label / Separator / Shortcut / Sub* |
| `Select` | Trigger / Content / Item / Label / Separator / Scroll* |
| `Tooltip` | `Tooltip` 自带 Provider，页面无需根节点配置 |
| `Popover` | Trigger / Content / Anchor / Close |
| `Switch` / `Checkbox` | 受控/非受控均透传 Radix |

## 6. 性能与代码分割

- **入口**：JS ≤220KB gzip、CSS ≤30KB gzip（`frontend/scripts/check-budget.mjs`）。
- **页面按需**：只用子模块导入（见 D3）；不要为了“写法统一”从 barrel 导入。
- **新增页面**：优先把重组件（Dialog/Dropdown/Tooltip）留在交互触发后才需要的路由 chunk 内。
- **Import chunk**：本次实测 53.1KB gzip（HEAD 基线 52.3KB），仍低于历史设计 §8.1 的 80KB gzip 上限；Tailwind CSS 已从该 chunk 移除，入口 CSS 增加约 2.9KB gzip。
  - 注：`frontend/scripts/check-budget.mjs` 当前仍只校验入口 chunk；Import chunk 的 80KB 上限为人工核对，历史设计 §8.1 的 manifest 统计尚未落地（本次未扩大范围）。

## 7. 门禁

```bash
cd frontend && pnpm -r lint       # ESLint：零口径 + 禁止页面直连 Radix / barrel
cd frontend && pnpm -r typecheck
cd frontend && pnpm -r test       # 含 architecture.test.ts + components.test.tsx
cd frontend && pnpm -r build
cd frontend && pnpm budget
```

`architecture.test.ts` 断言：

1. `src/components/ui` 之外不得出现 `@radix-ui/*` 导入；
2. 页面不得从 `@/components/ui` barrel 导入；
3. 包装层不得出现裸 hex；
4. `tailwind.config.ts` 必须保持 `tw-` / `preflight:false` / 全 src content；
5. `main.tsx` 必须引入 `styles/tailwind.css`，旧 `features/import/import.css` 不得回流；
6. 每个 `components/ui/*.tsx` 必须被 `index.ts` 导出。

ESLint 同步提供 `no-restricted-imports`（`@radix-ui/*` 与 `@/components/ui`）即时反馈。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 全局 CSS 影响既有页面 | 保留 `preflight:false`；base reset 只作用于 `tw-` 类元素；既有页未迁入 `tw-` 类，理论零影响；后续逐页迁移时做视觉回归 |
| 入口 CSS 随页面使用增长 | 预算门禁 30KB；新增工具类需在 PR 里看 `pnpm budget` |
| 页面误用 barrel 导致 chunk 膨胀 | ESLint + architecture test 双门禁；D3 写明原因与实测数据 |
| 透明度修饰符静默失效 | 文档 + 评审清单；需要 alpha 时改用 `*-soft` token |
| Radix 升级破坏 API | 包装层隔离；升级只改 `components/ui`；`pnpm -r test` 覆盖 smoke |
| 既有测试失败被误判为本次回归 | 本次验收已确认 `Dashboard.test.tsx`（2 条）与 `Assets.test.tsx`（1 条）在 HEAD 基线同样失败，与 UI 全局化无关 |

## 9. 验收记录

- 2026-11-19：`pnpm -r lint` 通过；`pnpm -r typecheck` 通过；新增 UI 门禁/组件测试 14 条通过；`pnpm -r build` 通过；`pnpm budget` 通过（JS 90.7KB / CSS 9.5KB gzip）。
- 全量 vitest：289 通过、3 失败；3 条失败均已在 HEAD 基线复现（非本次引入）。
- H-1 限定域相关代码（`features/import/import.css`、`#import-portal[data-import-ui]`）已移除；历史设计与评审文档保留原文，仅加“已被取代”注记。
