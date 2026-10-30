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
