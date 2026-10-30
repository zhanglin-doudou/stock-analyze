import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout, RequireAuth } from "./router";
import { clearSession, saveTokens } from "./store/session";

afterEach(() => {
  cleanup();
  clearSession();
});

describe("router / Layout（FT-09 导航与登录保护）", () => {
  it("顶部导航首项「看板」指向 /dashboard，外壳渲染子路由", () => {
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
    expect(screen.getByRole("link", { name: "看板" }).getAttribute("href")).toBe("/dashboard");
    expect(screen.getByText("资产页占位")).toBeTruthy();
    // Phase 1b：导航按 5 组排列，前 5 项为 概览/交易/分析 的前三个入口
    const labels = within(screen.getByRole("navigation", { name: "主导航" }))
      .getAllByRole("link")
      .map((item) => item.textContent);
    expect(labels.slice(0, 5)).toEqual(["看板", "交易流水", "回合", "净值与收益", "指标"]);
    // 当前页激活态由 NavLink 提供
    expect(link.getAttribute("aria-current")).toBe("page");
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
