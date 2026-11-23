import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within, act } from "@testing-library/react";
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

describe("GuideApp · 星星助手介绍", () => {
  it("展示星星助手的能力与合规提示", () => {
    render(<GuideApp />);

    expect(screen.getByRole("heading", { name: "星星助手：直接问你的账单" })).toBeTruthy();
    expect(screen.getByText(/只答你的数据/)).toBeTruthy();
    expect(screen.getByText(/随叫随到/)).toBeTruthy();

    const demo = screen.getByLabelText("星星助手对话示意");
    expect(within(demo).getByText(/不构成投资建议/)).toBeTruthy();
  });
});

describe("GuideApp · 看板截图轮播", () => {
  it("默认展示第 1 张，左右按钮可无限循环切换", () => {
    render(<GuideApp />);
    const board = screen.getByLabelText("看板截图");

    expect(screen.getByAltText("看板界面截图 1").getAttribute("src")).toBe("/guide/dashboard-1.png");

    fireEvent.click(within(board).getByRole("button", { name: "下一张截图" }));
    expect(screen.getByAltText("看板界面截图 2").getAttribute("src")).toBe("/guide/dashboard-2.png");

    fireEvent.click(within(board).getByRole("button", { name: "上一张截图" }));
    expect(screen.getByAltText("看板界面截图 1").getAttribute("src")).toBe("/guide/dashboard-1.png");

    // 第 1 张继续向前应循环到最后一张
    fireEvent.click(within(board).getByRole("button", { name: "上一张截图" }));
    expect(screen.getByAltText("看板界面截图 4").getAttribute("src")).toBe("/guide/dashboard-4.png");
  });

  it("点击圆点直接切到指定截图", () => {
    render(<GuideApp />);
    const board = screen.getByLabelText("看板截图");

    fireEvent.click(within(board).getByRole("button", { name: "第 3 张截图" }));
    expect(screen.getByAltText("看板界面截图 3").getAttribute("src")).toBe("/guide/dashboard-3.png");
  });

  it("单张截图的模块不显示切换按钮", () => {
    render(<GuideApp />);
    const round = screen.getByLabelText("回合截图");

    expect(within(round).queryByRole("button")).toBeNull();
    expect(within(round).getByAltText("回合分析界面截图").getAttribute("src")).toBe("/guide/round.png");
  });

  it("鼠标悬停 1 秒后才弹出放大预览，移出立即收起", () => {
    vi.useFakeTimers();
    try {
      render(<GuideApp />);
      const viewport = screen.getByLabelText("看板截图").querySelector(".guide-carousel-viewport") as Element;

      fireEvent.mouseEnter(viewport);
      // 延迟未满时不显示，避免快速划过反复闪出
      expect(document.querySelector(".guide-carousel-zoom")).toBeNull();

      act(() => {
        vi.advanceTimersByTime(1000);
      });
      const zoom = document.querySelector(".guide-carousel-zoom");
      expect(zoom).toBeTruthy();
      expect(zoom?.querySelector("img")?.getAttribute("src")).toBe("/guide/dashboard-1.png");

      fireEvent.mouseLeave(viewport);
      expect(document.querySelector(".guide-carousel-zoom")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
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
