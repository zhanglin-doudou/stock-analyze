/**
 * 回合旗标计数柱状图（`/rounds/insights`「加仓与后续下跌」卡片末尾）。
 *
 * 数据来自后端 `summary.flag_round_counts`：接口是游标分页的（默认 `limit=50`），
 * 前端只能看到当前页 `items`，累加会少计全期数量，所以计数必须由后端在全量
 * 筛选集上聚合。这里只做展示投影：
 * - 覆盖 v1.2 词表全部 8 个回合旗标，每类一个图例，不按次数拆开；
 * - 同一回合只计一次（后端按 `items[].flags` 判定，不累加次数）；
 * - 数据质量/缺口三态（缺理由/前瞻待算/公司行动）是查询别名，不进本图；
 * - 颜色走 tokens 语义色，浅/暗主题由 `useChartPalette` 跟随刷新。
 * nivo 图表懒加载：首屏不解析 `@nivo/bar`。
 */

import { lazy, Suspense, type ReactElement } from "react";
import { useChartPalette, type ChartPalette } from "../dashboard/tradeFlagChartColors";
import "./round-flag-charts.css";

/** 后端 `flag_round_counts` 的显示层收窄（SDK 里是可选数组）。 */
export interface FlagRoundCountLike {
  flag: string;
  count: number;
}

/** 柱状图数据行：柱高就是回合数，颜色来自 tokens。 */
export interface FlagBarDatum {
  id: string;
  label: string;
  count: number;
  color: string;
  /** nivo `BarDatum` 要求可索引的扁平字段。 */
  [key: string]: string | number;
}

/** 覆盖 v1.2 词表全部 item_flag；顺序与后端 `ITEM_FLAG_CODES` 一致。 */
const FLAG_BAR_META: Record<string, { label: string; series: string }> = {
  bottom_buy: { label: "抄底", series: "round_bottom_buy" },
  stop_loss_sold_early: { label: "止损卖飞", series: "round_stop_loss_sold_early" },
  profit_sold_early: { label: "止盈卖飞", series: "round_profit_sold_early" },
  drawdown: { label: "收益回撤", series: "round_drawdown" },
  intraday_t: { label: "日内做T", series: "round_intraday_t" },
  swing_t: { label: "波段做T", series: "round_swing_t" },
  intraday_rebalance_out: { label: "日内调仓卖出", series: "round_intraday_rebalance_out" },
  intraday_rebalance_in: { label: "日内调仓买入", series: "round_intraday_rebalance_in" },
};

const RoundFlagChartsNivo = lazy(() => import("./RoundFlagChartsNivo"));

export function buildFlagRoundBars(
  rows: FlagRoundCountLike[],
  palette: ChartPalette,
): FlagBarDatum[] {
  return rows.flatMap((row) => {
    const meta = FLAG_BAR_META[row.flag];
    if (!meta) return [];
    return [
      {
        id: row.flag,
        label: meta.label,
        count: row.count,
        color: palette.series[meta.series],
      },
    ];
  });
}

export function RoundFlagCharts({ rows }: { rows: FlagRoundCountLike[] }): ReactElement {
  const palette = useChartPalette();
  const bars = buildFlagRoundBars(rows, palette);

  // 全部旗标计数为 0（或后端未返回该字段）时不给空坐标轴，直接说明区间内没有命中。
  if (bars.length === 0 || bars.every((item) => item.count === 0)) {
    return (
      <p className="rfc-empty" data-testid="flag-round-charts">
        所选区间内没有任何回合命中旗标。
      </p>
    );
  }

  return (
    <div className="rfc" data-testid="flag-round-charts">
      <p className="muted" data-testid="flag-round-metrics">
        {bars.map((item) => `${item.label} ${item.count} 个回合`).join("；")}。
      </p>
      <div className="rfc-bar" role="img" aria-label="各回合旗标下的回合个数柱状图">
        <Suspense fallback={<p className="rfc-fallback">图表加载中…</p>}>
          <RoundFlagChartsNivo data={bars} palette={palette} />
        </Suspense>
      </div>
      <ul className="rfc-legend" data-testid="flag-round-legend">
        {bars.map((item) => (
          <li key={item.id}>
            <i style={{ background: item.color }} aria-hidden="true" />
            <span>{item.label}</span> <strong>{item.count}</strong> 个
          </li>
        ))}
      </ul>
      <p className="rfc-note muted">
        同一回合只计一次；止损卖飞 / 止盈卖飞按账单级 5 个交易日末收盘判定，与卖飞汇总的
        20 日（仅平仓笔）口径不同。
      </p>
    </div>
  );
}
