// 轻量 fetch 封装：Bearer 注入、RFC 7807 解析、401 刷新重放、全部一期端点。
//
// DTO 不再在本文件手写：统一来自 ./contract（generated SDK 类型接入层）。
// 后端契约字段变化会通过 contract.ts 的编译期断言在 `tsc` 阶段暴露。

import type {
  AuthTokens,
  BatchList,
  CommitImportBody,
  CreateTokenBody,
  ErrorCatalogOut,
  HealthOut,
  ImportBatch,
  Job,
  JobAccepted,
  JobStatus,
  LoginIn,
  LoginResponse,
  MeOut,
  PatCreateResponse,
  Preview,
  PreviewOptions,
  Problem,
  ProblemFieldError,
  QuarantinePage,
  ReadyOut,
  RefreshIn,
  RefreshOut,
  RegisterIn,
  RegisterResponse,
  RestoreResult,
  RevokeResult,
  TokenList,
  TradeHistoryPage,
  TradeMutationOut,
  TradeMutationResult,
  TradePage,
  TradePageDocument,
  TradePatchIn,
  VersionOut,
} from "./contract";

// 对外保持原有导出名（`import type { Trade } from "@sa/api-client"` 不受影响）。
export type * from "./contract";

export function isProblem(value: unknown): value is Problem {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.code === "string" && typeof v.status === "number";
}

/** 所有非 2xx（含网络失败）统一以此异常抛出，UI 直接渲染其 problem。 */
export class ApiProblemError extends Error {
  readonly problem: Problem;
  readonly status: number;
  readonly code: string;
  readonly user_message: string;
  readonly what_to_do: string;
  readonly retryable: boolean;
  readonly requestId: string;
  readonly errors: ProblemFieldError[];

  constructor(problem: Problem) {
    super(problem.user_message || problem.title || problem.code);
    this.name = "ApiProblemError";
    this.problem = problem;
    this.status = problem.status;
    this.code = problem.code;
    this.user_message = problem.user_message;
    this.what_to_do = problem.what_to_do;
    this.retryable = problem.retryable;
    this.requestId = problem.request_id;
    this.errors = problem.errors ?? [];
  }
}

export function isApiProblemError(value: unknown): value is ApiProblemError {
  return value instanceof ApiProblemError;
}

export interface Tokens {
  access: string | null;
  refresh: string | null;
}

export type JobEventName = "progress" | "done" | "error" | "reconnect";

export interface JobProgressData {
  job_id: string;
  progress: number;
  stage: string;
  message?: string;
  at?: string;
}

export interface JobDoneData {
  job_id: string;
  status: JobStatus;
  result_ref?: string | null;
}

export interface JobErrorData {
  job_id: string;
  error: Problem;
}

export interface JobEvent {
  id?: string;
  event: JobEventName;
  data: unknown;
}

export interface ListTradesQuery {
  limit?: number;
  cursor?: string;
  sort?: string;
  from?: string;
  to?: string;
  code?: string;
  side?: string;
  batch_id?: string;
  include_deleted?: boolean;
  fields?: string;
  include_total?: boolean;
}

export interface CursorQuery {
  [key: string]: QueryValue;
  limit?: number;
  cursor?: string;
}

export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue>;

export interface ClientOptions {
  baseUrl: string;
  /** 返回当前 access/refresh；由宿主（localStorage）提供。 */
  getToken?: () => Tokens;
  /** refresh 轮换成功后回写新令牌（可选）。 */
  onTokens?: (tokens: AuthTokens) => void;
  /** 无法刷新（或刷新失败）时触发，宿主应清会话并跳登录。 */
  onUnauthorized?: () => void;
  /** 便于测试注入。 */
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  path: string;
  method?: string;
  query?: Query;
  body?: unknown;
  formData?: FormData;
  headers?: Record<string, string>;
  omitAuth?: boolean;
  /** true 时忽略 baseUrl，按根路径（/healthz 等）请求。 */
  root?: boolean;
  ifMatch?: string;
  idempotencyKey?: string;
  accept?: string;
}

const REFRESHABLE_CODES = new Set(["TOKEN_EXPIRED", "TOKEN_REVOKED", "TOKEN_REUSED"]);

function buildUrl(baseUrl: string, path: string, query?: Query): string {
  const base = `${baseUrl.replace(/\/$/, "")}${path}`;
  if (!query) return base;
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `${base}${base.includes("?") ? "&" : "?"}${s}` : base;
}

async function parseJson<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  const contentType = res.headers.get("Content-Type") ?? "";
  if (!contentType.includes("json")) return text as unknown as T;
  return JSON.parse(text) as T;
}

async function readProblem(res: Response): Promise<Problem> {
  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    payload = undefined;
  }
  const p = isProblem(payload) ? payload : undefined;
  return {
    type: p?.type ?? "about:blank",
    title: p?.title ?? res.statusText ?? "Request failed",
    status: p?.status ?? res.status,
    detail: p?.detail ?? res.statusText ?? "",
    instance: p?.instance,
    code: p?.code ?? `HTTP_${res.status}`,
    user_message: p?.user_message ?? `请求失败（HTTP ${res.status}）。`,
    what_to_do: p?.what_to_do ?? "请稍后重试；若持续失败，请携带 request_id 联系支持。",
    retryable: p?.retryable ?? res.status >= 500,
    request_id: p?.request_id ?? res.headers.get("X-Request-Id") ?? "",
    errors: p?.errors,
  };
}

function networkProblem(detail: string): Problem {
  return {
    type: "about:blank",
    title: "Network error",
    status: 0,
    detail,
    code: "NETWORK_ERROR",
    user_message: "网络连接失败。",
    what_to_do: "确认网络可用、后端服务已启动（make api），然后重试。",
    retryable: true,
    request_id: "",
  };
}

/** 生成一个可用于 Idempotency-Key 的随机串。 */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `key_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

export function createClient(options: ClientOptions) {
  const baseUrl = options.baseUrl;
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const getToken = options.getToken ?? ((): Tokens => ({ access: null, refresh: null }));

  function rootBase(): string {
    try {
      return new URL("/", baseUrl).toString().replace(/\/$/, "");
    } catch {
      return "";
    }
  }

  let refreshInFlight: Promise<AuthTokens | null> | null = null;

  async function doRefresh(): Promise<AuthTokens | null> {
    const { refresh } = getToken();
    if (!refresh) return null;
    if (!refreshInFlight) {
      refreshInFlight = (async (): Promise<AuthTokens | null> => {
        try {
          const res = await fetchImpl(buildUrl(baseUrl, "/auth/refresh"), {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ refresh_token: refresh }),
          });
          if (!res.ok) return null;
          const tokens = await parseJson<RefreshOut>(res);
          options.onTokens?.(tokens);
          return tokens;
        } catch {
          return null;
        } finally {
          refreshInFlight = null;
        }
      })();
    }
    return refreshInFlight;
  }

  async function send<T>(req: RequestOptions, retried = false): Promise<{ data: T; headers: Headers }> {
    const url = buildUrl(req.root ? rootBase() : baseUrl, req.path, req.query);
    const headers = new Headers(req.headers);
    headers.set("Accept", req.accept ?? "application/json");
    const token = getToken();
    if (!req.omitAuth && token.access) headers.set("Authorization", `Bearer ${token.access}`);
    if (req.ifMatch) headers.set("If-Match", req.ifMatch);
    if (req.idempotencyKey) headers.set("Idempotency-Key", req.idempotencyKey);

    let body: BodyInit | undefined;
    if (req.formData) {
      body = req.formData;
    } else if (req.body !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(req.body);
    }

    let res: Response;
    try {
      res = await fetchImpl(url, { method: req.method ?? "GET", headers, body });
    } catch (err) {
      throw new ApiProblemError(networkProblem(err instanceof Error ? err.message : String(err)));
    }

    if (res.status === 304) return { data: undefined as T, headers: res.headers };
    if (res.ok) return { data: await parseJson<T>(res), headers: res.headers };

    const problem = await readProblem(res);
    if (res.status === 401 && !retried && REFRESHABLE_CODES.has(problem.code)) {
      const refreshed = await doRefresh();
      if (refreshed) return send<T>(req, true);
    }
    if (res.status === 401) options.onUnauthorized?.();
    throw new ApiProblemError(problem);
  }

  const auth = {
    register(body: RegisterIn) {
      return send<RegisterResponse>({ path: "/auth/register", method: "POST", body, omitAuth: true }).then((r) => r.data);
    },
    login(body: LoginIn) {
      return send<LoginResponse>({ path: "/auth/login", method: "POST", body, omitAuth: true }).then((r) => r.data);
    },
    refresh(refreshToken: string) {
      const body = { refresh_token: refreshToken } satisfies RefreshIn;
      return send<RefreshOut>({ path: "/auth/refresh", method: "POST", body, omitAuth: true }).then((r) => r.data);
    },
    logout() {
      return send<void>({ path: "/auth/logout", method: "POST" }).then(() => undefined);
    },
    me() {
      // API-005 的路径是 /auth/me（此前误写 /account/me，由契约测试防回归）。
      return send<MeOut>({ path: "/auth/me" }).then((r) => r.data);
    },
  };

  const tokens = {
    list(query: CursorQuery = {}) {
      return send<TokenList>({ path: "/tokens", query }).then((r) => r.data);
    },
    create(body: CreateTokenBody) {
      return send<PatCreateResponse>({ path: "/tokens", method: "POST", body }).then((r) => r.data.token);
    },
    revoke(tokenId: string) {
      return send<void>({ path: `/tokens/${tokenId}`, method: "DELETE" }).then(() => undefined);
    },
  };

  const imports = {
    preview(file: File, opts: PreviewOptions = {}) {
      const form = new FormData();
      form.append("file", file);
      // 仅使用契约 Body_previewImport 声明的字段（此前多余的 columns/mode/broker 后端并不读取）。
      if (opts.mapping) form.append("mapping", opts.mapping);
      if (opts.date_format) form.append("date_format", opts.date_format);
      return send<Preview>({ path: "/imports/preview", method: "POST", formData: form }).then((r) => r.data);
    },
    commit(body: CommitImportBody, idempotencyKey: string) {
      return send<JobAccepted>({ path: "/imports", method: "POST", body, idempotencyKey }).then((r) => r.data);
    },
    listBatches(query: CursorQuery = {}) {
      return send<BatchList>({ path: "/imports", query }).then((r) => r.data);
    },
    getBatch(batchId: string) {
      return send<ImportBatch>({ path: `/imports/${batchId}` }).then((r) => r.data);
    },
    /** API-015：撤销批次（同步 200，返回回退行数计数）。 */
    revoke(batchId: string, body: { reason?: string } = {}) {
      return send<RevokeResult>({ path: `/imports/${batchId}/revoke`, method: "POST", body }).then((r) => r.data);
    },
    quarantine(batchId: string, query: CursorQuery & { reason_code?: string } = {}) {
      return send<QuarantinePage>({ path: `/imports/${batchId}/quarantine`, query }).then((r) => r.data);
    },
  };

  const jobs = {
    get(jobId: string) {
      return send<Job>({ path: `/jobs/${jobId}` }).then((r) => r.data);
    },
    cancel(jobId: string) {
      return send<Job>({ path: `/jobs/${jobId}/cancel`, method: "POST" }).then((r) => r.data);
    },
    /** SSE 进度流：支持 Last-Event-ID 断线续传（浏览器 EventSource 无法带 Bearer，故用 fetch 流）。 */
    async *events(
      jobId: string,
      streamOptions: { lastEventId?: string; signal?: AbortSignal } = {},
    ): AsyncGenerator<JobEvent, void, void> {
      const headers = new Headers({ Accept: "text/event-stream" });
      const token = getToken();
      if (token.access) headers.set("Authorization", `Bearer ${token.access}`);
      if (streamOptions.lastEventId) headers.set("Last-Event-ID", streamOptions.lastEventId);

      let res: Response;
      try {
        res = await fetchImpl(buildUrl(baseUrl, `/jobs/${jobId}/events`), { headers, signal: streamOptions.signal });
      } catch (err) {
        throw new ApiProblemError(networkProblem(err instanceof Error ? err.message : String(err)));
      }
      if (res.status === 401) options.onUnauthorized?.();
      if (!res.ok) throw new ApiProblemError(await readProblem(res));
      if (!res.body) return;

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let current: { event: JobEventName | ""; data: string; id: string } = { event: "", data: "", id: "" };

      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let nl = buffer.indexOf("\n");
          while (nl >= 0) {
            let line = buffer.slice(0, nl);
            buffer = buffer.slice(nl + 1);
            if (line.endsWith("\r")) line = line.slice(0, -1);
            if (line === "") {
              if (current.data) {
                const parsed = toJobEvent(current);
                if (parsed) yield parsed;
              }
              current = { event: "", data: "", id: "" };
            } else if (!line.startsWith(":")) {
              const colon = line.indexOf(":");
              const field = colon === -1 ? line : line.slice(0, colon);
              let v = colon === -1 ? "" : line.slice(colon + 1);
              if (v.startsWith(" ")) v = v.slice(1);
              if (field === "event") current.event = v as JobEventName;
              else if (field === "data") current.data = current.data ? `${current.data}\n${v}` : v;
              else if (field === "id") current.id = v;
            }
            nl = buffer.indexOf("\n");
          }
        }
      } finally {
        reader.releaseLock();
      }
    },
  };

  const trades = {
    async list(query: ListTradesQuery = {}): Promise<TradePage> {
      // 契约 TradePage + 客户端附加的 meta（响应头 data_as_of 由后端 meta 中间件注入）。
      const { data, headers } = await send<TradePageDocument & { meta?: { data_as_of?: string } }>({
        path: "/trades",
        query: {
          limit: query.limit,
          cursor: query.cursor,
          sort: query.sort,
          trade_date_from: query.from,
          trade_date_to: query.to,
          security_code: query.code,
          side: query.side,
          batch_id: query.batch_id,
          include_deleted: query.include_deleted,
          fields: query.fields,
          include_total: query.include_total,
        },
      });
      const nextCursor = data.next_cursor ?? null;
      return {
        items: data.items ?? [],
        next_cursor: nextCursor,
        has_more: data.has_more ?? nextCursor !== null,
        meta: { data_as_of: data.meta?.data_as_of ?? "" },
        etag: headers.get("ETag"),
      };
    },
    async patch(tradeId: string, body: TradePatchIn, opts: { version: number; idempotencyKey: string }): Promise<TradeMutationResult> {
      // API-018 响应体是 TradeMutationOut { trade, affected_metrics }，不是裸 TradeOut。
      const { data, headers } = await send<TradeMutationOut>({
        path: `/trades/${tradeId}`,
        method: "PATCH",
        body,
        ifMatch: `"${opts.version}"`,
        idempotencyKey: opts.idempotencyKey,
      });
      return { trade: data.trade, affected_metrics: data.affected_metrics ?? [], etag: headers.get("ETag") };
    },
    remove(tradeId: string) {
      return send<void>({ path: `/trades/${tradeId}`, method: "DELETE" }).then(() => undefined);
    },
    restore(tradeId: string, reason?: string) {
      return send<RestoreResult>({
        path: `/trades/${tradeId}/restore`,
        method: "POST",
        body: reason ? { reason } : undefined,
      }).then((r) => r.data);
    },
    history(tradeId: string, query: CursorQuery = {}) {
      return send<TradeHistoryPage>({ path: `/trades/${tradeId}/history`, query }).then((r) => r.data);
    },
  };

  const ops = {
    healthz() {
      return send<HealthOut>({ path: "/healthz", root: true, omitAuth: true }).then((r) => r.data);
    },
    readyz() {
      return send<ReadyOut>({ path: "/readyz", root: true, omitAuth: true }).then((r) => r.data);
    },
    version() {
      return send<VersionOut>({ path: "/version", root: true, omitAuth: true }).then((r) => r.data);
    },
    errors() {
      return send<ErrorCatalogOut>({ path: "/errors", omitAuth: true }).then((r) => r.data);
    },
    openapi() {
      return send<Record<string, unknown>>({ path: "/openapi.json", omitAuth: true }).then((r) => r.data);
    },
    metrics() {
      return send<string>({ path: "/metrics", root: true, omitAuth: true, accept: "text/plain" }).then((r) => r.data);
    },
  };

  return { auth, tokens, imports, jobs, trades, ops, request: send };
}

export type ApiClient = ReturnType<typeof createClient>;

function toJobEvent(current: { event: JobEventName | ""; data: string; id: string }): JobEvent | null {
  try {
    const parsed: unknown = JSON.parse(current.data);
    return { id: current.id || undefined, event: (current.event || "progress") as JobEventName, data: parsed };
  } catch {
    return null;
  }
}
