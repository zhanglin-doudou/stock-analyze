import type { ReactElement, ReactNode } from "react";

export type IconName =
  | "grid"
  | "trades"
  | "rounds"
  | "nav"
  | "assets"
  | "metrics"
  | "import"
  | "history"
  | "report"
  | "export"
  | "attribution"
  | "settings"
  | "search"
  | "logout"
  | "chevron"
  | "focus"
  | "dot";

const PATHS: Record<IconName, ReactNode> = {
  grid: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
    </>
  ),
  trades: <path d="M4 7h16M4 12h16M4 17h10" />,
  rounds: (
    <>
      <path d="M20 8a8 8 0 1 0 1 6" />
      <path d="M20 3v5h-5" />
    </>
  ),
  nav: (
    <>
      <path d="M4 18V6" />
      <path d="M4 18h16" />
      <path d="M7 14l4-4 3 3 5-6" />
    </>
  ),
  assets: (
    <>
      <rect x="3" y="6" width="18" height="13" rx="2" />
      <path d="M3 10h18" />
      <circle cx="17" cy="14.5" r="1" />
    </>
  ),
  metrics: (
    <>
      <path d="M4 15l4-5 3 3 5-7 4 5" />
      <path d="M4 20h16" />
    </>
  ),
  import: (
    <>
      <path d="M12 15V4" />
      <path d="M7 9l5-5 5 5" />
      <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </>
  ),
  history: (
    <>
      <path d="M3 12a9 9 0 1 0 3-6.7" />
      <path d="M3 4v5h5" />
      <path d="M12 8v4l3 2" />
    </>
  ),
  report: (
    <>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h4" />
    </>
  ),
  export: (
    <>
      <path d="M12 4v11" />
      <path d="M8 11l4 4 4-4" />
      <path d="M5 18v2h14v-2" />
    </>
  ),
  attribution: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4v8l6 4" />
    </>
  ),
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
  dot: <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />,
};

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
