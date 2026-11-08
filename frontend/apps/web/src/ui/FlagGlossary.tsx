import type { FlagGlossaryOut } from "@sa/api-client";
import "./flag-glossary.css";

export interface FlagGlossaryProps {
  /** API-092/093 的 `flag_glossary`；缺失时整块不渲染（旧缓存/降级不报错）。 */
  entries?: FlagGlossaryOut[];
}

/**
 * 旗标说明：只渲染后端返回的 code / 中英文标签与说明，前端不做任何口径判断或翻译。
 */
export function FlagGlossary({ entries }: FlagGlossaryProps) {
  if (!entries?.length) return null;
  return (
    <section className="card flag-glossary" data-testid="flag-glossary">
      <h2>旗标说明</h2>
      <dl>
        {entries.map((entry) => (
          <div className="flag-glossary-item" key={entry.code}>
            <dt>
              <span>{entry.label_zh}</span>
              <code>{entry.code}</code>
            </dt>
            <dd>
              <p>{entry.description_zh}</p>
              <p className="muted">
                {entry.label_en}：{entry.description_en}
              </p>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
