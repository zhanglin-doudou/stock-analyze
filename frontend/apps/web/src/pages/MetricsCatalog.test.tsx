import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { MetricCatalog, MetricCatalogItem } from "@sa/api-client";
import { MetricsCatalog } from "./MetricsCatalog";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    metrics: {
      catalog: vi.fn(),
    },
  },
}));

const catalogApi = vi.mocked(api.metrics.catalog);

function makeItem(overrides: Partial<MetricCatalogItem> = {}): MetricCatalogItem {
  return {
    metric_id: "perf.win_rate",
    display_name: "胜率",
    display_name_en: "Win rate",
    domain: "perf",
    direction: "higher_better",
    unit: "ratio",
    precision: 8,
    definition_version: "1.0",
    scope: { unit: "round", cohort: "close_date", period: "month" },
    min_n: 10,
    ci_min_n: 10,
    ci: "wilson",
    min_trading_days: 0,
    window_kind: "month",
    bands: [
      { level: "watch", label: "需关注", from: null, to: "0.40000000" },
      { level: "neutral", label: "中性", from: "0.40000000", to: "0.55000000" },
      { level: "good", label: "良好", from: "0.55000000", to: null },
    ],
    meaning: "盈利平仓回合占比越高，说明出手质量越好。",
    method: "盈利平仓回合数 ÷ 总平仓回合数。",
    what_to_do: "查看下钻里的亏损回合。",
    caveat: "胜率必须与盈亏比联读。",
    ...overrides,
  };
}

function makeCatalog(items: MetricCatalogItem[]): MetricCatalog {
  return { registry_version: "abc123", items };
}

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter>
      <MetricsCatalog />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  catalogApi.mockResolvedValue(
    makeCatalog([
      makeItem(),
      makeItem({
        metric_id: "behavior.hhi",
        display_name: "持仓集中度",
        domain: "behavior",
        direction: "lower_better",
        min_trading_days: 0,
        bands: null,
        meaning: "个股权重平方和。",
        method: "Σ(权重²)。",
      }),
    ]),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MetricsCatalog 口径说明书", () => {
  it("展示 18 指标口径：中文名/方向/门槛/经验区间/含义", async () => {
    renderPage();

    expect(await screen.findByText("胜率")).toBeTruthy();
    expect(screen.getByText("持仓集中度")).toBeTruthy();
    expect(screen.getByText("perf.win_rate")).toBeTruthy();
    expect(screen.getAllByText("高好").length).toBeGreaterThan(0);
    expect(screen.getByText("低好")).toBeTruthy();
    expect(screen.getAllByText("n≥10").length).toBeGreaterThan(0);
    expect(screen.getByText(/需关注/)).toBeTruthy();
    expect(screen.getByText("盈利平仓回合占比越高，说明出手质量越好。")).toBeTruthy();
    expect(screen.getAllByText("查看下钻里的亏损回合。").length).toBeGreaterThan(0);
    expect(screen.getAllByText("胜率必须与盈亏比联读。").length).toBeGreaterThan(0);
  });

  it("加载失败展示 ProblemView", async () => {
    catalogApi.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    expect(await screen.findByText("发生了未预期的错误。")).toBeTruthy();
  });
});
