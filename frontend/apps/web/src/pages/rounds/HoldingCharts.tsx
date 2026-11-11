/**
 * 持有期分布图表（`/rounds/insights` 的「持有期分布」卡片）。
 *
 * 数据全部来自后端 `summary`，前端只做展示投影：
 * - 统计块：平均持有交易日/胜率/平均盈亏/合计盈亏/回合数（`summary.swing`）；
 * - 饼图：短线/波段/趋势的回合数量（`summary.swing`，分类口径在后端）；
 * - 柱状图：亏损/保本/盈利的平均持有交易日（`summary.holding_by_pnl`，
 *   盈亏判定与「保本」容差都在后端常量里，前端不重算）。
 * nivo 图表懒加载：首屏不解析 `@nivo/pie`、`@nivo/bar`。
 */

import { lazy, Suspense, type ReactElement } from "react";
import type { RoundInsightsPage } from "@sa/api-client";
import { signedClass } from "../../lib/signed";
import { useChartPalette, type ChartPalette } from "../dashboard/tradeFlagChartColors";
import "./holding-charts.css";

type InsightsSummary = NonNullable<RoundInsightsPage["summary"]>;

/** 饼图扇区：计数来自后端，颜色来自 tokens 语义色。 */
export interface HoldingPieDatum {
  id: string;
  label: string;
  value: number;
  color: string;
}

/** 柱状图数据行：柱高用后端「交易日 × 10」定点整数，展示串也由后端给。 */
export interface HoldingBarDatum {
  label: string;
  days_x10: number;
  display: string;
  count: number;
  color: string;
  /** nivo `BarDatum` 要求可索引的扁平字段。 */
  [key: string]: string | number;
}

/** 饼图图例短文案（仅展示用；分类本身仍以后端 `bucket` 为准）。 */
const BUCKET_CHART_LABELS: Record<string, string> = {
  short: "短线",
  swing: "波段",
  trend: "趋势",
};

/** 后端未给值（null）时统一显示 `—`，不补 0。 */
function display(value: string | null | undefined): string {
  return value ?? "—";
}

const HoldingChartsNivo = lazy(() => import("./HoldingChartsNivo"));

export interface HoldingChartData {
  pie: HoldingPieDatum[];
  bars: HoldingBarDatum[];
  /** 「保本」容差展示串（后端常量，默认 "100.00" 元）。 */
  tolerance: string;
}

export function buildHoldingCharts(
  summary: InsightsSummary,
  palette: ChartPalette,
): HoldingChartData {
  const pie: HoldingPieDatum[] = [
    {
      id: "short",
      label: BUCKET_CHART_LABELS.short,
      value: summary.swing.short,
      color: palette.series.bucket_short,
    },
    {
      id: "swing",
      label: BUCKET_CHART_LABELS.swing,
      value: summary.swing.swing,
      color: palette.series.bucket_swing,
    },
    {
      id: "trend",
      label: BUCKET_CHART_LABELS.trend,
      value: summary.swing.trend,
      color: palette.series.bucket_trend,
    },
  ];
  // 无样本的分类（均值为 null）不上柱，避免以 0 冒充「平均持有 0 天」。
  const bars: HoldingBarDatum[] = summary.holding_by_pnl.classes.flatMap((row) => {
    if (row.avg_holding_trading_days_x10 == null || row.avg_holding_trading_days == null) {
      return [];
    }
    return [
      {
        label: row.label,
        days_x10: row.avg_holding_trading_days_x10,
        display: row.avg_holding_trading_days,
        count: row.count,
        color: row.code === "loss" ? palette.down : row.code === "flat" ? palette.muted : palette.up,
      },
    ];
  });
  return { pie, bars, tolerance: summary.holding_by_pnl.flat_tolerance_display };
}

export function HoldingCharts({ summary }: { summary: InsightsSummary }): ReactElement {
  const palette = useChartPalette();
  const { pie, bars, tolerance } = buildHoldingCharts(summary, palette);

  if (summary.swing.closed === 0) {
    return (
      <p className="muted" data-testid="holding-charts">
        所选区间内没有已平仓回合，暂无可绘制的持有期分布。
      </p>
    );
  }

  return (
    <div className="hdc" data-testid="holding-charts">
      {/* 统计块在宽屏与两图同排、位于左侧；窄屏单列时排在图上方（样式负责）。 */}
      <table className="hdc-stats" data-testid="holding-stats">
        <tbody>
          <tr>
            <th scope="row">平均持有交易日</th>
            <td>
              <strong>{display(summary.swing.avg_holding_trading_days)}</strong>
            </td>
          </tr>
          <tr>
            <th scope="row">胜率</th>
            <td>
              <strong>{display(summary.swing.win_rate_pct)}</strong>
            </td>
          </tr>
          <tr>
            <th scope="row">平均盈亏</th>
            <td>
              <strong className={signedClass(summary.swing.avg_pnl_display)}>
                {display(summary.swing.avg_pnl_display)}
              </strong>
            </td>
          </tr>
          <tr>
            <th scope="row">合计盈亏</th>
            <td>
              <strong className={signedClass(summary.swing.total_pnl_display)}>
                {display(summary.swing.total_pnl_display)}
              </strong>
            </td>
          </tr>
          <tr>
            <td className="hdc-stat-total" colSpan={2}>
              <span>共计</span> <strong>{summary.swing.closed}</strong> <span>个回合</span>
            </td>
          </tr>
        </tbody>
      </table>

      <div className="hdc-block">
        <p className="hdc-title">回合数量（按持有期分类）</p>
        <div className="hdc-pie" role="img" aria-label="回合数量（按持有期分类）饼图">
          <Suspense fallback={<p className="hdc-fallback">图表加载中…</p>}>
            <HoldingChartsNivo mode="pie" data={pie} palette={palette} />
          </Suspense>
        </div>
        <ul className="hdc-legend" data-testid="holding-legend">
          {pie.map((item) => (
            <li key={item.id}>
              <i style={{ background: item.color }} aria-hidden="true" />
              <span>{item.label}</span> <strong>{item.value}</strong> 个
            </li>
          ))}
        </ul>
      </div>

      <div className="hdc-block">
        <p className="hdc-title">平均持有交易日（按盈亏）</p>
        <div className="hdc-bar" role="img" aria-label="平均持有交易日（按盈亏分类）柱状图">
          {bars.length === 0 ? (
            <p className="hdc-fallback">暂无持有交易日样本。</p>
          ) : (
            <Suspense fallback={<p className="hdc-fallback">图表加载中…</p>}>
              <HoldingChartsNivo mode="bar" data={bars} palette={palette} />
            </Suspense>
          )}
        </div>
        <p className="hdc-note">盈亏判定在后端；|损益| ≤ {tolerance} 元计为保本</p>
      </div>
    </div>
  );
}
