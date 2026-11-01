import { lazy, type ReactElement } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { useIsAuthenticated } from "./store/session";
import { FeaturePlaceholder } from "./pages/ComingSoon";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";

/** 登录守卫：无 token → 跳 /login。导出供路由测试直接渲染。 */
export function RequireAuth({ children }: { children: ReactElement }): ReactElement {
  const authenticated = useIsAuthenticated();
  if (!authenticated) return <Navigate to="/login" replace />;
  return children;
}

/** 应用外壳（Phase 1b 起为 AppShell）。导出供测试断言导航与登录守卫。 */
export function Layout(): ReactElement {
  return <AppShell />;
}

// 页面级 code-splitting：登录后页面按需加载，首屏只保留外壳与当前路由。
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const Trades = lazy(() => import("./pages/Trades").then((m) => ({ default: m.Trades })));
const Rounds = lazy(() => import("./pages/Rounds").then((m) => ({ default: m.Rounds })));
const RoundDetail = lazy(() =>
  import("./pages/RoundDetail").then((m) => ({ default: m.RoundDetail })),
);
const NavPage = lazy(() => import("./pages/Nav").then((m) => ({ default: m.Nav })));
const Assets = lazy(() => import("./pages/Assets").then((m) => ({ default: m.Assets })));
const Metrics = lazy(() => import("./pages/Metrics").then((m) => ({ default: m.Metrics })));
const MetricDetail = lazy(() =>
  import("./pages/MetricDetail").then((m) => ({ default: m.MetricDetail })),
);
const MetricsCatalog = lazy(() =>
  import("./pages/MetricsCatalog").then((m) => ({ default: m.MetricsCatalog })),
);
const Import = lazy(() => import("./pages/Import").then((m) => ({ default: m.Import })));
const Imports = lazy(() => import("./pages/Imports").then((m) => ({ default: m.Imports })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/register", element: <Register /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <Dashboard /> },
      { path: "trades", element: <Trades /> },
      { path: "rounds", element: <Rounds /> },
      { path: "rounds/:roundId", element: <RoundDetail /> },
      { path: "nav", element: <NavPage /> },
      { path: "assets", element: <Assets /> },
      { path: "metrics", element: <Metrics /> },
      { path: "metrics/catalog", element: <MetricsCatalog /> },
      { path: "metrics/:metricId", element: <MetricDetail /> },
      { path: "import", element: <Import /> },
      { path: "imports", element: <Imports /> },
      {
        path: "reports",
        element: (
          <FeaturePlaceholder
            title="报告"
            dependency="Period2-13 报告/导出页（依赖 Period2-09 月报）"
            description="月报与可下载报告尚未实现；此处仅预留入口。"
          />
        ),
      },
      {
        path: "exports",
        element: (
          <FeaturePlaceholder
            title="导出"
            dependency="Period2-10 导出（依赖 Period2-13）"
            description="账单/指标/报告导出尚未实现；此处仅预留入口。"
          />
        ),
      },
      {
        path: "attribution",
        element: (
          <FeaturePlaceholder
            title="归因"
            dependency="Period2-08 归因分析"
            description="收益归因（选股/择时/行业）尚未实现；此处仅预留入口。"
          />
        ),
      },
      { path: "settings", element: <Settings /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
