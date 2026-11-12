import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { RoundInsightsPage } from "@sa/api-client";
import { RoundInsights } from "./RoundInsights";
import { buildFlagRoundBars } from "./rounds/RoundFlagCharts";
import type { ChartPalette } from "./dashboard/tradeFlagChartColors";
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
        flags: ["bottom_buy", "drawdown"],
        status_counts: { bottom_buy: 2, stop_loss_sold_early: 0, profit_sold_early: 0 },
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
      // 默认全部旗标计数为 0（后端恒定返回词表全部条目，便于图例固定）；非零场景在专门用例里覆盖。
      flag_round_counts: [
        { flag: "bottom_buy", count: 0 },
        { flag: "stop_loss_sold_early", count: 0 },
        { flag: "profit_sold_early", count: 0 },
        { flag: "drawdown", count: 0 },
        { flag: "intraday_t", count: 0 },
        { flag: "swing_t", count: 0 },
        { flag: "intraday_rebalance_out", count: 0 },
        { flag: "intraday_rebalance_in", count: 0 },
      ],
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
    expect(screen.getAllByText("止损卖飞").length).toBeGreaterThan(0);
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

  it("回合旗标计数：覆盖全部 8 类，计数取后端 summary（全量），不按当前页 items 累加", async () => {
    // 首页 items 只带 1 个回合；若前端累加 items 会数少，这里 summary 给的是全期计数。
    const page = makePage();
    if (page.summary) {
      page.summary.flag_round_counts = [
        { flag: "bottom_buy", count: 12 },
        { flag: "stop_loss_sold_early", count: 4 },
        { flag: "profit_sold_early", count: 7 },
        { flag: "drawdown", count: 9 },
        { flag: "intraday_t", count: 3 },
        { flag: "swing_t", count: 6 },
        { flag: "intraday_rebalance_out", count: 2 },
        { flag: "intraday_rebalance_in", count: 1 },
      ];
    }
    listInsights.mockResolvedValue(page);
    renderPage();

    const chart = await screen.findByTestId("flag-round-charts");
    // 标题在卡片层（与「加仓与后续下跌」「卖飞」同层级），并标明卖飞的新口径。
    expect(screen.getByText("回合旗标统计（卖飞按账单级 5 日）")).toBeTruthy();
    // 指标行与汇总块同一写法：逐项「标签 + 回合数」，同一回合只计一次。
    const metrics = screen.getByTestId("flag-round-metrics").textContent ?? "";
    expect(metrics).toContain("抄底 12 个回合");
    expect(metrics).toContain("止损卖飞 4 个回合");
    expect(metrics).toContain("止盈卖飞 7 个回合");
    expect(metrics).toContain("收益回撤 9 个回合");
    expect(metrics).toContain("日内做T 3 个回合");
    expect(metrics).toContain("波段做T 6 个回合");
    expect(metrics).toContain("日内调仓卖出 2 个回合");
    expect(metrics).toContain("日内调仓买入 1 个回合");
    expect(chart.textContent).toContain("与卖飞汇总的");
    // 与持有期柱状图共用 `ResponsiveBar` 桩，因此断言数量 >= 2（懒加载已挂载）。
    await waitFor(() => expect(screen.getAllByTestId("nivo-bar").length).toBeGreaterThan(1));
    const legend = within(screen.getByTestId("flag-round-legend"));
    const labels = [
      "抄底",
      "止损卖飞",
      "止盈卖飞",
      "收益回撤",
      "日内做T",
      "波段做T",
      "日内调仓卖出",
      "日内调仓买入",
    ];
    for (const label of labels) {
      expect(legend.getByText(label)).toBeTruthy();
    }
    // 计数原样来自 summary；items 里只有 1 个回合（bottom_buy×2），不会被累加成 2。
    expect(legend.getByText("12")).toBeTruthy();
    expect(legend.getByText("4")).toBeTruthy();
    expect(legend.getByText("7")).toBeTruthy();
    expect(legend.getByText("9")).toBeTruthy();
    expect(legend.getByText("3")).toBeTruthy();
    expect(legend.getByText("6")).toBeTruthy();
    expect(legend.getByText("2")).toBeTruthy();
    expect(legend.getByText("1")).toBeTruthy();
    // 每个旗标只占一个图例，不按次数拆开。
    for (const label of labels) {
      expect(legend.getAllByText(label)).toHaveLength(1);
    }
  });

  it("回合旗标计数空态：全部为 0 时只说明无命中，不画空坐标轴", async () => {
    renderPage();

    // 当前页 items 有 bottom_buy×2，但 summary 计数为 0 → 证明前端不做累加。
    expect(await screen.findByText("抄底×2")).toBeTruthy();
    expect(screen.getByTestId("flag-round-charts").textContent).toContain(
      "没有任何回合命中旗标",
    );
    expect(screen.getAllByTestId("nivo-bar")).toHaveLength(1);
  });

  it("口径文案：卖飞标明 20 日/仅平仓笔与分母，加仓注明按回合计、不等同抄底", async () => {
    renderPage();

    await screen.findByText(/以下为事后统计/);
    const bodyText = document.body.textContent?.replace(/\s+/g, "") ?? "";
    expect(bodyText).toContain("20日卖飞（仅平仓笔）");
    expect(bodyText).toContain("止盈卖飞占卖出回合0/1=—");
    expect(bodyText).toContain("加仓1个回合（按回合计，不等同于抄底）");
  });
});

it("旗标说明：末尾渲染后端返回的 code 与中英文说明（前端不拼文案）", async () => {
  listInsights.mockResolvedValue(
    makePage({
      flag_glossary: [
        {
          code: "bottom_buy",
          label_zh: "抄底",
          label_en: "Bottom buy",
          description_zh: "回合内命中即时抄底或短期抄底的买入账单个数。",
          description_en: "Number of buy bills flagged as an instant or short-term bottom.",
          kind: "item_flag",
        },
        {
          code: "sold_too_early",
          label_zh: "卖飞",
          label_en: "Sold too early (any)",
          description_zh: "查询别名：命中止损卖飞或止盈卖飞。",
          description_en: "Query alias for stop-loss or profit sold too early.",
          kind: "filter_alias",
        },
      ],
    }),
  );
  renderPage();

  const section = await screen.findByTestId("flag-glossary");
  expect(section.textContent).toContain("旗标说明");
  expect(section.textContent).toContain("bottom_buy");
  expect(section.textContent).toContain("回合内命中即时抄底或短期抄底的买入账单个数。");
  expect(section.textContent).toContain("Number of buy bills flagged as an instant or short-term bottom.");
});

describe("buildFlagRoundBars", () => {
  const palette = {
    series: {
      round_bottom_buy: "#101010",
      round_stop_loss_sold_early: "#202020",
      round_profit_sold_early: "#303030",
      round_drawdown: "#404040",
      round_intraday_t: "#505050",
      round_swing_t: "#606060",
      round_intraday_rebalance_out: "#707070",
      round_intraday_rebalance_in: "#808080",
    },
  } as unknown as ChartPalette;

  it("覆盖词表全部 8 个回合旗标：颜色走 tokens、标签不按次数拆开", () => {
    const bars = buildFlagRoundBars(
      [
        { flag: "bottom_buy", count: 3 },
        { flag: "stop_loss_sold_early", count: 0 },
        { flag: "profit_sold_early", count: 5 },
        { flag: "drawdown", count: 9 },
        { flag: "intraday_t", count: 1 },
        { flag: "swing_t", count: 2 },
        { flag: "intraday_rebalance_out", count: 4 },
        { flag: "intraday_rebalance_in", count: 6 },
      ],
      palette,
    );
    expect(bars).toEqual([
      { id: "bottom_buy", label: "抄底", count: 3, color: "#101010" },
      { id: "stop_loss_sold_early", label: "止损卖飞", count: 0, color: "#202020" },
      { id: "profit_sold_early", label: "止盈卖飞", count: 5, color: "#303030" },
      { id: "drawdown", label: "收益回撤", count: 9, color: "#404040" },
      { id: "intraday_t", label: "日内做T", count: 1, color: "#505050" },
      { id: "swing_t", label: "波段做T", count: 2, color: "#606060" },
      { id: "intraday_rebalance_out", label: "日内调仓卖出", count: 4, color: "#707070" },
      { id: "intraday_rebalance_in", label: "日内调仓买入", count: 6, color: "#808080" },
    ]);
  });

  it("数据质量三态（查询别名）与未知码不入图", () => {
    const bars = buildFlagRoundBars(
      [
        { flag: "reason_missing", count: 7 },
        { flag: "horizon_pending", count: 7 },
        { flag: "corporate_action", count: 7 },
        { flag: "sold_too_early", count: 7 },
      ],
      palette,
    );
    expect(bars).toEqual([]);
  });

  it("后端未返回该字段（空数组）时不伪造柱", () => {
    expect(buildFlagRoundBars([], palette)).toEqual([]);
  });
});
