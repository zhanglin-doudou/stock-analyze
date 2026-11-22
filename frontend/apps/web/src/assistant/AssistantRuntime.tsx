import { useMemo, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
  type ThreadMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import remarkGfm from "remark-gfm";
import {
  isApiProblemError,
  type ChatConversationOut,
  type ChatMessageOut,
  type ChatTimelineEventOut,
} from "@sa/api-client";
import { api } from "../client";
import { formatChatTime } from "../lib/datetime";
import { LoadingState } from "../components/ui/spinner";
import { parseSse } from "./sse";
import { ExecutionTimeline } from "./timeline";
import "./assistant.css";

function MarkdownText() {
  return <MarkdownTextPrimitive remarkPlugins={[remarkGfm]} className="sa-markdown" />;
}

function messageText(message: ThreadMessageLike): string {
  const content = message.content;
  if (typeof content === "string") return content;
  return content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("\n");
}

export function toInitialMessages(conversation: ChatConversationOut): ThreadMessageLike[] {
  return [...(conversation.messages ?? [])]
    .sort((left, right) => {
      const byTime = Date.parse(left.created_at) - Date.parse(right.created_at);
      return byTime !== 0 ? byTime : left.message_id.localeCompare(right.message_id);
    })
    .filter((message) => message.content.trim().length > 0)
    .map((message) => ({
      id: message.message_id,
      role: message.role,
      content: [{ type: "text", text: message.content }],
      createdAt: new Date(message.created_at),
      ...(message.role === "assistant"
        ? { status: { type: "complete" as const, reason: "stop" as const } }
        : {}),
    }));
}

function renderAnswer(data: ChatMessageOut): string {
  const citations = data.citations ?? [];
  const numbers = new Map(citations.map((item) => [item.citation_id, item.number]));
  let text = data.answer.replace(/\[\[([^\]]+)\]\]/g, (_, id: string) => {
    const number = numbers.get(id);
    return number === undefined ? `[${id}]` : `[${number}]`;
  });
  if (!text.trim()) text = "星星助手暂时没有拿到可回答的数据。";

  if (data.status !== "ok" || data.fallback) {
    const reason =
      data.degrade_message ||
      data.degrade_reason ||
      (data.status === "needs_clarification" ? "需要补充一点信息" : "本次回答已降级");
    const action = data.what_to_do ? `\n>\n> ${data.what_to_do}` : "";
    text = `> ${reason}${action}\n\n${text}`;
  }

  if (citations.length > 0) {
    const sources = citations
      .map(
        (item) =>
          `- [${item.number}] ${item.label}：${item.value}（${item.source_endpoint}${
            item.data_as_of ? `，截至 ${item.data_as_of}` : ""
          }）`,
      )
      .join("\n");
    text = `${text}\n\n**数据来源**\n${sources}`;
  }
  return text;
}

export function createChatAdapter(sessionId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const lastUser = [...messages].reverse().find((message) => message.role === "user");
      const prompt = lastUser ? messageText(lastUser).trim() : "";
      if (!prompt) {
        yield { content: [{ type: "text" as const, text: "请先输入一个问题。" }] };
        return;
      }
      let timeline: ChatTimelineEventOut[] = [];
      const update = (text: string) => ({
        // step 可能先于首个 delta 到达；用零宽占位确保 assistant-ui 先建立消息气泡。
        content: [{ type: "text" as const, text: text || "\u200b" }],
        metadata: { custom: { timeline } },
      });
      try {
        const response = await api.chat.streamMessage(
          sessionId,
          { message: prompt },
          { signal: abortSignal },
        );
        const contentType = response.headers.get("content-type") ?? "";
        if (!contentType.includes("text/event-stream")) {
          const data = (await response.json()) as ChatMessageOut;
          timeline = data.timeline ?? [];
          yield update(renderAnswer(data));
          return;
        }

        let text = "";
        for await (const event of parseSse(response)) {
          if (event.event === "step") {
            const next = event.data as ChatTimelineEventOut;
            timeline = [...timeline.filter((item) => item.seq !== next.seq), next].sort(
              (left, right) => left.seq - right.seq,
            );
            yield update(text);
          } else if (event.event === "delta") {
            const delta = (event.data as { text?: string }).text ?? "";
            if (!delta) continue;
            text += delta;
            yield update(text);
          } else if (event.event === "done") {
            const message = (event.data as { message?: ChatMessageOut }).message;
            if (message) {
              timeline = message.timeline ?? timeline;
              yield update(renderAnswer(message));
            }
            return;
          } else if (event.event === "error") {
            const problem = event.data as { user_message?: string; what_to_do?: string };
            throw new Error(
              `${problem.user_message ?? "模型服务暂不可用"}${problem.what_to_do ? ` ${problem.what_to_do}` : ""}`,
            );
          }
        }
      } catch (err) {
        const message = isApiProblemError(err)
          ? `${err.user_message}${err.what_to_do ? ` ${err.what_to_do}` : ""}`
          : err instanceof Error
            ? err.message
            : "星星助手暂时不可用，请稍后重试。";
        yield update(`> ⚠ ${message}`);
      }
    },
  };
}

function toThreadMessageLike(message: ThreadMessage): ThreadMessageLike {
  return {
    id: message.id,
    role: message.role,
    content: message.content as ThreadMessageLike["content"],
    createdAt: message.createdAt,
    status: message.status,
  };
}

function LoadEarlier({ initialCursor }: { initialCursor: string | null }) {
  const aui = useAui();
  const current = useAuiState((state) => state.thread.messages);
  const [cursor, setCursor] = useState(initialCursor);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!cursor) return null;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.chat.getConversation({ limit: 8, before_id: cursor ?? undefined });
      const older = toInitialMessages(data);
      aui.thread.reset([...older, ...current.map(toThreadMessageLike)]);
      setCursor(data.next_cursor ?? null);
    } catch {
      setError("加载失败，请重试。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sa-load-earlier">
      {error ? (
        <span className="sa-load-earlier-error" role="alert">
          {error}
        </span>
      ) : null}
      {loading ? <LoadingState /> :
      <button
        type="button"
        className="sa-load-earlier-button"
        onClick={() => void load()}
        disabled={loading}
        aria-busy={loading}
      >
        加载更多
      </button>}
    </div>
  );
}

function MessageTimestamp() {
  const createdAt = useAuiState((state) => state.message.createdAt);
  return <time className="sa-message-time">{formatChatTime(createdAt)}</time>;
}

function ChatMessage() {
  return (
    <MessagePrimitive.Root>
      <MessagePrimitive.If user>
        <div className="sa-message" data-role="user">
          <div className="sa-message-stack">
            <div className="sa-message-bubble">
              <MessagePrimitive.Parts />
            </div>
            <MessageTimestamp />
          </div>
        </div>
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <div className="sa-message" data-role="assistant">
          <div className="sa-message-stack">
            <div className="sa-message-bubble">
              <ExecutionTimeline />
              <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
            </div>
            <MessageTimestamp />
          </div>
        </div>
      </MessagePrimitive.If>
    </MessagePrimitive.Root>
  );
}

export function AssistantThread({ initialCursor }: { initialCursor: string | null }) {
  return (
    <ThreadPrimitive.Root className="sa-assistant-thread">
      <ThreadPrimitive.Viewport className="sa-assistant-viewport" autoScroll>
        <LoadEarlier initialCursor={initialCursor} />
        <ThreadPrimitive.Empty>
          <div className="sa-assistant-empty">
            <div>
              <strong>星星助手</strong>
              <br />
              基于你的账单、回合、指标和归因回答；每个数字都能在“数据来源”里找到出处。
            </div>
            <div className="sa-suggestions">
              <ThreadPrimitive.Suggestion prompt="本月赚了多少？" send className="sa-suggestion">
                本月赚了多少？
              </ThreadPrimitive.Suggestion>
              <ThreadPrimitive.Suggestion prompt="这个月为什么亏？" send className="sa-suggestion">
                为什么亏？
              </ThreadPrimitive.Suggestion>
              <ThreadPrimitive.Suggestion prompt="我的胜率和最大回撤怎么样？" send className="sa-suggestion">
                胜率与回撤
              </ThreadPrimitive.Suggestion>
              <ThreadPrimitive.Suggestion prompt="我的持仓集中在哪些行业？" send className="sa-suggestion">
                持仓集中度
              </ThreadPrimitive.Suggestion>
              <ThreadPrimitive.Suggestion prompt="数据可信吗？" send className="sa-suggestion">
                数据可信吗？
              </ThreadPrimitive.Suggestion>
            </div>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>{() => <ChatMessage />}</ThreadPrimitive.Messages>
        <ThreadPrimitive.If running>
          <LoadingState label="星星正在看数据…" className="sa-thinking" />
        </ThreadPrimitive.If>
      </ThreadPrimitive.Viewport>
      <ComposerPrimitive.Root className="sa-assistant-composer">
        <ComposerPrimitive.Input
          className="sa-assistant-input"
          placeholder="问点什么，比如：这个月为什么亏？"
          rows={1}
          aria-label="向星星助手提问"
        />
        <ComposerPrimitive.Send className="sa-send">发送</ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}

export function AssistantSessionProvider({
  sessionId,
  initialMessages,
  children,
}: {
  sessionId: string;
  initialMessages: ThreadMessageLike[];
  children: ReactNode;
}) {
  const adapter = useMemo(() => createChatAdapter(sessionId), [sessionId]);
  const runtime = useLocalRuntime(adapter, { initialMessages });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
