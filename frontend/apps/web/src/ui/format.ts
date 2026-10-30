/**
 * Phase 1 · 展示层格式化辅助（只读后端字符串，不做任何数值运算）。
 */

export type SignTone = "pos" | "neg" | "flat" | "none";

const ZERO_PATTERN = /^-?0(?:\.0+)?%?$/;

/**
 * 由后端格式化字符串判断正负，用于颜色/箭头**展示**。
 * 不解析数字、不参与排序/聚合；数值语义仍以后端为准。
 */
export function signTone(value: string | null | undefined): SignTone {
  if (value == null || value === "" || value === "—") return "none";
  const trimmed = value.trim();
  if (ZERO_PATTERN.test(trimmed)) return "flat";
  return trimmed.startsWith("-") ? "neg" : "pos";
}

/** 后端字符串是否可展示；null/空/“—”统一显示占位符。 */
export function displayValue(value: string | null | undefined, placeholder = "—"): string {
  return value == null || value === "" ? placeholder : value;
}
