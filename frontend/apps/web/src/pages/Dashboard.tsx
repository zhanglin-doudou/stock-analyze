import { type CSSProperties, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type {
  DashboardOut,
  DashboardRoundOut,
  DashboardTradeCountOut,
  MetricBatch,
  MetricItem,
} from "@sa/api-client";
import { api } from "../client";
import { TrendChart } from "../charts/TrendChart";
import { ProblemView } from "../ui/Problem";
import { displayValue, signTone } from "../ui/format";
import {
  buildStateLabel,
  dataStatusLabel,
  needsRebuild,
  SAMPLE_STATUS_LABELS,
  type DataStatus,
} from "../ui/status";
import "./dashboard.css";

const UNIT_LABELS: Record<string, string> = {
  CNY: "元",
  ratio: "",
  days: "天",
  count: "次",
  z: "z",
};

function shown(display: string | null | undefined, raw: string | null | undefined): string | null | undefined {
  return display ?? raw;
}

function tone(value: string | null | undefined): string {
  const sign = signTone(value);
  return sign === "none" || sign === "flat" ? "num" : `num is-${sign}`;
}

function Value({ value, testId }: { value: string | null | undefined; testId?: string }): ReactElement {
  return (
    <strong className={tone(value)} data-testid={testId}>
      {displayValue(value)}
    </strong>
  );
}

function StatusBadge({ status, kind }: { status: string; kind: "build" | "data" | "sample" }): ReactElement {
  const label =
    kind === "build"
      ? buildStateLabel(status)
      : kind === "sample"
        ? (SAMPLE_STATUS_LABELS[status as keyof typeof SAMPLE_STATUS_LABELS] ?? status)
        : dataStatusLabel(status);
  return (
    <span className={`dash-badge is-${status}`}>{label}</span>
  );
}

function Kpi({
  label,
  value,
  testId,
  hint,
}: {
  label: string;
  value: string | null | undefined;
  testId: string;
  hint?: ReactNode;
}): ReactElement {
  return (
    <div className="dash-kpi" data-testid={testId}>
      <span className="dash-kpi-label">{label}</span>
      <Value value={value} />
      {hint ? <span className="dash-kpi-hint">{hint}</span> : null}
    </div>
  );
}

function RoundRow({ round, testPrefix }: { round: DashboardRoundOut; testPrefix: string }): ReactElement {
  return (
    <li data-testid={`${testPrefix}-${round.round_id}`} className="dash-round">
      <span className="dash-round-name">
        <Link to={`/rounds/${encodeURIComponent(round.round_id)}`}>
          {round.security_name}（{round.security_code}）
        </Link>
      </span>
      <span className={tone(round.pnl_display ?? round.pnl)}>{round.pnl_display ?? round.pnl}</span>
      <span className="dash-round-meta">
        {round.return_rate_pct} · {round.close_date} · 持有 {round.holding_days} 天
      </span>
    </li>
  );
}

function TradeCountRow({ item }: { item: DashboardTradeCountOut }): ReactElement {
  return (
    <li data-testid={`top-trade-${item.security_code}`} className="dash-round">
      <span className="dash-round-name">
        {item.security_name}（{item.security_code}）
      </span>
      <strong className="num">{item.trade_count} 笔</strong>
      <span className="dash-round-meta">
        买 {item.buy_count} · 卖 {item.sell_count} · 累计收益{" "}
        <span className={tone(item.pnl_display ?? item.pnl)}>
          {displayValue(shown(item.pnl_display, item.pnl))}
        </span>{" "}
        · 总费用 {displayValue(shown(item.total_fees_display, item.total_fees))}
      </span>
    </li>
  );
}

/** 年度指标（后端 metrics_period：今年年初 → 最新收盘日；低频操作者月度样本不足）。 */
function AnnualMetrics({
  period,
  start,
  end,
  batch,
  loading,
  error,
}: {
  period: string;
  start: string;
  end: string;
  batch: MetricBatch | null;
  loading: boolean;
  error: unknown;
}): ReactElement {
  const valueCell = (row: MetricItem): ReactNode => {
    if (row.value == null) return <span className="dash-muted">—</span>;
    const unit = UNIT_LABELS[row.unit] ?? row.unit;
    return (
      <strong className={tone(row.value)}>
        {row.value}
        {unit ? <span className="dash-muted"> {unit}</span> : null}
      </strong>
    );
  };
  return (
    <section className="dash-panel" data-testid="annual-metrics">
      <div className="dash-panel-head">
        <h2>年度指标（{period}）</h2>
        <span className="dash-panel-meta">
          {start} → {end} · 后端计算
        </span>
        <div className="dash-grow" />
        <Link
          className="dash-link"
          to={`/metrics?period=${period}`}
          data-testid="link-annual-metrics"
        >
          指标详情
        </Link>
      </div>
      <p className="dash-muted">
        低频操作者月度样本常不足，看板改用年度口径（今年年初 → 最新收盘日）；数值由后端计算，
        本页只展示（含 n / 可信度）。
      </p>
      {error != null ? <ProblemView error={error} /> : null}
      {loading && !batch ? <p className="dash-muted">加载中…</p> : null}
      {batch ? (
        <table className="dash-table dash-legacy-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>值</th>
              <th>样本</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {batch.items.map((row) => (
              <tr key={row.metric_id}>
                <td>
                  <Link to={`/metrics/${encodeURIComponent(row.metric_id)}?period=${period}`}>
                    {row.presentation?.display_name ?? row.metric_id}
                  </Link>
                </td>
                <td>{valueCell(row)}</td>
                <td>
                  {row.n}
                  {SAMPLE_STATUS_LABELS[row.sample_status as keyof typeof SAMPLE_STATUS_LABELS]
                    ? `（${SAMPLE_STATUS_LABELS[row.sample_status as keyof typeof SAMPLE_STATUS_LABELS]}）`
                    : null}
                </td>
                <td>
                  {row.presentation ? (
                    <span
                      className={`badge badge-${row.presentation.confidence.level}`}
                      title={row.presentation.verdict.rationale}
                    >
                      {row.presentation.verdict.label}
                    </span>
                  ) : (
                    <StatusBadge status={row.status} kind="data" />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

/**
 * Dashboard（Phase 1c）：资产概览 + 历史概览 + 总资产日线。
 * 数据源：/dashboard（聚合口径）+ /assets（每日快照，仅用于趋势图几何）。
 * 所有金额/比率原样展示后端字符串；本页不做任何数值计算（图表几何在 charts/geometry）。
 */
export function Dashboard(): ReactElement {
  const dashboardQuery = useQuery({
    queryKey: ["dashboard", "all"],
    queryFn: () => api.dashboard.get(),
  });
  const chartQuery = useQuery({
    queryKey: ["assets", "chart"],
    queryFn: () => api.assets.chart(200),
    staleTime: 60_000,
  });

  const data: DashboardOut | undefined = dashboardQuery.data;
  const metricsQuery = useQuery({
    queryKey: ["metrics", "annual", data?.metrics_period],
    queryFn: () => api.metrics.batch({ period: data?.metrics_period ?? "" }),
    enabled: data != null,
    staleTime: 60_000,
  });

  if (dashboardQuery.error != null) {
    return (
      <div>
        <h1>看板</h1>
        <ProblemView error={dashboardQuery.error} />
      </div>
    );
  }

  if (dashboardQuery.isLoading || !data) {
    return (
      <div>
        <h1>看板</h1>
        <p className="dash-muted">加载中…</p>
      </div>
    );
  }

  const asset = data.asset_overview;
  const history = data.history_overview;
  const drawdown = history.max_drawdown;
  const drawdownValue = drawdown.mdd_pct ?? (drawdown.sample_status === "blocked" ? "阻断" : "—");

  // API-085：后端已归一化 v∈[0,1] + 展示标签；前端只做几何投影（零口径）。
  const chart = chartQuery.data;
  const chartSeries = (chart?.points ?? []).map((point) => ({
    label: point.label,
    value: point.v,
    display: point.amount_label,
  }));

  return (
    <div className="dash">
      <header className="dash-head">
        <div className="dash-eyebrow">
          {data.as_of} · 全历史 · 数据截至 {data.data_as_of ?? "—"}
        </div>
        <h1 className="dash-title">看板</h1>
        <p className="dash-sub">先看清自己，再看清市场。所有数值均为后端口径，本页只做呈现与下钻。</p>
        <p className="dash-muted" data-testid="market-data-notice">
          行情来自本地缓存（仅收盘后同步，盘中不拉取实时行情）；数据截至 {data.data_as_of ?? "—"}。
        </p>
        <div className="dash-head-meta" data-testid="dashboard-meta">
          <span>
            构建状态 <StatusBadge status={data.build_state} kind="build" />
          </span>
          <span>
            页面状态 <StatusBadge status={data.status} kind="data" />
          </span>
          <span>数据截至 {data.data_as_of ?? "—"}</span>
          <span>口径 {data.definition_version}</span>
          <button
            className="dash-btn dash-btn-ghost"
            type="button"
            disabled={dashboardQuery.isFetching}
            onClick={() => void dashboardQuery.refetch()}
          >
            {dashboardQuery.isFetching ? "刷新中…" : "刷新"}
          </button>
        </div>
      </header>

      {needsRebuild(data.build_state) ? (
        <p className="dash-notice">
          当前资产快照不是最新：请到 <Link to={data.links.assets}>资产页</Link> 构建/更新资产表。
        </p>
      ) : null}

      {data.missing.length > 0 ? (
        <section className="dash-panel" data-testid="missing-list">
          <div className="dash-panel-head">
            <h2>缺失与降级项</h2>
          </div>
          <ul className="dash-missing">
            {data.missing.map((item) => (
              <li key={`${item.field}:${item.code}`} data-testid="missing-item" data-code={item.code}>
                <code>{item.code}</code> <span className="dash-muted">{item.field}</span>
                <div>{item.reason}</div>
                <div className="dash-muted">建议：{item.what_to_do}</div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* 资产概览 */}
      <section className="dash-hero" data-testid="asset-overview">
        <div className="dash-hero-main">
          <div className="dash-eyebrow">总资产 · 含资产余额与市值</div>
          <div className="dash-hero-value">
            <span className="dash-cny">¥</span>
            <span data-testid="kpi-total-assets">
              {displayValue(shown(asset.total_assets_display, asset.total_assets))}
            </span>
          </div>
          <div className="dash-hero-delta">
            <span className={tone(asset.total_profit)}>
              总收益 {displayValue(shown(asset.total_profit_display, asset.total_profit))}
            </span>
            <span className={tone(asset.max_cost_return_pct)}>
              峰值成本收益率 {displayValue(asset.max_cost_return_pct)}
            </span>
          </div>
          <div className="dash-hero-bill" data-testid="kpi-bill-range">
            <span>
              账单 {asset.bill_start_date ?? "—"} → {asset.bill_end_date ?? "—"}
            </span>
            <span data-testid="kpi-initial-capital">
              初始资金{" "}
              {displayValue(shown(asset.initial_capital_display, asset.initial_capital))}
            </span>
          </div>
          <p className="dash-hero-note">
            银证转账峰值转入成本{" "}
            {displayValue(shown(asset.max_invested_cost_display, asset.max_invested_cost))} · 总费用{" "}
            {displayValue(shown(asset.total_fees_display, asset.total_fees))}
          </p>
        </div>
        <div className="dash-mini">
          <Kpi
            label="持仓市值"
            value={shown(asset.market_value_display, asset.market_value)}
            testId="kpi-market-value"
          />
          <Kpi label="资产余额" value={shown(asset.balance_display, asset.balance)} testId="kpi-balance" />
          <div className="dash-kpi" data-testid="kpi-position">
            <span className="dash-kpi-label">仓位</span>
            <strong className="num">{displayValue(asset.position_pct)}</strong>
            <span className="dash-weight-track" aria-hidden="true">
              <i style={{ width: asset.position_pct ?? "0%" }} />
            </span>
          </div>
          <Kpi
            label="银证转账峰值转入成本"
            value={shown(asset.max_invested_cost_display, asset.max_invested_cost)}
            testId="kpi-max-invested-cost"
          />
          <Kpi
            label="总收益（全历史）"
            value={shown(asset.total_profit_display, asset.total_profit)}
            testId="kpi-total-profit"
          />
          <Kpi
            label="峰值成本收益率"
            value={asset.max_cost_return_pct}
            testId="kpi-max-cost-return"
          />
          <Kpi
            label="总费用消耗"
            value={shown(asset.total_fees_display, asset.total_fees)}
            testId="kpi-total-fees"
          />
        </div>
      </section>

      {/* 总资产趋势 */}
      <section className="dash-panel dash-chart-panel">
        <div className="dash-panel-head">
          <h2>总资产曲线</h2>
          <span className="dash-panel-meta">
            每日快照 · {chartSeries.length} 个交易日
          </span>
          <div className="dash-grow" />
          <Link className="dash-link" to={data.links.assets}>
            资产明细
          </Link>
        </div>
        {chartQuery.isLoading ? (
          <p className="dash-chart-empty">加载日线…</p>
        ) : chartQuery.error != null ? (
          <p className="dash-chart-empty">日线加载失败，资产概览不受影响。</p>
        ) : chartSeries.length === 0 ? (
          <p className="dash-chart-empty">暂无日线数据（先到资产页构建资产表）。</p>
        ) : (
          <TrendChart series={chartSeries} title="总资产曲线" />
        )}
        <div className="dash-chart-foot">
          <span>最新 {displayValue(shown(asset.total_assets_display, asset.total_assets))}</span>
          <span>回撤 {drawdownValue}</span>
          <span>有效观测 {drawdown.n_obs} 天</span>
        </div>
      </section>

      {/* 历史概览 + 持仓 */}
      <div className="dash-grid">
        <section className="dash-panel" data-testid="history-overview">
          <div className="dash-panel-head">
            <h2>历史概览</h2>
            <span className="dash-panel-meta">全历史 · 已平仓口径</span>
            <div className="dash-grow" />
            <Link className="dash-link" to={data.links.rounds}>
              回合
            </Link>
          </div>

          <div className="dash-win-stack" aria-hidden="true">
            <i className="is-win" style={{ width: history.win_pct ?? "0%" }} />
            <i className="is-loss" style={{ width: history.loss_pct ?? "0%" }} />
            <i className="is-flat" style={{ width: history.flat_pct ?? "0%" }} />
          </div>
          <div className="dash-win-legend">
            <span>盈利 <b>{history.win_count}</b> · {displayValue(history.win_pct)}</span>
            <span>亏损 <b>{history.loss_count}</b> · {displayValue(history.loss_pct)}</span>
            <span>持平 <b>{history.flat_count}</b></span>
          </div>

          <div className="dash-kpi-grid">
            <Kpi
              label="已平仓股票数"
              value={String(history.closed_security_count)}
              testId="kpi-closed-securities"
              hint={`平仓 ${history.closed_round_count} 笔`}
            />
            <Kpi label="盈利笔数" value={String(history.win_count)} testId="kpi-win-count" />
            <Kpi label="亏损笔数" value={String(history.loss_count)} testId="kpi-loss-count" />
            <Kpi label="盈利占比" value={history.win_pct} testId="kpi-win-pct" />
            <Kpi label="亏损占比" value={history.loss_pct} testId="kpi-loss-pct" />
            <Kpi
              label="历史最大单笔收益"
              value={shown(history.max_profit_display, history.max_profit)}
              testId="kpi-max-profit"
            />
            <Kpi
              label="最大收益 / 峰值成本"
              value={history.max_profit_cost_pct}
              testId="kpi-max-profit-cost"
            />
          </div>

          <div className="dash-drawdown" data-testid="kpi-max-drawdown">
            <div>
              <span className="dash-kpi-label">最大回撤</span>
              <strong className={drawdownValue.startsWith("-") ? "num is-neg" : "num"}>
                {drawdownValue}
              </strong>
            </div>
            <div className="dash-drawdown-meta">
              <span>
                {drawdown.from_date ?? "—"} → {drawdown.to_date ?? "—"}
              </span>
              <span>持续 {drawdown.duration_days ?? "—"} 天</span>
              <span>
                恢复{" "}
                {drawdown.recovery_days == null
                  ? drawdown.recovery_censored
                    ? "尚未恢复"
                    : "—"
                  : `${drawdown.recovery_days} 天`}
              </span>
              <span>
                <StatusBadge status={drawdown.sample_status} kind="sample" /> 有效观测{" "}
                {drawdown.n_obs} 天
              </span>
            </div>
          </div>
        </section>

        <section className="dash-panel">
          <div className="dash-panel-head">
            <h2>当前持仓</h2>
            <span className="dash-panel-meta">
              {asset.holdings.length} 只 · 市值 {displayValue(asset.market_value)}
            </span>
            <div className="dash-grow" />
            <Link className="dash-link" to={data.links.assets}>
              资产明细
            </Link>
          </div>
          {asset.holdings.length === 0 ? (
            <p className="dash-muted">没有持仓（或资产表未构建）。</p>
          ) : (
            <table className="dash-table dash-holdings">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>数量</th>
                  <th>市值</th>
                  <th>权重</th>
                </tr>
              </thead>
              <tbody>
                {asset.holdings.map((row) => (
                  <tr key={row.security_code} data-testid={`holding-row-${row.security_code}`}>
                    <td>
                      <span className="dash-holding-name">
                        {row.security_name}
                        <span className="dash-holding-code">{row.security_code}</span>
                      </span>
                    </td>
                    <td className="num">{row.quantity}</td>
                    <td className="num">{displayValue(shown(row.market_value_display, row.market_value))}</td>
                    <td>
                      <span className="dash-weight-cell">
                        <span className="num">{displayValue(row.weight_display ?? row.weight_pct)}</span>
                        <span className="dash-weight-track" aria-hidden="true">
                          <i style={{ "--w": row.weight_pct ?? "0" } as CSSProperties} />
                        </span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="dash-links">
            <Link to={data.links.assets}>资产明细</Link>
            <Link to={data.links.exports} data-testid="link-exports">
              导出（占位）
            </Link>
            <Link to={data.links.reports} data-testid="link-reports">
              月报（占位）
            </Link>
            <Link to={data.links.attribution} data-testid="link-attribution">
              归因（占位）
            </Link>
          </div>
        </section>
      </div>

      {/* 最赚 / 最亏 */}
      <div className="dash-grid">
        <section className="dash-panel">
          <div className="dash-panel-head">
            <h2>最赚 3 笔</h2>
            <div className="dash-grow" />
            <span className="dash-panel-meta">已平仓</span>
          </div>
          <ul className="dash-rounds">
            {history.top_rounds.map((round) => (
              <RoundRow key={round.round_id} round={round} testPrefix="top-round" />
            ))}
          </ul>
        </section>
        <section className="dash-panel">
          <div className="dash-panel-head">
            <h2>最亏 3 笔</h2>
            <div className="dash-grow" />
            <span className="dash-panel-meta">已平仓</span>
          </div>
          <ul className="dash-rounds">
            {history.bottom_rounds.map((round) => (
              <RoundRow key={round.round_id} round={round} testPrefix="bottom-round" />
            ))}
          </ul>
        </section>
      </div>

      {/* 持仓时长 Top3 / 交易次数 Top3（两列布局） */}
      <div className="dash-grid">
        <section className="dash-panel" data-testid="top-holding-duration">
          <div className="dash-panel-head">
            <h2>持仓时长 Top3</h2>
            <div className="dash-grow" />
            <span className="dash-panel-meta">已平仓</span>
          </div>
          {history.top_holding_rounds.length === 0 ? (
            <p className="dash-muted">暂无已平仓回合。</p>
          ) : (
            <ul className="dash-rounds">
              {history.top_holding_rounds.map((round) => (
                <RoundRow key={round.round_id} round={round} testPrefix="top-holding" />
              ))}
            </ul>
          )}
        </section>
        <section className="dash-panel" data-testid="top-trade-count">
          <div className="dash-panel-head">
            <h2>交易次数 Top3</h2>
            <div className="dash-grow" />
            <span className="dash-panel-meta">按证券代码聚合</span>
          </div>
          {history.top_trade_counts.length === 0 ? (
            <p className="dash-muted">暂无交易流水。</p>
          ) : (
            <ul className="dash-rounds">
              {history.top_trade_counts.map((item) => (
                <TradeCountRow key={item.security_code} item={item} />
              ))}
            </ul>
          )}
        </section>
      </div>

      <AnnualMetrics
        period={data.metrics_period}
        start={data.metrics_period_start}
        end={data.metrics_period_end}
        batch={metricsQuery.data ?? null}
        loading={metricsQuery.isLoading}
        error={metricsQuery.error}
      />

      <footer className="dash-foot">
        <span>数据截至 {data.data_as_of ?? "—"}</span>
        <span>构建状态 {buildStateLabel(data.build_state)}</span>
        <span>页面状态 {dataStatusLabel(data.status as DataStatus)}</span>
        <span className="dash-grow" />
        <span>金额与比率均由后端格式化</span>
      </footer>
    </div>
  );
}
