import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RoundInsights } from "./RoundInsights";

/**
 * StrictMode 回归锁：React 18 开发态会把 effect 挂载执行两次
 * （commitDoubleInvokeEffectsInDEV），页面用 useEffect 拉数据时曾出现两次网络请求。
 * 这里用「真实 api-client + 假 fetch」渲染，断言同一参数只落一次网络请求 ——
 * 合并发生在 api-client 的在途 GET 合并层，而不是页面里的 ref 守卫。
 */
const net = vi.hoisted(() => ({ urls: [] as string[] }));

const payload = vi.hoisted(() => ({
  period: "2026",
  period_basis: "close_date",
  definition_version: "v1",
  bucket_basis: "round",
  data_as_of: "2026-12-31",
  ranking_as_of: "2026-12-31",
  benchmark_id: "hs300_tr",
  basis: "benchmark_excess_20d",
  item_total: 0,
  items: [],
  summary: null,
  next_cursor: null,
  has_more: false,
  missing: [],
  assumptions: [],
  disclaimer: "以下为事后统计，仅用于个人复盘，不构成投资建议。",
}));

vi.mock("../client", async () => {
  const { createClient } = await import("@sa/api-client");
  const body = JSON.stringify(payload);
  const fetchImpl = async (input: RequestInfo | URL): Promise<Response> => {
    net.urls.push(String(input));
    return {
      status: 200,
      ok: true,
      headers: { get: () => "application/json" },
      text: async () => body,
    } as unknown as Response;
  };
  return { api: createClient({ baseUrl: "/api/v1", fetchImpl: fetchImpl as typeof fetch }) };
});

afterEach(() => {
  cleanup();
  net.urls.length = 0;
});

describe("RoundInsights · StrictMode 下的网络请求", () => {
  it("同一参数只请求一次 /rounds/insights", async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/rounds/insights?period=2026"]}>
          <RoundInsights />
        </MemoryRouter>
      </StrictMode>,
    );

    await waitFor(() => expect(net.urls.length).toBeGreaterThan(0));
    expect(await screen.findByText(/本月还没有可统计的回合/)).toBeTruthy();

    // 再等一拍，确认 StrictMode 的第二次 effect 没有额外落一次网络请求
    await new Promise((resolve) => setTimeout(resolve, 50));

    const insightCalls = net.urls.filter((url) => url.includes("/rounds/insights"));
    expect(insightCalls).toHaveLength(1);
    expect(insightCalls[0]).toContain("period=2026");
  });
});
