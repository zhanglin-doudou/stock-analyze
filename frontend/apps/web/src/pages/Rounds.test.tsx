import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Job, JobAccepted, JobPage, Round, RoundPage } from "@sa/api-client";
import { Rounds } from "./Rounds";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    rounds: {
      list: vi.fn(),
      get: vi.fn(),
      fills: vi.fn(),
      rebuild: vi.fn(),
    },
    jobs: {
      list: vi.fn(),
      get: vi.fn(),
    },
  },
}));

const listRounds = vi.mocked(api.rounds.list);
const rebuild = vi.mocked(api.rounds.rebuild);
const listJobs = vi.mocked(api.jobs.list);
const getJob = vi.mocked(api.jobs.get);

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

function makePage(items: Round[]): RoundPage {
  return { items, next_cursor: null, has_more: false };
}

function emptyJobs(): JobPage {
  return { items: [], next_cursor: null, backlog: {} };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: "job_rebuild",
    type: "rebuild_rounds",
    status: "succeeded",
    progress: 100,
    stage: "done",
    message: null,
    result_ref: null,
    error: null,
    attempt: 1,
    max_attempts: 3,
    available_at: "2026-06-20T08:00:00Z",
    heartbeat_at: null,
    created_at: "2026-06-20T08:00:00Z",
    started_at: "2026-06-20T08:00:01Z",
    finished_at: "2026-06-20T08:00:05Z",
    ...overrides,
  };
}

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/rounds"]}>
      <Rounds />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listRounds.mockResolvedValue(makePage([makeRound()]));
  listJobs.mockResolvedValue(emptyJobs());
  rebuild.mockResolvedValue({
    job_id: "job_rebuild",
    type: "rebuild_rounds",
    status: "pending",
    links: { self: "/api/v1/jobs/job_rebuild", events: "/api/v1/jobs/job_rebuild/events" },
  } satisfies JobAccepted);
  getJob.mockResolvedValue(makeJob());
});

afterEach(cleanup);

describe("Rounds 回合列表页", () => {
  it("展示回合：代码/状态/盈亏与详情入口", async () => {
    renderPage();

    expect(await screen.findByText("600519 贵州茅台")).toBeTruthy();
    // 状态既出现在筛选下拉，也出现在行内徽标。
    expect(screen.getAllByText("已平仓").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("650.00")).toBeTruthy();
    expect(screen.getByText("150.0000")).toBeTruthy();
    expect(screen.getByText("1600.00")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();

    const detail = screen.getByRole("link", { name: "详情" });
    expect(detail.getAttribute("href")).toBe("/rounds/rnd_1");
  });

  it("无回合时提示「导入账单后自动建立」并给出导入入口", async () => {
    listRounds.mockResolvedValue(makePage([]));
    renderPage();

    expect(await screen.findByText(/导入账单后系统会自动按 FIFO 建立回合/)).toBeTruthy();
    const importLinks = screen.getAllByRole("link", { name: "导入账单" });
    expect(importLinks.length).toBeGreaterThan(0);
    expect(importLinks[0].getAttribute("href")).toBe("/import");
  });

  it("按证券代码查询时把过滤条件传给 API", async () => {
    renderPage();
    await screen.findByText("600519 贵州茅台");

    fireEvent.change(screen.getByPlaceholderText("如 600519"), { target: { value: "000001" } });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() =>
      expect(listRounds).toHaveBeenLastCalledWith(expect.objectContaining({ security_code: "000001" })),
    );
  });

  it("「重建回合」提交异步任务并轮询到成功后刷新列表", async () => {
    renderPage();
    await screen.findByText("600519 贵州茅台");

    fireEvent.click(screen.getByRole("button", { name: "重建回合" }));

    await waitFor(() => expect(rebuild).toHaveBeenCalledTimes(1));
    expect(rebuild.mock.calls[0][0]).toEqual({ scope: "full" });
    expect(rebuild.mock.calls[0][1]).toBeTruthy();

    expect(await screen.findByText(/回合已重建完成/)).toBeTruthy();
    await waitFor(() => expect(listRounds.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
