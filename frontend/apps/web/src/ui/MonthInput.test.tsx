import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MonthInput, supportsMonthInput } from "./MonthInput";

afterEach(cleanup);

describe("MonthInput（Phase 4c）", () => {
  it("支持时渲染原生 month input 并回传 YYYY-MM", () => {
    const onChange = vi.fn();
    render(<MonthInput value="2026-10" onChange={onChange} />);
    const input = screen.getByLabelText("月份") as HTMLInputElement;
    expect(input.type).toBe("month");
    expect(input.value).toBe("2026-10");
    fireEvent.change(input, { target: { value: "2026-11" } });
    expect(onChange).toHaveBeenCalledWith("2026-11");
  });

  it("探测函数在 jsdom 下为 true，且不是降到 fallback", () => {
    expect(supportsMonthInput()).toBe(true);
    render(<MonthInput value="2026-10" onChange={vi.fn()} />);
    expect(document.querySelector(".month-fallback")).toBeNull();
  });
});
