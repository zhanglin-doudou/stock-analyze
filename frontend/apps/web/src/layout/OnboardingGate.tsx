import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { api } from "../client";

/**
 * 新用户引导门：根路径先确认是否已有账单导入。
 * - 有账单 → 看板；
 * - 无账单 → 导入账单页（带 welcome 提示，明确“先有账单才能往下走”）；
 * - 查询失败 → 看板（不把用户困在引导里）。
 */
export function OnboardingGate() {
  const imports = useQuery({
    queryKey: ["imports", "onboarding"],
    queryFn: () => api.imports.listBatches({ limit: 1 }),
    // 用户刚导入完回到根路径时必须重新确认，不能读旧缓存。
    staleTime: 0,
    gcTime: 0,
    retry: 1,
  });

  if (imports.isLoading) {
    return (
      <div className="shell-onboarding" role="status" aria-live="polite">
        <span className="shell-onboarding-dot" aria-hidden="true" />
        正在准备你的复盘…
      </div>
    );
  }

  if (imports.isError) {
    return <Navigate to="/dashboard" replace />;
  }

  const hasImports = (imports.data?.items.length ?? 0) > 0;
  return <Navigate to={hasImports ? "/dashboard" : "/import?welcome=1"} replace />;
}
