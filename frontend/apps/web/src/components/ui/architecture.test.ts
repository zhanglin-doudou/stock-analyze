import { describe, expect, it } from "vitest";

/**
 * UI 全局化门禁（2026-11-19）：
 * 1. 页面/功能代码不得直接 import `@radix-ui/*`，只能消费 `@/components/ui` 包装；
 * 2. 包装层不得出现裸 hex（视觉只允许 tokens.css 语义变量）；
 * 3. Tailwind 必须保持 tw- 前缀 + preflight:false + 全 src content；
 * 4. 全局 tailwind.css 必须由 main.tsx 引入，且旧 /import 限定域 CSS 不得回流；
 * 5. 每个包装组件都必须从 index.ts 统一导出，避免“封装了但页面拿不到”。
 */
const sources = import.meta.glob("../../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const configs = import.meta.glob("../../../tailwind.config.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const isTest = (path: string) => path.includes(".test.");
const isUiModule = (path: string) => path.startsWith("./");

describe("UI 全局化门禁", () => {
  it("页面/功能代码不得直接依赖 Radix", () => {
    const violations = Object.entries(sources)
      .filter(([path, source]) => !isUiModule(path) && !isTest(path) && /["']@radix-ui\//.test(source))
      .map(([path]) => path);
    expect(violations).toEqual([]);
  });

  it("页面/功能代码按需导入子模块，不拉 barrel", () => {
    const violations = Object.entries(sources)
      .filter(
        ([path, source]) =>
          !isUiModule(path) &&
          !isTest(path) &&
          /["']@\/components\/ui["']/.test(source),
      )
      .map(([path]) => path);
    expect(violations).toEqual([]);
  });

  it("包装层只消费 tokens，不写裸 hex", () => {
    const violations: string[] = [];
    for (const [path, source] of Object.entries(sources)) {
      if (!isUiModule(path) || isTest(path)) continue;
      const matches = source.match(/#[0-9a-fA-F]{3,8}\b/g);
      if (matches) violations.push(`${path}: ${[...new Set(matches)].join(", ")}`);
    }
    expect(violations).toEqual([]);
  });

  it("Tailwind 保持全局模式：tw- 前缀 / preflight:false / content 覆盖 src", () => {
    const config = Object.values(configs)[0];
    expect(config).toBeTruthy();
    expect(config).toContain('prefix: "tw-"');
    expect(config).toContain("preflight: false");
    expect(config).toContain('"./src/**/*.{ts,tsx}"');
  });

  it("全局 tailwind.css 由 main.tsx 引入，旧 /import 限定域 CSS 不得回流", () => {
    expect(sources["../../main.tsx"]).toContain('import "./styles/tailwind.css"');
    const stale = Object.entries(sources)
      .filter(([path, source]) => !isTest(path) && /features\/import\/import\.css/.test(source))
      .map(([path]) => path);
    expect(stale).toEqual([]);
  });

  it("所有包装组件都从 index.ts 统一导出", () => {
    const index = sources["./index.ts"];
    expect(index).toBeTruthy();
    const modules = Object.keys(sources)
      .filter((path) => isUiModule(path) && path.endsWith(".tsx") && !isTest(path))
      .map((path) => path.slice(2, -4));
    const missing = modules.filter((name) => !index.includes(`export * from "./${name}"`));
    expect(missing).toEqual([]);
  });
});
