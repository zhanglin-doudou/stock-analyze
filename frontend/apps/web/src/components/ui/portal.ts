import * as React from "react";

/**
 * Radix Portal 容器。
 *
 * 默认 `null` → Radix 自动挂到 `document.body`（全局样式下无需额外容器）。
 * 需要把弹层限制在某个局部样式域时（测试、嵌入页），在调用方用 Provider 注入。
 */
export const PortalContainerContext = React.createContext<HTMLElement | null>(null);

export function usePortalContainer(): HTMLElement | null {
  return React.useContext(PortalContainerContext);
}
