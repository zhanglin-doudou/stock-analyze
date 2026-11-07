import type { IconName } from "../ui/icons";

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** 二期未完成功能的占位入口：可见、可测试、文案标注“占位”。 */
  placeholder?: boolean;
}

export interface NavGroup {
  id: string;
  label: string;
  icon: IconName;
  items: NavItem[];
}

/**
 * 两级导航信息架构：一级分组（核心/分析/数据）+ 二级页面。
 * 顺序即侧栏顺序；设置固定在底部单独渲染。
 *
 * 图标约定：分组图标与任何二级图标**不得同名、不得同形**
 * （例如分组「核心」不能复用「看板」的 grid）；唯一性由 navigation.test.ts 兜底。
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "core",
    label: "核心",
    icon: "core",
    items: [
      { to: "/dashboard", label: "看板", icon: "grid" },
      { to: "/rounds/insights", label: "回合洞察", icon: "insights" },
      { to: "/attribution", label: "归因", icon: "attribution" },
    ],
  },
  {
    id: "analysis",
    label: "分析",
    icon: "pulse",
    items: [
      { to: "/rounds", label: "回合", icon: "rounds" },
      { to: "/nav", label: "净值与收益", icon: "nav" },
      { to: "/metrics", label: "指标", icon: "metrics" },
      { to: "/sectors", label: "板块", icon: "sectors" },
    ],
  },
  {
    id: "data",
    label: "数据",
    icon: "database",
    items: [
      { to: "/assets", label: "资产", icon: "assets" },
      { to: "/trades", label: "交易流水", icon: "trades" },
      { to: "/import", label: "导入账单", icon: "import" },
      { to: "/imports", label: "导入记录", icon: "history" },
      { to: "/reports", label: "月报", icon: "report", placeholder: true },
      { to: "/exports", label: "导出", icon: "export", placeholder: true },
    ],
  },
];

export const SETTINGS_ITEM: NavItem = { to: "/settings", label: "设置", icon: "settings" };

/**
 * 菜单激活匹配：
 * - `/rounds/insights` 是独立页面，不能被 `/rounds` 前缀吃掉；
 * - `/rounds/:id` 仍归属「回合」，`/metrics/:id` 与 `/metrics/catalog` 归属「指标」；
 * - `/import` 与 `/imports`、`/nav` 等按精确匹配。
 */
export function isNavItemActive(pathname: string, to: string): boolean {
  if (pathname === to) return true;
  if (to === "/rounds") {
    return pathname.startsWith("/rounds/") && pathname !== "/rounds/insights";
  }
  if (to === "/metrics") {
    return pathname.startsWith("/metrics/");
  }
  return false;
}

/** 当前路径所属的一级菜单；没有匹配返回 undefined。 */
export function activeGroupId(pathname: string): string | undefined {
  return NAV_GROUPS.find((group) =>
    group.items.some((item) => isNavItemActive(pathname, item.to)),
  )?.id;
}

export interface PageMeta {
  title: string;
  breadcrumb: string[];
}

const STATIC_PAGES: Record<string, PageMeta> = {
  "/dashboard": { title: "看板", breadcrumb: ["核心", "看板"] },
  "/trades": { title: "交易流水", breadcrumb: ["数据", "交易流水"] },
  "/rounds": { title: "回合", breadcrumb: ["分析", "回合"] },
  // 静态路由必须能被精确命中：否则会被 `/rounds/` 前缀规则误判为“回合详情”。
  "/rounds/insights": { title: "回合洞察", breadcrumb: ["核心", "回合洞察"] },
  "/nav": { title: "净值与收益", breadcrumb: ["分析", "净值与收益"] },
  "/metrics": { title: "指标", breadcrumb: ["分析", "指标"] },
  "/sectors": { title: "板块分析", breadcrumb: ["分析", "板块分析"] },
  "/attribution": { title: "归因", breadcrumb: ["核心", "归因"] },
  "/assets": { title: "资产", breadcrumb: ["数据", "资产"] },
  "/import": { title: "导入账单", breadcrumb: ["数据", "导入账单"] },
  "/imports": { title: "导入记录", breadcrumb: ["数据", "导入记录"] },
  "/reports": { title: "月报", breadcrumb: ["数据", "月报"] },
  "/exports": { title: "导出", breadcrumb: ["数据", "导出"] },
  "/settings": { title: "设置", breadcrumb: ["设置"] },
};

/** 由路径推导页面标题/面包屑；详情页按前缀匹配。 */
export function pageMeta(pathname: string): PageMeta {
  if (STATIC_PAGES[pathname]) return STATIC_PAGES[pathname];
  if (pathname.startsWith("/rounds/")) {
    return { title: "回合详情", breadcrumb: ["分析", "回合", "详情"] };
  }
  if (pathname.startsWith("/metrics/")) {
    return { title: "指标详情", breadcrumb: ["分析", "指标", "详情"] };
  }
  return { title: "交易复盘", breadcrumb: ["交易复盘"] };
}
