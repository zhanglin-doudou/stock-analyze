import type { ReactElement, ReactNode } from "react";

/**
 * Phase 1b 自研线性图标集（不引图标库）。
 *
 * 约定（菜单图标唯一性）：
 * - 每个导航图标必须形状唯一，禁止「分组图标 = 某个二级项图标」这类复用；
 *   唯一性由 `layout/navigation.test.ts` 在 vitest 中兜底（同名/同形都会失败）。
 * - 统一 24×24 viewBox、1.5px 描边、currentColor；小尺寸（15px）下必须可辨。
 * - 新增图标请放在对应语义分组内，并先确认不与已有路径重复。
 */
export type IconName =
  // 一级分组
  | "core"
  | "pulse"
  | "database"
  // 核心组
  | "grid"
  | "insights"
  | "attribution"
  // 分析组
  | "rounds"
  | "nav"
  | "metrics"
  | "sectors"
  // 数据组
  | "assets"
  | "trades"
  | "import"
  | "history"
  | "report"
  | "export"
  // 外壳
  | "settings"
  | "search"
  | "logout"
  | "chevron"
  | "focus"
  | "theme"
  | "dot";

const PATHS: Record<IconName, ReactNode> = {
  /* ── 一级分组 ─────────────────────────────── */
  // 核心：四角星（“关键/核心”语义，与看板的田字格明确区分）
  core: <path d="M12 3l2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z" />,
  // 分析：心电图脉冲（区别于“净值”的阶梯线、“指标”的仪表盘）
  pulse: <path d="M3 12h3.2l2.3-6 4.2 12 2.3-6H21" />,
  // 数据：数据库圆柱（区别于“资产”的钱包、“导入记录”的档案盒）
  database: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
      <path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" />
    </>
  ),

  /* ── 核心组 ───────────────────────────────── */
  // 看板：四格布局
  grid: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
    </>
  ),
  // 回合洞察：灯泡（洞察语义）
  insights: (
    <>
      <circle cx="12" cy="9.6" r="4.8" />
      <path d="M9.9 13.8v3.1h4.2v-3.1" />
      <path d="M10.6 18.6h2.8" />
    </>
  ),
  // 归因：饼图切出一块（收益拆成多个因子的“构成”语义；区别于“指标”的仪表盘半圆）
  attribution: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 12V3.5" />
      <path d="M12 12l8 3.6" />
    </>
  ),

  /* ── 分析组 ───────────────────────────────── */
  // 回合：循环箭头
  rounds: (
    <>
      <path d="M20 8a8 8 0 1 0 1 6" />
      <path d="M20 3v5h-5" />
    </>
  ),
  // 净值与收益：上升阶梯线（区别于“分析”的脉冲线）
  nav: <path d="M3 18h4.5v-4.5H12V9h4.5V4.5H21" />,
  // 指标：仪表盘（半圆 + 指针）
  metrics: (
    <>
      <path d="M4 17a8 8 0 0 1 16 0" />
      <path d="M12 17l4.2-5.4" />
      <circle cx="12" cy="17" r="1.3" />
    </>
  ),
  // 板块：不对称三格（一块大 + 两块小，区别于看板的等分四宫格）
  sectors: (
    <>
      <rect x="3" y="4" width="9.5" height="16" rx="1.6" />
      <rect x="15" y="4" width="6" height="7.2" rx="1.6" />
      <rect x="15" y="13.8" width="6" height="6.2" rx="1.6" />
    </>
  ),

  /* ── 数据组 ───────────────────────────────── */
  // 资产：钱包（含搭扣，区别于“数据”的圆柱）
  assets: (
    <>
      <path d="M5 7.5V6a2 2 0 0 1 2-2h8.5" />
      <rect x="3" y="7.5" width="18" height="12.5" rx="2.5" />
      <circle cx="16.6" cy="13.8" r="1.2" fill="currentColor" stroke="none" />
    </>
  ),
  // 交易流水：双向箭头（买/卖双向）
  trades: (
    <>
      <path d="M3.5 9h13" />
      <path d="M13.5 6l3 3-3 3" />
      <path d="M20.5 15H7.5" />
      <path d="M10.5 12l-3 3 3 3" />
    </>
  ),
  // 导入账单：向上箭头 + 托盘（上传）
  import: (
    <>
      <path d="M12 15V4" />
      <path d="M7 9l5-5 5 5" />
      <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </>
  ),
  // 导入记录：档案盒（区别于“回合”的循环箭头）
  history: (
    <>
      <rect x="3" y="4" width="18" height="4.6" rx="1.5" />
      <path d="M4.8 8.6V19a1 1 0 0 0 1 1h12.4a1 1 0 0 0 1-1V8.6" />
      <path d="M9.8 12.8h4.4" />
    </>
  ),
  // 月报：文档 + 折角 + 正文行
  report: (
    <>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M15 3v4h4" />
      <path d="M9 13h6M9 17h4" />
    </>
  ),
  // 导出：外送箭头（箭头离开方框，与“导入”的托盘上传不撞形）
  export: (
    <>
      <path d="M13 4h7v7" />
      <path d="M20 4l-8.5 8.5" />
      <path d="M17 13.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h5.5" />
    </>
  ),

  /* ── 外壳（顶栏/底部/交互） ───────────────── */
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M16 16l4 4" />
    </>
  ),
  logout: (
    <>
      <path d="M14 5h4a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-4" />
      <path d="M10 12H3" />
      <path d="M7 8l-4 4 4 4" />
    </>
  ),
  chevron: <path d="M9 6l6 6-6 6" />,
  focus: (
    <>
      <path d="M4 9V5a1 1 0 0 1 1-1h4" />
      <path d="M20 9V5a1 1 0 0 0-1-1h-4" />
      <path d="M4 15v4a1 1 0 0 0 1 1h4" />
      <path d="M20 15v4a1 1 0 0 1-1 1h-4" />
    </>
  ),
  theme: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none" />
    </>
  ),
  dot: <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />,
};

/** 全部图标名（由 PATHS 派生，供唯一性测试遍历；勿手写第二份清单）。 */
export const ICON_NAMES = Object.keys(PATHS) as IconName[];

export interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
}

/** 自研图标：线性 1.5px，统一 currentColor；不引入图标库。 */
export function Icon({ name, size = 19, className }: IconProps): ReactElement {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
