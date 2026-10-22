import { useState } from "react";
import type { RevokeResult } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "./Problem";

export interface RevokeBatchButtonProps {
  batchId: string;
  /** 撤销成功后回调；页面负责提示并刷新批次/流水。 */
  onRevoked: (result: RevokeResult) => void;
  disabled?: boolean;
  label?: string;
}

/**
 * 批次撤销按钮：先二次确认（页面内联，不弹浏览器 dialog），再调 `POST /imports/{id}/revoke`。
 * 撤销失败走 ProblemView（code / user_message / what_to_do），不做裸 fetch。
 */
export function RevokeBatchButton({ batchId, onRevoked, disabled, label = "撤销" }: RevokeBatchButtonProps) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function revoke(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const result = await api.imports.revoke(batchId);
      setConfirming(false);
      onRevoked(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="revoke-batch">
      {error ? <ProblemView error={error} /> : null}
      {confirming ? (
        <div className="row-actions" role="group" aria-label="撤销确认">
          <span className="muted">确认撤销批次 {batchId}？将软删本批次写入的流水，不可在页面内恢复。</span>
          <button className="btn" type="button" disabled={busy || disabled} onClick={() => void revoke()}>
            {busy ? "撤销中…" : "确认撤销"}
          </button>
          <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setConfirming(false)}>
            取消
          </button>
        </div>
      ) : (
        <button className="btn btn-ghost" type="button" disabled={disabled} onClick={() => setConfirming(true)}>
          {label}
        </button>
      )}
    </div>
  );
}
