import { useEffect, useRef, useState } from "react";
import type {
  JobDoneData,
  JobErrorData,
  JobProgressData,
  JobStatus,
  JobEvent,
} from "@sa/api-client";
import { api } from "../client";

/** SSE 重连上限（指数退避 500ms×(attempt+1)）。 */
const SSE_MAX_ATTEMPTS = 5;
/** SSE 失效后兜底轮询的间隔：低频，只求收尾，不追实时。 */
const FALLBACK_POLL_MS = 3000;

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>([
  "succeeded",
  "failed",
  "cancelled",
]);

export interface JobCompletionState {
  progress: number;
  stage: string;
  message: string;
  status: JobStatus | null;
  error: unknown;
}

export type JobDoneHandler = (status: JobStatus, resultRef: string | null, error?: unknown) => void;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 任务终态订阅（唯一实现）：SSE 为主通道，失效后才降级为低频 `GET /jobs/{id}`。
 *
 * 收尾职责集中在这里，`JobProgress` 只做展示、页面侧只做编排（无 UI 也能用），
 * 避免「组件订阅 + 页面轮询」两条并行通道。
 *
 * SSÉ 的两个缺口由兜底补齐：
 * 1. 流正常结束却没收到 done/error（EOF）：转 `jobs.get` 收尾；
 * 2. 后端「重连落在终态之后」补发的合成终态不含 `error`：转 `jobs.get` 取真实原因。
 */
export function useJobCompletion(
  jobId: string | null | undefined,
  onDone?: JobDoneHandler,
): JobCompletionState {
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [error, setError] = useState<unknown>(null);

  /** 回调放 ref：调用方每次渲染换新函数时不应重连 SSE。 */
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const controller = new AbortController();
    let lastEventId: string | undefined;
    let settled = false;

    /** 收尾：同一 job 只回调一次（SSE 收尾与兜底轮询可能都想收）。 */
    function finish(next: JobStatus, resultRef: string | null, err?: unknown): void {
      if (settled || cancelled) return;
      settled = true;
      setStatus(next);
      setProgress(100);
      if (err !== undefined) setError(err);
      doneRef.current?.(next, resultRef, err);
    }

    /** 用任务详情刷新进度；返回是否已到终态（并已收尾）。 */
    async function pollOnce(): Promise<boolean> {
      const job = await api.jobs.get(jobId as string);
      if (cancelled || settled) return true;
      if (TERMINAL.has(job.status)) {
        finish(job.status, job.result_ref ?? null, job.error ?? undefined);
        return true;
      }
      // 兜底期间也把详情里的进度回填到界面。
      setProgress(job.progress);
      setStage(job.stage);
      setMessage(job.message ?? "");
      setError(null);
      return false;
    }

    async function handle(event: JobEvent): Promise<boolean> {
      if (event.id) lastEventId = event.id;
      if (event.event === "progress") {
        const data = event.data as JobProgressData;
        setProgress(data.progress);
        setStage(data.stage);
        setMessage(data.message ?? "");
        return false;
      }
      if (event.event === "done") {
        const data = event.data as JobDoneData;
        finish(data.status, data.result_ref ?? null);
        return true;
      }
      if (event.event === "error") {
        const data = event.data as JobErrorData & { status?: JobStatus };
        if (data.error) {
          finish(data.status ?? "failed", null, data.error);
          return true;
        }
        // 合成终态（重连落在终态之后）没有 error：补一次详情拿真实原因。
        try {
          const job = await api.jobs.get(jobId as string);
          finish(job.status, job.result_ref ?? null, job.error ?? undefined);
        } catch {
          finish(data.status ?? "failed", null);
        }
        return true;
      }
      return false;
    }

    /** SSE 主通道：返回是否已收尾（正常结束但没终态 → false，交给兜底）。 */
    async function runSse(): Promise<boolean> {
      for (let attempt = 0; attempt < SSE_MAX_ATTEMPTS && !cancelled && !settled; attempt += 1) {
        try {
          for await (const event of api.jobs.events(jobId as string, {
            lastEventId,
            signal: controller.signal,
          })) {
            if (cancelled || settled) return true;
            if (await handle(event)) return true;
          }
          // 流正常结束却没有 done/error：不静默返回，降级为兜底轮询。
          return settled;
        } catch (err) {
          if (cancelled || settled) return true;
          setError(err);
          // 首个连接就失败 → 立刻并行启动兜底，不必等 5 次重试耗尽；
          // 谁先拿到终态由 finish() 的 settled 守卫决定，SSE 恢复后仍可抢先收尾。
          startFallback();
          await sleep(500 * (attempt + 1));
        }
      }
      return settled;
    }

    /** 兜底轮询：仅在 SSE 失效后启用；终态或卸载即停。 */
    async function runFallback(): Promise<void> {
      for (;;) {
        if (cancelled || settled) return;
        try {
          if (await pollOnce()) return;
        } catch (err) {
          if (!cancelled) setError(err);
        }
        await sleep(FALLBACK_POLL_MS);
      }
    }

    let fallbackStarted = false;
    function startFallback(): void {
      if (fallbackStarted || cancelled || settled) return;
      fallbackStarted = true;
      void runFallback();
    }

    void (async () => {
      const done = await runSse();
      if (!done && !cancelled && !settled) startFallback();
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [jobId]);

  return { progress, stage, message, status, error };
}
