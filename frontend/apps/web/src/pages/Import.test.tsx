import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ImportBatch, Job, JobEvent, Preview, RevokeResult } from "@sa/api-client";
import { Import } from "./Import";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    imports: {
      preview: vi.fn(),
      commit: vi.fn(),
      getBatch: vi.fn(),
      revoke: vi.fn(),
    },
    jobs: {
      events: vi.fn(async function* (): AsyncGenerator<JobEvent, void, void> {
        yield { id: "1", event: "done", data: { job_id: "job_1", status: "succeeded", result_ref: null } };
      }),
    },
  },
}));

const preview = vi.mocked(api.imports.preview);
const commit = vi.mocked(api.imports.commit);
const getBatch = vi.mocked(api.imports.getBatch);
const revoke = vi.mocked(api.imports.revoke);

function makePreview(): Preview {
  return {
    preview_id: "prv_1",
    file: { name: "huatai_2024.xlsx", sha256: "a".repeat(64), bytes: 2048 },
    columns: [
      {
        index: 1,
        name: "成交日期",
        sample: ["2024-01-02"],
        suggested_field: "trade_date",
        confidence: 0.99,
      },
    ],
    date_format: "YYYY-MM-DD",
    warnings: [],
    estimated: { parsed: 328, inserted: 326, skipped: 0, quarantined: 2 },
    cash_balance_available: true,
    can_commit: true,
  };
}

function makeBatch(overrides: Partial<ImportBatch> = {}): ImportBatch {
  return {
    batch_id: "bat_1",
    status: "succeeded",
    mode: "full",
    broker: "huatai",
    file_name: "huatai_2024.xlsx",
    counts: { parsed: 328, inserted: 326, skipped: 0, quarantined: 2 },
    warnings: [],
    assumptions: {},
    created_at: "2026-10-21T00:00:00Z",
    finished_at: "2026-10-21T00:00:05Z",
    revoked_at: null,
    can_revoke: true,
    ...overrides,
  };
}

const acceptedJob: Job = { job_id: "job_1", type: "import_commit", status: "pending", batch_id: "bat_1" };

function revokeResult(): RevokeResult {
  return {
    batch_id: "bat_1",
    status: "revoked",
    reverted: { inserted: 326, updated: 0 },
    revoked_at: "2026-10-21T00:01:00Z",
  };
}

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/import"]}>
      <Import />
    </MemoryRouter>,
  );
}

async function uploadAndCommit(): Promise<void> {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error("找不到文件输入框");
  fireEvent.change(input, { target: { files: [new File(["x"], "huatai_2024.xlsx")] } });
  fireEvent.click(await screen.findByRole("button", { name: "确认导入" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  preview.mockResolvedValue(makePreview());
  commit.mockResolvedValue(acceptedJob);
  getBatch.mockResolvedValue(makeBatch());
  revoke.mockResolvedValue(revokeResult());
});

afterEach(cleanup);

describe("Import 导入页结果区", () => {
  it("任务成功后展示批次号/插入行数/隔离数，并提供「查看批次」「撤销本次导入」入口", async () => {
    renderPage();
    await uploadAndCommit();

    // 任务 done 事件后拉批次详情
    expect(await screen.findByText(/插入 326 行/)).toBeTruthy();
    expect(screen.getByText("bat_1")).toBeTruthy();
    expect(screen.getByText(/隔离 2 行/)).toBeTruthy();
    expect(getBatch).toHaveBeenCalledWith("bat_1");

    const link = screen.getByRole("link", { name: "查看批次" });
    expect(link.getAttribute("href")).toBe("/imports?batch=bat_1");
    expect(screen.getByRole("button", { name: "撤销本次导入" })).toBeTruthy();
  });

  it("「撤销本次导入」二次确认后调用 revoke、刷新批次并提示回退行数", async () => {
    getBatch
      .mockResolvedValueOnce(makeBatch({ can_revoke: true }))
      .mockResolvedValueOnce(makeBatch({ status: "revoked", can_revoke: false, revoked_at: "2026-10-21T00:01:00Z" }));

    renderPage();
    await uploadAndCommit();

    fireEvent.click(await screen.findByRole("button", { name: "撤销本次导入" }));
    expect(revoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("bat_1"));
    expect(await screen.findByText(/回退流水 326 行/)).toBeTruthy();
    // 撤销后刷新批次：can_revoke=false → 按钮消失
    await waitFor(() => expect(screen.queryByRole("button", { name: "撤销本次导入" })).toBeNull());
    expect(getBatch).toHaveBeenCalledTimes(2);
  });
});
