import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Job, JobAccepted, JobEvent, JobPage, MetricBatch, MetricItem } from "@sa/api-client";
import { latestCompletedMonth } from "../lib/date";
import { Metrics } from "./Metrics";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    metrics: {
      batch: vi.fn(),
    },
    jobs: {
      list: vi.fn(),
      get: vi.fn(),
      events: vi.fn(),
      rebuildPeriod: vi.fn(),
    },
  },
}));

const batchMetrics = vi.mocked(api.metrics.batch);
const listJobs = vi.mocked(api.jobs.list);
const getJob = vi.mocked(api.jobs.get);
const jobEvents = vi.mocked(api.jobs.events);
const rebuildPeriod = vi.mocked(api.jobs.rebuildPeriod);

const CURRENT_MONTH = latestCompletedMonth();

function makeItem(overrides: Partial<MetricItem> = {}): MetricItem {
  return {
    metric_id: "perf.pnl_ratio",
    definition_version: "1.0",
    domain: "perf",
    period: CURRENT_MONTH,
    status: "ok",
    sample_status: "ok",
    reason: null,
    value: "1.50000000",
    unit: "ratio",
    precision: 8,
    n: 4,
    n_pending: 0,
    ci_low: null,
    ci_high: null,
    data_as_of: "2026-06-30",
    presentation: {
      display_name: "盈亏比",
      direction: "higher_better",
      confidence: { level: "high", label: "可信", reason: "样本与窗口满足门槛" },
      progress: {
        current: 4,
        required: 10,
        remaining: 6,
        unit: "samples",
        unit_label: "笔/日",
        label: "还差 6 笔/日 可出结论",
      },
      verdict: { level: "neutral", label: "中性", rationale: "经验区间判定" },
      bands: null,
      window: { kind: "month", label: CURRENT_MONTH },
      interpretation: {
        fact: "盈亏比 1.50000000；n=4。",
        meaning: "样本不足，仅展示原始值。",
        action: "再积累 6 个样本后再看。",
      },
      caveats: [],
    },
    ...overrides,
  };
}

function makeBatch(overrides: Partial<MetricBatch> = {}): MetricBatch {
  return {
    period: CURRENT_MONTH,
    data_as_of: "2026-06-30",
    definition_version: "1.0",
    assumptions: {},
    items: [
      makeItem(),
      makeItem({
        metric_id: "timing.beta",
        domain: "timing",
        status: "blocked",
        sample_status: "blocked",
        reason: "BENCHMARK_MISSING",
        value: undefined,
        presentation: null,
      }),
    ],
    ...overrides,
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: "job_rebuild",
    type: "rebuild_period",
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

function accepted(): JobAccepted {
  return {
    job_id: "job_rebuild",
    type: "rebuild_period",
    status: "pending",
    links: { self: "/api/v1/jobs/job_rebuild", events: "/api/v1/jobs/job_rebuild/events" },
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
function doneEvents(): AsyncGenerator<JobEvent, void, void> {
  async function* generator(): AsyncGenerator<JobEvent, void, void> {
    await new Promise((resolve) => setTimeout(resolve, 30));
    yield {
      id: "1",
      event: "done",
      data: { job_id: "job_rebuild", status: "succeeded", result_ref: null },
    };
  }
  return generator();
}

describe("Metrics 指标页（API-083）", () => {
  beforeEach(() => {
    batchMetrics.mockResolvedValue(makeBatch());
    listJobs.mockResolvedValue(emptyJobs());
    getJob.mockResolvedValue(makeJob());
    jobEvents.mockReturnValue(noEvents());
    rebuildPeriod.mockResolvedValue(accepted());
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("进入页面按当月加载指标并渲染值/状态/原因", async () => {
    render(
      <MemoryRouter>
        <Metrics />
      </MemoryRouter>,
    );
    await waitFor(() => expect(batchMetrics).toHaveBeenCalled());
    expect(batchMetrics).toHaveBeenCalledWith({
      period: CURRENT_MONTH,
      include_exploratory: false,
    });
    await screen.findByText("perf.pnl_ratio");
    expect(screen.getByTestId("market-data-notice").textContent).toContain("收盘后同步");
    expect(screen.getByText("1.50000000")).toBeTruthy();
    // blocked 行无 value 键（C-31）→ 显示 — 与原因
    expect(screen.getByText("BENCHMARK_MISSING")).toBeTruthy();
    expect(screen.getByText("阻断")).toBeTruthy();
  });

  it("点击「构建复盘数据」调 rebuildPeriod（核心口径）并跟踪 job 至完成后重拉", async () => {
    jobEvents.mockReturnValue(doneEvents());
    render(
      <MemoryRouter>
        <Metrics />
      </MemoryRouter>,
    );
    await waitFor(() => expect(batchMetrics).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "构建复盘数据" }));
    await waitFor(() => expect(rebuildPeriod).toHaveBeenCalled());
    const [body, key] = rebuildPeriod.mock.calls[0]!;
    expect(body).toEqual({ period: CURRENT_MONTH, include_exploratory: false });
    expect(typeof key).toBe("string");
    expect(key.length).toBeGreaterThanOrEqual(16);

    // SSE done 后重拉指标并提示完成（进度卡出现即完成，不做存在性断言以免竞态）
    await screen.findByTestId("metrics-notice");
    expect(batchMetrics.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("勾选「含探索指标」后以 include_exploratory=true 重新查询", async () => {
    render(
      <MemoryRouter>
        <Metrics />
      </MemoryRouter>,
    );
    await waitFor(() => expect(batchMetrics).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() =>
      expect(batchMetrics).toHaveBeenLastCalledWith({
        period: CURRENT_MONTH,
        include_exploratory: true,
      }),
    );
  });
});
