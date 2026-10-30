import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PatToken } from "@sa/api-client";
import { Settings } from "./Settings";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    tokens: {
      list: vi.fn(),
      create: vi.fn(),
      revoke: vi.fn(),
    },
  },
}));

const listTokens = vi.mocked(api.tokens.list);

function makeToken(): PatToken {
  return {
    token_id: "pat_1",
    name: "本地脚本",
    prefix: "pat_abcd",
    scopes: ["read"],
    expires_at: "2026-12-31T00:00:00Z",
    created_at: "2026-10-01T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-trend");
  listTokens.mockResolvedValue({ items: [makeToken()], total: 1 });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-trend");
});

describe("Settings（Phase 3）", () => {
  it("展示外观偏好并可切换密度/涨跌色（立即写根属性 + localStorage）", async () => {
    render(<Settings />);

    expect(screen.getByRole("heading", { name: "设置" })).toBeTruthy();
    const densityGroup = screen.getByRole("group", { name: "表格密度" });
    expect(densityGroup).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "紧凑" }));
    expect(document.documentElement.dataset.density).toBe("compact");
    expect(window.localStorage.getItem("sa.density")).toBe("compact");

    fireEvent.click(screen.getByRole("button", { name: "绿涨红跌" }));
    expect(document.documentElement.dataset.trend).toBe("green-up");
    expect(window.localStorage.getItem("sa.trend")).toBe("green-up");

    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("sa.theme")).toBe("dark");
  });

  it("渲染 PAT 列表并保留撤销入口", async () => {
    render(<Settings />);
    await waitFor(() => expect(listTokens).toHaveBeenCalled());
    expect(await screen.findByText("本地脚本")).toBeTruthy();
    expect(screen.getByText("pat_abcd")).toBeTruthy();
    expect(screen.getByRole("button", { name: "撤销" })).toBeTruthy();
  });
});
