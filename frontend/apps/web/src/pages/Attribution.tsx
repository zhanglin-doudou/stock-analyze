import { Fragment, type CSSProperties, type ReactElement } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type {
  Attribution,
  AttributionFactor,
  AttributionMissing,
} from '@sa/api-client';
import { api } from '../client';
import { latestCompletedMonth } from '../lib/date';
import { ProblemView } from '../ui/Problem';
import { displayValue, signTone } from '../ui/format';
import { SAMPLE_STATUS_LABELS, dataStatusLabel } from '../ui/status';
import './dashboard.css';
import './attribution.css';
import { PeriodInput } from '../ui/PeriodInput';

const TOTAL_PNL_BASIS_LABELS: Record<string, string> = {
  asset_daily_mtm: '资产日表盯市（现金 + 持仓市值）',
};
const SYMBOL_CLASS_LABELS: Record<string, string> = {
  stock: 'A 股',
  fund: '基金',
  bond: '债券',
  cash: '现金',
  pseudo: '非证券标的',
};
const REASON_CODE_LABELS: Record<string, string> = {
  SECTOR_MAPPING_MISSING: '缺行业归属',
  SECTOR_BAR_MISSING: '缺板块指数行情',
  SECTOR_BENCHMARK_WEIGHTS_MISSING: '缺基准权重',
  SECTOR_HISTORY_NOT_COVERED: '板块成分历史未覆盖',
  ASSET_SERIES_MISSING: '缺资产日表',
  ASSET_ROW_BLOCKED: '资产行阻断',
  ASSET_SERIES_STALE: '资产日表过期',
  COVERAGE_LOW: '资金覆盖率不足',
};

const RATIO_BASIS_LABELS: Record<string, string> = {
  'amount/total_pnl': '本期总损益',
};
const COVERAGE_BASE_LABELS: Record<string, string> = {
  market: '按市值加权',
  mixed: '市值与现金混合',
};
const BENCHMARK_LABELS: Record<string, string> = {
  hs300_tr: '沪深 300 全收益',
  zz500_tr: '中证 500 全收益',
  csi1000_tr: '中证 1000 全收益',
};

const RATIO_HINT =
  '贡献率 = 因子金额 / 本期总损益。总损益为负时，正金额表示“减少亏损”。';

/** 本地日期 → `YYYY-MM-DD`（仅拼字符串，不做业务数值换算）。 */
function isoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function lastNMonthsRange(count: number): string {
  const now = new Date();
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const start = new Date(end.getFullYear(), end.getMonth() - (count - 1), now.getDate());
  return `${isoDate(start)}..${isoDate(end)}`;
}

/** 今年年初至今天（U-14）。 */
function yearToDateRange(): string {
  const now = new Date();
  return `${now.getFullYear()}-01-01..${isoDate(now)}`;
}

const QUICK_RANGES: Array<{ label: string; build: () => string }> = [
  { label: '近3月', build: () => lastNMonthsRange(3) },
  { label: '近6月', build: () => lastNMonthsRange(6) },
  { label: '近12月', build: () => lastNMonthsRange(12) },
  { label: '今年', build: yearToDateRange },
  { label: '上月', build: () => latestCompletedMonth() },
];

/** 五因子释义（术语统一；U-15）。 */
const FACTOR_DEFINITIONS: Record<
  string,
  { definition: string; source: string }
> = {
  selection: {
    definition: '个股选择相对基准的超额收益。',
    source: '回合持仓 + 板块基准权重',
  },
  sector: {
    definition: '行业/板块配置权重相对基准的偏离收益。',
    source: '板块归属 + 板块指数 + 基准权重',
  },
  buy_timing: {
    definition: '买入时点相对区间均价产生的损益。',
    source: '买入成交价 + 区间行情',
  },
  sell_timing: {
    definition: '卖出时点相对区间均价产生的损益。',
    source: '卖出成交价 + 区间行情',
  },
  fee: {
    definition: '佣金、印花税、过户费等交易费用。',
    source: '流水费用列',
  },
};

/** 原始码值一律转人话；未知码保留可追溯但加“未识别”前缀（U-02）。 */
function labelFor(
  map: Record<string, string>,
  code: string | null | undefined,
): string {
  if (code == null || code === '') return '—';
  return map[code] ?? `未识别项（${code}）`;
}

/** 纯字符串加千分位与“元”，不解析数值（零口径，U-03）。 */
function formatMoney(value: string | null | undefined): string {
  if (value == null || value === '') return '—';
  const negative = value.startsWith('-');
  const body = negative ? value.slice(1) : value;
  const [integer, decimal] = body.split('.');
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}${grouped}${decimal != null ? `.${decimal}` : ''} 元`;
}

/** 百分比只取后端 `*_pct` 串；缺失时显示占位符（U-03）。 */
function formatPct(value: string | null | undefined): string {
  return value == null || value === '' ? '—' : value;
}

/**
 * 定点小数字符串比较（仅用于展示排序，不解析数值；零口径）。
 * 支持 `-12.34` / `0.00` / `1234.56` 这类后端金额串。
 */
function compareDecimalStrings(left: string, right: string): number {
  const signOf = (value: string): number => (value.startsWith('-') ? -1 : 1);
  const absOf = (value: string): string =>
    value.startsWith('-') ? value.slice(1) : value;
  const compareAbs = (a: string, b: string): number => {
    const [aInt = '', aDec = ''] = a.split('.');
    const [bInt = '', bDec = ''] = b.split('.');
    if (aInt.length !== bInt.length) return aInt.length - bInt.length;
    if (aInt !== bInt) return aInt < bInt ? -1 : 1;
    if (aDec !== bDec) return aDec < bDec ? -1 : 1;
    return 0;
  };
  const leftSign = signOf(left);
  const rightSign = signOf(right);
  if (leftSign !== rightSign) return leftSign - rightSign;
  const compared = compareAbs(absOf(left), absOf(right));
  return leftSign < 0 ? -compared : compared;
}

/** `130.18%` → true（仅字符串比较，不解析数值；U-08）。 */
function pctExceeds100(value: string | null | undefined): boolean {
  if (value == null || value === '') return false;
  const body = value.endsWith('%') ? value.slice(0, -1) : value;
  const [integer = ''] = body.replace(/^[+−-]/, '').split('.');
  return integer.length > 3 || (integer.length === 3 && integer >= '100');
}

const USER_ACTION_LABELS: Record<string, string> = {
  ASSET_SERIES_MISSING: '去资产页构建资产日表',
  ASSET_ROW_BLOCKED: '去资产页处理阻断行',
  ASSET_SERIES_STALE: '去资产页更新资产日表',
  SECTOR_DATA_MISSING: '去板块页同步板块数据',
  SECTOR_BAR_MISSING: '去板块页同步板块行情',
  SECTOR_HISTORY_NOT_COVERED: '去板块页同步历史成分',
  SECTOR_MAPPING_MISSING: '去板块页补齐缺失标的的行业归属',
  SECTOR_BENCHMARK_WEIGHTS_MISSING: '联系管理员同步基准权重',
  SECTOR_CLASSIFICATION_LOOKAHEAD: '确认 look-ahead 口径例外已批准',
  BRIDGE_MISMATCH: '去对账页检查断链',
  RESIDUAL_LARGE: '查看下方「残差去向」',
  RESIDUAL_WITHIN_TOLERANCE: '无需处理',
  COVERAGE_LOW: '查看下方覆盖率与未归因标的',
};

const SEVERITY_ORDER: Record<string, number> = {
  blocker: 0,
  warning: 1,
  info: 2,
};
const SEVERITY_LABELS: Record<string, string> = {
  blocker: '阻断',
  warning: '降级',
  info: 'ⓘ 提示',
};

/** 只读展示用：assumptions 中字符串项（U-06/U-11）。 */
function assumptionText(data: Attribution, key: string): string | undefined {
  const value = data.assumptions?.[key];
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** 只读展示用：assumptions.data_sources 三源截至日（U-11）。 */
function dataSourceText(data: Attribution, key: string): string | undefined {
  const sources = data.assumptions?.['data_sources'];
  if (sources == null || typeof sources !== 'object') return undefined;
  const value = (sources as Record<string, unknown>)[key];
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** 只读展示用：assumptions.data_starts 三源实际起点 + 请求区间起点。 */
function dataStartText(data: Attribution, key: string): string | undefined {
  const starts = data.assumptions?.['data_starts'];
  if (starts == null || typeof starts !== 'object') return undefined;
  const value = (starts as Record<string, unknown>)[key];
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** 是否使用了最新行业成分回溯（assumptions 里的布尔标记）。 */
function lookaheadActive(data: Attribution | undefined): boolean {
  return data?.assumptions?.['classification_lookahead'] === true;
}


function StatusBadge({ status }: { status: string }): ReactElement {
  return (
    <span className={`dash-badge is-${status}`} data-testid="attr-status-badge">
      {dataStatusLabel(status)}
    </span>
  );
}

/** 因子行（零口径）：宽度直接用后端 `presentation.bar_pct`，不解析不换算。 */
function FactorRow({
  factor,
  links,
}: {
  factor: AttributionFactor;
  links: Attribution['links'];
}): ReactElement {
  const barPct = (factor.presentation.bar_pct ?? '0%').replace(/^[+−-]/, '');
  const info = FACTOR_DEFINITIONS[factor.code];
  return (
    <li className="attr-factor" data-testid={`attr-factor-${factor.code}`}>
      <span className="attr-rank" aria-label={`排名 ${factor.rank}`}>
        {factor.rank}
      </span>
      <strong className="attr-factor-name">
        {factor.name}
        {info ? (
          <span
            className="attr-info"
            title={`${info.definition}数据来源：${info.source}`}
            aria-label={`${factor.name}释义`}
          >
            ⓘ
          </span>
        ) : null}
      </strong>
      <span className="attr-factor-amount num">
        {formatMoney(factor.amount)}
        <small className="attr-factor-ratio muted" title={RATIO_HINT}>
          贡献率 {formatPct(factor.presentation.ratio_pct)}
        </small>
      </span>
      <span className="attr-factor-share num">
        {formatPct(factor.presentation.share_pct)}
      </span>
      <span className="attr-factor-conclusion">
        {displayValue(factor.conclusion)}
        {factor.presentation.sector_bar_fallback ? (
          <span
            className="attr-factor-fallback muted"
            data-testid="attr-sector-fallback"
            title="期初缺板块指数 bar（节假日），已回退取参考日之后首个可得收盘"
          >
            期初回退价
          </span>
        ) : null}
        <Link
          className="attr-factor-link"
          to={factor.code === 'sector' ? links.sectors : links.rounds}
        >
          查看相关{factor.code === 'sector' ? '板块' : '回合'}
        </Link>
      </span>
      <div className="attr-bar" aria-hidden="true">
        <i
          data-tone={factor.presentation.tone}
          style={{ '--w': barPct } as CSSProperties}
        />
      </div>
    </li>
  );
}

function MissingList({
  missing,
}: {
  missing: AttributionMissing[];
}): ReactElement {
  // 阻断项优先（U-10）。
  const ordered = [...missing].sort(
    (left, right) =>
      (SEVERITY_ORDER[left.severity] ?? 2) -
      (SEVERITY_ORDER[right.severity] ?? 2),
  );
  return (
    <ul className="attr-missing" data-testid="attr-missing">
      {ordered.map((item, index) => (
        <li
          key={`${item.code}-${item.field}-${index}`}
          data-testid={`attr-missing-${item.code}`}
          data-severity={item.severity}
        >
          <span className="attr-severity" data-severity={item.severity}>
            {SEVERITY_LABELS[item.severity] ?? '降级'}
          </span>
          {item.severity === 'info' ? (
            <p>{item.reason}</p>
          ) : (
            <>
              <strong>{item.reason}</strong>
              <span className="muted">
                影响：{item.field_label ?? item.field}
              </span>
              <span className="attr-what-to-do">
                建议：{USER_ACTION_LABELS[item.code] ?? '联系管理员'}
              </span>
            </>
          )}
          <details className="attr-tech-details">
            <summary className="muted">技术详情</summary>
            <div className="muted">
              {item.code} · {item.field} · {item.what_to_do}
            </div>
          </details>
        </li>
      ))}
    </ul>
  );
}

/** 顶栏 KPI：把“本期盈亏”提到页面主信息位置（U-12）。 */
function TotalPnlBar({ data }: { data: Attribution }): ReactElement {
  const tone = signTone(data.total_pnl);
  const label =
    tone === 'neg' ? '本期亏损' : tone === 'pos' ? '本期盈利' : '本期持平';
  return (
    <section className="attr-pnl-bar" data-testid="attr-pnl-bar">
      <strong
        className={`attr-pnl-value num is-${tone}`}
        data-testid="attr-pnl-value"
      >
        {label} {formatMoney(data.total_pnl)}
      </strong>
      <span className="muted">数据截至 {data.data_as_of ?? '—'}</span>
    </section>
  );
}

/** 摘要卡：summary 与五因子固定列（后端 rank 决定展示顺序，前端不重排）。 */
function SummaryCard({ data }: { data: Attribution }): ReactElement {
  return (
    <section className="dash-panel attr-panel" data-testid="attr-summary">
      <div className="attr-panel-head">
        <h2>本期结论</h2>
        <span className="muted num">
          总损益 {formatMoney(data.total_pnl)} · 比率分母{' '}
          {labelFor(RATIO_BASIS_LABELS, data.ratio_basis)}
        </span>
      </div>
      {/* 结论整句只渲染后端 summary（含样本不足/空账户固定句，随 Accept-Language） */}
      {data.summary != null ? (
        <p className="attr-summary" data-testid="attr-summary-text">
          {data.summary}
        </p>
      ) : (
        <div className="attr-summary-na" data-testid="attr-summary-na">
          <p className="attr-summary">本期暂不生成结论</p>
          <p className="muted">
            {data.summary_na_reason ??
              '样本或数据覆盖不足，原因见下方「数据质量」。'}
          </p>
        </div>
      )}
      <ul className="attr-factors">
        <li className="attr-factor-head-row" aria-hidden="true">
          <span>排名</span>
          <span>因子</span>
          <span>金额</span>
          <span>占构成</span>
          <span>说明</span>
        </li>
        {/* 用后端 rank 排序，避免固定数组顺序与 rank 徽标出现 2,1,5,3,4（U-04） */}
        {[...(data.factors ?? [])]
          .sort((left, right) => left.rank - right.rank)
          .map((factor) => (
            <FactorRow key={factor.code} factor={factor} links={data.links} />
          ))}
      </ul>
      <p className="muted attr-ratio-hint">{RATIO_HINT}</p>
    </section>
  );
}

interface ResidualTolerance {
  amount?: string | null;
  pct?: string | null;
  base?: string | null;
}

/** 容差信息来自后端 `assumptions.residual_tolerance`；前端只窄化读取，不改数值。 */
function readResidualTolerance(
  assumptions: Attribution["assumptions"],
): ResidualTolerance | null {
  const value = assumptions?.residual_tolerance;
  return typeof value === 'object' && value !== null ? (value as ResidualTolerance) : null;
}

function ResidualCard({ data }: { data: Attribution }): ReactElement {
  // 桶按金额从大到小排序（只做展示排序，不解析数值；U-08）。
  const buckets = Object.entries(data.residual_breakdown ?? {}).sort(
    (left, right) => compareDecimalStrings(right[1], left[1]),
  );
  // 展示条数与「其余 N 只」均由后端切分（`display_limit` 单一来源），前端不截断。
  const unattributed = data.coverage?.unattributed ?? [];
  const tolerance = readResidualTolerance(data.assumptions);
  // 是否容差内由后端 missing[] 码决定，前端不做数值比较。
  const withinTolerance =
    data.missing?.some((item) => item.code === 'RESIDUAL_WITHIN_TOLERANCE') ?? false;
  return (
    <section className="dash-panel attr-panel" data-testid="attr-residual">
      <div className="attr-panel-head">
        <h2>残差去向</h2>
        <span className="muted num">
          残差 {formatMoney(data.residual)} · 残差比{' '}
          {formatPct(data.residual_ratio_pct)}
          {pctExceeds100(data.residual_ratio_pct) ? (
            <span className="attr-ratio-warn">（残差大于本期总损益）</span>
          ) : null}
        </span>
      </div>
      <p className="attr-residual-note">
        残差是五因子未能解释的损益。正数=未归因收益（抬高本期损益），
        负数=估值差/账面与市值口径差异（拉低本期损益）。
      </p>
      {/* 容差行：金额/期末总资产/比例均直接展示后端串；不比较数值、不取整。 */}
      {tolerance ? (
        <p className="attr-residual-tolerance" data-testid="attr-residual-tolerance">
          {tolerance.amount != null || tolerance.base != null
            ? `容差 ${formatMoney(tolerance.amount)}（期末总资产 ${formatMoney(tolerance.base)}）`
            : `容差比例 ${formatPct(tolerance.pct)}（期末资产未知，数据阻断时不判定）`}
          <span className="muted" data-testid="attr-residual-tolerance-status">
            {withinTolerance
              ? '容差内（不降级）'
              : data.status === 'blocked'
                ? '—（数据阻断，无法判定）'
                : '超出容差（已降级）'}
          </span>
        </p>
      ) : null}
      <div className="dash-kpi-grid attr-kpis">
        <div
          className="dash-kpi"
          title="三种覆盖口径：资金权重=已归因权益市值/应归因权益市值；标的=已归因数/本期参与数（历史候选含本期未参与，仅统计口径）；段=已归因段/可归因段"
        >
          <span className="dash-kpi-label">资金覆盖率（按资金权重）</span>
          <strong className="num" data-testid="attr-capital-ratio">
            {formatPct(data.coverage?.capital_ratio_pct)}
          </strong>
          <span className="dash-kpi-hint" data-testid="attr-coverage-bases">
            {data.coverage
              ? `按标的 ${data.coverage.symbols_attributed}/${data.coverage.symbols_participating} · 按段 ${data.coverage.segments_attributed}/${data.coverage.segments_total} · ${labelFor(COVERAGE_BASE_LABELS, data.coverage.base_basis)}`
              : '—'}
          </span>
        </div>
        <div className="dash-kpi">
          <span className="dash-kpi-label">样本</span>
          <strong className="num">{data.sample.n_attributed_segments}</strong>
          <span className="dash-kpi-hint">
            门槛 {data.sample.min_n} 段 ·{' '}
            {SAMPLE_STATUS_LABELS[data.sample.sample_status]}
          </span>
        </div>
      </div>
      <ul className="attr-buckets" data-testid="attr-residual-buckets">
        {buckets.map(([key, value]) => (
          <li key={key}>
            <span className="muted">
              {data.residual_breakdown_labels?.[key] ?? key}
            </span>
            <span className="num">{formatMoney(value)}</span>
          </li>
        ))}
      </ul>
      {/* 对账行：全部数字来自后端派生字段（零口径，不在前端加总/取整）。 */}
      {data.residual_reconciliation ? (
        <p className="attr-reconciliation" data-testid="attr-reconciliation">
          明细合计{' '}
          {formatMoney(data.residual_reconciliation.unattributed_symbols_total)}{' '}
          + 账户级{' '}
          {formatMoney(data.residual_reconciliation.account_level_total)} ={' '}
          <strong className="num">
            {formatMoney(data.residual_reconciliation.residual)}
          </strong>
        </p>
      ) : null}
      {unattributed.length > 0 ? (
        <details className="attr-unattributed-details">
          <summary className="muted">
            未归因标的明细（按金额降序
            {data.residual_reconciliation
              ? `，最多 ${data.residual_reconciliation.display_limit} 只`
              : ''}
            ）
          </summary>
          <ul className="attr-unattributed" data-testid="attr-unattributed">
            {unattributed.map((item) => (
              <li key={item.security_code}>
                {/* 代码 + 名称同一行数据；名称缺失只显示代码（不做占位错配）。 */}
                <strong>
                  {item.security_code}
                  {item.security_name ? ` ${item.security_name}` : ''}
                </strong>
                <span className="muted">
                  {labelFor(SYMBOL_CLASS_LABELS, item.symbol_class)}
                </span>
                <span className="muted">
                  {labelFor(REASON_CODE_LABELS, item.reason_code)}
                </span>
                <span className="num">{formatMoney(item.amount)}</span>
              </li>
            ))}
            {/* 第 display_limit 条之后的标的汇总行（非标的、不可点击）；数字全部来自后端。 */}
            {data.residual_reconciliation &&
            data.residual_reconciliation.others_count > 0 ? (
              <li
                className="attr-unattributed-others"
                data-testid="attr-unattributed-others"
              >
                <strong>
                  其余 {data.residual_reconciliation.others_count} 只
                </strong>
                <span className="num">
                  {formatMoney(data.residual_reconciliation.others_total)}
                </span>
              </li>
            ) : null}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

/** 页脚口径说明（definition_version / data_as_of / benchmark / assumptions；U-15）。 */
function CaliberNotes({ data }: { data: Attribution }): ReactElement {
  const assumptions = data.assumptions ?? {};
  return (
    <section className="attr-caliber" data-testid="attr-caliber">
      <h2 className="attr-caliber-title">口径说明</h2>
      <dl className="attr-caliber-list">
        <dt>定义版本</dt>
        <dd>{data.definition_version}</dd>
        <dt>数据截至</dt>
        <dd>{data.data_as_of ?? '—'}</dd>
        <dt>数据起点</dt>
        <dd>
          资产 {dataStartText(data, 'asset_daily') ?? '—'} · 行情{' '}
          {dataStartText(data, 'market') ?? '—'} · 板块{' '}
          {dataStartText(data, 'sector') ?? '—'} · 区间 {data.period_start}
        </dd>
        <dt>基准</dt>
        <dd>
          {labelFor(BENCHMARK_LABELS, assumptionText(data, 'benchmark_id'))}
        </dd>
        <dt>比率分母</dt>
        <dd>{labelFor(RATIO_BASIS_LABELS, data.ratio_basis)}</dd>
        <dt>总损益口径</dt>
        <dd>{labelFor(TOTAL_PNL_BASIS_LABELS, data.total_pnl_basis)}</dd>
      </dl>
      <p className="attr-caliber-term">
        术语：残差 = 未归属（与结论中的“未归属”同义）；板块配置 =
        用户常说的“板块”； 本期参与标的 =
        期末有持仓或区间内有买卖的标的（历史候选另计，仅统计口径）。
      </p>
      <details className="attr-tech-details">
        <summary className="muted">
          技术口径（assumptions {Object.keys(assumptions).length} 项）
        </summary>
        <dl className="attr-caliber-list">
          {Object.entries(assumptions).map(([key, value]) => (
            <Fragment key={key}>
              <dt>{key}</dt>
              <dd>
                {typeof value === 'string' ? value : JSON.stringify(value)}
              </dd>
            </Fragment>
          ))}
        </dl>
      </details>
    </section>
  );
}

function QualityCard({ data }: { data: Attribution }): ReactElement {
  const missing = data.missing ?? [];
  // info 只是提示（不影响归因口径），不计入数据质量缺口数。
  const gapCount = missing.filter((item) => item.severity !== 'info').length;
  const infoCount = missing.length - gapCount;
  const summary =
    gapCount > 0
      ? `数据质量缺口 ${gapCount} 项`
      : infoCount > 0
        ? `无数据质量缺口（${infoCount} 条提示）`
        : '无数据质量缺口';
  return (
    <section className="dash-panel attr-panel" data-testid="attr-quality">
      <div className="attr-panel-head">
        <h2>数据质量</h2>
        <span className="muted" data-testid="attr-quality-summary">
          {summary} · 数据截至 {data.data_as_of ?? '—'} · 口径{' '}
          {data.definition_version}
        </span>
      </div>
      {missing.length > 0 ? (
        <MissingList missing={missing} />
      ) : (
        <p className="muted">本期无降级项。</p>
      )}
      <nav className="attr-links" aria-label="相关入口">
        <Link to={data.links.rounds}>回合</Link>
        <Link to={data.links.sectors}>板块</Link>
        <Link to={data.links.dashboard}>看板</Link>
        <Link to={data.links.reports}>月报</Link>
      </nav>
    </section>
  );
}

export function AttributionPage(): ReactElement {
  const [params, setParams] = useSearchParams();
  const period = params.get('period') ?? yearToDateRange();
  // 与归因设计 §4.3 一致：lookahead 必须显式请求，默认 deny（不得默认回溯未来信息）。
  const allowLookahead = (params.get('lookahead') ?? 'deny') === 'allow';

  const query = useQuery({
    queryKey: ['attribution', period, allowLookahead],
    queryFn: () =>
      api.attribution.get({
        period,
        classification_lookahead: allowLookahead ? 'allow' : 'deny',
      }),
    placeholderData: keepPreviousData,
  });

  const updatePeriod = (next: string): void => {
    const draft = new URLSearchParams(params);
    if (next) draft.set('period', next);
    else draft.delete('period');
    setParams(draft, { replace: true });
  };

  const updateLookahead = (next: boolean): void => {
    const draft = new URLSearchParams(params);
    if (next) draft.set('lookahead', 'allow');
    else draft.set('lookahead', 'deny');
    setParams(draft, { replace: true });
  };

  if (query.error != null) {
    return (
      <div className="attr-page" data-testid="attribution-page">
        <h1 className="dash-title">收益归因</h1>
        <ProblemView error={query.error} onRetry={() => void query.refetch()} />
      </div>
    );
  }

  const data = query.data;
  const blocked = data?.status === 'blocked';
  const assetsAsOf = data ? dataSourceText(data, 'asset_daily') : undefined;
  const marketAsOf = data ? dataSourceText(data, 'market') : undefined;
  const sectorAsOf = data ? dataSourceText(data, 'sector') : undefined;
  const assetsStart = data ? dataStartText(data, 'asset_daily') : undefined;
  const marketStart = data ? dataStartText(data, 'market') : undefined;
  const sectorStart = data ? dataStartText(data, 'sector') : undefined;
  const hasDataSources =
    assetsAsOf != null || marketAsOf != null || sectorAsOf != null;

  return (
    <div
      className="attr-page"
      data-testid="attribution-page"
      aria-busy={query.isFetching}
    >
      <header className="dash-head">
        <div className="dash-eyebrow">归因口径 · 五因子</div>
        <h1 className="dash-title">收益归因</h1>
        <p className="dash-sub">
          {data
            ? `${data.period_start} ~ ${data.period_end} · 模型 ${data.model}`
            : '选择期间查看五因子收益拆解；所有数值均为后端口径，本页只做呈现。'}
        </p>
        {query.isFetching && query.isPlaceholderData ? (
          <p className="attr-updating" data-testid="attr-updating">
            正在更新…当前展示的是上一区间数据
          </p>
        ) : null}
        <div className="dash-head-meta">
          <PeriodInput
            value={period}
            disabled={query.isLoading}
            defaultEnd={data?.data_as_of ?? undefined}
            onChange={updatePeriod}
          />
          <nav className="attr-quick" aria-label="快捷区间">
            {QUICK_RANGES.map((item) => (
              <button
                key={item.label}
                type="button"
                className="attr-quick-button"
                disabled={query.isLoading}
                onClick={() => updatePeriod(item.build())}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <label
            className="attr-lookahead-toggle"
            title="行业归属取最新快照回溯历史区间，可能与当时实际归属不符（未来信息风险）"
          >
            <input
              type="checkbox"
              checked={allowLookahead}
              disabled={query.isLoading}
              onChange={(event) => updateLookahead(event.target.checked)}
            />
            使用最新行业成分回溯历史
          </label>
          <span>
            页面状态 <StatusBadge status={data?.status ?? 'missing'} />
          </span>
          <span>数据截至 {data?.data_as_of ?? '—'}</span>
          {data ? (
            <span data-testid="attr-data-starts">
              数据起点 资产 {assetsStart ?? '—'} · 行情 {marketStart ?? '—'} ·
              板块 {sectorStart ?? '—'} · 区间 {data.period_start}
            </span>
          ) : null}
          {data ? (
            <>
              {hasDataSources ? (
                <span data-testid="attr-data-sources">
                  资产 {assetsAsOf ?? '—'} · 行情 {marketAsOf ?? '—'} · 板块{' '}
                  {sectorAsOf ?? '—'}
                </span>
              ) : null}
              {blocked ? (
                <span data-testid="attr-period-end">
                  区间末端 {data.period_end}
                </span>
              ) : null}
              <span>
                总损益口径{' '}
                {labelFor(TOTAL_PNL_BASIS_LABELS, data.total_pnl_basis)}
              </span>
              <span>
                基准{' '}
                {labelFor(
                  BENCHMARK_LABELS,
                  assumptionText(data, 'benchmark_id'),
                )}
              </span>
            </>
          ) : null}
        </div>
        {lookaheadActive(data) ? (
          <p
            className="attr-lookahead-banner"
            data-testid="attr-lookahead-banner"
            role="alert"
          >
            已使用最新行业成分回溯历史：行业归属取自最新快照，可能与当时实际归属不符。
          </p>
        ) : null}
      </header>

      {data == null ? (
        <section className="dash-panel attr-panel" data-testid="attr-skeleton">
          <p className="muted">加载中…</p>
          <div className="attr-skeleton" aria-hidden="true">
            <span className="attr-skeleton-line" />
            <span className="attr-skeleton-line" />
            <span className="attr-skeleton-line is-short" />
          </div>
        </section>
      ) : (
        <>
          {blocked ? (
            // blocked：不渲染任何金额（§8.1-④），只渲染数据质量卡。
            <QualityCard data={data} />
          ) : (
            <>
              <TotalPnlBar data={data} />
              <SummaryCard data={data} />
              <ResidualCard data={data} />
              <QualityCard data={data} />
            </>
          )}
          <CaliberNotes data={data} />
        </>
      )}
    </div>
  );
}
