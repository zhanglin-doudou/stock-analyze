import { useEffect, useState } from "react";

/** 图表用到的语义 token（组件与图表主题只消费这些变量，不写裸色值）。 */
export interface ChartPalette {
  text: string;
  muted: string;
  grid: string;
  line: string;
  surface: string;
  /** 选中态描边色（海洋蓝，`--chart-4`），浅/暗主题各一套。 */
  selection: string;
  /** 收益正/负语义色（A 股红涨绿跌，跟随主题与 `data-trend` 偏好）。 */
  up: string;
  down: string;
  series: Record<string, string>;
}

/** 系列 code → tokens.css 变量名（买入青蓝系、卖出暖红系，同侧由深到浅）。 */
const SERIES_TOKENS: Record<string, string> = {
  buy: "--chart-buy-total",
  chase: "--chart-buy-1",
  instant_bottom: "--chart-buy-2",
  short_term_bottom: "--chart-buy-3",
  sell: "--chart-sell-total",
  stop_loss: "--chart-sell-1",
  instant_top: "--chart-sell-2",
  short_term_top: "--chart-sell-3",
  // 警示类状态旗标：独立色相（琥珀/紫/品红），不与买卖两套色系共用槽位。
  short_term_underwater: "--chart-warn-underwater",
  sell_drawdown: "--chart-warn-drawdown",
  short_term_sold_early: "--chart-warn-sold-early",
  // 柱状图互斥状态（买侧青蓝、卖侧暖红，与折线同色系但独立取槽位）。
  first_entry: "--chart-buy-0",
  addon_average: "--chart-buy-1",
  addon_dip: "--chart-buy-2",
  addon_chase: "--chart-buy-3",
  reduce_breakeven: "--chart-sell-1",
  reduce_profit: "--chart-sell-2",
  reduce_loss: "--chart-sell-3",
  // 清仓三态单独色块（玫红），仍属卖侧色系。
  full_exit_breakeven: "--chart-sell-exit-1",
  full_exit_profit: "--chart-sell-exit-2",
  full_exit_loss: "--chart-sell-exit-3",
  // 回合持有期分类（超短线/短线/中线/长线）饼图四分色：短→长由浅到深，长线用独立靛蓝。
  bucket_scalp: "--chart-buy-3",
  bucket_short: "--chart-buy-2",
  bucket_position: "--chart-buy-1",
  bucket_long: "--chart-bucket-long",
};

function readVar(style: CSSStyleDeclaration, name: string): string {
  return style.getPropertyValue(name).trim() || "currentColor";
}

function readPalette(): ChartPalette {
  const style = getComputedStyle(document.documentElement);
  const series: Record<string, string> = {};
  for (const [code, token] of Object.entries(SERIES_TOKENS)) {
    series[code] = readVar(style, token);
  }
  return {
    text: readVar(style, "--text-2"),
    muted: readVar(style, "--muted"),
    grid: readVar(style, "--chart-grid"),
    line: readVar(style, "--line"),
    surface: readVar(style, "--surface-raised"),
    selection: readVar(style, "--chart-4"),
    up: readVar(style, "--up"),
    down: readVar(style, "--down"),
    series,
  };
}

/**
 * 解析图表配色：从 tokens.css 读取实际色值，并跟随 `data-theme` 切换刷新。
 * nivo 的 SVG 属性需要具体色值（不吃 CSS 变量），因此这里只做 token → 值的桥接。
 */
export function useChartPalette(): ChartPalette {
  const [palette, setPalette] = useState<ChartPalette>(readPalette);
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setPalette(readPalette()));
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme", "data-trend"] });
    return () => observer.disconnect();
  }, []);
  return palette;
}
