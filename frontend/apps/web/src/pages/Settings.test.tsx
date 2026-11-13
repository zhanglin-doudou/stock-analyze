import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PatToken, SectorCatalogPage, SettingsOut } from "@sa/api-client";
import { Settings } from "./Settings";
import { api } from "../client";
import { clearSession, saveTokens } from "../store/session";

vi.mock("../client", () => ({
  api: {
    tokens: {
      list: vi.fn(),
      create: vi.fn(),
      revoke: vi.fn(),
    },
    settings: {
      get: vi.fn(),
      patch: vi.fn(),
      rebuildTradeFlags: vi.fn(),
    },
    jobs: {
      get: vi.fn(),
    },
    sectors: {
      list: vi.fn(),
      overview: vi.fn(),
    },
  },
}));

const listTokens = vi.mocked(api.tokens.list);
const getSettings = vi.mocked(api.settings.get);
const patchSettings = vi.mocked(api.settings.patch);
const rebuildTradeFlags = vi.mocked(api.settings.rebuildTradeFlags);
const getJob = vi.mocked(api.jobs.get);
const listSectors = vi.mocked(api.sectors.list);

function makeToken(): PatToken {
  return {
    token_id: "pat_1",
    name: "本地脚本",
    prefix: "pat_abcd",
    scopes: ["read"],
    expires_at: "2026-12-31T00:00:00Z",
    created_at: "2026-10-01T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
  };
}

function makeSettings(overrides: DeepPartial<SettingsOut> = {}): SettingsOut {
  const base: SettingsOut = {
    version: 2,
    updated_at: "2026-11-13T00:00:00Z",
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
      sessions: [
        { open: "09:30", close: "11:30" },
        { open: "13:00", close: "15:00" },
      ],
      data_ready_after: "16:30",
      session_notice_enabled: true,
      timezone: "Asia/Shanghai",
    },
    concept_filters: { mode: "off", excluded: [] },
    benchmark_id: "hs300_tr",
    privacy_flags: {},
  };
  return {
    ...base,
    ...overrides,
    bill_flags: { ...base.bill_flags, ...overrides.bill_flags },
    market_sessions: { ...base.market_sessions, ...overrides.market_sessions },
    concept_filters: { ...base.concept_filters, ...overrides.concept_filters },
    appearance: { ...base.appearance, ...overrides.appearance },
  };
}

type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends object ? Partial<T[K]> : T[K];
};

function emptyCatalog(): SectorCatalogPage {
  return {
    as_of: "2026-11-13",
    taxonomy: "concept",
    catalog_version: null,
    sector_data_state: "current",
    items: [],
    next_cursor: null,
    has_more: false,
    missing: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-trend");
  listTokens.mockResolvedValue({ items: [makeToken()], total: 1 });
  listSectors.mockResolvedValue(emptyCatalog());
});

afterEach(() => {
  cleanup();
  clearSession();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-trend");
});

describe("Settings（Phase 3）", () => {
  it("展示外观偏好并可切换密度/涨跌色（立即写根属性 + localStorage）", async () => {
    render(<Settings />);

    expect(screen.getByRole("heading", { name: "设置" })).toBeTruthy();
    const densityGroup = screen.getByRole("group", { name: "表格密度" });
    expect(densityGroup).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "紧凑" }));
    expect(document.documentElement.dataset.density).toBe("compact");
    expect(window.localStorage.getItem("sa.density")).toBe("compact");

    fireEvent.click(screen.getByRole("button", { name: "绿涨红跌" }));
    expect(document.documentElement.dataset.trend).toBe("green-up");
    expect(window.localStorage.getItem("sa.trend")).toBe("green-up");

    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("sa.theme")).toBe("dark");
  });

  it("支持跟随系统：持久化 system，根属性写系统解析后的主题", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }));
    render(<Settings />);

    fireEvent.click(screen.getByRole("button", { name: "跟随系统" }));
    expect(window.localStorage.getItem("sa.theme")).toBe("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("渲染 PAT 列表并保留撤销入口", async () => {
    render(<Settings />);
    await waitFor(() => expect(listTokens).toHaveBeenCalled());
    expect(await screen.findByText("本地脚本")).toBeTruthy();
    expect(screen.getByText("pat_abcd")).toBeTruthy();
    expect(screen.getByRole("button", { name: "撤销" })).toBeTruthy();
  });

  it("未登录不请求云端配置，只渲染外观与 PAT", async () => {
    render(<Settings />);
    await waitFor(() => expect(listTokens).toHaveBeenCalled());
    expect(getSettings).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "账单旗标阈值" })).toBeNull();
  });
});

describe("Settings（偏好四组，API-055/096）", () => {
  beforeEach(() => {
    saveTokens({ access_token: "access-token" });
    getSettings.mockResolvedValue({ settings: makeSettings(), etag: 'W/"2"', notModified: false } as never);
  });

  it("登录后从云端回填四组表单", async () => {
    render(<Settings />);

    expect(await screen.findByRole("heading", { name: "账单旗标阈值" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "行情时段" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "概念板块过滤" })).toBeTruthy();
    expect((screen.getByLabelText("追高阈值") as HTMLInputElement).value).toBe("1.00%");
    expect((screen.getByLabelText("取窗交易日数") as HTMLSelectElement).value).toBe("5");
    expect(screen.getByText("当前为默认阈值")).toBeTruthy();
  });

  it("只提交脏字段（阈值）并在非默认时提示重建账单旗标", async () => {
    const next = makeSettings({
      version: 3,
      bill_flags: { chase_margin_pct: "2.00%", flags_hash: "newhash000000000", is_default: false },
    });
    patchSettings.mockResolvedValue({ settings: next, etag: 'W/"3"' } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "账单旗标阈值" });

    fireEvent.change(screen.getByLabelText("追高阈值"), { target: { value: "2.00%" } });
    fireEvent.click(screen.getByRole("button", { name: "保存阈值" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalledTimes(1));
    expect(patchSettings).toHaveBeenCalledWith(
      { bill_flags: { chase_margin_pct: "2.00%" } },
      { version: 2, idempotencyKey: expect.any(String) },
    );
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByText("需重建账单旗标")).toBeTruthy();
    expect(screen.getByText(/配置指纹 newhash000000000/)).toBeTruthy();
  });

  it("窗口用下拉框，保存时发字符串窗口值", async () => {
    patchSettings.mockResolvedValue({
      settings: makeSettings({
        version: 3,
        bill_flags: { window_trading_days: "10", is_default: false },
      }),
      etag: 'W/"3"',
    } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "账单旗标阈值" });

    fireEvent.change(screen.getByLabelText("取窗交易日数"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "保存阈值" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings).toHaveBeenCalledWith(
      { bill_flags: { window_trading_days: "10" } },
      expect.objectContaining({ version: 2 }),
    );
  });

  it("行情时段只提交改动的就绪时间，并做前端交叉校验", async () => {
    patchSettings.mockResolvedValue({
      settings: makeSettings({
        version: 3,
        market_sessions: { data_ready_after: "17:00" },
      }),
      etag: 'W/"3"',
    } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "行情时段" });

    fireEvent.change(screen.getByLabelText("盘后就绪时间"), { target: { value: "09:00" } });
    expect(screen.getByText("盘后就绪时间不能早于收盘时间")).toBeTruthy();
    expect((screen.getByRole("button", { name: "保存时段" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("盘后就绪时间"), { target: { value: "17:00" } });
    fireEvent.click(screen.getByRole("button", { name: "保存时段" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings).toHaveBeenCalledWith(
      { market_sessions: { data_ready_after: "17:00" } },
      expect.objectContaining({ version: 2 }),
    );
  });

  it("概念过滤：数组发全量，失效项标记目录中未找到", async () => {
    getSettings.mockResolvedValue({
      settings: makeSettings({
        concept_filters: {
          mode: "hide",
          excluded: [
            { code: "BK0001", name: "旧概念", resolved: false },
            { code: "BK0002", name: "在册概念", resolved: true },
          ],
        },
      }),
      etag: 'W/"2"',
      notModified: false,
    } as never);
    listSectors.mockResolvedValue({
      ...emptyCatalog(),
      items: [
        {
          sector_code: "BK0003",
          sector_name: "新概念",
          taxonomy: "concept",
          level: 1,
          parent_code: null,
          path: [],
          member_count: 10,
          source: "em",
          classification_version: "v1",
          is_active: true,
        },
      ],
    } as never);
    patchSettings.mockResolvedValue({
      settings: makeSettings({
        version: 3,
        concept_filters: {
          mode: "hide",
          excluded: [
            { code: "BK0001", name: "旧概念", resolved: false },
            { code: "BK0002", name: "在册概念", resolved: true },
            { code: "BK0003", name: "新概念", resolved: true },
          ],
        },
      }),
      etag: 'W/"3"',
    } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "概念板块过滤" });
    expect(screen.getByText("目录中未找到")).toBeTruthy();

    fireEvent.click(await screen.findByLabelText(/新概念/));
    fireEvent.click(screen.getByRole("button", { name: "保存过滤" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings).toHaveBeenCalledWith(
      {
        concept_filters: {
          excluded: [
            { code: "BK0001", name: "旧概念" },
            { code: "BK0002", name: "在册概念" },
            { code: "BK0003", name: "新概念" },
          ],
        },
      },
      expect.objectContaining({ version: 2 }),
    );
  });

  it("恢复默认用 null 让后端回填默认值", async () => {
    patchSettings.mockResolvedValue({
      settings: makeSettings({ version: 3 }),
      etag: 'W/"3"',
    } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "账单旗标阈值" });

    const resetButtons = screen.getAllByRole("button", { name: "恢复默认" });
    fireEvent.click(resetButtons[0] as HTMLButtonElement);

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings).toHaveBeenCalledWith(
      {
        bill_flags: {
          near_equal_tolerance_pct: null,
          chase_margin_pct: null,
          stop_loss_margin_pct: null,
          short_term_margin_pct: null,
          window_trading_days: null,
        },
      },
      expect.objectContaining({ version: 2 }),
    );
  });

  it("409 冲突时重取版本并重试一次", async () => {
    const { ApiProblemError } = await import("@sa/api-client");
    patchSettings.mockRejectedValueOnce(
      new ApiProblemError({
        type: "about:blank",
        title: "Conflict",
        status: 409,
        detail: "",
        code: "VERSION_CONFLICT",
        user_message: "版本冲突",
        what_to_do: "重试",
        retryable: false,
        request_id: "",
      }),
    );
    getSettings
      .mockResolvedValueOnce({ settings: makeSettings(), etag: 'W/"2"', notModified: false } as never)
      .mockResolvedValueOnce({
        settings: makeSettings({ version: 4 }),
        etag: 'W/"4"',
        notModified: false,
      } as never);
    patchSettings.mockResolvedValueOnce({
      settings: makeSettings({ version: 5, bill_flags: { chase_margin_pct: "3.00%" } }),
      etag: 'W/"5"',
    } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "账单旗标阈值" });
    fireEvent.change(screen.getByLabelText("追高阈值"), { target: { value: "3.00%" } });
    fireEvent.click(screen.getByRole("button", { name: "保存阈值" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalledTimes(2));
    expect(patchSettings).toHaveBeenLastCalledWith(
      { bill_flags: { chase_margin_pct: "3.00%" } },
      expect.objectContaining({ version: 4 }),
    );
  });

  it("「立即重算」调用 API-097 并轮询到成功后刷新设置", async () => {
    rebuildTradeFlags.mockResolvedValue({
      job_id: "job_rebuild",
      type: "rebuild_trade_flags",
      status: "pending",
      links: {
        self: "/api/v1/jobs/job_rebuild",
        events: "/api/v1/jobs/job_rebuild/events",
      },
    } as never);
    getJob.mockResolvedValue({
      job_id: "job_rebuild",
      type: "rebuild_trade_flags",
      status: "succeeded",
      progress: 100,
      stage: "done",
      error: null,
    } as never);

    render(<Settings />);
    await screen.findByRole("heading", { name: "账单旗标阈值" });

    fireEvent.click(screen.getByTestId("settings-rebuild"));

    await waitFor(() =>
      expect(rebuildTradeFlags).toHaveBeenCalledWith({ scope: "all" }, expect.any(String)),
    );
    expect(await screen.findByTestId("settings-rebuild-done")).toBeTruthy();
    await waitFor(() => expect(getSettings).toHaveBeenCalledTimes(2));
  });
});
