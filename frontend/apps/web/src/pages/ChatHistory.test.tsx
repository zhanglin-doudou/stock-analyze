import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  ensureSession: vi.fn(),
  getConversation: vi.fn(),
  getTrace: vi.fn(),
  replay: vi.fn(),
  deleteConversation: vi.fn(),
}));

vi.mock("../client", () => ({
  api: {
    chat: {
      ensureSession: mocks.ensureSession,
      getConversation: mocks.getConversation,
      getTrace: mocks.getTrace,
      replay: mocks.replay,
      deleteConversation: mocks.deleteConversation,
    },
  },
}));

import { ChatHistory } from "./ChatHistory";

afterEach(() => cleanup());

describe("ChatHistory", () => {
  beforeEach(() => {
    mocks.ensureSession.mockResolvedValue({ conversation_id: "cnv_test" });
    mocks.getConversation.mockResolvedValue({
      conversation_id: "cnv_test",
      messages: [
        {
          message_id: "msg_u",
          conversation_id: "cnv_test",
          turn_id: "trn_test",
          role: "user",
          content: "本月赚了多少？",
          status: "ok",
          created_at: "2026-11-20T10:00:00Z",
        },
        {
          message_id: "msg_a",
          conversation_id: "cnv_test",
          turn_id: "trn_test",
          role: "assistant",
          content: "本月收益为正。",
          status: "ok",
          created_at: "2026-11-20T10:00:01Z",
        },
      ],
    });
    mocks.getTrace.mockResolvedValue({
      turn: {
        id: "trn_test",
        question: "本月赚了多少？",
        resolved_query: "本月赚了多少",
        status: "ok",
        understanding: { intents: ["portfolio"] },
      },
      tool_calls: [],
      llm_calls: [],
    });
    mocks.replay.mockResolvedValue({ match: true, diffs: [] });
    mocks.deleteConversation.mockResolvedValue(undefined);
  });

  it("列出问答轮次并展示轨迹与确定性回放", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ChatHistory />
      </MemoryRouter>,
    );
    expect((await screen.findAllByText("本月赚了多少？")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("本月收益为正。").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "回放（确定性）" }));
    expect(await screen.findByText("回放结果与原始回答一致。")).toBeTruthy();
    expect(mocks.replay).toHaveBeenCalledWith("trn_test", "deterministic");
  });

  it("清空问答历史需要二次确认", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ChatHistory />
      </MemoryRouter>,
    );
    await screen.findAllByText("本月赚了多少？");
    await user.click(screen.getByRole("button", { name: "清空问答历史" }));
    expect(await screen.findByText("确认清空问答历史？")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "确认清空" }));
    expect(await screen.findByText("问答历史已清空。")).toBeTruthy();
    expect(mocks.deleteConversation).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/还没有问答记录/)).toBeTruthy();
  });
});
