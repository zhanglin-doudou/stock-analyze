import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { SectorsOverview } from "@sa/api-client";
import { Sectors } from "./Sectors";
import sectorsSource from "./Sectors.tsx?raw";
import tableSource from "./sectors/SectorTable.tsx?raw";
import prefSource from "../charts/PreferenceBar.tsx?raw";
import { api } from "../client";

vi.mock("../client", () => ({
  api: { sectors: { overview: vi.fn() } },
}));

const getOverview = vi.mocked(api.sectors.overview);

const INDUSTRY_ROW = {
  sector_code: "801120.SI",
  sector_name: "食品饮料",
  level: 1,
  parent_code: null,
  market_value: "170620.00",
  market_value_display: "170,620.00",
  weight: "1.00000000",
  weight_pct: "100.00%",
  holding_count: 1,
  benchmark_weight: "0.30000000",
  benchmark_weight_pct: "30.00%",
  active_weight: "0.70000000",
  active_weight_pct: "70.00%",
  direction: "over" as const,
  exposure: null,
  exposure_pct: null,
  overlap: null,
};

function industry(overrides: Partial<SectorsOverview> = {}): SectorsOverview {
  const base: SectorsOverview = {
    as_of: "2026-07-01",
    taxonomy: "industry",
    level: 1,
    status: "ok",
    asset_build_state: "current",
    sector_data_state: "current",
    definition_version: "1.0",
    sector_dataset_version: "SW2021",
    data_as_of: "2026-09-30",
    sector_data_as_of: "2026-06-30",
    benchmark_id: "hs300",
    benchmark_as_of: "2026-06-30",
    weight_mode: "additive",
    portfolio: {
      holding_count: 1,
      classified_holding_count: 1,
      holding_market_value: "170620.00",
      equity_market_value: "170620.00",
      mapped_market_value: "170620.00",
      unclassified_market_value: "0.00",
      non_stock_market_value: "0.00",
      total_assets: "100620.00",
      total_assets_display: "100,620.00",
      coverage: "1.00000000",
      coverage_pct: "100.00%",
      coverage_basis: "mapped_equity_market_value / equity_market_value",
      equity_position: "1.00000000",
      equity_position_pct: "100.00%",
    },
    sectors: [INDUSTRY_ROW],
    others: null,
    unclassified: null,
    concentration: {
      method: "industry_single_membership",
      status: "ok",
      hhi: "1.00000000",
      effective_n: "1.00000000",
      top1_weight: "1.00000000",
      top3_weight: "1.00000000",
      benchmark_hhi: "0.34000000",
      n_sectors: 1,
    },
    preference: {
      deviation: "0.70000000",
      max_overweight: {
        sector_code: "801120.SI",
        sector_name: "食品饮料",
        active_weight: "0.70000000",
        active_weight_pct: "70.00%",
        direction: "over",
      },
      max_underweight: null,
      top_overweight: [
        {
          sector_code: "801120.SI",
          sector_name: "食品饮料",
          active_weight: "0.70000000",
          active_weight_pct: "70.00%",
          direction: "over",
        },
      ],
      top_underweight: [],
    },
    concept: null,
    missing: [],
    links: {
      dashboard: "/dashboard",
      sectors: "/sectors?taxonomy=industry&level=1&as_of=2026-07-01",
      attribution: "/attribution?period=2026-07",
    },
    assumptions: { calendar: "weekday_proxy" },
    inputs_hash: "b".repeat(64),
  };
  return { ...base, ...overrides };
}

function concept(overrides: Partial<SectorsOverview> = {}): SectorsOverview {
  const base = industry({
    taxonomy: "concept",
    level: 0,
    status: "insufficient",
    benchmark_id: null,
    benchmark_as_of: null,
    weight_mode: "overlapping",
    sectors: [
      {
        ...INDUSTRY_ROW,
        sector_code: "BK0999",
        sector_name: "白酒",
        level: 0,
        weight: null,
        weight_pct: null,
        benchmark_weight: null,
        benchmark_weight_pct: null,
        active_weight: null,
        active_weight_pct: null,
        direction: null,
        exposure: "1.00000000",
        exposure_pct: "100.00%",
        overlap: true,
      },
    ],
    preference: null,
    concentration: {
      method: "concept_overlap",
      status: "insufficient",
      hhi: null,
      effective_n: null,
      top1_weight: null,
      top3_weight: null,
      benchmark_hhi: null,
      n_sectors: 1,
    },
  });
  return {
    ...base,
    concept: {
      coverage: "1.00000000",
      coverage_pct: "100.00%",
      coverage_basis: "tagged_equity_market_value / equity_market_value",
      tagged_market_value: "170620.00",
      multi_tag_market_value: "0.00",
      multi_tag_pct: "0.00%",
      overlap: "1.00000000",
      hhi_tag: null,
      top1_concept: {
        sector_code: "BK0999",
        sector_name: "白酒",
        exposure: "1.00000000",
        exposure_pct: "100.00%",
      },
      top5_concepts: [],
      caveat: "概念板块可重叠、暴露不可加总",
    },
    ...overrides,
  };
}

function renderPage(entry = "/sectors"): ReturnType<typeof render> {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Sectors />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getOverview.mockImplementation(async ({ taxonomy }) =>
    taxonomy === "concept" ? concept() : industry(),
  );
});

afterEach(cleanup);

describe("Sectors 板块分析页", () => {
  it("行业默认视图：状态/标签页/KPI/偏好条/明细表", async () => {
    renderPage();

    expect(await screen.findByTestId("sectors-page")).toBeTruthy();
    await screen.findByTestId("sector-kpi-hhi");
    expect(screen.getByTestId("sector-tabs")).toBeTruthy();
    expect(screen.getByTestId("sector-status-badge").textContent).toBe("正常");
    expect(screen.getByTestId("sector-kpi-hhi").textContent).toContain("1.00000000");
    expect(screen.getByTestId("sector-kpi-top1").textContent).toContain("1.00000000");
    expect(screen.getByTestId("preference-bar-801120.SI")).toBeTruthy();
    expect(screen.getByTestId("sector-row-801120.SI").textContent).toContain("食品饮料");
    expect(screen.getByTestId("sector-row-801120.SI").textContent).toContain("170,620.00");
  });

  it("概念标签页（深链 taxonomy=concept）：概念 KPI + caveat + 无基准列", async () => {
    renderPage("/sectors?taxonomy=concept");

    expect(await screen.findByTestId("sector-concept-view")).toBeTruthy();
    expect(screen.getByTestId("concept-caveat").textContent).toContain("不可加总");
    expect(screen.getByTestId("sector-kpi-overlap").textContent).toContain("1.00000000");
    expect(screen.getByTestId("sector-row-BK0999").textContent).toContain("100.00%");
    expect(screen.queryByText("基准权重")).toBeNull();
    expect(screen.queryByText("主动权重")).toBeNull();
  });

  it("深链回填：level/parent 进入请求且展示下钻面包屑", async () => {
    renderPage("/sectors?taxonomy=industry&level=2&parent=801120.SI");
    await screen.findByTestId("sectors-page");

    expect(getOverview).toHaveBeenCalledWith(
      expect.objectContaining({ taxonomy: "industry", level: 2, parent: "801120.SI" }),
    );
    expect(screen.getByText(/当前下钻/)).toBeTruthy();
  });

  it("缺失降级：显示 sector-status 清单且不展示百分比", async () => {
    getOverview.mockResolvedValue(
      industry({
        status: "missing",
        concentration: {
          method: "industry_single_membership",
          status: "missing",
          hhi: null,
          effective_n: null,
          top1_weight: null,
          top3_weight: null,
          benchmark_hhi: null,
          n_sectors: 0,
        },
        sectors: [],
        preference: null,
        missing: [
          { field: "sector", code: "SECTOR_DATA_MISSING", reason: "无板块数据", what_to_do: "先同步" },
        ],
      }),
    );
    renderPage();

    expect(await screen.findByTestId("sector-status")).toBeTruthy();
    expect(screen.getByTestId("sector-missing-item").getAttribute("data-code")).toBe(
      "SECTOR_DATA_MISSING",
    );
    expect(screen.getByTestId("sector-kpi-hhi").textContent).toContain("—");
  });

  it("零口径：页面与组件源码不含 Number/parseFloat/parseInt/toFixed", () => {
    for (const source of [sectorsSource, tableSource, prefSource]) {
      expect(source).not.toMatch(/\bNumber\s*\(/);
      expect(source).not.toMatch(/\bparseFloat\b/);
      expect(source).not.toMatch(/\bparseInt\b/);
      expect(source).not.toMatch(/\.toFixed\s*\(/);
    }
  });
});
