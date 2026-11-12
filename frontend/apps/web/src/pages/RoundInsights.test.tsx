import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { RoundInsightsPage } from "@sa/api-client";
import { RoundInsights } from "./RoundInsights";
import { currentYearToken } from "../lib/date";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    rounds: {
      insights: vi.fn(),
      refreshInsights: vi.fn(),
    },
  },
}));

// 图表体积大且与口径无关：只验证「懒加载挂载 + 数据来自后端」，nivo 本身用桩替代。
vi.mock("@nivo/pie", () => ({
  ResponsivePie: () => <div data-testid="nivo-pie" />,
}));

vi.mock("@nivo/bar", () => ({
  ResponsiveBar: () => <div data-testid="nivo-bar" />,
}));

const listInsights = vi.mocked(api.rounds.insights);
const refreshInsights = vi.mocked(api.rounds.refreshInsights);

function makePage(overrides: Partial<RoundInsightsPage> = {}): RoundInsightsPage {
  const forward = {
    horizon: 20,
    status: "ok" as const,
    base_shifted: false,
    price_jump_review: false,
    b0_pct: "0.30%",
    b1_pct: "1.20%",
    b2_pct: "1.50%",
    b3_pct: "0.40%",
  };
  return {
    period: "2026-06",
    period_basis: "close_date",
    definition_version: "v1",
    bucket_basis: "round",
    data_as_of: "2026-06-30",
    ranking_as_of: "2026-06-30",
    benchmark_id: "hs300_tr",
    basis: "benchmark_excess_20d",
    item_total: 1,
    items: [
      {
        round_id: "rnd_1",
        security_code: "600519",
        security_name: "贵州茅台",
        status: "closed",
        open_date: "2026-06-02",
        close_date: "2026-06-20",
        holding_days_legacy: 14,
        holding_trading_days: 14,
        bucket: "position",
        pnl: "-650.00",
        return_rate_pct: "-3.25%",
        flags: ["addon", "continued_decline"],
        quality: [],
        buy_trade: {
          trade_id: "trd_buy_1",
          date: "2026-06-02",
          reason_label: "计划建仓",
          forward: { "20": forward },
        },
        sell_trade: {
          trade_id: "trd_sell_1",
          date: "2026-06-20",
          reason_label: null,
          forward: { "20": { ...forward, status: "pending", b1_pct: null, b3_pct: null } },
        },
      },
    ],
    summary: {
      swing: {
        closed: 1,
        scalp: 0,
        short: 0,
        position: 1,
        long: 0,
        avg_holding_trading_days: "14.00",
        win_rate_pct: "0.00%",
        avg_pnl_display: "-650.00",
        total_pnl_display: "-650.00",
      },
      fill_swing: {
        bucket_basis: "fill",
        pairs: 2,
        win_rate_pct: "50.00%",
        avg_holding_trading_days: "9.5",
        avg_pnl_display: "-325.00",
        total_pnl_display: "-650.00",
      },
      holding_by_pnl: {
        flat_tolerance_display: "100.00",
        classes: [
          {
            code: "loss",
            label: "亏损",
            count: 1,
            avg_holding_trading_days: "14.0",
            avg_holding_trading_days_x10: 140,
          },
          {
            code: "flat",
            label: "保本",
            count: 0,
            avg_holding_trading_days: null,
            avg_holding_trading_days_x10: null,
          },
          {
            code: "win",
            label: "盈利",
            count: 0,
            avg_holding_trading_days: null,
            avg_holding_trading_days_x10: null,
          },
        ],
      },
      addon: {
        count: 1,
        rounds: 1,
        declined_after_addon: 1,
        declined_pct: "100.00%",
        share_pct: "50.00%",
        avg_dip_pct: "-4.20%",
        avg_dip_coverage: "1/1",
      },
      sell_fly: {
        sell_count: 1,
        rounds: 1,
        profit_take_fly: 0,
        stop_loss_rebound: 0,
        fly_pct: null,
        missed_profit_worst_display: null,
        missed_profit_end_display: null,
      },
      reason_coverage: {
        annotated: 1,
        total_rounds: 2,
        coverage_pct: "50.00%",
        by_code: [{ code: "plan_build", label: "计划建仓", count: 1, share_pct: "100.00%" }],
      },
      ranking: {
        basis: "benchmark_excess_20d",
        definition_version: "v1",
        n_sample: 4,
        n_eligible: 4,
        sample_status: "no_conclusion",
        combined_median_pct: "0.40%",
        combined_avg_pct: "0.40%",
        quadrant_counts: { both_positive: 2 },
        per_stock: [],
      },
      pending: { forward_recompute: 1, reason_missing: 1 },
    },
    next_cursor: null,
    has_more: false,
    missing: [],
    assumptions: ["多账户合并统计"],
    disclaimer:
      "以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益；相对基准为 hs300_tr ETF 代理；缺失/公司行动显示 —，不显示 0%。",
    ...overrides,
  };
}

function renderPage(entry = "/rounds/insights"): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <RoundInsights />
    </MemoryRouter>,
  );
}

/** 卖飞金额带符号：正 → 少赚、负 → 多赚，前端只拼后端串与标签。 */
function makePageWithMissedProfit(): RoundInsightsPage {
  const page = makePage();
  if (page.summary) {
    page.summary.sell_fly = {
      ...page.summary.sell_fly,
      profit_take_fly: 1,
      missed_profit_worst_display: "5206.21",
      missed_profit_end_display: "-21965.03",
      missed_profit_worst_abs_display: "5206.21",
      missed_profit_worst_label: "少赚",
      missed_profit_end_abs_display: "21965.03",
      missed_profit_end_label: "多赚",
    };
  }
  return page;
}

beforeEach(() => {
  vi.clearAllMocks();
  listInsights.mockResolvedValue(makePage());
  refreshInsights.mockResolvedValue({
    job_id: "job_1",
    status: "pending",
    type: "refresh_round_insights",
    links: { self: "/jobs/job_1", events: "/jobs/job_1/events" },
  });
});

afterEach(cleanup);

describe("RoundInsights 回合洞察页", () => {
  it("带符号数值统一着色：负为跌色、正为涨色、零与缺失保持中性", async () => {
    renderPage();

    await screen.findByText("-3.25%");
    // pnl：列表列 + 摘要平均/合计，全部为负 → 跌色。
    for (const node of screen.getAllByText("-650.00")) {
      expect(node.className).toBe("money money-neg");
    }
    expect(screen.getByText("-3.25%").className).toBe("money money-neg");
    // 收益率、b3（正）→ 涨色（b3 列 + 排名中位数/均值）。
    for (const node of screen.getAllByText("0.40%")) {
      expect(node.className).toBe("money money-pos");
    }
    // 零值（胜率 0.00%，列表 + 统计块）中性，不染涨跌色。
    for (const node of screen.getAllByText("0.00%")) {
      expect(node.className).not.toMatch(/money-(pos|neg)/);
    }
    // 缺失值（卖飞少赚 null → —）同样中性。
    expect(screen.getAllByText("—").some((node) => node.className === "money")).toBe(true);
  });

  it("零口径：直接渲染后端字符串，缺失为 — 且不出现裸码", async () => {
    renderPage();

    expect(await screen.findByText("-3.25%")).toBeTruthy();
    expect(screen.getAllByText("-650.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/100\.00%/).length).toBeGreaterThan(0);
    // 卖后 pending → —（不能是 0%）。
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("计划建仓")).toBeTruthy();
    expect(screen.getAllByText("加仓后下跌").length).toBeGreaterThan(0);
    expect(screen.getByText(/以下为事后统计，仅用于个人复盘/)).toBeTruthy();
    // 设计 AC-P5：页面不露 B0/B1/B2/B3 裸码。
    expect(screen.queryByText(/B0|B1|B2|B3/)).toBeNull();
    // 口径假设原样展示（后端产出）。
    expect(screen.getByText(/多账户合并统计/)).toBeTruthy();
    // AC-S3：fill 级配对单列展示，并明示不与回合级相加（前端不做任何加法）。
    expect(screen.getByTestId("fill-swing").textContent).toContain("配对（fill 级）2 笔");
    expect(screen.getByTestId("fill-swing").textContent).toContain("不与上方回合相加");
  });

  it("空态：无回合时提示下一步，不显示排名/伪造数据", async () => {
    listInsights.mockResolvedValue(makePage({ item_total: 0, items: [], summary: null }));
    renderPage();

    expect(await screen.findByText(/本月还没有可统计的回合/)).toBeTruthy();
    expect(screen.queryByText("20 日相对基准排序")).toBeNull();
  });

  it("降级：missing[] 提供原因并提示缺失数值显示 —", async () => {
    listInsights.mockResolvedValue(
      makePage({ missing: ["HOLDING_CALENDAR_MISSING", "BENCHMARK_BARS_MISSING"] }),
    );
    renderPage();

    expect(await screen.findByText(/数据不完整：HOLDING_CALENDAR_MISSING、BENCHMARK_BARS_MISSING/)).toBeTruthy();
    expect(screen.getByText(/缺失数值显示「—」，不显示 0%/)).toBeTruthy();
  });

  it("Tabs 只映射后端筛选参数（中线 → bucket=position）", async () => {
    renderPage();
    const tab = await screen.findByRole("tab", { name: "中线" });
    tab.click();

    await waitFor(() => {
      expect(listInsights).toHaveBeenLastCalledWith(
        expect.objectContaining({ bucket: "position" }),
      );
    });
  });

  it("卖飞金额：正数少赚、负数多赚（后端给绝对值串与标签，前端不判符号）", async () => {
    listInsights.mockResolvedValue(makePageWithMissedProfit());
    renderPage();

    await screen.findByText(/以下为事后统计/);
    const bodyText = document.body.textContent?.replace(/\s+/g, "") ?? "";
    expect(bodyText).toContain("最差少赚5206.21，期末多赚21965.03");
    expect(bodyText).not.toContain("-21965.03");
  });

  it("卖飞金额缺失：只渲染 —，不出现「多赚 —」组合", async () => {
    renderPage();

    await screen.findByText(/以下为事后统计/);
    const bodyText = document.body.textContent?.replace(/\s+/g, "") ?? "";
    expect(bodyText).toContain("最差—，期末—");
    expect(bodyText).not.toContain("多赚—");
  });

  it("证券类型默认只选股票；勾选基金后按多选请求后端", async () => {
    renderPage();

    await waitFor(() => {
      expect(listInsights).toHaveBeenCalledWith(
        expect.objectContaining({ security_type: ["stock"] }),
      );
    });

    screen.getByRole("checkbox", { name: "基金" }).click();
    await waitFor(() => {
      expect(listInsights).toHaveBeenLastCalledWith(
        expect.objectContaining({ security_type: ["stock", "fund"] }),
      );
    });
    const otherBox = screen.getByRole("checkbox", { name: "其他" }) as HTMLInputElement;
    expect(otherBox.checked).toBe(false);
  });

  it("刷新前瞻走幂等键；202 时提示后台作业并在完成后刷新列表", async () => {
    renderPage();
    const button = await screen.findByRole("button", { name: "刷新前瞻" });
    button.click();

    await waitFor(() => {
      expect(refreshInsights).toHaveBeenCalledWith(
        expect.objectContaining({ scope: "missing" }),
        expect.any(String),
      );
    });
    expect(await screen.findByText(/已提交后台刷新任务（job_1）/)).toBeTruthy();
  });

  it("期间默认今年全年并写进 URL；URL 已有 period 时不覆盖", async () => {
    renderPage();
    await waitFor(() => {
      expect(listInsights).toHaveBeenCalledWith(
        expect.objectContaining({ period: currentYearToken() }),
      );
    });

    cleanup();
    listInsights.mockClear();
    renderPage("/rounds/insights?period=2026-06");
    await waitFor(() => {
      expect(listInsights).toHaveBeenCalledWith(expect.objectContaining({ period: "2026-06" }));
    });
    // 只补空缺：用户选过的期间不会被默认年度重置。
    expect(listInsights.mock.calls.every((call) => call[0]?.period === "2026-06")).toBe(true);
  });

  it("持有期分布：饼图/柱状图懒加载，计数与保本容差全部来自后端", async () => {
    renderPage();

    // 两张图都走 lazy + Suspense，异步挂载。
    expect(await screen.findByTestId("nivo-pie")).toBeTruthy();
    expect(screen.getByTestId("nivo-bar")).toBeTruthy();
    // 图例只渲染后端计数（超短线 0 / 短线 0 / 中线 1 / 长线 0）；限定在图例内，避开同名 Tab 与统计块。
    const legend = within(screen.getByTestId("holding-legend"));
    expect(legend.getByText("超短线")).toBeTruthy();
    expect(legend.getByText("短线")).toBeTruthy();
    expect(legend.getByText("中线")).toBeTruthy();
    expect(legend.getByText("长线")).toBeTruthy();
    expect(legend.getByText("1")).toBeTruthy();
    // 保本容差说明由后端 flat_tolerance_display 渲染，前端不写死 100。
    expect(screen.getByText(/计为保本/).textContent).toContain("100.00");
  });

  it("持有统计表：数值全部来自后端 summary.swing，前端只排版", async () => {
    renderPage();

    await screen.findByTestId("nivo-pie");
    const stats = within(screen.getByTestId("holding-stats"));
    // 旧的一处重复统计已删除：整页只保留与图表同行的这一份。
    expect(screen.getAllByTestId("holding-stats")).toHaveLength(1);
    expect(screen.getAllByText("平均持有交易日")).toHaveLength(1);
    expect(stats.getByText("平均持有交易日")).toBeTruthy();
    expect(stats.getByText("14.00")).toBeTruthy();
    expect(stats.getByText("胜率")).toBeTruthy();
    expect(stats.getByText("0.00%")).toBeTruthy();
    // 盈亏两行共用后端串，不改小数位、不重算。
    expect(stats.getByText("平均盈亏")).toBeTruthy();
    expect(stats.getByText("合计盈亏")).toBeTruthy();
    expect(stats.getAllByText("-650.00")).toHaveLength(2);
    // 合计行跨两列：回合总数同样来自后端。
    expect(stats.getByText("共计")).toBeTruthy();
    expect(stats.getByText("1")).toBeTruthy();
    expect(stats.getByText("个回合")).toBeTruthy();
  });

  it("持有期分布空态：无已平仓回合时不渲染图表、不伪造 0 天", async () => {
    const page = makePage();
    if (page.summary) {
      page.summary.swing = {
        ...page.summary.swing,
        closed: 0,
        scalp: 0,
        short: 0,
        position: 0,
        long: 0,
      };
      page.summary.holding_by_pnl = {
        flat_tolerance_display: "100.00",
        classes: page.summary.holding_by_pnl.classes.map((row) => ({
          ...row,
          count: 0,
          avg_holding_trading_days: null,
          avg_holding_trading_days_x10: null,
        })),
      };
    }
    listInsights.mockResolvedValue(page);
    renderPage();

    expect(await screen.findByText(/暂无可绘制的持有期分布/)).toBeTruthy();
    expect(screen.queryByTestId("nivo-pie")).toBeNull();
    expect(screen.queryByTestId("nivo-bar")).toBeNull();
  });
});

it("旗标说明：末尾渲染后端返回的 code 与中英文说明（前端不拼文案）", async () => {
  listInsights.mockResolvedValue(
    makePage({
      flag_glossary: [
        {
          code: "addon",
          label_zh: "加仓",
          label_en: "Add-on",
          description_zh: "本回合内有买入发生在已有持仓之后。",
          description_en: "A buy happened while the security was already held.",
          kind: "item_flag",
        },
        {
          code: "addon_decline",
          label_zh: "加仓后下跌",
          label_en: "Add-on then decline",
          description_zh: "同时命中 addon 与 continued_decline。",
          description_en: "Both addon and continued_decline.",
          kind: "filter_alias",
        },
      ],
    }),
  );
  renderPage();

  const section = await screen.findByTestId("flag-glossary");
  expect(section.textContent).toContain("旗标说明");
  expect(section.textContent).toContain("addon");
  expect(section.textContent).toContain("本回合内有买入发生在已有持仓之后。");
  expect(section.textContent).toContain("A buy happened while the security was already held.");
});
