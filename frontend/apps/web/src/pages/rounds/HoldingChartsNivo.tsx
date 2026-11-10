/**
 * 持有期分布图表的 nivo 实现（由 `HoldingCharts` 懒加载，首屏不解析）。
 * 颜色、主题全部来自 tokens 语义色，浅/暗主题由 `useChartPalette` 跟随刷新。
 */

import { ResponsiveBar } from "@nivo/bar";
import { ResponsivePie } from "@nivo/pie";

import { daysX10Label } from "../../charts/geometry";
import type { ChartPalette } from "../dashboard/tradeFlagChartColors";
import type { HoldingBarDatum, HoldingPieDatum } from "./HoldingCharts";

interface BaseProps {
  palette: ChartPalette;
}

interface PieChartProps extends BaseProps {
  mode: "pie";
  data: HoldingPieDatum[];
}

interface BarChartProps extends BaseProps {
  mode: "bar";
  data: HoldingBarDatum[];
}

export type HoldingChartsNivoProps = PieChartProps | BarChartProps;

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
  };
}

function PieChart({ data, palette }: PieChartProps) {
  return (
    <ResponsivePie<HoldingPieDatum>
      data={data}
      margin={{ top: 4, right: 4, bottom: 4, left: 4 }}
      innerRadius={0.55}
      padAngle={1.5}
      cornerRadius={2}
      activeOuterRadiusOffset={3}
      colors={(datum) => String(datum.data.color)}
      borderWidth={0}
      enableArcLinkLabels={false}
      enableArcLabels={false}
      theme={nivoTheme(palette)}
      tooltip={({ datum }) => (
        <div className="hdc-tooltip" role="tooltip">
          <div className="hdc-tooltip-head">{datum.label}</div>
          <div className="hdc-tooltip-row">
            <i style={{ background: String(datum.data.color) }} aria-hidden="true" />
            <span>{datum.value} 个回合</span>
          </div>
        </div>
      )}
    />
  );
}

function BarChart({ data, palette }: BarChartProps) {
  return (
    <ResponsiveBar<HoldingBarDatum>
      data={data}
      keys={["days_x10"]}
      indexBy="label"
      margin={{ top: 8, right: 8, bottom: 28, left: 44 }}
      padding={0.35}
      colors={(bar) => String(bar.data.color)}
      borderWidth={0}
      enableGridX={false}
      enableLabel={false}
      axisTop={null}
      axisRight={null}
      axisBottom={{ tickSize: 0, tickPadding: 6 }}
      axisLeft={{
        tickSize: 0,
        tickPadding: 4,
        tickValues: 4,
        format: (value) => daysX10Label(value as number),
      }}
      theme={nivoTheme(palette)}
      tooltip={({ indexValue, data: row }) => (
        <div className="hdc-tooltip" role="tooltip">
          <div className="hdc-tooltip-head">{indexValue}</div>
          <div className="hdc-tooltip-row">
            <i style={{ background: row.color }} aria-hidden="true" />
            <span>
              平均持有 {row.display} 个交易日（{row.count} 个回合）
            </span>
          </div>
        </div>
      )}
    />
  );
}

export default function HoldingChartsNivo(props: HoldingChartsNivoProps) {
  return props.mode === "pie" ? <PieChart {...props} /> : <BarChart {...props} />;
}
