import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { CommandPalette } from "./CommandPalette";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderPalette(onClose = vi.fn()) {
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <LocationProbe />
              <CommandPalette open onClose={onClose} />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return onClose;
}

afterEach(cleanup);

describe("CommandPalette（Phase 4b）", () => {
  it("搜索并回车跳转，然后关闭", () => {
    const onClose = renderPalette();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "资产" } });
    const option = screen.getByRole("option", { name: /资产/ });
    expect(option.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("location").textContent).toBe("/assets");
    expect(onClose).toHaveBeenCalled();
  });

  it("上下键切换 active，Esc 关闭", () => {
    const onClose = renderPalette();
    const input = screen.getByRole("combobox");
    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(1);
    const firstId = input.getAttribute("aria-activedescendant");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).not.toBe(firstId);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("无匹配时展示空态", () => {
    renderPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "zzzz" } });
    expect(screen.getByText("没有匹配的页面")).toBeTruthy();
  });
});
