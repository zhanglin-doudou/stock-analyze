import { useState } from "react";

import { ResponsiveBar } from "@nivo/bar";
import { ResponsiveLine, type LineSvgLayer } from "@nivo/line";

import type { ChartPalette } from "./tradeFlagChartColors";

/** 柱状图数据行：month + 「展示标签 → 计数」扁平字段（标签全部来自后端）。 */
export type TradeFlagBarDatum = Record<string, string | number>;

export interface TradeFlagBarKey {
  key: string;
  color: string;
}

export interface TradeFlagLineSerie {
  id: string;
  data: Array<{ x: string; y: number }>;
}

export interface TradeFlagChartsNivoProps {
  months: number;
  barData: TradeFlagBarDatum[];
  barKeys: TradeFlagBarKey[];
  lineSeries: TradeFlagLineSerie[];
  lineColors: string[];
  palette: ChartPalette;
}

function nivoTheme(palette: ChartPalette) {
  return {
    background: "transparent",
    text: { fill: palette.text, fontFamily: "var(--font-ui)", fontSize: 11 },
    axis: {
      domain: { line: { stroke: palette.line } },
      ticks: {
        line: { stroke: palette.line },
        text: { fill: palette.muted, fontSize: 11 },
      },
      legend: { text: { fill: palette.text, fontSize: 12 } },
    },
    grid: { line: { stroke: palette.grid, strokeWidth: 1 } },
    legends: { text: { fill: palette.text, fontSize: 11 } },
    crosshair: { line: { stroke: palette.muted, strokeWidth: 1 } },
  };
}

/** 24 个月时按 2 个月抽稀 x 轴标签，避免窄屏挤成一团（只抽标签，不抽数据）。 */
function tickValues(months: number, labels: string[]): string[] {
  const step = months > 12 ? 2 : 1;
  return labels.filter((_, index) => index % step === 0);
}

function BarTooltip({
  id,
  value,
  indexValue,
  color,
}: {
  id: string | number;
  value: number | string;
  indexValue: string | number;
  color: string;
}) {
  return (
    <div className="tfc-tooltip" role="tooltip">
      <div className="tfc-tooltip-head">{indexValue}</div>
      <div className="tfc-tooltip-row">
        <i className="tfc-tooltip-dot" style={{ background: color }} aria-hidden="true" />
        <span>{id}</span>
        <b>{value}</b>
      </div>
    </div>
  );
}

/** 图例/元素点击后的非选中透明度（仅视觉，不改变数据口径）。 */
const DIM_OPACITY = 0.5;

/** 柱状图选中系列的描边宽度；非选中系列用透明描边占位，不产生视觉变化。 */
const SELECTED_BAR_BORDER_WIDTH = 2;

/** 折线图默认线宽与选中线宽（选中态在默认线之上叠一条更粗的同色线）。 */
const BASE_LINE_WIDTH = 2;
const SELECTED_LINE_WIDTH = 5;

/** 选中系列的数据点直径（默认点直径 6），与加粗折线一起放大。 */
const SELECTED_POINT_DIAMETER = 10;

const HEX_DIGITS = "0123456789abcdef";

/** 十六进制字节 → 0..255；这里只做颜色解码，不解析业务数值。 */
function hexByte(pair: string): number {
  return (
    HEX_DIGITS.indexOf(pair[0].toLowerCase()) * 16 +
    HEX_DIGITS.indexOf(pair[1].toLowerCase())
  );
}

/** 给 token 解析出来的颜色附加透明度；无法解析时原样返回。 */
function withAlpha(color: string, alpha: number): string {
  const value = color.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const raw = hex[1];
    const full =
      raw.length === 3
        ? raw
            .split("")
            .map((char) => char + char)
            .join("")
        : raw;
    return `rgba(${hexByte(full.slice(0, 2))}, ${hexByte(full.slice(2, 4))}, ${hexByte(
      full.slice(4, 6),
    )}, ${alpha})`;
  }
  const rgb = /^rgba?\(([^)]+)\)$/i.exec(value);
  if (rgb) {
    const [r, g, b] = rgb[1].split(/[,\s/]+/).filter(Boolean);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}

/** 叠层只需要像素坐标与系列色，不参与任何数值换算。 */
interface LineLayerSerie {
  id: string | number;
  color: string;
  data: ReadonlyArray<{ position: { x: number; y: number } }>;
}

/**
 * 选中系列加粗：在默认折线之上再叠一条 5px 的同色线，并把该系列的数据点放大。
 * 只影响视觉，不改变点的位置与 tooltip / mesh 逻辑。
 */
function activeLineLayer(activeKey: string | null) {
  return function ActiveLineLayer({ series }: { series: readonly LineLayerSerie[] }) {
    if (activeKey === null) return null;
    const serie = series.find((item) => String(item.id) === activeKey);
    if (serie === undefined) return null;
    const d = serie.data
      .map((point, index) => `${index === 0 ? "M" : "L"} ${point.position.x} ${point.position.y}`)
      .join(" ");
    return (
      <g pointerEvents="none">
        <path d={d} fill="none" stroke={serie.color} strokeWidth={SELECTED_LINE_WIDTH} />
        {serie.data.map((point, index) => (
          <circle
            key={index}
            cx={point.position.x}
            cy={point.position.y}
            r={SELECTED_POINT_DIAMETER / 2}
            fill={serie.color}
          />
        ))}
      </g>
    );
  };
}

function HtmlLegend({
  items,
  activeKey,
  onToggle,
}: {
  items: TradeFlagBarKey[];
  activeKey: string | null;
  onToggle: (key: string) => void;
}) {
  return (
    <ul className="tfc-legend">
      {items.map((item) => {
        const dimmed = activeKey !== null && activeKey !== item.key;
        return (
          <li
            key={item.key}
            className={dimmed ? "tfc-legend-item is-dim" : "tfc-legend-item"}
          >
            <button
              type="button"
              className="tfc-legend-btn"
              aria-pressed={activeKey === item.key}
              onClick={() => onToggle(item.key)}
            >
              <i
                className="tfc-legend-dot"
                style={{ background: item.color }}
                aria-hidden="true"
              />
              <span>{item.key}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default function TradeFlagChartsNivo({
  months,
  barData,
  barKeys,
  lineSeries,
  lineColors,
  palette,
}: TradeFlagChartsNivoProps) {
  const theme = nivoTheme(palette);
  const tickLabels = barData.map((row) => String(row.month));
  const ticks = tickValues(months, tickLabels);
  const barColorByKey = new Map(barKeys.map((item) => [item.key, item.color]));
  const lineColorByKey = new Map(
    lineSeries.map((serie, index) => [serie.id, lineColors[index] ?? palette.muted]),
  );
  const lineLegend = lineSeries.map((serie, index) => ({
    key: serie.id,
    color: lineColors[index] ?? palette.muted,
  }));

  // 点击图例或图表元素：选中该系列，其余系列 0.5 透明度；再次点击同一系列取消。
  const [barActive, setBarActive] = useState<string | null>(null);
  const [lineActive, setLineActive] = useState<string | null>(null);
  const toggleBar = (key: string) => setBarActive((prev) => (prev === key ? null : key));
  const toggleLine = (key: string) => setLineActive((prev) => (prev === key ? null : key));

  const barColor = (key: string): string => {
    const base = barColorByKey.get(key) ?? palette.muted;
    return barActive !== null && barActive !== key ? withAlpha(base, DIM_OPACITY) : base;
  };
  const lineColor = (key: string): string => {
    const base = lineColorByKey.get(key) ?? palette.muted;
    return lineActive !== null && lineActive !== key ? withAlpha(base, DIM_OPACITY) : base;
  };

  // 选中系列的柱体描边用海洋蓝；非选中用透明描边占位，视觉上不出现边框。
  const barBorderColor = (bar: { data: { id: string | number } }): string =>
    barActive !== null && String(bar.data.id) === barActive ? palette.selection : "transparent";

  const lineLayers: LineSvgLayer<TradeFlagLineSerie>[] = [
    "grid",
    "markers",
    "axes",
    "areas",
    "crosshair",
    "lines",
    activeLineLayer(lineActive),
    "points",
    "slices",
    "mesh",
    "legends",
  ];

  return (
    <div className="tfc-chart-container">
      <div className="tfc-chart-block">
        <h3 className="tfc-chart-title">月度买卖状态（买侧建仓 / 加仓 · 卖侧减仓 / 清仓）</h3>
        {barData.length === 0 ? (
          <p className="dash-chart-empty" data-testid="tfc-bar-empty">
            所选区间内暂无买卖状态命中。
          </p>
        ) : (
          <>
            <div className="tfc-bar-scroll">
              <div className="tfc-chart tfc-chart-bar" style={{ minWidth: `${months * 24}px` }}>
                <ResponsiveBar
                  data={barData}
                  keys={barKeys.map((item) => item.key)}
                  indexBy="month"
                  margin={{ top: 12, right: 12, bottom: 64, left: 44 }}
                  padding={0.24}
                  groupMode="stacked"
                  valueScale={{ type: "linear" }}
                  colors={(datum) => barColor(String((datum as { id?: string | number }).id))}
                  borderWidth={SELECTED_BAR_BORDER_WIDTH}
                  borderColor={barBorderColor}
                  theme={theme}
                  onClick={(datum) => toggleBar(String(datum.id))}
                  axisBottom={{
                    tickSize: 0,
                    tickPadding: 8,
                    tickRotation: -45,
                    tickValues: ticks,
                  }}
                  axisLeft={{ tickSize: 0, tickPadding: 6, tickValues: 5 }}
                  enableGridX={false}
                  labelSkipHeight={12}
                  animate={false}
                  role="img"
                  ariaLabel="按月统计买入建仓/加仓与卖出减仓/清仓状态的叠放柱状图"
                  tooltip={({ id, value, indexValue, color }) => (
                    <BarTooltip id={id} value={value} indexValue={indexValue} color={color} />
                  )}
                />
              </div>
            </div>
            <HtmlLegend items={barKeys} activeKey={barActive} onToggle={toggleBar} />
          </>
        )}
      </div>

      <div className="tfc-chart-block">
        <h3 className="tfc-chart-title">操作分析趋势</h3>
        {lineSeries.length === 0 ? (
          <p className="dash-chart-empty">所选区间内暂无状态旗标命中。</p>
        ) : (
          <>
            <div className="tfc-chart tfc-chart-line">
              <ResponsiveLine
                data={lineSeries}
                margin={{ top: 12, right: 16, bottom: 64, left: 44 }}
                xScale={{ type: "point" }}
                yScale={{ type: "linear", min: 0, max: "auto", stacked: false }}
                colors={(series) => lineColor(String(series.id))}
                lineWidth={BASE_LINE_WIDTH}
                layers={lineLayers}
                theme={theme}
                onClick={(datum) => {
                  if ("seriesId" in datum) toggleLine(String(datum.seriesId));
                }}
                axisBottom={{
                  tickSize: 0,
                  tickPadding: 8,
                  tickRotation: -45,
                  tickValues: ticks,
                }}
                axisLeft={{ tickSize: 0, tickPadding: 6, tickValues: 5 }}
                enableGridX={false}
                animate={false}
                pointSize={6}
                pointBorderWidth={1}
                pointBorderColor={{ from: "serieColor" }}
                useMesh
                role="img"
                ariaLabel="买入与卖出状态旗标月度趋势折线图"
                tooltip={({ point }) => (
                  <BarTooltip
                    id={point.seriesId}
                    value={String(point.data.yFormatted ?? point.data.y)}
                    indexValue={String(point.data.xFormatted ?? point.data.x)}
                    color={point.seriesColor}
                  />
                )}
              />
            </div>
            <HtmlLegend items={lineLegend} activeKey={lineActive} onToggle={toggleLine} />
          </>
        )}
      </div>
    </div>
  );
}
