import type { ReactElement, ReactNode } from "react";
import "./filter-bar.css";

export interface FilterFieldProps {
  /** 字段标签文案（与既有页面保持一致）。 */
  label: string;
  children: ReactNode;
}

/**
 * 筛选字段：标签 + 控件。标签只做视觉呈现，控件的可访问名由自身的
 * `aria-label` 提供（如 `MonthInput` 默认「月份」、`PeriodInput` 默认「期间」）。
 */
export function FilterField({ label, children }: FilterFieldProps): ReactElement {
  return (
    <div className="filter-field">
      <span className="filter-field-label">{label}</span>
      {children}
    </div>
  );
}

export interface FilterBarProps {
  /** 无障碍分组名（屏幕阅读器朗读，如「回合筛选」）。 */
  ariaLabel: string;
  /** 筛选字段（通常为 `<FilterField/>`）；为空时只渲染操作区。 */
  children?: ReactNode;
  /** 主操作（应用查询 / 构建 / 刷新 / 快捷入口）。 */
  actions?: ReactNode;
  /** 重置回调；提供时在操作区末尾渲染统一的重置按钮。 */
  onReset?: () => void;
  /** 重置按钮文案，默认「重置」。 */
  resetLabel?: string;
  /** 结果计数（后端字符串或条数），`aria-live="polite"` 播报。 */
  count?: ReactNode;
  /** 结构化说明（保留既有的口径说明文案）。 */
  note?: ReactNode;
}

/**
 * 统一筛选区：字段网格 + 操作行 + 结果计数 + 说明。
 * 全 token 着色；窄屏自动折行（minmax(min(180px, 100%), 1fr)）不溢出。
 */
export function FilterBar({
  ariaLabel,
  children,
  actions,
  onReset,
  resetLabel = "重置",
  count,
  note,
}: FilterBarProps): ReactElement {
  const hasActions = actions != null || onReset != null;

  return (
    <section className="filter-bar" role="group" aria-label={ariaLabel}>
      {children != null ? <div className="filter-bar-fields">{children}</div> : null}
      {hasActions ? (
        <div className="filter-bar-actions">
          {actions}
          {onReset != null ? (
            <button className="btn btn-ghost filter-bar-reset" type="button" onClick={onReset}>
              {resetLabel}
            </button>
          ) : null}
        </div>
      ) : null}
      {count != null ? (
        <p className="filter-bar-count" role="status" aria-live="polite">
          {count}
        </p>
      ) : null}
      {note != null ? <div className="filter-bar-note">{note}</div> : null}
    </section>
  );
}
