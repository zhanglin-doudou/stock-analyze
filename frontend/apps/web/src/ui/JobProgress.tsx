import { useEffect, useState } from "react";
import type { JobDoneData, JobErrorData, JobProgressData, JobStatus, JobEvent } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "./Problem";

export interface JobProgressProps {
  jobId: string;
  onDone?: (status: JobStatus, resultRef: string | null) => void;
}

/** 订阅 `/jobs/{id}/events`，带 Last-Event-ID 断线续传，展示 progress/stage。 */
export function JobProgress({ jobId, onDone }: JobProgressProps) {
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let lastEventId: string | undefined;

    function handle(event: JobEvent): boolean {
      if (event.id) lastEventId = event.id;
      if (event.event === "progress") {
        const data = event.data as JobProgressData;
        setProgress(data.progress);
        setStage(data.stage);
        setMessage(data.message ?? "");
      } else if (event.event === "done") {
        const data = event.data as JobDoneData;
        setStatus(data.status);
        setProgress(100);
        onDone?.(data.status, data.result_ref ?? null);
        return true;
      } else if (event.event === "error") {
        const data = event.data as JobErrorData;
        setError(data.error);
        setStatus("failed");
        return true;
      }
      return false;
    }

    async function run(): Promise<void> {
      for (let attempt = 0; attempt < 5 && !cancelled; attempt += 1) {
        setError(null);
        try {
          for await (const event of api.jobs.events(jobId, { lastEventId, signal: controller.signal })) {
            if (cancelled) return;
            if (handle(event)) return;
          }
          return;
        } catch (err) {
          if (cancelled) return;
          setError(err);
          await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
        }
      }
    }

    void run();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [jobId, onDone]);

  return (
    <div className="job-progress">
      <div className="job-progress-bar">
        <div className="job-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <p className="muted">
        任务 {jobId}：{progress}%{stage ? ` · ${stage}` : ""}{status ? ` · ${status}` : ""}
      </p>
      {message ? <p className="muted">{message}</p> : null}
      {error ? <ProblemView error={error} /> : null}
    </div>
  );
}
