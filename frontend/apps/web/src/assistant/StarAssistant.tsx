import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { Link } from "react-router-dom";
import {
  AssistantDialog,
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
import { useMediaQuery } from "../layout/useMediaQuery";
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

type AssistantMode = "dialog" | "docked";

const DEFAULT_WIDTH_PERCENT = 35;
const MIN_WIDTH_PERCENT = 25;
const MAX_WIDTH_PERCENT = 50;
const DOCK_QUERY = "(min-width: 1920px) and (min-height: 1080px)";
const MODE_STORAGE_KEY = "sa.assistant.mode";

function readStoredMode(): AssistantMode {
  try {
    return window.localStorage.getItem(MODE_STORAGE_KEY) === "docked" ? "docked" : "dialog";
  } catch {
    return "dialog";
  }
}

function persistMode(mode: AssistantMode): void {
  try {
    window.localStorage.setItem(MODE_STORAGE_KEY, mode);
  } catch {
    // 隐私模式忽略即可，不影响当前会话
  }
}

function clampWidth(width: number): number {
  return Math.min(MAX_WIDTH_PERCENT, Math.max(MIN_WIDTH_PERCENT, width));
}

function widthFromClientX(element: HTMLElement, clientX: number): number | null {
  const bounds = element.parentElement?.getBoundingClientRect();
  if (!bounds || bounds.width <= 0) return null;
  return clampWidth(((bounds.right - clientX) / bounds.width) * 100);
}

function AssistantHeader({
  canDock,
  docked,
  onToggleMode,
  onClose,
}: {
  canDock: boolean;
  docked: boolean;
  onToggleMode: () => void;
  onClose: () => void;
}) {
  const modeLabel = docked ? "切换为悬浮窗" : "固定到右侧";
  return (
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
        {canDock ? (
          <button
            type="button"
            className="sa-icon-button sa-assistant-mode-toggle"
            title={modeLabel}
            aria-label={modeLabel}
            aria-pressed={docked}
            onClick={onToggleMode}
          >
            <Icon name="chevron" size={16} />
          </button>
        ) : null}
        <Link
          to="/chat/history"
          className="sa-icon-button"
          title="问答历史"
          aria-label="问答历史"
          onClick={onClose}
        >
          <Icon name="history" size={16} />
        </Link>
        <button
          type="button"
          className="sa-icon-button"
          title="关闭星星助手"
          aria-label="关闭星星助手"
          onClick={onClose}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
    </header>
  );
}

export function StarAssistant() {
  const canDock = useMediaQuery(DOCK_QUERY);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<AssistantMode>(readStoredMode);
  const [widthPercent, setWidthPercent] = useState(DEFAULT_WIDTH_PERCENT);
  const [boot, setBoot] = useState<BootState>({ status: "idle" });
  const dragging = useRef(false);
  const asideRef = useRef<HTMLElement>(null);
  const docked = open && canDock && mode === "docked";

  useEffect(() => {
    if (!canDock && mode === "docked") setMode("dialog");
  }, [canDock, mode]);

  useEffect(() => {
    if (docked) asideRef.current?.focus();
  }, [docked]);

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

  function close(): void {
    setOpen(false);
    setBoot({ status: "idle" });
  }

  function toggleMode(): void {
    const next = mode === "docked" ? "dialog" : "docked";
    setMode(next);
    persistMode(next);
  }

  function resizeFromPointer(event: PointerEvent<HTMLDivElement>): void {
    const nextWidth = widthFromClientX(event.currentTarget, event.clientX);
    if (nextWidth !== null) setWidthPercent(nextWidth);
  }

  function startDragging(event: PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) return;
    event.preventDefault();
    dragging.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeFromPointer(event);
  }

  function moveDragging(event: PointerEvent<HTMLDivElement>): void {
    if (dragging.current) resizeFromPointer(event);
  }

  function stopDragging(event: PointerEvent<HTMLDivElement>): void {
    dragging.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function resizeFromKeyboard(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? 1 : -1;
    const step = event.shiftKey ? 5 : 1;
    setWidthPercent((current) => clampWidth(current + direction * step));
  }

  const content = (
    <>
      <AssistantHeader
        canDock={canDock}
        docked={docked}
        onToggleMode={toggleMode}
        onClose={close}
      />
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
    </>
  );

  const surface = docked ? (
    <>
      <div
        className="sa-assistant-resizer"
        role="separator"
        aria-label="调整星星助手宽度"
        aria-orientation="vertical"
        aria-valuemin={MIN_WIDTH_PERCENT}
        aria-valuemax={MAX_WIDTH_PERCENT}
        aria-valuenow={Math.round(widthPercent)}
        tabIndex={0}
        title="拖拽调整宽度，或使用左右方向键"
        onPointerDown={startDragging}
        onPointerMove={moveDragging}
        onPointerUp={stopDragging}
        onPointerCancel={stopDragging}
        onLostPointerCapture={() => {
          dragging.current = false;
        }}
        onKeyDown={resizeFromKeyboard}
      />
      <aside
        ref={asideRef}
        className="sa-assistant-docked"
        aria-label="星星助手"
        tabIndex={-1}
        style={{ width: `${widthPercent}%` } as CSSProperties}
        onKeyDown={(event) => {
          if (event.key === "Escape") close();
        }}
      >
        {content}
      </aside>
    </>
  ) : (
    <AssistantDialogContent
      className="sa-assistant-panel"
      aria-describedby="sa-assistant-desc"
      onPointerDownOutside={(event) => event.preventDefault()}
      onInteractOutside={(event) => event.preventDefault()}
    >
      {content}
    </AssistantDialogContent>
  );

  return (
    <AssistantDialog
      open={open}
      modal={false}
      onOpenChange={(nextOpen) => {
        if (nextOpen) setOpen(true);
        else close();
      }}
    >
      <AssistantDialogTrigger asChild>
        <button
          type="button"
          className="sa-assistant-fab"
          aria-label="打开星星助手"
          hidden={open}
          onClick={() => setMode(canDock ? readStoredMode() : "dialog")}
        >
          <Icon name="star" className="sa-star" size={22} />
          <span>星星助手</span>
        </button>
      </AssistantDialogTrigger>
      {open ? surface : null}
    </AssistantDialog>
  );
}
