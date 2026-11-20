import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * 与 `tailwind.config.ts` 的 `prefix: "tw-"` 对齐，否则 `tw-w-full` / `tw-w-40`
 * 同时保留、由产物顺序决定胜负，调用方的覆盖类会失效。
 *
 * `extend` 把项目自定义的 token 类（text-body / rounded-card / shadow-1 / z-dialog …）
 * 归入正确分组，保证 `cn("tw-text-sm", "tw-text-body")` 能正确后者覆盖前者。
 */
const twMerge = extendTailwindMerge({
  prefix: "tw-",
  extend: {
    classGroups: {
      "font-size": [{ text: ["micro", "body", "small", "h1", "h2", "h3", "display"] }],
      rounded: [{ rounded: ["card", "pill"] }],
      shadow: [{ shadow: ["1", "2"] }],
      z: [{ z: ["rail", "topbar", "drawer", "dialog", "command", "toast"] }],
    },
  },
});

/** shadcn 约定：合并 Tailwind 类（手写类不受影响）。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
