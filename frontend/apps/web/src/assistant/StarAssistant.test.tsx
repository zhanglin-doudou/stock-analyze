import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

function renderWithShell() {
  return render(
    <MemoryRouter>
      <div className="app-shell">
        <div className="shell-main">
          <button type="button">主区操作</button>
        </div>
        <StarAssistant />
      </div>
    </MemoryRouter>,
  );
}

describe("StarAssistant", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mocks.ensureSession.mockClear();
    mocks.getConversation.mockClear();
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

  it("悬浮窗打开时外部仍可操作且不会关闭，close 后仅显示悬浮按钮", async () => {
    const user = userEvent.setup();
    renderWithShell();
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("本月收益为正。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开星星助手" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "主区操作" }));
    expect(screen.getByRole("dialog", { name: "星星助手" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "关闭星星助手" }));
    expect(screen.queryByRole("dialog", { name: "星星助手" })).toBeNull();
    expect(screen.getByRole("button", { name: "打开星星助手" })).toBeTruthy();
  });

  it("Esc 关闭悬浮窗", async () => {
    const user = userEvent.setup();
    renderWithShell();
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByRole("dialog", { name: "星星助手" })).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "星星助手" })).toBeNull();
    expect(screen.getByRole("button", { name: "打开星星助手" })).toBeTruthy();
  });

  it("右侧固定与悬浮窗往返时保持会话，且与 shell-main 平级", async () => {
    const user = userEvent.setup();
    const { container } = renderWithShell();
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("本月收益为正。")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "固定到右侧" }));
    expect(window.localStorage.getItem("sa.assistant.mode")).toBe("docked");
    const aside = await screen.findByRole("complementary", { name: "星星助手" });
    const shellMain = container.querySelector(".shell-main");
    expect(aside.parentElement).toBe(shellMain?.parentElement ?? null);
    expect(screen.queryByRole("dialog", { name: "星星助手" })).toBeNull();
    expect(screen.getByText("本月收益为正。")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "切换为悬浮窗" }));
    expect(await screen.findByRole("dialog", { name: "星星助手" })).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: "星星助手" })).toBeNull();
    expect(window.localStorage.getItem("sa.assistant.mode")).toBe("dialog");
    expect(mocks.ensureSession).toHaveBeenCalledTimes(1);
  });

  it("关闭后再次打开沿用上次的固定模式", async () => {
    const user = userEvent.setup();
    renderWithShell();
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("本月收益为正。")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "固定到右侧" }));
    expect(screen.queryByRole("dialog", { name: "星星助手" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "关闭星星助手" }));
    expect(screen.getByRole("button", { name: "打开星星助手" })).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: "星星助手" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByRole("complementary", { name: "星星助手" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "星星助手" })).toBeNull();
  });

  it("拖拽将宽度限制在 25% 到 50%，并支持键盘调整", async () => {
    const user = userEvent.setup();
    const { container } = renderWithShell();
    await user.click(screen.getByRole("button", { name: "打开星星助手" }));
    expect(await screen.findByText("本月收益为正。")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "固定到右侧" }));

    const appShell = container.querySelector(".app-shell") as HTMLElement;
    appShell.getBoundingClientRect = () =>
      ({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 1000,
        bottom: 800,
        width: 1000,
        height: 800,
        toJSON: () => ({}),
      }) as DOMRect;
    const separator = screen.getByRole("separator", { name: "调整星星助手宽度" });
    const aside = screen.getByRole("complementary", { name: "星星助手" });

    fireEvent.pointerDown(separator, { pointerId: 1, button: 0, clientX: 200 });
    expect(separator.getAttribute("aria-valuenow")).toBe("50");
    expect(aside.style.width).toBe("50%");
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 900 });
    expect(separator.getAttribute("aria-valuenow")).toBe("25");
    expect(aside.style.width).toBe("25%");
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 900 });

    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 200 });
    expect(separator.getAttribute("aria-valuenow")).toBe("25");
    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(separator.getAttribute("aria-valuenow")).toBe("26");
    fireEvent.keyDown(separator, { key: "ArrowLeft", shiftKey: true });
    expect(separator.getAttribute("aria-valuenow")).toBe("31");
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator.getAttribute("aria-valuenow")).toBe("30");
  });
});
