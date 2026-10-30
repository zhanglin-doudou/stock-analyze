import { describe, expect, it } from "vitest";
import { currentMonth, latestCompletedMonth } from "./date";

describe("currentMonth", () => {
  it("按本地日历取月份（不受 UTC 影响）", () => {
    // 本地时间 2026-11-01 00:30；若用 toISOString() 在东八区会得到 10 月。
    expect(currentMonth(new Date(2026, 10, 1, 0, 30))).toBe("2026-11");
    expect(currentMonth(new Date(2026, 0, 1, 0, 0))).toBe("2026-01");
    expect(currentMonth(new Date(2026, 8, 30, 23, 59))).toBe("2026-09");
  });

  it("补零到两位数", () => {
    expect(currentMonth(new Date(2026, 2, 5))).toBe("2026-03");
  });
});

describe("latestCompletedMonth", () => {
  it("返回上一个自然月（跨年/跨月）", () => {
    expect(latestCompletedMonth(new Date(2026, 10, 1, 0, 30))).toBe("2026-10");
    expect(latestCompletedMonth(new Date(2026, 0, 15))).toBe("2025-12");
    expect(latestCompletedMonth(new Date(2026, 8, 30, 23, 59))).toBe("2026-08");
  });
});
