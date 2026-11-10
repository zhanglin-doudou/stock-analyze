import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import {
  type AssetBuildAccepted,
  type AssetBuildState,
  type AssetChart,
  type AssetPage,
  type DashboardOut,
  type DashboardRoundOut,
  type DashboardTradeFlagsOut,
  type Job,
  type JobEvent,
  type JobPage,
  type MetricBatch,
  type SectorsOverview,
} from "@sa/api-client";
import { Assets } from "./Assets";
import { Dashboard } from "./Dashboard";
import { buildCharts } from "./dashboard/TradeFlagCharts";
import dashboardSource from "./Dashboard.tsx?raw";
import sectorBlockSource from "./dashboard/SectorBlock.tsx?raw";
import { resetAutoBuildGuard } from "../lib/useStaleAssetBuild";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    dashboard: { get: vi.fn(), tradeFlags: vi.fn() },
    metrics: { batch: vi.fn() },
    assets: { chart: vi.fn(), list: vi.fn(), build: vi.fn() },
    jobs: { list: vi.fn(), get: vi.fn(), events: vi.fn() },
    sectors: { overview: vi.fn() },
  },
}));

// nivo 依赖真实容器尺寸/ResizeObserver；jsdom 下用空组件替代，投影逻辑单独测。
vi.mock("@nivo/bar", () => ({ ResponsiveBar: () => null }));
vi.mock("@nivo/line", () => ({ ResponsiveLine: () => null }));

const getDashboard = vi.mocked(api.dashboard.get);
const getTradeFlags = vi.mocked(api.dashboard.tradeFlags);
const batchMetrics = vi.mocked(api.metrics.batch);
const getAssetChart = vi.mocked(api.assets.chart);
const getSectorsOverview = vi.mocked(api.sectors.overview);
const listAssets = vi.mocked(api.assets.list);
const buildAssets = vi.mocked(api.assets.build);
const listJobs = vi.mocked(api.jobs.list);
const getJob = vi.mocked(api.jobs.get);
const jobEvents = vi.mocked(api.jobs.events);

function makeRound(overrides: Partial<DashboardRoundOut> = {}): DashboardRoundOut {
  return {
    round_id: "rnd_600519",
    security_code: "600519",
    security_name: "贵州茅台",
    close_date: "2026-06-10",
    pnl: "2000.00",
    pnl_display: "2,000.00",
    return_rate: "0.10000000",
    return_rate_pct: "10.00%",
    holding_days: 5,
    ...overrides,
  };
}

function makeBuild(overrides: Partial<AssetBuildState> = {}): AssetBuildState {
  return {
    state: "current",
    last_status: "ok",
    last_built_at: "2026-09-30T08:00:00Z",
    last_built_through: "2026-09-30",
    last_mode: "incremental",
    last_rebuild_from: "2026-08-17",
    last_rows_written: 45,
    rows_count: 125,
    assumption: "back_solved",
    status_reason: null,
    has_new_market_data: false,
    definition_version: "1.0",
    ...overrides,
  };
}

function makeDashboard(overrides: Partial<DashboardOut> = {}): DashboardOut {
  return {
    as_of: "2026-07-01",
    data_as_of: "2026-09-30",
    legacy_period: "2026-06",
    metrics_period: "2026",
    metrics_period_start: "2026-01-01",
    metrics_period_end: "2026-07-01",
    status: "ok",
    build_state: "current",
    build: makeBuild(),
    definition_version: "1.0",
    asset_overview: {
      status: "ok",
      holdings: [
        {
          security_code: "600519",
          security_name: "贵州茅台",
          quantity: "100",
          cost: "170000.00",
          market_value: "170620.00",
          weight_pct: "1.00000000",
          cost_display: "170,000.00",
          market_value_display: "170,620.00",
          weight_display: "100.00%",
        },
      ],
      market_value: "170620.00",
      cash: "-70000.00",
      money_fund: "20000.00",
      balance: "-50000.00",
      total_assets: "100620.00",
      position_pct: "169.57%",
      total_profit: "620.00",
      max_invested_cost: "100000.00",
      max_cost_return_pct: "0.62%",
      total_fees: "0.00",
      bill_start_date: "2026-04-01",
      bill_end_date: "2026-06-30",
      initial_capital: "100000.00",
      initial_capital_display: "100,000.00",
      annualized_return: "0.01234567",
      annualized_return_pct: "1.23%",
      market_value_display: "170,620.00",
      cash_display: "-70,000.00",
      money_fund_display: "20,000.00",
      balance_display: "-50,000.00",
      total_assets_display: "100,620.00",
      total_profit_display: "620.00",
      max_invested_cost_display: "100,000.00",
      total_fees_display: "0.00",
    },
    history_overview: {
      status: "ok",
      closed_security_count: 3,
      closed_round_count: 3,
      win_count: 2,
      loss_count: 1,
      flat_count: 0,
      win_pct: "66.67%",
      loss_pct: "33.33%",
      flat_pct: null,
      max_profit: "2000.00",
      max_profit_display: "2,000.00",
      max_profit_round: makeRound(),
      max_profit_cost_pct: "2.00%",
      top_rounds: [
        makeRound(),
        makeRound({ round_id: "rnd_000001", security_code: "000001", security_name: "平安银行", pnl: "1000.00" }),
      ],
      top_holding_rounds: [
        makeRound({ round_id: "rnd_600519", holding_days: 120 }),
        makeRound({
          round_id: "rnd_000001",
          security_code: "000001",
          security_name: "平安银行",
          holding_days: 90,
        }),
        makeRound({
          round_id: "rnd_000858",
          security_code: "000858",
          security_name: "五粮液",
          holding_days: 60,
          pnl: "-500.00",
          return_rate_pct: "-5.00%",
        }),
      ],
      top_trade_counts: [
        {
          security_code: "600519",
          security_name: "贵州茅台",
          buy_count: 4,
          sell_count: 3,
          trade_count: 7,
          pnl: "2000.00",
          pnl_display: "2,000.00",
          total_fees: "56.78",
          total_fees_display: "56.78",
        },
        {
          security_code: "000001",
          security_name: "平安银行",
          buy_count: 2,
          sell_count: 2,
          trade_count: 4,
          pnl: "1000.00",
          pnl_display: "1,000.00",
          total_fees: "12.00",
          total_fees_display: "12.00",
        },
      ],
      bottom_rounds: [
        makeRound({
          round_id: "rnd_000858",
          security_code: "000858",
          security_name: "五粮液",
          pnl: "-500.00",
          return_rate_pct: "-5.00%",
        }),
      ],
      max_drawdown: {
        from_date: null,
        to_date: null,
        duration_days: null,
        duration_obs: 0,
        recovery_days: null,
        recovery_censored: false,
        mdd_pct: "0.00%",
        n_obs: 92,
        sample_status: "ok",
        reason: null,
      },
    },
    missing: [],
    links: {
      assets: "/assets",
      rounds: "/rounds",
      metrics: "/metrics?period=2026-06",
      reports: "/reports",
      exports: "/exports",
      attribution: "/attribution?period=2026-06",
      sectors: "/sectors?taxonomy=industry&level=1&as_of=2026-07-01",
    },
    assumptions: {
      calendar: "weekday_proxy",
      cost_model: "moving_average",
      total_profit: "full_history_bridge",
      mdd: "longest_valid_segment",
    },
    inputs_hash: "a".repeat(64),
    ...overrides,
  };
}

function missingDashboard(): DashboardOut {
  return makeDashboard({
    status: "missing",
    build_state: "missing",
    build: makeBuild({ state: "missing", last_status: null, last_mode: null, rows_count: 0 }),
    data_as_of: null,
    asset_overview: {
      status: "missing",
      holdings: [],
      market_value: null,
      cash: null,
      money_fund: null,
      balance: null,
      total_assets: null,
      position_pct: null,
      total_profit: null,
      max_invested_cost: null,
      max_cost_return_pct: null,
      total_fees: null,
      bill_start_date: null,
      bill_end_date: null,
      initial_capital: null,
      initial_capital_display: null,
      annualized_return: null,
      annualized_return_pct: null,
    },
    history_overview: {
      status: "missing",
      closed_security_count: 0,
      closed_round_count: 0,
      win_count: 0,
      loss_count: 0,
      flat_count: 0,
      win_pct: null,
      loss_pct: null,
      flat_pct: null,
      max_profit: null,
      max_profit_round: null,
      max_profit_cost_pct: null,
      top_rounds: [],
      top_holding_rounds: [],
      top_trade_counts: [],
      bottom_rounds: [],
      max_drawdown: {
        from_date: null,
        to_date: null,
        duration_days: null,
        duration_obs: null,
        recovery_days: null,
        recovery_censored: false,
        mdd_pct: null,
        n_obs: 0,
        sample_status: "blocked",
        reason: "资产表未构建",
      },
    },
    missing: [
      {
        field: "asset_overview",
        code: "DASHBOARD_ASSET_MISSING",
        reason: "尚无资产快照",
        what_to_do: "到资产页构建资产表",
      },
    ],
  });
}

function makeMetricBatch(): MetricBatch {
  return {
    period: "2026",
    definition_version: "1.0",
    data_as_of: "2026-06-30",
    assumptions: {},
    items: [
      {
        metric_id: "perf.total_return",
        definition_version: "1.0",
        domain: "perf",
        period: "2026",
        status: "ok",
        sample_status: "ok",
        reason: null,
        value: "12.34",
        unit: "CNY",
        precision: 2,
        n: 20,
        n_pending: 0,
        data_as_of: "2026-06-30",
      },
    ],
  };
}

function makeAssetChart(): AssetChart {
  return {
    points: [
      {
        date: "2026-06-29",
        v: "0.0000",
        label: "06-29",
        amount_label: "100000.00",
      },
      {
        date: "2026-06-30",
        v: "1.0000",
        label: "06-30",
        amount_label: "100620.00",
      },
    ],
    n_points: 2,
    build_state: "current",
    definition_version: "1.0",
    data_as_of: "2026-09-30",
  };
}

function makeIndustryOverview(overrides: Partial<SectorsOverview> = {}): SectorsOverview {
  return {
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
    sectors: [
      {
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
        direction: "over",
        exposure: null,
        exposure_pct: null,
        overlap: null,
      },
    ],
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
        active_weight: "0.7",
        active_weight_pct: "70.00%",
        direction: "over",
      },
      max_underweight: null,
      top_overweight: [
        {
          sector_code: "801120.SI",
          sector_name: "食品饮料",
          active_weight: "0.7",
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
    ...overrides,
  };
}

function makeConceptOverview(overrides: Partial<SectorsOverview> = {}): SectorsOverview {
  return {
    as_of: "2026-07-01",
    taxonomy: "concept",
    level: 0,
    status: "insufficient",
    asset_build_state: "current",
    sector_data_state: "current",
    definition_version: "1.0",
    sector_dataset_version: "EM",
    data_as_of: "2026-09-30",
    sector_data_as_of: "2026-06-30",
    benchmark_id: null,
    benchmark_as_of: null,
    weight_mode: "overlapping",
    portfolio: {
      holding_count: 1,
      classified_holding_count: 0,
      holding_market_value: "170620.00",
      equity_market_value: "170620.00",
      mapped_market_value: null,
      unclassified_market_value: null,
      non_stock_market_value: "0.00",
      total_assets: "100620.00",
      total_assets_display: "100,620.00",
      coverage: "1.00000000",
      coverage_pct: "100.00%",
      coverage_basis: null,
      equity_position: "1.00000000",
      equity_position_pct: "100.00%",
    },
    sectors: [
      {
        sector_code: "BK0999",
        sector_name: "白酒",
        level: 0,
        parent_code: null,
        market_value: null,
        market_value_display: null,
        weight: null,
        weight_pct: null,
        holding_count: 1,
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
    others: null,
    unclassified: null,
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
    preference: null,
    concept: {
      coverage: "1",
      coverage_pct: "100.00%",
      coverage_basis: "tagged_equity_market_value / equity_market_value",
      tagged_market_value: "170620.00",
      multi_tag_market_value: "0.00",
      multi_tag_pct: "0.00%",
      overlap: "1",
      hhi_tag: null,
      top1_concept: {
        sector_code: "BK0999",
        sector_name: "白酒",
        exposure: "1",
        exposure_pct: "100.00%",
      },
      top5_concepts: [
        {
          sector_code: "BK0999",
          sector_name: "白酒",
          exposure: "1",
          exposure_pct: "100.00%",
        },
      ],
      caveat: "概念板块可重叠、暴露不可加总",
    },
    missing: [],
    links: {
      dashboard: "/dashboard",
      sectors: "/sectors?taxonomy=concept&level=0",
      attribution: "/attribution?period=2026-07",
    },
    assumptions: {},
    inputs_hash: "c".repeat(64),
    ...overrides,
  };
}

function makeTradeFlags(overrides: Partial<DashboardTradeFlagsOut> = {}): DashboardTradeFlagsOut {
  const series = [
    { code: "chase", label_zh: "追涨", label_en: "Chase", side: "buy" as const, lookahead: false },
    { code: "instant_bottom", label_zh: "即时抄底", label_en: "Instant bottom", side: "buy" as const, lookahead: false },
    { code: "short_term_win", label_zh: "短期成功", label_en: "Short-term win", side: "buy" as const, lookahead: true },
    { code: "stop_loss", label_zh: "止损", label_en: "Stop loss", side: "sell" as const, lookahead: false },
    { code: "instant_top", label_zh: "即时逃顶", label_en: "Instant top", side: "sell" as const, lookahead: false },
    { code: "short_term_top", label_zh: "短期逃顶", label_en: "Short-term top", side: "sell" as const, lookahead: true },
  ];
  const stateSeries = [
    { code: "first_entry", label_zh: "首次建仓", label_en: "First entry", side: "buy" as const, lookahead: false },
    { code: "addon_average", label_zh: "均价加仓", label_en: "Add at average", side: "buy" as const, lookahead: false },
    { code: "addon_dip", label_zh: "抄底加仓", label_en: "Add on dip", side: "buy" as const, lookahead: false },
    { code: "addon_chase", label_zh: "追涨加仓", label_en: "Add on chase", side: "buy" as const, lookahead: false },
    { code: "reduce_breakeven", label_zh: "回本减仓", label_en: "Reduce at breakeven", side: "sell" as const, lookahead: false },
    { code: "reduce_profit", label_zh: "止盈减仓", label_en: "Reduce on profit", side: "sell" as const, lookahead: false },
    { code: "reduce_loss", label_zh: "止损减仓", label_en: "Reduce on loss", side: "sell" as const, lookahead: false },
    { code: "full_exit_breakeven", label_zh: "回本清仓", label_en: "Break-even exit", side: "sell" as const, lookahead: false },
    { code: "full_exit_profit", label_zh: "止盈清仓", label_en: "Profit exit", side: "sell" as const, lookahead: false },
    { code: "full_exit_loss", label_zh: "止损清仓", label_en: "Loss exit", side: "sell" as const, lookahead: false },
  ];
  const month = (
    label: string,
    buy_count: number,
    sell_count: number,
    hits: number[],
    stateHits: number[] = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  ) => ({
    month: label,
    label,
    buy_count,
    sell_count,
    flags: series.map((item, index) => ({ code: item.code, count: hits[index] ?? 0 })),
    states: stateSeries.map((item, index) => ({ code: item.code, count: stateHits[index] ?? 0 })),
  });
  return {
    window_months: 6,
    start_month: "2026-01",
    end_month: "2026-06",
    latest_complete_month: "2026-06",
    months_basis: "完整月 = 已自然结束的日历月。",
    definition_version: "1.1",
    has_data: true,
    has_flag_data: true,
    has_state_data: true,
    series,
    state_series: stateSeries,
    total_labels: { buy: "买入笔数", sell: "卖出笔数" },
    items: [
      month("2026-05", 1, 0, [0, 0, 0, 0, 0, 0]),
      month("2026-06", 3, 2, [2, 1, 0, 1, 0, 1], [1, 1, 1, 0, 0, 1, 0, 1, 0, 0]),
    ],
    months_without_data: ["2026-01", "2026-02", "2026-03", "2026-04"],
    ...overrides,
  };
}

function renderPage(): ReturnType<typeof render> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 看板 + 资产页同时挂载：验证跨页共用的模块级去重表只发一次 POST。 */
function renderBoth(): ReturnType<typeof render> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <Dashboard />
          <Assets />
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

function makeAssetPage(build: AssetBuildState): AssetPage {
  return {
    items: [],
    next_cursor: null,
    has_more: false,
    build,
    definition_version: build.definition_version,
    data_as_of: build.last_built_through,
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: "job_assets",
    type: "build_assets",
    status: "succeeded",
    progress: 100,
    stage: "done",
    message: null,
    result_ref: null,
    error: null,
    attempt: 1,
    max_attempts: 3,
    available_at: "2026-09-30T08:00:00Z",
    heartbeat_at: null,
    created_at: "2026-09-30T08:00:00Z",
    started_at: "2026-09-30T08:00:01Z",
    finished_at: "2026-09-30T08:00:05Z",
    ...overrides,
  };
}

function emptyJobs(): JobPage {
  return { items: [], next_cursor: null, backlog: {} };
}

function accepted(): AssetBuildAccepted {
  return {
    job_id: "job_assets",
    type: "build_assets",
    status: "pending",
    links: { self: "/api/v1/jobs/job_assets", events: "/api/v1/jobs/job_assets/events" },
  };
}

/** 默认 SSE：不产生事件（JobProgress 的 for-await 直接结束，不再重试）。 */
function noEvents(): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    /* no-op */
  }
  return generator();
}

beforeEach(() => {
  vi.clearAllMocks();
  resetAutoBuildGuard();
  getDashboard.mockResolvedValue(makeDashboard());
  getTradeFlags.mockResolvedValue(makeTradeFlags());
  batchMetrics.mockResolvedValue(makeMetricBatch());
  getAssetChart.mockResolvedValue(makeAssetChart());
  getSectorsOverview.mockImplementation(async ({ taxonomy }) =>
    taxonomy === "concept" ? makeConceptOverview() : makeIndustryOverview(),
  );
  // 资产表（跨页去重用）与 build job 的默认桩：未 stale 时不会走到 POST。
  listAssets.mockResolvedValue(makeAssetPage(makeBuild()));
  listJobs.mockResolvedValue(emptyJobs());
  getJob.mockResolvedValue(makeJob());
  buildAssets.mockResolvedValue(accepted());
  jobEvents.mockImplementation(() => noEvents());
});

afterEach(cleanup);

describe("Dashboard 看板", () => {
  it("FT-01 两板块 + 15 个 KPI + 持仓行 + 最赚最亏 + 回撤 + 占位链接", async () => {
    renderPage();

    expect(await screen.findByTestId("asset-overview")).toBeTruthy();
    expect(screen.getByTestId("history-overview")).toBeTruthy();
    // Phase 1c：总资产日线来自 /assets（真实数据，几何由 charts/geometry 完成）
    expect(await screen.findByRole("img", { name: /总资产曲线/ })).toBeTruthy();
    // Phase 4c：图表提供可展开数据表替代
    expect(screen.getByText(/查看数据表/)).toBeTruthy();

    for (const testId of [
      "kpi-market-value",
      "kpi-balance",
      "kpi-position",
      "kpi-total-profit",
      "kpi-max-invested-cost",
      "kpi-max-cost-return",
      "kpi-closed-securities",
      "kpi-win-count",
      "kpi-loss-count",
      "kpi-win-pct",
      "kpi-loss-pct",
      "kpi-max-profit",
      "kpi-max-profit-cost",
      "kpi-max-drawdown",
    ]) {
      expect(screen.getByTestId(testId), testId).toBeTruthy();
    }

    expect(screen.getByTestId("kpi-market-value").textContent).toContain("170,620.00");
    expect(screen.getByTestId("kpi-position").textContent).toContain("169.57%");
    expect(screen.getByTestId("kpi-total-profit").textContent).toContain("620.00");
    expect(screen.getByTestId("kpi-max-cost-return").textContent).toContain("0.62%");
    expect(screen.getByTestId("kpi-closed-securities").textContent).toContain("3");
    expect(screen.getByTestId("kpi-win-count").textContent).toContain("2");
    expect(screen.getByTestId("kpi-win-pct").textContent).toContain("66.67%");
    expect(screen.getByTestId("kpi-max-profit").textContent).toContain("2,000.00");
    expect(screen.getByTestId("kpi-max-profit-cost").textContent).toContain("2.00%");
    expect(screen.getByTestId("kpi-max-drawdown").textContent).toContain("0.00%");
    expect(screen.getByTestId("kpi-max-drawdown").textContent).toContain("92");

    expect(screen.getByTestId("holding-row-600519").textContent).toContain("贵州茅台");
    // 数量沿用后端展示串：整数份额不带尾零（"100"）。
    expect(within(screen.getByTestId("holding-row-600519")).getByText("100")).toBeTruthy();
    expect(screen.getByTestId("top-round-rnd_600519")).toBeTruthy();
    expect(screen.getByTestId("bottom-round-rnd_000858")).toBeTruthy();

    // 持仓时长 Top3 / 交易次数 Top3（两列布局）
    expect(screen.getByTestId("top-holding-duration")).toBeTruthy();
    expect(screen.getByTestId("top-trade-count")).toBeTruthy();
    expect(screen.getByTestId("top-holding-rnd_600519").textContent).toContain("持有 120 天");
    expect(screen.getByTestId("top-holding-rnd_000001").textContent).toContain("持有 90 天");
    expect(screen.getByTestId("top-trade-600519").textContent).toContain("7 笔");
    expect(screen.getByTestId("top-trade-000001").textContent).toContain("4 笔");
    expect(screen.getByTestId("top-trade-600519").textContent).toContain("买 4");
    expect(screen.getByTestId("top-trade-600519").textContent).toContain("卖 3");
    expect(screen.getByTestId("top-trade-600519").textContent).toContain("2,000.00");
    expect(screen.getByTestId("top-trade-600519").textContent).toContain("56.78");

    // 总资产下方：账单起止时间 + 初始资金
    expect(screen.getByTestId("kpi-bill-range").textContent).toContain("2026-04-01");
    expect(screen.getByTestId("kpi-bill-range").textContent).toContain("2026-06-30");
    expect(screen.getByTestId("kpi-initial-capital").textContent).toContain("100,000.00");
    expect(screen.getByTestId("kpi-annualized-return").textContent).toContain("1.23%");

    expect(screen.getByTestId("dashboard-meta").textContent).toContain("最新");
    expect(screen.getByTestId("dashboard-meta").textContent).toContain("正常");
    expect(screen.getByTestId("dashboard-meta").textContent).toContain("2026-09-30");
    expect(screen.getByTestId("market-data-notice").textContent).toContain("收盘后同步");

    expect(screen.getByTestId("link-reports").getAttribute("href")).toBe("/reports");
    expect(screen.getByTestId("link-exports").getAttribute("href")).toBe("/exports");
    expect(screen.getByTestId("link-attribution").getAttribute("href")).toBe(
      "/attribution?period=2026-06",
    );
    expect(screen.getByTestId("link-annual-metrics").getAttribute("href")).toBe(
      "/metrics?period=2026",
    );

    // 板块子块：asset-overview 的成员（不新增板块级 data-board）
    const sectorBlock = await screen.findByTestId("asset-sector-block");
    expect(sectorBlock.getAttribute("data-board-member")).toBe("asset-overview");
    expect(document.querySelectorAll("[data-board]")).toHaveLength(2);
    expect(screen.getByTestId("sector-kpi-hhi").textContent).toContain("1.00000000");
    expect(screen.getByTestId("sector-kpi-concept-coverage").textContent).toContain("100.00%");
    expect(screen.getByTestId("sector-pref-801120.SI").textContent).toContain("70.00%");
    expect(screen.getByTestId("link-sectors").getAttribute("href")).toBe(
      "/sectors?taxonomy=industry&level=1&as_of=2026-07-01",
    );

    // 年度指标自动加载（metrics_period = 今年），无需手动点击。
    await waitFor(() => expect(batchMetrics).toHaveBeenCalledWith({ period: "2026" }));
    expect(screen.queryByTestId("missing-list")).toBeNull();
  });

  it("FT-02 未构建：金额全「—」+ missing 清单 + 构建提示，不出现 0 冒充", async () => {
    getDashboard.mockResolvedValue(missingDashboard());

    renderPage();

    expect(await screen.findByTestId("asset-overview")).toBeTruthy();
    expect(screen.getByTestId("kpi-market-value").textContent).toContain("—");
    expect(screen.getByTestId("kpi-market-value").textContent).not.toContain("0.00");
    expect(screen.getByTestId("kpi-total-profit").textContent).toContain("—");
    expect(screen.getByTestId("kpi-win-pct").textContent).toContain("—");
    expect(screen.getByTestId("kpi-bill-range").textContent).toContain("—");
    expect(screen.getByTestId("kpi-initial-capital").textContent).toContain("—");
    expect(screen.getByTestId("kpi-annualized-return").textContent).toContain("—");
    expect(screen.getByText(/没有持仓/)).toBeTruthy();

    expect(screen.getByTestId("dashboard-meta").textContent).toContain("未构建");
    expect(screen.getByTestId("dashboard-meta").textContent).toContain("缺失");
    expect(screen.getByText(/请到/)).toBeTruthy();

    const item = screen.getByTestId("missing-item");
    expect(item.getAttribute("data-code")).toBe("DASHBOARD_ASSET_MISSING");
    expect(item.textContent).toContain("到资产页构建资产表");
    expect(screen.getByTestId("kpi-max-drawdown").textContent).toContain("阻断");
  });

  it("FT-12 看板 stale 自动触发：恰好 1 次 POST，完成后重拉看板", async () => {
    const staleBuild = makeBuild({ state: "stale", has_new_market_data: true });
    getDashboard
      .mockResolvedValueOnce(
        makeDashboard({ status: "stale", build_state: "stale", build: staleBuild }),
      )
      .mockResolvedValue(makeDashboard());

    // 可手动结算的 job 轮询：完成之前不重拉看板。
    let finish: (job: Job) => void = () => undefined;
    getJob.mockImplementation(
      () =>
        new Promise<Job>((resolve) => {
          finish = resolve;
        }),
    );

    renderPage();

    await waitFor(() => expect(buildAssets).toHaveBeenCalledTimes(1));
    expect(buildAssets.mock.calls[0][0]).toEqual({ mode: "auto" });
    expect(buildAssets.mock.calls[0][1]).toBeTruthy();
    // 简短状态行（不喧宾夺主），文案不引入第二套口径。
    expect((await screen.findByTestId("dashboard-auto-build")).textContent).toContain(
      "正在自动更新",
    );

    await waitFor(() => expect(getJob).toHaveBeenCalled());
    expect(getDashboard).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish(makeJob({ status: "succeeded" }));
    });

    // 完成后重拉看板（首屏 1 次 + 完成后 1 次），状态行消失，且不重复 POST。
    await waitFor(() => expect(getDashboard.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(screen.queryByTestId("dashboard-auto-build")).toBeNull());
    expect(buildAssets).toHaveBeenCalledTimes(1);
  });

  it("FT-13 跨页去重：/dashboard 与 /assets 同时挂载，同一 stale 代次全局只 1 次 POST", async () => {
    const staleBuild = makeBuild({ state: "stale", has_new_market_data: true });
    const staleDashboard = makeDashboard({
      status: "stale",
      build_state: "stale",
      build: staleBuild,
    });
    const seq: string[] = [];

    getDashboard.mockImplementation(async () => {
      seq.push("GET /dashboard");
      return staleDashboard;
    });
    listAssets.mockImplementation(async () => {
      seq.push("GET /assets");
      return makeAssetPage(staleBuild);
    });
    listJobs.mockImplementation(async () => {
      seq.push("GET /jobs?status=pending,running");
      return emptyJobs();
    });
    buildAssets.mockImplementation(async () => {
      seq.push("POST /assets/build");
      return accepted();
    });
    getJob.mockImplementation(async (jobId: string) => {
      seq.push(`GET /jobs/${jobId}`);
      return makeJob({ status: "running", progress: 20 });
    });
    jobEvents.mockImplementation(() => {
      seq.push("GET /jobs/job_assets/events");
      return noEvents();
    });

    renderBoth();

    await waitFor(() => expect(buildAssets).toHaveBeenCalledTimes(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
    });

    // 实测请求序列：两页各自 GET 数据，但同一 stale 代次全局只有 1 次 POST。
    const posts = seq.filter((item) => item === "POST /assets/build");
    expect(posts.length, seq.join(" → ")).toBe(1);
    expect(buildAssets).toHaveBeenCalledTimes(1);

    // 两页共用同一个 job：看板出状态行，资产页出进度条。
    expect(screen.getByTestId("dashboard-auto-build")).toBeTruthy();
    expect(screen.getByTestId("assets-job-progress")).toBeTruthy();
  });

  it("FT-03 年度指标自动加载：用 metrics_period 请求并原样展示 value", async () => {
    renderPage();
    await screen.findByTestId("asset-overview");

    await waitFor(() => expect(batchMetrics).toHaveBeenCalledWith({ period: "2026" }));
    expect(await screen.findByText("perf.total_return")).toBeTruthy();
    const annual = screen.getByTestId("annual-metrics");
    expect(annual.textContent).toContain("12.34");
    expect(annual.textContent).toContain("元");
  });

  it("FT-04 零口径：源码不含 Number/parseFloat/parseInt/toFixed", () => {
    for (const source of [dashboardSource, sectorBlockSource]) {
      expect(source).not.toMatch(/\bNumber\s*\(/);
      expect(source).not.toMatch(/\bparseFloat\b/);
      expect(source).not.toMatch(/\bparseInt\b/);
      expect(source).not.toMatch(/\.toFixed\s*\(/);
    }
  });

  it("FT-05 基准缺失：偏好降级为组合权重 Top3，概念警示可见", async () => {
    getSectorsOverview.mockImplementation(async ({ taxonomy }) =>
      taxonomy === "concept"
        ? makeConceptOverview()
        : makeIndustryOverview({ benchmark_id: null, preference: null }),
    );
    renderPage();

    const block = await screen.findByTestId("asset-sector-block");
    await screen.findByTestId("sector-weight-801120.SI");
    expect(block.textContent).toContain("组合权重 Top3");
    expect(screen.getByTestId("sector-weight-801120.SI").textContent).toContain("100.00%");
    expect(screen.queryByTestId("sector-pref-801120.SI")).toBeNull();
    expect(screen.getByTestId("sector-kpi-concept").textContent).toContain("不可加总");
  });

  it("FT-06 missing/blocked：板块子块不展示金额/百分比", async () => {
    getSectorsOverview.mockImplementation(async ({ taxonomy }) =>
      taxonomy === "concept"
        ? makeConceptOverview({ concept: null, missing: [
            { field: "sector", code: "SECTOR_DATA_MISSING", reason: "无板块数据", what_to_do: "先同步" },
          ] })
        : makeIndustryOverview({
            status: "blocked",
            concentration: {
              method: "industry_single_membership",
              status: "blocked",
              hhi: null,
              effective_n: null,
              top1_weight: null,
              top3_weight: null,
              benchmark_hhi: null,
              n_sectors: 0,
            },
            sectors: [],
            preference: null,
          }),
    );
    renderPage();

    const block = await screen.findByTestId("asset-sector-block");
    await screen.findByTestId("sector-kpi-hhi");
    expect(screen.getByTestId("sector-kpi-hhi").textContent).toContain("—");
    expect(block.textContent ?? "").not.toMatch(/\d+\.\d{2}%/);
  });

  it("FT-07 账单旗标图：默认请求 12 个月，切换范围改请求参数，月份口径来自后端", async () => {
    renderPage();

    const section = await screen.findByTestId("trade-flag-charts");
    await waitFor(() => expect(getTradeFlags).toHaveBeenCalledWith(12));
    expect(section.textContent).toContain("2026-01");
    expect(section.textContent).toContain("完整月 = 已自然结束的日历月。");
    expect(screen.getByTestId("tfc-range-12").getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByTestId("tfc-range-6"));
    await waitFor(() => expect(getTradeFlags).toHaveBeenCalledWith(6));
    expect(screen.getByTestId("tfc-range-6").getAttribute("aria-pressed")).toBe("true");
  });

  it("FT-08 无旗标数据：展示空态与构建命令，不渲染空白图表", async () => {
    getTradeFlags.mockResolvedValue(
      makeTradeFlags({
        has_data: false,
        has_flag_data: false,
        has_state_data: false,
        items: [],
        months_without_data: [],
      }),
    );
    renderPage();

    const empty = await screen.findByTestId("tfc-empty");
    expect(empty.textContent).toContain("没有账单旗标数据");
    expect(empty.textContent).toContain("make build-trade-flags");
    expect(document.querySelector(".tfc-chart-bar")).toBeNull();
  });

  it("FT-09 有交易但无加仓/减仓状态命中：两张图都给空态，不画空白坐标轴", async () => {
    getTradeFlags.mockResolvedValue(
      makeTradeFlags({ has_flag_data: false, has_state_data: false }),
    );
    renderPage();

    expect(await screen.findByTestId("tfc-unbuilt")).toBeTruthy();
    expect(await screen.findByTestId("tfc-bar-empty")).toBeTruthy();
    expect(
      await screen.findByText("所选区间内暂无状态旗标命中。"),
    ).toBeTruthy();
    expect(document.querySelector(".tfc-chart-bar")).toBeNull();
  });

  it("FT-10 图表数据投影：标签/计数原样透传，柱状系列先买后卖且配色按侧分组", () => {
    const palette = {
      text: "t",
      muted: "m",
      grid: "g",
      line: "l",
      surface: "s",
      selection: "sel",
      series: {
        buy: "buy-total",
        sell: "sell-total",
        chase: "buy-1",
        instant_bottom: "buy-2",
        short_term_win: "buy-3",
        stop_loss: "sell-1",
        instant_top: "sell-2",
        short_term_top: "sell-3",
        addon_average: "buy-1",
        addon_dip: "buy-2",
        addon_chase: "buy-3",
        first_entry: "buy-0",
        reduce_breakeven: "sell-1",
        reduce_profit: "sell-2",
        reduce_loss: "sell-3",
        full_exit_breakeven: "sell-exit-1",
        full_exit_profit: "sell-exit-2",
        full_exit_loss: "sell-exit-3",
      },
    };
    const charts = buildCharts(makeTradeFlags(), palette);

    expect(charts.barKeys).toEqual([
      { key: "首次建仓", color: "buy-0" },
      { key: "均价加仓", color: "buy-1" },
      { key: "抄底加仓", color: "buy-2" },
      { key: "追涨加仓", color: "buy-3" },
      { key: "回本减仓", color: "sell-1" },
      { key: "止盈减仓", color: "sell-2" },
      { key: "止损减仓", color: "sell-3" },
      { key: "回本清仓", color: "sell-exit-1" },
      { key: "止盈清仓", color: "sell-exit-2" },
      { key: "止损清仓", color: "sell-exit-3" },
    ]);
    expect(charts.barData[1]).toEqual({
      month: "2026-06",
      首次建仓: 1,
      均价加仓: 1,
      抄底加仓: 1,
      追涨加仓: 0,
      回本减仓: 0,
      止盈减仓: 1,
      止损减仓: 0,
      回本清仓: 1,
      止盈清仓: 0,
      止损清仓: 0,
    });
    expect(charts.lineSeries.map((serie) => serie.id)).toEqual([
      "追涨",
      "即时抄底",
      "短期成功",
      "止损",
      "即时逃顶",
      "短期逃顶",
    ]);
    expect(charts.lineSeries[0]?.data).toEqual([
      { x: "2026-05", y: 0 },
      { x: "2026-06", y: 2 },
    ]);
    expect(charts.lineColors).toEqual([
      "buy-1",
      "buy-2",
      "buy-3",
      "sell-1",
      "sell-2",
      "sell-3",
    ]);
  });

  it("FT-11 图例点击：其余系列叠加 0.5 透明度，再点同系列取消", async () => {
    renderPage();

    const first = await screen.findByRole("button", { name: "首次建仓" });
    const other = screen.getByRole("button", { name: "均价加仓" });
    expect(first.getAttribute("aria-pressed")).toBe("false");
    expect(other.closest("li")?.className).not.toContain("is-dim");

    fireEvent.click(first);
    expect(first.getAttribute("aria-pressed")).toBe("true");
    expect(first.closest("li")?.className).not.toContain("is-dim");
    expect(other.closest("li")?.className).toContain("is-dim");

    fireEvent.click(first);
    expect(first.getAttribute("aria-pressed")).toBe("false");
    expect(other.closest("li")?.className).not.toContain("is-dim");

    fireEvent.click(other);
    expect(other.getAttribute("aria-pressed")).toBe("true");
    expect(first.closest("li")?.className).toContain("is-dim");
  });
});
