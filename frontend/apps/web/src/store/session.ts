import { useSyncExternalStore } from "react";

/** access/refresh 令牌的持久化（localStorage）+ 订阅式会话状态。 */
export interface Tokens {
  access: string | null;
  refresh: string | null;
}

const ACCESS_KEY = "sa.access_token";
const REFRESH_KEY = "sa.refresh_token";

function readStore(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStore(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* 隐私模式等场景忽略写入失败 */
  }
}

let access: string | null = readStore(ACCESS_KEY);
let refresh: string | null = readStore(REFRESH_KEY);

const listeners = new Set<() => void>();
function emit(): void {
  for (const listener of listeners) listener();
}

export function getTokens(): Tokens {
  return { access, refresh };
}

export function isAuthenticated(): boolean {
  return access !== null;
}

export function saveTokens(tokens: { access_token: string; refresh_token?: string }): void {
  access = tokens.access_token;
  if (tokens.refresh_token) refresh = tokens.refresh_token;
  writeStore(ACCESS_KEY, access);
  writeStore(REFRESH_KEY, refresh);
  emit();
}

export function clearSession(): void {
  access = null;
  refresh = null;
  writeStore(ACCESS_KEY, null);
  writeStore(REFRESH_KEY, null);
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useIsAuthenticated(): boolean {
  return useSyncExternalStore(subscribe, isAuthenticated, () => false);
}
