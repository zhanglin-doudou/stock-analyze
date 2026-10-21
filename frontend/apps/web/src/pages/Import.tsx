import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { newIdempotencyKey, type ImportBatch, type Job, type JobStatus, type Preview, type RevokeResult } from "@sa/api-client";
import { api } from "../client";
import { JobProgress } from "../ui/JobProgress";
import { ProblemView } from "../ui/Problem";
import { RevokeBatchButton } from "../ui/RevokeBatch";
import { Uploader } from "../ui/Uploader";

export function Import() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [batchError, setBatchError] = useState<unknown>(null);
  const [revokeNotice, setRevokeNotice] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");

  async function onFile(file: File): Promise<void> {
    setBusy(true);
    setError(null);
    setPreview(null);
    setJob(null);
    setBatchId(null);
    setBatch(null);
    setBatchError(null);
    setRevokeNotice("");
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
    setBatch(null);
    setBatchError(null);
    setRevokeNotice("");
    try {
      // 幂等键：同 key 同 body 会被后端重放，避免重复导入
      const accepted = await api.imports.commit({ preview_id: preview.preview_id, mode: "full" }, newIdempotencyKey());
      setJob(accepted);
      setBatchId(accepted.batch_id ?? null);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const loadBatch = useCallback(async (id: string) => {
    setBatchError(null);
    try {
      setBatch(await api.imports.getBatch(id));
    } catch (err) {
      setBatchError(err);
    }
  }, []);

  // JobProgress 的 onDone 保持稳定：任务成功后拉批次详情（计数/隔离数/可撤销性）。
  const onJobDone = useCallback(
    (status: JobStatus) => {
      setDone(`任务结束：${status}`);
      if (status !== "succeeded" || !batchId) return;
      void loadBatch(batchId);
    },
    [batchId, loadBatch],
  );

  const onRevoked = useCallback(
    (result: RevokeResult) => {
      setRevokeNotice(
        `已撤销本次导入（批次 ${result.batch_id}）：回退流水 ${result.reverted.inserted ?? 0} 行。去「交易流水」可核对行数已回退。`,
      );
      // 撤销后刷新本次批次（can_revoke/status/revoked_at 会变）；流水页重新进入时按最新数据加载。
      void loadBatch(result.batch_id);
    },
    [loadBatch],
  );

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
          <JobProgress jobId={job.job_id} onDone={onJobDone} />
          {done ? <p className="notice">{done}</p> : null}
          {batchError ? <ProblemView error={batchError} onRetry={batchId ? () => void loadBatch(batchId) : undefined} /> : null}
          {batch ? (
            <div className="batch-result">
              <h3>本次导入结果</h3>
              <p>
                批次号 <code>{batch.batch_id}</code> · 插入 {batch.counts.inserted} 行 · 隔离 {batch.counts.quarantined} 行 · 跳过{" "}
                {batch.counts.skipped} 行
              </p>
              <div className="row-actions">
                <Link className="btn btn-ghost" to={`/imports?batch=${batch.batch_id}`}>
                  查看批次
                </Link>
                {batch.can_revoke ? (
                  <RevokeBatchButton batchId={batch.batch_id} label="撤销本次导入" onRevoked={onRevoked} />
                ) : (
                  <span className="muted">该批次已不可撤销（{batch.status}）。</span>
                )}
                <Link className="btn btn-ghost" to="/trades">
                  去交易流水核对
                </Link>
              </div>
              {revokeNotice ? <p className="notice">{revokeNotice}</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
