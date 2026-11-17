import * as React from "react";

/** Radix Portal 的容器：`/import` 页面挂载时创建 `#import-portal[data-import-ui]`，
 * 保证弹层仍落在 scoped reset 内（否则挂到 body 上 reset 失效）。 */
export const PortalContainerContext = React.createContext<HTMLElement | null>(null);

export function usePortalContainer(): HTMLElement | null {
  return React.useContext(PortalContainerContext);
}
