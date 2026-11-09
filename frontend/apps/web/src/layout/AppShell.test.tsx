import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout } from "../router";
import { clearSession } from "../store/session";

interface MediaMock {
  matches: boolean;
  media: string;
  onchange: null;
  addEventListener: (...args: unknown[]) => void;
  removeEventListener: (...args: unknown[]) => void;
  addListener: (...args: unknown[]) => void;
  removeListener: (...args: unknown[]) => void;
  dispatchEvent: () => boolean;
}

const originalMatchMedia = window.matchMedia;

function mockMedia(matches: boolean): void {
  const media: MediaMock = {
    matches,
    media: "(min-width: 1280px)",
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => true,
  };
  window.matchMedia = ((_query: string) => media) as unknown as typeof window.matchMedia;
}

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/metrics" element={<div>指标页占位</div>} />
          <Route path="/assets" element={<div>资产页占位</div>} />
          <Route path="/rounds/insights" element={<div>回合洞察占位</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockMedia(true);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  clearSession();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.title = "";
  window.matchMedia = originalMatchMedia;
});

describe("AppShell 两级菜单", () => {
  it("大屏渲染 3 个一级分组，并默认展开当前路由分组", () => {
    renderShell("/metrics");
    // 底部设置区靠 margin-top:auto 推到底部，不再依赖空 flex 占位节点
    expect(document.querySelector(".shell-rail-spacer")).toBeNull();
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "跳到主要内容" })).toBeTruthy();
    for (const group of ["核心", "分析", "数据"]) {
      expect(screen.getByRole("group", { name: group })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "分析" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "核心" }).getAttribute("aria-expanded")).toBe("false");
    // 收起的抽屉不进入可访问树
    expect(screen.queryByRole("link", { name: "看板" })).toBeNull();
    expect(screen.getByRole("link", { name: "指标" }).getAttribute("aria-current")).toBe("page");
  });

  it("一级菜单互斥展开，二级项带缩进/激活态", () => {
    renderShell("/metrics");
    fireEvent.click(screen.getByRole("button", { name: "数据" }));
    expect(screen.getByRole("button", { name: "数据" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "分析" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("link", { name: "资产" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "月报（占位）" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "核心" }));
    expect(screen.getByRole("button", { name: "核心" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByRole("link", { name: "资产" })).toBeNull();
  });

  it("大屏可通过边界中点按钮收起为一级图标，再展开", () => {
    const { container } = renderShell("/metrics");
    const rail = container.querySelector(".shell-rail");

    fireEvent.click(screen.getByRole("button", { name: "收起菜单" }));
    expect(screen.getByRole("button", { name: "展开菜单" })).toBeTruthy();
    expect(rail?.className).toContain("is-compact");
    expect(screen.queryByRole("link", { name: "指标" })).toBeNull();
    expect(window.localStorage.getItem("sa.nav.collapsed")).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: "展开菜单" }));
    expect(screen.getByRole("button", { name: "收起菜单" })).toBeTruthy();
    expect(rail?.className).toContain("is-expanded");
    expect(screen.getByRole("link", { name: "指标" }).getAttribute("aria-current")).toBe("page");
    expect(window.localStorage.getItem("sa.nav.collapsed")).toBe("0");
  });

  it("中屏只显示图标：hover 是 CSS，点击一级图标弹出二级 flyout", () => {
    mockMedia(false);
    renderShell("/metrics");
    const core = screen.getByRole("button", { name: "核心" });
    expect(core.getAttribute("data-tooltip")).toBe("核心");
    expect(screen.queryByRole("link", { name: "看板" })).toBeNull();

    fireEvent.click(core);
    expect(screen.getByRole("region", { name: "核心菜单" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "看板" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "分析" }));
    expect(screen.queryByRole("region", { name: "核心菜单" })).toBeNull();
    expect(screen.getByRole("region", { name: "分析菜单" })).toBeTruthy();
  });

  it("/rounds/insights 高亮「回合洞察」，不误伤「回合」", () => {
    renderShell("/rounds/insights");
    expect(screen.getByRole("link", { name: "回合洞察" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "分析" }));
    expect(screen.getByRole("link", { name: "回合" }).getAttribute("aria-current")).toBeNull();
  });

  it("⌘K 打开命令面板", () => {
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByRole("dialog", { name: "命令面板" })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "命令面板" })).toBeNull();
  });

  it("顶栏主题按钮在浅/深之间切换并持久化", () => {
    renderShell("/metrics");
    fireEvent.click(screen.getByRole("button", { name: "切换到深色主题" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("sa.theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "切换到浅色主题" })).toBeTruthy();
  });

  it("当前路由有 aria-current，且同步 document.title", () => {
    renderShell("/assets");
    expect(screen.getByRole("link", { name: "资产" }).getAttribute("aria-current")).toBe("page");
    expect(document.title).toBe("资产 · 交易复盘");
  });
});
