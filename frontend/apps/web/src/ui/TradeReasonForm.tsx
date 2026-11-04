import { useState } from "react";
import type { TradeReason, TradeReasonIn } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "./Problem";

/** 理由码中文标签（设计 §4.2 静态枚举；前端只做文案映射，不参与任何计算）。 */
const CODE_LABELS: Record<string, string> = {
  plan_build: "计划建仓",
  add_on: "主动加仓",
  bottom_fish: "抄底",
  chase: "追涨",
  rebound: "抢反弹",
  dca: "定投/分批",
  news: "消息/大V",
  emotion: "情绪驱动",
  other: "其他",
  plan_reduce: "计划减仓",
  take_profit: "止盈",
  stop_loss: "止损",
  break_even: "回本/保本",
  rebalance: "调仓/换股",
  fear: "恐慌/杀跌",
};

const BUY_CODES = [
  "plan_build",
  "add_on",
  "bottom_fish",
  "chase",
  "rebound",
  "dca",
  "news",
  "emotion",
  "other",
] as const;

const SELL_CODES = [
  "plan_reduce",
  "take_profit",
  "stop_loss",
  "break_even",
  "rebalance",
  "news",
  "fear",
  "other",
] as const;

const EMOTION_LABELS: Record<string, string> = {
  calm: "平静",
  anxious: "焦虑",
  greedy: "贪婪",
  fear: "恐惧",
  impulsive: "冲动",
  other: "其他",
};

export interface TradeReasonFormProps {
  tradeId: string;
  side: "buy" | "sell";
  /** 当前有效理由；null 表示尚未标注。 */
  initial: TradeReason | null;
  disabled?: boolean;
  onSaved: (reason: TradeReason) => void;
  onDeleted: () => void;
}

/**
 * 逐笔理由编辑（API-090/091）：理由码 + 可选情绪/标签/补充文本。
 * 更新带 `If-Match`（版本冲突 409 由 ProblemView 透出）；删除为软删。
 * `reason_text` 只在请求体里提交，不回显到日志。
 */
export function TradeReasonForm({
  tradeId,
  side,
  initial,
  disabled,
  onSaved,
  onDeleted,
}: TradeReasonFormProps) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState(initial?.reason_code ?? (side === "buy" ? "plan_build" : "plan_reduce"));
  const [emotion, setEmotion] = useState(initial?.emotion_code ?? "");
  const [text, setText] = useState(initial?.reason_text ?? "");
  const [tags, setTags] = useState((initial?.tags ?? []).join("，"));
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const codes = side === "buy" ? BUY_CODES : SELL_CODES;

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const body: TradeReasonIn = {
        reason_code: code,
        emotion_code: emotion || null,
        reason_text: text.trim() || null,
        tags: tags
          .split(/[,，]/)
          .map((item) => item.trim())
          .filter(Boolean),
      };
      const result = await api.trades.putReason(tradeId, body, initial?.version);
      onSaved(result.reason);
      setOpen(false);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.trades.deleteReason(tradeId, initial?.version);
      setConfirmingDelete(false);
      setOpen(false);
      onDeleted();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="reason-summary">
        {initial ? (
          <>
            <strong>{initial.reason_label}</strong>
            {initial.emotion_label ? <span className="muted"> · 情绪 {initial.emotion_label}</span> : null}
            {initial.late_annotated ? <span className="badge badge-pending">事后标注</span> : null}
            {initial.tags?.length ? (
              <span className="chips">
                {initial.tags.map((tag) => (
                  <span key={tag} className="chip">
                    {tag}
                  </span>
                ))}
              </span>
            ) : null}
            {initial.reason_text ? <p className="muted">{initial.reason_text}</p> : null}
          </>
        ) : (
          <span className="muted">未标注理由</span>
        )}
        {' '}
        <button className="btn btn-ghost" type="button" disabled={disabled} onClick={() => setOpen(true)}>
          {initial ? "编辑理由" : "添加理由"}
        </button>
      </div>
    );
  }

  return (
    <div className="reason-form">
      {error ? <ProblemView error={error} /> : null}
      <label>
        理由码
        <select value={code} onChange={(event) => setCode(event.target.value)}>
          {codes.map((item) => (
            <option key={item} value={item}>
              {CODE_LABELS[item] ?? item}
            </option>
          ))}
        </select>
      </label>
      <label>
        情绪（可选）
        <select value={emotion} onChange={(event) => setEmotion(event.target.value)}>
          <option value="">未选择</option>
          {Object.entries(EMOTION_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        标签（逗号分隔，最多 8 个）
        <input type="text" value={tags} onChange={(event) => setTags(event.target.value)} />
      </label>
      <label>
        补充说明（最多 500 字）
        <textarea
          rows={3}
          maxLength={500}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <div className="row-actions">
        <button className="btn" type="button" disabled={busy} onClick={() => void save()}>
          {busy ? "保存中…" : "保存理由"}
        </button>
        <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setOpen(false)}>
          取消
        </button>
        {initial ? (
          confirmingDelete ? (
            <button className="btn" type="button" disabled={busy} onClick={() => void remove()}>
              确认删除
            </button>
          ) : (
            <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setConfirmingDelete(true)}>
              删除理由
            </button>
          )
        ) : null}
      </div>
    </div>
  );
}
