import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { Attribution } from "@sa/api-client";
import { AttributionPage } from "./Attribution";
import pageSource from "./Attribution.tsx?raw";
import { api } from "../client";

vi.mock("../client", () => ({
  api: { attribution: { get: vi.fn() } },
}));

const getAttribution = vi.mocked(api.attribution.get);

const FACTORS: Attribution["factors"] = [
  {
    code: "selection",
    name: "选股",
    amount: "-1900.00",
    ratio: "-0.5937",
    share: "0.5940",
    rank: 1,
    conclusion: "拖累 1,900.00 元",
    presentation: { bar_pct: "59.40%", ratio_pct: "-59.37%", share_pct: "59.40%", tone: "negative" },
  },
  {
    code: "sector",
    name: "行业",
    amount: "820.00",
    ratio: "0.2562",
    share: "0.2560",
    rank: 2,
    conclusion: "贡献 820.00 元",
    presentation: { bar_pct: "25.60%", ratio_pct: "25.62%", share_pct: "25.60%", tone: "positive" },
  },
  {
    code: "buy_timing",
    name: "买点",
    amount: "-120.00",
    ratio: "-0.0375",
    share: "0.0375",
    rank: 3,
    conclusion: "拖累 120.00 元",
    presentation: { bar_pct: "3.75%", ratio_pct: "-3.75%", share_pct: "3.75%", tone: "negative" },
  },
  {
    code: "sell_timing",
    name: "卖点",
    amount: "0.00",
    ratio: "0.0000",
    share: "0.0000",
    rank: 4,
    conclusion: "影响较小",
    presentation: { bar_pct: "0.00%", ratio_pct: "0.00%", share_pct: "0.00%", tone: "flat" },
  },
  {
    code: "fee",
    name: "费用",
    amount: "-410.00",
    ratio: "-0.1281",
    share: "0.1281",
    rank: 5,
    conclusion: "拖累 410.00 元",
    presentation: { bar_pct: "12.81%", ratio_pct: "-12.81%", share_pct: "12.81%", tone: "negative" },
  },
];

function attribution(overrides: Partial<Attribution> = {}): Attribution {
  return {
    model: "five_factor",
    ratio_basis: "amount/total_pnl",
    period: "2026-06",
    period_start: "2026-06-01",
    period_end: "2026-06-30",
    status: "ok",
    definition_version: "five-factor@1.0",
    total_pnl_basis: "asset_daily_mtm",
    total_pnl: "-3200.00",
    ratio_na_reason: null,
    base_capital: "1054000.00",
    factors: FACTORS,
    summary:
      "本期亏损 3,200.00 元，主要来自「选股」（-1,900.00 元，占构成 59.4%）；其次为「行业」（820.00 元）。未归属 120.00 元，见残差明细。",
    residual: "120.00",
    residual_ratio: "0.0375",
    residual_ratio_pct: "3.75%",
    residual_breakdown_labels: {},
    residual_breakdown: {
      corporate_action: "0.00",
      unattributed: "120.00",
      dividend: "0.00",
      interest: "0.00",
      other: "0.00",
      valuation_gap: "0.00",
    },
    sample: {
      n_segments: 14,
      n_attributed_segments: 12,
      n_rounds: 3,
      n_symbols: 5,
      min_n: 10,
      sample_status: "ok",
    },
    coverage: {
      capital_total: "1054000.00",
      capital_attributed: "1042000.00",
      capital_excluded: "12000.00",
      capital_ratio: "0.9886",
      capital_ratio_pct: "98.9%",
      base_basis: "mixed",
      segments_total: 14,
      segments_attributed: 12,
      symbols_total: 6,
      symbols_attributed: 5,
      unattributed: [
        {
          security_code: "110059",
          symbol_class: "convertible_bond",
          reason_code: "CB_EXCLUDED",
          amount: "120.00",
        },
      ],
    },
    missing: [],
    assumptions: {
      data_sources: { assets: "2026-06-30", market: "2026-06-30", sector: "2026-06-29" },
    },
    data_as_of: "2026-06-30",
    inputs_hash: "sha256:abc",
    links: {
      dashboard: "/dashboard",
      sectors: "/sectors",
      rounds: "/rounds",
      reports: "/reports",
    },
    ...overrides,
  };
}

function renderPage(initialEntry = "/attribution?period=2026-06") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AttributionPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getAttribution.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("AttributionPage", () => {
  it("渲染后端字段：summary、五因子、残差、条宽直接用 bar_pct", async () => {
    getAttribution.mockResolvedValue(attribution());
    renderPage();

    expect((await screen.findByTestId("attr-summary-text")).textContent).toContain(
      "本期亏损 3,200.00 元",
    );
    expect(screen.getByTestId("attr-status-badge").textContent).toBe("正常");
    expect(screen.getByTestId("attr-factor-selection").textContent).toContain("选股");
    expect(screen.getByTestId("attr-factor-selection").textContent).toContain("拖累 1,900.00 元");
    expect(screen.getByTestId("attr-factor-fee").textContent).toContain("费用");
    expect(screen.getByTestId("attr-capital-ratio").textContent).toContain("98.9%");
    expect(screen.getByTestId("attr-coverage-bases").textContent).toContain("按标的 5/6");
    expect(screen.getByTestId("attr-coverage-bases").textContent).toContain("市值与现金混合");
    expect(screen.getByTestId("attr-data-sources").textContent).toContain(
      "资产 2026-06-30 · 行情 2026-06-30 · 板块 2026-06-29",
    );

    const bar = screen.getByTestId("attr-factor-selection").querySelector(".attr-bar > i");
    expect(bar).not.toBeNull();
    expect((bar as HTMLElement).style.getPropertyValue("--w")).toBe("59.40%");
    expect(bar?.getAttribute("data-tone")).toBe("negative");
    expect(screen.getByTestId("attr-unattributed").textContent).toContain("110059");
  });

  it("degraded：渲染金额同时列出缺失项与 what_to_do", async () => {
    getAttribution.mockResolvedValue(
      attribution({
        status: "degraded",
        missing: [
          {
            code: "SECTOR_BENCHMARK_WEIGHTS_MISSING",
            field: "factors.sector.amount",
            severity: "warning",
            reason: "基准成分权重缺失，行业因子不输出金额。",
            what_to_do: "请先同步板块基准权重后重试。",
          },
        ],
      }),
    );
    renderPage();

    await screen.findByTestId("attr-summary-text");
    expect(screen.getByTestId("attr-status-badge").textContent).toBe("降级");
    expect(screen.getByTestId("attr-factor-selection").textContent).toContain("选股");
    expect(screen.getByTestId("attr-missing").textContent).toContain("基准成分权重缺失");
    expect(screen.getByTestId("attr-missing").textContent).toContain(
      "请先同步板块基准权重后重试。",
    );
  });

  it("blocked：不渲染任何金额，只渲染数据质量卡", async () => {
    getAttribution.mockResolvedValue(
      attribution({
        status: "blocked",
        total_pnl: null,
        summary: null,
        base_capital: null,
        residual: null,
        residual_ratio: null,
        residual_breakdown: null,
        coverage: null,
        factors: FACTORS.map((factor) => ({
          ...factor,
          amount: null,
          ratio: null,
          share: null,
          conclusion: null,
          presentation: { bar_pct: null, ratio_pct: null, tone: "flat" },
        })),
        missing: [
          {
            code: "ASSET_ROW_BLOCKED",
            field: "period_start",
            severity: "blocker",
            reason: "期初资产行不可信（blocked）。",
            what_to_do: "请先在资产页处理阻断行后重试。",
          },
        ],
      }),
    );
    renderPage();

    await screen.findByTestId("attr-quality");
    expect(screen.getByTestId("attr-status-badge").textContent).toBe("阻断");
    expect(screen.queryByTestId("attr-summary")).toBeNull();
    expect(screen.queryByTestId("attr-residual")).toBeNull();
    expect(screen.getByTestId("attr-quality")).toBeTruthy();
    expect(screen.getByTestId("attr-missing").textContent).toContain("期初资产行不可信");
    expect(screen.getByTestId("attr-period-end").textContent).toContain("2026-06-30");
  });

  it("insufficient：金额照给，结论区用后端样本不足句（不在前端写死）", async () => {
    getAttribution.mockResolvedValue(
      attribution({
        summary: "本期可归因交易样本不足（6 段），暂不生成结论。",
        sample: {
          n_segments: 6,
          n_attributed_segments: 4,
          n_rounds: 1,
          n_symbols: 2,
          min_n: 10,
          sample_status: "insufficient",
        },
      }),
    );
    renderPage();

    expect((await screen.findByTestId("attr-summary-text")).textContent).toContain("样本不足");
    expect(screen.getByTestId("attr-factor-selection").textContent).toContain(
      "-1,900.00 元",
    );
  });

  it("空账户态：渲染后端固定句与零金额，不渲染缺失项", async () => {
    getAttribution.mockResolvedValue(
      attribution({
        status: "ok",
        total_pnl: "0.00",
        base_capital: "0.00",
        summary: "本期无交易/无持仓变化。",
        ratio_na_reason: "TOTAL_PNL_ZERO",
        residual: "0.00",
        residual_ratio: null,
        residual_breakdown: {
          corporate_action: "0.00",
          unattributed: "0.00",
          dividend: "0.00",
          interest: "0.00",
          other: "0.00",
          valuation_gap: "0.00",
        },
        factors: FACTORS.map((factor) => ({
          ...factor,
          amount: "0.00",
          ratio: null,
          share: null,
          conclusion: null,
          presentation: { bar_pct: "0.00%", ratio_pct: null, tone: "flat" },
        })),
        sample: {
          n_segments: 0,
          n_attributed_segments: 0,
          n_rounds: 0,
          n_symbols: 0,
          min_n: 10,
          sample_status: "insufficient",
        },
        coverage: {
          capital_total: "0.00",
          capital_attributed: "0.00",
          capital_excluded: "0.00",
          capital_ratio: null,
          base_basis: "market",
          segments_total: 0,
          segments_attributed: 0,
          symbols_total: 0,
          symbols_attributed: 0,
          unattributed: [],
        },
      }),
    );
    renderPage();

    expect((await screen.findByTestId("attr-summary-text")).textContent).toBe(
      "本期无交易/无持仓变化。",
    );
    expect(screen.getByTestId("attr-factor-selection").textContent).toContain("0.00");
    expect(screen.queryByTestId("attr-missing")).toBeNull();
  });

  it("残差去向：桶按金额降序、正负含义、残差比 >100% 提示（U-08）", async () => {
    getAttribution.mockResolvedValue(
      attribution({
        residual: "109609.30",
        residual_ratio_pct: "130.18%",
        residual_breakdown: {
          interest: "5.97",
          unattributed_price_pnl: "445625.33",
          valuation_gap: "-336022.00",
        },
        residual_breakdown_labels: {
          interest: "利息",
          unattributed_price_pnl: "未归因标的损益",
          valuation_gap: "估值差（未能解释）",
        },
      }),
    );
    renderPage();

    await screen.findByText("残差去向");
    const amounts = Array.from(
      screen.getByTestId("attr-residual-buckets").querySelectorAll("li > span:last-child"),
    ).map((node) => node.textContent ?? "");
    expect(amounts[0]).toContain("445,625.33");
    expect(amounts[1]).toContain("5.97");
    expect(amounts[2]).toContain("-336,022.00");
    expect(screen.getByTestId("attr-residual").textContent).toContain("正数=未归因收益");
    expect(screen.getByTestId("attr-residual").textContent).toContain("残差大于本期总损益");
  });

  it("数据质量：阻断项优先、显示用户动作、开发命令折叠（U-10）", async () => {
    getAttribution.mockResolvedValue(
      attribution({
        status: "degraded",
        missing: [
          {
            code: "SECTOR_BAR_MISSING",
            field: "factors.sector.amount",
            field_label: "因子归因",
            severity: "warning",
            reason: "5 只标的缺板块指数行情。",
            what_to_do: "补板块日线",
          },
          {
            code: "BRIDGE_MISMATCH",
            field: "total_pnl",
            field_label: "总损益",
            severity: "blocker",
            reason: "MTM 损益与桥接式差额超过 1 分。",
            what_to_do: "对账页检查断链",
          },
        ],
      }),
    );
    renderPage();

    await screen.findByTestId("attr-missing");
    const items = Array.from(screen.getByTestId("attr-missing").querySelectorAll("li"));
    expect(items[0]?.getAttribute("data-severity")).toBe("blocker");
    expect(items[0]?.textContent).toContain("去对账页检查断链");
    expect(items[0]?.textContent).toContain("影响：总损益");
    const details = items[0]?.querySelector("details.attr-tech-details");
    expect(details).not.toBeNull();
    expect(details?.textContent).toContain("BRIDGE_MISMATCH");
  });

  it("错误态：渲染 RFC 7807 人话", async () => {
    getAttribution.mockRejectedValue({
      code: "VALIDATION_ERROR",
      status: 400,
      user_message: "期间格式不正确",
      what_to_do: "请使用 YYYY-MM 格式",
      retryable: false,
      request_id: "req_1",
      errors: [],
      detail: "",
      title: "",
      type: "about:blank",
    });
    renderPage();

    expect(await screen.findByText(/期间格式不正确/)).toBeTruthy();
  });

  it("零口径：页面源码不出现数值解析/格式化调用", () => {
    for (const forbidden of ["parseFloat", "parseInt", "Number(", "toFixed"]) {
      expect(pageSource).not.toContain(forbidden);
    }
  });
});
