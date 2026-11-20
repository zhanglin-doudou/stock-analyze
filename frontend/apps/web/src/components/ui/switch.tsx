import * as React from "react";
import * as SwitchPrimitives from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";
import { disabledState, focusRing } from "./styles";

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitives.Root
    className={cn(
      `tw-inline-flex tw-h-5 tw-w-9 tw-shrink-0 tw-cursor-pointer tw-items-center tw-rounded-full tw-border tw-border-input tw-bg-surface-2 tw-transition-colors tw-duration-ui data-[state=checked]:tw-bg-primary data-[state=checked]:tw-border-primary ${focusRing} ${disabledState}`,
      className,
    )}
    {...props}
    ref={ref}
  >
    <SwitchPrimitives.Thumb
      className={cn(
        "tw-pointer-events-none tw-block tw-h-4 tw-w-4 tw-rounded-full tw-bg-primary-foreground tw-shadow-1 data-[state=unchecked]:tw-bg-foreground tw-transition-transform tw-duration-ui data-[state=checked]:tw-translate-x-4",
      )}
    />
  </SwitchPrimitives.Root>
));
Switch.displayName = SwitchPrimitives.Root.displayName;

export { Switch };
