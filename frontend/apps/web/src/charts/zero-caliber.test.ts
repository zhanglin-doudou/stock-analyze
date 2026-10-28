import { describe, expect, it } from "vitest";

/**
 * 零口径门禁（Phase 1 起替代“只扫 2 个单文件”的旧断言）：
 * 扫描 src 下所有 ts/tsx 源码：禁止 parseFloat / parseInt / Number( / toFixed，
 * 唯一豁免 `charts/geometry.ts`（像素投影白名单）与测试文件。
 *
 * 注意：本测试自身不能出现在扫描结果里。
 */
const sources = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** glob 的相对 key 在本目录下是 "./geometry.ts"，在 build 下可能是 "../charts/geometry.ts"。 */
function isGeometryWhitelist(path: string): boolean {
  return path === "./geometry.ts" || path.endsWith("/charts/geometry.ts");
}
const FORBIDDEN = /\b(?:parseFloat|parseInt|Number\s*\(|toFixed)\b/g;

describe("零口径门禁", () => {
  it("业务源码不得解析/格式化业务数值（geometry 白名单除外）", () => {
    const violations: string[] = [];
    for (const [path, source] of Object.entries(sources)) {
      if (path.includes(".test.") || isGeometryWhitelist(path)) continue;
      const matches = source.match(FORBIDDEN);
      if (matches) violations.push(`${path}: ${[...new Set(matches)].join(", ")}`);
    }
    expect(violations).toEqual([]);
  });

  it("白名单模块确实存在且只做几何映射", () => {
    const geometryKey = Object.keys(sources).find(isGeometryWhitelist);
    expect(geometryKey).toBeTruthy();
    // 反向断言：geometry 里允许 Number(，防止门禁误扫导致失效
    expect(sources[geometryKey as string]).toMatch(/Number\(/);
  });
});
