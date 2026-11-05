/**
 * Phase 0 · 主题/密度/趋势色偏好的唯一读写入口。
 *
 * 设计（见 docs/reviews/2026-10-28-frontend-refactor/README.md §9）：
 * - 主题状态写在 <html> 的 data 属性上，CSS 只消费 tokens.css 变量；
 * - 主题支持 light / dark / system；system 在 TS 层用 matchMedia 解析后写入
 *   实际的 light|dark，CSS 仍只保留两态，不新增 prefers-color-scheme 分支；
 * - 默认 light + cozy + red-up（A 股红涨绿跌）；
 * - 本模块不依赖 React，Phase 1 的 ThemeProvider 直接复用。
 */

export const THEME_STORAGE_KEY = "sa.theme";
export const DENSITY_STORAGE_KEY = "sa.density";
export const TREND_STORAGE_KEY = "sa.trend";

export const THEMES = ["light", "dark", "system"] as const;
export const DENSITIES = ["cozy", "compact"] as const;
export const TREND_COLORS = ["red-up", "green-up"] as const;

export type Theme = (typeof THEMES)[number];
export type ResolvedTheme = "light" | "dark";
export type Density = (typeof DENSITIES)[number];
export type TrendColor = (typeof TREND_COLORS)[number];

export interface Preferences {
  theme: Theme;
  density: Density;
  trend: TrendColor;
}

export const DEFAULT_PREFERENCES: Preferences = {
  theme: "light",
  density: "cozy",
  trend: "red-up",
};

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem">;
type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

function readOne<T extends string>(
  storage: ReadableStorage,
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const raw = storage.getItem(key);
  return allowed.includes(raw as T) ? (raw as T) : fallback;
}

/** 从 localStorage 读取偏好；任何非法值回落到默认值，不抛错。 */
export function loadPreferences(storage: ReadableStorage = window.localStorage): Preferences {
  return {
    theme: readOne(storage, THEME_STORAGE_KEY, THEMES, DEFAULT_PREFERENCES.theme),
    density: readOne(storage, DENSITY_STORAGE_KEY, DENSITIES, DEFAULT_PREFERENCES.density),
    trend: readOne(storage, TREND_STORAGE_KEY, TREND_COLORS, DEFAULT_PREFERENCES.trend),
  };
}

/** 持久化偏好；存储不可用（隐私模式）时静默跳过。 */
export function savePreferences(
  preferences: Preferences,
  storage: WritableStorage = window.localStorage,
): void {
  try {
    storage.setItem(THEME_STORAGE_KEY, preferences.theme);
    storage.setItem(DENSITY_STORAGE_KEY, preferences.density);
    storage.setItem(TREND_STORAGE_KEY, preferences.trend);
  } catch {
    // 本地工具允许隐私模式下不持久化；不得因此阻断页面。
  }
}

const THEME_COLORS: Record<ResolvedTheme, string> = {
  light: "#f6f5f0",
  dark: "#0b0e12",
};

/** 当前系统是否偏好深色；无 matchMedia（测试环境）时按浅色处理。 */
export function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** 把 light|dark|system 解析成实际主题；system 依赖系统偏好。 */
export function resolveTheme(theme: Theme, prefersDark = systemPrefersDark()): ResolvedTheme {
  if (theme === "system") {
    return prefersDark ? "dark" : "light";
  }
  return theme;
}

function syncThemeColor(resolved: ResolvedTheme, root: HTMLElement): void {
  const meta = root.ownerDocument.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  meta?.setAttribute("content", THEME_COLORS[resolved]);
}

/**
 * 把偏好写到根元素，供 CSS token 选择器消费。
 * 只使用 data-* 属性，不直接改 style，避免与组件样式打架。
 * data-theme 始终写解析后的 light|dark，保证 CSS 只有两态。
 */
export function applyPreferences(
  preferences: Preferences,
  root: HTMLElement = document.documentElement,
): void {
  const resolved = resolveTheme(preferences.theme);
  root.dataset.theme = resolved;
  root.dataset.density = preferences.density;
  root.dataset.trend = preferences.trend;
  syncThemeColor(resolved, root);
}

/** 应用并持久化（设置页切换入口用）。 */
export function updatePreferences(
  patch: Partial<Preferences>,
  storage: PreferenceStorage = window.localStorage,
  root: HTMLElement = document.documentElement,
): Preferences {
  const next: Preferences = { ...loadPreferences(storage), ...patch };
  applyPreferences(next, root);
  savePreferences(next, storage);
  return next;
}

/** 页面启动时调用：读存储 → 写根属性。默认值也会显式写入，便于 CSS 断言。 */
export function initPreferences(
  storage: ReadableStorage = window.localStorage,
  root: HTMLElement = document.documentElement,
): Preferences {
  const preferences = loadPreferences(storage);
  applyPreferences(preferences, root);
  return preferences;
}
