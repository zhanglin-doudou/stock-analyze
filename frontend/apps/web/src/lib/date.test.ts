import { describe, expect, it } from "vitest";
import {
  currentDate,
  currentMonth,
  currentYearToken,
  latestCompletedMonth,
  periodKind,
  periodYear,
  toRangeToken,
} from "./date";

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

describe("currentYearToken", () => {
  it("取本地年份（东八区年初 0:30 也不落到上一年）", () => {
    expect(currentYearToken(new Date(2026, 0, 1, 0, 30))).toBe("2026");
    expect(currentYearToken(new Date(2026, 11, 31, 23, 59))).toBe("2026");
  });
});

describe("currentDate", () => {
  it("按本地日历补零到日", () => {
    expect(currentDate(new Date(2026, 0, 5))).toBe("2026-01-05");
    expect(currentDate(new Date(2026, 10, 30))).toBe("2026-11-30");
  });
});

describe("periodKind / periodYear / toRangeToken", () => {
  it("按字符串形态判定期间种类", () => {
    expect(periodKind("2026")).toBe("year");
    expect(periodKind("2026-06")).toBe("month");
    expect(periodKind("2026-01-01..2026-06-30")).toBe("custom");
  });

  it("年度/月度/自定义都能取年份", () => {
    expect(periodYear("2026")).toBe("2026");
    expect(periodYear("2026-06")).toBe("2026");
    expect(periodYear("2026-01-01..2026-06-30")).toBe("2026");
  });

  it("闭区间 token 由起止日期拼接", () => {
    expect(toRangeToken("2026-01-01", "2026-06-30")).toBe("2026-01-01..2026-06-30");
  });
});
