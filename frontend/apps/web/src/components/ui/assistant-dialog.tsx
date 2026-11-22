import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

/**
 * 星星助手专用 Dialog：使用非模态 fixed 悬浮面板，保留 Esc 关闭，
 * 外部页面仍可交互，且点击外部不会关闭助手。
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
