/**
 * Phase 1 · 图表几何投影（零口径唯一白名单模块）。
 *
 * 允许在这里做的：把后端已格式化的展示字符串（"1.28400000"）或归一化数值
 * 映射为 SVG 像素坐标 / path 命令 / 命中测试。
 * 禁止在这里做的：求和、平均、收益率、回撤、权重、排序、四舍五入等业务口径运算。
 * 业务聚合一律由后端完成（Phase 2 起走 GET /views/*）。
 *
 * 门禁：`src/charts/zero-caliber.test.ts` 扫描全 src，只豁免本文件与测试；
 * 本文件也不得 import 业务 api 类型，只接受 primitive。
 */

export interface ChartPoint {
  x: number;
  y: number;
}

export interface NavSeriesPoint {
  /** x 轴展示标签，如 "26-04" */
  label: string;
  /** 后端格式化字符串，如 "1.04000000" */
  value: string;
}

export interface ChartPadding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface NavChartGeometry {
  width: number;
  height: number;
  padding: ChartPadding;
  points: ChartPoint[];
  /** 平滑折线 path d；无有效点时为 "" */
  line: string;
  /** 折线下方面积 path d；无有效点时为 "" */
  area: string;
  min: number;
  max: number;
  /** 数值 → y 坐标（供 hover/标注复用） */
  yFor: (value: number) => number;
}

const DEFAULT_PADDING: ChartPadding = { top: 16, right: 16, bottom: 24, left: 16 };

/**
 * 解析后端展示字符串为几何用数字。
 * 只接受十进制定点/百分比/千分位/货币符；其余（"—"、空串、NaN、Infinity）返回 null。
 */
export function toNumber(value: string | null | undefined): number | null {
  if (value == null) return null;
  const cleaned = value.trim().replace(/[¥￥,\s]/g, "").replace(/%$/, "");
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(cleaned)) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 线性映射；domain 退化（min===max）时返回固定中值，避免除零。 */
export function scaleLinear(
  domainMin: number,
  domainMax: number,
  rangeMin: number,
  rangeMax: number,
): (value: number) => number {
  if (domainMax === domainMin) {
    const mid = (rangeMin + rangeMax) / 2;
    return () => mid;
  }
  const ratio = (rangeMax - rangeMin) / (domainMax - domainMin);
  return (value: number) => rangeMin + (value - domainMin) * ratio;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/**
 * Catmull-Rom → 三次贝塞尔平滑折线。
 * 0 点返回 ""；1 点返回 M；2 点退化直线；其余按 tension 平滑。
 */
export function smoothPath(points: readonly ChartPoint[], tension = 0.5): string {
  if (points.length === 0) return "";
  const first = points[0];
  if (points.length === 1) return `M ${round1(first.x)} ${round1(first.y)}`;
  if (points.length === 2) {
    return `M ${round1(first.x)} ${round1(first.y)} L ${round1(points[1].x)} ${round1(points[1].y)}`;
  }
  let d = `M ${round1(first.x)} ${round1(first.y)}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = i > 0 ? points[i - 1] : points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = i + 2 < points.length ? points[i + 2] : p2;
    const c1x = p1.x + ((p2.x - p0.x) / 6) * tension;
    const c1y = p1.y + ((p2.y - p0.y) / 6) * tension;
    const c2x = p2.x - ((p3.x - p1.x) / 6) * tension;
    const c2y = p2.y - ((p3.y - p1.y) / 6) * tension;
    d += ` C ${round1(c1x)} ${round1(c1y)}, ${round1(c2x)} ${round1(c2y)}, ${round1(p2.x)} ${round1(p2.y)}`;
  }
  return d;
}

/** 折线 + 基线闭合为面积 path。 */
export function areaPath(points: readonly ChartPoint[], baselineY: number, tension = 0.5): string {
  if (points.length === 0) return "";
  const first = points[0];
  const last = points[points.length - 1];
  const line = smoothPath(points, tension);
  return `${line} L ${round1(last.x)} ${round1(baselineY)} L ${round1(first.x)} ${round1(baselineY)} Z`;
}

export interface NavChartOptions {
  width: number;
  height: number;
  padding?: Partial<ChartPadding>;
  /** 归一化下界/上界；不传则取数据 min/max */
  domain?: [number, number];
}

/**
 * 净值序列 → SVG 几何。x 按序号等距（月度采样），y 轴反向（值大 → y 小）。
 * 返回完整 path 与 yFor，调用方只负责画，不再做任何算术。
 */
export function buildNavChart(
  series: readonly NavSeriesPoint[],
  options: NavChartOptions,
): NavChartGeometry {
  const padding: ChartPadding = { ...DEFAULT_PADDING, ...options.padding };
  const { width, height } = options;
  const values = series
    .map((point) => toNumber(point.value))
    .filter((value): value is number => value != null);

  const empty: NavChartGeometry = {
    width,
    height,
    padding,
    points: [],
    line: "",
    area: "",
    min: 0,
    max: 0,
    yFor: () => height - padding.bottom,
  };
  if (values.length === 0) return empty;

  const [domainMin, domainMax] = options.domain ?? [
    Math.min(...values),
    Math.max(...values),
  ];
  const plotLeft = padding.left;
  const plotRight = width - padding.right;
  const plotTop = padding.top;
  const plotBottom = height - padding.bottom;
  const step = series.length > 1 ? (plotRight - plotLeft) / (series.length - 1) : 0;
  const yFor = scaleLinear(domainMin, domainMax, plotBottom, plotTop);

  const points: ChartPoint[] = [];
  series.forEach((point, index) => {
    const value = toNumber(point.value);
    if (value == null) return;
    points.push({ x: round1(plotLeft + step * index), y: round1(yFor(value)) });
  });

  return {
    width,
    height,
    padding,
    points,
    line: smoothPath(points),
    area: areaPath(points, plotBottom),
    min: domainMin,
    max: domainMax,
    yFor,
  };
}

export interface SparklineGeometry {
  line: string;
  area: string;
  last: ChartPoint | null;
}

/** KPI 卡内迷你趋势线；同样只做几何映射。 */
export function buildSparkline(
  values: readonly (string | null | undefined)[],
  width: number,
  height: number,
): SparklineGeometry {
  const numbers: number[] = [];
  for (const value of values) {
    const parsed = toNumber(value);
    if (parsed != null) numbers.push(parsed);
  }
  if (numbers.length === 0) return { line: "", area: "", last: null };
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const step = numbers.length > 1 ? width / (numbers.length - 1) : 0;
  const yFor = scaleLinear(min, max, height, 0);
  const points = numbers.map((value, index) => ({
    x: round1(step * index),
    y: round1(yFor(value)),
  }));
  return {
    line: smoothPath(points),
    area: areaPath(points, height),
    last: points[points.length - 1],
  };
}
