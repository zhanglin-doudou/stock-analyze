import type { KeyboardEvent, ReactElement, ReactNode } from "react";
import "./data-table.css";

export type ColumnAlign = "left" | "right" | "center";
/**
 * 列优先级（平板降级顺序）：
 * - 1：任何宽度都保留（关键列）
 * - 2：<=1023px 隐藏
 * - 3：<=1279px 隐藏
 */
export type ColumnPriority = 1 | 2 | 3;

export interface DataTableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: ColumnAlign;
  priority?: ColumnPriority;
  width?: string;
}

export interface DataTableProps<T> {
  columns: Array<DataTableColumn<T>>;
  rows: readonly T[];
  rowKey: (row: T) => string;
  caption: string;
  emptyText?: string;
  loading?: boolean;
  loadingText?: string;
  onRowClick?: (row: T) => void;
  rowTestId?: (row: T) => string | undefined;
}

function cellClass(column: DataTableColumn<unknown>): string {
  const align = column.align ?? "left";
  return `dt-cell dt-${align} dt-p${column.priority ?? 1}`;
}

/**
 * Phase 2 · 统一数据表：粘性表头、横向滚动、列优先级、键盘行导航、空/加载态。
 * 不承担排序/分页/格式化（排序由后端、格式由后端字符串、翻页用 Cursor）。
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  emptyText = "暂无数据",
  loading = false,
  loadingText = "加载中…",
  onRowClick,
  rowTestId,
}: DataTableProps<T>): ReactElement {
  const interactive = onRowClick != null;

  function handleRowKeyDown(event: KeyboardEvent<HTMLTableRowElement>, row: T): void {
    if (!interactive) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onRowClick(row);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const sibling =
        event.key === "ArrowDown"
          ? event.currentTarget.nextElementSibling
          : event.currentTarget.previousElementSibling;
      if (sibling instanceof HTMLElement) sibling.focus();
    }
  }

  return (
    <div className="dt-wrap">
      <table className="dt" aria-rowcount={rows.length} aria-colcount={columns.length}>
        <caption className="dt-caption">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cellClass(column as DataTableColumn<unknown>)}
                style={column.width ? { width: column.width } : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td className="dt-empty" colSpan={columns.length}>
                {loadingText}
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td className="dt-empty" colSpan={columns.length}>
                {emptyText}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={rowKey(row)}
                tabIndex={interactive ? 0 : undefined}
                onClick={interactive ? () => onRowClick(row) : undefined}
                onKeyDown={interactive ? (event) => handleRowKeyDown(event, row) : undefined}
                data-testid={rowTestId?.(row)}
                className={interactive ? "dt-row is-interactive" : "dt-row"}
              >
                {columns.map((column) => (
                  <td key={column.key} className={cellClass(column as DataTableColumn<unknown>)}>
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
