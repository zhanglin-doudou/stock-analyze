import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  ensureSession: vi.fn(),
  getConversation: vi.fn(),
}));

vi.mock("../client", () => ({
  api: {
    chat: {
      ensureSession: mocks.ensureSession,
      getConversation: mocks.getConversation,
    },
  },
}));

import { StarAssistant } from "./StarAssistant";

afterEach(() => cleanup());

function historyMessage(index: number, role: "user" | "assistant") {
  return {
    message_id: `msg_${index}`,
    conversation_id: "cnv_test",
    turn_id: `trn_${index}`,
    role,
    content: `历史消息 ${index}`,
    status: "ok",
    created_at: `2026-11-20T10:00:${String(index).padStart(2, "0")}Z`,
  };
}

describe("StarAssistant", () => {
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
  });

  it("打开悬浮窗并恢复历史消息，展示空状态建议", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <StarAssistant />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("本月收益为正。")).toBeTruthy();
    expect(screen.queryByText("数据来源")).toBeNull();
  });

  it("无历史时展示固定问题入口", async () => {
    mocks.getConversation.mockResolvedValue({ conversation_id: "cnv_test", messages: [] });
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <StarAssistant />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("本月赚了多少？")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "星星助手" })).toBeTruthy();
  });

  it("默认只加载 8 条，顶部可加载更早消息并显示 Spinner 状态", async () => {
    mocks.getConversation.mockReset();
    mocks.getConversation
      .mockResolvedValueOnce({
        conversation_id: "cnv_test",
        next_cursor: "msg_0",
        messages: Array.from({ length: 8 }, (_, index) =>
          historyMessage(index + 1, index % 2 === 0 ? "user" : "assistant"),
        ),
      })
      .mockResolvedValueOnce({
        conversation_id: "cnv_test",
        next_cursor: null,
        messages: [
          { ...historyMessage(0, "user"), message_id: "msg_old_u", content: "更早的问题" },
          {
            ...historyMessage(0, "assistant"),
            message_id: "msg_old_a",
            content: "更早的回答",
          },
        ],
      });
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <StarAssistant />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("历史消息 1")).toBeTruthy();
    expect(screen.getByText("历史消息 8")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "加载更多" }));
    expect(await screen.findByText("更早的问题")).toBeTruthy();
    expect(mocks.getConversation).toHaveBeenLastCalledWith({
      limit: 8,
      before_id: "msg_0",
    });
  });
});
