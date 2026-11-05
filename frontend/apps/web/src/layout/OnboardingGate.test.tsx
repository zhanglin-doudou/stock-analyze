import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { BatchList } from "@sa/api-client";
import { OnboardingGate } from "./OnboardingGate";
import { api } from "../client";

vi.mock("../client", () => ({
  api: { imports: { listBatches: vi.fn() } },
}));

const listBatches = vi.mocked(api.imports.listBatches);

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<OnboardingGate />} />
          <Route path="/import" element={<LocationProbe />} />
          <Route path="/dashboard" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("OnboardingGate（新用户先导入账单）", () => {
  it("没有账单 → 导入页 + welcome 提示", async () => {
    const empty: BatchList = { items: [], total_estimate: 0, next_cursor: null };
    listBatches.mockResolvedValue(empty);

    renderGate();
    const location = await screen.findByTestId("location");
    expect(location.textContent).toContain("/import?welcome=1");
    expect(listBatches).toHaveBeenCalledWith({ limit: 1 });
  });

  it("已有账单 → 看板", async () => {
    const batch = { batch_id: "batch_1" } as BatchList["items"][number];
    const page: BatchList = { items: [batch], total_estimate: 1, next_cursor: null };
    listBatches.mockResolvedValue(page);

    renderGate();
    const location = await screen.findByTestId("location");
    expect(location.textContent).toContain("/dashboard");
  });
});
