import { useMemo, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import remarkGfm from "remark-gfm";
import { isApiProblemError, type ChatConversationOut, type ChatMessageOut } from "@sa/api-client";
import { api } from "../client";
import { parseSse } from "./sse";
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
  return (conversation.messages ?? [])
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
      try {
        const response = await api.chat.streamMessage(
          sessionId,
          { message: prompt },
          { signal: abortSignal },
        );
        const contentType = response.headers.get("content-type") ?? "";
        if (!contentType.includes("text/event-stream")) {
          const data = (await response.json()) as ChatMessageOut;
          yield { content: [{ type: "text" as const, text: renderAnswer(data) }] };
          return;
        }

        let text = "";
        for await (const event of parseSse(response)) {
          if (event.event === "delta") {
            const delta = (event.data as { text?: string }).text ?? "";
            if (!delta) continue;
            text += delta;
            yield { content: [{ type: "text" as const, text }] };
          } else if (event.event === "done") {
            const message = (event.data as { message?: ChatMessageOut }).message;
            if (message) {
              yield { content: [{ type: "text" as const, text: renderAnswer(message) }] };
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
        yield { content: [{ type: "text" as const, text: `> ⚠ ${message}` }] };
      }
    },
  };
}

function ChatMessage() {
  return (
    <MessagePrimitive.Root>
      <MessagePrimitive.If user>
        <div className="sa-message" data-role="user">
          <div className="sa-message-bubble">
            <MessagePrimitive.Parts />
          </div>
        </div>
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <div className="sa-message" data-role="assistant">
          <div className="sa-message-bubble">
            <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
          </div>
        </div>
      </MessagePrimitive.If>
    </MessagePrimitive.Root>
  );
}

export function AssistantThread() {
  return (
    <ThreadPrimitive.Root className="sa-assistant-thread">
      <ThreadPrimitive.Viewport className="sa-assistant-viewport" autoScroll>
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
          <div className="sa-message-meta">星星正在看数据…</div>
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
