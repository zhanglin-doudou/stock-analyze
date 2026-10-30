/**
 * Phase 3a · 本地日期辅助。
 *
 * 约束：页面默认月份必须用**本地时区**（用户所在时区），不能用
 * `toISOString().slice(0,7)`（UTC）——否则月初 0:00–8:00（东八区）会落到上一个月。
 *
 * 期间 token（与后端同一约定，零口径：只拼字符串，不做数值换算）：
 * - `YYYY-MM` 月；`YYYY` 年；`YYYY-MM-DD..YYYY-MM-DD` 自定义闭区间。
 */
export function currentMonth(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return `${year}-${month}`;
}

/** 最近一个完整自然月（P0-4：指标页默认月份，避免展示进行中的当月）。 */
export function latestCompletedMonth(now: Date = new Date()): string {
  return currentMonth(new Date(now.getFullYear(), now.getMonth() - 1, 1));
}

/** 本地今天（`YYYY-MM-DD`）；看板年度区间终点用。 */
export function currentDate(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/** 今年（`YYYY`）：指标页/看板默认年度口径（低频操作者月度样本不足）。 */
export function currentYearToken(now: Date = new Date()): string {
  return `${now.getFullYear()}`;
}

export type PeriodKind = "month" | "year" | "custom";

/** 字符串级判定期间种类，不做任何取值换算。 */
export function periodKind(value: string): PeriodKind {
  if (value.includes("..")) return "custom";
  if (value.length === 4) return "year";
  return "month";
}

/** 自定义闭区间 token（ISO 日期串比较即可判大小）。 */
export function toRangeToken(from: string, to: string): string {
  return `${from}..${to}`;
}

/** 取 token 的年份部分（月/年/自定义都能取）。 */
export function periodYear(value: string): string {
  return value.slice(0, 4);
}
