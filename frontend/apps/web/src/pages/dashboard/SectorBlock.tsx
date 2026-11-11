import { lazy, Suspense, type ReactElement } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type {
  SectorConcentration,
  SectorPreference,
  SectorPreferenceItem,
  SectorRow,
  SectorsOverview,
} from "@sa/api-client";
import { api } from "../../client";
import { ProblemView } from "../../ui/Problem";
import { displayValue, signTone } from "../../ui/format";
import { dataStatusLabel } from "../../ui/status";
import { useChartPalette } from "./tradeFlagChartColors";
import type {
  SectorConceptBarDatum,
  SectorConceptBarKey,
} from "./SectorConceptChartNivo";

/** nivo 只在图表子组件里加载：看板首屏不背图表框架的体积。 */
const SectorConceptChartNivo = lazy(() => import("./SectorConceptChartNivo"));

function toneClass(value: string | null | undefined): string {
  const sign = signTone(value);
  return sign === "none" || sign === "flat" ? "num" : `num is-${sign}`;
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
    <div className="dash-kpi" data-testid={testId}>
      <span className="dash-kpi-label">{label}</span>
      <strong className="num">{displayValue(value)}</strong>
      {hint ? <span className="dash-kpi-hint">{hint}</span> : null}
    </div>
  );
}

function PreferenceRow({ item }: { item: SectorPreferenceItem }): ReactElement {
  return (
    <span
      className="dash-sector-chip"
      data-testid={`sector-pref-${item.sector_code}`}
      data-direction={item.direction ?? "flat"}
    >
      <span className="dash-sector-chip-name">{item.sector_name}</span>
      <strong className={toneClass(item.active_weight_pct)}>
        {item.direction === "over" ? "+" : ""}
        {displayValue(item.active_weight_pct)}
      </strong>
    </span>
  );
}

function PreferenceList({
  title,
  items,
}: {
  title: string;
  items: SectorPreferenceItem[];
}): ReactElement {
  return (
    <div className="dash-sector-pref-col">
      <span className="dash-kpi-label">{title}</span>
      {items.length === 0 ? (
        <span className="dash-muted">—</span>
      ) : (
        items.map((item) => <PreferenceRow key={item.sector_code} item={item} />)
      )}
    </div>
  );
}

function WeightList({ rows }: { rows: SectorRow[] }): ReactElement {
  return (
    <div className="dash-sector-pref-col">
      <span className="dash-kpi-label">组合权重 Top3（基准缺失）</span>
      {rows.length === 0 ? (
        <span className="dash-muted">—</span>
      ) : (
        rows.map((row) => (
          <span key={row.sector_code} className="dash-sector-chip" data-testid={`sector-weight-${row.sector_code}`}>
            <span className="dash-sector-chip-name">{row.sector_name}</span>
            <strong className="num">{displayValue(row.weight_pct)}</strong>
          </span>
        ))
      )}
    </div>
  );
}

/** 把行业偏好块（基准可用 → 超配/低配；否则回落组合权重 Top3）。 */
function preferenceRows(overview: SectorsOverview | undefined): {
  benchmark: boolean;
  over: SectorPreferenceItem[];
  under: SectorPreferenceItem[];
  top: SectorRow[];
} {
  if (!overview) return { benchmark: false, over: [], under: [], top: [] };
  const pref: SectorPreference | null | undefined = overview.preference;
  if (overview.benchmark_id && pref) {
    return {
      benchmark: true,
      over: pref.top_overweight ?? [],
      under: pref.top_underweight ?? [],
      top: [],
    };
  }
  return { benchmark: false, over: [], under: [], top: overview.sectors.slice(0, 3) };
}

function concentrationHint(concentration: SectorConcentration | undefined): string | undefined {
  if (!concentration) return undefined;
  return concentration.status === "ok" ? "集中度高" : dataStatusLabel(concentration.status);
}

interface ConceptGroup {
  holdings: { symbol: string; name: string }[];
  concepts: string[];
  count: number;
}

/**
 * 概念分布图数据：持仓集合完全相同的概念合并为一根柱子，按持仓只数降序。
 * 概念数 ≥3 的合并组改用「注释N」轴标签，并在图下方集中解释；否则直接用概念名。
 * 只做后端 `holdings` 明细到 nivo 结构的投影，不按名称推断归属。
 */
function buildConceptChart(
  overview: SectorsOverview | undefined,
  palette: ReturnType<typeof useChartPalette>,
): {
  data: SectorConceptBarDatum[];
  keys: SectorConceptBarKey[];
  maxCount: number;
  annotations: { label: string; text: string }[];
} | null {
  const rows = (overview?.sectors ?? []).filter(
    (row) => row.holdings != null && row.holdings.length > 0,
  );
  if (rows.length === 0) return null;

  // 以「排序后的 symbol 集合」为签名合并概念；同一标的可出现在多个概念组（多归属）。
  const groupsByKey = new Map<string, ConceptGroup>();
  for (const row of rows) {
    const refs = row.holdings ?? [];
    const signature = refs
      .map((ref) => ref.symbol)
      .sort()
      .join("|");
    let group = groupsByKey.get(signature);
    if (group == null) {
      group = {
        holdings: refs.map((ref) => ({ symbol: ref.symbol, name: ref.name })),
        concepts: [],
        count: refs.length,
      };
      groupsByKey.set(signature, group);
    }
    group.concepts.push(row.sector_name);
  }

  const groups = [...groupsByKey.values()];
  for (const group of groups) group.concepts.sort((a, b) => a.localeCompare(b));
  groups.sort(
    (a, b) => b.count - a.count || a.concepts.join("、").localeCompare(b.concepts.join("、")),
  );

  // 仅多根柱子、且该合并组概念数 ≥3 时编号；1~2 个概念仍直接展示概念名。
  const annotations: { label: string; text: string }[] = [];
  const labels = groups.map((group) => {
    if (groups.length > 1 && group.concepts.length >= 3) {
      const label = `注释${annotations.length + 1}（${group.concepts[0] ?? ""}等）`;
      annotations.push({
        label: `注释${annotations.length + 1}`,
        text: `${group.concepts.join("、")} —— 【${group.holdings.map((ref) => ref.name).join("、")}】`,
      });
      return label;
    }
    return group.concepts.join("、");
  });

  // 证券按首次出现顺序建键；同一 symbol 可出现在多根柱子（多归属，不去重）。
  const names = new Map<string, string>();
  for (const group of groups) {
    for (const ref of group.holdings) {
      if (!names.has(ref.symbol)) names.set(ref.symbol, ref.name);
    }
  }
  const colors = Object.values(palette.series);
  const keys = [...names.entries()].map(([key, label], index) => ({
    key,
    label,
    color: (colors.length > 0 ? colors[index % colors.length] : undefined) ?? palette.text,
  }));
  const data = groups.map((group, index) => {
    const datum: SectorConceptBarDatum = { concept: labels[index] ?? "" };
    for (const key of keys) datum[key.key] = 0;
    for (const ref of group.holdings) datum[ref.symbol] = 1;
    return datum;
  });
  const maxCount = Math.max(...groups.map((group) => group.count));
  return { data, keys, maxCount, annotations };
}

/**
 * Dashboard 子块（Phase 2）：行业集中度/偏好 + 概念持仓分布。
 * 数据：并行两次 API-087（industry / concept），任一失败只影响对应列。
 * 零口径：所有数字/百分比原样展示后端字符串，本组件不做任何数值计算。
 */
export function SectorBlock({ asOf, linkHref }: { asOf: string; linkHref: string }): ReactElement {
  const palette = useChartPalette();
  const industryQuery = useQuery({
    queryKey: ["sectors", "overview", asOf, "industry", 1, "", "default"],
    queryFn: () => api.sectors.overview({ taxonomy: "industry", as_of: asOf, level: 1 }),
    staleTime: 60_000,
  });
  const conceptQuery = useQuery({
    queryKey: ["sectors", "overview", asOf, "concept", 0, "", "default"],
    queryFn: () => api.sectors.overview({ taxonomy: "concept", as_of: asOf }),
    staleTime: 60_000,
  });

  const industry = industryQuery.data;
  const concept = conceptQuery.data;
  const pref = preferenceRows(industry);
  const chart = buildConceptChart(concept, palette);

  return (
    <section
      className="dash-panel"
      data-testid="asset-sector-block"
      data-board-member="asset-overview"
    >
      <div className="dash-panel-head">
        <h2>板块配置</h2>
        <span className="dash-panel-meta">
          {industry?.as_of ?? asOf} · 行业互斥 / 概念多归属
        </span>
        <div className="dash-grow" />
        <Link className="dash-link" to={linkHref} data-testid="link-sectors">
          板块分析
        </Link>
      </div>

      {industryQuery.error != null ? <ProblemView error={industryQuery.error} /> : null}

      <div className="dash-sector-layout dash-sector-stats">
        {industryQuery.isLoading && !industry ? (
          <p className="dash-muted">加载板块数据…</p>
        ) : null}

        {industry ? (
          <div className="dash-kpi-grid" data-testid="sector-kpi-concentration">
            <Kpi
              label="行业 HHI"
              value={industry.concentration.hhi}
              testId="sector-kpi-hhi"
              hint={concentrationHint(industry.concentration)}
            />
            <Kpi
              label="有效板块数"
              value={industry.concentration.effective_n}
              testId="sector-kpi-effective-n"
              hint={`${industry.concentration.n_sectors} 个板块`}
            />
            <Kpi label="Top3 占比" value={industry.concentration.top3_weight} testId="sector-kpi-top3" />
          </div>
        ) : null}

        {industry ? (
          <div className="dash-sector-pref" data-testid="sector-kpi-preference">
            {pref.benchmark ? (
              <>
                <PreferenceList title="超配 Top5" items={pref.over} />
                <PreferenceList title="低配 Top5" items={pref.under} />
              </>
            ) : (
              <>
                <WeightList rows={pref.top} />
                <span className="dash-kpi-hint">基准权重缺失，偏好降级为组合权重 Top3。</span>
              </>
            )}
          </div>
        ) : null}
      </div>

      <div className="dash-sector-chart" data-testid="sector-concept-chart">
        <h3 className="dash-sector-chart-title">概念分布（持仓只数）</h3>
        {conceptQuery.error != null ? (
          <p className="dash-chart-empty">概念板块暂不可用。</p>
        ) : conceptQuery.isLoading && !concept ? (
          <p className="dash-chart-empty">加载概念板块…</p>
        ) : chart ? (
          <>
            <p className="dash-kpi-hint" data-testid="sector-concept-hint">
              同一标的可属于多个概念：每个概念分别计数；持仓集合完全相同的概念已合并为一根柱子。
            </p>
            <Suspense fallback={<p className="dash-chart-empty">图表加载中…</p>}>
              <SectorConceptChartNivo
                data={chart.data}
                keys={chart.keys}
                maxCount={chart.maxCount}
                palette={palette}
              />
            </Suspense>
            {chart.annotations.length > 0 ? (
              <ul className="dsc-annotations" data-testid="sector-concept-annotations">
                {chart.annotations.map((item) => (
                  <li key={item.label}>
                    <b>{item.label}</b>：{item.text}
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="dash-chart-empty">暂无概念归属的股票持仓。</p>
        )}
      </div>
    </section>
  );
}
