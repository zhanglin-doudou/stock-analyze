import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

/**
 * 全局 Tailwind 配置（2026-11-19 UI 全局化，取代 `/import` 限定域 H-1）。
 *
 * 三条硬约束：
 * - `prefix:"tw-"`：与既有手写类（`.table`/`.card`/`.flex`…）隔离，页面可混用；
 * - `preflight:false`：不接管全站排版；组件所需的 border/表单 reset 见
 *   `src/styles/tailwind.css` 的 `[class*="tw-"]` 作用域；
 * - 颜色/圆角/字体/阴影/层级只映射 `src/styles/tokens.css` 语义变量，禁止新增私有色值。
 *
 * 新增 token 时同步 `tokens.css`、本文件与 `docs/architecture/2026-11-19-frontend-ui-globalization.md`。
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  prefix: "tw-",
  darkMode: ["selector", '[data-theme="dark"]'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        /* shadcn 语义别名（全部指向 tokens.css，不产生新色值） */
        background: "var(--bg)",
        foreground: "var(--text)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        "surface-raised": "var(--surface-raised)",
        scrim: "var(--scrim)",
        card: "var(--surface-raised)",
        "card-foreground": "var(--text)",
        popover: "var(--surface-raised)",
        "popover-foreground": "var(--text)",
        primary: "var(--accent)",
        "primary-foreground": "var(--accent-contrast)",
        secondary: "var(--surface-2)",
        "secondary-foreground": "var(--text-2)",
        muted: "var(--surface-2)",
        "muted-foreground": "var(--muted)",
        faint: "var(--faint)",
        accent: "var(--accent-soft)",
        "accent-foreground": "var(--text)",
        destructive: "var(--danger)",
        "destructive-foreground": "var(--accent-contrast)",
        border: "var(--line)",
        "border-strong": "var(--line-strong)",
        input: "var(--line-strong)",
        ring: "var(--accent)",

        /* 项目语义：涨跌 / 状态 / 表格（与手写 CSS 共用同一组 token） */
        up: "var(--up)",
        "up-soft": "var(--up-soft)",
        down: "var(--down)",
        "down-soft": "var(--down-soft)",
        flat: "var(--flat)",
        warn: "var(--warn)",
        "warn-soft": "var(--warn-soft)",
        danger: "var(--danger)",
        "danger-soft": "var(--danger-soft)",
        info: "var(--info)",
        "info-soft": "var(--info-soft)",
        ok: "var(--status-ok)",
        "ok-soft": "var(--status-ok-soft)",
        degraded: "var(--status-degraded)",
        "degraded-soft": "var(--status-degraded-soft)",
        blocked: "var(--status-blocked)",
        "blocked-soft": "var(--status-blocked-soft)",
        missing: "var(--status-missing)",
        "missing-soft": "var(--status-missing-soft)",
        "table-line": "var(--table-line)",
        "table-head": "var(--table-head-bg)",
        "table-stripe": "var(--table-stripe-bg)",
        "table-hover": "var(--table-hover-bg)",
      },
      borderRadius: {
        DEFAULT: "var(--r-sm)",
        card: "var(--r-card)",
        sm: "var(--r-sm)",
        md: "var(--r-md)",
        lg: "var(--r-lg)",
        pill: "var(--r-pill)",
      },
      boxShadow: { 1: "var(--shadow-1)", 2: "var(--shadow-2)" },
      fontFamily: {
        ui: "var(--font-ui)",
        display: "var(--font-display)",
        mono: "var(--font-mono)",
      },
      fontSize: {
        /* 语义字号：与 tokens.css 的 --fs-* 一一对应；常用默认档位保留 Tailwind 数值。 */
        micro: ["var(--fs-micro)", { lineHeight: "var(--lh-body)" }],
        body: ["var(--fs-body)", { lineHeight: "var(--lh-body)" }],
        small: ["var(--fs-sm)", { lineHeight: "var(--lh-body)" }],
        "h1": ["var(--fs-h1)", { lineHeight: "var(--lh-tight)" }],
        "h2": ["var(--fs-h2)", { lineHeight: "var(--lh-tight)" }],
        "h3": ["var(--fs-h3)", { lineHeight: "var(--lh-tight)" }],
        display: ["var(--fs-display)", { lineHeight: "var(--lh-tight)" }],
      },
      lineHeight: {
        tight: "var(--lh-tight)",
        body: "var(--lh-body)",
      },
      height: {
        control: "var(--control-h)",
        topbar: "var(--topbar-h)",
      },
      width: {
        control: "var(--control-h)",
      },
      minHeight: { touch: "var(--touch-min)" },
      zIndex: {
        rail: "var(--z-rail)",
        topbar: "var(--z-topbar)",
        drawer: "var(--z-drawer)",
        dialog: "var(--z-dialog)",
        command: "var(--z-command)",
        toast: "var(--z-toast)",
      },
      transitionDuration: { ui: "var(--dur)" },
      ringOffsetColor: {
        background: "var(--bg)",
        surface: "var(--surface)",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
