import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  newIdempotencyKey,
  type JobStatus,
  type MetricBatch,
  type MetricItem,
  type Problem,
} from "@sa/api-client";
import { api } from "../client";
import { JobProgress } from "../ui/JobProgress";
import { ProblemView } from "../ui/Problem";
import { Table, type Column } from "../ui/Table";

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>(["succeeded", "failed", "cancelled"]);

const DOMAIN_LABELS: Record<string, string> = {
  perf: "收益",
  timing: "择时",
  behavior: "行为",
  selection: "选股",
  attribution: "归因",
};

const STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  blocked: "阻断",
  stale: "过期",
  unreliable: "不可靠",
};

const SAMPLE_STATUS_LABELS: Record<string, string> = {
  ok: "",
  insufficient: "样本不足",
  pending_window: "窗口未满",
  blocked: "阻断",
};

const UNIT_LABELS: Record<string, string> = {
  CNY: "元",
  ratio: "",
  days: "天",
  count: "次",
  z: "z",
};

export function domainLabel(domain: string): string {
  return DOMAIN_LABELS[domain] ?? domain;
}

export function metricStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** 指标值单元格：非 ok 后端整键省略 value（C-31），统一显示「—」。 */
function valueCell(row: MetricItem): ReactNode {
  if (row.value == null) return <span className="muted">—</span>;
  const unit = UNIT_LABELS[row.unit] ?? row.unit;
  return (
    <strong>
      {row.value}
      {unit ? <span className="muted"> {unit}</span> : null}
    </strong>
  );
}

/** job failed 时若后端未给出 7807 错误体，用错误码兜底渲染。 */
function jobFailureProblem(status: JobStatus): Problem {
  return {
    type: "about:blank",
    title: "Rebuild period failed",
    status: 0,
    detail: `job status=${status}`,
    code: "REBUILD_PERIOD_FAILED",
    user_message: `复盘数据构建未成功（${status}）。`,
    what_to_do: "请稍后重试；若持续失败，请到任务页查看错误码或联系支持。",
    retryable: true,
    request_id: "",
  };
}

/** 指标页（/metrics）：按月一键构建复盘数据（rounds → 资产 → 净值 → 指标）+ 结果展示。 */
export function Metrics() {
  const [period, setPeriod] = useState(() => new Date().toISOString().slice(0, 7));
  const [includeExploratory, setIncludeExploratory] = useState(false);
  const [batch, setBatch] = useState<MetricBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  /** 同一个 job 可能被 SSE done 与页面轮询各通知一次：只处理一次。 */
  const handledJobRef = useRef<string | null>(null);

  const load = useCallback(
    async (targetPeriod: string, exploratory: boolean): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.metrics.batch({
          period: targetPeriod,
          include_exploratory: exploratory,
        });
        setBatch(data);
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(period, includeExploratory);
  }, [load, period, includeExploratory]);

  // 进入页面接管在跑的 rebuild_period（刷新后不重复点击）。
  useEffect(() => {
    let cancelled = false;
    async function takeover(): Promise<void> {
      try {
        const jobs = await api.jobs.list({ status: "pending,running", limit: 50 });
        const active = jobs.items.find((item) => item.type === "rebuild_period");
        if (active && !cancelled) {
          handledJobRef.current = null;
          setActiveJobId(active.job_id);
        }
      } catch {
        // 任务列表只是辅助提示：失败不阻断指标页本身。
      }
    }
    void takeover();
    return () => {
      cancelled = true;
    };
  }, []);

  const finishJob = useCallback(
    async (jobId: string, status: JobStatus, jobError?: unknown): Promise<void> => {
      if (handledJobRef.current === jobId) return;
      handledJobRef.current = jobId;
      setActiveJobId((current) => (current === jobId ? null : current));
      if (status === "succeeded") {
        setNotice("复盘数据构建完成（回合 → 资产 → 净值 → 指标），结果已刷新。");
        await load(period, includeExploratory);
        return;
      }
      await load(period, includeExploratory);
      setError(jobError ?? jobFailureProblem(status));
      setNotice(`复盘数据构建未成功（${status}），可稍后重试。`);
    },
    [load, period, includeExploratory],
  );

  // 页面级轮询兜底：JobProgress 只负责 SSE。
  useEffect(() => {
    if (!activeJobId) return;
    const jobId = activeJobId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll(): Promise<void> {
      try {
        const job = await api.jobs.get(jobId);
        if (cancelled) return;
        if (TERMINAL.has(job.status)) {
          await finishJob(jobId, job.status, job.error ?? undefined);
          return;
        }
      } catch (err) {
        if (!cancelled) setError(err);
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 800);
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [activeJobId, finishJob]);

  const handleSseDone = useCallback(
    (status: JobStatus) => {
      if (!activeJobId) return;
      void finishJob(activeJobId, status);
    },
    [activeJobId, finishJob],
  );

  const startBuild = useCallback(async (): Promise<void> => {
    setSubmitting(true);
    setError(null);
    setNotice("");
    try {
      // 构建默认只预热核心 10 个指标（exploratory 由下方勾选走按需读路径）。
      const accepted = await api.jobs.rebuildPeriod(
        { period, include_exploratory: false },
        newIdempotencyKey(),
      );
      handledJobRef.current = null;
      setActiveJobId(accepted.job_id);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }, [period]);

  const executing = activeJobId !== null || submitting;

  const columns: Array<Column<MetricItem>> = [
    { key: "metric_id", header: "指标", render: (row) => row.metric_id },
    { key: "domain", header: "域", render: (row) => domainLabel(row.domain) },
    { key: "value", header: "值", render: (row) => valueCell(row) },
    {
      key: "n",
      header: "样本",
      render: (row) => {
        const sample = SAMPLE_STATUS_LABELS[row.sample_status];
        return (
          <>
            {row.n}
            {sample ? <span className="muted">（{sample}）</span> : null}
          </>
        );
      },
    },
    {
      key: "status",
      header: "状态",
      render: (row) => (
        <span className={`badge badge-${row.status}`} title={row.reason ?? undefined}>
          {metricStatusLabel(row.status)}
        </span>
      ),
    },
    {
      key: "reason",
      header: "原因",
      render: (row) =>
        row.reason ? <span className="muted">{row.reason}</span> : <span className="muted">—</span>,
    },
  ];

  const emptyText = loading
    ? "加载中…"
    : executing
      ? "复盘数据正在构建中…"
      : "还没有指标结果：先点「构建复盘数据」，或先导入账单。";

  return (
    <div>
      <h1>指标</h1>
      <p className="muted">
        按月复盘指标（收益 / 择时 / 行为 / 选股）；数值由后端计算，本页只展示。
      </p>

      <div className="card filters">
        <div className="row-actions">
          <label>
            月份{" "}
            <input
              type="month"
              value={period}
              disabled={executing}
              onChange={(event) => setPeriod(event.target.value)}
            />
          </label>
          <button
            className="btn"
            type="button"
            disabled={executing || !period}
            aria-busy={executing}
            onClick={() => void startBuild()}
          >
            {executing ? "构建中…" : "构建复盘数据"}
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            disabled={loading}
            onClick={() => void load(period, includeExploratory)}
          >
            刷新
          </button>
          <label className="muted">
            <input
              type="checkbox"
              checked={includeExploratory}
              onChange={(event) => setIncludeExploratory(event.target.checked)}
            />{" "}
            含探索指标（HHI / 换手 / 摊平等，按需计算）
          </label>
        </div>
        <p className="muted">
          「构建复盘数据」在后台按月顺序执行：回合重建 → 资产表 → 净值 → 指标（默认核心 10
          个；探索指标勾选后按需计算）。净值页的「构建净值与收益」不受影响。
        </p>
      </div>

      {notice ? (
        <p className="notice" data-testid="metrics-notice">
          {notice}
        </p>
      ) : null}
      <ProblemView error={error} onRetry={() => void load(period, includeExploratory)} />

      {activeJobId ? (
        <div className="card" data-testid="metrics-job-progress">
          <JobProgress jobId={activeJobId} onDone={handleSseDone} />
        </div>
      ) : null}

      {batch ? (
        <>
          <div className="card" data-testid="metrics-summary">
            <p>
              <span className="muted">期间：</span>
              <strong>{batch.period}</strong>
              <span className="muted">　数据截至：</span>
              {batch.data_as_of}
              <span className="muted">　口径版本：</span>
              {batch.definition_version}
              <span className="muted">　指标数：</span>
              {batch.items.length}
            </p>
          </div>
          <Table
            columns={columns}
            rows={batch.items}
            rowKey={(row) => row.metric_id}
            emptyText={emptyText}
          />
          <p className="muted">
            没有数据？先到 <Link to="/import">导入账单</Link>，再点「构建复盘数据」。
          </p>
        </>
      ) : loading ? (
        <p className="muted">加载中…</p>
      ) : null}
    </div>
  );
}
