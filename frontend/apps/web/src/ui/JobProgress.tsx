import { useJobCompletion, type JobDoneHandler } from "../lib/useJobCompletion";
import { ProblemView } from "./Problem";

export interface JobProgressProps {
  jobId: string;
  /** 终态回调：`error` 为 SSE error 事件或任务详情里的真实错误体（可能缺失）。 */
  onDone?: JobDoneHandler;
}

/** 任务进度条：订阅逻辑见 `useJobCompletion`，本组件只负责展示。 */
export function JobProgress({ jobId, onDone }: JobProgressProps) {
  const { progress, stage, message, status, error } = useJobCompletion(jobId, onDone);

  return (
    <div className="job-progress">
      <div
        className="job-progress-bar"
        role="progressbar"
        aria-label="任务进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <div className="job-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <p className="muted" aria-live="polite">
        任务 {jobId}：{progress}%{stage ? ` · ${stage}` : ""}{status ? ` · ${status}` : ""}
      </p>
      {message ? <p className="muted">{message}</p> : null}
      {error ? <ProblemView error={error} /> : null}
    </div>
  );
}
