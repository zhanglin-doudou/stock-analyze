import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "tw-inline-flex tw-items-center tw-rounded-md tw-border tw-px-2 tw-py-0.5 tw-text-xs tw-font-medium",
  {
    variants: {
      variant: {
        default: "tw-border-transparent tw-bg-primary tw-text-primary-foreground",
        secondary: "tw-border-transparent tw-bg-secondary tw-text-secondary-foreground",
        outline: "tw-border-border tw-text-muted-foreground",
        destructive: "tw-border-transparent tw-bg-destructive tw-text-destructive-foreground",
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
