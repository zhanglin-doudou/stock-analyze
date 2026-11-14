import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { cn } from "@/lib/utils";
import { usePortalContainer } from "./portal";

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Trigger
    ref={ref}
    className={cn(
      "tw-flex tw-h-[var(--control-h)] tw-w-full tw-items-center tw-justify-between tw-gap-2 tw-rounded-sm tw-border tw-border-input tw-bg-surface tw-px-2 tw-text-sm focus-visible:tw-outline-2 focus-visible:tw-outline-offset-2 focus-visible:tw-outline-ring disabled:tw-cursor-not-allowed disabled:tw-opacity-50",
      className,
    )}
    {...props}
  >
    {children}
    <SelectPrimitive.Icon className="tw-text-muted-foreground">▾</SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = SelectPrimitive.Trigger.displayName;

const SelectContent = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = "popper", ...props }, ref) => {
  const container = usePortalContainer();
  return (
    <SelectPrimitive.Portal container={container}>
      <SelectPrimitive.Content
        ref={ref}
        position={position}
        sideOffset={4}
        className={cn(
          "tw-relative tw-z-50 tw-max-h-72 tw-min-w-[8rem] tw-overflow-hidden tw-rounded-md tw-border tw-border-border tw-bg-popover tw-text-popover-foreground tw-shadow-2",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport className="tw-p-1">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
});
SelectContent.displayName = SelectPrimitive.Content.displayName;

const SelectLabel = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Label>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Label>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.Label
    ref={ref}
    className={cn("tw-px-2 tw-py-1.5 tw-text-xs tw-text-muted-foreground", className)}
    {...props}
  />
));
SelectLabel.displayName = SelectPrimitive.Label.displayName;

const SelectItem = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Item
    ref={ref}
    className={cn(
      "tw-relative tw-flex tw-w-full tw-cursor-pointer tw-select-none tw-items-center tw-rounded-sm tw-py-1.5 tw-pl-6 tw-pr-2 tw-text-sm tw-outline-none focus:tw-bg-accent data-[disabled]:tw-pointer-events-none data-[disabled]:tw-opacity-50",
      className,
    )}
    {...props}
  >
    <span className="tw-absolute tw-left-2 tw-inline-flex tw-h-3.5 tw-w-3.5 tw-items-center tw-justify-center">
      <SelectPrimitive.ItemIndicator>✓</SelectPrimitive.ItemIndicator>
    </span>
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
  </SelectPrimitive.Item>
));
SelectItem.displayName = SelectPrimitive.Item.displayName;

export { Select, SelectGroup, SelectValue, SelectTrigger, SelectContent, SelectLabel, SelectItem };
