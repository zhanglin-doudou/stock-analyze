/**
 * UI 组件统一出口（公共面：测试/文档/整套消费）。
 *
 * 业务页面按需从 `@/components/ui/<component>` 导入，不要从这里 barrel 导入：
 * 包装组件在模块顶层调用 `React.forwardRef`，barrel 会让 Rollup 把整套 Radix
 * 打进当前页面 chunk（实测 `/import` +11KB gzip）。
 *
 * 页面不得直接依赖 `@radix-ui/*`；新增 Radix 组件先在本目录完成 tokens 化包装、
 * 导出到本文件，再允许业务使用。
 */
export * from "./alert-dialog";
export * from "./badge";
export * from "./button";
export * from "./card";
export * from "./checkbox";
export * from "./dialog";
export * from "./dropdown-menu";
export * from "./input";
export * from "./label";
export * from "./popover";
export * from "./portal";
export * from "./select";
export * from "./switch";
export * from "./table";
export * from "./tabs";
export * from "./tooltip";
