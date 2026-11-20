import { describe, expect, it } from "vitest";
import { parseSse } from "./sse";

function streamResponse(parts: string[]): Response {
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

describe("parseSse", () => {
  it("跨 chunk 解析 event/data，并支持 CRLF/多行 data", async () => {
    const response = streamResponse([
      "event: meta\r\ndata: {\"turn_id\":\"trn_1\"}\r\n\r\n",
      "event: delta\ndata: {\"text\":\"你",
      "好\"}\n\n",
      ": heartbeat\n\n",
      "event: done\ndata: {\"message\":{\"turn_id\":\"trn_1\"}}\n\n",
    ]);
    const events = [];
    for await (const event of parseSse(response)) events.push(event);
    expect(events).toEqual([
      { event: "meta", data: { turn_id: "trn_1" } },
      { event: "delta", data: { text: "你好" } },
      { event: "done", data: { message: { turn_id: "trn_1" } } },
    ]);
  });

  it("无 body 直接报错", async () => {
    await expect(async () => {
      for await (const _ of parseSse(new Response(null))) {
        // no-op
      }
    }).rejects.toThrow("SSE 响应没有 body");
  });
});
