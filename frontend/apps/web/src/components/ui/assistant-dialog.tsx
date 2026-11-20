import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

/**
 * 星星助手专用 Dialog：保留 Radix 的焦点陷阱/Esc 关闭，
 * 但使用 fixed 悬浮面板而不是页面中央的遮罩模态框。
 */
const AssistantDialog = DialogPrimitive.Root;
const AssistantDialogTrigger = DialogPrimitive.Trigger;
const AssistantDialogClose = DialogPrimitive.Close;

const AssistantDialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Content ref={ref} className={cn(className)} {...props}>
      {children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
AssistantDialogContent.displayName = DialogPrimitive.Content.displayName;

const AssistantDialogTitle = DialogPrimitive.Title;
const AssistantDialogDescription = DialogPrimitive.Description;

export {
  AssistantDialog,
  AssistantDialogTrigger,
  AssistantDialogClose,
  AssistantDialogContent,
  AssistantDialogTitle,
  AssistantDialogDescription,
};
