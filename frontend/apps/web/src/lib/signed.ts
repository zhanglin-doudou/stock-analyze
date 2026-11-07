/**
 * 带符号数值的着色工具（回合洞察 / 回合详情共用）。
 *
 * 前端零口径：这里只读后端返回的定点字符串做**符号判断**，不做任何数值解析，
 * 不改变字符串本身；颜色只来自语义 token（`--up` / `--down`，A 股红涨绿跌）。
 *
 * 三态：
 * - 负（`-` 开头）→ `money-neg`；
 * - 零（`0`、`0.00`、`0.0000`、`0.00%`、`-0.00`）→ 中性，不着色（零既不是涨也不是跌）；
 * - 正 → `money-pos`。
 * 空值占位（`null` / `undefined` / `—`）同样保持中性，避免把「无数据」染成涨跌色。
 */
export function signedClass(value: string | null | undefined): string {
  if (value == null || value === "" || value === "—") return "money";
  if (/^[+-]?0(?:\.0+)?%?$/.test(value)) return "money";
  if (value.startsWith("-")) return "money money-neg";
  return "money money-pos";
}
