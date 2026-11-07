import { useCallback, useEffect, useState, type ReactElement } from "react";
import { Link, useParams } from "react-router-dom";
import type {
  ForwardReturnOut,
  Round,
  RoundFill,
  RoundFillPage,
  RoundInsightDetail,
  TradeSideInsightOut,
} from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";
import { TradeReasonForm } from "../ui/TradeReasonForm";
import "./detail.css";
import { roundStatusLabel } from "./Rounds";

function signedClass(value: string): string {
  if (value.startsWith("-")) return "money money-neg";
  if (value === "0.00" || value === "0.0000") return "money";
  return "money money-pos";
}

const BUCKET_LABELS: Record<string, string> = {
  short: "短线（≤1 日）",
  swing: "波段（2~20 日）",
  trend: "趋势（>20 日）",
  unknown: "持有期未知",
};

const QUADRANT_LABELS: Record<string, string> = {
  both_positive: "买入后与卖出后均优于基准",
  buy_positive_sell_negative: "买入后优于基准，卖出后不及持有",
  buy_negative_sell_positive: "买入后不及基准，卖出后优于继续持有",
  both_negative: "买入后与卖出后均不及基准",
};

const FLAG_LABELS: Record<string, string> = {
  addon: "加仓",
  dip_sequence: "越跌越买",
  below_avg: "低于均价加仓",
  continued_decline: "加仓后下跌",
  profit_taking: "止盈",
  profit_take_fly: "卖飞",
  stop_loss_rebound: "止损后反弹",
  rising_sell_sequence: "越涨越卖",
  full_exit: "清仓",
  partial_exit: "部分卖出",
  same_day: "当日回转",
};

interface RankingDetail {
  rank?: number | null;
  buy_excess_pct?: string | null;
  sell_avoid_pct?: string | null;
  combined_pct?: string | null;
  quadrant?: string | null;
  basis?: string;
  definition_version?: string;
}

const STATUS_LABELS: Record<string, string> = {
  ok: "数据完整",
  pending: "还差交易日（未到窗口）",
  no_calendar: "缺交易日历",
  no_bar: "缺行情",
  stale_price: "最近收盘已过 5 个交易日",
  corporate_action: "窗口内含公司行动（未复权）",
  divisor_missing: "缺基准序列（不排名）",
};

/**
 * 术语映射（设计 §7.1）：页面不露 B0/B1/B2/B3 裸码。
 *
 * 这里只保留随 horizon 变化的 B1/B2/B3；B0 执行价差只依赖「成交价 + 成交当日收盘」，
 * 与持有期无关，放到笔次固有属性（见 `executionGap`），不在 5/10/20 列里重复。
 */
const FORWARD_ROWS = [
  { key: "b1_pct", label: "事件后走势（收盘到收盘）" },
  { key: "b2_pct", label: "成交后收益（按含费净价）" },
  { key: "b3_pct", label: "相对基准超额（20 日排序口径）" },
] as const;

const HORIZONS = ["5", "10", "20"] as const;

function text(value: string | null | undefined): string {
  return value ?? "—";
}

function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag;
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/**
 * 空值原因文案（设计 §7.2）：把后端 status 内联到单元格，避免只显示裸 `—` 让用户
 * 误以为数据缺失。只做文案映射，不含任何数值计算；pending 的交易日数取自该档 horizon。
 */
const EMPTY_REASONS: Record<string, string> = {
  corporate_action: "窗口含分红等公司行动",
  no_bar: "行情缺失",
  no_calendar: "基准数据缺失",
  stale_price: "行情停更（超 5 个交易日）",
  divisor_missing: "基准数据不足",
};

function emptyCellReason(status: string, horizon: string): string {
  if (status === "pending") return `待满 ${horizon} 个交易日`;
  return EMPTY_REASONS[status] ?? "";
}

interface ForwardCell {
  value: string | null;
  status: string;
}

function forwardCell(
  side: TradeSideInsightOut | null | undefined,
  horizon: string,
  key: string,
): ForwardCell {
  const forward: ForwardReturnOut | undefined = side?.forward?.[horizon];
  if (!forward) return { value: null, status: "no_bar" };
  return { value: forward[key as keyof ForwardReturnOut] as string | null, status: forward.status };
}

/**
 * 执行价差（B0 毛）是笔次固有属性：只由「成交价 + 成交当日收盘」决定，三个 horizon
 * 下是同一个数；仅在窗口未到/停牌等降级时才会缺值，故取首个有值的 horizon，只渲染
 * 后端字符串，前端不做任何计算或取整。
 */
function executionGap(side: TradeSideInsightOut | null | undefined): string | null {
  for (const horizon of HORIZONS) {
    const value = side?.forward?.[horizon]?.b0_pct;
    if (value != null) return value;
  }
  return null;
}

function forwardTable(side: TradeSideInsightOut | null | undefined, caption: string): ReactElement {
  return (
    <DataTable
      caption={caption}
      columns={[
        { key: "metric", header: "口径", render: (row: { label: string }) => row.label },
        ...HORIZONS.map((horizon) => ({
          key: horizon,
          header: `${horizon} 日`,
          align: "right" as const,
          render: (row: { cells: Record<string, ForwardCell> }) => {
            const cell = row.cells[horizon];
            if (cell.value !== null) return cell.value;
            // 缺失一律 `—（原因）`（不显示 0%），原因来自后端 status；tooltip 保留完整说明。
            const reason = emptyCellReason(cell.status, horizon);
            return <span title={statusLabel(cell.status)}>{reason ? `—（${reason}）` : "—"}</span>;
          },
        })),
      ]}
      rows={FORWARD_ROWS.map((row) => ({
        label: row.label,
        cells: Object.fromEntries(
          HORIZONS.map((horizon) => [horizon, forwardCell(side, horizon, row.key)]),
        ) as Record<string, ForwardCell>,
      }))}
      rowKey={(row) => row.label}
      emptyText="暂无前瞻数据"
    />
  );
}

/** 回合详情（/rounds/:roundId）：回合级口径 + FIFO 配对明细 + 回合洞察（API-093）。 */
export function RoundDetail() {
  const { roundId } = useParams<{ roundId: string }>();
  const [round, setRound] = useState<Round | null>(null);
  const [fills, setFills] = useState<RoundFillPage | null>(null);
  const [insight, setInsight] = useState<RoundInsightDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!roundId) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, fillPage, insightDetail] = await Promise.all([
        api.rounds.get(roundId),
        api.rounds.fills(roundId),
        // 洞察是增强块：失败时详情/配对仍可用，不让整页报错。
        api.rounds.insight(roundId).catch(() => null),
      ]);
      setRound(detail);
      setFills(fillPage);
      setInsight(insightDetail);
    } catch (err) {
      setError(err);
      setRound(null);
      setFills(null);
      setInsight(null);
    } finally {
      setLoading(false);
    }
  }, [roundId]);

  useEffect(() => {
    void load();
  }, [load]);

  const ranking = (insight?.ranking ?? {}) as RankingDetail;

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

          {insight ? (
            <>
              <h2 className="detail-section-title">操作理由</h2>
              <div className="detail-card">
                <h3>买入侧</h3>
                {insight.buy_trade ? (
                  <>
                    <p className="muted">
                      流水 <code>{insight.buy_trade.trade_id}</code> · {insight.buy_trade.date} · 净价{" "}
                      {text(insight.buy_trade.price_display)} · 金额 {text(insight.buy_trade.amount_display)}
                    </p>
                    <TradeReasonForm
                      tradeId={insight.buy_trade.trade_id}
                      side="buy"
                      initial={insight.buy_trade.reason ?? null}
                      onSaved={() => void load()}
                      onDeleted={() => void load()}
                    />
                  </>
                ) : (
                  <p className="muted">该回合没有可标注的买入流水。</p>
                )}
                <h3>卖出侧</h3>
                {insight.sell_trade ? (
                  <>
                    <p className="muted">
                      流水 <code>{insight.sell_trade.trade_id}</code> · {insight.sell_trade.date} · 净价{" "}
                      {text(insight.sell_trade.price_display)} · 金额 {text(insight.sell_trade.amount_display)}
                    </p>
                    <TradeReasonForm
                      tradeId={insight.sell_trade.trade_id}
                      side="sell"
                      initial={insight.sell_trade.reason ?? null}
                      onSaved={() => void load()}
                      onDeleted={() => void load()}
                    />
                  </>
                ) : (
                  <p className="muted">持仓中回合尚无卖出流水；卖出后可标注卖出理由。</p>
                )}
              </div>

              <h2 className="detail-section-title">执行价差（相对当日收盘）</h2>
              <div className="detail-card">
                <table className="detail-stats">
                  <tbody>
                    {insight.buy_trade ? (
                      <tr>
                        <th>买入侧（毛）</th>
                        <td>{text(executionGap(insight.buy_trade))}</td>
                        <th>成交日</th>
                        <td>{insight.buy_trade.date}</td>
                      </tr>
                    ) : null}
                    {insight.sell_trade ? (
                      <tr>
                        <th>卖出侧（毛）</th>
                        <td>{text(executionGap(insight.sell_trade))}</td>
                        <th>成交日</th>
                        <td>{insight.sell_trade.date}</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
                <p className="muted">
                  执行价差 = 成交价与<strong>成交当日收盘价</strong>的对比（买入：收盘−成交价；卖出：成交价−收盘；
                  正 = 买得低 / 卖得高）。
                </p>
              </div>

              <h2 className="detail-section-title">5 / 10 / 20 行情日</h2>
              <div className="detail-card">
                <p className="muted">
                  持有交易日（按基准日历）：{text(insight.holding_trading_days?.toString())}
                  {` · 持有期：${BUCKET_LABELS[insight.bucket] ?? insight.bucket}`}
                </p>
                <h3>买入后</h3>
                {forwardTable(insight.buy_trade, "买入侧前瞻收益")}
                <h3>卖出后</h3>
                {insight.sell_trade ? (
                  forwardTable(insight.sell_trade, "卖出侧前瞻收益")
                ) : (
                  <p className="muted">尚未卖出，卖出后才有卖后 5/10/20 数据。</p>
                )}
                <p className="muted">
                  缺失或公司行动显示「—」并给出原因，不显示 0%；公司行动（分红/送转/配股）本期不复权，
                  价格收益一律留空，避免“假跌”。
                </p>
              </div>

              <h2 className="detail-section-title">标签 / 排序</h2>
              <div className="detail-card">
                <p>
                  旗标：
                  {insight.flags?.length ? (
                    <span className="chips">
                      {insight.flags.map((flag) => (
                        <span key={flag} className="chip">
                          {flagLabel(flag)}
                        </span>
                      ))}
                    </span>
                  ) : (
                    " —"
                  )}
                </p>
                <table className="detail-stats">
                  <tbody>
                    <tr>
                      <th>买入后相对基准（20 日）</th>
                      <td>{text(ranking.buy_excess_pct)}</td>
                      <th>卖出后相对基准（20 日）</th>
                      <td>{text(ranking.sell_avoid_pct)}</td>
                    </tr>
                    <tr>
                      <th>综合（买卖两侧）</th>
                      <td>{text(ranking.combined_pct)}</td>
                      <th>排序名次</th>
                      <td>{ranking.rank == null ? "未参与排名" : String(ranking.rank)}</td>
                    </tr>
                    <tr>
                      <th>象限</th>
                      <td colSpan={3}>
                        {ranking.quadrant ? QUADRANT_LABELS[ranking.quadrant] ?? ranking.quadrant : "—"}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <p className="muted">
                  口径 {text(ranking.basis)} · 定义版本 {text(ranking.definition_version)}；
                  原始百分点为相对基准的事后表现，不是能力分或收益预测。
                </p>
                {insight.quality?.length ? (
                  <p className="muted">数据质量：{insight.quality.join("、")}</p>
                ) : null}
              </div>

              <p className="detail-note">{insight.disclaimer}</p>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
