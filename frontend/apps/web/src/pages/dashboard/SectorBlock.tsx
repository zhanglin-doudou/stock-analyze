import { type ReactElement } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type {
  SectorConceptBlock,
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
        {item.direction === "over" ? "+" : item.direction === "under" ? "−" : ""}
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

/**
 * Dashboard 子块（Phase 2）：行业集中度 + 偏好 + 概念覆盖/多归属警示。
 * 数据：并行两次 API-087（industry / concept），任一失败只影响本子块。
 * 零口径：所有数字/百分比原样展示后端字符串，本组件不做任何数值计算。
 */
export function SectorBlock({ asOf, linkHref }: { asOf: string; linkHref: string }): ReactElement {
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
  const conceptBlock: SectorConceptBlock | null | undefined = conceptQuery.data?.concept;
  const pref = preferenceRows(industry);

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

      {industryQuery.isLoading && !industry ? <p className="dash-muted">加载板块数据…</p> : null}

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
          <Kpi
            label="概念覆盖度"
            value={conceptBlock?.coverage_pct}
            testId="sector-kpi-concept-coverage"
          />
        </div>
      ) : null}

      {industry ? (
        <div className="dash-sector-pref" data-testid="sector-kpi-preference">
          {pref.benchmark ? (
            <>
              <PreferenceList title="超配 Top3" items={pref.over} />
              <PreferenceList title="低配 Top3" items={pref.under} />
            </>
          ) : (
            <>
              <WeightList rows={pref.top} />
              <span className="dash-kpi-hint">基准权重缺失，偏好降级为组合权重 Top3。</span>
            </>
          )}
        </div>
      ) : null}

      <div className="dash-sector-concept" data-testid="sector-kpi-concept">
        {conceptQuery.error != null ? (
          <span className="dash-muted">概念板块暂不可用。</span>
        ) : conceptBlock ? (
          <>
            <span>
              概念覆盖 {displayValue(conceptBlock.coverage_pct)} · 多标签{" "}
              {displayValue(conceptBlock.multi_tag_pct)} · 重叠合计 {displayValue(conceptBlock.overlap)}
            </span>
            {conceptBlock.caveat ? <span className="dash-sector-caveat">{conceptBlock.caveat}</span> : null}
          </>
        ) : conceptQuery.isLoading ? (
          <span className="dash-muted">加载概念板块…</span>
        ) : (
          <span className="dash-muted">暂无概念板块数据。</span>
        )}
      </div>
    </section>
  );
}
