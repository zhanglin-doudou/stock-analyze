import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { FeaturePlaceholder } from "./ComingSoon";

afterEach(cleanup);

function renderPlaceholder(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/reports"]}>
      <FeaturePlaceholder
        title="报告"
        dependency="Period2-13 报告/导出页（依赖 Period2-09 月报）"
        description="月报与可下载报告尚未实现；此处仅预留入口。"
      />
    </MemoryRouter>,
  );
}

describe("FeaturePlaceholder 占位页", () => {
  it("FT-05 展示标题/依赖/说明，可断言 data-dependency", () => {
    renderPlaceholder();

    const card = screen.getByTestId("feature-placeholder");
    expect(card.getAttribute("data-dependency")).toBe(
      "Period2-13 报告/导出页（依赖 Period2-09 月报）",
    );
    expect(card.getAttribute("data-feature")).toBe("报告");
    expect(screen.getByRole("heading", { name: "报告" })).toBeTruthy();
    expect(screen.getByText(/月报与可下载报告尚未实现/)).toBeTruthy();
    expect(screen.getByText(/尚未完成/)).toBeTruthy();
  });

  it("FT-06 不渲染假数据/假按钮：只有「返回看板」链接", () => {
    renderPlaceholder();

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByTestId("feature-placeholder").textContent).toContain(
      "没有真实数据、没有可用的下载/构建按钮，不会触发任何 job",
    );
    expect(screen.getByRole("link", { name: "返回看板" }).getAttribute("href")).toBe("/dashboard");
  });

  it("FT-07 backTo 可覆盖（占位页回到指定页）", () => {
    render(
      <MemoryRouter>
        <FeaturePlaceholder
          title="归因"
          dependency="Period2-08 归因分析"
          description="尚未实现"
          backTo="/metrics"
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "返回看板" }).getAttribute("href")).toBe("/metrics");
  });
});
