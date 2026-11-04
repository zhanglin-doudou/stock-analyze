import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Round, RoundFillPage, RoundInsightDetail } from "@sa/api-client";
import { RoundDetail } from "./RoundDetail";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    rounds: {
      get: vi.fn(),
      fills: vi.fn(),
      insight: vi.fn(),
    },
    trades: {
      putReason: vi.fn(),
      deleteReason: vi.fn(),
    },
  },
}));

const getRound = vi.mocked(api.rounds.get);
const getFills = vi.mocked(api.rounds.fills);
const getInsight = vi.mocked(api.rounds.insight);

function makeRound(overrides: Partial<Round> = {}): Round {
  return {
    round_id: "rnd_1",
    security_code: "600519",
    security_name: "贵州茅台",
    status: "closed",
    open_date: "2026-06-02",
    close_date: "2026-06-20",
    quantity: "150.0000",
    cost: "1600.00",
    proceeds: "2250.00",
    dividend: "0.00",
    pnl: "650.00",
    return_rate: "0.40625000",
    holding_days: 14,
    open_trade_id: "trd_buy_1",
    close_trade_id: "trd_sell_1",
    data_error: false,
    oversell_quantity: "0.0000",
    fills_count: 2,
    rebuilt_at: "2026-06-20T08:00:00Z",
    ...overrides,
  };
}

function makeFillPage(): RoundFillPage {
  return {
    items: [
      {
        fill_id: 1,
        sequence: 1,
        buy_trade_id: "trd_buy_1",
        sell_trade_id: "trd_sell_1",
        quantity: "100.0000",
        open_date: "2026-06-02",
        close_date: "2026-06-20",
        holding_days: 14,
      },
      {
        fill_id: 2,
        sequence: 2,
        buy_trade_id: "trd_buy_2",
        sell_trade_id: "trd_sell_1",
        quantity: "50.0000",
        open_date: "2026-06-10",
        close_date: "2026-06-20",
        holding_days: 8,
      },
    ],
    total: 2,
  };
}

function makeInsight(): RoundInsightDetail {
  const forward = {
    horizon: 20,
    status: "ok" as const,
    base_shifted: false,
    price_jump_review: false,
    b0_pct: "0.50%",
    b0_net_pct: "0.30%",
    b1_pct: "2.00%",
    b2_pct: "2.40%",
    b3_pct: "-1.10%",
    max_close_display: "110.00",
    min_close_display: "95.00",
  };
  return {
    round_id: "rnd_1",
    security_code: "600519",
    security_name: "贵州茅台",
    status: "closed",
    open_date: "2026-06-02",
    close_date: "2026-06-20",
    holding_days_legacy: 14,
    holding_trading_days: 14,
    bucket: "swing",
    pnl: "650.00",
    return_rate_pct: "40.62%",
    buy_trade: {
      trade_id: "trd_buy_1",
      date: "2026-06-02",
      price_display: "100.000000",
      quantity_display: "100.0000",
      amount_display: "-10000.00",
      reason_label: "计划建仓",
      reason: {
        annotated_at: "2026-06-21T08:00:00Z",
        emotion_code: "calm",
        emotion_label: "平静",
        late_annotated: true,
        reason_code: "plan_build",
        reason_label: "计划建仓",
        reason_text: null,
        side: "buy",
        tags: ["业绩超预期"],
        trade_id: "trd_buy_1",
        version: 2,
      },
      forward: { "5": forward, "10": forward, "20": forward },
    },
    sell_trade: {
      trade_id: "trd_sell_1",
      date: "2026-06-20",
      price_display: "120.000000",
      quantity_display: "150.0000",
      amount_display: "18000.00",
      reason_label: "止盈",
      reason: null,
      forward: { "20": { ...forward, status: "pending", b1_pct: null, b2_pct: null, b3_pct: null } },
    },
    fills: [],
    flags: ["profit_taking", "profit_take_fly"],
    quality: ["SAMPLE_INSUFFICIENT"],
    ranking: {
      rank: 12,
      buy_excess_pct: "1.20%",
      sell_avoid_pct: "-3.40%",
      combined_pct: "-1.10%",
      quadrant: "buy_positive_sell_negative",
      basis: "benchmark_excess_20d",
      definition_version: "v1",
    },
    disclaimer: "以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益。",
  };
}

function renderDetail(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/rounds/rnd_1"]}>
      <Routes>
        <Route path="/rounds/:roundId" element={<RoundDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getRound.mockResolvedValue(makeRound());
  getFills.mockResolvedValue(makeFillPage());
  getInsight.mockResolvedValue(makeInsight());
});

afterEach(cleanup);

describe("RoundDetail 回合详情页", () => {
  it("展示回合口径与 FIFO 配对明细", async () => {
    renderDetail();

    expect(await screen.findByText(/600519 贵州茅台/)).toBeTruthy();
    expect(screen.getByText("650.00")).toBeTruthy();
    expect(screen.getByText("150.0000")).toBeTruthy();
    expect(screen.getByText("1600.00")).toBeTruthy();
    expect(screen.getByText("0.40625000")).toBeTruthy();
    expect(screen.getByText(/共 2 笔配对/)).toBeTruthy();
    expect(screen.getAllByText("trd_buy_1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("trd_buy_2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("trd_sell_1").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "← 返回回合列表" }).getAttribute("href")).toBe("/rounds");
  });

  it("持仓中回合给出「暂无配对明细」说明，不展示负数/伪造数据", async () => {
    getRound.mockResolvedValue(
      makeRound({ status: "open", close_date: null, close_trade_id: null, fills_count: 0, quantity: "50.0000" }),
    );
    getFills.mockResolvedValue({ items: [], total: 0 });
    renderDetail();

    expect(await screen.findByText(/持仓中回合尚无卖出配对明细/)).toBeTruthy();
    expect(screen.getByText("持仓中")).toBeTruthy();
  });

  it("展示操作理由、5/10/20 行情日与标签排序（中文术语、缺失显示 —）", async () => {
    renderDetail();

    expect(await screen.findByText("操作理由")).toBeTruthy();
    expect(screen.getAllByText("执行价差（毛，相对当日收盘）").length).toBeGreaterThan(0);
    expect(screen.getByText("事后标注")).toBeTruthy();
    expect(screen.getByText("业绩超预期")).toBeTruthy();
    // 卖后未到 20 日：pending → 只显示 —（不补 0%），并带原因 tooltip。
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("卖飞")).toBeTruthy();
    expect(screen.getByText("买入后相对基准（20 日）")).toBeTruthy();
    expect(screen.getAllByText("-1.10%").length).toBeGreaterThan(0);
  });

  it("洞察接口失败时回合详情/配对明细仍可用", async () => {
    getInsight.mockRejectedValue(new Error("boom"));
    renderDetail();

    expect(await screen.findByText(/600519 贵州茅台/)).toBeTruthy();
    expect(screen.getByText(/共 2 笔配对/)).toBeTruthy();
    expect(screen.queryByText("操作理由")).toBeNull();
  });

  it("数据异常回合展示超卖提示", async () => {
    getRound.mockResolvedValue(
      makeRound({ status: "data_error", data_error: true, oversell_quantity: "100.0000" }),
    );
    renderDetail();

    expect(await screen.findByText(/超出可用持仓/)).toBeTruthy();
    expect(screen.getAllByText(/100.0000/).length).toBeGreaterThan(0);
  });
});
