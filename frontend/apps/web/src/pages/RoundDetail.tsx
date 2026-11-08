import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";
import { Link, useParams } from "react-router-dom";
import type {
  ForwardReturnOut,
  Round,
  RoundFill,
  RoundFillPage,
  RoundInsightDetail,
  TradeReason,
  TradeSideInsightOut,
} from "@sa/api-client";
import { api } from "../client";
import { signedClass } from "../lib/signed";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn, type DataTableExpansion } from "../ui/DataTable";
import { TradeReasonForm } from "../ui/TradeReasonForm";
import { Icon } from "../ui/icons";
import { FlagGlossary } from "../ui/FlagGlossary";
import "./detail.css";
import { roundStatusLabel } from "./Rounds";

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
            if (cell.value !== null)
              return <span className={signedClass(cell.value)}>{cell.value}</span>;
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

/** 洞察详情里 fills 的元素（后端只保证 buy/sell 为笔次视图，生成类型未细化）。 */
interface InsightFill {
  fill_id?: number;
  sequence?: number;
  buy?: TradeSideInsightOut | null;
  sell?: TradeSideInsightOut | null;
}

/** 短 id 不做截断（按字符长度判断，避免把短 id 截坏）。 */
const SHORT_ID_MAX = 12;

/**
 * 5/10/20 子表默认展开的流水笔数上限：每笔子表 4 行（表头 + 3 条口径）且紧跟主行，
 * 10 笔以内约两屏；超过则默认折叠，避免首屏被 40+ 行附加内容淹没（可逐笔点开）。
 */
const DEFAULT_EXPAND_MAX_ROWS = 10;

function shortTradeId(tradeId: string): string {
  if (tradeId.length <= SHORT_ID_MAX) return tradeId;
  return `${tradeId.slice(0, 6)}…${tradeId.slice(-4)}`;
}

/**
 * 回合流水 = 配对明细里的 buy/sell 按 `trade_id` 去重后合并（同一笔卖出可能参与多个
 * 配对，只能出现一次），再补回合级开仓/平仓流水；按日期升序、同日按 id 升序稳定排序。
 */
function uniqueTrades(
  insight: RoundInsightDetail,
  side: "buy" | "sell",
): TradeSideInsightOut[] {
  const byId = new Map<string, TradeSideInsightOut>();
  const add = (trade: TradeSideInsightOut | null | undefined): void => {
    if (trade && !byId.has(trade.trade_id)) byId.set(trade.trade_id, trade);
  };
  add(side === "buy" ? insight.buy_trade : insight.sell_trade);
  for (const fill of (insight.fills ?? []) as unknown as InsightFill[]) {
    add(side === "buy" ? fill.buy : fill.sell);
  }
  return [...byId.values()].sort((a, b) =>
    a.date === b.date ? a.trade_id.localeCompare(b.trade_id) : a.date.localeCompare(b.date),
  );
}

/** 复制完整流水 id（展示的是省略后的，复制的是完整值）；成功/失败都给可见反馈。 */
function CopyTradeId({ tradeId }: { tradeId: string }): ReactElement {
  const [feedback, setFeedback] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy(): Promise<void> {
    if (timer.current) clearTimeout(timer.current);
    try {
      await navigator.clipboard.writeText(tradeId);
      setFeedback("copied");
    } catch {
      setFeedback("failed");
    }
    timer.current = setTimeout(() => setFeedback("idle"), 2000);
  }

  return (
    <span className="trade-id-copy">
      <button
        type="button"
        className="trade-id-copy-btn"
        aria-label={`复制流水 ${tradeId} 的完整 ID`}
        title="复制完整 ID"
        onClick={() => void copy()}
      >
        <Icon name="copy" size={14} />
      </button>
      <span className="trade-id-feedback" role="status" aria-live="polite">
        {feedback === "copied" ? "已复制" : feedback === "failed" ? "复制失败" : ""}
      </span>
    </span>
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
  // 展开态与理由的局部回填：保存/删除后只改这一笔，不整页重取，避免丢展开态。
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [reasonOverrides, setReasonOverrides] = useState<Record<string, TradeReason | null>>({});

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

  /** 行内理由：优先用本次保存/删除后的局部值，否则用洞察返回的当前理由。 */
  function reasonOf(trade: TradeSideInsightOut): TradeReason | null {
    const override = reasonOverrides[trade.trade_id];
    return override !== undefined ? override : (trade.reason ?? null);
  }

  function tradeColumns(side: "buy" | "sell"): Array<DataTableColumn<TradeSideInsightOut>> {
    return [
      {
        key: "trade_id",
        header: "流水id",
        render: (trade) => (
          <span className="trade-id">
            <code title={trade.trade_id}>{shortTradeId(trade.trade_id)}</code>
            <CopyTradeId tradeId={trade.trade_id} />
          </span>
        ),
      },
      { key: "date", header: "日期", render: (trade) => trade.date },
      {
        key: "quantity",
        header: "数量",
        align: "right",
        render: (trade) => text(trade.quantity_display),
      },
      {
        key: "price",
        header: "价格",
        align: "right",
        render: (trade) => text(trade.price_display),
      },
      {
        key: "amount",
        header: "金额",
        align: "right",
        // 金额保留后端符号（买入为负），只展示字符串，不做 abs 或符号翻转。
        render: (trade) =>
          trade.amount_display == null ? (
            "—"
          ) : (
            <span className={signedClass(trade.amount_display)}>{trade.amount_display}</span>
          ),
      },
      {
        key: "gap",
        header: "执行价差（相对当日收盘）",
        align: "right",
        render: (trade) => {
          const gap = executionGap(trade);
          return gap == null ? "—" : <span className={signedClass(gap)}>{gap}</span>;
        },
      },
      {
        key: "reason",
        header: "操作理由",
        render: (trade) => (
          <div className="trade-reason">
            <TradeReasonForm
              tradeId={trade.trade_id}
              side={side}
              initial={reasonOf(trade)}
              onSaved={(reason) =>
                setReasonOverrides((prev) => ({ ...prev, [trade.trade_id]: reason }))
              }
              onDeleted={() => setReasonOverrides((prev) => ({ ...prev, [trade.trade_id]: null }))}
            />
          </div>
        ),
      },
    ];
  }

  const buyRows: TradeSideInsightOut[] = insight ? uniqueTrades(insight, "buy") : [];
  const sellRows: TradeSideInsightOut[] = insight ? uniqueTrades(insight, "sell") : [];
  const defaultExpanded = buyRows.length + sellRows.length <= DEFAULT_EXPAND_MAX_ROWS;

  const expansion: DataTableExpansion<TradeSideInsightOut> = {
    // 5/10/20 子表默认展开；流水笔数超过阈值（11 笔以上）才默认折叠，避免首屏过长。
    isExpanded: (trade) => expanded[trade.trade_id] ?? defaultExpanded,
    onToggle: (trade) =>
      setExpanded((prev) => ({ ...prev, [trade.trade_id]: !(prev[trade.trade_id] ?? defaultExpanded) })),
    toggleLabel: (trade, isOpen) =>
      `${isOpen ? "收起" : "展开"}流水 ${trade.trade_id} 的 5/10/20 行情日`,
    panelTestId: (trade) => `trade-forward-${trade.trade_id}`,
    render: (trade) => forwardTable(trade, `流水 ${shortTradeId(trade.trade_id)} 的 5/10/20 行情日`),
  };

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
                  <td className={signedClass(round.return_rate)}>{round.return_rate}</td>
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
              <h2 className="detail-section-title">买卖流水与事后表现</h2>
              <div className="detail-card">
                <p className="muted">
                  持有交易日（按基准日历）：{text(insight.holding_trading_days?.toString())}
                  {` · 持有期：${BUCKET_LABELS[insight.bucket] ?? insight.bucket}`} · 表格按流水
                  （trade）去重：同一笔卖出参与多个配对时只出现一次。
                </p>
                <h3>买入（{buyRows.length} 笔）</h3>
                <DataTable
                  caption="买入流水与事后表现"
                  columns={tradeColumns("buy")}
                  rows={buyRows}
                  rowKey={(row) => row.trade_id}
                  expansion={expansion}
                  emptyText="该回合没有可标注的买入流水。"
                />
                <h3>卖出（{sellRows.length} 笔）</h3>
                <DataTable
                  caption="卖出流水与事后表现"
                  columns={tradeColumns("sell")}
                  rows={sellRows}
                  rowKey={(row) => row.trade_id}
                  expansion={expansion}
                  emptyText="持仓中回合尚无卖出流水；卖出后可标注卖出理由。"
                />
                <p className="muted">
                  执行价差 = 成交价与<strong>成交当日收盘价</strong>的对比（买入：收盘−成交价；卖出：成交价−收盘；
                  正 = 买得低 / 卖得高）。它是该笔成交的固有属性，取首个有值的 5 / 10 / 20 档（三档同值，
                  仅在窗口未到或降级时缺值）。
                </p>
                <p className="muted">
                  展开任一行可看该笔的 5 / 10 / 20 行情日。缺失或公司行动显示「—」并给出原因，不显示 0%；
                  公司行动（分红/送转/配股）本期不复权，价格收益一律留空，避免“假跌”。
                </p>
                <p className="muted">
                  金额保留后端符号（买入为负）；数量/价格/金额/百分比均为后端返回的定点字符串，
                  前端不做算术或格式改写。
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
                      <td className={signedClass(ranking.buy_excess_pct)}>
                        {text(ranking.buy_excess_pct)}
                      </td>
                      <th>卖出后相对基准（20 日）</th>
                      <td className={signedClass(ranking.sell_avoid_pct)}>
                        {text(ranking.sell_avoid_pct)}
                      </td>
                    </tr>
                    <tr>
                      <th>综合（买卖两侧）</th>
                      <td className={signedClass(ranking.combined_pct)}>
                        {text(ranking.combined_pct)}
                      </td>
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
              <FlagGlossary entries={insight.flag_glossary} />
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
