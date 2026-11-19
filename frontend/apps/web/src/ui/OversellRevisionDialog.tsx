import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { OversellRevisionOut } from "@sa/api-client";
import { api } from "../client";
import { JobProgress } from "./JobProgress";
import { ProblemView } from "./Problem";
import "./revision-dialog.css";

/** 超卖人工修订状态（后端 status → 中文，前端不做额外推断）。 */
export const REVISION_STATUS_LABELS: Record<OversellRevisionOut["status"], string> = {
  none: "待补录成本",
  revised: "已人工修订",
  superseded: "真实账单已覆盖（待确认）",
  not_oversell: "无需修订",
};

export interface OversellRevisionDialogProps {
  /** 待补录成本的超卖卖出流水；为 null 时弹窗关闭。 */
  tradeId: string | null;
  onClose: () => void;
  /** 重算完成（注入行已生效）后通知调用方刷新/跳转。 */
  onSettled?: (revision: OversellRevisionOut) => void;
}

/**
 * 超卖成本补录弹窗（API-107~109）：按卖出流水读取修订状态，补录成本后插入
 * 「资金划转 + 无费用买入」两条注入行并触发回合/资产重算。
 *
 * 回合详情页与资产表页共用同一组件、同一条卖出流水与同一个接口，避免两套交互。
 */
export function OversellRevisionDialog({
  tradeId,
  onClose,
  onSettled,
}: OversellRevisionDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const [revision, setRevision] = useState<OversellRevisionOut | null>(null);
  const [cost, setCost] = useState("");
  const [isNewStock, setIsNewStock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  // tradeId 即开关：非空时 showModal，空时 close（ESC / 关闭按钮由 onClose 回传）。
  // jsdom 未完整实现 <dialog>，调用前守护一次。
  useEffect(() => {
    const element = dialogRef.current;
    if (!element) return;
    if (tradeId && !element.open && typeof element.showModal === "function") {
      element.showModal();
    }
    if (!tradeId && element.open && typeof element.close === "function") {
      element.close();
    }
  }, [tradeId]);

  useEffect(() => {
    setError(null);
    setJobId(null);
    if (!tradeId) {
      setRevision(null);
      setCost("");
      setIsNewStock(false);
      return;
    }
    let cancelled = false;
    api.trades
      .oversellRevision(tradeId)
      .then((data) => {
        if (cancelled) return;
        setRevision(data);
        setCost(data.cost ?? "");
        setIsNewStock(data.is_new_stock ?? false);
      })
      .catch((err) => {
        if (!cancelled) {
          setRevision(null);
          setError(err);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tradeId]);

  /** 重算完成后重查一次修订信息（回合 id 可能已变），再交给调用方刷新。 */
  const settle = useCallback(async (): Promise<void> => {
    if (!tradeId) return;
    try {
      const next = await api.trades.oversellRevision(tradeId);
      setRevision(next);
      onSettled?.(next);
    } catch (err) {
      setError(err);
    }
  }, [tradeId, onSettled]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!tradeId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.trades.putOversellRevision(tradeId, {
        cost: cost.trim(),
        is_new_stock: isNewStock,
      });
      setRevision(result.revision);
      if (result.rebuild_job_id) {
        setJobId(result.rebuild_job_id);
      } else {
        // 已有同参数重建在跑：直接查一次结果（可能尚未算完，页面会提示刷新）。
        await settle();
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function revert(): Promise<void> {
    if (!tradeId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.trades.deleteOversellRevision(tradeId);
      setRevision(result.revision);
      setJobId(result.rebuild_job_id ?? null);
      if (!result.rebuild_job_id) await settle();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={dialogRef} className="revision-dialog" aria-labelledby="revision-dialog-title" onClose={onClose}>
      <form method="dialog" className="revision-dialog-head">
        <h2 id="revision-dialog-title">超卖成本补录</h2>
        <button type="submit" aria-label="关闭">
          ×
        </button>
      </form>

      {revision ? (
        <>
          <table className="detail-stats">
            <tbody>
              <tr>
                <th>卖出流水</th>
                <td>
                  <code>{revision.sell.trade_id}</code>
                </td>
                <th>卖出日</th>
                <td>{revision.sell.trade_date}</td>
              </tr>
              <tr>
                <th>证券</th>
                <td>
                  {revision.sell.security_code} {revision.sell.security_name}
                </td>
                <th><b>卖出数量 / 金额</b></th>
                <td className="danger-text">
                  <b>{revision.sell.quantity} / {revision.sell.amount}</b>
                </td>
              </tr>
              <tr>
                <th><b>未配对数量</b></th>
                <td className="danger-text"><b>{revision.sell.oversell_quantity}</b></td>
                <th>修订状态</th>
                <td>{REVISION_STATUS_LABELS[revision.status]}</td>
              </tr>
            </tbody>
          </table>

          {revision.status === "superseded" ? (
            <div className="notice-warn" role="alert">
              重新导入的真实账单已经可以完整配对这笔卖出，人工修订的注入行可能不再需要。
              系统不会自动删除历史修订，请确认后在下方撤回。
            </div>
          ) : null}

          {revision.cash_trade && revision.buy_trade ? (
            <p className="muted">
              当前注入行：资金划转 <code>{revision.cash_trade.trade_id}</code>（
              {revision.cash_trade.amount}）、买入 <code>{revision.buy_trade.trade_id}</code>
              （数量 {revision.buy_trade.quantity}，金额 {revision.buy_trade.amount}），备注「
              {revision.buy_trade.note ?? "人工修订，非真实成交账单"}」。
            </p>
          ) : null}

          <form onSubmit={(event) => void submit(event)}>
            <label htmlFor="revision-cost">未配对部分的总成本（元）</label>
            <input
              id="revision-cost"
              inputMode="decimal"
              value={cost}
              onChange={(event) => setCost(event.target.value)}
              placeholder="例如 1000.00"
              autoComplete="off"
            />
            <label className="revision-dialog-switch" htmlFor="revision-is-new-stock">
              <input
                id="revision-is-new-stock"
                type="checkbox"
                checked={isNewStock}
                onChange={(event) => setIsNewStock(event.target.checked)}
              />
              是否属于打新收益
            </label>
            <div className="revision-dialog-actions">
              <button
                type="submit"
                className="btn"
                disabled={
                  busy ||
                  revision.status === "superseded" ||
                  revision.status === "not_oversell" ||
                  cost.trim() === ""
                }
              >
                {revision.status === "revised" ? "更新成本" : "提交修订"}
              </button>
              {revision.status === "revised" || revision.status === "superseded" ? (
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => void revert()}
                >
                  撤回修订
                </button>
              ) : null}
            </div>
          </form>

          {revision.buy_trade?.amount && revision.cost ? (
            <p className="muted">
              修订后按 FIFO 重新配对：数量 {revision.buy_trade.quantity}，买入成本 {revision.cost}；
              回合盈亏 = 卖出金额 − 买入成本（含原有持仓成本）。
            </p>
          ) : null}
        </>
      ) : (
        <p className="muted">正在读取该卖出的超卖信息…</p>
      )}

      {error ? <ProblemView error={error} /> : null}
      {jobId ? <JobProgress jobId={jobId} onDone={() => void settle()} /> : null}

      <p className="muted">
        提交后会在该卖出当日、排在卖出之前插入「资金划转 + 无费用买入」两条流水并自动重算回合；
        资产表请在全部补录完成后到资产表页点「更新资产表」统一重算。
      </p>
    </dialog>
  );
}
