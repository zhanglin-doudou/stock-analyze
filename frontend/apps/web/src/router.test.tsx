import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout, RequireAuth } from "./router";
import { clearSession, saveTokens } from "./store/session";

afterEach(() => {
  cleanup();
  clearSession();
});

describe("router / Layout（FT-09 导航与登录保护）", () => {
  it("顶部导航含「资产」链接且指向 /assets，外壳渲染子路由", () => {
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
    expect(screen.getByText("资产页占位")).toBeTruthy();
    // 原有导航项仍在（顺序：交易流水 → 回合 → 净值 → 资产）
    const labels = screen.getAllByRole("link").map((item) => item.textContent);
    expect(labels.slice(0, 4)).toEqual(["交易流水", "回合", "净值", "资产"]);
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
