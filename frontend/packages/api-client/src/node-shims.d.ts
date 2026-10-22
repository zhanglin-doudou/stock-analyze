/**
 * 最小 Node 内置模块声明（仅供 `contract.test.ts` 读取仓库内契约快照）。
 *
 * 本包 tsconfig 故意使用 `types: []`（不把 Node 全局/`@types/node` 带进浏览器构建），
 * 因此这里只声明测试实际用到的两个函数，避免为一个测试文件引入整套 Node 类型。
 */
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}

declare module "node:url" {
  export function fileURLToPath(url: string | URL): string;
}
