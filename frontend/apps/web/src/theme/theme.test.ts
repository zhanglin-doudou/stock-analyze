import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import indexHtml from "../../index.html?raw";
import {
  DEFAULT_PREFERENCES,
  DENSITY_STORAGE_KEY,
  THEME_STORAGE_KEY,
  TREND_STORAGE_KEY,
  applyPreferences,
  initPreferences,
  loadPreferences,
  resolveTheme,
  savePreferences,
  systemPrefersDark,
  updatePreferences,
} from "./theme";

function makeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => Array.from(map.keys())[index] ?? null,
    removeItem: (key: string) => void map.delete(key),
    setItem: (key: string, value: string) => void map.set(key, value),
  };
}

function stubSystemDark(matches: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-trend");
  document.head.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.remove());
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.remove());
});

describe("theme 偏好", () => {
  it("无存储时返回默认值", () => {
    expect(loadPreferences(makeStorage())).toEqual(DEFAULT_PREFERENCES);
  });

  it("读取已保存的合法值", () => {
    const storage = makeStorage();
    storage.setItem(THEME_STORAGE_KEY, "dark");
    storage.setItem(DENSITY_STORAGE_KEY, "compact");
    storage.setItem(TREND_STORAGE_KEY, "green-up");
    expect(loadPreferences(storage)).toEqual({
      theme: "dark",
      density: "compact",
      trend: "green-up",
    });
  });

  it("非法值回落到默认值，不抛错", () => {
    const storage = makeStorage();
    storage.setItem(THEME_STORAGE_KEY, "solarized");
    storage.setItem(DENSITY_STORAGE_KEY, "dense");
    storage.setItem(TREND_STORAGE_KEY, "rainbow");
    expect(loadPreferences(storage)).toEqual(DEFAULT_PREFERENCES);
  });

  it("applyPreferences 写到根元素 data-* 属性", () => {
    applyPreferences({ theme: "light", density: "compact", trend: "red-up" });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.dataset.density).toBe("compact");
    expect(document.documentElement.dataset.trend).toBe("red-up");
  });

  it("savePreferences 持久化三个键", () => {
    const storage = makeStorage();
    savePreferences({ theme: "dark", density: "cozy", trend: "green-up" }, storage);
    expect(storage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(storage.getItem(DENSITY_STORAGE_KEY)).toBe("cozy");
    expect(storage.getItem(TREND_STORAGE_KEY)).toBe("green-up");
  });

  it("updatePreferences 局部更新并持久化", () => {
    const storage = makeStorage();
    savePreferences({ theme: "light", density: "cozy", trend: "red-up" }, storage);
    const next = updatePreferences({ density: "compact" }, storage);
    expect(next).toEqual({ theme: "light", density: "compact", trend: "red-up" });
    expect(loadPreferences(storage).density).toBe("compact");
    expect(document.documentElement.dataset.density).toBe("compact");
  });

  it("initPreferences 默认值也显式写入根属性", () => {
    const storage = makeStorage();
    const preferences = initPreferences(storage);
    expect(preferences).toEqual(DEFAULT_PREFERENCES);
    expect(document.documentElement.dataset.trend).toBe("red-up");
  });

  it("resolveTheme 把 system 解析为实际 light|dark", () => {
    expect(resolveTheme("light")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });

  it("system 通过 matchMedia 读取系统深色偏好", () => {
    stubSystemDark(true);
    expect(systemPrefersDark()).toBe(true);
    expect(resolveTheme("system")).toBe("dark");
    stubSystemDark(false);
    expect(systemPrefersDark()).toBe(false);
    expect(resolveTheme("system")).toBe("light");
  });

  it("无 matchMedia 时 system 回退浅色，不抛错", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(systemPrefersDark()).toBe(false);
    expect(resolveTheme("system")).toBe("light");
  });

  it("system 偏好持久化为 system，根属性与 theme-color 写解析后的值", () => {
    const storage = makeStorage();
    storage.setItem(THEME_STORAGE_KEY, "system");
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = "#f6f5f0";
    document.head.append(meta);
    stubSystemDark(true);

    const preferences = initPreferences(storage);
    expect(preferences.theme).toBe("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(meta.getAttribute("content")).toBe("#0b0e12");

    const next = updatePreferences({ theme: "system" }, storage);
    expect(next.theme).toBe("system");
    expect(storage.getItem(THEME_STORAGE_KEY)).toBe("system");
  });

  it("index.html 在模块脚本前预置主题，避免首屏闪烁", () => {
    const boot = indexHtml.indexOf("sa.theme");
    const moduleScript = indexHtml.indexOf('type="module"');
    expect(boot).toBeGreaterThan(-1);
    expect(boot).toBeLessThan(moduleScript);
    expect(indexHtml).toContain("prefers-color-scheme: dark");
    expect(indexHtml).toContain('meta[name="theme-color"]');
  });
});
