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
});
