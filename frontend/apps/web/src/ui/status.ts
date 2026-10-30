/**
 * Phase 1 · 状态语义唯一来源（两轴）。
 *
 * 背景：现状 7 套 STATUS_LABELS 重复、blocked 在不同轴含义不同（见 01 审计 §3.7）。
 * 本模块只负责「文案 / 色调 / 是否显示数值 / 页面优先级」，不做任何业务判断。
 * 页面状态优先级由设计文档 D-12 定义：blocked > missing > insufficient > stale > degraded > ok。
 */

export const BUILD_STATES = ["missing", "current", "stale", "blocked"] as const;
export type BuildState = (typeof BUILD_STATES)[number];

export const DATA_STATUSES = [
  "ok",
  "degraded",
  "stale",
  "blocked",
  "missing",
  "insufficient",
  "unreliable",
  "pending_window",
] as const;
export type DataStatus = (typeof DATA_STATUSES)[number];

export const SAMPLE_STATUSES = ["ok", "insufficient", "pending_window", "blocked"] as const;
export type SampleStatus = (typeof SAMPLE_STATUSES)[number];

export type StatusTone = "ok" | "warn" | "danger" | "muted" | "pending";

export const BUILD_STATE_LABELS: Record<BuildState, string> = {
  missing: "未构建",
  current: "最新",
  stale: "待更新",
  blocked: "阻断",
};

export const DATA_STATUS_LABELS: Record<DataStatus, string> = {
  ok: "正常",
  degraded: "降级",
  stale: "过期",
  blocked: "阻断",
  missing: "缺失",
  insufficient: "样本不足",
  unreliable: "不可信",
  pending_window: "窗口未满",
};

export const SAMPLE_STATUS_LABELS: Record<SampleStatus, string> = {
  ok: "可信",
  insufficient: "样本不足",
  pending_window: "窗口未满",
  blocked: "阻断",
};

const BUILD_STATE_TONES: Record<BuildState, StatusTone> = {
  current: "ok",
  stale: "warn",
  missing: "muted",
  blocked: "danger",
};

const DATA_STATUS_TONES: Record<DataStatus, StatusTone> = {
  ok: "ok",
  degraded: "warn",
  stale: "warn",
  insufficient: "warn",
  unreliable: "warn",
  pending_window: "pending",
  missing: "muted",
  blocked: "danger",
};

const PAGE_PRIORITY: Record<DataStatus, number> = {
  ok: 0,
  degraded: 1,
  stale: 2,
  pending_window: 2,
  insufficient: 3,
  unreliable: 3,
  missing: 4,
  blocked: 5,
};

export function isBuildState(value: string): value is BuildState {
  return (BUILD_STATES as readonly string[]).includes(value);
}

export function isDataStatus(value: string): value is DataStatus {
  return (DATA_STATUSES as readonly string[]).includes(value);
}

export function buildStateLabel(state: string): string {
  return isBuildState(state) ? BUILD_STATE_LABELS[state] : state;
}

export function dataStatusLabel(status: string): string {
  return isDataStatus(status) ? DATA_STATUS_LABELS[status] : status;
}

export function buildStateTone(state: string): StatusTone {
  return isBuildState(state) ? BUILD_STATE_TONES[state] : "muted";
}

export function dataStatusTone(status: string): StatusTone {
  return isDataStatus(status) ? DATA_STATUS_TONES[status] : "muted";
}

/**
 * 页面 headline 状态：取最严重的一个。
 * 空数组 → "ok"（没有坏消息即正常）。
 */
export function worstDataStatus(statuses: readonly string[]): DataStatus {
  let worst: DataStatus = "ok";
  for (const status of statuses) {
    if (!isDataStatus(status)) continue;
    if (PAGE_PRIORITY[status] > PAGE_PRIORITY[worst]) worst = status;
  }
  return worst;
}

/**
 * 是否允许展示数值（后端不输出值为准，这里只用于前端降级门禁）。
 * - ok / degraded / stale：可展示（degraded/stale 需同时提示）；
 * - insufficient / unreliable / pending_window：可展示但必须带样本标签；
 * - blocked / missing：不展示数字，只展示原因与 what_to_do。
 */
export function isValueVisible(status: string): boolean {
  return status !== "blocked" && status !== "missing";
}

/** 数据陈旧类状态：需要显示“去构建/更新”提示。 */
export function needsRebuild(buildState: string): boolean {
  return buildState === "missing" || buildState === "stale" || buildState === "blocked";
}
