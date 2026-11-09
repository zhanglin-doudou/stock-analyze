import type { ReactElement } from "react";
import type { TradeFlagGlossaryOut } from "@sa/api-client";
import "./trade-flag-glossary.css";

/** 账单级旗标说明：整体折叠面板，默认折叠；文案全部来自后端词表。 */
export function TradeFlagGlossary({
  entries,
}: {
  entries: TradeFlagGlossaryOut[];
}): ReactElement | null {
  if (!entries.length) return null;
  const sides: Array<{ key: "buy" | "sell"; title: string }> = [
    { key: "buy", title: "买入侧" },
    { key: "sell", title: "卖出侧" },
  ];
  return (
    <details className="trade-flag-glossary">
      <summary>账单旗标说明（点击展开）</summary>
      <div className="trade-flag-glossary-body">
        {sides.map((side) => {
          const items = entries.filter((entry) => entry.side === side.key);
          if (!items.length) return null;
          return (
            <section key={side.key}>
              <h4>{side.title}</h4>
              <dl>
                {items.map((entry) => (
                  <div key={entry.code} className="trade-flag-glossary-row">
                    <dt>
                      <code>{entry.code}</code> {entry.label_zh}
                      {entry.lookahead ? <span className="chip">事后</span> : null}
                    </dt>
                    <dd>{entry.description_zh}</dd>
                  </div>
                ))}
              </dl>
            </section>
          );
        })}
      </div>
    </details>
  );
}
