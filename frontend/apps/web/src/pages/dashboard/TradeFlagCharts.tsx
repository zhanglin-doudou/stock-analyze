import { lazy, Suspense, useState, type ReactElement } from "react";
import { useQuery } from "@tanstack/react-query";
import type { DashboardTradeFlagsOut } from "@sa/api-client";
import { api } from "../../client";
import { useChartPalette } from "./tradeFlagChartColors";
import type { TradeFlagBarDatum, TradeFlagBarKey, TradeFlagLineSerie } from "./TradeFlagChartsNivo";
import "./trade-flag-charts.css";

/** nivo 只在图表子组件里加载：看板首屏不背图表框架的体积。 */
const TradeFlagChartsNivo = lazy(() => import("./TradeFlagChartsNivo"));

const RANGES = [6, 12, 24] as const;
type RangeMonths = (typeof RANGES)[number];

function stateCount(data: DashboardTradeFlagsOut, monthIndex: number, code: string): number {
  // 后端每月都下发全部互斥状态码；缺失视为 0（只取值，不做任何重算）。
  const item = data.items[monthIndex];
  return item?.states.find((state) => state.code === code)?.count ?? 0;
}

export function buildCharts(
  data: DashboardTradeFlagsOut,
  palette: ReturnType<typeof useChartPalette>,
) {
  const colorOf = (code: string): string => palette.series[code] ?? palette.text;

  // 柱状图：买侧首次建仓 + 三加仓、卖侧三减仓 + 三清仓，叠放展示（不含买卖总笔数）。
  const barKeys: TradeFlagBarKey[] = data.state_series.map((serie) => ({
    key: serie.label_zh,
    color: colorOf(serie.code),
  }));

  const barData: TradeFlagBarDatum[] = data.items.map((item, index) => {
    const row: TradeFlagBarDatum = { month: item.label };
    for (const serie of data.state_series) {
      row[serie.label_zh] = stateCount(data, index, serie.code);
    }
    return row;
  });

  const lineSeries: TradeFlagLineSerie[] = data.series.map((serie) => ({
    id: serie.label_zh,
    data: data.items.map((item) => ({
      x: item.label,
      y: item.flags.find((flag) => flag.code === serie.code)?.count ?? 0,
    })),
  }));
  const lineColors = data.series.map((serie) => colorOf(serie.code));

  return { barData, barKeys, lineSeries, lineColors };
}

/**
 * 看板 · 账单旗标月度分布（API-095）。
 *
 * 数据、旗标标签、总数文案、完整月口径全部来自后端；本组件只做「月份 × 旗标」到
 * nivo 数据结构的投影与配色映射，不做任何重算/取整。范围切换直接换请求参数。
 */
export function TradeFlagCharts(): ReactElement {
  const [months, setMonths] = useState<RangeMonths>(12);
  const palette = useChartPalette();
  const query = useQuery({
    queryKey: ["dashboard", "trade-flags", months],
    queryFn: () => api.dashboard.tradeFlags(months),
    staleTime: 60_000,
  });
  const data = query.data;

  const charts = data && data.has_data ? buildCharts(data, palette) : null;

  return (
    <section className="dash-panel tfc" data-testid="trade-flag-charts">
      <div className="dash-panel-head">
        <h2>账单旗标月度分布</h2>
        <span className="dash-panel-meta">按自然月聚合 · 后端计数</span>
        <div className="dash-grow" />
        <div className="tfc-range" role="group" aria-label="时间范围">
          {RANGES.map((range) => (
            <button
              key={range}
              type="button"
              className="tfc-range-btn"
              aria-pressed={months === range}
              data-testid={`tfc-range-${range}`}
              onClick={() => setMonths(range)}
            >
              最近 {range} 个月
            </button>
          ))}
        </div>
      </div>

      {data ? (
        <p className="dash-muted tfc-basis" data-testid="tfc-basis">
          {data.start_month} → {data.end_month} · {data.months_basis}
        </p>
      ) : null}

      {query.isLoading ? (
        <p className="dash-chart-empty">加载账单旗标…</p>
      ) : query.error != null ? (
        <p className="dash-chart-empty" data-testid="tfc-error">
          账单旗标加载失败，其他看板内容不受影响。
        </p>
      ) : data == null || !data.has_data ? (
        <div className="dash-chart-empty" data-testid="tfc-empty">
          <p>最近 {months} 个完整月没有账单旗标数据。</p>
          <p className="dash-muted">
            先运行 <code>make build-trade-flags ARGS='--month YYYY-MM'</code> 生成月度旗标。
          </p>
        </div>
      ) : (
        <>
          {data.months_without_data.length > 0 ? (
            <p className="dash-muted tfc-unbuilt" data-testid="tfc-unbuilt">
              其中 {data.months_without_data.length} 个月无旗标记录（可能未构建或当月无交易）：
              {data.months_without_data.join("、")}
            </p>
          ) : null}
          <Suspense fallback={<p className="dash-chart-empty">图表加载中…</p>}>
            {charts ? (
              <TradeFlagChartsNivo
                months={months}
                barData={data.has_state_data ? charts.barData : []}
                barKeys={charts.barKeys}
                lineSeries={data.has_flag_data ? charts.lineSeries : []}
                lineColors={charts.lineColors}
                palette={palette}
              />
            ) : null}
          </Suspense>
        </>
      )}
    </section>
  );
}
