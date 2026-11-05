import { Suspense, useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { clearSession } from "../store/session";
import { loadPreferences, resolveTheme, updatePreferences, type Theme } from "../theme/theme";
import { CommandPalette } from "../ui/CommandPalette";
import { Icon } from "../ui/icons";
import {
  NAV_GROUPS,
  SETTINGS_ITEM,
  activeGroupId,
  isNavItemActive,
  pageMeta,
  type NavGroup,
  type NavItem,
} from "./navigation";
import { useMediaQuery } from "./useMediaQuery";
import "./app-shell.css";

interface SubLinkProps {
  item: NavItem;
  active: boolean;
  tabIndex?: number;
  onNavigate?: () => void;
}

/** 二级菜单项：图标 + 中文 + 占位标签；compact 模式下由 flyout 展示。 */
function SubLink({ item, active, tabIndex, onNavigate }: SubLinkProps) {
  const label = item.placeholder ? `${item.label}（占位）` : item.label;
  return (
    <Link
      to={item.to}
      className={`shell-sub-link${active ? " is-active" : ""}${
        item.placeholder ? " is-placeholder" : ""
      }`}
      aria-current={active ? "page" : undefined}
      aria-label={label}
      title={label}
      tabIndex={tabIndex}
      onClick={onNavigate}
    >
      <span className="shell-sub-icon" aria-hidden="true">
        <Icon name={item.icon} size={15} />
      </span>
      <span className="shell-sub-label">{item.label}</span>
      {item.placeholder ? <span className="shell-mini-tag">占位</span> : null}
      {active ? <span className="shell-active-dot" aria-hidden="true" /> : null}
    </Link>
  );
}

function GroupSection({
  group,
  pathname,
  expanded,
  open,
  flyoutOpen,
  onToggle,
  onNavigate,
}: {
  group: NavGroup;
  pathname: string;
  expanded: boolean;
  open: boolean;
  flyoutOpen: boolean;
  onToggle: () => void;
  onNavigate: () => void;
}) {
  const active = group.items.some((item) => isNavItemActive(pathname, item.to));
  const drawerId = `shell-drawer-${group.id}`;
  const controls = expanded || flyoutOpen ? drawerId : undefined;

  return (
    <div
      className={`shell-group${active ? " is-active" : ""}${open ? " is-open" : ""}`}
      role="group"
      aria-label={group.label}
      data-group={group.id}
    >
      <button
        type="button"
        className="shell-group-trigger"
        aria-label={group.label}
        aria-expanded={expanded ? open : flyoutOpen}
        aria-controls={controls}
        data-tooltip={group.label}
        onClick={onToggle}
      >
        <span className="shell-group-icon" aria-hidden="true">
          <Icon name={group.icon} size={18} />
        </span>
        <span className="shell-group-label">{group.label}</span>
        <span className="shell-group-chevron" aria-hidden="true">
          <Icon name="chevron" size={13} />
        </span>
      </button>

      {expanded ? (
        <div
          id={drawerId}
          className="shell-drawer"
          data-open={open ? "true" : "false"}
          aria-hidden={!open}
        >
          <div className="shell-drawer-inner">
            {group.items.map((item) => (
              <SubLink
                key={item.to}
                item={item}
                active={isNavItemActive(pathname, item.to)}
                tabIndex={open ? undefined : -1}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        </div>
      ) : null}

      {!expanded && flyoutOpen ? (
        <div id={drawerId} className="shell-flyout" role="region" aria-label={`${group.label}菜单`}>
          <div className="shell-flyout-head">
            <span>{group.label}</span>
            <span className="shell-flyout-hint">快速前往</span>
          </div>
          <div className="shell-flyout-list">
            {group.items.map((item) => (
              <SubLink
                key={item.to}
                item={item}
                active={isNavItemActive(pathname, item.to)}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

const NAV_COLLAPSED_KEY = "sa.nav.collapsed";

function readRailCollapsed(): boolean {
  try {
    return window.localStorage.getItem(NAV_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function persistRailCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(NAV_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // 隐私模式忽略即可，不影响当前会话
  }
}

/**
 * 应用外壳：两级侧栏（展开/紧凑）+ 顶栏 + 内容区。
 * - >=1280px：展开 220px，一级分组手风琴（互斥展开，默认第一组），二级项缩进；
 * - <=1279px：紧凑 68px，只显示一级图标；hover 显示分组名，点击弹出二级 flyout；
 * - 专注模式隐藏侧栏；主题切换在顶栏保留。
 */
export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const meta = pageMeta(location.pathname);
  const wide = useMediaQuery("(min-width: 1280px)");
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => readRailCollapsed());
  const expanded = wide && !railCollapsed;
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [theme, setTheme] = useState<Theme>(() => loadPreferences().theme);
  const [openGroupId, setOpenGroupId] = useState<string>(
    () => activeGroupId(location.pathname) ?? NAV_GROUPS[0]?.id ?? "overview",
  );
  const [flyoutGroupId, setFlyoutGroupId] = useState<string | null>(null);
  const railRef = useRef<HTMLElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const resolvedTheme = resolveTheme(theme);
  const nextThemeLabel = resolvedTheme === "dark" ? "切换到浅色主题" : "切换到深色主题";

  useEffect(() => {
    document.title = `${meta.title} · 交易复盘`;
  }, [meta.title]);

  // 路由切换：打开所属一级分组，关闭紧凑模式浮层。
  useEffect(() => {
    const group = activeGroupId(location.pathname);
    if (group) setOpenGroupId(group);
    setFlyoutGroupId(null);
  }, [location.pathname]);

  // 断点切换：展开时关闭 flyout；紧凑时保持当前分组但隐藏手风琴。
  useEffect(() => {
    if (expanded) setFlyoutGroupId(null);
  }, [expanded]);

  // 窗口高度不足时导航区可滚动；路由/展开分组变化后把当前项滚到可视区，
  // 避免一级菜单展开后当前页被挤到折叠区之外。
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const active = nav.querySelector<HTMLElement>(".shell-sub-link.is-active");
    if (!active) return;
    const navRect = nav.getBoundingClientRect();
    const linkRect = active.getBoundingClientRect();
    if (linkRect.top < navRect.top) {
      nav.scrollTop -= navRect.top - linkRect.top;
    } else if (linkRect.bottom > navRect.bottom) {
      nav.scrollTop += linkRect.bottom - navRect.bottom;
    }
  }, [location.pathname, openGroupId, expanded]);

  // 紧凑模式：点击浮层外 / Esc 关闭。
  useEffect(() => {
    if (!flyoutGroupId) return;
    function onPointerDown(event: PointerEvent): void {
      const target = event.target as Element | null;
      if (target?.closest(".shell-flyout, .shell-group-trigger")) return;
      setFlyoutGroupId(null);
    }
    function onKeyDown(event: globalThis.KeyboardEvent): void {
      if (event.key === "Escape") setFlyoutGroupId(null);
    }
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [flyoutGroupId]);

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

  const settingsActive = isNavItemActive(location.pathname, SETTINGS_ITEM.to);

  return (
    <div
      className="app-shell"
      data-focus={focusMode ? "true" : "false"}
      data-nav-collapsed={expanded ? "false" : "true"}
    >
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>

      <aside
        ref={railRef}
        className={`shell-rail${expanded ? " is-expanded" : " is-compact"}`}
        data-nav-mode={expanded ? "expanded" : "compact"}
      >
        <div className="shell-brand" aria-label="交易复盘">
          <span className="shell-brand-mark" aria-hidden="true">
            静
          </span>
          <span className="shell-brand-text">
            <strong>交易复盘</strong>
            <small>个人复盘工作台</small>
          </span>
        </div>

        <nav ref={navRef} className="shell-nav" aria-label="主导航">
          {NAV_GROUPS.map((group) => {
            const open = openGroupId === group.id;
            const flyoutOpen = flyoutGroupId === group.id;
            return (
              <GroupSection
                key={group.id}
                group={group}
                pathname={location.pathname}
                expanded={expanded}
                open={open}
                flyoutOpen={flyoutOpen}
                onToggle={() => {
                  if (expanded) {
                    setOpenGroupId(group.id);
                    return;
                  }
                  setFlyoutGroupId((current) => (current === group.id ? null : group.id));
                }}
                onNavigate={() => setFlyoutGroupId(null)}
              />
            );
          })}
        </nav>

        <div className="shell-rail-bottom">
          <Link
            to={SETTINGS_ITEM.to}
            className={`shell-sub-link shell-settings-link${settingsActive ? " is-active" : ""}`}
            aria-current={settingsActive ? "page" : undefined}
            aria-label="设置"
            title="设置"
            data-tooltip="设置"
          >
            <span className="shell-sub-icon" aria-hidden="true">
              <Icon name="settings" size={16} />
            </span>
            <span className="shell-sub-label">设置</span>
          </Link>
          <button
            type="button"
            className="shell-sub-link shell-logout"
            aria-label="登出"
            title="登出"
            data-tooltip="登出"
            onClick={() => {
              clearSession();
              navigate("/login", { replace: true });
            }}
          >
            <span className="shell-sub-icon" aria-hidden="true">
              <Icon name="logout" size={16} />
            </span>
            <span className="shell-sub-label">登出</span>
          </button>
        </div>

        {wide ? (
          <button
            type="button"
            className="shell-rail-toggle"
            aria-label={expanded ? "收起菜单" : "展开菜单"}
            aria-expanded={expanded}
            title={expanded ? "收起菜单，专注右侧内容" : "展开菜单"}
            onClick={() => {
              const next = !railCollapsed;
              setRailCollapsed(next);
              persistRailCollapsed(next);
              if (next) setFlyoutGroupId(null);
            }}
          >
            <Icon name="chevron" size={12} className="icon" />
          </button>
        ) : null}
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
            aria-label={nextThemeLabel}
            title={nextThemeLabel}
            onClick={() =>
              setTheme(updatePreferences({ theme: resolvedTheme === "dark" ? "light" : "dark" }).theme)
            }
          >
            <Icon name="theme" size={16} />
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
