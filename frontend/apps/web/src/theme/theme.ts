/**
 * Phase 0 · 主题/密度/趋势色偏好的唯一读写入口。
 *
 * 设计（见 docs/reviews/2026-10-28-frontend-refactor/README.md §9）：
 * - 主题状态写在 <html> 的 data 属性上，CSS 只消费 tokens.css 变量；
 * - 默认 light + cozy + red-up（A 股红涨绿跌），D5 深色主题 Phase 5 再做；
 * - 本模块不依赖 React，Phase 1 的 ThemeProvider 直接复用。
 */

export const THEME_STORAGE_KEY = "sa.theme";
export const DENSITY_STORAGE_KEY = "sa.density";
export const TREND_STORAGE_KEY = "sa.trend";

export const THEMES = ["light", "dark"] as const;
export const DENSITIES = ["cozy", "compact"] as const;
export const TREND_COLORS = ["red-up", "green-up"] as const;

export type Theme = (typeof THEMES)[number];
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

/**
 * 把偏好写到根元素，供 CSS token 选择器消费。
 * 只使用 data-* 属性，不直接改 style，避免与组件样式打架。
 */
export function applyPreferences(
  preferences: Preferences,
  root: HTMLElement = document.documentElement,
): void {
  root.dataset.theme = preferences.theme;
  root.dataset.density = preferences.density;
  root.dataset.trend = preferences.trend;
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
