import { renderToString } from "react-dom/server";
import { FAQ, GuideApp } from "./GuideApp";

/** 构建期预渲染入口：renderToString 输出未登录版本静态 HTML（SEO 友好，无 window 依赖）。 */
export function render(): string {
  return renderToString(<GuideApp />);
}

/**
 * 引导页 SEO 元数据（构建期由 scripts/prerender-guide.mjs 读取，生成 <head> meta 与 JSON-LD）。
 * FAQ 直接复用 GuideApp.tsx 中的同一份文案，供 FAQPage 结构化数据使用。
 */
export const SEO = {
  title: "交易复盘 · 导入券商对账单，自动生成持仓回合与净值分析",
  description:
    "导入华泰/中信等券商对账单，自动解析、对账、生成资产持仓、交易回合、净值曲线与 18 项行为指标，个人投资复盘工作台。",
  faqs: FAQ,
};
