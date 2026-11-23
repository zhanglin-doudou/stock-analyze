/**
 * next 回跳参数的安全校验（防 open redirect）。
 *
 * 仅接受站内相对路径：以单个 `/` 开头；拒绝 `//`（协议相对）、反斜杠、
 * 任何 `:`（协议/端口混淆）与控制字符。校验不通过返回 null，调用方回退 `/`。
 * 登录回跳与引导页「返回工作台」共用它，保证两处口径一致。
 */
export function sanitizeNextPath(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const value = raw.trim();
  if (!value.startsWith("/")) return null;
  if (value.startsWith("//")) return null;
  if (value.includes("\\")) return null;
  if (value.includes(":")) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return null;
  return value;
}
