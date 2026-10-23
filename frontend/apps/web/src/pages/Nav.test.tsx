import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ApiProblemError, type Nav as NavResult } from "@sa/api-client";
import { Nav } from "./Nav";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    nav: {
      get: vi.fn(),
    },
  },
}));

const getNav = vi.mocked(api.nav.get);

function makeNav(overrides: Partial<NavResult> = {}): NavResult {
  return {
    period: "2026-06",
    benchmark_id: "hs300_tr",
    definition_version: "1.0",
    data_as_of: "2026-07-01T00:00:00Z",
    status: "ok",
    blocked_ranges: [],
    points: [
      {
        date: "2026-06-02",
        unit_nav: "10000.000000",
        twr_daily: "0.00000000",
        twr_daily_pct: "0.00%",
        cashflow: "0.00",
        blocked: false,
        total_assets: "50000.000",
        prev_total_assets: null,
        daily_pnl: null,
        holdings: [],
      },
      {
        date: "2026-06-20",
        unit_nav: "11000.000000",
        twr_daily: "0.10000000",
        twr_daily_pct: "10.00%",
        cashflow: "0.00",
        blocked: false,
        total_assets: "55000.000",
        prev_total_assets: "50000.000",
        daily_pnl: "5000.000",
        holdings: [],
      },
    ],
    summary: {
      mwr: "0.10000000",
      twr: "0.10000000",
      annualized_twr: "0.18000000",
      mwr_minus_twr: "0.00000000",
      beta: null,
      alpha_annual: null,
      t_value: null,
      tracking_error: null,
      ir: null,
      n_days: 30,
      assumption: "statement",
    },
    ...overrides,
  };
}

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/nav"]}>
      <Nav />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getNav.mockResolvedValue(makeNav());
});

afterEach(cleanup);

describe("Nav 净值与收益页", () => {
  it("初始只展示构建入口，不自动请求", () => {
    renderPage();
    expect(screen.getByRole("button", { name: "构建净值与收益" })).toBeTruthy();
    expect(screen.queryByTestId("nav-result")).toBeNull();
    expect(getNav).not.toHaveBeenCalled();
  });

  it("点击构建后展示汇总指标与每日净值", async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText("月份"), { target: { value: "2026-06" } });
    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    await waitFor(() => expect(getNav).toHaveBeenCalledWith({ period: "2026-06" }));

    expect(await screen.findByTestId("nav-result")).toBeTruthy();
    expect(screen.getByText(/已构建 2026-06 的净值与收益/)).toBeTruthy();
    // 汇总区（twr 与 mwr 同值，故用 getAllByText）
    expect(screen.getByTestId("nav-summary")).toBeTruthy();
    expect(screen.getAllByText("0.10000000").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("0.18000000")).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();
    // 日点
    expect(screen.getByText("2026-06-02")).toBeTruthy();
    expect(screen.getByText("10000.000000")).toBeTruthy();
    expect(screen.getByText("11000.000000")).toBeTruthy();
    // 昨日资产 / 日收益额 / 日收益百分比
    expect(screen.getByText("昨日资产")).toBeTruthy();
    expect(screen.getAllByText("50000.000").length).toBeGreaterThanOrEqual(2); // 06-02 总资产 + 06-20 昨日资产
    expect(screen.getByText("日收益额")).toBeTruthy();
    expect(screen.getAllByText("55000.000").length).toBeGreaterThanOrEqual(2); // 表格 + 持仓区总资产
    expect(screen.getByText("5000.000")).toBeTruthy();
    expect(screen.getByText("0.00%")).toBeTruthy();
    expect(screen.getByText("10.00%")).toBeTruthy();
    // 整月无阻断点 → 不展示阻断列
    expect(screen.queryByText("阻断")).toBeNull();
  });

  it("存在阻断点时才展示阻断列", async () => {
    getNav.mockResolvedValue(
      makeNav({
        status: "blocked",
        blocked_ranges: [{ from: "2026-06-10", to: "2026-06-12" }],
        points: [
          {
            date: "2026-06-10",
            unit_nav: "0.000000",
            twr_daily: "0.00000000",
            twr_daily_pct: "0.00%",
            cashflow: "0.00",
            blocked: true,
            total_assets: "0.000",
            prev_total_assets: null,
            daily_pnl: null,
            holdings: [],
          },
        ],
      }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    expect(await screen.findByTestId("nav-result")).toBeTruthy();
    expect(screen.getAllByText("阻断").length).toBeGreaterThanOrEqual(2);
  });

  it("阻断状态展示阻断区间且阻断日不显示资产值", async () => {
    getNav.mockResolvedValue(
      makeNav({
        status: "blocked",
        blocked_ranges: [{ from: "2026-06-10", to: "2026-06-12" }],
        points: [
          {
            date: "2026-06-10",
            unit_nav: "0.000000",
            twr_daily: "0.00000000",
            twr_daily_pct: "0.00%",
            cashflow: "0.00",
            blocked: true,
            total_assets: "0.000",
            prev_total_assets: null,
            daily_pnl: null,
            holdings: [],
          },
        ],
      }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    expect(await screen.findByTestId("nav-blocked")).toBeTruthy();
    expect(screen.getByText("2026-06-10 ~ 2026-06-12")).toBeTruthy();
    expect(screen.getAllByText("阻断").length).toBeGreaterThanOrEqual(2);
    // blocked 点不输出净值/收益，只有两个占位符（单位净值 + 日收益）
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/阻断日不输出资产值/)).toBeTruthy();
  });

  it("降级状态给出排查提示", async () => {
    getNav.mockResolvedValue(makeNav({ status: "degraded" }));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    expect(await screen.findByText(/净值为降级结果/)).toBeTruthy();
  });

  it("期初资产为反推值时给出口径提示", async () => {
    getNav.mockResolvedValue(
      makeNav({ summary: { ...makeNav().summary, assumption: "back_solved" } }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    expect(await screen.findByText(/期初资产为反推值/)).toBeTruthy();
  });

  it("后端 422（月份非法）走 RFC 7807 错误渲染", async () => {
    getNav.mockRejectedValue(
      new ApiProblemError({
        type: "about:blank",
        title: "Invalid Period",
        status: 422,
        detail: "period 必须是 YYYY-MM",
        code: "INVALID_PERIOD",
        user_message: "区间参数不合法。",
        what_to_do: "请检查起止日期后重试。",
        retryable: false,
        request_id: "req_nav_1",
      }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    expect(await screen.findByText("区间参数不合法。")).toBeTruthy();
    expect(screen.getByText("INVALID_PERIOD")).toBeTruthy();
    expect(screen.queryByTestId("nav-result")).toBeNull();
  });

  it("月份格式非法时本地拦截，不发请求", async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText("月份"), { target: { value: "2026-6" } });
    fireEvent.click(screen.getByRole("button", { name: "构建净值与收益" }));

    expect(await screen.findByText(/月份格式应为 YYYY-MM/)).toBeTruthy();
    expect(getNav).not.toHaveBeenCalled();
  });
});
