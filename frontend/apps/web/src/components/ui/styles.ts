/**
 * 包装层共享的 Tailwind 片段（仅本目录引用，不对外导出）。
 *
 * 目的：焦点环、禁用态、遮罩、弹层面板只定义一次，新增 Radix 组件必须复用，
 * 避免每个组件各写一套 outline/ring/阴影导致观感漂移。
 */

/** 焦点环：2px 语义 accent + 2px 偏移，与 `styles.css` 的 `:focus-visible` 观感一致。 */
export const focusRing =
  "focus-visible:tw-outline-none focus-visible:tw-ring-2 focus-visible:tw-ring-ring focus-visible:tw-ring-offset-2 focus-visible:tw-ring-offset-background";

export const disabledState = "disabled:tw-pointer-events-none disabled:tw-opacity-50";

/** 模态遮罩（对话 / 警告对话共用）。 */
export const overlayClass = "tw-fixed tw-inset-0 tw-z-overlay tw-bg-scrim tw-backdrop-blur-[2px]";

/** 浮层面板（下拉 / 弹出 / 选择器共用）。 */
export const panelClass =
  "tw-z-dialog tw-rounded-md tw-border tw-border-border tw-bg-popover tw-text-popover-foreground tw-shadow-2";

/** 浮层进入/退出动效（tailwindcss-animate）。 */
export const popoverMotion =
  "data-[state=open]:tw-animate-in data-[state=open]:tw-fade-in-0 data-[state=open]:tw-zoom-in-95 data-[state=closed]:tw-animate-out data-[state=closed]:tw-fade-out-0 data-[state=closed]:tw-zoom-out-95";

/** 菜单/选择器按 side 滑入，避免从中心缩放造成的错位感。 */
export const panelSideMotion =
  "data-[state=open]:data-[side=bottom]:tw-slide-in-from-top-2 data-[state=open]:data-[side=left]:tw-slide-in-from-right-2 data-[state=open]:data-[side=right]:tw-slide-in-from-left-2 data-[state=open]:data-[side=top]:tw-slide-in-from-bottom-2";
