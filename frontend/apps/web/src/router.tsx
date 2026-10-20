import type { ReactElement } from "react";
import { Link, Navigate, Outlet, createBrowserRouter, useNavigate } from "react-router-dom";
import { clearSession, useIsAuthenticated } from "./store/session";
import { Import } from "./pages/Import";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Settings } from "./pages/Settings";
import { Trades } from "./pages/Trades";

function RequireAuth({ children }: { children: ReactElement }): ReactElement {
  const authenticated = useIsAuthenticated();
  if (!authenticated) return <Navigate to="/login" replace />;
  return children;
}

function Layout(): ReactElement {
  const navigate = useNavigate();
  return (
    <div className="app">
      <header className="app-header">
        <strong>交易复盘</strong>
        <nav className="app-nav">
          <Link to="/trades">交易流水</Link>
          <Link to="/import">导入</Link>
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
      { index: true, element: <Navigate to="/trades" replace /> },
      { path: "trades", element: <Trades /> },
      { path: "import", element: <Import /> },
      { path: "settings", element: <Settings /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
