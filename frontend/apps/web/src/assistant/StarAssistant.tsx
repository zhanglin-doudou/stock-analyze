import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AssistantDialog,
  AssistantDialogClose,
  AssistantDialogContent,
  AssistantDialogDescription,
  AssistantDialogTitle,
  AssistantDialogTrigger,
} from "@/components/ui/assistant-dialog";
import { isApiProblemError } from "@sa/api-client";
import type { ThreadMessageLike } from "@assistant-ui/react";
import { api } from "../client";
import { Icon } from "../ui/icons";
import { AssistantSessionProvider, AssistantThread, toInitialMessages } from "./AssistantRuntime";
import { LoadingState } from "../components/ui/spinner";
import "./assistant.css";

type BootState =
  | { status: "idle" | "loading" }
  | {
      status: "ready";
      sessionId: string;
      initialMessages: ThreadMessageLike[];
      nextCursor: string | null;
    }
  | { status: "error"; message: string };

export function StarAssistant() {
  const [open, setOpen] = useState(false);
  const [boot, setBoot] = useState<BootState>({ status: "idle" });

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setBoot({ status: "loading" });
    void (async () => {
      try {
        const session = await api.chat.ensureSession();
        const conversation = await api.chat.getConversation({ limit: 8 });
        if (!cancelled) {
          setBoot({
            status: "ready",
            sessionId: session.conversation_id,
            initialMessages: toInitialMessages(conversation),
            nextCursor: conversation.next_cursor ?? null,
          });
        }
      } catch (err) {
        if (!cancelled) {
          setBoot({
            status: "error",
            message: isApiProblemError(err)
              ? `${err.user_message}${err.what_to_do ? ` ${err.what_to_do}` : ""}`
              : "星星助手暂时连不上，请稍后重试。",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  return (
    <AssistantDialog open={open} onOpenChange={setOpen}>
      <AssistantDialogTrigger asChild>
        <button type="button" className="sa-assistant-fab" aria-label="打开星星助手">
          <Icon name="star" className="sa-star" size={22} />
          <span>星星助手</span>
        </button>
      </AssistantDialogTrigger>
      <AssistantDialogContent className="sa-assistant-panel" aria-describedby="sa-assistant-desc">
          <header className="sa-assistant-header">
            <div>
              <AssistantDialogTitle className="sa-assistant-title">
                <Icon name="star" size={20} />
                <span>星星助手</span>
              </AssistantDialogTitle>
              <AssistantDialogDescription id="sa-assistant-desc" className="sa-assistant-subtitle">
                基于你的账单、回合、指标与归因回答
              </AssistantDialogDescription>
            </div>
            <div className="sa-assistant-header-actions">
              <Link
                to="/chat/history"
                className="sa-icon-button"
                title="问答历史"
                aria-label="问答历史"
                onClick={() => setOpen(false)}
              >
                <Icon name="history" size={16} />
              </Link>
              <AssistantDialogClose asChild>
                <button type="button" className="sa-icon-button" aria-label="关闭星星助手">
                  <span aria-hidden="true">×</span>
                </button>
              </AssistantDialogClose>
            </div>
          </header>

          {boot.status === "ready" ? (
            <AssistantSessionProvider sessionId={boot.sessionId} initialMessages={boot.initialMessages}>
              <AssistantThread initialCursor={boot.nextCursor} />
            </AssistantSessionProvider>
          ) : boot.status === "error" ? (
            <div className="sa-assistant-error" role="alert">
              {boot.message}
            </div>
          ) : (
            <div className="sa-assistant-empty">
              <LoadingState label="正在准备你的对话…" />
            </div>
          )}
      </AssistantDialogContent>
    </AssistantDialog>
  );
}
