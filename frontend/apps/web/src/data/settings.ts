/**
 * 用户偏好（API-055 GET / API-096 PATCH）的前端读写入口。
 *
 * 设计（docs/architecture/2026-11-11-user-preferences.md §7.2/§7.3）：
 * - 外观仍走 localStorage 快路径（首屏不等待网络），登录后由服务端覆盖；
 * - 服务端 `updated_at === null`（无行）时以本地值播种，避免丢掉用户已有选择；
 * - PATCH 带 `If-Match: W/"version"`；409 重取后重试一次；
 * - 所有失败都由调用方决定提示方式，本模块只负责传输与缓存。
 */

import { isApiProblemError, newIdempotencyKey } from "@sa/api-client";
import type { SettingsOut, SettingsPatchIn } from "@sa/api-client";
import { api } from "../client";
import { isAuthenticated } from "../store/session";
import { loadPreferences, updatePreferences, type Preferences } from "../theme/theme";

let cache: SettingsOut | null = null;

/** 读取偏好（未登录返回 null；默认命中进程内缓存）。 */
export async function fetchSettings(force = false): Promise<SettingsOut | null> {
  if (!isAuthenticated()) return null;
  if (!force && cache) return cache;
  const { settings } = await api.settings.get();
  if (settings) cache = settings;
  return cache;
}

export function cachedSettings(): SettingsOut | null {
  return cache;
}

/** 登出/切换账号时丢弃进程内缓存，避免把上一个用户的偏好回填给下一个人。 */
export function clearSettingsCache(): void {
  cache = null;
}

/** 局部更新；只传脏字段。409 = 其他标签页已改版本 → 重取后重试一次。 */
export async function saveSettings(patch: SettingsPatchIn): Promise<SettingsOut | null> {
  if (!isAuthenticated()) return null;
  const patchOnce = async (version: number): Promise<SettingsOut> => {
    const { settings } = await api.settings.patch(patch, {
      version,
      idempotencyKey: newIdempotencyKey(),
    });
    return settings;
  };

  let current = cache ?? (await fetchSettings());
  if (!current) return null;
  try {
    cache = await patchOnce(current.version);
  } catch (err) {
    if (!(isApiProblemError(err) && err.status === 409)) throw err;
    current = await fetchSettings(true);
    if (!current) return null;
    cache = await patchOnce(current.version);
  }
  return cache;
}

function appearanceOf(settings: SettingsOut): Preferences {
  return {
    theme: settings.appearance.theme,
    density: settings.appearance.density,
    trend: settings.appearance.trend,
  };
}

/**
 * 登录后同步外观（首屏仍用 localStorage 的即时值）：
 * - 服务端无行 → 本地值写入云端（read 范围令牌会 403，保持本地生效即可）；
 * - 服务端有值 → 覆盖本地。
 * 返回同步后的本地偏好；未登录或拉取失败时返回 null（不影响页面）。
 */
export async function syncAppearance(): Promise<Preferences | null> {
  let settings: SettingsOut | null;
  try {
    settings = await fetchSettings();
  } catch {
    return null;
  }
  if (!settings) return null;
  const remote = appearanceOf(settings);
  if (settings.updated_at === null) {
    const local = loadPreferences();
    if (
      local.theme === remote.theme &&
      local.density === remote.density &&
      local.trend === remote.trend
    ) {
      return local;
    }
    try {
      await saveSettings({ appearance: local });
    } catch {
      // 播种失败（如只读令牌）不阻断：本地值继续生效。
    }
    return loadPreferences();
  }
  updatePreferences(remote);
  return loadPreferences();
}
