import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  newIdempotencyKey,
  type JobAccepted,
  type RoundInsightItem,
  type RoundInsightsPage,
  type RoundInsightsRefreshOut,
} from "@sa/api-client";
import { api } from "../client";
import { currentYearToken } from "../lib/date";
import { signedClass } from "../lib/signed";
import { Cursor } from "../ui/Cursor";
import { FilterBar, FilterField } from "../ui/FilterBar";
import { FlagGlossary } from "../ui/FlagGlossary";
import { PeriodInput } from "../ui/PeriodInput";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";
import { HoldingCharts } from "./rounds/HoldingCharts";

/** 设计 §6.3 的 `flag` 词表（前端只做筛选选择，不参与计算）。 */
const FLAG_OPTIONS = [
  { value: "", label: "全部" },
  { value: "addon_decline", label: "加仓后下跌" },
  { value: "sold_too_early", label: "卖飞" },
  { value: "reason_missing", label: "缺理由" },
  { value: "horizon_pending", label: "前瞻待算" },
  { value: "corporate_action", label: "公司行动" },
];

const BUCKET_OPTIONS = [
  { value: "", label: "全部持有期" },
  { value: "scalp", label: "超短线（≤3 日）" },
  { value: "short", label: "短线（4~10 日）" },
  { value: "position", label: "中线（11~126 日）" },
  { value: "long", label: "长线（>126 日）" },
];

const SORT_OPTIONS = [
  { value: "combined", label: "综合（买卖两侧）" },
  { value: "buy", label: "买入侧" },
  { value: "sell", label: "卖出侧" },
  { value: "pnl", label: "盈亏" },
  { value: "return", label: "收益率" },
  { value: "close_date", label: "平仓日" },
];

/** 证券类型多选（值域与后端 `security_type` 白名单一致，四类覆盖全部回合）。 */
const SECURITY_TYPE_OPTIONS = [
  { value: "stock", label: "股票" },
  { value: "fund", label: "基金" },
  { value: "convertible_bond", label: "可转债" },
  { value: "other", label: "其他" },
] as const;

/** 页面默认只看股票（货基申购赎回会稀释回合统计）；URL 缺省即此值，保持干净。 */
const DEFAULT_SECURITY_TYPES: string[] = ["stock"];

/** URL 缺省 → 默认股票；非法/空值回落默认，不把空选择传给后端（避免变成「全部」）。 */
function parseSecurityTypes(raw: string | null): string[] {
  if (!raw) return DEFAULT_SECURITY_TYPES;
  const allowed = new Set<string>(SECURITY_TYPE_OPTIONS.map((option) => option.value));
  const picked = raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => allowed.has(value));
  return picked.length ? picked : DEFAULT_SECURITY_TYPES;
}

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

const BUCKET_LABELS: Record<string, string> = {
  scalp: "超短线（≤3 日）",
  short: "短线（4~10 日）",
  position: "中线（11~126 日）",
  long: "长线（>126 日）",
  unknown: "未知",
};

interface InsightTab {
  id: string;
  label: string;
  bucket?: string;
  flag?: string;
  hasReason?: string;
}

/** 设计 §7.1 的 Tabs：每个 Tab 只映射到既有筛选参数，不额外计算。 */
const TABS: InsightTab[] = [
  { id: "", label: "回合表现" },
  { id: "position", label: "中线", bucket: "position" },
  { id: "addon", label: "抄底", flag: "addon_decline" },
  { id: "sellfly", label: "卖飞", flag: "sold_too_early" },
  { id: "reason", label: "待补理由", hasReason: "false" },
];

/** 泛型对象在 SDK 里是 `{[key: string]: unknown}`，此处只做显示层收窄（不改口径）。 */
interface AddonSummary {
  count: number;
  declined_after_addon: number;
  declined_pct?: string | null;
  avg_dip_pct?: string | null;
  /** 形如 "3/9"：avg_dip_pct 的有效样本占全部加仓笔次，避免只看到百分比而误读样本量。 */
  avg_dip_coverage?: string | null;
}
interface SellFlySummary {
  sell_count: number;
  profit_take_fly: number;
  stop_loss_rebound: number;
  fly_pct?: string | null;
  missed_profit_worst_display?: string | null;
  missed_profit_end_display?: string | null;
  /** 后端给好的绝对值串 + 措辞（少赚/多赚）；前端只渲染，不判符号、不做 abs。 */
  missed_profit_worst_abs_display?: string | null;
  missed_profit_worst_label?: string | null;
  missed_profit_end_abs_display?: string | null;
  missed_profit_end_label?: string | null;
}
interface RankingSummary {
  n_sample: number;
  n_eligible: number;
  sample_status: string;
  combined_median_pct?: string | null;
  combined_avg_pct?: string | null;
  quadrant_counts?: Record<string, number>;
}
interface PendingSummary {
  forward_recompute: number;
  reason_missing: number;
}

const SAMPLE_STATUS_LABELS: Record<string, string> = {
  ok: "样本充足（≥30）",
  insufficient: "样本不足",
  no_conclusion: "样本过少，暂不下结论",
};

function text(value: string | null | undefined): string {
  return value ?? "—";
}

function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag;
}

/** 回合洞察（/rounds/insights）：事后统计，只渲染后端产出字符串（前端零口径）。 */
export function RoundInsights() {
  const [search, setSearch] = useSearchParams();
  const periodParam = search.get("period") ?? "";
  // 默认期间 = 今年全年（`YYYY` token）；补进 URL 而不是只做内存兜底，
  // 这样刷新/切走再回来仍按同一区间查询，用户选过的期间不会被默认值重置。
  const period = periodParam || currentYearToken();
  const bucket = search.get("bucket") ?? "";
  const flag = search.get("flag") ?? "";
  const hasReason = search.get("has_reason") ?? "";
  const securityCode = search.get("security_code") ?? "";
  const securityTypeParam = search.get("security_type");
  const securityTypes = useMemo(
    () => parseSecurityTypes(securityTypeParam),
    [securityTypeParam],
  );
  const sort = search.get("sort") ?? "combined";

  const [codeDraft, setCodeDraft] = useState(securityCode);
  const [page, setPage] = useState<RoundInsightsPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const setParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(search);
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete("cursor");
      setSearch(next, { replace: true });
    },
    [search, setSearch],
  );

  // URL 缺 period 时把默认年度补进去（只补空缺，不覆盖用户已选区间）。
  useEffect(() => {
    if (periodParam) return;
    const next = new URLSearchParams(search);
    next.set("period", currentYearToken());
    setSearch(next, { replace: true });
  }, [periodParam, search, setSearch]);

  const load = useCallback(
    async (cursor?: string) => {
      setLoading(true);
      setError(null);
      try {
        setPage(
          await api.rounds.insights({
            period: period || undefined,
            bucket: bucket || undefined,
            flag: flag ? [flag] : undefined,
            has_reason: hasReason === "" ? undefined : hasReason === "true",
            security_code: securityCode || undefined,
            security_type: securityTypes,
            sort,
            sort_dir: "desc",
            include_summary: true,
            limit: 50,
            cursor,
          }),
        );
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [period, bucket, flag, hasReason, securityCode, securityTypes, sort],
  );

  useEffect(() => {
    void load();
  }, [load]);

  /** 勾选/取消证券类型；至少保留一项，且仅「股票」时把 URL 参数删掉保持干净。 */
  function toggleSecurityType(value: string): void {
    const picked = securityTypes.includes(value)
      ? securityTypes.filter((item) => item !== value)
      : [...securityTypes, value];
    if (picked.length === 0) return;
    const ordered = SECURITY_TYPE_OPTIONS.map((option) => option.value).filter((item) =>
      picked.includes(item),
    );
    const isDefault = ordered.length === 1 && ordered[0] === DEFAULT_SECURITY_TYPES[0];
    setParam("security_type", isDefault ? "" : ordered.join(","));
  }

  async function refresh(): Promise<void> {
    setRefreshing(true);
    setError(null);
    setNotice("");
    try {
      const result = await api.rounds.refreshInsights(
        { period: period || null, scope: "missing" },
        newIdempotencyKey(),
      );
      if ("job_id" in result) {
        const accepted = result as JobAccepted;
        setNotice(`已提交后台刷新任务（${accepted.job_id}）；期间可继续浏览旧结果。`);
      } else {
        const stats = result as RoundInsightsRefreshOut;
        setNotice(
          `刷新完成：计算 ${stats.computed}，跳过（缓存有效）${stats.skipped}，待算 ${stats.pending}。`,
        );
      }
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setRefreshing(false);
    }
  }

  const columns: Array<DataTableColumn<RoundInsightItem>> = [
    {
      key: "security",
      header: "代码 / 名称",
      priority: 1,
      render: (row) => (
        <Link to={`/rounds/${row.round_id}`}>
          {`${row.security_code} ${row.security_name ?? ""}`.trim()}
        </Link>
      ),
    },
    { key: "close", header: "平仓日", priority: 2, render: (row) => row.close_date ?? "—" },
    { key: "holding", header: "持有交易日", priority: 2, align: "right", render: (row) => text(row.holding_trading_days?.toString()) },
    {
      key: "bucket",
      header: "持有期",
      priority: 2,
      render: (row) => BUCKET_LABELS[row.bucket] ?? row.bucket,
    },
    {
      key: "pnl",
      header: "盈亏",
      priority: 1,
      align: "right",
      render: (row) => <span className={signedClass(row.pnl)}>{text(row.pnl)}</span>,
    },
    {
      key: "return",
      header: "收益率",
      priority: 1,
      align: "right",
      render: (row) => <span className={signedClass(row.return_rate_pct)}>{text(row.return_rate_pct)}</span>,
    },
    {
      key: "buy_b3",
      header: "买入后相对基准（20 日）",
      priority: 2,
      align: "right",
      render: (row) => (
        <span className={signedClass(row.buy_trade?.forward?.["20"]?.b3_pct)}>
          {text(row.buy_trade?.forward?.["20"]?.b3_pct)}
        </span>
      ),
    },
    {
      key: "sell_b3",
      header: "卖出后相对基准（20 日）",
      priority: 2,
      align: "right",
      render: (row) => (
        <span className={signedClass(row.sell_trade?.forward?.["20"]?.b3_pct)}>
          {text(row.sell_trade?.forward?.["20"]?.b3_pct)}
        </span>
      ),
    },
    { key: "buy_reason", header: "买入理由", priority: 2, render: (row) => text(row.buy_trade?.reason_label) },
    { key: "sell_reason", header: "卖出理由", priority: 2, render: (row) => text(row.sell_trade?.reason_label) },
    {
      key: "flags",
      header: "旗标",
      priority: 3,
      render: (row) =>
        row.flags?.length ? (
          <span className="chips">
            {row.flags.map((item, index) => (
              <span key={item} className="chip">
                {flagLabel(item)}
                {index + 1 !== row.flags?.length && <span>, </span>}
              </span>
            ))}
          </span>
        ) : (
          "—"
        ),
    },
  ];

  const summary = page?.summary;
  const addon = summary?.addon as AddonSummary | undefined;
  const sellFly = summary?.sell_fly as SellFlySummary | undefined;
  const ranking = summary?.ranking as RankingSummary | undefined;
  const pending = summary?.pending as PendingSummary | undefined;

  return (
    <div>
      <h1>回合洞察</h1>
      <p className="muted">
        {page?.disclaimer ??
          "以下为事后统计，仅用于个人复盘，不构成投资建议；不评价个人能力，不预测收益；" +
            "相对基准为 hs300_tr ETF 代理；缺失/公司行动显示 —，不显示 0%。"}
      </p>

      {page?.missing?.length ? (
        <div className="notice notice-warn">
          数据不完整：{page.missing.join("、")}；缺失数值显示「—」，不显示 0%。
        </div>
      ) : null}
      {notice ? <div className="notice">{notice}</div> : null}
      {error ? <ProblemView error={error} /> : null}

      <div className="row-actions" role="tablist" aria-label="回合洞察视图">
        {TABS.map((tab) => {
          const active =
            tab.id === ""
              ? bucket === "" && flag === "" && hasReason === ""
              : bucket === (tab.bucket ?? "") && flag === (tab.flag ?? "") && hasReason === (tab.hasReason ?? "");
          return (
            <button
              key={tab.id || "all"}
              className={active ? "btn" : "btn btn-ghost"}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => {
                const next = new URLSearchParams();
                if (tab.bucket) next.set("bucket", tab.bucket);
                if (tab.flag) next.set("flag", tab.flag);
                if (tab.hasReason) next.set("has_reason", tab.hasReason);
                if (period) next.set("period", period);
                // 证券类型是全局范围偏好（同 period），切 Tab 时保留
                if (securityTypeParam) next.set("security_type", securityTypeParam);
                next.delete("cursor");
                setSearch(next, { replace: true });
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <FilterBar
        ariaLabel="回合洞察筛选"
        count={page ? `共 ${page.item_total} 条` : undefined}
        actions={
          <button className="btn" type="button" onClick={() => void refresh()} disabled={refreshing}>
            {refreshing ? "刷新中…" : "刷新前瞻"}
          </button>
        }
      >
        <FilterField label="统计月份">
          <PeriodInput
            value={period}
            onChange={(value) => setParam("period", value)}
          />
        </FilterField>
        <FilterField label="持有期">
          <select
            aria-label="持有期"
            value={bucket}
            onChange={(event) => setParam("bucket", event.target.value)}
          >
            {BUCKET_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField label="旗标">
          <select
            aria-label="旗标"
            value={flag}
            onChange={(event) => setParam("flag", event.target.value)}
          >
            {FLAG_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField label="理由">
          <select
            aria-label="理由"
            value={hasReason}
            onChange={(event) => setParam("has_reason", event.target.value)}
          >
            <option value="">全部</option>
            <option value="true">已标注理由</option>
            <option value="false">未标注理由</option>
          </select>
        </FilterField>
        
        <FilterField label="证券代码">
          <input
            type="text"
            aria-label="证券代码"
            value={codeDraft}
            placeholder="如 600519"
            onChange={(event) => setCodeDraft(event.target.value)}
            onBlur={() => setParam("security_code", codeDraft)}
            onKeyDown={(event) => {
              if (event.key === "Enter") setParam("security_code", codeDraft);
            }}
          />
        </FilterField>
        <FilterField label="证券类型">
          <span className="filter-field-options">
            {SECURITY_TYPE_OPTIONS.map((option) => (
              <label key={option.value} className="check">
                <input
                  type="checkbox"
                  checked={securityTypes.includes(option.value)}
                  onChange={() => toggleSecurityType(option.value)}
                />
                {option.label}
              </label>
            ))}
          </span>
        </FilterField>
        <FilterField label="排序">
          <select
            aria-label="排序"
            value={sort}
            onChange={(event) => setParam("sort", event.target.value)}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </FilterField>
      </FilterBar>

      {summary ? (
        <div className="cards">
          <div className="card">
            <h3>持有期分布</h3>
            <HoldingCharts summary={summary} />
            <p className="muted" data-testid="fill-swing">
              配对（fill 级）{summary.fill_swing.pairs} 笔 · 胜率{" "}
              {text(summary.fill_swing.win_rate_pct)} · 平均持有{" "}
              {text(summary.fill_swing.avg_holding_trading_days)} 个交易日；统计单位为 fill，不与上方回合相加
            </p>
          </div>

          <div className="card">
            <h3>加仓与后续下跌</h3>
            {addon ? (
              <p className="muted">
                加仓 {addon.count} 次，后续下跌 {addon.declined_after_addon} 次；
                下跌占比 {text(addon.declined_pct)}；平均回撤 {text(addon.avg_dip_pct)}
                {addon.avg_dip_coverage ? `（${addon.avg_dip_coverage}）` : ""}。
              </p>
            ) : (
              <p className="muted">—</p>
            )}
            <h3 style={{ marginTop: 16 }}>卖飞</h3>
            {sellFly ? (
              <p className="muted">
                卖出 {sellFly.sell_count} 次；卖飞 {sellFly.profit_take_fly}（止盈）/{" "}
                {sellFly.stop_loss_rebound}（止损后反弹）；占比 {text(sellFly.fly_pct)}；
                最差{sellFly.missed_profit_worst_label ?? ""}{" "}
                <span className={signedClass(sellFly.missed_profit_worst_abs_display)}>
                  {text(sellFly.missed_profit_worst_abs_display)}
                </span>
                ，期末{sellFly.missed_profit_end_label ?? ""}{" "}
                <span className={signedClass(sellFly.missed_profit_end_abs_display)}>
                  {text(sellFly.missed_profit_end_abs_display)}
                </span>
                。
              </p>
            ) : (
              <p className="muted">—</p>
            )}
          </div>

          <div className="card">
            <h3>理由覆盖</h3>
            <p className="muted">
              已标注 {summary.reason_coverage.annotated} / {summary.reason_coverage.total_rounds}，
              覆盖率 {text(summary.reason_coverage.coverage_pct)}
            </p>
            {summary.reason_coverage.by_code?.length ? (
              <span className="chips">
                {summary.reason_coverage.by_code.map((row) => (
                  <span key={row.code} className="chip">
                    {row.label} {row.share_pct}
                  </span>
                ))}
              </span>
            ) : null}
          </div>

          <div className="card">
            <h3>20 日相对基准排序</h3>
            {ranking ? (
              <>
                <p className="muted">
                  样本 {ranking.n_sample} / 可排序 {ranking.n_eligible}；{SAMPLE_STATUS_LABELS[ranking.sample_status] ?? ranking.sample_status}
                </p>
                <div className="kv">
                  <span>中位数</span>
                  <strong className={signedClass(ranking.combined_median_pct)}>
                    {text(ranking.combined_median_pct)}
                  </strong>
                  <span>均值</span>
                  <strong className={signedClass(ranking.combined_avg_pct)}>
                    {text(ranking.combined_avg_pct)}
                  </strong>
                </div>
              </>
            ) : (
              <p className="muted">—</p>
            )}
            {pending ? (
              <p className="muted">
                待刷新前瞻 {pending.forward_recompute} · 缺理由 {pending.reason_missing}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}

      {page && page.item_total === 0 && !loading ? (
        <p className="muted">
          本月还没有可统计的回合。导入账单后系统会自动建立回合；也可到
          <Link to="/rounds">「回合」页</Link>手动重建。
        </p>
      ) : null}

      {page && page.items.length > 0 ? (
        <DataTable columns={columns} rows={page.items} rowKey={(row) => row.round_id} caption="回合洞察明细" />
      ) : null}

      {page && page.has_more ? (
        <Cursor
          hasMore={page.has_more}
          nextCursor={page.next_cursor ?? null}
          disabled={loading}
          onNext={() => void load(page.next_cursor ?? undefined)}
        />
      ) : null}

      {page?.assumptions?.length ? (
        <p className="muted">口径假设：{page.assumptions.join("；")}</p>
      ) : null}

      <FlagGlossary entries={page?.flag_glossary} />
    </div>
  );
}
