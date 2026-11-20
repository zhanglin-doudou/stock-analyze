import { useEffect, useMemo, useState } from "react";
import type {
  ChatConversationOut,
  ChatReplayOut,
  ChatTraceOut,
  ChatHistoryMessageOut,
} from "@sa/api-client";
import { api } from "../client";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { formatChatTime } from "../lib/datetime";
import { LoadingState } from "../components/ui/spinner";
import { ProblemView } from "../ui/Problem";
import "./chat-history.css";

type TurnRow = {
  turnId: string;
  question: string;
  answer: string;
  status: string;
  createdAt: string;
};

function buildTurns(conversation: ChatConversationOut): TurnRow[] {
  const byTurn = new Map<string, TurnRow>();
  for (const message of conversation.messages as ChatHistoryMessageOut[]) {
    if (!message.turn_id) continue;
    const current = byTurn.get(message.turn_id) ?? {
      turnId: message.turn_id,
      question: "",
      answer: "",
      status: message.status,
      createdAt: message.created_at,
    };
    if (message.role === "user") {
      current.question = message.content;
      current.createdAt = message.created_at;
    } else {
      current.answer = message.content;
      current.status = message.status;
    }
    byTurn.set(message.turn_id, current);
  }
  return [...byTurn.values()].sort(
    (left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt),
  );
}

function statusClass(status: string): string {
  if (status === "ok") return "is-ok";
  if (status === "blocked") return "is-blocked";
  return "is-degraded";
}

function statusLabel(status: string): string {
  if (status === "ok") return "正常";
  if (status === "blocked") return "数据阻断";
  if (status === "needs_clarification") return "待澄清";
  return "已降级";
}

function answerPreview(answer: string): string {
  const text = answer.replace(/\s+/g, " ").trim();
  return text.length > 72 ? `${text.slice(0, 72)}…` : text || "（无回答）";
}

function TraceSection({ trace }: { trace: ChatTraceOut }) {
  const turn = trace.turn as Record<string, unknown>;
  const understanding = turn.understanding;
  const toolCalls = trace.tool_calls ?? [];
  const llmCalls = trace.llm_calls ?? [];
  return (
    <>
      <div className="chat-detail-section">
        <h3>理解与指代</h3>
        <div className="chat-trace-row">
          <span className="chat-trace-key">改写问题</span>
          <span className="chat-trace-value">{String(turn.resolved_query ?? "—")}</span>
        </div>
        <div className="chat-trace-row">
          <span className="chat-trace-key">状态</span>
          <span className="chat-trace-value">{String(turn.status ?? "—")}</span>
        </div>
        <details>
          <summary className="chat-trace-key">understanding JSON</summary>
          <pre className="chat-detail-pre">{JSON.stringify(understanding ?? {}, null, 2)}</pre>
        </details>
      </div>
      <div className="chat-detail-section">
        <h3>工具调用（{toolCalls.length}）</h3>
        {toolCalls.length === 0 ? <p className="muted">无工具调用</p> : null}
        {toolCalls.map((call) => (
          <details key={String(call.id ?? call.seq)}>
            <summary>
              {String(call.tool)} · {String(call.status)} · {String(call.row_count ?? 0)} 行 ·{" "}
              {String(call.latency_ms ?? 0)}ms
            </summary>
            <pre className="chat-detail-pre">{JSON.stringify({ args: call.args, result: call.result }, null, 2)}</pre>
          </details>
        ))}
      </div>
      <div className="chat-detail-section">
        <h3>模型调用（{llmCalls.length}）</h3>
        {llmCalls.length === 0 ? <p className="muted">无模型调用（模板回答）</p> : null}
        {llmCalls.map((call) => (
          <details key={String(call.id ?? call.seq)}>
            <summary>
              {String(call.purpose)} · {String(call.model)} · {String(call.status)} ·{" "}
              {String(call.total_tokens ?? "—")} tokens
            </summary>
            <pre className="chat-detail-pre">{JSON.stringify(call, null, 2)}</pre>
          </details>
        ))}
      </div>
    </>
  );
}

export function ChatHistory() {
  const [conversation, setConversation] = useState<ChatConversationOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [selectedTurn, setSelectedTurn] = useState<string | null>(null);
  const [trace, setTrace] = useState<ChatTraceOut | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<unknown>(null);
  const [replay, setReplay] = useState<ChatReplayOut | null>(null);
  const [replayError, setReplayError] = useState<unknown>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");

  const turns = useMemo(() => (conversation ? buildTurns(conversation) : []), [conversation]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        await api.chat.ensureSession();
        const data = await api.chat.getConversation({ limit: 100 });
        if (cancelled) return;
        setConversation(data);
        setSelectedTurn((current) => current ?? buildTurns(data)[0]?.turnId ?? null);
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedTurn) {
      setTrace(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      setTraceLoading(true);
      setTraceError(null);
      setReplay(null);
      setReplayError(null);
      try {
        const data = await api.chat.getTrace(selectedTurn);
        if (!cancelled) setTrace(data);
      } catch (err) {
        if (!cancelled) setTraceError(err);
      } finally {
        if (!cancelled) setTraceLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedTurn]);

  async function clearHistory() {
    setClearing(true);
    setClearError(null);
    try {
      await api.chat.deleteConversation();
      setConversation(null);
      setSelectedTurn(null);
      setTrace(null);
      setReplay(null);
      setNotice("问答历史已清空。");
      setClearOpen(false);
    } catch (err) {
      setClearError(err);
    } finally {
      setClearing(false);
    }
  }

  async function runReplay() {
    if (!selectedTurn) return;
    setReplayLoading(true);
    setReplayError(null);
    try {
      setReplay(await api.chat.replay(selectedTurn, "deterministic"));
    } catch (err) {
      setReplayError(err);
    } finally {
      setReplayLoading(false);
    }
  }

  const selected = turns.find((turn) => turn.turnId === selectedTurn) ?? null;

  return (
    <div className="chat-history" data-testid="chat-history-page">
      <header className="chat-history-head">
        <div>
          <h1 className="chat-history-title">问答历史</h1>
          <p className="chat-history-sub">
            每次提问都会保存理解、工具调用、模型调用和最终回答；可以在这里逐轮回看并做确定性回放。
          </p>
        </div>
        <div className="chat-history-actions">
          <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
            <AlertDialogTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="tw-border-danger tw-text-danger hover:tw-bg-danger-soft"
                disabled={clearing || turns.length === 0}
              >
                清空问答历史
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认清空问答历史？</AlertDialogTitle>
                <AlertDialogDescription>
                  清空后当前会话与历史轮次将不再显示，且无法恢复。请确认是否继续。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  className="tw-bg-destructive tw-text-destructive-foreground hover:tw-opacity-90"
                  disabled={clearing}
                  onClick={(event) => {
                    event.preventDefault();
                    void clearHistory();
                  }}
                >
                  {clearing ? "清空中…" : "确认清空"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </header>

      {notice ? (
        <p className="chat-history-notice" role="status">
          {notice}
        </p>
      ) : null}
      <ProblemView error={clearError} />

      {loading ? <LoadingState label="加载中…" /> : null}
      <ProblemView error={error} />
      {!loading && !error && turns.length === 0 ? (
        <div className="chat-history-card chat-history-empty">
          还没有问答记录。点击右下角的「星星助手」开始第一次提问。
        </div>
      ) : null}

      {turns.length > 0 ? (
        <div className="chat-history-grid">
          <aside className="chat-history-card chat-history-list" aria-label="问答轮次">
            <div className="chat-history-list-head">共 {turns.length} 轮 · 最新在前</div>
            {turns.map((turn) => (
              <button
                key={turn.turnId}
                type="button"
                className={`chat-turn-button${turn.turnId === selectedTurn ? " is-active" : ""}`}
                onClick={() => setSelectedTurn(turn.turnId)}
              >
                <div className="chat-turn-question">{turn.question || "（无问题）"}</div>
                <div className="chat-turn-preview">{answerPreview(turn.answer)}</div>
                <div className="chat-turn-meta">
                  <span className={`chat-history-status ${statusClass(turn.status)}`}>
                    {statusLabel(turn.status)}
                  </span>
                  <span>{formatChatTime(turn.createdAt)}</span>
                </div>
              </button>
            ))}
          </aside>

          <article className="chat-history-card chat-history-detail">
            {selected ? (
              <>
                <h2 className="chat-detail-question">{selected.question || "（无问题）"}</h2>
                <div className="chat-detail-actions">
                  <span className={`chat-history-status ${statusClass(selected.status)}`}>
                    {statusLabel(selected.status)}
                  </span>
                  <button
                    type="button"
                    className="btn"
                    disabled={replayLoading}
                    onClick={() => void runReplay()}
                  >
                    {replayLoading ? <LoadingState label="回放中…" /> : "回放（确定性）"}
                  </button>
                </div>
                <div className="chat-detail-answer">{selected.answer || "（无回答）"}</div>
                <ProblemView error={replayError} />
                {replay ? (
                  replay.match ? (
                    <p className="chat-replay-ok">回放结果与原始回答一致。</p>
                  ) : (
                    <div className="chat-replay-diff">
                      <strong>回放存在差异</strong>
                      <pre className="chat-detail-pre">{JSON.stringify(replay.diffs, null, 2)}</pre>
                    </div>
                  )
                ) : null}

                <div className="chat-detail-section">
                  <h3>回放轨迹</h3>
                  {traceLoading ? <LoadingState label="加载轨迹中…" /> : null}
                  <ProblemView error={traceError} />
                  {trace ? <TraceSection trace={trace} /> : null}
                </div>
              </>
            ) : (
              <p className="muted">从左侧选择一轮问答。</p>
            )}
          </article>
        </div>
      ) : null}
    </div>
  );
}
