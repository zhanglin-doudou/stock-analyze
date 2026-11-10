import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useRoutes } from "react-router-dom";
import { Layout, RequireAuth, appRoutes } from "./router";
import { pageMeta } from "./layout/navigation";
import { clearSession, saveTokens } from "./store/session";

/** 用同一份路由表在 MemoryRouter 下渲染（见下方 jsdom/AbortSignal 说明）。 */
function AppRoutes() {
  return useRoutes(appRoutes);
}

afterEach(() => {
  cleanup();
  clearSession();
});

describe("router / Layout（FT-09 导航与登录保护）", () => {
  it("外壳渲染子路由，侧栏一级分组可展开到「看板」", () => {
    render(
      <MemoryRouter initialEntries={["/assets"]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/assets" element={<div>资产页占位</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "资产" });
    expect(link.getAttribute("href")).toBe("/assets");
    expect(link.getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("资产页占位")).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("group", { name: "数据" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "核心" }));
    expect(screen.getByRole("link", { name: "看板" }).getAttribute("href")).toBe("/dashboard");
  });

  it("/rounds/insights 命中静态路由（不被 rounds/:roundId 吃掉）", async () => {
    saveTokens({ access_token: "access-token", refresh_token: "refresh-token" });
    // 页面挂载后会把默认期间写回 `?period=`（replace 导航）。数据路由在 jsdom 下会
    // 用 jsdom 的 AbortSignal 构造 node 的 Request 而抛未处理拒绝，这里用 MemoryRouter
    // 渲染同一份 appRoutes，路由表与导航高亮的断言等价。
    render(
      <MemoryRouter initialEntries={["/rounds/insights"]}>
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "回合洞察" })).toBeTruthy();
    // 面包屑/标题不能被“回合详情”前缀规则误伤。
    expect(pageMeta("/rounds/insights").title).toBe("回合洞察");
    // 菜单激活：回合洞察命中自身，不把「回合」也高亮。
    expect(screen.getByRole("link", { name: "回合洞察" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "分析" }));
    expect(screen.getByRole("link", { name: "回合" }).getAttribute("aria-current")).toBeNull();
  });

  it("未登录访问 /assets 跳转登录页", () => {
    clearSession();
    render(
      <MemoryRouter initialEntries={["/assets"]}>
        <Routes>
          <Route
            path="/assets"
            element={
              <RequireAuth>
                <div>受保护的资产页</div>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<div>登录页占位</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("登录页占位")).toBeTruthy();
    expect(screen.queryByText("受保护的资产页")).toBeNull();
  });

  it("已登录时 RequireAuth 放行", () => {
    saveTokens({ access_token: "access-token", refresh_token: "refresh-token" });
    render(
      <MemoryRouter initialEntries={["/assets"]}>
        <Routes>
          <Route
            path="/assets"
            element={
              <RequireAuth>
                <div>受保护的资产页</div>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<div>登录页占位</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("受保护的资产页")).toBeTruthy();
  });
});
