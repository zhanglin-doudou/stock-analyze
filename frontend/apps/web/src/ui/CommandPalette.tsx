import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { NAV_GROUPS, SETTINGS_ITEM, type NavItem } from "../layout/navigation";
import "./command-palette.css";

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface CommandEntry extends NavItem {
  group: string;
  keywords: string;
}

/** 扁平化导航为命令条目；占位页也可跳转（保留可发现性）。 */
function buildEntries(): CommandEntry[] {
  const entries: CommandEntry[] = [];
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      entries.push({ ...item, group: group.label, keywords: `${group.label} ${item.label} ${item.to}` });
    }
  }
  entries.push({
    ...SETTINGS_ITEM,
    group: "设置",
    keywords: `设置 主题 密度 涨跌色 PAT ${SETTINGS_ITEM.to}`,
  });
  return entries;
}

/**
 * Phase 4b · 命令面板（⌘K / Ctrl+K）。
 * 自研（无 cmdk 依赖）：combobox + listbox、上下键/Enter/Esc、点击遮罩关闭。
 */
export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const entries = useMemo(buildEntries, []);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return entries;
    return entries.filter((entry) => entry.keywords.toLowerCase().includes(keyword));
  }, [entries, query]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) return null;

  const activeEntry = filtered[active];

  function run(entry: CommandEntry | undefined): void {
    if (!entry) return;
    navigate(entry.to);
    onClose();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "Tab") {
      // 面板内只有输入框可聚焦：Tab 不逃逸出 dialog。
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => (filtered.length === 0 ? 0 : (index + 1) % filtered.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) =>
        filtered.length === 0 ? 0 : (index - 1 + filtered.length) % filtered.length,
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      run(activeEntry);
    }
  }

  return createPortal(
    <div
      className="cmd-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="cmd"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        onKeyDown={handleKeyDown}
      >
        <input
          ref={inputRef}
          className="cmd-input"
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="cmd-list"
          aria-activedescendant={activeEntry ? `cmd-item-${activeEntry.to}` : undefined}
          placeholder="搜索页面 / 跳到…（Esc 关闭）"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="cmd-list" id="cmd-list" role="listbox" aria-label="命令结果">
          {filtered.length === 0 ? (
            <li className="cmd-empty">没有匹配的页面</li>
          ) : (
            filtered.map((entry, index) => (
              <li
                key={entry.to}
                id={`cmd-item-${entry.to}`}
                role="option"
                aria-selected={index === active}
                className={index === active ? "cmd-item is-active" : "cmd-item"}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => {
                  event.preventDefault();
                  run(entry);
                }}
              >
                <span className="cmd-item-label">{entry.label}</span>
                <span className="cmd-item-group">{entry.group}</span>
                <span className="cmd-item-path">{entry.to}</span>
              </li>
            ))
          )}
        </ul>
        <div className="cmd-foot">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> 选择
          </span>
          <span>
            <kbd>Enter</kbd> 跳转
          </span>
          <span>
            <kbd>Esc</kbd> 关闭
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
