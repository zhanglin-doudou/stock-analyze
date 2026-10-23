import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Round, RoundFillPage } from "@sa/api-client";
import { RoundDetail } from "./RoundDetail";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    rounds: {
      get: vi.fn(),
      fills: vi.fn(),
    },
  },
}));

const getRound = vi.mocked(api.rounds.get);
const getFills = vi.mocked(api.rounds.fills);

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

  it("数据异常回合展示超卖提示", async () => {
    getRound.mockResolvedValue(
      makeRound({ status: "data_error", data_error: true, oversell_quantity: "100.0000" }),
    );
    renderDetail();

    expect(await screen.findByText(/超出可用持仓/)).toBeTruthy();
    expect(screen.getAllByText(/100.0000/).length).toBeGreaterThan(0);
  });
});
