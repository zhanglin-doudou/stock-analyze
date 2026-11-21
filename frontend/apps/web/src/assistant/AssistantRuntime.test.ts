import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  streamMessage: vi.fn(),
}));

vi.mock("../client", () => ({
  api: { chat: { streamMessage: mocks.streamMessage } },
}));

import { createChatAdapter } from "./AssistantRuntime";

function sseResponse(parts: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const part of parts) controller.enqueue(encoder.encode(part));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

const message = {
  conversation_id: "cnv_1",
  turn_id: "trn_1",
  message_id: "msg_1",
  answer: "最终回答[[1]]",
  answer_format: "markdown",
  citations: [
    {
      number: 1,
      citation_id: "c1_1",
      label: "本月收益",
      value: "-1234.56",
      source_endpoint: "GET /api/v1/dashboard",
      data_as_of: "2026-11-19",
    },
  ],
  claims: [],
  status: "ok",
  degrade_reason: null,
  degrade_message: null,
  what_to_do: null,
  retryable: false,
  llm_used: true,
  fallback: false,
  understanding: {},
  context_usage: null,
  timings: {},
};

describe("createChatAdapter SSE", () => {
  it("逐块 yield，done 后补充引用并替换为最终回答", async () => {
    mocks.streamMessage.mockResolvedValue(
      sseResponse([
        'event: meta\ndata: {"turn_id":"trn_1"}\n\n',
        'event: delta\ndata: {"text":"最终"}\n\n',
        'event: delta\ndata: {"text":"回答"}\n\n',
        `event: done\ndata: ${JSON.stringify({ message })}\n\n`,
      ]),
    );
    const adapter = createChatAdapter("cnv_1");
    const chunks: string[] = [];
    const generator = adapter.run({
      messages: [{ role: "user", content: [{ type: "text", text: "本月赚多少？" }] }],
    } as never) as AsyncIterable<{
      content: Array<{ type: string; text: string }>;
      metadata?: { custom?: { timeline?: unknown[] } };
    }>;
    for await (const result of generator) {
      const part = result.content[0];
      if (part && part.type === "text") chunks.push(part.text);
    }
    expect(chunks[0]).toBe("最终");
    expect(chunks[1]).toBe("最终回答");
    expect(chunks.at(-1)).toContain("最终回答[1]");
    expect(chunks.at(-1)).toContain("数据来源");
    expect(mocks.streamMessage).toHaveBeenCalledWith("cnv_1", { message: "本月赚多少？" }, { signal: undefined });
  });

  it("step 事件写入 metadata.custom.timeline", async () => {
    mocks.streamMessage.mockResolvedValue(
      sseResponse([
        'event: meta\ndata: {"turn_id":"trn_1"}\n\n',
        'event: step\nid: 1\ndata: {"seq":1,"kind":"understanding.started","phase":"understand","at":"2026-11-21T10:00:00Z","payload":{}}\n\n',
        'event: step\nid: 2\ndata: {"seq":2,"kind":"tool.call.succeeded","phase":"execute","call_id":"call_1","at":"2026-11-21T10:00:01Z","payload":{"title":"流水查询","row_count":2}}\n\n',
        `event: done\ndata: ${JSON.stringify({ message })}\n\n`,
      ]),
    );
    const adapter = createChatAdapter("cnv_1");
    const timelines: unknown[][] = [];
    const generator = adapter.run({
      messages: [{ role: "user", content: [{ type: "text", text: "本月赚多少？" }] }],
    } as never) as AsyncIterable<{
      content: Array<{ type: string; text: string }>;
      metadata?: { custom?: { timeline?: unknown[] } };
    }>;
    for await (const result of generator) {
      const timeline = result.metadata?.custom?.timeline;
      if (timeline) timelines.push(timeline);
    }
    expect(timelines.at(-1)).toHaveLength(2);
  });
});
