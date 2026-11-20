import * as React from "react";
import { Spinner as RadixSpinner } from "@radix-ui/themes/components/spinner";
import { cn } from "@/lib/utils";
import "./spinner.css";

export interface SpinnerProps extends React.ComponentPropsWithoutRef<typeof RadixSpinner> {
  /** 仅供屏幕阅读器；可见加载文案由 LoadingState 负责。 */
  label?: string;
}

/** Radix Themes Spinner 的项目封装：统一 tokens 化颜色/尺寸与 a11y。 */
export const Spinner = React.forwardRef<HTMLSpanElement, SpinnerProps>(
  ({ className, label, ...props }, ref) => (
    <span role="status" aria-live="polite" aria-busy="true" className="tw-inline-flex">
      <RadixSpinner ref={ref} className={cn("tw-shrink-0 tw-text-primary", className)} {...props} />
      {label && <span className="tw-sr-only">{label}</span>}
    </span>
  ),
);
Spinner.displayName = "Spinner";

export function LoadingState({
  label,
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <span
      className={cn("tw-inline-flex tw-items-center tw-gap-2 tw-text-small tw-text-muted", className)}
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
    >
      <Spinner label={label} />
      <span>{label}</span>
    </span>
  );
}
