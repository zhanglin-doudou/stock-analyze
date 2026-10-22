/**
 * 生成式 SDK 类型接入层（contract = 契约单源）。
 *
 * 背景：`api/generated/schema.d.ts` 由 `make sdk`（openapi-typescript）从
 * `contracts/openapi.json` 生成，**禁止手改**。本文件是前端唯一允许引用 generated
 * 类型的地方：
 *   1) 集中 re-export `components` / `paths` / `operations`；
 *   2) 把 generated schema 名收敛成前端可读的 DTO 别名；
 *   3) 用 conditional type 做**编译期契约断言**：后端字段一旦被删/改名，
 *      `pnpm -r typecheck` 立即失败，而不是等到运行时才炸。
 *
 * 约定：
 * - 生成类型是唯一事实来源；客户端只允许「别名」或「基于生成的收窄/增强」。
 * - 客户端需要额外字段（如 TradePage 的 meta/etag）时，用 `extends` 叠加，
 *   并保留下面 `Assert*` 断言把叠加层的契约部分钉死。
 * - `http.ts` 只从这里取 DTO，不再维护第二份独立 DTO 定义。
 */
import type { components, operations, paths } from "./api/generated/schema";

export type { components, operations, paths };

/** 契约 schema 全集：`ApiSchemas["TradeOut"]` 等。 */
export type ApiSchemas = components["schemas"];
/** 契约路径表：`/api/v1/trades` 等。 */
export type ApiPaths = paths;
/** 契约 operationId 表：`listTrades` 等。 */
export type ApiOperations = operations;
/** 按 operationId 取单个操作类型。 */
export type ApiOperation<Id extends keyof operations> = operations[Id];

// ── 编译期断言工具（只存在于类型空间，运行时零开销） ─────────────────────

/** 断言：T 必须为 true 字面量；条件不成立即 tsc 报错。 */
type Assert<T extends true> = T;
/** 断言：A 是 B 的子集（用于客户端收窄联合类型不越出契约）。 */
type IsSubset<A, B> = [A] extends [B] ? true : false;
/** 断言：T 拥有 K 中列出的全部字段（用于「http.ts 读取的字段」防漂移）。 */
type FieldsPresent<T, K extends PropertyKey> = Exclude<K, keyof T> extends never ? true : false;

// ── generated 原始别名（名字 = OpenAPI schema 名，便于对照契约） ──────────

/** RFC 7807 错误体（OpenAPI schema 名 `Error`）。 */
export type ErrorDocument = ApiSchemas["Error"];
export type ProblemFieldError = NonNullable<ErrorDocument["errors"]>[number];
export type ErrorCatalogOut = ApiSchemas["ErrorCatalogOut"];

export type UserBrief = ApiSchemas["UserBrief"];
export type AuthOut = ApiSchemas["AuthOut"];
export type MeOut = ApiSchemas["MeOut"];
export type LoginIn = ApiSchemas["LoginIn"];
export type RegisterIn = ApiSchemas["RegisterIn"];
export type RefreshIn = ApiSchemas["RefreshIn"];
export type RefreshOut = ApiSchemas["RefreshOut"];
export type LogoutIn = ApiSchemas["LogoutIn"];

export type PatIn = ApiSchemas["PatIn"];
export type PatMeta = ApiSchemas["PatMeta"];
export type PatMetaWithSecret = ApiSchemas["PatMetaWithSecret"];
export type PatCreateResponse = ApiSchemas["PatCreateResponse"];
export type PatListOut = ApiSchemas["PatListOut"];

export type TradeOut = ApiSchemas["TradeOut"];
export type TradePageDocument = ApiSchemas["TradePage"];
export type TradeMutationOut = ApiSchemas["TradeMutationOut"];
export type TradeRestoreOut = ApiSchemas["TradeRestoreOut"];
export type TradeHistoryItem = ApiSchemas["TradeHistoryItem"];
export type TradeHistoryOut = ApiSchemas["TradeHistoryOut"];
export type TradePatchIn = ApiSchemas["TradePatchIn"];
export type AffectedMetric = ApiSchemas["AffectedMetric"];

export type PreviewOut = ApiSchemas["PreviewOut"];
export type PreviewColumnOut = ApiSchemas["PreviewColumn"];
export type PreviewWarningOut = ApiSchemas["PreviewWarning"];
export type PreviewEstimatedOut = ApiSchemas["PreviewEstimated"];
export type PreviewFileOut = ApiSchemas["PreviewFile"];

export type ImportCommitIn = ApiSchemas["ImportCommitIn"];
export type ImportRange = ApiSchemas["ImportRange"];
export type BatchOut = ApiSchemas["BatchOut"];
export type BatchDetailOut = ApiSchemas["BatchDetailOut"];
export type BatchFillOut = ApiSchemas["BatchFill"];
export type BatchPageOut = ApiSchemas["BatchPage"];
export type BatchCountsOut = ApiSchemas["BatchCounts"];
export type RevokeOut = ApiSchemas["RevokeOut"];
export type QuarantineRawOut = ApiSchemas["QuarantineRaw"];
export type QuarantineItemOut = ApiSchemas["QuarantineItem"];
export type QuarantinePageOut = ApiSchemas["QuarantinePage"];

export type JobOut = ApiSchemas["JobOut"];
export type JobAcceptedOut = ApiSchemas["JobAcceptedOut"];
export type JobLinks = ApiSchemas["JobLinks"];
export type JobPage = ApiSchemas["JobPage"];

// Period2-02：FIFO 回合（API-028~031）
export type RoundOut = ApiSchemas["RoundOut"];
export type RoundPageDocument = ApiSchemas["RoundPage"];
export type RoundFillOut = ApiSchemas["RoundFillOut"];
export type RoundFillPageDocument = ApiSchemas["RoundFillPage"];
export type RebuildRoundsIn = ApiSchemas["RebuildRoundsIn"];
export type RebuildRoundsBody = RebuildRoundsIn;

// Period2-03：净值与收益（API-032）
export type NavOut = ApiSchemas["NavOut"];
export type NavPointOut = ApiSchemas["NavPointOut"];
export type NavSummaryOut = ApiSchemas["NavSummaryOut"];

export type HealthOut = ApiSchemas["HealthOut"];
export type ReadyOut = ApiSchemas["ReadyOut"];
export type VersionOut = ApiSchemas["VersionOut"];

// ── 前端对外 DTO（全部建立在 generated 之上；能别名就别名） ────────────────

/** RFC 7807 错误体（UI/ApiProblemError 的公开类型；字段来自契约 `Error`）。 */
export type Problem = ErrorDocument;

/** 当前用户（API-005 的 `MeOut.user`）。 */
export type User = UserBrief;
export type UserRole = User["role"];

export type AuthTokens = RefreshOut;
export type LoginResponse = AuthOut;
export type RegisterResponse = AuthOut;

export type PatToken = PatMeta;
export type CreatedPatToken = PatMetaWithSecret;
export type TokenList = PatListOut;

export type Trade = TradeOut;
export type TradeSide = Trade["side"];

/** API-011 提交导入请求体（`ImportCommitIn`）。 */
export type CommitImportBody = ImportCommitIn;
/** API-007 创建 PAT 请求体（`PatIn`）。 */
export type CreateTokenBody = PatIn;
/** API-010 预览时除文件外的 multipart 字段（`Body_previewImport` 去掉 `file`）。 */
export type PreviewOptions = Omit<ApiSchemas["Body_previewImport"], "file">;

/** 流水列表（客户端增强）：契约 `TradePage` + HTTP 头里的 ETag + 响应 meta。
 * 契约部分由 `AssertTradePageBase` 钉死，附加字段只在客户端使用。
 */
export interface TradePage extends TradePageDocument {
  meta: { data_as_of: string };
  etag: string | null;
}

/** PATCH 流水的客户端结果：契约 `TradeMutationOut` + ETag 头。 */
export interface TradeMutationResult {
  trade: TradeMutationOut["trade"];
  affected_metrics: NonNullable<TradeMutationOut["affected_metrics"]>;
  etag: string | null;
}
/** 兼容旧名（http.ts 内部使用）。 */
export type TradeResult = TradeMutationResult;
export type RestoreResult = TradeRestoreOut;
export type TradeHistoryRecord = TradeHistoryItem;
export type TradeHistoryPage = TradeHistoryOut;

export type Preview = PreviewOut;
export type PreviewColumn = PreviewColumnOut;
export type PreviewWarning = PreviewWarningOut;
export type PreviewEstimate = PreviewEstimatedOut;

export type ImportBatch = BatchDetailOut;
/** 批次列表项（API-012 只返回 BatchOut，不含 fills[]）。 */
export type ImportBatchListItem = BatchOut;
export type ImportBatchFill = BatchFillOut;
export type ImportBatchStatus = ImportBatch["status"];
export type BatchList = BatchPageOut;
export type BatchCounts = BatchCountsOut;
export type RevokeResult = RevokeOut;
export type QuarantineRaw = QuarantineRawOut;
export type QuarantineItem = QuarantineItemOut;
export type QuarantinePage = QuarantinePageOut;

/** API-062/064 返回的任务详情（`JobOut`）。 */
export type Job = JobOut;
/** API-011 提交导入返回的 202 体（`JobAcceptedOut`，不是 JobOut）。 */
export type JobAccepted = JobAcceptedOut;
export type JobStatus = Job["status"];
export type JobType = Job["type"];
/** API-061 任务列表（`JobPage`）。 */
export type JobList = JobPage;

/** Period2-02：回合（一次卖出 + 其消耗的买入 lot 配对）与 FIFO 明细。 */
export type Round = RoundOut;
export type RoundStatus = Round["status"];
export type RoundPage = RoundPageDocument;
export type RoundFill = RoundFillOut;
export type RoundFillPage = RoundFillPageDocument;

/** Period2-03：净值与收益（份额法 TWR + XIRR/MWR）。 */
export type Nav = NavOut;
export type NavStatus = Nav["status"];
export type NavPoint = NavPointOut;
export type NavSummary = NavSummaryOut;

// ── 编译期断言：后端契约变化必须在这里/typecheck 里炸出来 ─────────────────

/** http.ts / UI 读取的核心字段必须仍存在于 generated schema。 */
export type AssertTradeFields = Assert<
  FieldsPresent<
    Trade,
    | "trade_id"
    | "trade_date"
    | "security_code"
    | "side"
    | "quantity"
    | "price"
    | "amount"
    | "commission"
    | "stamp_tax"
    | "other_fee"
    | "cash_balance"
    | "cash_balance_available"
    | "dedupe_key"
    | "version"
    | "deleted_at"
  >
>;
export type AssertPreviewFields = Assert<
  FieldsPresent<Preview, "preview_id" | "file" | "columns" | "warnings" | "estimated" | "cash_balance_available" | "can_commit">
>;
export type AssertPreviewEstimatedFields = Assert<FieldsPresent<PreviewEstimate, "parsed" | "inserted" | "skipped" | "quarantined">>;
export type AssertBatchFields = Assert<
  FieldsPresent<ImportBatch, "batch_id" | "status" | "mode" | "broker" | "file_name" | "counts" | "filled_count" | "fills" | "warnings" | "can_revoke">
>;
export type AssertBatchCountsFields = Assert<FieldsPresent<BatchCounts, "parsed" | "inserted" | "skipped" | "quarantined">>;
export type AssertJobFields = Assert<FieldsPresent<Job, "job_id" | "type" | "status" | "progress" | "stage" | "result_ref">>;
export type AssertJobAcceptedFields = Assert<FieldsPresent<JobAccepted, "job_id" | "type" | "batch_id" | "links">>;
export type AssertPatFields = Assert<FieldsPresent<PatToken, "token_id" | "name" | "prefix" | "scopes" | "expires_at">>;
export type AssertProblemFields = Assert<
  FieldsPresent<Problem, "code" | "user_message" | "what_to_do" | "retryable" | "request_id" | "errors" | "status" | "title" | "type">
>;
export type AssertQuarantineFields = Assert<FieldsPresent<QuarantineItem, "line_no" | "raw" | "reason_code" | "resolved">>;
export type AssertRevokeFields = Assert<FieldsPresent<RevokeResult, "batch_id" | "status" | "reverted" | "revoked_at">>;
/** 回合页/详情页读取的字段必须仍存在于契约（后端改名 → tsc 立即失败）。 */
export type AssertRoundFields = Assert<
  FieldsPresent<
    Round,
    | "round_id"
    | "security_code"
    | "security_name"
    | "status"
    | "open_date"
    | "close_date"
    | "quantity"
    | "cost"
    | "proceeds"
    | "dividend"
    | "pnl"
    | "return_rate"
    | "holding_days"
    | "open_trade_id"
    | "close_trade_id"
    | "data_error"
    | "oversell_quantity"
    | "fills_count"
    | "rebuilt_at"
  >
>;
export type AssertRoundFillFields = Assert<
  FieldsPresent<
    RoundFill,
    | "fill_id"
    | "sequence"
    | "buy_trade_id"
    | "sell_trade_id"
    | "quantity"
    | "open_date"
    | "close_date"
    | "holding_days"
  >
>;
export type AssertRoundPageFields = Assert<FieldsPresent<RoundPage, "items" | "next_cursor" | "has_more">>;
/** 净值页读取的字段必须仍存在于契约（后端改名 → tsc 立即失败）。 */
export type AssertNavFields = Assert<
  FieldsPresent<
    Nav,
    "period" | "points" | "summary" | "status" | "blocked_ranges" | "benchmark_id" | "data_as_of" | "definition_version"
  >
>;
export type AssertNavPointFields = Assert<FieldsPresent<NavPoint, "date" | "unit_nav" | "twr_daily" | "cashflow" | "blocked">>;
export type AssertNavSummaryFields = Assert<
  FieldsPresent<NavSummary, "mwr" | "twr" | "annualized_twr" | "mwr_minus_twr" | "n_days">
>;


/** 客户端增强类型必须仍然覆盖契约响应（契约删字段 → 这里报错）。 */
export type AssertTradePageBase = Assert<IsSubset<TradePageDocument, Pick<TradePage, keyof TradePageDocument>>>;
export type AssertTradeMutationSource = Assert<IsSubset<TradeMutationResult["trade"], TradeOut>>;
