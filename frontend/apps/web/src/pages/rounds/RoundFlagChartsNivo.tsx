/**
 * 回合旗标计数柱状图的 nivo 实现（由 `RoundFlagCharts` 懒加载，首屏不解析）。
 * 颜色、主题全部来自 tokens 语义色，浅/暗主题由 `useChartPalette` 跟随刷新。
 */

import { ResponsiveBar } from "@nivo/bar";

import type { ChartPalette } from "../dashboard/tradeFlagChartColors";
import type { FlagBarDatum } from "./RoundFlagCharts";

interface Props {
  data: FlagBarDatum[];
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
  };
}

export default function RoundFlagChartsNivo({ data, palette }: Props) {
  return (
    <ResponsiveBar<FlagBarDatum>
      data={data}
      keys={["count"]}
      indexBy="label"
      margin={{ top: 16, right: 8, bottom: 52, left: 36 }}
      padding={0.3}
      colors={(bar) => String(bar.data.color)}
      borderWidth={0}
      enableGridX={false}
      enableLabel
      labelSkipHeight={12}
      labelTextColor={palette.text}
      axisTop={null}
      axisRight={null}
      // 八个旗标文案较长，底部标签倾斜避免互相重叠。
      axisBottom={{ tickSize: 0, tickPadding: 6, tickRotation: -30 }}
      axisLeft={{ tickSize: 0, tickPadding: 4, tickValues: 4, format: (value) => String(value) }}
      theme={nivoTheme(palette)}
      tooltip={({ indexValue, data: row }) => (
        <div className="rfc-tooltip" role="tooltip">
          <div className="rfc-tooltip-head">{indexValue}</div>
          <div className="rfc-tooltip-row">
            <i style={{ background: row.color }} aria-hidden="true" />
            <span>{row.count} 个回合</span>
          </div>
        </div>
      )}
    />
  );
}
