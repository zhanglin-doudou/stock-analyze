import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import {
  type AssetChart,
  type DashboardOut,
  type DashboardRoundOut,
  type MetricBatch,
} from "@sa/api-client";
import { Dashboard } from "./Dashboard";
import dashboardSource from "./Dashboard.tsx?raw";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    dashboard: { get: vi.fn() },
    metrics: { batch: vi.fn() },
    assets: { chart: vi.fn() },
  },
}));

const getDashboard = vi.mocked(api.dashboard.get);
const batchMetrics = vi.mocked(api.metrics.batch);
const getAssetChart = vi.mocked(api.assets.chart);

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
    definition_version: "1.0",
    asset_overview: {
      status: "ok",
      holdings: [
        {
          security_code: "600519",
          security_name: "贵州茅台",
          quantity: "100.0000",
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
        { security_code: "600519", security_name: "贵州茅台", trade_count: 7 },
        { security_code: "000001", security_name: "平安银行", trade_count: 4 },
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

beforeEach(() => {
  vi.clearAllMocks();
  getDashboard.mockResolvedValue(makeDashboard());
  batchMetrics.mockResolvedValue(makeMetricBatch());
  getAssetChart.mockResolvedValue(makeAssetChart());
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
    expect(screen.getByTestId("holding-row-600519").textContent).toContain("100.0000");
    expect(screen.getByTestId("top-round-rnd_600519")).toBeTruthy();
    expect(screen.getByTestId("bottom-round-rnd_000858")).toBeTruthy();

    // 持仓时长 Top3 / 交易次数 Top3（两列布局）
    expect(screen.getByTestId("top-holding-duration")).toBeTruthy();
    expect(screen.getByTestId("top-trade-count")).toBeTruthy();
    expect(screen.getByTestId("top-holding-rnd_600519").textContent).toContain("持有 120 天");
    expect(screen.getByTestId("top-holding-rnd_000001").textContent).toContain("持有 90 天");
    expect(screen.getByTestId("top-trade-600519").textContent).toContain("7 笔");
    expect(screen.getByTestId("top-trade-000001").textContent).toContain("4 笔");

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
    expect(screen.getByText(/没有持仓/)).toBeTruthy();

    expect(screen.getByTestId("dashboard-meta").textContent).toContain("未构建");
    expect(screen.getByTestId("dashboard-meta").textContent).toContain("缺失");
    expect(screen.getByText(/请到/)).toBeTruthy();

    const item = screen.getByTestId("missing-item");
    expect(item.getAttribute("data-code")).toBe("DASHBOARD_ASSET_MISSING");
    expect(item.textContent).toContain("到资产页构建资产表");
    expect(screen.getByTestId("kpi-max-drawdown").textContent).toContain("阻断");
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
    expect(dashboardSource).not.toMatch(/\bNumber\s*\(/);
    expect(dashboardSource).not.toMatch(/\bparseFloat\b/);
    expect(dashboardSource).not.toMatch(/\bparseInt\b/);
    expect(dashboardSource).not.toMatch(/\.toFixed\s*\(/);
  });
});
