import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Icon, ICON_NAMES, type IconName } from "../ui/icons";
import { NAV_GROUPS, SETTINGS_ITEM } from "./navigation";

/** 渲染成静态 SVG 字符串，用于比较「形状是否重复」（同名不同形也会被抓到）。 */
function shapeOf(name: IconName): string {
  return renderToStaticMarkup(<Icon name={name} />);
}

const GROUP_ICONS: IconName[] = NAV_GROUPS.map((group) => group.icon);
const ITEM_ICONS: IconName[] = [
  ...NAV_GROUPS.flatMap((group) => group.items.map((item) => item.icon)),
  SETTINGS_ITEM.icon,
];

describe("导航图标唯一性", () => {
  it("分组图标互不重复，且不与任何二级项/设置项图标同名", () => {
    expect(new Set(GROUP_ICONS).size).toBe(GROUP_ICONS.length);
    for (const groupIcon of GROUP_ICONS) {
      expect(ITEM_ICONS).not.toContain(groupIcon);
    }
  });

  it("二级项图标互不重复（含设置项）", () => {
    expect(new Set(ITEM_ICONS).size).toBe(ITEM_ICONS.length);
  });

  it("所有被引用的图标都在图标集里（不留悬空引用）", () => {
    for (const name of [...GROUP_ICONS, ...ITEM_ICONS]) {
      expect(ICON_NAMES).toContain(name);
    }
  });

  it("任何两个图标不得渲染出完全相同的形状（防复制粘贴撞形）", () => {
    const seen = new Map<string, IconName>();
    for (const name of ICON_NAMES) {
      const shape = shapeOf(name);
      const existing = seen.get(shape);
      expect(existing, `图标 "${name}" 与 "${existing}" 形状完全重复`).toBeUndefined();
      seen.set(shape, name);
    }
  });

  it("图标集里的每个图标都有真实绘制内容（非空 svg）", () => {
    for (const name of ICON_NAMES) {
      const markup = shapeOf(name);
      expect(markup).toContain("<svg");
      // 去掉 <svg ...> 包裹后必须还有图形元素
      expect(markup.replace(/<svg[^>]*>/, "").replace("</svg>", "")).not.toBe("");
    }
  });
});

describe("导航文案", () => {
  it("一级分组为「核心 / 分析 / 数据」", () => {
    expect(NAV_GROUPS.map((group) => group.label)).toEqual(["核心", "分析", "数据"]);
  });

  it("分组 id 与 label 对齐（core 组不再叫 overview）", () => {
    expect(NAV_GROUPS[0].id).toBe("core");
    expect(NAV_GROUPS[0].items.map((item) => item.to)).toContain("/dashboard");
  });
});
