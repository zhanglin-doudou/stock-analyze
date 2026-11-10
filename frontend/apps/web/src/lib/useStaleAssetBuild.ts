import { useCallback, useEffect, useRef, useState } from "react";
import {
  newIdempotencyKey,
  type AssetBuildAccepted,
  type AssetBuildState,
  type JobStatus,
  type Problem,
} from "@sa/api-client";
import { api } from "../client";

/** 自动构建状态：idle（无需更新/已收敛）/ submitting（POST 中）/ running（job 执行中）/ failed（转手动重试）。 */
export type AutoBuildStatus = "idle" | "submitting" | "running" | "failed";

export type AssetBuildMode = "auto" | "full";

export interface StaleAssetBuild {
  status: AutoBuildStatus;
  jobId: string | null;
  /** 失败原因（POST 的 RFC 7807 错误体或 job.error），直接交给 ProblemView 渲染。 */
  error: unknown;
  /** 页面主动构建（资产页「更新资产表」/「强制重新构建」）。 */
  submit(mode?: AssetBuildMode): Promise<void>;
  /** 失败后手动重试：丢弃该 stale 代次的失败记录再重新 POST。 */
  retry(): void;
  /** 透传给 `<JobProgress onDone>`：SSE 与轮询收敛到同一完成路径。 */
  handleJobDone(status: JobStatus): void;
}

export interface UseStaleAssetBuildOptions {
  /** 后端只读构建摘要（`build` 块，`/assets` 与 `/dashboard` 同源）；缺失时不触发。 */
  build: AssetBuildState | null | undefined;
  /** 构建成功后重新拉取本页数据（资产页 = 列表，看板 = 看板数据）。 */
  onRefresh: () => void | Promise<void>;
}

/** 模块级唯一真源：stale 指纹 → POST 结果。
 *
 * 放在模块级而不是组件内：StrictMode 双挂载、`/assets` 与 `/dashboard` 同时挂载都共享
 * 同一份状态，同一 stale 代次全局只会发一次 `POST /assets/build`；任一侧卸载/切换都
 * 不会让另一侧重复触发。
 */
const autoBuildSubmissions = new Map<string, Promise<string | null>>();

/** 一次 stale 代次的指纹：只用后端只读字段，前端不做 staleness 计算。
 *  `last_built_at` 每次构建都会刷新 → 构建完成后即为新代次。 */
export function staleFingerprint(build: AssetBuildState): string {
  return [
    build.state,
    build.last_built_at ?? "",
    build.last_built_through ?? "",
    build.rows_count,
    build.definition_version,
  ].join("|");
}

/** 测试专用：清空模块级触发守卫（用例之间需要干净的 stale 代次）。 */
export function resetAutoBuildGuard(): void {
  autoBuildSubmissions.clear();
}

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>(["succeeded", "failed", "cancelled"]);

/** job failed 且后端未给出 7807 错误体时，用错误码兜底渲染。 */
function jobFailureProblem(status: JobStatus): Problem {
  return {
    type: "about:blank",
    title: "Build assets failed",
    status: 0,
    detail: `job status=${status}`,
    code: "BUILD_ASSETS_FAILED",
    user_message: `资产表构建未成功（${status}）。`,
    what_to_do: "请稍后重试；若持续失败，请到任务页查看错误码或联系支持。",
    retryable: true,
    request_id: "",
  };
}

/**
 * 资产表 stale 自动构建（前端检测 + 显式触发）。
 *
 * 时序：`build.state == "stale"` → effect 内 `POST /assets/build{mode:auto}`（带
 * Idempotency-Key）→ 轮询 job 到终态 → succeeded 调 `onRefresh()`；失败转手动重试。
 * 判定与文案全部来自后端，本 hook 只做编排与去重。
 */
export function useStaleAssetBuild({
  build,
  onRefresh,
}: UseStaleAssetBuildOptions): StaleAssetBuild {
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  /** 失败时所在的 stale 代次指纹（null 表示无失败）；页面数据换到新代次后自动清除提示。 */
  const [failedKey, setFailedKey] = useState<string | null>(null);
  /** 同一个 job 可能被 SSE done 与页面轮询各通知一次：只处理一次。 */
  const handledJobRef = useRef<string | null>(null);
  /** 回调放 ref：避免页面每次渲染换新函数导致轮询 effect 抖动。 */
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;
  const buildRef = useRef(build);
  buildRef.current = build;

  const currentKey = useCallback(
    (): string | null => (buildRef.current ? staleFingerprint(buildRef.current) : null),
    [],
  );

  const finishJob = useCallback(
    async (jobId: string, status: JobStatus, jobError?: unknown): Promise<void> => {
      if (handledJobRef.current === jobId) return;
      handledJobRef.current = jobId;
      setActiveJobId((current) => (current === jobId ? null : current));
      if (status === "succeeded") {
        await refreshRef.current();
        return;
      }
      setError(jobError ?? jobFailureProblem(status));
      setFailedKey(currentKey());
    },
    [currentKey],
  );

  /** 发一次 POST，返回 job_id；失败保留后端 7807 错误体并返回 null。 */
  const postBuild = useCallback(
    async (mode: AssetBuildMode): Promise<string | null> => {
      try {
        const accepted: AssetBuildAccepted = await api.assets.build({ mode }, newIdempotencyKey());
        handledJobRef.current = null;
        return accepted.job_id;
      } catch (err) {
        setError(err);
        setFailedKey(currentKey());
        return null;
      }
    },
    [currentKey],
  );

  const submit = useCallback(
    async (mode: AssetBuildMode = "auto"): Promise<void> => {
      setSubmitting(true);
      setError(null);
      setFailedKey(null);
      const task = postBuild(mode);
      // 手动/自动共用同一去重表：同一 stale 代次后续不会再被 effect 重复 POST。
      if (build) autoBuildSubmissions.set(staleFingerprint(build), task);
      const jobId = await task;
      if (jobId) setActiveJobId(jobId);
      setSubmitting(false);
    },
    [build, postBuild],
  );

  const handleJobDone = useCallback(
    (status: JobStatus): void => {
      if (!activeJobId) return;
      void finishJob(activeJobId, status);
    },
    [activeJobId, finishJob],
  );

  const retry = useCallback((): void => {
    if (build) autoBuildSubmissions.delete(staleFingerprint(build));
    void submit("auto");
  }, [build, submit]);

  // 进入页面接管在跑的 build_assets（刷新后不重复点击、不误报「未构建」）。
  useEffect(() => {
    let cancelled = false;
    async function takeover(): Promise<void> {
      try {
        const jobs = await api.jobs.list({ status: "pending,running", limit: 50 });
        const active = jobs.items.find((item) => item.type === "build_assets");
        if (active && !cancelled) {
          handledJobRef.current = null;
          setActiveJobId((current) => current ?? active.job_id);
        }
      } catch {
        // 任务列表只是辅助提示：失败不阻断页面本身。
      }
    }
    void takeover();
    return () => {
      cancelled = true;
    };
  }, []);

  // 轮询兜底：JobProgress 只负责 SSE（失败会重试 SSE 但不轮询）。
  // 卸载/路由切换时置 cancelled 并清定时器，不再发起下一次轮询。
  useEffect(() => {
    if (!activeJobId) return;
    const jobId = activeJobId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll(): Promise<void> {
      try {
        const job = await api.jobs.get(jobId);
        if (cancelled) return;
        if (TERMINAL.has(job.status)) {
          await finishJob(jobId, job.status, job.error ?? undefined);
          return;
        }
      } catch (err) {
        if (!cancelled) setError(err);
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 800);
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [activeJobId, finishJob]);

  // 页面数据换到新代次（构建完成 / 数据刷新）→ 清掉旧代次的失败提示；
  // 未构建/已最新状态下手动构建失败时指纹不变，提示保持到用户重试或数据变化。
  useEffect(() => {
    if (failedKey === null || !build) return;
    if (staleFingerprint(build) !== failedKey) setFailedKey(null);
  }, [build, failedKey]);

  // 前端检测 + 显式触发：只在读到后端 stale 时 POST（不在渲染期触发）。
  // 同一 stale 指纹共用模块级 Promise：StrictMode 双挂载、跨页并发挂载都只发一次 POST。
  useEffect(() => {
    if (!build || build.state !== "stale") return;
    if (activeJobId || submitting) return;

    const fingerprint = staleFingerprint(build);
    let submission = autoBuildSubmissions.get(fingerprint);
    if (!submission) {
      // 先同步占位再 await：并发挂载的第二个实例只复用同一 Promise，不会二次 POST。
      setSubmitting(true);
      submission = postBuild("auto");
      autoBuildSubmissions.set(fingerprint, submission);
      void submission.finally(() => setSubmitting(false));
    }

    let cancelled = false;
    void submission.then((jobId) => {
      if (cancelled) return;
      if (!jobId) {
        setFailedKey(currentKey());
        return;
      }
      if (handledJobRef.current === jobId) return;
      setActiveJobId((current) => current ?? jobId);
    });
    return () => {
      cancelled = true;
    };
  }, [build, activeJobId, submitting, postBuild, currentKey]);

  const status: AutoBuildStatus = failedKey !== null
    ? "failed"
    : activeJobId
      ? "running"
      : submitting
        ? "submitting"
        : "idle";

  return { status, jobId: activeJobId, error, submit, retry, handleJobDone };
}
