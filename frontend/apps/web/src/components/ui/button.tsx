import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { disabledState, focusRing } from "./styles";
import { Spinner } from "./spinner";

/**
 * 按钮。默认高度/圆角取项目控制件 token（--control-h / --r-sm），
 * 与手写 `.btn`、SelectTrigger 保持一致；新增页面优先用本组件。
 */
const buttonVariants = cva(
  `tw-inline-flex tw-items-center tw-justify-center tw-gap-2 tw-whitespace-nowrap tw-rounded-sm tw-text-body tw-font-medium tw-transition-colors tw-duration-ui ${focusRing} ${disabledState}`,
  {
    variants: {
      variant: {
        default: "tw-bg-primary tw-text-primary-foreground hover:tw-opacity-90",
        secondary: "tw-bg-secondary tw-text-secondary-foreground hover:tw-bg-accent",
        outline: "tw-border tw-border-input tw-bg-transparent tw-text-foreground hover:tw-bg-accent",
        ghost: "tw-text-foreground hover:tw-bg-accent",
        destructive: "tw-bg-destructive tw-text-destructive-foreground hover:tw-opacity-90",
        link: "tw-text-primary tw-underline-offset-4 hover:tw-underline",
      },
      size: {
        default: "tw-h-control tw-px-4 tw-py-2",
        sm: "tw-h-8 tw-rounded-sm tw-px-3 tw-text-small",
        lg: "tw-h-10 tw-rounded-sm tw-px-6",
        icon: "tw-h-control tw-w-control",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /**
   * 接口/异步操作进行中：前置 spinner、禁用交互并标记 `aria-busy`。
   * 文案切换（如「提交中…」）由调用方通过 children 决定，组件不猜测。
   */
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, disabled, children, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || loading}
        aria-busy={loading ? true : undefined}
        {...props}
      >
        {/* Slot 要求单子节点，asChild 模式下不注入 spinner */}
        {loading && !asChild ? <Spinner className="tw-text-current" /> : null}
        {children}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
