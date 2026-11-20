import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cn } from "@/lib/utils";
import { usePortalContainer } from "./portal";

const TooltipProvider = TooltipPrimitive.Provider;
const TooltipTrigger = TooltipPrimitive.Trigger;

/**
 * 自带 Provider 的 Tooltip：页面无需在根节点手工包 Provider 即可直接使用。
 * 若应用已包 `TooltipProvider`，Radix 允许嵌套，行为不变（内层 delay 生效）。
 */
export interface TooltipProps
  extends React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Root> {
  delayDuration?: number;
}

function Tooltip({ delayDuration = 200, ...props }: TooltipProps) {
  return (
    <TooltipPrimitive.Provider delayDuration={delayDuration}>
      <TooltipPrimitive.Root {...props} />
    </TooltipPrimitive.Provider>
  );
}
Tooltip.displayName = "Tooltip";

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, children, ...props }, ref) => {
  const container = usePortalContainer();
  return (
    <TooltipPrimitive.Portal container={container ?? undefined}>
      <TooltipPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        className={cn(
          // 不能加 overflow-hidden：箭头在内容框外，会被裁掉导致「晚一帧才出现」。
          "tw-z-toast tw-rounded-sm tw-bg-foreground tw-px-2.5 tw-py-1.5 tw-text-small tw-text-background tw-shadow-2 tw-animate-in tw-fade-in-0 tw-zoom-in-95 data-[state=closed]:tw-animate-out data-[state=closed]:tw-fade-out-0 data-[state=closed]:tw-zoom-out-95",
          className,
        )}
        {...props}
      >
        {children}
        {/* 固定箭头尺寸：省掉一次 ResizeObserver 测量，箭头与内容同帧入场。 */}
        <TooltipPrimitive.Arrow width={10} height={5} className="tw-fill-foreground" />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
});
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };

type TooltipSide = React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>["side"];
type TooltipAlign = React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>["align"];

export interface HintTooltipProps {
  /** tooltip 内容：接受任意节点（多行说明用 `<span className="tw-block">` 分行）。 */
  content: React.ReactNode;
  /** 触发元素，被包进可聚焦的 `span`（键盘 Tab 也能打开）。 */
  children: React.ReactNode;
  side?: TooltipSide;
  align?: TooltipAlign;
  delayDuration?: number;
  /** 触发元素的可访问名称（如「阻断原因」）；留空则由子节点文本承担。 */
  label?: string;
  className?: string;
  contentClassName?: string;
}

/**
 * 通用说明型 Tooltip：一行封装 `Tooltip + 可聚焦触发区 + 主题色内容`。
 *
 * 视觉与 `TooltipContent` 共用同一套 tokens（前景/背景反转、圆角、阴影），
 * 页面不需要再引入 Radix 或手写触发元素样式。
 */
function HintTooltip({
  content,
  children,
  side = "top",
  align = "center",
  delayDuration = 150,
  label,
  className,
  contentClassName,
}: HintTooltipProps) {
  return (
    <Tooltip delayDuration={delayDuration}>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          aria-label={label}
          className={cn(
            "tw-inline-flex tw-cursor-help tw-items-center tw-rounded-sm tw-outline-none focus-visible:tw-ring-2 focus-visible:tw-ring-ring focus-visible:tw-ring-offset-2 focus-visible:tw-ring-offset-background",
            className,
          )}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent
        side={side}
        align={align}
        className={cn("tw-max-w-xs tw-leading-body", contentClassName)}
      >
        {content}
      </TooltipContent>
    </Tooltip>
  );
}
HintTooltip.displayName = "HintTooltip";

export { HintTooltip };
