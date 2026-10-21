/**
 * 契约测试：前端 SDK 类型 ↔ `contracts/openapi.json` 快照 ↔ 生成物 `schema.d.ts`。
 *
 * 目的：在纯前端门禁（无后端、无 pytest、不连库）里防止三类漂移：
 *   1) 一期 30 个端点的 method+path 从契约快照里消失；
 *   2) 快照已更新但 `make sdk` 没重跑（generated 落后于快照）；
 *   3) `http.ts` 读取的字段在 generated 中不存在（手写 DTO 回潮 / 字段改名）。
 *
 * 说明：API-075 `GET /api/v1/openapi.json` 是 FastAPI 自引用端点，不会出现在自己的
 * spec.paths 里（API-Catalog §1 已登记），这里作为唯一的已知例外断言。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

interface OpenApiSchema {
  required?: string[];
  properties?: Record<string, unknown>;
}

interface OpenApiDocument {
  paths: Record<string, Record<string, unknown>>;
  components: { schemas: Record<string, OpenApiSchema> };
}

interface Phase1Endpoint {
  id: string;
  method: "get" | "post" | "patch" | "delete";
  /** 相对 `/api/v1` 的路径；`root` 端点为根路径（运维/契约元数据）。 */
  path: string;
  root?: boolean;
}

/** 仓库根：本文件在 `frontend/packages/api-client/src/`。 */
const REPO_ROOT = new URL("../../../../", import.meta.url);

const SNAPSHOT_FILE = fileURLToPath(new URL("contracts/openapi.json", REPO_ROOT));
const GENERATED_FILE = fileURLToPath(new URL("./api/generated/schema.d.ts", import.meta.url));
const HTTP_FILE = fileURLToPath(new URL("./http.ts", import.meta.url));

const snapshot = JSON.parse(readFileSync(SNAPSHOT_FILE, "utf8")) as OpenApiDocument;
const generated = readFileSync(GENERATED_FILE, "utf8");
const httpSource = readFileSync(HTTP_FILE, "utf8");

/**
 * 一期 30 端点（DEV-PLAN-v2 附录 A.1「期次=一」的 API-ID 集合，与 API-Catalog §1 对齐）。
 * 硬编码是刻意的：希望「端点被误删/改路径」时测试失败，而不是随契约自动跟随。
 */
const PHASE1_ENDPOINTS: Phase1Endpoint[] = [
  { id: "API-001", method: "post", path: "/auth/register" },
  { id: "API-002", method: "post", path: "/auth/login" },
  { id: "API-003", method: "post", path: "/auth/refresh" },
  { id: "API-004", method: "post", path: "/auth/logout" },
  { id: "API-005", method: "get", path: "/auth/me" },
  { id: "API-007", method: "post", path: "/tokens" },
  { id: "API-008", method: "get", path: "/tokens" },
  { id: "API-009", method: "delete", path: "/tokens/{token_id}" },
  { id: "API-010", method: "post", path: "/imports/preview" },
  { id: "API-011", method: "post", path: "/imports" },
  { id: "API-012", method: "get", path: "/imports" },
  { id: "API-013", method: "get", path: "/imports/{batch_id}" },
  { id: "API-014", method: "get", path: "/imports/{batch_id}/quarantine" },
  { id: "API-015", method: "post", path: "/imports/{batch_id}/revoke" },
  { id: "API-017", method: "get", path: "/trades" },
  { id: "API-018", method: "patch", path: "/trades/{trade_id}" },
  { id: "API-019", method: "delete", path: "/trades/{trade_id}" },
  { id: "API-020", method: "post", path: "/trades/{trade_id}/restore" },
  { id: "API-021", method: "get", path: "/trades/{trade_id}/history" },
  { id: "API-026", method: "get", path: "/reconciliation" },
  { id: "API-061", method: "get", path: "/jobs" },
  { id: "API-062", method: "get", path: "/jobs/{job_id}" },
  { id: "API-063", method: "get", path: "/jobs/{job_id}/events" },
  { id: "API-064", method: "post", path: "/jobs/{job_id}/cancel" },
  { id: "API-071", method: "get", path: "/healthz", root: true },
  { id: "API-072", method: "get", path: "/readyz", root: true },
  { id: "API-073", method: "get", path: "/version", root: true },
  { id: "API-075", method: "get", path: "/openapi.json", root: true },
  { id: "API-076", method: "get", path: "/errors" },
  { id: "API-080", method: "get", path: "/trades/{trade_id}" },
];

/** FastAPI 自身的 `/api/v1/openapi.json` 不在其导出的 spec 里（API-Catalog §1 自引用）。 */
const SELF_REFERENTIAL_ENDPOINTS = new Set(["API-075"]);

function fullPath(endpoint: Phase1Endpoint): string {
  return endpoint.root ? endpoint.path : `/api/v1${endpoint.path}`;
}

/** 从 generated `schema.d.ts` 中粗略截取某个 schema 的声明块（用于字段存在性断言）。 */
function schemaBlock(name: string): string {
  const marker = `        ${name}: {`;
  const start = generated.indexOf(marker);
  expect(start, `generated/schema.d.ts 缺少 schema ${name}（是否忘记 make sdk？）`).toBeGreaterThanOrEqual(0);
  const after = generated.slice(start + marker.length);
  const next = after.search(/\n {8}[A-Za-z_$][\w$]*: \{/);
  return next === -1 ? after : after.slice(0, next);
}

function expectFields(schemaName: string, fields: string[]): void {
  const block = schemaBlock(schemaName);
  for (const field of fields) {
    expect(block, `${schemaName}.${field} 不在 generated schema 中`).toMatch(new RegExp(`\\n\\s{8,}${field}\\??:`));
  }
}

describe("一期端点与契约快照对齐（DEV-PLAN 附录 A.1）", () => {
  it("30 个一期端点均在快照 paths 中（API-075 为 FastAPI 自引用例外）", () => {
    expect(PHASE1_ENDPOINTS).toHaveLength(30);

    const missing = PHASE1_ENDPOINTS.filter((endpoint) => !snapshot.paths[fullPath(endpoint)]?.[endpoint.method]);
    expect(missing.map((endpoint) => endpoint.id)).toEqual([...SELF_REFERENTIAL_ENDPOINTS]);
  });

  it("30 个一期端点均已进入 generated paths（generated 与快照同步）", () => {
    const missing = PHASE1_ENDPOINTS.filter(
      (endpoint) => !SELF_REFERENTIAL_ENDPOINTS.has(endpoint.id) && !generated.includes(`    "${fullPath(endpoint)}": {`),
    );
    expect(missing.map((endpoint) => endpoint.id)).toEqual([]);
  });

  it("快照里的每个 schema 都已被生成（防止 make sdk 未重跑）", () => {
    const missing = Object.keys(snapshot.components.schemas).filter((name) => !generated.includes(`        ${name}: {`));
    expect(missing).toEqual([]);
  });
});

describe("RFC 7807 错误体（Error schema）", () => {
  it("包含 code/user_message/what_to_do/retryable/request_id/errors 六个扩展字段", () => {
    const error = snapshot.components.schemas.Error;
    expect(error).toBeDefined();
    const properties = Object.keys(error?.properties ?? {});
    expect(properties).toEqual(
      expect.arrayContaining(["code", "user_message", "what_to_do", "retryable", "request_id", "errors"]),
    );
    expect(error?.required).toEqual(
      expect.arrayContaining(["code", "user_message", "what_to_do", "retryable", "request_id"]),
    );
  });

  it("generated Error 中同样存在这六个字段（UI ProblemView 依赖）", () => {
    expectFields("Error", ["code", "user_message", "what_to_do", "retryable", "request_id", "errors"]);
  });
});

describe("http.ts 核心字段 ↔ generated schema", () => {
  it("流水 / 分页 / 预览 / 批次 / 任务 / PAT / 撤销 / 隔离区字段存在", () => {
    expectFields("TradeOut", [
      "trade_id",
      "trade_date",
      "security_code",
      "side",
      "quantity",
      "price",
      "amount",
      "commission",
      "stamp_tax",
      "other_fee",
      "cash_balance",
      "cash_balance_available",
      "dedupe_key",
      "version",
      "deleted_at",
    ]);
    expectFields("TradePage", ["items", "next_cursor", "has_more"]);
    expectFields("TradeMutationOut", ["trade", "affected_metrics"]);

    expectFields("PreviewOut", ["preview_id", "file", "columns", "warnings", "estimated", "cash_balance_available", "can_commit"]);
    expectFields("PreviewEstimated", ["parsed", "inserted", "skipped", "quarantined"]);

    expectFields("BatchOut", ["batch_id", "status", "mode", "broker", "file_name", "counts", "warnings", "can_revoke"]);
    expectFields("BatchCounts", ["parsed", "inserted", "skipped", "quarantined"]);
    expectFields("RevokeOut", ["batch_id", "status", "reverted", "revoked_at"]);
    expectFields("QuarantineItem", ["line_no", "raw", "reason_code", "resolved"]);
    expectFields("QuarantinePage", ["items", "next_cursor", "total_estimate"]);

    expectFields("JobOut", ["job_id", "type", "status", "progress", "stage", "result_ref"]);
    expectFields("JobAcceptedOut", ["job_id", "type", "batch_id", "links"]);
    expectFields("MeOut", ["user", "settings"]);
    expectFields("PatListOut", ["items", "total"]);
  });

  it("API-005 路径已修正为 /auth/me，且 http.ts 不再回退为独立 DTO", () => {
    expect(httpSource).toContain('path: "/auth/me"');
    expect(httpSource).not.toContain('"/account/me"');
    // DTO 必须来自 contract.ts（generated 别名），不能在本文件重新手写。
    for (const dto of ["Problem", "Trade", "Preview", "ImportBatch", "Job"]) {
      expect(httpSource, `http.ts 不应再手写 DTO: ${dto}`).not.toContain(`export interface ${dto} {`);
    }
  });
});
