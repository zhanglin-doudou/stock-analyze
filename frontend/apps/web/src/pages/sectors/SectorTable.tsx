import { type ReactElement } from "react";
import type { SectorRow, SectorTaxonomy } from "@sa/api-client";
import { displayValue, signTone } from "../../ui/format";

function toneClass(value: string | null | undefined): string {
  const sign = signTone(value);
  return sign === "none" || sign === "flat" ? "num" : `num is-${sign}`;
}

function directionText(direction: string | null | undefined): string {
  if (direction === "over") return "超配";
  if (direction === "under") return "低配";
  return "";
}

interface SectorTableProps {
  rows: SectorRow[];
  taxonomy: SectorTaxonomy;
  /** L1/L2 行可展开下一级（一次只查一层）。 */
  onExpand?: (row: SectorRow) => void;
}

/**
 * 板块明细表（零口径）：金额/比率/百分比全部原样展示后端字符串。
 * industry 用 weight/benchmark/active 列；concept 用 exposure 列，且无基准/主动权重列。
 */
export function SectorTable({ rows, taxonomy, onExpand }: SectorTableProps): ReactElement {
  if (rows.length === 0) {
    return <p className="dash-muted">暂无板块数据。</p>;
  }
  const isConcept = taxonomy === "concept";
  return (
    <table className="dash-table sector-table">
      <thead>
        <tr>
          <th>板块</th>
          <th>市值</th>
          {isConcept ? <th>暴露</th> : <th>权重</th>}
          {isConcept ? <th>多归属</th> : <th>基准权重</th>}
          {isConcept ? null : <th>主动权重</th>}
          <th>持仓数</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const expandable = !isConcept && onExpand != null && row.level < 3;
          return (
            <tr
              key={row.sector_code}
              data-testid={`sector-row-${row.sector_code}`}
              data-level={row.level}
            >
              <td>
                {expandable ? (
                  <button
                    type="button"
                    className="sector-expand"
                    aria-label={`展开 ${row.sector_name}`}
                    onClick={() => onExpand?.(row)}
                  >
                    +
                  </button>
                ) : null}
                <span className="dash-holding-name">
                  {row.sector_name}
                  <span className="dash-holding-code">{row.sector_code}</span>
                </span>
              </td>
              <td className="num">{displayValue(row.market_value_display ?? row.market_value)}</td>
              {isConcept ? (
                <td className="num">{displayValue(row.exposure_pct ?? row.exposure)}</td>
              ) : (
                <td className="num">{displayValue(row.weight_pct ?? row.weight)}</td>
              )}
              {isConcept ? (
                <td>
                  {row.overlap ? <span className="dash-badge is-degraded">多归属</span> : <span className="dash-muted">—</span>}
                </td>
              ) : (
                <td className="num">{displayValue(row.benchmark_weight_pct ?? row.benchmark_weight)}</td>
              )}
              {isConcept ? null : (
                <td className={toneClass(row.active_weight_pct ?? row.active_weight)}>
                  {directionText(row.direction)}
                  {displayValue(row.active_weight_pct ?? row.active_weight)}
                </td>
              )}
              <td className="num">{row.holding_count}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
