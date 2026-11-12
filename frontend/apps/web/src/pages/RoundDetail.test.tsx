import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type {
  Round,
  RoundFillPage,
  RoundInsightDetail,
  TradeReason,
  TradeSideInsightOut,
} from "@sa/api-client";
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
const putReason = vi.mocked(api.trades.putReason);

function makeRound(overrides: Partial<Round> = {}): Round {
  return {
    round_id: "rnd_1",
    security_code: "600519",
    security_name: "贵州茅台",
    status: "closed",
    open_date: "2026-06-02",
    close_date: "2026-06-20",
    quantity: "150",
    cost: "1600.00",
    proceeds: "2250.00",
    dividend: "0.00",
    pnl: "650.00",
    return_rate: "0.40625000",
    holding_days: 14,
    open_trade_id: "trd_buy_1",
    close_trade_id: "trd_sell_1",
    data_error: false,
    oversell_quantity: "0",
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
        quantity: "100",
        open_date: "2026-06-02",
        close_date: "2026-06-20",
        holding_days: 14,
      },
      {
        fill_id: 2,
        sequence: 2,
        buy_trade_id: "trd_buy_2",
        sell_trade_id: "trd_sell_1",
        quantity: "50",
        open_date: "2026-06-10",
        close_date: "2026-06-20",
        holding_days: 8,
      },
    ],
    total: 2,
  };
}

function makeReason(overrides: Partial<TradeReason> = {}): TradeReason {
  return {
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
    ...overrides,
  };
}

function makeSide(overrides: Partial<TradeSideInsightOut> = {}): TradeSideInsightOut {
  return {
    trade_id: "trd_buy_1",
    date: "2026-06-02",
    price_display: "100.000000",
    quantity_display: "100",
    amount_display: "-10000.00",
    reason_label: null,
    reason: null,
    forward: {},
    ...overrides,
  };
}

function makeInsight(overrides: Partial<RoundInsightDetail> = {}): RoundInsightDetail {
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
    bucket: "position",
    pnl: "650.00",
    return_rate_pct: "40.62%",
    buy_trade: {
      trade_id: "trd_buy_1",
      date: "2026-06-02",
      price_display: "100.000000",
      quantity_display: "100",
      amount_display: "-10000.00",
      reason_label: "计划建仓",
      reason: makeReason(),
      forward: { "5": forward, "10": forward, "20": forward },
    },
    sell_trade: {
      trade_id: "trd_sell_1",
      date: "2026-06-20",
      price_display: "120.000000",
      quantity_display: "150",
      amount_display: "18000.00",
      reason_label: "止盈",
      reason: null,
      forward: { "20": { ...forward, status: "pending", b1_pct: null, b2_pct: null, b3_pct: null } },
    },
    fills: [],
    flags: ["bottom_buy", "stop_loss_sold_early", "drawdown"],
    status_counts: { bottom_buy: 2, stop_loss_sold_early: 1, profit_sold_early: 0 },
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
    ...overrides,
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

/** 展开按钮的无障碍名称随展开态变化：展开 → 收起。 */
function toggle(rowLabel: string): HTMLButtonElement {
  return screen.getByRole("button", { name: new RegExp(`(展开|收起)流水 ${rowLabel} 的 5/10/20 行情日`) });
}

/** DataTable 主数据行（排除展开的子表面板行）。 */
function mainRows(table: HTMLElement): HTMLTableRowElement[] {
  return [...table.querySelectorAll<HTMLTableRowElement>(":scope > tbody > tr:not(.dt-expando)")];
}

beforeEach(() => {
  vi.clearAllMocks();
  getRound.mockResolvedValue(makeRound());
  getFills.mockResolvedValue(makeFillPage());
  getInsight.mockResolvedValue(makeInsight());
});

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(navigator, "clipboard");
});

describe("RoundDetail 回合详情页", () => {
  it("展示回合口径与 FIFO 配对明细", async () => {
    renderDetail();

    expect(await screen.findByText(/600519 贵州茅台/)).toBeTruthy();
    expect(screen.getByText("650.00")).toBeTruthy();
    // 数量「150」：回合口径 + 卖出流水各一次（买入是 100）；整数份额不带小数点/尾零。
    expect(screen.getAllByText("150")).toHaveLength(2);
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
      makeRound({ status: "open", close_date: null, close_trade_id: null, fills_count: 0, quantity: "50" }),
    );
    getFills.mockResolvedValue({ items: [], total: 0 });
    renderDetail();

    expect(await screen.findByText(/持仓中回合尚无卖出配对明细/)).toBeTruthy();
    expect(screen.getByText("持仓中")).toBeTruthy();
  });

  it("买卖两张表按流水列出：id/日期/数量/价格/金额/执行价差/操作理由", async () => {
    renderDetail();

    expect(await screen.findByText("买入（1 笔）")).toBeTruthy();
    expect(screen.getByText("卖出（1 笔）")).toBeTruthy();
    const buyTable = screen.getByRole("table", { name: "买入流水与事后表现" });
    const headCells = [...buyTable.querySelectorAll(":scope > thead > tr > th")];
    expect(headCells.map((th) => th.textContent)).toEqual([
      "流水id",
      "日期",
      "数量",
      "价格",
      "金额",
      "执行价差（相对当日收盘）",
      "账单旗标",
      "操作理由",
    ]);

    // 逐笔字段全部来自后端定点字符串：数量不补零、金额保留负号、执行价差用 b0。
    const buyRow = mainRows(buyTable)[0];
    const buyCells = [...buyRow.querySelectorAll(":scope > td")].map((td) => td.textContent);
    expect(buyRow.textContent).toContain("trd_buy_1");
    expect(buyRow.textContent).toContain("2026-06-02");
    // 数量列：整数份额不带尾零（"100"）；价格列仍守 6 位小数。
    expect(buyCells[2]).toBe("100");
    expect(buyCells[3]).toBe("100.000000");
    expect(buyRow.textContent).toContain("-10000.00");
    expect(buyRow.textContent).toContain("0.50%");
    // 金额负 → 跌色；执行价差正 → 涨色（与回合洞察页同一 `signedClass`）。
    expect(within(buyRow).getByText("-10000.00").className).toBe("money money-neg");
    expect(within(buyRow).getByText("0.50%").className).toBe("money money-pos");

    // 「操作理由」与「执行价差」是以列为单位（买卖各一张表），不再整段重复。
    expect(screen.getAllByText("操作理由")).toHaveLength(2);
    expect(screen.getAllByText("执行价差（相对当日收盘）")).toHaveLength(2);
    expect(screen.queryByText("执行价差（毛，相对当日收盘）")).toBeNull();
    // 买卖两侧各一次执行价差，值来自 b0（与 horizon 无关）。
    expect(screen.getAllByText("0.50%")).toHaveLength(2);

    const sellTable = screen.getByRole("table", { name: "卖出流水与事后表现" });
    const sellRow = mainRows(sellTable)[0];
    expect(sellRow.textContent).toContain("18000.00");
  });

  it("账单旗标列渲染后端文案，说明面板默认折叠", async () => {
    const base = makeInsight();
    getInsight.mockResolvedValue({
      ...base,
      buy_trade: {
        ...base.buy_trade!,
        trade_flags: [
          {
            code: "addon_average",
            label_zh: "均价加仓",
            label_en: "Add-on at average cost",
            status: "hit",
            status_label: "命中",
            lookahead: false,
            lookahead_label: null,
            reason: null,
          },
        ],
      },
      trade_flag_glossary: [
        {
          code: "addon_average",
          label_zh: "均价加仓",
          label_en: "Add-on at average cost",
          description_zh: "含费净买价落在持仓均价的上下 1% 以内。",
          description_en: "Within ±1% of the average cost.",
          side: "buy",
          lookahead: false,
        },
        {
          code: "short_term_bottom",
          label_zh: "短期抄底",
          label_en: "Short-term bottom",
          description_zh: "含费净买价距前后各 5 个行情日最低收盘价 5% 以内。",
          description_en: "Within 5% of the 10-day lowest close.",
          side: "buy",
          lookahead: true,
        },
        {
          code: "short_term_underwater",
          label_zh: "短期被套",
          label_en: "Short-term underwater",
          description_zh: "买入后连续 5 个行情日收盘价都低于含费净买价。",
          description_en: "Every close in the next 5 sessions stays below the net buy price.",
          side: "buy",
          lookahead: true,
        },
        {
          code: "sell_drawdown",
          label_zh: "回撤",
          label_en: "Drawdown",
          description_zh: "净卖价跌破前 5 日最低收盘价 1% 以上，但仍在成本线上方。",
          description_en: "Broke the 5-day low by more than 1% while still above cost.",
          side: "sell",
          lookahead: false,
        },
        {
          code: "short_term_sold_early",
          label_zh: "短期卖飞",
          label_en: "Sold too early",
          description_zh: "含费净卖价低于卖出后第 5 个行情日收盘价 5% 以上。",
          description_en: "Net sell price is more than 5% below the 5th future close.",
          side: "sell",
          lookahead: true,
        },
      ],
    });
    renderDetail();

    expect((await screen.findAllByText("均价加仓")).length).toBeGreaterThan(0);
    const panel = document.querySelector("details.trade-flag-glossary") as HTMLDetailsElement;
    expect(panel).toBeTruthy();
    expect(panel.open).toBe(false);
    // 词表由后端驱动：改名与新增旗标都能直接展示，旧名称不再出现
    expect(panel.textContent).toContain("短期抄底");
    expect(panel.textContent).toContain("短期被套");
    expect(panel.textContent).toContain("短期卖飞");
    expect(panel.textContent).toContain("回撤");
    expect(panel.textContent).not.toContain("短期成功");
  });

  it("同一笔卖出参与多个配对时按 trade 去重，行数 = 流水笔数", async () => {
    const base = makeInsight();
    const buy1 = base.buy_trade!;
    const sell1 = base.sell_trade!;
    const buy2 = makeSide({
      trade_id: "trd_buy_2",
      date: "2026-06-10",
      quantity_display: "50",
      amount_display: "-5000.00",
    });
    const sell2 = makeSide({
      trade_id: "trd_sell_2",
      date: "2026-06-21",
      quantity_display: "50",
      amount_display: "6000.00",
      reason: null,
      forward: {},
    });
    getInsight.mockResolvedValue({
      ...base,
      // 3 个 fill，但买入只有 2 笔、卖出也只有 2 笔（sell1 参与两个配对）。
      fills: [
        { buy: buy1, sell: sell1 },
        { buy: buy2, sell: sell1 },
        { buy: buy2, sell: sell2 },
      ],
    });
    renderDetail();

    expect(await screen.findByText("买入（2 笔）")).toBeTruthy();
    expect(screen.getByText("卖出（2 笔）")).toBeTruthy();
    const buyTable = screen.getByRole("table", { name: "买入流水与事后表现" });
    const buyRows = mainRows(buyTable);
    expect(buyRows).toHaveLength(2); // 2 笔流水（展开面板行不计）
    // 日期升序：06-02 在 06-10 之前。
    expect(buyRows[0].textContent).toContain("2026-06-02");
    expect(buyRows[1].textContent).toContain("2026-06-10");
    const sellTable = screen.getByRole("table", { name: "卖出流水与事后表现" });
    expect(mainRows(sellTable)).toHaveLength(2);
  });

  it("5/10/20 子表默认展开：aria-expanded/aria-controls 与键盘可用", async () => {
    renderDetail();

    const button = await screen.findByRole("button", {
      name: "收起流水 trd_buy_1 的 5/10/20 行情日",
    });
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const panelId = button.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    const panel = document.getElementById(panelId as string);
    expect(panel).toBeTruthy();
    const subTable = within(panel as HTMLElement).getByRole("table");
    expect(within(subTable).getAllByRole("row")).toHaveLength(4); // 表头 + 3 行口径
    expect(within(subTable).getByRole("columnheader", { name: "5 日" })).toBeTruthy();
    expect(within(subTable).getByRole("columnheader", { name: "10 日" })).toBeTruthy();
    expect(within(subTable).getByRole("columnheader", { name: "20 日" })).toBeTruthy();
    // 子表里的百分比同样走统一符号着色（b3 为负）。
    expect(within(subTable).getAllByText("2.00%")).toHaveLength(3);

    // 原生 button：Enter/Space 由浏览器触发 click，这里直接触发同样走 onClick。
    fireEvent.click(button);
    const collapsed = screen.getByRole("button", {
      name: "展开流水 trd_buy_1 的 5/10/20 行情日",
    });
    expect(collapsed.getAttribute("aria-expanded")).toBe("false");
    expect(collapsed.getAttribute("aria-controls")).toBeNull();
    expect(screen.queryByTestId("trade-forward-trd_buy_1")).toBeNull();
  });

  it("流水超过 10 笔时子表默认折叠（首屏过长）", async () => {
    const base = makeInsight();
    const buys = Array.from({ length: 6 }, (_, i) =>
      makeSide({ trade_id: `trd_buy_${i + 1}`, date: `2026-06-0${i + 1}`, forward: {} }),
    );
    const sells = Array.from({ length: 6 }, (_, i) =>
      makeSide({ trade_id: `trd_sell_${i + 1}`, date: `2026-06-2${i + 1}`, forward: {} }),
    );
    getInsight.mockResolvedValue({
      ...base,
      buy_trade: buys[0],
      sell_trade: sells[0],
      fills: [
        ...buys.map((buy) => ({ buy, sell: sells[0] })),
        ...sells.map((sell) => ({ buy: buys[0], sell })),
      ],
    });
    renderDetail();

    expect(await screen.findByText("买入（6 笔）")).toBeTruthy();
    expect(screen.getByText("卖出（6 笔）")).toBeTruthy();
    // 12 笔 > 阈值 10 → 默认全部折叠。
    expect(screen.getAllByRole("button", { name: /^展开流水 / })).toHaveLength(12);
    expect(document.querySelectorAll(".dt-expando")).toHaveLength(0);

    fireEvent.click(toggle("trd_buy_2"));
    expect(
      screen.getByRole("button", { name: "收起流水 trd_buy_2 的 5/10/20 行情日" }),
    ).toBeTruthy();
  });

  it("展示标签/排序与中文术语", async () => {
    renderDetail();

    expect(await screen.findByText("买卖流水与事后表现")).toBeTruthy();
    expect(screen.getByText("事后标注")).toBeTruthy();
    expect(screen.getByText("业绩超预期")).toBeTruthy();
    expect(screen.getByText("抄底×2")).toBeTruthy();
    expect(screen.getByText("收益回撤")).toBeTruthy();
    expect(screen.getByText("买入后相对基准（20 日）")).toBeTruthy();
    expect(screen.getAllByText("-1.10%").length).toBeGreaterThan(0);
  });

  it("卖出流水子表默认展开，直接内联「待满 20 个交易日」", async () => {
    renderDetail();

    expect(await screen.findByText("卖出（1 笔）")).toBeTruthy();
    expect(screen.getAllByText("—（待满 20 个交易日）")).toHaveLength(3);
  });

  it("空值单元格内联后端原因：公司行动 / 行情缺失 / 待满交易日", async () => {
    const base = makeInsight();
    const buy = base.buy_trade!;
    const ok10 = buy.forward!["10"]!;
    const ok20 = buy.forward!["20"]!;
    getInsight.mockResolvedValue({
      ...base,
      buy_trade: {
        ...buy,
        forward: {
          "5": { ...ok20, status: "no_bar", b1_pct: null, b2_pct: null, b3_pct: null },
          "10": ok10,
          "20": { ...ok20, status: "corporate_action", b1_pct: null, b2_pct: null, b3_pct: null },
        },
      },
    });
    renderDetail();

    expect(await screen.findByText("买入（1 笔）")).toBeTruthy();

    // 每档 3 行（事件后走势/成交后收益/相对基准）都带原因，不再是裸「—」。
    expect(screen.getAllByText("—（窗口含分红等公司行动）")).toHaveLength(3);
    expect(screen.getAllByText("—（行情缺失）").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText("—（待满 20 个交易日）")).toHaveLength(3);
    expect(screen.queryByText("—")).toBeNull();
    // 内联文案之外保留 tooltip 完整说明（三行各自带同一 status 的 title）。
    expect(screen.getAllByTitle("窗口内含公司行动（未复权）")).toHaveLength(3);
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
      makeRound({ status: "data_error", data_error: true, oversell_quantity: "100" }),
    );
    renderDetail();

    expect(await screen.findByText(/超出可用持仓/)).toBeTruthy();
    expect(screen.getAllByText("100").length).toBeGreaterThan(0);
  });

  it("长流水 id 头尾保留，短 id 不截断；复制按钮复制完整 id", async () => {
    const fullId = "trd_01HZX9K2HY3M4N5P6Q7R8S9TVYT13";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    getInsight.mockResolvedValue(
      makeInsight({ buy_trade: makeSide({ trade_id: fullId }), fills: [] }),
    );
    renderDetail();

    expect(await screen.findByText("trd_01…YT13")).toBeTruthy();
    // 短 id（≤12 字符）原样展示，不截断（配对明细里同样出现）。
    expect(screen.getAllByText("trd_sell_1").length).toBeGreaterThan(0);
    expect(screen.getByTitle(fullId)).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: `复制流水 ${fullId} 的完整 ID` }),
    );
    expect(await screen.findByText("已复制")).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(fullId);
  });

  it("复制失败给出可见提示", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    renderDetail();

    fireEvent.click(
      await screen.findByRole("button", { name: "复制流水 trd_buy_1 的完整 ID" }),
    );
    expect(await screen.findByText("复制失败")).toBeTruthy();
  });

  it("理由保存在成功后局部回填（不整页重取）", async () => {
    const saved = makeReason({ trade_id: "trd_sell_1", side: "sell", reason_code: "plan_reduce", reason_label: "计划减仓", version: 1 });
    putReason.mockResolvedValue({ reason: saved, etag: '"1"' });
    renderDetail();

    const sellTable = await screen.findByRole("table", { name: "卖出流水与事后表现" });
    fireEvent.click(within(sellTable).getByRole("button", { name: "添加理由" }));
    fireEvent.click(within(sellTable).getByRole("button", { name: "保存理由" }));

    await waitFor(() =>
      expect(putReason).toHaveBeenCalledWith("trd_sell_1", expect.anything(), undefined),
    );
    expect(within(sellTable).getByText("计划减仓")).toBeTruthy();
    expect(getInsight).toHaveBeenCalledTimes(1);
  });

  it("理由保存失败保留已填内容并展示错误", async () => {
    putReason.mockRejectedValue(new Error("boom"));
    renderDetail();

    const sellTable = await screen.findByRole("table", { name: "卖出流水与事后表现" });
    fireEvent.click(within(sellTable).getByRole("button", { name: "添加理由" }));
    fireEvent.change(within(sellTable).getByLabelText(/补充说明/), {
      target: { value: "计划外减仓" },
    });
    fireEvent.click(within(sellTable).getByRole("button", { name: "保存理由" }));

    expect(await within(sellTable).findByRole("alert")).toBeTruthy();
    expect(screen.getByText("发生了未预期的错误。")).toBeTruthy();
    // 表单仍在，输入内容未丢。
    expect((within(sellTable).getByLabelText(/补充说明/) as HTMLTextAreaElement).value).toBe(
      "计划外减仓",
    );
  });
});

it("旗标说明：详情页末尾渲染后端返回的 code 与中英文说明", async () => {
  getInsight.mockResolvedValue(
    makeInsight({
      flag_glossary: [
        {
          code: "bottom_buy",
          label_zh: "抄底",
          label_en: "Bottom buy",
          description_zh: "回合内命中即时抄底或短期抄底的买入账单个数。",
          description_en: "Number of buy bills flagged as an instant or short-term bottom.",
          kind: "item_flag",
        },
      ],
    }),
  );
  renderDetail();

  const section = await screen.findByTestId("flag-glossary");
  expect(section.textContent).toContain("bottom_buy");
  expect(section.textContent).toContain("回合内命中即时抄底或短期抄底的买入账单个数。");
  expect(section.textContent).toContain("Number of buy bills flagged as an instant or short-term bottom.");
});
