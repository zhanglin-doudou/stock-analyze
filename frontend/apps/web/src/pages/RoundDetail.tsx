import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Round, RoundFill, RoundFillPage } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";
import "./detail.css";
import { roundStatusLabel } from "./Rounds";

function signedClass(value: string): string {
  if (value.startsWith("-")) return "money money-neg";
  if (value === "0.00" || value === "0.0000") return "money";
  return "money money-pos";
}

/** 回合详情（/rounds/:roundId）：回合级口径 + FIFO 配对明细（可追溯到买卖流水）。 */
export function RoundDetail() {
  const { roundId } = useParams<{ roundId: string }>();
  const [round, setRound] = useState<Round | null>(null);
  const [fills, setFills] = useState<RoundFillPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!roundId) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, fillPage] = await Promise.all([api.rounds.get(roundId), api.rounds.fills(roundId)]);
      setRound(detail);
      setFills(fillPage);
    } catch (err) {
      setError(err);
      setRound(null);
      setFills(null);
    } finally {
      setLoading(false);
    }
  }, [roundId]);

  useEffect(() => {
    void load();
  }, [load]);

  const columns: Array<DataTableColumn<RoundFill>> = [
    { key: "seq", header: "#", render: (row) => row.sequence },
    { key: "buy", header: "买入流水", render: (row) => <code title={row.buy_trade_id}>{row.buy_trade_id}</code> },
    { key: "sell", header: "卖出流水", render: (row) => <code title={row.sell_trade_id}>{row.sell_trade_id}</code> },
    { key: "qty", header: "数量", render: (row) => row.quantity },
    { key: "open", header: "买入日", render: (row) => row.open_date },
    { key: "close", header: "卖出日", render: (row) => row.close_date },
    { key: "days", header: "持有交易日", render: (row) => row.holding_days },
  ];

  return (
    <div className="detail-page">
      <header className="detail-head">
        <Link className="detail-back" to="/rounds">
          ← 返回回合列表
        </Link>
        <div className="detail-eyebrow">交易 · 回合详情</div>
        {round ? (
          <h1 className="detail-title">
            {round.security_code} {round.security_name}
          </h1>
        ) : (
          <h1 className="detail-title">回合详情</h1>
        )}
        {round ? (
          <p className="detail-sub">
            回合 {round.round_id} · 开仓 {round.open_date}
            {round.close_date ? ` · 平仓 ${round.close_date}` : " · 尚未平仓"}
            {` · 持有 ${round.holding_days} 个交易日 · 配对 ${round.fills_count} 笔`}
          </p>
        ) : null}
      </header>

      {loading ? <p className="muted">加载中…</p> : null}
      <ProblemView error={error} onRetry={() => void load()} />

      {round ? (
        <>
          <p className="detail-status-line">
            状态 <span className={`badge badge-${round.status}`}>{roundStatusLabel(round.status)}</span>
            <span>数量 {round.quantity}</span>
          </p>

          {round.data_error ? (
            <div className="notice-warn" role="alert">
              该回合涉及超卖等数据异常：超出可用持仓
              {round.oversell_quantity} 未配对。请核对账单是否存在缺失买入记录。
            </div>
          ) : null}

          <div className="detail-card">
            <h2>回合口径</h2>
            <table className="detail-stats">
              <tbody>
                <tr>
                  <th>数量</th>
                  <td>{round.quantity}</td>
                  <th>买入成本（含费）</th>
                  <td>{round.cost}</td>
                </tr>
                <tr>
                  <th>卖出金额</th>
                  <td>{round.proceeds}</td>
                  <th>股息净额</th>
                  <td>{round.dividend}</td>
                </tr>
                <tr>
                  <th>回合盈亏</th>
                  <td className={signedClass(round.pnl)}>{round.pnl}</td>
                  <th>收益率（小数）</th>
                  <td>{round.return_rate}</td>
                </tr>
                <tr>
                  <th>开仓流水</th>
                  <td>
                    <code>{round.open_trade_id}</code>
                  </td>
                  <th>平仓流水</th>
                  <td>{round.close_trade_id ? <code>{round.close_trade_id}</code> : "—"}</td>
                </tr>
              </tbody>
            </table>
            <p className="muted">
              口径：回合盈亏 = Σ卖出发生金额 − Σ对应买入发生金额绝对值 + 该股数对应股息净额（税后）；
              收益率 = 盈亏 / 买入成本。金额/数量/比率均为后端返回的定点字符串，前端不做算术。
            </p>
          </div>

          <h2 className="detail-section-title">FIFO 配对明细</h2>
          <DataTable
            caption="回合配对明细"
            columns={columns}
            rows={fills?.items ?? []}
            rowKey={(row) => String(row.fill_id)}
            emptyText={
              round.status === "open" ? "持仓中回合尚无卖出配对明细（卖出后才产生 fills）。" : "没有配对明细。"
            }
          />
          {fills ? (
            <p className="detail-note">共 {fills.total} 笔配对；买卖流水 ID 可在「交易流水」核对。</p>
          ) : null}

          <p className="detail-note">
            数据重建时间：{round.rebuilt_at} · 去 <Link to="/trades">交易流水</Link> 核对原始行。
          </p>
        </>
      ) : null}
    </div>
  );
}
