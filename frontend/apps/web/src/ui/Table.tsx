import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
}

export interface TableProps<T> {
  columns: Array<Column<T>>;
  rows: T[];
  rowKey: (row: T) => string;
  emptyText?: string;
}

export function Table<T>({ columns, rows, rowKey, emptyText = "暂无数据" }: TableProps<T>) {
  if (rows.length === 0) return <p className="muted">{emptyText}</p>;
  return (
    <table className="table">
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key}>{column.header}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((column) => (
              <td key={column.key}>{column.render(row)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
