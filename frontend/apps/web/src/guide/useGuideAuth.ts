import { useEffect, useState } from "react";
import { isAuthenticated } from "../store/session";
import { sanitizeNextPath } from "../utils/nextPath";

/**
 * 引导页客户端登录态与「返回工作台」回跳地址。
 * SSR 与客户端首屏都返回未登录 + "/"，hydrate 后在 effect 中切换，
 * 因此服务端静态 HTML 始终是未登录版本（SEO 友好），且无 hydration mismatch。
 */
export function useGuideAuth(): { authed: boolean; homeHref: string } {
  const [authed, setAuthed] = useState(false);
  const [homeHref, setHomeHref] = useState("/");

  useEffect(() => {
    setAuthed(isAuthenticated());
    const next = sanitizeNextPath(new URLSearchParams(window.location.search).get("next"));
    if (next) setHomeHref(next);
  }, []);

  return { authed, homeHref };
}
