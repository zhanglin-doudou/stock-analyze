import { type ReactElement } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { SectorsOverview, SectorTaxonomy } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { CaveatBanner } from "../ui/Caveat";
import { PreferenceBar } from "../charts/PreferenceBar";
import { displayValue } from "../ui/format";
import { buildStateLabel, dataStatusLabel, isDataStatus } from "../ui/status";
import { SectorTable } from "./sectors/SectorTable";
import "./sectors.css";

function asTaxonomy(raw: string | null): SectorTaxonomy {
  return raw === "concept" ? "concept" : "industry";
}

/** 只接受 1/2/3；非法值回落到 1（零口径：按字符串比较，不做数值解析）。 */
function asLevel(raw: string | null, taxonomy: SectorTaxonomy): number {
  if (taxonomy === "concept") return 0;
  if (raw === "2") return 2;
  if (raw === "3") return 3;
  return 1;
}

function StatusBadge({ status }: { status: string }): ReactElement {
  const label = isDataStatus(status) ? dataStatusLabel(status) : status;
  return (
    <span className={`dash-badge is-${status}`} data-testid="sector-status-badge">
      {label}
    </span>
  );
}

function Kpi({
  label,
  value,
  testId,
}: {
  label: string;
  value: string | null | undefined;
  testId: string;
}): ReactElement {
  return (
    <div className="dash-kpi" data-testid={testId}>
      <span className="dash-kpi-label">{label}</span>
      <strong className="num">{displayValue(value)}</strong>
    </div>
  );
}

function IndustryView({
  overview,
  onLevel,
  onExpand,
}: {
  overview: SectorsOverview;
  onLevel: (level: number) => void;
  onExpand: (code: string, level: number) => void;
}): ReactElement {
  const concentration = overview.concentration;
  const preference = overview.preference;
  const benchmark = Boolean(overview.benchmark_id && preference);
  const byCode = new Map(overview.sectors.map((row) => [row.sector_code, row]));
  const prefRows = benchmark
    ? [...(preference?.top_overweight ?? []), ...(preference?.top_underweight ?? [])]
        .map((item) => byCode.get(item.sector_code))
        .filter((row): row is NonNullable<typeof row> => row != null)
    : [];

  return (
    <div data-testid="sector-industry-view">
      <div className="dash-kpi-grid sector-kpis">
        <Kpi label="HHI" value={concentration.hhi} testId="sector-kpi-hhi" />
        <Kpi label="有效板块数" value={concentration.effective_n} testId="sector-kpi-effective-n" />
        <Kpi label="Top1" value={concentration.top1_weight} testId="sector-kpi-top1" />
        <Kpi label="Top3" value={concentration.top3_weight} testId="sector-kpi-top3" />
        <Kpi
          label="覆盖率"
          value={overview.portfolio.coverage_pct}
          testId="sector-kpi-coverage"
        />
      </div>

      {benchmark && prefRows.length > 0 ? (
        <div className="sector-pref" data-testid="sector-preference">
          <div className="dash-panel-head">
            <h2>偏好（超配 / 低配 TopN）</h2>
            <span className="dash-panel-meta">基准 {overview.benchmark_id}</span>
          </div>
          {prefRows.map((row) => (
            <PreferenceBar key={row.sector_code} row={row} />
          ))}
        </div>
      ) : (
        <p className="dash-muted" data-testid="sector-preference-fallback">
          基准权重不可用，偏好条暂不展示（可在资产/指标页检查基准数据）。
        </p>
      )}

      <div className="sector-toolbar">
        <span className="dash-kpi-label">层级</span>
        {[1, 2, 3].map((level) => (
          <button
            key={level}
            type="button"
            className={`sector-level${overview.level === level ? " is-active" : ""}`}
            onClick={() => onLevel(level)}
            aria-pressed={overview.level === level}
          >
            L{level}
          </button>
        ))}
      </div>

      <SectorTable
        rows={overview.sectors}
        taxonomy="industry"
        onExpand={(row) => onExpand(row.sector_code, row.level + 1)}
      />

      <p className="sector-foot">
        分母：已映射股票市值 / 股票总市值；基准 {overview.benchmark_as_of ?? "—"}；分类版本{" "}
        {overview.sector_dataset_version ?? "—"}；PIT 口径：仅首次同步日之后可回放。
      </p>
    </div>
  );
}

function ConceptView({ overview }: { overview: SectorsOverview }): ReactElement {
  const concept = overview.concept;
  return (
    <div data-testid="sector-concept-view">
      <div className="dash-kpi-grid sector-kpis">
        <Kpi label="覆盖率" value={concept?.coverage_pct} testId="sector-kpi-coverage" />
        <Kpi label="重叠合计" value={concept?.overlap} testId="sector-kpi-overlap" />
        <Kpi label="多标签占比" value={concept?.multi_tag_pct} testId="sector-kpi-multi-tag" />
        <Kpi label="HHI_tag" value={concept?.hhi_tag} testId="sector-kpi-hhi-tag" />
        <Kpi
          label="Top1 暴露"
          value={concept?.top1_concept?.exposure_pct}
          testId="sector-kpi-top1"
        />
      </div>

      <CaveatBanner text={concept?.caveat} />

      <SectorTable rows={overview.sectors} taxonomy="concept" />

      <p className="sector-foot">
        概念板块可多归属、暴露不可加总，也不可与行业 HHI 横向比较；覆盖率分母为股票总市值。
      </p>
    </div>
  );
}

/**
 * 板块分析页（API-087/088）：行业（互斥、可加总）+ 概念（多归属、不可加总）。
 * 深链：`/sectors?taxonomy=industry&level=1&parent=801010.SI&as_of=…`；切 Tab/层级只改 query。
 * 零口径：所有数字来自后端字符串，本页只做展示与层级/参数切换。
 */
export function Sectors(): ReactElement {
  const [params, setParams] = useSearchParams();
  const taxonomy = asTaxonomy(params.get("taxonomy"));
  const level = asLevel(params.get("level"), taxonomy);
  const parent = params.get("parent") ?? undefined;
  const asOf = params.get("as_of") ?? undefined;

  const overviewQuery = useQuery({
    queryKey: ["sectors", "overview", asOf ?? "latest", taxonomy, level, parent ?? "", "default"],
    queryFn: () =>
      api.sectors.overview({
        taxonomy,
        as_of: asOf,
        level: taxonomy === "industry" ? level : undefined,
        parent,
      }),
    placeholderData: keepPreviousData,
  });

  const update = (next: Record<string, string | undefined>): void => {
    const draft = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined || value === "") draft.delete(key);
      else draft.set(key, value);
    }
    setParams(draft, { replace: true });
  };

  if (overviewQuery.error != null) {
    return (
      <div data-testid="sectors-page">
        <h1>板块分析</h1>
        <ProblemView error={overviewQuery.error} onRetry={() => void overviewQuery.refetch()} />
      </div>
    );
  }

  const overview = overviewQuery.data;

  return (
    <div className="sectors" data-testid="sectors-page">
      <header className="dash-head">
        <div className="dash-eyebrow">分类口径 · 行业互斥 / 概念多归属</div>
        <h1 className="dash-title">板块分析</h1>
        <p className="dash-sub">看清自己的行业与概念暴露；所有数值均为后端口径，本页只做呈现与下钻。</p>
        <div className="dash-head-meta">
          <span>
            页面状态 <StatusBadge status={overview?.status ?? "missing"} />
          </span>
          {overview ? (
            <span>
              数据状态 <span className={`dash-badge is-${overview.sector_data_state}`}>{buildStateLabel(overview.sector_data_state)}</span>
            </span>
          ) : null}
          <span>数据截至 {overview?.data_as_of ?? "—"}</span>
          <span>分类截至 {overview?.sector_data_as_of ?? "—"}</span>
          <button
            className="dash-btn dash-btn-ghost"
            type="button"
            disabled={overviewQuery.isFetching}
            onClick={() => void overviewQuery.refetch()}
          >
            {overviewQuery.isFetching ? "刷新中…" : "刷新"}
          </button>
        </div>
      </header>

      <div className="sector-tabs" data-testid="sector-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={taxonomy === "industry"}
          className={`sector-tab${taxonomy === "industry" ? " is-active" : ""}`}
          onClick={() => update({ taxonomy: "industry", level: "1", parent: undefined })}
        >
          行业板块
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={taxonomy === "concept"}
          className={`sector-tab${taxonomy === "concept" ? " is-active" : ""}`}
          onClick={() => update({ taxonomy: "concept", level: undefined, parent: undefined })}
        >
          概念板块
        </button>
      </div>

      {overview && overview.missing.length > 0 ? (
        <section className="dash-panel" data-testid="sector-status">
          <div className="dash-panel-head">
            <h2>缺失与降级项</h2>
          </div>
          <ul className="dash-missing">
            {overview.missing.map((item) => (
              <li key={`${item.field}:${item.code}`} data-testid="sector-missing-item" data-code={item.code}>
                <code>{item.code}</code> <span className="dash-muted">{item.field}</span>
                <div>{item.reason}</div>
                <div className="dash-muted">建议：{item.what_to_do}</div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {parent ? (
        <p className="sector-breadcrumb">
          当前下钻：<code>{parent}</code> · L{level}
          <button
            type="button"
            className="sector-back"
            onClick={() => update({ parent: undefined, level: level > 1 ? String(level - 1) : "1" })}
          >
            返回上一级
          </button>
        </p>
      ) : null}

      {overviewQuery.isLoading && !overview ? <p className="dash-muted">加载板块数据…</p> : null}

      {overview ? (
        <section className="dash-panel">
          {taxonomy === "industry" ? (
            <IndustryView
              overview={overview}
              onLevel={(next) => update({ level: String(next), parent: undefined })}
              onExpand={(code, next) => update({ parent: code, level: String(next) })}
            />
          ) : (
            <ConceptView overview={overview} />
          )}

          {overview.others ? (
            <p className="sector-foot">
              其余板块聚合：市值 {displayValue(overview.others.market_value)} · 权重{" "}
              {displayValue(overview.others.weight_pct)} · 持仓 {overview.others.holding_count} 只
            </p>
          ) : null}
        </section>
      ) : null}

      <div className="dash-links">
        {overview ? (
          <Link to={`/dashboard`} data-testid="link-dashboard">
            返回看板
          </Link>
        ) : null}
        <span className="dash-muted">金额与比率均由后端格式化；本页不做任何数值计算。</span>
      </div>
    </div>
  );
}
