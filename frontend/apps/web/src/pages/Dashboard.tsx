import { useCallback, useEffect, useState, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  type DashboardOut,
  type DashboardRoundOut,
  type MetricBatch,
  type MetricItem,
} from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";

const STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  degraded: "降级",
  stale: "待更新",
  blocked: "阻断",
  missing: "缺失",
  insufficient: "样本不足",
};

const BUILD_STATE_LABELS: Record<string, string> = {
  missing: "未构建",
  current: "最新",
  stale: "待更新",
  blocked: "阻断",
};

const SAMPLE_STATUS_LABELS: Record<string, string> = {
  ok: "可信",
  insufficient: "样本不足",
  unreliable: "不可信",
  blocked: "阻断",
};

const UNIT_LABELS: Record<string, string> = {
  CNY: "元",
  ratio: "",
  days: "天",
  count: "次",
  z: "z",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** 指标值：非 ok 后端省略 value，统一显示「—」；不做任何数值换算（前端零口径）。 */
function metricValue(row: MetricItem): ReactNode {
  if (row.value == null) return <span className="muted">—</span>;
  const unit = UNIT_LABELS[row.unit] ?? row.unit;
  return (
    <strong>
      {row.value}
      {unit ? <span className="muted"> {unit}</span> : null}
    </strong>
  );
}

function Value({ value }: { value: string | null | undefined }): ReactElement {
  if (value == null) return <span className="muted">—</span>;
  return <strong className="money">{value}</strong>;
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
  hint?: string;
}): ReactElement {
  return (
    <div className="kpi" data-testid={testId}>
      <span className="kpi-label">{label}</span>
      <Value value={value} />
      {hint ? <span className="muted kpi-hint">{hint}</span> : null}
    </div>
  );
}

function RoundRow({
  round,
  testPrefix,
}: {
  round: DashboardRoundOut;
  testPrefix: string;
}): ReactElement {
  return (
    <li data-testid={`${testPrefix}-${round.round_id}`}>
      <Link to={`/rounds/${encodeURIComponent(round.round_id)}`}>
        {round.security_name}（{round.security_code}）
      </Link>{" "}
      <span className="money">{round.pnl}</span>{" "}
      <span className="muted">
        {round.return_rate_pct} · {round.close_date} · {round.holding_days} 天
      </span>
    </li>
  );
}

/** Dashboard（/dashboard）：资产概览 + 历史概览两个板块；旧子块按 legacy_period 按需加载。 */
export function Dashboard(): ReactElement {
  const [data, setData] = useState<DashboardOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [legacy, setLegacy] = useState<MetricBatch | null>(null);
  const [legacyLoading, setLegacyLoading] = useState(false);
  const [legacyError, setLegacyError] = useState<unknown>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.dashboard.get());
    } catch (exc) {
      setError(exc);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadLegacy = useCallback(async (): Promise<void> => {
    if (!data) return;
    setLegacyLoading(true);
    setLegacyError(null);
    try {
      setLegacy(await api.metrics.batch({ period: data.legacy_period }));
    } catch (exc) {
      setLegacyError(exc);
    } finally {
      setLegacyLoading(false);
    }
  }, [data]);

  if (error != null) {
    return (
      <div>
        <h1>看板</h1>
        <ProblemView error={error} />
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div>
        <h1>看板</h1>
        <p className="muted">加载中…</p>
      </div>
    );
  }

  const asset = data.asset_overview;
  const history = data.history_overview;
  const drawdown = history.max_drawdown;

  return (
    <div>
      <div className="row-actions">
        <h1>看板</h1>
        <button className="btn btn-ghost" type="button" disabled={loading} onClick={() => void load()}>
          刷新
        </button>
      </div>
      <p className="muted">
        资产概览为最新资产日快照（{data.as_of}）；历史概览为全历史；旧五卡为月度口径（
        {data.legacy_period}）。所有数值由后端格式化，本页只展示。
      </p>
      <p className="muted" data-testid="dashboard-meta">
        构建状态 <span className={`badge badge-${data.build_state}`}>{BUILD_STATE_LABELS[data.build_state] ?? data.build_state}</span>{" "}
        · 页面状态 <span className={`badge badge-${data.status}`}>{statusLabel(data.status)}</span>
        {data.data_as_of ? <> · 数据截至 {data.data_as_of}</> : null}
      </p>

      {data.build_state === "missing" || data.build_state === "stale" || data.build_state === "blocked" ? (
        <p className="notice notice-warn">
          当前资产快照不是最新：请到 <Link to={data.links.assets}>资产页</Link> 构建/更新资产表。
        </p>
      ) : null}

      {data.missing.length > 0 ? (
        <div className="card" data-testid="missing-list">
          <h2>缺失与降级项</h2>
          <ul>
            {data.missing.map((item) => (
              <li key={`${item.field}:${item.code}`} data-testid="missing-item" data-code={item.code}>
                <code>{item.code}</code> <span className="muted">{item.field}</span>
                <div>{item.reason}</div>
                <div className="muted">建议：{item.what_to_do}</div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <section className="card" data-testid="asset-overview">
        <h2>资产概览</h2>
        <div className="kpi-grid">
          <Kpi label="持仓市值" value={asset.market_value} testId="kpi-market-value" />
          <Kpi label="现金余额" value={asset.cash} testId="kpi-cash" />
          <Kpi label="仓位" value={asset.position_pct} testId="kpi-position" />
          <Kpi label="总收益（全历史）" value={asset.total_profit} testId="kpi-total-profit" />
          <Kpi
            label="历史峰值持仓成本"
            value={asset.max_invested_cost}
            testId="kpi-max-invested-cost"
          />
          <Kpi
            label="峰值成本收益率"
            value={asset.max_cost_return_pct}
            testId="kpi-max-cost-return"
          />
          <Kpi label="总费用消耗" value={asset.total_fees} testId="kpi-total-fees" />
          <Kpi label="总资产" value={asset.total_assets} testId="kpi-total-assets" />
        </div>

        <h3>当前持仓</h3>
        {asset.holdings.length === 0 ? (
          <p className="muted">没有持仓（或资产表未构建）。</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>数量</th>
                <th>成本</th>
                <th>市值</th>
                <th>权重</th>
              </tr>
            </thead>
            <tbody>
              {asset.holdings.map((row) => (
                <tr key={row.security_code} data-testid={`holding-row-${row.security_code}`}>
                  <td>{row.security_code}</td>
                  <td>{row.security_name}</td>
                  <td>{row.quantity}</td>
                  <td>{row.cost}</td>
                  <td>{row.market_value ?? "—"}</td>
                  <td>{row.weight_pct ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="row-actions">
          <Link to={data.links.assets}>资产明细</Link>
          <Link to={data.links.exports} data-testid="link-exports">
            导出（占位）
          </Link>
          <Link to={data.links.reports} data-testid="link-reports">
            月报（占位）
          </Link>
        </div>
      </section>

      <section className="card" data-testid="history-overview">
        <h2>历史概览</h2>
        <div className="kpi-grid">
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
          <Kpi label="历史最大单笔收益" value={history.max_profit} testId="kpi-max-profit" />
          <Kpi
            label="最大收益 / 峰值成本"
            value={history.max_profit_cost_pct}
            testId="kpi-max-profit-cost"
          />
        </div>

        <div className="card" data-testid="kpi-max-drawdown">
          <h3>最大回撤</h3>
          <p>
            <Value value={drawdown.mdd_pct} />{" "}
            <span className="muted">
              {drawdown.from_date ?? "—"} → {drawdown.to_date ?? "—"} · 持续{" "}
              {drawdown.duration_days == null ? "—" : `${drawdown.duration_days} 天`} · 恢复{" "}
              {drawdown.recovery_days == null
                ? drawdown.recovery_censored
                  ? "尚未恢复"
                  : "—"
                : `${drawdown.recovery_days} 天`}
            </span>
          </p>
          <p className="muted">
            <span className={`badge badge-${drawdown.sample_status}`}>
              {SAMPLE_STATUS_LABELS[drawdown.sample_status] ?? drawdown.sample_status}
            </span>{" "}
            有效观测 {drawdown.n_obs} 天
            {drawdown.reason ? <> · {drawdown.reason}</> : null}
          </p>
        </div>

        {history.max_profit_round ? (
          <div data-testid="max-profit-round">
            <h3>最大单笔收益</h3>
            <RoundRow round={history.max_profit_round} testPrefix="max-profit" />
          </div>
        ) : null}

        <h3>最赚 3 笔</h3>
        <ul>
          {history.top_rounds.map((round) => (
            <RoundRow key={round.round_id} round={round} testPrefix="top-round" />
          ))}
        </ul>

        <h3>最亏 3 笔</h3>
        <ul>
          {history.bottom_rounds.map((round) => (
            <RoundRow key={round.round_id} round={round} testPrefix="bottom-round" />
          ))}
        </ul>

        <div className="row-actions">
          <Link to={data.links.rounds}>回合</Link>
          <Link to={data.links.metrics} data-testid="link-legacy-metrics">
            月度指标（{data.legacy_period}）
          </Link>
          <Link to={data.links.attribution} data-testid="link-attribution">
            归因（占位）
          </Link>
        </div>
      </section>

      <section className="card" data-testid="legacy-metrics">
        <h2>月度指标（{data.legacy_period}）</h2>
        <p className="muted">
          旧五卡为月度口径，按需加载；数值由后端计算，本页只展示（含 n / 可信度）。
        </p>
        <button className="btn" type="button" disabled={legacyLoading} onClick={() => void loadLegacy()}>
          {legacyLoading ? "加载中…" : "加载月度指标"}
        </button>
        {legacyError != null ? <ProblemView error={legacyError} /> : null}
        {legacy ? (
          <table className="table">
            <thead>
              <tr>
                <th>指标</th>
                <th>值</th>
                <th>样本</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {legacy.items.map((row) => (
                <tr key={row.metric_id}>
                  <td>
                    <Link to={`/metrics/${encodeURIComponent(row.metric_id)}?period=${data.legacy_period}`}>
                      {row.metric_id}
                    </Link>
                  </td>
                  <td>{metricValue(row)}</td>
                  <td>
                    {row.n}
                    {SAMPLE_STATUS_LABELS[row.sample_status]
                      ? `（${SAMPLE_STATUS_LABELS[row.sample_status]}）`
                      : null}
                  </td>
                  <td>
                    <span className={`badge badge-${row.status}`} title={row.reason ?? undefined}>
                      {statusLabel(row.status)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </div>
  );
}
