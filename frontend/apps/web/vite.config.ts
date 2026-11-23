/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import type { Connect, Plugin } from "vite";
import react from "@vitejs/plugin-react";

/**
 * 把 `/guide` 重写到静态入口 `/guide/index.html`。
 * 生产环境由 nginx 的 `location = /guide` 完成同一映射；开发/预览服务器默认会把 `/guide`
 * 回退到 SPA 的 index.html，导致 SPA 通配路由与根路径守卫互相跳转、无限循环。
 */
function guideStaticEntry(): Plugin {
  const rewrite = (req: Connect.IncomingMessage): void => {
    if (req.url === "/guide") {
      req.url = "/guide/index.html";
    } else if (req.url?.startsWith("/guide?")) {
      req.url = `/guide/index.html${req.url.slice("/guide".length)}`;
    }
  };
  const middleware: Connect.NextHandleFunction = (req, _res, next) => {
    rewrite(req);
    next();
  };
  return {
    name: "guide-static-entry",
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

/**
 * 给 SPA 入口 index.html 注入 `canonical → /guide`。
 * 根路径 `/` 是 CSR 空壳，未登录时靠客户端整页跳转到 `/guide`；canonical 让搜索引擎把 `/`
 * 归一到 `/guide`，避免根域名被索引成一个没有正文的空页面。未设置 STOCK_PUBLIC_URL 时不注入，
 * 与引导页保持一致，避免错误域名被索引。
 */
function spaCanonicalToGuide(): Plugin {
  return {
    name: "spa-canonical-to-guide",
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        if (ctx.filename.endsWith("/guide/index.html")) return html;
        const base = (process.env.STOCK_PUBLIC_URL || "").replace(/\/+$/, "");
        if (!base) return html;
        return {
          html,
          tags: [
            {
              tag: "link",
              attrs: { rel: "canonical", href: `${base}/guide` },
              injectTo: "head",
            },
          ],
        };
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), guideStaticEntry(), spaCanonicalToGuide()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL("./index.html", import.meta.url)),
        guide: fileURLToPath(new URL("./guide/index.html", import.meta.url)),
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
  },
})