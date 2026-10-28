import { useId } from "react";
import type { ReactElement } from "react";
import { buildNavChart } from "./geometry";

export interface TrendSeriesPoint {
  label: string;
  value: string;
}

export interface TrendChartProps {
  series: readonly TrendSeriesPoint[];
  title: string;
  emptyText?: string;
  height?: number;
}

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 260;

/**
 * Phase 1 · 总资产趋势图（自研 SVG，几何只在 charts/geometry.ts）。
 * 数据来自后端每日资产快照；x 轴等距采样，y 轴为几何映射，不做业务运算。
 * 可访问性：role=img + 概括 aria-label；不依赖颜色单独编码。
 */
export function TrendChart({
  series,
  title,
  emptyText = "暂无日线数据（先到资产页构建资产表）。",
}: TrendChartProps): ReactElement {
  const gradientId = `trend-${useId().replace(/:/g, "")}`;
  const chart = buildNavChart(series, {
    width: VIEW_WIDTH,
    height: VIEW_HEIGHT,
    padding: { top: 20, right: 12, bottom: 28, left: 12 },
  });
  const last = series[series.length - 1];

  if (chart.points.length === 0) {
    return <p className="dash-chart-empty">{emptyText}</p>;
  }

  const lastPoint = chart.points[chart.points.length - 1];

  return (
    <div className="dash-trend">
      <svg
        className="dash-trend-svg"
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${title}：${series.length} 个交易日，最新 ${last?.value ?? "—"}`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity="0.14" />
            <stop offset="100%" stopColor="var(--chart-1)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <g stroke="var(--chart-grid)" strokeWidth="1">
          <line x1="12" y1="20" x2="988" y2="20" />
          <line x1="12" y1="140" x2="988" y2="140" strokeDasharray="1 6" />
          <line x1="12" y1="232" x2="988" y2="232" />
        </g>
        <path d={chart.area} fill={`url(#${gradientId})`} />
        <path
          d={chart.line}
          fill="none"
          stroke="var(--chart-1)"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle
          cx={lastPoint.x}
          cy={lastPoint.y}
          r="3.4"
          fill="var(--surface)"
          stroke="var(--chart-1)"
          strokeWidth="1.6"
        />
      </svg>
      <div className="dash-trend-axis" aria-hidden="true">
        <span>{series[0]?.label}</span>
        <span>{series[Math.floor(series.length / 2)]?.label}</span>
        <span>{last?.label}</span>
      </div>
    </div>
  );
}
