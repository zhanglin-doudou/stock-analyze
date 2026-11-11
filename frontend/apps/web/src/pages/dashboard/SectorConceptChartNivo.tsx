/**
 * 概念分布柱状图（由 `SectorBlock` 懒加载，首屏不解析 nivo）。
 *
 * 数据全部来自后端 `sectors[].holdings[]`：横轴概念、纵轴持仓只数（后端 `holding_count`
 * 排序后投影为 1 只/标的），图例按证券名称展开。同一标的属于多个概念时，每个概念
 * 各计一次，合计可大于持仓数——这里不做任何去重或重算。
 */

import { ResponsiveBar } from "@nivo/bar";

import type { ChartPalette } from "./tradeFlagChartColors";

/** 一行 = 一个概念；`concept` 为轴标签，其余键为证券 symbol → 1（该概念持有该股）。 */
export type SectorConceptBarDatum = Record<string, string | number>;

export interface SectorConceptBarKey {
  /** 证券 symbol（nivo keys 唯一键）。 */
  key: string;
  /** 证券名称（图例/tooltip 展示）。 */
  label: string;
  color: string;
}

export interface SectorConceptChartNivoProps {
  data: SectorConceptBarDatum[];
  keys: SectorConceptBarKey[];
  /** 后端 `holding_count` 的最大值，仅用于生成整数刻度。 */
  maxCount: number;
  palette: ChartPalette;
}

/** 只数轴用整数刻度，避免 nivo 默认出现 0.5 只。 */
function integerTicks(max: number): number[] {
  if (max <= 5) return Array.from({ length: max + 1 }, (_, index) => index);
  const step = Math.ceil(max / 5);
  const ticks: number[] = [];
  for (let value = 0; value <= max; value += step) ticks.push(value);
  return ticks;
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

export default function SectorConceptChartNivo({
  data,
  keys,
  maxCount,
  palette,
}: SectorConceptChartNivoProps) {
  const labelByKey = new Map(keys.map((item) => [item.key, item.label]));
  const colorByKey = new Map(keys.map((item) => [item.key, item.color]));
  // 轴标签旋转 -45°，按最长标签预留底部空间并同步抬高容器，避免括注被裁切。
  const maxLabelLength = data.reduce(
    (max, datum) => Math.max(max, String(datum.concept ?? "").length),
    0,
  );
  const bottomMargin = Math.max(80, Math.ceil(maxLabelLength * 8) + 12);
  const chartHeight = 200 + bottomMargin;

  return (
    <div className="dsc-chart-block">
      <div className="dsc-chart" style={{ height: chartHeight }}>
        <ResponsiveBar<SectorConceptBarDatum>
          data={data}
          keys={keys.map((item) => item.key)}
          indexBy="concept"
          margin={{ top: 8, right: 8, bottom: bottomMargin, left: 36 }}
          padding={0.28}
          groupMode="stacked"
          colors={(bar) => colorByKey.get(String(bar.id)) ?? palette.muted}
          borderWidth={0}
          enableGridX={false}
          enableLabel={false}
          axisTop={null}
          axisRight={null}
          axisBottom={{ tickSize: 0, tickPadding: 6, tickRotation: -45 }}
          axisLeft={{
            tickSize: 0,
            tickPadding: 6,
            tickValues: integerTicks(maxCount),
            format: (value) => String(value),
          }}
          theme={nivoTheme(palette)}
          animate={false}
          role="img"
          ariaLabel="概念持仓分布柱状图（按证券名称堆叠持仓只数）"
          tooltip={({ id, indexValue, color }) => (
            <div className="dsc-tooltip" role="tooltip">
              <div className="dsc-tooltip-head">{indexValue}</div>
              <div className="dsc-tooltip-row">
                <i className="dsc-legend-dot" style={{ background: color }} aria-hidden="true" />
                <span>{labelByKey.get(String(id)) ?? String(id)}</span>
              </div>
            </div>
          )}
        />
      </div>
      <ul className="dsc-legend" data-testid="sector-concept-legend">
        {keys.map((item) => (
          <li className="dsc-legend-item" key={item.key}>
            <i className="dsc-legend-dot" style={{ background: item.color }} aria-hidden="true" />
            <span>{item.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
