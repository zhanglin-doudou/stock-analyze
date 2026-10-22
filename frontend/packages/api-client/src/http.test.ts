import { describe, expect, it, vi } from "vitest";
import { ApiProblemError, createClient, isApiProblemError, type AuthTokens, type Problem } from "./http";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function problemBody(code: string, status: number): Problem {
  return {
    type: `about:blank`,
    title: code,
    status,
    // 契约 Error.detail 为必填（RFC 7807 扩展体），网络层兜底也补空串。
    detail: "版本冲突",
    code,
    user_message: "这条数据刚被改过。",
    what_to_do: "刷新后重新提交。",
    retryable: false,
    request_id: "req_1",
    errors: [{ field: "version", code: code, message: "stale" }],
  };
}

describe("createClient", () => {
  it("401 TOKEN_EXPIRED 时刷新令牌并重放一次", async () => {
    const calls: string[] = [];
    let refreshed = false;
    const captured: { tokens: AuthTokens | null } = { tokens: null };
    let unauthorizedCalled = false;

    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/auth/refresh")) {
        return jsonResponse(200, {
          access_token: "new-access",
          refresh_token: "new-refresh",
          expires_in: 900,
          token_type: "Bearer",
        });
      }
      return refreshed
        ? jsonResponse(200, { items: [], next_cursor: null })
        : jsonResponse(401, problemBody("TOKEN_EXPIRED", 401));
    });

    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: refreshed ? "new-access" : "old-access", refresh: "old-refresh" }),
      onTokens: (t) => {
        captured.tokens = t;
        refreshed = true;
      },
      onUnauthorized: () => {
        unauthorizedCalled = true;
      },
      fetchImpl: fetchImpl as typeof fetch,
    });

    const page = await client.trades.list({ limit: 1 });

    expect(page.items).toEqual([]);
    expect(refreshed).toBe(true);
    expect(captured.tokens?.access_token).toBe("new-access");
    expect(unauthorizedCalled).toBe(false);
    // 原始请求 → 刷新 → 重放，不重复刷新
    expect(calls.filter((c) => c.includes("/auth/refresh"))).toHaveLength(1);
    expect(calls.filter((c) => c.includes("/trades"))).toHaveLength(2);
  });

  it("把 7807 解析为 ApiProblemError（含 code/what_to_do/retryable/request_id/errors）", async () => {
    const fetchImpl = vi.fn(async (): Promise<Response> => jsonResponse(409, problemBody("VERSION_CONFLICT", 409)));

    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: "token", refresh: null }),
      fetchImpl: fetchImpl as typeof fetch,
    });

    let caught: unknown;
    try {
      await client.trades.patch("trd_1", { quantity: "1.0000" }, { version: 2, idempotencyKey: "k1" });
    } catch (err) {
      caught = err;
    }

    expect(isApiProblemError(caught)).toBe(true);
    const problem = (caught as ApiProblemError).problem;
    expect(problem.code).toBe("VERSION_CONFLICT");
    expect(problem.status).toBe(409);
    expect(problem.what_to_do).toBe("刷新后重新提交。");
    expect(problem.retryable).toBe(false);
    expect(problem.request_id).toBe("req_1");
    expect(problem.errors?.[0]?.field).toBe("version");
  });

  it("未带 refresh 时 401 直接触发 onUnauthorized 并抛错", async () => {
    const fetchImpl = vi.fn(async (): Promise<Response> => jsonResponse(401, problemBody("TOKEN_REVOKED", 401)));
    let unauthorizedCalled = false;

    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: "token", refresh: null }),
      onUnauthorized: () => {
        unauthorizedCalled = true;
      },
      fetchImpl: fetchImpl as typeof fetch,
    });

    await expect(client.ops.errors()).rejects.toBeInstanceOf(ApiProblemError);
    expect(unauthorizedCalled).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("网络失败也包装为 7807 形状的 ApiProblemError", async () => {
    const fetchImpl = vi.fn(async (): Promise<Response> => {
      throw new TypeError("Failed to fetch");
    });

    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: "token", refresh: null }),
      fetchImpl: fetchImpl as typeof fetch,
    });

    const err = await client.ops.version().catch((e: unknown) => e);
    expect(isApiProblemError(err)).toBe(true);
    expect((err as ApiProblemError).code).toBe("NETWORK_ERROR");
  });

  it("回合列表/明细/配对明细走 /rounds 契约端点并原样透传过滤条件", async () => {
    const paths: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      paths.push(url);
      if (url.includes("/fills")) {
        return jsonResponse(200, { items: [], total: 0 });
      }
      if (url.match(/\/rounds\/[^?]+/)) {
        return jsonResponse(200, {
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
        });
      }
      return jsonResponse(200, { items: [], next_cursor: null, has_more: false });
    });

    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: "token", refresh: null }),
      fetchImpl: fetchImpl as typeof fetch,
    });

    const page = await client.rounds.list({
      limit: 50,
      security_code: "600519",
      status: "closed",
      from: "2026-06-01",
      to: "2026-06-30",
    });
    expect(page.items).toEqual([]);
    const listUrl = paths[0];
    expect(listUrl).toContain("/api/v1/rounds?");
    expect(listUrl).toContain("security_code=600519");
    expect(listUrl).toContain("status=closed");
    expect(listUrl).toContain("from=2026-06-01");
    expect(listUrl).toContain("to=2026-06-30");
    expect(listUrl).toContain("limit=50");

    const detail = await client.rounds.get("rnd_1");
    expect(detail.round_id).toBe("rnd_1");
    expect(detail.pnl).toBe("650.00");

    const fills = await client.rounds.fills("rnd_1");
    expect(fills.items).toEqual([]);
    expect(paths[2]).toContain("/api/v1/rounds/rnd_1/fills");
  });

  it("手动重建回合 POST /jobs/rebuild-rounds 携带 Idempotency-Key 与 body", async () => {
    const fetchImpl = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
        jsonResponse(202, {
          job_id: "job_rebuild",
          type: "rebuild_rounds",
          status: "pending",
          links: {
            self: "/api/v1/jobs/job_rebuild",
            events: "/api/v1/jobs/job_rebuild/events",
          },
        }),
    );
    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: "token", refresh: null }),
      fetchImpl: fetchImpl as typeof fetch,
    });

    const accepted = await client.rounds.rebuild({ scope: "full" }, "idem-1");
    expect(accepted.job_id).toBe("job_rebuild");

    const [url, init] = fetchImpl.mock.calls[0];
    expect(String(url)).toBe("/api/v1/jobs/rebuild-rounds");
    expect(init?.method).toBe("POST");
    const headers = init?.headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("idem-1");
    expect(JSON.parse(String(init?.body))).toEqual({ scope: "full" });
  });

  it("任务列表 jobs.list 支持 status/limit 过滤（回合页判断重建是否仍在跑）", async () => {
    const fetchImpl = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
        jsonResponse(200, { items: [], next_cursor: null }),
    );
    const client = createClient({
      baseUrl: "/api/v1",
      getToken: () => ({ access: "token", refresh: null }),
      fetchImpl: fetchImpl as typeof fetch,
    });

    const page = await client.jobs.list({ status: "pending,running", limit: 20 });
    expect(page.items).toEqual([]);
    const url = String(fetchImpl.mock.calls[0][0]);
    expect(url).toContain("/api/v1/jobs?");
    expect(url).toContain("status=pending%2Crunning");
    expect(url).toContain("limit=20");
  });
});
