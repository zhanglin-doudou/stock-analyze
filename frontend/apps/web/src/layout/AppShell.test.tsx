import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout } from "../router";
import { clearSession } from "../store/session";

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/metrics" element={<div>指标页占位</div>} />
          <Route path="/assets" element={<div>资产页占位</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  clearSession();
  document.title = "";
});

describe("AppShell（Phase 1b）", () => {
  it("按 5 组渲染导航，占位入口可见", () => {
    renderShell("/metrics");
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "跳到主要内容" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "概览" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "交易" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "分析" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "数据" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "月报（占位）" })).toBeTruthy();
  });

  it("⌘K 打开命令面板，F 切换专注模式", () => {
    const { container } = renderShell("/assets");
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByRole("dialog", { name: "命令面板" })).toBeTruthy();

    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "命令面板" })).toBeNull();

    const shell = container.querySelector(".app-shell");
    expect(shell?.getAttribute("data-focus")).toBe("false");
    fireEvent.keyDown(window, { key: "f" });
    expect(shell?.getAttribute("data-focus")).toBe("true");
  });

  it("当前路由有 aria-current，且同步 document.title", () => {
    renderShell("/assets");
    const assets = screen.getByRole("link", { name: "资产" });
    expect(assets.getAttribute("aria-current")).toBe("page");
    expect(document.title).toBe("资产 · 交易复盘");
  });
});
