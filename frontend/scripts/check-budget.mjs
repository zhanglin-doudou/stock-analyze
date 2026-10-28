#!/usr/bin/env node
/**
 * Phase 4c · 首屏性能预算门禁。
 * 预算（docs/reviews/.../README.md §14）：首屏 JS ≤220KB gzip、CSS ≤30KB gzip。
 * 只检查 Vite 构建产物中的入口 chunk（index-*），页面 chunk 按路由懒加载不占首屏。
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";

const LIMITS = { js: 220 * 1024, css: 30 * 1024 };
const assetsDir = join(process.cwd(), "apps/web/dist/assets");
if (!existsSync(assetsDir)) {
  console.error(`[budget] 未找到构建产物：${assetsDir}，请先运行 pnpm -r build`);
  process.exit(1);
}

const files = readdirSync(assetsDir);
const entryJs = files.find((name) => /^index-.*\.js$/.test(name));
const entryCss = files.find((name) => /^index-.*\.css$/.test(name));

function gzipSize(name) {
  const content = readFileSync(join(assetsDir, name));
  return { raw: content.length, gzip: gzipSync(content, { level: 9 }).length };
}

let failed = false;
for (const [label, name, limit] of [
  ["JS", entryJs, LIMITS.js],
  ["CSS", entryCss, LIMITS.css],
]) {
  if (!name) {
    console.error(`[budget] 缺少入口 ${label} chunk（index-*）`);
    failed = true;
    continue;
  }
  const size = gzipSize(name);
  const ok = size.gzip <= limit;
  console.log(
    `[budget] ${label} ${name}: raw ${(size.raw / 1024).toFixed(1)}KB / gzip ${(
      size.gzip / 1024
    ).toFixed(2)}KB（预算 ${(limit / 1024).toFixed(0)}KB）${ok ? "✅" : "❌"}`,
  );
  if (!ok) failed = true;
}
if (failed) process.exit(1);
console.log("[budget] 首屏预算通过");
