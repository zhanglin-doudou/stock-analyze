import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** 与 `tailwind.config.ts` 的 `prefix: "tw-"` 对齐，否则 `tw-w-full` / `tw-w-40`
 * 同时保留、由产物顺序决定胜负，调用方的覆盖类会失效。 */
const twMerge = extendTailwindMerge({ prefix: "tw-" });

/** shadcn 约定：合并 Tailwind 类（手写类不受影响）。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
