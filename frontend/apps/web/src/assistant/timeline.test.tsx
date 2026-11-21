import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ChatTimelineEventOut } from "@sa/api-client";
import { TimelineList } from "./timeline";

function event(
  seq: number,
  kind: string,
  payload: Record<string, unknown> = {},
  callId?: string,
): ChatTimelineEventOut {
  return {
    seq,
    kind,
    phase: "execute",
    call_id: callId,
    at: "2026-11-21T10:00:00Z",
    payload,
  };
}

describe("TimelineList", () => {
  it("按事件渲染内部/外部工具与下一步模型思考", () => {
    render(
      <TimelineList
        events={[
          event(1, "tool.call.started", { title: "流水查询", source: "builtin" }, "call_1"),
          event(2, "tool.call.succeeded", { title: "流水查询", row_count: 2 }, "call_1"),
          event(3, "model.thinking.started", { purpose: "plan" }),
          event(4, "model.thinking.completed", { purpose: "plan", status: "ok" }),
          event(5, "tool.call.started", { title: "实时行情", source: "mcp" }, "call_2"),
        ]}
      />,
    );
    expect(screen.getByText("正在调用内部工具：流水查询")).toBeTruthy();
    expect(screen.getByText("流水查询调用成功（2 行）")).toBeTruthy();
    expect(screen.getByText("下一步 · 模型正在思考")).toBeTruthy();
    expect(screen.getByText("下一步 · 正在调用外部工具：实时行情")).toBeTruthy();
  });

  it("失败事件带错误文案", () => {
    render(
      <TimelineList
        events={[
          event(1, "tool.call.failed", {
            title: "实时行情",
            source: "mcp",
            error_message: "认证失败",
          }),
        ]}
      />,
    );
    expect(screen.getByText("实时行情调用失败：认证失败")).toBeTruthy();
  });
});
