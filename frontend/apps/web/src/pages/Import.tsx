import { useState } from "react";
import { newIdempotencyKey, type Job, type Preview } from "@sa/api-client";
import { api } from "../client";
import { JobProgress } from "../ui/JobProgress";
import { ProblemView } from "../ui/Problem";
import { Uploader } from "../ui/Uploader";

export function Import() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");

  async function onFile(file: File): Promise<void> {
    setBusy(true);
    setError(null);
    setPreview(null);
    setJob(null);
    setDone("");
    try {
      setPreview(await api.imports.preview(file));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function commit(): Promise<void> {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      // 幂等键：同 key 同 body 会被后端重放，避免重复导入
      setJob(await api.imports.commit({ preview_id: preview.preview_id, mode: "full" }, newIdempotencyKey()));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>导入账单</h1>
      <Uploader disabled={busy} onFile={(file) => void onFile(file)} />
      <ProblemView error={error} onRetry={preview ? () => void commit() : undefined} />

      {preview ? (
        <div className="card">
          <h2>需求确认单</h2>
          <p className="muted">
            文件：{preview.file.name}（{preview.file.bytes} 字节）· 可提交：{preview.can_commit ? "是" : "否"} · 资金余额列：
            {preview.cash_balance_available ? "有" : "缺（暂无法对账）"}
          </p>
          <h3>列映射建议</h3>
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>列名</th>
                <th>建议字段</th>
                <th>置信度</th>
                <th>样例</th>
              </tr>
            </thead>
            <tbody>
              {preview.columns.map((column) => (
                <tr key={column.index}>
                  <td>{column.index}</td>
                  <td>{column.name}</td>
                  <td>{column.suggested_field ?? "（未识别）"}</td>
                  <td>{column.confidence}</td>
                  <td className="muted">{column.sample.join(" / ")}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>预检计数</h3>
          <p>
            解析 {preview.estimated.parsed} · 将写入 {preview.estimated.inserted} · 跳过 {preview.estimated.skipped} · 隔离{" "}
            {preview.estimated.quarantined}
          </p>

          {preview.warnings.length > 0 ? (
            <>
              <h3>警告</h3>
              <ul>
                {preview.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    <code>{warning.code}</code> {warning.row ? `第 ${warning.row} 行` : ""} {warning.message}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          <button className="btn" type="button" disabled={busy || !preview.can_commit || Boolean(job)} onClick={() => void commit()}>
            {busy ? "提交中…" : "确认导入"}
          </button>
        </div>
      ) : null}

      {job ? (
        <div className="card">
          <h2>导入进度</h2>
          <JobProgress
            jobId={job.job_id}
            onDone={(status) => setDone(`任务结束：${status}`)}
          />
          {done ? <p className="notice">{done}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
