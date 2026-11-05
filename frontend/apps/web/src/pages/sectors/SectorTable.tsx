import { type ReactElement } from "react";
import type { SectorRow, SectorTaxonomy } from "@sa/api-client";
import { displayValue, signTone } from "../../ui/format";
import "../../ui/data-table.css";

function toneClass(value: string | null | undefined): string | undefined {
  const sign = signTone(value);
  if (sign === "none" || sign === "flat") return undefined;
  return `dt-tone-${sign}`;
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
export function SectorTable({ rows, taxonomy }: SectorTableProps): ReactElement {
  if (rows.length === 0) {
    return <p className="muted">暂无板块数据。</p>;
  }
  const isConcept = taxonomy === "concept";
  return (
    <table className="dt-static sector-table">
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
          return (
            <tr
              key={row.sector_code}
              data-testid={`sector-row-${row.sector_code}`}
              data-level={row.level}
            >
              <td>
                <span className="dt-label">
                  {row.sector_name}
                  <span className="dt-label-code">{row.sector_code}</span>
                </span>
              </td>
              <td>{displayValue(row.market_value_display ?? row.market_value)}</td>
              {isConcept ? (
                <td>{displayValue(row.exposure_pct ?? row.exposure)}</td>
              ) : (
                <td>{displayValue(row.weight_pct ?? row.weight)}</td>
              )}
              {isConcept ? (
                <td>
                  {row.overlap ? <span className="badge badge-degraded">多归属</span> : <span className="muted">—</span>}
                </td>
              ) : (
                <td>{displayValue(row.benchmark_weight_pct ?? row.benchmark_weight)}</td>
              )}
              {isConcept ? null : (
                <td className={toneClass(row.active_weight_pct ?? row.active_weight)}>
                  {directionText(row.direction)}
                  {displayValue(row.active_weight_pct ?? row.active_weight)}
                </td>
              )}
              <td>{row.holding_count}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
