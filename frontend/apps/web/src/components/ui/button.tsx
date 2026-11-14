import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "tw-inline-flex tw-items-center tw-justify-center tw-gap-2 tw-whitespace-nowrap tw-rounded-md tw-text-sm tw-font-medium tw-transition-colors focus-visible:tw-outline-none focus-visible:tw-ring-2 focus-visible:tw-ring-ring disabled:tw-pointer-events-none disabled:tw-opacity-50",
  {
    variants: {
      variant: {
        default: "tw-bg-primary tw-text-primary-foreground hover:tw-opacity-90",
        secondary: "tw-bg-secondary tw-text-secondary-foreground hover:tw-bg-accent",
        outline: "tw-border tw-border-input tw-bg-transparent hover:tw-bg-accent",
        ghost: "hover:tw-bg-accent",
        destructive: "tw-bg-destructive tw-text-destructive-foreground hover:tw-opacity-90",
      },
      size: {
        default: "tw-h-9 tw-px-4 tw-py-2",
        sm: "tw-h-8 tw-rounded-md tw-px-3 tw-text-xs",
        lg: "tw-h-10 tw-px-6",
        icon: "tw-h-8 tw-w-8",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
