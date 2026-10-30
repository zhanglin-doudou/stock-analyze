import { useEffect, useMemo, useState, type ReactElement } from "react";
import {
  currentDate,
  currentYearToken,
  periodKind,
  periodYear,
  toRangeToken,
  type PeriodKind,
} from "../lib/date";
import { MonthInput } from "./MonthInput";
import "./month-input.css";

export interface PeriodInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  "aria-label"?: string;
}

/**
 * 期间选择：年度（默认）/ 月度 / 自定义闭区间，输出与后端同一 period token
 * （`YYYY` / `YYYY-MM` / `YYYY-MM-DD..YYYY-MM-DD`）。
 *
 * 低频操作者月度样本常不足，页面默认年度；仍可切回月度或自选区间。
 */
export function PeriodInput({
  value,
  onChange,
  disabled,
  "aria-label": ariaLabel = "期间",
}: PeriodInputProps): ReactElement {
  const kind = periodKind(value);
  const [range, setRange] = useState<{ from: string; to: string }>(() => {
    if (kind !== "custom") return { from: "", to: "" };
    const [from = "", to = ""] = value.split("..");
    return { from, to };
  });

  useEffect(() => {
    if (kind !== "custom") return;
    const [from = "", to = ""] = value.split("..");
    setRange((current) => (current.from === from && current.to === to ? current : { from, to }));
  }, [kind, value]);

  const years = useMemo(() => {
    const year = new Date().getFullYear();
    return Array.from({ length: 12 }, (_, index) => `${year - 8 + index}`);
  }, []);

  function switchKind(next: PeriodKind): void {
    const year = periodYear(value) || currentYearToken();
    if (next === "year") {
      onChange(year);
      return;
    }
    if (next === "month") {
      onChange(`${year}-01`);
      return;
    }
    const from = `${year}-01-01`;
    const to = currentDate();
    setRange({ from, to });
    if (from <= to) onChange(toRangeToken(from, to));
  }

  function updateRange(next: { from: string; to: string }): void {
    setRange(next);
    if (next.from && next.to && next.from <= next.to) {
      onChange(toRangeToken(next.from, next.to));
    }
  }

  return (
    <span className="period-input" aria-label={ariaLabel}>
      <select
        aria-label="期间类型"
        className="month-select"
        value={kind}
        disabled={disabled}
        onChange={(event) => switchKind(event.target.value as PeriodKind)}
      >
        <option value="year">年度</option>
        <option value="month">月度</option>
        <option value="custom">自定义</option>
      </select>
      {kind === "month" ? (
        <MonthInput value={value} disabled={disabled} onChange={onChange} aria-label="月份" />
      ) : null}
      {kind === "year" ? (
        <select
          aria-label="年份"
          className="month-select"
          value={periodYear(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {years.map((year) => (
            <option key={year} value={year}>
              {year} 年
            </option>
          ))}
        </select>
      ) : null}
      {kind === "custom" ? (
        <span className="period-range">
          <input
            type="date"
            className="month-input"
            aria-label="开始日期"
            value={range.from}
            disabled={disabled}
            onChange={(event) => updateRange({ ...range, from: event.target.value })}
          />
          <span className="muted">至</span>
          <input
            type="date"
            className="month-input"
            aria-label="结束日期"
            value={range.to}
            disabled={disabled}
            onChange={(event) => updateRange({ ...range, to: event.target.value })}
          />
        </span>
      ) : null}
    </span>
  );
}
