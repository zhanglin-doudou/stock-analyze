import { useEffect, useState } from "react";
import { useAuiState } from "@assistant-ui/react";
import type { ChatTimelineEventOut } from "@sa/api-client";

type TimelinePayload = {
  title?: string;
  tool?: string;
  source?: string;
  status?: string;
  row_count?: number;
  latency_ms?: number;
  total_ms?: number;
  error_message?: string;
  message?: string;
  period?: string | null;
  calls?: unknown[];
};

function payloadOf(event: ChatTimelineEventOut): TimelinePayload {
  return (event.payload ?? {}) as TimelinePayload;
}

const TOOL_TITLES: Record<string, string> = {
  assets_query: "资产表",
  trades_query: "流水查询",
  rounds_query: "回合列表",
  round_detail: "回合详情",
  portfolio_snapshot: "资产概览",
  performance_metrics: "绩效指标",
  attribution_summary: "五因子归因",
  nav_summary: "净值摘要",
  sector_exposure: "板块暴露",
  data_quality: "数据质量",
  metric_explain: "指标口径",
};

function displayTitle(payload: TimelinePayload): string {
  const title = payload.title || payload.tool || "工具";
  return TOOL_TITLES[title] ?? title;
}

function sourceLabel(source: string | undefined): string {
  return source === "mcp" ? "外部工具" : "内部工具";
}

function eventLabel(event: ChatTimelineEventOut, previous: ChatTimelineEventOut | undefined): string {
  const payload = payloadOf(event);
  const title = displayTitle(payload);
  switch (event.kind) {
    case "understanding.started":
      return "正在理解用户意图";
    case "understanding.completed":
      return payload.period ? `已理解：${payload.period}` : "已理解用户意图";
    case "plan.started":
      return "正在规划查询";
    case "plan.created":
      return `计划调用 ${payload.calls?.length ?? 0} 个工具`;
    case "model.thinking.started":
      return previous?.kind === "tool.call.succeeded" || previous?.kind === "tool.call.failed"
        ? "下一步 · 模型正在思考"
        : "模型正在思考";
    case "model.thinking.completed":
      return payload.status === "error" ? "模型思考失败" : "模型思考完成";
    case "tool.call.started":
      return `${previous?.kind === "model.thinking.completed" ? "下一步 · " : ""}正在调用${sourceLabel(payload.source)}：${title}`;
    case "tool.call.succeeded":
      return `${title}调用成功${payload.row_count ? `（${payload.row_count} 行）` : ""}`;
    case "tool.call.failed":
      return `${title}调用失败：${payload.error_message || payload.message || "未知错误"}`;
    case "tool.call.skipped":
      return `跳过${title}`;
    case "tool.circuit_open":
      return `${title}暂时熔断`;
    case "answer.started":
      return "正在整理回答";
    case "validation.started":
      return "正在校验数字与引用";
    case "validation.failed":
      return "校验未通过，正在修正";
    case "fallback.triggered":
      return "已降级为结构化摘要";
    case "turn.completed":
      return "回答完成";
    case "turn.failed":
      return `处理失败${payload.message ? `：${payload.message}` : ""}`;
    default:
      return event.kind;
  }
}

function statusOf(event: ChatTimelineEventOut, isLast: boolean): "ok" | "warn" | "error" | "run" {
  if (event.kind === "tool.call.failed" || event.kind === "turn.failed") return "error";
  if (event.kind === "validation.failed" || event.kind === "fallback.triggered") return "warn";
  if (isLast && event.kind !== "turn.completed" && event.kind !== "turn.failed") return "run";
  return "ok";
}

function summaryOf(events: ChatTimelineEventOut[]): string {
  const tools = new Set<string>();
  let totalMs = 0;
  let steps = 0;
  for (const event of events) {
    const payload = payloadOf(event);
    if (event.kind === "tool.call.started" && event.call_id) tools.add(event.call_id);
    if (event.kind === "plan.created" && payload.calls) steps = Math.max(steps, 1);
    if (event.kind === "turn.completed" && payload.total_ms) totalMs = payload.total_ms;
  }
  const parts = [`${steps || 1} 步`, `${tools.size} 个工具`];
  if (totalMs > 0) {
    parts.push(
      totalMs >= 1000
        ? `${Math.floor(totalMs / 1000)}.${Math.floor((totalMs % 1000) / 100)}s`
        : `${totalMs}ms`,
    );
  }
  return parts.join(" · ");
}

export function TimelineList({ events }: { events: ChatTimelineEventOut[] }) {
  return (
    <ol className="sa-timeline-list" aria-label="执行过程" aria-live="polite">
      {events.map((event, index) => {
        const status = statusOf(event, index === events.length - 1);
        return (
          <li key={`${event.seq}-${event.kind}`} className="sa-timeline-item" data-status={status}>
            <span className="sa-timeline-mark" aria-hidden="true">
              {status === "error" ? "!" : status === "warn" ? "…" : status === "run" ? "•" : "✓"}
            </span>
            <span className="sa-timeline-label">{eventLabel(event, events[index - 1])}</span>
            {event.kind === "tool.call.succeeded" && payloadOf(event).latency_ms ? (
              <span className="sa-timeline-meta">{payloadOf(event).latency_ms}ms</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export function ExecutionTimeline() {
  const timeline = useAuiState(
    (state) =>
      (state.message.metadata?.custom as { timeline?: ChatTimelineEventOut[] } | undefined)
        ?.timeline,
  );
  const running = useAuiState((state) => state.message.status?.type === "running");
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (running) setOpen(true);
  }, [running]);

  if (!timeline?.length) return null;
  const summary = summaryOf(timeline);
  return (
    <details
      className="sa-timeline"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="sa-timeline-summary">
        {running ? "正在执行" : "执行过程"} · {summary}
      </summary>
      <TimelineList events={timeline} />
    </details>
  );
}
