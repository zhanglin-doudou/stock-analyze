import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_PREFERENCES,
  DENSITY_STORAGE_KEY,
  THEME_STORAGE_KEY,
  TREND_STORAGE_KEY,
  applyPreferences,
  initPreferences,
  loadPreferences,
  savePreferences,
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

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-trend");
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
});
