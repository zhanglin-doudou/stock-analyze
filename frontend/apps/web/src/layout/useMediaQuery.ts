import { useEffect, useState } from "react";

/**
 * 响应式断点的 JS 版本（仅用于行为差异，如展开/收起、hover flyout）。
 * 布局本身仍由 CSS media query 负责；无 matchMedia 的测试环境默认返回 true。
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return true;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia(query);
    const onChange = (): void => setMatches(media.matches);
    onChange();
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    }
    // Safari < 14 / 旧环境
    media.addListener(onChange);
    return () => media.removeListener(onChange);
  }, [query]);

  return matches;
}
