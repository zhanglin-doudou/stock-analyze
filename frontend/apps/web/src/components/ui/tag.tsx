import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Tag：Radix 风格的软色标签，可选尾部数字徽标。
 *
 * - 内置色调：normal（中性）/ danger / warning / green，只消费 tokens.css 语义色；
 * - 数字徽标只展示后端给出的计数（`count`），组件不做任何数值计算或格式化；
 * - 标签本身是普通 `span`，调用方可继续传 `title`/`aria-*` 等 HTML 属性。
 */
const tagVariants = cva(
  "tw-inline-flex tw-items-center tw-gap-1 tw-rounded-sm tw-border tw-px-2 tw-py-0.5 tw-text-small tw-font-medium tw-whitespace-nowrap",
  {
    variants: {
      color: {
        normal: "tw-border-transparent tw-bg-secondary tw-text-secondary-foreground",
        danger: "tw-border-transparent tw-bg-danger-soft tw-text-danger",
        warning: "tw-border-transparent tw-bg-warn-soft tw-text-warn",
        green: "tw-border-transparent tw-bg-ok-soft tw-text-ok",
      },
    },
    defaultVariants: { color: "normal" },
  },
);

/** 数字徽标：描边+当前文字色，浅底上仍可分辨，避免额外色值。 */
const countClass =
  "tw-inline-flex tw-h-4 tw-min-w-4 tw-items-center tw-justify-center tw-rounded-pill tw-border tw-border-current tw-px-1 tw-text-micro tw-font-semibold tw-leading-none tw-tabular-nums";

export interface TagProps
  extends Omit<React.HTMLAttributes<HTMLSpanElement>, "color">,
    VariantProps<typeof tagVariants> {
  /** 数字徽标；`null`/`undefined` 不渲染，`0` 按调用方语义照常展示。 */
  count?: number | string | null;
}

function Tag({ className, color, count, children, ...props }: TagProps) {
  return (
    <span className={cn(tagVariants({ color }), className)} {...props}>
      {children}
      {count == null || count === "" ? null : <span className={countClass}>×{count}</span>}
    </span>
  );
}

export { Tag, tagVariants };
