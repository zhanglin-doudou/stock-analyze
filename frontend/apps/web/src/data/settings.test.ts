import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsOut } from "@sa/api-client";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  authed: vi.fn(() => true),
}));

vi.mock("../client", () => ({
  api: { settings: { get: mocks.get, patch: mocks.patch } },
}));

vi.mock("../store/session", () => ({
  isAuthenticated: mocks.authed,
}));

function makeSettings(overrides: Partial<SettingsOut> & { updated_at: string | null }): SettingsOut {
  return {
    version: 2,
    appearance: { theme: "light", density: "cozy", trend: "red-up" },
    bill_flags: {
      near_equal_tolerance_pct: "1.00%",
      chase_margin_pct: "1.00%",
      stop_loss_margin_pct: "1.00%",
      short_term_margin_pct: "5.00%",
      window_trading_days: "5",
      flags_hash: "c425bc5b871f59f5",
      is_default: true,
    },
    market_sessions: {
      sessions: [{ open: "09:30", close: "11:30" }],
      data_ready_after: "16:30",
      session_notice_enabled: true,
      timezone: "Asia/Shanghai",
    },
    concept_filters: { mode: "off", excluded: [] },
    benchmark_id: "hs300_tr",
    privacy_flags: {},
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mocks.authed.mockReturnValue(true);
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("偏好同步（API-055/096）", () => {
  it("未登录不请求偏好接口", async () => {
    mocks.authed.mockReturnValue(false);
    const { fetchSettings, saveSettings } = await import("./settings");

    expect(await fetchSettings()).toBeNull();
    expect(await saveSettings({ appearance: { theme: "dark" } })).toBeNull();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it("服务端无行时保留本地外观并播种到云端", async () => {
    window.localStorage.setItem("sa.density", "compact");
    mocks.get.mockResolvedValue({
      settings: makeSettings({ updated_at: null }),
      etag: 'W/"1"',
      notModified: false,
    });
    mocks.patch.mockResolvedValue({
      settings: makeSettings({ updated_at: "2026-11-13T00:00:00Z", version: 2 }),
      etag: 'W/"2"',
    });
    const { syncAppearance } = await import("./settings");

    const preferences = await syncAppearance();

    expect(preferences?.density).toBe("compact");
    expect(mocks.patch).toHaveBeenCalledWith(
      { appearance: { theme: "light", density: "compact", trend: "red-up" } },
      expect.objectContaining({ version: 2 }),
    );
    expect(window.localStorage.getItem("sa.density")).toBe("compact");
  });

  it("服务端已保存外观时覆盖本地（首屏快路径不阻塞）", async () => {
    window.localStorage.setItem("sa.theme", "light");
    mocks.get.mockResolvedValue({
      settings: makeSettings({
        updated_at: "2026-11-13T00:00:00Z",
        appearance: { theme: "dark", density: "compact", trend: "green-up" },
      }),
      etag: 'W/"2"',
      notModified: false,
    });
    const { syncAppearance } = await import("./settings");

    const preferences = await syncAppearance();

    expect(preferences).toEqual({ theme: "dark", density: "compact", trend: "green-up" });
    expect(window.localStorage.getItem("sa.theme")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(mocks.patch).not.toHaveBeenCalled();
  });
});
