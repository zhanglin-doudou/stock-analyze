/**
 * 构建期预渲染（SSG）：把 /guide 渲染为含完整内容与 SEO 元信息的静态 HTML。
 * 仅用 react-dom/server（随 react 已有），不引入第三方预渲染插件。
 *
 * 前置：先执行 `vite build`（产出 dist/guide/index.html 客户端模板）与
 *      `vite build --ssr src/guide/server.tsx --outDir dist-ssr`（产出服务端 render）。
 * 本脚本：
 *   1. renderToString 结果注入 dist/guide/index.html 的 #guide-root；
 *   2. </head> 前注入 description / OG / canonical / JSON-LD（WebSite + FAQPage）；
 *   3. 用 STOCK_PUBLIC_URL 替换 robots.txt / sitemap.xml 占位（未设置时降级，避免错误域名被索引）。
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { render, SEO } from "../dist-ssr/server.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const dist = resolve(__dirname, "..", "dist");
const publicUrl = (process.env.STOCK_PUBLIC_URL || "").replace(/\/+$/, "");

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function buildHeadMeta(base) {
  const website = { "@context": "https://schema.org", "@type": "WebSite", name: "交易复盘" };
  if (base) website.url = base;
  const faqPage = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: SEO.faqs.map((f) => ({
      "@type": "Question",
      name: f.q,
      acceptedAnswer: { "@type": "Answer", text: f.a },
    })),
  };
  const lines = [
    `    <meta name="description" content="${esc(SEO.description)}">`,
    `    <meta name="robots" content="index,follow">`,
    `    <meta property="og:type" content="website">`,
    `    <meta property="og:title" content="${esc(SEO.title)}">`,
    `    <meta property="og:description" content="${esc(SEO.description)}">`,
    `    <meta property="og:image" content="${base}/guide/bill-raw.png">`,
    `    <meta name="twitter:card" content="summary_large_image">`,
  ];
  if (base) {
    lines.push(`    <link rel="canonical" href="${base}/guide">`);
    lines.push(`    <meta property="og:url" content="${base}/guide">`);
  }
  lines.push(`    <script type="application/ld+json">${JSON.stringify(website)}</script>`);
  lines.push(`    <script type="application/ld+json">${JSON.stringify(faqPage)}</script>`);
  return lines.join("\n");
}

// 1. 注入 guide HTML（SSR 内容 + head meta）
const appHtml = render();
const guidePath = resolve(dist, "guide", "index.html");
let html = readFileSync(guidePath, "utf8");
if (!html.includes('<div id="guide-root"></div>')) {
  throw new Error("[prerender-guide] 未找到空的 #guide-root 容器，模板可能已被修改");
}
html = html.replace('<div id="guide-root"></div>', `<div id="guide-root">${appHtml}</div>`);
// 注释中也可能出现 </head> 字样，必须注入真正的 head 结束标签（最后一个）之前。
const headCloseAt = html.lastIndexOf("</head>");
if (headCloseAt === -1) throw new Error("[prerender-guide] 未找到 head 结束标签");
html = `${html.slice(0, headCloseAt)}${buildHeadMeta(publicUrl)}\n  ${html.slice(headCloseAt)}`;
writeFileSync(guidePath, html);

// 2. robots.txt：替换域名占位；未设置时移除 Sitemap 绝对行
const robotsPath = resolve(dist, "robots.txt");
if (existsSync(robotsPath)) {
  let robots = readFileSync(robotsPath, "utf8");
  if (publicUrl) {
    robots = robots.replaceAll("__STOCK_PUBLIC_URL__", publicUrl);
  } else {
    robots = robots.split("\n").filter((l) => !l.startsWith("Sitemap:")).join("\n");
  }
  writeFileSync(robotsPath, robots);
}

// 3. sitemap.xml：替换构建日期与域名（未设置时退化为相对路径）
const sitemapPath = resolve(dist, "sitemap.xml");
if (existsSync(sitemapPath)) {
  let sitemap = readFileSync(sitemapPath, "utf8");
  sitemap = sitemap.replaceAll("__BUILD_DATE__", new Date().toISOString().slice(0, 10));
  sitemap = sitemap.replaceAll("__STOCK_PUBLIC_URL__", publicUrl);
  writeFileSync(sitemapPath, sitemap);
}

console.log(
  `[prerender-guide] 完成：guide 静态 HTML 注入 ${appHtml.length} 字符；STOCK_PUBLIC_URL=${publicUrl || "(未设置，走降级)"}`,
);
