import { useId, useState } from "react";
import type { CSSProperties, ReactElement } from "react";
import { buildNavChart, type ChartPoint } from "./geometry";

export type TrendCashflowDirection = "in" | "out" | "none";

export interface TrendSeriesPoint {
  label: string;
  /** 几何值（API-085 的归一化 v，或总资产展示串） */
  value: string;
  /** 展示用金额/标签（可选；缺省用 value） */
  display?: string;
  /** 后端 ISO 日期（tooltip / 数据表用） */
  date?: string;
  /** 当日银证转账净额（后端定点字符串；正=净转入、负=净转出） */
  cashflow?: string;
  /** 转账方向由后端判定（in/out/none），前端不解析金额符号 */
  cashflowDirection?: TrendCashflowDirection;
}

export interface TrendChartProps {
  series: readonly TrendSeriesPoint[];
  title: string;
  emptyText?: string;
  height?: number;
}

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 260;
const MARKER_TOP_THRESHOLD = 70;

interface MarkerDatum {
  point: ChartPoint;
  source: TrendSeriesPoint;
  direction: Exclude<TrendCashflowDirection, "none">;
  index: number;
}

function transferAmount(point: TrendSeriesPoint): string {
  if (point.cashflowDirection === "in") return `+${point.cashflow ?? ""}`;
  return point.cashflow ?? "—";
}

function transferText(point: TrendSeriesPoint): string {
  if (point.cashflowDirection === "in") return `净转入 ${transferAmount(point)}`;
  if (point.cashflowDirection === "out") return `净转出 ${transferAmount(point)}`;
  return "无银证转账";
}

function markerLabel(point: TrendSeriesPoint): string {
  const date = point.date ?? point.label;
  return `${date} 当日资产 ${point.display ?? point.value}，银证${transferText(point)}`;
}

/**
 * 总资产趋势图（自研 SVG；几何只在 charts/geometry.ts）。
 * - 主折线来自 API-085；cashflow/cashflow_direction 由后端给，前端只展示、不解析金额。
 * - 净转入/转出日叠加两种颜色的小圆环；hover/focus/点击显示当日资产与净转账金额。
 * - 可展开数据表作为图形替代；圆环的命中区用 HTML button 铺在 SVG 上，键盘可达。
 */
export function TrendChart({
  series,
  title,
  emptyText = "暂无日线数据（先到资产页构建资产表）。",
}: TrendChartProps): ReactElement {
  const gradientId = `trend-${useId().replace(/:/g, "")}`;
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
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
  const markers: MarkerDatum[] = [];
  series.forEach((source, index) => {
    const direction = source.cashflowDirection;
    const point = chart.points[index];
    if (!point || (direction !== "in" && direction !== "out")) return;
    markers.push({ point, source, direction, index });
  });
  const active = activeIndex == null ? null : markers.find((marker) => marker.index === activeIndex) ?? null;
  const tooltipAlign =
    active == null
      ? "center"
      : active.point.x < VIEW_WIDTH * 0.16
        ? "start"
        : active.point.x > VIEW_WIDTH * 0.84
          ? "end"
          : "center";
  const tooltipPlacement = active != null && active.point.y < MARKER_TOP_THRESHOLD ? "below" : "above";
  const tooltipTransform =
    tooltipPlacement === "below"
      ? tooltipAlign === "start"
        ? "translate(0, 12px)"
        : tooltipAlign === "end"
          ? "translate(-100%, 12px)"
          : "translate(-50%, 12px)"
      : tooltipAlign === "start"
        ? "translate(0, calc(-100% - 12px))"
        : tooltipAlign === "end"
          ? "translate(-100%, calc(-100% - 12px))"
          : "translate(-50%, calc(-100% - 12px))";
  const positionStyle = (point: ChartPoint): CSSProperties => ({
    left: `${(point.x / VIEW_WIDTH) * 100}%`,
    top: `${(point.y / VIEW_HEIGHT) * 100}%`,
  });

  return (
    <div className="dash-trend">
      {markers.length > 0 ? (
        <div className="dash-trend-top">
          <div className="dash-trend-legend">
            <span className="is-in">
              <i className="dash-trend-legend-dot" />
              银证净转入
            </span>
            <span className="is-out">
              <i className="dash-trend-legend-dot" />
              银证净转出
            </span>
            <span className="dash-trend-legend-count">{markers.length} 天有转账</span>
          </div>
          <span className="dash-trend-hint">悬停或键盘聚焦圆环查看当日资产</span>
        </div>
      ) : null}

      <div className="dash-trend-plot">
        <svg
          className="dash-trend-svg"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${title}：${series.length} 个交易日，最新 ${last?.display ?? last?.value ?? "—"}${
            markers.length > 0 ? `，${markers.length} 个银证转账日` : ""
          }`}
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
          <g aria-hidden="true">
            {markers.map((marker) => (
              <circle
                key={`ring-${marker.source.date ?? marker.source.label}`}
                className={`dash-trend-marker-ring is-${marker.direction}${
                  activeIndex === marker.index ? " is-active" : ""
                }`}
                cx={marker.point.x}
                cy={marker.point.y}
                r="3.2"
              />
            ))}
          </g>
        </svg>

        <div className="dash-trend-marker-layer">
          {markers.map((marker) => (
            <button
              key={`hit-${marker.source.date ?? marker.source.label}`}
              type="button"
              className={`dash-trend-marker-hit is-${marker.direction}`}
              style={positionStyle(marker.point)}
              aria-label={markerLabel(marker.source)}
              onMouseEnter={() => setActiveIndex(marker.index)}
              onMouseLeave={() => setActiveIndex((current) => (current === marker.index ? null : current))}
              onFocus={() => setActiveIndex(marker.index)}
              onBlur={() => setActiveIndex((current) => (current === marker.index ? null : current))}
              onClick={() => setActiveIndex(marker.index)}
            />
          ))}
        </div>

        {active ? (
          <div
            className={`dash-trend-tooltip is-${active.direction}`}
            role="tooltip"
            style={{ ...positionStyle(active.point), transform: tooltipTransform } as CSSProperties}
          >
            <span className="dash-trend-tooltip-date">{active.source.date ?? active.source.label}</span>
            <span className="dash-trend-tooltip-row">
              当日资产 <strong>{active.source.display ?? active.source.value}</strong>
            </span>
            <span className={`dash-trend-tooltip-row is-${active.direction}`}>
              银证{active.direction === "in" ? "净转入" : "净转出"}{" "}
              <strong>{transferAmount(active.source)}</strong>
            </span>
          </div>
        ) : null}
      </div>

      <div className="dash-trend-axis" aria-hidden="true">
        <span>{series[0]?.label}</span>
        <span>{series[Math.floor(series.length / 2)]?.label}</span>
        <span>{last?.label}</span>
      </div>

      <details className="dash-chart-data">
        <summary>查看数据表（{series.length} 行）</summary>
        <table>
          <caption>{title}数据表</caption>
          <thead>
            <tr>
              <th scope="col">日期</th>
              <th scope="col">总资产</th>
              <th scope="col">银证转账</th>
            </tr>
          </thead>
          <tbody>
            {series.map((point) => (
              <tr key={`${point.date ?? point.label}-${point.label}`}>
                <td>{point.date ?? point.label}</td>
                <td className="num">{point.display ?? point.value}</td>
                <td
                  className={
                    point.cashflowDirection === "in"
                      ? "is-pos"
                      : point.cashflowDirection === "out"
                        ? "is-neg"
                        : ""
                  }
                >
                  {transferText(point)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
