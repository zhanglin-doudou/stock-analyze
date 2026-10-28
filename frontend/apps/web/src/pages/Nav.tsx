import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Nav, NavPoint, NavStatus } from "@sa/api-client";
import { api } from "../client";
import { ProblemView } from "../ui/Problem";
import { DataTable, type DataTableColumn } from "../ui/DataTable";

const STATUS_LABELS: Record<NavStatus, string> = {
  ok: "正常",
  blocked: "数据阻断",
  degraded: "降级",
};

/** 状态中文标签；未知状态原样展示（后端新增枚举时不至于空白）。 */
export function navStatusLabel(status: string): string {
  return STATUS_LABELS[status as NavStatus] ?? status;
}

/** 正负着色只做**符号展示**，不做任何数值计算（前端零口径）。 */
function signedClass(value: string): string {
  if (value.startsWith("-")) return "money money-neg";
  if (/^0(\.0+)?$/.test(value)) return "money";
  return "money money-pos";
}

/** 当前月份（YYYY-MM）。仅取系统日历做输入框默认值，不参与任何收益计算。 */
function currentMonth(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return `${now.getFullYear()}-${month}`;
}

const PERIOD_RE = /^\d{4}-\d{2}$/;

/** 找到最后一个非 blocked 的 NavPoint，作为持仓明细默认选中日。 */
function lastUnblocked(points: NavPoint[]): NavPoint | undefined {
  for (let i = points.length - 1; i >= 0; i--) {
    if (!points[i].blocked) return points[i];
  }
  return undefined;
}

/** 净值与收益（/nav）：按月构建份额法 TWR 与 XIRR/MWR 并展示每日净值 + 持仓。 */
export function Nav() {
  const [searchParams, setSearchParams] = useSearchParams();
  const period = searchParams.get('period') || currentMonth();
  const [result, setResult] = useState<Nav | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedDate, setSelectedDate] = useState<string>("");

  const build = useCallback(async () => {
    if (!PERIOD_RE.test(period)) {
      setFormError("月份格式应为 YYYY-MM，例如 2026-06。");
      return;
    }
    setLoading(true);
    setError(null);
    setFormError("");
    setNotice("");
    setSelectedDate("");
    try {
      const data = await api.nav.get({ period });
      setResult(data);
      const defaultPoint = lastUnblocked(data.points);
      if (defaultPoint) setSelectedDate(defaultPoint.date);
      // 期初现金为区间首条余额行反推时给出口径提示（API-032 assumption）。
      const assumptionHint =
        data.summary.assumption === "back_solved"
          ? " 期初资产为反推值（assumption=back_solved）。"
          : "";
      if (data.status === "blocked") {
        setNotice(
          "本期净值存在阻断区间（缺期初资产锚点、缺行情或对账断链），阻断日不输出资产值。" +
            assumptionHint,
        );
      } else if (data.status === "degraded") {
        setNotice("本期部分日期存在 FIFO 数据异常，净值为降级结果，仅供排查参考。" + assumptionHint);
      } else {
        setNotice(`已构建 ${data.period} 的净值与收益。` + assumptionHint);
      }
    } catch (err) {
      setResult(null);
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [period]);

  const columns: Array<DataTableColumn<NavPoint>> = [
    { key: "date", header: "日期", render: (row) => row.date },
    {
      key: "total_assets",
      header: "总资产",
      render: (row) =>
        row.blocked ? <span className="muted">—</span> : <strong>{row.total_assets}</strong>,
    },
    {
      key: "prev_total_assets",
      header: "昨日资产",
      render: (row) =>
        row.blocked || row.prev_total_assets == null ? (
          <span className="muted">—</span>
        ) : (
          row.prev_total_assets
        ),
    },
    {
      key: "nav",
      header: "单位净值",
      render: (row) => (row.blocked ? <span className="muted">—</span> : row.unit_nav),
    },
    {
      key: "twr",
      header: "日收益（%）",
      render: (row) =>
        row.blocked ? (
          <span className="muted">—</span>
        ) : (
          <span className={signedClass(row.twr_daily_pct)}>{row.twr_daily_pct}</span>
        ),
    },
    {
      key: "daily_pnl",
      header: "日收益额",
      render: (row) =>
        row.blocked || row.daily_pnl == null ? (
          <span className="muted">—</span>
        ) : (
          <span className={signedClass(row.daily_pnl)}>{row.daily_pnl}</span>
        ),
    },
    {
      key: "cashflow",
      header: "外部现金流",
      render: (row) => <span className={signedClass(row.cashflow)}>{row.cashflow}</span>,
    },
  ];

  // 整月没有任何阻断点时不展示阻断列（避免正常月份出现一列全空）。
  if (result?.points.some((point) => point.blocked)) {
    columns.push({
      key: "blocked",
      header: "阻断",
      render: (row) => (row.blocked ? <span className="badge badge-data_error">阻断</span> : null),
    });
  }

  const summary = result?.summary;
  const blockedRanges = result?.blocked_ranges ?? [];

  const selectedPoint = useMemo(
    () => result?.points.find((p) => p.date === selectedDate),
    [result, selectedDate],
  );

  const holdingColumns: Array<DataTableColumn<NonNullable<NavPoint["holdings"]>[number]>> = [
    { key: "security_code", header: "代码", render: (row) => row.security_code },
    { key: "security_name", header: "名称", render: (row) => row.security_name },
    { key: "quantity", header: "持有份额", render: (row) => row.quantity },
    { key: "avg_cost", header: "买入均价", render: (row) => row.avg_cost },
    { key: "close_price", header: "现价", render: (row) => row.close_price },
    {
      key: "market_value",
      header: "市值",
      render: (row) => (
        <span className={signedClass(row.market_value)}>{row.market_value}</span>
      ),
    },
  ];

  return (
    <div>
      <h1>净值与收益</h1>
      <p className="muted">
        按月计算份额法时间加权收益率（TWR）与资金加权收益率（XIRR/MWR）；现金流日按日末发生假设处理。
        本期不含基准对比，beta/alpha/IR 等指标留待后续版本。
      </p>

      <div className="card filters">
        <label>
          月份
          <input
            type="month"
            value={period}
            onChange={(e) => setSearchParams({ period: e.target.value })}
            aria-label="月份"
          />
        </label>
        <div className="row-actions">
          <button className="btn" type="button" disabled={loading} onClick={() => void build()}>
            {loading ? "构建中…" : "构建净值与收益"}
          </button>
          <Link className="btn btn-ghost" to="/rounds">
            查看回合
          </Link>
        </div>
      </div>

      {notice ? <p className="notice">{notice}</p> : null}
      {formError ? <p className="field-error">{formError}</p> : null}
      <ProblemView error={error} onRetry={() => void build()} />

      {result ? (
        <div className="card" data-testid="nav-result">
          <p>
            区间：<strong>{result.period}</strong>
            <span className="muted">　状态：</span>
            <span className={`badge badge-${result.status}`}>{navStatusLabel(result.status)}</span>
            <span className="muted">　基准：{result.benchmark_id}</span>
            <span className="muted">　口径版本：{result.definition_version}</span>
          </p>
          {summary ? (
            <table className="table" data-testid="nav-summary">
              <thead>
                <tr>
                  <th>TWR（累计，小数）</th>
                  <th>年化 TWR</th>
                  <th>MWR / XIRR</th>
                  <th>MWR − TWR</th>
                  <th>自然日</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    <span className={signedClass(summary.twr)}>{summary.twr}</span>
                  </td>
                  <td>
                    <span className={signedClass(summary.annualized_twr)}>{summary.annualized_twr}</span>
                  </td>
                  <td>
                    <span className={signedClass(summary.mwr)}>{summary.mwr}</span>
                  </td>
                  <td>
                    <span className={signedClass(summary.mwr_minus_twr)}>{summary.mwr_minus_twr}</span>
                  </td>
                  <td>{summary.n_days}</td>
                </tr>
              </tbody>
            </table>
          ) : null}

          {blockedRanges.length > 0 ? (
            <div className="notice-warn" data-testid="nav-blocked">
              <p>以下区间被阻断，不输出资产值：</p>
              <ul>
                {blockedRanges.map((range, index) => (
                  <li key={`${range.from}-${range.to}-${index}`}>
                    {range.from} ~ {range.to}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <DataTable
            caption="每日净值表"
            columns={columns}
            rows={result.points}
            rowKey={(row) => row.date}
            emptyText="本期没有可输出的净值日点（可能缺少余额行或行情数据）。"
          />
          <p className="muted">数据截至：{result.data_as_of}</p>

          {/* 每日持仓分析 */}
          <hr style={{ margin: "24px 0", border: "none", borderTop: "1px solid var(--border)" }} />
          <h2>每日持仓分析</h2>
          {(() => {
            const unblocked = result.points.filter((p) => !p.blocked);
            if (unblocked.length === 0) {
              return <p className="muted">本期所有日均被阻断，无可展示的持仓。</p>;
            }
            return (
              <>
                <label>
                  <span className="muted">选择日期：</span>
                  <select
                    value={selectedDate}
                    onChange={(e) => setSelectedDate(e.target.value)}
                    aria-label="持仓日期"
                    style={{ marginLeft: "8px" }}
                  >
                    {unblocked.map((p) => (
                      <option key={p.date} value={p.date}>
                        {p.date}　总资产 {p.total_assets}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedPoint ? (
                  <div style={{ marginTop: "16px" }} data-testid="nav-holdings">
                    <p>
                      <strong>{selectedPoint.date}</strong>
                      <span className="muted">　总资产：</span>
                      <span className="money">{selectedPoint.total_assets}</span>
                    </p>
                    <DataTable
                      caption="每日持仓明细"
                      columns={holdingColumns}
                      rows={selectedPoint.holdings ?? []}
                      rowKey={(row) => row.security_code}
                      emptyText="该日无持仓（可能已全部清仓）。"
                    />
                  </div>
                ) : null}
              </>
            );
          })()}
        </div>
      ) : (
        <p className="muted">
          选择月份后点「构建净值与收益」。首次构建需要账单已导入且存在现金余额行；
          没有数据？先到 <Link to="/import">导入账单</Link>。
        </p>
      )}
    </div>
  );
}
