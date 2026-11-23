import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { GuideApp } from "./GuideApp";
import { clearSession, saveTokens } from "../store/session";

const mocks = vi.hoisted(() => ({ login: vi.fn() }));

vi.mock("../client", () => ({
  api: { auth: { login: mocks.login } },
}));

afterEach(() => {
  cleanup();
  clearSession();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  document.documentElement.removeAttribute("data-theme");
});

describe("GuideApp", () => {
  it("展示主链路、账单对比图片与显著的合规声明", () => {
    render(<GuideApp />);

    expect(screen.getByRole("heading", { name: /把券商对账单/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "导入账单：一份对账单的解析之旅" })).toBeTruthy();
    expect(screen.getByAltText(/华泰证券对账单原始表格/).getAttribute("src")).toBe("/guide/bill-raw.png");
    expect(screen.getByAltText(/解析后的结构化账本表格/).getAttribute("src")).toBe("/guide/bill-parsed.png");

    const compliance = screen.getByRole("note");
    expect(within(compliance).getByText(/不构成任何投资建议/)).toBeTruthy();
    expect(within(compliance).getByText(/历史业绩不代表未来表现/)).toBeTruthy();
    expect(screen.getByText("本系统不构成任何投资建议。投资有风险，入市需谨慎。")).toBeTruthy();
  });

  it("未登录时提供登录与注册入口", () => {
    render(<GuideApp />);

    expect(screen.getByRole("link", { name: "登录" }).getAttribute("href")).toBe("/login");
    expect(screen.getByRole("link", { name: "注册" }).getAttribute("href")).toBe("/register");
    expect(screen.queryByRole("link", { name: "返回工作台" })).toBeNull();
  });

  it("登录后返回工作台，并保留安全的来源路径", async () => {
    saveTokens({ access_token: "access-token", refresh_token: "refresh-token" });
    window.history.replaceState({}, "", "/guide?next=%2Fassets%3Fperiod%3D2026-01");
    render(<GuideApp />);

    const back = await screen.findByRole("link", { name: "返回工作台" });
    expect(back.getAttribute("href")).toBe("/assets?period=2026-01");
    expect(screen.getByRole("link", { name: "立即导入账单" }).getAttribute("href")).toBe("/import");
  });

  it("拒绝把站外 next 用作返回地址", async () => {
    saveTokens({ access_token: "access-token" });
    window.history.replaceState({}, "", "/guide?next=https%3A%2F%2Fevil.example");
    render(<GuideApp />);

    const back = await screen.findByRole("link", { name: "返回工作台" });
    expect(back.getAttribute("href")).toBe("/");
  });
});

describe("GuideApp · demo 一键体验", () => {
  it("未登录时在「免费注册开始」下一行提供「用demo账号体验」，点击立即以 demo 账号登录", async () => {
    mocks.login.mockResolvedValue({ access_token: "demo-access", refresh_token: "demo-refresh" });
    const assign = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign },
      writable: true,
      configurable: true,
    });
    try {
      render(<GuideApp />);
      fireEvent.click(screen.getByRole("button", { name: "用demo账号体验" }));
      await waitFor(() =>
        expect(mocks.login).toHaveBeenCalledWith({
          email: "xingst01@demo.com",
          password: "xingst666",
        }),
      );
      expect(window.localStorage.getItem("sa.access_token")).toBe("demo-access");
      expect(assign).toHaveBeenCalledWith("/");
    } finally {
      Object.defineProperty(window, "location", {
        value: originalLocation,
        writable: true,
        configurable: true,
      });
    }
  });

  it("登录失败就地提示且不跳转", async () => {
    mocks.login.mockRejectedValue(new Error("boom"));
    render(<GuideApp />);

    fireEvent.click(screen.getByRole("button", { name: "用demo账号体验" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "用demo账号体验" })).toBeTruthy();
  });

  it("登录后不再显示 demo 入口", async () => {
    saveTokens({ access_token: "access-token", refresh_token: "refresh-token" });
    render(<GuideApp />);

    await screen.findByRole("link", { name: "返回工作台" });
    expect(screen.queryByRole("button", { name: "用demo账号体验" })).toBeNull();
  });
});
