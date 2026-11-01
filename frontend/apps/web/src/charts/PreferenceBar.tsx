import { type CSSProperties, type ReactElement } from "react";
import type { SectorRow } from "@sa/api-client";
import { displayValue, signTone } from "../ui/format";

function toneClass(value: string | null | undefined): string {
  const sign = signTone(value);
  return sign === "none" || sign === "flat" ? "num" : `num is-${sign}`;
}

/**
 * 偏好条（零口径）：条形宽度直接复用后端绝对百分比串（`weight_pct`），
 * 主动偏移仅以文本展示（带 +/- 与文字方向，颜色不是唯一信息载体）。
 * 不做任何数值解析/归一/换算。
 */
export function PreferenceBar({ row }: { row: SectorRow }): ReactElement {
  const width = (row.weight_pct ?? "0%").replace(/^[+−-]/, "");
  const direction = row.direction ?? "flat";
  const directionLabel = direction === "over" ? "超配" : direction === "under" ? "低配" : "持平";
  return (
    <div
      className="pref-bar"
      data-testid={`preference-bar-${row.sector_code}`}
      data-direction={direction}
    >
      <span className="pref-bar-name">{row.sector_name}</span>
      <span className="pref-bar-track" aria-hidden="true">
        <i style={{ "--w": width } as CSSProperties} />
      </span>
      <strong className={toneClass(row.active_weight_pct)}>
        {directionLabel} {displayValue(row.active_weight_pct)}
      </strong>
    </div>
  );
}
