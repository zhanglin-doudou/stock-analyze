import { Suspense, useEffect } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearSession } from "../store/session";
import { Icon } from "../ui/icons";
import { NAV_GROUPS, SETTINGS_ITEM, pageMeta, type NavItem } from "./navigation";
import "./app-shell.css";

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? "shell-nav-link is-active" : "shell-nav-link";
}

function RailLink({ item }: { item: NavItem }) {
  return (
    <NavLink
      to={item.to}
      className={navClass}
      aria-label={item.placeholder ? `${item.label}（占位）` : item.label}
      title={item.placeholder ? `${item.label}（占位）` : item.label}
    >
      <Icon name={item.icon} />
      <span className="sr-only">{item.label}</span>
    </NavLink>
  );
}

/**
 * Phase 1b · 应用外壳（A+ 混合）：左侧 76px 图标栏 + 顶部面包屑/命令入口/账户。
 * - 导航按 5 组分组（D8），占位入口保留；
 * - 页面标题与面包屑由路由推导（document.title 同步）；
 * - 内容区统一 max-width 1440 + tokens 间距，旧页面沿用现有卡片样式。
 */
export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const meta = pageMeta(location.pathname);

  useEffect(() => {
    document.title = `${meta.title} · 交易复盘`;
  }, [meta.title]);

  return (
    <div className="app-shell">
      <aside className="shell-rail">
        <div className="shell-brand" aria-label="交易复盘">
          <span className="shell-brand-mark" aria-hidden="true">
            静
          </span>
        </div>

        <nav className="shell-nav" aria-label="主导航">
          {NAV_GROUPS.map((group) => (
            <div className="shell-nav-group" role="group" aria-label={group.label} key={group.id}>
              <span className="sr-only">{group.label}</span>
              {group.items.map((item) => (
                <RailLink item={item} key={item.to} />
              ))}
            </div>
          ))}
        </nav>

        <div className="shell-rail-spacer" />
        <div className="shell-nav-group shell-nav-settings" role="group" aria-label="设置">
          <RailLink item={SETTINGS_ITEM} />
        </div>
        <button
          type="button"
          className="shell-nav-link shell-logout"
          aria-label="登出"
          title="登出"
          onClick={() => {
            clearSession();
            navigate("/login", { replace: true });
          }}
        >
          <Icon name="logout" />
          <span className="sr-only">登出</span>
        </button>
      </aside>

      <div className="shell-main">
        <header className="shell-topbar">
          <div className="shell-crumb">
            <span className="shell-crumb-root">交易复盘</span>
            <span className="shell-crumb-sep" aria-hidden="true">
              /
            </span>
            <span className="shell-crumb-current">{meta.breadcrumb.slice(-1)[0]}</span>
          </div>
          <div className="shell-topbar-grow" />
          <button
            type="button"
            className="shell-command"
            title="命令面板（Phase 4 开放）"
            aria-label="搜索或跳转（Phase 4 开放）"
            disabled
          >
            <Icon name="search" size={15} />
            <span className="shell-command-text">搜索或跳转</span>
            <kbd>⌘K</kbd>
            <span className="shell-tag">Phase 4</span>
          </button>
          <span className="shell-status">
            <i className="shell-status-dot" aria-hidden="true" />
            本地实例
          </span>
        </header>

        <main className="shell-content">
          <Suspense fallback={<p className="shell-loading">加载中…</p>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
