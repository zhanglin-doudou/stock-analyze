import { useCallback, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  type AssetBuildState,
  type AssetPage,
  type AssetPoint,
  type Round,
} from "@sa/api-client";
import { api } from "../client";
import { periodToDateRange } from "../lib/date";
import { useStaleAssetBuild } from "../lib/useStaleAssetBuild";
import { Cursor } from "../ui/Cursor";
import { FilterBar, FilterField } from "../ui/FilterBar";
import { JobProgress } from "../ui/JobProgress";
import { OversellRevisionDialog } from "../ui/OversellRevisionDialog";
import { PeriodInput } from "../ui/PeriodInput";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";

const PAGE_LIMIT = 50;

/** 资产表筛选：期间 token（`YYYY` / `YYYY-MM` / `YYYY-MM-DD..YYYY-MM-DD`）+ 行状态 + 排序。 */
interface AssetFilters {
  period: string;
  status: string;
  sort: "trade_date" | "-trade_date";
}

const EMPTY_FILTERS: AssetFilters = { period: "", status: "", sort: "trade_date" };

const SORT_LABELS: Record<AssetFilters["sort"], string> = {
  trade_date: "正序（旧 → 新）",
  "-trade_date": "倒序（新 → 旧）",
};

const ROW_STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  blocked: "阻断",
  degraded: "降级",
};

/** 行状态中文标签；未知状态原样展示（后端新增枚举时不至于空白）。 */
export function assetStatusLabel(status: string): string {
  return ROW_STATUS_LABELS[status] ?? status;
}

const MODE_LABELS: Record<string, string> = {
  full: "全量",
  incremental: "增量",
  noop: "无变化",
};

/** 最近构建模式中文标签；未构建（null）显示「—」。 */
export function assetModeLabel(mode: string | null | undefined): string {
  if (!mode) return "—";
  return MODE_LABELS[mode] ?? mode;
}

const STATE_LABELS: Record<string, string> = {
  missing: "未构建",
  current: "最新",
  stale: "待更新",
  blocked: "阻断",
};

/** 构建状态机中文标签；未知状态原样展示。 */
export function assetBuildStateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

/** 正负着色只做**符号展示**，不做任何数值计算（前端零口径）。
 * 注意：`0.00` / `0.00%` 视为零值、不着色（修正 Nav 页 signedClass 对 "0.00%" 的误判）。
 */
export function signedClass(value: string): string {
  if (/^-?0(?:\.0+)?%?$/.test(value)) return "money";
  if (value.startsWith("-")) return "money money-neg";
  return "money money-pos";
}

/** 金额/比率单元格：blocked 行后端返回 null，统一显示「—」。 */
function moneyCell(value: string | null | undefined): ReactNode {
  if (value == null) return <span className="muted">—</span>;
  return <span className={signedClass(value)}>{value}</span>;
}

/** 时间戳只做字符串展示（去掉 ISO 的 T/Z），不做任何时区换算。 */
function timestampLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace("T", " ").replace("Z", " UTC");
}

/** 构建完成提示：按 `build.last_mode/last_rebuild_from/last_rows_written` 渲染。 */
export function buildNotice(
  build: AssetBuildState,
  dataAsOf: string | null | undefined,
): string {
  const asOf = dataAsOf ?? build.last_built_through ?? "—";
  if (build.last_mode === "noop") return "资产表已是最新，无需更新。";
  if (build.last_mode === "incremental") {
    return `增量更新完成：重算 ${build.last_rows_written} 天（自 ${build.last_rebuild_from ?? "—"} 起），数据截至 ${asOf}。`;
  }
  if (build.last_mode === "full") {
    return `已构建 ${build.last_rows_written} 天资产记录，数据截至 ${asOf}。`;
  }
  return `资产表构建完成，数据截至 ${asOf}。`;
}

interface BuildSummaryProps {
  page: AssetPage;
}

/** 汇总条：数据截至 / 记录数 / 最近构建 / 模式 / 锚点 / 口径版本 + stale/degraded/blocked 提示。 */
function BuildSummary({ page }: BuildSummaryProps): ReactElement {
  const build = page.build;
  return (
    <div className="card" data-testid="assets-summary">
      <p>
        <span className="muted">数据截至：</span>
        <strong>{page.data_as_of ?? "—"}</strong>
        <span className="muted">　记录数：</span>
        {build.rows_count}
        <span className="muted">　最近构建/检查：</span>
        {timestampLabel(build.last_built_at)}
        <span className="muted">　最近模式：</span>
        {assetModeLabel(build.last_mode)}
        <span className="muted">　重写行数：</span>
        {build.last_rows_written}
        <span className="muted">　锚点：</span>
        {build.assumption === "back_solved" ? "期初反推（back_solved）" : "—"}
        <span className="muted">　口径版本：</span>
        {page.definition_version}
        <span className="muted">　状态：</span>
        <span className={`badge badge-${build.state === "current" ? "ok" : build.state}`}>
          {assetBuildStateLabel(build.state)}
        </span>
      </p>
      {build.state === "stale" ? (
        <p className="notice-warn" data-testid="assets-stale">
          有新流水/新行情，建议更新。
          {build.has_new_market_data ? "（检测到新行情数据）" : ""}
        </p>
      ) : null}
      {build.session_notice === "market_open" ? (
        <p className="muted" data-testid="assets-session-notice">
          当日还未收盘（A 股 15:00 收盘），资产表暂不包含当日数据。
        </p>
      ) : null}
      {build.session_notice === "sync_pending" ? (
        <p className="notice-warn" data-testid="assets-session-notice">
          当日已收盘但行情尚未同步——请先在终端运行 make sync-market
          同步行情数据，再点「更新资产表」。
        </p>
      ) : null}
      {build.last_status === "degraded" ? (
        <div className="notice-warn" data-testid="assets-degraded">
          <p>部分日期存在阻断/降级，修复数据后点「更新资产表」即可重算。</p>
          {build.status_reason ? <p className="muted">{build.status_reason}</p> : null}
        </div>
      ) : null}
      {build.state === "blocked" ? (
        <div className="notice-warn" data-testid="assets-blocked-banner">
          <p>资产表被阻断：缺少期初资产锚点或行情数据，无法输出资产值。</p>
          {build.status_reason ? <p className="muted">{build.status_reason}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** 资产表（/assets）：每日资产快照列表 + 构建/更新（异步 job + SSE 进度 + 轮询兜底）。 */
export function Assets() {
  const [draft, setDraft] = useState<AssetFilters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<AssetFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState<AssetPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  // 超卖回合：资产行的「补录成本」入口按 close_date 对应到这一天，复用回合页的修订弹窗。
  const [oversellRounds, setOversellRounds] = useState<Round[]>([]);
  const [revisionTradeId, setRevisionTradeId] = useState<string | null>(null);
  const [autoFiltered, setAutoFiltered] = useState(false);
  const autoFilterApplied = useRef(false);

  const load = useCallback(
    async (cursor?: string): Promise<AssetPage | null> => {
      setLoading(true);
      setError(null);
      const range = periodToDateRange(filters.period);
      try {
        const data = await api.assets.list({
          limit: PAGE_LIMIT,
          cursor,
          from: range.from || undefined,
          to: range.to || undefined,
          status: filters.status || undefined,
          sort: filters.sort,
        });
        if (cursor) {
          setPage((previous) => {
            if (!previous) return data;
            const seen = new Set(previous.items.map((row) => row.date));
            const appended = data.items.filter((row) => !seen.has(row.date));
            return { ...data, items: [...previous.items, ...appended] };
          });
        } else {
          setPage(data);
        }
        return data;
      } catch (err) {
        setError(err);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [filters],
  );

  const loadOversellRounds = useCallback(async (): Promise<void> => {
    try {
      const list = await api.rounds.list({ limit: 100, status: "oversell" });
      setOversellRounds(list.items);
    } catch {
      // 修订入口是增强块：接口失败不影响资产表本身。
      setOversellRounds([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadOversellRounds();
  }, [loadOversellRounds]);

  // 首屏加载完成且整体状态非正常（degraded/blocked）时，用该状态回填筛选并触发一次过滤，
  // 让异常数据直接可见；只自动应用一次，之后尊重用户的手动筛选。
  useEffect(() => {
    if (!page || autoFilterApplied.current) return;
    autoFilterApplied.current = true;
    const abnormal = page.build.last_status;
    if (abnormal && abnormal !== "ok") {
      setDraft((previous) => ({ ...previous, status: abnormal }));
      setFilters((previous) => ({ ...previous, status: abnormal }));
      setAutoFiltered(true);
    }
  }, [page]);

  /** 构建成功后重拉列表并给出完成提示（文案全部来自后端 build 摘要）。 */
  const refreshAfterBuild = useCallback(async (): Promise<void> => {
    const fresh = await load();
    void loadOversellRounds();
    if (fresh) setNotice(buildNotice(fresh.build, fresh.data_as_of));
  }, [load, loadOversellRounds]);

  // 资产表 stale 自动构建：判定 / 触发 / 全局去重 / 轮询都在公共 hook 内（与看板同源）。
  const auto = useStaleAssetBuild({ build: page?.build, onRefresh: refreshAfterBuild });

  const build = page?.build;
  const missing = build === undefined || build.state === "missing";
  const executing = auto.status === "submitting" || auto.status === "running";
  const actionLabel = executing
    ? missing
      ? "构建中…"
      : "更新中…"
    : missing
      ? "构建资产表"
      : "更新资产表";

  /** 日期 → 当日超卖回合（用于「补录成本」入口；按 close_date 精确对应资产行）。 */
  const oversellByDate = new Map<string, Round[]>();
  for (const item of oversellRounds) {
    if (!item.close_date || !item.close_trade_id) continue;
    const list = oversellByDate.get(item.close_date) ?? [];
    list.push(item);
    oversellByDate.set(item.close_date, list);
  }

  const columns: Array<DataTableColumn<AssetPoint>> = [
    { key: "date", header: "日期", priority: 1, render: (row) => row.date },
    {
      key: "total_assets",
      header: "总资产",
      priority: 1,
      align: "right",
      render: (row) =>
        row.total_assets == null ? (
          <span className="muted">—</span>
        ) : (
          <strong>{row.total_assets}</strong>
        ),
    },
    { key: "cash", header: "现金", priority: 3, align: "right", render: (row) => moneyCell(row.cash) },
    {
      key: "market_value",
      header: "持仓市值",
      priority: 1,
      align: "right",
      render: (row) => moneyCell(row.market_value),
    },
    {
      key: "holding_cost",
      header: "持仓成本",
      priority: 3,
      align: "right",
      render: (row) => moneyCell(row.holding_cost),
    },
    {
      key: "unrealized_pnl",
      header: "浮动盈亏",
      priority: 2,
      align: "right",
      render: (row) => moneyCell(row.unrealized_pnl),
    },
    {
      key: "daily_pnl",
      header: "日收益额",
      priority: 3,
      align: "right",
      render: (row) => moneyCell(row.daily_pnl),
    },
    {
      key: "daily_return_pct",
      header: "日收益率",
      priority: 2,
      align: "right",
      render: (row) =>
        row.daily_return_pct == null ? (
          <span className="muted">—</span>
        ) : (
          <span className={signedClass(row.daily_return_pct)}>{row.daily_return_pct}</span>
        ),
    },
    {
      key: "cashflow",
      header: "外部现金流",
      priority: 3,
      align: "right",
      render: (row) => moneyCell(row.cashflow),
    },
    {
      key: "holding_count",
      header: "持仓数",
      priority: 3,
      align: "right",
      render: (row) => (row.status === "blocked" ? <span className="muted">—</span> : row.holding_count),
    },
    {
      key: "status",
      header: "状态",
      priority: 1,
      render: (row) => {
        const oversells = oversellByDate.get(row.date) ?? [];
        return (
          <span className="assets-status">
            <span className={`badge badge-${row.status}`} title={row.status_reason ?? undefined}>
              {assetStatusLabel(row.status)}
            </span>
            {oversells.map((item) => (
              <button
                key={item.round_id}
                type="button"
                className="btn btn-text"
                title={`超卖卖出 ${item.close_trade_id}，补录该笔卖出的成本`}
                onClick={() => setRevisionTradeId(item.close_trade_id ?? null)}
              >
                补录成本{oversells.length > 1 ? `（${item.security_code}）` : ""}
              </button>
            ))}
          </span>
        );
      },
    },
  ];

  const blocked = page?.build.state === "blocked";
  const emptyText = loading
    ? "加载中…"
    : executing
      ? "资产表正在构建中…"
      : blocked
        ? "资产表被阻断，暂无可用记录。"
        : "还没有资产记录：先导入账单，再点「构建资产表」。";

  return (
    <div>
      <h1>资产表</h1>
      <p className="muted">
        每日资产快照（总资产 = 现金 + 持仓市值），用于资产变化与后续回撤；本页不做计算。
      </p>
      <p className="muted" data-testid="market-data-notice">
        行情来自本地缓存（仅收盘后同步，盘中不拉取实时行情）；构建前请确认已执行收盘后同步。
      </p>

      <FilterBar
        ariaLabel="资产表筛选与操作"
        count={build ? `共 ${build.rows_count} 条记录` : undefined}
        actions={
          <>
            <button
              className="btn"
              type="button"
              disabled={loading}
              onClick={() => setFilters(draft)}
            >
              应用筛选
            </button>
            <button
              className="btn btn-ghost"
              type="button"
              disabled={executing}
              aria-busy={executing}
              onClick={() => {
                setNotice("");
                void auto.submit("auto");
              }}
            >
              {actionLabel}
            </button>
            <button className="btn btn-ghost" type="button" disabled={loading} onClick={() => void load()}>
              刷新
            </button>
          </>
        }
        onReset={() => {
          setDraft(EMPTY_FILTERS);
          setFilters(EMPTY_FILTERS);
          setAutoFiltered(false);
        }}
        resetLabel="清空筛选"
        note={
          <p className="muted">
            构建/更新在后台执行（默认 mode=auto：增量优先，必要时全量重算）；完成后会自动刷新列表。
          </p>
        }
      >
        <FilterField label="时间范围">
          <PeriodInput
            value={draft.period}
            aria-label="时间范围"
            onChange={(value) => setDraft({ ...draft, period: value })}
          />
        </FilterField>
        <FilterField label="状态">
          <select
            aria-label="状态"
            value={draft.status}
            onChange={(event) => setDraft({ ...draft, status: event.target.value })}
          >
            <option value="">全部</option>
            <option value="ok">正常</option>
            <option value="degraded">降级</option>
            <option value="blocked">阻断</option>
          </select>
        </FilterField>
        <FilterField label="排序">
          <select
            aria-label="排序"
            value={draft.sort}
            onChange={(event) =>
              setDraft({ ...draft, sort: event.target.value as AssetFilters["sort"] })
            }
          >
            <option value="trade_date">{SORT_LABELS.trade_date}</option>
            <option value="-trade_date">{SORT_LABELS["-trade_date"]}</option>
          </select>
        </FilterField>
      </FilterBar>

      {autoFiltered ? (
        <p className="notice-warn" data-testid="assets-auto-filter">
          资产表当前整体状态为非正常（{assetStatusLabel(filters.status)}），已自动按该状态筛选；
          修复数据或补录成本后点「更新资产表」即可重算。
        </p>
      ) : null}

      {notice ? (
        <div className="row-actions">
          <p className="notice" data-testid="assets-notice">
            {notice}
          </p>
          <p>不满意？或者试试:</p>
          <button
            className="btn btn-text"
            type="button"
            disabled={executing}
            aria-busy={executing}
            onClick={() => {
              setNotice("");
              void auto.submit("full");
            }}
          >
            强制重新构建
          </button>
        </div>
      ) : null}
      <ProblemView error={error} onRetry={() => void load()} />

      {auto.jobId ? (
        <div className="card" data-testid="assets-job-progress">
          <JobProgress jobId={auto.jobId} onDone={auto.handleJobDone} />
        </div>
      ) : null}

      {page ? (
        <>
          <BuildSummary page={page} />
          {auto.status === "failed" ? (
            <div className="notice-warn" data-testid="assets-auto-retry">
              <ProblemView error={auto.error} />
              <p>构建未成功，可稍后重试。</p>
              <button
                className="btn btn-text"
                type="button"
                disabled={executing}
                onClick={auto.retry}
              >
                手动重试
              </button>
            </div>
          ) : null}
          <DataTable
            caption="每日资产快照"
            columns={columns}
            rows={page.items}
            rowKey={(row) => row.date}
            loading={loading && page.items.length === 0}
            emptyText={emptyText}
          />
          <Cursor
            hasMore={page.has_more}
            nextCursor={page.next_cursor ?? null}
            disabled={loading || executing}
            onNext={() => void load(page.next_cursor ?? undefined)}
            onReset={() => void load()}
          />
          <p className="muted">
            没有数据？先到 <Link to="/import">导入账单</Link>，再点「构建资产表」。
          </p>
        </>
      ) : loading ? (
        <p className="muted">加载中…</p>
      ) : null}

      <OversellRevisionDialog
        tradeId={revisionTradeId}
        onClose={() => setRevisionTradeId(null)}
        onSettled={() => {
          setRevisionTradeId(null);
          // 只刷新超卖回合列表（已补录的回合会从列表消失）；不刷新资产表，
          // 避免 build.state 变 stale 触发 useStaleAssetBuild 自动构建——
          // 资产表等用户全部补录完成后手动点「更新资产表」统一重算。
          void loadOversellRounds();
        }}
      />
    </div>
  );
}
