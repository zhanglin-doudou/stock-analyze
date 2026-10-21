import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { BatchList, ImportBatch, QuarantinePage, RevokeResult } from "@sa/api-client";
import { Imports } from "./Imports";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    imports: {
      listBatches: vi.fn(),
      getBatch: vi.fn(),
      revoke: vi.fn(),
      quarantine: vi.fn(),
    },
  },
}));

const listBatches = vi.mocked(api.imports.listBatches);
const getBatch = vi.mocked(api.imports.getBatch);
const revoke = vi.mocked(api.imports.revoke);
const quarantine = vi.mocked(api.imports.quarantine);

function makeBatch(overrides: Partial<ImportBatch> = {}): ImportBatch {
  return {
    batch_id: "bat_1",
    status: "succeeded",
    mode: "full",
    broker: "huatai",
    file_name: "huatai_2024.xlsx",
    counts: { parsed: 328, inserted: 326, skipped: 0, quarantined: 2 },
    filled_count: 0,
    warnings: [],
    assumptions: {},
    created_at: "2026-10-21T00:00:00Z",
    finished_at: "2026-10-21T00:00:05Z",
    revoked_at: null,
    can_revoke: true,
    ...overrides,
  };
}

function listOf(items: ImportBatch[]): BatchList {
  return { items, next_cursor: null };
}

function emptyQuarantine(): QuarantinePage {
  return { items: [], next_cursor: null, total_estimate: 0 };
}

function revokeResult(): RevokeResult {
  return {
    batch_id: "bat_1",
    status: "revoked",
    reverted: { inserted: 326, updated: 0 },
    revoked_at: "2026-10-21T00:01:00Z",
  };
}

function renderPage(entry = "/imports"): void {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Imports />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getBatch.mockResolvedValue(makeBatch());
  quarantine.mockResolvedValue(emptyQuarantine());
});

afterEach(cleanup);

describe("Imports 导入记录页", () => {
  it("渲染批次列表：状态、counts 摘要，can_revoke 时显示撤销", async () => {
    listBatches.mockResolvedValue(
      listOf([
        makeBatch({ batch_id: "bat_ok", can_revoke: true }),
        makeBatch({
          batch_id: "bat_revoked",
          status: "revoked",
          can_revoke: false,
          revoked_at: "2026-10-20T00:00:00Z",
          counts: { parsed: 10, inserted: 8, skipped: 1, quarantined: 1 },
        }),
      ]),
    );

    renderPage();

    expect(await screen.findByText("bat_ok")).toBeTruthy();
    expect(screen.getByText("bat_revoked")).toBeTruthy();
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByText("已撤销")).toBeTruthy();
    expect(screen.getByText(/解析 328 · 写入 326 · 跳过 0 · 隔离 2/)).toBeTruthy();
    // 只有 can_revoke=true 的那行有「撤销」按钮
    expect(screen.getAllByRole("button", { name: "撤销" })).toHaveLength(1);
    expect(listBatches).toHaveBeenCalledWith({ limit: 20, cursor: undefined });
  });

  it("撤销需二次确认，调用 revoke 成功后刷新列表并提示回退行数", async () => {
    listBatches
      .mockResolvedValueOnce(listOf([makeBatch()]))
      .mockResolvedValueOnce(listOf([makeBatch({ status: "revoked", can_revoke: false, revoked_at: "2026-10-21T00:01:00Z" })]));
    revoke.mockResolvedValue(revokeResult());

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "撤销" }));
    expect(revoke).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("bat_1"));
    expect(listBatches).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/已撤销批次 bat_1：回退流水 326 行/)).toBeTruthy();
    expect(await screen.findByText("已撤销")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "撤销" })).toBeNull();
  });

  it("can_revoke=false 时不显示撤销按钮", async () => {
    listBatches.mockResolvedValue(listOf([makeBatch({ status: "failed", can_revoke: false, finished_at: null })]));

    renderPage();

    expect(await screen.findByText("bat_1")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "撤销" })).toBeNull();
    expect(screen.queryByText(/撤销本批次/)).toBeNull();
  });

  it("详情展示隔离区空态（复用 QuarantineList）", async () => {
    listBatches.mockResolvedValue(listOf([makeBatch()]));
    quarantine.mockResolvedValue(emptyQuarantine());

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "详情" }));

    expect(await screen.findByText("批次详情")).toBeTruthy();
    expect(await screen.findByText("隔离区为空")).toBeTruthy();
    expect(getBatch).toHaveBeenCalledWith("bat_1");
    expect(quarantine).toHaveBeenCalledWith("bat_1", {});
  });

  it("支持 ?batch=<id> 深链直接展开批次详情（导入页「查看批次」入口）", async () => {
    listBatches.mockResolvedValue(listOf([makeBatch()]));

    renderPage("/imports?batch=bat_1");

    expect(await screen.findByText("批次详情")).toBeTruthy();
    expect(getBatch).toHaveBeenCalledWith("bat_1");
    expect(await screen.findByText("无额外假设")).toBeTruthy();
  });
});
