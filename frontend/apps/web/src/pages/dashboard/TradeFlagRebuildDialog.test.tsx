import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../client";
import { TradeFlagRebuildDialog } from "./TradeFlagRebuildDialog";

vi.mock("../../client", () => ({
  api: {
    settings: {
      tradeFlagRebuildStatus: vi.fn(),
      rebuildTradeFlags: vi.fn(),
    },
  },
}));

type Done = (status: string, result: unknown, error?: unknown) => void;
let onDone: Done | null = null;

vi.mock("../../lib/useJobCompletion", () => ({
  useJobCompletion: (_jobId: string | null, callback: Done) => {
    onDone = callback;
    return { progress: 42, stage: "", message: "", status: null, error: null };
  },
}));

const getStatus = vi.mocked(api.settings.tradeFlagRebuildStatus);
const rebuild = vi.mocked(api.settings.rebuildTradeFlags);

function renderDialog(onClose = vi.fn()): { onClose: ReturnType<typeof vi.fn> } {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <TradeFlagRebuildDialog open onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

beforeEach(() => {
  onDone = null;
  getStatus.mockResolvedValue({
    needs_rebuild: true,
    reasons: [{ code: "PENDING_MARKET_COVERED", message: "有 2 条待算旗标的未来行情已覆盖。" }],
    definition_version: "1.4",
    flags_hash: "hash",
    has_bill_data: true,
    bill_count: 1,
    current_flag_rows: 8,
    legacy_flag_rows: 0,
    pending_count: 2,
    pending_covered_count: 2,
    pending_months: ["2026-06"],
    uncovered_count: 0,
    uncovered_months: [],
    market_as_of: "2026-06-20",
    flag_kinds: 2,
    flag_total: 3,
    flags: [
      { code: "chase", label_zh: "追涨买入", count: 2 },
      { code: "short_term_bottom", label_zh: "短期抄底", count: 1 },
    ],
  });
  rebuild.mockResolvedValue({
    job_id: "job_1",
    type: "rebuild_trade_flags",
    status: "pending",
    links: { self: "/jobs/job_1", events: "/jobs/job_1/events" },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("TradeFlagRebuildDialog", () => {
  it("展示旗标种类/数量与需要重建的原因", async () => {
    renderDialog();
    expect((await screen.findByTestId("tfr-summary")).textContent).toContain(
      "共 2 种旗标 / 3 条记录",
    );
    expect(screen.getByTestId("tfr-reasons").textContent).toContain("有 2 条待算旗标");
    const rows = screen.getByTestId("tfr-flags").querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("追涨买入");
    expect(rows[0]?.textContent).toContain("2");
  });

  it("主按钮按 auto 更新并显示 loading，完成后 3 秒关闭", async () => {
    const { onClose } = renderDialog();
    await screen.findByTestId("tfr-summary");
    fireEvent.click(screen.getByTestId("tfr-rebuild"));

    await waitFor(() =>
      expect(rebuild).toHaveBeenCalledWith(
        { scope: "all", months: [], mode: "auto" },
        expect.any(String),
      ),
    );
    expect(screen.getByTestId("tfr-rebuild").textContent).toContain("更新中 42%…");

    vi.useFakeTimers();
    act(() => onDone?.("succeeded", { months: ["2026-06"] }));
    expect(screen.getByTestId("tfr-done")).toBeTruthy();
    expect(screen.getByTestId("tfr-rebuild").textContent).toContain("更新完成");

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("「强制全量更新」触发 full 模式并复用主按钮 loading", async () => {
    renderDialog();
    await screen.findByTestId("tfr-summary");
    fireEvent.click(screen.getByTestId("tfr-rebuild-full"));

    await waitFor(() =>
      expect(rebuild).toHaveBeenCalledWith(
        { scope: "all", months: [], mode: "full" },
        expect.any(String),
      ),
    );
    expect(screen.getByTestId("tfr-rebuild").textContent).toContain("更新中 42%…");
  });
});
