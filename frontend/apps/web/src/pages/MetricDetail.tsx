import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { MetricDrilldown, MetricDrilldownItem, MetricExplain } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { Table, type Column } from "../ui/Table";
import { UNIT_LABELS, domainLabel, metricStatusLabel } from "./Metrics";

const SAMPLE_STATUS_LABELS: Record<string, string> = {
  ok: "样本充足",
  insufficient: "样本不足",
  pending_window: "窗口未满",
  blocked: "阻断",
};

const CI_LABELS: Record<string, string> = {
  wilson: "Wilson 区间",
  bootstrap: "Bootstrap 区间",
  none: "不提供区间",
};

/** 样本明细值列：后端定点字符串，只展示不计算（零口径）。 */
function drilldownValue(item: MetricDrilldownItem): string {
  if (item.value == null) return "—";
  const unit = UNIT_LABELS[item.unit] ?? item.unit;
  return unit ? `${item.value} ${unit}` : item.value;
}

/** 指标详情页（/metrics/:metricId）：解释（含义）/ 计算方式 / 样本明细。 */
export function MetricDetail() {
  const { metricId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const period = searchParams.get("period") ?? new Date().toISOString().slice(0, 7);
  const [explain, setExplain] = useState<MetricExplain | null>(null);
  const [drilldown, setDrilldown] = useState<MetricDrilldown | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [drilldownError, setDrilldownError] = useState<unknown>(null);

  const load = useCallback(
    async (targetPeriod: string): Promise<void> => {
      setLoading(true);
      setError(null);
      setDrilldownError(null);
      try {
        setExplain(await api.metrics.explain(metricId, { period: targetPeriod }));
      } catch (err) {
        setExplain(null);
        setDrilldown(null);
        setError(err);
        setLoading(false);
        return;
      }
      // 样本明细失败不阻断解释展示（解释是详情页的主内容）。
      try {
        setDrilldown(await api.metrics.drilldown(metricId, { period: targetPeriod, limit: 50 }));
      } catch (err) {
        setDrilldown(null);
        setDrilldownError(err);
      }
      setLoading(false);
    },
    [metricId],
  );

  useEffect(() => {
    void load(period);
  }, [load, period]);

  const columns: Array<Column<MetricDrilldownItem>> = [
    { key: "label", header: "样本", render: (row) => row.label },
    { key: "value", header: "值", render: (row) => <strong>{drilldownValue(row)}</strong> },
    {
      key: "event_date",
      header: "日期",
      render: (row) => row.event_date ?? <span className="muted">—</span>,
    },
    {
      key: "security_code",
      header: "标的",
      render: (row) => row.security_code ?? <span className="muted">—</span>,
    },
    {
      key: "round_id",
      header: "回合",
      render: (row) =>
        row.round_id ? (
          <Link to={`/rounds/${row.round_id}`}>{row.round_id}</Link>
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];

  return (
    <div>
      <p className="muted">
        <Link to={`/metrics?period=${period}`}>← 返回指标列表</Link>
      </p>
      <h1>{metricId}</h1>
      <p className="muted">
        {explain ? `${domainLabel(explain.domain)} · ` : ""}期间 {period}
      </p>

      <div className="card filters">
        <div className="row-actions">
          <label>
            月份{" "}
            <input
              type="month"
              value={period}
              onChange={(event) => setSearchParams({ period: event.target.value })}
            />
          </label>
          <button
            className="btn btn-ghost"
            type="button"
            disabled={loading}
            onClick={() => void load(period)}
          >
            刷新
          </button>
        </div>
      </div>

      <ProblemView error={error} onRetry={() => void load(period)} />

      {explain ? (
        <>
          <div className="card" data-testid="metric-status">
            <p>
              <span className="muted">状态：</span>
              <span className={`badge badge-${explain.status}`}>
                {metricStatusLabel(explain.status)}
              </span>
              <span className="muted">　数据截至：</span>
              {explain.data_as_of}
              <span className="muted">　口径版本：</span>
              {explain.definition_version}
            </p>
            {explain.reason ? <p className="muted">原因：{explain.reason}</p> : null}
            {explain.what_to_do ? <p>{explain.what_to_do}</p> : null}
          </div>

          <div className="card" data-testid="metric-meaning">
            <h2>意义</h2>
            <p>{explain.meaning}</p>
            {explain.conclusion && explain.conclusion !== explain.meaning ? (
              <>
                <h3>本期结论</h3>
                <p>{explain.conclusion}</p>
              </>
            ) : null}
          </div>

          <div className="card" data-testid="metric-method">
            <h2>计算方式</h2>
            <p>{explain.method}</p>
          </div>

          <div className="card" data-testid="metric-sample">
            <h2>样本</h2>
            <p>
              <span className="muted">样本量：</span>
              <strong>{explain.sample.n}</strong>
              <span className="muted">（待满 {explain.sample.n_pending}）　判定门槛：</span>
              {explain.sample.min_n}
              <span className="muted">　状态：</span>
              {SAMPLE_STATUS_LABELS[explain.sample.sample_status] ?? explain.sample.sample_status}
            </p>
            {explain.sample.ci !== "none" ? (
              <p className="muted">
                区间方法：{CI_LABELS[explain.sample.ci] ?? explain.sample.ci}（样本 ≥{" "}
                {explain.sample.ci_min_n} 才给出）
              </p>
            ) : null}
            <p className="muted">{explain.drilldown.description}</p>
            {drilldownError ? (
              <ProblemView error={drilldownError} onRetry={() => void load(period)} />
            ) : null}
            <Table
              columns={columns}
              rows={drilldown?.items ?? []}
              rowKey={(row) => row.key}
              emptyText={loading ? "加载中…" : "该指标在本期没有可下钻的样本行。"}
            />
          </div>
        </>
      ) : loading ? (
        <p className="muted">加载中…</p>
      ) : null}
    </div>
  );
}
