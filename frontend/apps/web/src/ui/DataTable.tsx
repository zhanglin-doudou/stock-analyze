import { Fragment, useId, type KeyboardEvent, type ReactElement, type ReactNode } from "react";
import { Icon } from "./icons";
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
  /** 行内展开区：切换按钮落在首列，展开内容占满整行。 */
  expansion?: DataTableExpansion<T>;
}

export interface DataTableExpansion<T> {
  isExpanded: (row: T) => boolean;
  onToggle: (row: T) => void;
  /** 展开后渲染在整行下方的面板内容。 */
  render: (row: T) => ReactNode;
  /** 切换按钮的无障碍名称（需说明展开/收起的是哪一行）。 */
  toggleLabel: (row: T, expanded: boolean) => string;
  panelTestId?: (row: T) => string | undefined;
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
  expansion,
}: DataTableProps<T>): ReactElement {
  const interactive = onRowClick != null;
  const baseId = useId();

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
      <table
        className="dt"
        aria-rowcount={rows.length}
        aria-colcount={columns.length}
        aria-busy={loading || undefined}
      >
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
            rows.map((row, index) => {
              const expanded = expansion?.isExpanded(row) ?? false;
              const panelId = `${baseId}-panel-${index}`;
              return (
                <Fragment key={rowKey(row)}>
                  <tr
                    tabIndex={interactive ? 0 : undefined}
                    onClick={interactive ? () => onRowClick(row) : undefined}
                    onKeyDown={interactive ? (event) => handleRowKeyDown(event, row) : undefined}
                    data-testid={rowTestId?.(row)}
                    className={interactive ? "dt-row is-interactive" : "dt-row"}
                  >
                    {columns.map((column, columnIndex) => (
                      <td key={column.key} className={cellClass(column as DataTableColumn<unknown>)}>
                        {expansion && columnIndex === 0 ? (
                          <button
                            type="button"
                            className="dt-expander"
                            aria-expanded={expanded}
                            aria-controls={expanded ? panelId : undefined}
                            aria-label={expansion.toggleLabel(row, expanded)}
                            onClick={(event) => {
                              event.stopPropagation();
                              expansion.onToggle(row);
                            }}
                          >
                            <Icon name="chevron" size={12} className="dt-expander-icon" />
                          </button>
                        ) : null}
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                  {expansion && expanded ? (
                    <tr
                      className="dt-expando"
                      id={panelId}
                      data-testid={expansion.panelTestId?.(row)}
                    >
                      <td className="dt-cell dt-expando-cell" colSpan={columns.length}>
                        {expansion.render(row)}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
