import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { newIdempotencyKey, type TradeFlagRebuildStatusOut } from "@sa/api-client";
import { api } from "../../client";
import { useJobCompletion } from "../../lib/useJobCompletion";
import { ProblemView } from "../../ui/Problem";
import "./trade-flag-rebuild-dialog.css";

const AUTO_CLOSE_MS = 3000;

type Phase = "idle" | "running" | "done" | "error";

export interface TradeFlagRebuildDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * 看板 · 更新账单旗标弹窗（API-111 状态 + API-097 重建）。
 *
 * 状态、原因、旗标计数全部来自后端；本组件只编排「读取 → 更新 → 轮询 → 完成关闭」。
 * 「更新账单旗标」按 auto 模式（缺版本全量、否则只更新待算且行情已覆盖的月份）；
 * 「强制全量更新」按 full 模式全时段覆盖，二者共用同一个主按钮 loading。
 */
export function TradeFlagRebuildDialog({ open, onClose }: TradeFlagRebuildDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<TradeFlagRebuildStatusOut | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [startError, setStartError] = useState<unknown>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // open 即开关：jsdom 未完整实现 <dialog>，调用前守护一次。
  useEffect(() => {
    const element = dialogRef.current;
    if (!element) return;
    if (open && !element.open && typeof element.showModal === "function") {
      element.showModal();
    }
    if (!open && element.open && typeof element.close === "function") {
      element.close();
    }
  }, [open]);

  const clearCloseTimer = useCallback((): void => {
    if (closeTimer.current !== null) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  useEffect(() => {
    clearCloseTimer();
    setStatus(null);
    setStatusError(null);
    setStartError(null);
    setPhase("idle");
    setJobId(null);
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api.settings
      .tradeFlagRebuildStatus()
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch((err) => {
        if (!cancelled) setStatusError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, clearCloseTimer]);

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer]);

  const completion = useJobCompletion(jobId, (next, _resultRef, error) => {
    if (next === "succeeded") {
      setPhase("done");
      // 月度图表依赖旗标行，完成后刷新看板数据。
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "trade-flags"] });
      clearCloseTimer();
      closeTimer.current = setTimeout(onClose, AUTO_CLOSE_MS);
      return;
    }
    setPhase("error");
    if (error !== undefined) setStartError(error);
  });

  const busy = phase === "running";

  async function start(mode: "auto" | "full"): Promise<void> {
    if (busy) return;
    clearCloseTimer();
    setStartError(null);
    setPhase("running");
    setJobId(null);
    try {
      const accepted = await api.settings.rebuildTradeFlags(
        { scope: "all", months: [], mode },
        newIdempotencyKey(),
      );
      setJobId(accepted.job_id);
    } catch (err) {
      setPhase("error");
      setStartError(err);
    }
  }

  // 上一个 job 的终态会残留 status/progress；只有进行中才展示真实百分比。
  const progressLive =
    completion.status === null ||
    completion.status === "pending" ||
    completion.status === "running";
  const mainLabel =
    phase === "running"
      ? progressLive
        ? `更新中 ${completion.progress}%…`
        : "更新中…"
      : phase === "done"
        ? "更新完成"
        : "更新账单旗标";

  return (
    <dialog
      ref={dialogRef}
      className="tfr-dialog"
      aria-labelledby="tfr-dialog-title"
      onClose={onClose}
    >
      <form method="dialog" className="tfr-dialog-head">
        <h2 id="tfr-dialog-title">更新账单旗标</h2>
        <button type="submit" aria-label="关闭">
          ×
        </button>
      </form>

      {status ? (
        <>
          <p className="muted tfr-summary" data-testid="tfr-summary">
            口径版本 {status.definition_version} · 共 {status.flag_kinds} 种旗标 /{" "}
            {status.flag_total} 条记录
            {status.market_as_of ? ` · 行情截至 ${status.market_as_of}` : ""}
          </p>

          {status.reasons.length > 0 ? (
            <ul className="tfr-reasons" data-testid="tfr-reasons">
              {status.reasons.map((reason) => (
                <li key={reason.code}>{reason.message}</li>
              ))}
            </ul>
          ) : (
            <p className="muted" data-testid="tfr-up-to-date">
              当前账单旗标无需更新。
            </p>
          )}

          {status.flags.length > 0 ? (
            <table className="tfr-flags" data-testid="tfr-flags">
              <thead>
                <tr>
                  <th scope="col">账单旗标</th>
                  <th scope="col">数量</th>
                </tr>
              </thead>
              <tbody>
                {status.flags.map((item) => (
                  <tr key={item.code}>
                    <td>{item.label_zh}</td>
                    <td className="tfr-count">{item.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">尚无当前口径的账单旗标数据。</p>
          )}
        </>
      ) : loading ? (
        <p className="muted">正在读取账单旗标状态…</p>
      ) : null}

      {phase === "running" ? (
        <div className="tfr-progress" data-testid="tfr-progress">
          <div
            className="tfr-progress-bar"
            role="progressbar"
            aria-label="更新账单旗标进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={completion.progress}
          >
            <div className="tfr-progress-fill" style={{ width: `${completion.progress}%` }} />
          </div>
        </div>
      ) : null}

      {phase === "done" ? (
        <p className="tfr-done" data-testid="tfr-done" aria-live="polite">
          更新完成，3 秒后自动关闭。
        </p>
      ) : null}

      {statusError ? <ProblemView error={statusError} /> : null}
      {startError ?? completion.error ? (
        <ProblemView error={startError ?? completion.error} />
      ) : null}

      <div className="tfr-actions">
        <button
          type="button"
          className="btn"
          data-testid="tfr-rebuild"
          disabled={busy || phase === "done"}
          onClick={() => void start("auto")}
        >
          {mainLabel}
        </button>
        <button
          type="button"
          className="tfr-link-btn"
          data-testid="tfr-rebuild-full"
          disabled={busy || phase === "done"}
          onClick={() => void start("full")}
        >
          强制全量更新
        </button>
      </div>

      <p className="muted tfr-hint">
        自动更新：口径版本缺失或变更时全时段重建，否则只更新未来行情已覆盖的待算旗标。
      </p>
    </dialog>
  );
}
