import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

/**
 * 限定范围 Tailwind（仅 `/import` 子域，H-1 已批准）：
 * - `preflight:false`：不重置全局样式；
 * - `prefix:"tw-"`：避免与既有手写 CSS 的 `.table` 等通用类名冲突；
 * - 颜色全部映射到 `tokens.css` 语义变量，不新增色值。
 */
export default {
  content: [
    "./src/features/import/**/*.{ts,tsx}",
    "./src/components/ui/**/*.{ts,tsx}",
    "./src/pages/Import.tsx",
  ],
  prefix: "tw-",
  darkMode: ["selector", '[data-theme="dark"]'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        background: "var(--bg)",
        foreground: "var(--text)",
        surface: "var(--surface)",
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
        accent: "var(--accent-soft)",
        "accent-foreground": "var(--text)",
        destructive: "var(--danger)",
        "destructive-foreground": "var(--accent-contrast)",
        border: "var(--line)",
        input: "var(--line-strong)",
        ring: "var(--accent)",
      },
      borderRadius: {
        card: "var(--r-card)",
        lg: "var(--r-lg)",
        md: "var(--r-md)",
        sm: "var(--r-sm)",
      },
      /* 站内已有 `--shadow-1/2`；不补映射时 `tw-shadow-1/2` 不会生成任何声明。 */
      boxShadow: { 1: "var(--shadow-1)", 2: "var(--shadow-2)" },
      fontFamily: {
        ui: "var(--font-ui)",
        display: "var(--font-display)",
        mono: "var(--font-mono)",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
