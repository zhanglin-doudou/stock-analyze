import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FilterBar, FilterField } from "./FilterBar";

afterEach(() => {
  cleanup();
});

describe("FilterBar", () => {
  it("渲染字段、操作行、结果计数与说明，并带无障碍分组名", () => {
    render(
      <FilterBar
        ariaLabel="回合筛选"
        count="本页 2 条"
        actions={<button type="button">查询</button>}
        note={<p className="muted">口径说明</p>}
      >
        <FilterField label="证券代码">
          <input type="text" aria-label="证券代码" />
        </FilterField>
        <FilterField label="状态">
          <select aria-label="状态">
            <option value="">全部</option>
          </select>
        </FilterField>
      </FilterBar>,
    );

    expect(screen.getByRole("group", { name: "回合筛选" })).toBeTruthy();
    expect(screen.getByText("证券代码")).toBeTruthy();
    expect(screen.getByText("状态")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查询" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("本页 2 条");
    expect(screen.getByText("口径说明")).toBeTruthy();
    expect(screen.getByLabelText("证券代码")).toBeTruthy();
    expect(screen.getByLabelText("状态")).toBeTruthy();
  });

  it("重置按钮默认文案并触发 onReset", () => {
    const onReset = vi.fn();
    render(<FilterBar ariaLabel="筛选" onReset={onReset} />);

    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("重置按钮文案可自定义，且重置按钮排在操作之后", () => {
    const onReset = vi.fn();
    render(
      <FilterBar
        ariaLabel="筛选"
        onReset={onReset}
        resetLabel="清空筛选"
        actions={<button type="button">查询</button>}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "清空筛选" }));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "重置" })).toBeNull();
  });

  it("未提供 onReset 时不渲染重置按钮", () => {
    render(<FilterBar ariaLabel="筛选" actions={<button type="button">刷新</button>} />);

    expect(screen.getByRole("button", { name: "刷新" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重置" })).toBeNull();
    expect(screen.queryByRole("button", { name: "清空筛选" })).toBeNull();
  });
});
