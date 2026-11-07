import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

const tradeApi = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock("../client", () => ({
  api: {
    trades: {
      list: tradeApi.list,
      patch: vi.fn(),
      remove: vi.fn(),
      restore: vi.fn(),
    },
  },
}));

import { Trades } from "./Trades";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

function renderTrades(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Trades />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function searchText(): string {
  return screen.getByTestId("location-search").textContent ?? "";
}

beforeEach(() => {
  tradeApi.list.mockResolvedValue({
    items: [],
    next_cursor: null,
    has_more: false,
    meta: { data_as_of: "2026-11-06" },
    etag: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Trades · 期间筛选（period 写入 URL）", () => {
  it("URL 带 period 时，页面初次渲染即按该期间（日期范围）请求", async () => {
    renderTrades("/trades?period=2026");

    await waitFor(() => expect(tradeApi.list).toHaveBeenCalledTimes(1));
    expect(tradeApi.list).toHaveBeenCalledWith({
      limit: 50,
      cursor: undefined,
      from: "2026-01-01",
      to: "2026-12-31",
      include_deleted: undefined,
    });
  });

  it("在页面上改期间：URL 更新为 ?period=YYYY，并按新期间重新请求", async () => {
    renderTrades("/trades?period=2026");
    await waitFor(() => expect(tradeApi.list).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("年份"), { target: { value: "2025" } });

    await waitFor(() => expect(searchText()).toBe("?period=2025"));
    await waitFor(() =>
      expect(tradeApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ from: "2025-01-01", to: "2025-12-31" }),
      ),
    );
  });

  it("无 period 时 URL 保持干净，且请求不带日期范围", async () => {
    renderTrades("/trades");
    await waitFor(() => expect(tradeApi.list).toHaveBeenCalled());

    expect(searchText()).toBe("");
    const query = tradeApi.list.mock.calls[0][0] as { from?: string; to?: string };
    expect(query.from).toBeUndefined();
    expect(query.to).toBeUndefined();
  });

  it("非法 period 安全降级为无筛选：不崩、不改写 URL、不发脏参数", async () => {
    renderTrades("/trades?period=abc");

    expect(await screen.findByRole("heading", { name: "交易流水" })).toBeTruthy();
    await waitFor(() => expect(tradeApi.list).toHaveBeenCalled());

    const query = tradeApi.list.mock.calls[0][0] as { from?: string; to?: string };
    expect(query.from).toBeUndefined();
    expect(query.to).toBeUndefined();
    expect(searchText()).toBe("?period=abc");
  });

  it("改期间不丢失其他筛选，且游标回到第一页", async () => {
    tradeApi.list.mockResolvedValue({
      items: [],
      next_cursor: "cursor-2",
      has_more: true,
      meta: { data_as_of: "2026-11-06" },
      etag: null,
    });
    renderTrades("/trades?period=2026");
    await waitFor(() => expect(tradeApi.list).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByLabelText("包含已删除"));
    await waitFor(() =>
      expect(tradeApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ include_deleted: true })),
    );

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() =>
      expect(tradeApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-2" })),
    );

    fireEvent.change(screen.getByLabelText("年份"), { target: { value: "2024" } });

    await waitFor(() => expect(searchText()).toBe("?period=2024"));
    await waitFor(() =>
      expect(tradeApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({
          from: "2024-01-01",
          to: "2024-12-31",
          include_deleted: true,
          cursor: undefined,
        }),
      ),
    );
  });
});
