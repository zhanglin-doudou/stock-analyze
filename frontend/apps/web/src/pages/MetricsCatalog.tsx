import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { MetricCatalog, MetricCatalogItem } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";
import { domainLabel } from "./Metrics";

const DIRECTION_LABELS: Record<string, string> = {
  higher_better: "高好",
  lower_better: "低好",
  neutral: "中性",
};

const DOMAIN_ORDER = ["perf", "timing", "behavior", "selection", "attribution"];

function bandText(item: MetricCatalogItem): string {
  if (!item.bands || item.bands.length === 0) return "—";
  return item.bands
    .map((band) => `${band.label} [${band.from ?? "-∞"}, ${band.to ?? "+∞"})`)
    .join(" / ");
}

function thresholdText(item: MetricCatalogItem): string {
  if (item.min_trading_days > 0) return `≥${item.min_trading_days} 个交易日`;
  return `n≥${item.min_n}`;
}

/** 指标口径说明书（/metrics/catalog，API-086）：18 指标定义/方向/窗口/经验区间。 */
export function MetricsCatalog() {
  const [catalog, setCatalog] = useState<MetricCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setCatalog(await api.metrics.catalog());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(() => {
    const items = catalog?.items ?? [];
    return [...items].sort((a, b) => {
      const domain = DOMAIN_ORDER.indexOf(a.domain) - DOMAIN_ORDER.indexOf(b.domain);
      if (domain !== 0) return domain;
      return a.metric_id.localeCompare(b.metric_id);
    });
  }, [catalog]);

  const columns: Array<DataTableColumn<MetricCatalogItem>> = [
    {
      key: "metric_id",
      header: "指标",
      render: (row) => (
        <>
          <strong>{row.display_name}</strong>
          <span className="muted"> {row.metric_id}</span>
        </>
      ),
    },
    { key: "domain", header: "域", render: (row) => domainLabel(row.domain) },
    { key: "direction", header: "方向", render: (row) => DIRECTION_LABELS[row.direction] ?? row.direction },
    { key: "threshold", header: "样本门槛", render: (row) => thresholdText(row) },
    { key: "bands", header: "经验区间", render: (row) => <span className="muted">{bandText(row)}</span> },
    {
      key: "meaning",
      header: "含义 / 算法",
      render: (row) => (
        <>
          <div>{row.meaning}</div>
          <div className="muted">{row.method}</div>
        </>
      ),
    },
    {
      key: "action",
      header: "建议 / 注意",
      render: (row) => (
        <>
          {row.what_to_do ? <div>{row.what_to_do}</div> : null}
          {row.caveat ? <div className="muted">{row.caveat}</div> : null}
        </>
      ),
    },
    { key: "version", header: "口径版本", render: (row) => <span className="muted">v{row.definition_version}</span> },
  ];

  return (
    <div className="detail-page">
      <header className="detail-head">
        <Link className="detail-back" to="/metrics">
          ← 返回指标
        </Link>
        <div className="detail-eyebrow">指标 · 口径说明书</div>
        <h1 className="detail-title">指标口径说明</h1>
        <p className="detail-sub">
          18 个指标的定义、方向、窗口与经验区间；经验区间仅为方向提示，非权威阈值。数值由后端计算，本页只展示。
        </p>
      </header>

      <div className="detail-toolbar">
        <div className="row-actions">
          <button className="btn btn-ghost" type="button" disabled={loading} onClick={() => void load()}>
            刷新
          </button>
        </div>
      </div>

      <ProblemView error={error} onRetry={() => void load()} />
      <DataTable
        caption="指标口径说明书"
        columns={columns}
        rows={rows}
        rowKey={(row) => row.metric_id}
        loading={loading && rows.length === 0}
        emptyText="暂无指标口径数据。"
      />
    </div>
  );
}
