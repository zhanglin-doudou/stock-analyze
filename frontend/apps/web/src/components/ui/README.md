# UI 组件规范（Tailwind + Radix 包装层）

> 完整决策见 [`docs/architecture/2026-11-19-frontend-ui-globalization.md`](../../../../../../docs/architecture/2026-11-19-frontend-ui-globalization.md)。
> 一句话：**页面只消费本目录的封装组件；新增 Radix 组件必须先在这里完成 tokens 化包装与测试。**
>
> 不直接跑 shadcn CLI：`components.json` 仅保留路径/前缀生成约定；组件一律按本规范手工落位与改造。

## 页面怎么用

```tsx
// DO — 按需导入子模块（保持路由 chunk 只包含用到的 Radix）
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

// DON'T — 不要从 barrel 导入：会把整套 Radix 打进当前页面 chunk
import { Button, Dialog } from "@/components/ui";

// DON'T — 页面不得直接依赖 Radix
import * as DialogPrimitive from "@radix-ui/react-dialog";
```

Tailwind 工具类在任何页面可用，但必须带 `tw-` 前缀：

```tsx
<div className="tw-flex tw-items-center tw-gap-3 tw-rounded-sm tw-border tw-border-border tw-bg-surface tw-p-4">
  <span className="tw-text-small tw-text-muted-foreground">持仓市值</span>
  <strong className="tw-text-h3 tw-tabular-nums">{backendAmountString}</strong>
</div>
```

## 必须遵守

1. **颜色/圆角/阴影/层级只取 `tokens.css` 语义变量**：`tw-bg-surface`、`tw-text-muted-foreground`、`tw-rounded-card`、`tw-shadow-2`、`tw-z-dialog`。禁止 `tw-bg-[#fff]`、`tw-text-[#123]`。
2. **不要对 `var()` 颜色用 `/opacity`**：Tailwind v3 会静默忽略 `tw-bg-primary/50`。需要浅色用 `tw-bg-accent`（`--accent-soft`）、`tw-bg-warn-soft` 等。
3. **控制件尺寸统一**：`tw-h-control`（34px）、`tw-rounded-sm`（6px）；卡片用 `tw-rounded-card`（项目纸感 0px）。
4. **焦点/禁用态复用 `./styles`**：`focusRing`、`disabledState`，不要各写一套 outline。
5. **Portal 用 `usePortalContainer()`**：默认 `undefined` → `document.body`；不要再建 `#import-portal`。
6. **a11y 不降级**：保留 Radix 的 `aria-*` 与键盘行为；图标 `aria-hidden`；关闭按钮带 `tw-sr-only` 文案。
7. **零口径不破坏**：金额/数量/比率只渲染后端字符串，包装层不做 `Number()` / `parseFloat` / `toFixed`。

## 新增 Radix 组件流程

```text
1. pnpm add @radix-ui/react-xxx
2. 新建 src/components/ui/xxx.tsx：forwardRef + cn() + tokens + CVA（如需变体）
3. 从 ./styles 取 focusRing / disabledState / overlayClass / panelClass / popoverMotion
4. 在 index.ts 增加 export * from "./xxx";
5. 在 components.test.tsx 增加 smoke（渲染 + 打开/关闭 + 键盘可达）
6. 页面用 import { Xxx } from "@/components/ui/xxx"
7. pnpm -r lint && pnpm -r test && pnpm -r build && pnpm budget
```

## 现有组件

| 文件 | 导出 |
|---|---|
| `button.tsx` | `Button`, `buttonVariants` |
| `input.tsx` | `Input`, `Textarea` |
| `label.tsx` | `Label` |
| `badge.tsx` | `Badge`, `badgeVariants` |
| `card.tsx` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter` |
| `table.tsx` | `Table`, `TableHeader`, `TableBody`, `TableFooter`, `TableRow`, `TableHead`, `TableCell`, `TableCaption` |
| `select.tsx` | `Select*` |
| `switch.tsx` | `Switch` |
| `checkbox.tsx` | `Checkbox` |
| `tabs.tsx` | `Tabs*` |
| `dialog.tsx` | `Dialog*` |
| `alert-dialog.tsx` | `AlertDialog*` |
| `dropdown-menu.tsx` | `DropdownMenu*` |
| `tooltip.tsx` | `Tooltip*`（自带 Provider） |
| `popover.tsx` | `Popover*` |
| `portal.ts` | `PortalContainerContext`, `usePortalContainer` |
| `index.ts` | 统一出口（仅测试/文档/整套消费；页面按子模块导入） |

## 门禁

- `eslint.config.js`：页面禁止 `@radix-ui/*` 与 `@/components/ui` barrel。
- `architecture.test.ts`：包装层裸 hex、配置回归、旧限定域 CSS 回流、统一导出。
- `components.test.tsx`：包装组件 smoke + a11y。
