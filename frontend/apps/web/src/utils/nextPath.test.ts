import { describe, expect, it } from "vitest";
import { sanitizeNextPath } from "./nextPath";

describe("sanitizeNextPath", () => {
  it.each(["/", "/assets", "/assets?period=2026-01", "/rounds/insights#top"])(
    "保留站内路径 %s",
    (path) => {
      expect(sanitizeNextPath(path)).toBe(path);
    },
  );

  it("去除路径两侧空白", () => {
    expect(sanitizeNextPath("  /assets?period=2026-01  ")).toBe("/assets?period=2026-01");
  });

  it.each([
    null,
    undefined,
    "",
    "assets",
    "https://evil.example",
    "//evil.example",
    "\\\\evil.example",
    "/assets\\evil",
    "/assets:443",
    "javascript:alert(1)",
    "/assets\n/login",
    "/assets\t/login",
  ])("拒绝不安全或无效路径 %s", (path) => {
    expect(sanitizeNextPath(path)).toBeNull();
  });
});
