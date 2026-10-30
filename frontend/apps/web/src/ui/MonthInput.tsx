import { useMemo, type ReactElement } from "react";
import "./month-input.css";

export interface MonthInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  "aria-label"?: string;
  min?: string;
  max?: string;
}

/**
 * 月份选择：优先原生 `input[type=month]`；iPadOS Safari 不支持该类型时降级为
 * 「年 + 月」两个 select。两条路径输出同一 `YYYY-MM`，不改变页面数据流。
 */
export function supportsMonthInput(): boolean {
  if (typeof document === "undefined") return false;
  const input = document.createElement("input");
  input.type = "month";
  return input.type === "month";
}

export function MonthInput({
  value,
  onChange,
  disabled,
  "aria-label": ariaLabel = "月份",
  min,
  max,
}: MonthInputProps): ReactElement {
  const supported = useMemo(supportsMonthInput, []);
  const [year = "", month = ""] = value.split("-");

  if (supported) {
    return (
      <input
        type="month"
        className="month-input"
        value={value}
        min={min}
        max={max}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  const currentYear = new Date().getFullYear();
  const years = Array.from({ length: 12 }, (_, index) => currentYear - 8 + index);
  const months = Array.from({ length: 12 }, (_, index) => String(index + 1).padStart(2, "0"));

  function update(nextYear: string, nextMonth: string): void {
    if (!nextYear || !nextMonth) return;
    onChange(`${nextYear}-${nextMonth}`);
  }

  return (
    <span className="month-fallback">
      <select
        aria-label="年份"
        className="month-select"
        value={year || String(currentYear)}
        disabled={disabled}
        onChange={(event) => update(event.target.value, month || "01")}
      >
        {years.map((item) => (
          <option key={item} value={String(item)}>
            {item} 年
          </option>
        ))}
      </select>
      <select
        aria-label="月份"
        className="month-select"
        value={month || "01"}
        disabled={disabled}
        onChange={(event) => update(year || String(currentYear), event.target.value)}
      >
        {months.map((item) => (
          <option key={item} value={item}>
            {item.replace(/^0/, "")} 月
          </option>
        ))}
      </select>
    </span>
  );
}
