import * as React from "react";
import { cn } from "@/lib/utils";
import { disabledState, focusRing } from "./styles";

/** 文本输入。Radix 没有 input primitive，这里按同一套 tokens 封装原生控件。 */
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        `tw-flex tw-h-control tw-w-full tw-rounded-sm tw-border tw-border-input tw-bg-surface tw-px-3 tw-py-1 tw-text-body tw-text-foreground placeholder:tw-text-faint transition-colors tw-duration-ui ${focusRing} ${disabledState}`,
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      `tw-flex tw-min-h-20 tw-w-full tw-rounded-sm tw-border tw-border-input tw-bg-surface tw-px-3 tw-py-2 tw-text-body tw-text-foreground placeholder:tw-text-faint transition-colors tw-duration-ui ${focusRing} ${disabledState}`,
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export { Input, Textarea };
