import { ResponsiveBar } from "@nivo/bar";
import { ResponsiveLine } from "@nivo/line";
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

function HtmlLegend({ items }: { items: TradeFlagBarKey[] }) {
  return (
    <ul className="tfc-legend">
      {items.map((item) => (
        <li key={item.key} className="tfc-legend-item">
          <i className="tfc-legend-dot" style={{ background: item.color }} aria-hidden="true" />
          <span>{item.key}</span>
        </li>
      ))}
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
  const labels = barData.map((row) => String(row.month));
  const ticks = tickValues(months, labels);
  const barColors = barKeys.map((item) => item.color);
  const lineLegend = lineSeries.map((serie, index) => ({
    key: serie.id,
    color: lineColors[index] ?? palette.muted,
  }));

  return (
    <>
      <div className="tfc-chart-block">
        <h3 className="tfc-chart-title">月度买卖状态（买侧建仓 / 加仓 · 卖侧减仓 / 清仓）</h3>
        {barData.length === 0 ? (
          <p className="dash-chart-empty" data-testid="tfc-bar-empty">
            所选区间内暂无买卖状态命中。
          </p>
        ) : (
          <>
            <div className="tfc-bar-scroll">
              <div className="tfc-chart tfc-chart-bar" style={{ minWidth: `${months * 36}px` }}>
                <ResponsiveBar
                  data={barData}
                  keys={barKeys.map((item) => item.key)}
                  indexBy="month"
                  margin={{ top: 12, right: 12, bottom: 64, left: 44 }}
                  padding={0.24}
                  groupMode="stacked"
                  valueScale={{ type: "linear" }}
                  colors={barColors}
                  theme={theme}
                  axisBottom={{
                    tickSize: 0,
                    tickPadding: 8,
                    tickRotation: -45,
                    tickValues: ticks,
                  }}
                  axisLeft={{ tickSize: 0, tickPadding: 6, tickValues: 5 }}
                  enableGridX={false}
                  enableLabel={false}
                  animate={false}
                  role="img"
                  ariaLabel="按月统计买入建仓/加仓与卖出减仓/清仓状态的叠放柱状图"
                  tooltip={({ id, value, indexValue, color }) => (
                    <BarTooltip id={id} value={value} indexValue={indexValue} color={color} />
                  )}
                />
              </div>
            </div>
            <HtmlLegend items={barKeys} />
          </>
        )}
      </div>

      <div className="tfc-chart-block">
        <h3 className="tfc-chart-title">状态旗标月度趋势</h3>
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
                colors={lineColors}
                theme={theme}
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
            <HtmlLegend items={lineLegend} />
          </>
        )}
      </div>
    </>
  );
}
