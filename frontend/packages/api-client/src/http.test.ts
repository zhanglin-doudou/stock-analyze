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
});
