import type { ReactElement } from "react";
import { Link, Navigate, Outlet, createBrowserRouter, useNavigate } from "react-router-dom";
import { clearSession, useIsAuthenticated } from "./store/session";
import { Assets } from "./pages/Assets";
import { FeaturePlaceholder } from "./pages/ComingSoon";
import { Dashboard } from "./pages/Dashboard";
import { Import } from "./pages/Import";
import { Imports } from "./pages/Imports";
import { Login } from "./pages/Login";
import { MetricDetail } from "./pages/MetricDetail";
import { Metrics } from "./pages/Metrics";
import { Nav } from "./pages/Nav";
import { Register } from "./pages/Register";
import { RoundDetail } from "./pages/RoundDetail";
import { Rounds } from "./pages/Rounds";
import { Settings } from "./pages/Settings";
import { Trades } from "./pages/Trades";

/** 登录守卫：无 token → 跳 /login。导出供路由测试直接渲染。 */
export function RequireAuth({ children }: { children: ReactElement }): ReactElement {
  const authenticated = useIsAuthenticated();
  if (!authenticated) return <Navigate to="/login" replace />;
  return children;
}

/** 应用外壳（顶部导航 + 内容区）。导出供测试断言导航与登录守卫。 */
export function Layout(): ReactElement {
  const navigate = useNavigate();
  return (
    <div className="app">
      <header className="app-header">
        <strong>交易复盘</strong>
        <nav className="app-nav">
          <Link to="/dashboard">看板</Link>
          <Link to="/trades">交易流水</Link>
          <Link to="/rounds">回合</Link>
          <Link to="/nav">净值</Link>
          <Link to="/assets">资产</Link>
          <Link to="/metrics">指标</Link>
          <Link to="/import">导入</Link>
          <Link to="/imports">导入记录</Link>
          <Link to="/settings">设置</Link>
        </nav>
        <button
          className="btn btn-ghost"
          type="button"
          onClick={() => {
            clearSession();
            navigate("/login", { replace: true });
          }}
        >
          登出
        </button>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

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
      { path: "nav", element: <Nav /> },
      { path: "assets", element: <Assets /> },
      { path: "metrics", element: <Metrics /> },
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
