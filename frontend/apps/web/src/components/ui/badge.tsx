import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/** 徽标。圆角沿用既有 `.badge` 的 10px（--r-md），字号取 --fs-sm。 */
const badgeVariants = cva(
  "tw-inline-flex tw-items-center tw-rounded-md tw-border tw-px-2 tw-py-0.5 tw-text-small tw-font-medium tw-whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "tw-border-transparent tw-bg-primary tw-text-primary-foreground",
        secondary: "tw-border-transparent tw-bg-secondary tw-text-secondary-foreground",
        outline: "tw-border-border tw-text-muted-foreground",
        destructive: "tw-border-transparent tw-bg-destructive tw-text-destructive-foreground",
        success: "tw-border-transparent tw-bg-ok-soft tw-text-ok",
        warn: "tw-border-transparent tw-bg-warn-soft tw-text-warn",
        info: "tw-border-transparent tw-bg-info-soft tw-text-info",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
