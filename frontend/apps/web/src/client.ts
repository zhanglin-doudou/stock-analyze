import { createClient } from "@sa/api-client";
import { clearSession, getTokens, saveTokens } from "./store/session";

/** 全局唯一 API 客户端；dev 下经 Vite proxy 到后端 127.0.0.1:8000。 */
export const api = createClient({
  baseUrl: "/api/v1",
  getToken: getTokens,
  onTokens: saveTokens,
  onUnauthorized: () => {
    clearSession();
    if (window.location.pathname !== "/login") {
      window.location.replace("/login");
    }
  },
});
