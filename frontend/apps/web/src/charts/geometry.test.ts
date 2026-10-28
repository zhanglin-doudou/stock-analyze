import { describe, expect, it } from "vitest";
import {
  areaPath,
  buildNavChart,
  buildSparkline,
  scaleLinear,
  smoothPath,
  toNumber,
} from "./geometry";

describe("toNumber（展示字符串 → 几何数字）", () => {
  it("解析定点/百分比/千分位/货币符", () => {
    expect(toNumber("1.28400000")).toBe(1.284);
    expect(toNumber("-12.80%")).toBe(-12.8);
    expect(toNumber("1,284,360.00")).toBe(1284360);
    expect(toNumber("¥1,000.00")).toBe(1000);
    expect(toNumber("+0.5")).toBe(0.5);
  });

  it("空值/占位符/非法值返回 null", () => {
    expect(toNumber(null)).toBeNull();
    expect(toNumber(undefined)).toBeNull();
    expect(toNumber("")).toBeNull();
    expect(toNumber("—")).toBeNull();
    expect(toNumber("abc")).toBeNull();
    expect(toNumber("NaN")).toBeNull();
    expect(toNumber("Infinity")).toBeNull();
  });
});

describe("scaleLinear", () => {
  it("端点与中点映射", () => {
    const scale = scaleLinear(1, 1.2, 100, 0);
    expect(scale(1)).toBe(100);
    expect(scale(1.1)).toBeCloseTo(50);
    expect(scale(1.2)).toBe(0);
  });

  it("domain 退化时返回固定中值", () => {
    const scale = scaleLinear(5, 5, 0, 100);
    expect(scale(5)).toBe(50);
  });
});

describe("smoothPath", () => {
  it("0/1/2 点退化", () => {
    expect(smoothPath([])).toBe("");
    expect(smoothPath([{ x: 10, y: 20 }])).toBe("M 10 20");
    expect(
      smoothPath([
        { x: 10, y: 20 },
        { x: 30, y: 40 },
      ]),
    ).toBe("M 10 20 L 30 40");
  });

  it("3 点 golden path（锁定几何算法）", () => {
    expect(
      smoothPath([
        { x: 0, y: 100 },
        { x: 50, y: 50 },
        { x: 100, y: 0 },
      ]),
    ).toBe(
      "M 0 100 C 4.2 95.8, 41.7 58.3, 50 50 C 58.3 41.7, 95.8 4.2, 100 0",
    );
  });
});

describe("areaPath", () => {
  it("以基线闭合", () => {
    const path = areaPath(
      [
        { x: 0, y: 80 },
        { x: 100, y: 20 },
      ],
      100,
    );
    expect(path.startsWith("M 0 80 L 100 20")).toBe(true);
    expect(path.endsWith("L 100 100 L 0 100 Z")).toBe(true);
  });
});

describe("buildNavChart", () => {
  it("值越大 y 越小，x 等距", () => {
    const chart = buildNavChart(
      [
        { label: "A", value: "1.000" },
        { label: "B", value: "1.100" },
        { label: "C", value: "1.200" },
      ],
      { width: 300, height: 100, padding: { top: 0, right: 0, bottom: 0, left: 0 } },
    );
    expect(chart.min).toBe(1);
    expect(chart.max).toBe(1.2);
    expect(chart.points).toEqual([
      { x: 0, y: 100 },
      { x: 150, y: 50 },
      { x: 300, y: 0 },
    ]);
    expect(chart.line.startsWith("M 0 100")).toBe(true);
    expect(chart.area.endsWith("L 300 100 L 0 100 Z")).toBe(true);
  });

  it("无有效值时返回空几何", () => {
    const chart = buildNavChart([{ label: "A", value: "—" }], { width: 100, height: 50 });
    expect(chart.points).toEqual([]);
    expect(chart.line).toBe("");
    expect(chart.area).toBe("");
  });

  it("支持显式 domain", () => {
    const chart = buildNavChart([{ label: "A", value: "5" }], {
      width: 100,
      height: 100,
      padding: { top: 0, right: 0, bottom: 0, left: 0 },
      domain: [0, 10],
    });
    expect(chart.points[0].y).toBe(50);
  });
});

describe("buildSparkline", () => {
  it("映射到 0..height 并返回最后一点", () => {
    const spark = buildSparkline(["1", "2", "3"], 100, 20);
    expect(spark.line.startsWith("M 0 20")).toBe(true);
    expect(spark.last).toEqual({ x: 100, y: 0 });
  });

  it("空序列返回空", () => {
    expect(buildSparkline([null, "—"], 100, 20)).toEqual({ line: "", area: "", last: null });
  });
});
