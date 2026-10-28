import { QueryClient } from "@tanstack/react-query";

/**
 * Phase 1 · 全局数据层。
 *
 * 约定（调研 README §11/§13）：
 * - 业务页面只通过 useQuery 调用 api-client，不在组件里拼 fetch；
 * - 金额/比率的聚合、排序、格式化仍由后端完成，前端只展示；
 * - 本地个人工具：staleTime 30s、关闭窗口聚焦重取、失败重试 1 次，避免打断复盘。
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}

/** 应用级单例；测试可自行 createQueryClient() 隔离。 */
export const queryClient = createQueryClient();

/**
 * Query key 约定：`[资源, 参数...]` 前缀式，便于 invalidateQueries 批量失效。
 * 迁移期未迁移页面仍用 useState/useEffect，不在本文件注册。
 */
export const queryKeys = {
  dashboard: (range: string) => ["dashboard", range] as const,
  nav: (period: string) => ["nav", period] as const,
  metrics: (period: string, exploratory: boolean) =>
    ["metrics", period, exploratory] as const,
  rounds: (filters: Record<string, string>) => ["rounds", filters] as const,
  trades: (filters: Record<string, string>) => ["trades", filters] as const,
} as const;
