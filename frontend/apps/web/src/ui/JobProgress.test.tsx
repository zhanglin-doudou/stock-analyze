import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { Job, JobEvent, Problem } from "@sa/api-client";
import { JobProgress } from "./JobProgress";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    jobs: {
      get: vi.fn(),
      events: vi.fn(),
    },
  },
}));

const getJob = vi.mocked(api.jobs.get);
const jobEvents = vi.mocked(api.jobs.events);

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

function events(items: JobEvent[]): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    for (const item of items) yield item;
  }
  return generator();
}

/** SSE 正常结束但没有任何事件（EOF 无 done/error）。 */
function noEvents(): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    /* no-op */
  }
  return generator();
}

/** SSE 连接直接失败（如网络中断 / 5xx）。 */
function failingEvents(): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    throw new Error("sse down");
  }
  return generator();
}

function flush(): Promise<void> {
  return act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  getJob.mockReset();
  jobEvents.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("JobProgress 收尾职责", () => {
  it("SSE done 事件直接收尾：透传 status/result_ref，且不触发兜底轮询", async () => {
    jobEvents.mockImplementation(() =>
      events([
        { event: "progress", data: { job_id: "job_assets", progress: 40, stage: "scan" } },
        {
          event: "done",
          data: { job_id: "job_assets", status: "succeeded", result_ref: "ref_rows" },
        },
      ]),
    );
    const onDone = vi.fn();
    render(<JobProgress jobId="job_assets" onDone={onDone} />);

    await flush();

    expect(onDone).toHaveBeenCalledWith("succeeded", "ref_rows", undefined);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(getJob).not.toHaveBeenCalled();
  });

  it("SSE error 事件带错误体：透传到 onDone，不再二次请求详情", async () => {
    const problem: Problem = {
      type: "about:blank",
      title: "Build failed",
      status: 500,
      detail: "anchor missing",
      code: "ASSET_ANCHOR_MISSING",
      user_message: "缺少期初资产锚点。",
      what_to_do: "请补录期初持仓。",
      retryable: false,
      request_id: "req_1",
    };
    jobEvents.mockImplementation(() =>
      events([{ event: "error", data: { job_id: "job_assets", error: problem } }]),
    );
    const onDone = vi.fn();
    render(<JobProgress jobId="job_assets" onDone={onDone} />);

    await flush();

    expect(onDone).toHaveBeenCalledWith("failed", null, problem);
    expect(getJob).not.toHaveBeenCalled();
  });

  it("合成终态缺 error（重连落在终态之后）时补一次任务详情拿真实原因", async () => {
    const problem: Problem = {
      type: "about:blank",
      title: "Build failed",
      status: 500,
      detail: "quote missing",
      code: "MARKET_DATA_MISSING",
      user_message: "缺少行情数据。",
      what_to_do: "先同步行情。",
      retryable: false,
      request_id: "req_2",
    };
    jobEvents.mockImplementation(() =>
      events([{ event: "error", data: { job_id: "job_assets", status: "failed" } }]),
    );
    getJob.mockResolvedValue(makeJob({ status: "failed", error: problem }));
    const onDone = vi.fn();
    render(<JobProgress jobId="job_assets" onDone={onDone} />);

    await flush();

    expect(getJob).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith("failed", null, problem);
  });

  it("SSE 流正常结束却没收终态 → 兜底 jobs.get 收尾，不再静默返回", async () => {
    jobEvents.mockImplementation(() => noEvents());
    getJob.mockResolvedValue(makeJob({ status: "succeeded", result_ref: "ref_fallback" }));
    const onDone = vi.fn();
    render(<JobProgress jobId="job_assets" onDone={onDone} />);

    await flush();
    await flush();

    expect(getJob).toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledWith("succeeded", "ref_fallback", undefined);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("兜底轮询遇到非终态按 3 秒间隔重试，终态后停止", async () => {
    vi.useFakeTimers();
    jobEvents.mockImplementation(() => noEvents());
    getJob
      .mockResolvedValueOnce(makeJob({ status: "running", progress: 30 }))
      .mockResolvedValue(makeJob({ status: "succeeded" }));
    const onDone = vi.fn();
    render(<JobProgress jobId="job_assets" onDone={onDone} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getJob).toHaveBeenCalledTimes(1);
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("30");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(getJob).toHaveBeenCalledTimes(2);
    expect(onDone).toHaveBeenCalledWith("succeeded", null, undefined);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(getJob).toHaveBeenCalledTimes(2);
  });

  it("SSE 首连失败即启用兜底轮询，不必等 5 次重试耗尽", async () => {
    jobEvents.mockImplementation(() => failingEvents());
    getJob.mockResolvedValue(makeJob({ status: "succeeded" }));
    const onDone = vi.fn();
    render(<JobProgress jobId="job_assets" onDone={onDone} />);

    await flush();
    await flush();

    expect(getJob).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith("succeeded", null, undefined);
  });
});
