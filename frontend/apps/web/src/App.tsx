import { QueryClientProvider } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { queryClient } from "./data/queryClient";
import { router } from "./router";

/**
 * Phase 1 · 应用根：QueryClientProvider + 路由。
 * `client` 可注入，便于页面/集成测试构造隔离缓存。
 */
export function App({ client = queryClient }: { client?: QueryClient } = {}) {
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
