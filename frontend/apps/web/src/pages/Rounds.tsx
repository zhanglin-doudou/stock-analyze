import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  newIdempotencyKey,
  type JobAccepted,
  type JobStatus,
  type Round,
  type RoundPage,
  type RoundStatus,
} from "@sa/api-client";
import { api } from "../client";
import { Cursor } from "../ui/Cursor";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";

const STATUS_LABELS: Record<RoundStatus, string> = {
  closed: "已平仓",
  open: "持仓中",
  data_error: "数据异常",
};

/** 状态中文标签；未知状态原样展示（后端新增枚举时不至于空白）。 */
export function roundStatusLabel(status: string): string {
  return STATUS_LABELS[status as RoundStatus] ?? status;
}

/** 盈亏/现金流的正负着色只做**符号展示**，不做任何数值计算（前端零口径）。 */
function signedClass(value: string): string {
  if (value.startsWith("-")) return "money money-neg";
  if (value === "0.00" || value === "0.0000") return "money";
  return "money money-pos";
}

interface RoundFilters {
  security_code: string;
  status: string;
  from: string;
  to: string;
}

const EMPTY_FILTERS: RoundFilters = { security_code: "", status: "", from: "", to: "" };

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>(["succeeded", "failed", "cancelled"]);

/** 回合列表（/rounds）：导入后自动建立，支持按代码/状态/日期查询与手动重建。 */
export function Rounds() {
  const [draft, setDraft] = useState<RoundFilters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<RoundFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState<RoundPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [jobProgress, setJobProgress] = useState(0);
  const [rebuilding, setRebuilding] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      setLoading(true);
      setError(null);
      try {
        setPage(
          await api.rounds.list({
            limit: 50,
            cursor,
            security_code: filters.security_code || undefined,
            status: filters.status || undefined,
            from: filters.from || undefined,
            to: filters.to || undefined,
          }),
        );
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [filters],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // 自动重建（导入/撤销/分类后入队）进行中：进入页面即接管进度轮询。
  const watchActiveRebuild = useCallback(async () => {
    try {
      const jobs = await api.jobs.list({ status: "pending,running", limit: 50 });
      const active = jobs.items.find((item) => item.type === "rebuild_rounds");
      if (active) {
        setActiveJobId(active.job_id);
        setJobStatus(active.status);
        setJobProgress(active.progress);
      }
    } catch {
      // 任务列表只是辅助提示，失败不阻断回合列表本身。
    }
  }, []);

  useEffect(() => {
    void watchActiveRebuild();
  }, [watchActiveRebuild]);

  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll(): Promise<void> {
      try {
        const job = await api.jobs.get(activeJobId as string);
        if (cancelled) return;
        setJobStatus(job.status);
        setJobProgress(job.progress);
        if (TERMINAL.has(job.status)) {
          setActiveJobId(null);
          setNotice(
            job.status === "succeeded"
              ? "回合已重建完成，列表已刷新。"
              : `回合重建未成功（${job.status}）；可稍后重试或到任务页查看原因。`,
          );
          void load();
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
  }, [activeJobId, load]);

  async function rebuild(): Promise<void> {
    setRebuilding(true);
    setError(null);
    setNotice("");
    try {
      const accepted: JobAccepted = await api.rounds.rebuild({ scope: "full" }, newIdempotencyKey());
      setActiveJobId(accepted.job_id);
      setJobStatus(accepted.status);
      setJobProgress(0);
      setNotice("已提交重建任务；回合生成期间可继续浏览旧数据。");
    } catch (err) {
      setError(err);
    } finally {
      setRebuilding(false);
    }
  }

  const columns: Array<DataTableColumn<Round>> = [
    {
      key: "security",
      header: "代码 / 名称",
      priority: 1,
      render: (row) => `${row.security_code} ${row.security_name}`.trim(),
    },
    {
      key: "status",
      header: "状态",
      priority: 1,
      render: (row) => <span className={`badge badge-${row.status}`}>{roundStatusLabel(row.status)}</span>,
    },
    { key: "open", header: "开仓日", priority: 2, render: (row) => row.open_date },
    { key: "close", header: "平仓日", priority: 2, render: (row) => row.close_date ?? "—" },
    { key: "quantity", header: "数量", priority: 2, align: "right", render: (row) => row.quantity },
    { key: "cost", header: "买入成本", priority: 2, align: "right", render: (row) => row.cost },
    { key: "proceeds", header: "卖出金额", priority: 2, align: "right", render: (row) => row.proceeds },
    { key: "dividend", header: "股息", priority: 3, align: "right", render: (row) => row.dividend },
    {
      key: "pnl",
      header: "盈亏",
      priority: 1,
      align: "right",
      render: (row) => <span className={signedClass(row.pnl)}>{row.pnl}</span>,
    },
    { key: "return", header: "收益率（小数）", priority: 3, align: "right", render: (row) => row.return_rate },
    { key: "days", header: "持有交易日", priority: 3, align: "right", render: (row) => row.holding_days },
    { key: "fills", header: "配对笔数", priority: 3, align: "right", render: (row) => row.fills_count },
    {
      key: "actions",
      header: "操作",
      priority: 1,
      align: "right",
      render: (row) => (
        <Link className="btn btn-ghost" to={`/rounds/${row.round_id}`}>
          详情
        </Link>
      ),
    },
  ];

  const building = activeJobId !== null;
  const emptyText = loading
    ? "加载中…"
    : building
      ? `回合正在生成中（${jobStatus ?? "running"} ${jobProgress}%）…`
      : "还没有回合：导入账单后系统会自动按 FIFO 建立回合，也可点「重建回合」。";

  return (
    <div>
      <h1>回合</h1>
      <p className="muted">
        导入账单成功后，后台会自动按 FIFO 配对建立回合（可分批减仓、计入股息/送转/配股）。
        一个回合 = 一次卖出及其消耗的买入批次；尚未卖出的持仓显示为「持仓中」。
      </p>

      <div className="card filters">
        <label>
          证券代码
          <input
            type="text"
            value={draft.security_code}
            placeholder="如 600519"
            onChange={(e) => setDraft({ ...draft, security_code: e.target.value })}
          />
        </label>
        <label>
          状态
          <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
            <option value="">全部</option>
            <option value="closed">已平仓</option>
            <option value="open">持仓中</option>
            <option value="data_error">数据异常</option>
          </select>
        </label>
        <label>
          起始日
          <input type="date" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
        </label>
        <label>
          截止日
          <input type="date" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        </label>
        <div className="row-actions">
          <button className="btn" type="button" onClick={() => setFilters(draft)}>
            查询
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => {
              setDraft(EMPTY_FILTERS);
              setFilters(EMPTY_FILTERS);
            }}
          >
            清空筛选
          </button>
          <button className="btn btn-ghost" type="button" disabled={loading} onClick={() => void load()}>
            刷新
          </button>
          <button className="btn btn-ghost" type="button" disabled={rebuilding || building} onClick={() => void rebuild()}>
            {building ? `重建中… ${jobProgress}%` : "重建回合"}
          </button>
        </div>
      </div>

      {notice ? <p className="notice">{notice}</p> : null}
      <ProblemView error={error} onRetry={() => void load()} />

      <DataTable
        caption="回合列表"
        columns={columns}
        rows={page?.items ?? []}
        rowKey={(row) => row.round_id}
        emptyText={emptyText}
        loading={loading && page == null}
      />

      {page ? (
        <Cursor
          hasMore={page.has_more}
          nextCursor={page.next_cursor ?? null}
          disabled={loading}
          onNext={() => void load(page.next_cursor ?? undefined)}
          onReset={() => void load()}
        />
      ) : null}

      <p className="muted">
        没有数据？先到 <Link to="/import">导入账单</Link>；导入完成会自动建立回合。
      </p>
    </div>
  );
}
