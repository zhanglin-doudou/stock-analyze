import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearSession } from "../store/session";
import { CommandPalette } from "../ui/CommandPalette";
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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [focusMode, setFocusMode] = useState(false);

  useEffect(() => {
    document.title = `${meta.title} · 交易复盘`;
  }, [meta.title]);

  // Phase 4b 键盘地图：⌘K/Ctrl+K 命令面板；F 切换专注模式（输入框内不触发）。
  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent): void {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "SELECT" ||
        target?.isContentEditable === true;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (paletteOpen || typing) return;
      if (event.key === "f" || event.key === "F") {
        event.preventDefault();
        setFocusMode((enabled) => !enabled);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [paletteOpen]);

  return (
    <div className="app-shell" data-focus={focusMode ? "true" : "false"}>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
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
            title="命令面板（⌘K / Ctrl+K）"
            aria-label="搜索或跳转"
            aria-keyshortcuts="Meta+K Control+K"
            onClick={() => setPaletteOpen(true)}
          >
            <Icon name="search" size={15} />
            <span className="shell-command-text">搜索或跳转</span>
            <kbd>⌘K</kbd>
          </button>
          <button
            type="button"
            className="shell-iconbtn"
            aria-label={focusMode ? "退出专注模式" : "进入专注模式"}
            aria-pressed={focusMode}
            title="专注模式（F）"
            onClick={() => setFocusMode((enabled) => !enabled)}
          >
            <Icon name="focus" size={16} />
          </button>
          <span className="shell-status">
            <i className="shell-status-dot" aria-hidden="true" />
            本地实例
          </span>
        </header>

        <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
        <main className="shell-content" id="main-content" tabIndex={-1}>
          <Suspense fallback={<p className="shell-loading">加载中…</p>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
