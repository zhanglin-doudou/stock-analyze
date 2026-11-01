import { type ReactElement } from "react";

/**
 * 概念板块警示条：概念可重叠、不可加总、不可与行业 HHI 横比。
 * 文案来自后端 `concept.caveat`（本组件不生成口径话术）。
 */
export function CaveatBanner({ text }: { text?: string | null }): ReactElement | null {
  if (!text) return null;
  return (
    <div className="caveat" data-testid="concept-caveat" role="note">
      {text}
    </div>
  );
}
