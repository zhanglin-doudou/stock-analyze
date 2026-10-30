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
  items: NavItem[];
}

/**
 * Phase 1 · 导航信息架构（D8：9 入口合并为 5 组，占位项不隐藏）。
 * 顺序即侧栏顺序；设置固定在底部单独渲染。
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview",
    label: "概览",
    items: [{ to: "/dashboard", label: "看板", icon: "grid" }],
  },
  {
    id: "trading",
    label: "交易",
    items: [
      { to: "/trades", label: "交易流水", icon: "trades" },
      { to: "/rounds", label: "回合", icon: "rounds" },
    ],
  },
  {
    id: "analysis",
    label: "分析",
    items: [
      { to: "/nav", label: "净值与收益", icon: "nav" },
      { to: "/metrics", label: "指标", icon: "metrics" },
      { to: "/attribution", label: "归因", icon: "attribution", placeholder: true },
    ],
  },
  {
    id: "data",
    label: "数据",
    items: [
      { to: "/assets", label: "资产", icon: "assets" },
      { to: "/import", label: "导入账单", icon: "import" },
      { to: "/imports", label: "导入记录", icon: "history" },
      { to: "/reports", label: "月报", icon: "report", placeholder: true },
      { to: "/exports", label: "导出", icon: "export", placeholder: true },
    ],
  },
];

export const SETTINGS_ITEM: NavItem = { to: "/settings", label: "设置", icon: "settings" };

export interface PageMeta {
  title: string;
  breadcrumb: string[];
}

const STATIC_PAGES: Record<string, PageMeta> = {
  "/dashboard": { title: "看板", breadcrumb: ["概览", "看板"] },
  "/trades": { title: "交易流水", breadcrumb: ["交易", "交易流水"] },
  "/rounds": { title: "回合", breadcrumb: ["交易", "回合"] },
  "/nav": { title: "净值与收益", breadcrumb: ["分析", "净值与收益"] },
  "/metrics": { title: "指标", breadcrumb: ["分析", "指标"] },
  "/attribution": { title: "归因", breadcrumb: ["分析", "归因"] },
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
    return { title: "回合详情", breadcrumb: ["交易", "回合", "详情"] };
  }
  if (pathname.startsWith("/metrics/")) {
    return { title: "指标详情", breadcrumb: ["分析", "指标", "详情"] };
  }
  return { title: "交易复盘", breadcrumb: ["交易复盘"] };
}
