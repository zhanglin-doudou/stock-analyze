import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import {
  ApiProblemError,
  type MetricDrilldown,
  type MetricExplain,
  type Problem,
} from "@sa/api-client";
import { MetricDetail } from "./MetricDetail";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    metrics: {
      explain: vi.fn(),
      drilldown: vi.fn(),
    },
  },
}));

const explainMetric = vi.mocked(api.metrics.explain);
const drilldownMetric = vi.mocked(api.metrics.drilldown);

function makeExplain(overrides: Partial<MetricExplain> = {}): MetricExplain {
  return {
    metric_id: "perf.win_rate",
    definition_version: "1.0",
    period: "2026-06",
    meaning: "已平仓回合里盈利占比，越高说明选股与卖出更稳定。",
    conclusion: "已平仓回合里盈利占比，越高说明选股与卖出更稳定。",
    method: "用盈利回合数除以已平仓回合数，并给出 Wilson 区间。",
    domain: "perf",
    unit: "ratio",
    precision: 8,
    sample: {
      n: 12,
      n_pending: 0,
      sample_status: "ok",
      min_n: 10,
      ci_min_n: 10,
      ci: "wilson",
    },
    drilldown: {
      endpoint: "/api/v1/metrics/perf.win_rate/drilldown",
      params: { period: "{period}" },
      description: "查看本期每个已平仓回合的盈亏。",
    },
    status: "ok",
    reason: null,
    what_to_do: null,
    assumptions: {},
    data_as_of: "2026-06-30",
    presentation: {
      display_name: "胜率",
      direction: "higher_better",
      confidence: { level: "high", label: "可信", reason: "样本与窗口满足门槛" },
      progress: {
        current: 12,
        required: 10,
        remaining: 0,
        unit: "samples",
        unit_label: "笔/日",
        label: "已满足样本门槛",
      },
      verdict: {
        level: "neutral",
        label: "中性",
        rationale: "95% 置信区间跨过区间边界，点估计不足以定档",
      },
      bands: [
        { level: "watch", label: "需关注", from: null, to: "0.40000000" },
        { level: "neutral", label: "中性", from: "0.40000000", to: "0.55000000" },
        { level: "good", label: "良好", from: "0.55000000", to: null },
      ],
      window: { kind: "month", label: "2026-06" },
      interpretation: {
        fact: "胜率 0.58333333；n=12。",
        meaning: "95% 置信区间跨过区间边界，点估计不足以定档",
        action: "查看下钻里的亏损回合，确认亏损是否集中在同一标的。",
      },
      caveats: [],
    },
    ...overrides,
  };
}

function makeDrilldown(overrides: Partial<MetricDrilldown> = {}): MetricDrilldown {
  return {
    metric_id: "perf.win_rate",
    definition_version: "1.0",
    period: "2026-06",
    status: "ok",
    reason: null,
    items: [
      {
        key: "round_1",
        value: "8200.00",
        unit: "CNY",
        label: "600519 回合盈亏",
        round_id: "round_1",
        event_date: "2026-06-12",
        security_code: "600519",
      },
    ],
    total: 1,
    source_endpoints: ["/api/v1/rounds"],
    params: {},
    next_cursor: null,
    truncated: false,
    assumptions: {},
    data_as_of: "2026-06-30",
    ...overrides,
  };
}

function drilldownProblem(): Problem {
  return {
    type: "about:blank",
    title: "Metric Not Found",
    status: 404,
    detail: "未知指标",
    code: "METRIC_NOT_FOUND",
    user_message: "该指标不存在。",
    what_to_do: "返回指标列表后重试。",
    retryable: false,
    request_id: "req_metric_1",
  };
}

function renderPage(path = "/metrics/perf.win_rate?period=2026-06"): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/metrics/:metricId" element={<MetricDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  explainMetric.mockResolvedValue(makeExplain());
  drilldownMetric.mockResolvedValue(makeDrilldown());
});

afterEach(cleanup);

describe("MetricDetail 指标详情页", () => {
  it("FT-01 展示意义、计算方式、样本门槛与样本明细行", async () => {
    renderPage();

    expect(await screen.findByTestId("metric-meaning")).toBeTruthy();
    expect(screen.getByText("已平仓回合里盈利占比，越高说明选股与卖出更稳定。")).toBeTruthy();
    expect(screen.getByText("用盈利回合数除以已平仓回合数，并给出 Wilson 区间。")).toBeTruthy();
    expect(screen.getByTestId("metric-sample").textContent).toContain("12");
    expect(screen.getByTestId("metric-sample").textContent).toContain("10");
    expect(screen.getByTestId("metric-sample").textContent).toContain("样本充足");
    expect(await screen.findByText("600519 回合盈亏")).toBeTruthy();
    expect(screen.getByText("8200.00 元")).toBeTruthy();
    const roundLink = screen.getByRole("link", { name: "round_1" });
    expect(roundLink.getAttribute("href")).toBe("/rounds/round_1");

    expect(explainMetric).toHaveBeenCalledWith("perf.win_rate", { period: "2026-06" });
    expect(drilldownMetric).toHaveBeenCalledWith("perf.win_rate", { period: "2026-06", limit: 50 });
  });

  it("FT-02 样本不足：conclusion 为空但意义/计算方式/建议仍可见", async () => {
    explainMetric.mockResolvedValue(
      makeExplain({
        conclusion: null,
        status: "ok",
        reason: "INSUFFICIENT_SAMPLE",
        what_to_do: "增加样本或等待窗口走完后再看结论。",
        sample: {
          n: 2,
          n_pending: 0,
          sample_status: "insufficient",
          min_n: 10,
          ci_min_n: 10,
          ci: "wilson",
        },
      }),
    );
    drilldownMetric.mockResolvedValue(makeDrilldown({ items: [], total: 0 }));

    renderPage();

    expect(await screen.findByTestId("metric-meaning")).toBeTruthy();
    expect(screen.getByText("已平仓回合里盈利占比，越高说明选股与卖出更稳定。")).toBeTruthy();
    expect(screen.getByText("用盈利回合数除以已平仓回合数，并给出 Wilson 区间。")).toBeTruthy();
    expect(screen.getByTestId("metric-sample").textContent).toContain("样本不足");
    expect(screen.getByText("增加样本或等待窗口走完后再看结论。")).toBeTruthy();
    // 无结论时不渲染「本期结论」小节。
    expect(screen.queryByText("本期结论")).toBeNull();
  });

  it("FT-03 切换月份重新加载；样本明细失败不阻断解释", async () => {
    drilldownMetric.mockRejectedValue(new ApiProblemError(drilldownProblem()));

    renderPage();

    expect(await screen.findByTestId("metric-meaning")).toBeTruthy();
    expect(await screen.findByText("该指标不存在。")).toBeTruthy();
    expect(screen.getByText("该指标在本期没有可下钻的样本行。")).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/月份/), { target: { value: "2026-05" } });
    await waitFor(() =>
      expect(explainMetric).toHaveBeenLastCalledWith("perf.win_rate", { period: "2026-05" }),
    );
  });
});
