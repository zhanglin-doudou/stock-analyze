import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TrendChart, type TrendSeriesPoint } from "./TrendChart";

afterEach(cleanup);

const SERIES: TrendSeriesPoint[] = [
  {
    label: "06-28",
    value: "0.0000",
    display: "100,000.00",
    date: "2026-06-28",
    cashflow: "0.00",
    cashflowDirection: "none",
  },
  {
    label: "06-29",
    value: "0.5000",
    display: "110,000.00",
    date: "2026-06-29",
    cashflow: "10000.00",
    cashflowDirection: "in",
  },
  {
    label: "06-30",
    value: "1.0000",
    display: "120,000.00",
    date: "2026-06-30",
    cashflow: "-5000.00",
    cashflowDirection: "out",
  },
];

describe("TrendChart 银证净转入/转出标记", () => {
  it("只为有转账的日期渲染圆环，并保留可展开数据表", () => {
    const { container } = render(<TrendChart series={SERIES} title="总资产曲线" />);

    expect(screen.getByRole("img", { name: /总资产曲线/ })).toBeTruthy();
    expect(container.querySelectorAll(".dash-trend-marker-ring")).toHaveLength(2);
    expect(container.querySelectorAll(".dash-trend-marker-ring.is-in")).toHaveLength(1);
    expect(container.querySelectorAll(".dash-trend-marker-ring.is-out")).toHaveLength(1);
    expect(screen.getByText(/查看数据表/)).toBeTruthy();
    expect(screen.getByText("2 天有转账")).toBeTruthy();
  });

  it("hover 圆环显示当日资产与净转入/净转出金额", () => {
    render(<TrendChart series={SERIES} title="总资产曲线" />);

    const inflow = screen.getByRole("button", { name: /2026-06-29/ });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseEnter(inflow);
    const inflowTip = screen.getByRole("tooltip");
    expect(inflowTip.textContent).toContain("当日资产");
    expect(inflowTip.textContent).toContain("110,000.00");
    expect(inflowTip.textContent).toContain("净转入");
    expect(inflowTip.textContent).toContain("+10000.00");

    fireEvent.mouseLeave(inflow);
    expect(screen.queryByRole("tooltip")).toBeNull();

    const outflow = screen.getByRole("button", { name: /2026-06-30/ });
    fireEvent.focus(outflow);
    const outflowTip = screen.getByRole("tooltip");
    expect(outflowTip.textContent).toContain("净转出");
    expect(outflowTip.textContent).toContain("-5000.00");
    fireEvent.blur(outflow);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("数据表给出每日资产与净转账（含无转账日）", () => {
    render(<TrendChart series={SERIES} title="总资产曲线" />);
    fireEvent.click(screen.getByText(/查看数据表/));
    const table = screen.getByRole("table");
    expect(table.textContent).toContain("2026-06-28");
    expect(table.textContent).toContain("100,000.00");
    expect(table.textContent).toContain("净转入 +10000.00");
    expect(table.textContent).toContain("净转出 -5000.00");
  });
});
