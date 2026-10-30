/**
 * Phase 3a · 本地日期辅助。
 *
 * 约束：页面默认月份必须用**本地时区**（用户所在时区），不能用
 * `toISOString().slice(0,7)`（UTC）——否则月初 0:00–8:00（东八区）会落到上一个月。
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
