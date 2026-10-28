import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DataTable, type DataTableColumn } from "./DataTable";

interface Row {
  id: string;
  code: string;
  pnl: string;
}

const rows: Row[] = [
  { id: "r1", code: "600519", pnl: "650.00" },
  { id: "r2", code: "000858", pnl: "-500.00" },
];

const columns: Array<DataTableColumn<Row>> = [
  { key: "code", header: "代码", priority: 1, render: (row) => row.code },
  { key: "pnl", header: "盈亏", priority: 3, align: "right", render: (row) => row.pnl },
  { key: "actions", header: "操作", priority: 1, render: (row) => `详情 ${row.id}` },
];

afterEach(cleanup);

describe("DataTable", () => {
  it("渲染 caption/表头/行，并带列优先级类名", () => {
    render(
      <DataTable columns={columns} rows={rows} rowKey={(row) => row.id} caption="测试表格" />,
    );
    expect(screen.getByText("测试表格")).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("600519")).toBeTruthy();
    const headers = screen.getAllByRole("columnheader");
    expect(headers[0].className).toContain("dt-p1");
    expect(headers[1].className).toContain("dt-p3");
    expect(headers[1].className).toContain("dt-right");
  });

  it("空态与加载态", () => {
    const { rerender } = render(
      <DataTable
        columns={columns}
        rows={[]}
        rowKey={(row: Row) => row.id}
        caption="空表"
        emptyText="还没有数据"
      />,
    );
    expect(screen.getByText("还没有数据")).toBeTruthy();
    rerender(
      <DataTable
        columns={columns}
        rows={[]}
        rowKey={(row: Row) => row.id}
        caption="空表"
        loading
        loadingText="加载中"
      />,
    );
    expect(screen.getByText("加载中")).toBeTruthy();
  });

  it("键盘 Enter 触发 row click，ArrowDown 移动焦点", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        caption="键盘表"
        onRowClick={onRowClick}
        rowTestId={(row) => `row-${row.id}`}
      />,
    );
    const first = screen.getByTestId("row-r1");
    first.focus();
    fireEvent.keyDown(first, { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);

    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByTestId("row-r2"));
  });
});
