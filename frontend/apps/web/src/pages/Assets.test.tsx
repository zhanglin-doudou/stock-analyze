import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  ApiProblemError,
  type AssetBuildAccepted,
  type AssetBuildState,
  type AssetPage,
  type AssetPoint,
  type Job,
  type JobEvent,
  type JobPage,
  type Problem,
} from "@sa/api-client";
import { Assets } from "./Assets";
import assetsSource from "./Assets.tsx?raw";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    assets: {
      list: vi.fn(),
      build: vi.fn(),
    },
    jobs: {
      list: vi.fn(),
      get: vi.fn(),
      events: vi.fn(),
    },
  },
}));

const listAssets = vi.mocked(api.assets.list);
const buildAssets = vi.mocked(api.assets.build);
const listJobs = vi.mocked(api.jobs.list);
const getJob = vi.mocked(api.jobs.get);
const jobEvents = vi.mocked(api.jobs.events);

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
    definition_version: "1.1",
    ...overrides,
  };
}

function makePoint(overrides: Partial<AssetPoint> = {}): AssetPoint {
  return {
    date: "2026-06-30",
    cash: "-70000.00",
    money_fund: "20000.00",
    balance: "-50000.00",
    market_value: "170620.00",
    total_assets: "100620.00",
    holding_cost: "170000.00",
    unrealized_pnl: "620.00",
    holding_count: 1,
    cashflow: "0.00",
    prev_total_assets: "100000.00",
    daily_pnl: "620.00",
    daily_return: "0.00620000",
    daily_return_pct: "0.62%",
    status: "ok",
    status_reason: null,
    ...overrides,
  };
}

function makePage(overrides: Partial<AssetPage> = {}): AssetPage {
  return {
    items: [makePoint()],
    next_cursor: null,
    has_more: false,
    build: makeBuild(),
    definition_version: "1.1",
    data_as_of: "2026-09-30",
    ...overrides,
  };
}

/** 未构建（build.state=missing）：除 state/rows_count/last_mode/has_new_market_data/definition_version 外全 null。 */
function missingPage(items: AssetPoint[] = []): AssetPage {
  return {
    items,
    next_cursor: null,
    has_more: false,
    build: {
      state: "missing",
      last_status: null,
      last_built_at: null,
      last_built_through: null,
      last_mode: null,
      last_rebuild_from: null,
      last_rows_written: 0,
      rows_count: 0,
      assumption: null,
      status_reason: null,
      has_new_market_data: false,
      definition_version: "1.1",
    },
    definition_version: "1.1",
    data_as_of: null,
  };
}

function blockedPage(reason = "缺少期初资产锚点（无现金余额行）"): AssetPage {
  return makePage({
    items: [],
    data_as_of: null,
    build: makeBuild({
      state: "blocked",
      last_status: "blocked",
      last_built_through: null,
      last_mode: null,
      last_rebuild_from: null,
      last_rows_written: 0,
      rows_count: 0,
      assumption: null,
      status_reason: reason,
    }),
  });
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

/** SSE 正常结束：先停留一小段时间让 JobProgress 可被观测，再发 done 事件。 */
function doneEvents(status: Job["status"] = "succeeded"): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    await new Promise((resolve) => setTimeout(resolve, 30));
    yield {
      id: "1",
      event: "done",
      data: { job_id: "job_assets", status, result_ref: null },
    };
  }
  return generator();
}

/** SSE 失败：每次迭代都抛错（JobProgress 会重试 SSE；页面轮询负责兜底）。 */
function failingEvents(): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    throw new Error("sse down");
  }
  return generator();
}

function assetProblem(): Problem {
  return {
    type: "about:blank",
    title: "Invalid Cursor",
    status: 400,
    detail: "cursor 损坏",
    code: "INVALID_CURSOR",
    user_message: "分页游标不合法。",
    what_to_do: "回到第一页后重试。",
    retryable: true,
    request_id: "req_assets_1",
  };
}

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/assets"]}>
      <Assets />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listAssets.mockResolvedValue(makePage());
  listJobs.mockResolvedValue(emptyJobs());
  getJob.mockResolvedValue(makeJob());
  buildAssets.mockResolvedValue(accepted());
  jobEvents.mockImplementation(() => noEvents());
});

afterEach(cleanup);

describe("Assets 资产表页", () => {
  it("FT-01 首次进入不自动构建：「构建资产表」+ 空态 + 导入链接", async () => {
    listAssets.mockResolvedValue(missingPage());

    renderPage();

    expect(screen.getByRole("button", { name: "构建资产表" })).toBeTruthy();
    expect(await screen.findByText(/还没有资产记录/)).toBeTruthy();
    const importLinks = screen.getAllByRole("link", { name: "导入账单" });
    expect(importLinks[0].getAttribute("href")).toBe("/import");
    expect(buildAssets).not.toHaveBeenCalled();
  });

  it("FT-02 构建流程：调用 build(auto, key)、展示 JobProgress、SSE 结束后重拉列表并变「更新资产表」", async () => {
    const after: AssetPage = makePage({
      items: [makePoint({ date: "2026-06-30" }), makePoint({ date: "2026-07-31" })],
      build: makeBuild({ state: "current", last_mode: "full", last_rows_written: 125 }),
    });
    listAssets.mockResolvedValueOnce(missingPage()).mockResolvedValue(after);
    getJob.mockResolvedValue(makeJob({ status: "running" }));
    jobEvents.mockImplementation(() => doneEvents("succeeded"));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "构建资产表" }));

    await waitFor(() => expect(buildAssets).toHaveBeenCalledTimes(1));
    expect(buildAssets.mock.calls[0][0]).toEqual({ mode: "auto" });
    expect(buildAssets.mock.calls[0][1]).toBeTruthy();
    expect(await screen.findByTestId("assets-job-progress")).toBeTruthy();

    expect(await screen.findByTestId("assets-notice")).toBeTruthy();
    expect(screen.getByTestId("assets-notice").textContent).toContain(
      "已构建 125 天资产记录，数据截至 2026-09-30。",
    );
    expect(screen.getByRole("button", { name: "更新资产表" })).toBeTruthy();
    await waitFor(() => expect(listAssets.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("FT-03 增量提示：last_mode=incremental 展示重算天数与起点", async () => {
    listAssets.mockResolvedValueOnce(missingPage()).mockResolvedValue(
      makePage({
        build: makeBuild({
          state: "current",
          last_mode: "incremental",
          last_rebuild_from: "2026-08-17",
          last_rows_written: 45,
        }),
      }),
    );
    getJob.mockResolvedValue(makeJob({ status: "running" }));
    jobEvents.mockImplementation(() => doneEvents("succeeded"));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "构建资产表" }));

    expect(await screen.findByText(/增量更新完成：重算 45 天（自 2026-08-17 起）/)).toBeTruthy();
    expect(screen.getByText(/数据截至 2026-09-30/)).toBeTruthy();
  });

  it("FT-03 no-op 提示：last_mode=noop 展示「已是最新」", async () => {
    listAssets.mockResolvedValueOnce(missingPage()).mockResolvedValue(
      makePage({ build: makeBuild({ state: "current", last_mode: "noop", last_rows_written: 0 }) }),
    );
    getJob.mockResolvedValue(makeJob({ status: "running" }));
    jobEvents.mockImplementation(() => doneEvents("succeeded"));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "构建资产表" }));

    expect(await screen.findByText("资产表已是最新，无需更新。")).toBeTruthy();
  });

  it("FT-03 stale 提示：build.state=stale → 「有新流水/新行情，建议更新」", async () => {
    listAssets.mockResolvedValue(
      makePage({ build: makeBuild({ state: "stale", has_new_market_data: true }) }),
    );

    renderPage();

    expect(await screen.findByTestId("assets-stale")).toBeTruthy();
    expect(screen.getByText(/有新流水\/新行情，建议更新/)).toBeTruthy();
    expect(screen.getByText(/检测到新行情数据/)).toBeTruthy();
  });

  it("FT-04/FT-04b 列表：金额/百分比原样展示、blocked 行「—」+ badge、0.00% 不着色", async () => {
    listAssets.mockResolvedValue(
      makePage({
        items: [
          makePoint({ date: "2026-06-30", daily_return_pct: "0.00%", daily_pnl: "0.00" }),
          makePoint({
            date: "2026-06-29",
            daily_pnl: "-620.00",
            daily_return_pct: "-0.61%",
            unrealized_pnl: "-120.00",
            status: "degraded",
            status_reason: null,
          }),
          makePoint({
            date: "2026-06-28",
            cash: null,
            money_fund: null,
            balance: null,
            market_value: null,
            total_assets: null,
            holding_cost: null,
            unrealized_pnl: null,
            holding_count: 0,
            cashflow: null,
            prev_total_assets: null,
            daily_pnl: null,
            daily_return: null,
            daily_return_pct: null,
            status: "blocked",
            status_reason: "缺少 600519 行情",
          }),
        ],
      }),
    );

    renderPage();
    await screen.findByText("2026-06-30");

    // 表头
    for (const header of [
      "日期",
      "总资产",
      "现金",
      "持仓市值",
      "持仓成本",
      "浮动盈亏",
      "日收益额",
      "日收益率",
      "外部现金流",
      "持仓数",
      "状态",
    ]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeTruthy();
    }

    // 金额/百分比原样展示（同一数值在正常/降级两行都会出现）
    expect(screen.getAllByText("100620.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("-70000.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("170620.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("170000.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("-620.00")).toBeTruthy();

    // 零值不着色；正负只按符号着色
    expect(screen.getByText("0.00%").className).toBe("money");
    for (const zero of screen.getAllByText("0.00")) {
      expect(zero.className).toBe("money");
    }
    expect(screen.getByText("-0.61%").className).toBe("money money-neg");
    expect(screen.getByText("-620.00").className).toBe("money money-neg");

    // blocked 行：数值列「—」+ 阻断 badge（status_reason 作为 title）
    const blockedRowBadges = screen.getAllByText("阻断");
    expect(blockedRowBadges.length).toBeGreaterThanOrEqual(1);
    const blockedBadge = blockedRowBadges[0];
    expect(blockedBadge.className).toContain("badge-blocked");
    expect(blockedBadge.getAttribute("title")).toBe("缺少 600519 行情");
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(8);

    // degraded badge
    const degraded = screen.getByText("降级");
    expect(degraded.className).toContain("badge-degraded");
    // 正常 badge
    expect(screen.getByText("正常").className).toContain("badge-ok");
  });

  it("FT-05 分页：请求带 cursor，追加不重复", async () => {
    const firstRow = makePoint({ date: "2026-06-30" });
    const secondRow = makePoint({ date: "2026-05-31" });
    listAssets.mockResolvedValueOnce(
      makePage({ items: [firstRow], has_more: true, next_cursor: "cursor_1" }),
    );
    listAssets.mockResolvedValueOnce(makePage({ items: [firstRow, secondRow] }));

    renderPage();
    await screen.findByText("2026-06-30");

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await waitFor(() =>
      expect(listAssets).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 50, cursor: "cursor_1" })),
    );
    expect(await screen.findByText("2026-05-31")).toBeTruthy();
    // 首页那条不重复出现
    expect(screen.getAllByText("2026-06-30").length).toBe(1);
  });

  it("FT-06 列表错误：RFC 7807 展示 user_message/what_to_do/错误码并可重试", async () => {
    listAssets.mockRejectedValueOnce(new ApiProblemError(assetProblem()));

    renderPage();

    expect(await screen.findByText("分页游标不合法。")).toBeTruthy();
    expect(screen.getByText(/回到第一页后重试。/)).toBeTruthy();
    expect(screen.getByText("INVALID_CURSOR")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();

    listAssets.mockResolvedValue(makePage());
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("2026-06-30")).toBeTruthy();
  });

  it("FT-06 job failed：轮询到 failed 后展示 job 错误码", async () => {
    listAssets.mockResolvedValue(missingPage());
    getJob.mockResolvedValue(
      makeJob({
        job_id: "job_assets",
        status: "failed",
        progress: 80,
        error: {
          type: "about:blank",
          title: "Build failed",
          status: 500,
          detail: "boom",
          code: "ASSET_BUILD_FAILED",
          user_message: "资产表构建失败。",
          what_to_do: "请检查行情数据后重试。",
          retryable: true,
          request_id: "req_job_1",
        },
      }),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "构建资产表" }));

    expect(await screen.findByText("资产表构建失败。")).toBeTruthy();
    expect(screen.getByText("ASSET_BUILD_FAILED")).toBeTruthy();
    expect(screen.getByText(/可稍后重试/)).toBeTruthy();
  });

  it("FT-08 汇总条：数据截至/记录数/最近构建/模式/重写行数/锚点/口径版本", async () => {
    renderPage();

    const summary = await screen.findByTestId("assets-summary");
    expect(screen.getByText("2026-09-30")).toBeTruthy();
    expect(screen.getByText(/2026-09-30 08:00:00 UTC/)).toBeTruthy();
    expect(summary.textContent).toContain("记录数：125");
    expect(summary.textContent).toContain("最近模式：增量");
    expect(summary.textContent).toContain("重写行数：45");
    expect(summary.textContent).toContain("锚点：期初反推（back_solved）");
    expect(summary.textContent).toContain("口径版本：1.1");
  });

  it("FT-08 blocked 横幅：state=blocked 显示阻断原因与空态", async () => {
    listAssets.mockResolvedValue(blockedPage());

    renderPage();

    expect(await screen.findByTestId("assets-blocked-banner")).toBeTruthy();
    expect(screen.getAllByText(/资产表被阻断/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("缺少期初资产锚点（无现金余额行）")).toBeTruthy();
    expect(screen.getByText("资产表被阻断，暂无可用记录。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "更新资产表" })).toBeTruthy();
  });

  it("FT-08 degraded 提示：last_status=degraded 展示部分阻断/降级提示", async () => {
    listAssets.mockResolvedValue(makePage({ build: makeBuild({ last_status: "degraded" }) }));

    renderPage();

    expect(await screen.findByTestId("assets-degraded")).toBeTruthy();
    expect(screen.getByText(/部分日期存在阻断\/降级/)).toBeTruthy();
  });

  it("FT-10 可访问性：提交中按钮 disabled + aria-busy + 「构建中…」", async () => {
    listAssets.mockResolvedValue(missingPage());
    let resolveBuild: (value: AssetBuildAccepted) => void = () => undefined;
    buildAssets.mockImplementation(
      () =>
        new Promise<AssetBuildAccepted>((resolve) => {
          resolveBuild = resolve;
        }),
    );

    renderPage();
    const button = await screen.findByRole("button", { name: "构建资产表" });
    fireEvent.click(button);

    const busy = await screen.findByRole("button", { name: "构建中…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(busy.getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      resolveBuild(accepted());
    });
  });

  it("FT-11 进入页面接管 pending/running 的 build_assets，不重复点击", async () => {
    listJobs.mockResolvedValue({
      items: [makeJob({ job_id: "job_takeover", status: "running", progress: 40 })],
      next_cursor: null,
      backlog: {},
    });
    getJob.mockResolvedValue(makeJob({ job_id: "job_takeover", status: "running", progress: 40 }));

    renderPage();

    expect(await screen.findByTestId("assets-job-progress")).toBeTruthy();
    expect(screen.getByText(/job_takeover/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "更新中…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(buildAssets).not.toHaveBeenCalled();
  });

  it("FT-12 SSE 连续失败时页面轮询兜底到 succeeded 并刷新列表", async () => {
    listAssets.mockResolvedValueOnce(missingPage()).mockResolvedValue(
      makePage({ build: makeBuild({ state: "current", last_mode: "full", last_rows_written: 125 }) }),
    );
    jobEvents.mockImplementation(() => failingEvents());
    getJob.mockResolvedValue(makeJob({ status: "succeeded" }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "构建资产表" }));

    expect(await screen.findByTestId("assets-notice")).toBeTruthy();
    expect(screen.getByTestId("assets-notice").textContent).toContain(
      "已构建 125 天资产记录，数据截至 2026-09-30。",
    );
    await waitFor(() => expect(listAssets.mock.calls.length).toBeGreaterThanOrEqual(2));
    // 兜底收敛后不再占用按钮
    expect(screen.getByRole("button", { name: "更新资产表" })).toBeTruthy();
  });

  it("FT-07 零口径：源码不含 Number(/parseFloat/parseInt/toFixed", () => {
    expect(assetsSource).not.toMatch(/\bNumber\s*\(/);
    expect(assetsSource).not.toMatch(/\bparseFloat\b/);
    expect(assetsSource).not.toMatch(/\bparseInt\b/);
    expect(assetsSource).not.toMatch(/\.toFixed\s*\(/);
    // 百分比来自后端，不自行换算
    expect(assetsSource).not.toMatch(/daily_return\b(?!_pct)/);
  });
});
